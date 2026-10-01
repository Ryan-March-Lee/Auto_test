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
