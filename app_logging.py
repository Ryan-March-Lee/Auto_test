"""最小日志基础设施（重构阶段 0.3）。

只提供进程级日志初始化和统一格式；各业务模块通过 ``get_logger``
获取挂在专用 ``pa_auto_test`` logger 下的模块 logger，并在关键事件点
追加日志调用。本模块不改变任何现有控制流、print 输出或 GUI 回调。

设计约束：

- 只配置专用 ``pa_auto_test`` logger，``propagate = False``；
  不修改 root logger 的级别和处理器，第三方库和宿主进程日志不受影响。
- ``setup_logging`` 幂等：launcher 和 GUI 入口都调用时只初始化一次。
- 日志文件写入项目 ``logs/`` 目录，文件名带进程启动时间戳和进程 ID。
- 脱敏：统一 Formatter 自动把 VISA 地址、常见密钥/token 参数替换为
  ``[REDACTED:*]``。脱敏在格式化阶段执行，晚于所有 handler。
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from project_paths import LOGS_DIR, PROJECT_ROOT


PathLike = Union[str, Path]

ROOT_LOGGER_NAME = "pa_auto_test"
_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_MARKER_ATTRIBUTE = "_pa_auto_test_handler"
_NULL_HANDLER_ATTRIBUTE = "_pa_auto_test_null_handler"

# 脱敏规则：按顺序应用；匹配内容替换为 [REDACTED:类别]。
# 1. VISA 地址，例如 TCPIP0::192.168.1.201::inst0::INSTR、
#    TCPIP0::192.168.1.201::5025::SOCKET、USB0::...::RAW。
_VISA_ADDRESS_PATTERN = re.compile(
    r"(?:TCPIP|GPIB|USB|ASRL|VXI|ENET|SOCKET)[0-9]*::"
    r"(?:[^\s'\"]+::)*(?:INSTR|SOCKET|RAW)",
    re.IGNORECASE,
)
# 2. URL 中的用户凭证 user:pass@host
_URL_CREDENTIALS_PATTERN = re.compile(r"(?<=://)[^\s/@:]+:[^\s/@]+@")
# 3. Authorization Bearer 头必须先于通用 key-value 规则处理，
#    否则通用规则会把 Bearer 当成值而留下真正的 token。
_AUTHORIZATION_BEARER_PATTERN = re.compile(
    r"(?i)\bAuthorization\s*:\s*Bearer\s+[^\s,;]+"
)
# 4. 常见密钥参数 key=... / token=... / password=...
_KEY_VALUE_PATTERN = re.compile(
    r"(?i)\b((?:api[_-]?key|subscription[_-]?key|secret|token|password|passwd|authorization)"
    r"([_ -]?[a-z0-9_]*)?)\s*[=:]\s*['\"]?([^\s'\",;]+)"
)


def _redact_message(message: str) -> str:
    """对已格式化的日志消息做脱敏替换。"""
    message = _VISA_ADDRESS_PATTERN.sub(r"[REDACTED:visa]", message)
    message = _URL_CREDENTIALS_PATTERN.sub(r"[REDACTED:credentials]@", message)
    message = _AUTHORIZATION_BEARER_PATTERN.sub(
        "Authorization: [REDACTED:secret]", message
    )
    message = _KEY_VALUE_PATTERN.sub(r"\1=[REDACTED:secret]", message)
    return message


class RedactingFormatter(logging.Formatter):
    """在格式化阶段执行脱敏的 Formatter，先格式化再统一替换。"""

    def format(self, record: logging.LogRecord) -> str:
        return _redact_message(super().format(record))


def _ensure_null_handler(logger: logging.Logger) -> None:
    """在正式日志初始化前静默丢弃记录，避免触发 logging.lastResort。"""
    if getattr(logger, _NULL_HANDLER_ATTRIBUTE, False):
        return
    logger.addHandler(logging.NullHandler())
    setattr(logger, _NULL_HANDLER_ATTRIBUTE, True)


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """返回挂在 ``pa_auto_test`` 专用 logger 下的模块 logger。

    ``name`` 传 ``__name__`` 即可，完整保留模块路径，挂在
    ``pa_auto_test.<module path>`` 下。不初始化时也可安全调用
    （记录会因 ``propagate=False`` 被丢弃，不会漏到 root）。
    """
    if not name:
        logger = logging.getLogger(ROOT_LOGGER_NAME)
        _ensure_null_handler(logger)
        return logger
    if name == ROOT_LOGGER_NAME:
        logger = logging.getLogger(ROOT_LOGGER_NAME)
        _ensure_null_handler(logger)
        return logger
    if name.startswith(f"{ROOT_LOGGER_NAME}."):
        root_logger = logging.getLogger(ROOT_LOGGER_NAME)
        _ensure_null_handler(root_logger)
        return logging.getLogger(name)
    root_logger = logging.getLogger(ROOT_LOGGER_NAME)
    _ensure_null_handler(root_logger)
    return logging.getLogger(f"{ROOT_LOGGER_NAME}.{name}")


def setup_logging(
    log_directory: Optional[PathLike] = None,
    *,
    console: bool = False,
    level: int = logging.INFO,
) -> Path:
    """初始化专用日志并返回日志文件路径。

    幂等：重复调用不会添加重复的处理器，返回首次创建的日志文件路径。
    只影响 ``pa_auto_test`` logger；root logger 的级别和处理器不变。
    """
    root_logger = logging.getLogger(ROOT_LOGGER_NAME)
    _ensure_null_handler(root_logger)
    existing = getattr(root_logger, _MARKER_ATTRIBUTE, None)
    if existing is not None:
        return existing

    directory = Path(log_directory) if log_directory is not None else LOGS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = directory / f"pa_auto_test_{timestamp}_{os.getpid()}.log"

    formatter = RedactingFormatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)
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
    root_logger.propagate = False

    setattr(root_logger, _MARKER_ATTRIBUTE, log_path)
    root_logger.info("日志初始化完成: %s (项目根: %s, 进程: %d)",
                     log_path, PROJECT_ROOT, os.getpid())
    return log_path


def current_log_path() -> Optional[Path]:
    """返回当前日志文件路径；尚未初始化时返回 ``None``。"""
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    return getattr(logger, _MARKER_ATTRIBUTE, None)


def reset_logging() -> None:
    """仅测试使用：移除专用 logger 的全部处理器并复位状态。"""
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    if hasattr(logger, _MARKER_ATTRIBUTE):
        delattr(logger, _MARKER_ATTRIBUTE)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    setattr(logger, _NULL_HANDLER_ATTRIBUTE, False)
    _ensure_null_handler(logger)
    logger.propagate = False
    logger.setLevel(logging.NOTSET)
