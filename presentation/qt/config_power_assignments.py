"""供电分配条目的纯数据读取（不依赖 Qt，也不读取表单控件）。

界面槽位（``pa_unit1_power`` 等）与配置槽位（``carrier`` 等）之间由
:mod:`presentation.qt.config_projection` 的 ``ASSIGNMENT_SLOT_PATHS`` 连接；本模块只回答
“一条分配声明了哪些物理通道”。配置页的读数（``config_form_state``）与独立电源表单
（``config_power_section``）共用这一条规则，避免两边各写一份通道回落逻辑。
"""

from typing import Any, Mapping

__all__ = ["POWER_SUPPLY_CHANNELS", "assignment_channels"]

# 每台电源固定两个通道：通道名既是配置里的键，也是标签页内通道分组框的标题。
POWER_SUPPLY_CHANNELS = ("CH1", "CH2")


def assignment_channels(entry: Any) -> list[str]:
    """一条供电分配声明的物理通道（“属于哪个 PA 单元”）。

    编辑段里 ``supplies.<slot>.channel`` 是分配通道集合，计划只决定它们的顺序；
    两者集合相等由正式校验比较。条目缺少 ``channel``（尚未迁移/新建槽位）时回落到
    每台电源固定的两个通道；显式的空列表原样返回，让正式校验报告结构错误，不在读取
    时替用户补通道。
    """
    if isinstance(entry, Mapping):
        channels = entry.get("channel")
        if isinstance(channels, (list, tuple)):
            return [item for item in channels if isinstance(item, str) and item.strip()]
    return list(POWER_SUPPLY_CHANNELS)
