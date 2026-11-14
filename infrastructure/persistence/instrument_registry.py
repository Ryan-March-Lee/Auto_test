"""仪器注册表仓储：单文件 JSON，人眼可读、可手改、可 diff。

为什么不用数据库：现场只有个位数仪器、人工点击扫描、一年改几次 IP。
这个负载下 SQLite 的唯一好处是约束由数据库强制，但代价是文件不可读、不可 diff、
必须装工具才能查看。同样的约束在 :meth:`InstrumentRegistry._validate` 里用代码校验即可，
真出现重复会直接报错而不是静默接受。

文件结构（``instruments.json``）::

    {
      "schema_version": 4,
      "updated_at": "2026-10-28T14:39:46+08:00",
      "instruments": [
        {
          "vendor": "RIGOL TECHNOLOGIES",
          "model": "DP832A",             # 型号可重复，不参与身份
          "full_code": "DP8B214901519",  # 设备返回的完整设备码，唯一身份来源
          "label": "DP8B1519",           # 现场缩写设备码（short_code）＝电源的 supply_id
          "device_key": "厂商:完整设备码"  # 不落盘，读取时由厂商与完整设备码派生
          "order_no": null,              # *IDN? 里的订单号，不并入完整设备码
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

- 设备唯一标识由 ``厂商 + 完整设备码`` 派生（``device_key``），不落盘，避免两处不一致。
  设备查找、去重、绑定与预选只认这个键。
- **型号可重复**：登记表不保存、也不生成“型号-同型号序号”资产名；设备的区别由缩写设备码
  ``label`` 给出。登记、排序与显示都不再使用 ``name`` / ``model_index``。
- ``current_ip`` 只是当前连接地址；IP 变化时旧地址进 ``known_ips`` 并保留最近若干条。
- ``role`` 是这台设备在测试链路里的类别（``signal_generator`` / ``spectrum_analyzer`` /
  ``power_supply``），不含“第几台”：多台电源共用 ``power_supply``，靠各自的缩写设备码
  （``label``）区分。现场每台仪器只承担一种类别，因此角色随设备登记一次，界面不再让用户
  逐台选择；空值与未知角色都视为“未分配角色”，不允许绑定。
- 读写分工：只接受当前 schema（``schema_version == 4``），不再读取旧版本设备表。旧字段
  ``serial`` / ``name`` / ``model_index`` 与旧槽位角色都会被明确拒绝。**保存统一写新字段**，
  写盘前由 :func:`_canonical_document` 补齐 ``label``（规则见
  :mod:`domain.instruments.labels`）；没有缩写码的设备按完整设备码补生成。
- **缩写码对所有登记设备生效**：它只由完整设备码派生，与角色无关，因此换角色、换 IP、改
  型号都不会换码。界面上凡是显示这台设备，都用“型号（缩写设备码）”，例如
  ``DP832A（DP8B1519）``、``SMW200A（5462）``。
- 补生成缩写码的失败处理分两档：**电源**没有缩写码就没有 ``supply_id``，配置与路由会失配，
  因此派生失败一律阻止保存并指出是哪台设备；**其它角色与未登记角色的设备**缩写码只用于
  展示，派生失败时留空（显示退回型号），不阻断保存。
- **已有缩写码原样保留**：迁移只填空缺，不规范化、不裁剪空白、不改大小写；缩写码必须能当
  ``supply_id`` 用（非空字符串、不含空白与 ``/``），否则读取/保存直接报错，交给操作员
  改正——迁移不许把写错的缩写码悄悄“洗干净”。``null``、空串与纯空白都表示“还没有缩写码”，
  保存时统一写 ``null``。
- 缩写码冲突阻止保存并报出冲突的两台设备，绝不覆盖、也不静默改名；电源无法从完整设备码
  派生缩写码时同样阻止保存并指出是哪台设备。
- 写入使用 :func:`write_json_atomic`（临时文件 + 原子替换），中断不会留下半截文件；
  迁移、校验、``schema_version`` 与 ``updated_at`` 的更新都在内存副本上完成，写盘成功后才
  提交回对象，因此失败时内存文档与原文件都保持迁移前的样子。
- 本模块不发起网络探测，只负责读写设备表；扫描逻辑属于发现模块。
"""

from __future__ import annotations

import copy
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
    canonical_role,
    make_device_key,
    normalize_full_code,
    normalize_model,
    normalize_role,
    normalize_text,
    normalize_vendor,
)
from infrastructure.filesystem.atomic_json import read_json, write_json_atomic
from infrastructure.filesystem.paths import DEFAULT_PATHS, PathLike

SCHEMA_VERSION = 4
DEFAULT_REGISTRY_FILENAME = "instruments.json"
MAX_KNOWN_IPS = 3

# 缩写码同时是 ``supply_id``，会被拼成 ``supply_id/CHx``；含空白或 ``/`` 的缩写码会在路由处被截断。
_LABEL_SEPARATOR = "/"

_VALID_STATUSES = {status.value for status in DeviceStatus}


class InstrumentRegistryError(ValueError):
    """设备表内容不合法（字段缺失、重复完整设备码、缩写码冲突等）。"""


def utc_now_iso() -> str:
    """带本地时区偏移的 ISO 时间戳，便于人工阅读。"""
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def default_registry_path() -> Path:
    return DEFAULT_PATHS.instruments_file


def _empty_document() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "updated_at": utc_now_iso(), "instruments": []}


def _entry_full_code(entry: Mapping[str, Any]) -> str:
    """读取条目的完整设备码；只认当前字段 ``full_code``。"""
    return normalize_full_code(entry.get("full_code"))


def _entry_device_key(entry: Mapping[str, Any]) -> str:
    return make_device_key(entry.get("vendor"), _entry_full_code(entry))


def _declared_entry_role(entry: Mapping[str, Any]) -> str | None:
    """设备表条目写回时的 ``role`` 取值。

    只接受当前类别；未登记角色写 ``None``；**认不出来的角色原样返回**，交给
    :func:`_validate_document` 报错——迁移不能顺手把写错的角色丢掉，否则手改 JSON 的错误
    会静默消失。
    """
    raw = entry.get("role")
    role = canonical_role(raw)
    if role is not None:
        return role
    return None if not normalize_text(raw) else raw


def _existing_label(entry: Mapping[str, Any]) -> Any:
    """条目里已有的缩写码，**原样**返回；``null``、空串与纯空白表示“还没有缩写码”。

    这里刻意不做 :func:`normalize_text`：缩写码是人工可见的现场短码，迁移的任务只是补空缺。
    裁剪空白或统一大小写会让文件里的值与操作员写的值不一致，也会掩盖“缩写码不能当
    ``supply_id``”这类错误；含空白与 ``/`` 的缩写码直接由 :func:`_validate_document` 报错。
    类型写错的值同样原样留下，让校验说出“必须是字符串或 null”，而不是悄悄改成 ``null``。
    """
    raw = entry.get("label")
    if raw is None:
        return None
    if not isinstance(raw, str):
        return raw
    return raw if raw.strip() else None


def _canonical_document(document: Mapping[str, Any]) -> dict[str, Any]:
    """返回保存到当前 schema 的设备表副本：规范角色、补生成缩写码。

    只改身份相关字段（``role`` / ``label``），其余内容（地址、选件、备注、未知字段）原样
    保留。规则：

    1. ``role``：未登记角色写 ``null``，未知角色原样留下等校验报错；
    2. ``label``：每条记录都写这个字段（没有缩写码写 ``null``）；已有缩写码原样保留，
       不裁剪、不改大小写；**有完整设备码却没有缩写码的设备按完整设备码补生成一个**
       （不分角色——缩写码只由完整设备码派生），冲突按同一模块的规则依次加宽，全部候选
       都被占用时抛 :class:`InstrumentRegistryError`。补生成的顺序是电源优先，其余按文件
       顺序：电源的缩写码就是 ``supply_id``，先挑就先占住首选候选；
    3. 补生成失败分两档：电源没有缩写码就没有 ``supply_id``，配置与路由会失配，因此一律抛
       :class:`InstrumentRegistryError`；其它角色与未登记角色的设备缩写码只用于展示，派生
       失败时留 ``null``，显示退回型号，不阻断保存。

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
            # 已有缩写码原样冻结，空值才写 ``null``：每条记录都带这个字段，文件自身就能说明
            # “这台还没有缩写码”。
            entry["label"] = _existing_label(entry)
    # 已有缩写码一律冻结：迁移只填空缺，不改写、不回收。
    taken = [
        normalize_text(entry.get("label"))
        for entry in entries
        if isinstance(entry, Mapping)
    ]

    def _missing_label_entries():
        """还没有缩写码、且有完整设备码可派生的条目：**电源先**，其余按文件顺序。

        电源的缩写码就是 ``supply_id``，先挑就先占住首选候选，不会被别的设备挤到加宽的形态；
        非电源设备只在必要时让路，它们的缩写码只用于展示。
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
        full_code = _entry_full_code(entry)
        if not full_code:
            # 缺完整设备码的记录由 _validate_document 报错；没有身份就没有派生缩写码的依据。
            continue
        vendor_hint = normalize_text(entry.get("vendor")) or "(未填厂商)"
        model_hint = normalize_text(entry.get("model")) or "(无型号)"
        is_power_supply = entry.get("role") == POWER_SUPPLY_ROLE
        try:
            label = generate_instrument_label(full_code, taken)
        except InstrumentLabelError as error:
            if not is_power_supply:
                # 非电源的缩写码只用于展示：派生失败留空，显示退回型号，不挡住整份保存。
                continue
            raise InstrumentRegistryError(
                f"设备 {vendor_hint} {model_hint}（完整设备码 {full_code}）"
                f"无法生成电源缩写码：{error}"
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
        vendor=normalize_vendor(entry.get("vendor")),
        model=normalize_model(entry.get("model")),
        full_code=_entry_full_code(entry),
        device_key=_entry_device_key(entry),
        order_no=normalize_text(entry.get("order_no")) or None,
        firmware=normalize_text(entry.get("firmware")) or None,
        options=tuple(normalize_text(option) for option in (entry.get("options") or [])),
        status=DeviceStatus(normalize_text(entry.get("status")) or DeviceStatus.UNKNOWN.value),
        first_seen_at=normalize_text(entry.get("first_seen_at")) or None,
        last_seen_at=normalize_text(entry.get("last_seen_at")) or None,
        notes=normalize_text(entry.get("notes")) or None,
        # 缩写码只从注册表读取，不在这里生成：缩写码由登记/迁移动作写入并冻结。
        label=normalize_text(entry.get("label")) or None,
        # 旧槽位 ``PS1``..``PS4`` 原样读出，谁要写回谁负责用 ``canonical_role`` 折叠；
        # 读取侧不折叠，否则按旧角色找到设备的既有调用（发现面板、配置页）会全部失配。
        role=normalize_role(entry.get("role")),
        endpoints=endpoints,
    )


def _validate_label(value: Any, where: str) -> str | None:
    """校验一个条目的 ``label``，返回查重用的比较键（没有缩写码时返回 ``None``）。

    缩写码就是 ``supply_id``，会被拼成 ``supply_id/CHx``，因此：

    - 必须是字符串或 ``null``；``null``、空串与纯空白都表示“还没有缩写码”；
    - 首尾不得有空白，内部不得有空白字符，也不得含通道分隔符 ``/``，否则路由会把
      ``supply_id`` 截断；
    - 校验按**原字面值**进行，不做 :func:`normalize_text` 之类的清洗：写错的缩写码必须报错，
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
    """校验设备表内容：版本门禁、必填字段、唯一性与缩写码契约。纯函数，不改入参。

    :meth:`InstrumentRegistry.save` 用它校验“将要写盘的副本”，所以迁移、校验与写入之间没有
    缝隙：校验失败时内存文档与原文件都不变。

    **型号可以重复**：校验不要求 ``name`` / ``model_index``，也不按型号分配序号；设备唯一性
    只由 ``device_key`` 回答，用户可见的稳定性只由全局唯一的 ``label`` 回答。
    """
    declared_version = document.get("schema_version")
    if (
        not isinstance(declared_version, int)
        or isinstance(declared_version, bool)
    ):
        raise InstrumentRegistryError(
            f"设备表缺少有效的 schema_version，实际 {declared_version!r}"
        )
    if declared_version != SCHEMA_VERSION:
        raise InstrumentRegistryError(
            f"设备表 schema_version={declared_version} 不受支持；"
            f"当前只接受 {SCHEMA_VERSION}，请重新扫描并保存设备表"
        )
    entries = document.get("instruments")
    if not isinstance(entries, list):
        raise InstrumentRegistryError("设备表的 instruments 字段必须是数组")
    seen_keys: dict[str, int] = {}
    seen_labels: dict[str, str] = {}

    def describe(index: int, entry: Mapping[str, Any]) -> str:
        """冲突消息里的设备称呼：型号 + 缩写码或完整设备码，不使用已退役的资产名。"""
        model_text = normalize_text(entry.get("model")) or "(无型号)"
        code_text = (
            normalize_text(entry.get("label"))
            or _entry_full_code(entry)
            or f"instruments[{index}]"
        )
        return f"{model_text}（{code_text}）"

    for index, entry in enumerate(entries):
        where = f"instruments[{index}]"
        if not isinstance(entry, dict):
            raise InstrumentRegistryError(f"{where} 必须是对象")
        vendor = normalize_vendor(entry.get("vendor"))
        model = normalize_model(entry.get("model"))
        full_code = _entry_full_code(entry)
        if not vendor:
            raise InstrumentRegistryError(f"{where} 缺少厂商 vendor")
        if not model:
            raise InstrumentRegistryError(f"{where} 缺少型号 model")
        if not full_code:
            raise InstrumentRegistryError(f"{where} 缺少完整设备码 full_code")
        if normalize_text(entry.get("serial")):
            raise InstrumentRegistryError(
                f"{where} 使用了已退役字段 serial；完整设备码必须写在 full_code"
            )
        if normalize_text(entry.get("name")) or entry.get("model_index") is not None:
            raise InstrumentRegistryError(
                f"{where} 使用了已退役的资产名字段 name/model_index；设备身份只由 "
                "full_code 与 label 表达"
            )
        status = normalize_text(entry.get("status")) or DeviceStatus.UNKNOWN.value
        if status not in _VALID_STATUSES:
            raise InstrumentRegistryError(
                f"{where}.status 必须是 {sorted(_VALID_STATUSES)} 之一，实际 {status!r}"
            )
        # 缩写码冲突必须阻止保存并报出两台设备，绝不覆盖、也不静默改名。
        label_key = _validate_label(entry.get("label"), where)
        if label_key is not None:
            if label_key in seen_labels:
                raise InstrumentRegistryError(
                    f"缩写设备码冲突: {entry.get('label')!r} 同时属于 "
                    f"{seen_labels[label_key]} 与 {describe(index, entry)}；"
                    "缩写设备码必须唯一，请人工改名后重试"
                )
            seen_labels[label_key] = describe(index, entry)
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
        device_key = make_device_key(vendor, full_code)
        if device_key in seen_keys:
            raise InstrumentRegistryError(
                f"完整设备码重复: 厂商 {vendor} + 完整设备码 {full_code}"
                f"（{describe(seen_keys[device_key], entries[seen_keys[device_key]])} 与 "
                f"{describe(index, entry)}）"
            )
        seen_keys[device_key] = index


class InstrumentRegistry:
    """设备表门面：加载、校验、查询、写入。"""

    def __init__(self, path: PathLike | None = None, document: dict[str, Any] | None = None) -> None:
        self.path = Path(path) if path is not None else default_registry_path()
        source = copy.deepcopy(document) if document is not None else _empty_document()
        self._document = source
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
        迁移与校验都作用于内存副本，只有写盘成功才替换 :attr:`_document`。因此缩写码冲突、
        完整设备码无法派生缩写码、结构不合法或磁盘写入失败时，内存文档与原文件都保持迁移前
        的样子，原子写入再保证磁盘上不会出现半截文件。
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
        """列出设备，可按型号或状态过滤；选件匹配用 :meth:`Instrument.has_options`。

        排序只使用型号与缩写设备码，不再有“同型号序号”；相同型号的多台设备按各自的缩写码
        稳定排列。
        """
        instruments = [_to_instrument(entry) for entry in self._document["instruments"]]
        if model is not None:
            wanted = normalize_model(model)
            instruments = [item for item in instruments if item.model == wanted]
        if status is not None:
            instruments = [item for item in instruments if item.status is status]
        return sorted(
            instruments,
            key=lambda item: (item.model, item.label or "", item.full_code),
        )

    def _find_entry(self, predicate: Any) -> dict[str, Any] | None:
        for entry in self._document["instruments"]:
            if predicate(entry):
                return entry
        return None

    def get_by_device_key(self, device_key: str) -> Instrument | None:
        """按系统匹配键查找设备；这是生产代码唯一的设备查找入口。"""
        wanted = normalize_text(device_key).casefold()
        entry = self._find_entry(lambda item: _entry_device_key(item) == wanted)
        return _to_instrument(entry) if entry else None

    def get_by_full_code(self, full_code: str) -> list[Instrument]:
        """按完整设备码查设备（可能跨厂商命中多台，因此返回列表）。"""
        wanted = normalize_full_code(full_code)
        return [
            _to_instrument(entry)
            for entry in self._document["instruments"]
            if _entry_full_code(entry) == wanted
        ]

    def at_ip(self, ip: str) -> Instrument | None:
        """查当前占用某 IP 的设备（用于识别 IP 复用冲突）。"""
        wanted = normalize_text(ip)
        entry = self._find_entry(lambda item: normalize_text(item.get("current_ip")) == wanted)
        return _to_instrument(entry) if entry else None

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
        """按 ``(厂商, 完整设备码)`` 写入或更新设备，返回 ``(设备, 是否新建)``。

        命中已有设备时只刷新可变属性，**不改动缩写设备码**，保证它永久稳定；型号可以随设备
        应答更新，型号重复不影响身份。

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
            self._document["instruments"].append(
                {
                    "vendor": normalize_vendor(identity.vendor),
                    "model": normalize_model(identity.model),
                    "full_code": normalize_full_code(identity.full_code),
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

    @staticmethod
    def _require_device_key(instrument: Instrument | str) -> str:
        """把“设备对象或匹配键”统一成 ``device_key``。

        设备查找只允许使用 ``device_key``：型号、缩写码、显示文本与 IP 都不是身份，按它们
        查找会把两台同型号设备或换过地址的设备混在一起。
        """
        if isinstance(instrument, Instrument):
            return instrument.device_key
        key = normalize_text(instrument).casefold()
        if not key:
            raise InstrumentRegistryError("设备查找需要非空 device_key")
        return key

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
        device_key = self._require_device_key(instrument)
        entry = self._find_entry(lambda item: _entry_device_key(item) == device_key)
        if entry is None:
            raise InstrumentRegistryError(
                f"设备表中没有 device_key={device_key!r} 的设备，无法登记地址"
            )
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
        updated = self.get_by_device_key(device_key)
        assert updated is not None
        return updated

    def set_status(self, instrument: Instrument | str, status: DeviceStatus) -> Instrument:
        device_key = self._require_device_key(instrument)
        entry = self._find_entry(lambda item: _entry_device_key(item) == device_key)
        if entry is None:
            raise InstrumentRegistryError(f"设备表中没有 device_key={device_key!r} 的设备")
        entry["status"] = status.value
        self._validate()
        updated = self.get_by_device_key(device_key)
        assert updated is not None
        return updated

    def set_role(self, instrument: Instrument | str, role: str | None) -> Instrument:
        """登记或清除一台设备的固定测试角色。

        角色只由设备表决定：扫描结果、面板与配置写入都读这一个字段，不再有第二处
        “用户选择”的角色来源。``role=None`` 表示这台设备暂时不承担任何测试角色。

        旧槽位 ``PS1``..``PS4`` 仍可作为输入（兼容旧调用），字面量先原样写入内存；
        :meth:`save` 时统一折叠成 ``power_supply``，并为该设备补生成缩写设备码。
        """
        device_key = self._require_device_key(instrument)
        entry = self._find_entry(lambda item: _entry_device_key(item) == device_key)
        if entry is None:
            raise InstrumentRegistryError(f"设备表中没有 device_key={device_key!r} 的设备")
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
        updated = self.get_by_device_key(device_key)
        assert updated is not None
        return updated


__all__ = [
    "DEFAULT_REGISTRY_FILENAME",
    "InstrumentRegistry",
    "InstrumentRegistryError",
    "MAX_KNOWN_IPS",
    "SCHEMA_VERSION",
    "default_registry_path",
    "make_device_key",
    "utc_now_iso",
]
