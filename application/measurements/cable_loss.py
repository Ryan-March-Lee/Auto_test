"""Application use case for the two-step cable-loss measurement."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from app.cancellation import CancellationToken, MeasurementCancelled
from application.dto import CableLossMeasurementRequest, MeasurementResult, legacy_result
from application.measurements.services import CableLossService
from domain.run.models import STAGE_CABLE_LOSS


logger = logging.getLogger(__name__)


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
        self.owns_measurement_port = bool(
            request.safety_options.get("owns_measurement_port", False)
        )
        self.run_id = request.run_id
        self.run_directory = request.run_directory
        self.result_repository = request.result_repository
        if self.result_repository is None:
            raise ValueError("CableLossUseCase 必须注入 result_repository")
        # 运行契约运行会注入阶段记录器：结果写入当前运行的 inputs/cable_loss.json
        # 并更新 manifest；旧兼容流程不注入，保持原有全局结果路径行为。
        self.stage_recorder = request.stage_recorder
        # 只有阶段已开始且尚未结束的阶段才允许写入 failed/cancelled。
        self._stage_open = False
        self.sleep_fn = sleep_fn or time.sleep
        self._token = request.cancellation_token or CancellationToken()
        self._service = CableLossService(
            self.config,
            self.inst_ctrl,
            run_id=self.run_id,
            event_sink=request.event_sink,
            cancellation_token=self._token,
            sleep_fn=self.sleep_fn,
            owns_measurement_port=self.owns_measurement_port,
        )
        self._waiting_for_path2 = False
        self._started = False
        self._completed = False
        self._terminal = False
        self._cleaned_after_stop = False
        self._step_pause_callback: Callable[[str], None] | None = None
        self.last_result: MeasurementResult | None = None

    @property
    def path1_losses(self) -> dict[Any, float]:
        """Return the path-1 measurements as an explicit result view."""
        return self._service.path1_losses

    @property
    def cable_losses(self) -> dict[float, dict[str, float]]:
        """Return completed cable-loss values, normalized to float keys."""
        result = self.last_result or MeasurementResult.from_payload(
            self.request.measurement_type, {}, run_id=self.run_id
        )
        return {
            float(key): value for key, value in result.get("cable_losses", {}).items()
        }

    def set_step_pause_callback(self, callback: Callable[[str], None] | None) -> None:
        """Register the presentation callback for the path-2 checkpoint."""
        self._step_pause_callback = callback

    def measure_path_loss(self, frequency: float) -> float:
        return self._service.measure_path_loss(frequency)

    def measure_all_frequencies(self) -> MeasurementResult:
        if self._terminal:
            raise RuntimeError("线损测量已经结束，不能重复执行")
        if self._started:
            raise RuntimeError("线损测量已经开始，不能重复开始")
        self._started = True
        try:
            self._begin_stage()
            payload = self._service.run(path2_confirmed=False)
            result = legacy_result(
                self.request.measurement_type,
                payload,
                run_id=self.run_id,
            )
            self._waiting_for_path2 = result.status.value == "waiting_for_continue"
            self.last_result = result
            if self._waiting_for_path2 and self._step_pause_callback:
                self._step_pause_callback("请连接路径2")
            return result
        except MeasurementCancelled as error:
            self._record_cancel(getattr(error, "reason", None) or "测量已取消")
            self._terminal = True
            raise
        except Exception as error:
            self._record_failure(error)
            self._terminal = True
            raise
        finally:
            if not self._waiting_for_path2:
                self._started = False

    def continue_to_step2(self) -> MeasurementResult:
        if self._terminal:
            raise RuntimeError("线损测量已经结束，不能继续")
        if not self._waiting_for_path2:
            raise RuntimeError("线损测量当前不在等待路径2确认状态")
        try:
            payload = self._service.run(path2_confirmed=True)
            result = legacy_result(
                self.request.measurement_type,
                payload,
                run_id=self.run_id,
            )
            self.last_result = result
            result = self._save(result)
            self.last_result = result
            self._completed = True
            self._terminal = True
            return result
        except MeasurementCancelled as error:
            self._record_cancel(getattr(error, "reason", None) or "测量已取消")
            self._terminal = True
            raise
        except Exception as error:
            self._record_failure(error)
            self._terminal = True
            raise
        finally:
            self._waiting_for_path2 = False
            self._started = False

    def stop_measurement(self) -> None:
        self._token.request_stop(reason="用户停止")
        self._abort_waiting("用户停止")

    def cancel_measurement(self) -> None:
        self._token.request_cancel(reason="任务已取消")
        self._abort_waiting("任务已取消")

    def emergency_stop(self) -> None:
        self._token.request_emergency_stop(reason="紧急停止")
        self._abort_waiting("紧急停止")

    def _abort_waiting(self, reason: str) -> None:
        """在等待路径2确认时停止：清理仪器并把阶段标记为已取消。"""
        if self._waiting_for_path2 and not self._cleaned_after_stop:
            self._service.cleanup()
            self._waiting_for_path2 = False
            self._started = False
            self._terminal = True
            self._cleaned_after_stop = True
            self._record_cancel(reason)

    def _begin_stage(self) -> None:
        """开始当前运行的线损阶段；未注入记录器时保持旧流程行为。"""
        if self.stage_recorder is None:
            return
        self.stage_recorder.start(STAGE_CABLE_LOSS)
        self._stage_open = True

    def _record_complete(self, result: MeasurementResult) -> Any:
        """把完成的线损结果写入当前运行并登记为 ``completed``。"""
        if self.stage_recorder is None:
            return None
        reference = self.stage_recorder.complete(STAGE_CABLE_LOSS, result.to_dict())
        self._stage_open = False
        return reference

    def _record_failure(self, error: Exception) -> None:
        if self.stage_recorder is None or not self._stage_open:
            return
        self._stage_open = False
        self._try_record(
            lambda: self.stage_recorder.fail(STAGE_CABLE_LOSS, str(error) or error.__class__.__name__)
        )

    def _record_cancel(self, reason: str) -> None:
        if self.stage_recorder is None or not self._stage_open:
            return
        self._stage_open = False
        self._try_record(lambda: self.stage_recorder.cancel(STAGE_CABLE_LOSS, reason))

    @staticmethod
    def _try_record(action: Callable[[], Any]) -> None:
        """阶段状态写入失败不应掩盖原始测量结果或异常。"""
        try:
            action()
        except Exception:
            logger.exception("线损阶段状态记录失败")

    def _save(self, result: MeasurementResult) -> MeasurementResult:
        if self.stage_recorder is not None:
            return self._save_to_run_directory(result)
        saved = self.result_repository.save(
            result.to_dict(),
            result_type="cable_loss",
            run_id=self.run_id,
            run_directory=self.run_directory,
        )
        self.run_directory = saved.run_directory
        return result.with_saved_result(
            archive_path=_saved_path(saved, "archive_path"),
            legacy_copy_path=_saved_path(saved, "legacy_copy_path"),
        )

    def _save_to_run_directory(self, result: MeasurementResult) -> MeasurementResult:
        """运行契约运行：权威结果写入 ``inputs/cable_loss.json``。

        运行目录结果是本次运行的权威来源，先原子提交并登记 manifest；全局旧路径
        只作兼容副本，写入失败只记录兼容性告警，不把已完成的阶段重新判定为失败。
        """
        payload = result.to_dict()
        reference = self._record_complete(result)
        compatibility_copy = self._write_compatibility_copy(payload)
        archive_path = Path(self.run_directory) / reference.path
        return result.with_saved_result(
            archive_path=str(archive_path),
            legacy_copy_path=str(compatibility_copy) if compatibility_copy is not None else None,
        )

    def _write_compatibility_copy(self, payload: Mapping[str, Any]) -> Path | None:
        """尽力写入全局兼容副本；失败时保留权威结果并记录告警。"""
        try:
            return self.result_repository.save_compatibility_copy(
                payload,
                result_type="cable_loss",
                run_id=self.run_id,
            )
        except Exception:
            logger.exception("线损全局兼容副本写入失败，运行目录结果仍然有效")
            return None


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


def _saved_path(saved: Any, name: str) -> str | None:
    value = getattr(saved, name, None)
    return str(value) if value is not None else None
