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

# 兼容结果文件命名规则：固定文件名类型（线损）与时间戳前缀类型（映射/功放）。
# 写入侧和只读历史浏览侧共用同一份定义，避免两套命名漂移。
FIXED_RESULT_FILENAMES: Mapping[str, str] = {
    "cable_loss": "cable_loss_results.json",
}
TIMESTAMPED_RESULT_PREFIXES: Mapping[str, str] = {
    "driver_power_mapping": "driver_power_mapping",
    "amplifier_measurement": "amplifier_measurement",
}
# 全局历史/兼容结果的类型清单，供只读历史浏览枚举使用。
HISTORY_RESULT_TYPES: tuple[str, ...] = (
    "cable_loss",
    "driver_power_mapping",
    "amplifier_measurement",
)


def fixed_result_filename(result_type: str) -> Optional[str]:
    """返回固定文件名类型的兼容结果文件名；时间戳型返回 ``None``。"""
    return FIXED_RESULT_FILENAMES.get(result_type)


def timestamped_result_prefix(result_type: str) -> Optional[str]:
    """返回时间戳型结果的文件名前缀；固定文件名类型返回 ``None``。"""
    return TIMESTAMPED_RESULT_PREFIXES.get(result_type)


def history_result_files(root: PathLike, result_type: str) -> tuple[Path, ...]:
    """按修改时间升序列出某类型的只读历史结果文件。

    ``_model.json`` 是运行归档内的 canonical model 文件，不属于全局历史结果，
    因此从浏览列表中排除。未知结果类型直接拒绝，避免调用方误以为“没有结果”。
    """
    fixed = fixed_result_filename(result_type)
    if fixed is not None:
        path = Path(root) / fixed
        return (path,) if path.is_file() else ()
    prefix = timestamped_result_prefix(result_type)
    if prefix is None:
        raise ValueError(f"不支持的测量结果类型: {result_type}")
    return tuple(
        sorted(
            (
                path
                for path in Path(root).glob(f"{prefix}_*.json")
                if path.is_file() and not path.name.endswith("_model.json")
            ),
            key=lambda item: item.stat().st_mtime,
        )
    )


def latest_history_result_file(root: PathLike, result_type: str) -> Path:
    """返回某类型最新的只读历史结果文件；没有文件时抛出 ``FileNotFoundError``。"""
    files = history_result_files(root, result_type)
    if not files:
        raise FileNotFoundError(f"未找到结果类型为 {result_type} 的结果文件")
    return files[-1]


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
    """把测量结果写入当前运行目录，并生成 canonical model。

    正式结果只落在运行目录内：不再写 ``test_results/`` 下的固定文件名或时间戳
    兼容副本。返回值就是本次运行的权威结果路径，调用方据此登记 manifest。
    """
    name = validate_result_filename(filename)
    validate_run_id(run_id)
    result = _with_run_id(result, run_id, "测量结果")
    if run_directory is None:
        run_directory = create_run_directory(run_id, paths=paths)
    archive_path = Path(run_directory) / name
    if archive_path.exists():
        raise FileExistsError(f"归档结果已存在，不允许覆盖: {archive_path}")
    save_json_result(archive_path, result, result_type=result_type, encoder=encoder)
    try:
        if result_type in {"cable_loss", "driver_power_mapping", "amplifier_measurement", "amplifier"}:
            from domain.result_reading import parse_result_model

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
