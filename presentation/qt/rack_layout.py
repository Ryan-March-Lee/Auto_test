"""电源九宫格排布的纯数据工具。

仪器排布页的格位只描述“软件画面与现场电源架怎么对应”，不属于正式配置字段；
保存命名配置快照时把它作为界面恢复信息写进 ``metadata.json``。本模块只做三件事：

- 从配置里取本次需要摆放的电源引用（``supply_id``）；
- 清洗一份格位映射，丢掉未知电源、越界格位与重复格位；
- 给没有格位的电源按配置顺序补空位，保证旧快照（没有排布信息）也能直接进入
  电源配置页，并能从测试配置页一路返回排布页重新摆放。

模块不依赖 Qt，也不读写文件，测试可以直接构造映射。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .config_form_bindings import effective_device_bindings, power_supply_bindings

#: 与仪器排布页的 3×3 九宫格一致；格位下标从左上开始、先行后列。
SLOT_COUNT = 9


def configured_power_supply_ids(config: Any) -> list[str]:
    """返回配置里出现过的电源引用，按“绑定记录 → 旧 instruments 键”的顺序去重。

    绑定记录是当前写回结构的唯一来源；``instruments.power_supplies`` 里仍保留的旧键
    也纳入，避免加载旧文档时排布页少画一台电源。返回顺序稳定，供补位使用。
    """
    ids: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        supply_id = str(value or "").strip()
        if supply_id and supply_id not in seen:
            seen.add(supply_id)
            ids.append(supply_id)

    bindings = effective_device_bindings(config)
    for supply_id in power_supply_bindings(bindings):
        add(supply_id)
    instruments = config.get("instruments") if isinstance(config, Mapping) else None
    supplies = (
        instruments.get("power_supplies")
        if isinstance(instruments, Mapping)
        else None
    )
    if isinstance(supplies, Mapping):
        for supply_id in supplies:
            add(supply_id)
    return ids


def normalize_rack_slots(
    assignments: Any,
    supply_ids: Iterable[str],
) -> dict[str, int]:
    """清洗并补全一份格位映射。

    已知电源保留合法且不重复的格位；其余电源按 ``supply_ids`` 的顺序补进剩余空位。
    未知电源、越界格位、非整数和重复格位都直接丢弃，不抛异常——快照元数据损坏时
    仍应能加载配置，只是排布退回默认。
    """
    ids = _ordered_ids(supply_ids)
    known = set(ids)
    slot_of: dict[str, int] = {}
    used: set[int] = set()
    if isinstance(assignments, Mapping):
        for raw_id, raw_index in assignments.items():
            supply_id = str(raw_id or "").strip()
            if not supply_id or supply_id not in known or supply_id in slot_of:
                continue
            if isinstance(raw_index, bool):
                continue
            try:
                index = int(raw_index)
            except (TypeError, ValueError):
                continue
            if not 0 <= index < SLOT_COUNT or index in used:
                continue
            slot_of[supply_id] = index
            used.add(index)

    next_slot = 0
    for supply_id in ids:
        if supply_id in slot_of:
            continue
        while next_slot in used:
            next_slot += 1
        if next_slot >= SLOT_COUNT:
            break
        slot_of[supply_id] = next_slot
        used.add(next_slot)
    return {supply_id: slot_of[supply_id] for supply_id in ids if supply_id in slot_of}


def _ordered_ids(supply_ids: Iterable[str]) -> list[str]:
    """去重但保持输入顺序；空值直接跳过。"""
    ordered: list[str] = []
    seen: set[str] = set()
    for value in supply_ids:
        supply_id = str(value or "").strip()
        if supply_id and supply_id not in seen:
            seen.add(supply_id)
            ordered.append(supply_id)
    return ordered


__all__ = [
    "SLOT_COUNT",
    "configured_power_supply_ids",
    "normalize_rack_slots",
]
