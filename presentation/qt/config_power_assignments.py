"""电源通道候选的纯数据读取（不依赖 Qt，也不读取表单控件）。

上电计划以物理路由 ``supply_id/channel`` 为唯一身份；本模块只回答“一台已绑定电源
声明了哪些可用通道”。配置页与独立电源表单共用这一条规则，避免各写一份通道回落逻辑。
"""

from typing import Any, Mapping

__all__ = ["POWER_SUPPLY_CHANNELS", "assignment_channels"]

# 每台电源固定两个通道：通道名既是配置里的键，也是标签页内通道分组框的标题。
POWER_SUPPLY_CHANNELS = ("CH1", "CH2")


def assignment_channels(entry: Any) -> list[str]:
    """一台电源声明的物理通道，作为上电计划的候选。

    条目缺少 ``channel``（尚未迁移/新建槽位）时回落到每台电源固定的两个通道；显式的
    空列表原样返回，让正式校验报告结构错误，不在读取时替用户补通道。
    """
    if isinstance(entry, Mapping):
        channels = entry.get("channel")
        if isinstance(channels, (list, tuple)):
            return [item for item in channels if isinstance(item, str) and item.strip()]
    return list(POWER_SUPPLY_CHANNELS)
