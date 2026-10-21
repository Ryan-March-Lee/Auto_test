"""已弃用兼容入口：请迁移到 ``infrastructure.config.legacy_conversion``。"""

from infrastructure.config.legacy_conversion import (
    LegacyConfigConversionResult, PathLike, convert_legacy_config,
    convert_legacy_config_file, legacy_config_to_run_mapping,
    legacy_config_to_test_plan, load_legacy_config, parse_legacy_attenuator,
)

__all__ = [
    "LegacyConfigConversionResult", "PathLike", "convert_legacy_config",
    "convert_legacy_config_file", "legacy_config_to_run_mapping",
    "legacy_config_to_test_plan", "load_legacy_config", "parse_legacy_attenuator",
]
