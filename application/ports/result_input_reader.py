"""Application contract for loading persisted measurement inputs.

测量输入端口只接受**显式路径**：路径由调用方明确给出，可以是阶段结果目录中的正式
结果文件，也可以是调用方认可的兼容结果文件；端口不判断路径属于哪个运行，也不提供
“按最新写入时间自动查找”的无路径读取，避免全局历史结果被自动带入正式运行。

驱动映射输入端口返回已校验的 :class:`DriverMappingInput`：正式测试消费其中的目标
网格，原始实测点只作审计。端口不再把裸“输入 -> 输出”字典交给调用方。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping, Protocol, runtime_checkable

from .types import PathLike

if TYPE_CHECKING:  # pragma: no cover - 仅类型标注，避免 dto/ports 运行时循环导入
    from application.dto.measurement_results import DriverMappingInput, MeasurementResult


@runtime_checkable
class ResultInputReader(Protocol):
    """Read calibration inputs from an explicit, already-verified path."""

    def read_cable_loss(self, path: PathLike) -> Mapping[str, Any]:
        """Load cable-loss data from the explicitly provided path."""

    def read_driver_mapping(self, path: PathLike) -> "DriverMappingInput":
        """Load the validated two-layer driver-mapping input from the path."""

    def read_cable_loss_result(self, result: "MeasurementResult") -> Mapping[str, Any]:
        """Validate an already-loaded formal cable-loss result without re-reading."""

    def read_driver_mapping_result(
        self, result: "MeasurementResult"
    ) -> "DriverMappingInput":
        """Turn an already-loaded formal driver-mapping result into its target input."""


__all__ = ["ResultInputReader"]
