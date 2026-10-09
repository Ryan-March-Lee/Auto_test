"""Legacy compatibility exports for canonical persistence implementation."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping, Optional, Union

from infrastructure.persistence import json_result_repository as _canonical

# Keep historical patch targets bound to the canonical implementation.
os = _canonical.os

PathLike = Union[str, Path]
TEST_RESULTS_DIR = _canonical.TEST_RESULTS_DIR
_DEFAULT_TEST_RESULTS_DIR = TEST_RESULTS_DIR
RUN_ID_PATTERN = _canonical.RUN_ID_PATTERN


@contextmanager
def _canonical_path_scope():
    # Preserve legacy tests and external scripts that patch this module-level path.
    previous = _canonical.TEST_RESULTS_DIR
    if TEST_RESULTS_DIR != _DEFAULT_TEST_RESULTS_DIR:
        _canonical.TEST_RESULTS_DIR = TEST_RESULTS_DIR
    try:
        yield
    finally:
        _canonical.TEST_RESULTS_DIR = previous


def load_json_result(path: PathLike):
    return _canonical.load_json_result(path)


def save_json_result(path: PathLike, result: Mapping[str, Any], *, result_type: str,
                     schema_version: str = "1.0", encoder: type = __import__("json").JSONEncoder):
    return _canonical.save_json_result(
        path, result, result_type=result_type, schema_version=schema_version, encoder=encoder
    )


def create_run_directory(run_id: str) -> Path:
    with _canonical_path_scope():
        return _canonical.create_run_directory(run_id)


def validate_run_id(run_id: str) -> str:
    return _canonical.validate_run_id(run_id)


def new_run_id() -> str:
    return _canonical.new_run_id()


def write_run_snapshot(run_id: str, test_plan: Mapping[str, Any], run_mapping: Mapping[str, Any], **kwargs: Any) -> Path:
    with _canonical_path_scope():
        return _canonical.write_run_snapshot(run_id, test_plan, run_mapping, **kwargs)


def write_legacy_run_snapshot(run_id: str, legacy_config: Mapping[str, Any], **kwargs: Any) -> Path:
    with _canonical_path_scope():
        return _canonical.write_legacy_run_snapshot(run_id, legacy_config, **kwargs)


def save_measurement_result(result: Mapping[str, Any], *, result_type: str, legacy_path: PathLike,
                            run_id: str, run_directory: Optional[Path] = None,
                            encoder: type = __import__("json").JSONEncoder):
    with _canonical_path_scope():
        return _canonical.save_measurement_result(
            result, result_type=result_type, legacy_path=legacy_path, run_id=run_id,
            run_directory=run_directory, encoder=encoder,
        )


def save_measurement_model(model: Any, *, legacy_payload: Mapping[str, Any], run_directory: PathLike,
                           filename: str | None = None, encoder: type = __import__("json").JSONEncoder):
    return _canonical.save_measurement_model(
        model, legacy_payload=legacy_payload, run_directory=run_directory,
        filename=filename, encoder=encoder,
    )


__all__ = [
    "RUN_ID_PATTERN", "TEST_RESULTS_DIR", "create_run_directory", "load_json_result",
    "new_run_id", "save_json_result", "save_measurement_model", "save_measurement_result",
    "validate_run_id", "write_legacy_run_snapshot", "write_run_snapshot",
]
