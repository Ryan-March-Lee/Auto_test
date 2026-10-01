"""Compatibility adapter from instrument drivers/session to measurement services."""

from __future__ import annotations

from typing import Mapping


class PortMeasurementAdapter:
    """Expose the service's historical instrument methods over new ports.

    ``dut_power_channels`` and ``driver_power_channels`` map gate/drain roles
    to physical channel names. Driver power is optional because many driver
    amplifiers are externally powered.
    """

    def __init__(
        self,
        session,
        *,
        dut_power_channels: Mapping[str, str] | None = None,
        driver_power_channels: Mapping[str, str] | None = None,
        power_settings: Mapping[str, Mapping[str, float]] | None = None,
    ):
        self.session = session
        self.signal_gen = session.signal_generator
        self.signal_generator = session.signal_generator
        self.spectrum_analyzer = session.spectrum_analyzer
        self.power_supply = session.power_supply
        self.dut_power_channels = dict(dut_power_channels or {})
        self.driver_power_channels = dict(driver_power_channels or {})
        self.power_settings = {key: dict(value) for key, value in (power_settings or {}).items()}
        self._validate_power_channels()
        self._session_cleaned = False
        self._active_power_groups: dict[str, dict[str, str]] = {}

    def _validate_power_channels(self) -> None:
        for label, channels in (
            ("DUT", self.dut_power_channels),
            ("driver", self.driver_power_channels),
        ):
            if set(channels) - {"gate", "drain"}:
                raise ValueError(f"{label} power channels may only use gate/drain roles")
            if len(set(channels.values())) != len(channels):
                raise ValueError(f"{label} power roles must map to unique physical channels")
        overlap = set(self.dut_power_channels.values()) & set(self.driver_power_channels.values())
        if overlap:
            raise ValueError(f"DUT and driver power channels overlap: {', '.join(sorted(overlap))}")

    def set_frequency(self, frequency_ghz: float, *, timeout_s: float = 5.0) -> None:
        frequency_hz = float(frequency_ghz) * 1e9
        self.signal_generator.set_frequency_hz(frequency_hz, timeout_s=timeout_s)
        self.spectrum_analyzer.configure_center_frequency_hz(frequency_hz, timeout_s=timeout_s)

    def set_power(self, power_dbm: float, *, timeout_s: float = 5.0) -> None:
        was_enabled = bool(getattr(self.signal_generator, "rf_enabled", False))
        if was_enabled:
            self.signal_generator.set_rf_enabled(False, timeout_s=timeout_s)
        try:
            self.signal_generator.set_power_dbm(power_dbm, timeout_s=timeout_s)
        except Exception:
            # A failed power change must leave RF disabled.  Re-enabling it in
            # finally could energize the previous output after a failed write.
            raise
        else:
            if was_enabled:
                self.signal_generator.set_rf_enabled(True, timeout_s=timeout_s)

    def set_center_frequency(self, frequency_ghz: float, *, timeout_s: float = 5.0) -> None:
        self.spectrum_analyzer.configure_center_frequency_hz(float(frequency_ghz) * 1e9, timeout_s=timeout_s)

    def set_span(self, span_mhz: float, *, timeout_s: float = 5.0) -> None:
        self.spectrum_analyzer.configure_bandwidth_hz(float(span_mhz) * 1e6, timeout_s=timeout_s)

    def measure_power_with_average(self, *, timeout_s: float = 10.0) -> float:
        return self.spectrum_analyzer.measure_power_dbm(timeout_s=timeout_s)

    def rf_output_on(self, *, timeout_s: float = 5.0) -> None:
        self.session.set_rf_enabled(True, timeout_s=timeout_s)

    def rf_output_off(self, *, timeout_s: float = 5.0) -> None:
        self.session.set_rf_enabled(False, timeout_s=timeout_s)

    def start_measurement(self) -> None:
        """Enter the measurement state without enabling DUT power outputs."""
        self.session.start_measurement()

    def emergency_power_off_all(self, *, timeout_s: float = 5.0) -> list[BaseException]:
        """Turn off both channels on every discovered supply before closing."""
        errors = []
        self._last_emergency_power_off: list[tuple[int, str]] = []
        supplies = getattr(self.power_supply, "supplies", None)
        if isinstance(supplies, dict):
            routed_channels = [
                (index, f"{name}/{channel}")
                for index, name in enumerate(supplies)
                for channel in ("CH2", "CH1")
            ]
        else:
            supply_list = supplies or [self.power_supply]
            routed_channels = [
                (index, channel)
                for index, _supply in enumerate(supply_list)
                for channel in ("CH2", "CH1")
            ]
        for index, channel in routed_channels:
            try:
                self.power_supply.set_output_enabled(channel, False, timeout_s=timeout_s)
            except Exception as error:
                errors.append(error)
            else:
                self._last_emergency_power_off.append((index, channel))
        return errors

    def set_voltage(self, _supply_name: str, channel: str, voltage: float) -> None:
        setter = getattr(self.power_supply, "set_voltage_v", None)
        (setter or self.power_supply.set_voltage)(self._physical_channel(_supply_name, channel), voltage)

    def set_current(self, _supply_name: str, channel: str, current: float) -> None:
        setter = getattr(self.power_supply, "set_current_limit_a", None)
        (setter or self.power_supply.set_current_limit)(self._physical_channel(_supply_name, channel), current)

    def read_voltage(self, _supply_name: str, channel: str) -> float:
        reader = getattr(self.power_supply, "read_voltage_v", None)
        return (reader or self.power_supply.read_voltage)(self._physical_channel(_supply_name, channel))

    def read_current(self, _supply_name: str, channel: str) -> float:
        reader = getattr(self.power_supply, "read_current_a", None)
        return (reader or self.power_supply.read_current)(self._physical_channel(_supply_name, channel))

    def _physical_channel(self, supply_name: str, channel: str) -> str:
        """Preserve the configured supply identity for multi-supply hardware."""
        supplies = getattr(self.power_supply, "supplies", None)
        if isinstance(supplies, dict):
            return f"{supply_name}/{channel}"
        return channel

    def _power_session(self, roles: Mapping[str, str]) -> None:
        if not roles:
            return
        if self._session_cleaned:
            raise RuntimeError("instrument session has already been cleaned")
        self.session.power_on(roles=tuple(roles), power_channels=roles)

    def setup_driver_amplifier_power(self) -> None:
        self._configure_channels(self.driver_power_channels)

    def setup_dut_power(self) -> None:
        self._configure_channels(self.dut_power_channels)

    def _configure_channels(self, roles: Mapping[str, str]) -> None:
        for role, channel in roles.items():
            settings = self.power_settings.get(role, {})
            if "voltage_v" in settings:
                setter = getattr(self.power_supply, "set_voltage_v", None)
                (setter or self.power_supply.set_voltage)(channel, settings["voltage_v"])
            if "current_a" in settings:
                setter = getattr(self.power_supply, "set_current_limit_a", None)
                (setter or self.power_supply.set_current_limit)(channel, settings["current_a"])

    def power_on_driver(self) -> None:
        self._power_session(self.driver_power_channels)
        if self.driver_power_channels:
            self._active_power_groups["driver"] = dict(self.driver_power_channels)

    def power_on_sequence(self) -> None:
        self._power_session(self.dut_power_channels)
        if self.dut_power_channels:
            self._active_power_groups["dut"] = dict(self.dut_power_channels)

    def power_off_driver(self) -> None:
        if self.driver_power_channels:
            self.session.power_off(
                roles=tuple(self.driver_power_channels),
                power_channels=self.driver_power_channels,
            )
            self._active_power_groups.pop("driver", None)

    def power_off_sequence(self) -> None:
        roles = tuple(self.dut_power_channels)
        if roles:
            self.session.power_off(roles=roles, power_channels=self.dut_power_channels)
            self._active_power_groups.pop("dut", None)

    def close_all(self, *, close_rf: bool = False, timeout_s: float = 30.0):
        if self._session_cleaned:
            return []
        errors = []
        if close_rf:
            try:
                self.session.set_rf_enabled(False, timeout_s=timeout_s)
            except Exception as error:
                errors.append(error)
        for group, channels in list(self._active_power_groups.items()):
            try:
                self.session.power_off(roles=tuple(channels), power_channels=channels, timeout_s=timeout_s)
            except Exception as error:
                errors.append(error)
            else:
                self._active_power_groups.pop(group, None)
        try:
            self.session.close(
                emergency=True,
                power_roles=tuple(self.dut_power_channels),
                power_channels=self.dut_power_channels or None,
                timeout_s=timeout_s,
            )
        except Exception as error:
            errors.append(error)
        else:
            self._session_cleaned = True
        return errors

    def safe_shutdown(self):
        """Compatibility name for GUI callers during the migration."""
        return self.close_all(close_rf=True)
