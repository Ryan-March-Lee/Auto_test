"""JSON 配置仓储。

配置对象由 ``domain.configuration`` 定义；本模块只负责 JSON 文件读取、
旧格式转换以及仓储协议的基础设施实现。
"""

from __future__ import annotations

from application.ports.config_repository import (
    ConfigurationLoadResult,
    ConfigurationRepository as ConfigurationRepositoryPort,
    PathLike,
)
from domain.configuration.models import (
    RunConfiguration,
    RunResourceMapping,
    TestPlan,
    validate_run_configuration,
)
from domain.configuration.types import ConfigValidationResult
from infrastructure.config.json_io import load_json_object
from infrastructure.config.legacy_conversion import (
    LegacyConfigConversionResult,
    convert_legacy_config,
)


def load_json(path: PathLike) -> dict:
    """读取 JSON 对象，保留旧配置模型 API 的文件读取语义。"""
    return load_json_object(path)


def load_test_plan(path: PathLike) -> TestPlan:
    return TestPlan.from_dict(load_json(path))


def load_run_mapping(path: PathLike) -> RunResourceMapping:
    return RunResourceMapping.from_dict(load_json(path))


def load_run_configuration(plan_path: PathLike, mapping_path: PathLike) -> RunConfiguration:
    return RunConfiguration(load_test_plan(plan_path), load_run_mapping(mapping_path))


class JsonTestPlanRepository:
    def load(self, path: PathLike) -> TestPlan:
        return TestPlan.from_dict(load_json_object(path))


class JsonRunMappingRepository:
    def load(self, path: PathLike) -> RunResourceMapping:
        return RunResourceMapping.from_dict(load_json_object(path))


class JsonConfigurationRepository(ConfigurationRepositoryPort):
    """加载拆分后的 JSON 配置，或将旧组合格式转换为领域对象。"""

    def load(self, plan_path: PathLike, mapping_path: PathLike | None = None) -> ConfigurationLoadResult:
        plan_data = load_json_object(plan_path)
        if mapping_path is None:
            return self.load_legacy_data(plan_data)

        configuration = RunConfiguration(
            TestPlan.from_dict(plan_data),
            RunResourceMapping.from_dict(load_json_object(mapping_path)),
        )
        return ConfigurationLoadResult(configuration, validate_run_configuration(configuration))

    def load_legacy_data(self, value: dict) -> ConfigurationLoadResult:
        converted: LegacyConfigConversionResult = convert_legacy_config(value)
        configuration = RunConfiguration(converted.test_plan, converted.run_mapping)
        validation = validate_run_configuration(configuration)
        if converted.errors:
            validation = ConfigValidationResult(
                errors=validation.errors + converted.errors,
                warnings=validation.warnings + converted.warnings,
            )
        return ConfigurationLoadResult(
            configuration=configuration,
            validation=validation,
            warnings=converted.warnings,
            unresolved_fields=converted.unresolved_fields,
            source_format="legacy",
            conversion_errors=converted.errors,
        )

    def load_for_run(self, path: PathLike, mapping_path: PathLike | None = None) -> ConfigurationLoadResult:
        """兼容旧端口；新应用代码应使用 application.configuration_service。"""
        from application.configuration_service import load_for_run

        return load_for_run(self, path, mapping_path)


TestPlanRepository = JsonTestPlanRepository
RunMappingRepository = JsonRunMappingRepository
ConfigurationRepository = JsonConfigurationRepository


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
