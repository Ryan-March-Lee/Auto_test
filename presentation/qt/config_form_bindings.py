"""设备绑定的读取与规范化。

本模块只处理“设备绑定记录”这一类数据：从 ``device_bindings`` 读出
``supply_id -> 记录`` 的可用集合，把步骤 5 的过渡结构与只有地址的旧配置折叠成当前
结构，并把不合格的记录变成面向操作员的句子。输入是配置/绑定字典，输出是普通
``dict`` / ``list``：不读控件、不写配置、不碰 Qt，因此可以在没有 QApplication 的情况下
单独测试，也不需要另一块表单的控件状态。

控件读数与配置合并属于 :mod:`presentation.qt.config_form_state`；正式运行映射的编写在
:mod:`presentation.qt.configuration_authoring`，正式校验在
:mod:`domain.configuration.rules` 与 :mod:`infrastructure.config.validation`。这里既不
构造正式 schema，也不决定设备身份（身份只来自设备发现结果与设备表）。
"""

import copy
from typing import Any, Mapping

from domain.instruments.models import (
    INSTRUMENT_ROLES,
    POWER_SUPPLY_ROLE,
    canonical_role,
    make_device_key,
    normalize_serial,
)

# ``device_bindings`` 里的电源集合：键是 ``supply_id``（= 设备短标签），值是绑定记录。
# 电源是同一类别下的多台设备，不能像信号源那样用角色键存放——否则第二台会覆盖第一台。
POWER_SUPPLY_BINDINGS = "power_supplies"
# 旧配置只有 ``instruments.<角色>.address``、没有绑定记录。迁移时用这个来源标记，
# 表示“地址来自已保存的配置，尚未经过设备发现确认”，与 ``scan`` / ``simulated`` 分开。
LEGACY_BINDING_SOURCE = "legacy_config"
# 电源引用会拼成 ``supply_id/CHx`` 交给 ``instrument.measurement_factory`` 路由，因此
# ``supply_id`` 与注册表里的 ``label`` 受同一套约束：不能含空白和通道分隔符。
SUPPLY_ID_SEPARATOR = "/"


def _record_copy(value: Any) -> dict | None:
    """复制一条绑定记录；不是对象时返回 ``None``（无法解释的条目按缺失处理）。"""
    return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else None


def binding_address(binding: Any) -> str:
    """返回绑定记录里的连接地址；记录缺失或地址不是文本时返回空串。"""
    if not isinstance(binding, Mapping):
        return ""
    address = binding.get("visa_address")
    return address.strip() if isinstance(address, str) else ""


def binding_device_key(record: Any) -> str | None:
    """返回一条设备绑定记录的身份键 ``厂商:序列号``；无法确定身份时返回 ``None``。

    身份是 ``device_key``。旧记录只有厂商与序列号两段时在这里重新拼出同一个键；
    只有序列号没有厂商时无法确定身份，返回 ``None``（不做“仅按序列号”的猜测匹配，
    否则两个厂商的同号设备会被混为一谈）。
    """
    if not isinstance(record, Mapping):
        return None
    key = str(record.get("device_key") or "").strip().casefold()
    if key:
        return key
    vendor = str(record.get("vendor") or "").strip()
    serial = str(record.get("serial") or "").strip()
    if not vendor or not serial:
        return None
    try:
        return make_device_key(vendor, serial)
    except ValueError:
        return None


def _legacy_supply_id(record: Mapping[str, Any]) -> str | None:
    """旧槽位绑定里的短标签；记录没有标签就没有可用的 ``supply_id``。"""
    for field in ("supply_id", "label"):
        value = record.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def supply_id_problem(value: Any) -> str | None:
    """返回一个不能当 ``supply_id`` 使用的具体原因；可用时返回 ``None``。

    规则与注册表里的 ``label`` 同源：引用会拼成 ``supply_id/CHx``，
    所以不能为空、不能含空白、不能含通道分隔符 ``/``。这里只判断、不做清洗——写错的
    标签必须报错，不能在校验时被悄悄改成“看起来合法”的值。
    """
    if not isinstance(value, str):
        return "引用必须是字符串"
    if not value.strip():
        return "引用为空"
    if value != value.strip():
        return "引用含首尾空白"
    if SUPPLY_ID_SEPARATOR in value:
        return f"不得含通道分隔符 {SUPPLY_ID_SEPARATOR!r}"
    if any(char.isspace() for char in value):
        return "不得含空白"
    return None


def _binding_identity_problem(record: Mapping[str, Any]) -> str | None:
    """返回一条绑定记录身份不自洽的原因；身份完整时返回 ``None``。

    身份就是 ``device_key``（``厂商:序列号``）：记录必须能给出它，且它必须与记录里的
    厂商/序列号一致。手改文件、旧版本写出的“地址 + 矛盾身份”不能进入可用绑定集合，
    否则配置会连接到一台身份说不清（或身份与地址错配）的设备。
    """
    identity = binding_device_key(record)
    if identity is None:
        return "缺少设备身份（device_key，或厂商 + 序列号）"
    device_key = str(record.get("device_key") or "").strip()
    if not device_key:
        return None
    vendor = str(record.get("vendor") or "").strip()
    serial = str(record.get("serial") or "").strip()
    if vendor and serial:
        try:
            expected = make_device_key(vendor, serial)
        except ValueError:
            return f"设备身份无法解析（{vendor!r} / {serial!r}）"
        if device_key.casefold() != expected:
            return f"设备身份与厂商/序列号不一致（{device_key} ≠ {expected}）"
        return None
    if serial and ":" in device_key:
        # 没有厂商时至少要求 ``device_key`` 携带的序列号与本次记录一致。
        tail = device_key.rsplit(":", 1)[1].strip().casefold()
        stable = normalize_serial(serial).casefold()
        if tail and stable and tail != stable:
            return f"设备身份与序列号不一致（{device_key} ≠ …{normalize_serial(serial)}）"
    return None


def _iter_power_binding_entries(
    bindings: Mapping[str, Any],
) -> list[tuple[str, str | None, dict | None]]:
    """逐条列出电源绑定候选：``(来源说明, supply_id, 记录)``。

    新结构 ``power_supplies[<supply_id>]`` 里每一条都是一次绑定声明，必须可用；记录
    不是对象时也返回一条（``记录`` 为 ``None``），让校验路径能明确报告，而不是静默丢失。
    集合键按**原字面值**参与校验：首尾空白同样是不合法的引用，不在这里悄悄裁剪。

    旧槽位的过渡记录只有带上身份标记（短标签或 ``device_key``）才算“一次绑定尝试”；
    **只有地址的旧绑定**按既定规则直接丢弃并要求重新扫描——地址不是身份，报成错误只会
    让一份本来就需要重扫的旧配置无法保存。过渡记录里的短标签允许去掉首尾空白：那是
    步骤 5 写出的历史字段，不属于任何配置键。
    """
    entries: list[tuple[str, str | None, dict | None]] = []
    nested = bindings.get(POWER_SUPPLY_BINDINGS)
    if isinstance(nested, Mapping):
        for key, value in nested.items():
            supply_id = key if isinstance(key, str) else str(key)
            entries.append((f"电源绑定 {key}", supply_id, _record_copy(value)))
    for key, value in bindings.items():
        if key in INSTRUMENT_ROLES or key == POWER_SUPPLY_BINDINGS:
            continue
        if canonical_role(key) != POWER_SUPPLY_ROLE:
            continue
        record = _record_copy(value)
        if record is None:
            continue
        supply_id = _legacy_supply_id(record)
        if supply_id is None and binding_device_key(record) is None:
            # 只有地址的旧绑定：既定规则是重新扫描，不是一条可诊断的错误记录。
            continue
        entries.append((f"旧槽位绑定 {key}", supply_id, record))
    return entries


def power_supply_bindings(bindings: Any) -> dict[str, dict]:
    """返回 ``supply_id -> 绑定记录`` 的电源绑定集合。

    新结构是 ``device_bindings.power_supplies[<supply_id>]``（集合的键就是引用 ID，
    记录内部不再重复写 ``supply_id`` / ``label`` / ``role``）。读取时兼容步骤 5 的过渡
    结构：记录按旧槽位 ``PS1``..``PS4`` 落键、记录内带短标签与设备身份。旧记录只有在
    **身份可确认**（``厂商:序列号``）且带短标签时才会迁移——只有地址的旧绑定不是身份，
    按既定规则必须重新扫描，这里不猜成某台电源。

    每条记录都要过同一套校验（见 :func:`power_supply_binding_problems`）：引用能否当
    ``supply_id``、身份是否完整自洽。不合格的记录**不进入**集合，因此不会出现在电源
    分配下拉框里，也不会被写地址；问题本身由校验路径明确报告，而不是静默消失。

    同一台设备只能出现在一条电源绑定里；手改文件造出的重复身份只保留第一条，不静默
    让同一台设备被连接两次（重复同样计入问题列表）。
    """
    if not isinstance(bindings, Mapping):
        return {}
    supplies: dict[str, dict] = {}
    seen: dict[str, str] = {}
    for _where, supply_id, record in _iter_power_binding_entries(bindings):
        if record is None or not supply_id or supply_id in supplies:
            continue
        if supply_id_problem(supply_id) or _binding_identity_problem(record):
            continue
        identity = binding_device_key(record)
        if identity is None or identity in seen:
            continue
        seen[identity] = supply_id
        record.pop("role", None)
        record.pop("label", None)
        record.pop("supply_id", None)
        supplies[supply_id] = record
    return supplies


def power_supply_binding_problems(bindings: Any) -> list[str]:
    """列出电源绑定里每条不可用的记录（面向操作员的句子）；可用绑定不产生条目。

    存在的意义是“不要静默丢弃”：不合格的记录不会成为可选项，但必须能让操作员看到
    是哪一条、为什么不能用——否则一次手改或旧版本写出的坏绑定会表现为“电源莫名
    消失”，而配置里的地址在保存时被清空也无人知晓。
    """
    if not isinstance(bindings, Mapping):
        return []
    problems: list[str] = []
    claimed: dict[str, str] = {}
    devices: dict[str, str] = {}
    for where, supply_id, record in _iter_power_binding_entries(bindings):
        display = f"{where}（{supply_id}）" if supply_id else where
        if record is None:
            problems.append(f"{where} 不是一条绑定记录")
            continue
        if not supply_id:
            problems.append(f"{where} 没有可用的电源引用（supply_id）")
            continue
        reason = supply_id_problem(supply_id)
        if reason:
            problems.append(f"{display} 的引用不能作为 supply_id：{reason}")
            continue
        reason = _binding_identity_problem(record)
        if reason:
            problems.append(f"{display} {reason}")
            continue
        if supply_id in claimed:
            problems.append(f"{display} 与 {claimed[supply_id]} 使用了同一个电源引用")
            continue
        claimed[supply_id] = where
        identity = binding_device_key(record)
        if identity is not None and identity in devices:
            problems.append(
                f"{display} 与 {devices[identity]} 是同一台设备 {identity}，"
                "同一台设备只能有一条电源绑定"
            )
            continue
        if identity is not None:
            devices[identity] = display
    return problems


def binding_records(bindings: Any) -> list[tuple[str, dict]]:
    """扁平展开绑定记录，返回 ``(键, 记录)``。

    信号源与频谱仪用角色键，电源用 ``supply_id``。这里只用于遍历、显示与生成消息，
    不作为写回结构：电源写回时必须回到 ``power_supplies.<supply_id>``。
    """
    records: list[tuple[str, dict]] = []
    if not isinstance(bindings, Mapping):
        return records
    for role in INSTRUMENT_ROLES:
        record = _record_copy(bindings.get(role))
        if record is not None:
            records.append((role, record))
    records.extend(power_supply_bindings(bindings).items())
    return records


def _normalized_bindings(bindings: Mapping[str, Any]) -> dict[str, Any]:
    """把一份 ``device_bindings`` 规范成“角色键 + 电源集合”的结构。"""
    normalized: dict[str, Any] = {}
    for role in INSTRUMENT_ROLES:
        record = _record_copy(bindings.get(role))
        if record is not None:
            normalized[role] = record
    supplies = power_supply_bindings(bindings)
    if supplies:
        normalized[POWER_SUPPLY_BINDINGS] = supplies
    return normalized


def _legacy_device_bindings(instruments: Mapping[str, Any]) -> dict[str, dict]:
    """把只有地址的旧配置迁移成信号源/频谱仪的绑定记录。

    只迁移“启用且地址非空”的仪器：禁用项不会被迁移动作重新启用。迁移记录没有序列号
    与 ``device_key``，因此下一次扫描不会按身份预选，面板会把它显示为“沿用已保存的
    地址，未扫描确认”。

    **电源不在迁移范围内**：配置里的一个地址既不是设备身份，也不能说明是哪一台电源。
    旧的 ``instruments.power_supplies.<槽位>.address`` 只是“以前连过这个地址”，按既定规则
    必须重新扫描设备表并应用，才能得到带 ``device_key`` 的电源绑定。
    """
    bindings: dict[str, dict] = {}
    for role in INSTRUMENT_ROLES:
        item = instruments.get(role)
        if not isinstance(item, Mapping):
            continue
        address = item.get("address")
        if not isinstance(address, str) or not address.strip():
            continue
        if item.get("enabled") is False:
            continue
        bindings[role] = {
            "name": None,
            "vendor": None,
            "model": item.get("model"),
            "serial": None,
            "device_key": None,
            "visa_address": address.strip(),
            "last_ip": None,
            "last_port": None,
            "known": False,
            "source": LEGACY_BINDING_SOURCE,
        }
    return bindings


def effective_device_bindings(config: Any) -> dict[str, dict]:
    """返回一份配置里的有效设备绑定，作为仪器连接的唯一来源。

    结构是“信号源 / 频谱仪各一条 + ``power_supplies`` 里按 ``supply_id`` 的多条”。
    配置带 ``device_bindings`` 时以它为准（即使为空——用户清除过绑定）；缺少该键的旧
    配置才按 ``instruments.<角色>.address`` 做一次性迁移（只含信号源与频谱仪）。迁移只
    发生在这里，之后保存的配置都带显式绑定记录，不会因为“地址还在”而重新变回可连接
    状态。

    旧字段只在读入边界被识别：步骤 5 的电源绑定按旧槽位落键，记录里带短标签与身份，
    这里折叠成 ``power_supplies[<supply_id>]``；只有地址的电源绑定不具备身份，不迁移。
    """
    if not isinstance(config, Mapping):
        return {}
    bindings = config.get("device_bindings")
    if isinstance(bindings, Mapping):
        return _normalized_bindings(bindings)
    instruments = config.get("instruments")
    instruments = instruments if isinstance(instruments, Mapping) else {}
    return _legacy_device_bindings(instruments)


def bound_power_supplies(bindings: Any) -> list[str]:
    """返回已绑定且有地址的电源 ``supply_id``，按绑定记录顺序。

    这是电源分配唯一的可用来源：集合的大小与成员完全由本次设备绑定决定，1 台、2 台或
    5 台都只是同一份集合的不同长度，不再有固定的 ``PS1``..``PS4`` 顺序或数量。没有
    地址的绑定不算可用（连接需要地址），未绑定的配置引用也不会因此获得新候选。
    """
    return [
        supply_id
        for supply_id, record in power_supply_bindings(bindings).items()
        if binding_address(record)
    ]


def bound_power_supply_entries(bindings: Any) -> dict[str, dict]:
    """返回 ``supply_id -> 绑定记录``，只保留已绑定且有地址的电源（与可用集合同序）。

    界面的可选项用它补显示文本（短标签 + 型号）：型号是绑定记录里**真实设备**的型号，
    不是配置里可能残留的旧型号，也不是从显示字符串反解析出来的。
    """
    return {
        supply_id: record
        for supply_id, record in power_supply_bindings(bindings).items()
        if binding_address(record)
    }


__all__ = [
    "LEGACY_BINDING_SOURCE",
    "POWER_SUPPLY_BINDINGS",
    "binding_address",
    "binding_device_key",
    "binding_records",
    "bound_power_supplies",
    "bound_power_supply_entries",
    "effective_device_bindings",
    "power_supply_binding_problems",
    "power_supply_bindings",
    "supply_id_problem",
]
