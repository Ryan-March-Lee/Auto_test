"""Application-facing configuration repository contract and result DTO."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from config_models import RunConfiguration, RunResourceMapping, TestPlan
from config_validation import ConfigIssue, ConfigValidationResult


PathLike = str | Path


@dataclass(frozen=True)
class ConfigurationLoadResult:
    configuration: RunConfiguration
    validation: ConfigValidationResult
    warnings: list[ConfigIssue] = field(default_factory=list)
    unresolved_fields: list[str] = field(default_factory=list)
    source_format: str = "modern"
    conversion_errors: list[ConfigIssue] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.validation.valid


class TestPlanRepository(Protocol):
    def load(self, path: PathLike) -> TestPlan: ...


class RunMappingRepository(Protocol):
    def load(self, path: PathLike) -> RunResourceMapping: ...


class ConfigurationRepository(Protocol):
    def load(self, plan_path: PathLike, mapping_path: PathLike | None = None) -> ConfigurationLoadResult: ...

    def load_legacy_data(self, value: dict) -> ConfigurationLoadResult: ...

    def load_for_run(self, path: PathLike, mapping_path: PathLike | None = None) -> ConfigurationLoadResult: ...
