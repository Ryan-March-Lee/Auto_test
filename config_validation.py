"""兼容入口：配置规则已迁移到 domain.configuration。"""

from domain.configuration.rules import validate_config
from domain.configuration.types import ConfigIssue, ConfigValidationResult
from infrastructure.config.validation import load_config, validate_config_file

__all__ = [
    "ConfigIssue", "ConfigValidationResult", "load_config", "validate_config",
    "validate_config_file",
]
