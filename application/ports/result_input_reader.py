"""Application contract for loading persisted measurement inputs.

测量输入端口只接受**显式路径**：路径必须来自当前运行 manifest 登记并校验过的阶段
结果。端口刻意不提供“按最新写入时间自动查找”的无路径读取，避免全局历史结果被自动
带入正式运行；无路径兼容查找已在重构步骤 B3 删除。

驱动映射输入端口返回已校验的 :class:`DriverMappingInput`：正式测试消费其中的目标
网格，原始实测点只作审计。端口不再把裸“输入 -> 输出”字典交给调用方。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping, Protocol, runtime_checkable

from .types import PathLike

if TYPE_CHECKING:  # pragma: no cover - 仅类型标注，避免 dto/ports 运行时循环导入
    from application.dto.measurement_results import DriverMappingInput


@runtime_checkable
class ResultInputReader(Protocol):
    """Read calibration inputs from an explicit, already-verified path."""

    def read_cable_loss(self, path: PathLike) -> Mapping[str, Any]:
        """Load cable-loss data from the explicitly provided path."""

    def read_driver_mapping(self, path: PathLike) -> "DriverMappingInput":
        """Load the validated two-layer driver-mapping input from the path."""


__all__ = ["ResultInputReader"]
