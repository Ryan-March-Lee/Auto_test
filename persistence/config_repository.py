"""Compatibility exports for the JSON configuration repository."""

from infrastructure.persistence.json_config_repository import (
    ConfigurationRepository,
    JsonConfigurationRepository,
    JsonRunMappingRepository,
    JsonTestPlanRepository,
    RunMappingRepository,
    TestPlanRepository,
)
from application.ports.config_repository import ConfigurationLoadResult

__all__ = [
    "ConfigurationLoadResult",
    "ConfigurationRepository",
    "JsonConfigurationRepository",
    "JsonRunMappingRepository",
    "JsonTestPlanRepository",
    "RunMappingRepository",
    "TestPlanRepository",
]
