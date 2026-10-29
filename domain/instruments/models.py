"""仪器注册表领域模型：设备身份由 ``(厂商, 序列号)`` 唯一确定。

核心约定：

- 设备唯一标识是 :func:`make_device_key` 生成的 ``厂商:序列号``，既不是 IP 也不是名称。
  IP 会漂移，名称只是给人看的业务标签，任何识别与去重逻辑都只认序列号。
- ``name`` 的格式是 ``型号-同型号序号``，如 ``DP832A-2``；同型号序号**只增不减、不回收**，
  退役设备保留行与序号，新设备取该型号当前最大序号 + 1。
- IP 属于 :class:`InstrumentEndpoint`，一台设备可以有多条历史地址，其中至多一条 ``is_current``。
- 序列号解析兼容 ``订单号/序列号`` 形式（如 R&S 的 ``1412.0000K02/105462``），
  订单号单独存放，不参与唯一标识。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DeviceStatus(str, Enum):
    """设备在线状态。

    区分 ``UNREACHABLE`` 与 ``OFFLINE`` 是为了避免一次扫描抖动就把资产判成离线：
    前者表示 IP 存在（ARP 有应答）但读不到 ``*IDN?``，通常是 SCPI 会话被占用或仪器正在重启。
    """

    ONLINE = "online"
    UNREACHABLE = "unreachable"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


def normalize_text(value: Any) -> str:
    """折叠首尾与连续空白，用于统一厂商标识和型号文本。"""
    if value is None:
        return ""
    return " ".join(str(value).split())


def normalize_vendor(value: Any) -> str:
    return normalize_text(value)


def normalize_model(value: Any) -> str:
    return normalize_text(value).upper()


def normalize_serial(value: Any) -> str:
    """序列号归一化：去除引号与空白并统一大写，避免大小写差异造成重复建档。"""
    text = normalize_text(value).strip().strip('"').strip("'")
    return text.upper()


# 测试角色登记在设备表里：现场每台仪器只承担一种角色，界面不再让用户逐台选择。
INSTRUMENT_ROLES = ("signal_generator", "spectrum_analyzer")
POWER_SUPPLY_SLOTS = ("PS1", "PS2", "PS3", "PS4")
TEST_ROLES = INSTRUMENT_ROLES + POWER_SUPPLY_SLOTS


def normalize_role(value: Any) -> str | None:
    """把设备表的 ``role`` 归一化为 :data:`TEST_ROLES` 之一；空值或未知角色返回 ``None``。

    未知角色不在这里猜成某个默认角色：角色决定地址往哪一路写，猜错会把电源接到
    错误的通道上。
    """
    text = normalize_text(value)
    return text if text in TEST_ROLES else None


def make_device_key(vendor: Any, serial: Any) -> str:
    """生成设备唯一键 ``厂商:序列号``（小写归一化）。

    厂商不能省略：不同的 ``*IDN?`` 里出现相同序列号是完全可能的。
    """
    vendor_text = normalize_vendor(vendor).casefold()
    serial_text = normalize_serial(serial).casefold()
    if not vendor_text:
        raise ValueError("设备唯一键需要非空厂商标识")
    if not serial_text:
        raise ValueError("设备唯一键需要非空序列号")
    return f"{vendor_text}:{serial_text}"


def build_instrument_name(model: Any, model_index: int) -> str:
    """按 ``型号-序号`` 生成资产名。"""
    model_text = normalize_text(model)
    if not model_text:
        raise ValueError("生成设备名需要非空型号")
    if not isinstance(model_index, int) or model_index < 1:
        raise ValueError("同型号序号必须是 >= 1 的整数")
    return f"{model_text}-{model_index}"


@dataclass(frozen=True)
class InstrumentIdentity:
    """从 ``*IDN?`` 响应解析出的设备身份。"""

    vendor: str
    model: str
    serial: str
    raw_idn: str
    order_no: str | None = None
    firmware: str | None = None

    @property
    def device_key(self) -> str:
        return make_device_key(self.vendor, self.serial)

    @classmethod
    def from_idn(cls, idn: Any) -> "InstrumentIdentity":
        """解析 ``*IDN?`` 响应，至少需要 ``厂商,型号,序列号`` 三段。"""
        text = normalize_text(idn)
        if not text:
            raise ValueError("空的 *IDN? 响应无法识别设备")
        parts = [part.strip() for part in text.split(",")]
        if len(parts) < 3:
            raise ValueError(f"*IDN? 响应字段不足，无法解析: {text!r}")
        vendor, model, serial_field = parts[0], parts[1], parts[2]
        firmware = next((part for part in parts[3:] if part), None)
        order_no: str | None = None
        serial = serial_field
        if "/" in serial_field:
            head, _, tail = serial_field.partition("/")
            if head.strip() and tail.strip():
                order_no, serial = head.strip(), tail.strip()
        if not normalize_vendor(vendor):
            raise ValueError(f"*IDN? 响应缺少厂商标识: {text!r}")
        if not normalize_serial(serial):
            raise ValueError(f"*IDN? 响应缺少序列号: {text!r}")
        return cls(
            vendor=normalize_vendor(vendor),
            model=normalize_model(model),
            serial=normalize_serial(serial),
            raw_idn=text,
            order_no=normalize_text(order_no) or None,
            firmware=normalize_text(firmware) or None,
        )


@dataclass(frozen=True)
class InstrumentEndpoint:
    """设备的一个网络地址别名（IP 只是线索，不是身份）。

    ``is_current`` 不落盘，由设备记录里的 ``current_ip`` 推导，避免两处状态不一致。
    """

    ip: str
    mac: str | None = None
    idn_port: int | None = None
    scpi_port: int | None = None
    visa_address: str | None = None
    is_current: bool = True
    source: str = "scan"
    last_seen_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ip": self.ip,
            "mac": self.mac,
            "idn_port": self.idn_port,
            "scpi_port": self.scpi_port,
            "visa_address": self.visa_address,
            "is_current": self.is_current,
            "source": self.source,
            "last_seen_at": self.last_seen_at,
        }


@dataclass(frozen=True)
class Instrument:
    """一台物理仪器资产。"""

    id: int
    name: str
    vendor: str
    model: str
    model_index: int
    serial: str
    device_key: str
    order_no: str | None = None
    firmware: str | None = None
    options: tuple[str, ...] = field(default_factory=tuple)
    status: DeviceStatus = DeviceStatus.UNKNOWN
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    notes: str | None = None
    # 这台设备在测试链路里的固定角色（``signal_generator`` / ``spectrum_analyzer`` /
    # ``PS1``..``PS4``）；``None`` 表示尚未登记角色，不能用于绑定。
    role: str | None = None
    endpoints: tuple[InstrumentEndpoint, ...] = field(default_factory=tuple)

    @property
    def current_endpoint(self) -> InstrumentEndpoint | None:
        for endpoint in self.endpoints:
            if endpoint.is_current:
                return endpoint
        return None

    @property
    def current_ip(self) -> str | None:
        endpoint = self.current_endpoint
        return endpoint.ip if endpoint else None

    def has_options(self, required: tuple[str, ...] | list[str]) -> bool:
        """判断是否具备全部必需选件（大小写与空白不敏感）。"""
        available = {normalize_text(option).upper() for option in self.options}
        return all(normalize_text(option).upper() in available for option in required)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "vendor": self.vendor,
            "model": self.model,
            "model_index": self.model_index,
            "serial": self.serial,
            "device_key": self.device_key,
            "order_no": self.order_no,
            "firmware": self.firmware,
            "options": list(self.options),
            "status": self.status.value,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
            "notes": self.notes,
            "role": self.role,
            "current_ip": self.current_ip,
            "endpoints": [endpoint.to_dict() for endpoint in self.endpoints],
        }


__all__ = [
    "DeviceStatus",
    "INSTRUMENT_ROLES",
    "Instrument",
    "InstrumentEndpoint",
    "InstrumentIdentity",
    "POWER_SUPPLY_SLOTS",
    "TEST_ROLES",
    "build_instrument_name",
    "make_device_key",
    "normalize_model",
    "normalize_role",
    "normalize_serial",
    "normalize_text",
    "normalize_vendor",
]
