"""兼容入口：配置模型已迁移到 domain.configuration。"""

from domain.configuration.models import (
    ChannelMapping, InstrumentMapping, PathLike, PowerChannelPlan,
    RunConfiguration, RunResourceMapping, SUPPORTED_COMPRESSION_POINTS,
    SUPPORTED_SCHEMA_VERSION, TestPlan, validate_cable_loss_configuration,
    validate_driver_mapping_configuration, validate_mapping, validate_plan,
    validate_run_configuration, validate_run_mapping, validate_test_plan,
)
from infrastructure.persistence.json_config_repository import (
    load_json, load_run_configuration, load_run_mapping, load_test_plan,
)

__all__ = [
    "ChannelMapping", "InstrumentMapping", "PathLike", "PowerChannelPlan",
    "RunConfiguration", "RunResourceMapping", "SUPPORTED_COMPRESSION_POINTS",
    "SUPPORTED_SCHEMA_VERSION", "TestPlan", "load_json", "load_run_configuration",
    "load_run_mapping", "load_test_plan", "validate_cable_loss_configuration",
    "validate_driver_mapping_configuration", "validate_mapping", "validate_plan",
    "validate_run_configuration", "validate_run_mapping", "validate_test_plan",
]
