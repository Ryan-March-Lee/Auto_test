"""Ports used by application use cases."""
from .config_repository import (
    ConfigurationLoadResult,
    ConfigurationRepository,
    RunMappingRepository,
    TestPlanRepository,
)
from .configuration_snapshots import (
    ConfigurationSaveOutcome,
    ConfigurationSnapshot,
    ConfigurationSnapshotRepository,
)
from .device_binding import DeviceBindingPort
from .measurement_session import (
    CleanupRecord,
    CleanupStepStatus,
    MeasurementSessionBinding,
    MeasurementSessionPort,
    SafetyCleanupError,
    SafetyCleanupPort,
)
from .result_artifacts import (
    ArtifactGenerationError,
    ArtifactGenerationRequest,
    GeneratedArtifact,
    ResultArtifactGeneratorPort,
)
from .result_input_reader import ResultInputReader
from .result_repository import MeasurementResultRepository, SavedMeasurementResult
from .run_history import (
    RunHistoryEntry,
    RunHistoryRepository,
    RunHistoryState,
    RunStageResultEntry,
)
from .run_repository import RunLayout, RunRepositoryPort
from .stage_artifacts import (
    StageArtifactReference,
    StageArtifactRepository,
    stage_artifact_filename,
    validate_stage_artifact_filename,
)
from .stage_result_repository import (
    LoadedStageResult,
    MeasurementStage,
    StageResultEntry,
    StageResultError,
    StageResultReference,
    StageResultRepository,
    coerce_measurement_stage,
    measurement_stage_for_type,
    measurement_type_for_stage,
    require_stage_result,
    require_stage_result_path,
)
from .types import PathLike

__all__ = [
    "ArtifactGenerationError",
    "ArtifactGenerationRequest",
    "CleanupRecord",
    "CleanupStepStatus",
    "ConfigurationLoadResult",
    "ConfigurationRepository",
    "ConfigurationSaveOutcome",
    "ConfigurationSnapshot",
    "ConfigurationSnapshotRepository",
    "DeviceBindingPort",
    "GeneratedArtifact",
    "LoadedStageResult",
    "MeasurementResultRepository",
    "MeasurementSessionBinding",
    "MeasurementSessionPort",
    "MeasurementStage",
    "PathLike",
    "ResultArtifactGeneratorPort",
    "ResultInputReader",
    "RunHistoryEntry",
    "RunHistoryRepository",
    "RunHistoryState",
    "RunLayout",
    "RunMappingRepository",
    "RunRepositoryPort",
    "RunStageResultEntry",
    "SafetyCleanupError",
    "SafetyCleanupPort",
    "SavedMeasurementResult",
    "StageArtifactReference",
    "StageArtifactRepository",
    "StageResultEntry",
    "StageResultError",
    "StageResultReference",
    "StageResultRepository",
    "TestPlanRepository",
    "coerce_measurement_stage",
    "measurement_stage_for_type",
    "measurement_type_for_stage",
    "require_stage_result",
    "require_stage_result_path",
    "stage_artifact_filename",
    "validate_stage_artifact_filename",
]
