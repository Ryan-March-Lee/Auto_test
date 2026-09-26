"""Qt thread adapters for the shared measurement services.

Workers own only Qt thread lifetime, cancellation requests and signal
translation.  Measurement orchestration remains in the compatibility service
adapters in :mod:`enhanced_workers` until the production migration is done.
"""

from __future__ import annotations

from datetime import datetime
from threading import Event
import time

from PySide6.QtCore import QThread, QObject, Signal

from app.cancellation import MeasurementCancelled

class WorkerSignals(QObject):
    """Stable signal contract consumed by the GUI pages."""

    finished = Signal()
    error = Signal(str)
    stopped = Signal(str)
    result = Signal(object)
    progress = Signal(int)
    message = Signal(str)
    data_update = Signal(dict)
    step_pause = Signal(str)


class BaseWorker(QThread):
    """Common Qt lifecycle and cancellation adapter."""

    def __init__(self) -> None:
        super().__init__()
        self.signals = WorkerSignals()
        self._service = None
        self._stop_requested = False

    @property
    def service(self):
        return self._service

    def stop(self) -> None:
        self._stop_requested = True
        if self._service is not None:
            self._service.stop_measurement()

    def emit_message(self, message: str) -> None:
        self.signals.message.emit(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    def _failed(self, label: str, error: Exception) -> None:
        if isinstance(error, MeasurementCancelled):
            self.signals.stopped.emit(error.reason)
        else:
            self.signals.error.emit(f"{label}: {error}")


class InstrumentWorker(BaseWorker):
    """Connect instruments through the existing application adapter."""

    def __init__(self, config_path: str, sleep_fn=None):
        super().__init__()
        self.config_path = config_path
        self.sleep_fn = sleep_fn or time.sleep

    def run(self) -> None:
        try:
            from app.gui_runtime import connect_instruments

            self.emit_message("正在初始化仪器控制...")
            self.signals.progress.emit(25)
            controller = connect_instruments(self.config_path)
            self.signals.progress.emit(75)
            self.sleep_fn(1)
            self.signals.progress.emit(100)
            self.emit_message("仪器连接成功！")
            self.signals.result.emit(controller)
            self.signals.finished.emit()
        except Exception as error:
            self._failed("仪器连接失败", error)


class CableLossWorker(BaseWorker):
    """Run the two-step cable-loss service and expose its checkpoint."""

    def __init__(self, config_path: str, sleep_fn=None):
        super().__init__()
        self.config_path = config_path
        self.sleep_fn = sleep_fn
        self._continue_event = Event()
        self._waiting_for_continue = False

    def run(self) -> None:
        try:
            self.emit_message("开始线损测量...")
            from app.gui_runtime import create_cable_loss_measurement, prepare_configuration

            prepared = prepare_configuration(self.config_path)
            self._service = create_cable_loss_measurement(
                self.config_path,
                prepared_run=prepared,
                progress_callback=self.signals.progress.emit,
                message_callback=self.signals.message.emit,
                sleep_fn=self.sleep_fn,
            )
            if self._stop_requested:
                self._service.stop_measurement()
                return
            self._service.set_step_pause_callback(self.signals.step_pause.emit)
            self._service.measure_all_frequencies()
            self._waiting_for_continue = True
            if self._stop_requested:
                self.signals.stopped.emit("用户停止")
                return
            self._continue_event.wait()
            self._waiting_for_continue = False
            if self._stop_requested:
                self.signals.stopped.emit("用户停止")
                return
            self._service.continue_to_step2()
            self.emit_message("线损测量完成！")
            self.signals.finished.emit()
        except Exception as error:
            self._failed("线损测量失败", error)
        finally:
            self._waiting_for_continue = False

    def continue_measurement(self) -> None:
        if self._service is None:
            return
        self._continue_event.set()

    def stop(self) -> None:
        super().stop()
        self._continue_event.set()


class DriverMappingWorker(BaseWorker):
    """Run the shared driver-power mapping service."""

    def __init__(self, config_path: str, sleep_fn=None):
        super().__init__()
        self.config_path = config_path
        self.sleep_fn = sleep_fn

    def run(self) -> None:
        try:
            self.emit_message("开始驱动功放映射测量...")
            from app.gui_runtime import create_driver_mapping_measurement, prepare_configuration

            prepared = prepare_configuration(self.config_path)
            self._service = create_driver_mapping_measurement(
                self.config_path,
                prepared_run=prepared,
                progress_callback=self.signals.progress.emit,
                message_callback=self.signals.message.emit,
                data_callback=self.signals.data_update.emit,
                sleep_fn=self.sleep_fn,
            )
            self._service.measure_all_frequencies()
            self.emit_message("驱动功放映射测量完成！")
            self.signals.finished.emit()
        except Exception as error:
            self._failed("驱动映射测量失败", error)


class AmplifierWorker(BaseWorker):
    """Run the shared amplifier measurement service."""

    def __init__(self, config_path: str, sleep_fn=None):
        super().__init__()
        self.config_path = config_path
        self.sleep_fn = sleep_fn

    def run(self) -> None:
        try:
            self.emit_message("开始主功放测量...")
            from app.gui_runtime import create_amplifier_measurement, prepare_configuration

            prepared = prepare_configuration(self.config_path)
            self._service = create_amplifier_measurement(
                self.config_path,
                prepared_run=prepared,
                progress_callback=self.signals.progress.emit,
                message_callback=self.signals.message.emit,
                data_callback=self.signals.data_update.emit,
                sleep_fn=self.sleep_fn,
            )
            self._service.measure_all_frequencies()
            self.emit_message("主功放测量完成！")
            self.signals.finished.emit()
        except Exception as error:
            self._failed("主功放测量失败", error)


__all__ = [
    "WorkerSignals",
    "BaseWorker",
    "InstrumentWorker",
    "CableLossWorker",
    "DriverMappingWorker",
    "AmplifierWorker",
]
