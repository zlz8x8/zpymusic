"""三项用户反馈的回归测试（M6）。

**问题 1（曲谱视图被顶下去、滚不回顶端）**：跟随滚动曾是"每帧 ``centerOn`` 当前行中心"。
起播瞬间第一行被强行居中 → 视图下移一小段；播放中用户每滚一点就被下一帧拉回去 →
"怎么都回不到最顶端"。现在改为"高亮音符还在视口舒适区内就完全不动"。

顺带修掉两个同源问题：

* 缩放工具条按"贴右边缘"定位，压住了纵向滚动条顶端 4 px
  （用户原话："适应宽度的面板太靠右，挤占了滚动条的一部分位置"）；
* ``NativeScoreView._mount()`` 可被重复调用：旧 ``QGraphicsSvgItem`` 留在场景里、
  其 renderer 的 Python 引用被顶掉后 GC → 悬垂指针 → 下一次重绘进程崩溃（实测）。

**问题 2（信息区浅蓝字看不清）**：日志区下方那行上下文文本在**浅色底**上用了
``#9cdcfe``（深色主题的浅蓝）。→ 测试它的亮度足够低。

**问题 3（状态栏一直显示"未选择套件"）**：``lbl_current`` 只在生成成功时写过一次，
播放页切换套件从不更新。→ 测试播放页选中套件后状态栏跟着变。

**问题 4（首次显示的曲谱不是"适应宽度"，换一首才正常）**：``fit_width()`` 用的输入是
``self._view.viewport().width()``，而 Qt 的布局链里父控件的 ``resizeEvent`` 先跑、
内层视口尺寸稍后才更新 —— 首次显示时实测 ``view.width()==906`` 而
``viewport().width()==626``，算出的缩放 0.29 还被 :data:`MIN_ZOOM` 夹到 0.30。
现在改用视图控件宽度，并在视图/视口尺寸变化与首次显示时自动重算。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QColor

from zpymusic.core.suite import Suite, SuiteLayout
from zpymusic.sync.score_view import NativeScoreView, SystemRef

REPO = Path(__file__).resolve().parents[1]
SUITES = REPO / "staff" / "suites"

needs_suite = pytest.mark.skipif(
    not (SUITES / "canon-in-d-easy" / "canon-in-d-easy.sync.json").is_file(),
    reason="需要先生成 canon-in-d-easy 套件",
)

VIEW_W, VIEW_H = 950, 430


def _build_view(qapp) -> tuple[NativeScoreView, object]:  # noqa: ANN001
    """造一个已载入套件、且所有行都解析出几何的原生视图。"""
    suite = Suite(SuiteLayout(root=SUITES, base="canon-in-d-easy"))
    doc = suite.load_sync()
    systems = [
        SystemRef(
            index=s.index,
            file=s.file,
            width=s.width,
            height=s.height,
            note_ids=list(s.note_ids),
        )
        for s in doc.systems
    ]
    view = NativeScoreView()
    view.resize(VIEW_W, VIEW_H)
    view.show()
    view.load(suite.dir, systems)
    for s in view.systems:  # 懒加载：测试需要所有行的几何
        view._mount(s)
    qapp.processEvents()
    return view, suite


def _highlight(view: NativeScoreView, system: int) -> None:
    """高亮某个 system 的前几个音符（等价于控制器 tick 做的事）。"""
    ids = next(s.note_ids for s in view.systems if s.index == system)[:3]
    plan = view.geometry_index.plan(view.system_of, ids, pad=1.5)
    assert plan.rects, f"system {system} 应该有几何数据"
    view.set_highlight(plan)


def _visible_rect(view: NativeScoreView) -> QRect:  # noqa: ANN201
    return view.view.mapToScene(view.view.viewport().rect()).boundingRect()


# ===========================================================================
# 问题 1：跟随滚动
# ===========================================================================
@needs_suite
class TestFollowScroll:
    def test_start_of_playback_does_not_move_view(self, qapp) -> None:  # noqa: ANN001
        """起播第一帧不得移动视图（"点播放后谱面上移一小段"的直接回归）。"""
        view, _suite = _build_view(qapp)
        sb = view.view.verticalScrollBar()
        assert sb.value() == 0
        _highlight(view, 1)
        assert sb.value() == 0, "高亮第一行时不该滚动视图"

    def test_user_can_stay_at_top_while_playing(self, qapp) -> None:  # noqa: ANN001
        """用户滚到顶端后，后续帧不得把视图拉下去（"滚不到顶端"的直接回归）。"""
        view, _suite = _build_view(qapp)
        sb = view.view.verticalScrollBar()
        sb.setValue(0)
        for _ in range(3):  # 模拟连续几帧的高亮
            _highlight(view, 1)
        assert sb.value() == 0

    def test_no_scroll_while_highlight_in_comfort_band(self, qapp) -> None:  # noqa: ANN001
        """高亮音符还在视口舒适区内时，绝不动视图（用户手动滚动优先）。"""
        view, _suite = _build_view(qapp)
        sb = view.view.verticalScrollBar()
        sb.setValue(40)
        _highlight(view, 1)
        assert sb.value() == 40

    def test_follows_when_highlight_leaves_view(self, qapp) -> None:  # noqa: ANN001
        """高亮跑到下面几行时仍然要跟随，并把当前行顶端对齐到视口 25% 处。"""
        view, _suite = _build_view(qapp)
        system = max(s.index for s in view.systems)
        _highlight(view, system)
        sb = view.view.verticalScrollBar()
        assert sb.value() > 0, "换到后面几行时应跟随滚动"

        visible = _visible_rect(view)
        expected_top = view._rects[system].top() - visible.height() * 0.25
        assert abs(visible.top() - expected_top) <= visible.height() * 0.05, (
            f"当前行顶端应落在视口 25% 处：期望 {expected_top:.0f}，实际 {visible.top():.0f}"
        )

    def test_follow_disabled_never_scrolls(self, qapp) -> None:  # noqa: ANN001
        view, _suite = _build_view(qapp)
        view.set_follow(False)
        sb = view.view.verticalScrollBar()
        sb.setValue(120)
        _highlight(view, max(s.index for s in view.systems))
        _highlight(view, 1)
        assert sb.value() == 120

    def test_mount_is_idempotent(self, qapp) -> None:  # noqa: ANN001
        """重复挂载同一个 system 会留下悬垂 renderer（实测崩溃），必须被挡住。"""
        view, _suite = _build_view(qapp)
        before = len(view._scene.items())
        assert view._mount(view.systems[0]) is True
        assert len(view._scene.items()) == before, "重复 _mount 不该再往场景里加 item"


@needs_suite
class TestZoomBarDoesNotCoverScrollBar:
    def test_zoom_bar_clears_vertical_scrollbar(self, qapp) -> None:  # noqa: ANN001
        """缩放条必须让开纵向滚动条（否则点不到上箭头、拖不到最上端）。"""
        from zpymusic.ui.tab_play import ScoreViewHost

        view, _suite = _build_view(qapp)
        host = ScoreViewHost(view)
        host.resize(VIEW_W, VIEW_H)
        host.show()
        qapp.processEvents()

        sb = view.view.verticalScrollBar()
        assert sb.isVisible(), "这个尺寸下应该有纵向滚动条"
        sb_rect = QRect(
            sb.mapTo(host, QPoint(0, 0)),
            sb.mapTo(host, QPoint(sb.width(), sb.height())),
        )
        assert host.overlay_right_inset() >= sb.width()
        assert not host.zoom_bar.geometry().intersects(sb_rect), (
            f"缩放条 {host.zoom_bar.geometry()} 压住了滚动条 {sb_rect}"
        )


# ===========================================================================
# 问题 2：日志区上下文文本配色
# ===========================================================================
class TestLogContextContrast:
    def test_context_text_is_dark(self, qapp) -> None:  # noqa: ANN001
        """上下文行在浅色底上必须用深色字（原 #9cdcfe 亮度 0.78，几乎看不见）。"""
        from zpymusic.ui.log_dock import LogDock

        dock = LogDock()
        style = dock.context.styleSheet()
        m = re.search(r"color\s*:\s*(#[0-9a-fA-F]{6})", style)
        assert m, f"上下文的样式里应显式给出颜色：{style!r}"
        c = QColor(m.group(1))
        lum = 0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF()
        assert lum < 0.30, f"{m.group(1)} 亮度 {lum:.2f} 在浅色底上仍然偏亮"

    def test_set_context_still_fills_text(self, qapp) -> None:  # noqa: ANN001
        from zpymusic.ui.log_dock import LogDock

        dock = LogDock()
        dock.set_context(source="a.musicxml", target_dir="/tmp/x", engine="verovio 1.0")
        assert "a.musicxml" in dock.context.text()
        assert "verovio 1.0" in dock.context.text()


# ===========================================================================
# 问题 4：首屏就要"适应宽度"
# ===========================================================================
@needs_suite
class TestFitWidthOnFirstDisplay:
    @staticmethod
    def _expected(view: NativeScoreView) -> float:
        return (view.view.width() - view.overlay_right_inset() - 16) / view._scene.sceneRect().width()

    def test_fit_applied_when_widget_grows_before_show(self, qapp) -> None:  # noqa: ANN001
        """隐藏期间以默认尺寸 load，随后被布局放大 —— 必须自动重新适配。

        这就是用户看到的现象：默认 640×480 下 load，播放页首次显示时尺寸变成
        906×444，旧代码不重算 → 停留在被 MIN_ZOOM 夹住的 30%。
        """
        suite = Suite(SuiteLayout(root=SUITES, base="canon-in-d-easy"))
        doc = suite.load_sync()
        systems = [
            SystemRef(
                index=s.index, file=s.file, width=s.width, height=s.height,
                note_ids=list(s.note_ids),
            )
            for s in doc.systems
        ]
        view = NativeScoreView()
        view.resize(640, 480)
        view.load(suite.dir, systems)          # 未显示
        view.resize(906, 444)                  # 播放页首次显示时布局给的尺寸
        view.show()
        for _ in range(3):
            qapp.processEvents()

        expected = self._expected(view)
        assert view.zoom() == pytest.approx(expected, rel=0.02), (
            f"首屏应适应宽度：期望 {expected:.4f}，实际 {view.zoom():.4f}"
        )
        assert view.zoom() > 0.35, "旧实现会停在被 MIN_ZOOM 夹住的 0.30"

    def test_refit_on_window_resize_and_respects_manual_zoom(self, qapp) -> None:  # noqa: ANN001
        view, _suite = _build_view(qapp)
        view.resize(760, 380)
        qapp.processEvents()
        assert view.zoom() == pytest.approx(self._expected(view), rel=0.02)

        # 手动缩放会关掉"适应宽度"（ScoreViewHost 的行为）→ 改尺寸不得覆盖用户的缩放
        factor = view.zoom() * 0.95  # 注意别低于 MIN_ZOOM（0.3），否则会被夹取
        view.set_fit_width(False)
        view.set_zoom(factor)
        view.resize(980, 500)
        qapp.processEvents()
        assert view.zoom() == pytest.approx(factor, rel=1e-6)

        # 重新打开"适应宽度"应立即适配当前尺寸
        view.set_fit_width(True)
        assert view.zoom() == pytest.approx(self._expected(view), rel=0.02)

    def test_zoom_changed_signal_fires(self, qapp) -> None:  # noqa: ANN001
        """缩放工具条的百分比显示依赖这个信号（否则自动适配后数字还是旧的）。"""
        view, _suite = _build_view(qapp)
        seen: list[float] = []
        view.zoom_changed.connect(seen.append)
        view.resize(700, 360)
        qapp.processEvents()
        assert seen, "自动重新适配应发出 zoom_changed"
        assert seen[-1] == pytest.approx(view.zoom())

    def test_first_display_in_mainwindow_is_fit(self, qapp) -> None:  # noqa: ANN001
        """端到端：切到"同步播放"页时，第一首曲谱就已经是适应宽度。

        这里**显式钉住 ``native`` 后端**：用例断言的是原生后端的算式
        （``QGraphicsView`` 的 transform × sceneRect），而 ``auto`` 会随运行环境
        在 WebEngine / 原生之间摇摆（能不能启动 Chromium 取决于是否受限沙箱），
        让整条用例变成环境相关。不锁后端的版本见下一个用例。
        """
        from zpymusic.common.config import AppConfig
        from zpymusic.ui.main_window import MainWindow

        cfg = AppConfig.load()
        cfg.ui.score_backend = "native"
        win = MainWindow(cfg)
        try:
            win.resize(1280, 880)
            win.show()
            for _ in range(3):
                qapp.processEvents()
            win.tabs.setCurrentWidget(win.tab_play)
            for _ in range(3):
                qapp.processEvents()
            view = win.tab_play.score_view
            expected = self._expected(view)
            assert view.zoom() == pytest.approx(expected, rel=0.02)
            bar = win.tab_play.view_host.zoom_bar
            assert bar.btn_level.text() == f"{view.zoom() * 100:.0f}%", (
                "缩放条的百分比显示也要跟着自动适配更新"
            )
        finally:
            win.close()

    def test_first_display_fit_is_backend_agnostic(self, qapp) -> None:  # noqa: ANN001
        """端到端（**不锁定后端**）：首屏必须真的缩小过，且缩放条数字与视图一致。

        价值就在于"不假设后端"：默认配置是 ``auto``，在能启动 Chromium 的机器上
        拿到 ``WebScoreView``、在受限环境里回退 ``NativeScoreView`` —— 两者都必须
        满足同一组不变量。这条用例正是"WebEngine 后端漏实现适应宽度"那类事故的护栏
        （当时按钮勾着、谱面却停在 100%，两个后端都能跑、只有断言后端无关才抓得到）。
        """
        from zpymusic.common.config import AppConfig
        from zpymusic.ui.main_window import MainWindow

        cfg = AppConfig()
        cfg.ui.score_backend = "auto"
        win = MainWindow(cfg)
        try:
            win.resize(1280, 880)
            win.show()
            for _ in range(3):
                qapp.processEvents()
            win.tabs.setCurrentWidget(win.tab_play)
            for _ in range(6):
                qapp.processEvents()

            view = win.tab_play.score_view
            bar = win.tab_play.view_host.zoom_bar
            assert view.fit_width_enabled is True, "默认应勾选「适应宽度」"
            assert bar.btn_fit.isChecked() is True
            assert view.zoom() < 1.0, (
                f"{type(view).__name__}：2100px 宽的谱面在 1280px 窗口里必须缩小，"
                f"不能停在 100%（实际 {view.zoom():.4f}）"
            )
            assert bar.btn_level.text() == f"{view.zoom() * 100:.0f}%"
        finally:
            win.close()


# ===========================================================================
# 问题 3：状态栏"当前套件"跟随播放页选择
# ===========================================================================
@needs_suite
class TestStatusBarTracksSelection:
    def test_statusbar_updates_on_suite_selection(self, qapp) -> None:  # noqa: ANN001
        """切换播放页里的套件，状态栏必须跟着变（原来永远是"未选择套件"）。"""
        from zpymusic.common.config import AppConfig
        from zpymusic.ui.main_window import MainWindow

        win = MainWindow(AppConfig.load())
        try:
            qapp.processEvents()
            assert win.lbl_current.text() != "未选择套件"
            assert "当前套件" in win.lbl_current.text()

            lst = win.tab_play.lst
            if lst.count() < 2:
                pytest.skip("套件不足 2 个，无法验证切换")
            first = win.lbl_current.text()
            lst.setCurrentRow(1)
            qapp.processEvents()
            assert win.lbl_current.text() != first
            assert win.tab_play.current_suite is not None
            assert win.tab_play.current_suite.base in win.lbl_current.text()
        finally:
            win.close()
