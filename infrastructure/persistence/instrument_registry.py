"""仪器注册表仓储：单文件 JSON，人眼可读、可手改、可 diff。

为什么不用数据库：现场只有个位数仪器、人工点击扫描、一年改几次 IP。
这个负载下 SQLite 的唯一好处是约束由数据库强制，但代价是文件不可读、不可 diff、
必须装工具才能查看。同样的约束在 :meth:`InstrumentRegistry._validate` 里用代码校验即可，
真出现重复会直接报错而不是静默接受。

文件结构（``instruments.json``）::

    {
      "schema_version": 1,
      "updated_at": "2026-10-28T14:39:46+08:00",
      "instruments": [
        {
          "name": "SMW200A-1",          # 资产名 = 型号-同型号序号，永久稳定
          "vendor": "Rohde&Schwarz",
          "model": "SMW200A",
          "model_index": 1,
          "serial": "105462",           # 唯一身份，永不参与匹配的只有 IP 和名称
          "order_no": "1412.0000K02",
          "firmware": "5.20.043.47",
          "options": ["SMW-B9", "SMW-K526"],
          "role": "signal_generator",   # 固定测试角色，界面直接按它写入连接配置
          "status": "online",
          "current_ip": "192.168.1.201",
          "notes": null,
          "first_seen_at": "...",
          "last_seen_at": "...",
          "known_ips": [               # 历史地址，最近在前，最多 MAX_KNOWN_IPS 条
            {"ip": "...", "mac": "...", "idn_port": 5025, "visa_address": "...", "last_seen": "..."}
          ]
        }
      ]
    }

设计约束：

- 设备唯一标识由 ``厂商 + 序列号`` 派生（``device_key``），不落盘，避免两处不一致。
- ``current_ip`` 只是当前连接地址；IP 变化时旧地址进 ``known_ips`` 并保留最近若干条。
- 资产名和同型号序号一经分配不再改动；退役设备保留记录与序号，新设备取最大序号 + 1。
- ``role`` 是这台设备在测试链路里的固定角色（``signal_generator`` / ``spectrum_analyzer`` /
  ``PS1``..``PS4``）。现场每台仪器只承担一种角色，因此角色随设备登记一次，界面不再让
  用户逐台选择；空值与未知角色都视为“未分配角色”，不允许绑定。
- 写入使用 :func:`write_json_atomic`（临时文件 + 原子替换），中断不会留下半截文件。
- 本模块不发起网络探测，只负责读写设备表；扫描逻辑属于后续的发现模块。
"""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from domain.instruments.models import (
    TEST_ROLES,
    DeviceStatus,
    Instrument,
    InstrumentEndpoint,
    InstrumentIdentity,
    build_instrument_name,
    make_device_key,
    normalize_model,
    normalize_role,
    normalize_serial,
    normalize_text,
    normalize_vendor,
)
from infrastructure.filesystem.atomic_json import read_json, write_json_atomic
from infrastructure.filesystem.paths import DEFAULT_PATHS, PathLike

SCHEMA_VERSION = 2
DEFAULT_REGISTRY_FILENAME = "instruments.json"
MAX_KNOWN_IPS = 3

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
        role=normalize_role(entry.get("role")),
        endpoints=endpoints,
    )


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
        self._validate()
        self._document["schema_version"] = SCHEMA_VERSION
        self._document["updated_at"] = utc_now_iso()
        return write_json_atomic(self.path, self._document)

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
        entries = self._document.get("instruments")
        if not isinstance(entries, list):
            raise InstrumentRegistryError("设备表的 instruments 字段必须是数组")
        seen_names: dict[str, int] = {}
        seen_keys: dict[str, int] = {}
        seen_slots: dict[tuple[str, int], str] = {}
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
    "MAX_KNOWN_IPS",
    "SCHEMA_VERSION",
    "default_registry_path",
    "make_device_key",
    "utc_now_iso",
]
