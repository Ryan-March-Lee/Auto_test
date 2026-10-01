"""Application contract for loading persisted measurement inputs."""

from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable

from .result_repository import PathLike


@runtime_checkable
class ResultInputReader(Protocol):
    """Read calibration inputs without exposing storage implementation details."""

    def read_cable_loss(self, path: PathLike | None = None) -> Mapping[str, Any]:
        """Load cable-loss data from an explicit or default path."""

    def read_driver_mapping(self, path: PathLike | None = None) -> Mapping[str, Any]:
        """Load driver mapping data from an explicit or newest result path."""
