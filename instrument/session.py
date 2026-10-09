"""设备会话的唯一正式实现。

会话只协调 driver、供电控制和资源所有权；配置读取与 VISA 资源创建由
``measurement_factory`` 完成。外部注入资源默认不由会话关闭。
"""

from __future__ import annotations

from collections.abc import Mapping

from .ports import InstrumentState
from .power_control import PowerController
from .safety import shutdown_instruments


class InstrumentSessionError(RuntimeError):
    def __init__(self, message: str, errors: list[BaseException]):
        super().__init__(message)
        self.errors = tuple(errors)


class ManagedInstrumentSession:
    """统一管理设备生命周期、安全动作和资源所有权。"""

    def __init__(self, signal_generator, spectrum_analyzer, power_supply,
                 power_channels: Mapping[str, str] | None = None, *,
                 owns_signal_generator: bool = True,
                 owns_spectrum_analyzer: bool = True,
                 owns_power_supply: bool = True,
                 can_disable_rf: bool = True,
                 can_disable_power: bool = True,
                 sleep_fn=None, gate_settle_time_s: float = 1.5,
                 drain_settle_time_s: float = 2.0):
        self.signal_generator = signal_generator
        self.spectrum_analyzer = spectrum_analyzer
        self.power_supply = power_supply
        self.power_channels = dict(power_channels or {})
        self.owns_signal_generator = owns_signal_generator
        self.owns_spectrum_analyzer = owns_spectrum_analyzer
        self.owns_power_supply = owns_power_supply
        self.can_disable_rf = can_disable_rf
        self.can_disable_power = can_disable_power
        self.state = InstrumentState.CREATED
        self._attempted = []
        self._connected = []
        self._power = PowerController(
            power_supply, self.power_channels, sleep_fn=sleep_fn,
            gate_settle_time_s=gate_settle_time_s,
            drain_settle_time_s=drain_settle_time_s,
        )

    def validate(self, *, timeout_s: float = 10.0) -> None:
        if self.state is not InstrumentState.CREATED:
            raise ValueError(f"cannot validate from {self.state.value}")
        if len(set(self.power_channels.values())) != len(self.power_channels):
            raise ValueError("power roles must map to unique physical channels")
        if set(self.power_channels) - {"gate", "drain"}:
            raise ValueError("power mapping contains an unknown supply role")
        if self.power_channels and set(self.power_channels) != {"gate", "drain"}:
            raise ValueError("gate and drain channels must be mapped together")
        self.state = InstrumentState.VALIDATED

    def connect(self, *, timeout_s: float = 30.0) -> None:
        if self.state is not InstrumentState.VALIDATED:
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

    def prepare(self, *, frequency_hz=None, bandwidth_hz=None,
                timeout_s: float = 30.0) -> None:
        if self.state is not InstrumentState.CONNECTED:
            raise ValueError(f"cannot prepare from {self.state.value}")
        try:
            if frequency_hz is not None:
                self.signal_generator.set_frequency_hz(frequency_hz, timeout_s=timeout_s)
                self.spectrum_analyzer.configure_center_frequency_hz(frequency_hz, timeout_s=timeout_s)
            if bandwidth_hz is not None:
                self.spectrum_analyzer.configure_bandwidth_hz(bandwidth_hz, timeout_s=timeout_s)
            # Drivers use this flag to guard RF enable.  Preparation is a
            # lifecycle state even when the caller leaves optional frequency
            # and bandwidth configuration to a later measurement step.
            if hasattr(self.signal_generator, "prepared"):
                self.signal_generator.prepared = True
            self.state = InstrumentState.PREPARED
        except Exception as error:
            try:
                self.close(timeout_s=timeout_s)
            except Exception as cleanup_error:
                raise error from cleanup_error
            raise

    def power_on(self, *, roles=None, power_channels=None, timeout_s: float = 10.0) -> None:
        if self.state not in (InstrumentState.PREPARED, InstrumentState.POWERED):
            raise ValueError(f"cannot power on from {self.state.value}")
        channels = self.power_channels if power_channels is None else dict(power_channels)
        if roles is None:
            roles = ("gate", "drain")
        selected = {role: channels[role] for role in roles if role in channels}
        controller = PowerController(self.power_supply, selected, sleep_fn=self._power.sleep_fn,
                                     gate_settle_time_s=self._power.gate_settle_time_s,
                                     drain_settle_time_s=self._power.drain_settle_time_s)
        try:
            controller.power_on(timeout_s=timeout_s)
            self.state = InstrumentState.POWERED
        except Exception as error:
            try:
                self.close(timeout_s=timeout_s)
            except Exception as cleanup_error:
                raise error from cleanup_error
            raise

    def power_off(self, *, roles=None, power_channels=None, timeout_s: float = 10.0) -> None:
        if self.power_supply not in self._connected:
            return
        channels = self.power_channels if power_channels is None else dict(power_channels)
        if roles is None:
            roles = ("gate", "drain")
        selected = {role: channels[role] for role in roles if role in channels}
        PowerController(self.power_supply, selected, sleep_fn=self._power.sleep_fn,
                        gate_settle_time_s=self._power.gate_settle_time_s,
                        drain_settle_time_s=self._power.drain_settle_time_s).power_off(timeout_s=timeout_s)

    def start_measurement(self) -> None:
        if self.state not in (InstrumentState.PREPARED, InstrumentState.POWERED):
            raise ValueError(f"cannot measure from {self.state.value}")
        self.state = InstrumentState.MEASURING

    def set_rf_enabled(self, enabled: bool, *, timeout_s: float = 5.0) -> None:
        if enabled and self.state not in (InstrumentState.POWERED, InstrumentState.MEASURING):
            raise ValueError("RF may only be enabled after preparation and power-on")
        self.signal_generator.set_rf_enabled(enabled, timeout_s=timeout_s)

    def stop(self, *, emergency=False, timeout_s: float = 30.0) -> None:
        if not emergency and self.state not in (InstrumentState.POWERED, InstrumentState.MEASURING):
            raise ValueError(f"ordinary stop is invalid from {self.state.value}")
        self.close(emergency=emergency, timeout_s=timeout_s)

    def close(self, *, emergency=False, timeout_s: float = 30.0, **kwargs) -> None:
        if self.state is InstrumentState.CLEANED:
            return
        self.state = InstrumentState.STOPPING
        def close_owned(*, rf_safe=True, power_safe=True):
            errors = []
            owned = []
            if self.owns_spectrum_analyzer and self.spectrum_analyzer in self._attempted:
                owned.append(self.spectrum_analyzer)
            # 电源输出失败后仍关闭连接；下一次安全操作不能依赖一个
            # 已知处于异常状态的 VISA 连接。RF 关闭失败则保留信号源，
            # 以便现场重试关闭 RF。
            if self.owns_power_supply and self.power_supply in self._attempted:
                owned.append(self.power_supply)
            if rf_safe and self.owns_signal_generator and self.signal_generator in self._attempted:
                owned.append(self.signal_generator)
            for device in owned:
                try:
                    device.close(timeout_s=timeout_s)
                except Exception as error:
                    errors.append(error)
                else:
                    if device in self._connected:
                        self._connected.remove(device)
                    if device in self._attempted:
                        self._attempted.remove(device)
            return errors

        try:
            shutdown_instruments(
                rf_off=(lambda: self.set_rf_enabled(False, timeout_s=timeout_s))
                if self.can_disable_rf else None,
                power_off=lambda: self.power_off(
                    power_channels=kwargs.get("power_channels"),
                    roles=kwargs.get("power_roles"), timeout_s=timeout_s,
                ) if self.can_disable_power else None,
                close=close_owned,
                emergency=emergency,
            )
        except Exception as error:
            errors = list(getattr(error, "errors", (error,)))
            raise InstrumentSessionError("instrument cleanup failed", errors) from errors[0]
        self.state = InstrumentState.CLEANED
