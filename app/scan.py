"""把耗时采集挪出 UI 线程。

`psutil.process_iter()` 要遍历全系统进程表，机器上进程一多就是 100–500ms。
放在主线程里（Qt 槽函数）会直接表现为界面卡顿、动画掉帧。

这里提供一个极小的线程池封装：
  - `run(token, fn, on_done)`：`fn` 在工作线程跑，`on_done(result)` 回到主线程执行；
  - 同一个 token 在跑完之前重复 `run()` 会被忽略，避免反复点按钮时堆积任务；
  - `fn` 抛异常时 `result` 为 `None`，由调用方决定怎么兜底；
  - 信号强制用 `QueuedConnection`，确保回调一定在主线程，不碰 Qt 的线程亲和性红线。
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal


class _Task(QRunnable):
    class Signals(QObject):
        done = Signal(str, object)

    def __init__(self, token: str, fn: Callable[[], Any]) -> None:
        super().__init__()
        self.token = token
        self.fn = fn
        self.signals = _Task.Signals()
        self.setAutoDelete(True)

    def run(self) -> None:  # 工作线程
        try:
            result = self.fn()
        except Exception:
            result = None
        try:
            self.signals.done.emit(self.token, result)
        except Exception:
            pass


class AsyncRunner(QObject):
    """一次性后台采集：工作线程跑 fn，主线程收结果。"""

    def __init__(self, parent: QObject | None = None, max_threads: int = 2) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max(1, int(max_threads)))
        self._tasks: dict[str, _Task] = {}
        self._handlers: dict[str, Callable[[Any], None]] = {}
        self._closed = False

    def is_busy(self, token: str) -> bool:
        return token in self._tasks

    def run(
        self, token: str, fn: Callable[[], Any], on_done: Callable[[Any], None]
    ) -> bool:
        """提交任务；同 token 未完成时返回 False。"""
        if self._closed or token in self._tasks:
            return False
        task = _Task(token, fn)
        task.signals.done.connect(self._on_done, Qt.QueuedConnection)
        self._tasks[token] = task
        self._handlers[token] = on_done
        try:
            self._pool.start(task)
        except Exception:
            self._tasks.pop(token, None)
            self._handlers.pop(token, None)
            return False
        return True

    def _on_done(self, token: str, result: Any) -> None:
        self._tasks.pop(token, None)
        handler = self._handlers.pop(token, None)
        if handler is None or self._closed:
            return
        try:
            handler(result)
        except Exception:
            pass

    def shutdown(self, timeout_ms: int = 1500) -> None:
        """退出前调用：丢弃未执行的任务，等正在跑的最多 timeout_ms。"""
        self._closed = True
        self._handlers.clear()
        self._tasks.clear()
        try:
            self._pool.clear()
        except Exception:
            pass
        try:
            self._pool.waitForDone(timeout_ms)
        except Exception:
            pass
