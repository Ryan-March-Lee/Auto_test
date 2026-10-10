"""旧测量结果载荷的短期兼容适配器。

应用层和展示层只应通过本模块读取历史 service 字段。正式结果元数据仍由
``MeasurementResult`` 提供，适配器不负责业务计算或持久化。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .measurement_results import MeasurementResult, thaw


def legacy_payload(value: MeasurementResult | Mapping[str, Any]) -> Mapping[str, Any]:
    """Return a detached mapping for legacy result consumers."""
    if isinstance(value, MeasurementResult):
        return thaw(value.payload)
    if isinstance(value, Mapping):
        return dict(value)
    return {}


__all__ = ["legacy_payload"]
