"""Application-level assembly for hardware and offline measurement ports.

The factory keeps hardware construction out of measurement services.  It owns
exactly two things: opening the VISA resources named by a hardware assembly plan
and building the instrument session around them, and building an offline
simulated session.  Every configuration fact it needs — addresses, capability
ranges, power-supply identity fields, the channels each supply declares, the
explicit ``supply_id/CHx`` routes, log texts and the shutdown order — arrives in
:class:`instrument.assembly_plan.HardwareAssemblyPlan`, so this module never
reads a configuration key and a broken configuration never opens a VISA resource.

Power supplies are a **dynamic set**: the plan may route to one, two or five
supplies without a code change here.  A routed supply is connected,
identity-checked and initial-output-checked on its own, and ``_ConfiguredPowerSupply``
routes the explicit ``supply_id/CHx`` token to the driver built for that supply —
the routing key is the ``supply_id``, never the display label.

Logs always name a supply by its field short label: channel actions carry the
routing token (``DP8B1519/CH1``) and are printed as the short label plus channel
(``supply_channel_text``), and every connection writes one audit line with the
plan's identity text, the raw ``*IDN?`` reply and the identity verdict, so a log
entry maps to the physical label, can be traced to the exact device and says out
loud whether the two identities agree.

Resource ownership is explicit and the release order does not depend on this
split: everything opened here is closed here.  When assembly fails, the resources
opened so far are released in reverse order, then the ResourceManager, and the
original assembly error is the one that reaches the caller — a failed cleanup is
only logged.

硬件模式的配置权威是**当前运行目录内冻结的硬件配置快照**：生产调用只经
:func:`app.gui_runtime.connect_instruments` 从运行契约解析并通过 manifest 校验后传入
``hardware_config_path``。本模块**不做来源校验**：它只是接受一个配置路径的工厂，
`instrument` 层纯装配单测与受控真机冒烟可以显式传入配置路径（见《无运行契约调用方清单》
第 3.0 节第 2 条与第 5 节）；“只能用运行快照”这条约束由生产组合根与门禁保证，不由本模块保证。
"""

from __future__ import annotations

from typing import Any, Mapping

import pyvisa

from application.errors import (
    CleanupError,
    DeviceCapabilityError,
    DeviceConnectionError,
    FailureRecord,
    ProtocolError,
    cause_of,
)
from domain.instruments.labels import IDENTITY_MISMATCHED, supply_identity_verdict
from infrastructure.logging.app_logging import get_logger

from .assembly_plan import hardware_assembly_plan
from .drivers import ScpiPowerSupplyDriver, ScpiSignalGeneratorDriver, ScpiSpectrumAnalyzerDriver
from .measurement_adapter import PortMeasurementAdapter
from .session import ManagedInstrumentSession
from .transport import VisaScpiTransport
from .simulation import (
    SimulatedPowerSupply,
    SimulatedSignalGenerator,
    SimulatedSpectrumAnalyzer,
)

# 本项目生产供电只使用 DP832A；身份不符仍属于能力问题，不是连接问题。
POWER_SUPPLY_MODEL = "DP832A"
# 初始输出检查逐通道查询：每台电源只查自己已配置的通道（不是固定 CH1/CH2）。
POWER_OUTPUT_QUERY = "OUTP? {channel}"
# 通道状态既能回 "ON"/"OFF"，也可能回 1/0：只有这些值表示输出已关闭。
POWER_OUTPUT_OFF_STATES = frozenset({"0", "OFF"})

logger = get_logger(__name__)


def create_measurement_port(
    hardware_config_path: str | None = None,
    *,
    mode: str = "hardware",
    power_channels: Mapping[str, str] | None = None,
    driver_power_channels: Mapping[str, str] | None = None,
    power_settings: Mapping[str, Mapping[str, float]] | None = None,
    recorder: Any = None,
):
    """Create a service port for hardware or offline simulation mode.

    ``hardware_config_path`` 是硬件装配配置的路径：**生产调用**传入的是运行目录内冻结的
    硬件配置快照（``runs/<run_id>/inputs/hardware_config.json``），由唯一装配入口
    :func:`app.gui_runtime.connect_instruments` 从当前运行契约解析并校验后给出；
    `instrument` 层纯装配单测与受控真机冒烟可以直接传入配置文件路径（清单第 3.0 节第 2 条
    与第 5 节）。本模块不做来源校验，来源约束由组合根与门禁保证。仿真模式不打开任何 VISA
    资源，因此不需要该路径。

    Hardware construction owns the VISA manager and all resources through the
    returned session.  Validation of addresses, roles and channels happens
    before any VISA resource is opened (see :mod:`instrument.assembly_plan`).
    """
    if mode == "hardware":
        if hardware_config_path is None:
            raise ValueError(
                "hardware 模式必须提供运行快照的硬件配置路径（hardware_config_path）；"
                "请先创建或重新加载运行"
            )
        return _create_hardware_measurement_port(hardware_config_path, recorder=recorder)
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
        # Session deliberately retains failed resources so a later close() can
        # retry them.
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
    and failure records (``DP8B1519``); the assembly plan already applied the
    configuration's naming rule, so this port only falls back to the routing key
    when a caller passes no label at all.
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
        self.supply_labels = {name: str(labels.get(name) or name) for name in self.supplies}

    def label(self, supply_name: str) -> str:
        """返回已配置电源的短标签；未配置的电源返回原值，供错误消息使用。"""
        return self.supply_labels.get(supply_name) or str(supply_name)

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


def _create_hardware_measurement_port(hardware_config_path: str, *, recorder=None):
    from infrastructure.config.json_io import load_config_file

    config = load_config_file(hardware_config_path)
    # 装配校验（地址、角色、通道）必须在创建任何 VISA 资源之前完成；计划是只读的，
    # 下面只按计划取事实，不再回读配置。
    plan = hardware_assembly_plan(config)
    manager = _open_resource_manager()
    resources = []
    try:
        signal_resource = _open_device(manager, plan.signal.address, "信号源")
        resources.append(signal_resource)
        spectrum_resource = _open_device(manager, plan.spectrum.address, "频谱仪")
        resources.append(spectrum_resource)

        configured_supplies = {}
        # 顺序就是计划里 ``supplies`` 的顺序（按 ``supply_id`` 排序），急停顺序里的
        # 电源下标以同一顺序为准。
        for supply in plan.supplies:
            label = f"生产电源 {supply.label}"
            resource = _open_device(manager, supply.address, label)
            resources.append(resource)
            identity = _query(resource, "*IDN?", label)
            # 审计用的一行：这台电源在配置里的完整身份（短标签 · 型号 · 完整序列号 ·
            # 设备键）、设备自己应答的 *IDN? 原文，以及两边的核对结论。日志里短标签负责
            # 快速对应实物，完整序列号与结论负责最终追溯（步骤 14）。
            verdict_status, verdict = supply_identity_verdict(supply.serial, identity)
            log_identity = (
                logger.warning if verdict_status == IDENTITY_MISMATCHED else logger.info
            )
            log_identity(
                "生产电源身份核对: %s；设备应答 *IDN? %s；核对结果 %s",
                supply.identity_text,
                identity,
                verdict,
            )
            if POWER_SUPPLY_MODEL not in identity.upper():
                # 型号不符时设备仍然可用，但不能用于本项目的供电动作：
                # 这是能力/前提问题，不是连接问题。
                raise DeviceCapabilityError(
                    f"{label} 设备身份不是 {POWER_SUPPLY_MODEL}: {identity}"
                )
            # 初始输出检查覆盖这台电源声明的全部通道：输出开着就禁止进入测量，
            # 无论该通道这次有没有被分配。错误与日志都带 ``short-label/CHx``。
            checked = ", ".join(
                f"{supply.label}/{channel}" for channel in supply.declared_channels
            )
            states = []
            for channel in supply.declared_channels:
                states.append(
                    _query(
                        resource,
                        POWER_OUTPUT_QUERY.format(channel=channel),
                        f"{supply.label}/{channel}",
                    )
                )
            if any(state.upper() not in POWER_OUTPUT_OFF_STATES for state in states):
                raise DeviceCapabilityError(
                    f"{label} 初始输出未关闭，禁止上电测量（检查通道: {checked}）"
                )
            logger.info("生产电源 %s 连接成功，检查通道: %s", supply.label, checked)
            configured_supplies[supply.supply_id] = ScpiPowerSupplyDriver(
                VisaScpiTransport(resource, recorder=recorder)
            )

        signal = ScpiSignalGeneratorDriver(
            VisaScpiTransport(signal_resource, recorder=recorder),
            min_frequency_hz=plan.signal.min_frequency_hz,
            max_frequency_hz=plan.signal.max_frequency_hz,
            min_power_dbm=plan.signal.min_power_dbm,
            max_power_dbm=plan.signal.max_power_dbm,
        )
        spectrum = ScpiSpectrumAnalyzerDriver(
            VisaScpiTransport(spectrum_resource, recorder=recorder)
        )
        # 路由键是 ``supply_id``；标签只用于日志、关闭失败记录与错误消息。
        power = _ConfiguredPowerSupply(
            configured_supplies, supply_labels=plan.supply_labels
        )
        session = _VisaSession(
            manager,
            signal,
            spectrum,
            power,
            plan.all_channels,
            # 通道动作日志用 ``短标签/CHx``；物理路由仍是上面的 token。
            power_channel_labels=plan.channel_labels,
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
            dut_power_channels=plan.dut_channels,
            driver_power_channels=plan.driver_channels,
            power_settings=plan.power_settings,
            qualified_power_channels=True,
            emergency_channels=plan.emergency_channels,
            power_channel_labels=plan.channel_labels,
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


__all__ = ["create_measurement_port"]
