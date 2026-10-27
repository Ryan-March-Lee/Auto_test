"""设备安全动作和资源所有权边界。"""

from __future__ import annotations

import inspect

from application.errors import CleanupError, FailureRecord, cause_of


class InstrumentSafetyError(CleanupError):
    """设备安全清理失败；``failures`` 逐项记录失败动作。"""


def close_accepts_safety_flags(close: object) -> bool:
    """判断 ``close`` 回调是否接受 ``rf_safe`` / ``power_safe`` 安全标记。

    安全标记决定连接清理时哪些危险资源必须保留以便现场重试，因此调用形式必须
    通过签名显式判定，不能依赖“先调用、捕获 ``TypeError`` 再重试”的猜测方式：
    设备清理内部抛出的 ``TypeError`` 是真实失败，必须原样上报，不能被误判成
    签名不兼容而换成另一种调用形式重试。

    无法内省的 C 扩展或内置可调用对象按端口契约（带安全标记）调用，失败由
    调用方记录为真实清理错误。
    """
    try:
        parameters = inspect.signature(close).parameters
    except (TypeError, ValueError):
        return True
    for parameter in parameters.values():
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            return True
        if parameter.name in ("rf_safe", "power_safe"):
            return True
    return False


def shutdown_instruments(*, rf_off=None, power_off=None, close=None,
                         emergency: bool = False) -> None:
    """统一执行 RF、供电和连接清理。

    ``close`` 只应关闭本 session 创建并拥有的资源。外部注入的端口由其
    创建方负责关闭；因此所有权通过是否传入 ``close`` 显式表达。
    紧急停止与普通停止都优先 RF 关闭，但紧急停止允许从任意状态进入。

    连接清理始终尽力执行，不会因为 RF 或供电动作失败而跳过；``close`` 接受
    ``rf_safe`` / ``power_safe`` 时可据此决定保留哪些危险资源，见
    :func:`close_accepts_safety_flags`。

    每个失败动作都以 :class:`application.errors.FailureRecord` 记录动作名，
    最终一次性抛出 :class:`InstrumentSafetyError`；任何失败都不会被丢弃。
    """
    del emergency  # 预留给调用方记录策略，当前两种路径都必须先断 RF。
    failures: list[FailureRecord] = []
    rf_safe = rf_off is None
    power_safe = power_off is None
    if rf_off is not None:
        try:
            rf_off()
            rf_safe = True
        except Exception as error:  # 清理动作必须聚合任意设备错误后继续
            failures.append(FailureRecord("rf_output", error))
    if power_off is not None:
        try:
            power_off()
            power_safe = True
        except Exception as error:  # 清理动作必须聚合任意设备错误后继续
            failures.append(FailureRecord("power_supplies", error))
    if close is not None:
        try:
            if close_accepts_safety_flags(close):
                result = close(rf_safe=rf_safe, power_safe=power_safe)
            else:
                result = close()
            if result:
                failures.extend(FailureRecord("resources", error) for error in result)
        except Exception as error:  # 连接释放失败同样进入聚合记录
            failures.append(FailureRecord("resources", error))
    if failures:
        raise InstrumentSafetyError("设备安全清理存在失败", failures) from cause_of(failures)
