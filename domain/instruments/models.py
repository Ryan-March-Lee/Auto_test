"""仪器注册表领域模型：设备身份由 ``(厂商, 完整设备码)`` 唯一确定。

核心约定：

- 设备唯一标识是 :func:`make_device_key` 生成的 ``厂商:完整设备码``，既不是 IP 也不是名称。
  IP 会漂移，名称只是给人看的显示文本，任何识别与去重逻辑都只认完整设备码。
- ``model`` 是可重复的设备型号；**同一型号可以有多台设备**，不存在“型号-序号”资产名。
  设备之间的区别由缩写设备码 ``label``（契约名 ``short_code``）给出，它由完整设备码派生、
  首次登记后冻结，界面与配置选择都显示“型号（缩写设备码）”。
- IP 属于 :class:`InstrumentEndpoint`，一台设备可以有多条历史地址，其中至多一条 ``is_current``。
- 完整设备码解析兼容 ``订单号/设备码`` 形式（如 R&S 的 ``1412.0000K02/105462``），
  订单号单独存放在 ``order_no``，不参与唯一标识。
- ``role`` 只表示设备类别（``signal_generator`` / ``spectrum_analyzer`` / ``power_supply``），
  不含“第几台”；多台电源共用 :data:`POWER_SUPPLY_ROLE`，靠各自的缩写设备码区分。
  旧槽位编号不再被识别，读取到 ``PS1``..``PS4`` 与未知角色一样返回 ``None``。
- ``label`` 是完整设备码派生的现场缩写设备码（契约名 ``short_code``），**所有登记设备都有**
  （不分角色），首次生成后冻结；界面显示优先用它（电源的缩写码同时是配置里的 ``supply_id``）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final

# 完整设备码归一化最多应用的轮数：剥引号、折叠空白各占一轮，实测两轮收敛，留两轮余量。
_FULL_CODE_NORMALIZE_PASSES: Final = 4


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


def _normalize_full_code_once(value: Any) -> str:
    """单轮完整设备码归一化：折叠空白、去掉一层成对引号并统一大写。"""
    text = normalize_text(value).strip().strip('"').strip("'")
    return text.upper()


def normalize_full_code(value: Any) -> str:
    """完整设备码归一化：反复应用单轮归一化直到结果不再变化，避免大小写差异造成重复建档。

    ``*IDN?`` 分段可能同时带引号与空白（``' " dp8b214901519 " '``），单轮只剥掉一层引号，
    所以这里循环到稳定。**这一层是唯一的稳定值来源**：完整设备码的写入、``device_key``
    与缩写设备码都必须用它，否则同一台设备会得到互不相认的身份与标签。

    内部空白（``DP8B 2149 01519``）是设备身份的一部分，保留不动；缩写设备码生成器据此
    拒绝这类完整设备码，见 :mod:`domain.instruments.labels`。
    """
    current = _normalize_full_code_once(value)
    for _ in range(_FULL_CODE_NORMALIZE_PASSES - 1):
        following = _normalize_full_code_once(current)
        if following == current:
            break
        current = following
    return current


# 角色登记在设备表里：现场每台仪器只承担一种类别，界面不再让用户逐台选择。
# ``role`` 只回答“这是哪一类设备”，不回答“第几台”：多台电源都是 ``power_supply``，
# 靠各自的缩写设备码（:attr:`Instrument.label`）区分，不再需要固定槽位常量。
INSTRUMENT_ROLES = ("signal_generator", "spectrum_analyzer")
POWER_SUPPLY_ROLE = "power_supply"
# 可登记（可写回）的角色取值：新配置与新注册表只允许这些值。
DEVICE_ROLES = INSTRUMENT_ROLES + (POWER_SUPPLY_ROLE,)
# 归一化可以接受的取值与可写回角色一致；旧槽位不再兼容。
TEST_ROLES = DEVICE_ROLES


def normalize_role(value: Any) -> str | None:
    """把设备表的 ``role`` 归一化为 :data:`DEVICE_ROLES` 之一；空值或未知角色返回 ``None``。

    未知角色不在这里猜成某个默认角色：角色决定地址往哪一路写，猜错会把电源接到
    错误的通道上。
    """
    text = normalize_text(value)
    if text in DEVICE_ROLES:
        return text
    return None


def canonical_role(value: Any) -> str | None:
    """把可识别的角色规范化为 :data:`DEVICE_ROLES` 之一，供新配置与新注册表写回。

    空值与未知角色返回 ``None``（不猜成电源）。纯计算、不落盘。
    """
    return normalize_role(value)


def make_device_key(vendor: Any, full_code: Any) -> str:
    """生成设备唯一键 ``厂商:完整设备码``（小写归一化）。

    厂商不能省略：不同的 ``*IDN?`` 里出现相同设备码是完全可能的。
    """
    vendor_text = normalize_vendor(vendor).casefold()
    code_text = normalize_full_code(full_code).casefold()
    if not vendor_text:
        raise ValueError("设备唯一键需要非空厂商标识")
    if not code_text:
        raise ValueError("设备唯一键需要非空完整设备码")
    return f"{vendor_text}:{code_text}"


@dataclass(frozen=True)
class InstrumentIdentity:
    """从 ``*IDN?`` 响应解析出的设备身份。"""

    vendor: str
    model: str
    full_code: str
    raw_idn: str
    order_no: str | None = None
    firmware: str | None = None

    @property
    def device_key(self) -> str:
        return make_device_key(self.vendor, self.full_code)

    @classmethod
    def from_idn(cls, idn: Any) -> "InstrumentIdentity":
        """解析 ``*IDN?`` 响应，至少需要 ``厂商,型号,完整设备码`` 三段。"""
        text = normalize_text(idn)
        if not text:
            raise ValueError("空的 *IDN? 响应无法识别设备")
        parts = [part.strip() for part in text.split(",")]
        if len(parts) < 3:
            raise ValueError(f"*IDN? 响应字段不足，无法解析: {text!r}")
        vendor, model, code_field = parts[0], parts[1], parts[2]
        firmware = next((part for part in parts[3:] if part), None)
        order_no: str | None = None
        full_code = code_field
        if "/" in code_field:
            head, _, tail = code_field.partition("/")
            if head.strip() and tail.strip():
                order_no, full_code = head.strip(), tail.strip()
        if not normalize_vendor(vendor):
            raise ValueError(f"*IDN? 响应缺少厂商标识: {text!r}")
        if not normalize_full_code(full_code):
            raise ValueError(f"*IDN? 响应缺少完整设备码: {text!r}")
        return cls(
            vendor=normalize_vendor(vendor),
            model=normalize_model(model),
            full_code=normalize_full_code(full_code),
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
    vendor: str
    model: str
    full_code: str
    device_key: str
    order_no: str | None = None
    firmware: str | None = None
    options: tuple[str, ...] = field(default_factory=tuple)
    status: DeviceStatus = DeviceStatus.UNKNOWN
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    notes: str | None = None
    # 这台设备在测试链路里的类别（``signal_generator`` / ``spectrum_analyzer`` /
    # ``power_supply``）；旧注册表里的 ``PS1``..``PS4`` 走兼容分支原样保留，写回时由调用方
    # 用 :func:`canonical_role` 折叠成 ``power_supply``。``None`` 表示尚未登记角色，不能用于绑定。
    role: str | None = None
    endpoints: tuple[InstrumentEndpoint, ...] = field(default_factory=tuple)
    # 现场缩写设备码：由完整设备码派生，**每台登记设备都有**（不分角色），首次登记时写入
    # 注册表并冻结，不因 IP、型号、角色变化或重新扫描而重算。电源的缩写码同时是配置与运行
    # 映射里的 ``supply_id``；其它设备只用它做展示名。``None`` 表示这台设备还没有缩写码
    # （完整设备码派生失败，或设备表还没保存过），未保存过的新条目也仍是 ``None``。
    #
    # 只能追加在既有字段之后：``Instrument`` 是公开领域模型，位置参数顺序一旦改变，
    # 外部按位置传入 ``role`` / ``endpoints`` 的调用会被静默改写成别的字段。
    label: str | None = None

    @property
    def display_name(self) -> str:
        """现场显示名：有缩写设备码时用它，否则退回型号，再退回完整设备码。"""
        return self.label or self.model or self.full_code

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
            "vendor": self.vendor,
            "model": self.model,
            "full_code": self.full_code,
            "device_key": self.device_key,
            "label": self.label,
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
    "DEVICE_ROLES",
    "DeviceStatus",
    "INSTRUMENT_ROLES",
    "Instrument",
    "InstrumentEndpoint",
    "InstrumentIdentity",
    "POWER_SUPPLY_ROLE",
    "TEST_ROLES",
    "canonical_role",
    "make_device_key",
    "normalize_full_code",
    "normalize_model",
    "normalize_role",
    "normalize_text",
    "normalize_vendor",
]
