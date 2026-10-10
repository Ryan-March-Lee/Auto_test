"""测量流程的统一安全清理包装。"""

from __future__ import annotations

from typing import Any, Callable, List

from infrastructure.logging.app_logging import get_logger
from instrument.flow import SafetyShutdownFlow


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
