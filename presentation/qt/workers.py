"""Qt-only thread adapters for application measurement use cases."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from threading import Event
import time

from PySide6.QtCore import QThread, QObject, Signal

from app.cancellation import MeasurementCancelled
from infrastructure.logging.app_logging import get_logger
from .run_contract import require_prepared_run

logger = get_logger(__name__)


def _close_errors(result) -> list[BaseException]:
    """把 ``close_all()`` 的返回值收敛成错误列表（``None`` 视为已关闭）。"""
    if result is None:
        return []
    if isinstance(result, BaseException):
        return [result]
    try:
        items = tuple(result)
    except TypeError:
        return [RuntimeError(f"端口关闭返回了无法判定的结果: {result!r}")]
    return [
        item if isinstance(item, BaseException) else RuntimeError(str(item))
        for item in items
        if item is not None
    ]


class WorkerSignals(QObject):
    finished = Signal()
    error = Signal(str)
    stopped = Signal(str)
    result = Signal(object)
    # 结构化归档结果（application.dto.archival.ArchivalOutcome）；未启用自动归档时不发。
    archival = Signal(object)
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
        self._termination_reason: str | None = None

    @property
    def service(self):
        return self._service

    @property
    def stop_reason(self) -> str:
        return self._termination_reason or "用户停止"

    def _request_termination(self, reason: str) -> bool:
        if self._stop_requested:
            return False
        self._termination_reason = reason
        self._stop_requested = True
        return True

    def stop(self) -> None:
        if not self._request_termination("用户停止"):
            return
        if self._service is not None:
            self._service.stop_measurement()

    def _stop_service(self) -> None:
        if self._service is not None:
            self._service.stop_measurement()

    def _emit_stopped(self, reason: str) -> None:
        """先交出停止前保存的部分结果（如果有），再报告停止。"""
        partial = getattr(self._service, "last_partial_result", None)
        if partial is not None:
            self.signals.result.emit(partial)
        self.signals.stopped.emit(reason)

    def emit_message(self, message: str) -> None:
        self.signals.message.emit(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    def _emit_archival(self) -> None:
        """把应用层自动归档结果转发给页面；未启用归档时不发事件。

        worker 只转发结构化 DTO，不解释产物含义；文案与按钮状态由页面生成。
        """
        outcome = getattr(self._service, "last_archival_outcome", None)
        if outcome is not None:
            self.signals.archival.emit(outcome)

    def _failed(self, label: str, error: Exception) -> None:
        if isinstance(error, MeasurementCancelled):
            self._emit_stopped(error.reason)
            return
        if self._stop_requested:
            # 用户已经请求停止：停止后的次生异常（例如部分结果保存失败）不得把
            # “已停止”改写成“测量失败”。异常详情写入运行日志，终态仍是停止。
            self.emit_message(f"{label}: {error}")
            self._emit_stopped(self.stop_reason)
            return
        self.signals.error.emit(f"{label}: {error}")

    def _construction_failed(self, error: Exception) -> None:
        self.signals.error.emit(f"应用用例构造失败: {error}")

    @staticmethod
    def _default_factory(name: str) -> Callable:
        from app import gui_runtime

        return getattr(gui_runtime, name)


class InstrumentWorker(BaseWorker):
    """按运行契约连接仪器；没有运行对象时不得打开任何设备。

    ``prepared_run`` 是连接的唯一输入：硬件配置由运行契约的冻结快照解析，因此
    “连的是本次运行的仪器”可以被证明。worker 不再接收配置路径参数——连接入口
    只接受运行对象，配置来源没有第二条通道。
    """

    def __init__(
        self,
        prepared_run,
        *,
        sleep_fn=None,
        measurement_port=None,
        connect_factory=None,
    ):
        super().__init__()
        self.prepared_run = prepared_run
        self.sleep_fn = sleep_fn or time.sleep
        self.measurement_port = measurement_port
        self.connect_factory = connect_factory
        # 自行关闭但未确认关闭的端口：保留引用，由协调器收管为未决清理。
        self.unreleased_port = None

    def _close_own_port(self, controller) -> None:
        """关闭尚未交接的端口；未确认关闭时保留引用并报告，绝不静默丢弃。"""
        self.measurement_port = None
        close = getattr(controller, "close_all", None)
        if close is None:
            return
        errors: list[BaseException] = []
        try:
            result = close(close_rf=True)
        except Exception as error:  # noqa: BLE001 - 关闭失败必须上报，不能覆盖原始错误
            errors.append(error)
        else:
            errors.extend(_close_errors(result))
        if not errors:
            return
        self.unreleased_port = controller
        detail = "；".join(str(error) or type(error).__name__ for error in errors)
        self.emit_message(f"仪器关闭未确认: {detail}")
        logger.warning("仪器关闭未确认: %s", detail)

    def run(self) -> None:
        controller = None
        handed_off = False
        try:
            self.emit_message("正在初始化仪器控制...")
            self.signals.progress.emit(25)
            # 缺少运行契约时明确失败：连接仪器必须绑定本次运行，绝不使用全局配置。
            prepared = require_prepared_run(self.prepared_run, "仪器连接")
            connect_instruments = self.connect_factory or self._default_factory("connect_instruments")
            controller = connect_instruments(prepared)
            self.measurement_port = controller
            if self._stop_requested:
                self._close_own_port(controller)
                self._emit_stopped(self.stop_reason)
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
                self._close_own_port(controller)
            self._failed("仪器连接失败", error)


class CableLossWorker(BaseWorker):
    """装配并驱动线损测量用例；运行契约是唯一输入。"""

    def __init__(
        self,
        *,
        prepared_run,
        sleep_fn=None,
        measurement_port=None,
        measurement_factory=None,
    ):
        super().__init__()
        self.sleep_fn = sleep_fn
        self.measurement_port = measurement_port
        self.measurement_factory = measurement_factory
        self.prepared_run = prepared_run
        self._continue_event = Event()
        self._waiting_for_continue = False
        self._continue_requested = False

    def run(self) -> None:
        try:
            self.emit_message("开始线损测量...")
            try:
                create_measurement = self.measurement_factory or self._default_factory("create_cable_loss_measurement")
                # 缺少运行契约时明确失败，绝不在 worker 内隐式创建运行。
                prepared = require_prepared_run(self.prepared_run, "线损测量")
                # 校验通过后、装配测量用例前再次检查停止意图：应用组装层可能连接仪器，
                # 因此停止请求必须在这里拦下，而不是等到组装完成。
                if self._stop_requested:
                    self._emit_stopped(self.stop_reason)
                    return
                self._service = create_measurement(prepared_run=prepared,
                    progress_callback=self.signals.progress.emit, message_callback=self.signals.message.emit,
                    data_callback=self.signals.data_update.emit, sleep_fn=self.sleep_fn,
                    measurement_port=self.measurement_port)
            except Exception as error:
                self._construction_failed(error)
                return
            if self._stop_requested:
                self._stop_service()
                self._emit_stopped(self.stop_reason)
                return
            self._service.set_step_pause_callback(self.signals.step_pause.emit)
            self._service.measure_all_frequencies()
            self._waiting_for_continue = True
            if self._stop_requested:
                self._emit_stopped(self.stop_reason)
                return
            self._continue_event.wait()
            self._waiting_for_continue = False
            if self._stop_requested:
                self._emit_stopped(self.stop_reason)
                return
            self._service.continue_to_step2()
            result = getattr(self._service, "last_result", None)
            if result is None:
                raise RuntimeError("线损测量完成但未提供结构化结果")
            self.signals.result.emit(result)
            self._emit_archival()
            self.emit_message("线损测量完成！")
            self.signals.finished.emit()
        except Exception as error:
            self._failed("线损测量失败", error)
        finally:
            self._waiting_for_continue = False
            self.measurement_port = None

    def continue_stage2_measurement(self) -> None:
        if self._service is None or not self._waiting_for_continue or self._continue_requested:
            return
        self._continue_requested = True
        self._continue_event.set()

    def stop(self) -> None:
        super().stop()
        self._continue_event.set()


class DriverMappingWorker(BaseWorker):
    """装配并驱动驱动映射测量用例；运行契约与显式线损来源是唯一输入。"""

    def __init__(
        self,
        *,
        prepared_run,
        cable_loss_path=None,
        sleep_fn=None,
        measurement_port=None,
        measurement_factory=None,
    ):
        super().__init__()
        self.sleep_fn = sleep_fn
        self.measurement_port = measurement_port
        self.measurement_factory = measurement_factory
        self.prepared_run = prepared_run
        # 页面在点击开始时冻结的显式线损来源；None 表示旧调用方走运行引用。
        self.cable_loss_path = cable_loss_path

    def run(self) -> None:
        self._run_measurement("开始驱动功放映射测量...", "create_driver_mapping_measurement", "驱动映射测量")

    def _run_measurement(self, start_message, factory_name, label):
        try:
            self.emit_message(start_message)
            if self._stop_requested:
                self._emit_stopped(self.stop_reason)
                return
            try:
                create_measurement = self.measurement_factory or self._default_factory(factory_name)
                # 缺少运行契约时明确失败，绝不在 worker 内隐式创建运行。
                prepared = require_prepared_run(self.prepared_run, "驱动映射测量")
                if self._stop_requested:
                    self._emit_stopped(self.stop_reason)
                    return
                self._service = create_measurement(prepared_run=prepared,
                    cable_loss_path=self.cable_loss_path,
                    progress_callback=self.signals.progress.emit, message_callback=self.signals.message.emit,
                    data_callback=self.signals.data_update.emit, sleep_fn=self.sleep_fn,
                    measurement_port=self.measurement_port)
            except Exception as error:
                self._construction_failed(error)
                return
            if self._stop_requested:
                self._stop_service()
                self._emit_stopped(self.stop_reason)
                return
            self._service.measure_all_frequencies()
            result = getattr(self._service, "last_result", None)
            if result is None:
                raise RuntimeError(f"{label}完成但未提供结构化结果")
            self.signals.result.emit(result)
            self._emit_archival()
            self.emit_message(f"{label}完成！")
            self.signals.finished.emit()
        except Exception as error:
            self._failed(f"{label}失败", error)
        finally:
            self.measurement_port = None


class AmplifierWorker(BaseWorker):
    """装配并驱动主功放测量用例；运行契约与显式校准来源是唯一输入。"""

    def __init__(
        self,
        *,
        prepared_run,
        cable_loss_path=None,
        driver_mapping_path=None,
        sleep_fn=None,
        measurement_port=None,
        measurement_factory=None,
    ):
        super().__init__()
        self.sleep_fn = sleep_fn
        self.measurement_port = measurement_port
        self.measurement_factory = measurement_factory
        self.prepared_run = prepared_run
        # 页面在点击开始时冻结的显式校准来源；None 表示旧调用方走运行引用。
        self.cable_loss_path = cable_loss_path
        self.driver_mapping_path = driver_mapping_path

    def run(self) -> None:
        try:
            self.emit_message("开始主功放测量...")
            if self._stop_requested:
                self._emit_stopped(self.stop_reason)
                return
            try:
                create_measurement = self.measurement_factory or self._default_factory("create_amplifier_measurement")
                # 缺少运行契约时明确失败，绝不在 worker 内隐式创建运行。
                prepared = require_prepared_run(self.prepared_run, "主功放测量")
                if self._stop_requested:
                    self._emit_stopped(self.stop_reason)
                    return
                self._service = create_measurement(prepared_run=prepared,
                    cable_loss_path=self.cable_loss_path,
                    driver_mapping_path=self.driver_mapping_path,
                    progress_callback=self.signals.progress.emit, message_callback=self.signals.message.emit,
                    data_callback=self.signals.data_update.emit, sleep_fn=self.sleep_fn,
                    measurement_port=self.measurement_port)
            except Exception as error:
                self._construction_failed(error)
                return
            if self._stop_requested:
                self._stop_service()
                self._emit_stopped(self.stop_reason)
                return
            self._service.measure_all_frequencies()
            result = getattr(self._service, "last_result", None)
            if result is None:
                raise RuntimeError("主功放测量完成但未提供结构化结果")
            self.signals.result.emit(result)
            self._emit_archival()
            self.emit_message("主功放测量完成！")
            self.signals.finished.emit()
        except Exception as error:
            self._failed("主功放测量失败", error)
        finally:
            self.measurement_port = None


__all__ = ["WorkerSignals", "BaseWorker", "InstrumentWorker", "CableLossWorker", "DriverMappingWorker", "AmplifierWorker"]
