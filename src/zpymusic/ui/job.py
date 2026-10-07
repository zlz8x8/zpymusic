"""单次后台作业（非批量），如启动时的环境探测（需求 M0）。

比 :class:`~zpymusic.ui.worker.BatchRunner` 更轻量：一次调用 + 一个结果。
"""

from __future__ import annotations

import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal

from ..common.log import get_logger

log = get_logger(__name__)


class _JobWorker(QObject):
    done = Signal(object, str)

    def __init__(self, fn: Callable[[], Any]) -> None:
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            self.done.emit(self._fn(), "")
        except Exception as e:  # noqa: BLE001
            log.exception("后台作业失败")
            self.done.emit(None, f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}")


class Job(QObject):
    """在独立线程里跑一个函数，完成后在 UI 线程发 ``finished``。

    用法::

        self._job = Job(probe_all, parent=self)
        self._job.finished.connect(self._on_probed)
        self._job.start()
    """

    finished = Signal(object, str)  # result, error

    def __init__(self, fn: Callable[[], Any], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._fn = fn
        self._thread: QThread | None = None
        self._worker: _JobWorker | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def start(self) -> None:
        if self.is_running:
            return
        self._thread = QThread()
        self._worker = _JobWorker(self._fn)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.done.connect(self._on_done)
        self._thread.start()

    def wait(self, timeout_ms: int = 15_000) -> bool:
        return True if self._thread is None else self._thread.wait(timeout_ms)

    def _on_done(self, result: object, error: str) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(5_000)
            self._thread = None
            self._worker = None
        self.finished.emit(result, error)
