"""Application-level assembly for hardware and offline measurement ports.

The factory keeps hardware construction out of measurement services.  The
hardware path uses only explicitly configured instrument addresses, power
roles and physical channels: every assigned power channel must declare its
``gate``/``drain`` role in the configuration.  Automatic power-supply
discovery stays in the hardware-smoke entry points
(:mod:`hardware.power_supply_discovery`) and is never used for production
assembly.

Power supplies are a **dynamic set**: ``instruments.power_supplies`` is keyed by
``supply_id`` (the field short label, e.g. ``DP8B1519``) and every physical
route is the explicit ``supply_id/CHx`` token.  A routed supply is connected,
identity-checked and initial-output-checked on its own, so 1, 2 or 5 supplies
need no code change.  ``test_instrument_routing`` forbids ``CH1``/``CH2`` as
assembly constants, so the power driver model and the channel query form live
in the constants below, not in the per-supply loop.

Logs always name a supply by its field short label: channel actions carry the
routing token (``DP8B1519/CH1``) and are printed as the short label plus channel
(``supply_channel_text``), and every connection writes one audit line with the
short label, model, full serial and device key plus the raw ``*IDN?`` reply and
the identity verdict, so a log entry maps to the physical label, can be traced
to the exact device and says out loud whether the two identities agree.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import pyvisa

from application.errors import (
    CleanupError,
    DeviceCapabilityError,
    DeviceConnectionError,
    FailureRecord,
    ProtocolError,
    cause_of,
)
from domain.configuration.rules import assignment_supply_id
from domain.instruments.labels import (
    IDENTITY_MISMATCHED,
    supply_channel_text,
    supply_identity_text,
    supply_identity_verdict,
)
from infrastructure.logging.app_logging import get_logger

from .drivers import ScpiPowerSupplyDriver, ScpiSignalGeneratorDriver, ScpiSpectrumAnalyzerDriver
from .measurement_adapter import PortMeasurementAdapter
from .session import ManagedInstrumentSession
from .transport import VisaScpiTransport
from .simulation import (
    SimulatedPowerSupply,
    SimulatedSignalGenerator,
    SimulatedSpectrumAnalyzer,
)

# 生产供电只接受配置中显式声明的角色；不再按 CH1/CH2 推导。
POWER_ROLES = ("gate", "drain")
# 本项目生产供电只使用 DP832A；身份不符仍属于能力问题，不是连接问题。
POWER_SUPPLY_MODEL = "DP832A"
# 初始输出检查逐通道查询：每台电源只查自己已配置的通道（不是固定 CH1/CH2）。
POWER_OUTPUT_QUERY = "OUTP? {channel}"
# 通道状态既能回 "ON"/"OFF"，也可能回 1/0：只有这些值表示输出已关闭。
POWER_OUTPUT_OFF_STATES = frozenset({"0", "OFF"})

logger = get_logger(__name__)


def create_measurement_port(
    config_path: str | None = None,
    *,
    mode: str = "hardware",
    power_channels: Mapping[str, str] | None = None,
    driver_power_channels: Mapping[str, str] | None = None,
    power_settings: Mapping[str, Mapping[str, float]] | None = None,
    recorder: Any = None,
):
    """Create a service port for hardware or offline simulation mode.

    Hardware construction owns the VISA manager and all resources through the
    returned session.  Validation of addresses, roles and channels happens
    before any VISA resource is opened.
    """
    if mode == "hardware":
        if config_path is None:
            raise ValueError("hardware 模式必须提供 config_path")
        return _create_hardware_measurement_port(config_path, recorder=recorder)
    if mode != "simulation":
        raise ValueError(f"不支持的测量端口模式: {mode}")

    dut_channels = dict(power_channels or {})
    driver_channels = dict(driver_power_channels or {})
    session_channels = dut_channels or {"gate": "A", "drain": "B"}
    session = ManagedInstrumentSession(
        SimulatedSignalGenerator(recorder),
        SimulatedSpectrumAnalyzer(recorder=recorder),
        SimulatedPowerSupply(recorder),
        session_channels,
    )
    session.validate()
    session.connect()
    session.prepare()
    return PortMeasurementAdapter(
        session,
        dut_power_channels=dut_channels,
        driver_power_channels=driver_channels,
        power_settings=power_settings,
    )


class _VisaSession(ManagedInstrumentSession):
    """Session variant that also releases the ResourceManager it owns."""

    def __init__(self, resource_manager, *devices, **kwargs):
        super().__init__(*devices, **kwargs)
        self.resource_manager = resource_manager
        self._resource_manager_closed = False

    def close(self, *args, **kwargs):
        # Keep the manager alive when device cleanup fails: SafetyInstrument-
        # Session deliberately retains failed resources so a later close()
        # can retry them.
        if self.state.value != "cleaned":
            super().close(*args, **kwargs)
        if not self._resource_manager_closed:
            self.resource_manager.close()
            self._resource_manager_closed = True

    @property
    def resources_closed(self) -> bool:
        return self._resource_manager_closed and self.state.value == "cleaned"


class _ConfiguredPowerSupply:
    """Power-supply port that routes explicit ``supply/channel`` tokens.

    Every method mirrors :class:`instrument.ports.PowerSupplyPort`; there is
    no attribute forwarding, so an unknown operation or channel fails at the
    call boundary instead of being routed by method name.

    The mapping key is the ``supply_id`` and is the only routing identity: a
    token physically routes by that key and never by the display label.
    ``supply_labels`` gives the field short label of each routed supply for logs
    and failure records (``DP8B1519``); it defaults to the routing key.
    """

    def __init__(
        self,
        supplies: Mapping[str, Any],
        *,
        supply_labels: Mapping[str, str] | None = None,
    ):
        if not supplies:
            raise ValueError("生产电源路由至少需要一个已配置电源")
        self.supplies = dict(supplies)
        labels = dict(supply_labels or {})
        self.supply_labels = {
            name: _short_label(labels.get(name), name) for name in self.supplies
        }

    def label(self, supply_name: str) -> str:
        """返回已配置电源的短标签；未配置的电源返回原值，供错误消息使用。"""
        return self.supply_labels.get(supply_name) or _short_label(None, supply_name)

    def connect(self, *, timeout_s: float = 10.0) -> None:
        for name, supply in self.supplies.items():
            logger.info("连接生产电源 %s", self.label(name))
            supply.connect(timeout_s=timeout_s)

    def close(self, *, timeout_s: float = 5.0) -> None:
        failures = []
        for name, supply in self.supplies.items():
            try:
                supply.close(timeout_s=timeout_s)
            except Exception as error:  # 单个电源关闭失败不阻止释放其余电源
                logger.warning("生产电源 %s 关闭失败: %s", self.label(name), error)
                failures.append(FailureRecord(self.label(name), error))
        if failures:
            raise CleanupError("power supply close failed", failures) from cause_of(failures)

    def set_voltage_v(self, channel: str, voltage_v: float, *, timeout_s: float = 5.0) -> None:
        supply, physical_channel = self._route(channel)
        supply.set_voltage_v(physical_channel, voltage_v, timeout_s=timeout_s)

    def set_current_limit_a(self, channel: str, current_a: float, *, timeout_s: float = 5.0) -> None:
        supply, physical_channel = self._route(channel)
        supply.set_current_limit_a(physical_channel, current_a, timeout_s=timeout_s)

    def set_voltage_protection_state(
        self, channel: str, enabled: bool, *, timeout_s: float = 5.0
    ) -> None:
        supply, physical_channel = self._route(channel)
        supply.set_voltage_protection_state(physical_channel, enabled, timeout_s=timeout_s)

    def set_current_protection_state(
        self, channel: str, enabled: bool, *, timeout_s: float = 5.0
    ) -> None:
        supply, physical_channel = self._route(channel)
        supply.set_current_protection_state(physical_channel, enabled, timeout_s=timeout_s)

    def set_voltage_protection(
        self, channel: str, voltage_protection_v: float, *, timeout_s: float = 5.0
    ) -> None:
        supply, physical_channel = self._route(channel)
        supply.set_voltage_protection(
            physical_channel, voltage_protection_v, timeout_s=timeout_s
        )

    def set_current_protection(
        self, channel: str, current_protection_a: float, *, timeout_s: float = 5.0
    ) -> None:
        supply, physical_channel = self._route(channel)
        supply.set_current_protection(
            physical_channel, current_protection_a, timeout_s=timeout_s
        )

    def set_output_enabled(self, channel: str, enabled: bool, *, timeout_s: float = 5.0) -> None:
        supply, physical_channel = self._route(channel)
        supply.set_output_enabled(physical_channel, enabled, timeout_s=timeout_s)

    def read_voltage_v(self, channel: str, *, timeout_s: float = 5.0) -> float:
        supply, physical_channel = self._route(channel)
        return supply.read_voltage_v(physical_channel, timeout_s=timeout_s)

    def read_current_a(self, channel: str, *, timeout_s: float = 5.0) -> float:
        supply, physical_channel = self._route(channel)
        return supply.read_current_a(physical_channel, timeout_s=timeout_s)

    def set_voltage(self, channel: str, voltage_v: float, *, timeout_s: float = 5.0) -> None:
        self.set_voltage_v(channel, voltage_v, timeout_s=timeout_s)

    def set_current_limit(self, channel: str, current_a: float, *, timeout_s: float = 5.0) -> None:
        self.set_current_limit_a(channel, current_a, timeout_s=timeout_s)

    def read_voltage(self, channel: str, *, timeout_s: float = 5.0) -> float:
        return self.read_voltage_v(channel, timeout_s=timeout_s)

    def read_current(self, channel: str, *, timeout_s: float = 5.0) -> float:
        return self.read_current_a(channel, timeout_s=timeout_s)

    def _route(self, channel: str):
        try:
            supply_name, physical_channel = str(channel).split("/", 1)
        except ValueError as error:
            raise ValueError(f"未配置的生产电源通道: {channel}") from error
        supply = self.supplies.get(supply_name)
        if supply is None:
            raise ValueError(f"未配置的生产电源通道: {channel}")
        return supply, physical_channel


def _open_resource_manager() -> Any:
    """创建 VISA 资源管理器；驱动库或底层库失败都属于连接问题。

    只翻译 pyvisa 库错误与操作系统级错误；调用参数错误仍保持 ``TypeError`` /
    ``ValueError``，不会被伪装成“设备连不上”。
    """
    try:
        return pyvisa.ResourceManager()
    except (pyvisa.errors.Error, OSError) as error:
        raise DeviceConnectionError(f"无法创建 VISA 资源管理器: {error}") from error


def _open_device(manager: Any, address: str, label: str) -> Any:
    """打开一个 VISA 资源；连接层失败统一成 :class:`DeviceConnectionError`。

    只翻译 pyvisa 的连接层错误，其余异常原样上抛：装配层的配置错误仍然是
    ``ValueError``，不会被伪装成设备连接问题。
    """
    try:
        return manager.open_resource(address)
    except (pyvisa.errors.VisaIOError, OSError) as error:
        raise DeviceConnectionError(f"无法连接{label} {address}: {error}") from error


def _query(resource: Any, command: str, label: str) -> str:
    """在已打开的 VISA 资源上查询；命令层失败属于协议错误。"""
    try:
        return resource.query(command).strip()
    except (pyvisa.errors.VisaIOError, OSError) as error:
        raise ProtocolError(f"{label} 查询失败 ({command}): {error}") from error


def _create_hardware_measurement_port(config_path: str, *, recorder=None):
    from infrastructure.config.json_io import load_config_file

    config = load_config_file(config_path)
    # 装配校验（地址、角色、通道）必须在创建任何 VISA 资源之前完成。
    plan = _hardware_assembly_plan(config)
    manager = _open_resource_manager()
    resources = []
    try:
        signal_resource = _open_device(manager, plan["signal_address"], "信号源")
        resources.append(signal_resource)
        spectrum_resource = _open_device(manager, plan["spectrum_address"], "频谱仪")
        resources.append(spectrum_resource)

        configured_supplies = {}
        for power_name in plan["supply_names"]:
            power_config = plan["power_configs"][power_name]
            label = f"生产电源 {plan['supply_labels'][power_name]}"
            resource = _open_device(manager, power_config["address"], label)
            resources.append(resource)
            identity = _query(resource, "*IDN?", label)
            # 审计用的一行：这台电源在配置里的完整身份（短标签 · 型号 · 完整序列号 ·
            # 设备键）、设备自己应答的 *IDN? 原文，以及两边的核对结论。日志里短标签负责
            # 快速对应实物，完整序列号与结论负责最终追溯（步骤 14）。
            verdict_status, verdict = supply_identity_verdict(
                power_config.get("serial"), identity
            )
            log_identity = (
                logger.warning if verdict_status == IDENTITY_MISMATCHED else logger.info
            )
            log_identity(
                "生产电源身份核对: %s；设备应答 *IDN? %s；核对结果 %s",
                plan["supply_details"][power_name],
                identity,
                verdict,
            )
            if POWER_SUPPLY_MODEL not in identity.upper():
                # 型号不符时设备仍然可用，但不能用于本项目的供电动作：
                # 这是能力/前提问题，不是连接问题。
                raise DeviceCapabilityError(
                    f"{label} 设备身份不是 {POWER_SUPPLY_MODEL}: {identity}"
                )
            # 初始输出检查覆盖这台电源配置的全部通道：输出开着就禁止进入测量，
            # 无论该通道这次有没有被分配。错误与日志都带 ``short-label/CHx``。
            supply_label = plan["supply_labels"][power_name]
            checked = ", ".join(
                f"{supply_label}/{channel}"
                for channel in _configured_channels(power_config, power_name)
            )
            states = []
            for channel in _configured_channels(power_config, power_name):
                states.append(
                    _query(
                        resource,
                        POWER_OUTPUT_QUERY.format(channel=channel),
                        f"{supply_label}/{channel}",
                    )
                )
            if any(state.upper() not in POWER_OUTPUT_OFF_STATES for state in states):
                raise DeviceCapabilityError(
                    f"{label} 初始输出未关闭，禁止上电测量（检查通道: {checked}）"
                )
            logger.info("生产电源 %s 连接成功，检查通道: %s", supply_label, checked)
            configured_supplies[power_name] = ScpiPowerSupplyDriver(
                VisaScpiTransport(resource, recorder=recorder)
            )

        signal_config = plan["signal_config"]
        signal = ScpiSignalGeneratorDriver(
            VisaScpiTransport(signal_resource, recorder=recorder),
            min_frequency_hz=signal_config.get("min_frequency_hz"),
            max_frequency_hz=signal_config.get("max_frequency_hz"),
            min_power_dbm=signal_config.get("min_power_dbm"),
            max_power_dbm=signal_config.get("max_power_dbm"),
        )
        spectrum = ScpiSpectrumAnalyzerDriver(
            VisaScpiTransport(spectrum_resource, recorder=recorder)
        )
        # 路由键是 ``supply_id``；标签只用于日志、关闭失败记录与错误消息。
        power = _ConfiguredPowerSupply(
            configured_supplies, supply_labels=plan["supply_labels"]
        )
        session = _VisaSession(
            manager,
            signal,
            spectrum,
            power,
            plan["all_channels"],
            # 通道动作日志用 ``短标签/CHx``；物理路由仍是上面的 token。
            power_channel_labels=plan["channel_labels"],
        )
        session.validate()
        try:
            session.connect()
        except (pyvisa.errors.VisaIOError, OSError) as error:
            # 驱动层没翻译的底层连接失败仍必须属于连接分类。
            raise DeviceConnectionError(f"设备连接失败: {error}") from error
        session.prepare()
        return PortMeasurementAdapter(
            session,
            dut_power_channels=plan["dut_channels"],
            driver_power_channels=plan["driver_channels"],
            power_settings=plan["power_settings"],
            qualified_power_channels=True,
            emergency_channels=plan["emergency_channels"],
            power_channel_labels=plan["channel_labels"],
        )
    except Exception:  # 装配失败必须释放已打开的 VISA 资源后原样抛出
        for resource in reversed(resources):
            try:
                resource.close()
            except Exception as error:  # 释放失败只记录，装配异常才是终态
                logger.warning("装配失败后的 VISA 资源关闭也失败: %s", error)
        try:
            manager.close()
        except Exception as error:  # ResourceManager 释放失败同样只记录
            logger.warning("装配失败后的 ResourceManager 关闭也失败: %s", error)
        raise


def _hardware_assembly_plan(config: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the hardware assembly and return the explicit construction plan.

    该函数只读取配置，不创建 VISA 资源；地址重复、角色缺失/重复、通道不存在
    都在连接任何仪器之前失败。

    返回的 ``supply_labels`` 是路由键（``supply_id``）到现场短标签的映射，只用于显示与
    错误消息；``supply_details`` 是同一台电源的详情/审计文本（短标签 · 型号 · 完整序列号 ·
    设备键），逐台电源的身份核对日志直接用它，不再从配置里各挑各的字段；
    ``channel_labels`` 把每个路由 token（``supply_id/CHx``）映射到日志用的
    ``短标签/CHx``，通道动作日志因此说人话，而路由仍只认 token。
    """
    instruments = _mapping_section(config, "instruments")
    signal_config = _mapping_section(instruments, "signal_generator", "instruments")
    spectrum_config = _mapping_section(instruments, "spectrum_analyzer", "instruments")
    power_configs = _mapping_section(instruments, "power_supplies", "instruments", optional=True)
    if not signal_config.get("enabled", True) or not spectrum_config.get("enabled", True):
        raise ValueError("硬件测量路径要求信号源和频谱仪均已启用")

    signal_address = _required_address(signal_config, "信号源")
    spectrum_address = _required_address(spectrum_config, "频谱仪")
    if signal_address == spectrum_address:
        raise ValueError(f"信号源与频谱仪的 VISA 地址重复: {signal_address}")
    enabled_names = []
    for name, item in power_configs.items():
        if not isinstance(item, Mapping):
            raise ValueError(f"生产电源 {name} 必须配置对象")
        if item.get("enabled", True):
            enabled_names.append(name)
    assignments = _mapping_section(config, "power_supply_assignment")
    dut_assignment = _mapping_section(
        assignments, "dut_amplifier", "power_supply_assignment", optional=True
    )
    driver_assignment = _mapping_section(
        assignments, "driver_amplifier", "power_supply_assignment", optional=True
    )
    assigned_names = set()
    for group in (dut_assignment, driver_assignment):
        for supply in _supply_assignments(group).values():
            if not isinstance(supply, Mapping):
                raise ValueError("供电分配条目必须是对象")
            # 分配引用与配置页、文件校验共用同一个判据：``supply_id`` 为准，只有
            # 完全没有该字段的旧记录才回落读一次 ``name``。返回值原样保留，供步骤 13
            # 的迁移器区分“旧槽位名”和“未迁移的引用”。
            name = _assigned_supply_name(supply)
            if name is None:
                continue
            assigned_names.add(name)
    if not enabled_names and not assigned_names:
        raise ValueError("硬件测量路径至少需要一个电源模板配置")
    if not assigned_names:
        raise ValueError("生产硬件路径必须显式配置电源角色和通道")

    # 分组时按 ``supply_id`` 匹配 ``power_supplies`` 的键；同时接受该电源自己的
    # ``label`` 和旧文件残留的 ``visual`` 槽位（``PS1`` 等）。两个来源都必须是
    # 唯一的一台电源且身份一致，绝不按顺序或长度猜。解析在“已启用”检查之前：
    # 别名先落到一台确定的设备上，再判断它有没有被启用。短标签只用于显示，
    # 不参与路由。
    visual_names = _supply_visual_names(power_configs)
    supply_keys = {}
    supply_labels = {}
    for reference in sorted(assigned_names):
        supply_key = _resolve_supply_key(reference, enabled_names, power_configs, visual_names)
        supply_keys[reference] = supply_key
        supply_labels[supply_key] = _short_label(
            power_configs[supply_key].get("label"), supply_key
        )
    duplicate_labels = _duplicate_labels(supply_labels)
    if duplicate_labels:
        # 同一个显示名对应两台电源时，日志与错误会指向错误的设备编号，而路由
        # 本身仍然正确。这里直接拒绝，而不是冒险让操作者认错电源。
        raise ValueError(
            "生产电源短标签重复: " + ", ".join(sorted(duplicate_labels))
        )

    # 身份核对日志用的详情文本（短标签 · 型号 · 完整序列号 · 设备键）：在装配计划里算一次，
    # 逐台电源的日志与错误因此共用同一份事实，不各自从配置里挑字段（步骤 14）。
    supply_details = {
        name: supply_identity_text(
            supply_labels[name],
            power_configs[name].get("model"),
            power_configs[name].get("serial"),
            power_configs[name].get("device_key"),
        )
        for name in supply_labels
    }

    supply_addresses = {}
    for reference in sorted(assigned_names):
        power_name = supply_keys[reference]
        power_label = supply_labels[power_name]
        power_config = power_configs[power_name]
        address = _required_address(power_config, f"生产电源 {power_label}")
        if address == signal_address:
            raise ValueError(f"生产电源 {power_label} 与信号源地址重复: {address}")
        if address == spectrum_address:
            raise ValueError(f"生产电源 {power_label} 与频谱仪地址重复: {address}")
        for other_name, other_address in supply_addresses.items():
            if address == other_address:
                raise ValueError(
                    f"生产电源 {power_label} 与"
                    f"生产电源 {supply_labels[other_name]} 地址重复: {address}"
                )
        supply_addresses[power_name] = address

    dut_channels, dut_settings, dut_channel_labels = _assigned_channels(
        dut_assignment, power_configs, supply_keys, supply_labels, "DUT"
    )
    driver_channels, driver_settings, driver_channel_labels = _assigned_channels(
        driver_assignment, power_configs, supply_keys, supply_labels, "驱动"
    )
    overlap = set(dut_channels.values()) & set(driver_channels.values())
    if overlap:
        raise ValueError(
            "DUT 与驱动功放占用了同一个电源通道: " + ", ".join(sorted(overlap))
        )
    supply_names = sorted(supply_labels)
    return {
        "signal_config": signal_config,
        "signal_address": signal_address,
        "spectrum_address": spectrum_address,
        "power_configs": power_configs,
        "supply_names": supply_names,
        "supply_labels": supply_labels,
        "supply_details": supply_details,
        "dut_channels": dut_channels,
        "driver_channels": driver_channels,
        "power_settings": {**driver_settings, **dut_settings},
        "all_channels": {**driver_channels, **dut_channels},
        # 路由 token -> 日志里的 ``短标签/CHx``：路由值不变，只是给日志与错误一份可读写法。
        "channel_labels": {**driver_channel_labels, **dut_channel_labels},
        "emergency_channels": _emergency_channels(
            supply_names, driver_channels, dut_channels
        ),
    }


def _mapping_section(
    parent: Mapping[str, Any], key: str, prefix: str = "", *, optional: bool = False
) -> Mapping[str, Any]:
    section = parent.get(key)
    if section is None and optional:
        return {}
    if not isinstance(section, Mapping):
        path = f"{prefix}.{key}" if prefix else key
        raise ValueError(f"硬件配置缺少对象段: {path}")
    return section


def _supply_assignments(assignment: Mapping[str, Any]) -> Mapping[str, Any]:
    supplies = assignment.get("supplies", {})
    if not isinstance(supplies, Mapping):
        raise ValueError("供电分配必须提供 supplies 对象")
    return supplies


def _configured_channels(power_config: Mapping[str, Any], power_name: str) -> Mapping[str, Any]:
    channels = power_config.get("channels")
    if not isinstance(channels, Mapping) or not channels:
        raise ValueError(f"生产电源 {power_name} 必须配置非空通道对象")
    return channels


def _required_address(instrument_config: Mapping[str, Any], label: str) -> str:
    address = instrument_config.get("address")
    if not isinstance(address, str) or not address.strip():
        raise ValueError(f"{label} 必须配置 address")
    return address.strip()


def _assigned_channels(
    assignment: Mapping[str, Any],
    power_configs: Mapping[str, Any],
    supply_keys: Mapping[str, str],
    supply_labels: Mapping[str, str],
    label: str,
) -> tuple[dict[str, str], dict[str, dict[str, float]], dict[str, str]]:
    """Resolve assigned logical roles to explicit ``supply_id/channel`` tokens.

    ``supply_keys`` 把分配里的引用（``supply_id`` / ``label`` / 旧槽位）解析成
    ``power_supplies`` 的集合键。物理路由的 token 只由集合键拼成：短标签是显示
    文本，可能被改动或与键不同，绝不能出现在被路由的通道值里。错误消息用短标签。

    第三个返回值是同一批 token 的**日志写法**（``短标签/CHx``）。它在拼 token 的同一处
    生成，因为这里同时握着集合键与短标签两个来源；调用方不必、也不许从 token 反解析。
    """
    channels = {}
    settings = {}
    channel_labels = {}
    for supply in _supply_assignments(assignment).values():
        if not isinstance(supply, Mapping):
            raise ValueError(f"{label} 供电分配条目必须是对象")
        reference = _assigned_supply_name(supply)
        power_name = supply_keys.get(reference)
        power_config = power_configs.get(power_name)
        if not isinstance(power_config, Mapping):
            raise ValueError(f"供电分配引用了不存在的电源: {reference}")
        power_label = supply_labels.get(power_name, str(power_name))
        configured_channels = _configured_channels(power_config, power_name)
        for channel in _channel_list(supply, power_label):
            channel_config = configured_channels.get(channel)
            if not isinstance(channel_config, Mapping):
                raise ValueError(f"供电分配引用了不存在的通道: {power_label}/{channel}")
            token = f"{power_name}/{channel}"
            channel_labels[token] = supply_channel_text(power_name, channel, power_label)
            role = _explicit_role(channel_config, token)
            if role in channels and channels[role] != token:
                raise ValueError(
                    f"供电角色重复配置: {role} 同时出现在 {channels[role]} 和 {token}"
                )
            channels[role] = token
            settings[role] = _channel_settings(channel_config, token)
    if channels and set(channels) != {"gate", "drain"}:
        raise ValueError(f"{label} 电源必须同时配置 gate 和 drain 通道")
    return channels, settings, channel_labels


def _assigned_supply_name(supply: Mapping[str, Any]) -> str | None:
    """供电分配引用的电源名，原样返回（不裁剪、不猜测）。

    ``domain.configuration.rules.assignment_supply_id`` 是引用判据的唯一实现，
    这里只把它返回值再次收紧成“非空字符串”，避免非字符串进入集合键。
    """
    name = assignment_supply_id(supply)
    if not isinstance(name, str) or not name.strip():
        return None
    return name


def _resolve_supply_key(
    reference: str,
    enabled_names: Sequence[str],
    power_configs: Mapping[str, Any],
    visual_names: Mapping[str, list[str]],
) -> str:
    """把分配里的电源引用解析成 ``power_supplies`` 的集合键。

    ``supply_id`` 就是集合键，直接命中。旧文件仍可能写 ``label``（迁移前的显示名）
    或 ``visual`` 槽位（``PS1`` 等）：它们必须唯一指向一台电源，且这台电源必须已
    启用、身份自洽。任何歧义都明确失败——按位置或按数量取一台，等于把电接到
    不知道哪台设备上。

    歧义判定不只看“已启用的候选”：一个名字既是某台的集合键、又是另一台的
    ``label`` 时，两台都没启用也不能悄悄挑一台。因此命中集合键时仍要求名字类
    别名不再指向**其它**电源。
    """
    if reference in power_configs:
        aliases = [
            name
            for name in _alias_supply_keys(reference, power_configs, visual_names)
            if name != reference
        ]
        if aliases:
            raise ValueError(
                f"供电分配引用的电源名 {reference} 同时是另一台电源的 label/visual: "
                + ", ".join(
                    _short_label(power_configs[name].get("label"), name) for name in aliases
                )
                + f"；请改写成 supply_id {reference} 之外的唯一写法"
            )
        key = reference
    else:
        candidates = _alias_supply_keys(reference, power_configs, visual_names)
        if not candidates:
            raise ValueError(f"供电分配引用了不存在的电源: {reference}")
        if len(candidates) > 1:
            raise ValueError(
                f"供电分配引用的电源名 {reference} 对应多台电源: "
                + ", ".join(sorted(candidates))
                + "；请改写成各自的 supply_id"
            )
        key = candidates[0]
    if key not in enabled_names:
        raise ValueError(f"供电分配引用了未启用的电源: {reference}")
    return key


def _alias_supply_keys(
    reference: str,
    power_configs: Mapping[str, Any],
    visual_names: Mapping[str, list[str]],
) -> list[str]:
    """按 ``label`` 与旧 ``visual`` 槽位找出引用可能指向的电源键。"""
    matched = [
        name
        for name, config in power_configs.items()
        if isinstance(config, Mapping)
        and _short_label(config.get("label"), name) == reference
    ]
    matched.extend(visual_names.get(reference, ()))
    return list(dict.fromkeys(matched))


def _supply_visual_names(power_configs: Mapping[str, Any]) -> dict[str, list[str]]:
    """旧文件里残留的 ``visual`` 槽位名到电源键的索引（只用于引用解析）。"""
    index: dict[str, list[str]] = {}
    for name, config in power_configs.items():
        if not isinstance(config, Mapping):
            continue
        visual = config.get("visual")
        if isinstance(visual, str) and visual.strip():
            index.setdefault(visual.strip(), []).append(name)
    return index


def _duplicate_labels(supply_labels: Mapping[str, str]) -> set[str]:
    """出现不止一次的短标签：显示名重复时日志无法唯一指向一台电源。"""
    seen: set[str] = set()
    duplicated: set[str] = set()
    for label in supply_labels.values():
        if label in seen:
            duplicated.add(label)
        seen.add(label)
    return duplicated


def _short_label(label: Any, fallback: Any) -> str:
    """电源的现场短标签：配置里的 ``label`` 优先，没有时才用集合键。"""
    if isinstance(label, str) and label.strip():
        return label.strip()
    return str(fallback)


def _channel_list(supply: Mapping[str, Any], power_label: str) -> list[str]:
    channels = supply.get("channel")
    if not isinstance(channels, list) or not channels:
        raise ValueError(f"供电分配必须提供非空通道列表: {power_label}")
    names = [channel for channel in channels if isinstance(channel, str)]
    if len(names) != len(channels):
        raise ValueError(f"供电分配的通道必须是字符串: {power_label}")
    return names


def _explicit_role(channel_config: Mapping[str, Any], token: str) -> str:
    role = channel_config.get("role")
    normalized = role.strip().lower() if isinstance(role, str) else ""
    if normalized not in POWER_ROLES:
        raise ValueError(f"电源通道未配置显式 gate/drain 角色: {token}")
    return normalized


def _channel_settings(channel_config: Mapping[str, Any], token: str) -> dict[str, float]:
    try:
        return {
            "voltage_v": float(channel_config["voltage"]["value"]),
            "current_a": float(channel_config["current"]["value"]),
        }
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"电源通道缺少有效的电压/电流设置: {token}") from error


def _emergency_channels(
    supply_names: Sequence[str],
    *role_groups: Mapping[str, str],
) -> tuple[tuple[int, str], ...]:
    """Return the outputs a shutdown must switch off, drain before gate.

    Only tokens assigned to a declared role are returned: those are the outputs
    the port can energize, and they are the only channels the power drivers
    support.  Order follows the safe off sequence (``drain`` before ``gate``)
    instead of the order channels appear in the configuration.  The first tuple
    item is the supply index, matching the order drivers are created in.
    """
    positions = {name: index for index, name in enumerate(supply_names)}
    ordered: list[str] = []
    for role in ("drain", "gate"):
        for group in role_groups:
            token = group.get(role)
            if token is not None and token not in ordered:
                ordered.append(token)
    return tuple((positions[token.split("/", 1)[0]], token) for token in ordered)


__all__ = ["create_measurement_port"]
