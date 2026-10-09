"""设备安全动作和资源所有权边界。"""

from __future__ import annotations


class InstrumentSafetyError(RuntimeError):
    def __init__(self, message: str, errors: list[BaseException]):
        super().__init__(message)
        self.errors = tuple(errors)


def shutdown_instruments(*, rf_off=None, power_off=None, close=None,
                         emergency: bool = False) -> None:
    """统一执行 RF、供电和连接清理。

    ``close`` 只应关闭本 session 创建并拥有的资源。外部注入的端口由其
    创建方负责关闭；因此所有权通过是否传入 ``close`` 显式表达。
    紧急停止与普通停止都优先 RF 关闭，但紧急停止允许从任意状态进入。
    """
    del emergency  # 预留给调用方记录策略，当前两种路径都必须先断 RF。
    errors: list[BaseException] = []
    rf_safe = rf_off is None
    power_safe = power_off is None
    if rf_off is not None:
        try:
            rf_off()
            rf_safe = True
        except Exception as error:
            errors.append(error)
    if power_off is not None:
        try:
            power_off()
            power_safe = True
        except Exception as error:
            errors.append(error)
    # 连接清理仍然要尽力执行：设备动作失败不应把连接泄漏到进程之外。
    # close 实现可以根据 rf_safe/power_safe 决定哪些危险资源保留重试，
    # 但安全编排器不能静默跳过它。
    if close is not None:
        try:
            result = close(rf_safe=rf_safe, power_safe=power_safe)
            if result:
                errors.extend(result)
        except TypeError:
            try:
                result = close()
                if result:
                    errors.extend(result)
            except Exception as error:
                errors.append(error)
        except Exception as error:
            errors.append(error)
    if errors:
        raise InstrumentSafetyError("设备安全清理存在失败", errors) from errors[0]
