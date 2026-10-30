"""JSON 配置仓储。

配置对象由 ``domain.configuration`` 定义；本模块是**应用配置 JSON 的唯一持久化入口**：

- 读取：文档读取原语只有 ``infrastructure.config.json_io`` 一处（``load_json_object`` /
  ``load_config_file``），本模块的 ``load()`` 只在其上解析正式 schema，不自己打开文件。
  支持单文件组合配置（根节点包含 ``schema_version``（``"1.0"``）、``test_plan`` 和
  ``run_mapping``，与配置页面保存出的文件一致），或拆分配置（一个正式测试方案文件加
  一个正式运行映射文件）；
- 写入：只有 :meth:`JsonConfigurationRepository.save_document`。配置页面保存、旧电源
  配置迁移器等生产写入点都只委托它，因此正式配置的序列化格式（缩进、UTF-8、非 ASCII
  字符策略）与原子替换策略只在这里定义一次（重构计划步骤 3）。

边界分工（刻意不合并）：

- **读取与写入各自只有一处实现**：配置文件文档的读取走
  ``infrastructure.config.json_io``（GUI 适配器、启动器校验和本模块的 ``load()`` 共用），
  写入走 :meth:`JsonConfigurationRepository.save_document`；本模块不提供第二个文档读取
  入口，也不在 ``load()`` 之外解析正式 schema。
- **校验与持久化分开**：本模块只解析和落盘，正式 schema 的编写/校验由
  ``presentation.qt.configuration_authoring``（配置页保存）与
  ``infrastructure.config.validation``（文件级校验）负责，写入口不做“顺手修好”。
- **运行快照不属于本模块**：``runs/<run_id>/config.json`` 等冻结快照由运行仓储
  （``infrastructure.persistence.run_repository``）创建并登记 manifest，配置仓储不写
  任何运行目录。

旧版 ``config.json`` 的自动转换已删除。缺失必需段或 schema 版本不匹配时返回
带明确校验错误的加载结果，由调用方在硬件动作前失败。
"""

from __future__ import annotations

from pathlib import Path
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
from infrastructure.filesystem.atomic_json import write_json_atomic


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
    """正式配置仓储：在共享读取边界上解析组合/拆分配置，写入只经 :meth:`save_document`。"""

    # -- 持久化（唯一写入口）------------------------------------------------

    def save_document(self, path: PathLike, document: Mapping[str, Any]) -> Path:
        """写入正式用户配置文件。

        这是生产代码里**唯一**的配置写入实现：GUI 配置适配器（
        :class:`infrastructure.gui_data_service.JsonConfigurationAdapter`）与旧电源配置
        迁移器（:func:`infrastructure.config.legacy_power_supply_migration.save_migrated_configuration`）
        都只委托本方法，因此正式格式（``indent=4``、UTF-8、非 ASCII 原样保留）与
        “临时文件 + fsync + 原子替换”的失败语义只有一份实现。

        本方法只负责持久化：它不解释配置 schema，也不做任何修复或降级；写入内容必须
        是调用方已经编写/校验过的正式文档，否则原样写入并由读取边界报错。非映射载荷
        由原子写入原语拒绝（``ValueError``），不会产生半份文件。

        读取不在本方法的作用范围内：配置文件文档的读取统一走
        ``infrastructure.config.json_io``（``load_json_object`` / ``load_config_file``），
        本仓储因此不再提供第二套文档读取入口。
        """
        return write_json_atomic(Path(path), document, indent=4, ensure_ascii=False)

    # -- 正式配置模型 -------------------------------------------------------

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
