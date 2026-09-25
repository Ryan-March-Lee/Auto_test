"""与 UI 无关的普通停止和紧急停止协议。"""

from __future__ import annotations

from enum import Enum
from threading import Event, Lock


class CancellationMode(str, Enum):
    NORMAL = "normal"
    EMERGENCY = "emergency"


class MeasurementCancelled(Exception):
    """测量在安全边界或紧急路径被取消。"""

    def __init__(self, mode: CancellationMode, reason: str | None = None):
        self.mode = mode
        self.reason = reason or mode.value
        super().__init__(f"测量已取消 ({mode.value}): {self.reason}")


class CancellationToken:
    """可注入测量服务的线程安全取消令牌。

    普通停止要求服务在当前安全边界结束；紧急停止要求服务立即进入
    RF 优先的清理路径。令牌本身不执行硬件动作。
    """

    def __init__(self) -> None:
        self._event = Event()
        self._lock = Lock()
        self._mode: CancellationMode | None = None
        self._reason: str | None = None

    @property
    def mode(self) -> CancellationMode | None:
        with self._lock:
            return self._mode

    @property
    def reason(self) -> str | None:
        with self._lock:
            return self._reason

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self, mode: CancellationMode = CancellationMode.NORMAL, *, reason: str | None = None) -> None:
        """设置取消请求；已请求紧急停止时不能被普通停止降级。"""
        with self._lock:
            if self._mode is CancellationMode.EMERGENCY:
                return
            self._mode = mode
            self._reason = reason or mode.value
            self._event.set()

    def request_stop(self, *, reason: str | None = None) -> None:
        self.cancel(CancellationMode.NORMAL, reason=reason)

    def request_emergency_stop(self, *, reason: str | None = None) -> None:
        self.cancel(CancellationMode.EMERGENCY, reason=reason)

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise MeasurementCancelled(self.mode or CancellationMode.NORMAL, self.reason)
