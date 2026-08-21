"""最小日志基础设施（重构阶段 0.3）。

只提供进程级日志初始化和统一格式；各业务模块继续使用
``logging.getLogger(__name__)`` 获取模块 logger，并在关键事件点
追加日志调用。本模块不改变任何现有控制流、print 输出或 GUI 回调。

设计约束：

- ``setup_logging`` 幂等：launcher 和 GUI 入口都调用时只初始化一次。
- 日志文件写入项目 ``logs/`` 目录，文件名带进程启动时间戳。
- 脱敏规则：调用方不得把仪器 VISA 地址、API 密钥或聊天内容写入日志；
  电源和通道使用设备名/通道名（如 ``PS4/CH1``）标识。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from project_paths import LOGS_DIR, PROJECT_ROOT


PathLike = Union[str, Path]

_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_MARKER_ATTRIBUTE = "_pa_auto_test_handler"


def setup_logging(
    log_directory: Optional[PathLike] = None,
    *,
    console: bool = False,
    level: int = logging.INFO,
) -> Path:
    """初始化进程级日志并返回日志文件路径。

    幂等：重复调用不会添加重复的处理器，返回首次创建的日志文件路径。
    """
    root_logger = logging.getLogger()
    existing = getattr(root_logger, _MARKER_ATTRIBUTE, None)
    if existing is not None:
        return existing

    directory = Path(log_directory) if log_directory is not None else LOGS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = directory / f"pa_auto_test_{timestamp}.log"

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    handlers = [file_handler]
    if console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        handlers.append(console_handler)

    for handler in handlers:
        root_logger.addHandler(handler)
    root_logger.setLevel(level)

    setattr(root_logger, _MARKER_ATTRIBUTE, log_path)
    root_logger.info("日志初始化完成: %s (项目根: %s)", log_path, PROJECT_ROOT)
    return log_path


def current_log_path() -> Optional[Path]:
    """返回当前日志文件路径；尚未初始化时返回 ``None``。"""
    root_logger = logging.getLogger()
    return getattr(root_logger, _MARKER_ATTRIBUTE, None)
