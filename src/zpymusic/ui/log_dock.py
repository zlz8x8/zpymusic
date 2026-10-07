"""把 ``logging`` 记录接到 GUI 日志区（需求 F4.3 / F4.4）。

安装一个 :class:`QtLogHandler`，按级别着色，并提供一个可折叠、可过滤、可导出的
``LogDock``。用法::

    dock = LogDock()
    install_qt_log_handler(dock)
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..common.log import DATE_FORMAT, get_logger
from ..common.paths import LOGS_DIR

log = get_logger(__name__)

LEVEL_COLORS = {
    logging.DEBUG: "#8a8a8a",
    logging.INFO: "#d4d4d4",
    logging.WARNING: "#e6b422",
    logging.ERROR: "#e05252",
    logging.CRITICAL: "#ff3b3b",
}
LEVEL_NAMES = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "警告",
    logging.ERROR: "错误",
    logging.CRITICAL: "严重",
}


class _Bridge(QObject):
    """把任意线程的日志记录安全地投递到 UI 线程。"""

    record = Signal(str, int, str)


class QtLogHandler(logging.Handler):
    """把日志写进 :class:`LogDock` 的 handler。"""

    def __init__(self, sink: "LogDock", level: int = logging.INFO) -> None:
        super().__init__(level)
        self._bridge = _Bridge()
        self._bridge.record.connect(sink.append_record)
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = self.format(record)
            self._bridge.record.emit(text, record.levelno, record.name)
        except Exception:  # noqa: BLE001 - 日志失败不能影响主流程
            self.handleError(record)


class LogDock(QDockWidget):
    """警告 / 信息 / 日志展示区。"""

    def __init__(self, parent: QWidget | None = None, *, min_level: int = logging.INFO) -> None:
        super().__init__("日志 / 信息 / 警告", parent)
        self.setObjectName("logDock")
        self.setAllowedAreas(Qt.AllDockWidgetAreas)
        self._min_level = min_level
        self._show_debug = False
        self._counter: dict[int, int] = {}

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # --- 工具条 -------------------------------------------------------
        bar = QHBoxLayout()
        self.level_box = QComboBox()
        for name, lvl in (("全部", logging.DEBUG), ("信息及以上", logging.INFO),
                          ("仅警告与错误", logging.WARNING), ("仅错误", logging.ERROR)):
            self.level_box.addItem(name, lvl)
        self.level_box.setCurrentIndex(1)
        self.level_box.currentIndexChanged.connect(self._on_level_changed)
        bar.addWidget(QLabel("级别："))
        bar.addWidget(self.level_box)

        self.autoscroll = QCheckBox("自动滚动")
        self.autoscroll.setChecked(True)
        bar.addWidget(self.autoscroll)

        self.stats = QLabel("")
        self.stats.setStyleSheet("color:#888;")
        bar.addWidget(self.stats)
        bar.addStretch(1)

        btn_clear = QPushButton("清空")
        btn_clear.clicked.connect(self.clear)
        bar.addWidget(btn_clear)

        btn_export = QPushButton("导出…")
        btn_export.clicked.connect(self._export)
        bar.addWidget(btn_export)

        btn_open = QPushButton("打开日志目录")
        btn_open.clicked.connect(self._open_log_dir)
        bar.addWidget(btn_open)
        layout.addLayout(bar)

        # --- 文本区 -------------------------------------------------------
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(5000)  # 防止长任务把内存吃满
        self.view.setStyleSheet(
            "QPlainTextEdit{background:#1e1e1e;color:#d4d4d4;"
            "font-family:Consolas,'Cascadia Mono',monospace;font-size:9pt;}"
        )
        layout.addWidget(self.view, 1)
        self.setWidget(root)

        # 记录当前源文件名 / 目标目录 / 目标文件名 / 音色库（需求 F4.4）
        # 注意配色：这行**不在**深色日志框里，而是在 QDockWidget 的浅色底上，
        # 原先用 #9cdcfe（VS Code 深色主题的浅蓝）在浅色底上几乎看不清。
        self.context = QLabel("")
        self.context.setObjectName("logContext")
        self.context.setStyleSheet(
            "#logContext{color:#0b3d91;background:#eef2f7;border:1px solid #d6dde7;"
            "border-radius:3px;padding:2px 6px;}"
        )
        self.context.setWordWrap(True)
        self.context.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.context.setToolTip("当前源文件 / 目标目录 / 目标文件 / 音色库 / 引擎")
        root.layout().addWidget(self.context)

    # ------------------------------------------------------------------ 接口
    def set_context(self, **kwargs: object) -> None:
        """更新"当前上下文"一行（源文件 / 目标目录 / 目标文件 / 音色库 / 引擎）。"""
        order = [
            ("源文件", kwargs.get("source")),
            ("目标目录", kwargs.get("target_dir")),
            ("目标文件", kwargs.get("target_files")),
            ("音色库", kwargs.get("soundfont")),
            ("引擎", kwargs.get("engine")),
        ]
        parts = [f"{k}: {v}" for k, v in order if v]
        self.context.setText("  |  ".join(parts))

    def append_record(self, text: str, level: int, name: str) -> None:
        """由 :class:`QtLogHandler` 在 UI 线程调用。"""
        self._counter[level] = self._counter.get(level, 0) + 1
        self._update_stats()
        if not self._should_show(level):
            return
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(LEVEL_COLORS.get(level, "#d4d4d4")))
        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text + "\n", fmt)
        if self.autoscroll.isChecked():
            self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

    def add_note(self, level: str, text: str) -> None:
        """外部（非 logging）直接追加一条。"""
        mapping = {
            "info": logging.INFO,
            "warning": logging.WARNING,
            "error": logging.ERROR,
            "debug": logging.DEBUG,
        }
        lvl = mapping.get(level, logging.INFO)
        stamp = datetime.now().strftime(DATE_FORMAT)
        self.append_record(f"{stamp} {LEVEL_NAMES.get(lvl, 'INFO'):<4} ui: {text}", lvl, "ui")

    def clear(self) -> None:
        self.view.clear()
        self._counter.clear()
        self._update_stats()

    # ------------------------------------------------------------------ 内部
    def _should_show(self, level: int) -> bool:
        if level == logging.DEBUG and not self._show_debug:
            return False
        return level >= int(self.level_box.currentData())

    def _on_level_changed(self) -> None:
        self.add_note("info", "日志级别过滤已切换（历史记录不受影响）")

    def _update_stats(self) -> None:
        w = self._counter.get(logging.WARNING, 0)
        e = self._counter.get(logging.ERROR, 0) + self._counter.get(logging.CRITICAL, 0)
        text = f"警告 {w} / 错误 {e}"
        self.stats.setText(text)
        self.stats.setStyleSheet("color:#e6b422;" if w or e else "color:#888;")

    def _export(self) -> None:
        default = str(Path.home() / f"zpymusic_log_{datetime.now():%Y%m%d_%H%M%S}.log")
        path, _ = QFileDialog.getSaveFileName(self, "导出日志", default, "日志文件 (*.log *.txt)")
        if not path:
            return
        try:
            Path(path).write_text(self.view.toPlainText(), encoding="utf-8")
            self.add_note("info", f"日志已导出：{path}")
        except OSError as e:
            self.add_note("error", f"导出失败：{e}")

    def _open_log_dir(self) -> None:
        import os  # noqa: PLC0415
        import subprocess  # noqa: PLC0415
        import sys  # noqa: PLC0415

        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(LOGS_DIR))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(LOGS_DIR)])
            else:
                subprocess.Popen(["xdg-open", str(LOGS_DIR)])
        except OSError as e:
            self.add_note("error", f"无法打开日志目录：{e}")


def install_qt_log_handler(dock: LogDock) -> QtLogHandler:
    """把 handler 挂到根 logger 上并返回它（供后续移除）。"""
    handler = QtLogHandler(dock, level=logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)-26s %(message)s", DATE_FORMAT))
    logging.getLogger().addHandler(handler)
    return handler
