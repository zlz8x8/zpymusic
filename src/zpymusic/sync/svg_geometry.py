"""Verovio SVG 的元素几何解析（需求 §5.3 F2.6 的原生后端支撑）。

**为什么需要它**：M4 实测确认 `QSvgRenderer.boundsOnElement()` / `QGraphicsSvgItem.setElementId()`
在 Qt6 下**不可用** —— 对任意 ID 都返回整个 viewport 的矩形（840×188 的页面返回 699×1738），
且 `elementExists()` 对确实存在的 ID 返回 False。因此不依赖 QtWebEngine 的曲谱视图
必须自己算高亮框。

Verovio 的 SVG 有两条关键结构（M4 实测）：

1. **字形放在 `<defs>` 里，正文用 `<use>` 引用**，位置写在 `transform` 上::

       <defs><g id="E0A4-c3h20hk"><path d="M776 583c-62 -2 …"/></g></defs>
       …
       <g id="cqzw94h" class="note" data-pname="d" data-oct="4">
         <g class="notehead">
           <use xlink:href="#E0A4-c3h20hk" transform="translate(4857, 2150) scale(0.72, 0.72)"/>
         </g>
         <g id="pydebe9" class="stem"><path d="M4866 2178 L4866 2987" stroke-width="18"/></g>
       </g>

   所以 **`<use>` 本身只是一个点**，必须解引用到 `<defs>` 里的字形才能得到真实尺寸。

2. **坐标系是 Verovio 内部单位**，约 **100 单位 = 1 px**（根元素 `width="840px"`），
   统一乘 :data:`VEROVIO_UNITS_PER_PX` 换算。

**路径解析用 Qt 自己**：Verovio 的字形路径大量使用相对命令与隐式重复
（``c`` 后面跟多组坐标）。用正则抓数字对会把相对坐标当成绝对坐标，算出来的框会小得离谱
（实测只有 3.0×2.5 px，而真实符头约 12×13 px）。因此改用 `QPainterPath` +
``QPainterPathStroker``：Qt 的 SVG 路径解析器保证精确，并且能正确处理 H/V、弧线、
平滑曲线等所有命令。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from ..common.log import get_logger

log = get_logger(__name__)

__all__ = [
    "Box",
    "ElementGeometry",
    "SvgGeometry",
    "parse_svg",
    "verovio_units_per_px",
    "flatten_svg",
    "inline_stroke_attributes",
    "HIGHLIGHTABLE_CLASSES",
    "MEASURE_CLASS",
    "VEROVIO_UNITS_PER_PX",
]

#: Verovio 内部单位 → px 的**默认**换算系数（找不到 viewBox 时的回退值）
VEROVIO_UNITS_PER_PX = 0.01

#: 这些 class 的元素可以被高亮（Verovio 把 MEI 元素名写成 class）
HIGHLIGHTABLE_CLASSES = frozenset({"note", "chord", "rest", "mRest"})

#: 小节容器的 class（Verovio：``<g class="measure" id="<小节 xml:id>">``）。
#: 它的包围盒 = 子树内全部形状（谱线、符头、符干、终止小节线…）的并集，
#: 正是"高亮当前小节"需要的矩形：宽度 = 小节宽度、高度 ≈ 该行谱表高度。
MEASURE_CLASS = "measure"

_SHAPE_TAGS = frozenset({"use", "path", "ellipse", "circle", "rect", "polygon", "polyline", "line"})
_NUM_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_TRANSFORM_RE = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)")
_SVG_SIZE_RE = re.compile(
    r'<svg[^>]*?width="([\d.]+)(?:px)?"[^>]*?height="([\d.]+)(?:px)?"', re.S
)
_HREF_RE = re.compile(r"#([^\"']+)")


def _inner_viewbox_scale(text: str, outer_w: float) -> tuple[float, float]:
    """算出「内部坐标 → 外层 px」的换算系数。

    Verovio 的 SVG 结构是**嵌套两层**（这是本模块第一版算错尺寸的原因）::

        <svg width="840px" height="188px">                      ← 外层，真实输出尺寸
          <svg class="definition-scale" viewBox="0 0 21000 4680">  ← 内层，所有图形坐标都在这个空间
            <g class="page-margin" transform="translate(500, 500)">
              <g class="system"> … <path d="M1510 620 L7382 620"/> …

    因此 `path` / `use` 里的坐标要乘的是 **外层 px ÷ viewBox 宽度**（实测 canon 为
    840 / 21000 = 0.04）。若忽略这一步、按"100 单位 = 1 px"去猜，会得到
    2.3 px 的符头（真实约 9.3 px），从而做出小得离谱的高亮框。
    """
    m = re.search(
        r'<svg[^>]*class="[^"]*definition-scale[^"]*"[^>]*viewBox="([-\d.\s,]+)"', text
    )
    if not m:
        m = re.search(r'<svg[^>]*viewBox="([-\d.\s,]+)"', text)
    if not m:
        return VEROVIO_UNITS_PER_PX, VEROVIO_UNITS_PER_PX
    nums = [float(n) for n in _NUM_RE.findall(m.group(1))]
    if len(nums) < 4 or nums[2] <= 0:
        return VEROVIO_UNITS_PER_PX, VEROVIO_UNITS_PER_PX
    sx = outer_w / nums[2]
    return sx, sx


def verovio_units_per_px(svg_text: str) -> float:
    """返回该 SVG 的「内部单位 → px」换算系数（由内层 viewBox 决定）。

    实测：canon 的 system SVG 为 ``840px`` / ``viewBox="0 0 21000 4680"`` → ``0.04``。
    """
    m = _SVG_SIZE_RE.search(svg_text)
    outer = float(m.group(1)) if m else 0.0
    if outer <= 0:
        return VEROVIO_UNITS_PER_PX
    return _inner_viewbox_scale(svg_text, outer)[0]


#: 需要补 stroke 的图形标签
_STROKE_TARGETS = ("path", "ellipse", "polygon", "polyline", "rect", "line")
_OPEN_TAG_RE = re.compile(
    r"<(" + "|".join(_STROKE_TARGETS) + r")(\s[^>]*)?/?>"
)


def inline_stroke_attributes(svg_text: str) -> str:
    """把 ``stroke:currentColor`` 内联成元素属性。

    **为什么必须做**：Verovio 的谱线长这样::

        <path d="M1510 620 L7382 620" stroke-width="13" />

    **它没有 stroke 属性**，颜色来自内嵌 CSS 规则
    ``#<svgid> path {stroke:currentColor}``。而 QtSvg 只支持 SVG Tiny 1.2 的
    子集，**不套用这条 CSS 规则**，于是谱线完全没有描边 —— 而 ``<path>`` 的
    ``fill`` 默认为黑色但**线条没有面积**，结果就是"**音符看得见（符头是实心字形）、
    五线谱的线全都不见了**"（M4 实测：渲染深色像素 4501，横贯谱线 0 条）。

    内联后同一份 SVG 渲染出 5 条谱线（深色像素 9379，横贯行号 59/66/117/124/131）。
    只在元素**整段开标签里没有** ``stroke="..."`` 时才补，因此不会覆盖 Verovio
    显式指定的颜色，也不会产生重复属性。
    """

    def repl(m: re.Match[str]) -> str:
        tag, rest = m.group(1), m.group(2) or ""
        if 'stroke="' in rest:
            return m.group(0)  # 已有显式 stroke，保持原样
        return f'<{tag} stroke="currentColor"{rest}>' if rest else f'<{tag} stroke="currentColor">'

    return _OPEN_TAG_RE.sub(repl, svg_text)


def flatten_svg(svg_text: str, *, inline_stroke: bool = True) -> str:
    """把 Verovio 的**嵌套 ``<svg>``** 展平成一个单层 SVG，并（默认）内联 stroke。

    两件事都是为了 QtSvg 能正确渲染：

    1. **展平嵌套 svg**：QtSvg 无法渲染 Verovio 的嵌套结构 ——
       它会报告 ``isValid() == True`` 且 ``defaultSize`` 正确，但**一个像素都画不出来**
       （M4 实测：840×188 的 system 渲染出来是纯白，深色像素 0 个）。
    2. **内联 stroke**：见 :func:`inline_stroke_attributes`，否则**五线谱的线不可见**。

    **实现上刻意用文本替换而不是 ElementTree 重写**：``ET.tostring()`` 会把标签加上
    ``ns0:`` 前缀、把空元素写成自闭合、丢弃 ``xmlns:xlink`` 的原始写法，
    这些都可能让 QtSvg 解析失败。文本替换只动必要的位置，其余字节原样保留。

    Args:
        svg_text: Verovio 输出的 SVG 文本。
        inline_stroke: 是否内联 stroke。设为 False 仅用于对照实验/测试。

    Returns:
        处理后的 SVG 文本。
    """
    m = re.search(r"<svg[^>]*class=\"[^\"]*definition-scale[^\"]*\"[^>]*>", svg_text)
    if not m:
        # 没有嵌套结构，但可能仍需要内联 stroke
        return inline_stroke_attributes(svg_text) if inline_stroke else svg_text

    inner_tag = m.group(0)
    vb = re.search(r'viewBox="([^"]+)"', inner_tag)
    view_box = vb.group(1) if vb else ""

    # 1) 内层 <svg ...> → <g ...>（保留 class/color/font-family 等属性）
    end = m.end()
    replacement = "<g" + inner_tag[len("<svg") :]
    out = svg_text[: m.start()] + replacement + svg_text[end:]

    # 2) 与之配对的第一个 "</svg>" → "</g>"（从内层位置往后找，最近的即它的闭合）
    close = out.find("</svg>", m.start())
    if close < 0:
        return inline_stroke_attributes(svg_text) if inline_stroke else svg_text
    out = out[:close] + "</g>" + out[close + len("</svg>") :]

    # 3) 把 viewBox 提到根 svg（若根上还没有）
    if view_box:
        root_m = re.search(r"<svg\b[^>]*>", out)
        if root_m and "viewBox=" not in root_m.group(0):
            tag = root_m.group(0)[:-1] + f' viewBox="{view_box}">'
            out = out[: root_m.start()] + tag + out[root_m.end() :]

    # 4) 内联 stroke（否则谱线不可见）
    return inline_stroke_attributes(out) if inline_stroke else out


def flatten_svg_elementtree(svg_text: str) -> str:
    """``flatten_svg`` 的 ElementTree 版本（保留作为对照实现）。

    ⚠ 实测它会把 SVG 重写成 ``ns0:`` 前缀、自闭合标签等形式，
    可能让 QtSvg 拒绝解析，因此**不作为默认实现**。
    """
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError:
        return svg_text

    inner = None
    parent = None
    index = 0
    for p in root.iter():
        for i, c in enumerate(list(p)):
            if _local(c.tag) == "svg" and "definition-scale" in (c.attrib.get("class") or ""):
                inner, parent, index = c, p, i
                break
        if inner is not None:
            break
    if inner is None or parent is None:
        return svg_text

    view_box = inner.attrib.get("viewBox", "")
    kids = list(inner)
    inherited = {k: v for k, v in inner.attrib.items() if k in ("color", "font-family", "font-size")}
    parent.remove(inner)
    for k in kids:
        for key, val in inherited.items():
            k.attrib.setdefault(key, val)
        parent.insert(index, k)
        index += 1
    if view_box and not root.attrib.get("viewBox"):
        root.set("viewBox", view_box)
    body = ET.tostring(root, encoding="unicode")
    if not body.startswith("<?xml"):
        body = '<?xml version="1.0" encoding="UTF-8"?>\n' + body
    return body


@dataclass(frozen=True)
class Box:
    """矩形（px，相对 SVG 左上角）。"""

    x: float
    y: float
    w: float
    h: float

    @property
    def empty(self) -> bool:
        """零面积视为空（符干等只有高度没有宽度的形状需要靠描边补宽度）。"""
        return self.w <= 0.0 or self.h <= 0.0

    def union(self, other: "Box") -> "Box":
        x0 = min(self.x, other.x)
        y0 = min(self.y, other.y)
        x1 = max(self.x + self.w, other.x + other.w)
        y1 = max(self.y + self.h, other.y + other.h)
        return Box(x0, y0, x1 - x0, y1 - y0)

    def expand(self, pad: float) -> "Box":
        return Box(self.x - pad, self.y - pad, self.w + 2 * pad, self.h + 2 * pad)

    def to_dict(self) -> list[float]:
        return [round(self.x, 2), round(self.y, 2), round(self.w, 2), round(self.h, 2)]

    @classmethod
    def from_seq(cls, seq: list[float]) -> "Box":
        return cls(float(seq[0]), float(seq[1]), float(seq[2]), float(seq[3]))


@dataclass
class ElementGeometry:
    """一个元素的几何与分类信息。"""

    id: str
    box: Box
    classes: tuple[str, ...] = ()
    data: dict[str, str] = field(default_factory=dict)
    note_count: int = 0
    """该元素内部包含的音符字形数（和弦 > 1）。"""

    @property
    def is_note(self) -> bool:
        return "note" in self.classes

    @property
    def is_chord(self) -> bool:
        return "chord" in self.classes

    @property
    def is_rest(self) -> bool:
        return "rest" in self.classes or "mRest" in self.classes

    @property
    def is_measure(self) -> bool:
        return MEASURE_CLASS in self.classes


@dataclass
class SvgGeometry:
    """一个 SVG 文件的解析结果。"""

    width: float
    height: float
    elements: dict[str, ElementGeometry] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def element_count(self) -> int:
        return len(self.elements)

    @property
    def highlightable(self) -> list[ElementGeometry]:
        return [e for e in self.elements.values() if set(e.classes) & HIGHLIGHTABLE_CLASSES]

    @property
    def measures(self) -> list[ElementGeometry]:
        """全部小节容器（按解析顺序，即谱面顺序）。"""
        return [e for e in self.elements.values() if e.is_measure]

    def measure_box(self, measure_id: str) -> Box | None:
        """取某个小节的矩形（px，相对 SVG 左上角）。

        Verovio 的 ``<g class="measure">`` 直接使用 MEI 的小节 ``xml:id``，
        与 ``sync.json.measures[].id`` 一致（M7 实测 10 个套件 1833 处全部命中）。

        有两处 ID 变体需要容忍（都来自 Verovio 的"展开/重复"排版）：

        * 查询带 ``-rendN`` 后缀、而 SVG 用的是 base ID（小节被反复渲染）；
        * 反过来：查询是 base ID、而 SVG 里是带后缀的那一个。

        Returns:
            小节矩形；找不到时返回 ``None``（视图会退回逐音符高亮）。
        """
        hit = self.elements.get(measure_id)
        if hit is not None and hit.is_measure and not hit.box.empty:
            return hit.box
        base = re.sub(r"-rend\d*$", "", measure_id)
        if base != measure_id:
            hit = self.elements.get(base)
            if hit is not None and hit.is_measure and not hit.box.empty:
                return hit.box
        # 反向：查询是 base，SVG 用了带 -rendN 的展开 ID
        prefix = base + "-rend"
        for m in self.measures:
            if m.id.startswith(prefix) and not m.box.empty:
                return m.box
        return None

    def box_of(self, element_id: str) -> Box | None:
        """取元素包围盒；带 ``-rendN`` 后缀时回退到 base ID。"""
        hit = self.elements.get(element_id)
        if hit is not None and not hit.box.empty:
            return hit.box
        base = re.sub(r"-rend\d*$", "", element_id)
        hit = self.elements.get(base)
        return hit.box if hit is not None and not hit.box.empty else None

    def to_dict(self) -> dict:
        return {
            "width": round(self.width, 2),
            "height": round(self.height, 2),
            "elements": {
                k: {
                    "box": v.box.to_dict(),
                    "class": " ".join(v.classes),
                    **({"data": v.data} if v.data else {}),
                }
                for k, v in self.elements.items()
            },
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SvgGeometry":
        els: dict[str, ElementGeometry] = {}
        for k, v in (d.get("elements") or {}).items():
            els[k] = ElementGeometry(
                id=k,
                box=Box.from_seq(v["box"]),
                classes=tuple((v.get("class") or "").split()),
                data=dict(v.get("data") or {}),
            )
        return cls(
            width=float(d.get("width", 0)), height=float(d.get("height", 0)), elements=els
        )


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _attr(el: ET.Element, name: str, default: str = "") -> str:
    if name in el.attrib:
        return el.attrib[name]
    for k, v in el.attrib.items():
        if _local(k) == name:
            return v
    return default


def _parse_transform(value: str | None, qt_transform):
    """SVG ``transform`` 字符串 → ``QTransform``。

    按 SVG 语义**从左到右**依次作用（最右的先作用于图形），故每步左乘累积矩阵。
    """
    out = qt_transform()
    if not value:
        return out
    for name, args in _TRANSFORM_RE.findall(value):
        nums = [float(n) for n in _NUM_RE.findall(args)]
        step = qt_transform()
        if name == "translate":
            step.translate(nums[0] if nums else 0.0, nums[1] if len(nums) > 1 else 0.0)
        elif name == "scale":
            sx = nums[0] if nums else 1.0
            step.scale(sx, nums[1] if len(nums) > 1 else sx)
        elif name == "rotate":
            deg = nums[0] if nums else 0.0
            if len(nums) >= 3:
                step.translate(nums[1], nums[2])
                step.rotate(deg)
                step.translate(-nums[1], -nums[2])
            else:
                step.rotate(deg)
        elif name == "matrix" and len(nums) >= 6:
            step = qt_transform(nums[0], nums[1], nums[2], nums[3], nums[4], nums[5])
        elif name == "skewX" and nums:
            import math

            step.shear(math.tan(math.radians(nums[0])), 0.0)
        elif name == "skewY" and nums:
            import math

            step.shear(0.0, math.tan(math.radians(nums[0])))
        out = step * out
    return out


def parse_svg(source: str | Path, *, stroke_pad: bool = True) -> SvgGeometry:
    """解析一个 Verovio SVG（字符串或路径），返回可高亮元素的几何。

    Args:
        source: SVG 文本或路径。
        stroke_pad: 是否把描边宽度（``stroke-width``）算进包围盒。
            Verovio 的符干是"零面积"直线路径，不补描边宽度会得到 0 高度。

    Raises:
        ValueError: 输入不是可解析的 SVG。
    """
    from PySide6.QtCore import QRectF  # noqa: PLC0415
    from PySide6.QtGui import QPainterPath, QPainterPathStroker, QTransform  # noqa: PLC0415

    text = source.read_text(encoding="utf-8") if isinstance(source, Path) else str(source)
    m = _SVG_SIZE_RE.search(text)
    if not m:
        raise ValueError("不是有效的 SVG：缺少根元素的 width/height")
    width, height = float(m.group(1)), float(m.group(2))

    root = ET.fromstring(text)
    scale, _ = _inner_viewbox_scale(text, width)
    warnings: list[str] = []

    by_id: dict[str, ET.Element] = {}
    for el in root.iter():
        eid = el.attrib.get("id")
        if eid and eid not in by_id:
            by_id[eid] = el

    # ------------------------------------------------------------------ 字形路径
    glyph_cache: dict[str, QPainterPath] = {}
    stroker: QPainterPathStroker | None = None
    if stroke_pad:
        stroker = QPainterPathStroker()
        stroker.setWidth(1.0)  # 最小补边，避免零面积路径塌缩

    def shape_path(tag: str, el: ET.Element) -> QPainterPath | None:
        """把一个叶子形状转成 QPainterPath（局部坐标）。"""
        p = QPainterPath()
        try:
            if tag == "path":
                p.addPath(_path_from_d(_attr(el, "d")))
                return p
            if tag == "ellipse":
                cx, cy = float(_attr(el, "cx", "0")), float(_attr(el, "cy", "0"))
                rx, ry = float(_attr(el, "rx", "0")), float(_attr(el, "ry", "0"))
                p.addEllipse(QRectF(cx - rx, cy - ry, 2 * rx, 2 * ry))
                return p
            if tag == "circle":
                cx, cy = float(_attr(el, "cx", "0")), float(_attr(el, "cy", "0"))
                r = float(_attr(el, "r", "0"))
                p.addEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))
                return p
            if tag == "rect":
                p.addRect(
                    QRectF(
                        float(_attr(el, "x", "0")),
                        float(_attr(el, "y", "0")),
                        float(_attr(el, "width", "0")),
                        float(_attr(el, "height", "0")),
                    )
                )
                return p
            if tag == "line":
                p.moveTo(float(_attr(el, "x1", "0")), float(_attr(el, "y1", "0")))
                p.lineTo(float(_attr(el, "x2", "0")), float(_attr(el, "y2", "0")))
                return p
            if tag in ("polygon", "polyline"):
                nums = [float(n) for n in _NUM_RE.findall(_attr(el, "points"))]
                pairs = list(zip(nums[0::2], nums[1::2]))
                if not pairs:
                    return None
                p.moveTo(*pairs[0])
                for xy in pairs[1:]:
                    p.lineTo(*xy)
                if tag == "polygon":
                    p.closeSubpath()
                return p
        except (ValueError, TypeError):
            return None
        return None

    def _path_from_d(d: str) -> QPainterPath:
        """用 QPainterPath 的 SVG 路径解析能力（Qt 6 支持 ``addPath`` 之外的
        ``QPainterPath.fromString`` 风格的解析，但需要借助 QSvgRenderer 之外的手段，
        这里用一个足够完整的手写解析器）。"""
        return _parse_path_d(d)

    def glyph_path(gid: str, depth: int = 0) -> QPainterPath | None:
        """取 ``<defs>`` 里某个字形的路径（局部坐标）。"""
        if depth > 12:
            return None
        if gid in glyph_cache:
            return glyph_cache[gid]
        el = by_id.get(gid)
        if el is None:
            return None
        path = QPainterPath()
        _accumulate(el, QTransform(), path, in_defs=True, depth=depth + 1, collect_ids=False)
        glyph_cache[gid] = path
        return path

    def _accumulate(
        el: ET.Element,
        base,
        out: QPainterPath,
        *,
        in_defs: bool,
        depth: int,
        collect_ids: bool,
    ) -> None:
        """把子树里所有形状（含解引用的 use）累加进 ``out``。"""
        if depth > 24:
            return
        tag = _local(el.tag)
        xf = _parse_transform(el.attrib.get("transform"), QTransform) * base
        if tag == "use":
            href = _attr(el, "href")
            mm = _HREF_RE.search(href)
            if mm:
                gp = glyph_path(mm.group(1))
                if gp is not None and not gp.isEmpty():
                    sub = QPainterPath(gp)
                    sub = sub * xf  # QPainterPath * QTransform
                    out.addPath(sub)
        elif tag in _SHAPE_TAGS:
            sp = shape_path(tag, el)
            if sp is not None and not sp.isEmpty():
                out.addPath(sp * xf)
        for child in el:
            _accumulate(
                child, xf, out, in_defs=in_defs, depth=depth + 1, collect_ids=collect_ids
            )

    # ------------------------------------------------------------------ 遍历正文
    elements: dict[str, ElementGeometry] = {}

    def walk(
        el: ET.Element,
        base,
        owner: ElementGeometry | None,
        in_defs: bool,
        enclosing_measure: ElementGeometry | None,
    ) -> None:
        tag = _local(el.tag)
        xf = _parse_transform(el.attrib.get("transform"), QTransform) * base
        eid = el.attrib.get("id")
        classes = tuple((_attr(el, "class") or "").split())
        current = owner
        measure = enclosing_measure
        if eid and not in_defs:
            current = ElementGeometry(
                id=eid,
                box=Box(0.0, 0.0, 0.0, 0.0),
                classes=classes,
                data={
                    k: v
                    for k, v in el.attrib.items()
                    if k.startswith("data-") or k in ("pname", "oct", "dur")
                },
            )
            elements[eid] = current
            if MEASURE_CLASS in classes:
                measure = current

        child_defs = in_defs or tag in ("defs", "symbol")

        if not in_defs and current is not None and tag in _SHAPE_TAGS:
            # 以本元素为根，收集全部子形状（会解引用 use）
            local = QPainterPath()
            _accumulate(el, base, local, in_defs=False, depth=0, collect_ids=False)
            if not local.isEmpty():
                r = local.boundingRect()
                if stroker is not None:
                    r = r.united(stroker.createStroke(local).boundingRect())
                b = Box(r.x() * scale, r.y() * scale, r.width() * scale, r.height() * scale)
                current.box = b if current.box.empty else current.box.union(b)
                if measure is not None and measure is not current:
                    # 所属小节的矩形 = 其子树内所有形状的并集
                    # （谱线给出宽度、符头/符干给出高度、终止小节线给出右边界）。
                    # 边走边并，避免为了小节再单独遍历一次子树。
                    measure.box = b if measure.box.empty else measure.box.union(b)

        for child in el:
            walk(child, xf, current, child_defs, measure)

    walk(root, QTransform(), None, False, None)

    kept = {k: v for k, v in elements.items() if not v.box.empty}
    dropped = len(elements) - len(kept)
    if dropped:
        warnings.append(f"{dropped} 个元素未解析到几何（纯容器或仅含未解引用的 use）")
    if not any(set(v.classes) & HIGHLIGHTABLE_CLASSES for v in kept.values()):
        warnings.append("未解析出任何可高亮元素（note/chord/rest），该 SVG 可能不是 Verovio 输出")

    return SvgGeometry(width=width, height=height, elements=kept, warnings=warnings)


# --------------------------------------------------------------------------- SVG path d 解析
_CMD_ARITY: dict[str, int] = {
    "M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0,
}
_PATH_TOKEN_RE = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])|([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)")


def _parse_path_d(d: str):
    """解析 SVG 的 ``d`` 属性为 ``QPainterPath``。

    为什么要自己写：``QPainterPath`` 在 PySide6 里没有公开的 SVG 路径字符串解析入口
    （只有 ``QSvgRenderer`` 内部有）。实现要点：

    * 按**命令元数**（arity）消费参数，支持**隐式重复**
      （如 ``c`` 后面跟多组 6 个数字，或 ``M`` 之后的多组坐标按 ``L`` 处理）；
    * 数字用正则整体匹配，因此 ``118-72`` 这类**省略分隔符**的写法也能正确切成两个数；
    * 椭圆弧 ``A`` 用端点直线近似（Verovio 的字形路径不使用弧线）。
    """
    from PySide6.QtGui import QPainterPath  # noqa: PLC0415

    path = QPainterPath()
    tokens = [m.group(1) or m.group(2) for m in _PATH_TOKEN_RE.finditer(d or "")]
    if not tokens:
        return path

    i = 0
    cmd = ""
    cur = (0.0, 0.0)
    sub_start = (0.0, 0.0)
    last_ctrl: tuple[float, float] | None = None

    def take(n: int) -> list[float]:
        nonlocal i
        vals = [float(t) for t in tokens[i : i + n]]
        i += n
        return vals

    def is_command(pos: int) -> bool:
        return pos < len(tokens) and len(tokens[pos]) == 1 and tokens[pos].isalpha()

    while i < len(tokens):
        if is_command(i):
            cmd = tokens[i]
            i += 1
            if cmd in "Zz":
                path.closeSubpath()
                cur = sub_start
                last_ctrl = None
                continue
        if not cmd or cmd in "Zz":
            i += 1  # 数据出现在命令之前：跳过，避免死循环
            continue

        upper = cmd.upper()
        arity = _CMD_ARITY.get(upper)
        if arity is None or i + arity > len(tokens):
            break
        vals = take(arity)

        try:
            if upper == "M":
                x, y = vals
                if cmd == "m":
                    x += cur[0]; y += cur[1]
                path.moveTo(x, y)
                cur = sub_start = (x, y)
                cmd = "L" if cmd == "M" else "l"  # 隐式重复按 lineto
                last_ctrl = None
            elif upper == "L":
                x, y = vals
                if cmd == "l":
                    x += cur[0]; y += cur[1]
                path.lineTo(x, y)
                cur = (x, y)
                last_ctrl = None
            elif upper == "H":
                x = vals[0] + (cur[0] if cmd == "h" else 0.0)
                path.lineTo(x, cur[1])
                cur = (x, cur[1])
                last_ctrl = None
            elif upper == "V":
                y = vals[0] + (cur[1] if cmd == "v" else 0.0)
                path.lineTo(cur[0], y)
                cur = (cur[0], y)
                last_ctrl = None
            elif upper == "C":
                x1, y1, x2, y2, x, y = vals
                if cmd == "c":
                    x1 += cur[0]; y1 += cur[1]
                    x2 += cur[0]; y2 += cur[1]
                    x += cur[0]; y += cur[1]
                path.cubicTo(x1, y1, x2, y2, x, y)
                last_ctrl = (x2, y2)
                cur = (x, y)
            elif upper == "S":
                x2, y2, x, y = vals
                if cmd == "s":
                    x2 += cur[0]; y2 += cur[1]
                    x += cur[0]; y += cur[1]
                x1, y1 = (
                    (2 * cur[0] - last_ctrl[0], 2 * cur[1] - last_ctrl[1])
                    if last_ctrl is not None
                    else cur
                )
                path.cubicTo(x1, y1, x2, y2, x, y)
                last_ctrl = (x2, y2)
                cur = (x, y)
            elif upper == "Q":
                x1, y1, x, y = vals
                if cmd == "q":
                    x1 += cur[0]; y1 += cur[1]
                    x += cur[0]; y += cur[1]
                path.quadTo(x1, y1, x, y)
                last_ctrl = (x1, y1)
                cur = (x, y)
            elif upper == "T":
                x, y = vals
                if cmd == "t":
                    x += cur[0]; y += cur[1]
                x1, y1 = (
                    (2 * cur[0] - last_ctrl[0], 2 * cur[1] - last_ctrl[1])
                    if last_ctrl is not None
                    else cur
                )
                path.quadTo(x1, y1, x, y)
                last_ctrl = (x1, y1)
                cur = (x, y)
            elif upper == "A":
                x, y = vals[5], vals[6]
                if cmd == "a":
                    x += cur[0]; y += cur[1]
                path.lineTo(x, y)  # 用端点近似弧线
                cur = (x, y)
                last_ctrl = None
        except (ValueError, IndexError):
            break

    return path
