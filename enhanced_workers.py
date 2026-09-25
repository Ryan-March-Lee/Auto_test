"""Qt adapters for the shared, Qt-independent measurement services."""

from __future__ import annotations

import json
import time
from typing import Any

import numpy as np

from PySide6.QtCore import QThread, Signal, QObject

from app.cancellation import CancellationToken
from app.events import CheckpointEvent, MessageEvent, ProgressEvent, RealtimeDataEvent
from app_logging import get_logger
from config_io import load_config_file
from instrument_control import InstrumentControl
from measurement_calculations import compensate_amplifier_output_power
from measurement_calculations import calculate_cable_losses
from measurement_services import CableLossService, DriverPowerMappingService, AmplifierMeasurementService
from project_paths import CABLE_LOSS_FILE, CONFIG_FILE, TEST_RESULTS_DIR, resolve_path
from result_storage import load_json_result, new_run_id, save_measurement_result, write_legacy_run_snapshot


logger = get_logger(__name__)


class _NumpyJSONEncoder(json.JSONEncoder):
    def default(self, value):
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            return float(value)
        return super().default(value)


class _CallbackEventSink:
    def __init__(self, progress=None, message=None, data=None, checkpoint=None):
        self.progress = progress
        self.message = message
        self.data = data
        self.checkpoint = checkpoint

    def publish(self, event):
        if isinstance(event, ProgressEvent) and self.progress:
            self.progress(int(event.fraction * 100))
        elif isinstance(event, MessageEvent) and self.message:
            self.message(event.message)
        elif isinstance(event, RealtimeDataEvent) and self.data:
            self.data(dict(event.data))
        elif isinstance(event, CheckpointEvent) and self.checkpoint:
            self.checkpoint(event.prompt)


class _LegacyResultAdapter:
    def __init__(self, config, *, run_id=None):
        self.config = config
        self.run_id = run_id or new_run_id()
        self.run_directory = write_legacy_run_snapshot(self.run_id, config, status="created")

    def _save(self, result, result_type, legacy_path, *, encoder=None):
        archive_path, _ = save_measurement_result(
            result, result_type=result_type, legacy_path=legacy_path,
            run_id=self.run_id, run_directory=self.run_directory,
            encoder=encoder or _NumpyJSONEncoder,
        )
        self.run_directory = archive_path.parent


class EnhancedCableLossMeasurement(_LegacyResultAdapter):
    def __init__(self, config_path=None, progress_callback=None, message_callback=None,
                 sleep_fn=None, run_id=None):
        config_path = resolve_path(config_path, CONFIG_FILE)
        config = load_config_file(config_path)
        super().__init__(config, run_id=run_id)
        self.inst_ctrl = InstrumentControl(config_path)
        self.sleep_fn = sleep_fn or time.sleep
        self._token = CancellationToken()
        self._service = CableLossService(
            config, self.inst_ctrl, run_id=self.run_id,
            event_sink=_CallbackEventSink(progress_callback, message_callback, checkpoint=self._pause),
            cancellation_token=self._token, sleep_fn=self.sleep_fn,
        )
        self.path1_losses = self._service._path1_losses
        self.cable_losses = {}
        self.step_pause_callback = None
        self._waiting_for_path2 = False

    def _pause(self, message):
        if self.step_pause_callback:
            self.step_pause_callback(message)

    def set_step_pause_callback(self, callback):
        self.step_pause_callback = callback

    def measure_path_loss(self, frequency):
        return self._service.measure_path_loss(frequency)

    def stop_measurement(self):
        self._token.request_emergency_stop(reason="用户停止")
        if self._waiting_for_path2:
            self._service._cleanup()
            self._waiting_for_path2 = False

    def measure_all_frequencies(self):
        result = self._service.run(path2_confirmed=False)
        self.path1_losses = dict(self._service._path1_losses)
        self._waiting_for_path2 = True
        return result

    def continue_to_step2(self):
        result = self._service.run(path2_confirmed=True)
        self._waiting_for_path2 = False
        self.cable_losses = {float(key): value for key, value in result["cable_losses"].items()}
        self._save(
            {"attenuator_value": self._service.attenuator_value,
             "cable_losses": result["cable_losses"]},
            "cable_loss", CABLE_LOSS_FILE,
        )
        return result

    def _measure_step2(self):
        """Compatibility hook retained for callers from the previous GUI adapter."""
        if not hasattr(self, "_service"):
            for frequency in self.config["test_frequencies"]:
                path2 = self.measure_path_loss(frequency)
                self.cable_losses[frequency] = calculate_cable_losses(
                    path1_loss=self.path1_losses[frequency], path2_loss=path2,
                    attenuator_value=self.attenuator_value,
                )
            self.save_results()
            return
        return self.continue_to_step2()


class EnhancedDriverPowerMapping(_LegacyResultAdapter):
    def __init__(self, config_path=None, loss_data_path=None, progress_callback=None,
                 message_callback=None, data_callback=None, sleep_fn=None, run_id=None):
        config_path = resolve_path(config_path, CONFIG_FILE)
        loss_data_path = resolve_path(loss_data_path, CABLE_LOSS_FILE)
        config = load_config_file(config_path)
        super().__init__(config, run_id=run_id)
        self.inst_ctrl = InstrumentControl(config_path)
        self.sleep_fn = sleep_fn or time.sleep
        self._token = CancellationToken()
        self._service = DriverPowerMappingService(
            config, self.inst_ctrl, load_json_result(loss_data_path), run_id=self.run_id,
            event_sink=_CallbackEventSink(progress_callback, message_callback, data_callback),
            cancellation_token=self._token, sleep_fn=self.sleep_fn, settle_delay_s=3.0,
        )
        self.power_mapping = {}

    def stop_measurement(self):
        self._token.request_emergency_stop(reason="用户停止")

    def measure_all_frequencies(self):
        result = self._service.run()
        self.power_mapping = result["power_mapping"]
        self._save(
            {"power_mapping": self.power_mapping,
             "config": {key: self.config["signal_source"][key] for key in ("start_power", "stop_power", "step")}},
            "driver_power_mapping", TEST_RESULTS_DIR / f"driver_power_mapping_{time.strftime('%Y%m%d_%H%M%S')}.json",
        )
        return result


class EnhancedAmplifierMeasurement(_LegacyResultAdapter):
    def __init__(self, config_path=None, loss_data_path=None, driver_mapping_path=None,
                 progress_callback=None, message_callback=None, data_callback=None,
                 sleep_fn=None, run_id=None):
        config_path = resolve_path(config_path, CONFIG_FILE)
        loss_data_path = resolve_path(loss_data_path, CABLE_LOSS_FILE)
        config = load_config_file(config_path)
        super().__init__(config, run_id=run_id)
        driver_mapping = None
        if config["driver_mode"]["enabled"]:
            if driver_mapping_path is None:
                files = sorted(TEST_RESULTS_DIR.glob("driver_power_mapping_*.json"), key=lambda item: item.stat().st_mtime)
                if not files:
                    raise FileNotFoundError("驱动模式已开启，但未找到驱动映射文件")
                driver_mapping_path = str(files[-1])
            driver_mapping = load_json_result(driver_mapping_path)["power_mapping"]
        self.inst_ctrl = InstrumentControl(config_path)
        self.sleep_fn = sleep_fn or time.sleep
        self._token = CancellationToken()
        self._service = AmplifierMeasurementService(
            config, self.inst_ctrl, load_json_result(loss_data_path), driver_mapping,
            run_id=self.run_id, event_sink=_CallbackEventSink(progress_callback, message_callback, data_callback),
            cancellation_token=self._token, sleep_fn=self.sleep_fn, settle_delay_s=3.0,
        )
        self.loss_data = self._service.loss_data
        self.driver_mapping = driver_mapping
        self.measurement_results = {}

    def stop_measurement(self):
        self._token.request_emergency_stop(reason="用户停止")

    def calculate_actual_power(self, frequency, measured_power):
        from amplifier_measurement import compensate_amplifier_output_power as calculate
        attenuator = float(self.config["attenuator"]["type"].replace("dB", ""))
        return calculate(
            measured_power=measured_power, frequency=frequency,
            loss_data=self.loss_data["cable_losses"], attenuator_value=attenuator,
        )

    def perform_power_sweep(self, frequency):
        if not hasattr(self, "_service"):
            self._service = AmplifierMeasurementService(
                self.config, self.inst_ctrl, self.loss_data, getattr(self, "driver_mapping", None),
                sleep_fn=getattr(self, "sleep_fn", time.sleep), settle_delay_s=3.0,
            )
        return self._service.perform_power_sweep(frequency)

    def measure_all_frequencies(self):
        result = self._service.run()
        self.measurement_results = result["results"]
        self._save(
            {"config": self.config, "results": self.measurement_results},
            "amplifier_measurement",
            TEST_RESULTS_DIR / f"amplifier_measurement_{time.strftime('%Y%m%d_%H%M%S')}.json",
        )
        return result


class WorkerSignals(QObject):
    finished = Signal()
    error = Signal(str)
    message = Signal(str)
    progress = Signal(int)


class InstrumentWorker(QThread):
    def __init__(self, config_path=None, sleep_fn=None):
        super().__init__()
        self.config_path = resolve_path(config_path, CONFIG_FILE)
        self.sleep_fn = sleep_fn or time.sleep
        self.signals = WorkerSignals()

    def run(self):
        try:
            self.signals.message.emit("正在连接仪器...")
            self.signals.progress.emit(20)
            InstrumentControl(self.config_path)
            self.signals.progress.emit(60)
            self.sleep_fn(1)
            self.signals.progress.emit(100)
            self.signals.message.emit("所有启用的仪器连接成功")
            self.signals.finished.emit()
        except Exception as error:
            self.signals.error.emit(f"仪器连接失败: {error}")
            self.signals.message.emit(f"仪器连接失败: {error}")
