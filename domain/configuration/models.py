"""测试方案和单次运行资源映射的数据模型与离线校验。

本模块只定义配置对象并执行输入校验，不负责 JSON 文件读取，也不创建 VISA 或其他硬件连接。

归属：domain。配置规则与值对象的唯一正式实现，
不依赖 Qt、VISA、文件格式与基础设施；正式配置文档只由 application 的配置编写服务生成。
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Union

from domain.configuration.types import ConfigIssue, ConfigValidationResult


PathLike = Union[str, os.PathLike[str]]
# 配置文档（根 config.json、test_plan、run_mapping）的唯一版本来源。写入只写
# SUPPORTED_SCHEMA_VERSION；读取接受 READABLE_SCHEMA_VERSIONS（按旧→新排列）。
# 其它 ``schema_version`` 轴（运行快照包装、manifest、设备表、迁移摘要、结果文件）
# 各自定义，不引用这里的值。
SUPPORTED_SCHEMA_VERSION = "1.1"
READABLE_SCHEMA_VERSIONS = ("1.0", "1.1")
# 旧角色化电源段的最后版本；1.0 只读，不能保存或创建新运行。
LEGACY_SCHEMA_VERSION = READABLE_SCHEMA_VERSIONS[0]
SUPPORTED_COMPRESSION_POINTS = {"1dB", "3dB", "5dB"}
PLACEHOLDER_VALUES = {"", "无", "待填写", "<channel_name>", "<gate_or_drain_or_other>"}
# 组内相邻两次通道开启之间的最大等待秒数（单位秒）。放在 domain 是因为它既是
# 编辑文档的字段范围（v1.1 §6.8），也要被 domain 的 ``validate_power_plan`` 使用；
# application 的 ``PowerPlan`` 值对象从本模块导入同一数值，全仓不出现第二份。
MAX_INTER_CHANNEL_DELAY_S = 60.0
# ``channel_settings`` 每条记录的完整字段集合（§6.1、§6.3）：路由两段平铺 + 工作值/
# 保护值 + 单位。1.1 不得写通道 ``role`` / ``connection`` 或其他旧字段；这里既是
# 写出前的形状判据，也用于拒绝任意未知字段。
CHANNEL_SETTING_FIELDS = frozenset(
    {
        "supply_id",
        "channel",
        "voltage",
        "current",
        "voltage_protection",
        "current_protection",
        "unit_voltage",
        "unit_current",
    }
)
# 旧运行快照里单数电源条目的键；新快照只写 ``instruments.power_supplies``。
LEGACY_POWER_SUPPLY_KEY = "power_supply"
# 运行映射里电源集合的键。电源只有一个来源：写它就不能再写旧单数条目。
POWER_SUPPLIES_KEY = "power_supplies"


@dataclass(frozen=True)
class PowerChannelPlan:
    """一个旧式供电角色键下的测试方案参数，不绑定现场物理通道名。

    ``role`` 只为读取/写回 1.0 正式段保留；步骤 5 起它不再参与任何校验或上电，
    新计划（``power_plan`` + ``channel_settings``）由后续步骤接管。
    """

    role: Optional[str] = None
    voltage: Optional[float] = None
    current: Optional[float] = None
    voltage_protection: Optional[float] = None
    current_protection: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PowerChannelPlan":
        known = {
            "role",
            "connection",
            "voltage",
            "current",
            "voltage_protection",
            "current_protection",
            "unit_voltage",
            "unit_current",
        }
        return cls(
            role=value.get("role", value.get("connection")),
            voltage=value.get("voltage"),
            current=value.get("current"),
            voltage_protection=value.get("voltage_protection"),
            current_protection=value.get("current_protection"),
            extra={key: item for key, item in value.items() if key not in known},
        )

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "role": self.role,
            "voltage": self.voltage,
            "current": self.current,
            "voltage_protection": self.voltage_protection,
            "current_protection": self.current_protection,
            "unit_voltage": "V",
            "unit_current": "A",
        }
        result.update(self.extra)
        return result


@dataclass(frozen=True)
class TestPlan:
    """稳定的测试方法和参数。"""

    schema_version: str = SUPPORTED_SCHEMA_VERSION
    template: bool = False
    plan_id: Optional[str] = None
    frequencies: List[Any] = field(default_factory=list)
    frequency_unit: str = "GHz"
    start_power: Optional[float] = None
    stop_power: Optional[float] = None
    power_step: Optional[float] = None
    power_unit: str = "dBm"
    compression_point: Optional[str] = None
    attenuator_value: Optional[float] = None
    attenuator_unit: str = "dB"
    max_input_power: Optional[float] = None
    # 1.0 只读兼容：角色键 -> 参数。1.1 不再写这个字段。
    dut_power_channels: Dict[str, PowerChannelPlan] = field(default_factory=dict)
    # 1.1：漏极测量路由与每路由工作值/保护值。保持文档形状（两段路由对象与
    # 平铺参数），由 :func:`validate_run_configuration` 做跨段判定；domain 不引入
    # 第二份路由/计划值对象，application 的 ``PowerPlan`` 只负责编辑段的值对象。
    drain_channel: Any = None
    channel_settings: List[Any] = field(default_factory=list)
    driver_enabled: bool = False
    # 1.0 只读兼容：角色键 -> 参数。1.1 用 driver_channel_settings。
    driver_power_channels: Dict[str, PowerChannelPlan] = field(default_factory=dict)
    driver_channel_settings: List[Any] = field(default_factory=list)
    # 来源文档是否使用 1.0 的角色化电源字段。用于版本分派与 1.1 的“不得写旧字段”
    # 判定；程序内直接构造的对象没有这个标记。
    legacy_power_fields: bool = field(default=False, compare=False, repr=False)
    other_parameters: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TestPlan":
        frequencies = value.get("frequencies", {})
        signal_source = value.get("signal_source", {})
        compression = value.get("compression_point", {})
        attenuator = value.get("attenuator", {})
        dut = value.get("dut", {})
        driver = value.get("driver_mode", {})
        return cls(
            schema_version=value.get("schema_version", ""),
            template=value.get("template", False),
            plan_id=value.get("plan_id"),
            frequencies=_list_value(frequencies.get("values", [])) if isinstance(frequencies, Mapping) else [],
            frequency_unit=frequencies.get("unit", "GHz") if isinstance(frequencies, Mapping) else "GHz",
            start_power=signal_source.get("start_power") if isinstance(signal_source, Mapping) else None,
            stop_power=signal_source.get("stop_power") if isinstance(signal_source, Mapping) else None,
            power_step=signal_source.get("step") if isinstance(signal_source, Mapping) else None,
            power_unit=signal_source.get("unit", "dBm") if isinstance(signal_source, Mapping) else "dBm",
            compression_point=compression.get("type") if isinstance(compression, Mapping) else None,
            attenuator_value=attenuator.get("value") if isinstance(attenuator, Mapping) else None,
            attenuator_unit=attenuator.get("unit", "dB") if isinstance(attenuator, Mapping) else "dB",
            max_input_power=dut.get("max_input_power") if isinstance(dut, Mapping) else None,
            dut_power_channels=_channel_plans(
                _first_mapping(dut, "power_roles", "power_channels") if isinstance(dut, Mapping) else {}
            ),
            drain_channel=dut.get("drain_channel") if isinstance(dut, Mapping) else None,
            channel_settings=_list_value(
                dut.get("channel_settings", []) if isinstance(dut, Mapping) else []
            ),
            driver_enabled=driver.get("enabled", False) if isinstance(driver, Mapping) else False,
            driver_power_channels=_channel_plans(
                _first_mapping(driver, "power_roles", "power_channels") if isinstance(driver, Mapping) else {}
            ),
            driver_channel_settings=_list_value(
                driver.get("channel_settings", []) if isinstance(driver, Mapping) else []
            ),
            legacy_power_fields=_has_legacy_power_fields(dut, driver),
            other_parameters=_mapping_value(value.get("other_parameters", {})),
            raw=dict(value),
        )

    def to_dict(self) -> Dict[str, Any]:
        """按版本写出正式测试方案。

        1.0 是历史只读形状（角色键参数），只有 ``schema_version == "1.0"`` 的读取结果
        才会再写成它；当前版本一律写 ``drain_channel`` / ``channel_settings``，绝不
        重新输出 ``power_roles`` / ``power_channels``。
        """
        if self.schema_version == LEGACY_SCHEMA_VERSION:
            dut: Dict[str, Any] = {
                "max_input_power": self.max_input_power,
                "power_roles": {name: channel.to_dict() for name, channel in self.dut_power_channels.items()},
            }
            driver_mode: Dict[str, Any] = {
                "enabled": self.driver_enabled,
                "power_roles": {name: channel.to_dict() for name, channel in self.driver_power_channels.items()},
            }
        else:
            dut = {
                "max_input_power": self.max_input_power,
                "drain_channel": _drain_document(self.drain_channel),
                "channel_settings": [dict(item) if isinstance(item, Mapping) else item for item in self.channel_settings],
            }
            driver_mode = {
                "enabled": self.driver_enabled,
                "channel_settings": [
                    dict(item) if isinstance(item, Mapping) else item
                    for item in self.driver_channel_settings
                ],
            }
        return {
            "schema_version": self.schema_version,
            "template": self.template,
            "plan_id": self.plan_id,
            "frequencies": {"values": list(self.frequencies), "unit": self.frequency_unit},
            "signal_source": {
                "start_power": self.start_power,
                "stop_power": self.stop_power,
                "step": self.power_step,
                "unit": self.power_unit,
            },
            "compression_point": {"type": self.compression_point},
            "attenuator": {"value": self.attenuator_value, "unit": self.attenuator_unit},
            "dut": dut,
            "driver_mode": driver_mode,
            "other_parameters": dict(self.other_parameters),
        }


@dataclass(frozen=True)
class InstrumentMapping:
    """运行快照里的一台仪器（信号源、频谱仪或旧单数电源条目）。

    新快照冻结 ``label`` / ``model`` / ``full_code`` / ``device_key`` / ``visa_address``，
    运行期间不回查设备表；旧快照只有 ``model`` / ``visa_address``，读取时完整设备码仍从
    旧字段 ``serial`` 投影，写出时只保留已知字段，避免把历史形状改写成半新半旧。

    身份字段只能追加在既有的 ``model`` / ``visa_address`` 之后：按位置构造
    ``InstrumentMapping(model, visa_address)`` 的调用不能被静默改写成别的字段。
    """

    model: Optional[str] = None
    visa_address: Optional[str] = None
    label: Optional[str] = None
    full_code: Optional[str] = None
    device_key: Optional[str] = None

    @classmethod
    def from_dict(cls, value: Any) -> "InstrumentMapping":
        value = value if isinstance(value, Mapping) else {}
        # 历史读取边界：旧运行快照把完整设备码写在 ``serial`` 里。
        full_code = value.get("full_code")
        if full_code is None:
            full_code = value.get("serial")
        return cls(
            label=value.get("label"),
            model=value.get("model"),
            full_code=full_code,
            device_key=value.get("device_key"),
            visa_address=value.get("visa_address"),
        )

    def to_dict(self) -> Dict[str, Any]:
        fields = {
            "label": self.label,
            "model": self.model,
            "full_code": self.full_code,
            "device_key": self.device_key,
            "visa_address": self.visa_address,
        }
        return {key: value for key, value in fields.items() if value is not None}


@dataclass(frozen=True)
class PowerSupplyMapping:
    """运行快照里的一台本机供电电源，自带身份而不是只有名称。

    集合键（``RunResourceMapping.power_supplies``）是 ``supply_id``，即现场缩写设备码；
    ``device_key`` / ``full_code`` 用于追溯，``visa_address`` 用于连接。快照冻结后不再
    回查设备注册表，历史查看与重现都以这条记录为准。新快照只写 ``full_code``，不写任何
    旧字段名。
    """

    label: Optional[str] = None
    model: Optional[str] = None
    full_code: Optional[str] = None
    device_key: Optional[str] = None
    visa_address: Optional[str] = None

    @classmethod
    def from_dict(cls, value: Any) -> "PowerSupplyMapping":
        value = value if isinstance(value, Mapping) else {}
        # 历史读取边界：旧运行快照把完整设备码写在 ``serial`` 里。读入后立即投影为
        # ``full_code``；新文档不再写出旧键。
        full_code = value.get("full_code")
        if full_code is None:
            full_code = value.get("serial")
        return cls(
            label=value.get("label"),
            model=value.get("model"),
            full_code=full_code,
            device_key=value.get("device_key"),
            visa_address=value.get("visa_address"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "model": self.model,
            "full_code": self.full_code,
            "device_key": self.device_key,
            "visa_address": self.visa_address,
        }

    @classmethod
    def from_instrument_slot(
        cls, value: Any, *, address: Optional[str] = None
    ) -> "PowerSupplyMapping":
        """把旧的单数 ``instruments.power_supply`` 读成一个条目。

        旧条目只有 ``model`` / ``visa_address``；``model`` 当时存的是电源槽位名
        （``PS1`` 等），因此按槽位名回填 ``label``/``device_key``，让旧快照在
        ``power_supplies`` 里也能被唯一指到。``address`` 用于取回条目里的真实地址：
        旧运行上下文把单数条目改写成 ``power_supply: {model, visa_address}``。
        """
        slot = value if isinstance(value, Mapping) else {}
        instrument = InstrumentMapping.from_dict(slot)
        label = instrument.model or None
        return cls(
            label=label,
            model=label,
            full_code=None,
            device_key=label,
            visa_address=address if address is not None else instrument.visa_address,
        )


@dataclass(frozen=True)
class ChannelMapping:
    channel: str
    role: Optional[str] = None
    connection: Optional[str] = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ChannelMapping":
        return cls(
            channel=value.get("channel", ""),
            role=value.get("role"),
            connection=value.get("connection"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {"channel": self.channel, "role": self.role, "connection": self.connection}


@dataclass(frozen=True)
class RunResourceMapping:
    """本次测试现场实际使用的设备和通道映射。"""

    schema_version: str = SUPPORTED_SCHEMA_VERSION
    template: bool = False
    run_id: Optional[str] = None
    run_datetime: Optional[str] = None
    operator: Optional[str] = None
    instruments: Dict[str, InstrumentMapping] = field(default_factory=dict)
    power_supplies: Dict[str, PowerSupplyMapping] = field(default_factory=dict)
    # 来源文档是否显式写了 ``instruments.power_supplies``（哪怕集合是空的）。
    # 判断“写的是新结构还是旧结构”必须看这个键在不在，不能看集合是不是非空：
    # 否则 ``power_supplies: {}`` 加上旧单数条目会被当成纯旧快照悄悄放行，序列化
    # 时也会在两种结构之间来回变形状。它只描述文档形状，不参与相等比较。
    power_supplies_declared: bool = field(default=False, compare=False, repr=False)
    driver_enabled: bool = False
    # 1.0 只读兼容：角色化通道清单。1.1 不再写这两个字段。
    driver_power_channels: List[ChannelMapping] = field(default_factory=list)
    dut_power_channels: List[ChannelMapping] = field(default_factory=list)
    # 1.1：冻结的上电计划（``{power_sequence, inter_channel_delay_s}``）。顺序只在
    # 编辑段可写，这里是创建运行时冻结的副本；形状由 validate_run_mapping 校验。
    dut_power_plan: Any = None
    driver_power_plan: Any = None
    # 来源文档是否使用 1.0 的角色化电源字段（版本分派与“1.1 不得写旧字段”）。
    legacy_power_fields: bool = field(default=False, compare=False, repr=False)
    notes: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunResourceMapping":
        instruments = value.get("instruments", {})
        instruments = instruments if isinstance(instruments, Mapping) else {}
        power_supplies = instruments.get(POWER_SUPPLIES_KEY)
        driver = value.get("driver_mode", {})
        # 1.0 只读兼容：角色化通道清单是原字段。1.1 文档没有它，旧消费方（测量输入
        # DTO、装配）仍按同一属性读取；这里从冻结的上电计划按顺序投影，role/connection
        # 不再有语义，等步骤 7–9 改成直接消费物理路由后删除该投影。
        dut_legacy = _channel_mappings(value.get("dut_power_channels", []))
        if not dut_legacy:
            dut_legacy = _plan_channel_mappings(value.get("dut_power_plan"))
        driver_legacy = _channel_mappings(
            driver.get("power_channels", []) if isinstance(driver, Mapping) else []
        )
        if not driver_legacy:
            driver_legacy = _plan_channel_mappings(
                driver.get("power_plan") if isinstance(driver, Mapping) else None
            )
        return cls(
            schema_version=value.get("schema_version", ""),
            template=value.get("template", False),
            run_id=value.get("run_id"),
            run_datetime=value.get("run_datetime"),
            operator=value.get("operator"),
            # 电源集合有自己的一层结构，不进 ``instruments`` 的逐台仪器表，否则
            # ``instruments`` 里会多出一条空仪器记录。
            instruments={
                name: InstrumentMapping.from_dict(item)
                for name, item in instruments.items()
                if name != POWER_SUPPLIES_KEY
            },
            power_supplies=_power_supply_mappings(power_supplies),
            power_supplies_declared=POWER_SUPPLIES_KEY in instruments,
            driver_enabled=driver.get("enabled", False) if isinstance(driver, Mapping) else False,
            driver_power_channels=driver_legacy,
            dut_power_channels=dut_legacy,
            dut_power_plan=value.get("dut_power_plan"),
            driver_power_plan=(
                driver.get("power_plan") if isinstance(driver, Mapping) else None
            ),
            legacy_power_fields=_mapping_uses_legacy_power_fields(value, driver),
            notes=value.get("notes"),
            raw=dict(value),
        )

    def to_dict(self) -> Dict[str, Any]:
        """按来源的形状写回电源段：新结构只写集合，旧快照只写单数条目。

        电源只有一个来源，所以两种形状互斥。旧快照（只有 ``instruments.power_supply``、
        没有集合）读入后再写回仍是旧形状，不会被补一个空集合变成“新旧混写”的文档；
        新结构则丢掉旧单数条目，只写 ``instruments.power_supplies``。
        """
        instruments = {name: item.to_dict() for name, item in self.instruments.items()}
        if self.declares_power_supplies():
            # 电源只有一个来源：写集合时不再保留旧单数条目。
            instruments.pop(LEGACY_POWER_SUPPLY_KEY, None)
            instruments[POWER_SUPPLIES_KEY] = {
                supply_id: item.to_dict() for supply_id, item in self.power_supplies.items()
            }
        if self.schema_version == LEGACY_SCHEMA_VERSION:
            driver_mode: Dict[str, Any] = {
                "enabled": self.driver_enabled,
                "power_channels": [item.to_dict() for item in self.driver_power_channels],
            }
            dut_power_channels: List[Any] = [
                item.to_dict() for item in self.dut_power_channels
            ]
        else:
            driver_mode = {
                "enabled": self.driver_enabled,
                "power_plan": _plan_document(self.driver_power_plan),
            }
            dut_power_channels = []
        result: Dict[str, Any] = {
            "schema_version": self.schema_version,
            "template": self.template,
            "run_id": self.run_id,
            "run_datetime": self.run_datetime,
            "operator": self.operator,
            "instruments": instruments,
            "driver_mode": driver_mode,
        }
        if self.schema_version == LEGACY_SCHEMA_VERSION:
            result["dut_power_channels"] = dut_power_channels
        else:
            result["dut_power_plan"] = _plan_document(self.dut_power_plan)
        result["notes"] = self.notes
        return result

    def declares_power_supplies(self) -> bool:
        """这份映射的电源段是否按新结构写。

        有集合条目，或来源文档显式写了集合键（即使集合是空的），都算新结构；程序里
        直接构造出 ``power_supplies={...}`` 的映射同样算。旧单数条目只有在“没有集合”
        时才是这个问题的唯一答案。
        """
        return bool(self.power_supplies) or self.power_supplies_declared

    def legacy_power_supply(self) -> Optional[InstrumentMapping]:
        """旧单数 ``instruments.power_supply`` 的读取兼容。

        新快照不再写这个键；历史运行的字段查看仍可以按旧名字取到“唯一那台”电源。
        恰好一台电源时用它的型号和地址回填，多台时返回 ``None``——多台电源之下
        “那台电源”没有唯一答案，调用方应改用 :attr:`power_supplies`。
        """
        instrument = self.instruments.get(LEGACY_POWER_SUPPLY_KEY)
        if instrument is not None:
            return instrument
        if len(self.power_supplies) != 1:
            return None
        item = next(iter(self.power_supplies.values()))
        return InstrumentMapping(model=item.model, visa_address=item.visa_address)


@dataclass(frozen=True)
class RunConfiguration:
    test_plan: TestPlan
    run_mapping: RunResourceMapping


def validate_test_plan(plan: Union[TestPlan, Mapping[str, Any]]) -> ConfigValidationResult:
    plan = _as_test_plan(plan)
    errors: List[ConfigIssue] = []
    warnings: List[ConfigIssue] = []
    error = lambda path, message: errors.append(ConfigIssue("error", path, message))

    _validate_version(plan.schema_version, "schema_version", error)
    for field_name, value, expected in (
        ("frequencies.unit", plan.frequency_unit, "GHz"),
        ("signal_source.unit", plan.power_unit, "dBm"),
        ("attenuator.unit", plan.attenuator_unit, "dB"),
    ):
        if value != expected:
            error(field_name, f"当前仅支持 {expected}，收到 {value!r}")
    if not isinstance(plan.template, bool):
        error("template", "必须是布尔值")
    if plan.template:
        error("template", "模板不能直接作为正式运行方案")
    if not isinstance(plan.driver_enabled, bool):
        error("driver_mode.enabled", "必须是布尔值")
    _finite_list(plan.frequencies, "frequencies.values", error, positive=True)
    if not plan.frequencies:
        error("frequencies.values", "必须是非空列表")
    else:
        numeric_frequencies = [float(item) for item in plan.frequencies if _is_finite_number(item)]
        if len(set(numeric_frequencies)) != len(numeric_frequencies):
            error("frequencies.values", "不能包含重复频率")
    start = _number(plan.start_power, "signal_source.start_power", error)
    stop = _number(plan.stop_power, "signal_source.stop_power", error)
    _number(plan.power_step, "signal_source.step", error, positive=True)
    if start is not None and stop is not None and start > stop:
        error("signal_source", "start_power 不能大于 stop_power")
    if plan.compression_point not in SUPPORTED_COMPRESSION_POINTS:
        error("compression_point.type", "必须是 1dB、3dB 或 5dB")
    _number(plan.attenuator_value, "attenuator.value", error, non_negative=True)
    _number(plan.max_input_power, "dut.max_input_power", error)
    legacy = _is_legacy_plan(plan)
    if legacy:
        if not plan.dut_power_channels:
            error("dut.power_channels", "必须至少配置一个 DUT 电源通道")
        _validate_channels(plan.dut_power_channels, "dut.power_channels", error)
        _validate_channels(plan.driver_power_channels, "driver_mode.power_channels", error)
        _validate_driver_mode(
            plan.driver_enabled,
            plan.driver_power_channels,
            error,
            path="driver_mode.power_channels",
        )
    else:
        # 1.1 不能再写 1.0 的角色化电源字段：两套结构同时存在就没有唯一来源。
        if "power_roles" in _mapping_value(plan.raw.get("dut")) or "power_channels" in _mapping_value(plan.raw.get("dut")):
            error("dut.power_roles", "1.1 不再写 power_roles/power_channels；电源参数只写 channel_settings")
        if "power_roles" in _mapping_value(plan.raw.get("driver_mode")) or "power_channels" in _mapping_value(plan.raw.get("driver_mode")):
            error("driver_mode.power_roles", "1.1 不再写 power_roles/power_channels；电源参数只写 channel_settings")
        for section_path, section in (
            ("dut", _mapping_value(plan.raw.get("dut"))),
            ("driver_mode", _mapping_value(plan.raw.get("driver_mode"))),
        ):
            raw_settings = section.get("channel_settings")
            if raw_settings is not None and not isinstance(raw_settings, list):
                error(f"{section_path}.channel_settings", "必须是列表")
        _validate_channel_settings(plan.channel_settings, "dut.channel_settings", error)
        _validate_channel_settings(
            plan.driver_channel_settings, "driver_mode.channel_settings", error
        )
        _validate_drain_routes(plan.drain_channel, "dut.drain_channel", error)
        _validate_driver_mode(
            plan.driver_enabled,
            plan.driver_channel_settings,
            error,
            path="driver_mode.channel_settings",
        )
    return ConfigValidationResult(errors=errors, warnings=warnings)


def validate_run_mapping(mapping: Union[RunResourceMapping, Mapping[str, Any]]) -> ConfigValidationResult:
    mapping = _as_run_mapping(mapping)
    errors: List[ConfigIssue] = []
    warnings: List[ConfigIssue] = []
    error = lambda path, message: errors.append(ConfigIssue("error", path, message))

    _validate_version(mapping.schema_version, "schema_version", error)
    if not isinstance(mapping.template, bool):
        error("template", "必须是布尔值")
    if mapping.template:
        error("template", "模板不能直接作为正式运行映射")
    if not isinstance(mapping.driver_enabled, bool):
        error("driver_mode.enabled", "必须是布尔值")
    # 电源是集合：不再要求单数 ``instruments.power_supply``，改为校验电源集合本身。
    _validate_identity_field_conflicts(mapping, error)
    for name in ("signal_generator", "spectrum_analyzer"):
        instrument = mapping.instruments.get(name)
        if instrument is None:
            error(f"instruments.{name}", "缺少仪器配置")
            continue
        _required_text(instrument.model, f"instruments.{name}.model", error)
        _required_text(instrument.visa_address, f"instruments.{name}.visa_address", error)
    _validate_power_supplies(mapping, error)
    if _is_legacy_mapping(mapping):
        _validate_mappings(mapping.dut_power_channels, "dut_power_channels", error)
        _validate_mappings(mapping.driver_power_channels, "driver_mode.power_channels", error)
        dut_channels = {item.channel for item in mapping.dut_power_channels}
        driver_channels = {item.channel for item in mapping.driver_power_channels}
        if not dut_channels:
            error("dut_power_channels", "必须至少分配一个 DUT 电源通道")
        overlap = dut_channels & driver_channels
        if overlap:
            error("power_channels", f"DUT 和驱动功放不能重复使用通道: {', '.join(sorted(overlap))}")
        _validate_channel_routing(mapping, error)
        _validate_driver_mode(
            mapping.driver_enabled,
            mapping.driver_power_channels,
            error,
            path="driver_mode.power_channels",
        )
    else:
        if "dut_power_channels" in mapping.raw:
            error("dut_power_channels", "1.1 不再写 dut_power_channels；上电顺序只写 dut_power_plan")
        if "power_channels" in _mapping_value(mapping.raw.get("driver_mode")):
            error("driver_mode.power_channels", "1.1 不再写 power_channels；上电顺序只写 power_plan")
        dut_sequence = _validate_plan_document(
            mapping.dut_power_plan, "dut_power_plan", error
        )
        driver_sequence = _validate_plan_document(
            mapping.driver_power_plan, "driver_mode.power_plan", error
        )
        overlap = set(dut_sequence or ()) & set(driver_sequence or ())
        if overlap:
            error(
                "driver_mode.power_plan.power_sequence",
                "DUT 和驱动功放不能重复使用通道: "
                + ", ".join(f"{supply_id}/{channel}" for supply_id, channel in sorted(overlap)),
            )
        _validate_driver_plan(
            mapping.driver_enabled,
            driver_sequence,
            error,
        )
    return ConfigValidationResult(errors=errors, warnings=warnings)


def validate_run_configuration(
    configuration: RunConfiguration, *, require_runnable: bool = True
) -> ConfigValidationResult:
    """校验一份运行配置。

    ``require_runnable=False`` 只做结构/类型校验，跳过“能不能真的开始一次运行”的
    v1.1 电源规则（§6.12 规则 8–13）。配置文件保存与迁移写盘用这个模式：迁移把用户
    尚未排序的计划写成空计划，这是可保存的合法状态（§6.6），真正的可运行判据在创建
    运行时再过一遍。默认仍为 ``True``，启动器、创建运行与继续运行的行为不变。
    """
    plan_result = validate_test_plan(configuration.test_plan)
    mapping_result = validate_run_mapping(configuration.run_mapping)
    errors = plan_result.errors + mapping_result.errors
    warnings = plan_result.warnings + mapping_result.warnings
    if _is_legacy_configuration(configuration):
        # 1.0 只做结构/类型校验：跳过 v1.1 的电源计划、漏极与运行必填规则，只给一条
        # warning。warning 不得升级为 error，否则历史配置打不开、启动器校验会误红。
        warnings.append(
            ConfigIssue(
                "warning",
                "schema_version",
                "旧版电源计划：可查看，不能创建新运行；请先用迁移器升级到 "
                f"{SUPPORTED_SCHEMA_VERSION}",
            )
        )
    elif require_runnable:
        errors.extend(_validate_current_power_configuration(configuration))
    return ConfigValidationResult(errors=errors, warnings=warnings)


def require_current_schema_version(
    configuration: RunConfiguration,
) -> List[ConfigIssue]:
    """创建/继续运行前的版本门禁：两段都必须等于当前写入版本。

    读取/查看/导出按 :data:`READABLE_SCHEMA_VERSIONS` 处理，1.0 的历史配置因此仍能
    打开；本判据只在“要基于它创建或继续运行”时使用（v1.1 §6.12 规则 16）。
    """
    issues: List[ConfigIssue] = []
    for section, version in (
        ("test_plan.schema_version", configuration.test_plan.schema_version),
        ("run_mapping.schema_version", configuration.run_mapping.schema_version),
    ):
        if version != SUPPORTED_SCHEMA_VERSION:
            issues.append(
                ConfigIssue(
                    "error",
                    section,
                    f"旧版配置不能创建或继续运行，请先用迁移器升级到 "
                    f"{SUPPORTED_SCHEMA_VERSION}；当前为 {version!r}",
                )
            )
    return issues


def validate_cable_loss_configuration(configuration: RunConfiguration) -> ConfigValidationResult:
    """校验线损测量所需的最小配置。

    线损测量只使用信号源、频谱仪和衰减器，不应被
    功放测试的供电通道或驱动功放映射阻塞。完整功放运行仍使用
    ``validate_run_configuration``。
    """
    errors: List[ConfigIssue] = []
    error = lambda path, message: errors.append(ConfigIssue("error", path, message))
    plan = configuration.test_plan
    mapping = configuration.run_mapping

    _validate_version(plan.schema_version, "schema_version", error)
    _finite_list(plan.frequencies, "frequencies.values", error, positive=True)
    if not plan.frequencies:
        error("frequencies.values", "必须是非空列表")
    _number(plan.attenuator_value, "attenuator.value", error, non_negative=True)

    for name in ("signal_generator", "spectrum_analyzer"):
        instrument = mapping.instruments.get(name)
        if instrument is None:
            error(f"instruments.{name}", "缺少仪器配置")
            continue
        _required_text(instrument.visa_address, f"instruments.{name}.visa_address", error)

    return ConfigValidationResult(errors=errors, warnings=[])


def validate_driver_mapping_configuration(configuration: RunConfiguration) -> ConfigValidationResult:
    """校验驱动功放映射所需的最小配置。

    驱动映射只测量信号源到频谱仪之间的 RF 链路。驱动功放可能由
    自带电源供电，因此不能把 DUT 供电通道或驱动功放电源分配作为本
    测量的必填项；驱动功放计划为空即表示自带供电，本系统不控制其电源。
    """
    errors: List[ConfigIssue] = []
    error = lambda path, message: errors.append(ConfigIssue("error", path, message))
    plan = configuration.test_plan
    mapping = configuration.run_mapping

    _validate_version(plan.schema_version, "schema_version", error)
    _finite_list(plan.frequencies, "frequencies.values", error, positive=True)
    if not plan.frequencies:
        error("frequencies.values", "必须是非空列表")
    _number(plan.start_power, "signal_source.start_power", error)
    _number(plan.stop_power, "signal_source.stop_power", error)
    _number(plan.power_step, "signal_source.step", error, positive=True)
    _number(plan.attenuator_value, "attenuator.value", error, non_negative=True)

    for name in ("signal_generator", "spectrum_analyzer"):
        instrument = mapping.instruments.get(name)
        if instrument is None:
            error(f"instruments.{name}", "缺少仪器配置")
            continue
        _required_text(instrument.visa_address, f"instruments.{name}.visa_address", error)

    # 驱放上电计划只校验形状：空序列表示驱放自带供电，不要求本机上电顺序。
    # 1.0 只读，不做该判据，交给创建运行的版本门禁。
    if not _is_legacy_configuration(configuration):
        _validate_plan_document(
            mapping.driver_power_plan, "run_mapping.driver_mode.power_plan", error
        )

    return ConfigValidationResult(errors=errors, warnings=[])


def _as_test_plan(value: Union[TestPlan, Mapping[str, Any]]) -> TestPlan:
    return value if isinstance(value, TestPlan) else TestPlan.from_dict(value)


def _as_run_mapping(value: Union[RunResourceMapping, Mapping[str, Any]]) -> RunResourceMapping:
    return value if isinstance(value, RunResourceMapping) else RunResourceMapping.from_dict(value)


def _channel_plans(value: Any) -> Dict[str, PowerChannelPlan]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(name): PowerChannelPlan.from_dict(item) if isinstance(item, Mapping) else PowerChannelPlan()
        for name, item in value.items()
    }


def _first_mapping(value: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in value:
            return value[key]
    return {}


def _has_legacy_power_fields(dut: Any, driver: Any) -> bool:
    """正式测试方案来源文档是否使用 1.0 的角色化电源字段。"""
    for section in (dut, driver):
        if not isinstance(section, Mapping):
            continue
        if any(key in section for key in ("power_roles", "power_channels")):
            return True
    return False


def _mapping_uses_legacy_power_fields(
    value: Mapping[str, Any], driver: Any
) -> bool:
    """运行映射来源文档是否使用 1.0 的角色化通道清单。"""
    if "dut_power_channels" in value:
        return True
    return isinstance(driver, Mapping) and "power_channels" in driver


def _plan_channel_mappings(plan: Any) -> List["ChannelMapping"]:
    """从 1.1 上电计划投影出旧的 ``ChannelMapping`` 清单（只读兼容）。

    顺序就是冻结的上电顺序；``role`` / ``connection`` 已无业务含义，保持 ``None``。
    消费方应只读取 ``channel``（``supply_id/CHx``），步骤 7–9 结束后这份投影会删除。
    """
    if not isinstance(plan, Mapping):
        return []
    sequence = plan.get("power_sequence")
    if not isinstance(sequence, list):
        return []
    mappings: List[ChannelMapping] = []
    for item in sequence:
        if not isinstance(item, Mapping):
            continue
        supply_id = item.get("supply_id")
        channel = item.get("channel")
        if not isinstance(supply_id, str) or not isinstance(channel, str):
            continue
        mappings.append(ChannelMapping(channel=f"{supply_id}/{channel}"))
    return mappings


def _route_document(value: Any) -> Any:
    """把一条路由收敛成 v1.1 的两段对象。

    正式模型允许调用方直接传入路由值对象（例如 application 的 ``SupplyChannel``），
    而 domain 不能反向依赖 application。``to_dict`` 必须只输出 JSON 原生类型，因此
    这里按 ``supply_id`` / ``channel`` 两个属性投影，不猜其它字段。未知形状原样返回，
    让正式校验报告问题。
    """
    if isinstance(value, Mapping):
        return {"supply_id": value.get("supply_id"), "channel": value.get("channel")}
    # application 的 ``DrainChannel`` 是“可选路由”包装：未配置时写 None，配置后
    # 解包到内层路由。domain 不导入该类型，只按属性形状识别。
    if hasattr(value, "route"):
        inner = value.route
        return None if inner is None else _route_document(inner)
    supply_id = getattr(value, "supply_id", None)
    channel = getattr(value, "channel", None)
    if isinstance(supply_id, str) and isinstance(channel, str):
        return {"supply_id": supply_id, "channel": channel}
    return value


def _drain_document(value: Any) -> Any:
    """把漏极字段收敛成 JSON 原生形状：``None``、单条路由对象或路由对象列表。

    ``drain_channel`` 兼容两种文档形状：旧配置写单条路由，配置页多选漏极时写列表。
    值对象（例如 application 的 ``DrainChannel``）按 ``routes`` 展开；单元素列表仍
    写成单条对象，保持旧文档与黄金样例的原样形状。
    """
    if isinstance(value, (list, tuple)):
        documents = [_route_document(item) for item in value]
        return _single_or_list(documents)
    routes = getattr(value, "routes", None)
    if routes is not None and not isinstance(value, (str, bytes, Mapping)):
        documents = [_route_document(item) for item in routes]
        return _single_or_list(documents)
    return _route_document(value)


def _single_or_list(documents: list[Any]) -> Any:
    if not documents:
        return None
    return documents[0] if len(documents) == 1 else documents


def _plan_document(value: Any) -> Any:
    """把一组上电计划收敛成 v1.1 的 ``{power_sequence, inter_channel_delay_s}``。

    与 :func:`_route_document` 同理：值对象（例如 application 的 ``PowerPlan``）
    不能直接进入 ``to_dict``，否则运行快照无法 JSON 序列化。顺序保持不变。
    """
    if isinstance(value, Mapping):
        sequence = value.get("power_sequence")
        return {
            "power_sequence": (
                [_route_document(item) for item in sequence]
                if isinstance(sequence, (list, tuple))
                else sequence
            ),
            "inter_channel_delay_s": value.get("inter_channel_delay_s"),
        }
    sequence = getattr(value, "power_sequence", None)
    if sequence is not None:
        return {
            "power_sequence": [_route_document(item) for item in sequence],
            "inter_channel_delay_s": getattr(value, "inter_channel_delay_s", None),
        }
    return value


def _list_value(value: Any) -> List[Any]:
    return list(value) if isinstance(value, list) else []


def _mapping_value(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _channel_mappings(value: Any) -> List[ChannelMapping]:
    if not isinstance(value, list):
        return []
    return [
        ChannelMapping.from_dict(item) if isinstance(item, Mapping) else ChannelMapping(channel="")
        for item in value
    ]


def _power_supply_mappings(value: Any) -> Dict[str, PowerSupplyMapping]:
    """读取 ``instruments.power_supplies``；非映射或空值时是空集合。"""
    if not isinstance(value, Mapping):
        return {}
    return {
        str(supply_id): PowerSupplyMapping.from_dict(item)
        for supply_id, item in value.items()
    }


def _validate_version(value: Any, path: str, error: Any) -> None:
    if value not in READABLE_SCHEMA_VERSIONS:
        if isinstance(value, str) and value > SUPPORTED_SCHEMA_VERSION:
            error(
                path,
                f"配置由更新版本写入（{value}），请升级程序；"
                f"当前支持 {SUPPORTED_SCHEMA_VERSION}",
            )
            return
        expected = " 或 ".join(READABLE_SCHEMA_VERSIONS)
        error(path, f"schema_version 必须是 {expected}，收到 {value!r}")


def _is_legacy_plan(plan: TestPlan) -> bool:
    """测试方案是否按 1.0 角色化电源段解释。

    版本是唯一判据：``1.0`` 文档只读兼容；程序内直接构造（``raw`` 为空）且显式写了
    旧字段的对象也按旧结构解释，避免尚未迁移的调用方突然被套上 1.1 的必填规则。
    """
    return plan.schema_version == LEGACY_SCHEMA_VERSION or (
        not plan.raw and plan.legacy_power_fields
    )


def _is_legacy_mapping(mapping: RunResourceMapping) -> bool:
    return mapping.schema_version == LEGACY_SCHEMA_VERSION or (
        not mapping.raw and mapping.legacy_power_fields
    )


def _is_legacy_configuration(configuration: RunConfiguration) -> bool:
    return _is_legacy_plan(configuration.test_plan) or _is_legacy_mapping(
        configuration.run_mapping
    )


def _validate_route_shape(
    value: Any,
    path: str,
    error: Any,
    *,
    allowed_extra: frozenset[str] = frozenset(),
) -> Optional[tuple[str, str]]:
    """校验一条两段路由（``{supply_id, channel}``），返回去空白后的路由。

    ``allowed_extra`` 只给 ``channel_settings`` 用：那里的元素是“路由两段平铺 + 参数”，
    允许的额外字段就是 :data:`CHANNEL_SETTING_FIELDS` 里的参数。纯路由（计划序列、
    漏极引用）仍不接受额外键，``channel_settings`` 也不接受 ``role`` / ``connection``
    或任何未知字段。
    """
    if value is None:
        return None
    if not isinstance(value, Mapping):
        error(path, "供电路由必须是 {supply_id, channel} 对象")
        return None
    unknown = {
        key
        for key in value
        if key not in {"supply_id", "channel"} and key not in allowed_extra
    }
    if unknown:
        error(
            path,
            "供电路由不能包含额外字段: "
            + "、".join(sorted(str(key) for key in unknown)),
        )
        return None
    supply_id = value.get("supply_id")
    channel = value.get("channel")
    if (
        not isinstance(supply_id, str)
        or not supply_id.strip()
        or "/" in supply_id
        or not isinstance(channel, str)
        or not channel.strip()
        or "/" in channel
    ):
        error(path, "供电路由必须是非空且不含 '/' 的 supply_id 与 channel")
        return None
    return supply_id.strip(), channel.strip()


def _validate_drain_routes(
    value: Any,
    path: str,
    error: Any,
) -> tuple[tuple[tuple[str, str], str], ...]:
    """校验漏极字段的形状，返回 ``((supply_id, channel), 字段路径), ...``。

    ``drain_channel`` 既接受单条 ``{supply_id, channel}`` 对象（旧配置），也接受
    对象列表（配置页多选 ``Cal.`` 时写出的形状）。``None`` 与空列表都表示未配置；
    重复路由会让直流功率重复计算，因此按错误报告。
    """
    if value is None:
        return ()
    if isinstance(value, Mapping):
        items = ((value, path),)
    elif isinstance(value, (list, tuple)):
        items = tuple((item, f"{path}[{index}]") for index, item in enumerate(value))
    else:
        error(path, "漏极通道必须是 {supply_id, channel} 对象或对象列表")
        return ()
    routes: list[tuple[tuple[str, str], str]] = []
    seen: set[tuple[str, str]] = set()
    for item, item_path in items:
        route = _validate_route_shape(item, item_path, error)
        if route is None:
            continue
        if route in seen:
            error(item_path, "漏极通道不能重复")
            continue
        seen.add(route)
        routes.append((route, item_path))
    return tuple(routes)


def _validate_channel_settings(values: List[Any], path: str, error: Any) -> None:
    """校验 ``channel_settings`` 的形状：平铺路由 + 参数，路由不得重复。

    这里只回答“这条记录能不能被当成一条物理路由”；工作值是否可读由
    :func:`_validate_current_power_configuration` 按上电顺序判定。
    """
    if not isinstance(values, list):
        error(path, "必须是列表")
        return
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(values):
        item_path = f"{path}[{index}]"
        route = _validate_route_shape(
            item, item_path, error, allowed_extra=CHANNEL_SETTING_FIELDS
        )
        if route is None:
            continue
        if route in seen:
            error(f"{item_path}.channel", f"通道重复分配: {route[0]}/{route[1]}")
        seen.add(route)


def _validate_plan_document(
    value: Any, path: str, error: Any
) -> Optional[tuple[tuple[str, str], ...]]:
    """校验 1.1 冻结上电计划的形状，返回 ``((supply_id, channel), ...)``。

    ``power_sequence`` 与 ``inter_channel_delay_s`` 必须同时出现；缺键按形状错误报告，
    不用空序列/0.0 补全。顺序保留，重复路由与越界间隔一律失败。
    """
    if not isinstance(value, Mapping):
        error(path, "上电计划必须是对象")
        return None
    unknown = {
        key for key in value if key not in {"power_sequence", "inter_channel_delay_s"}
    }
    if unknown:
        error(path, "不能包含未知字段: " + "、".join(sorted(str(key) for key in unknown)))
    missing = [
        key for key in ("power_sequence", "inter_channel_delay_s") if key not in value
    ]
    if missing:
        for key in missing:
            error(f"{path}.{key}", "缺少必需字段")
        return None
    sequence = value["power_sequence"]
    if not isinstance(sequence, list):
        error(f"{path}.power_sequence", "必须是列表")
        return None
    routes: List[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(sequence):
        item_path = f"{path}.power_sequence[{index}]"
        route = _validate_route_shape(item, item_path, error)
        if route is None:
            continue
        if route in seen:
            error(f"{path}.power_sequence", f"同一物理通道不能重复: {route[0]}/{route[1]}")
            continue
        seen.add(route)
        routes.append(route)
    delay = value["inter_channel_delay_s"]
    if not _is_finite_number(delay):
        error(f"{path}.inter_channel_delay_s", "必须是有限数值（秒）")
    elif float(delay) < 0 or float(delay) > MAX_INTER_CHANNEL_DELAY_S:
        error(
            f"{path}.inter_channel_delay_s",
            f"必须在 0 到 {MAX_INTER_CHANNEL_DELAY_S:g} 秒之间",
        )
    return tuple(routes)


def _validate_driver_plan(
    enabled: Any, sequence: Optional[tuple[tuple[str, str], ...]], error: Any
) -> None:
    """驱放冻结计划与 ``enabled`` 一致（规则 7 的正式段版本）。

    空序列是合法状态：它表示驱放由自带电源供电，本系统不控制其电源。
    """
    if sequence is None:
        return
    if enabled is not True and sequence:
        error("driver_mode.power_plan", "驱放测试模式关闭时不能配置驱动功放上电计划")


def _settings_routes(
    values: List[Any], path: str, error: Any
) -> Dict[tuple[str, str], Dict[str, Any]]:
    """把 ``channel_settings`` 读成路由表；重复与非法项已由形状校验报告。"""
    routes: Dict[tuple[str, str], Dict[str, Any]] = {}
    for index, item in enumerate(values):
        if not isinstance(item, Mapping):
            continue
        route = _validate_route_shape(
            item, f"{path}[{index}]", error, allowed_extra=CHANNEL_SETTING_FIELDS
        )
        if route is None:
            continue
        routes.setdefault(route, dict(item))
    return routes


def _validate_current_power_configuration(
    configuration: RunConfiguration,
) -> List[ConfigIssue]:
    """v1.1 完整运行的电源/漏极规则（§6.12 规则 8–13）。"""
    errors: List[ConfigIssue] = []

    def error(path: str, message: str) -> None:
        errors.append(ConfigIssue("error", path, message))

    plan = configuration.test_plan
    mapping = configuration.run_mapping
    # 形状错误已由 validate_run_mapping 报告；这里只取序列，不重复报形状问题。
    sequence = _validate_plan_document(
        mapping.dut_power_plan, "run_mapping.dut_power_plan", lambda _path, _message: None
    )
    settings: Dict[tuple[str, str], Dict[str, Any]] = {}
    for index, item in enumerate(plan.channel_settings):
        if not isinstance(item, Mapping):
            continue
        # 形状与字段白名单已由 validate_test_plan 报告；这里只取路由，不重复报错。
        route = _validate_route_shape(
            item,
            f"test_plan.dut.channel_settings[{index}]",
            lambda _path, _message: None,
            allowed_extra=CHANNEL_SETTING_FIELDS,
        )
        if route is None:
            continue
        settings.setdefault(route, {**item, "_index": index})

    # 规则 8：主功放的 DUT 上电顺序不能为空。
    if sequence is not None and not sequence:
        error(
            "run_mapping.dut_power_plan.power_sequence",
            "主功放运行必须配置非空的 DUT 上电顺序",
        )

    # 运行就绪还要求运行映射至少登记一台本机电源：1.1 的快照按计划路由生成，
    # 保存/迁移阶段允许为空，创建运行前必须已有实际使用的电源。
    if not mapping.power_supplies:
        error(
            "run_mapping.instruments.power_supplies",
            "必须至少登记一台本机供电电源",
        )

    # 规则 9：channel_settings 的路由集合必须等于序列路由集合。
    if sequence is not None and set(sequence) != set(settings):
        error(
            "test_plan.dut.channel_settings",
            "每个上电通道必须恰有一条通道参数，且不能有未上电的参数",
        )

    # 规则 10：序列里的每个路由都必须有可读的工作电压/电流。
    if sequence is not None:
        for route in sequence:
            item = settings.get(route)
            if item is None:
                continue
            index = item.get("_index", 0)
            for field in ("voltage", "current"):
                value = item.get(field)
                if not _is_finite_number(value):
                    error(
                        f"test_plan.dut.channel_settings[{index}].{field}",
                        "上电通道必须提供可读的工作值",
                    )

    # 规则 11/12：主功放必须至少有一个有效漏极，且全部在序列与通道参数里。
    # 文档形状既可以是单条两段路由，也可以是路由列表（配置页多选时写出列表）：
    # 多漏极各自读取电压/电流，效率按它们之和计算。
    drain_path = "test_plan.dut.drain_channel"
    drains = _validate_drain_routes(plan.drain_channel, drain_path, error)
    if not drains:
        error(
            drain_path,
            "主功放运行必须指定至少一个漏极通道（仅用于效率计算，不影响上电顺序）",
        )
    for drain, drain_item_path in drains:
        if sequence is not None and drain not in set(sequence):
            error(drain_item_path, "漏极通道必须包含在 DUT 上电顺序中")
        if drain not in settings:
            error(drain_item_path, "漏极通道必须提供通道参数（电压/电流）")
        # 规则 13：漏极电源必须已在快照中登记，且地址非空。
        supply = mapping.power_supplies.get(drain[0])
        if supply is None:
            error(
                f"run_mapping.instruments.power_supplies.{drain[0]}",
                "漏极通道引用的电源没有登记在运行映射中",
            )
        # 地址非空由 `_validate_power_supplies` 对每条已登记电源统一判定（同一路径），
        # 不在这里重复报告。
    return errors


def _finite_list(values: List[Any], path: str, error: Any, *, positive: bool = False) -> None:
    for index, value in enumerate(values):
        _number(value, f"{path}[{index}]", error, positive=positive)


def _is_finite_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _number(value: Any, path: str, error: Any, *, positive: bool = False, non_negative: bool = False) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        error(path, "必须是有限数值")
        return None
    parsed = float(value)
    if positive and parsed <= 0:
        error(path, "必须大于 0")
    if non_negative and parsed < 0:
        error(path, "必须大于或等于 0")
    return parsed


def _optional_number(value: Any, path: str, error: Any, *, positive: bool = False, non_negative: bool = False) -> Optional[float]:
    """Validate an optional numeric field without treating omission as zero."""
    if value is None:
        return None
    return _number(value, path, error, positive=positive, non_negative=non_negative)


def _validate_channels(channels: Mapping[str, PowerChannelPlan], path: str, error: Any) -> None:
    for name, channel in channels.items():
        channel_path = f"{path}.{name}"
        if not name.strip() or name in PLACEHOLDER_VALUES:
            error(channel_path, "通道名称不能是空值或占位值")
        _optional_number(channel.voltage, f"{channel_path}.voltage", error)
        _optional_number(channel.current, f"{channel_path}.current", error, non_negative=True)
        _optional_number(channel.voltage_protection, f"{channel_path}.voltage_protection", error)
        _optional_number(channel.current_protection, f"{channel_path}.current_protection", error, non_negative=True)


def _validate_identity_field_conflicts(mapping: RunResourceMapping, error: Any) -> None:
    """同一快照条目不得同时写 ``serial`` 与 ``full_code``。

    ``serial`` 只在历史读取边界被识别，读入后立即投影成 ``full_code``；新写出的快照只有
    ``full_code``。两条同时出现说明文档里存在两份互相独立的“完整设备码”，无法判断哪一份
    才是这台设备的身份，连接前的身份核验也就失去意义。只检查来源文档里真实出现的键，
    程序内直接构造的映射没有这条冲突。
    """
    instruments = mapping.raw.get("instruments") if mapping.raw else None
    if not isinstance(instruments, Mapping):
        return
    for name, entry in instruments.items():
        if name == POWER_SUPPLIES_KEY and isinstance(entry, Mapping):
            for supply_id, supply in entry.items():
                if isinstance(supply, Mapping):
                    _check_identity_fields(
                        supply, f"instruments.{POWER_SUPPLIES_KEY}.{supply_id}", error
                    )
            continue
        if isinstance(entry, Mapping):
            _check_identity_fields(entry, f"instruments.{name}", error)


def _check_identity_fields(entry: Mapping[str, Any], path: str, error: Any) -> None:
    if "serial" in entry and "full_code" in entry:
        error(
            path,
            "快照不能同时写 serial 与 full_code：完整设备码只有 full_code 一个字段",
        )


def _validate_power_supplies(mapping: RunResourceMapping, error: Any) -> None:
    """校验运行映射里的电源段。

    - 1.1 的电源快照按计划路由生成：尚未确认上电顺序时集合为空是结构完整的可保存
      状态，运行就绪由规则 8 与 ``_validate_current_power_configuration`` 另行判定；
      1.0 旧结构没有计划可推导，空集合仍报“缺电源段”；
    - 新快照不得同时写旧单数条目，电源只有一个来源。判据是集合**键**在不在，不是
      集合非不非空——``power_supplies: {}`` 加旧条目同样是混写；
    - 旧快照的单数条目仍在时，按旧规则检查它自己的 ``model`` / ``visa_address``，
      缺字段报在旧键上，历史运行不会因为换了结构而“读得进、校验不过”；
    - 每条必须带身份：``label``、``model``、``full_code``、``device_key``、``visa_address``；
    - 集合键必须是该条目的 ``label``（它同时是 ``supply_id/CHx`` 的路由前缀）；
    - 地址不能与其它仪器或另一台电源重复，``device_key`` 不能重复——同一台设备
      在快照里出现两次只能意味着写错了身份。
    """
    supplies = mapping.power_supplies
    instruments = mapping.instruments
    seen_keys: Dict[str, str] = {}
    seen_addresses: Dict[str, str] = {}
    for name in ("signal_generator", "spectrum_analyzer"):
        instrument = instruments.get(name)
        if instrument is not None and isinstance(instrument.visa_address, str):
            seen_addresses.setdefault(instrument.visa_address.strip(), f"instruments.{name}")
    legacy = instruments.get(LEGACY_POWER_SUPPLY_KEY)
    legacy_entry = legacy if isinstance(legacy, InstrumentMapping) else None
    if legacy_entry is not None and mapping.declares_power_supplies():
        error(
            f"instruments.{LEGACY_POWER_SUPPLY_KEY}",
            "新运行映射只写 instruments.power_supplies，不能同时写旧的单数电源条目",
        )
    if legacy_entry is not None:
        _required_text(
            legacy_entry.model, f"instruments.{LEGACY_POWER_SUPPLY_KEY}.model", error
        )
        _required_text(
            legacy_entry.visa_address,
            f"instruments.{LEGACY_POWER_SUPPLY_KEY}.visa_address",
            error,
        )
    if not supplies:
        # 1.1 的运行映射只登记“计划里实际用到的电源”：尚未确认上电顺序时集合为空是
        # 结构完整、但还不能运行的状态（运行就绪由规则 8 与 `_validate_current_power_configuration`
        # 另行判定）。1.0 的旧结构没有计划可以推导，空集合仍然意味着缺电源段。
        if legacy_entry is None and _is_legacy_mapping(mapping):
            error(f"instruments.{POWER_SUPPLIES_KEY}", "必须至少登记一台本机供电电源")
        return
    for supply_id, supply in supplies.items():
        path = f"instruments.{POWER_SUPPLIES_KEY}.{supply_id}"
        if not _required_text(supply_id, path, error):
            continue
        if supply_id != supply.label:
            error(path, f"集合键必须是该电源的 label: {supply_id!r} != {supply.label!r}")
        for field in ("label", "model", "full_code", "device_key", "visa_address"):
            _required_text(getattr(supply, field), f"{path}.{field}", error)
        if isinstance(supply.device_key, str) and supply.device_key.strip():
            key = supply.device_key.strip()
            if key in seen_keys:
                error(
                    f"{path}.device_key",
                    f"同一设备不能登记为两台电源: {key}（另见 {seen_keys[key]}）",
                )
            else:
                seen_keys[key] = path
        if isinstance(supply.visa_address, str) and supply.visa_address.strip():
            address = supply.visa_address.strip()
            if address in seen_addresses:
                error(
                    f"{path}.visa_address",
                    f"多台仪器共用同一 VISA 地址: {address}（另见 {seen_addresses[address]}）",
                )
            else:
                seen_addresses[address] = path


def _validate_channel_routing(mapping: RunResourceMapping, error: Any) -> None:
    """通道必须写成 ``supply_id/CHx``，且前缀指向快照里的电源。

    新快照的每个通道值都是显式路由（与 ``measurement_factory`` 的 ``supply/channel``
    一致）。唯一的例外是已冻结的历史快照：它本来就写成裸通道名 ``CH1``，用旧的单数
    电源条目指代唯一电源，硬性要求加上路由会让历史运行再也打不开。因此只在旧快照
    （不带 ``instruments.power_supplies``）下放行裸通道名。
    """
    legacy_snapshot = _is_legacy_power_snapshot(mapping)
    for field in ("dut_power_channels", "driver_mode.power_channels"):
        channels = (
            mapping.dut_power_channels
            if field == "dut_power_channels"
            else mapping.driver_power_channels
        )
        for index, item in enumerate(channels):
            token = item.channel
            if not isinstance(token, str) or not token:
                continue
            if "/" not in token:
                if not legacy_snapshot:
                    error(
                        f"{field}[{index}].channel",
                        f"电源通道必须写成 supply_id/CHx: {token!r}",
                    )
                continue
            supply_id, _, channel = token.partition("/")
            if supply_id not in mapping.power_supplies:
                error(
                    f"{field}[{index}].channel",
                    f"通道路由指向快照里没有的电源: {supply_id!r}",
                )
            elif not channel.strip():
                error(f"{field}[{index}].channel", f"通道路由缺少通道名: {token!r}")


def _is_legacy_power_snapshot(mapping: RunResourceMapping) -> bool:
    """快照只带旧的单数 ``instruments.power_supply``，没有电源集合（连空集合都没有）。"""
    if mapping.declares_power_supplies():
        return False
    return isinstance(mapping.instruments.get(LEGACY_POWER_SUPPLY_KEY), InstrumentMapping)


def _validate_mappings(mappings: List[ChannelMapping], path: str, error: Any) -> None:
    seen = set()
    for index, item in enumerate(mappings):
        item_path = f"{path}[{index}]"
        if not _required_text(item.channel, f"{item_path}.channel", error):
            continue
        if item.channel in seen:
            error(f"{item_path}.channel", f"通道重复分配: {item.channel}")
        seen.add(item.channel)
        _required_text(item.connection, f"{item_path}.connection", error)


def _validate_driver_mode(
    enabled: Any,
    channels: Any,
    error: Any,
    *,
    path: str = "driver_mode.power_channels",
) -> None:
    """校验驱放测试模式与驱动通道是否一致。

    关闭驱放测试模式时不得配置驱动功放通道。开启时通道可有可无：通道为空表示驱放
    自带供电（本系统不控制其电源），通道非空表示由本机直流电源供电。``path`` 区分
    1.0 的 ``power_channels`` 与 1.1 的 ``channel_settings``，判据本身只有一份。
    """
    if enabled is not True and channels:
        error(path, "驱放测试模式关闭时不能配置驱动功放通道")


def _required_text(value: Any, path: str, error: Any) -> bool:
    if not isinstance(value, str) or not value.strip() or value.strip() in PLACEHOLDER_VALUES:
        error(path, "必须填写非空实际值")
        return False
    return True


# 便于调用方使用更短的通用名称。
validate_plan = validate_test_plan
validate_mapping = validate_run_mapping
