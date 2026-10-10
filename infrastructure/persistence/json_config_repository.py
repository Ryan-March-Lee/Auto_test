"""兼容入口：JSON 配置仓储已迁移到 ``infrastructure.config``。"""

from infrastructure.config.json_config_repository import (
    ConfigurationRepository,
    JsonConfigurationRepository,
    JsonRunMappingRepository,
    JsonTestPlanRepository,
    RunMappingRepository,
    TestPlanRepository,
    load_json,
    load_run_configuration,
    load_run_mapping,
    load_test_plan,
)

__all__ = [
    "load_json",
    "load_test_plan",
    "load_run_mapping",
    "load_run_configuration",
    "JsonTestPlanRepository",
    "JsonRunMappingRepository",
    "JsonConfigurationRepository",
    "TestPlanRepository",
    "RunMappingRepository",
    "ConfigurationRepository",
]
