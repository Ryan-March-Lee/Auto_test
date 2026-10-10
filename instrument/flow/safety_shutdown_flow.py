"""统一的 RF -> 电源 -> 连接安全清理流程。"""

from __future__ import annotations

from typing import Callable


class SafetyShutdownError(RuntimeError):
    def __init__(self, message: str, errors: list[BaseException]):
        super().__init__(message)
        self.errors = tuple(errors)


class SafetyShutdownFlow:
    """所有退出路径共用的尽力清理入口。

    连接关闭仅在对应的安全动作成功后执行：RF 关闭失败时不关闭信号源连接，
    电源关闭失败时不关闭电源连接，从而保留重试机会并避免违反安全不变量。
    """

    def __init__(self, *, rf_off: Callable[[], None] | None = None,
                 power_off: Callable[[], None] | None = None,
                 close: Callable[..., list[BaseException] | None] | None = None):
        self.rf_off = rf_off
        self.power_off = power_off
        self.close = close
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
                close_errors = self.close(rf_safe=rf_safe, power_safe=power_safe)
                if close_errors:
                    errors.extend(close_errors)
            except TypeError:
                # 兼容旧 close_all(close_rf=False) 入口；安全 flow 仍保证 RF
                # 优先，只在 RF 已安全时才允许旧入口关闭连接。
                try:
                    close_errors = self.close(close_rf=False) if rf_safe else []
                    if close_errors:
                        errors.extend(close_errors)
                except Exception as error:
                    errors.append(error)
            except Exception as error:
                errors.append(error)
        if errors:
            raise SafetyShutdownError("安全清理存在失败: " + "; ".join(map(str, errors)), errors) from errors[0]
        self._cleaned = True
