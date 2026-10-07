"""MusicXML 读取、容器解包、规范化与元数据提取（需求 §5.1.3）。

本模块**不依赖 verovio / Qt**，只做 XML 层的工作，便于单测。

关键实测事实（见 docs/requirements.md 附录 A）：
  * 本机 10 个样本全部是 ``.mxl``（ZIP 容器），且 ``META-INF/container.xml`` 指向的
    根文件名不统一（``score.xml`` 或 ``lg-*.xml``）→ **不能假设文件名**。
  * 根元素可能是 ``score-partwise`` 或 ``score-timewise``；后者需要转换。
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from ..common.errors import MusicXMLError
from ..common.log import get_logger
from ..common.paths import MUSICXML_SUFFIXES

__all__ = [
    "PartInfo",
    "MusicXMLDocument",
    "read_musicxml",
    "local_name",
    "normalize_root_name",
]

log = get_logger(__name__)

_ZIP_MAGIC = b"PK\x03\x04"
_XML_DECL = '<?xml version="1.0" encoding="UTF-8"?>'


# --------------------------------------------------------------------------- 工具
def local_name(tag: str) -> str:
    """去掉 ``{namespace}`` 前缀，返回本地标签名。"""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _first(elem: ET.Element | None, *names: str) -> ET.Element | None:
    """深度优先找第一个本地名匹配的子元素。"""
    if elem is None:
        return None
    for child in elem.iter():
        if local_name(child.tag) in names:
            return child
    return None


def _text(elem: ET.Element | None, default: str = "") -> str:
    if elem is None or elem.text is None:
        return default
    return elem.text.strip()


def normalize_root_name(path: Path) -> str:
    """把 ``x.mxl`` / ``x.musicxml`` / ``x.xml`` 统一成主名（去掉扩展名）。"""
    name = path.name
    low = name.lower()
    for suf in sorted(MUSICXML_SUFFIXES, key=len, reverse=True):
        if low.endswith(suf):
            return name[: -len(suf)]
    return path.stem


# --------------------------------------------------------------------------- 数据模型
@dataclass
class PartInfo:
    """一个声部（MusicXML ``score-part``）及其 MIDI 乐器信息。"""

    id: str
    name: str = ""
    midi_channel: int | None = None
    """MusicXML ``midi-channel`` 为 1–16；缺省时按声部序推断。"""
    midi_program: int | None = None
    """GM 音色号 0–127（MusicXML 中写 1–128，本类统一归一为 0–127）。"""
    instrument_name: str = ""
    part_name: str = ""
    volume: float | None = None
    pan: int | None = None

    @property
    def is_percussion(self) -> bool:
        """MIDI 通道 10（0-based 9）为打击乐。"""
        return self.midi_channel == 10

    @property
    def program_display(self) -> str:
        if self.midi_program is None:
            return "（未指定 → 默认钢琴）"
        from .gm import gm_program_name  # 延迟导入，避免循环

        return f"{self.midi_program:03d} {gm_program_name(self.midi_program)}"

    @property
    def program_was_explicit(self) -> bool:
        return self.midi_program is not None


@dataclass
class MusicXMLDocument:
    """已读取并规范化的 MusicXML 文档。"""

    source: Path
    """原始源文件路径（可能是 .mxl）。"""
    base_name: str
    """主名，不含扩展名。"""
    root_tag: str
    """``score-partwise`` 或 ``score-timewise``。"""
    version: str = ""
    title: str = ""
    composer: str = ""
    parts: list[PartInfo] = field(default_factory=list)
    xml_bytes: bytes = b""
    """**规范化后的** MusicXML 字节流（UTF-8，partwise，去 BOM）。"""
    warnings: list[str] = field(default_factory=list)
    unrecognized_root: bool = False

    @property
    def part_count(self) -> int:
        return len(self.parts)

    @property
    def is_multi_part(self) -> bool:
        return len(self.parts) > 1

    @property
    def has_explicit_programs(self) -> bool:
        return any(p.program_was_explicit for p in self.parts)

    def write_normalized(self, dest: Path) -> Path:
        """把规范化后的 XML 写到 ``dest``。"""
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.xml_bytes)
        return dest


# --------------------------------------------------------------------------- 读取
def read_musicxml(path: Path | str) -> MusicXMLDocument:
    """读取 MusicXML（``.xml`` / ``.musicxml`` / ``.mxl``）并规范化。

    Raises:
        MusicXMLError: 文件不存在、不是有效 ZIP / XML、或根元素不受支持。
    """
    p = Path(path)
    if not p.is_file():
        raise MusicXMLError(f"源文件不存在：{p}")

    raw = p.read_bytes()
    if not raw.strip():
        raise MusicXMLError(f"源文件为空：{p}")

    if raw[:4] == _ZIP_MAGIC:
        xml_bytes, inner_name = _read_mxl(p, raw)
        log.debug("%s：从 .mxl 容器解出 %s", p.name, inner_name)
    else:
        xml_bytes = raw

    root, full = _parse_xml(xml_bytes, p)
    tag = local_name(root.tag)
    warnings: list[str] = []
    unrecognized = False

    if tag == "score-timewise":
        warnings.append("源文件为 score-timewise，已转换为 score-partwise（规格允许的先决转换）")
        root = _timewise_to_partwise(root)
        tag = "score-partwise"
    elif tag != "score-partwise":
        unrecognized = True
        raise MusicXMLError(
            f"不支持的 MusicXML 根元素 <{tag}>，期望 <score-partwise> 或 <score-timewise>（文件：{p}）"
        )

    parts = _extract_parts(root)
    if not parts:
        raise MusicXMLError(f"未在 <part-list> 中找到任何 <score-part>（文件：{p}）")

    actual_parts = {local_name(e.tag) for e in root if local_name(e.tag) == "part"}
    if not actual_parts:
        warnings.append("文档中没有任何 <part> 数据（可能只有骨架）")

    title = _text(_first(root, "work-title")) or _text(_first(root, "movement-title"))
    composer = _composer(root)
    version = root.get("version", "")

    normalized = _serialize(root)
    return MusicXMLDocument(
        source=p,
        base_name=normalize_root_name(p),
        root_tag=tag,
        version=version,
        title=title or normalize_root_name(p),
        composer=composer,
        parts=parts,
        xml_bytes=normalized,
        warnings=warnings,
        unrecognized_root=unrecognized,
    )


def _read_mxl(path: Path, raw: bytes) -> tuple[bytes, str]:
    """按 ZIP 容器规范从 ``.mxl`` 中取出根 MusicXML，返回 ``(bytes, 内部文件名)``。"""
    import io

    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as e:
        raise MusicXMLError(f"{path.name} 是 ZIP 但无法打开：{e}") from e

    with zf:
        names = zf.namelist()
        container = _find_entry(names, "META-INF/container.xml")
        root_path: str | None = None
        if container is not None:
            try:
                croot = ET.fromstring(zf.read(container))
                for rf in croot.iter():
                    if local_name(rf.tag) == "rootfile":
                        root_path = rf.get("full-path")
                        break
            except ET.ParseError as e:
                raise MusicXMLError(f"{path.name}: META-INF/container.xml 解析失败：{e}") from e

        target: str | None = None
        if root_path:
            target = _find_entry(names, root_path)
            if target is None:
                raise MusicXMLError(
                    f"{path.name}: container.xml 指向的 rootfile 不存在：{root_path}"
                )
        if target is None:
            # 回退：取第一个非 META-INF 的 .xml/.musicxml
            cands = [
                n
                for n in names
                if not n.startswith("META-INF/")
                and n.lower().endswith((".xml", ".musicxml"))
                and not n.endswith("/")
            ]
            if not cands:
                raise MusicXMLError(
                    f"{path.name}: ZIP 内未找到 MusicXML 文件（entries={names[:10]}）"
                )
            if len(cands) > 1:
                log.warning("%s：ZIP 内有多个候选 XML，取 %s", path.name, cands[0])
            target = cands[0]

        return zf.read(target), target


def _find_entry(names: list[str], wanted: str) -> str | None:
    """在 ZIP entry 名中按大小写不敏感匹配路径。"""
    norm = wanted.replace("\\", "/").lstrip("./")
    for n in names:
        if n.replace("\\", "/").lstrip("./").lower() == norm.lower():
            return n
    return None


def _parse_xml(raw: bytes, path: Path) -> tuple[ET.Element, str]:
    """解析 XML 字节流，处理 BOM / UTF-16 / 非法声明。"""
    text: str
    for enc in ("utf-8-sig", "utf-16", "utf-8"):
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    else:
        raise MusicXMLError(f"{path.name} 无法解码为文本（尝试 utf-8/utf-16）")

    # 去掉 DOCTYPE（ElementTree 不做外部实体解析，但 <!DOCTYPE ...[]> 会干扰）
    cleaned = re.sub(r"<!DOCTYPE[^>]*(\[[^\]]*\])?>", "", text, count=1)
    cleaned = cleaned.lstrip("\ufeff")
    try:
        root = ET.fromstring(cleaned)
    except ET.ParseError as e:
        raise MusicXMLError(f"{path.name} 不是有效的 XML：{e}") from e
    return root, cleaned


def _serialize(root: ET.Element) -> bytes:
    """序列化为 UTF-8 带 XML 声明的字节流。"""
    body = ET.tostring(root, encoding="unicode")
    if not body.startswith("<?xml"):
        body = f"{_XML_DECL}\n{body}"
    return body.encode("utf-8")


# --------------------------------------------------------------------------- 元数据提取
def _composer(root: ET.Element) -> str:
    """尽力找到作曲者显示名。"""
    for ident in root.iter():
        if local_name(ident.tag) != "identification":
            continue
        for creator in ident:
            if local_name(creator.tag) == "creator" and (creator.get("type") or "") == "composer":
                return _text(creator)
    for creator in root.iter():
        if local_name(creator.tag) == "creator":
            return _text(creator)
    return ""


def _extract_parts(root: ET.Element) -> list[PartInfo]:
    """从 ``<part-list>`` 提取声部与 MIDI 乐器信息。

    注意 MusicXML 的 ``midi-channel`` 是 1–16，``midi-program`` 是 1–128；
    本函数统一归一为 ``midi_channel`` 1–16、``midi_program`` 0–127。
    """
    part_list = _first(root, "part-list")
    if part_list is None:
        return []

    parts: list[PartInfo] = []
    for sp in part_list:
        if local_name(sp.tag) != "score-part":
            continue
        pid = sp.get("id", "")
        info = PartInfo(id=pid, part_name=_text(_first(sp, "part-name")))

        score_instr = _first(sp, "score-instrument")
        if score_instr is not None:
            info.instrument_name = (
                _text(_first(score_instr, "instrument-name")) or score_instr.get("id", "")
            )

        midi_instr = _first(sp, "midi-instrument")
        if midi_instr is not None:
            ch = _text(_first(midi_instr, "midi-channel"))
            if ch.isdigit():
                info.midi_channel = int(ch)
            prog = _text(_first(midi_instr, "midi-program"))
            if prog.isdigit():
                # MusicXML 1–128 → GM 0–127；个别文件写 0，按 0 处理
                info.midi_program = max(0, int(prog) - 1)
            vol = _text(_first(midi_instr, "volume"))
            if vol:
                try:
                    info.volume = float(vol)
                except ValueError:
                    pass
            pan = _text(_first(midi_instr, "pan"))
            if pan.lstrip("-").isdigit():
                info.pan = int(pan)

        info.name = info.part_name or info.instrument_name or pid
        parts.append(info)

    _assign_default_channels(parts)
    return parts


def _assign_default_channels(parts: list[PartInfo]) -> None:
    """为缺少 ``midi-channel`` 的声部推断通道。

    规则（需求 §7.4）：跳过通道 10（打击乐）。按 (channel, program) 去重——
    多个声部共享同一通道+音色时视为同一乐器组，符合 MIDI 16 通道的现实约束。
    """
    used: set[tuple[int, int]] = set()
    next_ch = 1
    for p in parts:
        if p.midi_channel is not None:
            used.add((p.midi_channel, p.midi_program if p.midi_program is not None else -1))
            continue
        while next_ch == 10 or any(c == next_ch for c, _ in used):
            next_ch += 1
            if next_ch > 16:
                next_ch = 1  # 通道耗尽，允许复用（会在上层给警告）
        p.midi_channel = next_ch
        used.add((next_ch, p.midi_program if p.midi_program is not None else -1))
        next_ch += 1


def _timewise_to_partwise(root: ET.Element) -> ET.Element:
    """把 ``score-timewise`` 转换为 ``score-partwise``（需求 §5.1.3 步骤 2）。

    结构对照::

        timewise:  score-timewise / measure[@number] / part[@id] / (note|attributes|...)
        partwise:  score-partwise / part[@id] / measure[@number] / (note|attributes|...)

    即两者互为转置：外层与内层标签互换，``measure`` 的属性与 ``part`` 的 ``id``
    各自跟随其元素。声部顺序取 ``<part-list>`` 的顺序；若某小节缺少某声部，
    则补一个空 ``<measure>`` 占位，以保持小节对齐。
    """
    new_root = ET.Element(root.tag.replace("score-timewise", "score-partwise"))
    for key, value in root.attrib.items():
        new_root.set(key, value)

    part_list = _first(root, "part-list")
    if part_list is not None:
        new_root.append(part_list)

    # 声部顺序：优先 part-list，其次按出现顺序补齐
    order: list[str] = []
    if part_list is not None:
        order = [
            sp.get("id", "")
            for sp in part_list
            if local_name(sp.tag) == "score-part" and sp.get("id")
        ]

    measures: list[ET.Element] = [m for m in root if local_name(m.tag) == "measure"]
    for m in measures:
        for part in m:
            if local_name(part.tag) == "part":
                pid = part.get("id", "")
                if pid and pid not in order:
                    order.append(pid)

    # 建立 part id -> {measure_index: part 元素}
    by_part: dict[str, dict[int, ET.Element]] = {pid: {} for pid in order}
    for mi, m in enumerate(measures):
        for part in m:
            if local_name(part.tag) == "part":
                pid = part.get("id", "")
                if pid in by_part:
                    by_part[pid][mi] = part

    for pid in order:
        part_el = ET.SubElement(new_root, "part")
        part_el.set("id", pid)
        for mi, m in enumerate(measures):
            measure_el = ET.SubElement(part_el, "measure")
            for key, value in m.attrib.items():
                measure_el.set(key, value)
            if mi not in by_part[pid]:
                measure_el.set("implicit", "yes")  # 占位空小节，便于小节对齐
                continue
            for inner in by_part[pid][mi]:
                measure_el.append(inner)

    return new_root


# --------------------------------------------------------------------------- 便捷
def iter_source_files(folder: Path | str, *, recursive: bool = False) -> list[Path]:
    """列出目录下的 MusicXML 源文件（按名称排序）。"""
    d = Path(folder)
    if not d.is_dir():
        return []
    it = d.rglob("*") if recursive else d.glob("*")
    files = [p for p in it if p.is_file() and p.suffix.lower() in MUSICXML_SUFFIXES]
    return sorted(files, key=lambda p: p.name.lower())
