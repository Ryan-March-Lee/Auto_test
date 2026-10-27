"""功放安全掉电流程：尽力先 Drain、再 Gate，并汇总错误。"""

from __future__ import annotations

from typing import Callable, Mapping

from application.errors import CleanupError, FailureRecord, cause_of


class PowerOffFlowError(CleanupError):
    """安全掉电失败；``failures`` 逐项记录失败通道。"""


class PowerOffFlow:
    def __init__(self, channels: Mapping[str, object], *, sleep_fn: Callable[[float], None] | None = None,
                 settle_time_s: float = 2.0, event_sink: Callable[[str, str], None] | None = None):
        self.channels = dict(channels)
        if set(self.channels) != {"gate", "drain"}:
            raise ValueError("安全掉电必须同时提供 gate 和 drain 通道")
        self.sleep_fn = sleep_fn or (lambda _seconds: None)
        if settle_time_s < 0:
            raise ValueError("settle_time_s 必须大于或等于 0")
        self.settle_time_s = float(settle_time_s)
        self.event_sink = event_sink or (lambda _step, _status: None)

    def run(self, *, timeout_s: float = 5.0) -> tuple[str, ...]:
        failures: list[FailureRecord] = []
        completed: list[str] = []
        for role in ("drain", "gate"):
            name = f"{role}.output_off"
            self.event_sink(name, "started")
            try:
                self.channels[role].set_output_enabled(False, timeout_s=timeout_s)
            except Exception as error:  # 先关 drain 再关 gate，两项都必须尝试
                failures.append(FailureRecord(name, error))
                self.event_sink(name, "failed")
            else:
                completed.append(name)
                self.event_sink(name, "completed")
            if role == "drain":
                self.sleep_fn(self.settle_time_s)
        if failures:
            raise PowerOffFlowError("安全掉电存在失败", failures) from cause_of(failures)
        return tuple(completed)
