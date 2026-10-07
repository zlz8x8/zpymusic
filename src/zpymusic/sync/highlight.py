"""高亮逻辑（需求 §5.3 F2.6 / D5）。

职责：把"当前要高亮的元素 ID / 小节 ID"变成**可绘制的矩形列表**，
并处理两件事：

* **跟随小节（M7 起的主策略）**：播放时高亮的是**当前小节**，
  矩形宽度 = 小节宽度、高度 ≈ 该行谱表高度。小节矩形直接取自
  Verovio SVG 里的 ``<g class="measure" id="<小节 xml:id>">``（见
  :meth:`~zpymusic.sync.svg_geometry.SvgGeometry.measure_box`），
  因此与谱面排版天然一致，且每小节只重画一次（不再逐音符刷新）。
* **逐音符回退**：套件没有 ``measures`` 数据（旧 ``sync.json``）时，
  退回"高亮当前发音音符/和弦"，并把同一 system 内的多个音符合并成一个矩形。

本模块不依赖 Qt（只依赖 :mod:`svg_geometry` 里的纯数据类），因此可单测。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .svg_geometry import Box, SvgGeometry

__all__ = [
    "HighlightRect",
    "HighlightPlan",
    "SystemGeometryIndex",
    "build_css",
    "CSS_VARS",
    "MEASURE_PAD",
]

_REND_SUFFIX_RE = re.compile(r"-rend\d*$")

#: 小节高亮框向外扩多少 px（让框不贴着谱线与小节线，观感更清楚）
MEASURE_PAD = 2.0

#: 注入到 WebEngine 页面的 CSS 变量名（与 `highlight.css` 保持一致）
CSS_VARS = {
    "color": "--zpy-hl-color",
    "opacity": "--zpy-hl-alpha",
    "class": "zpy-hl",
    "measure_class": "zpy-hl-measure",
}


@dataclass(frozen=True)
class HighlightRect:
    """一个要高亮的矩形（system 局部坐标，px）。"""

    system: int
    element_id: str
    box: Box

    def padded(self, pad: float) -> "HighlightRect":
        return HighlightRect(self.system, self.element_id, self.box.expand(pad))


@dataclass
class HighlightPlan:
    """一次高亮的完整绘制计划。

    Attributes:
        rects: 要高亮的矩形（``element_id`` 的含义由 :attr:`measure_id` 决定：
            为空时是元素 ID，否则是**小节 ID**）。
        ids: 当前正在发声的元素 ID（状态栏/校准用；小节模式下仅作参考）。
        measure_id: 非空表示"跟随小节"模式 —— 该小节就是当前高亮对象，
            ``rects`` 里 ``box`` 为空的项需要由视图按小节几何补全
            （懒加载时该行的 SVG 可能还没解析）。
    """

    rects: tuple[HighlightRect, ...] = ()
    ids: tuple[str, ...] = ()
    measure_id: str = ""

    @property
    def empty(self) -> bool:
        return not self.rects

    @property
    def is_measure(self) -> bool:
        return bool(self.measure_id)

    def by_system(self) -> dict[int, list[HighlightRect]]:
        out: dict[int, list[HighlightRect]] = {}
        for r in self.rects:
            out.setdefault(r.system, []).append(r)
        return out

    @property
    def first_system(self) -> int:
        return min((r.system for r in self.rects), default=0)


def build_css(color: str = "#FF8C00", opacity: float = 0.35) -> str:
    """生成注入 WebEngine 的 CSS（高亮颜色与透明度可调，F2.6）。"""
    cls = CSS_VARS["class"]
    mcls = CSS_VARS["measure_class"]
    return f"""
/* zpyMusic 高亮样式（由设置动态注入） */
:root {{
  --zpy-hl-color: {color};
  --zpy-hl-alpha: {max(0.0, min(1.0, opacity))};
}}
g.note.{cls}, g.chord.{cls}, g.rest.{cls}, g.mRest.{cls} {{
  fill: var(--zpy-hl-color) !important;
  color: var(--zpy-hl-color) !important;
  opacity: var(--zpy-hl-alpha);
}}
g.chord.{cls} > g.note {{
  fill: var(--zpy-hl-color) !important;
  color: var(--zpy-hl-color) !important;
}}
/* 小节高亮：一个盖住整个小节的半透明矩形（不遮挡谱面、不拦截鼠标） */
rect.{mcls} {{
  fill: var(--zpy-hl-color) !important;
  stroke: var(--zpy-hl-color) !important;
  stroke-width: 1;
  opacity: var(--zpy-hl-alpha);
  pointer-events: none;
}}
/* 高亮状态的流畅过渡 */
g.note, g.chord {{
  transition: fill 60ms linear, color 60ms linear, opacity 60ms linear;
}}
"""


class SystemGeometryIndex:
    """按 system 索引的元素几何，供高亮与"点击跳转"使用。

    数据来自 ``sync.json.systems[].note_ids`` 与每个 SVG 的
    :class:`~zpymusic.sync.svg_geometry.SvgGeometry`。
    """

    def __init__(self) -> None:
        self._by_system: dict[int, SvgGeometry] = {}
        self._files: dict[int, str] = {}
        self._box_cache: dict[tuple[int, str], Box | None] = {}

    # ------------------------------------------------------------------ 装载
    def set_file(self, system: int, rel_path: str) -> None:
        self._files[system] = rel_path

    def file_of(self, system: int) -> str:
        return self._files.get(system, "")

    def has(self, system: int) -> bool:
        return system in self._by_system

    @property
    def loaded_systems(self) -> list[int]:
        return sorted(self._by_system)

    def put(self, system: int, geom: SvgGeometry) -> None:
        self._by_system[system] = geom
        # 该 system 的旧缓存全部失效
        for key in [k for k in self._box_cache if k[0] == system]:
            del self._box_cache[key]

    def geometry(self, system: int) -> SvgGeometry | None:
        return self._by_system.get(system)

    # ------------------------------------------------------------------ 查询
    def box_of(self, system: int, element_id: str, *, resolve_chord: bool = True) -> Box | None:
        """取元素在指定 system 内的矩形。

        Args:
            resolve_chord: True 时，若元素本身没有几何（例如它是 ``chord`` 的子音符），
                则向上找所属和弦/音符组；反之亦然（给和弦则合并其成员）。
        """
        key = (system, element_id)
        if key in self._box_cache:
            return self._box_cache[key]

        geom = self._by_system.get(system)
        if geom is None:
            self._box_cache[key] = None
            return None

        box = geom.box_of(element_id)
        if box is None and resolve_chord:
            # 尝试 base id（去掉 -rendN）
            base = _REND_SUFFIX_RE.sub("", element_id)
            box = geom.box_of(base)
        self._box_cache[key] = box
        return box

    def merged_box(self, system: int, element_ids: Iterable[str]) -> Box | None:
        """把多个元素的矩形合并成一个（用于"整个和弦一起高亮"）。"""
        out: Box | None = None
        for eid in element_ids:
            b = self.box_of(system, eid)
            if b is None:
                continue
            out = b if out is None else out.union(b)
        return out

    # ------------------------------------------------------------------ 小节
    def measure_box(self, system: int, measure_id: str) -> Box | None:
        """取某小节在指定 system 内的矩形（宽度 = 小节宽度，高度 ≈ 谱表高度）。

        只有当该 system 的 SVG 已经解析过（挂载过）时才有数据；
        未挂载时返回 ``None``，由视图挂载后再解析 —— 见
        :meth:`NativeScoreView._apply_highlight`。
        """
        geom = self._by_system.get(system)
        if geom is None:
            return None
        return geom.measure_box(measure_id)

    def measure_plan(
        self,
        systems: Sequence[int],
        measure_id: str,
        *,
        ids: Sequence[str] = (),
        pad: float = MEASURE_PAD,
    ) -> HighlightPlan:
        """生成"高亮当前小节"的计划（每个含该小节的 system 一个矩形）。

        已解析出几何的 system 直接带上矩形；尚未解析的留空框，
        由视图在挂载该行后自行补全（懒加载/跳转时常见）。
        """
        rects: list[HighlightRect] = []
        for system in systems:
            box = self.measure_box(system, measure_id)
            rects.append(
                HighlightRect(
                    system,
                    measure_id,
                    box.expand(pad) if box is not None else Box(0.0, 0.0, 0.0, 0.0),
                )
            )
        return HighlightPlan(rects=tuple(rects), ids=tuple(ids), measure_id=measure_id)

    def plan(
        self,
        system_of: dict[str, int],
        element_ids: Sequence[str],
        *,
        pad: float = 1.5,
        merge_same_system: bool = True,
    ) -> HighlightPlan:
        """算出"要高亮哪些矩形"。

        Args:
            system_of: ``{元素ID: system 序号}``（来自 ``sync.json`` 的 systems[].note_ids）。
            element_ids: 当前要高亮的元素 ID。
            pad: 每个矩形向外扩多少 px（让高亮更明显，不遮盖符头细节）。
            merge_same_system: 同一 system 内的多个元素合并为一个矩形
                （"高亮整个和弦/整个小节"的观感更整齐）。
        """
        rects: list[HighlightRect] = []
        seen: set[str] = set()
        for eid in element_ids:
            if eid in seen:
                continue
            seen.add(eid)
            system = system_of.get(eid)
            if system is None:
                base = _REND_SUFFIX_RE.sub("", eid)
                system = system_of.get(base)
            if system is None:
                continue
            box = self.box_of(system, eid)
            if box is None or box.empty:
                continue
            rects.append(HighlightRect(system, eid, box.expand(pad)))

        if merge_same_system and len(rects) > 1:
            grouped: dict[int, list[HighlightRect]] = {}
            for r in rects:
                grouped.setdefault(r.system, []).append(r)
            merged: list[HighlightRect] = []
            for system, group in sorted(grouped.items()):
                box = group[0].box
                for r in group[1:]:
                    box = box.union(r.box)
                merged.append(
                    HighlightRect(system, "+".join(r.element_id for r in group), box)
                )
            rects = merged

        return HighlightPlan(rects=tuple(rects), ids=tuple(seen))

    # ------------------------------------------------------------------ 反查
    def element_at(self, system: int, x: float, y: float) -> str:
        """命中测试：返回 (x, y) 处最合适的可高亮元素 ID（用于点击谱面跳转）。

        优先返回面积最小的命中元素（符头比整个 system 更精确）。
        小节容器（``g.measure``）虽然也有矩形，但**不参与**命中 ——
        否则点在谱表空白处会返回小节 ID，点击跳转就失效了。
        """
        geom = self._by_system.get(system)
        if geom is None:
            return ""
        best: tuple[float, str] | None = None
        for eid, eg in geom.elements.items():
            if eg.is_measure:
                continue
            if not eg.box.empty and eg.box.x <= x <= eg.box.x + eg.box.w and eg.box.y <= y <= eg.box.y + eg.box.h:
                area = eg.box.w * eg.box.h
                if best is None or area < best[0]:
                    best = (area, eid)
        if best is not None:
            return best[1]
        # 退一步：只按 x 找最近的音符（点击谱线空白处也能定位）
        nearest: tuple[float, str] | None = None
        for eid, eg in geom.elements.items():
            if not eg.is_note or eg.box.empty:
                continue
            d = abs((eg.box.x + eg.box.w / 2) - x)
            if nearest is None or d < nearest[0]:
                nearest = (d, eid)
        return nearest[1] if nearest and nearest[0] <= 40 else ""
