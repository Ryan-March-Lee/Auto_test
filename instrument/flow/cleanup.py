"""测量流程的统一安全清理包装与清理端口适配器。

本模块属于设备安全动作层：它把任意测量仪器控制器按
RF -> 电源 -> 连接 的顺序做尽力清理，并汇总所有失败，语义与
:class:`SafetyShutdownFlow` 保持一致。

:class:`SafetyCleanupAdapter` 把该设备安全流程接到 application 的安全清理
端口（``application.ports.measurement_session.SafetyCleanupPort``）：application
只保存协议引用，不导入本模块的具体实现。
"""

from __future__ import annotations

from typing import Any, Callable, List

from application.errors import FailureRecord, cause_of
from application.ports.measurement_session import (
    CleanupRecord,
    CleanupStepStatus,
    SafetyCleanupError,
)
from infrastructure.logging.app_logging import get_logger

from .safety_shutdown_flow import SafetyShutdownError, SafetyShutdownFlow
from ..safety import InstrumentSafetyError


logger = get_logger(__name__)


def cleanup_measurement(
    instrument_control: Any,
    *,
    power_cleanup: Callable[[], None] | None = None,
    safety_flow: SafetyShutdownFlow | None = None,
    close_resource: bool = True,
) -> None:
    """按 RF -> 电源 -> 连接顺序尽力清理，并汇总所有失败。"""
    if safety_flow is not None:
        safety_flow.run(close_resource=close_resource)
        return

    failures: List[FailureRecord] = []
    logger.info("测量安全清理开始")

    rf_safe = getattr(instrument_control, "signal_gen", None) is None
    if not rf_safe:
        try:
            instrument_control.rf_output_off()
            rf_safe = True
        except Exception as error:  # 清理动作必须聚合任意设备错误后继续
            failures.append(FailureRecord("rf_output", error))
            logger.exception("RF 关闭失败: %s", error)

    if power_cleanup is not None:
        try:
            power_cleanup()
        except Exception as error:  # 清理动作必须聚合任意设备错误后继续
            failures.append(FailureRecord("power_supplies", error))
            logger.exception("电源清理失败: %s", error)

    # RF 未确认关闭时保留连接，与 SafetyShutdownFlow 一致：现场可以重试关闭 RF，
    # 而不是在一个无法确认输出状态的会话上做进一步动作。
    if close_resource and rf_safe:
        try:
            close_errors = instrument_control.close_all(close_rf=False) or ()
        except Exception as error:  # 连接释放失败同样进入聚合记录
            failures.append(FailureRecord("resources", error))
            logger.exception("仪器连接清理失败: %s", error)
        else:
            failures.extend(
                FailureRecord("resources", error) for error in close_errors
            )

    if failures:
        raise InstrumentSafetyError("测量安全清理存在失败", failures) from cause_of(failures)
    logger.info("测量安全清理完成")


class SafetyCleanupAdapter:
    """用 ``SafetyShutdownFlow`` 实现 application 的安全清理端口。

    动作顺序与 :func:`cleanup_measurement` 一致：RF -> 电源 -> 连接。
    资源释放策略与 ``SafetyShutdownFlow`` 完全一致：RF 关闭失败时连
    ``close_all`` 都不调用，保留全部连接以便现场重试关闭 RF；电源或连接
    释放失败时仍释放连接（异常状态的 VISA 会话不可再被后续安全动作依赖），
    失败写进记录而不会被吞掉。无论成功或失败都生成结构化清理记录，失败由
    :class:`SafetyCleanupError` 携带记录抛出。
    """

    def __init__(self, instrument_control: Any) -> None:
        if instrument_control is None:
            raise ValueError("安全清理适配器必须注入 instrument_control")
        self.instrument_control = instrument_control

    def cleanup(
        self,
        *,
        power_cleanup: Callable[[], None] | None = None,
        release_resources: bool = True,
    ) -> CleanupRecord:
        control = self.instrument_control
        statuses: dict[str, CleanupStepStatus] = {
            "rf_output": CleanupStepStatus.SKIPPED,
            "power_supplies": CleanupStepStatus.SKIPPED,
            "resources": CleanupStepStatus.SKIPPED,
        }

        def rf_off() -> None:
            try:
                control.rf_output_off()
            except Exception:
                statuses["rf_output"] = CleanupStepStatus.FAILED
                raise
            statuses["rf_output"] = CleanupStepStatus.PERFORMED

        def power_off() -> None:
            if power_cleanup is None:  # pragma: no cover - 仅在未注入电源清理时不可达
                return
            try:
                power_cleanup()
            except Exception:
                statuses["power_supplies"] = CleanupStepStatus.FAILED
                raise
            statuses["power_supplies"] = CleanupStepStatus.PERFORMED

        def close(*, rf_safe: bool = True, power_safe: bool = True) -> list[BaseException]:
            if not rf_safe:
                # RF 未确认关闭：保留设备连接以便现场重试关闭。
                return []
            try:
                errors = list(control.close_all(close_rf=False) or ())
            except Exception:
                statuses["resources"] = CleanupStepStatus.FAILED
                raise
            statuses["resources"] = (
                CleanupStepStatus.FAILED if errors else CleanupStepStatus.PERFORMED
            )
            return errors

        has_rf = getattr(control, "signal_gen", None) is not None
        flow = SafetyShutdownFlow(
            rf_off=rf_off if has_rf else None,
            power_off=power_off if power_cleanup is not None else None,
            close=close if release_resources else None,
        )
        failure: SafetyShutdownError | None = None
        try:
            flow.run(close_resource=release_resources)
        except SafetyShutdownError as error:
            failure = error
        if failure is not None:
            record = CleanupRecord(
                rf_output=statuses["rf_output"],
                power_supplies=statuses["power_supplies"],
                resources=statuses["resources"],
                errors=tuple(item.message() for item in failure.failures),
                failures=failure.failures,
            )
            logger.error("测量安全清理存在失败: %s", record.errors)
            # 在 except 块之外抛出：调用方正在处理的测量异常仍是本错误的上下文，
            # 清理失败不会被测量错误吞掉，也不会覆盖原始测量异常。
            raise SafetyCleanupError(record) from cause_of(failure.failures)
        return CleanupRecord(
            rf_output=statuses["rf_output"],
            power_supplies=statuses["power_supplies"],
            resources=statuses["resources"],
        )


__all__ = ["SafetyCleanupAdapter", "cleanup_measurement"]
