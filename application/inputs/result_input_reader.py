"""Read persisted measurement inputs at the application composition boundary.

测量输入读取只接受**显式路径**：本读取器负责解析并加载调用方提供的路径，省略路径
会立即失败，绝不回退到“最新结果”自动查找。路径可以来自阶段结果目录的正式文件，也
可以是调用方认可的兼容结果文件；路径的归属、来源运行和完整性由调用方负责，本读取器
不判断“这份文件是否属于当前运行”，也不扫描目录。
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
from domain.measurement.values import CableLossTable


_MISSING_PATH_MESSAGE = (
    "测量输入必须显式提供路径（例如阶段结果目录中的结果文件）；"
    "不提供路径时不会回退到“最新结果”自动查找"
)


class ResultInputReader(ResultInputReaderPort):
    """Resolve and load calibration inputs from explicitly provided paths.

    The reader never discovers a result by modification time or scans a stage
    directory: every call requires an explicit path. Attribution/verification of
    that path is the caller's responsibility; stage-directory callers make sure
    the path came from the stage result repository.

    正式阶段结果一律先经 :func:`measurement_result_from_document` 校验，类型与
    调用入口不符时明确失败。步骤 7 之前写入运行目录、没有 ``measurement_type``
    的旧线损载荷仍按只读兼容处理，直到迁移/清理步骤退役；它不是新的正式输入格式。
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
    def _cable_loss_input(document: Mapping[str, Any]) -> Mapping[str, Any]:
        """校验线损输入并返回调用方后续使用的文档。

        阶段目录文件是正式结果，必须符合正式 DTO：类型必须是 ``cable_loss``，
        字段、状态和 schema 由 :func:`measurement_result_from_document` 统一校验。
        步骤 7 之前运行目录中的旧线损载荷没有 ``measurement_type``，仅按只读兼容
        读取：``cable_losses`` 必须是子对象，并按正式线损表结构校验频率键、线缆字段
        和数值，避免把任意或残缺 JSON 当线损输入交给业务层。旧载荷保持原样返回，
        不在读取器里补造正式结果字段。
        """
        if not isinstance(document, Mapping):
            raise ValueError("线损输入必须是 JSON 对象")
        if "measurement_type" in document:
            result = measurement_result_from_document(document)
            if result.measurement_type is not MeasurementType.CABLE_LOSS:
                raise ValueError("线损输入必须是 cable_loss 结果")
            return document
        cable_losses = document.get("cable_losses")
        if not isinstance(cable_losses, Mapping):
            raise ValueError("线损输入缺少 cable_losses 子对象")
        try:
            CableLossTable.from_mapping(cable_losses, source="线损输入")
        except TypeError as error:
            # 读取器边界统一以 ValueError 表达“不能作为线损输入”，与其它入口一致。
            raise ValueError(str(error)) from error
        return document

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
        return self._cable_loss_input(
            self.repository.load(self._explicit_path(path))
        )

    def read_driver_mapping(self, path: PathLike | None) -> DriverMappingInput:
        return self._driver_mapping_input(
            self.repository.load(self._explicit_path(path))
        )


__all__ = ["ResultInputReader"]
