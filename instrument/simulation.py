"""Deterministic instrument adapters for offline measurement and safety tests."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union


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
