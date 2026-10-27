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

from application.ports.measurement_session import (
    CleanupRecord,
    CleanupStepStatus,
    SafetyCleanupError,
)
from infrastructure.logging.app_logging import get_logger

from .safety_shutdown_flow import SafetyShutdownError, SafetyShutdownFlow


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

    errors: List[BaseException] = []
    logger.info("测量安全清理开始")

    if getattr(instrument_control, "signal_gen", None) is not None:
        try:
            instrument_control.rf_output_off()
        except Exception as error:
            errors.append(error)
            logger.exception("RF 关闭失败: %s", error)

    if power_cleanup is not None:
        try:
            power_cleanup()
        except Exception as error:
            errors.append(error)
            logger.exception("电源清理失败: %s", error)

    if close_resource:
        try:
            errors.extend(instrument_control.close_all(close_rf=False))
        except Exception as error:
            errors.append(error)
            logger.exception("仪器连接清理失败: %s", error)

    if errors:
        details = "; ".join(str(error) for error in errors)
        raise RuntimeError(f"测量安全清理存在失败: {details}") from errors[0]
    logger.info("测量安全清理完成")


class SafetyCleanupAdapter:
    """用 ``SafetyShutdownFlow`` 实现 application 的安全清理端口。

    动作顺序与 :func:`cleanup_measurement` 一致：RF -> 电源 -> 连接。RF 关闭
    失败时不关闭信号源连接，保留现场重试机会；电源或连接失败仍继续后续安全
    动作。无论成功或失败都生成结构化清理记录，失败由
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
                errors=tuple(
                    str(item) or item.__class__.__name__ for item in failure.errors
                ),
            )
            logger.error("测量安全清理存在失败: %s", record.errors)
            # 在 except 块之外抛出：调用方正在处理的测量异常仍是本错误的上下文，
            # 清理失败不会被测量错误吞掉，也不会覆盖原始测量异常。
            cause = failure.errors[0] if failure.errors else failure
            raise SafetyCleanupError(record) from cause
        return CleanupRecord(
            rf_output=statuses["rf_output"],
            power_supplies=statuses["power_supplies"],
            resources=statuses["resources"],
        )


__all__ = ["SafetyCleanupAdapter", "cleanup_measurement"]
