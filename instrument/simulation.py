"""Deterministic instrument adapters for offline measurement and safety tests."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from .ports import InstrumentState


FailureRule = Union[str, Callable[[str], bool], None]


def _matches_failure(rule: FailureRule, action: str) -> bool:
    if rule is None:
        return False
    return bool(rule(action)) if callable(rule) else rule == action


@dataclass
class CommandRecorder:
    commands: List[Tuple[str, str, object]] = field(default_factory=list)

    def record(self, device: str, action: str, value: object = None) -> None:
        self.commands.append((device, action, value))


class FailureInjector:
    """统一的模拟故障注入器；默认注入一次，便于测试恢复路径。"""

    def __init__(self, fail_on: FailureRule = None):
        self.fail_on = fail_on
        self.fail_on_query: FailureRule = None
        self.fail_on_cleanup: FailureRule = None
        self._once: List[Tuple[str, FailureRule]] = []

    def inject(self, action: FailureRule, *, once: bool = True, phase: str = "action") -> None:
        if phase not in {"action", "query", "cleanup"}:
            raise ValueError("phase must be action, query or cleanup")
        if once:
            self._once.append((phase, action))
        elif phase == "action":
            self.fail_on = action
        elif phase == "query":
            self.fail_on_query = action
        else:
            self.fail_on_cleanup = action

    def should_fail(self, action: str, *, phase: str = "action") -> bool:
        for index, (rule_phase, rule) in enumerate(self._once):
            if rule_phase == phase and _matches_failure(rule, action):
                self._once.pop(index)
                return True
        rule = {
            "action": self.fail_on,
            "query": self.fail_on_query,
            "cleanup": self.fail_on_cleanup,
        }[phase]
        if rule is None and phase != "action":
            rule = self.fail_on
        return _matches_failure(rule, action)


class _SimulatedDevice:
    """共享模拟设备的记录和故障注入行为。"""

    device_name = "device"

    def _init_simulation(self, recorder: Optional[CommandRecorder], fail_on: FailureRule) -> None:
        self.recorder = recorder or CommandRecorder()
        self.fail_on = fail_on
        self.failure_injector = FailureInjector(fail_on)

    def inject_failure(self, action: FailureRule, *, once: bool = True, phase: str = "action") -> None:
        self.failure_injector.inject(action, once=once, phase=phase)
        if not once and phase == "action":
            # ``fail_on`` 是保留的公开兼容属性；同步它才能让持续注入
            # 与旧调用方式以及后续动作使用同一份规则。
            self.fail_on = action

    def _record(self, action: str, value=None, *, phase: str = "action") -> None:
        self.recorder.record(self.device_name, action, value)
        # 保持旧测试直接修改 ``fail_on`` 的兼容性。
        self.failure_injector.fail_on = self.fail_on
        if self.failure_injector.should_fail(action, phase=phase):
            display_name = self.device_name.replace("_", " ")
            raise RuntimeError(f"simulated {display_name} failure: {action}")


class SimulatedSignalGenerator(_SimulatedDevice):
    device_name = "signal_generator"
    def __init__(self, recorder: Optional[CommandRecorder] = None, *, fail_on: FailureRule = None):
        self._init_simulation(recorder, fail_on)
        self.connected = False
        self.rf_enabled = False
        self.prepared = False
        self.frequency_hz = None
        self.power_dbm = None

    def _command(self, action: str, value=None):
        self._record(action, value)

    def connect(self, *, timeout_s=10.0):
        self._command("connect")
        self.connected = True

    def set_frequency_hz(self, frequency_hz, *, timeout_s=5.0):
        self._command("set_frequency_hz", frequency_hz)
        if not self.connected or frequency_hz <= 0:
            raise ValueError("signal generator must be connected and frequency positive")
        self.frequency_hz = frequency_hz

    def set_power_dbm(self, power_dbm, *, timeout_s=5.0):
        self._command("set_power_dbm", power_dbm)
        if not self.connected or self.rf_enabled:
            raise RuntimeError("power can only be set while connected with RF off")
        self.power_dbm = power_dbm

    def set_rf_enabled(self, enabled, *, timeout_s=5.0):
        self._command("rf_on" if enabled else "rf_off")
        if enabled and (not self.connected or not self.prepared):
            raise RuntimeError("RF requires a connected and prepared signal generator")
        self.rf_enabled = bool(enabled)

    def close(self, *, timeout_s=5.0):
        if self.connected:
            self.set_rf_enabled(False, timeout_s=timeout_s)
            self._record("close", phase="cleanup")
            self.connected = False


class SimulatedSpectrumAnalyzer(_SimulatedDevice):
    device_name = "spectrum_analyzer"
    def __init__(self, readings: Sequence[float] = (-30.0,), recorder=None, *,
                 fail_on: FailureRule = None, reading_model: Optional[Callable[[float], float]] = None,
                 input_power_source=None):
        self._init_simulation(recorder, fail_on)
        self.readings = list(readings)
        self.read_index = 0
        self.reading_model = reading_model
        self.input_power_source = input_power_source
        self.connected = False
        self.configured = False

    def _command(self, action, value=None):
        self._record(action, value)

    def connect(self, *, timeout_s=10.0):
        self._command("connect")
        self.connected = True

    def configure_center_frequency_hz(self, frequency_hz, *, timeout_s=5.0):
        self._command("center_frequency_hz", frequency_hz)
        if not self.connected or frequency_hz <= 0:
            raise ValueError("analyzer must be connected and frequency positive")
        self.configured = True

    def configure_bandwidth_hz(self, bandwidth_hz, *, timeout_s=5.0):
        self._command("bandwidth_hz", bandwidth_hz)
        if not self.connected or bandwidth_hz <= 0:
            raise ValueError("analyzer must be connected and bandwidth positive")
        self.configured = True

    def set_center_frequency_hz(self, frequency_hz, *, timeout_s=5.0):
        self.configure_center_frequency_hz(frequency_hz, timeout_s=timeout_s)

    def set_span_hz(self, span_hz, *, timeout_s=5.0):
        self.configure_bandwidth_hz(span_hz, timeout_s=timeout_s)

    def set_resolution_bandwidth_hz(self, bandwidth_hz, *, timeout_s=5.0):
        self._command("resolution_bandwidth_hz", bandwidth_hz)
        if not self.connected or bandwidth_hz <= 0:
            raise ValueError("analyzer must be connected and resolution bandwidth positive")

    def set_video_bandwidth_hz(self, bandwidth_hz, *, timeout_s=5.0):
        self._command("video_bandwidth_hz", bandwidth_hz)
        if not self.connected or bandwidth_hz <= 0:
            raise ValueError("analyzer must be connected and video bandwidth positive")

    def measure_power_dbm(self, *, timeout_s=10.0):
        self._command("measure_power_dbm")
        if self.failure_injector.should_fail("measure_power_dbm", phase="query"):
            raise RuntimeError("simulated spectrum_analyzer query failure: measure_power_dbm")
        if not self.connected or not self.configured:
            raise RuntimeError("analyzer must be connected and configured")
        if not self.readings:
            raise RuntimeError("no simulated readings configured")
        if self.reading_model is not None:
            if self.input_power_source is None or self.input_power_source.power_dbm is None:
                raise RuntimeError("no simulated input power configured")
            value = self.reading_model(float(self.input_power_source.power_dbm))
        else:
            value = self.readings[min(self.read_index, len(self.readings) - 1)]
        self.read_index += 1
        return float(value)

    def measure_peak_power_dbm(self, *, timeout_s=10.0):
        return self.measure_power_dbm(timeout_s=timeout_s)

    def close(self, *, timeout_s=5.0):
        if self.connected:
            self._record("close", phase="cleanup")
            self.connected = False


class SimulatedPowerSupply(_SimulatedDevice):
    device_name = "power_supply"
    def __init__(self, recorder=None, *, fail_on: FailureRule = None,
                 voltage_readings: Optional[Mapping[str, float]] = None,
                 current_readings: Optional[Mapping[str, float]] = None):
        self._init_simulation(recorder, fail_on)
        self.connected = False
        self.voltages: Dict[str, float] = {}
        self.currents: Dict[str, float] = {}
        self.outputs: Dict[str, bool] = {}
        self.voltage_readings = dict(voltage_readings or {})
        self.current_readings = dict(current_readings or {})

    def _command(self, action, value=None):
        self._record(action, value)

    def connect(self, *, timeout_s=10.0):
        self._command("connect")
        self.connected = True

    def set_voltage(self, channel, voltage_v, *, timeout_s=5.0):
        self._command("set_voltage", (channel, voltage_v))
        if not self.connected or self.outputs.get(channel, False):
            raise RuntimeError("voltage requires connection and output off")
        self.voltages[channel] = float(voltage_v)

    def set_voltage_v(self, channel, voltage_v, *, timeout_s=5.0):
        """单位明确的端口别名，与真实电源 driver 保持一致。"""
        self.set_voltage(channel, voltage_v, timeout_s=timeout_s)

    def set_current_limit(self, channel, current_a, *, timeout_s=5.0):
        self._command("set_current_limit", (channel, current_a))
        if not self.connected or current_a < 0:
            raise ValueError("current limit requires connection and non-negative current")
        self.currents[channel] = float(current_a)

    def set_current_limit_a(self, channel, current_a, *, timeout_s=5.0):
        """单位明确的端口别名，与真实电源 driver 保持一致。"""
        self.set_current_limit(channel, current_a, timeout_s=timeout_s)

    def set_output_enabled(self, channel, enabled, *, timeout_s=5.0):
        self._command("output_on" if enabled else "output_off", channel)
        if not self.connected:
            raise RuntimeError("power supply is not connected")
        self.outputs[channel] = bool(enabled)

    def read_voltage(self, channel, *, timeout_s=5.0):
        self._command("read_voltage", channel)
        if self.failure_injector.should_fail("read_voltage", phase="query"):
            raise RuntimeError("simulated power_supply query failure: read_voltage")
        if not self.connected:
            raise RuntimeError("power supply is not connected")
        if channel in self.voltage_readings:
            return float(self.voltage_readings[channel])
        return self.voltages.get(channel, 0.0) if self.outputs.get(channel, False) else 0.0

    def read_voltage_v(self, channel, *, timeout_s=5.0):
        return self.read_voltage(channel, timeout_s=timeout_s)

    def read_current(self, channel, *, timeout_s=5.0):
        self._command("read_current", channel)
        if self.failure_injector.should_fail("read_current", phase="query"):
            raise RuntimeError("simulated power_supply query failure: read_current")
        if not self.connected:
            raise RuntimeError("power supply is not connected")
        return float(self.current_readings.get(channel, 0.0))

    def read_current_a(self, channel, *, timeout_s=5.0):
        return self.read_current(channel, timeout_s=timeout_s)

    def close(self, *, timeout_s=5.0):
        if self.connected:
            self._record("close", phase="cleanup")
            self.connected = False


class _LegacySafetyInstrumentSession:
    """Coordinates state transitions and best-effort RF/power/connection cleanup."""

    def __init__(self, signal_generator, spectrum_analyzer, power_supply,
                 power_channels: Optional[Mapping[str, str]] = None, *, gate_role="gate", drain_role="drain"):
        self.signal_generator = signal_generator
        self.spectrum_analyzer = spectrum_analyzer
        self.power_supply = power_supply
        self.power_channels = dict(power_channels or {})
        self.gate_role = gate_role
        self.drain_role = drain_role
        self.state = InstrumentState.CREATED
        self._connected = []
        self._attempted = []
        self._prepared = False

    def validate(self, *, timeout_s=10.0):
        if self.state != InstrumentState.CREATED:
            raise ValueError(f"cannot validate from {self.state.value}")
        if len(set(self.power_channels.values())) != len(self.power_channels):
            raise ValueError("power roles must map to unique physical channels")
        if not set(self.power_channels).issubset({self.gate_role, self.drain_role}):
            raise ValueError("power mapping contains an unknown supply role")
        if self.power_channels and set(self.power_channels) != {self.gate_role, self.drain_role}:
            raise ValueError("gate and drain channels must be mapped together")
        self.state = InstrumentState.VALIDATED

    def connect(self, *, timeout_s=30.0):
        if self.state != InstrumentState.VALIDATED:
            raise ValueError(f"cannot connect from {self.state.value}")
        try:
            for device in (self.signal_generator, self.spectrum_analyzer, self.power_supply):
                if device not in self._attempted:
                    self._attempted.append(device)
                device.connect(timeout_s=timeout_s)
                if device not in self._connected:
                    self._connected.append(device)
        except Exception as error:
            try:
                self.close(timeout_s=timeout_s)
            except Exception as cleanup_error:
                raise error from cleanup_error
            raise
        self.state = InstrumentState.CONNECTED

    def prepare(self, *, frequency_hz=None, bandwidth_hz=None, timeout_s=30.0):
        if self.state != InstrumentState.CONNECTED:
            raise ValueError(f"cannot prepare from {self.state.value}")
        try:
            if frequency_hz is not None:
                self.signal_generator.set_frequency_hz(frequency_hz, timeout_s=timeout_s)
                self.spectrum_analyzer.configure_center_frequency_hz(frequency_hz, timeout_s=timeout_s)
            if bandwidth_hz is not None:
                self.spectrum_analyzer.configure_bandwidth_hz(bandwidth_hz, timeout_s=timeout_s)
            self.signal_generator.prepared = True
            self._prepared = True
            self.state = InstrumentState.PREPARED
        except Exception as error:
            try:
                self.close(timeout_s=timeout_s)
            except Exception as cleanup_error:
                raise error from cleanup_error
            raise

    def power_on(self, *, roles=None, power_channels=None, timeout_s=10.0):
        if self.state not in (InstrumentState.PREPARED, InstrumentState.POWERED):
            raise ValueError(f"cannot power on from {self.state.value}")
        selected_roles = tuple((self.gate_role, self.drain_role) if roles is None else roles)
        channels = self.power_channels if power_channels is None else power_channels
        if not set(selected_roles).issubset({self.gate_role, self.drain_role}):
            raise ValueError("power roles contain an unknown supply role")
        try:
            for role in (self.gate_role, self.drain_role):
                if role not in selected_roles:
                    continue
                channel = channels.get(role)
                if channel is not None:
                    self.power_supply.set_output_enabled(channel, True, timeout_s=timeout_s)
            self.state = InstrumentState.POWERED
        except Exception as error:
            try:
                self.close(timeout_s=timeout_s)
            except Exception as cleanup_error:
                raise error from cleanup_error
            raise

    def start_measurement(self):
        if self.state not in (InstrumentState.PREPARED, InstrumentState.POWERED):
            raise ValueError(f"cannot measure from {self.state.value}")
        self.state = InstrumentState.MEASURING

    def set_rf_enabled(self, enabled, *, timeout_s=5.0):
        if enabled and self.state not in (InstrumentState.POWERED, InstrumentState.MEASURING):
            raise ValueError("RF may only be enabled after preparation and power-on")
        self.signal_generator.set_rf_enabled(enabled, timeout_s=timeout_s)

    def stop(self, *, emergency=False, timeout_s=30.0):
        """Stop at a safe boundary; emergency stop accepts every active state."""
        if not emergency and self.state not in (InstrumentState.POWERED, InstrumentState.MEASURING):
            raise ValueError(f"ordinary stop is invalid from {self.state.value}")
        self.close(emergency=emergency, timeout_s=timeout_s)

    def power_off(self, *, roles=None, power_channels=None, timeout_s=10.0):
        if self.power_supply not in self._connected:
            return
        selected_roles = tuple((self.drain_role, self.gate_role) if roles is None else roles)
        channels = self.power_channels if power_channels is None else power_channels
        if not set(selected_roles).issubset({self.gate_role, self.drain_role}):
            raise ValueError("power roles contain an unknown supply role")
        errors = []
        for role in (self.drain_role, self.gate_role):
            if role not in selected_roles:
                continue
            channel = channels.get(role)
            if channel is not None:
                try:
                    self.power_supply.set_output_enabled(channel, False, timeout_s=timeout_s)
                except Exception as error:
                    errors.append(error)
        if errors:
            raise RuntimeError("power shutdown failed: " + "; ".join(map(str, errors))) from errors[0]

    def close(self, *, emergency=False, power_roles=None, power_channels=None, timeout_s=30.0):
        if self.state == InstrumentState.CLEANED:
            return
        self.state = InstrumentState.STOPPING
        errors = []
        rf_safe = self.signal_generator not in self._attempted
        power_safe = self.power_supply not in self._connected
        if self.signal_generator in self._attempted:
            try:
                self.signal_generator.set_rf_enabled(False, timeout_s=timeout_s)
            except Exception as error:
                errors.append(error)
            else:
                rf_safe = True
        try:
            self.power_off(roles=power_roles, power_channels=power_channels, timeout_s=timeout_s)
        except Exception as error:
            errors.append(error)
        else:
            power_safe = True
        devices = []
        if self.spectrum_analyzer in self._attempted:
            devices.append(self.spectrum_analyzer)
        # A broadcast power-off can report one supply failure after already
        # attempting every supply.  Close each owned resource so one failed
        # broadcast does not prevent cleanup of the remaining connections.
        if self.power_supply in self._attempted:
            devices.append(self.power_supply)
        if rf_safe and self.signal_generator in self._attempted:
            devices.append(self.signal_generator)
        for device in devices:
            try:
                device.close(timeout_s=timeout_s)
            except Exception as error:
                errors.append(error)
            else:
                if device in self._connected:
                    self._connected.remove(device)
                if device in self._attempted:
                    self._attempted.remove(device)
        if errors:
            self.state = InstrumentState.STOPPING
            raise RuntimeError("instrument cleanup failed: " + "; ".join(map(str, errors))) from errors[0]
        self.state = InstrumentState.CLEANED


@dataclass
class RecordedSequence:
    """Small JSON-friendly command recording suitable for sanitized replay fixtures."""
    commands: List[Tuple[str, str, object]] = field(default_factory=list)

    @classmethod
    def from_recorder(cls, recorder: CommandRecorder):
        return cls(list(recorder.commands))

    def replay(self, handler: Callable[[str, str, object], None]) -> None:
        for command in self.commands:
            handler(*command)

    def assert_order(self, *expected: Tuple[str, str, object]) -> None:
        """Assert that recorded commands occur in the supplied order.

        Other commands may occur between the expected commands.  Matching the
        complete command tuple, rather than only the action name, keeps the
        assertion useful for multi-channel safety flows.
        """
        for command in expected:
            if not isinstance(command, tuple) or len(command) != 3:
                raise ValueError("expected commands must be 3-item tuples")

        command_index = 0
        for expected_command in expected:
            try:
                command_index = self.commands.index(expected_command, command_index) + 1
            except ValueError as error:
                recorded = self.commands[max(0, command_index - 2):command_index + 3]
                raise AssertionError(
                    "expected command order was not recorded: "
                    f"{expected_command!r} after index {command_index - 1}; "
                    f"nearby commands: {recorded!r}"
                ) from error

    def assert_action_order(self, *expected: Tuple[str, str]) -> None:
        """Assert device/action order while ignoring command values."""
        for action in expected:
            if not isinstance(action, tuple) or len(action) != 2:
                raise ValueError("expected actions must be 2-item tuples")

        actions = [(device, action) for device, action, _value in self.commands]
        action_index = 0
        for expected_action in expected:
            try:
                action_index = actions.index(expected_action, action_index) + 1
            except ValueError as error:
                nearby = actions[max(0, action_index - 2):action_index + 3]
                raise AssertionError(
                    "expected device/action order was not recorded: "
                    f"{expected_action!r} after index {action_index - 1}; "
                    f"nearby actions: {nearby!r}"
                ) from error

    def to_json(self) -> str:
        return json.dumps(self.commands, ensure_ascii=False)

    @classmethod
    def from_json(cls, payload: str):
        commands = json.loads(payload)
        if not isinstance(commands, list) or any(not isinstance(item, list) or len(item) != 3 for item in commands):
            raise ValueError("invalid recorded command sequence")
        return cls([tuple(item) for item in commands])


# 迁移期兼容名称。正式实现位于 instrument.session，仿真设备仍可直接注入。
from .session import ManagedInstrumentSession

SafetyInstrumentSession = ManagedInstrumentSession
