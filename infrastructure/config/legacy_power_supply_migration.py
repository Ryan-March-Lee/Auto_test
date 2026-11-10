"""旧 ``PS1..PS4`` 配置到 1.1 电源计划的字段级迁移器（电源重构计划步骤 14）。

迁移只回答一个问题：**旧配置里的每个电源条目和每条供电分配，能不能用已登记的身份
改写成新字段**。它不扫描设备，也不猜身份：

- 身份判据只有 ``device_key``（``厂商:完整设备码``）与注册表里冻结的缩写设备码 ``label``；
- 旧地址 ``TCPIP0::…`` 只作重新扫描的线索（``address_hint``），**不参与**身份判定；
- 认不出是哪一台的旧条目不会凭空得到身份，它的通道参数与仍引用它的分配写进迁移摘要，
  由操作员按摘要重新扫描后重填（摘要把每个旧条目的判定、身份与参数逐行写清）。

判定顺序::

    1. 旧条目自带 ``device_key`` / 完整设备码，注册表唯一命中      -> migrated
    2. 旧键是 ``PSn``，注册表正好有一条 ``role == PSn``          -> migrated
    3. 旧键是 ``PSn``，该角色对应多台 / 命中的设备已有新键       -> conflict
    4. 身份字段互相矛盾、注册表里身份不唯一、``device_key`` 不在册 -> conflict
    5. 旧条目只有 ``address``（地址命中注册表也只是线索）        -> needs_rescan
    6. 旧条目既无 ``address`` 也无完整设备码                      -> unidentifiable
    7. 两个旧条目落在同一 ``device_key``                         -> conflict（阻止整次迁移）

**身份字段之间必须互相印证**：旧条目同时写 ``device_key`` 与完整设备码时，两者必须指向
设备表里的同一条记录；指向不同设备、或注册表里同一 ``device_key`` / 完整设备码对应多条
记录，都按“无法确认是哪一台”报 ``conflict``，绝不按字段顺序挑一个继续迁移。

步骤 14 起，迁移**不再按旧角色生成上电顺序或漏极路由**：

- 每组的 ``power_supply_assignment.<group>.power_plan`` 写成空计划（空序列 + ``0.0``），
  摘要列出候选物理路由，由用户在电源页确认；
- 旧 ``role == "drain"`` 只有在 DUT 分配通道里恰好一条、且能唯一映射到物理路由时才写入
  ``dut_config.drain_channel``；0 条或多条一律写 ``null`` 并记入摘要待确认项；
- 旧 ``test_plan.dut.power_roles`` 与旧 ``run_mapping.dut_power_channels`` 只在 role 集合
  完全一致、各自唯一、token 能解析到迁移后的电源/通道时才用于重建 ``channel_settings``；
  否则该组参数不迁移，摘要标记 ``configuration.power_settings_unmapped``；
- 通道 ``role``、``power_roles``、``power_channels`` / ``connection`` 不再出现在输出里。

迁移的输入与输出都是内存对象：:func:`migrate_legacy_power_supplies` 返回目标配置与逐条摘要，
不改入参、不写盘。**只有每一条旧条目都 ``migrated``、且没有仍为空的供电分配引用时，结果才
允许落盘**（:attr:`LegacyMigrationResult.writable`）：:func:`save_migrated_configuration` 在
写盘前先跑文件级校验，任何未决项或校验失败都抛错且原文件逐字节不变。认不出的旧条目的通道
参数只存在于摘要里，因此摘要必须能离开进程保存——用 :func:`save_migration_summary` 把它写成
独立的摘要文件，而不是靠“先把半成品写进 config.json”来留存。

写盘内容是**由 authoring 重新生成的 1.1 文档**：迁移器只改编辑段（身份、引用、通道参数、
空计划和漏极），正式 ``test_plan`` / ``run_mapping`` 一律交给
:class:`application.configuration_authoring.ConfigurationAuthoringService`，不手写。
``runs/`` 下的历史快照不迁移、不改写，仍按 1.0 只读路径查看。

现场样例（``tests/fixtures/power_supply_identity/``）：``config_slots_legacy.json`` +
``registry_legacy_v2.json`` -> ``config_slots_migrated_partial.json`` +
``migration_summary_legacy.json``。
"""

from __future__ import annotations

import copy
import math
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import unquote

from application.configuration_authoring import (
    ConfigurationAuthoringService,
    ConfigurationEditingDocument,
    OFFICIAL_SCHEMA_VERSION,
)
from domain.configuration.models import (
    REWRITABLE_SCHEMA_VERSIONS,
    SUPPORTED_SCHEMA_VERSION,
)
from domain.configuration.rules import (
    ASSIGNMENT_GROUPS,
    assignment_supply_id,
)
from domain.instruments.models import (
    LEGACY_ROLES,
    normalize_full_code,
    normalize_role,
    normalize_text,
)
from infrastructure.config.json_config_repository import JsonConfigurationRepository
from infrastructure.config.json_io import load_json_object
from infrastructure.config.validation import validate_saveable_configuration
from infrastructure.filesystem.atomic_json import read_json, write_json_atomic
from infrastructure.persistence.instrument_registry import (
    DEFAULT_REGISTRY_FILENAME,
    InstrumentRegistry,
    InstrumentRegistryError,
)

PathLike = Any

# 迁移摘要的版本号；摘要结构变化时递增。
# 3：步骤 14 增加每组空计划/候选路由、漏极迁移状态与通道参数映射状态。
SUMMARY_SCHEMA_VERSION = 3

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

# 通道参数映射状态（步骤 14 §6.14 规则 2）：只有旧角色两侧唯一配对成功才搬运参数。
CHANNEL_SETTINGS_MAPPED = "mapped"
CHANNEL_SETTINGS_UNMAPPED = "unmapped"
CHANNEL_SETTINGS_NOT_APPLICABLE = "not_applicable"
# 漏极迁移状态（步骤 14 §6.14 规则 3）：不按通道名猜，0 条或多条一律待确认。
DRAIN_MIGRATED = "migrated"
DRAIN_PRESERVED = "preserved"
DRAIN_NEEDS_CONFIRMATION = "needs_confirmation"
DRAIN_NOT_APPLICABLE = "not_applicable"
# 旧角色通道参数无法唯一配对时摘要使用的稳定错误码（§6.14 规则 2）。
CODE_POWER_SETTINGS_UNMAPPED = "configuration.power_settings_unmapped"

# 集合键与条目内部重复的身份字段：新格式只从注册表取身份，旧条目里的同名键不沿用。
_IDENTITY_FIELDS = ("label", "device_key", "full_code", "serial", "model")
_SEPARATOR = "/"
_IPV4 = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
# 旧供电角色只用于读取兼容与参数配对，**不生成上电顺序**。
_LEGACY_DUT_DRAIN = "drain"
_LEGACY_DRIVER = "driver"
_EMPTY_POWER_PLAN = {"power_sequence": [], "inter_channel_delay_s": 0.0}


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
    full_code: Optional[str]
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
            "full_code": self.full_code,
            "label": self.label,
            "address_hint": self.address_hint,
            "reason": self.reason,
            "assignments": [dict(item) for item in self.assignments],
            "channels": copy.deepcopy(self.channels),
        }


@dataclass(frozen=True)
class LegacyChannelMapping:
    """一组旧角色化通道参数能否唯一映射到迁移后的物理路由。

    ``routes`` 是成功解析出的物理路由；``settings`` 只用于在迁移内部写回编辑段的通道
    参数，不进入摘要（摘要只回答“能不能映射、为什么不能”）。
    """

    group: str
    status: str
    reason: str
    routes: Tuple[Tuple[str, str], ...] = ()
    settings: Tuple[Tuple[Tuple[str, str], Dict[str, Any]], ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "routes": [_route_document(route) for route in self.routes],
        }


@dataclass(frozen=True)
class LegacyDrainMigration:
    """旧 ``role == "drain"`` 声明的迁移结果（步骤 14 §6.14 规则 3）。"""

    status: str
    reason: str
    route: Optional[Tuple[str, str]] = None
    candidates: Tuple[Tuple[str, str], ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "route": _route_document(self.route) if self.route is not None else None,
            "candidates": [_route_document(route) for route in self.candidates],
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
    # 已迁移干净的 1.1 文档不再保存旧 ``supplies``/未决引用，单看文档无法区分“完整迁移
    # 但计划尚未确认”和“迁移缺少来源、需要人工补全”。重新迁移这类文档时，计划为空即
    # 不能断言迁移完整；路径让写盘门槛继续要求操作员在电源页确认顺序。
    unconfirmed_plans: Tuple[str, ...] = ()
    # 步骤 14 新增：每组空计划的候选路由、旧角色参数映射状态、漏极迁移状态。
    # 这些只用于摘要，不进入配置 schema（§6.7：确认状态不落库）。
    plan_candidates: Tuple[Tuple[str, Tuple[Tuple[str, str], ...]], ...] = ()
    channel_mappings: Tuple[LegacyChannelMapping, ...] = ()
    drain_migration: Optional[LegacyDrainMigration] = None

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
        return (
            not self.pending
            and not self.unresolved_references
            and not self.unconfirmed_plans
        )

    def summary(
        self,
        *,
        registry_source: Optional[str] = None,
        config_source: Optional[str] = None,
        migrated_config_source: Optional[str] = None,
        generated_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """面向操作员的摘要；``source`` 段与旧配置结构相同，只为追溯用。

        ``groups`` 不保存配置里的确认状态（§6.7：状态派生、不落库），只记录这次迁移
        写出的空计划和候选路由；``drain_channel`` / ``channel_settings`` 记录“自动迁移
        能确定什么、什么必须由用户确认”。
        """
        mappings = {item.group: item for item in self.channel_mappings}
        candidates = dict(self.plan_candidates)
        groups: Dict[str, Any] = {}
        for group in _ordered_groups(_mapping(self.config.get("power_supply_assignment"))):
            plan = _mapping(_mapping(self.config.get("power_supply_assignment")).get(group)).get(
                "power_plan"
            )
            mapping = mappings.get(group)
            groups[group] = {
                "power_plan": copy.deepcopy(plan)
                if isinstance(plan, Mapping)
                else copy.deepcopy(_EMPTY_POWER_PLAN),
                "candidate_routes": [
                    _route_document(route) for route in candidates.get(group, ())
                ],
                "channel_settings": mapping.to_dict()
                if mapping is not None
                else {
                    "status": CHANNEL_SETTINGS_NOT_APPLICABLE,
                    "reason": "旧配置没有可配对的角色化通道参数",
                    "routes": [],
                },
            }
        return {
            "schema_version": SUMMARY_SCHEMA_VERSION,
            "source": {
                "registry": registry_source,
                "config": config_source,
                "migrated_config": migrated_config_source,
            },
            "generated_at": generated_at or _now_iso(),
            "entries": [entry.to_dict() for entry in self.entries],
            "groups": groups,
            "drain_channel": (
                self.drain_migration.to_dict()
                if self.drain_migration is not None
                else {
                    "status": DRAIN_NOT_APPLICABLE,
                    "reason": "旧配置没有 DUT 漏极声明",
                    "route": None,
                    "candidates": [],
                }
            ),
            "unresolved_references": list(self.unresolved_references),
            "unconfirmed_plans": list(self.unconfirmed_plans),
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
        candidates = dict(self.plan_candidates)
        for group, routes in candidates.items():
            text = (
                "、".join(_route_token(route) for route in routes)
                if routes
                else "(无候选通道)"
            )
            lines.append(
                f"上电计划 {group}: 空计划，待用户在电源页确认；候选通道: {text}"
            )
        if self.drain_migration is not None:
            drain = self.drain_migration
            lines.append(
                "漏极: "
                + (
                    _route_token(drain.route)
                    if drain.route is not None
                    else "(未写入，待确认)"
                )
                + f" [{drain.status}] {drain.reason}"
            )
        for mapping in self.channel_mappings:
            lines.append(
                f"通道参数 {mapping.group}: [{mapping.status}] {mapping.reason}"
            )
        if self.unresolved_references:
            lines.append(
                "仍为空的供电分配引用: "
                + "、".join(self.unresolved_references)
                + "（这些参数只在本摘要里，重扫后按上面的旧值重填）"
            )
        if self.unconfirmed_plans:
            lines.append(
                "仍待确认的上电计划: "
                + "、".join(self.unconfirmed_plans)
                + "（迁移产物不再保存旧引用，请在电源页确认顺序后再写盘）"
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

    身份字段之间必须互相印证：``device_key`` 与完整设备码指向不同记录、注册表里同一
    ``device_key`` / 同一完整设备码对应多条记录、或旧条目声明的 ``device_key`` 根本不在
    设备表里，都返回 ``conflict``——这些都是“数据需要人核对”的信号，不是“换一台猜”的理由。
    """
    records = _registry_records(registry)
    full_code = _entry_full_code(entry)
    device_key = normalize_text(entry.get("device_key")).casefold()
    if not full_code and not device_key:
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
    by_code = tuple(
        record
        for record in records
        if full_code and _record_full_code(record).casefold() == full_code.casefold()
    )
    if len(by_key) > 1:
        return _IdentityEvidence(
            conflict=(
                f"设备表里有 {len(by_key)} 条记录共用 device_key {device_key!r}"
                f"（{'、'.join(_record_description(record) for record in by_key)}）；"
                "同一台设备只能登记一次，请先改正设备表"
            )
        )
    if len(by_code) > 1:
        return _IdentityEvidence(
            conflict=(
                f"设备表里有 {len(by_code)} 条记录共用完整设备码 {full_code}"
                f"（{'、'.join(_record_description(record) for record in by_code)}）；"
                "完整设备码重复时无法确认是哪一台，请先改正设备表"
            )
        )
    if device_key:
        if not by_key:
            if not by_code:
                return _IdentityEvidence()
            return _IdentityEvidence(
                conflict=(
                    f"旧条目声明的 device_key {device_key!r} 不在设备表里；"
                    "旧配置与设备表不一致，请核对后重试（不能只凭完整设备码猜成另一台设备）"
                )
            )
        if by_code and by_code[0] is not by_key[0]:
            # device_key 与完整设备码指向两台不同的设备：这是数据矛盾，不是“取更可信的那个”。
            return _IdentityEvidence(
                conflict=(
                    f"旧条目自相矛盾：device_key 指向 {_record_description(by_key[0])}，"
                    f"完整设备码 {full_code} 却属于 {_record_description(by_code[0])}；"
                    "请核对旧配置与设备表后重试"
                )
            )
        return _IdentityEvidence((by_key[0],))
    return _IdentityEvidence((by_code[0],) if by_code else ())


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
            full_code=_record_full_code(identity) or None,
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
            + "、".join(_record_description(record) for record in matches)
            + "），不能确定是哪一台；请先在设备表里改正角色后重试",
        )
    if not matches:
        if address:
            return verdict(
                STATUS_NEEDS_RESCAN,
                f"旧条目只有地址，没有完整设备码；{address} "
                + (
                    "不是任何已登记设备的地址"
                    if hint is None
                    else "只是某台已登记设备的历史地址，地址不是身份"
                )
                + "，不能推断是哪一台电源，必须重新扫描确认后再绑定",
            )
        return verdict(
            STATUS_UNIDENTIFIABLE,
            f"旧条目既没有地址也没有完整设备码，无法识别是哪台设备；"
            f"设备表里没有 role={old_key} 或完整设备码能对上的已登记电源"
            f"（若设备表已迁移过、旧槽位角色已折叠成 power_supply，旧键就无法再对应到设备），"
            "也不能凭型号猜测它是电源",
        )

    record = matches[0]
    label = normalize_text(record.get("label"))
    full_code = _record_full_code(record)
    name = _record_name(record)
    if not full_code:
        return verdict(
            STATUS_CONFLICT,
            f"旧键 {old_key} 命中的设备 {name} 没有完整设备码，无法确认身份；请修正设备表后重试",
        )
    if _identity_source(entry):
        matched_by = "device_key" if normalize_text(entry.get("device_key")) else "完整设备码"
        reason = f"旧条目自带设备身份，按{matched_by}命中 {name}（完整设备码 {full_code}）；"
        claimed = _entry_full_code(entry)
        if claimed and claimed != full_code:
            # device_key 唯一命中、完整设备码却写着别的值：迁移按 device_key 走，但把不一致
            # 说出来，不让操作员以为旧条目里的完整设备码也被采信了。
            reason += f"注意旧条目写的完整设备码 {claimed} 与设备表不一致，迁移以 device_key 为准；"
    else:
        reason = (
            f"旧槽位按注册表 role={old_key} 命中唯一设备 {name}（完整设备码 {full_code}）；"
        )
    if not label:
        return verdict(
            STATUS_NEEDS_RESCAN,
            f"旧键 {old_key} 命中的设备 {name}（完整设备码 {full_code}）还没有缩写设备码；"
            "请先保存一次设备表补齐缩写码，再重新迁移",
        )
    if _SEPARATOR in label or any(char.isspace() for char in label):
        return verdict(
            STATUS_CONFLICT,
            f"旧键 {old_key} 命中的设备 {name} 的缩写设备码 {label!r} 不能当 supply_id 用"
            f"（不得含空白或 {_SEPARATOR!r}），请在设备表里改正后重试",
        )
    reason += "迁移后保持未绑定未启用，需在配置页选中该设备"
    if enabled:
        reason += "；旧条目是启用状态，迁移后不沿用（可用电源只来自设备绑定）"
    owner = touched.get(label) if label else None
    if owner is not None and owner != old_key:
        return verdict(
            STATUS_CONFLICT,
            f"旧键 {old_key} 与 {owner} 落到同一台设备 {name}（完整设备码 {full_code}）上；"
            "两台设备才能各占一条电源配置，不能自动合并，请人工处理",
            record=record,
            label=label,
        )
    if old_key == label:
        # 旧键本身就是 ``supply_id``：这是已经迁移过的条目（迁移可重复执行）。
        return verdict(
            STATUS_MIGRATED,
            f"条目已按 supply_id 写在新格式下，命中 {name}（完整设备码 {full_code}）；原样保留",
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

    - 已迁移的设备表（``schema_version: 4``，电源带 ``label`` 与 ``full_code``）；
    - 现场旧设备表（``schema_version`` 1/2/3，``role: "PS3"``、没有 ``label``，完整设备码可能
      仍写在 ``serial`` 里）。此时
      ``migrate_registry_labels=True`` 会先在**内存副本**上按步骤 4 的规则补齐短标签
      （旧槽位折叠成 ``power_supply``，按序列号生成标签），迁移用的就是这份补全后的设备表。
      调用方要落盘时用 :func:`persist_registry_labels`：配置迁移不会悄悄改写设备表。
      ``migrate_registry_labels=False`` 时旧设备表被原样使用——没有标签的设备不可能成为
      ``supply_id``，判定为 ``needs_rescan``（先保存一次设备表），绝不在这里另生成一套编号。

    目标配置的写法（步骤 14 起）：

    - 编辑段 ``instruments.power_supplies`` 的键改为 ``supply_id`` 并补上 ``label`` /
      ``device_key`` / ``full_code`` / ``model``；通道参数与未知字段原样保留；旧 ``enabled``
      不沿用——可用性只来自设备绑定：有带身份的绑定记录时地址与 ``enabled: true`` 一起写回
      （与配置页 ``_apply_device_binding`` 同一条规则），否则写 ``enabled: false`` 且不留地址；
      旧条目的 ``address`` 只在这两种情况下写进配置，其余情况删除并把旧值留在摘要
      ``legacy_address`` 里，不给配置写一个没人确认过的连接目标；
    - ``power_supply_assignment.*.supplies.*`` 的引用写成 ``supply_id``（能迁移时）或
      ``supply_id: null``（不能迁移时），过渡别名 ``name`` 只在能迁移时同值写出；
    - 每组 ``power_plan`` 写成空计划（空序列 + ``0.0``），**不根据旧角色生成顺序**；摘要的
      ``groups.<组>.candidate_routes`` 列出用户在电源页可选的物理路由；
    - 旧 ``test_plan.dut.power_roles`` 与旧 ``run_mapping.dut_power_channels`` 只在 role 集合
      完全一致、各自唯一、token 能解析到迁移后的电源/通道时用于重建通道参数；否则该组的
      ``channel_settings`` 保持为空，摘要 ``channel_settings.status = unmapped``；
    - 旧 ``role == "drain"`` 只在 DUT 分配通道里恰好一条且能唯一映射时写入
      ``dut_config.drain_channel``；否则写 ``null``，摘要记录 ``needs_confirmation``；
    - ``device_bindings`` 只保留单设备角色，旧槽位键移除；能迁移的电源若旧绑定本身带身份
      （``source != legacy_config``），折进 ``power_supplies.<supply_id>``，只有地址的旧绑定
      一律丢弃并交由重新扫描；
    - ``power_supply_assignment.<组>.power_supply_count`` 按该组仍能落地的引用数重算；
      ``dut_config.power_supply_count`` 是测试方案里的现场参数，**不重算**（迁移不改测试方案）；
    - 正式 ``test_plan`` / ``run_mapping`` 由 authoring 从编辑段重新生成，迁移输出
      当前写入版本（``schema_version = 1.2``），不再保留 ``role`` / ``power_roles`` /
      ``power_channels`` / ``connection`` / ``dut_power_channels``。``runs/`` 下的历史快照
      不迁移、不改写。

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
    # 已经是可保存的新版（1.1/1.2）且不含旧槽位/旧角色字段的文档原样返回：迁移必须可
    # 重复执行，也不能把用户已经确认的顺序或漏极重写成空计划。唯一例外是已退役的驱放
    # 供电方式：它不参与计划判定，删掉它既不改写计划，又保证迁移输出总是干净的新版。
    if _is_current_clean_document(config):
        _strip_driver_power_source(target)
        return LegacyMigrationResult(
            config=target,
            entries=tuple(entries),
            old_keys=old_keys,
            unresolved=_unresolved_entries(entries),
            unresolved_references=_unresolved_references(target),
            unconfirmed_plans=_unconfirmed_plan_paths(target),
            plan_candidates=_plan_candidates(target),
            channel_mappings=(),
            drain_migration=None,
        )

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
    _apply_trusted_bindings(target_supplies, bindings, migrated_by_old)

    # 步骤 14：先按旧角色唯一配对重建通道参数，再把每组计划写成空计划。旧供电分配
    # 段只在这里用于读旧数据与统计待决项；写出的 1.1 编辑段只保留上电计划，不再给
    # 器件固定分配电源。
    channel_mappings = _migrate_channel_settings(target, config, migrated_by_old)
    drain_migration = _migrate_drain_channel(target, config, migrated_by_old)
    _write_empty_power_plans(target)
    unresolved = _unresolved_references(target)
    plan_candidates = _plan_candidates(target)
    _strip_power_assignments(target)
    _strip_driver_power_source(target)

    authored = _author_migrated_document(target)

    return LegacyMigrationResult(
        config=authored,
        entries=tuple(entries),
        old_keys=old_keys,
        unresolved=_unresolved_entries(entries),
        unresolved_references=unresolved,
        plan_candidates=plan_candidates,
        channel_mappings=channel_mappings,
        drain_migration=drain_migration,
    )


def _unresolved_entries(
    entries: Iterable[LegacyMigrationEntry],
) -> Tuple[Tuple[str, str], ...]:
    return tuple(
        (entry.old_key, entry.reason)
        for entry in entries
        if entry.status in STATUSES_REQUIRING_ACTION
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
       :func:`infrastructure.config.validation.validate_saveable_configuration`，**不能跳过**；
       它校验整份文件的结构/类型（含正式段与硬件装配段），但跳过“运行就绪”规则——迁移
       写出的空计划必须由用户先确认顺序（§6.6），创建运行时自然会再拦一次。校验只拒绝
       写入，不会先写再校验。

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
    if result.unconfirmed_plans:
        raise LegacyConfigMigrationError(
            "迁移产物仍有未确认的上电计划，未写入任何文件（原配置保持不变）: "
            + "、".join(result.unconfirmed_plans)
            + "；这份 1.1 文档已不再保存旧引用或旧条目，无法仅凭文档证明迁移完整，"
            "请先在电源页确认顺序再保存"
        )
    check = validator if validator is not None else validate_saveable_configuration
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
    # 写盘同样只走正式配置的唯一写入口：迁移不另起一套序列化/替换实现，格式与失败
    # 语义（原文件不变、异常原样上抛）与配置页面保存完全一致。
    return JsonConfigurationRepository().save_document(path, result.config)


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

    旧条目自己的 ``label`` / ``device_key`` / ``full_code`` / ``serial`` / ``model`` 一律丢弃：身份只有一个
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
        migrated["full_code"] = entry.full_code
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
            prepared.pop("name", None)
            if "full_code" not in prepared:
                prepared["full_code"] = prepared.get("serial")
            prepared.pop("serial", None)
            prepared["label"] = migrated.label
            prepared["supply_id"] = migrated.new_key
            power_bindings.setdefault(migrated.new_key, prepared)
            continue
        kept[key] = _current_binding_record(record)
    # 电源绑定是一个集合：旧槽位键移除后仍保留这个键，文件自身就能说明“还没有绑定”。
    kept["power_supplies"] = power_bindings
    target["device_bindings"] = kept
    return power_bindings


def _current_binding_record(record: Any) -> Any:
    """把一条保留的绑定记录投影成当前字段：``serial`` -> ``full_code``，丢弃旧资产名。

    迁移写出的是**新文档**：旧字段名只在读入时识别一次，不能继续带进结果。``name`` 是
    已退役的资产名，``serial`` 由 ``full_code`` 取代；两者同时出现时以 ``full_code`` 为准。
    非映射记录原样返回，交给使用方按“不是一条绑定记录”处理。
    """
    if not isinstance(record, Mapping):
        return record
    prepared = copy.deepcopy(dict(record))
    prepared.pop("name", None)
    if "full_code" not in prepared:
        prepared["full_code"] = prepared.get("serial")
    prepared.pop("serial", None)
    return prepared


def _is_current_clean_document(config: Mapping[str, Any]) -> bool:
    """这份文档是否已经是可以保存的版本、且不含任何 1.0 电源字段。

    只有“已经迁移干净”的文档才原样返回；迁移可重复执行，但绝不能把用户确认过的
    顺序、间隔或漏极重新写成空计划。可接受的是当前版本与显式可重写版本
    （:data:`REWRITABLE_SCHEMA_VERSIONS`，当前为 1.1）：1.1 交给配置页显式重新
    保存升级到 1.2，不由迁移器隐式改写；1.0 与未知版本仍按旧逻辑迁移/拒绝。
    """
    version = config.get("schema_version")
    if version not in (*REWRITABLE_SCHEMA_VERSIONS, SUPPORTED_SCHEMA_VERSION):
        return False
    return not _has_legacy_power_inputs(config)


def _has_legacy_power_inputs(config: Mapping[str, Any]) -> bool:
    """是否存在只在 1.0 读取兼容层出现的电源字段。"""
    supplies = _mapping(_mapping(config.get("instruments")).get("power_supplies"))
    for key, supply in supplies.items():
        if str(key) in LEGACY_ROLES:
            return True
        for channel in _mapping(_mapping(supply).get("channels")).values():
            if "role" in _mapping(channel):
                return True
    groups = _mapping(config.get("power_supply_assignment"))
    for group in groups.values():
        for assignment in _mapping(_mapping(group).get("supplies")).values():
            item = _mapping(assignment)
            if "name" in item and "supply_id" not in item:
                return True
            if item.get("supply_id") in LEGACY_ROLES:
                return True
    plan = _mapping(config.get("test_plan"))
    for section in (_mapping(plan.get("dut")), _mapping(plan.get("driver_mode"))):
        if "power_roles" in section or "power_channels" in section:
            return True
    mapping = _mapping(config.get("run_mapping"))
    if "dut_power_channels" in mapping:
        return True
    if "power_channels" in _mapping(mapping.get("driver_mode")):
        return True
    if "power_supply" in _mapping(mapping.get("instruments")):
        return True
    return False


def _write_empty_power_plans(target: Dict[str, Any]) -> None:
    """把每组计划写成空计划；从不根据旧角色生成顺序（§6.14 规则 4）。

    走到这里的文档都含 1.0 遗留电源字段，本次迁移必须把顺序交回用户确认：旧角色不能
    变成顺序，残留的 ``power_plan`` 也不能当成“已经确认过”而沿用。已经迁移干净的新版
    文档在 :func:`migrate_legacy_power_supplies` 里提前返回，用户确认过的顺序、间隔与
    漏极不会因此被清空。
    """
    groups = target.get("power_supply_assignment")
    if not isinstance(groups, dict):
        return
    for group in groups.values():
        if not isinstance(group, dict):
            continue
        group["power_plan"] = copy.deepcopy(_EMPTY_POWER_PLAN)


def _author_migrated_document(target: Dict[str, Any]) -> Dict[str, Any]:
    """把编辑段交给 authoring，生成当前版本正式段（§6.14 规则 5）。

    迁移器自己不改写 ``test_plan`` / ``run_mapping``；通道 ``role``、旧 ``name`` 别名与
    旧正式段都在这一步消失，1.0 的标量功率扫描也在 authoring 的保存边界包装为
    当前版本的 ``power_scan_plans`` 列表。
    """
    target["schema_version"] = OFFICIAL_SCHEMA_VERSION
    return ConfigurationAuthoringService().author_document(
        ConfigurationEditingDocument.from_mapping(target)
    )


def _migrate_channel_settings(
    target: Dict[str, Any],
    source: Mapping[str, Any],
    migrated_by_old: Mapping[str, LegacyMigrationEntry],
) -> Tuple[LegacyChannelMapping, ...]:
    """按旧 role 值唯一配对，重建每组的通道参数（§6.14 规则 2）。

    只有 role 集合完全一致、role 各自唯一、每条旧 token 都能解析成**迁移后**的
    物理路由，并且路由落在该组分配通道里时，才把旧 ``power_roles`` 的数值搬运到编辑段；
    任何一步不满足都只返回 ``unmapped``，不采信旧正式段的参数，由摘要提示用户重新核对。
    正式 ``channel_settings`` 始终由 authoring 从编辑段生成，迁移器不手写正式段。
    """
    mappings: List[LegacyChannelMapping] = []
    for group in ASSIGNMENT_GROUPS:
        roles, rows = _legacy_role_inputs(source, group)
        if not roles and not rows:
            mappings.append(
                LegacyChannelMapping(
                    group=group,
                    status=CHANNEL_SETTINGS_NOT_APPLICABLE,
                    reason="旧配置没有角色化通道参数，沿用编辑段里的通道数值",
                )
            )
            continue
        if not roles or not rows:
            mappings.append(
                LegacyChannelMapping(
                    group=group,
                    status=CHANNEL_SETTINGS_UNMAPPED,
                    reason=(
                        f"{CODE_POWER_SETTINGS_UNMAPPED}: "
                        "旧 test_plan 的 power_roles 与旧 run_mapping 的通道列表"
                        "不是两边都有，无法按 role 唯一配对"
                    ),
                )
            )
            continue

        failure = _legacy_role_mapping_failure(
            target, group, roles, rows, migrated_by_old
        )
        if failure is not None:
            mappings.append(
                LegacyChannelMapping(
                    group=group,
                    status=CHANNEL_SETTINGS_UNMAPPED,
                    reason=f"{CODE_POWER_SETTINGS_UNMAPPED}: {failure}",
                )
            )
            continue

        record_by_role, route_by_role = _legacy_role_maps(
            target, group, roles, rows, migrated_by_old
        )
        settings: List[Tuple[Tuple[str, str], Dict[str, Any]]] = []
        for role, record in record_by_role.items():
            route = route_by_role[role]
            values = _legacy_channel_values(record)
            _write_channel_values(target, route, values)
            settings.append((route, values))
        mappings.append(
            LegacyChannelMapping(
                group=group,
                status=CHANNEL_SETTINGS_MAPPED,
                reason="按旧 role 值唯一配对到迁移后的物理路由",
                routes=tuple(route_by_role.values()),
                settings=tuple(settings),
            )
        )
    return tuple(mappings)


def _legacy_role_inputs(
    source: Mapping[str, Any], group: str
) -> Tuple[Mapping[str, Any], List[Any]]:
    plan = _mapping(source.get("test_plan"))
    mapping = _mapping(source.get("run_mapping"))
    if group == "dut_amplifier":
        roles = _mapping(_mapping(plan.get("dut")).get("power_roles"))
        rows = _list(mapping.get("dut_power_channels"))
        return roles, list(rows)
    roles = _mapping(_mapping(plan.get("driver_mode")).get("power_roles"))
    rows = _list(_mapping(mapping.get("driver_mode")).get("power_channels"))
    return roles, list(rows)


def _legacy_role_mapping_failure(
    target: Mapping[str, Any],
    group: str,
    roles: Mapping[str, Any],
    rows: Sequence[Any],
    migrated_by_old: Mapping[str, LegacyMigrationEntry],
) -> Optional[str]:
    """检查旧 role 两侧能否唯一配对；返回失败原因，成功返回 ``None``。"""
    role_records: Dict[str, Mapping[str, Any]] = {}
    for key, item in roles.items():
        record = _mapping(item)
        role = normalize_text(record.get("role")) or normalize_text(key)
        if not role:
            return f"旧 power_roles 的 {key!r} 没有可识别的 role"
        if role in role_records:
            return f"旧 power_roles 里 role={role!r} 重复，无法确定哪条参数属于哪个通道"
        role_records[role] = record
    if group == "driver_amplifier":
        if set(role_records) != {_LEGACY_DRIVER}:
            return "驱放旧 power_roles 只能有一个 driver 键"
        if len(rows) != 1:
            return f"驱放旧通道有 {len(rows)} 条，只有一个 driver 参数，无法区分是哪一条"
    routes_by_role: Dict[str, Tuple[str, str]] = {}
    for row in rows:
        item = _mapping(row)
        role = normalize_text(item.get("role"))
        if not role:
            return "旧运行映射里有通道没有 role，无法与旧参数配对"
        if role in routes_by_role:
            return f"旧运行映射里 role={role!r} 出现多次，无法唯一配对"
        route = _resolve_legacy_route(item, target, group, migrated_by_old)
        if route is None:
            return f"旧运行映射里 role={role!r} 的通道无法解析成迁移后的物理路由"
        routes_by_role[role] = route
    if set(routes_by_role) != set(role_records):
        return (
            "旧 power_roles 与旧运行映射的 role 集合不一致（"
            + "、".join(sorted(set(role_records)))
            + " vs "
            + "、".join(sorted(set(routes_by_role)))
            + "）"
        )
    group_routes = set(_assignment_routes(target, group))
    supplies = _mapping(_mapping(target.get("instruments")).get("power_supplies"))
    for role, route in routes_by_role.items():
        if route not in group_routes:
            return f"role={role!r} 映射到 {_route_token(route)}，但该通道不在迁移后的分配里"
        channels = _mapping(_mapping(supplies.get(route[0])).get("channels"))
        if route[1] not in channels:
            return f"role={role!r} 映射到 {_route_token(route)}，但电源没有声明该通道"
    return None


def _legacy_role_maps(
    target: Mapping[str, Any],
    group: str,
    roles: Mapping[str, Any],
    rows: Sequence[Any],
    migrated_by_old: Mapping[str, LegacyMigrationEntry],
) -> Tuple[Dict[str, Mapping[str, Any]], Dict[str, Tuple[str, str]]]:
    """在 :func:`_legacy_role_mapping_failure` 通过后建立 role 两侧的映射。"""
    record_by_role: Dict[str, Mapping[str, Any]] = {}
    for key, item in roles.items():
        record = _mapping(item)
        role = normalize_text(record.get("role")) or normalize_text(key)
        record_by_role[role] = record
    route_by_role: Dict[str, Tuple[str, str]] = {}
    for row in rows:
        item = _mapping(row)
        role = normalize_text(item.get("role"))
        route_by_role[role] = _resolve_legacy_route(item, target, group, migrated_by_old)
    return record_by_role, route_by_role


def _resolve_legacy_route(
    row: Mapping[str, Any],
    target: Mapping[str, Any],
    group: str,
    migrated_by_old: Mapping[str, LegacyMigrationEntry],
) -> Optional[Tuple[str, str]]:
    """把旧快照的 ``CH1`` 或 ``supply_id/CH1`` 解析成迁移后的物理路由。

    裸通道名只属于 1.0 单电源快照：先取旧单数 ``instruments.power_supply`` 的身份，再
    经身份迁移表改写。解析不了就返回 ``None``——绝不按 ``CH1``/``CH2`` 猜电源或角色。
    """
    token = normalize_text(row.get("channel"))
    if not token:
        return None
    if _SEPARATOR in token:
        supply_ref, channel = token.split(_SEPARATOR, 1)
    else:
        supply_ref, channel = _legacy_single_supply_reference(target, group), token
    supply_id = _mapped_supply_id(supply_ref, migrated_by_old)
    channel = normalize_text(channel)
    if not supply_id or not channel or _SEPARATOR in channel:
        return None
    return supply_id, channel


def _legacy_single_supply_reference(source: Mapping[str, Any], group: str) -> Optional[str]:
    """1.0 快照的裸通道属于哪台电源：旧单数条目优先，其次该组唯一引用。"""
    single = _mapping(_mapping(_mapping(source.get("run_mapping")).get("instruments")).get("power_supply"))
    for key in ("label", "model", "device_key"):
        value = normalize_text(single.get(key))
        if value:
            return value
    references: List[str] = []
    for assignment in _mapping(_mapping(_mapping(source.get("power_supply_assignment")).get(group)).get("supplies")).values():
        value = assignment_supply_id(_mapping(assignment))
        if value is not None and value not in references:
            references.append(value)
    return references[0] if len(references) == 1 else None


def _mapped_supply_id(
    reference: Optional[str],
    migrated_by_old: Mapping[str, LegacyMigrationEntry],
) -> Optional[str]:
    """旧引用 -> 迁移后的 ``supply_id``；已经是新键的原样返回。"""
    text = normalize_text(reference)
    if not text:
        return None
    entry = migrated_by_old.get(text)
    if entry is not None and entry.new_key:
        return entry.new_key
    return text if _looks_like_new_reference(text) else None


def _legacy_channel_values(record: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "voltage": _finite_number(record.get("voltage")),
        "current": _finite_number(record.get("current")),
        "voltage_protection": _finite_number(record.get("voltage_protection")),
        "current_protection": _finite_number(record.get("current_protection")),
    }


def _write_channel_values(
    target: Dict[str, Any], route: Tuple[str, str], values: Mapping[str, Any]
) -> bool:
    """把旧正式段的数值写回编辑段的通道参数；``None`` 不覆盖已有值。"""
    supply = _mapping(
        _mapping(_mapping(target.get("instruments")).get("power_supplies")).get(route[0])
    )
    channel = supply.get("channels")
    if not isinstance(channel, dict):
        return False
    item = channel.get(route[1])
    if not isinstance(item, dict):
        return False
    for section, field, value in (
        ("voltage", "value", values.get("voltage")),
        ("current", "value", values.get("current")),
        ("voltage", "protection", values.get("voltage_protection")),
        ("current", "protection", values.get("current_protection")),
    ):
        if value is None:
            continue
        block = item.get(section)
        if not isinstance(block, dict):
            block = {}
            item[section] = block
        block[field] = value
    return True


def _migrate_drain_channel(
    target: Dict[str, Any],
    source: Mapping[str, Any],
    migrated_by_old: Mapping[str, LegacyMigrationEntry],
) -> LegacyDrainMigration:
    """按旧 ``role == "drain"`` 迁移漏极；0 条或多条一律待确认，不按 CH 名猜。"""
    dut_config = target.get("dut_config")
    if not isinstance(dut_config, dict):
        dut_config = {}
        target["dut_config"] = dut_config
    preserved = dut_config.get("drain_channel")
    if preserved is not None:
        route = _route_from_document(preserved)
        return LegacyDrainMigration(
            status=DRAIN_PRESERVED,
            reason="文档里已有漏极引用，迁移不改写用户确认过的漏极",
            route=route,
        )

    rows = [
        _mapping(row)
        for row in _list(_mapping(source.get("run_mapping")).get("dut_power_channels"))
        if normalize_text(_mapping(row).get("role")) == _LEGACY_DUT_DRAIN
    ]
    candidates = [
        route
        for route in (
            _resolve_legacy_route(row, target, "dut_amplifier", migrated_by_old)
            for row in rows
        )
        if route is not None
    ]
    group_routes = set(_assignment_routes(target, "dut_amplifier"))
    if not rows and not group_routes:
        return LegacyDrainMigration(
            status=DRAIN_NOT_APPLICABLE,
            reason="旧配置没有 DUT 电源通道，也没有漏极声明",
        )
    if len(rows) != 1:
        dut_config["drain_channel"] = None
        reason = (
            "旧配置没有 role=drain 的 DUT 通道，漏极必须由用户确认"
            if not rows
            else f"旧配置有 {len(rows)} 条 role=drain 的 DUT 通道，不能确定哪一个才是漏极"
        )
        return LegacyDrainMigration(
            status=DRAIN_NEEDS_CONFIRMATION,
            reason=reason,
            candidates=tuple(candidates),
        )
    route = _resolve_legacy_route(rows[0], target, "dut_amplifier", migrated_by_old)
    if route is None:
        dut_config["drain_channel"] = None
        return LegacyDrainMigration(
            status=DRAIN_NEEDS_CONFIRMATION,
            reason="旧 role=drain 的通道无法映射到迁移后的物理路由",
            candidates=tuple(candidates),
        )
    if route not in group_routes:
        dut_config["drain_channel"] = None
        return LegacyDrainMigration(
            status=DRAIN_NEEDS_CONFIRMATION,
            reason=(
                f"旧 role=drain 映射到 {_route_token(route)}，"
                "但该通道不在迁移后的 DUT 分配里"
            ),
            candidates=tuple(candidates),
        )
    dut_config["drain_channel"] = _route_document(route)
    return LegacyDrainMigration(
        status=DRAIN_MIGRATED,
        reason=f"旧配置恰好一条 role=drain 且唯一映射到 {_route_token(route)}",
        route=route,
        candidates=tuple(candidates),
    )


def _plan_candidates(
    document: Mapping[str, Any],
) -> Tuple[Tuple[str, Tuple[Tuple[str, str], ...]], ...]:
    """每组分配里可选的物理路由，供用户在电源页确认上电顺序。"""
    groups = _mapping(document.get("power_supply_assignment"))
    result: List[Tuple[str, Tuple[Tuple[str, str], ...]]] = []
    for group in _ordered_groups(groups):
        result.append((group, tuple(_assignment_routes(document, group))))
    return tuple(result)


def _assignment_routes(document: Mapping[str, Any], group: str) -> List[Tuple[str, str]]:
    """一组分配引用里的物理路由（按出现顺序去重，未落地的引用跳过）。"""
    routes: List[Tuple[str, str]] = []
    supplies = _mapping(_mapping(_mapping(document.get("power_supply_assignment")).get(group)).get("supplies"))
    for assignment in supplies.values():
        supply_id = assignment_supply_id(_mapping(assignment))
        if supply_id is None:
            continue
        for channel in _channel_list(_mapping(assignment).get("channel")):
            route = (supply_id, channel)
            if route not in routes:
                routes.append(route)
    return routes


def _route_document(route: Tuple[str, str]) -> Dict[str, str]:
    return {"supply_id": route[0], "channel": route[1]}


def _route_token(route: Tuple[str, str]) -> str:
    return f"{route[0]}{_SEPARATOR}{route[1]}"


def _route_from_document(value: Any) -> Optional[Tuple[str, str]]:
    if not isinstance(value, Mapping):
        return None
    supply_id = normalize_text(value.get("supply_id"))
    channel = normalize_text(value.get("channel"))
    if not supply_id or not channel or _SEPARATOR in supply_id or _SEPARATOR in channel:
        return None
    return supply_id, channel


def _finite_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


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


def _plan_has_routes(assignment: Any) -> bool:
    """一组的 ``power_plan`` 是否已有至少一条可解析的路由。"""
    plan = _mapping(_mapping(assignment).get("power_plan"))
    sequence = plan.get("power_sequence")
    if not isinstance(sequence, Sequence) or isinstance(sequence, (str, bytes)):
        return False
    return any(isinstance(item, Mapping) for item in sequence)


def _unconfirmed_plan_paths(document: Mapping[str, Any]) -> Tuple[str, ...]:
    """重新迁移“已迁移干净”的 1.1 文档时，仍未确认路线的计划路径。

    这类文档已经删除旧 ``supplies``，因此无法再区分“完整迁移但用户尚未排序”和
    “迁移本身缺来源、需要人工补全”。写盘门槛不能把后者当成完整迁移：DUT 计划是运行
    就绪的必需项，为空时必须先确认，而不是返回 ``writable=True``。驱放计划不同：
    空计划就是自带供电，是合法的完整状态，不要求确认。
    """
    paths: List[str] = []
    groups = _mapping(document.get("power_supply_assignment"))
    if not _plan_has_routes(groups.get("dut_amplifier")):
        paths.append("power_supply_assignment.dut_amplifier.power_plan")
    return tuple(paths)


def _strip_power_assignments(target: Dict[str, Any]) -> None:
    """删除旧编辑段的“按器件固定分配电源”表，只保留上电计划。

    1.1 的电源使用完全由 ``power_plan.power_sequence`` 决定；``supplies`` 与
    ``power_supply_count`` 只用于读取旧配置、判断迁移是否完整，不能写进新文档。
    """
    groups = target.get("power_supply_assignment")
    if not isinstance(groups, dict):
        return
    for group in groups.values():
        if not isinstance(group, dict):
            continue
        group.pop("supplies", None)
        group.pop("power_supply_count", None)


def _strip_driver_power_source(target: Dict[str, Any]) -> None:
    """删除已退役的驱放供电方式（编辑段与两份正式段）。

    1.1 不再保存该选择：驱动功放上电计划为空即表示自带供电，计划非空即表示由本机
    直流电源供电。文档里残留的 ``power_source`` 只是旧版本的写盘结果，读取时忽略，
    迁移或重新保存时删除。
    """
    sections = [target.get("driver_mode")]
    for name in ("test_plan", "run_mapping"):
        sections.append(_mapping(target.get(name)).get("driver_mode"))
    for driver in sections:
        if isinstance(driver, dict):
            driver.pop("power_source", None)


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
    full_code = _record_full_code(record).casefold()
    if vendor and full_code:
        return f"{vendor}:{full_code}"
    return None


def _record_full_code(record: Mapping[str, Any]) -> str:
    """读取设备记录里的完整设备码。

    历史读取边界：旧设备表与旧绑定把它写在 ``serial`` 里；``full_code`` 优先，读入后立即
    投影为 ``full_code``，新文档不再写出旧键。
    """
    value = record.get("full_code")
    if value is None:
        value = record.get("serial")
    return normalize_full_code(value)


def _record_name(record: Mapping[str, Any]) -> str:
    """设备的可读名称：缩写设备码优先，其次型号，最后完整设备码。

    已退役的资产名 ``name`` 不再读取；设备表里也不再有这个字段。
    """
    return (
        normalize_text(record.get("label"))
        or normalize_text(record.get("model"))
        or _record_full_code(record)
        or "(未登记设备)"
    )


def _record_description(record: Mapping[str, Any]) -> str:
    """冲突消息里的设备描述：厂商 + 型号 + 缩写码/完整设备码，便于区分同型号的两台。"""
    vendor = normalize_text(record.get("vendor")) or "(无厂商)"
    model = normalize_text(record.get("model")) or "(无型号)"
    code = normalize_text(record.get("label")) or _record_full_code(record) or "(无设备码)"
    return f"{vendor} {model}（{code}）"


def _entry_full_code(entry: Mapping[str, Any]) -> str:
    """旧配置条目的完整设备码：``full_code`` 优先，兼容旧 ``serial``。"""
    value = entry.get("full_code")
    if value is None:
        value = entry.get("serial")
    return normalize_full_code(value)


def _identity_source(entry: Mapping[str, Any]) -> bool:
    """旧条目自带身份（``device_key`` 或完整设备码），走第一条判定。"""
    return bool(normalize_text(entry.get("device_key")) or _entry_full_code(entry))


def _binding_has_identity(record: Any) -> bool:
    if not isinstance(record, Mapping):
        return False
    if normalize_text(record.get("source")).casefold() == "legacy_config":
        return False
    return bool(normalize_text(record.get("device_key")) and _record_full_code(record))


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
    "CHANNEL_SETTINGS_MAPPED",
    "CHANNEL_SETTINGS_NOT_APPLICABLE",
    "CHANNEL_SETTINGS_UNMAPPED",
    "CODE_POWER_SETTINGS_UNMAPPED",
    "DRAIN_MIGRATED",
    "DRAIN_NEEDS_CONFIRMATION",
    "DRAIN_NOT_APPLICABLE",
    "DRAIN_PRESERVED",
    "LegacyChannelMapping",
    "LegacyConfigMigrationError",
    "LegacyDrainMigration",
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
