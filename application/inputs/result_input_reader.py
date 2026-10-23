"""Read persisted measurement inputs at the application composition boundary.

正式测量输入只接受显式路径：调用方必须传入当前运行 manifest 登记并校验过的阶段
结果路径。省略路径会立即失败，绝不回退到“最新结果”自动查找，从而保证历史数据
不会被带入正式运行。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from application.ports.result_input_reader import ResultInputReader as ResultInputReaderPort
from application.ports.result_repository import MeasurementResultRepository, PathLike


_MISSING_PATH_MESSAGE = (
    "正式测量输入必须显式提供当前运行 manifest 登记的路径；"
    "无路径的兼容读取请使用 application.inputs.LegacyResultInputReader"
)


class ResultInputReader(ResultInputReaderPort):
    """Resolve and load calibration inputs from explicitly provided paths.

    The reader never discovers a result by modification time: every call requires
    the path that the current run manifest has verified.
    """

    def __init__(self, repository: MeasurementResultRepository):
        self.repository = repository

    @staticmethod
    def _explicit_path(path: PathLike | None) -> Path:
        """Resolve the caller-provided path; ``None`` is rejected, not defaulted."""
        if path is None:
            raise ValueError(_MISSING_PATH_MESSAGE)
        return Path(path).expanduser().resolve()

    @staticmethod
    def _power_mapping(result: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            return result["power_mapping"]
        except KeyError as exc:
            raise ValueError("驱动映射结果缺少 power_mapping 字段") from exc

    def read_cable_loss(self, path: PathLike | None) -> Mapping[str, Any]:
        return self.repository.load(self._explicit_path(path))

    def read_driver_mapping(self, path: PathLike | None) -> Mapping[str, Any]:
        return self._power_mapping(self.repository.load(self._explicit_path(path)))


__all__ = ["ResultInputReader"]
