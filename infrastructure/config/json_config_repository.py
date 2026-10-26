"""JSON 配置仓储。

配置对象由 ``domain.configuration`` 定义；本模块只负责读取正式配置 JSON：

- 单文件组合配置：根节点包含 ``schema_version``（``"1.0"``）、``test_plan`` 和
  ``run_mapping``，与配置页面保存出的文件一致；
- 拆分配置：一个正式测试方案文件加一个正式运行映射文件。

旧版 ``config.json`` 的自动转换已删除。缺失必需段或 schema 版本不匹配时返回
带明确校验错误的加载结果，由调用方在硬件动作前失败。
"""

from __future__ import annotations

from typing import Any, Mapping

from application.ports.config_repository import (
    ConfigurationLoadResult,
    ConfigurationRepository as ConfigurationRepositoryPort,
    PathLike,
)
from domain.configuration.models import (
    RunConfiguration,
    RunResourceMapping,
    SUPPORTED_SCHEMA_VERSION,
    TestPlan,
    validate_run_configuration,
)
from domain.configuration.types import ConfigIssue, ConfigValidationResult
from infrastructure.config.json_io import load_json_object


def load_json(path: PathLike) -> dict:
    """读取 JSON 对象，保留配置仓储的文件读取语义。"""
    return load_json_object(path)


def load_test_plan(path: PathLike) -> TestPlan:
    return TestPlan.from_dict(load_json(path))


def load_run_mapping(path: PathLike) -> RunResourceMapping:
    return RunResourceMapping.from_dict(load_json(path))


def load_run_configuration(plan_path: PathLike, mapping_path: PathLike) -> RunConfiguration:
    return RunConfiguration(load_test_plan(plan_path), load_run_mapping(mapping_path))


def load_combined_configuration(document: Mapping[str, Any]) -> ConfigurationLoadResult:
    """把单文件正式组合配置解析为运行配置。

    只接受正式 schema；缺失 ``test_plan`` / ``run_mapping`` 或根 ``schema_version``
    不匹配时，在返回结果中给出明确错误，不做任何旧格式推断。
    """
    errors: list[ConfigIssue] = []
    if document.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
        errors.append(
            ConfigIssue(
                "error",
                "schema_version",
                f"配置根节点 schema_version 必须是 {SUPPORTED_SCHEMA_VERSION}",
            )
        )
    plan_data = document.get("test_plan")
    mapping_data = document.get("run_mapping")
    if not isinstance(plan_data, Mapping):
        errors.append(ConfigIssue("error", "test_plan", "缺少正式测试方案配置段"))
        plan_data = {}
    if not isinstance(mapping_data, Mapping):
        errors.append(ConfigIssue("error", "run_mapping", "缺少正式运行映射配置段"))
        mapping_data = {}
    configuration = RunConfiguration(
        TestPlan.from_dict(plan_data),
        RunResourceMapping.from_dict(mapping_data),
    )
    validation = validate_run_configuration(configuration)
    if errors:
        validation = ConfigValidationResult(
            errors=errors + validation.errors,
            warnings=validation.warnings,
        )
    return ConfigurationLoadResult(configuration=configuration, validation=validation)


class JsonTestPlanRepository:
    def load(self, path: PathLike) -> TestPlan:
        return TestPlan.from_dict(load_json_object(path))


class JsonRunMappingRepository:
    def load(self, path: PathLike) -> RunResourceMapping:
        return RunResourceMapping.from_dict(load_json_object(path))


class JsonConfigurationRepository(ConfigurationRepositoryPort):
    """读取正式配置：单文件组合配置或拆分方案/映射文件。"""

    def load(self, plan_path: PathLike, mapping_path: PathLike | None = None) -> ConfigurationLoadResult:
        if mapping_path is None:
            return load_combined_configuration(load_json_object(plan_path))

        configuration = RunConfiguration(
            TestPlan.from_dict(load_json_object(plan_path)),
            RunResourceMapping.from_dict(load_json_object(mapping_path)),
        )
        return ConfigurationLoadResult(
            configuration=configuration,
            validation=validate_run_configuration(configuration),
        )

    def load_for_run(self, path: PathLike, mapping_path: PathLike | None = None) -> ConfigurationLoadResult:
        """应用层运行前预检入口；新应用代码应使用 application.configuration_service。"""
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
    "load_combined_configuration",
    "JsonTestPlanRepository",
    "JsonRunMappingRepository",
    "JsonConfigurationRepository",
    "TestPlanRepository",
    "RunMappingRepository",
    "ConfigurationRepository",
]
