"""旧 ``PS1..PS4`` 配置到 ``supply_id`` 的字段级迁移器（电源重构计划步骤 13）。

迁移只回答一个问题：**旧配置里的每个电源条目和每条供电分配，能不能用已登记的身份
改写成新字段**。它不扫描设备，也不猜身份：

- 身份判据只有 ``device_key``（``厂商:序列号``）与注册表里冻结的短标签 ``label``；
- 旧地址 ``TCPIP0::…`` 只作重新扫描的线索（``address_hint``），**不参与**身份判定；
- 认不出是哪一台的旧条目不会凭空得到身份，它的通道参数与仍引用它的分配写进迁移摘要，
  由操作员按摘要重新扫描后重填（摘要把每个旧条目的判定、身份与参数逐行写清）。

判定顺序::

    1. 旧条目自带 ``device_key`` / ``serial``，注册表唯一命中    -> migrated
    2. 旧键是 ``PSn``，注册表正好有一条 ``role == PSn``          -> migrated
    3. 旧键是 ``PSn``，该角色对应多台 / 命中的设备已有新键       -> conflict
    4. 身份字段互相矛盾、注册表里身份不唯一、``device_key`` 不在册 -> conflict
    5. 旧条目只有 ``address``（地址命中注册表也只是线索）        -> needs_rescan
    6. 旧条目既无 ``address`` 也无 ``serial``                    -> unidentifiable
    7. 两个旧条目落在同一 ``device_key``                         -> conflict（阻止整次迁移）

**身份字段之间必须互相印证**：旧条目同时写 ``device_key`` 与 ``serial`` 时，两者必须指向
设备表里的同一条记录；指向不同设备、或注册表里同一 ``device_key`` / 序列号对应多条记录，
都按“无法确认是哪一台”报 ``conflict``，绝不按字段顺序挑一个继续迁移。

迁移的输入与输出都是内存对象：:func:`migrate_legacy_power_supplies` 返回目标配置与逐条摘要，
不改入参、不写盘。**只有每一条旧条目都 ``migrated``、且没有仍为空的供电分配引用时，结果才
允许落盘**（:attr:`LegacyMigrationResult.writable`）：:func:`save_migrated_configuration` 在
写盘前先跑文件级校验，任何未决项或校验失败都抛错且原文件逐字节不变。认不出的旧条目的通道
参数只存在于摘要里，因此摘要必须能离开进程保存——用 :func:`save_migration_summary` 把它写成
独立的摘要文件，而不是靠“先把半成品写进 config.json”来留存。

现场样例（``tests/fixtures/power_supply_identity/``）：``config_slots_legacy.json`` +
``registry_legacy_v2.json`` -> ``config_slots_migrated_partial.json`` +
``migration_summary_legacy.json``。
"""

from __future__ import annotations

import copy
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import unquote

from domain.configuration.rules import ASSIGNMENT_GROUPS, assignment_supply_id
from domain.instruments.models import (
    LEGACY_ROLES,
    normalize_role,
    normalize_serial,
    normalize_text,
)
from infrastructure.config.json_io import load_json_object
from infrastructure.config.validation import validate_official_config
from infrastructure.filesystem.atomic_json import read_json, write_json_atomic
from infrastructure.persistence.instrument_registry import (
    DEFAULT_REGISTRY_FILENAME,
    InstrumentRegistry,
    InstrumentRegistryError,
)

PathLike = Any

# 迁移摘要的版本号；摘要结构变化时递增。
SUMMARY_SCHEMA_VERSION = 1

# 迁移判定结果：只有这四种。
STATUS_MIGRATED = "migrated"
STATUS_NEEDS_RESCAN = "needs_rescan"
STATUS_UNIDENTIFIABLE = "unidentifiable"
STATUS_CONFLICT = "conflict"
MIGRATION_STATUSES = (
    STATUS_MIGRATED,
    STATUS_NEEDS_RESCAN,
    STATUS_UNIDENTIFIABLE,
    STATUS_CONFLICT,
)
# 需要人工处理后才能使用的判定：目标配置里不会为它们生成电源条目。
STATUSES_REQUIRING_ACTION = (STATUS_NEEDS_RESCAN, STATUS_UNIDENTIFIABLE, STATUS_CONFLICT)

# 集合键与条目内部重复的身份字段：新格式只从注册表取身份，旧条目里的同名键不沿用。
_IDENTITY_FIELDS = ("label", "device_key", "serial", "model")
_SEPARATOR = "/"
_IPV4 = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
# 供电角色与连接说明的措辞与配置作者（``presentation.qt.configuration_authoring``）一致：
# 迁移生成的运行快照必须和配置页保存出来的那份长得一样，否则同一份映射会有两种文本。
_DUT_GATE = "gate"
_DUT_DRAIN = "drain"
_DRIVER_ROLE = "driver"


class LegacyConfigMigrationError(ValueError):
    """迁移输入不合法（不是对象、电源集合不是对象等），或未决结果被要求写盘。"""


@dataclass(frozen=True)
class LegacyMigrationEntry:
    """一个旧电源条目的迁移判定，对应迁移摘要里的一行。

    字段顺序与摘要 JSON 一致；``channels`` / ``assignments`` 原样取自旧配置，
    不因“未绑定”而丢参数，也是操作员重新扫描后重填的依据。
    """

    old_key: str
    legacy_address: Optional[str]
    legacy_enabled: Optional[bool]
    status: str
    new_key: Optional[str]
    device_key: Optional[str]
    serial: Optional[str]
    label: Optional[str]
    address_hint: Optional[str]
    reason: str
    assignments: Tuple[Dict[str, Any], ...] = ()
    channels: Dict[str, Any] = field(default_factory=dict)
    # 设备表里的型号，只用于写目标配置的电源条目；摘要只输出判定字段，不含它。
    model: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "old_key": self.old_key,
            "legacy_address": self.legacy_address,
            "legacy_enabled": self.legacy_enabled,
            "status": self.status,
            "new_key": self.new_key,
            "device_key": self.device_key,
            "serial": self.serial,
            "label": self.label,
            "address_hint": self.address_hint,
            "reason": self.reason,
            "assignments": [dict(item) for item in self.assignments],
            "channels": copy.deepcopy(self.channels),
        }


@dataclass(frozen=True)
class LegacyMigrationResult:
    """迁移结果：目标配置、逐条摘要，以及**没有进入目标配置**的旧键。"""

    config: Dict[str, Any]
    entries: Tuple[LegacyMigrationEntry, ...]
    old_keys: Tuple[str, ...]
    # (旧键, 面向操作员的原因)：这些旧键在目标配置里没有电源条目，只能人工重扫。
    unresolved: Tuple[Tuple[str, str], ...] = ()
    # 迁移后仍为空的分配引用（``dut_amplifier.carrier`` 这类角色键）：电源认不出来时
    # ``supply_id`` 只能写 ``null``。它们让“这份文档还缺什么”可被程序读取，而不是只
    # 靠人看配置猜；写盘门槛也据此拒绝半成品。
    unresolved_references: Tuple[str, ...] = ()

    @property
    def blocked(self) -> bool:
        """存在 ``conflict`` 判定时整次迁移不可用（身份自相矛盾，需人工核对）。"""
        return any(entry.status == STATUS_CONFLICT for entry in self.entries)

    @property
    def pending(self) -> Tuple[LegacyMigrationEntry, ...]:
        """需要人工处理后才能使用、才能落盘的条目。

        包含 ``needs_rescan`` / ``unidentifiable`` / ``conflict``——这三类的通道参数只留在
        摘要里，目标配置不会为它们生成电源条目。
        """
        return tuple(
            entry for entry in self.entries if entry.status in STATUSES_REQUIRING_ACTION
        )

    @property
    def writable(self) -> bool:
        """整次迁移是否已完整：全部条目 ``migrated``，且没有仍为空的分配引用。

        只有这时目标配置才等价于“操作员重扫并重填后”的形态；任何未决项都意味着参数
        只留在摘要里，写进 ``config.json`` 会让它们从配置里彻底消失。
        """
        return not self.pending and not self.unresolved_references

    def summary(
        self,
        *,
        registry_source: Optional[str] = None,
        config_source: Optional[str] = None,
        migrated_config_source: Optional[str] = None,
        generated_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """面向操作员的摘要；``source`` 段与旧配置结构相同，只为追溯用。"""
        return {
            "schema_version": SUMMARY_SCHEMA_VERSION,
            "source": {
                "registry": registry_source,
                "config": config_source,
                "migrated_config": migrated_config_source,
            },
            "generated_at": generated_at or _now_iso(),
            "entries": [entry.to_dict() for entry in self.entries],
        }

    def summary_text(self) -> str:
        """摘要的纯文本形式：写盘前给操作员确认用，不需要日志或界面即可读。"""
        lines = ["电源配置迁移摘要"]
        counts: Dict[str, int] = {}
        for entry in self.entries:
            counts[entry.status] = counts.get(entry.status, 0) + 1
        lines.append(
            "旧键: "
            + ", ".join(self.old_keys)
            + "；判定: "
            + "、".join(f"{status} {counts[status]}" for status in MIGRATION_STATUSES if status in counts)
        )
        for entry in self.entries:
            lines.append(
                f"- {entry.old_key} -> {entry.new_key or '(无)'} [{entry.status}] {entry.reason}"
            )
        if self.unresolved_references:
            lines.append(
                "仍为空的供电分配引用: "
                + "、".join(self.unresolved_references)
                + "（这些参数只在本摘要里，重扫后按上面的旧值重填）"
            )
        return "\n".join(lines)


# --------------------------------------------------------------------------- 摘要字段


def legacy_address_hint(registry: Mapping[str, Any], address: Any) -> Optional[str]:
    """旧地址命中注册表某台设备的 ``known_ips`` / ``current_ip`` 时给出其 ``device_key``。

    地址只是线索：调用方绝不能因为这里有值就自动迁移。
    """
    ip = _address_ip(address)
    if not ip:
        return None
    for record in _registry_records(registry):
        if normalize_text(record.get("current_ip")) == ip:
            return _record_device_key(record)
        for endpoint in _list(record.get("known_ips")):
            if isinstance(endpoint, Mapping) and normalize_text(endpoint.get("ip")) == ip:
                return _record_device_key(record)
    return None


def legacy_assignments(config: Mapping[str, Any], old_key: str) -> List[Dict[str, Any]]:
    """仍引用 ``old_key`` 的供电分配：组（``dut_amplifier`` / ``driver_amplifier``）、
    角色键与旧通道列表。这是“未绑定引用必须在摘要里交代清楚”的唯一实现。"""
    result: List[Dict[str, Any]] = []
    groups = config.get("power_supply_assignment")
    if not isinstance(groups, Mapping):
        return result
    for group_name in _ordered_groups(groups):
        group = groups.get(group_name)
        supplies = group.get("supplies") if isinstance(group, Mapping) else None
        if not isinstance(supplies, Mapping):
            continue
        for role_key, assignment in supplies.items():
            if not isinstance(assignment, Mapping):
                continue
            if assignment_supply_id(assignment) != old_key:
                continue
            result.append(
                {
                    "group": str(group_name),
                    "role_key": str(role_key),
                    "channels": _channel_list(assignment.get("channel")),
                }
            )
    return result


# --------------------------------------------------------------------------- 条目判定


@dataclass(frozen=True)
class _IdentityEvidence:
    """旧条目在设备表里指向哪台设备；``conflict`` 非空表示身份无法确认。

    “找不到设备”与“找到多台/自相矛盾”是两件事：前者还可以退回地址线索（``needs_rescan``），
    后者是数据本身错了，必须由人核对，不能按字段顺序挑一个继续迁移。
    """

    records: Tuple[Mapping[str, Any], ...] = ()
    conflict: Optional[str] = None


def _match_identity(
    registry: Mapping[str, Any], old_key: str, entry: Mapping[str, Any]
) -> _IdentityEvidence:
    """按旧的电源条目找出对应的设备记录（只按身份与旧角色，不看地址）。

    ``registry`` 必须是**原样**设备表：旧槽位 ``PS3`` 记录的 ``role`` 在补标签时会被折叠成
    ``power_supply``，那之后就回答不了“哪个旧键对应哪台设备”了。

    身份字段之间必须互相印证：``device_key`` 与 ``serial`` 指向不同记录、注册表里同一
    ``device_key`` / 同一序列号对应多条记录、或旧条目声明的 ``device_key`` 根本不在设备表里，
    都返回 ``conflict``——这些都是“数据需要人核对”的信号，不是“换一台猜”的理由。
    """
    records = _registry_records(registry)
    serial = normalize_serial(entry.get("serial"))
    device_key = normalize_text(entry.get("device_key")).casefold()
    if not serial and not device_key:
        # 没有自带身份时才退回旧槽位角色：``PS3`` 对应哪台设备由设备表登记的角色回答。
        role = normalize_text(old_key)
        if role in LEGACY_ROLES:
            return _IdentityEvidence(
                tuple(record for record in records if normalize_role(record.get("role")) == role)
            )
        return _IdentityEvidence()

    by_key = tuple(
        record
        for record in records
        if device_key and _record_device_key(record) == device_key
    )
    by_serial = tuple(
        record
        for record in records
        if serial and normalize_serial(record.get("serial")).casefold() == serial.casefold()
    )
    if len(by_key) > 1:
        return _IdentityEvidence(
            conflict=(
                f"设备表里有 {len(by_key)} 条记录共用 device_key {device_key!r}"
                f"（{'、'.join(_record_name(record) for record in by_key)}）；"
                "同一台设备只能登记一次，请先改正设备表"
            )
        )
    if len(by_serial) > 1:
        return _IdentityEvidence(
            conflict=(
                f"设备表里有 {len(by_serial)} 条记录共用序列号 {serial}"
                f"（{'、'.join(_record_name(record) for record in by_serial)}）；"
                "序列号重复时无法确认是哪一台，请先改正设备表"
            )
        )
    if device_key:
        if not by_key:
            if not by_serial:
                return _IdentityEvidence()
            return _IdentityEvidence(
                conflict=(
                    f"旧条目声明的 device_key {device_key!r} 不在设备表里；"
                    "旧配置与设备表不一致，请核对后重试（不能只凭序列号猜成另一台设备）"
                )
            )
        if by_serial and by_serial[0] is not by_key[0]:
            # device_key 与 serial 指向两台不同的设备：这是数据矛盾，不是“取更可信的那个”。
            return _IdentityEvidence(
                conflict=(
                    f"旧条目自相矛盾：device_key 指向 {_record_name(by_key[0])}"
                    f"（序列号 {_record_serial(by_key[0])}），"
                    f"序列号 {serial} 却属于 {_record_name(by_serial[0])}；"
                    "请核对旧配置与设备表后重试"
                )
            )
        return _IdentityEvidence((by_key[0],))
    return _IdentityEvidence((by_serial[0],) if by_serial else ())


def _with_labeled_identity(
    records: Sequence[Mapping[str, Any]], labeled: Optional[Mapping[str, Any]]
) -> List[Mapping[str, Any]]:
    """把原样设备表里命中的记录换成“补过标签”的同一台设备（按 ``device_key`` 对齐）。

    原样设备表保留旧槽位角色，只有它能回答“``PS3`` 对应哪台设备”；补过标签的设备表才有
    ``label``。两者按 ``device_key`` 对齐后，判定与身份来源各取其所长。没有 ``device_key``
    的记录不参与对齐（缺厂商/序列号的记录本身就不该出现在设备表里）。
    """
    if not isinstance(labeled, Mapping):
        return list(records)
    by_key: Dict[str, Mapping[str, Any]] = {}
    for record in _registry_records(labeled):
        key = _record_device_key(record)
        if key is not None:
            by_key[key] = record
    resolved: List[Mapping[str, Any]] = []
    for record in records:
        key = _record_device_key(record)
        resolved.append(by_key.get(key, record) if key is not None else record)
    return resolved


def _classify_entry(
    old_key: str,
    entry: Mapping[str, Any],
    registry: Mapping[str, Any],
    touched: Mapping[str, str],
    config: Mapping[str, Any],
    labeled_registry: Optional[Mapping[str, Any]] = None,
) -> LegacyMigrationEntry:
    address = normalize_text(entry.get("address")) or None
    raw_enabled = entry.get("enabled")
    enabled = raw_enabled if isinstance(raw_enabled, bool) else None
    hint = legacy_address_hint(registry, address)
    channels = copy.deepcopy(entry.get("channels"))
    # 摘要里的分配与通道参数原样取自旧配置：它们是操作员重扫后重填的唯一依据。
    assignments = tuple(legacy_assignments(config, old_key))

    def verdict(
        status: str,
        reason: str,
        *,
        record: Optional[Mapping[str, Any]] = None,
        label: Optional[str] = None,
    ) -> LegacyMigrationEntry:
        identity = record if isinstance(record, Mapping) else {}
        resolved_label = (
            label if label is not None else normalize_text(identity.get("label"))
        ) or None
        # ``conflict`` 条目指名的设备仍写进身份字段（便于操作员核对是哪两台打架），但目标
        # 配置里没有它的条目，所以 ``new_key`` 为空——不能让调用方误以为它已迁移。
        return LegacyMigrationEntry(
            old_key=old_key,
            legacy_address=address,
            legacy_enabled=enabled,
            status=status,
            new_key=resolved_label if status == STATUS_MIGRATED else None,
            device_key=_record_device_key(identity),
            serial=normalize_serial(identity.get("serial")) or None,
            label=resolved_label,
            address_hint=hint,
            reason=reason,
            assignments=assignments,
            channels=channels if isinstance(channels, dict) else {},
            model=normalize_text(identity.get("model")) or None,
        )

    evidence = _match_identity(registry, old_key, entry)
    if evidence.conflict:
        # 身份字段自相矛盾或设备表里身份不唯一：整条判定失败，不猜是哪一台。
        return verdict(STATUS_CONFLICT, evidence.conflict)
    matches = _with_labeled_identity(evidence.records, labeled_registry)
    if len(matches) > 1:
        return verdict(
            STATUS_CONFLICT,
            f"旧键 {old_key} 同时对应 {len(matches)} 台已登记设备（"
            + "、".join(f"{_record_name(record)}/{_record_serial(record)}" for record in matches)
            + "），不能确定是哪一台；请先在设备表里改正角色后重试",
        )
    if not matches:
        if address:
            return verdict(
                STATUS_NEEDS_RESCAN,
                f"旧条目只有地址，没有序列号；{address} "
                + (
                    "不是任何已登记设备的地址"
                    if hint is None
                    else "只是某台已登记设备的历史地址，地址不是身份"
                )
                + "，不能推断是哪一台电源，必须重新扫描确认后再绑定",
            )
        return verdict(
            STATUS_UNIDENTIFIABLE,
            f"旧条目既没有地址也没有序列号，无法识别是哪台设备；"
            f"设备表里没有 role={old_key} 或序列号能对上的已登记电源"
            f"（若设备表已迁移过、旧槽位角色已折叠成 power_supply，旧键就无法再对应到设备），"
            "也不能凭型号猜测它是电源",
        )

    record = matches[0]
    label = normalize_text(record.get("label"))
    serial = normalize_serial(record.get("serial"))
    name = _record_name(record)
    if not serial:
        return verdict(
            STATUS_CONFLICT,
            f"旧键 {old_key} 命中的设备 {name} 没有序列号，无法确认身份；请修正设备表后重试",
        )
    if _identity_source(entry):
        matched_by = "device_key" if normalize_text(entry.get("device_key")) else "序列号"
        reason = f"旧条目自带设备身份，按{matched_by}命中 {name}（序列号 {serial}）；"
        claimed = normalize_serial(entry.get("serial"))
        if claimed and claimed != serial:
            # device_key 唯一命中、序列号却写着别的值：迁移按 device_key 走，但把不一致说出来，
            # 不让操作员以为旧条目里的序列号也被采信了。
            reason += f"注意旧条目写的序列号 {claimed} 与设备表不一致，迁移以 device_key 为准；"
    else:
        reason = (
            f"旧槽位按注册表 role={old_key} 命中唯一设备 {name}（序列号 {serial}）；"
        )
    if not label:
        return verdict(
            STATUS_NEEDS_RESCAN,
            f"旧键 {old_key} 命中的设备 {name}（序列号 {serial}）还没有短标签；"
            "请先保存一次设备表补齐标签，再重新迁移",
        )
    if _SEPARATOR in label or any(char.isspace() for char in label):
        return verdict(
            STATUS_CONFLICT,
            f"旧键 {old_key} 命中的设备 {name} 的短标签 {label!r} 不能当 supply_id 用"
            f"（不得含空白或 {_SEPARATOR!r}），请在设备表里改正后重试",
        )
    reason += "迁移后保持未绑定未启用，需在配置页选中该设备"
    if enabled:
        reason += "；旧条目是启用状态，迁移后不沿用（可用电源只来自设备绑定）"
    owner = touched.get(label) if label else None
    if owner is not None and owner != old_key:
        return verdict(
            STATUS_CONFLICT,
            f"旧键 {old_key} 与 {owner} 落到同一台设备 {name}（序列号 {serial}）上；"
            "两台设备才能各占一条电源配置，不能自动合并，请人工处理",
            record=record,
            label=label,
        )
    if old_key == label:
        # 旧键本身就是 ``supply_id``：这是已经迁移过的条目（迁移可重复执行）。
        return verdict(
            STATUS_MIGRATED,
            f"条目已按 supply_id 写在新格式下，命中 {name}（序列号 {serial}）；原样保留",
            record=record,
            label=label,
        )
    return verdict(STATUS_MIGRATED, reason, record=record, label=label)


# --------------------------------------------------------------------------- 入口


def migrate_legacy_power_supplies(
    config: Mapping[str, Any],
    registry: Mapping[str, Any],
    *,
    migrate_registry_labels: bool = True,
) -> LegacyMigrationResult:
    """把旧配置里的电源集合、分配引用、设备绑定与运行映射改写成 ``supply_id`` 形式。

    ``config`` 是当前配置文档；``registry`` 是设备表文档。两者都**只读**，返回值是新对象。

    ``registry`` 的两种写法都接受：

    - 已迁移的设备表（``schema_version: 3``，电源带 ``label``）；
    - 现场旧设备表（``schema_version: 2``，``role: "PS3"``、没有 ``label``）。此时
      ``migrate_registry_labels=True`` 会先在**内存副本**上按步骤 4 的规则补齐短标签
      （旧槽位折叠成 ``power_supply``，按序列号生成标签），迁移用的就是这份补全后的设备表。
      调用方要落盘时用 :func:`persist_registry_labels`：配置迁移不会悄悄改写设备表。
      ``migrate_registry_labels=False`` 时旧设备表被原样使用——没有标签的设备不可能成为
      ``supply_id``，判定为 ``needs_rescan``（先保存一次设备表），绝不在这里另生成一套编号。

    目标配置的写法：

    - ``instruments.power_supplies`` 的键改为 ``supply_id`` 并补上 ``label`` / ``device_key`` /
      ``serial`` / ``model``；通道参数与未知字段原样保留；旧 ``enabled`` 不沿用——可用性只
      来自设备绑定：有带身份的绑定记录时地址与 ``enabled: true`` 一起写回（与配置页
      ``_apply_device_binding`` 同一条规则），否则写 ``enabled: false`` 且不留地址；旧条目的
      ``address`` 只在这两种情况下写进配置，其余情况删除并把旧值留在摘要 ``legacy_address``
      里，不给配置写一个没人确认过的连接目标；
    - ``power_supply_assignment.*.supplies.*`` 的引用写成 ``supply_id``（能迁移时）或
      ``supply_id: null``（不能迁移时），过渡别名 ``name`` 只在能迁移时同值写出；
    - ``device_bindings`` 只保留单设备角色，旧槽位键移除；能迁移的电源若旧绑定本身带身份
      （``source != legacy_config``），折进 ``power_supplies.<supply_id>``，只有地址的旧绑定
      一律丢弃并交由重新扫描；
    - ``run_mapping.instruments.power_supply``（单数）改写成 ``instruments.power_supplies``，
      快照只含被供电分配实际引用且身份完整的电源；``dut_power_channels`` 与
      ``driver_mode.power_channels`` 按迁移后的分配重建成 ``supply_id/CHx`` 路由，旧槽位
      通道名不保留；
    - ``power_supply_assignment.<组>.power_supply_count`` 按该组仍能落地的引用数重算；
      ``dut_config.power_supply_count`` 是测试方案里的现场参数，**不重算**（迁移不改测试方案）。

    返回值里的 :attr:`LegacyMigrationResult.writable` 说明这次迁移是否已完整；只有它为真时
    才允许用 :func:`save_migrated_configuration` 写盘。
    """
    if not isinstance(config, Mapping):
        raise LegacyConfigMigrationError("待迁移的配置必须是对象")
    if not isinstance(registry, Mapping):
        raise LegacyConfigMigrationError("设备表必须是对象")
    instruments = config.get("instruments")
    legacy_supplies = instruments.get("power_supplies") if isinstance(instruments, Mapping) else None
    if legacy_supplies is not None and not isinstance(legacy_supplies, Mapping):
        raise LegacyConfigMigrationError("instruments.power_supplies 必须是对象")

    identity_registry = (
        _registry_with_labels(registry) if migrate_registry_labels else registry
    )
    target = copy.deepcopy(dict(config))
    old_keys = tuple(str(key) for key in (legacy_supplies or {}))
    # 已经被占用的新键：旧槽位互相之间，以及配置里本来就有的（已迁移过的）``supply_id``。
    # 后者让“迁移可重复执行”：再跑一次时，已迁移的条目不会因为“键已存在”被误判成冲突。
    touched: Dict[str, str] = {
        str(key): str(key)
        for key in (legacy_supplies or {})
        if str(key) not in LEGACY_ROLES
    }
    entries: List[LegacyMigrationEntry] = []
    for old_key in old_keys:
        raw_entry = legacy_supplies[old_key]
        entry = _classify_entry(
            old_key,
            raw_entry if isinstance(raw_entry, Mapping) else {},
            registry,
            touched,
            config,
            identity_registry,
        )
        entries.append(entry)
        if entry.status == STATUS_MIGRATED and entry.new_key:
            touched[entry.new_key] = old_key

    migrated_by_old = {
        entry.old_key: entry for entry in entries if entry.status == STATUS_MIGRATED
    }
    # 旧键已经是 ``supply_id`` 的条目按原样保留：它们本来就是新格式，再重写一遍只会把
    # 通道参数重新拼一次、把地址再删一次。迁移因此可以对同一份配置重复执行。
    rewritten = {
        entry.old_key: entry
        for entry in migrated_by_old.values()
        if entry.new_key != entry.old_key
    }
    target_supplies = _migrated_supply_entries(legacy_supplies or {}, rewritten)
    _write_instrument_supplies(target, target_supplies)
    _rewrite_assignments(target, migrated_by_old)
    bindings = _rewrite_device_bindings(target, config, migrated_by_old)
    _recount_assignments(target)
    _apply_trusted_bindings(target_supplies, bindings, migrated_by_old)
    _rewrite_run_mapping(target)

    unresolved = tuple(
        (entry.old_key, entry.reason)
        for entry in entries
        if entry.status in STATUSES_REQUIRING_ACTION
    )
    return LegacyMigrationResult(
        config=target,
        entries=tuple(entries),
        old_keys=old_keys,
        unresolved=unresolved,
        unresolved_references=_unresolved_references(target),
    )


def save_migrated_configuration(
    path: PathLike,
    result: LegacyMigrationResult,
    *,
    validator: Optional[Any] = None,
) -> Path:
    """把**完整**的迁移结果写回配置路径；任何未决项或校验失败都抛错且原文件逐字节不变。

    写盘门槛（缺一不可）：

    1. 每一条旧条目都判定为 ``migrated``（:attr:`LegacyMigrationResult.writable`）；
       ``needs_rescan`` / ``unidentifiable`` / ``conflict`` 一律拒绝——这类条目的通道参数
       只存在于摘要里，写进 ``config.json`` 就等于把它们删掉，而“原配置还能继续用”也会
       一起失去。要留存这些参数，用 :func:`save_migration_summary` 保存摘要，重扫补全后
       再迁移一次。
    2. 迁移后没有仍为空的供电分配引用（``supply_id: null``）。
    3. 文件级校验通过：``validator`` 缺省使用
       :func:`infrastructure.config.validation.validate_official_config`，**不能跳过**；
       校验只拒绝写入，不会先写再校验。

    返回写入路径（原子替换）。
    """
    if not isinstance(result, LegacyMigrationResult):
        raise LegacyConfigMigrationError("写盘只接受 LegacyMigrationResult")
    pending = result.pending
    if pending:
        raise LegacyConfigMigrationError(
            "迁移还有未决条目，未写入任何文件（原配置保持不变）: "
            + "、".join(f"{entry.old_key}[{entry.status}]" for entry in pending)
            + "；这些条目的通道参数只在摘要里，请先用 save_migration_summary 保存摘要，"
            "重新扫描补全后再迁移"
        )
    if result.unresolved_references:
        raise LegacyConfigMigrationError(
            "迁移后仍有未落地的供电分配引用，未写入任何文件（原配置保持不变）: "
            + "、".join(result.unresolved_references)
        )
    check = validator if validator is not None else validate_official_config
    validation = check(result.config)
    if not getattr(validation, "valid", False):
        errors = [
            f"{issue.path}: {issue.message}"
            for issue in getattr(validation, "errors", ())
        ]
        raise LegacyConfigMigrationError(
            "迁移结果未通过文件级校验，未写入任何文件（原配置保持不变）: "
            + "; ".join(errors)
        )
    return write_json_atomic(Path(path), result.config)


def save_migration_summary(
    path: PathLike,
    result: LegacyMigrationResult,
    *,
    registry_source: Optional[str] = None,
    config_source: Optional[str] = None,
    migrated_config_source: Optional[str] = None,
    generated_at: Optional[str] = None,
) -> Path:
    """把迁移摘要写成独立的 JSON 文件（原子写入），不碰配置与设备表。

    摘要是“认不出的旧条目”唯一的参数留存处：既然这些参数不允许写进 ``config.json``，
    操作员的补全依据就必须能离开进程、跨重启还在。文件内容与
    :meth:`LegacyMigrationResult.summary` 逐字段相同。
    """
    summary = result.summary(
        registry_source=registry_source,
        config_source=config_source,
        migrated_config_source=migrated_config_source,
        generated_at=generated_at,
    )
    return write_json_atomic(Path(path), summary)


def default_summary_path(config_path: PathLike) -> Path:
    """摘要的默认落点：与配置同目录的 ``<配置名>.migration-summary.json``。

    单独一个文件，不覆盖配置、也不进入“正式配置”的读取路径；操作员按摘要补全后可以删除，
    下次迁移会重新生成。
    """
    path = Path(config_path)
    return path.with_name(f"{path.name}.migration-summary.json")


def load_and_migrate(
    config_path: PathLike, registry_path: PathLike
) -> LegacyMigrationResult:
    """读取配置文件与设备表并迁移；两者都**不会**被写回。"""
    config = load_json_object(config_path)
    registry = load_json_object(registry_path)
    return migrate_legacy_power_supplies(config, registry)


def persist_registry_labels(registry_path: PathLike) -> Path:
    """把现场旧设备表迁移成新格式并原子写入（步骤 4 的注册表迁移）。

    迁移器的输入是“设备表已经补过短标签”的状态；这一步由操作员显式执行一次，而不是让
    配置迁移顺手改写设备表。内容不合法（标签冲突、序列号派不出标签等）时
    :class:`InstrumentRegistryError` 直接抛出，原文件不变。
    """
    registry = InstrumentRegistry.load(registry_path)
    return registry.save()


# --------------------------------------------------------------------------- 注册表身份来源


def _registry_with_labels(registry: Mapping[str, Any]) -> Mapping[str, Any]:
    """在内存副本上按步骤 4 的规则补齐设备表短标签；设备表结构不合法时抛错。

    直接走注册表的保存路径（:meth:`InstrumentRegistry.save`）取得“保存后设备表会是什么
    样子”，再丢弃写盘结果：迁移用的标签与注册表落盘后的标签因此是同一套规则、同一批值。
    这里**不写盘**——要落盘时调用方用 :func:`persist_registry_labels`。
    """
    scratch = Path(tempfile.mkdtemp(prefix="power-supply-registry-")) / DEFAULT_REGISTRY_FILENAME
    try:
        saved = InstrumentRegistry(path=scratch, document=dict(registry)).save()
        return read_json(saved)
    except InstrumentRegistryError as error:
        raise LegacyConfigMigrationError(
            f"设备表无法补齐电源短标签，迁移未开始: {error}"
        ) from error
    finally:
        shutil.rmtree(scratch.parent, ignore_errors=True)


# --------------------------------------------------------------------------- 目标配置改写


def _migrated_supply_entries(
    legacy_supplies: Mapping[str, Any],
    migrated_by_old: Mapping[str, LegacyMigrationEntry],
) -> Dict[str, Any]:
    """新 ``power_supplies`` 集合：键是 ``supply_id``，身份取自命中的注册表记录。

    旧条目自己的 ``label`` / ``device_key`` / ``serial`` / ``model`` 一律丢弃：身份只有一个
    来源（设备表）。旧 ``address`` 先删除，等设备绑定确认过再补回
    （:func:`_apply_trusted_bindings`）。**旧键已经等于新键的条目原样保留**：它们本来就是
    新格式，重写只会把参数与地址再动一次，也会让迁移无法重复执行。
    """
    entries: Dict[str, Any] = {
        str(key): copy.deepcopy(value)
        for key, value in legacy_supplies.items()
        if str(key) not in LEGACY_ROLES
    }
    for old_key, entry in migrated_by_old.items():
        if entry.new_key is None or entry.new_key == old_key:
            continue
        raw = legacy_supplies.get(old_key)
        migrated = copy.deepcopy(dict(raw)) if isinstance(raw, Mapping) else {}
        for name in _IDENTITY_FIELDS:
            migrated.pop(name, None)
        migrated.pop("address", None)
        migrated.pop("legacy_address", None)
        # 可用电源只来自设备绑定：迁移不沿用旧的“启用”标志。
        migrated["label"] = entry.label
        migrated["device_key"] = entry.device_key
        migrated["serial"] = entry.serial
        migrated["model"] = entry.model
        migrated["enabled"] = False
        entries[entry.new_key] = migrated
    return entries


def _apply_trusted_bindings(
    supplies: Dict[str, Any],
    bindings: Mapping[str, Any],
    migrated_by_old: Mapping[str, LegacyMigrationEntry],
) -> None:
    """把“设备绑定确认过地址”的电源连接到配置里：地址与启用状态一起写。

    唯一的连接来源是该电源的绑定记录（``visa_address``）。这与配置页的
    ``_apply_device_binding`` 是同一条规则：有地址就启用、没地址就禁用。没有绑定的旧条目
    （只有地址）不获得地址也不启用——地址不是身份，没确认过就不该成为连接目标，这类电源
    等操作员重新扫描应用。

    已经迁移过的条目（``old_key == new_key``）同样按绑定校正一次：配置里写着启用、绑定却
    早已被清除时，迁移不能把那个状态当成事实保留下来。
    """
    for old_key, entry in migrated_by_old.items():
        if entry.new_key is None:
            continue
        binding = bindings.get(entry.new_key)
        address = normalize_text(binding.get("visa_address")) if isinstance(binding, Mapping) else ""
        if not address:
            continue
        supply = supplies.get(entry.new_key)
        if isinstance(supply, dict):
            supply["address"] = address
            supply["enabled"] = True


def _write_instrument_supplies(target: Dict[str, Any], supplies: Dict[str, Any]) -> None:
    instruments = target.get("instruments")
    if not isinstance(instruments, dict):
        instruments = {}
        target["instruments"] = instruments
    if supplies or "power_supplies" in instruments:
        instruments["power_supplies"] = supplies


def _rewrite_assignments(
    target: Dict[str, Any], migrated_by_old: Mapping[str, LegacyMigrationEntry]
) -> None:
    """把分配引用改写到新键；不能迁移的引用写成 ``null`` 并保持空选择。"""
    groups = target.get("power_supply_assignment")
    if not isinstance(groups, dict):
        return
    for group in groups.values():
        supplies = group.get("supplies") if isinstance(group, dict) else None
        if not isinstance(supplies, dict):
            continue
        for assignment in supplies.values():
            if not isinstance(assignment, dict):
                continue
            old_reference = assignment_supply_id(assignment)
            migrated = migrated_by_old.get(old_reference) if old_reference else None
            new_reference: Optional[str] = None
            if migrated is not None:
                new_reference = migrated.new_key
            elif old_reference is not None and _looks_like_new_reference(old_reference):
                # 已经是新键的引用原样保留：迁移对已迁移的配置可重复执行。
                new_reference = old_reference
            _write_assignment_reference(assignment, new_reference)


def _write_assignment_reference(assignment: Dict[str, Any], supply_id: Optional[str]) -> None:
    """写出引用：``supply_id`` 为准，过渡别名 ``name`` 只在能迁移时同值保留。

    不能迁移的引用写 ``supply_id: null`` 并**删除** ``name``：留着旧槽位名会让旧读取方
    以为它仍指向一台电源，而新代码只认 ``supply_id``。
    """
    if supply_id is None:
        assignment["supply_id"] = None
        assignment.pop("name", None)
        return
    had_alias = "supply_id" not in assignment and "name" in assignment
    assignment["supply_id"] = supply_id
    if had_alias or "name" in assignment:
        assignment["name"] = supply_id


def _rewrite_device_bindings(
    target: Dict[str, Any],
    source: Mapping[str, Any],
    migrated_by_old: Mapping[str, LegacyMigrationEntry],
) -> Dict[str, Any]:
    """旧槽位绑定不迁移身份：只有本身带身份的过渡记录才折进 ``power_supplies``。

    返回新的电源绑定集合（``supply_id -> 记录``），供地址确认使用。
    """
    bindings = target.get("device_bindings")
    if not isinstance(bindings, dict):
        return {}
    source_bindings = source.get("device_bindings")
    source_bindings = source_bindings if isinstance(source_bindings, Mapping) else {}
    power_bindings: Dict[str, Any] = {}
    kept: Dict[str, Any] = {}
    keep = _mapping(source.get("device_bindings")).get("power_supplies")
    current = _mapping(bindings.get("power_supplies"))
    for supply_id, record in current.items():
        # 配置里已有的（已迁移过的）电源绑定原样保留：迁移只填空缺，不改写已确认的地址。
        if isinstance(keep, Mapping) and supply_id in keep:
            power_bindings[str(supply_id)] = copy.deepcopy(record)
    for key, record in bindings.items():
        if key in LEGACY_ROLES:
            migrated = migrated_by_old.get(str(key))
            source_record = source_bindings.get(key)
            if migrated is None or migrated.new_key is None:
                continue
            if not _binding_has_identity(source_record):
                # 只有地址的旧绑定（source=legacy_config）不是身份，必须重新扫描。
                continue
            prepared = copy.deepcopy(dict(source_record))
            prepared["label"] = migrated.label
            prepared["supply_id"] = migrated.new_key
            power_bindings.setdefault(migrated.new_key, prepared)
            continue
        kept[key] = record
    # 电源绑定是一个集合：旧槽位键移除后仍保留这个键，文件自身就能说明“还没有绑定”。
    kept["power_supplies"] = power_bindings
    target["device_bindings"] = kept
    return power_bindings


def _rewrite_run_mapping(target: Dict[str, Any]) -> None:
    """旧单数 ``power_supply`` 段改写成集合；快照只含实际使用且身份完整的电源。

    通道行不沿用旧写法：旧快照的 ``CH1`` 与 ``PS1-CH1 -> DUT 栅极`` 没有物理路由，一旦
    电源集合出现（哪怕是空集合），``validate_run_mapping`` 就要求每条通道写成
    ``supply_id/CHx`` 且前缀指向快照里的电源。因此这里按**迁移后的分配**重新生成通道行：
    能落地的引用按 ``<supply_id>/<通道>`` 与电源自己声明的通道角色写，落地不了的一律不写
    （参数仍留在迁移摘要里，由操作员重扫后重填）。
    """
    mapping = target.get("run_mapping")
    if not isinstance(mapping, Mapping):
        return
    instruments = mapping.get("instruments")
    if not isinstance(instruments, dict):
        return
    instruments.pop("power_supply", None)
    entries: Dict[str, Any] = {}
    for supply_id in _used_supply_ids(target):
        entry = _run_mapping_supply_entry(target, supply_id)
        if entry is not None:
            entries[supply_id] = entry
    instruments["power_supplies"] = entries
    mapping["dut_power_channels"] = _channel_snapshot(target, entries, "dut_amplifier")
    driver = mapping.get("driver_mode")
    if isinstance(driver, dict):
        driver["power_channels"] = _channel_snapshot(target, entries, "driver_amplifier")


def _channel_snapshot(
    target: Mapping[str, Any],
    entries: Mapping[str, Any],
    group: str,
) -> List[Dict[str, Any]]:
    """按某个分配组生成快照通道行：``channel`` 是 ``supply_id/CHx``，``role`` 取电源声明。

    只写快照里有条目的电源（``entries``）：引用没落地、或电源身份/地址不完整的，其通道
    不进快照——半条路由比没有路由更危险，校验会发现快照不完整并拒绝创建运行。
    """
    rows: List[Dict[str, Any]] = []
    assignments = _mapping(_mapping(target.get("power_supply_assignment")).get(group))
    supplies = _mapping(_mapping(target.get("instruments")).get("power_supplies"))
    for assignment in _mapping(assignments.get("supplies")).values():
        if not isinstance(assignment, Mapping):
            continue
        supply_id = assignment_supply_id(assignment)
        if supply_id is None or supply_id not in entries:
            continue
        declared = _mapping(_mapping(supplies.get(supply_id)).get("channels"))
        for channel in _channel_list(assignment.get("channel")):
            token = f"{supply_id}{_SEPARATOR}{channel}"
            role = normalize_text(_mapping(declared.get(channel)).get("role")) or None
            rows.append(
                {"channel": token, "role": role, "connection": _connection_text(token, role)}
            )
    return rows


def _connection_text(token: str, role: Optional[str]) -> str:
    """连接说明与配置作者写的一致：``<token> -> DUT 栅极/漏极``、``<token> -> 驱动功放``。"""
    if role == _DUT_GATE:
        return f"{token} -> DUT 栅极"
    if role == _DUT_DRAIN:
        return f"{token} -> DUT 漏极"
    if role == _DRIVER_ROLE:
        return f"{token} -> 驱动功放"
    return token


def _resolvable_count(supplies: Mapping[str, Any]) -> int:
    return sum(
        1 for assignment in supplies.values() if assignment_supply_id(assignment) is not None
    )


def _unresolved_references(document: Mapping[str, Any]) -> Tuple[str, ...]:
    """迁移后仍为空的分配引用（``dut_amplifier.carrier`` 这样的 ``组.角色键``）。

    它是“这份文档还缺什么”的机器可读形式：写盘门槛用它拒绝半成品，摘要文本用它提醒操作员
    哪些参数只留在摘要里。
    """
    pending: List[str] = []
    groups = _mapping(document.get("power_supply_assignment"))
    for group_name in _ordered_groups(groups):
        supplies = _mapping(_mapping(groups.get(group_name)).get("supplies"))
        for role_key, assignment in supplies.items():
            if not isinstance(assignment, Mapping):
                continue
            if assignment_supply_id(assignment) is None:
                pending.append(f"{group_name}.{role_key}")
    return tuple(pending)


def _used_supply_ids(document: Mapping[str, Any]) -> List[str]:
    """按出现顺序返回供电分配实际引用的 ``supply_id``（去重）。"""
    referenced: List[str] = []
    groups = _mapping(document.get("power_supply_assignment"))
    for group_name in _ordered_groups(groups):
        supplies = _mapping(_mapping(groups.get(group_name)).get("supplies"))
        for assignment in supplies.values():
            supply_id = assignment_supply_id(assignment)
            if supply_id is not None and supply_id not in referenced:
                referenced.append(supply_id)
    return referenced


def _run_mapping_supply_entry(
    target: Mapping[str, Any],
    supply_id: str,
) -> Optional[Dict[str, Any]]:
    """快照里的电源条目：身份与地址都取自迁移后的配置条目。

    地址只可能是设备绑定确认过的那一个（见 :func:`_apply_trusted_bindings`）；没有确认过
    地址的电源写成空地址，交给正式校验，绝不回扫设备表去替它挑一个。
    """
    supplies = _mapping(_mapping(target.get("instruments")).get("power_supplies"))
    supply = supplies.get(supply_id)
    if not isinstance(supply, Mapping):
        return None
    label = normalize_text(supply.get("label"))
    device_key = normalize_text(supply.get("device_key"))
    if not label or not device_key:
        return None
    return {
        "label": label,
        "model": supply.get("model"),
        "serial": supply.get("serial"),
        "device_key": device_key,
        "visa_address": normalize_text(supply.get("address")) or None,
    }


def _recount_assignments(target: Dict[str, Any]) -> None:
    """``power_supply_count`` 按“仍能落地的引用数”重算，不让旧计数留在新配置里。

    只重算供电分配组自己的计数：它是“这一组实际配了几台电源”，引用置空后继续写着旧数字会
    让 ``validate_config`` 报一条与事实无关的差异警告。``dut_config.power_supply_count`` 属于
    测试方案参数，迁移不改测试方案。
    """
    groups = target.get("power_supply_assignment")
    if not isinstance(groups, dict):
        return
    for group in groups.values():
        supplies = group.get("supplies") if isinstance(group, dict) else None
        if not isinstance(supplies, dict):
            continue
        if "power_supply_count" in group:
            group["power_supply_count"] = _resolvable_count(supplies)


# --------------------------------------------------------------------------- 小工具


def _registry_records(registry: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    records = registry.get("instruments")
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        return []
    return [record for record in records if isinstance(record, Mapping)]


def _record_device_key(record: Mapping[str, Any]) -> Optional[str]:
    declared = normalize_text(record.get("device_key")).casefold()
    if declared:
        return declared
    vendor = normalize_text(record.get("vendor")).casefold()
    serial = normalize_serial(record.get("serial")).casefold()
    if vendor and serial:
        return f"{vendor}:{serial}"
    return None


def _record_name(record: Mapping[str, Any]) -> str:
    return normalize_text(record.get("name")) or "(未命名设备)"


def _record_serial(record: Mapping[str, Any]) -> str:
    return normalize_serial(record.get("serial")) or "(无序列号)"


def _identity_source(entry: Mapping[str, Any]) -> bool:
    """旧条目自带身份（``device_key`` 或 ``serial``），走第一条判定。"""
    return bool(normalize_text(entry.get("device_key")) or normalize_serial(entry.get("serial")))


def _binding_has_identity(record: Any) -> bool:
    if not isinstance(record, Mapping):
        return False
    if normalize_text(record.get("source")).casefold() == "legacy_config":
        return False
    return bool(normalize_text(record.get("device_key")) and normalize_text(record.get("serial")))


def _looks_like_new_reference(value: str) -> bool:
    """引用不是旧槽位名就当作已经是新键（迁移可重复执行，已迁移的配置原样通过）。"""
    return bool(normalize_text(value)) and normalize_text(value) not in LEGACY_ROLES


def _address_ip(address: Any) -> Optional[str]:
    """从 VISA 资源名里取 IP；认不出时返回 ``None``。"""
    text = normalize_text(address)
    if not text:
        return None
    if _IPV4.match(text):
        return text
    parts = text.split("::")
    if len(parts) >= 2 and _IPV4.match(parts[1]):
        return parts[1]
    candidate = unquote(parts[-1]) if parts else text
    return candidate if _IPV4.match(candidate) else None


def _ordered_groups(groups: Mapping[str, Any]) -> List[str]:
    ordered = [name for name in ASSIGNMENT_GROUPS if name in groups]
    ordered.extend(name for name in groups if name not in ASSIGNMENT_GROUPS)
    return [str(name) for name in ordered]


def _channel_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> Iterable[Any]:
    return value if isinstance(value, list) else ()


def _now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


__all__ = [
    "LegacyConfigMigrationError",
    "LegacyMigrationEntry",
    "LegacyMigrationResult",
    "MIGRATION_STATUSES",
    "STATUS_CONFLICT",
    "STATUS_MIGRATED",
    "STATUS_NEEDS_RESCAN",
    "STATUS_UNIDENTIFIABLE",
    "STATUSES_REQUIRING_ACTION",
    "SUMMARY_SCHEMA_VERSION",
    "default_summary_path",
    "legacy_address_hint",
    "legacy_assignments",
    "load_and_migrate",
    "migrate_legacy_power_supplies",
    "persist_registry_labels",
    "save_migrated_configuration",
    "save_migration_summary",
]
