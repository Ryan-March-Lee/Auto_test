"""Qt-only thread adapters for application measurement use cases."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from threading import Event
import time

from PySide6.QtCore import QThread, QObject, Signal

from app.cancellation import MeasurementCancelled
from application.lifecycle import StopIntent


class WorkerSignals(QObject):
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
        self._termination_intent: StopIntent | None = None
        self._termination_reason: str | None = None

    @property
    def service(self):
        return self._service

    @property
    def stop_reason(self) -> str:
        return self._termination_reason or "用户停止"

    @property
    def termination_intent(self) -> StopIntent | None:
        return self._termination_intent

    def _request_termination(self, intent: StopIntent, reason: str) -> bool:
        if self._termination_intent is StopIntent.EMERGENCY_STOP:
            return False
        if self._termination_intent is not None:
            if intent is not StopIntent.EMERGENCY_STOP:
                return False
        self._termination_intent = intent
        self._termination_reason = reason
        self._stop_requested = True
        return True

    def stop(self) -> None:
        if not self._request_termination(StopIntent.STOP, "用户停止"):
            return
        if self._service is not None:
            self._service.stop_measurement()

    def cancel(self) -> None:
        if not self._request_termination(StopIntent.CANCEL, "任务已取消"):
            return
        if self._service is not None:
            cancel = getattr(self._service, "cancel_measurement", None)
            if cancel is None:
                raise RuntimeError("测量服务不支持取消")
            cancel()

    def emergency_stop(self) -> None:
        if not self._request_termination(StopIntent.EMERGENCY_STOP, "紧急停止"):
            return
        if self._service is not None:
            emergency_stop = getattr(self._service, "emergency_stop", None)
            if emergency_stop is None:
                raise RuntimeError("测量服务不支持紧急停止")
            emergency_stop()

    def _stop_service(self) -> None:
        if self._service is None:
            return
        if self._termination_intent is StopIntent.EMERGENCY_STOP:
            emergency_stop = getattr(self._service, "emergency_stop", None)
            if emergency_stop is None:
                raise RuntimeError("测量服务不支持紧急停止")
            emergency_stop()
        elif self._termination_intent is StopIntent.CANCEL:
            cancel = getattr(self._service, "cancel_measurement", None)
            if cancel is None:
                raise RuntimeError("测量服务不支持取消")
            cancel()
        else:
            self._service.stop_measurement()

    def emit_message(self, message: str) -> None:
        self.signals.message.emit(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    def _failed(self, label: str, error: Exception) -> None:
        if isinstance(error, MeasurementCancelled):
            self.signals.stopped.emit(error.reason)
        else:
            self.signals.error.emit(f"{label}: {error}")

    def _construction_failed(self, error: Exception) -> None:
        self.signals.error.emit(f"应用用例构造失败: {error}")

    @staticmethod
    def _default_factory(name: str) -> Callable:
        from app import gui_runtime

        return getattr(gui_runtime, name)


class InstrumentWorker(BaseWorker):
    def __init__(self, config_path: str, sleep_fn=None, measurement_port=None, connect_factory=None):
        super().__init__()
        self.config_path = config_path
        self.sleep_fn = sleep_fn or time.sleep
        self.measurement_port = measurement_port
        self.connect_factory = connect_factory

    def run(self) -> None:
        controller = None
        handed_off = False
        try:
            self.emit_message("正在初始化仪器控制...")
            self.signals.progress.emit(25)
            connect_instruments = self.connect_factory or self._default_factory("connect_instruments")
            controller = connect_instruments(self.config_path)
            self.measurement_port = controller
            if self._stop_requested:
                close = getattr(controller, "close_all", None)
                if close is not None:
                    close(close_rf=True)
                self.measurement_port = None
                self.signals.stopped.emit(self.stop_reason)
                return
            self.signals.progress.emit(75)
            self.sleep_fn(1)
            self.signals.progress.emit(100)
            self.emit_message("仪器连接成功！")
            handed_off = True
            self.signals.result.emit(controller)
            self.signals.finished.emit()
        except Exception as error:
            if controller is not None and not handed_off:
                close = getattr(controller, "close_all", None)
                if close is not None:
                    try:
                        close(close_rf=True)
                    except Exception:
                        pass
                self.measurement_port = None
            self._failed("仪器连接失败", error)


class CableLossWorker(BaseWorker):
    def __init__(self, config_path: str, sleep_fn=None, measurement_port=None, measurement_factory=None, prepare_factory=None):
        super().__init__()
        self.config_path = config_path
        self.sleep_fn = sleep_fn
        self.measurement_port = measurement_port
        self.measurement_factory = measurement_factory
        self.prepare_factory = prepare_factory
        self._continue_event = Event()
        self._waiting_for_continue = False
        self._continue_requested = False

    def run(self) -> None:
        try:
            self.emit_message("开始线损测量...")
            try:
                prepare_configuration = self.prepare_factory or self._default_factory("prepare_configuration")
                create_measurement = self.measurement_factory or self._default_factory("create_cable_loss_measurement")
                prepared = prepare_configuration(self.config_path, operation="cable_loss")
                self._service = create_measurement(self.config_path, prepared_run=prepared,
                    progress_callback=self.signals.progress.emit, message_callback=self.signals.message.emit,
                    data_callback=self.signals.data_update.emit, sleep_fn=self.sleep_fn,
                    measurement_port=self.measurement_port)
            except Exception as error:
                self._construction_failed(error)
                return
            if self._stop_requested:
                self._stop_service()
                self.signals.stopped.emit(self.stop_reason)
                return
            self._service.set_step_pause_callback(self.signals.step_pause.emit)
            self._service.measure_all_frequencies()
            self._waiting_for_continue = True
            if self._stop_requested:
                self.signals.stopped.emit(self.stop_reason)
                return
            self._continue_event.wait()
            self._waiting_for_continue = False
            if self._stop_requested:
                self.signals.stopped.emit(self.stop_reason)
                return
            self._service.continue_to_step2()
            result = getattr(self._service, "last_result", None)
            if result is None:
                raise RuntimeError("线损测量完成但未提供结构化结果")
            self.signals.result.emit(result)
            self.emit_message("线损测量完成！")
            self.signals.finished.emit()
        except Exception as error:
            self._failed("线损测量失败", error)
        finally:
            self._waiting_for_continue = False
            self.measurement_port = None

    def continue_measurement(self) -> None:
        if self._service is None or not self._waiting_for_continue or self._continue_requested:
            return
        self._continue_requested = True
        self._continue_event.set()

    def stop(self) -> None:
        super().stop()
        self._continue_event.set()

    def cancel(self) -> None:
        super().cancel()
        self._continue_event.set()

    def emergency_stop(self) -> None:
        super().emergency_stop()
        self._continue_event.set()


class DriverMappingWorker(BaseWorker):
    def __init__(self, config_path: str, sleep_fn=None, measurement_port=None, measurement_factory=None, prepare_factory=None):
        super().__init__()
        self.config_path = config_path
        self.sleep_fn = sleep_fn
        self.measurement_port = measurement_port
        self.measurement_factory = measurement_factory
        self.prepare_factory = prepare_factory

    def run(self) -> None:
        self._run_measurement("开始驱动功放映射测量...", "create_driver_mapping_measurement", "driver_mapping", "驱动映射测量")

    def _run_measurement(self, start_message, factory_name, operation, label):
        try:
            self.emit_message(start_message)
            if self._stop_requested:
                self.signals.stopped.emit(self.stop_reason)
                return
            try:
                prepare_configuration = self.prepare_factory or self._default_factory("prepare_configuration")
                create_measurement = self.measurement_factory or self._default_factory(factory_name)
                if operation is None:
                    prepared = prepare_configuration(self.config_path)
                else:
                    prepared = prepare_configuration(self.config_path, operation=operation)
                if self._stop_requested:
                    self.signals.stopped.emit(self.stop_reason)
                    return
                self._service = create_measurement(self.config_path, prepared_run=prepared,
                    progress_callback=self.signals.progress.emit, message_callback=self.signals.message.emit,
                    data_callback=self.signals.data_update.emit, sleep_fn=self.sleep_fn,
                    measurement_port=self.measurement_port)
            except Exception as error:
                self._construction_failed(error)
                return
            if self._stop_requested:
                self._stop_service()
                self.signals.stopped.emit(self.stop_reason)
                return
            self._service.measure_all_frequencies()
            result = getattr(self._service, "last_result", None)
            if result is None:
                raise RuntimeError(f"{label}完成但未提供结构化结果")
            self.signals.result.emit(result)
            self.emit_message(f"{label}完成！")
            self.signals.finished.emit()
        except Exception as error:
            self._failed(f"{label}失败", error)
        finally:
            self.measurement_port = None


class AmplifierWorker(BaseWorker):
    def __init__(self, config_path: str, sleep_fn=None, measurement_port=None, measurement_factory=None, prepare_factory=None):
        super().__init__()
        self.config_path = config_path
        self.sleep_fn = sleep_fn
        self.measurement_port = measurement_port
        self.measurement_factory = measurement_factory
        self.prepare_factory = prepare_factory

    def run(self) -> None:
        try:
            self.emit_message("开始主功放测量...")
            if self._stop_requested:
                self.signals.stopped.emit(self.stop_reason)
                return
            try:
                prepare_configuration = self.prepare_factory or self._default_factory("prepare_configuration")
                create_measurement = self.measurement_factory or self._default_factory("create_amplifier_measurement")
                prepared = prepare_configuration(self.config_path)
                if self._stop_requested:
                    self.signals.stopped.emit(self.stop_reason)
                    return
                self._service = create_measurement(self.config_path, prepared_run=prepared,
                    progress_callback=self.signals.progress.emit, message_callback=self.signals.message.emit,
                    data_callback=self.signals.data_update.emit, sleep_fn=self.sleep_fn,
                    measurement_port=self.measurement_port)
            except Exception as error:
                self._construction_failed(error)
                return
            if self._stop_requested:
                self._stop_service()
                self.signals.stopped.emit(self.stop_reason)
                return
            self._service.measure_all_frequencies()
            result = getattr(self._service, "last_result", None)
            if result is None:
                raise RuntimeError("主功放测量完成但未提供结构化结果")
            self.signals.result.emit(result)
            self.emit_message("主功放测量完成！")
            self.signals.finished.emit()
        except Exception as error:
            self._failed("主功放测量失败", error)
        finally:
            self.measurement_port = None


__all__ = ["WorkerSignals", "BaseWorker", "InstrumentWorker", "CableLossWorker", "DriverMappingWorker", "AmplifierWorker"]
