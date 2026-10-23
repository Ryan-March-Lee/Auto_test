"""Application contract for loading persisted measurement inputs.

正式测量输入端口只接受**显式路径**：路径必须来自当前运行 manifest 登记并校验过
的阶段结果。端口刻意不提供“按最新写入时间自动查找”的无路径读取，避免全局历史
结果被自动带入正式运行。旧兼容入口的无路径查找请使用
:mod:`application.ports.legacy_result_input_reader`。
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable

from .types import PathLike


@runtime_checkable
class ResultInputReader(Protocol):
    """Read calibration inputs from an explicit, already-verified path."""

    def read_cable_loss(self, path: PathLike) -> Mapping[str, Any]:
        """Load cable-loss data from the explicitly provided path."""

    def read_driver_mapping(self, path: PathLike) -> Mapping[str, Any]:
        """Load driver mapping data from the explicitly provided path."""


__all__ = ["ResultInputReader"]
