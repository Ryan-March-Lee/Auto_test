"""测量结果的兼容读写边界。"""

from __future__ import annotations

import json
import os
import tempfile
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Mapping, Union

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

    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(destination.parent),
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
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
    """创建不会覆盖既有结果的运行目录。"""
    validate_run_id(run_id)
    directory = TEST_RESULTS_DIR / run_id
    directory.mkdir(parents=True, exist_ok=False)
    return directory


def validate_run_id(run_id: str) -> str:
    """校验运行 ID 必须是安全的单层目录名。"""
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run_id 必须是 1-100 个字母、数字、点、下划线或短横线")
    return run_id


def new_run_id() -> str:
    """生成不会包含路径语义的运行 ID。"""
    return f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
