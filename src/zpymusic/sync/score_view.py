"""曲谱视图抽象与两种后端（需求 §5.3 F2.6/F2.7、风险 R4）。

M4 实测结论决定了这里的架构：

| 后端 | 能力 | 本机实测 |
| :--- | :--- | :--- |
| **WebEngine**（`QWebEngineView` + DOM） | 高亮最自然（改 CSS class）、缩放/滚动/点击都现成 | ⚠ 本机受限沙箱下**无法运行**：Chromium 需要命名管道 IPC，被拒 → `named-platform-channel-pipe ... 拒绝访问`。代码已实现，正常环境可用 |
| **Native**（`QtSvg` + 自绘高亮） | 零 WebEngine 依赖 | ✅ 可用，但有两个坑：① QtSvg **无法渲染 Verovio 的嵌套 `<svg>`**（`isValid()` 为真但一个像素都不画），必须先 `flatten_svg()`；② `boundsOnElement()` 不可用，高亮框要用 `svg_geometry.parse_svg()` 自己算 |

因此对外只暴露 :class:`ScoreView`，由 `create_score_view()` 按可用性选择实现，
失败时自动回退。播放/时间轴逻辑对两者完全一致。

**高亮策略（M7 起）**：跟随当前小节 —— 计划里 ``HighlightPlan.measure_id`` 非空即为此模式，
矩形取自 SVG 的 ``<g class="measure">``（宽度 = 小节宽度、高度 ≈ 谱表高度）；
目标行若尚未挂载（懒加载），视图会先按需挂载再补全矩形
（:meth:`NativeScoreView._resolve_measure_boxes`）。没有小节数据的旧套件退回逐音符高亮。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsView,
    QLabel,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from ..common.log import get_logger
from .highlight import MEASURE_PAD, HighlightPlan, HighlightRect, SystemGeometryIndex
from .svg_geometry import SvgGeometry, flatten_svg, parse_svg

log = get_logger(__name__)

__all__ = ["SystemRef", "ScoreView", "NativeScoreView", "create_score_view"]

#: 每个 system 之间留的垂直间距（px，虚拟坐标）
SYSTEM_GAP = 28
#: 视口上下各预加载多少屏（懒加载窗口）
MOUNT_MARGIN_SCREENS = 1.5
#: 跟随滚动的"舒适区"：高亮音符落在视口上/下这个比例之内就不动视图
FOLLOW_BAND = 0.12
#: 需要滚动时，把当前行的顶端放到视口高度的这个比例处（阅读位置稳定）
FOLLOW_ANCHOR = 0.25
#: 缩放范围（"不能手动缩小"的修复：允许缩到 30%，放大到 400%）
MIN_ZOOM = 0.3
MAX_ZOOM = 4.0


@dataclass
class SystemRef:
    """一个 system 的引用信息（来自 ``sync.json``）。"""

    index: int
    file: str
    width: float = 0
    height: float = 0
    note_ids: list[str] = field(default_factory=list)
    measure_ids: list[str] = field(default_factory=list)
    """该行依次出现的小节 ID（去重、按谱面顺序）。

    由控制器从 ``sync.json``（``notes[].measure``）算出，**不需要解析 SVG** ——
    因此"高亮当前小节"即使目标行还没挂载也能先定位到行号。
    """


class ScoreView(QWidget):
    """曲谱显示控件接口。

    Signals:
        element_clicked: 用户点击了某个元素（携带元素 ID），用于"点击谱面跳转"（F2.4）。
        systems_changed: 已挂载的 system 集合发生变化（供调试/状态显示）。
    """

    element_clicked = Signal(str)
    systems_changed = Signal(list)
    #: 实际缩放系数发生变化（含"适应宽度"在窗口尺寸变化后的自动重算）。
    #: 缩放工具条的百分比显示据此更新 —— 否则首屏自动适配后数字仍是旧的。
    zoom_changed = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._systems: list[SystemRef] = []
        self._root: Path | None = None
        self._system_map_cache: dict[str, int] | None = None
        self._measure_map_cache: dict[str, list[int]] | None = None

    # ------------------------------------------------------------------ 接口
    def load(self, root: Path, systems: list[SystemRef]) -> None:
        """载入一个套件的全部 system（``root`` 为套件目录）。"""
        raise NotImplementedError

    def set_highlight(self, plan: HighlightPlan) -> HighlightPlan | None:
        """应用高亮计划。

        Returns:
            实际生效的计划；返回 ``None`` 表示与传入的计划一致。
            "跟随小节"模式下，目标行可能还没挂载（矩形要挂载后才知道），
            视图会把补全后的计划返回给控制器，使 ``controller._plan``
            始终反映**真实画出**的内容。
        """
        raise NotImplementedError

    def clear_highlight(self) -> None:
        raise NotImplementedError

    def scroll_to_element(self, element_id: str, *, center: bool = True) -> bool:
        raise NotImplementedError

    def scroll_to_system(self, system: int) -> None:
        raise NotImplementedError

    def set_highlight_style(self, color: str, opacity: float) -> None:
        raise NotImplementedError

    def set_follow(self, follow: bool) -> None:
        raise NotImplementedError

    @property
    def follow(self) -> bool:
        return False

    def loaded_system_count(self) -> int:
        return 0

    def overlay_right_inset(self) -> int:
        """浮层（缩放工具条）在右侧必须让开的像素数。

        曲谱视图右侧通常有纵向滚动条（Fusion 下约 14 px）。缩放条是**浮**在
        视图上的，若按"贴右边缘"定位就会压住滚动条顶端，用户既点不到上箭头
        也拖不到最上端。视图自己最清楚滚动条有多宽，因此由视图回答这个值。
        """
        return 0

    # ------------------------------------------------------------------ 公共
    @property
    def systems(self) -> list[SystemRef]:
        return self._systems

    @property
    def root(self) -> Path | None:
        return self._root

    def build_system_map(self, plan_system_of: dict[str, int]) -> None:
        """把 ``systems[].note_ids`` 展开成 ``{元素ID: system}``（供 highlight 用）。

        结果被缓存：控制器每帧（约 30 fps）都会取一次这个映射，
        交响乐量级有 29324 个元素，逐帧重建会造成可观的浪费。
        """
        if self._system_map_cache is None:
            mapping: dict[str, int] = {}
            for s in self._systems:
                for eid in s.note_ids:
                    mapping.setdefault(eid, s.index)
            self._system_map_cache = mapping
        plan_system_of.clear()
        plan_system_of.update(self._system_map_cache)

    def invalidate_system_map(self) -> None:
        """``load()`` 之后需要调用（``load`` 内部已自动调用）。"""
        self._system_map_cache = None
        self._measure_map_cache = None

    # ------------------------------------------------------------------ 小节
    def _measure_map(self) -> dict[str, list[int]]:
        """``{小节ID: [system 序号…]}``（惰性缓存）。

        两个来源合起来用：

        1. ``systems[].measure_ids``（来自 ``sync.json`` 的 ``notes[].measure``）
           —— 不需要解析 SVG，因此**目标行还没挂载时也能定位**；
        2. 已挂载/解析过的行里**实际出现**的小节 ID（见 :meth:`_index_measures`）
           —— 用来补上"整小节只有休止符"这类没有音符、因而查不到归属的小节。

        正常情况一个列表只有一个元素；一小节被换行拆成两段时会有两个
        （两段各自高亮自己那一半）。
        """
        if self._measure_map_cache is None:
            mapping: dict[str, list[int]] = {}
            for s in self._systems:
                for mid in getattr(s, "measure_ids", None) or ():
                    self._register_measure(mapping, mid, s.index)
            self._measure_map_cache = mapping
        return self._measure_map_cache

    @staticmethod
    def _register_measure(mapping: dict[str, list[int]], measure_id: str, system: int) -> None:
        """登记小节 → system，并把 ``-rendN`` 变体一起登记（两种写法都认得）。

        Verovio 遇到反复记号会把同一小节展开成 ``vugbrzt`` 与 ``vugbrzt-rend2``
        两个 ID（``sync.json.measures[]`` 里两个都在、onset 不同），
        而 SVG 里只画其中一个 —— 两种写法必须都能定位到同一行。
        """
        keys = {measure_id, re.sub(r"-rend\d*$", "", measure_id)}
        for key in keys:
            if not key:
                continue
            systems = mapping.setdefault(key, [])
            if system not in systems:
                systems.append(system)

    def _index_measures(self, system: int, geom: SvgGeometry) -> None:
        """把某一行**实际含有**的小节登记进映射（挂载时调用）。"""
        mapping = self._measure_map()
        for m in geom.measures:
            self._register_measure(mapping, m.id, system)

    def measure_systems(self, measure_id: str) -> list[int]:
        """包含该小节的 system 序号（通常恰好一个；跨行的不完整小节可能多个）。

        不需要解析 SVG：小节归属来自 ``sync.json``，因此**目标行还没挂载时
        也能定位**，视图随后会按需挂载该行再取小节矩形。
        """
        if not measure_id:
            return []
        return list(self._measure_map().get(measure_id, ()))


# ===========================================================================
# 原生后端
# ===========================================================================
class _HighlightOverlay(QGraphicsItem):
    """画在所有 system 之上的高亮层（形态 B：半透明覆盖框）。

    选"半透明框"而不是"改音符颜色"的原因：QtSvg 只能整幅渲染，
    无法单独给某个元素换色（`boundsOnElement` 也不可用）。
    覆盖框是纯增量绘制，且天然不会破坏谱面内容 —— 谱面始终清晰可读。

    M7 起主策略是"高亮当前小节"，因此这里的矩形通常就是一个小节的
    "小节宽度 × 谱表高度"（没有小节数据的旧套件仍然画音符框）。
    """

    def __init__(self) -> None:
        super().__init__()
        self.setZValue(1000)
        self.rects: list[tuple[QRectF, str]] = []
        self.color = QColor("#FF8C00")
        self.alpha = 0.35
        self._bounds = QRectF()

    def set_rects(self, rects: list[tuple[QRectF, str]], bounds: QRectF) -> None:
        self.prepareGeometryChange()
        self.rects = rects
        self._bounds = bounds
        self.update()

    def boundingRect(self) -> QRectF:  # noqa: N802 - Qt 命名
        return self._bounds

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        if not self.rects:
            return
        fill = QColor(self.color)
        fill.setAlphaF(max(0.0, min(1.0, self.alpha)))
        stroke = QColor(self.color)
        stroke.setAlphaF(min(1.0, max(0.0, self.alpha) + 0.35))
        pen = QPen(stroke)
        pen.setCosmetic(True)
        pen.setWidthF(1.2)
        painter.setPen(pen)
        painter.setBrush(QBrush(fill))
        for rect, _eid in self.rects:
            painter.drawRoundedRect(rect, 2.5, 2.5)


class NativeScoreView(ScoreView):
    """纯 Qt（QtSvg + QGraphicsView）实现，零 WebEngine 依赖。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self._view = QGraphicsView(self._scene)
        self._view.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        self._view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self._view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self._view.setBackgroundBrush(QBrush(QColor("#ffffff")))
        self._view.setFrameShape(QGraphicsView.Shape.NoFrame)
        self._view.viewport().setCursor(Qt.CursorShape.ArrowCursor)

        self._overlay = _HighlightOverlay()
        self._scene.addItem(self._overlay)

        self._items: dict[int, QGraphicsItem] = {}
        self._renderers: dict[int, object] = {}
        self._fit_width = True
        """宽度自适应（默认开）：避免按固定页宽渲染的 SVG 出现横向滚动与两侧留白。"""
        self._fit_view_size: tuple[int, int] | None = None
        """上一次"适应宽度"所用的视图尺寸（尺寸没变就不重复适配）。"""
        """显式持有 ``QSvgRenderer`` 的 Python 引用。

        必须保留：``QGraphicsSvgItem.setSharedRenderer()`` 只在 C++ 侧引用它，
        若 Python 包装对象被 GC，底层渲染器会被销毁，item 内部留下悬垂指针 ——
        之后任何 ``item.renderer()`` / 绘制都会 access violation（实测崩溃点）。
        """
        self._rects: dict[int, QRectF] = {}
        self._geoms: dict[int, SvgGeometry] = {}
        self._follow = True
        self._plan = HighlightPlan()
        self._measure_fallback_warned = False
        """小节几何缺失时只提示一次（否则每换一个小节都会刷屏）。"""
        self._system_of: dict[str, int] = {}
        self._geom_index = SystemGeometryIndex()
        self._lazy = QTimer(self)
        self._lazy.setSingleShot(True)
        self._lazy.setInterval(40)
        self._lazy.timeout.connect(self._ensure_visible)

        self._placeholder = QLabel(
            "尚未载入套件。\n\n请在左侧选择一个套件（或先在「生成套件」页生成）。"
        )
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("color:#888;font-size:12pt;")
        self._stack = QStackedLayout(self)
        self._stack.addWidget(self._placeholder)
        self._stack.addWidget(self._view)
        self._stack.setCurrentIndex(0)

        self._view.horizontalScrollBar().valueChanged.connect(self._schedule_lazy)
        self._view.verticalScrollBar().valueChanged.connect(self._schedule_lazy)
        self._view.viewport().installEventFilter(self)

    # ------------------------------------------------------------------ 载入
    def load(self, root: Path, systems: list[SystemRef]) -> None:
        self._clear_scene()
        self._root = Path(root)
        self._systems = list(systems)
        self._system_map_cache = None
        self._measure_map_cache = None
        self._measure_fallback_warned = False
        self._system_of.clear()
        for s in self._systems:
            for eid in s.note_ids:
                self._system_of.setdefault(eid, s.index)
            self._geom_index.set_file(s.index, s.file)

        y = 0.0
        for s in self._systems:
            h = s.height or 200.0
            self._rects[s.index] = QRectF(0.0, y, s.width or 840.0, h)
            y += h + SYSTEM_GAP
        self._scene.setSceneRect(QRectF(0, 0, max((r.width() for r in self._rects.values()), default=840), y))

        if self._systems:
            self._stack.setCurrentIndex(1)
            self._view.verticalScrollBar().setValue(0)
            # 先按视口宽度自适应，再加载首屏
            if self._fit_width:
                self.fit_width()
            self._ensure_visible(force=True)
        else:
            self._stack.setCurrentIndex(0)
            self._placeholder.setText("该套件没有任何 system（SVG 缺失？）")

    def _clear_scene(self) -> None:
        for item in self._items.values():
            self._scene.removeItem(item)
        self._items.clear()
        self._renderers.clear()
        self._geoms.clear()
        self._rects.clear()
        self._overlay.set_rects([], QRectF())
        self._plan = HighlightPlan()

    # ------------------------------------------------------------------ 懒加载
    def _schedule_lazy(self) -> None:
        self._lazy.start()

    def _visible_range(self) -> tuple[float, float]:
        vr = self._view.mapToScene(self._view.viewport().rect()).boundingRect()
        margin = vr.height() * MOUNT_MARGIN_SCREENS
        return vr.top() - margin, vr.bottom() + margin

    def _ensure_visible(self, *, force: bool = False) -> None:
        if not self._systems:
            return
        top, bottom = self._visible_range()
        changed = False
        for s in self._systems:
            rect = self._rects.get(s.index)
            if rect is None:
                continue
            visible = rect.bottom() >= top and rect.top() <= bottom
            mounted = s.index in self._items
            if visible and not mounted:
                if self._mount(s):
                    changed = True
            elif not visible and mounted:
                # 离得太远就卸载，控制内存（four-seasons 有 276 个 system）
                self._unmount(s.index)
                changed = True
        if changed:
            self.systems_changed.emit(sorted(self._items))
            self._apply_highlight()

    def _mount(self, s: SystemRef) -> bool:
        if s.index in self._items:
            # 绝不能重复挂载：旧 QGraphicsSvgItem 会被留在场景里、而它的
            # renderer 失去 Python 引用后被 GC → 悬垂指针 → 重绘时进程崩溃
            # （实测：对同一个 system 调两次 _mount，下一次绘制直接 access violation）。
            return True
        if not self._root or not s.file:
            return False
        path = self._root / s.file
        if not path.is_file():
            log.warning("缺少 system SVG：%s", path)
            return False
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            log.warning("无法读取 %s：%s", path, e)
            return False

        geom = parse_svg(text)
        renderer = self._make_renderer(text)
        if renderer is None:
            return False

        from PySide6.QtSvgWidgets import QGraphicsSvgItem  # noqa: PLC0415

        item = QGraphicsSvgItem()
        item.setSharedRenderer(renderer)
        item.setPos(self._rects[s.index].topLeft())
        # 让 item 自己持有 renderer 的 Python 引用（setData 存 Python 对象）。
        # 见 __init__ 中 _renderers 的说明：不持有会 GC 掉底层渲染器 → 崩溃。
        item.setData(0, renderer)
        self._scene.addItem(item)
        self._items[s.index] = item
        self._renderers[s.index] = renderer
        self._geoms[s.index] = geom
        self._geom_index.put(s.index, geom)
        self._index_measures(s.index, geom)
        return True

    @staticmethod
    def _make_renderer(text: str):
        """把嵌套 SVG 展平后交给 QtSvg（不展平则渲染为空白）。"""
        from PySide6.QtSvg import QSvgRenderer  # noqa: PLC0415

        flat = flatten_svg(text)
        renderer = QSvgRenderer(flat.encode("utf-8"))
        if not renderer.isValid():
            renderer = QSvgRenderer(text.encode("utf-8"))
        if not renderer.isValid():
            log.warning("QtSvg 无法解析该 system SVG")
            return None
        return renderer

    def _unmount(self, index: int) -> None:
        item = self._items.pop(index, None)
        if item is not None:
            self._scene.removeItem(item)
        self._renderers.pop(index, None)
        self._geoms.pop(index, None)

    # ------------------------------------------------------------------ 高亮
    def set_highlight(self, plan: HighlightPlan) -> HighlightPlan:
        self._plan = plan
        # 先补全小节矩形（可能需要按需挂载目标行），再跟随、再绘制 ——
        # 否则"跳到还没挂载的行"时首帧既没有矩形也无法定位。
        self._resolve_measure_boxes()
        if self._follow and self._plan.rects:
            self._follow_highlight(self._plan)
        self._apply_highlight()
        return self._plan

    def _resolve_measure_boxes(self) -> None:
        """把"跟随小节"计划里尚未解析的矩形补全（顺带按需挂载该行）。

        懒加载视图只会解析视口附近的行，而 ``sync.json`` 里的小节 ID 在
        未挂载的 SVG 里当然取不到矩形。这里按小节所属的 system 先挂载、
        再从几何索引取矩形，使"拖动进度条到很远的位置"也能立刻看到高亮。

        万一所有候选行都取不到小节矩形（``sync.json`` 与 SVG 不是同一次
        渲染，例如手工替换过 SVG），就退回逐音符高亮 —— 宁可高亮得粗一点，
        也不要"整首曲子完全没有高亮"。
        """
        if not self._plan.measure_id or not self._plan.rects:
            return
        rects: list[HighlightRect] = []
        changed = False
        resolved = False
        for hr in self._plan.rects:
            box = hr.box
            if box.empty:
                if hr.system not in self._items:
                    self._mount_by_index(hr.system)
                found = self._geom_index.measure_box(hr.system, self._plan.measure_id)
                if found is not None and not found.empty:
                    box = found.expand(MEASURE_PAD)
                    changed = True
            if not box.empty:
                resolved = True
            rects.append(hr if box is hr.box else HighlightRect(hr.system, hr.element_id, box))
        if changed:
            self._plan = HighlightPlan(
                rects=tuple(rects), ids=self._plan.ids, measure_id=self._plan.measure_id
            )
        if not resolved:
            self._fallback_to_notes()

    def _fallback_to_notes(self) -> None:
        """小节矩形拿不到 → 用当前发声的元素做逐音符高亮。"""
        measure_id = self._plan.measure_id
        ids = list(self._plan.ids)
        if not ids:
            self._plan = HighlightPlan()
            return
        if not self._measure_fallback_warned:
            self._measure_fallback_warned = True
            log.warning(
                "小节 %s 在 SVG 里找不到对应几何（sync.json 与 SVG 可能不是同一次渲染），"
                "已退回逐音符高亮",
                measure_id,
            )
        self._plan = self._geom_index.plan(self._system_of, ids, pad=1.5)

    def _plan_scene_bounds(self, plan: HighlightPlan) -> QRectF:
        """把高亮计划里的 system 局部矩形换算成场景坐标的并集。"""
        bounds = QRectF()
        for hr in plan.rects:
            origin = self._rects.get(hr.system)
            if origin is None:
                continue
            r = QRectF(hr.box.x, hr.box.y, hr.box.w, hr.box.h).translated(origin.topLeft())
            bounds = r if bounds.isNull() else bounds.united(r)
        return bounds

    def _follow_highlight(self, plan: HighlightPlan) -> None:
        """跟随滚动：**只在当前高亮的小节离开视口舒适区时**才移动视图。

        旧实现（"每帧 ``centerOn`` 当前行的中心"）有两个可观察的毛病：

        * 起播那一瞬间视图被强行下移 —— 用户看到"点播放后谱面上移一小段，
          右侧滚动条往下走一小段"；
        * 播放中鼠标滚轮 / 拖动滚动条每滚一点，就被下一帧的 ``centerOn`` 拉回去，
          于是"怎么都回不到最顶端"。

        现在的规则：当前高亮的小节还在视口上下 :data:`FOLLOW_BAND` 的舒适区内就
        **完全不动**（用户可以自由滚动查看其它行）；只有当它真的快跑出视口时才滚动一次，
        并把**当前行的顶端**对齐到视口上部 :data:`FOLLOW_ANCHOR` 处，
        这样每一行的阅读位置一致，也不会出现半行悬在屏幕外的抖动。
        """
        rect = self._rects.get(plan.first_system)
        if rect is None:
            return
        viewport = self._view.viewport()
        if viewport.width() <= 0 or viewport.height() <= 0:
            return  # 还没完成布局，等下一帧
        visible = self._view.mapToScene(viewport.rect()).boundingRect()
        if visible.isEmpty():
            return

        target = self._plan_scene_bounds(plan)
        if target.isNull():
            target = rect

        band_top = visible.top() + visible.height() * FOLLOW_BAND
        band_bottom = visible.bottom() - visible.height() * FOLLOW_BAND
        inside_y = band_top <= target.center().y() <= band_bottom
        inside_x = visible.left() <= target.center().x() <= visible.right()
        if inside_y and inside_x:
            return  # 还在舒适区 → 不打扰用户

        center = visible.center()
        if not inside_x:
            center.setX(target.center().x())
        # centerOn(y) 之后视口顶端 = y - h/2，令"当前行顶端"落在 h*FOLLOW_ANCHOR 处
        center.setY(rect.top() + visible.height() * FOLLOW_ANCHOR)
        self._view.centerOn(center)

    def _apply_highlight(self) -> None:
        self._resolve_measure_boxes()
        rects: list[tuple[QRectF, str]] = []
        bounds = QRectF()
        for hr in self._plan.rects:
            origin = self._rects.get(hr.system)
            if origin is None:
                continue
            if hr.system not in self._items and not self._mount_by_index(hr.system):
                # 高亮所在的行还没挂载：先按需挂载（跳转/拖动时常见）
                continue
            if hr.box.empty:
                # 小节矩形仍未解析出来（SVG 缺失/ID 不匹配）→ 不画退化矩形
                continue
            r = QRectF(hr.box.x, hr.box.y, hr.box.w, hr.box.h).translated(origin.topLeft())
            rects.append((r, hr.element_id))
            bounds = r if bounds.isNull() else bounds.united(r)
        self._overlay.set_rects(rects, bounds.adjusted(-4, -4, 4, 4))

    def _mount_by_index(self, index: int) -> bool:
        s = next((x for x in self._systems if x.index == index), None)
        if s is None:
            return False
        return self._mount(s)

    def clear_highlight(self) -> None:
        self._plan = HighlightPlan()
        self._overlay.set_rects([], QRectF())

    def set_highlight_style(self, color: str, opacity: float) -> None:
        self._overlay.color = QColor(color)
        self._overlay.alpha = float(opacity)
        self._overlay.update()

    # ------------------------------------------------------------------ 导航
    def scroll_to_element(self, element_id: str, *, center: bool = True) -> bool:
        system = self._system_of.get(element_id)
        if system is None:
            system = self._system_of.get(re.sub(r"-rend\d*$", "", element_id))
        if system is None:
            return False
        if system not in self._items:
            self._mount_by_index(system)
        self.scroll_to_system(system, center=center)
        return True

    def scroll_to_system(self, system: int, *, center: bool = True) -> None:
        rect = self._rects.get(system)
        if rect is None:
            return
        target = rect.adjusted(-40, -60, 40, 60)
        if center:
            self._view.centerOn(target.center())
        else:
            self._view.ensureVisible(target, 20, 60)
        self._schedule_lazy()

    def set_follow(self, follow: bool) -> None:
        self._follow = bool(follow)

    @property
    def follow(self) -> bool:
        return self._follow

    def loaded_system_count(self) -> int:
        return len(self._items)

    def overlay_right_inset(self) -> int:
        """让浮层避开本视图的纵向滚动条（永远预留，避免滚动条出现/消失时抖动）。"""
        sb = self._view.verticalScrollBar()
        if sb is None:
            return 0
        return max(0, int(sb.sizeHint().width()))

    def set_zoom(self, factor: float) -> float:
        """设置缩放（1.0 = 原始 px 大小）。返回实际生效的系数（会被范围夹取）。"""
        f = max(MIN_ZOOM, min(MAX_ZOOM, float(factor)))
        self._view.resetTransform()
        self._view.scale(f, f)
        self._zoom = f
        self._schedule_lazy()
        self.zoom_changed.emit(f)
        return f

    def zoom(self) -> float:
        return float(self._view.transform().m11())

    def fit_width(self) -> float:
        """把曲谱缩放到**刚好铺满当前视口宽度**（内容更窄时也放大到铺满）。

        用途：SVG 是按固定页面宽度（如 2100px）渲染的，而窗口可能只有
        900–1600 px 宽，直接 1:1 显示就会出现横向滚动条、两侧留白。
        改为自适应宽度后，谱面始终充满可用区域，也就不会再"横向很大"。

        Note:
            可用宽度取自**视图控件宽度** ``self._view.width()``，而不是
            ``self._view.viewport().width()``。两个原因（都是实测踩出来的）：

            1. Qt 的布局链里，父控件的 ``resizeEvent`` 先跑，内层
               ``QGraphicsView`` 的 viewport 尺寸要等它自己处理完 resize 才更新。
               播放页第一次显示时实测 ``view.width()==906`` 而
               ``viewport().width()==626``，用后者算出的缩放只有 0.29（还会被
               :data:`MIN_ZOOM` 夹到 0.30），表现为"首屏没有适应宽度，换一首才对"。
            2. viewport 宽度会随纵向滚动条出现/消失而变，拿它当输入会形成
               "缩放→滚动条→宽度→缩放"的反馈环。

        Returns:
            实际生效的缩放系数。
        """
        scene_w = self._scene.sceneRect().width()
        if scene_w <= 0:
            return self.zoom()
        avail = max(100, self._view.width() - self.overlay_right_inset() - 16)
        # 先记账再缩放：set_zoom 可能同步触发视口的 Resize 事件，
        # 若那时 _fit_view_size 还是旧值，就会在里面又调用一次 fit_width（重入）。
        self._fit_view_size = (self._view.width(), self._view.height())
        return self.set_zoom(avail / scene_w)

    def _maybe_fit_width(self) -> None:
        """尺寸真的变了才重新"适应宽度"（避免无谓的重算与抖动）。"""
        if not self._fit_width or not self._systems:
            return
        size = (self._view.width(), self._view.height())
        if size[0] <= 0 or size == self._fit_view_size:
            return
        self.fit_width()

    def set_fit_width(self, on: bool) -> None:
        """开启/关闭"宽度自适应"。开启后在窗口尺寸变化时自动重新适配。"""
        self._fit_width = bool(on)
        if self._fit_width:
            self.fit_width()

    @property
    def fit_width_enabled(self) -> bool:
        return self._fit_width

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._maybe_fit_width()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """首次显示时补一次适配。

        隐藏期间 Qt 会把子控件的 resize 事件**推迟**到显示时才派发，
        而推迟的这一刻内层视口尺寸仍可能是旧的。这里在显示后再补算一次
        （尺寸没变则是空操作），保证"第一次看到的曲谱就是适应宽度"。
        """
        super().showEvent(event)
        self._maybe_fit_width()

    def renderer_for(self, system: int):
        """返回某个 system 的 :class:`QSvgRenderer`（缩略图/导出/测试用）。

        Note:
            实测 ``QGraphicsScene.render()`` 传入**部分源矩形**时不稳定
            （离屏 400×300 目标 + 840×188 源会 access violation）。
            若要离屏出图，请直接用这个 renderer 的 ``render(painter, rect)``。
        """
        item = self._items.get(system)
        if item is None:
            self._mount_by_index(system)
            item = self._items.get(system)
        if item is None:
            return None
        return item.renderer()

    @property
    def view(self) -> QGraphicsView:
        return self._view

    @property
    def geometry_index(self) -> SystemGeometryIndex:
        """元素几何索引（高亮与点击命中测试用）。"""
        return self._geom_index

    @property
    def system_of(self) -> dict[str, int]:
        """``{元素ID: system 序号}``。"""
        return self._system_of

    # ------------------------------------------------------------------ 点击
    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt 命名
        from PySide6.QtCore import QEvent  # noqa: PLC0415

        if obj is self._view.viewport():
            if event.type() == QEvent.Type.Resize:
                # 内层视口尺寸变化（含纵向滚动条出现/消失）也要重算适应宽度
                self._maybe_fit_width()
            elif event.type() == QEvent.Type.MouseButtonRelease:
                if event.button() == Qt.MouseButton.LeftButton:
                    self._on_click(event.position().toPoint())
        return super().eventFilter(obj, event)

    def _on_click(self, viewport_pos) -> None:
        scene_pos = self._view.mapToScene(viewport_pos)
        for index, rect in self._rects.items():
            if not rect.contains(scene_pos):
                continue
            local_x = scene_pos.x() - rect.x()
            local_y = scene_pos.y() - rect.y()
            geom = self._geoms.get(index)
            if geom is None:
                self._mount_by_index(index)
                geom = self._geoms.get(index)
            if geom is None:
                return
            from .highlight import SystemGeometryIndex  # noqa: PLC0415

            idx = SystemGeometryIndex()
            idx.put(index, geom)
            eid = idx.element_at(index, local_x, local_y)
            if eid:
                self.element_clicked.emit(eid)
            return


def create_score_view(
    parent: QWidget | None = None, *, prefer: str = "auto", log_sink=None
) -> ScoreView:
    """按可用性创建曲谱视图。

    Args:
        prefer: ``auto`` 优先 WebEngine、失败回退原生；``native`` 强制原生；
            ``web`` 强制 WebEngine（失败则抛错，便于诊断）。

    Returns:
        :class:`ScoreView` 实例。``auto`` 模式下**永不失败**（兜底为原生）。

    Note:
        WebEngine 的可用性通过**子进程探测**（:func:`~zpymusic.sync.web_view.is_webengine_available`）
        决定，因为 Chromium 初始化失败是致命 abort、无法在本进程里捕获。
        探测在导入任何 QtWebEngine 模块**之前**完成，避免拖慢启动。
    """
    from ..common.log import get_logger as _get_logger

    log_ = log_sink or _get_logger(__name__)
    mode = (prefer or "auto").lower()

    if mode in ("auto", "web"):
        try:
            from .web_view import is_webengine_available  # noqa: PLC0415

            if is_webengine_available():
                from .web_view import WebScoreView  # noqa: PLC0415

                view = WebScoreView(parent)
                if view.backend_ready:
                    log_.info("曲谱视图后端：QWebEngineView（DOM 高亮）")
                    return view
                reason = view.unavailable_reason or "初始化失败"
                view.destroy()
                if mode == "web":
                    raise RuntimeError(f"WebEngine 后端不可用：{reason}")
                log_.warning("WebEngine 后端不可用（%s），回退到原生 QtSvg 后端", reason)
            elif mode == "web":
                raise RuntimeError("WebEngine 子进程探测失败（详见日志）")
            else:
                log_.info("WebEngine 不可用，使用 QtSvg 原生后端")
        except ImportError as e:
            if mode == "web":
                raise
            log_.warning("无法导入 WebEngine 后端（%s），回退到原生后端", e)
        except Exception as e:  # noqa: BLE001 - 自动回退不应中断启动
            if mode == "web":
                raise
            log_.warning("WebEngine 后端初始化失败（%s），回退到原生后端", e)

    log_.info("曲谱视图后端：QtSvg 原生（半透明高亮框）")
    return NativeScoreView(parent)
