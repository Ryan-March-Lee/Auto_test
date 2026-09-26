"""Qt-independent measurement services shared by CLI and GUI adapters.

The services intentionally use the existing instrument capability names while
the VISA adapters are migrated.  This keeps the stage 3 boundary useful now:
measurement orchestration lives here, and UI code only translates events.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping, Protocol

import numpy as np

from app.cancellation import CancellationToken
from app.events import CheckpointEvent, EventSink, MessageEvent, ProgressEvent, RealtimeDataEvent
from app.events import StoppedEvent
from measurement_calculations import (
    calculate_cable_losses,
    calculate_compression_result,
    calculate_dut_input_power,
    calculate_efficiency,
    calculate_gain,
    compensate_amplifier_output_power,
    compensate_driver_output_power,
)
from measurement_lifecycle import cleanup_measurement
from instrument.ports import MeasurementInstrumentPort


class ResultRepository(Protocol):
    def save(self, result: Any) -> Any:
        """Persist a service result and return the repository-specific value."""


class NullEventSink:
    def publish(self, event: Any) -> None:
        pass


def _publish(sink: EventSink | None, event: Any) -> None:
    if sink is not None:
        sink.publish(event)


class _Service:
    measurement_type = "measurement"

    def __init__(self, config: Mapping[str, Any], instrument: MeasurementInstrumentPort, *, run_id: str = "run-legacy",
                 event_sink: EventSink | None = None, cancellation_token: CancellationToken | None = None,
                 result_repository: ResultRepository | Callable[[Any], Any] | None = None,
                 sleep_fn: Callable[[float], None] | None = None,
                 settle_delay_s: float = 5.0):
        self.config = config
        self.inst_ctrl = instrument
        self.run_id = run_id
        self.event_sink = event_sink
        self.cancellation_token = cancellation_token or CancellationToken()
        self.result_repository = result_repository
        self.sleep_fn = sleep_fn or time.sleep
        self.settle_delay_s = settle_delay_s

    def _check_cancelled(self) -> None:
        token = getattr(self, "cancellation_token", None)
        if token is not None:
            token.raise_if_cancelled()

    def _message(self, message: str) -> None:
        _publish(getattr(self, "event_sink", None), MessageEvent(getattr(self, "run_id", "run-legacy"), message=message))

    def _progress(self, fraction: float, stage: str) -> None:
        _publish(getattr(self, "event_sink", None), ProgressEvent(getattr(self, "run_id", "run-legacy"), fraction=fraction, stage=stage))

    def _save(self, result: Any) -> Any:
        repository = getattr(self, "result_repository", None)
        if repository is None:
            return result
        saver = repository.save if hasattr(repository, "save") else repository
        return saver(result)

    def _cleanup(self, power_cleanup: Callable[[], None] | None = None) -> None:
        cleanup_measurement(self.inst_ctrl, power_cleanup=power_cleanup)


class CableLossService(_Service):
    """Measure both cable paths and apply the single cable-loss calculation."""

    measurement_type = "cable_loss"

    def __init__(self, config: Mapping[str, Any], instrument: Any, **kwargs: Any):
        super().__init__(config, instrument, **kwargs)
        self.attenuator_value = float(config["attenuator"]["type"].replace("dB", ""))
        self._path1_losses: dict[Any, float] = {}

    def measure_path_loss(self, frequency: float) -> float:
        self._check_cancelled()
        self.inst_ctrl.set_frequency(frequency)
        self.inst_ctrl.set_power(0)
        self.inst_ctrl.rf_output_on()
        try:
            self.inst_ctrl.set_center_frequency(frequency)
            self.inst_ctrl.set_span(10)
            value = self.inst_ctrl.measure_power_with_average()
            self.sleep_fn(1)
            return abs(0 - value)
        finally:
            self.inst_ctrl.rf_output_off()

    def run(self, *, path2_confirmed: bool = True) -> dict[str, Any]:
        frequencies = self.config["test_frequencies"]
        path1: dict[Any, float] = dict(self._path1_losses)
        path2: dict[Any, float] = {}
        waiting_for_path2 = False
        try:
            if not path1:
                _publish(self.event_sink, CheckpointEvent(self.run_id, checkpoint="path1", prompt="请连接路径1"))
                for index, frequency in enumerate(frequencies):
                    path1[frequency] = self.measure_path_loss(frequency)
                    self._progress((index + 1) / (2 * len(frequencies)), "path1")
                self._path1_losses = dict(path1)
            if not path2_confirmed:
                waiting_for_path2 = True
                return {"path1_losses": path1, "path2_losses": path2, "status": "waiting"}
            _publish(self.event_sink, CheckpointEvent(self.run_id, checkpoint="path2", prompt="请连接路径2"))
            for index, frequency in enumerate(frequencies):
                path2[frequency] = self.measure_path_loss(frequency)
                self._progress(0.5 + (index + 1) / (2 * len(frequencies)), "path2")
            losses = {frequency: calculate_cable_losses(path1[frequency], path2[frequency], self.attenuator_value)
                      for frequency in frequencies}
            result = {"attenuator_value": self.attenuator_value,
                      "cable_losses": {str(key): value for key, value in losses.items()}}
            self._save(result)
            self._progress(1.0, "complete")
            return result
        finally:
            if not waiting_for_path2:
                self._cleanup()


class DriverPowerMappingService(_Service):
    """Measure driver output mapping and publish each point as an event."""

    measurement_type = "driver_power_mapping"

    def __init__(self, config: Mapping[str, Any], instrument: Any, loss_data: Mapping[str, Any], **kwargs: Any):
        super().__init__(config, instrument, **kwargs)
        self.loss_data = loss_data
        self.attenuator_value = float(config["attenuator"]["type"].replace("dB", ""))

    def run(self) -> dict[str, Any]:
        mapping: dict[str, dict[str, float]] = {}
        try:
            self.inst_ctrl.setup_driver_amplifier_power()
            self.inst_ctrl.power_on_driver()
            frequencies = self.config["test_frequencies"]
            for frequency_index, frequency in enumerate(frequencies):
                self._check_cancelled()
                self.inst_ctrl.set_power(-40)
                self.inst_ctrl.set_frequency(frequency)
                self.inst_ctrl.set_center_frequency(frequency)
                self.inst_ctrl.set_span(10)
                values: dict[str, float] = {}
                points = np.arange(self.config["signal_source"]["start_power"],
                                   self.config["signal_source"]["stop_power"] + self.config["signal_source"]["step"],
                                   self.config["signal_source"]["step"])
                self.inst_ctrl.rf_output_on()
                try:
                    for point_index, input_power in enumerate(points):
                        self._check_cancelled()
                        self.inst_ctrl.set_power(input_power)
                        getattr(self, "sleep_fn", time.sleep)(self.settle_delay_s)
                        raw = self.inst_ctrl.measure_power_with_average()
                        actual = compensate_driver_output_power(raw, frequency, self.loss_data["cable_losses"], self.attenuator_value)
                        values[str(input_power)] = actual
                        sweep_data = {
                            "input_power_sg": [float(key) for key in values],
                            "output_power_driver": list(values.values()),
                            "gain": [output - float(key) for key, output in values.items()],
                        }
                        _publish(getattr(self, "event_sink", None), RealtimeDataEvent(getattr(self, "run_id", "run-legacy"), measurement=self.measurement_type,
                            data={"frequency": frequency, "sweep_data": sweep_data,
                                  "current_point": {"input_power": input_power, "output_power": actual}}))
                        self._progress((frequency_index + (point_index + 1) / len(points)) / len(frequencies), "scan")
                finally:
                    self.inst_ctrl.rf_output_off()
                mapping[str(frequency)] = values
            result = {"power_mapping": mapping}
            self._save(result)
            return result
        finally:
            self._cleanup(power_cleanup=self.inst_ctrl.power_off_driver)


class AmplifierMeasurementService(_Service):
    """Run the main amplifier sweep, including protection and compression logic."""

    measurement_type = "amplifier_measurement"

    def __init__(self, config: Mapping[str, Any], instrument: Any, loss_data: Mapping[str, Any],
                 driver_mapping: Mapping[str, Any] | None = None, **kwargs: Any):
        super().__init__(config, instrument, **kwargs)
        self.loss_data = loss_data
        self.driver_mapping = driver_mapping

    def perform_power_sweep(self, frequency: float) -> dict[str, Any]:
        compression_type = self.config["compression_point"]["type"]
        compression_value = float(compression_type.replace("dB", ""))
        maximum = self.config.get("dut_config", {}).get("max_input_power", float("inf"))
        self.inst_ctrl.set_power(-40)
        self.inst_ctrl.set_frequency(frequency)
        self.inst_ctrl.set_center_frequency(frequency)
        self.inst_ctrl.set_span(10)
        supplies = self.config["power_supply_assignment"]["dut_amplifier"]["supplies"]
        data = {"input_power_dut": [], "output_power_dut": [], "gain": [], "sg_power": [],
                "voltages": [], "currents": [], "dc_power": [], "efficiency": []}
        points = np.arange(self.config["signal_source"]["start_power"],
                           self.config["signal_source"]["stop_power"] + self.config["signal_source"]["step"],
                           self.config["signal_source"]["step"])
        self.inst_ctrl.rf_output_on()
        try:
            for index, sg_power in enumerate(points):
                self._check_cancelled()
                dut_input = calculate_dut_input_power(sg_power, frequency, self.loss_data["cable_losses"], self.driver_mapping)
                if dut_input > maximum:
                    self._message(
                        f"DUT 输入保护触发: {frequency} GHz, {dut_input:.2f} dBm > {maximum:.2f} dBm"
                    )
                    _publish(
                        self.event_sink,
                        StoppedEvent(self.run_id, reason="DUT 输入功率保护", emergency=True),
                    )
                    break
                self.inst_ctrl.set_power(sg_power)
                getattr(self, "sleep_fn", time.sleep)(self.settle_delay_s)
                raw = self.inst_ctrl.measure_power_with_average()
                attenuator = float(self.config["attenuator"]["type"].replace("dB", ""))
                output = compensate_amplifier_output_power(raw, frequency, self.loss_data["cable_losses"], attenuator)
                voltage, current, dc_power = {}, {}, 0.0
                for supply_name, info in supplies.items():
                    for channel in info["channel"]:
                        v = self.inst_ctrl.read_voltage(info["name"], channel)
                        i = self.inst_ctrl.read_current(info["name"], channel)
                        dc_power += v * i
                        voltage[f"{supply_name}_{channel}"] = v
                        current[f"{supply_name}_{channel}"] = i
                gain = calculate_gain(output, dut_input)
                data["sg_power"].append(sg_power); data["input_power_dut"].append(dut_input)
                data["output_power_dut"].append(output); data["gain"].append(gain)
                data["voltages"].append(voltage); data["currents"].append(current)
                data["dc_power"].append(dc_power); data["efficiency"].append(calculate_efficiency(output, dc_power))
                _publish(getattr(self, "event_sink", None), RealtimeDataEvent(getattr(self, "run_id", "run-legacy"), measurement=self.measurement_type,
                    data={"frequency": frequency, "sweep_data": {key: list(value) for key, value in data.items()},
                          "current_point": {"sg_power": sg_power, "dut_input_power": dut_input,
                                            "output_power": output, "gain": gain,
                                            "efficiency": data["efficiency"][-1],
                                            "dc_power": dc_power}}))
                self._progress((index + 1) / max(len(points), 1), "scan")
                if index >= 3 and calculate_gain(np.mean(data["gain"][:3]), gain) >= compression_value:
                    break
        finally:
            self.inst_ctrl.rf_output_off()
        result = calculate_compression_result(data["gain"], data["input_power_dut"], data["output_power_dut"],
            data["efficiency"], data["sg_power"], compression_value, small_gain_points=3)
        return {"compression_type": compression_type, "compression_point": result["compression_point"],
                "small_signal_gain": result["small_signal_gain"], "sweep_data": data,
                "compression_achieved": result["compression_achieved"]}

    def run(self) -> dict[str, Any]:
        results: dict[str, Any] = {}
        try:
            if self.config["driver_mode"]["enabled"]:
                self.inst_ctrl.setup_driver_amplifier_power()
            self.inst_ctrl.setup_dut_power()
            self.inst_ctrl.power_on_sequence()
            self.sleep_fn(2)
            for frequency in self.config["test_frequencies"]:
                self._check_cancelled()
                results[str(frequency)] = self.perform_power_sweep(frequency)
            result = {"results": results, "config": self.config}
            self._save(result)
            return result
        finally:
            self._cleanup(power_cleanup=self.inst_ctrl.power_off_sequence)
