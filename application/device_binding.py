"""设备选择与设备绑定的唯一写入边界（GUI 重构步骤 5）。

归属：application。配置页只提交“本次勾选结果”，
本服务负责：

- 角色唯一性、电源引用可用性、地址/身份完整性、同一台设备不重复绑定与整批替换
  （R14–R16）；
- 绑定记录的读取边界身份核对（R13 的记录级判定）：记录里声明出的厂商、完整设备码
  必须与 ``device_key`` 一致；
- 生成新的 ``device_bindings`` 集合与结构化的 :class:`ValidationResult`。

服务不读控件、不写文件、不创建仪器，也不更新任何 Qt 控件：卡片选中状态、面板文案与
电源标签页刷新都属于 presentation。用户输入失败通过 :class:`DeviceBindingResult` 返回，
不使用异常；而**调用方契约**错误（请求不是命令 DTO、设备记录不是对象）属于程序错误，
按 :class:`TypeError` 抛出，不伪装成用户可修正的问题。

绑定结构：信号源与频谱仪用角色键（``device_bindings.signal_generator`` /
``spectrum_analyzer``），电源用 ``device_bindings.power_supplies[<supply_id>]``。应用是
**整批替换**：本次勾选结果就是完整的设备绑定，没勾选的旧角色与旧电源会被移除。

身份语义仍归 ``domain.instruments``（``make_device_key`` 与归一化）；``domain`` 只回答
“什么算同一个身份”，这里判定的是“这一条记录的身份是否自洽”。
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from application.configuration_editing import ValidationIssue, ValidationResult
from domain.instruments.models import (
    DEVICE_ROLES,
    POWER_SUPPLY_ROLE,
    make_device_key,
    normalize_full_code,
    normalize_vendor,
)

__all__ = [
    "AppliedDeviceBinding",
    "ApplyDeviceSelectionRequest",
    "DeviceBindingResult",
    "DeviceBindingService",
    "LEGACY_BINDING_SOURCE",
    "POWER_SUPPLY_BINDINGS",
    "SelectedDevice",
    "SUPPLY_ID_SEPARATOR",
    "binding_device_key",
    "binding_identity_problem",
    "legacy_binding_count",
    "supply_id_problem",
]

# ``device_bindings`` 里的电源集合：键是 ``supply_id``（= 设备短标签），值是绑定记录。
# 电源是同一类别下的多台设备，不能像信号源那样用角色键存放——否则第二台会覆盖第一台。
POWER_SUPPLY_BINDINGS = "power_supplies"
# 绑定记录的来源标记：地址来自已保存的配置、尚未被设备发现确认。表现层只做展示
# （扫描框初值与“未扫描确认”提示），不另写一份字面量；写入规则仍只在本模块。
LEGACY_BINDING_SOURCE = "legacy_config"
# 电源引用会拼成 ``supply_id/CHx`` 交给 ``instrument.measurement_factory`` 路由，因此
# ``supply_id`` 与注册表里的 ``label`` 受同一套约束：不能含空白和通道分隔符。
SUPPLY_ID_SEPARATOR = "/"

# 可写绑定的角色集合来自 domain 的词汇表：这一层只回答“这一批能不能写”，不另立第二份
# 角色清单，否则新增/重命名角色时两处会分叉。
_VALID_ROLES = frozenset(DEVICE_ROLES)

# 问题的稳定错误码：文案可以改，错误码不随文案变化。
_CODE_EMPTY_SELECTION = "device_binding.empty_selection"
_CODE_UNKNOWN_ROLE = "device_binding.unknown_role"
_CODE_MISSING_REFERENCE = "device_binding.missing_reference"
_CODE_INVALID_REFERENCE = "device_binding.invalid_reference"
_CODE_DUPLICATE_REFERENCE = "device_binding.duplicate_reference"
_CODE_DUPLICATE_ROLE = "device_binding.duplicate_role"
_CODE_MISSING_ADDRESS = "device_binding.missing_address"
_CODE_MISSING_IDENTITY = "device_binding.missing_identity"
_CODE_UNPARSEABLE_IDENTITY = "device_binding.unparseable_identity"
_CODE_IDENTITY_MISMATCH = "device_binding.identity_mismatch"
_CODE_MISSING_POWER_IDENTITY = "device_binding.missing_power_identity"
_CODE_DUPLICATE_DEVICE = "device_binding.duplicate_device"


def _mapping_sequence(values: Any, label: str) -> tuple[Mapping[str, Any], ...]:
    """把调用方交付的发现结果收敛为受控 tuple。

    条目不是对象属于**调用方契约错误**（发现面板只交付字典记录），因此按程序错误抛出
    :class:`TypeError` 并指出位置，不收敛成空记录——那会把程序 bug 伪装成“未知的测试
    角色: None”这类用户可修正的问题。
    """
    if isinstance(values, (str, bytes, Mapping)) or not isinstance(values, Iterable):
        raise TypeError(f"{label} 必须是设备记录序列，收到 {type(values).__name__}")
    items = tuple(values)
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise TypeError(
                f"{label}[{index}] 必须是设备记录对象，收到 {type(item).__name__}"
            )
    return items


@dataclass(frozen=True)
class SelectedDevice:
    """本次勾选的一台设备（发现面板交付的字段快照）。

    字段全部按发现结果的原始读数保留：服务只回答“这一批能不能写、写成什么”，不在
    这里按型号或地址猜设备。``supply_id`` / ``label`` 是电源的配置引用；``device_key``
    （``厂商:完整设备码``）是身份，``address`` 只是本次连接地址。
    """

    role: Any = None
    address: Any = None
    vendor: Any = None
    model: Any = None
    label: Any = None
    supply_id: Any = None
    full_code: Any = None
    device_key: Any = None
    ip: Any = None
    port: Any = None
    known: Any = False
    simulated: Any = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SelectedDevice":
        """按发现面板的字段名读取一条设备；非对象条目属于调用方契约错误。"""
        if not isinstance(value, Mapping):
            raise TypeError(
                f"设备记录必须是对象，收到 {type(value).__name__}"
            )
        return cls(
            role=value.get("role"),
            address=value.get("address"),
            vendor=value.get("vendor"),
            model=value.get("model"),
            label=value.get("label"),
            supply_id=value.get("supply_id"),
            full_code=value.get("full_code"),
            device_key=value.get("device_key"),
            ip=value.get("ip"),
            port=value.get("port"),
            known=value.get("known"),
            simulated=value.get("simulated"),
        )

    @property
    def reference(self) -> str:
        """电源的配置引用 ID（``supply_id`` 优先，回退 ``label``）；不可用时为空串。"""
        for value in (self.supply_id, self.label):
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""


@dataclass(frozen=True)
class ApplyDeviceSelectionRequest:
    """“应用本次勾选结果”的明确命令 DTO。

    ``devices`` 是面板交付顺序的快照；服务不读控件、不回查面板，整批替换只依赖它。
    """

    devices: tuple[SelectedDevice, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.devices, (str, bytes, Mapping)) or not isinstance(
            self.devices, Iterable
        ):
            raise TypeError(
                "devices 必须是 SelectedDevice 序列，"
                f"收到 {type(self.devices).__name__}"
            )
        devices = tuple(self.devices)
        for index, device in enumerate(devices):
            if not isinstance(device, SelectedDevice):
                raise TypeError(
                    f"devices[{index}] 必须是 SelectedDevice，"
                    f"收到 {type(device).__name__}"
                )
        object.__setattr__(self, "devices", devices)

    @classmethod
    def from_assignments(
        cls, assignments: Iterable[Mapping[str, Any]] | None
    ) -> "ApplyDeviceSelectionRequest":
        """把发现面板交付的字典列表收敛为命令；``None`` 与空序列等价。

        非对象条目按调用方契约错误抛出 :class:`TypeError`，与 ``devices`` 的元素类型检查
        保持一致：同一个列表直接构造请求会失败，经这里也不能被悄悄转成空记录。
        """
        if assignments is None:
            return cls()
        return cls(
            devices=tuple(
                SelectedDevice.from_mapping(item)
                for item in _mapping_sequence(assignments, "发现结果")
            )
        )


@dataclass(frozen=True)
class AppliedDeviceBinding:
    """一条成功写入的绑定，只用于显示成功摘要；业务值在绑定集合里。"""

    role: str
    supply_id: str | None = None
    model: Any = None
    full_code: Any = None
    ip: Any = None


@dataclass(frozen=True)
class DeviceBindingResult:
    """一次“应用设备”的结果：新的绑定集合 + 结构化问题。

    ``ok`` 为真时 ``bindings`` 是完整的 ``device_bindings`` 结构（整批替换的结果）；
    为假时 ``bindings`` 为空，调用方必须保持原绑定不变——服务不返回半份配置。
    ``applied`` 是成功写入的显示摘要，``simulated`` 标记这一批是否含模拟数据。
    """

    bindings: Mapping[str, Any] = field(default_factory=dict)
    applied: tuple[AppliedDeviceBinding, ...] = ()
    validation: ValidationResult = field(default_factory=ValidationResult)
    simulated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.bindings, Mapping):
            raise TypeError(
                "bindings 必须是绑定对象，"
                f"收到 {type(self.bindings).__name__}"
            )
        if not isinstance(self.validation, ValidationResult):
            raise TypeError(
                "validation 必须是 ValidationResult，"
                f"收到 {type(self.validation).__name__}"
            )
        applied = tuple(self.applied)
        for index, item in enumerate(applied):
            if not isinstance(item, AppliedDeviceBinding):
                raise TypeError(
                    f"applied[{index}] 必须是 AppliedDeviceBinding，"
                    f"收到 {type(item).__name__}"
                )
        # 输入快照：调用方之后改写原绑定字典不会改变已经返回的结果。
        object.__setattr__(self, "bindings", copy.deepcopy(dict(self.bindings)))
        object.__setattr__(self, "applied", applied)
        object.__setattr__(self, "simulated", bool(self.simulated))

    @property
    def ok(self) -> bool:
        """整批写入成功（没有错误级问题）。"""
        return self.validation.valid

    @property
    def issues(self) -> tuple[ValidationIssue, ...]:
        return self.validation.issues

    def messages(self) -> tuple[str, ...]:
        return self.validation.messages()

    def to_mapping(self) -> dict[str, Any]:
        """输出绑定集合的可写副本；调用方不能通过返回值改写结果内部状态。"""
        return copy.deepcopy(dict(self.bindings))


class DeviceBindingService:
    """设备绑定的唯一应用服务：校验整批选择并生成新的绑定集合。

    服务无状态，调用方按需构造即可。失败时返回第一条问题（整批拒绝），不写入任何绑定。
    """

    def apply(self, request: ApplyDeviceSelectionRequest) -> DeviceBindingResult:
        """应用一次设备选择，返回新的绑定集合或结构化问题。"""
        if not isinstance(request, ApplyDeviceSelectionRequest):
            raise TypeError(
                "request 必须是 ApplyDeviceSelectionRequest，"
                f"收到 {type(request).__name__}"
            )
        devices = request.devices
        if not devices:
            return _reject(
                "device_bindings",
                _CODE_EMPTY_SELECTION,
                "请先在发现结果中勾选本次测试所需设备",
            )

        # 第一遍只看角色与电源引用：未知角色、唯一角色重复、电源没有可用的引用 ID 是
        # “这一批要做什么”的问题，先报出来，避免用户看到的是地址/完整设备码之类更细节的错。
        used_roles: dict[str, str] = {}
        supply_ids: dict[str, str] = {}
        for device in devices:
            role = device.role
            if role not in _VALID_ROLES:
                return _reject(
                    "device_bindings.role",
                    _CODE_UNKNOWN_ROLE,
                    f"未知的测试角色: {role}",
                )
            if role == POWER_SUPPLY_ROLE:
                # 电源是类别不是编号：多台各自独立，这里只判定引用 ID 可用且唯一。
                supply_id = device.reference
                if not supply_id:
                    return _reject(
                        "device_bindings.power_supplies",
                        _CODE_MISSING_REFERENCE,
                        f"电源 {device.model or device.ip} 还没有缩写设备码"
                        "（label/supply_id），没有可写的配置引用；请先在设备表里保存一次为它"
                        "补上缩写码，再重新扫描",
                    )
                reason = supply_id_problem(supply_id)
                if reason:
                    # 引用会拼成 ``supply_id/CHx``：含空白或 ``/`` 的标签写进配置后
                    # 路由会截断，宁可整批拒绝并指出要修的标签，也不悄悄改名。
                    return _reject(
                        f"device_bindings.power_supplies.{supply_id}",
                        _CODE_INVALID_REFERENCE,
                        f"电源缩写设备码 {supply_id!r} 不能作为配置引用（{reason}）；"
                        "请先在设备表里修正该缩写码并保存，再重新扫描",
                    )
                if supply_id in supply_ids:
                    return _reject(
                        f"device_bindings.power_supplies.{supply_id}",
                        _CODE_DUPLICATE_REFERENCE,
                        f"本次应用有两行发现结果都指向电源 {supply_id}"
                        f"（{supply_ids[supply_id]} 与 {device.ip}）；请只保留一台",
                    )
                supply_ids[supply_id] = str(device.ip or "")
                continue
            if role in used_roles:
                return _reject(
                    f"device_bindings.{role}",
                    _CODE_DUPLICATE_ROLE,
                    f"角色 {role} 在本次应用中被重复指定（{used_roles[role]} 与 "
                    f"{device.ip}）；请只保留一台设备",
                )
            used_roles[role] = str(device.ip or "")

        # 第二遍校验每条记录的地址与身份，全部通过后才写绑定记录。
        checked = _checked_devices(devices)
        if isinstance(checked, DeviceBindingResult):
            return checked

        # 第三遍：同一个 device_key 不能出现在两条电源绑定里（同一台设备不能接两次）。
        bound_keys: dict[str, str] = {}
        for device, identity in checked:
            if device.role != POWER_SUPPLY_ROLE or not identity:
                continue
            supply_id = device.reference
            if identity in bound_keys:
                return _reject(
                    f"device_bindings.power_supplies.{supply_id}",
                    _CODE_DUPLICATE_DEVICE,
                    f"电源 {supply_id} 与 {bound_keys[identity]} 是同一台设备"
                    f"（{identity}），同一台设备不能重复绑定；请只保留一台",
                )
            bound_keys[identity] = supply_id

        return _bindings_of(devices, checked)


def _checked_devices(
    devices: tuple[SelectedDevice, ...],
) -> list[tuple[SelectedDevice, str]] | DeviceBindingResult:
    """第二遍校验：地址、完整设备码、身份自洽与电源身份。

    全部通过时返回 ``(设备, 身份)`` 列表；任何一条不通过时返回**拒绝结果**（空绑定集合），
    调用方据此整批放弃，不写半份绑定。
    """
    checked: list[tuple[SelectedDevice, str]] = []
    for device in devices:
        role = str(device.role)
        address = device.address
        if not isinstance(address, str) or not address.strip():
            return _reject(
                f"device_bindings.{role}",
                _CODE_MISSING_ADDRESS,
                f"{role} 的发现地址为空，未应用",
            )
        full_code = str(device.full_code or "").strip()
        vendor = str(device.vendor or "").strip()
        if not full_code:
            return _reject(
                f"device_bindings.{role}",
                _CODE_MISSING_IDENTITY,
                f"{role} 缺少设备身份（完整设备码），未应用",
            )
        device_key = str(device.device_key or "").strip()
        if device_key and vendor:
            try:
                expected = make_device_key(vendor, full_code)
            except ValueError as error:
                return _reject(
                    f"device_bindings.{role}.device_key",
                    _CODE_UNPARSEABLE_IDENTITY,
                    f"{role} 的设备身份无法解析: {error}",
                )
            if device_key.casefold() != expected:
                return _reject(
                    f"device_bindings.{role}.device_key",
                    _CODE_IDENTITY_MISMATCH,
                    f"{role} 的设备身份与完整设备码不一致"
                    f"（{device_key} ≠ {expected}），未应用",
                )
        identity = device_key.casefold()
        if device_key and not vendor:
            # 只有 ``device_key`` 时至少要求它携带的完整设备码与本次读到的一致，
            # 否则地址会被写到一个身份自相矛盾的名字下。
            tail = device_key.rsplit(":", 1)[-1].strip().casefold()
            if tail != normalize_full_code(full_code).casefold():
                return _reject(
                    f"device_bindings.{role}.device_key",
                    _CODE_IDENTITY_MISMATCH,
                    f"{role} 的设备身份与完整设备码不一致"
                    f"（{device_key} ≠ {full_code}），未应用",
                )
        if not identity and vendor:
            try:
                identity = make_device_key(vendor, full_code)
            except ValueError:
                identity = ""
        if role == POWER_SUPPLY_ROLE and not identity:
            # 电源绑定的身份是唯一匹配依据：没有它，下一次扫描找不回这台设备，
            # 配置读取也会把这条记录判为不可用，等于写了一条坏绑定。
            return _reject(
                f"device_bindings.power_supplies.{device.reference}",
                _CODE_MISSING_POWER_IDENTITY,
                f"电源 {device.reference} 缺少可用于绑定的设备身份"
                "（device_key，或厂商 + 完整设备码），未应用",
            )
        checked.append((device, identity))
    return checked


def _bindings_of(
    devices: tuple[SelectedDevice, ...],
    checked: list[tuple[SelectedDevice, str]],
) -> DeviceBindingResult:
    """把通过校验的记录写成当前 ``device_bindings`` 结构。"""
    bindings: dict[str, Any] = {}
    supplies: dict[str, dict[str, Any]] = {}
    applied: list[AppliedDeviceBinding] = []
    for device, identity in checked:
        role = str(device.role)
        record = {
            "vendor": device.vendor,
            "model": device.model,
            "full_code": device.full_code,
            # 身份统一写成 ``厂商:完整设备码`` 的小写形式；记录必须带身份，才能在下一次
            # 扫描时按身份找回同一台设备。
            "device_key": identity or device.device_key,
            "visa_address": str(device.address).strip(),
            "last_ip": device.ip,
            # 探测端口只是“这台设备在本网段怎么被读出身份”的记录，不参与拼接连接地址。
            "last_port": device.port,
            "known": bool(device.known),
            # 模拟扫描写入的绑定带来源标记，避免演示数据被当成现场设备记录。
            "source": "simulated" if device.simulated else "scan",
        }
        if role == POWER_SUPPLY_ROLE:
            # 集合的键就是引用 ID（= 缩写设备码）：记录里不再重复写 label/supply_id。
            supply_id = device.reference
            supplies[supply_id] = record
            applied.append(
                AppliedDeviceBinding(
                    role=role,
                    supply_id=supply_id,
                    model=device.model,
                    full_code=device.full_code,
                    ip=device.ip,
                )
            )
        else:
            # 信号源与频谱仪的缩写设备码没有别的存放位置：必须随绑定一起冻结，配置条目
            # 与运行快照才能带上 short_code（``label``）。
            record["label"] = device.label
            bindings[role] = record
            applied.append(
                AppliedDeviceBinding(
                    role=role,
                    model=device.model,
                    full_code=device.full_code,
                    ip=device.ip,
                )
            )
    if supplies:
        bindings[POWER_SUPPLY_BINDINGS] = supplies
    return DeviceBindingResult(
        bindings=bindings,
        applied=tuple(applied),
        simulated=any(device.simulated for device in devices),
    )


def _reject(path: str, code: str, message: str) -> DeviceBindingResult:
    return DeviceBindingResult(
        validation=ValidationResult.of(
            ValidationIssue(path=path, code=code, message=message)
        )
    )


# ---- 绑定记录的读取边界 -----------------------------------------------------


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


def binding_device_key(record: Any) -> str | None:
    """返回一条设备绑定记录的身份键 ``厂商:完整设备码``；无法确定身份时返回 ``None``。

    身份是 ``device_key``。旧记录只有厂商与完整设备码两段时在这里重新拼出同一个键；
    只有完整设备码没有厂商时无法确定身份，返回 ``None``（不做“仅按设备码”的猜测匹配，
    否则两个厂商的同号设备会被混为一谈）。
    """
    if not isinstance(record, Mapping):
        return None
    key = str(record.get("device_key") or "").strip().casefold()
    if key:
        return key
    vendor = str(record.get("vendor") or "").strip()
    full_code = _binding_full_code(record)
    if not vendor or not full_code:
        return None
    try:
        return make_device_key(vendor, full_code)
    except ValueError:
        return None


def _binding_full_code(record: Mapping[str, Any]) -> str:
    """读取绑定记录里的完整设备码；``full_code`` 优先。

    历史读取边界：旧绑定把完整设备码写在 ``serial`` 里。读入后立即投影为 ``full_code``，
    新写出的绑定只带 ``full_code``。
    """
    value = record.get("full_code")
    if value is None:
        value = record.get("serial")
    return str(value or "").strip()


def binding_identity_problem(record: Mapping[str, Any]) -> str | None:
    """返回一条绑定记录身份不自洽的原因；身份完整时返回 ``None``。

    身份就是 ``device_key``（``厂商:完整设备码``）：记录必须能给出它，且记录里**每一个**
    声明出来的身份片段（厂商、完整设备码）都必须与 ``device_key`` 的对应部分一致。只声明
    其中一个时同样要核对——只看“两个都在”会放过“厂商写着 A、键指向 B”的记录；而声明了
    厂商或完整设备码却给不出 ``厂商:完整设备码`` 形式的键时，身份无法核对，也算不自洽。

    本判定属于 application：``domain.instruments`` 只定义身份语义（``make_device_key``
    与归一化），这里判定的是“这一条记录的身份是否自洽”。配置页读取绑定集合时也通过
    ``presentation.qt.config_form_bindings`` 复用同一条规则。
    """
    identity = binding_device_key(record)
    if identity is None:
        return "缺少设备身份（device_key，或厂商 + 完整设备码）"
    device_key = str(record.get("device_key") or "").strip()
    if not device_key:
        # 身份由厂商 + 完整设备码拼出（:func:`make_device_key`）：不存在第二份取值可核对。
        return None
    vendor = normalize_vendor(record.get("vendor"))
    full_code = normalize_full_code(_binding_full_code(record))
    if not vendor and not full_code:
        return None
    parts = _identity_parts(device_key)
    if parts is None:
        return f"设备身份不是 厂商:完整设备码 形式（{device_key}）"
    key_vendor, key_code = parts
    if vendor and full_code:
        try:
            expected = make_device_key(vendor, full_code)
        except ValueError:
            return f"设备身份无法解析（{vendor!r} / {full_code!r}）"
        if device_key.casefold() != expected:
            return f"设备身份与厂商/完整设备码不一致（{device_key} ≠ {expected}）"
        return None
    if vendor and vendor.casefold() != key_vendor:
        return f"设备身份与厂商不一致（{device_key} ≠ {vendor.casefold()}:…）"
    if full_code and full_code.casefold() != key_code:
        return f"设备身份与完整设备码不一致（{device_key} ≠ …{full_code}）"
    return None


def legacy_binding_count(bindings: Any) -> int:
    """统计“沿用已保存地址、未扫描确认”的旧绑定条数。

    绑定结构只在本模块解释：当前结构是角色键 + ``power_supplies`` 集合，步骤 5 的过渡
    结构还可能在顶层留下 ``PS1``..``PS4`` 槽位。两种结构都按记录里的 ``source`` 判定；
    电源集合是嵌套的，单独遍历。这只是一个展示计数，不参与绑定写入或身份判定。
    """
    if not isinstance(bindings, Mapping):
        return 0
    count = 0
    supplies = bindings.get(POWER_SUPPLY_BINDINGS)
    if isinstance(supplies, Mapping):
        count += sum(
            1
            for record in supplies.values()
            if isinstance(record, Mapping)
            and record.get("source") == LEGACY_BINDING_SOURCE
        )
    for key, record in bindings.items():
        if key == POWER_SUPPLY_BINDINGS:
            continue
        if (
            isinstance(record, Mapping)
            and record.get("source") == LEGACY_BINDING_SOURCE
        ):
            count += 1
    return count


def _identity_parts(device_key: str) -> tuple[str, str] | None:
    """把 ``device_key`` 拆成 ``(厂商, 完整设备码)``（小写归一化）；不是该形式时返回 ``None``。

    键由 :func:`domain.instruments.models.make_device_key` 拼成 ``厂商:完整设备码``，所以按
    **最后**一个分隔符拆分：厂商标识本身带冒号时，完整设备码仍是最后一段。
    """
    if ":" not in device_key:
        return None
    vendor, full_code = device_key.rsplit(":", 1)
    return vendor.strip().casefold(), full_code.strip().casefold()
