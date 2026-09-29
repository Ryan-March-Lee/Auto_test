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

    def set_frequency(self, frequency_ghz: float) -> None:
        frequency_hz = float(frequency_ghz) * 1e9
        self.signal_generator.set_frequency_hz(frequency_hz)
        self.spectrum_analyzer.configure_center_frequency_hz(frequency_hz)

    def set_power(self, power_dbm: float) -> None:
        was_enabled = bool(getattr(self.signal_generator, "rf_enabled", False))
        if was_enabled:
            self.signal_generator.set_rf_enabled(False)
        try:
            self.signal_generator.set_power_dbm(power_dbm)
        finally:
            if was_enabled:
                self.signal_generator.set_rf_enabled(True)

    def set_center_frequency(self, frequency_ghz: float) -> None:
        self.spectrum_analyzer.configure_center_frequency_hz(float(frequency_ghz) * 1e9)

    def set_span(self, span_mhz: float) -> None:
        self.spectrum_analyzer.configure_bandwidth_hz(float(span_mhz) * 1e6)

    def measure_power_with_average(self) -> float:
        return self.spectrum_analyzer.measure_power_dbm()

    def rf_output_on(self) -> None:
        self.session.set_rf_enabled(True)

    def rf_output_off(self) -> None:
        self.session.set_rf_enabled(False)

    def set_voltage(self, _supply_name: str, channel: str, voltage: float) -> None:
        self.power_supply.set_voltage(channel, voltage)

    def set_current(self, _supply_name: str, channel: str, current: float) -> None:
        self.power_supply.set_current_limit(channel, current)

    def read_voltage(self, _supply_name: str, channel: str) -> float:
        return self.power_supply.read_voltage(channel)

    def read_current(self, _supply_name: str, channel: str) -> float:
        return self.power_supply.read_current(channel)

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
                self.power_supply.set_voltage(channel, settings["voltage_v"])
            if "current_a" in settings:
                self.power_supply.set_current_limit(channel, settings["current_a"])

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

    def close_all(self, *, close_rf: bool = False):
        if self._session_cleaned:
            return []
        errors = []
        for group, channels in list(self._active_power_groups.items()):
            try:
                self.session.power_off(roles=tuple(channels), power_channels=channels)
            except Exception as error:
                errors.append(error)
            else:
                self._active_power_groups.pop(group, None)
        try:
            self.session.close(
                emergency=True,
                power_roles=tuple(self.dut_power_channels),
                power_channels=self.dut_power_channels or None,
            )
        except Exception as error:
            errors.append(error)
        else:
            self._session_cleaned = True
        return errors
