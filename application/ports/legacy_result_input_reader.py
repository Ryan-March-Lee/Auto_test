"""旧兼容入口专用的测量输入读取契约。

这些方法**不是**正式测量输入端口的一部分：它们在没有显式路径时回退到全局兼容
结果（固定线损文件或时间戳型最新结果），只用于尚未迁移的旧兼容/回滚入口，并且
在调用时发出弃用警告。新运行必须使用当前 ``run_id`` manifest 登记的显式输入。
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable


@runtime_checkable
class LegacyResultInputReader(Protocol):
    """无路径兼容读取能力，仅服务旧兼容测量入口。"""

    def read_legacy_cable_loss(self) -> Mapping[str, Any]:
        """读取全局兼容线损副本（旧的固定路径行为）。"""

    def read_latest_legacy_driver_mapping(self) -> Mapping[str, Any]:
        """读取全局最新的兼容驱动映射结果。"""


__all__ = ["LegacyResultInputReader"]
