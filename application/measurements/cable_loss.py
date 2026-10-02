"""Application use case for the two-step cable-loss measurement."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, Callable

from app.cancellation import CancellationToken
from application.dto import CableLossMeasurementRequest
from application.ports.result_repository import MeasurementResultRepository
from measurement_services import CableLossService


class CableLossUseCase:
    """Coordinate cable-loss measurement without GUI or persistence details.

    The first call to :meth:`measure_all_frequencies` measures path 1 and
    returns a waiting result.  :meth:`continue_to_step2` resumes the same
    service, measures path 2, and persists the completed result through the
    injected repository.
    """

    def __init__(
        self,
        request: CableLossMeasurementRequest,
        *,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self.request = request
        if request.measurement_port is None:
            raise ValueError("CableLossUseCase 必须由应用组装层注入 measurement_port")
        self.config = _service_config(request.configuration)
        self.inst_ctrl = request.measurement_port
        self.run_id = request.run_id
        self.run_directory = request.run_directory
        self.result_repository = request.result_repository
        if self.result_repository is None:
            raise ValueError("CableLossUseCase 必须注入 result_repository")
        self.sleep_fn = sleep_fn or time.sleep
        self._token = request.cancellation_token or CancellationToken()
        self._service = CableLossService(
            self.config,
            self.inst_ctrl,
            run_id=self.run_id,
            event_sink=request.event_sink,
            cancellation_token=self._token,
            sleep_fn=self.sleep_fn,
        )
        self._waiting_for_path2 = False
        self._started = False
        self._completed = False
        self._terminal = False
        self._cleaned_after_stop = False
        self._step_pause_callback: Callable[[str], None] | None = None
        self.last_result: dict[str, Any] | None = None

    @property
    def path1_losses(self) -> dict[Any, float]:
        """Return the path-1 measurements as an explicit result view."""
        return self._service.path1_losses

    @property
    def cable_losses(self) -> dict[float, dict[str, float]]:
        """Return completed cable-loss values, normalized to float keys."""
        result = self.last_result or {}
        return {
            float(key): value for key, value in result.get("cable_losses", {}).items()
        }

    def set_step_pause_callback(self, callback: Callable[[str], None] | None) -> None:
        """Register the presentation callback for the path-2 checkpoint."""
        self._step_pause_callback = callback

    def measure_path_loss(self, frequency: float) -> float:
        return self._service.measure_path_loss(frequency)

    def measure_all_frequencies(self) -> dict[str, Any]:
        if self._terminal:
            raise RuntimeError("线损测量已经结束，不能重复执行")
        if self._started:
            raise RuntimeError("线损测量已经开始，不能重复开始")
        self._started = True
        try:
            result = self._service.run(path2_confirmed=False)
            self._waiting_for_path2 = result.get("status") == "waiting"
            self.last_result = result
            if self._waiting_for_path2 and self._step_pause_callback:
                self._step_pause_callback("请连接路径2")
            return result
        except Exception:
            self._terminal = True
            raise
        finally:
            if not self._waiting_for_path2:
                self._started = False

    def continue_to_step2(self) -> dict[str, Any]:
        if self._terminal:
            raise RuntimeError("线损测量已经结束，不能继续")
        if not self._waiting_for_path2:
            raise RuntimeError("线损测量当前不在等待路径2确认状态")
        try:
            result = self._service.run(path2_confirmed=True)
            self.last_result = result
            self._save(result)
            self._completed = True
            self._terminal = True
            return result
        except Exception:
            self._terminal = True
            raise
        finally:
            self._waiting_for_path2 = False
            self._started = False

    def stop_measurement(self) -> None:
        self._token.request_emergency_stop(reason="用户停止")
        if self._waiting_for_path2 and not self._cleaned_after_stop:
            self._service.cleanup()
            self._waiting_for_path2 = False
            self._started = False
            self._terminal = True
            self._cleaned_after_stop = True

    def _save(self, result: Mapping[str, Any]) -> None:
        saved = self.result_repository.save(
            result,
            result_type="cable_loss",
            run_id=self.run_id,
            run_directory=self.run_directory,
        )
        self.run_directory = saved.run_directory


def _service_config(configuration: Any) -> Mapping[str, Any]:
    """Convert the validated configuration model to the service contract."""
    if isinstance(configuration, Mapping):
        return configuration
    plan = configuration.test_plan
    attenuator = plan.attenuator_value
    return {
        "test_frequencies": list(plan.frequencies),
        "attenuator": {"type": f"{attenuator}dB"},
    }
