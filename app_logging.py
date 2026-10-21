"""已弃用兼容入口：请迁移到 ``infrastructure.logging.app_logging``。"""

from infrastructure.logging.app_logging import (
    ROOT_LOGGER_NAME, RedactingFormatter, current_log_path, get_logger,
    reset_logging, setup_logging,
)

__all__ = [
    "ROOT_LOGGER_NAME", "RedactingFormatter", "current_log_path", "get_logger",
    "reset_logging", "setup_logging",
]
