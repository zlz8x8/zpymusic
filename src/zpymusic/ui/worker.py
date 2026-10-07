"""后台任务与进度上报（需求 F1.9 / F3.5）。

约束与设计：

* **Verovio 的 toolkit 不是线程安全的**，因此每个工作线程各自创建自己的 toolkit
  （``render_score`` 内部就是这样做的），绝不跨线程共享实例。
* 任务体统一在 ``QThread`` 里跑，进度与消息通过 **Qt Signal** 回到 UI 线程；
  业务代码（``core.*``）只看到普通 Python 回调，从而保持"core 不依赖 Qt"。
* 取消采用**协作式**：设置一个 ``threading.Event``，任务在检查点主动退出。
"""

from __future__ import annotations

import threading
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from PySide6.QtCore import QObject, QThread, Signal

from ..common.log import get_logger
from ..common.errors import TaskCancelled

log = get_logger(__name__)


@dataclass
class TaskOutcome:
    """一个工作项的执行结果。"""

    key: str
    ok: bool
    payload: Any = None
    error: str = ""
    cancelled: bool = False
    warnings: list[str] = field(default_factory=list)


class _Worker(QObject):
    """在 QThread 中执行 ``fn(index, item)``。"""

    progress = Signal(int, int, str)  # done, total, stage
    message = Signal(str, str)  # level, text
    finished = Signal(object)  # TaskOutcome
    all_finished = Signal(list)  # list[TaskOutcome]

    def __init__(
        self,
        items: Sequence[Any],
        fn: Callable[[int, Any, Callable[[str, int, int], None], Callable[[], bool]], TaskOutcome],
        cancel_event: threading.Event,
    ) -> None:
        super().__init__()
        self._items = list(items)
        self._fn = fn
        self._cancel = cancel_event

    def run(self) -> None:
        outcomes: list[TaskOutcome] = []
        total = len(self._items)
        for i, item in enumerate(self._items):
            if self._cancel.is_set():
                outcomes.append(
                    TaskOutcome(key=str(item), ok=False, cancelled=True, error="已取消")
                )
                self.finished.emit(outcomes[-1])
                continue

            def report_progress(stage: str, done: int, count: int, _i: int = i) -> None:
                self.progress.emit(_i, count, f"[{_i + 1}/{total}] {stage} {done}/{count}")

            def report_message(level: str, text: str, _i: int = i) -> None:
                self.message.emit(level, f"[{_i + 1}/{total}] {text}")

            try:
                outcome = self._fn(i, item, report_progress, self._cancel.is_set)
            except TaskCancelled:
                outcome = TaskOutcome(key=str(item), ok=False, cancelled=True, error="已取消")
            except Exception as e:  # noqa: BLE001 - 单个任务失败不能中断批量
                log.exception("任务项失败：%s", item)
                outcome = TaskOutcome(
                    key=str(item),
                    ok=False,
                    error=f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}",
                )
            outcomes.append(outcome)
            self.finished.emit(outcome)
            if self._cancel.is_set():
                # 用户取消：剩余项标记为取消
                for rest in self._items[i + 1:]:
                    outcomes.append(
                        TaskOutcome(key=str(rest), ok=False, cancelled=True, error="已取消")
                    )
                break
        self.all_finished.emit(outcomes)


class BatchRunner(QObject):
    """批量任务的封装：管理 QThread 生命周期、取消与信号转发。

    用法::

        runner = BatchRunner(items, work_fn, parent=self)
        runner.progress.connect(self._on_progress)
        runner.item_done.connect(self._on_item)
        runner.finished.connect(self._on_all_done)
        runner.start()
    """

    progress = Signal(int, int, str)
    message = Signal(str, str)
    item_done = Signal(object)
    finished = Signal(list)

    def __init__(
        self,
        items: Sequence[Any],
        work_fn: Callable[[int, Any, Callable[[str, int, int], None], Callable[[], bool]], TaskOutcome],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._items = list(items)
        self._work_fn = work_fn
        self._cancel_event = threading.Event()
        self._thread: QThread | None = None
        self._worker: _Worker | None = None

    # ------------------------------------------------------------------ 状态
    @property
    def total(self) -> int:
        return len(self._items)

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    @property
    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    # ------------------------------------------------------------------ 控制
    def start(self) -> None:
        if self.is_running:
            log.warning("批量任务已在运行，忽略重复启动")
            return
        self._cancel_event.clear()
        self._thread = QThread()
        self._worker = _Worker(self._items, self._work_fn, self._cancel_event)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self.progress)
        self._worker.message.connect(self.message)
        self._worker.finished.connect(self.item_done)
        self._worker.all_finished.connect(self._on_all_finished)
        self._thread.start()

    def cancel(self) -> None:
        """请求取消（协作式；当前文件会尽快退出）。"""
        self._cancel_event.set()
        log.info("已请求取消，等待当前步骤退出…")

    def wait(self, timeout_ms: int = 10_000) -> bool:
        if self._thread is None:
            return True
        return self._thread.wait(timeout_ms)

    def _on_all_finished(self, outcomes: list) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(5_000)
            self._thread = None
            self._worker = None
        self.finished.emit(outcomes)
