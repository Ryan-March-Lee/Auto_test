"""Load and validate modern configuration or the legacy config.json format."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union

from config_models import (
    RunConfiguration,
    RunResourceMapping,
    TestPlan,
    load_json,
    validate_run_configuration,
)
from config_validation import ConfigIssue, ConfigValidationResult
from legacy_config_conversion import LegacyConfigConversionResult, convert_legacy_config


PathLike = Union[str, Path]


@dataclass(frozen=True)
class ConfigurationLoadResult:
    configuration: RunConfiguration
    validation: ConfigValidationResult
    warnings: List[ConfigIssue] = field(default_factory=list)
    unresolved_fields: List[str] = field(default_factory=list)
    source_format: str = "modern"
    conversion_errors: List[ConfigIssue] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.validation.valid


class TestPlanRepository:
    """Read-only repository for a test plan file."""

    def load(self, path: PathLike) -> TestPlan:
        return TestPlan.from_dict(load_json(path))


class RunMappingRepository:
    """Read-only repository for a run resource mapping file."""

    def load(self, path: PathLike) -> RunResourceMapping:
        return RunResourceMapping.from_dict(load_json(path))


class ConfigurationRepository:
    """Load split modern configuration, or convert legacy config without I/O to hardware."""

    def load(self, plan_path: PathLike, mapping_path: Optional[PathLike] = None) -> ConfigurationLoadResult:
        plan_data = load_json(plan_path)
        if mapping_path is None:
            return self.load_legacy_data(plan_data)

        configuration = RunConfiguration(
            TestPlan.from_dict(plan_data),
            RunResourceMapping.from_dict(load_json(mapping_path)),
        )
        return ConfigurationLoadResult(configuration, validate_run_configuration(configuration))

    def load_legacy_data(self, value: dict) -> ConfigurationLoadResult:
        """Convert an already loaded legacy dictionary using the same validation boundary."""
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

    def load_for_run(self, path: PathLike, mapping_path: Optional[PathLike] = None) -> ConfigurationLoadResult:
        """Load a configuration for execution and reject incomplete mappings.

        ``load`` remains useful for conversion review.  This stricter entry
        point is the only one intended for an application run.
        """
        loaded = self.load(path, mapping_path)
        if not loaded.valid:
            return loaded
        if not loaded.configuration.run_mapping.wiring_confirmed:
            issue = ConfigIssue("error", "wiring.confirmed", "运行前必须确认现场接线")
            return ConfigurationLoadResult(
                configuration=loaded.configuration,
                validation=ConfigValidationResult(
                    errors=loaded.validation.errors + [issue],
                    warnings=loaded.validation.warnings,
                ),
                warnings=loaded.warnings,
                unresolved_fields=loaded.unresolved_fields,
                source_format=loaded.source_format,
                conversion_errors=loaded.conversion_errors,
            )
        return loaded
