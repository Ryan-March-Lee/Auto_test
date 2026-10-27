"""统一的 RF -> 电源 -> 连接安全清理流程。"""

from __future__ import annotations

from typing import Callable

from ..safety import close_accepts_safety_flags


class SafetyShutdownError(RuntimeError):
    def __init__(self, message: str, errors: list[BaseException]):
        super().__init__(message)
        self.errors = tuple(errors)


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
        errors: list[BaseException] = []
        rf_safe = self.rf_off is None
        power_safe = self.power_off is None
        if self.rf_off is not None:
            try:
                self.rf_off()
                rf_safe = True
            except Exception as error:
                errors.append(error)
        if self.power_off is not None:
            try:
                self.power_off()
                power_safe = True
            except Exception as error:
                errors.append(error)
        if self.close is not None and close_resource:
            try:
                if self._close_accepts_flags:
                    close_errors = self.close(rf_safe=rf_safe, power_safe=power_safe)
                else:
                    # 旧式 close_all(close_rf=False) 入口：RF 未确认关闭时不调用。
                    close_errors = self.close(close_rf=False) if rf_safe else None
                if close_errors:
                    errors.extend(close_errors)
            except Exception as error:
                errors.append(error)
        if errors:
            raise SafetyShutdownError("安全清理存在失败: " + "; ".join(map(str, errors)), errors) from errors[0]
        self._cleaned = True
