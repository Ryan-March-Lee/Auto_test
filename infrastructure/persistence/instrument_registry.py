"""仪器注册表仓储：单文件 JSON，人眼可读、可手改、可 diff。

为什么不用数据库：现场只有个位数仪器、人工点击扫描、一年改几次 IP。
这个负载下 SQLite 的唯一好处是约束由数据库强制，但代价是文件不可读、不可 diff、
必须装工具才能查看。同样的约束在 :meth:`InstrumentRegistry._validate` 里用代码校验即可，
真出现重复会直接报错而不是静默接受。

文件结构（``instruments.json``）::

    {
      "schema_version": 3,
      "updated_at": "2026-10-28T14:39:46+08:00",
      "instruments": [
        {
          "name": "DP832A-1",           # 资产名 = 型号-同型号序号，永久稳定，但不进界面
          "vendor": "RIGOL TECHNOLOGIES",
          "model": "DP832A",
          "model_index": 1,
          "serial": "DP8B214901519",    # 唯一身份，永不参与匹配的只有 IP 和名称
          "label": "DP8B1519",          # 现场短标签（见下）＝电源的 supply_id，首次生成后冻结
          "order_no": null,
          "firmware": "00.01.16.00.02",
          "options": [],
          "role": "power_supply",       # 设备类别（不含“第几台”），多台电源靠 label 区分
          "status": "online",
          "current_ip": "192.168.1.102",
          "notes": null,
          "first_seen_at": "...",
          "last_seen_at": "...",
          "known_ips": [               # 历史地址，最近在前，最多 MAX_KNOWN_IPS 条
            {"ip": "...", "mac": "...", "idn_port": 5555, "visa_address": "...", "last_seen": "..."}
          ]
        }
      ]
    }

设计约束：

- 设备唯一标识由 ``厂商 + 序列号`` 派生（``device_key``），不落盘，避免两处不一致。
- ``current_ip`` 只是当前连接地址；IP 变化时旧地址进 ``known_ips`` 并保留最近若干条。
- 资产名和同型号序号一经分配不再改动；退役设备保留记录与序号，新设备取最大序号 + 1。
- ``role`` 是这台设备在测试链路里的类别（``signal_generator`` / ``spectrum_analyzer`` /
  ``power_supply``），不含“第几台”：多台电源共用 ``power_supply``，靠各自的短标签
  （``label``）区分。现场每台仪器只承担一种类别，因此角色随设备登记一次，界面不再让用户
  逐台选择；空值与未知角色都视为“未分配角色”，不允许绑定。
- 读写分工：**读取兼容旧 schema**（``schema_version`` 1/2、``role: "PS1".."PS4"``、没有
  ``label``），读出来仍是旧字面量，好让按旧槽位工作的调用方（设备发现面板、配置页，属于
  步骤 5、6）继续可用；**保存统一写新字段**，写盘前由 :func:`_canonical_document` 迁移：
  旧槽位经 ``domain.instruments.models.canonical_role`` 折叠成 ``power_supply``，没有标签
  的设备按序列号补生成 ``label``（规则见 :mod:`domain.instruments.labels`）。
- **标签对所有登记设备生效**：它只由序列号派生，与角色无关，因此换角色、换 IP、改同型号
  序号都不会换标签。界面上凡是显示这台设备，用的都是标签（电源是 ``DP8B1519（DP832A）``，
  其它仪器是 ``SMW200A（5462）``），资产名 ``型号-同型号序号`` 只作文件里的软件资产名，
  不进界面——同型号两台靠标签区分，不靠后缀数字。
- 补生成标签的失败处理分两档：**电源**没有标签就没有 ``supply_id``，配置与路由会失配，
  因此派生失败一律阻止保存并指出是哪台设备；**其它角色与未登记角色的设备**标签只用于
  展示，派生失败时留空（显示退回资产名），不阻断保存。
- 迁移只改 ``role`` 与 ``label``：资产名、序列号、地址和人工补充的字段逐字节保留。
- **已有标签原样保留**：迁移只填空缺，不规范化、不裁剪空白、不改大小写；标签必须能当
  ``supply_id`` 用（非空字符串、不含空白与 ``/``），否则读取/保存直接报错，交给操作员
  改正——迁移不许把写错的标签悄悄“洗干净”。``null``、空串与纯空白都表示“还没有标签”，
  保存时统一写 ``null``。
- 标签冲突阻止保存并报出冲突的两台设备，绝不覆盖、也不静默改名；电源无法从序列号派生标签
  时同样阻止保存并指出是哪台设备。
- 写入使用 :func:`write_json_atomic`（临时文件 + 原子替换），中断不会留下半截文件；
  迁移、校验、``schema_version`` 与 ``updated_at`` 的更新都在内存副本上完成，写盘成功后才
  提交回对象，因此失败时内存文档与原文件都保持迁移前的样子。
- 本模块不发起网络探测，只负责读写设备表；扫描逻辑属于发现模块。
"""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from domain.instruments.labels import InstrumentLabelError, generate_instrument_label
from domain.instruments.models import (
    POWER_SUPPLY_ROLE,
    TEST_ROLES,
    DeviceStatus,
    Instrument,
    InstrumentEndpoint,
    InstrumentIdentity,
    build_instrument_name,
    canonical_role,
    make_device_key,
    normalize_model,
    normalize_role,
    normalize_serial,
    normalize_text,
    normalize_vendor,
)
from infrastructure.filesystem.atomic_json import read_json, write_json_atomic
from infrastructure.filesystem.paths import DEFAULT_PATHS, PathLike

SCHEMA_VERSION = 3
# 读得动的旧版本：两者都缺 ``label``、电源用槽位编号 ``PS1``..``PS4``。读取时按旧字面量返回，
# 保存时由 :func:`_canonical_document` 迁移；比当前版本更新的文件直接报错，避免降级覆盖。
LEGACY_SCHEMA_VERSIONS = (1, 2)
DEFAULT_REGISTRY_FILENAME = "instruments.json"
MAX_KNOWN_IPS = 3

# 标签同时是 ``supply_id``，会被拼成 ``supply_id/CHx``；含空白或 ``/`` 的标签会在路由处被截断。
_LABEL_SEPARATOR = "/"

_VALID_STATUSES = {status.value for status in DeviceStatus}


class InstrumentRegistryError(ValueError):
    """设备表内容不合法（字段缺失、重复序列号、序号冲突等）。"""


def utc_now_iso() -> str:
    """带本地时区偏移的 ISO 时间戳，便于人工阅读。"""
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def default_registry_path() -> Path:
    return DEFAULT_PATHS.instruments_file


def _empty_document() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "updated_at": utc_now_iso(), "instruments": []}


def _entry_device_key(entry: Mapping[str, Any]) -> str:
    return make_device_key(entry.get("vendor"), entry.get("serial"))


def _declared_entry_role(entry: Mapping[str, Any]) -> str | None:
    """设备表条目写回时的 ``role`` 取值。

    旧槽位 ``PS1``..``PS4`` 经 :func:`canonical_role` 折叠成 ``power_supply``；未登记角色写
    ``None``；**认不出来的角色原样返回**，交给 :func:`_validate_document` 报错——
    迁移不能顺手把写错的角色丢掉，否则手改 JSON 的错误会静默消失。
    """
    raw = entry.get("role")
    role = canonical_role(raw)
    if role is not None:
        return role
    return None if not normalize_text(raw) else raw


def _existing_label(entry: Mapping[str, Any]) -> Any:
    """条目里已有的标签，**原样**返回；``null``、空串与纯空白表示“还没有标签”。

    这里刻意不做 :func:`normalize_text`：标签是人工可见的现场短标签，迁移的任务只是补空缺。
    裁剪空白或统一大小写会让文件里的值与操作员写的值不一致，也会掩盖“标签不能当
    ``supply_id``”这类错误；含空白与 ``/`` 的标签直接由 :func:`_validate_document` 报错。
    类型写错的值同样原样留下，让校验说出“必须是字符串或 null”，而不是悄悄改成 ``null``。
    """
    raw = entry.get("label")
    if raw is None:
        return None
    if not isinstance(raw, str):
        return raw
    return raw if raw.strip() else None


def _canonical_document(document: Mapping[str, Any]) -> dict[str, Any]:
    """返回迁移到当前 schema 的设备表副本：折叠旧角色、为登记设备补生成短标签。

    只改 ``role`` 与 ``label`` 两个字段，其余内容（资产名、序列号、地址、人工补充的字段）
    原样保留。规则：

    1. ``role``：旧槽位折叠成 ``power_supply``，未登记角色写 ``null``，未知角色原样留下
       等校验报错；
    2. ``label``：每条记录都写这个字段（没有标签写 ``null``）；已有标签原样保留，不裁剪、
       不改大小写；**有序列号却没有标签的设备按序列号补生成一个**（不分角色——标签只由
       序列号派生，见 :mod:`domain.instruments.labels`），冲突按同一模块的规则依次加宽，
       全部候选都被占用时抛 :class:`InstrumentRegistryError`。补生成的顺序是电源优先，
       其余按文件顺序：电源的标签就是 ``supply_id``，先挑就先占住首选候选；
    3. 补生成失败分两档：电源没有标签就没有 ``supply_id``，配置与路由会失配，因此一律抛
       :class:`InstrumentRegistryError`；其它角色与未登记角色的设备标签只用于展示，派生
       失败时留 ``null``，显示退回资产名，不阻断保存。

    纯函数：不改入参，也不写盘；由 :meth:`InstrumentRegistry.save` 在内存副本上调用。
    """
    migrated = copy.deepcopy(dict(document))
    entries = migrated.get("instruments")
    if not isinstance(entries, list):
        # 结构错误由 _validate_document 报“instruments 字段必须是数组”，这里不重复判定。
        return migrated
    for entry in entries:
        if isinstance(entry, dict):
            entry["role"] = _declared_entry_role(entry)
            # 已有标签原样冻结，空值才写 ``null``：每条记录都带这个字段，文件自身就能说明
            # “这台还没有标签”。
            entry["label"] = _existing_label(entry)
    # 已有标签一律冻结：迁移只填空缺，不改写、不回收。
    taken = [
        normalize_text(entry.get("label"))
        for entry in entries
        if isinstance(entry, Mapping)
    ]

    def _missing_label_entries():
        """还没有标签、且有序列号可派生的条目：**电源先**，其余按文件顺序。

        电源的标签就是 ``supply_id``，先挑就先占住首选候选，不会被别的设备挤到加宽的形态；
        非电源设备只在必要时让路，它们的标签只用于展示。
        """
        pending = [
            entry
            for entry in entries
            if isinstance(entry, dict) and not normalize_text(entry.get("label"))
        ]
        supplies = [entry for entry in pending if entry.get("role") == POWER_SUPPLY_ROLE]
        others = [entry for entry in pending if entry.get("role") != POWER_SUPPLY_ROLE]
        return [*supplies, *others]

    for entry in _missing_label_entries():
        serial = normalize_serial(entry.get("serial"))
        if not serial:
            # 缺序列号的记录由 _validate_document 报错；没有身份就没有派生标签的依据。
            continue
        name = normalize_text(entry.get("name")) or "(未命名记录)"
        is_power_supply = entry.get("role") == POWER_SUPPLY_ROLE
        try:
            label = generate_instrument_label(serial, taken)
        except InstrumentLabelError as error:
            if not is_power_supply:
                # 非电源的标签只用于展示：派生失败留空，显示退回资产名，不挡住整份保存。
                continue
            raise InstrumentRegistryError(
                f"设备 {name}（序列号 {serial}）无法生成电源短标签：{error}"
            ) from error
        entry["label"] = label
        taken.append(label)
    return migrated


def _to_endpoint(entry: Mapping[str, Any], current_ip: str | None) -> InstrumentEndpoint:
    ip = normalize_text(entry.get("ip"))
    return InstrumentEndpoint(
        ip=ip,
        mac=normalize_text(entry.get("mac")) or None,
        idn_port=entry.get("idn_port"),
        scpi_port=entry.get("scpi_port"),
        visa_address=normalize_text(entry.get("visa_address")) or None,
        is_current=bool(current_ip) and ip == current_ip,
        source=normalize_text(entry.get("source")) or "scan",
        last_seen_at=normalize_text(entry.get("last_seen")) or None,
    )


def _to_instrument(entry: Mapping[str, Any]) -> Instrument:
    current_ip = normalize_text(entry.get("current_ip")) or None
    known_ips = entry.get("known_ips") or []
    endpoints = tuple(
        _to_endpoint(item, current_ip) for item in known_ips if isinstance(item, Mapping)
    )
    return Instrument(
        id=0,
        name=normalize_text(entry.get("name")),
        vendor=normalize_vendor(entry.get("vendor")),
        model=normalize_model(entry.get("model")),
        model_index=int(entry.get("model_index") or 0),
        serial=normalize_serial(entry.get("serial")),
        device_key=_entry_device_key(entry),
        order_no=normalize_text(entry.get("order_no")) or None,
        firmware=normalize_text(entry.get("firmware")) or None,
        options=tuple(normalize_text(option) for option in (entry.get("options") or [])),
        status=DeviceStatus(normalize_text(entry.get("status")) or DeviceStatus.UNKNOWN.value),
        first_seen_at=normalize_text(entry.get("first_seen_at")) or None,
        last_seen_at=normalize_text(entry.get("last_seen_at")) or None,
        notes=normalize_text(entry.get("notes")) or None,
        # 短标签只从注册表读取，不在这里生成：标签由登记/迁移动作写入并冻结。
        label=normalize_text(entry.get("label")) or None,
        # 旧槽位 ``PS1``..``PS4`` 原样读出，谁要写回谁负责用 ``canonical_role`` 折叠；
        # 读取侧不折叠，否则按旧角色找到设备的既有调用（发现面板、配置页）会全部失配。
        role=normalize_role(entry.get("role")),
        endpoints=endpoints,
    )


def _validate_label(value: Any, where: str) -> str | None:
    """校验一个条目的 ``label``，返回查重用的比较键（没有标签时返回 ``None``）。

    标签就是 ``supply_id``，会被拼成 ``supply_id/CHx``，因此：

    - 必须是字符串或 ``null``；``null``、空串与纯空白都表示“还没有标签”；
    - 首尾不得有空白，内部不得有空白字符，也不得含通道分隔符 ``/``，否则路由会把
      ``supply_id`` 截断；
    - 校验按**原字面值**进行，不做 :func:`normalize_text` 之类的清洗：写错的标签必须报错，
      不能被读取或保存悄悄改写成“看起来合法”的值。
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise InstrumentRegistryError(f"{where}.label 必须是字符串或 null，实际 {value!r}")
    label = value.strip()
    if not label:
        return None
    if label != value:
        raise InstrumentRegistryError(
            f"{where}.label={value!r} 不能作为 supply_id：不得含首尾空白"
        )
    if _LABEL_SEPARATOR in label or any(char.isspace() for char in label):
        raise InstrumentRegistryError(
            f"{where}.label={value!r} 不能作为 supply_id：不得含空白或 {_LABEL_SEPARATOR!r}"
        )
    return label.upper()


def _validate_document(document: Mapping[str, Any]) -> None:
    """校验设备表内容：版本门禁、必填字段、唯一性与标签契约。纯函数，不改入参。

    :meth:`InstrumentRegistry.save` 用它校验“将要写盘的副本”，所以迁移、校验与写入之间没有
    缝隙：校验失败时内存文档与原文件都不变。
    """
    declared_version = document.get("schema_version")
    if declared_version is not None:
        if (
            not isinstance(declared_version, int)
            or isinstance(declared_version, bool)
            or declared_version < 1
        ):
            raise InstrumentRegistryError(
                f"设备表 schema_version 必须是正整数，实际 {declared_version!r}"
            )
        if declared_version > SCHEMA_VERSION:
            raise InstrumentRegistryError(
                f"设备表 schema_version={declared_version} 高于本版本支持的 "
                f"{SCHEMA_VERSION}；请升级程序后再读取"
            )
    entries = document.get("instruments")
    if not isinstance(entries, list):
        raise InstrumentRegistryError("设备表的 instruments 字段必须是数组")
    seen_names: dict[str, int] = {}
    seen_keys: dict[str, int] = {}
    seen_slots: dict[tuple[str, int], str] = {}
    seen_labels: dict[str, str] = {}
    for index, entry in enumerate(entries):
        where = f"instruments[{index}]"
        if not isinstance(entry, dict):
            raise InstrumentRegistryError(f"{where} 必须是对象")
        vendor = normalize_vendor(entry.get("vendor"))
        model = normalize_model(entry.get("model"))
        serial = normalize_serial(entry.get("serial"))
        name = normalize_text(entry.get("name"))
        if not vendor:
            raise InstrumentRegistryError(f"{where} 缺少厂商 vendor")
        if not model:
            raise InstrumentRegistryError(f"{where} 缺少型号 model")
        if not serial:
            raise InstrumentRegistryError(f"{where} 缺少序列号 serial")
        if not name:
            raise InstrumentRegistryError(f"{where} 缺少资产名 name")
        raw_index = entry.get("model_index")
        if not isinstance(raw_index, int) or raw_index < 1:
            raise InstrumentRegistryError(f"{where}.model_index 必须是 >= 1 的整数")
        expected_name = build_instrument_name(model, raw_index)
        if name != expected_name:
            raise InstrumentRegistryError(
                f"{where}.name 必须是 {expected_name!r}（型号-同型号序号），实际 {name!r}"
            )
        status = normalize_text(entry.get("status")) or DeviceStatus.UNKNOWN.value
        if status not in _VALID_STATUSES:
            raise InstrumentRegistryError(
                f"{where}.status 必须是 {sorted(_VALID_STATUSES)} 之一，实际 {status!r}"
            )
        # 标签冲突必须阻止保存并报出两台设备，绝不覆盖、也不静默改名。
        label_key = _validate_label(entry.get("label"), where)
        if label_key is not None:
            if label_key in seen_labels:
                raise InstrumentRegistryError(
                    f"标签冲突: {entry.get('label')!r} 同时属于 "
                    f"{seen_labels[label_key]} 与 {name}；电源短标签必须唯一，请人工改名后重试"
                )
            seen_labels[label_key] = name
        raw_role = entry.get("role")
        if raw_role not in (None, "") and normalize_role(raw_role) is None:
            raise InstrumentRegistryError(
                f"{where}.role 必须是 {list(TEST_ROLES)} 之一，实际 {raw_role!r}"
            )
        known_ips = entry.get("known_ips") or []
        if not isinstance(known_ips, list):
            raise InstrumentRegistryError(f"{where}.known_ips 必须是数组")
        current_ip = normalize_text(entry.get("current_ip"))
        if current_ip and current_ip not in {
            normalize_text(item.get("ip")) for item in known_ips if isinstance(item, Mapping)
        }:
            raise InstrumentRegistryError(
                f"{where}.current_ip={current_ip!r} 必须出现在 known_ips 中"
            )
        device_key = make_device_key(vendor, serial)
        if name in seen_names:
            raise InstrumentRegistryError(f"资产名重复: {name}")
        if device_key in seen_keys:
            raise InstrumentRegistryError(
                f"序列号重复: 厂商 {vendor} + 序列号 {serial}（{name} 与 "
                f"{normalize_text(entries[seen_keys[device_key]].get('name'))}）"
            )
        slot = (model, raw_index)
        if slot in seen_slots:
            raise InstrumentRegistryError(
                f"同型号序号重复: {model} 的序号 {raw_index} 同时属于 "
                f"{seen_slots[slot]} 与 {name}"
            )
        seen_names[name] = index
        seen_keys[device_key] = index
        seen_slots[slot] = name


class InstrumentRegistry:
    """设备表门面：加载、校验、查询、写入。"""

    def __init__(self, path: PathLike | None = None, document: dict[str, Any] | None = None) -> None:
        self.path = Path(path) if path is not None else default_registry_path()
        self._document = copy.deepcopy(document) if document is not None else _empty_document()
        self._validate()

    # ---------- 加载与保存 ----------

    @classmethod
    def load(cls, path: PathLike | None = None) -> "InstrumentRegistry":
        """读取设备表；文件不存在时返回空表（不写盘）。"""
        target = Path(path) if path is not None else default_registry_path()
        if not target.exists():
            return cls(path=target)
        payload = read_json(target)
        if not isinstance(payload, dict):
            raise InstrumentRegistryError(f"设备表根节点必须是对象: {target}")
        return cls(path=target, document=payload)

    def save(self) -> Path:
        """迁移为当前 schema 后写入设备表。

        顺序固定为“副本上迁移 → 副本上校验 → 补版本与时间戳 → 原子写入 → 提交回对象”：
        迁移与校验都作用于内存副本，只有写盘成功才替换 :attr:`_document`。因此标签冲突、
        序列号无法派生标签、结构不合法或磁盘写入失败时，内存文档与原文件都保持迁移前的
        样子，原子写入再保证磁盘上不会出现半截文件。
        """
        migrated = _canonical_document(self._document)
        migrated["schema_version"] = SCHEMA_VERSION
        migrated["updated_at"] = utc_now_iso()
        _validate_document(migrated)
        path = write_json_atomic(self.path, migrated)
        self._document = migrated
        return path

    def reload(self) -> None:
        if self.path.exists():
            payload = read_json(self.path)
            if not isinstance(payload, dict):
                raise InstrumentRegistryError(f"设备表根节点必须是对象: {self.path}")
            self._document = payload
        else:
            self._document = _empty_document()
        self._validate()

    @property
    def schema_version(self) -> int:
        return int(self._document.get("schema_version") or SCHEMA_VERSION)

    @property
    def raw(self) -> dict[str, Any]:
        return copy.deepcopy(self._document)

    # ---------- 校验 ----------

    def _validate(self) -> None:
        _validate_document(self._document)

    # ---------- 查询 ----------

    def list(
        self,
        *,
        model: str | None = None,
        status: DeviceStatus | None = None,
    ) -> list[Instrument]:
        """列出设备，可按型号或状态过滤；选件匹配用 :meth:`Instrument.has_options`。"""
        instruments = [_to_instrument(entry) for entry in self._document["instruments"]]
        if model is not None:
            wanted = normalize_model(model)
            instruments = [item for item in instruments if item.model == wanted]
        if status is not None:
            instruments = [item for item in instruments if item.status is status]
        return sorted(instruments, key=lambda item: (item.model, item.model_index))

    def _find_entry(self, predicate: Any) -> dict[str, Any] | None:
        for entry in self._document["instruments"]:
            if predicate(entry):
                return entry
        return None

    def get_by_name(self, name: str) -> Instrument | None:
        entry = self._find_entry(lambda item: normalize_text(item.get("name")) == normalize_text(name))
        return _to_instrument(entry) if entry else None

    def get_by_device_key(self, device_key: str) -> Instrument | None:
        wanted = normalize_text(device_key).casefold()
        entry = self._find_entry(lambda item: _entry_device_key(item) == wanted)
        return _to_instrument(entry) if entry else None

    def get_by_serial(self, serial: str) -> list[Instrument]:
        wanted = normalize_serial(serial)
        return [
            _to_instrument(entry)
            for entry in self._document["instruments"]
            if normalize_serial(entry.get("serial")) == wanted
        ]

    def at_ip(self, ip: str) -> Instrument | None:
        """查当前占用某 IP 的设备（用于识别 IP 复用冲突）。"""
        wanted = normalize_text(ip)
        entry = self._find_entry(lambda item: normalize_text(item.get("current_ip")) == wanted)
        return _to_instrument(entry) if entry else None

    def next_model_index(self, model: str) -> int:
        wanted = normalize_model(model)
        indexes = [
            int(entry["model_index"])
            for entry in self._document["instruments"]
            if normalize_model(entry.get("model")) == wanted
        ]
        return (max(indexes) if indexes else 0) + 1

    # ---------- 写入 ----------

    def upsert(
        self,
        identity: InstrumentIdentity,
        *,
        firmware: str | None = None,
        options: Iterable[str] | None = None,
        status: DeviceStatus = DeviceStatus.ONLINE,
        seen_at: str | None = None,
        notes: str | None = None,
        role: str | None = None,
    ) -> tuple[Instrument, bool]:
        """按 ``(厂商, 序列号)`` 写入或更新设备，返回 ``(设备, 是否新建)``。

        命中已有设备时只刷新可变属性，**不改动资产名与同型号序号**，保证它们永久稳定。

        ``options`` / ``firmware`` / ``order_no`` / ``role`` 采用“有值才覆盖”的语义：
        传 ``None`` 或不传表示本次没有新的值、保留原值，避免一次扫描把已登记的角色清空。
        需要显式清除角色时用 :meth:`set_role` 传 ``None``。

        这里写入的 ``role`` 仍是读取侧认识的取值（旧槽位原样保留，面板还按它工作）；折叠成
        ``power_supply`` 发生在 :meth:`save` 的迁移里，与读取侧共用同一个 ``canonical_role``。
        """
        timestamp = seen_at or utc_now_iso()
        device_key = identity.device_key
        role_value = None if role is None else normalize_role(role)
        if role is not None and role_value is None:
            raise InstrumentRegistryError(
                f"未知的测试角色 {role!r}；必须是 {list(TEST_ROLES)} 之一"
            )
        option_list = (
            None
            if options is None
            else sorted({normalize_text(item) for item in options if normalize_text(item)})
        )
        firmware_text = normalize_text(firmware) or identity.firmware or None

        entry = self._find_entry(lambda item: _entry_device_key(item) == device_key)
        if entry is None:
            model = normalize_model(identity.model)
            model_index = self.next_model_index(model)
            self._document["instruments"].append(
                {
                    "name": build_instrument_name(model, model_index),
                    "vendor": normalize_vendor(identity.vendor),
                    "model": model,
                    "model_index": model_index,
                    "serial": normalize_serial(identity.serial),
                    "label": None,
                    "order_no": identity.order_no,
                    "firmware": firmware_text,
                    "options": option_list or [],
                    "role": role_value,
                    "status": status.value,
                    "current_ip": "",
                    "notes": normalize_text(notes) or None,
                    "first_seen_at": timestamp,
                    "last_seen_at": timestamp,
                    "known_ips": [],
                }
            )
            created = True
        else:
            entry["model"] = normalize_model(identity.model)
            if identity.order_no:
                entry["order_no"] = identity.order_no
            if firmware_text:
                entry["firmware"] = firmware_text
            if option_list is not None:
                entry["options"] = option_list
            if role_value is not None:
                entry["role"] = role_value
            entry["status"] = status.value
            entry["last_seen_at"] = timestamp
            if normalize_text(notes):
                entry["notes"] = normalize_text(notes)
            created = False
        self._validate()
        instrument = self.get_by_device_key(device_key)
        assert instrument is not None
        return instrument, created

    def record_endpoint(
        self,
        instrument: Instrument | str,
        *,
        ip: str,
        mac: str | None = None,
        idn_port: int | None = None,
        scpi_port: int | None = None,
        visa_address: str | None = None,
        source: str = "scan",
        seen_at: str | None = None,
    ) -> Instrument:
        """登记当前 IP，并把旧地址压入 ``known_ips`` 历史（最多 :data:`MAX_KNOWN_IPS` 条）。"""
        name = instrument if isinstance(instrument, str) else instrument.name
        entry = self._find_entry(lambda item: normalize_text(item.get("name")) == normalize_text(name))
        if entry is None:
            raise InstrumentRegistryError(f"设备表中没有资产 {name!r}，无法登记地址")
        ip_text = normalize_text(ip)
        if not ip_text:
            raise InstrumentRegistryError("登记地址需要非空 IP")
        timestamp = seen_at or utc_now_iso()
        mac_text = normalize_text(mac).upper().replace(":", "-") or None
        known_ips: list[dict[str, Any]] = [
            item for item in (entry.get("known_ips") or []) if isinstance(item, dict)
        ]
        existing = next(
            (item for item in known_ips if normalize_text(item.get("ip")) == ip_text), None
        )
        merged = {
            "ip": ip_text,
            "mac": mac_text or (existing or {}).get("mac"),
            "idn_port": idn_port if idn_port is not None else (existing or {}).get("idn_port"),
            "scpi_port": scpi_port if scpi_port is not None else (existing or {}).get("scpi_port"),
            "visa_address": normalize_text(visa_address) or (existing or {}).get("visa_address"),
            "source": normalize_text(source) or "scan",
            "last_seen": timestamp,
        }
        history = [item for item in known_ips if normalize_text(item.get("ip")) != ip_text]
        history.sort(key=lambda item: normalize_text(item.get("last_seen")), reverse=True)
        entry["known_ips"] = [merged] + history[: MAX_KNOWN_IPS - 1]
        entry["current_ip"] = ip_text
        entry["last_seen_at"] = timestamp
        self._validate()
        updated = self.get_by_name(name)
        assert updated is not None
        return updated

    def set_status(self, instrument: Instrument | str, status: DeviceStatus) -> Instrument:
        name = instrument if isinstance(instrument, str) else instrument.name
        entry = self._find_entry(lambda item: normalize_text(item.get("name")) == normalize_text(name))
        if entry is None:
            raise InstrumentRegistryError(f"设备表中没有资产 {name!r}")
        entry["status"] = status.value
        self._validate()
        updated = self.get_by_name(name)
        assert updated is not None
        return updated

    def set_role(self, instrument: Instrument | str, role: str | None) -> Instrument:
        """登记或清除一台设备的固定测试角色。

        角色只由设备表决定：扫描结果、面板与配置写入都读这一个字段，不再有第二处
        “用户选择”的角色来源。``role=None`` 表示这台设备暂时不承担任何测试角色。

        旧槽位 ``PS1``..``PS4`` 仍可作为输入（兼容旧调用），字面量先原样写入内存；
        :meth:`save` 时统一折叠成 ``power_supply``，并为该设备补生成短标签。
        """
        name = instrument if isinstance(instrument, str) else instrument.name
        entry = self._find_entry(lambda item: normalize_text(item.get("name")) == normalize_text(name))
        if entry is None:
            raise InstrumentRegistryError(f"设备表中没有资产 {name!r}")
        if role is None:
            entry.pop("role", None)
        else:
            role_value = normalize_role(role)
            if role_value is None:
                raise InstrumentRegistryError(
                    f"未知的测试角色 {role!r}；必须是 {list(TEST_ROLES)} 之一"
                )
            entry["role"] = role_value
        self._validate()
        updated = self.get_by_name(name)
        assert updated is not None
        return updated


__all__ = [
    "DEFAULT_REGISTRY_FILENAME",
    "InstrumentRegistry",
    "InstrumentRegistryError",
    "LEGACY_SCHEMA_VERSIONS",
    "MAX_KNOWN_IPS",
    "SCHEMA_VERSION",
    "default_registry_path",
    "make_device_key",
    "utc_now_iso",
]
