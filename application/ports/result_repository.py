"""Application contract for persisting measurement results."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from .types import PathLike


__all__ = ["MeasurementResultRepository", "PathLike", "SavedMeasurementResult"]


@dataclass(frozen=True)
class SavedMeasurementResult:
    """Locations and identity assigned to a persisted measurement result."""

    archive_path: Path
    run_directory: Path
    run_id: str


class MeasurementResultRepository(Protocol):
    """Minimal persistence boundary required by measurement use cases."""

    def new_run_id(self) -> str:
        """Create a path-safe identifier for a measurement run."""

    def save(
        self,
        result: Mapping[str, Any],
        *,
        result_type: str,
        run_id: str,
        run_directory: PathLike | None = None,
    ) -> SavedMeasurementResult:
        """Persist a result in the run directory and return its authoritative path."""

    def load(self, path: PathLike) -> Mapping[str, Any]:
        """Load a previously persisted result."""
