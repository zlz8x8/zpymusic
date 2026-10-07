"""把任意 :class:`~zpymusic.sync.score_view.ScoreView` 包成普通 QWidget，并浮一个缩放工具条。

**为什么单独放一个模块**：播放页（:mod:`zpymusic.ui.tab_play`）与源文件预览
（:mod:`zpymusic.ui.preview`）都要用它 —— 预览窗口复用同一套"适应宽度 / 缩放 /
滚动 + 懒加载"逻辑，就不必再写一遍曲谱视图的容器代码。``tab_play`` 仍然
``from .score_host import ScoreViewHost`` 再导出，保持既有导入路径可用。
"""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from .zoom_bar import ZoomBar


class ScoreViewHost(QWidget):
    """把任意 :class:`ScoreView` 包成普通 QWidget，并在右上角浮一个缩放工具条。

    存在的理由：WebEngine 后端返回的不是 QWidget（自带 ``widget`` 属性），
    另外缩放控件要叠在曲谱之上，用覆盖式定位比塞进布局更省空间。
    """

    def __init__(self, score_view, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.score_view = score_view
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        inner = getattr(score_view, "widget", score_view)
        if isinstance(inner, QWidget):
            layout.addWidget(inner)

        # 缩放工具条：只有支持 set_zoom 的后端才可用
        self.zoom_bar = ZoomBar(self, fit_default=getattr(score_view, "_fit_width", True))
        self.zoom_bar.zoom_changed.connect(self._apply_zoom)
        self.zoom_bar.fit_width_changed.connect(self._apply_fit_width)
        if hasattr(score_view, "set_zoom"):
            init = score_view.zoom() if hasattr(score_view, "zoom") else 1.0
            self.zoom_bar.set_zoom_display(init)
            # 视图自己重算缩放时（首次显示、窗口尺寸变化后的"适应宽度"）同步百分比，
            # 否则数字会停在旧值上，看起来像"没有适应宽度"。
            zoom_changed = getattr(score_view, "zoom_changed", None)
            if zoom_changed is not None:
                zoom_changed.connect(self.zoom_bar.set_zoom_display)
        else:
            self.zoom_bar.set_available(False, "该后端不支持缩放")
        self.zoom_bar.reposition()

    def _apply_zoom(self, factor: float) -> None:
        if not hasattr(self.score_view, "set_zoom"):
            return
        # 手动缩放时自动取消"适应宽度"，否则缩放会被立刻覆盖
        if hasattr(self.score_view, "set_fit_width"):
            self.score_view.set_fit_width(False)
        if self.zoom_bar.btn_fit.isChecked():
            self.zoom_bar.btn_fit.blockSignals(True)
            self.zoom_bar.btn_fit.setChecked(False)
            self.zoom_bar.btn_fit.blockSignals(False)
        self.score_view.set_zoom(factor)
        if hasattr(self.score_view, "zoom"):
            self.zoom_bar.set_zoom_display(self.score_view.zoom())

    def _apply_fit_width(self, on: bool) -> None:
        if not hasattr(self.score_view, "set_fit_width"):
            return
        self.score_view.set_fit_width(on)
        if on and hasattr(self.score_view, "zoom"):
            self.zoom_bar.set_zoom_display(self.score_view.zoom())

    def refresh_zoom_display(self) -> None:
        if hasattr(self.score_view, "zoom"):
            self.zoom_bar.set_zoom_display(self.score_view.zoom())

    def overlay_right_inset(self) -> int:
        """缩放条右侧要让开的像素（曲谱视图的纵向滚动条宽度）。

        否则缩放条会压住滚动条的顶端，"适应宽度"按钮看起来把滚动条挤掉了一截，
        用户既点不到上箭头也拖不到最上端。
        """
        fn = getattr(self.score_view, "overlay_right_inset", None)
        if not callable(fn):
            return 0
        try:
            return max(0, int(fn()))
        except Exception:  # noqa: BLE001 - 定位失败不该影响界面
            return 0

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self.zoom_bar.reposition()
