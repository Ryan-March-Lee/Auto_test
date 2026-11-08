"""Read persisted measurement inputs at the application composition boundary.

测量输入读取只接受**显式路径**：本读取器负责解析并加载调用方提供的路径，省略路径
会立即失败，绝不回退到“最新结果”自动查找。路径的归属与完整性校验不在本读取器内：
运行契约运行的路径由 :class:`RunStageRecorder.verified_result_path` 按当前 ``run_id``
manifest 校验后才传入；旧兼容流程则直接传入调用方认可的显式路径。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from application.dto import (
    DriverMappingInput,
    MeasurementType,
    measurement_result_from_document,
)
from application.ports.result_input_reader import ResultInputReader as ResultInputReaderPort
from application.ports.result_repository import MeasurementResultRepository, PathLike


_MISSING_PATH_MESSAGE = (
    "测量输入必须显式提供路径（运行契约场景为当前运行 manifest 登记的阶段结果）；"
    "无路径的“最新结果”兼容读取已在重构步骤 B3 删除"
)


class ResultInputReader(ResultInputReaderPort):
    """Resolve and load calibration inputs from explicitly provided paths.

    The reader never discovers a result by modification time: every call requires
    an explicit path. Attribution/verification of that path is the caller's
    responsibility (run-contract callers pass a manifest-verified path).
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
    def _driver_mapping_input(document: Mapping[str, Any]) -> DriverMappingInput:
        """把正式驱动映射文档收敛为可执行的**目标网格输入**。

        正式输入必须是 ``schema_version=2.0`` 的双层契约；旧 ``power_mapping``
        文档会被正式结果读取器明确拒绝，不会被当成目标输入表。目标网格为空
        （映射尚未生成目标表）或目标源功率落在原始曲线之外时，同样不能作为正式
        测试输入。原始点只随输入对象携带供审计，不作为目标表返回。
        """
        result = measurement_result_from_document(document)
        if result.measurement_type is not MeasurementType.DRIVER_POWER_MAPPING:
            raise ValueError("驱动映射输入必须是 driver_power_mapping 结果")
        return DriverMappingInput.from_result(result)

    def read_cable_loss(self, path: PathLike | None) -> Mapping[str, Any]:
        return self.repository.load(self._explicit_path(path))

    def read_driver_mapping(self, path: PathLike | None) -> DriverMappingInput:
        return self._driver_mapping_input(
            self.repository.load(self._explicit_path(path))
        )


__all__ = ["ResultInputReader"]
