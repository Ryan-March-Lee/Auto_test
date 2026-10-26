"""只读文件型历史结果仓储：供 GUI 历史查看与导出使用。

该适配器只暴露读取能力：不写入、不删除、不移动历史结果文件，不把历史文件登记
到任何运行 manifest，并强制把读取范围限制在 ``test_results`` 历史目录内。
正式测量输入仍由
:class:`infrastructure.persistence.result_repository.FileMeasurementResultRepository`
配合当前运行 manifest 提供。

本模块独立维护旧历史目录的枚举规则，不依赖正式运行结果仓储或
``json_result_repository`` 的写入实现。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from application.ports.result_history import HistoryResultEntry, ResultHistoryRepository
from infrastructure.filesystem.paths import PathService


# 步骤 3 之前由旧入口写入的全局历史类型与命名规则；仅供本只读适配器发现既有文件。
HISTORY_RESULT_TYPES: tuple[str, ...] = (
    "cable_loss",
    "driver_power_mapping",
    "amplifier_measurement",
)
_FIXED_HISTORY_FILENAMES: Mapping[str, str] = {
    "cable_loss": "cable_loss_results.json",
}
_TIMESTAMPED_HISTORY_PREFIXES: Mapping[str, str] = {
    "driver_power_mapping": "driver_power_mapping",
    "amplifier_measurement": "amplifier_measurement",
}


def _history_result_files(root: Path, result_type: str) -> tuple[Path, ...]:
    """按修改时间升序列出某类型的旧历史结果文件。"""
    fixed = _FIXED_HISTORY_FILENAMES.get(result_type)
    if fixed is not None:
        path = Path(root) / fixed
        return (path,) if path.is_file() else ()
    prefix = _TIMESTAMPED_HISTORY_PREFIXES.get(result_type)
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


def _latest_history_result_file(root: Path, result_type: str) -> Path:
    files = _history_result_files(root, result_type)
    if not files:
        raise FileNotFoundError(f"未找到结果类型为 {result_type} 的结果文件")
    return files[-1]


def _load_json_object(path: Path) -> Mapping[str, Any]:
    with path.open("r", encoding="utf-8") as result_file:
        value = json.load(result_file)
    if not isinstance(value, dict):
        raise ValueError("结果根节点必须是 JSON 对象")
    return value


class FileResultHistoryRepository(ResultHistoryRepository):
    """全局兼容副本/历史结果文件的只读访问实现。"""

    def __init__(self, *, paths: PathService):
        self.paths = paths

    # -- 读取 ---------------------------------------------------------------

    def list_entries(self) -> tuple[HistoryResultEntry, ...]:
        entries = [
            HistoryResultEntry(
                result_id=path.name,
                result_type=result_type,
                modified_at=datetime.fromtimestamp(path.stat().st_mtime),
            )
            for result_type in HISTORY_RESULT_TYPES
            for path in _history_result_files(self.paths.test_results_dir, result_type)
        ]
        return tuple(
            sorted(entries, key=lambda entry: entry.modified_at, reverse=True)
        )

    def latest_result_id(self, result_type: str) -> str:
        path = _latest_history_result_file(self.paths.test_results_dir, result_type)
        return path.name

    def load(self, result_id: str) -> Mapping[str, Any]:
        return _load_json_object(self._resolve(result_id))

    def read_bytes(self, result_id: str) -> bytes:
        return self._resolve(result_id).read_bytes()

    # -- 路径边界 -----------------------------------------------------------

    def _resolve(self, result_id: str) -> Path:
        """把 ``result_id`` 解析为历史目录内的文件，越界或缺失一律拒绝。

        只接受历史目录下的单个 JSON 文件名：拒绝绝对路径、目录分隔符、``..``
        片段和符号链接逃逸，从而保证历史浏览无法读取运行目录或项目外的文件。
        """
        if not isinstance(result_id, str) or not result_id.strip():
            raise ValueError("无效的结果标识")
        if Path(result_id).name != result_id or Path(result_id).suffix != ".json":
            raise ValueError("无效的结果标识")
        root = self.paths.test_results_dir.resolve()
        candidate = (self.paths.test_results_dir / result_id).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise ValueError(f"结果标识越出历史目录: {result_id}") from error
        if not candidate.is_file():
            raise FileNotFoundError(result_id)
        return candidate


__all__ = ["FileResultHistoryRepository"]
