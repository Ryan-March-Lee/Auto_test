"""旧兼容入口专用的输入读取器：显式读取 + 无路径兼容查找。

``ResultInputReader`` 只做显式路径的正式读取；本类在它的基础上补回旧兼容入口需要
的无路径查找（固定线损副本 / 最新映射结果）。两者都发出弃用警告，且只在没有运行
契约的旧流程中使用。
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Mapping

from application.ports.legacy_result_input_reader import (
    LegacyResultInputReader as LegacyResultInputReaderPort,
)
from .result_input_reader import ResultInputReader


class LegacyResultInputReader(ResultInputReader, LegacyResultInputReaderPort):
    """兼容读取器：服务旧兼容/回滚测量入口，不用于正式运行输入。"""

    def legacy_cable_loss_path(self) -> Path:
        """兼容线损副本的固定路径（旧的全局读取位置）。"""
        return self.repository.legacy_path("cable_loss")

    def read_legacy_cable_loss(self) -> Mapping[str, Any]:
        warnings.warn(
            "未显式提供线损输入路径的兼容读取已弃用；"
            "新运行必须使用当前 run_id manifest 登记的输入",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.repository.load(self.legacy_cable_loss_path())

    def latest_legacy_driver_mapping_path(self) -> Path:
        """兼容驱动映射的“最新结果”路径（按修改时间扫描全局目录）。"""
        warnings.warn(
            "未显式提供 driver_mapping_path 的兼容读取已弃用；"
            "新运行必须使用当前 run_id manifest 登记的输入",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.repository.latest_path("driver_power_mapping")

    def read_latest_legacy_driver_mapping(self) -> Mapping[str, Any]:
        return self._power_mapping(
            self.repository.load(self.latest_legacy_driver_mapping_path())
        )


__all__ = ["LegacyResultInputReader"]
