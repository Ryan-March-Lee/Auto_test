"""统一的 RF -> 电源 -> 连接安全清理流程。"""

from __future__ import annotations

from typing import Callable

from application.errors import CleanupError, FailureRecord, cause_of

from ..safety import close_accepts_safety_flags


class SafetyShutdownError(CleanupError):
    """安全清理失败；``failures`` 逐项记录动作名与原始异常。"""


class SafetyShutdownFlow:
    """所有退出路径共用的尽力清理入口。

    动作顺序固定为 RF -> 电源 -> 连接，任一动作失败都不阻止后续安全动作。
    连接清理始终执行，并把 ``rf_safe`` / ``power_safe`` 交给 ``close`` 实现：

    - RF 关闭失败（``rf_safe=False``）时保留信号源连接，现场可以重试关闭 RF；
    - 电源关闭失败（``power_safe=False``）时仍释放连接：已经处于异常状态的
      VISA 会话不能再被后续安全动作依赖（见 ``ManagedInstrumentSession.close``）。

    ``close`` 的调用形式按签名判定（带安全标记，或旧式 ``close_all(close_rf=False)``），
    不通过捕获 ``TypeError`` 猜测签名，避免把设备清理内部的真实 ``TypeError``
    误当成签名不兼容而重复执行清理。

    每个失败动作都记录成 :class:`application.errors.FailureRecord`（``rf_output`` /
    ``power_supplies`` / ``resources``），多故障一次聚合上报；成功后再次调用是
    无操作，失败后再次调用会重新尝试全部动作。
    """

    def __init__(self, *, rf_off: Callable[[], None] | None = None,
                 power_off: Callable[[], None] | None = None,
                 close: Callable[..., list[BaseException] | None] | None = None):
        self.rf_off = rf_off
        self.power_off = power_off
        self.close = close
        self._close_accepts_flags = close is not None and close_accepts_safety_flags(close)
        self._cleaned = False

    def run(self, *, close_resource: bool = True) -> None:
        if self._cleaned:
            return
        failures: list[FailureRecord] = []
        rf_safe = self.rf_off is None
        power_safe = self.power_off is None
        if self.rf_off is not None:
            try:
                self.rf_off()
                rf_safe = True
            except Exception as error:  # 清理动作必须聚合任意设备错误后继续
                failures.append(FailureRecord("rf_output", error))
        if self.power_off is not None:
            try:
                self.power_off()
                power_safe = True
            except Exception as error:  # 清理动作必须聚合任意设备错误后继续
                failures.append(FailureRecord("power_supplies", error))
        if self.close is not None and close_resource:
            try:
                if self._close_accepts_flags:
                    close_errors = self.close(rf_safe=rf_safe, power_safe=power_safe)
                else:
                    # 旧式 close_all(close_rf=False) 入口：RF 未确认关闭时不调用。
                    close_errors = self.close(close_rf=False) if rf_safe else None
                if close_errors:
                    failures.extend(
                        FailureRecord("resources", error) for error in close_errors
                    )
            except Exception as error:  # 连接释放失败同样进入聚合记录
                failures.append(FailureRecord("resources", error))
        if failures:
            raise SafetyShutdownError("安全清理存在失败", failures) from cause_of(failures)
        self._cleaned = True
