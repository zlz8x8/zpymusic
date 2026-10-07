"""``zpymusic.ui`` —— PySide6 界面层。

约定：本包可以依赖 :mod:`zpymusic.core` 与 :mod:`zpymusic.sync`，
但**反向依赖是不允许的**（core / sync 不得导入 PySide6）。
"""

from __future__ import annotations

from .log_dock import LogDock, install_qt_log_handler

__all__ = ["LogDock", "install_qt_log_handler", "run_gui"]


def run_gui(argv: list[str] | None = None) -> int:
    """延迟导入，避免 ``import zpymusic.ui`` 时就把 Qt 全量拉起来。"""
    from .main_window import run_gui as _run

    return _run(argv)
