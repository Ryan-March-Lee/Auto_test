"""仪器注册表领域模型：设备身份由 ``(厂商, 序列号)`` 唯一确定。

核心约定：

- 设备唯一标识是 :func:`make_device_key` 生成的 ``厂商:序列号``，既不是 IP 也不是名称。
  IP 会漂移，名称只是给人看的业务标签，任何识别与去重逻辑都只认序列号。
- ``name`` 的格式是 ``型号-同型号序号``，如 ``DP832A-2``；同型号序号**只增不减、不回收**，
  退役设备保留行与序号，新设备取该型号当前最大序号 + 1。它只是文件里的软件资产名，**不进
  界面**：界面一律显示短标签，同型号两台靠标签区分，不靠后缀数字。
- IP 属于 :class:`InstrumentEndpoint`，一台设备可以有多条历史地址，其中至多一条 ``is_current``。
- 序列号解析兼容 ``订单号/序列号`` 形式（如 R&S 的 ``1412.0000K02/105462``），
  订单号单独存放，不参与唯一标识。
- ``role`` 只表示设备类别（``signal_generator`` / ``spectrum_analyzer`` / ``power_supply``），
  不含“第几台”；多台电源共用 :data:`POWER_SUPPLY_ROLE`，靠各自的短标签区分。
  旧的槽位编号 ``PS1``..``PS4`` 只在读取旧设备表时被识别，读出的字面量原样保留给读取方；
  写回时由 :func:`canonical_role` 折叠成 ``power_supply``，注册表的保存路径已经接上这一折叠。
- ``label`` 是序列号派生的现场短标签，**所有登记设备都有**（不分角色），首次生成后冻结；
  界面显示优先用它（电源的标签同时是配置里的 ``supply_id``），``name`` 只是软件资产名。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final

# 序列号归一化最多应用的轮数：剥引号、折叠空白各占一轮，实测两轮收敛，留两轮余量。
_SERIAL_NORMALIZE_PASSES: Final = 4


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


def _normalize_serial_once(value: Any) -> str:
    """单轮序列号归一化：折叠空白、去掉一层成对引号并统一大写。"""
    text = normalize_text(value).strip().strip('"').strip("'")
    return text.upper()


def normalize_serial(value: Any) -> str:
    """序列号归一化：反复应用单轮归一化直到结果不再变化，避免大小写差异造成重复建档。

    ``*IDN?`` 分段可能同时带引号与空白（``' " dp8b214901519 " '``），单轮只剥掉一层引号，
    所以这里循环到稳定。**这一层是唯一的稳定值来源**：``serial``、``device_key`` 与短标签
    都必须用它，否则同一台设备会得到互不相认的身份与标签。

    内部空白（``DP8B 2149 01519``）是设备身份的一部分，保留不动；短标签生成器据此拒绝
    这类序列号，见 :mod:`domain.instruments.labels`。
    """
    current = _normalize_serial_once(value)
    for _ in range(_SERIAL_NORMALIZE_PASSES - 1):
        following = _normalize_serial_once(current)
        if following == current:
            break
        current = following
    return current


# 角色登记在设备表里：现场每台仪器只承担一种类别，界面不再让用户逐台选择。
# ``role`` 只回答“这是哪一类设备”，不回答“第几台”：多台电源都是 ``power_supply``，
# 靠各自的短标签（:attr:`Instrument.label`）区分，不再需要固定槽位常量。
INSTRUMENT_ROLES = ("signal_generator", "spectrum_analyzer")
POWER_SUPPLY_ROLE = "power_supply"
# 可登记（可写回）的角色取值：新配置与新注册表只允许这些值。
DEVICE_ROLES = INSTRUMENT_ROLES + (POWER_SUPPLY_ROLE,)
# 旧电源槽位编号：只在读取旧设备表时被识别，写回时由 :func:`canonical_role` 折叠成
# ``POWER_SUPPLY_ROLE``。常量保留下来供迁移识别与旧数据兼容，**不再作为新角色集合使用**。
# 这里是固定槽位字面量的**唯一**登记处（读取边界）：其它生产模块不得再写槽位名，
# 也不得用它批量生成电源键——新增电源只按 ``supply_id``（= 短标签）落键，不需要改常量。
POWER_SUPPLY_SLOTS = ("PS1", "PS2", "PS3", "PS4")
LEGACY_ROLES = POWER_SUPPLY_SLOTS
# 归一化可以接受的取值：新类别 + 旧槽位（兼容分支）。
TEST_ROLES = DEVICE_ROLES + LEGACY_ROLES


def normalize_role(value: Any) -> str | None:
    """把设备表的 ``role`` 归一化为 :data:`TEST_ROLES` 之一；空值或未知角色返回 ``None``。

    新类别（:data:`DEVICE_ROLES`）原样通过；旧槽位编号 ``PS1``..``PS4`` 走兼容分支
    也原样返回，好让读取方还能按旧角色找到对应设备。**这里不做折叠**：写回时要用的
    规范取值由 :func:`canonical_role` 提供，两者分开才能既能读旧表又不写回旧值。

    未知角色不在这里猜成某个默认角色：角色决定地址往哪一路写，猜错会把电源接到
    错误的通道上。
    """
    text = normalize_text(value)
    if text in DEVICE_ROLES:
        return text
    if text in LEGACY_ROLES:
        return text
    return None


def canonical_role(value: Any) -> str | None:
    """把可识别的角色折叠为 :data:`DEVICE_ROLES` 之一，供新配置与新注册表写回。

    旧槽位 ``PS1``..``PS4`` 折叠成 :data:`POWER_SUPPLY_ROLE`；``signal_generator`` /
    ``spectrum_analyzer`` / ``power_supply`` 原样返回；空值与未知角色返回 ``None``
    （不猜成电源）。

    本函数是**写回**的唯一折叠入口，纯计算、不落盘：``InstrumentRegistry.save`` 在写盘前调用
    它，把内存里可能还带旧槽位的文档迁移成新字段。
    """
    role = normalize_role(value)
    if role is not None and role in LEGACY_ROLES:
        return POWER_SUPPLY_ROLE
    return role


def is_legacy_power_supply_slot(value: Any) -> bool:
    """``value`` 是否是旧电源槽位编号（``PS1``..``PS4``）。

    只回答“这段文本是旧槽位名”，**绝不**用来生成新的电源键：新配置的电源键是
    ``supply_id``（= 短标签），与槽位序号无关。需要它的场合都在读取边界上，例如判断旧
    配置条目把槽位名填进了 ``model``（那不是型号），好让写回的运行快照留空而不是把编号
    当型号。
    """
    return normalize_text(value) in LEGACY_ROLES


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
    # 这台设备在测试链路里的类别（``signal_generator`` / ``spectrum_analyzer`` /
    # ``power_supply``）；旧注册表里的 ``PS1``..``PS4`` 走兼容分支原样保留，写回时由调用方
    # 用 :func:`canonical_role` 折叠成 ``power_supply``。``None`` 表示尚未登记角色，不能用于绑定。
    role: str | None = None
    endpoints: tuple[InstrumentEndpoint, ...] = field(default_factory=tuple)
    # 现场短标签：由序列号派生，**每台登记设备都有**（不分角色），首次登记时写入注册表并
    # 冻结，不因 IP、型号序号、角色变化或重新扫描而重算。电源的标签同时是配置与运行映射里
    # 的 ``supply_id``；其它设备只用它做展示名。``None`` 表示这台设备还没有标签（序列号派生
    # 失败，或设备表还没保存过），未保存过的新条目也仍是 ``None``。
    #
    # 只能追加在既有字段之后：``Instrument`` 是公开领域模型，位置参数顺序一旦改变，
    # 外部按位置传入 ``role`` / ``endpoints`` 的调用会被静默改写成别的字段。
    label: str | None = None

    @property
    def display_name(self) -> str:
        """现场显示名：有短标签时用短标签，否则退回软件资产名。"""
        return self.label or self.name

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
    "LEGACY_ROLES",
    "POWER_SUPPLY_ROLE",
    "POWER_SUPPLY_SLOTS",
    "TEST_ROLES",
    "build_instrument_name",
    "canonical_role",
    "is_legacy_power_supply_slot",
    "make_device_key",
    "normalize_model",
    "normalize_role",
    "normalize_serial",
    "normalize_text",
    "normalize_vendor",
]
