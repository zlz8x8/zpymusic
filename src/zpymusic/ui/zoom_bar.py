"""曲谱视图的缩放工具条（需求 §5.3 补充：解决"不能手动缩小"）。

浮动在曲谱视图右上角，提供：

* ``−`` / ``+`` 缩放（30%–400%）
* 百分比按钮：点击回到 100%
* **适应宽度**：勾选后谱面始终铺满视口宽度；这是解决
  "按固定页宽（2100px）渲染的 SVG 在窄窗口里横向滚动、两侧大量留白"的关键按钮

键盘快捷键在播放页注册（``Ctrl+=`` / ``Ctrl+-`` / ``Ctrl+0`` / ``Ctrl+W``）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..common.log import get_logger

log = get_logger(__name__)

__all__ = ["ZoomBar"]


class ZoomBar(QFrame):
    """浮动缩放控制条。"""

    zoom_changed = Signal(float)
    fit_width_changed = Signal(bool)

    def __init__(self, parent: QWidget, *, fit_default: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("zoomBar")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "#zoomBar{background:rgba(250,250,250,235);border:1px solid #bbb;"
            "border-radius:6px;}"
            "#zoomBar QPushButton{padding:2px 8px;}"
        )
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 3, 6, 3)
        lay.setSpacing(4)

        self.btn_out = QPushButton("−")
        self.btn_out.setFixedWidth(28)
        self.btn_out.setToolTip("缩小（Ctrl+-）")
        lay.addWidget(self.btn_out)

        self.btn_level = QPushButton("100%")
        self.btn_level.setFixedWidth(58)
        self.btn_level.setToolTip("点击恢复 100%（Ctrl+0）")
        lay.addWidget(self.btn_level)

        self.btn_in = QPushButton("+")
        self.btn_in.setFixedWidth(28)
        self.btn_in.setToolTip("放大（Ctrl+=）")
        lay.addWidget(self.btn_in)

        self.btn_fit = QPushButton("适应宽度")
        self.btn_fit.setCheckable(True)
        self.btn_fit.setChecked(fit_default)
        self.btn_fit.setToolTip(
            "让谱面始终铺满窗口宽度。\n"
            "SVG 是按固定页面宽度渲染的（默认 2100px），\n"
            "在窄窗口里 1:1 显示会出现横向滚动条与两侧留白。"
        )
        lay.addWidget(self.btn_fit)

        self.hint = QLabel("")
        self.hint.setStyleSheet("color:#888;font-size:8pt;")
        lay.addWidget(self.hint)

        self.btn_out.clicked.connect(lambda: self._step(1 / 1.15))
        self.btn_in.clicked.connect(lambda: self._step(1.15))
        self.btn_level.clicked.connect(lambda: self.zoom_changed.emit(1.0))
        self.btn_fit.toggled.connect(self.fit_width_changed)

        self._zoom = 1.0

    # ------------------------------------------------------------------ 接口
    def set_zoom_display(self, factor: float) -> None:
        self._zoom = float(factor)
        self.btn_level.setText(f"{factor * 100:.0f}%")

    def _step(self, factor: float) -> None:
        self.zoom_changed.emit(self._zoom * factor)

    def reposition(self) -> None:
        """贴到父控件右上角（并让开右侧的纵向滚动条）。"""
        parent = self.parentWidget()
        if parent is None:
            return
        self.adjustSize()
        margin = 10
        self.move(max(0, parent.width() - self.width() - margin - _right_inset(parent)), margin)

    def set_available(self, ok: bool, reason: str = "") -> None:
        """不支持缩放的视图（如 WebEngine 后端）里禁用并说明。"""
        for w in (self.btn_out, self.btn_in, self.btn_level, self.btn_fit):
            w.setEnabled(ok)
        self.hint.setText(reason)


def _right_inset(parent: QWidget) -> int:
    """问父控件（曲谱视图宿主）右侧要让开多少像素。

    宿主会把这个问题转给曲谱视图 —— 只有视图知道自己的纵向滚动条有多宽。
    """
    fn = getattr(parent, "overlay_right_inset", None)
    if not callable(fn):
        return 0
    try:
        return max(0, int(fn()))
    except Exception:  # noqa: BLE001 - 定位失败不该影响界面
        return 0
