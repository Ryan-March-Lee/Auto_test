"""Application contract for persisting measurement results."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Union


PathLike = Union[str, Path]


@dataclass(frozen=True)
class SavedMeasurementResult:
    """Locations and identity assigned to a persisted measurement result."""

    archive_path: Path
    legacy_copy_path: Path
    run_directory: Path
    run_id: str


class MeasurementResultRepository(Protocol):
    """Minimal persistence boundary required by measurement use cases."""

    def new_run_id(self) -> str:
        """Create a path-safe identifier for a measurement run."""

    def create_legacy_run_snapshot(
        self, run_id: str, config: Mapping[str, Any], *, status: str = "created"
    ) -> Path:
        """Create a run snapshot from the legacy configuration shape."""

    def legacy_path(self, result_type: str) -> Path:
        """Return the compatibility path for a result type."""

    def latest_path(self, result_type: str) -> Path:
        """Return the newest timestamped compatibility result path."""

    def save(
        self,
        result: Mapping[str, Any],
        *,
        result_type: str,
        run_id: str,
        run_directory: PathLike | None = None,
    ) -> SavedMeasurementResult:
        """Persist a result and return its archive and compatibility paths."""

    def load(self, path: PathLike) -> Mapping[str, Any]:
        """Load a previously persisted result."""
