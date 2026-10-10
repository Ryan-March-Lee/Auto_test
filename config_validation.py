"""迁移期兼容入口：配置规则和文件校验已迁移到正式分层模块。"""

from domain.configuration.rules import validate_config
from domain.configuration.types import ConfigIssue, ConfigValidationResult
from infrastructure.config.validation import load_config, validate_config_file

__all__ = [
    "ConfigIssue", "ConfigValidationResult", "load_config", "validate_config",
    "validate_config_file",
]
