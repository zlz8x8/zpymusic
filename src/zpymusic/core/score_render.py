"""Verovio 渲染：MusicXML → SVG（每 system 一个文件）+ MIDI + 时间轴（需求 §5.2 / D1 / D2）。

核心实测结论（M0，见 docs/requirements.md 附录 A）：

1. ``getTimesForElement()`` **不可用于全曲**：four-seasons 有 29324 个音符 ID，
   逐个调用耗时 **132 s**，且本机 verovio 6.3.0 对绝大多数 ID 返回全 0。
   → 因此时间轴改为**只用 timemap 的 ``on``/``off`` 配对**构建，全曲 **0.17 s**。
2. 该方案的正确性用 ``getElementsAtTime(ms)`` 交叉验证通过（4/4 抽样一致）。
3. ``breaks="smart"`` + ``pageHeight=400`` + ``adjustPageHeight=True``
   得到"一行一个紧凑 SVG"。

本模块**不导入 Qt**。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterable

from ..common.config import RenderConfig
from ..common.errors import RenderError, TaskCancelled
from ..common.log import get_logger

log = get_logger(__name__)

__all__ = [
    "TimelineNote",
    "SystemInfo",
    "MeasureInfo",
    "RenderedScore",
    "render_score",
    "render_preview_svgs",
    "render_svgs",
    "svg_size",
    "build_notes_from_timemap",
    "verovio_version",
    "verovio_resource_path",
    "PREVIEW_MAX_SYSTEMS",
]

_DIM_RE = re.compile(r'<svg[^>]*?width="([^"]+)"[^>]*?height="([^"]+)"')
_REND_SUFFIX_RE = re.compile(r"-rend\d*$")

#: 「源文件预览」最多渲染多少行。
#: 大曲目（four-seasons 有 276 行）全渲染要十几秒，预览没必要 —— 12 行足够看清排版，
#: 且 canon 这类小曲目会**全部**渲染出来（实测只渲染 3 行 ≈ 130 ms）。
PREVIEW_MAX_SYSTEMS = 12

ProgressFn = Callable[[str, int, int], None]
"""进度回调：``(阶段说明, 已完成, 总数)``。"""
CancelFn = Callable[[], bool]
"""取消检查：返回 True 表示用户已请求取消。"""


# --------------------------------------------------------------------------- 数据模型
@dataclass
class TimelineNote:
    """一个可发声 / 可高亮的音符事件。"""

    id: str
    onset_ms: int
    dur_ms: int
    qstamp: float = 0.0
    is_rest: bool = False
    measure_id: str = ""
    rendered: bool = False
    """True 表示该 ID 原本是 Verovio 展开（连音 / 反复）产生的 ``*-rendN`` 元素，
    已由 :func:`_build_notated_map` / :func:`_merge_and_finalize` 归并回原始 ID。"""

    @property
    def end_ms(self) -> int:
        return self.onset_ms + self.dur_ms

    @property
    def visual_id(self) -> str:
        """用于在 SVG 中定位的 ID（去掉 ``-rendN`` 后缀）。"""
        return _REND_SUFFIX_RE.sub("", self.id) if self.rendered else self.id

    def to_dict(self) -> dict:
        d: dict = {"id": self.id, "onset_ms": self.onset_ms, "dur_ms": self.dur_ms}
        if self.qstamp:
            d["qstamp"] = round(self.qstamp, 6)
        if self.is_rest:
            d["is_rest"] = True
        if self.measure_id:
            d["measure"] = self.measure_id
        if self.rendered:
            d["rendered"] = True
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TimelineNote":
        nid = str(d["id"])
        return cls(
            id=nid,
            onset_ms=int(d["onset_ms"]),
            dur_ms=int(d["dur_ms"]),
            qstamp=float(d.get("qstamp", 0.0)),
            is_rest=bool(d.get("is_rest", False)),
            measure_id=str(d.get("measure", "")),
            rendered=bool(d.get("rendered", False)) or nid != _REND_SUFFIX_RE.sub("", nid),
        )


@dataclass
class SystemInfo:
    """一个 system（一行谱）= 一个 SVG 文件。"""

    index: int
    """1-based，与 `pageNo` 一致。"""
    file: str
    width: int = 0
    height: int = 0
    first_id: str = ""
    last_id: str = ""
    note_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "file": self.file,
            "width": self.width,
            "height": self.height,
            "first_id": self.first_id,
            "last_id": self.last_id,
            "note_ids": self.note_ids,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SystemInfo":
        return cls(
            index=int(d["index"]),
            file=str(d["file"]),
            width=int(d.get("width", 0)),
            height=int(d.get("height", 0)),
            first_id=str(d.get("first_id", "")),
            last_id=str(d.get("last_id", "")),
            note_ids=[str(x) for x in d.get("note_ids", [])],
        )


@dataclass
class MeasureInfo:
    """一个小节（用于显示"当前小节"与跳转）。"""

    id: str
    onset_ms: int
    qstamp: float = 0.0

    def to_dict(self) -> dict:
        return {"id": self.id, "onset_ms": self.onset_ms, "qstamp": round(self.qstamp, 6)}

    @classmethod
    def from_dict(cls, d: dict) -> "MeasureInfo":
        return cls(
            id=str(d["id"]),
            onset_ms=int(d["onset_ms"]),
            qstamp=float(d.get("qstamp", 0.0)),
        )


@dataclass
class RenderedScore:
    """一次 Verovio 渲染的全部产物。"""

    notes: list[TimelineNote] = field(default_factory=list)
    systems: list[SystemInfo] = field(default_factory=list)
    measures: list[MeasureInfo] = field(default_factory=list)
    midi_bytes: bytes = b""
    duration_ms: int = 0
    initial_bpm: float | None = None
    engine_version: str = ""
    warnings: list[str] = field(default_factory=list)
    tie_merged: int = 0
    """因连音 / 延音而归并掉的展开片段数（自检用）。"""

    @property
    def note_count(self) -> int:
        """时间轴事件数（同一音符被重复演奏时会多于元素数）。"""
        return len(self.notes)

    @property
    def element_count(self) -> int:
        """不同的可高亮元素数（去重后）。"""
        return len({n.visual_id for n in self.notes})

    @property
    def page_count(self) -> int:
        return len(self.systems)

    def system_of(self, element_id: str) -> int:
        """返回元素所属 system 序号（0 表示未找到）。"""
        base = _REND_SUFFIX_RE.sub("", element_id)
        for s in self.systems:
            if element_id in s.note_ids or base in s.note_ids:
                return s.index
        return 0


# --------------------------------------------------------------------------- 时间轴构建
def build_notes_from_timemap(
    timemap: Iterable[dict],
    *,
    include_rests: bool = False,
    notated_of: dict[str, str] | None = None,
) -> tuple[list[TimelineNote], list[MeasureInfo], int]:
    """单遍配对 timemap 的 ``on`` / ``off``，构建音符时间轴。

    Verovio timemap 条目形如::

        {"measureOn": "<id>", "on": ["<id>", ...], "off": [...],
         "qstamp": 0.5, "tstamp": 300, "tempo": 100}

    ``tstamp`` 为**毫秒**（实测：qstamp 212 对应 tstamp 127200，即 600 ms/四分音符 = 100 BPM）。

    **连音（tie）的处理**：Verovio 会把跨小节的连音展开成 ``<base>-rend2`` 之类的
    新元素 ID。若不处理，它们会作为"幽灵音符"混进时间轴——实测 canon 里有 29 个
    这样的 ID，既在 SVG 里找不到，又与 ``<base>`` 重复计数。因此：

    1. 用 ``notated_of``（来自 ``toolkit.getNotatedIdForElement``）把所有 ID 规范化
       回原始 ``xml:id``；
    2. 同一规范化 ID 的**时间区间互相重叠**时才合并（这样连音的多个片段会合成
       一个事件，而重复演奏的同一个音符因区间不重叠仍保持为两个事件）。

    Args:
        timemap: ``toolkit.renderToTimemap(...)`` 的返回值。
        include_rests: 是否把休止符也作为事件写出（需求 §5.1.4）。
        notated_of: ``{元素ID: 原始xml:id}`` 映射，用于解析展开 ID。

    Returns:
        ``(notes, measures, duration_ms, merged_count)``；``notes`` 按 ``onset_ms`` 升序，
        ``merged_count`` 是归并掉的展开片段数（自检用）。
    """
    entries = list(timemap)
    entries.sort(key=lambda e: (int(e.get("tstamp", 0)),))
    nmap = notated_of or {}

    def canon(eid: str) -> str:
        return nmap.get(eid) or _REND_SUFFIX_RE.sub("", eid)

    open_at: dict[str, int] = {}  # 原始 ID -> 起始毫秒
    qstamp_at: dict[str, float] = {}
    measure_of: dict[str, str] = {}
    current_measure = ""
    raw: list[tuple[str, int, int, float, str]] = []  # (canon_id, onset, off, qstamp, measure)
    measures: list[MeasureInfo] = []
    total = 0

    def close(eid: str, t: int, q: float) -> None:
        start = open_at.pop(eid)
        q0 = qstamp_at.pop(eid, q)
        raw.append((canon(eid), start, t, q0, measure_of.pop(eid, current_measure)))

    for entry in entries:
        t = int(entry.get("tstamp", 0))
        q = float(entry.get("qstamp", 0.0))
        total = max(total, t)

        mid = entry.get("measureOn")
        if mid:
            current_measure = str(mid)
            measures.append(MeasureInfo(id=current_measure, onset_ms=t, qstamp=q))

        # 1) 先处理 off：结束此前 on 的同一元素
        for eid in entry.get("off") or []:
            key = _pair_key(str(eid), open_at)
            if key is not None:
                close(key, t, q)

        # 2) 再处理 on：开始新的音符（同一 ID 重复 on 时先收口前一次）
        for eid in entry.get("on") or []:
            eid = str(eid)
            if eid in open_at:
                close(eid, t, q)
            open_at[eid] = t
            qstamp_at[eid] = q
            measure_of[eid] = current_measure

    # 收尾：仍在发声的音符延续到全曲结束
    for eid in list(open_at):
        close(eid, total, qstamp_at.get(eid, 0.0))

    # 注意：不要用 _merge_and_finalize 的返回值覆盖 measures —— 小节信息在上面的
    # 循环里已经累积好了（曾经的 bug：被空列表占位符覆盖，导致 sync.json 里小节数为 0）。
    notes, total, merged = _merge_and_finalize(raw)
    measures.sort(key=lambda m: m.onset_ms)
    return notes, measures, total, merged


def _merge_and_finalize(
    raw: list[tuple[str, int, int, float, str]],
) -> tuple[list[TimelineNote], int, int]:
    """按规范化 ID 归并区间重叠的片段。

    * 区间**重叠**（含相接）→ 合并成一个事件（连音的多个片段）；
    * 区间**不重叠** → 保留为两个事件（同一音符被重复演奏）。

    Returns:
        ``(notes, total_ms, merged_count)``。
    """
    grouped: dict[str, list[tuple[int, int, float, str]]] = {}
    order: list[str] = []
    for nid, start, off, q, measure in raw:
        if nid not in grouped:
            grouped[nid] = []
            order.append(nid)
        grouped[nid].append((start, off, q, measure))

    notes: list[TimelineNote] = []
    merged = 0
    total = 0
    for nid in order:
        spans = sorted(grouped[nid], key=lambda s: (s[0], s[1]))
        cur_start, cur_end, cur_q, cur_measure = spans[0]
        total = max(total, cur_end)
        for start, off, q, measure in spans[1:]:
            total = max(total, off)
            if start <= cur_end:  # 重叠或相接 → 合并（连音 / 延音）
                cur_end = max(cur_end, off)
                merged += 1
                continue
            notes.append(
                TimelineNote(
                    id=nid,
                    onset_ms=cur_start,
                    dur_ms=max(0, cur_end - cur_start),
                    qstamp=cur_q,
                    measure_id=cur_measure,
                )
            )
            cur_start, cur_end, cur_q, cur_measure = start, off, q, measure
        notes.append(
            TimelineNote(
                id=nid,
                onset_ms=cur_start,
                dur_ms=max(0, cur_end - cur_start),
                qstamp=cur_q,
                measure_id=cur_measure,
            )
        )

    notes.sort(key=lambda n: (n.onset_ms, n.id))
    return notes, total, merged


def _pair_key(eid: str, open_at: dict[str, int]) -> str | None:
    """把 ``off`` 里的元素 ID 对应到某个已 ``on`` 的 ID。

    连音 / 反复会让 Verovio 用 ``<base>-rendN`` 作为展开后的 ID，而 ``on`` 里
    可能是 base 也可能是 rendN。优先精确匹配，否则按去后缀的形式配对。
    """
    if eid in open_at:
        return eid
    base = _REND_SUFFIX_RE.sub("", eid)
    if base in open_at:
        return base
    for key in open_at:
        if _REND_SUFFIX_RE.sub("", key) == base:
            return key
    return None


# --------------------------------------------------------------------------- 主入口
def verovio_version() -> str:
    """返回 Verovio 版本号（去掉 ``[undefined]`` 之类后缀）。"""
    import verovio  # noqa: PLC0415

    return verovio.toolkit().getVersion().split("[")[0].strip()


def verovio_resource_path(resources: str | Path | None = None) -> str:
    """Verovio 自带的字形 / 字体资源目录（Bravura、Leipzig 等）。

    **为什么要显式指定**（M5 实测，verovio 6.3.0 / Windows）：只要主线程里先渲染过一次，
    之后在**工作线程**里新建 toolkit 就会找不到资源，``loadData()`` 直接失败并打印::

        [Error] Bravura font could not be loaded.
        [Error] The data cannot be loaded because the font resources are not available

    即"后台线程渲染"（GUI 里生成套件、源文件预览）整条链路会失败，而 CLI / 单元测试
    （主线程渲染）完全正常 —— 现象上极难定位。改成每个 toolkit 都显式
    ``setResourcePath()`` 之后，主线程与工作线程都稳定。

    Args:
        resources: 用户配置的覆盖路径（``AppConfig.paths.verovio_resources``）。

    Returns:
        存在且可用的资源目录；找不到时返回空串（调用方跳过设置，保持 Verovio 默认行为）。
    """
    if resources:
        p = Path(resources)
        if p.is_dir():
            return str(p)
    import verovio  # noqa: PLC0415

    data = Path(verovio.__file__).resolve().parent / "data"
    return str(data) if data.is_dir() else ""


def _prepare_toolkit(tk, resources: str | Path | None = None) -> None:  # noqa: ANN001
    """建立 toolkit 后立刻显式指定资源目录（见 :func:`verovio_resource_path`）。"""
    path = verovio_resource_path(resources)
    if path:
        tk.setResourcePath(path)


def render_score(
    xml_bytes: bytes,
    config: RenderConfig,
    *,
    progress: ProgressFn | None = None,
    cancelled: CancelFn | None = None,
    emit_svg: Callable[[int, str], None] | None = None,
    resources: str | Path | None = None,
) -> RenderedScore:
    """把（规范化后的）MusicXML 渲染成 SVG + MIDI + 时间轴。

    Args:
        xml_bytes: 规范化后的 MusicXML 字节流（``MusicXMLDocument.xml_bytes``）。
        config: 渲染参数。
        progress: 进度回调。
        cancelled: 取消检查回调；返回 True 时抛 ``TaskCancelled``。
        emit_svg: 每渲染完一个 system 调用 ``emit_svg(page_no, svg_text)``。
            为空时 SVG 不落盘也不保留（只用于取 MIDI / 时间轴的场景）。
        resources: Verovio 资源目录覆盖（``AppConfig.paths.verovio_resources``）。

    Raises:
        RenderError: 加载或渲染失败。
        TaskCancelled: 用户取消。
    """
    import base64  # noqa: PLC0415

    import verovio  # noqa: PLC0415

    def check_cancel() -> None:
        if cancelled is not None and cancelled():
            raise TaskCancelled("用户取消了渲染任务")

    tk = verovio.toolkit()
    _prepare_toolkit(tk, resources)
    opts = _verovio_options(config)
    if not tk.setOptions(opts):
        raise RenderError(f"Verovio 拒绝以下渲染选项：{opts}")

    version = tk.getVersion().split("[")[0].strip()
    if not tk.loadData(xml_bytes.decode("utf-8")):
        detail = tk.getLog().strip()
        raise RenderError(f"Verovio 无法加载该 MusicXML：{detail[:500]}")

    check_cancel()

    # renderToMIDI 必须先调用：时间查询类 API 依赖它（需求 §5.2 的顺序说明）
    try:
        midi_b64 = tk.renderToMIDI()
    except Exception as e:  # noqa: BLE001
        raise RenderError(f"MIDI 导出失败：{e}") from e
    midi_bytes = base64.b64decode(midi_b64) if midi_b64 else b""

    check_cancel()

    tm_opts = {
        "includeMeasures": True,
        "includeRests": bool(config.include_rests_in_sync),
    }
    try:
        timemap = tk.renderToTimemap(tm_opts)
    except Exception as e:  # noqa: BLE001
        raise RenderError(f"时间轴导出失败：{e}") from e
    if not isinstance(timemap, list):
        # 老版本返回 JSON 字符串
        try:
            timemap = json.loads(timemap)
        except (TypeError, json.JSONDecodeError) as e:
            raise RenderError(f"时间轴格式无法识别：{type(timemap).__name__}") from e

    notes, measures, duration_ms, tie_merged = build_notes_from_timemap(
        timemap,
        include_rests=config.include_rests_in_sync,
        notated_of=_build_notated_map(tk, timemap),
    )
    if not notes:
        raise RenderError("时间轴为空：该乐谱可能没有任何可播放内容")

    rendered = RenderedScore(
        notes=notes,
        measures=measures,
        midi_bytes=midi_bytes,
        duration_ms=duration_ms,
        initial_bpm=_initial_bpm(timemap),
        engine_version=version,
        tie_merged=tie_merged,
    )
    rendered.warnings.extend(_sanity_warnings(rendered, timemap))

    # --- SVG ---------------------------------------------------------------
    pages = tk.getPageCount()
    if config.max_systems and pages > config.max_systems:
        rendered.warnings.append(
            f"按配置只渲染前 {config.max_systems} 个 system（原共 {pages} 个）"
        )
        pages = config.max_systems
    if pages <= 0:
        raise RenderError("Verovio 未产生任何页面（getPageCount() == 0）")

    rendered.systems = _render_systems(
        tk, pages, config, rendered, progress=progress, cancelled=cancelled, emit_svg=emit_svg
    )
    return rendered


def _build_notated_map(tk, timemap: Iterable[dict]) -> dict[str, str]:
    """为 timemap 里出现的每个元素 ID 建立 ``{展开ID: 原始ID}`` 映射。

    Verovio 的 ``getNotatedIdForElement`` 专门用于把连音 / 反复展开产生的
    ``<base>-rendN`` 映射回原始 ``xml:id``。实测（canon）有 29 个这样的 ID，
    若不解析会变成"幽灵音符"。整曲只调用一次，成本可忽略。
    """
    ids: set[str] = set()
    for e in timemap:
        ids.update(str(i) for i in (e.get("on") or []))
        ids.update(str(i) for i in (e.get("off") or []))
    out: dict[str, str] = {}
    for eid in ids:
        try:
            base = tk.getNotatedIdForElement(eid)
        except Exception:  # noqa: BLE001 - 查不到就退回去后缀
            base = ""
        base = base or _REND_SUFFIX_RE.sub("", eid)
        if base != eid:
            out[eid] = base
    return out


def _verovio_options(config: RenderConfig) -> dict:
    opts: dict = {
        "scale": config.scale,
        "pageWidth": config.page_width,
        "footer": config.footer,
        "header": config.header,
        "svgAdditionalAttribute": list(config.svg_additional_attributes),
        "svgFormatRaw": False,
    }
    if config.single_page:
        opts["breaks"] = "none"
        opts["pageHeight"] = config.max_page_height
        opts["adjustPageHeight"] = True
    else:
        opts["breaks"] = config.breaks
        opts["pageHeight"] = min(config.page_height, config.max_page_height)
        opts["adjustPageHeight"] = bool(config.adjust_page_height)
    return opts


def _initial_bpm(timemap: Iterable[dict]) -> float | None:
    """timemap 中首个出现的 ``tempo`` 即初始速度。"""
    for e in timemap:
        t = e.get("tempo")
        if t:
            try:
                return float(t)
            except (TypeError, ValueError):
                return None
    return None


def _render_systems(
    tk,
    pages: int,
    config: RenderConfig,
    rendered: RenderedScore,
    *,
    progress: ProgressFn | None,
    cancelled: CancelFn | None,
    emit_svg: Callable[[int, str], None] | None,
) -> list[SystemInfo]:
    """逐 system 渲染 SVG，并用 ``getPageWithElement`` 建立 ID → system 映射。"""
    # 1) 先把每个音符归到它的 system
    page_of: dict[str, int] = {}
    for n in rendered.notes:
        if n.is_rest and not config.include_rests_in_sync:
            continue
        try:
            p = int(tk.getPageWithElement(n.id))
        except Exception:  # noqa: BLE001 - 个别展开 ID 可能查不到
            p = 0
        if p <= 0 and n.rendered:
            try:
                p = int(tk.getPageWithElement(n.visual_id))
            except Exception:  # noqa: BLE001
                p = 0
        page_of[n.visual_id] = p or 1

    buckets: dict[int, list[str]] = {p: [] for p in range(1, pages + 1)}
    for n in rendered.notes:
        p = page_of.get(n.visual_id, 1)
        if 1 <= p <= pages and n.visual_id not in buckets[p]:
            buckets[p].append(n.visual_id)

    # 2) 逐页渲染
    systems: list[SystemInfo] = []
    for p in range(1, pages + 1):
        if cancelled is not None and cancelled():
            raise TaskCancelled("用户取消了 SVG 渲染")
        try:
            svg = tk.renderToSVG(p)
        except Exception as e:  # noqa: BLE001
            rendered.warnings.append(f"第 {p} 个 system 渲染失败，已跳过：{e}")
            continue
        if not svg:
            rendered.warnings.append(f"第 {p} 个 system 渲染结果为空，已跳过")
            continue
        w, h = _svg_size(svg)
        ids = buckets.get(p, [])
        systems.append(
            SystemInfo(
                index=p,
                file="",  # 文件名由 suite 层决定
                width=w,
                height=h,
                first_id=ids[0] if ids else "",
                last_id=ids[-1] if ids else "",
                note_ids=ids,
            )
        )
        if emit_svg is not None:
            emit_svg(p, svg)
        if progress is not None:
            progress("渲染 SVG", p, pages)

    return systems


def render_preview_svgs(
    xml_bytes: bytes,
    config: RenderConfig,
    *,
    max_systems: int = PREVIEW_MAX_SYSTEMS,
    resources: str | Path | None = None,
) -> list[str]:
    """只渲染前若干行 SVG，供「源文件预览」用（需求 §5.4.2 F3.8）。

    与 :func:`render_svgs` 的唯一区别是**强制逐行排版**：即使配置里开了
    ``single_page``，预览也不该为一个大曲目生成几十 MB 的单页 SVG。

    Args:
        xml_bytes: 规范化后的 MusicXML 字节流（``MusicXMLDocument.xml_bytes``）。
        config: 渲染参数（用 ``AppConfig.render``）。
        max_systems: 最多渲染多少行；``<=0`` 表示不限制。
        resources: Verovio 资源目录覆盖（``AppConfig.paths.verovio_resources``）。

    Returns:
        每行一个 SVG 文本（顺序即谱面顺序）。

    Raises:
        RenderError: 加载或渲染失败（调用方应把消息显示给用户）。
    """
    return render_svgs(
        xml_bytes,
        replace(config, single_page=False),
        max_systems=max_systems,
        resources=resources,
    )


def render_svgs(
    xml_bytes: bytes,
    config: RenderConfig,
    *,
    max_systems: int = 0,
    resources: str | Path | None = None,
) -> list[str]:
    """只渲染 SVG（不导出 MIDI / 时间轴），排版由配置决定。

    ``musicxml → svg`` 转换用它（``single_page=True`` ⇒ 一个"连续纵向"的 ``.svg`` 文件）；
    ``pdf → svg`` 也从提取出的内嵌乐谱重新渲染。与 :func:`render_score` 相比：
    不导出 MIDI / 时间轴，也不要求"必须有音符"（空乐谱也能看版面）。

    Args:
        xml_bytes: 规范化后的 MusicXML 字节流。
        config: 渲染参数。
        max_systems: 最多渲染多少页/行；``<=0`` 表示不限制。
        resources: Verovio 资源目录覆盖。

    Raises:
        RenderError: 加载或渲染失败。
    """
    import verovio  # noqa: PLC0415

    tk = verovio.toolkit()
    _prepare_toolkit(tk, resources)
    opts = _verovio_options(config)
    if not tk.setOptions(opts):
        raise RenderError(f"Verovio 拒绝以下渲染选项：{opts}")
    if not tk.loadData(xml_bytes.decode("utf-8")):
        raise RenderError(f"Verovio 无法加载该 MusicXML：{tk.getLog().strip()[:300]}")

    pages = tk.getPageCount()
    if pages <= 0:
        raise RenderError("Verovio 未产生任何页面（getPageCount() == 0）")
    limit = pages if max_systems <= 0 else min(pages, max_systems)
    return [tk.renderToSVG(p) for p in range(1, limit + 1)]


def svg_size(svg: str) -> tuple[int, int]:
    """从 SVG 根元素解析 ``width``/``height``（形如 ``840px``）；失败返回 ``(0, 0)``。

    预览窗口要按真实尺寸建 :class:`~zpymusic.sync.score_view.SystemRef`，
    否则"适应宽度"会把 2100 px 宽的谱面当成 840 px 宽，右侧被裁掉。
    """
    m = _DIM_RE.search(svg)
    if not m:
        return 0, 0
    try:
        return int(re.sub(r"\D", "", m.group(1)) or 0), int(re.sub(r"\D", "", m.group(2)) or 0)
    except ValueError:
        return 0, 0


def _svg_size(svg: str) -> tuple[int, int]:
    """``svg_size`` 的历史名字（内部调用点保持不变）。"""
    return svg_size(svg)


# --------------------------------------------------------------------------- 自检
def _sanity_warnings(rendered: RenderedScore, timemap: list[dict]) -> list[str]:
    """时间轴自检（需求 §8.2 验收标准 2 的前置检查）。"""
    warns: list[str] = []
    on_events = sum(len(e.get("on") or []) for e in timemap)
    if on_events and rendered.note_count > on_events:
        warns.append(
            f"时间轴事件数（{rendered.note_count}）多于 timemap 的 on 事件数（{on_events}），"
            "可能存在未配对的 off"
        )
    if rendered.tie_merged:
        warns.append(
            f"已归并 {rendered.tie_merged} 个连音 / 延音展开片段（否则会成为找不到元素的幽灵音符）"
        )
    negative = sum(1 for n in rendered.notes if n.dur_ms <= 0)
    if negative:
        warns.append(f"有 {negative} 个事件时长为 0 或负值（可能是装饰音或数据异常）")
    return warns
