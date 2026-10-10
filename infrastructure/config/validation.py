"""配置文件加载与文件级校验入口。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Union

from domain.configuration.rules import ConfigValidationResult, validate_config
from infrastructure.config.json_io import load_config_file


PathLike = Union[str, Path]


def load_config(config_path: Optional[PathLike] = None) -> Dict[str, Any]:
    """加载 JSON 配置，不创建仪器连接，也不修改文件。"""
    return load_config_file(config_path)


def validate_config_file(config_path: Optional[PathLike] = None) -> ConfigValidationResult:
    """加载并校验指定配置文件。"""
    return validate_config(load_config(config_path))


__all__ = [
    "ConfigValidationResult",
    "load_config",
    "validate_config",
    "validate_config_file",
]
