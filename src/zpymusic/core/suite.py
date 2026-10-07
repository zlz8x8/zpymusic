"""套件（suite）路径、命名、发现与完整性校验（需求 §5.1.1）。

目录布局（相对套件根，如 ``staff/suites``）::

    <base>/
      <base>.musicxml      规范化后的 MusicXML（权威源）
      <base>.mxl           原始压缩源（若源是 .mxl，仅用于溯源）
      <base>.mid           Verovio 导出
      <base>.mp3           FluidSynth + ffmpeg
      <base>.wav           可选中间产物
      <base>.sync.json     时间轴 + 元数据
      svg/sys-0001.svg …   每个 system（一行谱）一个文件

说明：文档 §5.1.1 初稿把 SVG 写成单文件 ``<base>.svg``。M0 实测后改为
``svg/`` 子目录 + 每行一个文件（见 §5.1.2 与附录 A 的布局试验）：大曲目
（four-seasons 有 276 个 system）必须懒加载，否则单个十几万像素高的 SVG
既慢又占内存。文档已同步更新。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from ..common.errors import SuiteError
from ..common.log import get_logger
from .sync_model import SyncDoc

log = get_logger(__name__)

__all__ = ["Suite", "SuiteLayout", "OverwritePolicy", "svg_system_name"]


def svg_system_name(index: int) -> str:
    """system SVG 的文件名（固定 4 位序号，便于按名排序）。"""
    return f"sys-{index:04d}.svg"


class OverwritePolicy:
    """覆盖策略常量（需求 F1.10）。"""

    SKIP = "skip"
    OVERWRITE = "overwrite"
    NEWDIR = "newdir"

    ALL = (SKIP, OVERWRITE, NEWDIR)


@dataclass
class SuiteLayout:
    """套件内各产物的路径计算。"""

    root: Path
    """套件根目录，如 ``staff/suites``。"""
    base: str
    """主名（不含扩展名）。"""

    @property
    def dir(self) -> Path:
        return self.root / self.base

    def file(self, suffix: str) -> Path:
        return self.dir / f"{self.base}{suffix}"

    @property
    def musicxml(self) -> Path:
        return self.file(".musicxml")

    @property
    def mxl(self) -> Path:
        return self.file(".mxl")

    @property
    def midi(self) -> Path:
        return self.file(".mid")

    @property
    def mp3(self) -> Path:
        return self.file(".mp3")

    @property
    def wav(self) -> Path:
        return self.file(".wav")

    @property
    def sync(self) -> Path:
        return self.file(".sync.json")

    @property
    def mei(self) -> Path:
        """可选：本工具的 MEI 旁路文件，供 svg → musicxml 使用（需求 §5.4.1）。"""
        return self.file(".mei")

    @property
    def svg_dir(self) -> Path:
        return self.dir / "svg"

    def svg_system(self, index: int) -> Path:
        return self.svg_dir / svg_system_name(index)

    def svg_rel(self, index: int) -> str:
        """相对套件目录的 SVG 路径（写进 sync.json，保持可移植）。"""
        return f"svg/{svg_system_name(index)}"

    def existing(self) -> list[Path]:
        """返回套件目录中已存在的产物（不含目录本身）。"""
        if not self.dir.is_dir():
            return []
        out: list[Path] = []
        for p in sorted(self.dir.iterdir()):
            if p.is_file():
                out.append(p)
            elif p.is_dir() and p.name == "svg":
                out.extend(sorted(p.glob("*.svg")))
        return out


@dataclass
class Suite:
    """一个已存在的套件。"""

    layout: SuiteLayout
    sync: SyncDoc | None = None
    warnings: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ 发现
    @property
    def base(self) -> str:
        return self.layout.base

    @property
    def dir(self) -> Path:
        return self.layout.dir

    @property
    def has_musicxml(self) -> bool:
        return self.layout.musicxml.is_file() or self.layout.mxl.is_file()

    @property
    def has_svg(self) -> bool:
        d = self.layout.svg_dir
        return d.is_dir() and any(d.glob("*.svg"))

    @property
    def has_midi(self) -> bool:
        return self.layout.midi.is_file()

    @property
    def has_mp3(self) -> bool:
        return self.layout.mp3.is_file()

    @property
    def has_sync(self) -> bool:
        return self.layout.sync.is_file()

    @property
    def audio_files(self) -> list[Path]:
        """可用于播放的音频（优先 MP3，其次 MIDI）。"""
        out: list[Path] = []
        for p in (self.layout.mp3, self.layout.midi, self.layout.wav):
            if p.is_file():
                out.append(p)
        return out

    def load_sync(self) -> SyncDoc:
        """读取 ``sync.json``（带缓存）。

        Raises:
            SuiteError: 文件缺失或 schema 不受支持。
        """
        if self.sync is not None:
            return self.sync
        if not self.has_sync:
            raise SuiteError(f"套件 {self.base} 缺少 {self.layout.sync.name}，无法同步高亮")
        self.sync = SyncDoc.load(self.layout.sync)
        return self.sync

    # ------------------------------------------------------------------ 校验
    def validate(self, *, strict: bool = False) -> list[str]:
        """返回缺失项清单（需求 §7.2 的 ``suite.validate()``）。

        Args:
            strict: True 时把"缺少 MP3"也视为问题。
        """
        problems: list[str] = []
        if not self.has_musicxml:
            problems.append(f"缺少 MusicXML（{self.layout.musicxml.name} / {self.layout.mxl.name}）")
        if not self.has_svg:
            problems.append("缺少 SVG（svg/ 目录为空）")
        if not self.has_midi:
            problems.append(f"缺少 MIDI（{self.layout.midi.name}）")
        if not self.has_sync:
            problems.append(f"缺少同步数据（{self.layout.sync.name}）")
        elif self.has_svg:
            # 交叉校验：sync 里记录的 svg 文件是否都在
            try:
                doc = self.load_sync()
            except SuiteError as e:
                problems.append(str(e))
            else:
                missing = [f for f in doc.svg_files if not (self.dir / f).is_file()]
                if missing:
                    problems.append(f"sync.json 引用了 {len(missing)} 个不存在的 SVG（如 {missing[0]}）")
        if strict and not self.has_mp3:
            problems.append(f"缺少 MP3（{self.layout.mp3.name}）")
        return problems

    def is_complete(self, *, strict: bool = False) -> bool:
        return not self.validate(strict=strict)


# --------------------------------------------------------------------------- 发现 / 创建
def discover_suites(root: Path | str) -> list[Suite]:
    """扫描套件根目录，返回其中包含 ``*.sync.json`` 或音频的套件。"""
    r = Path(root)
    if not r.is_dir():
        return []
    suites: list[Suite] = []
    for d in sorted(r.iterdir(), key=lambda p: p.name.lower()):
        if not d.is_dir():
            continue
        syncs = sorted(d.glob("*.sync.json"))
        if not syncs:
            continue
        base = syncs[0].name[: -len(".sync.json")]
        suites.append(Suite(SuiteLayout(root=r, base=base)))
    return suites


def make_layout(root: Path | str, base: str) -> SuiteLayout:
    """构造套件布局。"""
    if not base or base in {".", ".."}:
        raise SuiteError(f"非法的套件主名：{base!r}")
    if any(ch in base for ch in '<>:"/\\|?*'):
        raise SuiteError(f"套件主名包含非法字符：{base!r}")
    return SuiteLayout(root=Path(root), base=base)


def resolve_target(
    root: Path | str,
    base: str,
    policy: str,
    *,
    incoming: Iterable[Path] = (),
) -> tuple[SuiteLayout, list[str]]:
    """按覆盖策略决定最终写入的套件目录。

    Args:
        root: 套件根目录。
        base: 期望主名。
        policy: 见 :class:`OverwritePolicy`。
        incoming: 本次将要生成的文件（用于判断冲突）。

    Returns:
        ``(layout, notes)``；``notes`` 是给用户看的说明（如"已跳过 / 已改用 xxx-2"）。

    Raises:
        SuiteError: 策略非法。
    """
    if policy not in OverwritePolicy.ALL:
        raise SuiteError(f"未知的覆盖策略：{policy!r}（可选 {OverwritePolicy.ALL}）")

    layout = make_layout(root, base)
    notes: list[str] = []
    if not layout.dir.exists():
        return layout, notes

    existing = {p.name for p in layout.existing()}
    clash = sorted({p.name for p in incoming} & existing)
    if not clash:
        return layout, notes

    if policy == OverwritePolicy.OVERWRITE:
        notes.append(f"将覆盖套件 {base} 中已存在的 {len(clash)} 个文件：" + ", ".join(clash[:4]))
        return layout, notes
    if policy == OverwritePolicy.SKIP:
        notes.append(f"套件 {base} 已存在 {len(clash)} 个同名文件，按 skip 策略跳过")
        return layout, notes

    # newdir：找一个不冲突的序号目录
    for i in range(2, 1000):
        cand = make_layout(root, f"{base}-{i}")
        if not cand.dir.exists():
            notes.append(f"套件 {base} 已存在，改用 {cand.base}")
            return cand, notes
    raise SuiteError(f"无法为 {base} 找到可用的新目录名（已试到 {base}-999）")


def copy_source(source: Path, layout: SuiteLayout) -> tuple[Path, Path | None]:
    """把源文件复制进套件目录（需求 F1.7）。

    Returns:
        ``(规范化 XML 路径, 原始 .mxl 路径或 None)``。
    """
    layout.dir.mkdir(parents=True, exist_ok=True)
    src = Path(source)
    if src.suffix.lower() == ".mxl":
        dest = layout.mxl
        shutil.copy2(src, dest)
        return layout.musicxml, dest
    shutil.copy2(src, layout.musicxml)
    return layout.musicxml, None
