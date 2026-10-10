"""Coordinate GUI measurement worker lifecycles without owning hardware ports."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable
from uuid import uuid4

from PySide6.QtCore import QObject, Signal
from application.lifecycle import StopIntent

from .measurement_controller_contract import (
    ControllerState,
    MeasurementCommand,
    WorkerFactories,
)
from .measurement_state import (
    MeasurementKind,
    MeasurementResultReference,
    MeasurementStatus,
    MeasurementViewState,
)


class _ControllerSignals(QObject):
    """Qt signals keep page callbacks on their receiver's Qt thread."""

    progress = Signal(int)
    message = Signal(str)
    data_update = Signal(object)
    result = Signal(object)
    finished = Signal()
    stopped = Signal(str)
    error = Signal(str)
    step_pause = Signal(str)
    rejected = Signal(str)
    state_changed = Signal(object)
    thread_finished = Signal()

    def __init__(self) -> None:
        super().__init__()


class MeasurementController:
    """Own measurement workers and expose a stable, framework-neutral event API."""

    def __init__(
        self,
        worker_factories: WorkerFactories,
        config_path_provider: Callable[[], str],
        *,
        measurement_port: Any = None,
        log_sink: Callable[[str], Any] | None = None,
    ) -> None:
        """Create a controller.

        ``config_path_provider`` is the runtime configuration authority.  The
        path carried by a page command is retained as page input metadata, but
        is intentionally replaced by the provider value before worker
        construction so stale page input cannot select another run.
        """
        self._worker_factories = dict(worker_factories)
        self._config_path_provider = config_path_provider
        self._measurement_port = measurement_port
        self._log_sink = log_sink
        self.signals = _ControllerSignals()
        self._state = ControllerState()
        self._worker: Any = None
        self._worker_slots: list[tuple[Any, Callable[..., Any]]] = []
        self._thread_finished_signal: Any = None
        self._shutdown_waiting = False
        self._ignore_thread_finished_once = False
        self._stop_intent: StopIntent | None = None

    @property
    def state(self) -> ControllerState:
        return self._state

    @property
    def current_worker(self) -> Any:
        """当前活动 worker；仅供生命周期诊断，不转移 worker 所有权。"""
        return self._worker

    def set_measurement_port(self, measurement_port: Any) -> None:
        """更新由 GUI/runtime 注入的当前测量端口引用。"""
        if self._state.is_active:
            raise RuntimeError("测量运行期间不能替换测量端口")
        self._measurement_port = measurement_port

    def start_cable_loss(self, command: MeasurementCommand) -> bool:
        return self._start(MeasurementKind.CABLE_LOSS, command)

    def start_driver_mapping(self, command: MeasurementCommand) -> bool:
        return self._start(MeasurementKind.DRIVER_MAPPING, command)

    def start_amplifier(self, command: MeasurementCommand) -> bool:
        return self._start(MeasurementKind.AMPLIFIER, command)

    def _start(self, kind: MeasurementKind, command: MeasurementCommand) -> bool:
        if not isinstance(command, MeasurementCommand):
            raise TypeError("command 必须是 MeasurementCommand")
        if self._state.is_active:
            self.signals.rejected.emit("已有测量正在运行")
            return False
        self._ignore_thread_finished_once = False
        self._stop_intent = None

        preparing = MeasurementViewState(kind).prepare()
        self._set_state(kind, preparing)
        try:
            factory = self._worker_factories[kind]
            config_path = self._config_path_provider()
            if not isinstance(config_path, str) or not config_path.strip():
                raise ValueError("配置路径提供者返回了空路径")
            effective_command = MeasurementCommand(config_path, command.options)
            worker = factory(effective_command, measurement_port=self._measurement_port)
            self._worker = worker
            self._bind_worker(worker)
            self._set_state(kind, preparing.run())
            worker.start()
            return True
        except Exception as error:
            self._disconnect_worker()
            self._worker = None
            self._set_state(kind, preparing.fail(str(error) or error.__class__.__name__))
            self.signals.error.emit(str(error))
            return False

    def _bind_worker(self, worker: Any) -> None:
        mapping = {
            "progress": self._on_progress,
            "message": self._on_message,
            "data_update": self.signals.data_update.emit,
            "result": self._on_result,
            "finished": self._on_finished,
            "stopped": self._on_stopped,
            "error": self._on_error,
            "step_pause": self._on_step_pause,
        }
        for name, slot in mapping.items():
            signal = getattr(worker.signals, name, None)
            if signal is not None:
                signal.connect(slot)
                self._worker_slots.append((signal, slot))
        thread_finished = getattr(worker, "finished", None)
        if thread_finished is not None and thread_finished is not getattr(worker.signals, "finished", None):
            thread_finished.connect(self._on_thread_finished)
            self._thread_finished_signal = thread_finished

    def _disconnect_worker(self) -> None:
        for signal, slot in self._worker_slots:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError, ValueError):
                pass
        self._worker_slots.clear()
        if self._thread_finished_signal is not None:
            try:
                self._thread_finished_signal.disconnect(self._on_thread_finished)
            except (RuntimeError, TypeError, ValueError):
                pass
            self._thread_finished_signal = None

    def _set_state(self, kind: MeasurementKind, view_state: MeasurementViewState) -> None:
        self._state = ControllerState(kind, view_state.status, view_state)
        self.signals.state_changed.emit(self._state)

    def _update_view(self, **changes: Any) -> None:
        view = self._state.view_state
        if view is None:
            return
        updated = replace(view, **changes) if view.status in {
            MeasurementStatus.WAITING_FOR_CONTINUE,
            MeasurementStatus.STOPPING,
        } else view.transition(view.status, **changes)
        self._set_state(self._state.kind, updated)

    def _on_progress(self, value: int) -> None:
        if self._state.view_state is not None and self._state.status is MeasurementStatus.RUNNING:
            self._update_view(progress=max(0, min(100, int(value))))
        self.signals.progress.emit(value)

    def _on_message(self, text: str) -> None:
        if self._log_sink is not None:
            self._log_sink(text)
        if self._state.view_state is not None and not self._state.status.is_terminal:
            self._update_view(message=text)
        self.signals.message.emit(text)

    def _on_result(self, value: Any) -> None:
        kind = self._state.kind
        if kind is None:
            return
        reference = MeasurementResultReference(str(uuid4()), kind, value)
        self._update_view(result=reference)
        self.signals.result.emit(reference)

    def _on_step_pause(self, message: str) -> None:
        if self._state.kind is MeasurementKind.CABLE_LOSS and self._state.status is MeasurementStatus.RUNNING:
            view = self._state.view_state.wait_for_continue(message)
            self._set_state(MeasurementKind.CABLE_LOSS, view)
        self.signals.step_pause.emit(message)

    def _on_finished(self) -> None:
        view = self._state.view_state
        if view is not None and not view.status.is_terminal:
            if self._stop_intent is not None:
                if self._stop_intent is StopIntent.EMERGENCY_STOP:
                    reason = view.stop_reason or "紧急停止"
                    self._set_state(view.kind, view.emergency_stop(reason))
                else:
                    reason = view.stop_reason or (
                        "任务已取消" if self._stop_intent is StopIntent.CANCEL else "用户停止"
                    )
                    try:
                        self._set_state(view.kind, view.stop(reason))
                    except ValueError:
                        self._set_state(view.kind, view.transition(MeasurementStatus.CANCELLED, stop_reason=reason))
                self.signals.stopped.emit(reason)
                self._cleanup_if_non_threaded()
                return
            result = view.result
            if result is None:
                self._on_error("worker 完成但未提供测量结果")
                return
            try:
                self._set_state(view.kind, view.finish(result))
            except ValueError:
                self._set_state(view.kind, view.transition(MeasurementStatus.FAILED, error_text="worker 在非法状态下完成"))
                self.signals.error.emit("worker 在非法状态下完成")
        self.signals.finished.emit()
        self._cleanup_if_non_threaded()

    def _on_stopped(self, reason: str) -> None:
        view = self._state.view_state
        final_reason = reason or (view.stop_reason if view is not None else None) or "用户停止"
        if view is not None and not view.status.is_terminal:
            if self._stop_intent is not None:
                final_reason = view.stop_reason or (
                    "紧急停止" if self._stop_intent is StopIntent.EMERGENCY_STOP
                    else "任务已取消" if self._stop_intent is StopIntent.CANCEL
                    else "用户停止"
                )
            else:
                final_reason = reason or view.stop_reason or "用户停止"
            if self._stop_intent is StopIntent.EMERGENCY_STOP:
                self._set_state(view.kind, view.emergency_stop(final_reason))
            else:
                try:
                    self._set_state(view.kind, view.stop(final_reason))
                except ValueError:
                    self._set_state(view.kind, view.transition(MeasurementStatus.CANCELLED, stop_reason=final_reason))
        self.signals.stopped.emit(final_reason)
        self._cleanup_if_non_threaded()

    def _on_error(self, text: str) -> None:
        view = self._state.view_state
        if view is not None and not view.status.is_terminal:
            self._set_state(view.kind, view.fail(text or "测量失败"))
        self.signals.error.emit(text)
        self._cleanup_if_non_threaded()

    def _on_thread_finished(self) -> None:
        if self._shutdown_waiting:
            return
        if self._ignore_thread_finished_once:
            self._ignore_thread_finished_once = False
            return
        if self._state.is_active:
            text = "worker 线程结束但未报告终态"
            view = self._state.view_state
            if view is not None:
                try:
                    self._set_state(view.kind, view.fail(text))
                except ValueError:
                    pass
            self.signals.error.emit(text)
        self._cleanup_worker()
        self.signals.thread_finished.emit()

    def _cleanup_if_non_threaded(self) -> None:
        if self._thread_finished_signal is None:
            self._cleanup_worker()
            self.signals.thread_finished.emit()

    def _cleanup_worker(self) -> None:
        self._disconnect_worker()
        self._worker = None

    def stop(self) -> bool:
        if not self._state.is_active or self._worker is None:
            self.signals.rejected.emit("当前没有可停止的测量")
            return False
        return self._request_stop("stop")

    def cancel(self) -> bool:
        if not self._state.is_active or self._worker is None:
            self.signals.rejected.emit("当前没有可取消的测量")
            return False
        return self._request_stop("cancel")

    def emergency_stop(self) -> bool:
        if not self._state.is_active or self._worker is None:
            self.signals.rejected.emit("当前没有可紧急停止的测量")
            return False
        return self._request_stop("emergency_stop")

    def _request_stop(self, method: str) -> bool:
        view = self._state.view_state
        reason = {"emergency_stop": "紧急停止", "cancel": "任务已取消"}.get(method, "用户停止")
        intent = StopIntent.EMERGENCY_STOP if method == "emergency_stop" else StopIntent.STOP
        if method == "cancel":
            intent = StopIntent.CANCEL
        if self._stop_intent is StopIntent.EMERGENCY_STOP:
            intent = self._stop_intent
            reason = "紧急停止"
        elif self._stop_intent is None or intent is StopIntent.EMERGENCY_STOP:
            self._stop_intent = intent
        else:
            intent = self._stop_intent
            reason = "任务已取消" if intent is StopIntent.CANCEL else "用户停止"
        if view is not None and view.status in {MeasurementStatus.RUNNING, MeasurementStatus.WAITING_FOR_CONTINUE, MeasurementStatus.PREPARING}:
            self._set_state(view.kind, view.stop(reason, stopping=True))
        try:
            getattr(self._worker, method)()
        except Exception as error:
            self._on_error(str(error) or error.__class__.__name__)
            return False
        return True

    def shutdown(self, timeout_ms: int = 5000) -> bool:
        """请求停止并在有限时间内等待 worker 退出。

        关闭窗口时不能无限期阻塞 Qt GUI 线程。若 worker 未在期限内退出，
        保留 worker 引用并返回 ``False``，由窗口拒绝关闭，等待后续重试。
        controller 不负责关闭外部注入的 measurement port。
        """
        if not isinstance(timeout_ms, int) or timeout_ms < 0:
            raise ValueError("timeout_ms 必须是非负整数")
        worker = self._worker
        if not self._state.is_active or worker is None:
            return True
        if not self._request_stop("stop"):
            return False
        if self._worker is None:
            return True
        wait = getattr(worker, "wait", None)
        if wait is None:
            return False
        try:
            self._shutdown_waiting = True
            completed = wait(timeout_ms)
        except TypeError:
            completed = wait()
        finally:
            self._shutdown_waiting = False
        if completed is False:
            self.signals.error.emit("测量线程未能在关闭期限内停止")
            return False

        view = self._state.view_state
        if view is not None and self._state.status is MeasurementStatus.STOPPING:
            reason = view.stop_reason or (
                "紧急停止" if self._stop_intent is StopIntent.EMERGENCY_STOP
                else "任务已取消" if self._stop_intent is StopIntent.CANCEL
                else "用户停止"
            )
            if self._stop_intent is StopIntent.EMERGENCY_STOP:
                self._set_state(view.kind, view.emergency_stop(reason))
            else:
                self._set_state(view.kind, view.stop(reason))
            self.signals.stopped.emit(reason)
        self._ignore_thread_finished_once = True
        self._cleanup_worker()
        self.signals.thread_finished.emit()
        return True

    def continue_cable_loss(self) -> bool:
        if self._state.kind is not MeasurementKind.CABLE_LOSS or self._state.status is not MeasurementStatus.WAITING_FOR_CONTINUE or self._worker is None:
            self.signals.rejected.emit("线损测量当前不在等待继续状态")
            return False
        method = getattr(self._worker, "continue_measurement", None)
        if method is None:
            self.signals.rejected.emit("当前线损 worker 不支持继续")
            return False
        try:
            method()
        except Exception as error:
            self._on_error(str(error))
            return False
        view = self._state.view_state
        self._set_state(MeasurementKind.CABLE_LOSS, view.continue_running())
        return True


__all__ = ["MeasurementController"]
