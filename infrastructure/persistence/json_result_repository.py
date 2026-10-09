"""Canonical JSON persistence implementation for measurement results."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Union

from infrastructure.persistence.json_encoder import NumpyJSONEncoder
from project_paths import TEST_RESULTS_DIR, ensure_directory

PathLike = Union[str, Path]
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def load_json_result(path: PathLike) -> Dict[str, Any]:
    """读取旧格式或带元数据的新格式结果。"""
    with Path(path).open("r", encoding="utf-8") as result_file:
        value = json.load(result_file)
    if not isinstance(value, dict):
        raise ValueError("结果根节点必须是 JSON 对象")
    return value


def save_json_result(
    path: PathLike,
    result: Mapping[str, Any],
    *,
    result_type: str,
    schema_version: str = "1.0",
    encoder: type[json.JSONEncoder] = json.JSONEncoder,
) -> Path:
    """以兼容结构原子写入结果，并补充统一元数据字段。"""
    destination = Path(path)
    ensure_directory(destination.parent)
    payload = dict(result)
    payload.setdefault("schema_version", schema_version)
    payload.setdefault("result_type", result_type)
    payload.setdefault("saved_at", datetime.now().isoformat(timespec="seconds"))
    temporary_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=str(destination.parent),
            prefix=f".{destination.name}.", suffix=".tmp", delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(payload, temporary_file, indent=4, ensure_ascii=False, cls=encoder)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, destination)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    return destination


def create_run_directory(run_id: str) -> Path:
    validate_run_id(run_id)
    directory = TEST_RESULTS_DIR / run_id
    directory.mkdir(parents=True, exist_ok=False)
    return directory


def validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run_id 必须是 1-100 个字母、数字、点、下划线或短横线")
    return run_id


def new_run_id() -> str:
    return f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"


def write_run_snapshot(
    run_id: str,
    test_plan: Mapping[str, Any],
    run_mapping: Mapping[str, Any],
    *,
    software_version: Optional[str] = None,
    git_commit: Optional[str] = None,
    start_time: Optional[str] = None,
    status: str = "created",
    error: Optional[str] = None,
) -> Path:
    validate_run_id(run_id)
    test_plan = _with_run_id(test_plan, run_id, "测试方案快照")
    run_mapping = _with_run_id(run_mapping, run_id, "运行映射快照")
    run_directory = create_run_directory(run_id)
    try:
        save_json_result(run_directory / "test_plan_snapshot.json", test_plan, result_type="test_plan_snapshot")
        save_json_result(run_directory / "run_mapping_snapshot.json", run_mapping, result_type="run_mapping_snapshot")
        save_json_result(
            run_directory / "run_metadata.json",
            {
                "run_id": run_id,
                "software_version": software_version,
                "git_commit": git_commit,
                "start_time": start_time or datetime.now().isoformat(timespec="seconds"),
                "end_time": None,
                "status": status,
                "error": error,
            },
            result_type="run_metadata",
        )
    except Exception:
        shutil.rmtree(run_directory, ignore_errors=True)
        raise
    return run_directory


def write_legacy_run_snapshot(run_id: str, legacy_config: Mapping[str, Any], **kwargs: Any) -> Path:
    """将旧版 config.json 转成新模型格式后写入运行快照。"""
    from infrastructure.persistence.json_config_repository import JsonConfigurationRepository

    loaded = JsonConfigurationRepository().load_legacy_data(dict(legacy_config))
    if loaded.conversion_errors:
        details = "; ".join(f"{item.path}: {item.message}" for item in loaded.conversion_errors)
        raise ValueError(f"旧配置无法生成运行快照: {details}")

    run_directory = write_run_snapshot(
        run_id,
        loaded.configuration.test_plan.to_dict(),
        loaded.configuration.run_mapping.to_dict(),
        **kwargs,
    )
    try:
        save_json_result(
            run_directory / "conversion_review.json",
            {
                "status": "needs_review" if loaded.warnings or loaded.unresolved_fields else "converted",
                "warnings": [item.__dict__ for item in loaded.warnings],
                "unresolved_fields": loaded.unresolved_fields,
                "source": "legacy_config",
            },
            result_type="conversion_review",
        )
    except Exception:
        shutil.rmtree(run_directory, ignore_errors=True)
        raise
    return run_directory


def save_measurement_result(
    result: Mapping[str, Any],
    *,
    result_type: str,
    legacy_path: PathLike,
    run_id: str,
    run_directory: Optional[Path] = None,
    encoder: type[json.JSONEncoder] = json.JSONEncoder,
) -> tuple[Path, Path]:
    """归档结果并写入旧路径兼容副本，同时生成 canonical model。"""
    validate_run_id(run_id)
    result = _with_run_id(result, run_id, "测量结果")
    if run_directory is None:
        run_directory = create_run_directory(run_id)
    legacy_path = Path(legacy_path)
    archive_path = Path(run_directory) / legacy_path.name
    if archive_path.exists():
        raise FileExistsError(f"归档结果已存在，不允许覆盖: {archive_path}")
    save_json_result(archive_path, result, result_type=result_type, encoder=encoder)
    try:
        save_json_result(legacy_path, result, result_type=result_type, encoder=encoder)
        if result_type in {"cable_loss", "driver_power_mapping", "amplifier_measurement", "amplifier"}:
            from result_reading import parse_result_model

            model = parse_result_model(result)
            model = replace(
                model,
                plan_snapshot=_load_snapshot(Path(run_directory) / "test_plan_snapshot.json"),
                resource_snapshot=_load_snapshot(Path(run_directory) / "run_mapping_snapshot.json"),
            )
            save_measurement_model(
                model, legacy_payload=result, run_directory=run_directory,
                filename=f"{result_type}_model.json", encoder=encoder,
            )
    except Exception:
        Path(archive_path).unlink(missing_ok=True)
        raise
    return Path(archive_path), legacy_path


def _load_snapshot(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return load_json_result(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def save_measurement_model(
    model: Any,
    *,
    legacy_payload: Mapping[str, Any],
    run_directory: PathLike,
    filename: str | None = None,
    encoder: type[json.JSONEncoder] = json.JSONEncoder,
) -> Path:
    run_directory = Path(run_directory)
    ensure_directory(run_directory)
    model_dict = model.to_dict()
    name = filename or f"{model_dict.get('measurement_type', 'measurement')}_model.json"
    payload = {
        "canonical_model": model_dict,
        "legacy_payload": dict(legacy_payload),
        "run_id": str(model_dict.get("run_id", "legacy")),
        "result_type": "versioned_measurement_result",
        "schema_version": model_dict.get("schema_version", "1.0"),
        "method_version": model_dict.get("method_version", "1.0"),
    }
    return save_json_result(
        run_directory / name, payload, result_type="versioned_measurement_result",
        schema_version=str(model_dict.get("schema_version", "1.0")), encoder=encoder,
    )


def _with_run_id(value: Mapping[str, Any], run_id: str, label: str) -> Dict[str, Any]:
    payload = dict(value)
    existing = payload.get("run_id")
    if existing not in (None, run_id):
        raise ValueError(f"{label} run_id 不一致: {existing} != {run_id}")
    payload["run_id"] = run_id
    return payload
