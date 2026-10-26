"""Application-facing configuration repository contract and result DTO."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from domain.configuration.models import RunConfiguration, RunResourceMapping, TestPlan
from domain.configuration.types import ConfigIssue, ConfigValidationResult


PathLike = str | Path


@dataclass(frozen=True)
class ConfigurationLoadResult:
    """正式配置加载结果。

    仓储只读取正式配置 schema；不存在“旧格式转换报告”或“待人工确认字段”。
    不符合正式 schema 的配置通过 ``validation`` 明确失败。
    """

    configuration: RunConfiguration
    validation: ConfigValidationResult
    warnings: list[ConfigIssue] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.validation.valid


class TestPlanRepository(Protocol):
    def load(self, path: PathLike) -> TestPlan: ...


class RunMappingRepository(Protocol):
    def load(self, path: PathLike) -> RunResourceMapping: ...


class ConfigurationRepository(Protocol):
    def load(self, plan_path: PathLike, mapping_path: PathLike | None = None) -> ConfigurationLoadResult: ...

    def load_for_run(self, path: PathLike, mapping_path: PathLike | None = None) -> ConfigurationLoadResult: ...
