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
from infrastructure.filesystem.paths import (
    DEFAULT_PATHS, TEST_RESULTS_DIR, PathService, ensure_directory,
)

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


def create_run_directory(run_id: str, *, paths: PathService | None = None) -> Path:
    validate_run_id(run_id)
    directory = (paths.test_results_dir if paths is not None else TEST_RESULTS_DIR) / run_id
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
    paths: PathService | None = None,
) -> Path:
    validate_run_id(run_id)
    test_plan = _with_run_id(test_plan, run_id, "测试方案快照")
    run_mapping = _with_run_id(run_mapping, run_id, "运行映射快照")
    run_directory = create_run_directory(run_id, paths=paths)
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


def validate_result_filename(filename: str) -> str:
    """校验运行目录内的测量结果文件名。

    只接受纯文件名（不能含目录、盘符或 ``.``/``..`` 片段），并要求 ``.json``
    后缀；非法值直接拒绝，避免调用方把结果写到运行目录之外或产生歧义命名。
    """
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError("测量结果文件名不能为空")
    if filename != filename.strip():
        raise ValueError(f"测量结果文件名不能包含首尾空白: {filename!r}")
    if "\x00" in filename:
        raise ValueError("测量结果文件名不能包含空字符")
    if "/" in filename or "\\" in filename or ":" in filename:
        raise ValueError(f"测量结果文件名必须是运行目录内的纯文件名: {filename!r}")
    if filename in {".", ".."}:
        raise ValueError(f"测量结果文件名非法: {filename!r}")
    if Path(filename).suffix.lower() != ".json":
        raise ValueError(f"测量结果文件名必须使用 .json 后缀: {filename!r}")
    return filename


_FORMAL_RESULT_TYPES = frozenset(
    {"cable_loss", "driver_power_mapping", "amplifier", "amplifier_measurement"}
)


def save_measurement_result(
    result: Mapping[str, Any],
    *,
    result_type: str,
    filename: str,
    run_id: str,
    run_directory: Optional[Path] = None,
    encoder: type[json.JSONEncoder] = json.JSONEncoder,
    paths: PathService | None = None,
) -> Path:
    """把正式测量结果写入当前运行目录，并生成 canonical model。

    写入前把结果文档收敛为正式结果 DTO：非正式 schema（旧字段别名、缺少
    ``measurement_type`` / ``status``、测量类型与数据字段不一致）在落盘前直接报错，
    不会写出无法读取的结果。结果文件就是 ``DTO.to_dict()`` 的文档形状，canonical
    model 工件保存同一个 DTO。
    """
    from application.dto.measurement_results import measurement_result_from_document

    if result_type not in _FORMAL_RESULT_TYPES:
        raise ValueError(f"不支持的测量结果类型: {result_type}")
    name = validate_result_filename(filename)
    validate_run_id(run_id)
    document = measurement_result_from_document(_with_run_id(result, run_id, "测量结果"))
    if run_directory is None:
        run_directory = create_run_directory(run_id, paths=paths)
    archive_path = Path(run_directory) / name
    if archive_path.exists():
        raise FileExistsError(f"归档结果已存在，不允许覆盖: {archive_path}")
    save_json_result(
        archive_path, document.to_dict(), result_type=result_type, encoder=encoder
    )
    try:
        model = replace(
            document,
            configuration_snapshot=(
                _load_snapshot(Path(run_directory) / "test_plan_snapshot.json")
                or document.configuration_snapshot
            ),
            device_summary=(
                _load_snapshot(Path(run_directory) / "run_mapping_snapshot.json")
                or document.device_summary
            ),
        )
        save_measurement_model(
            model, run_directory=run_directory,
            filename=f"{result_type}_model.json", encoder=encoder,
        )
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
    return archive_path


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
    run_directory: PathLike,
    filename: str | None = None,
    encoder: type[json.JSONEncoder] = json.JSONEncoder,
) -> Path:
    """把版本化模型工件写入运行目录。

    工件只保存正式 ``canonical_model``：原始服务载荷属于运行目录中另行登记的
    结果文件，不再作为兼容字段复制进模型工件。模型必须是正式结果 DTO。
    """
    from application.dto.measurement_results import MeasurementResult

    if not isinstance(model, MeasurementResult):
        raise ValueError("canonical model 必须是正式结果 DTO")
    run_directory = Path(run_directory)
    ensure_directory(run_directory)
    model_dict = model.to_dict()
    name = filename or f"{model_dict.get('measurement_type', 'measurement')}_model.json"
    payload = {
        "canonical_model": model_dict,
        "run_id": str(model_dict.get("run_id", "")),
        "result_type": "versioned_measurement_result",
    }
    return save_json_result(
        run_directory / name, payload, result_type="versioned_measurement_result",
        encoder=encoder,
    )


def _with_run_id(value: Mapping[str, Any], run_id: str, label: str) -> Dict[str, Any]:
    payload = dict(value)
    existing = payload.get("run_id")
    if existing not in (None, run_id):
        raise ValueError(f"{label} run_id 不一致: {existing} != {run_id}")
    payload["run_id"] = run_id
    return payload
