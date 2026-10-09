"""JSON-backed configuration repository and legacy-format conversion."""

from __future__ import annotations

from pathlib import Path

from application.ports.config_repository import (
    ConfigurationLoadResult,
    ConfigurationRepository as ConfigurationRepositoryPort,
    PathLike,
)
from config_models import (
    RunConfiguration,
    RunResourceMapping,
    TestPlan,
    load_json,
    validate_run_configuration,
)
from config_validation import ConfigIssue, ConfigValidationResult
from legacy_config_conversion import LegacyConfigConversionResult, convert_legacy_config


class JsonTestPlanRepository:
    def load(self, path: PathLike) -> TestPlan:
        return TestPlan.from_dict(load_json(path))


class JsonRunMappingRepository:
    def load(self, path: PathLike) -> RunResourceMapping:
        return RunResourceMapping.from_dict(load_json(path))


class JsonConfigurationRepository(ConfigurationRepositoryPort):
    """Load split JSON configuration or convert the legacy combined format."""

    def load(self, plan_path: PathLike, mapping_path: PathLike | None = None) -> ConfigurationLoadResult:
        plan_data = load_json(plan_path)
        if mapping_path is None:
            return self.load_legacy_data(plan_data)

        configuration = RunConfiguration(
            TestPlan.from_dict(plan_data),
            RunResourceMapping.from_dict(load_json(mapping_path)),
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
        loaded = self.load(path, mapping_path)
        if not loaded.valid or loaded.configuration.run_mapping.wiring_confirmed:
            return loaded

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


TestPlanRepository = JsonTestPlanRepository
RunMappingRepository = JsonRunMappingRepository
ConfigurationRepository = JsonConfigurationRepository
