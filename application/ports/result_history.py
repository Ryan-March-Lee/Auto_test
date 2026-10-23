"""只读历史结果浏览端口，与正式测量输入端口相互独立。

历史浏览只负责列出、定位和读取全局兼容副本/历史结果文件，供 GUI 查看和导出；
正式测量输入只能来自当前运行 manifest 登记并校验过的阶段结果。

端口刻意只暴露 ``result_id``（历史目录内的文件标识），而不是任意文件系统路径：
路径拼接、目录边界校验和越界拒绝全部由基础设施实现负责，调用方无法借历史浏览
读取历史目录之外的文件。端口也没有写入方法，也没有“把历史文件当作正式输入”的
能力，因此历史数据不会被自动带入新运行。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol, runtime_checkable


@dataclass(frozen=True)
class HistoryResultEntry:
    """一个只读历史结果的标识信息。

    ``result_type`` 使用领域结果类型（例如 ``cable_loss``），展示用的中文标签由
    表现层决定。固定文件名类型（线损）只会暴露当前兼容副本，时间戳类型（驱动
    映射/主功放）会暴露多个历史文件。
    """

    result_id: str
    result_type: str
    modified_at: datetime


@runtime_checkable
class ResultHistoryRepository(Protocol):
    """历史结果文件的只读视图。

    实现方不得写入、移动或删除历史结果文件，不得读取历史目录之外的文件，也不得
    把历史文件登记到任何运行 manifest；正式测量输入请使用当前运行的阶段记录器。
    """

    def list_entries(self) -> tuple[HistoryResultEntry, ...]:
        """返回全部历史结果，按修改时间从新到旧排序。"""

    def latest_result_id(self, result_type: str) -> str:
        """返回该类型最新的历史结果标识，仅用于只读浏览和导出。"""

    def load(self, result_id: str) -> Mapping[str, Any]:
        """按标识读取历史结果，不写回、不迁移。"""

    def read_bytes(self, result_id: str) -> bytes:
        """按标识读取历史结果的原始字节，用于逐字节导出。"""


__all__ = ["HistoryResultEntry", "ResultHistoryRepository"]
