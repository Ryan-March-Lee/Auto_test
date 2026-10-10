"""Worker 信号绑定和引用清理协调器。"""

from __future__ import annotations

from typing import Any, Callable


class WorkerLifecycleCoordinator:
    """把 Qt worker 的连接细节从 controller 中隔离出来。"""

    def __init__(self, handlers: dict[str, Callable[..., Any]], thread_finished: Callable[[], Any]):
        self._handlers = handlers
        self._thread_finished = thread_finished
        self._slots: list[tuple[Any, Callable[..., Any]]] = []
        self._thread_signal: Any = None

    @property
    def thread_signal(self) -> Any:
        return self._thread_signal

    def bind(self, worker: Any) -> None:
        if self._slots or self._thread_signal is not None:
            self.disconnect()
        for name, slot in self._handlers.items():
            signal = getattr(worker.signals, name, None)
            if signal is not None:
                signal.connect(slot)
                self._slots.append((signal, slot))
        signal = getattr(worker, "finished", None)
        worker_signal = getattr(worker.signals, "finished", None)
        if signal is not None and signal is not worker_signal:
            signal.connect(self._thread_finished)
            self._thread_signal = signal

    def disconnect(self) -> None:
        for signal, slot in self._slots:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError, ValueError):
                pass
        self._slots.clear()
        if self._thread_signal is not None:
            try:
                self._thread_signal.disconnect(self._thread_finished)
            except (RuntimeError, TypeError, ValueError):
                pass
            self._thread_signal = None


__all__ = ["WorkerLifecycleCoordinator"]
