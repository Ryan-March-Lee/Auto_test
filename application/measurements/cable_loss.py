"""Application use case for the two-step cable-loss measurement."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from app.cancellation import CancellationToken, MeasurementCancelled
from application.archival_service import ArchivalOutcome, archive_completed_stage
from application.dto import (
    CableLossMeasurementRequest,
    CableLossMeasurementResult,
    MeasurementResult,
    MeasurementStatus,
    generate_measurement_id,
)
from application.measurements.services import CableLossService
from application.ports.measurement_session import CleanupRecord, SafetyCleanupError
from application.ports.stage_result_repository import MeasurementStage
from application.run_stages import StageResultNotRecorded
from domain.run.models import STAGE_CABLE_LOSS


logger = logging.getLogger(__name__)


class CableLossUseCase:
    """Coordinate cable-loss measurement without GUI or persistence details.

    The first call to :meth:`measure_all_frequencies` measures path 1 and
    returns a waiting result.  :meth:`continue_to_step2` resumes the same
    service, measures path 2, and persists the completed result into the
    independent ``test_results/cable_loss/`` stage directory.

    正式结果写入由注入的阶段结果仓库完成：成功结果先写入 ``cable_loss/``，
    再由阶段记录器把引用登记到当前运行 manifest。运行目录不再保存正式结果的
    第二份副本，manifest 只记录阶段目录文件的路径与校验值；测量失败/取消只登记
    阶段状态，不产生阶段结果文件。阶段文件已提交、运行引用登记失败时保留这份正式
    结果并以 :class:`StageResultNotRecorded` 报告，而不是把它当成失败测量删除。
    部分结果（用户停止）仍按运行契约写在 ``results/`` 下，只供浏览/导出，不会被
    当作正式输入。
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
        self.safety_cleanup = request.safety_cleanup
        if self.safety_cleanup is None:
            raise ValueError("CableLossUseCase 必须由应用组装层注入 safety_cleanup")
        # 阶段结果仓库是正式结果写入边界（步骤 7）：结果写入独立阶段目录，
        # 运行 manifest 只登记引用。
        self.stage_result_repository = request.stage_result_repository
        if self.stage_result_repository is None:
            raise ValueError(
                "CableLossUseCase 必须由应用组装层注入 stage_result_repository"
            )
        # 阶段记录器是请求契约的一部分（D1）：阶段状态与结果引用写入当前运行。
        self.stage_recorder = request.stage_recorder
        # 只有阶段已开始且尚未结束的阶段才允许写入 failed/cancelled。
        self._stage_open = False
        # 阶段成功提交后的自动归档入口；组装层总是注入。
        self.archival_service = request.archival_service
        self.last_archival_outcome: ArchivalOutcome | None = None
        self.sleep_fn = sleep_fn or time.sleep
        self._token = request.cancellation_token or CancellationToken()
        self._service = CableLossService(
            self.config,
            self.inst_ctrl,
            run_id=self.run_id,
            safety_cleanup=self.safety_cleanup,
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
        self._partial_saved = False
        self._step_pause_callback: Callable[[str], None] | None = None
        self.last_result: MeasurementResult | None = None
        self.last_partial_result: MeasurementResult | None = None

    @property
    def path1_losses(self) -> dict[Any, float]:
        """Return the path-1 measurements as an explicit result view."""
        return self._service.path1_losses

    @property
    def last_cleanup_record(self) -> CleanupRecord | None:
        """Return the structured record of the latest safety cleanup."""
        return self._service.last_cleanup_record

    @property
    def cable_losses(self) -> dict[float, dict[str, float]]:
        """Return completed cable-loss values, normalized to float keys."""
        result = self.last_result
        if not isinstance(result, CableLossMeasurementResult):
            return {}
        return {
            float(frequency): dict(losses)
            for frequency, losses in result.cable_losses().items()
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
            result = CableLossMeasurementResult.from_service_payload(
                payload,
                run_id=self.run_id,
            )
            self._waiting_for_path2 = result.status.value == "waiting_for_continue"
            self.last_result = result
            if self._waiting_for_path2 and self._step_pause_callback:
                self._step_pause_callback("请连接路径2")
            return result
        except MeasurementCancelled as error:
            try:
                self._save_partial(error.reason)
            finally:
                # 停止必须是确定的终态：部分结果保存（含意外异常）不得让阶段
                # 停留在 running，也不得改动“本次已停止”的语义。
                self._terminal = True
                self._record_cancel(getattr(error, "reason", None) or "测量已取消")
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
            result = CableLossMeasurementResult.from_service_payload(
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
            try:
                self._save_partial(error.reason)
            finally:
                # 停止必须是确定的终态：部分结果保存（含意外异常）不得让阶段
                # 停留在 running，也不得改动“本次已停止”的语义。
                self._terminal = True
                self._record_cancel(getattr(error, "reason", None) or "测量已取消")
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

    def _abort_waiting(self, reason: str) -> None:
        """在等待路径2确认时停止：清理仪器并把阶段标记为已取消。

        清理失败是明确的运行终态：阶段记录为失败并抛出携带结构化记录的
        ``SafetyCleanupError``，不降级为日志。
        """
        if self._waiting_for_path2 and not self._cleaned_after_stop:
            self._waiting_for_path2 = False
            self._started = False
            self._terminal = True
            self._cleaned_after_stop = True
            try:
                self._service.cleanup()
            except SafetyCleanupError as error:
                self._record_failure(error)
                raise
            try:
                self._save_partial(reason)
            finally:
                # 即使部分结果保存失败，等待路径2时的停止也必须落到 cancelled。
                self._record_cancel(reason)

    def _save_partial(self, reason: str) -> None:
        """保存停止前已经完整测完的频点。

        可预期的持久化失败（I/O、数据契约）只记录日志，停止照常成立；其余异常原样
        上抛，由调用点的 ``finally`` 保证阶段已经落到 ``cancelled``。
        """
        if self._partial_saved or not self._stage_open:
            return
        try:
            payload = self._service.partial_payload()
            result = CableLossMeasurementResult.from_service_payload(
                payload,
                run_id=self.run_id,
                status=MeasurementStatus.CANCELLED,
                termination={"intent": "stop", "reason": reason or "用户停止"},
            )
            if not result.points:
                logger.info("停止时没有完整的线损频点，未保存部分结果")
                return
            reference = self.stage_recorder.save_partial(
                STAGE_CABLE_LOSS,
                result.to_dict(),
                description="用户停止时保存的部分结果",
            )
            self.last_partial_result = result.with_saved_result(
                archive_path=str(Path(self.run_directory) / reference.path)
            )
            self._partial_saved = True
        except (OSError, TypeError, ValueError):
            logger.exception("保存线损部分结果失败")

    def _begin_stage(self) -> None:
        """开始当前运行的线损阶段。"""
        self.stage_recorder.start(STAGE_CABLE_LOSS)
        self._stage_open = True

    def _record_complete(self, reference: Any) -> Any:
        """把阶段结果引用登记到当前运行 manifest。"""
        run_reference = self.stage_recorder.complete_with_stage_result(
            STAGE_CABLE_LOSS, reference
        )
        self._stage_open = False
        return run_reference

    def _record_failure(self, error: Exception) -> None:
        if not self._stage_open:
            return
        self._stage_open = False
        self._try_record(
            lambda: self.stage_recorder.fail(STAGE_CABLE_LOSS, str(error) or error.__class__.__name__)
        )

    def _record_cancel(self, reason: str) -> None:
        if not self._stage_open:
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
        """正式完成保存：结果写入 ``cable_loss/``，运行 manifest 只登记引用。

        顺序固定为“构造带身份的正式 DTO → 原子写入阶段目录 → 登记运行引用”，
        因此阶段目录文件是正式结果的权威来源，运行目录不再复制同一份结果。阶段
        文件保存与引用登记是两个提交边界：阶段文件已提交后登记失败，不删除这份
        正式结果，而是以 :class:`StageResultNotRecorded` 明确报告“结果已保存、
        运行引用登记失败”，错误说明里给出阶段文件位置。阶段结果提交后立即触发
        自动归档：线损没有 CSV/报告交付产物，因此按契约登记为 ``not_applicable``。
        """
        formal = self._formal_result(result)
        stage_reference = self.stage_result_repository.save(
            MeasurementStage.CABLE_LOSS, formal
        )
        try:
            self._record_complete(stage_reference)
        except Exception as error:
            # 已提交的正式结果不能因为运行记账失败而被删除或隐藏：删除本身也可能
            # 失败，而且会丢掉一次已完成的测量。调用方据异常消息提示结果位置。
            raise StageResultNotRecorded(
                STAGE_CABLE_LOSS,
                stage_reference,
                str(error) or error.__class__.__name__,
            ) from error
        self.last_archival_outcome = archive_completed_stage(
            self.archival_service,
            run_id=self.run_id,
            stage=STAGE_CABLE_LOSS,
        )
        return formal.with_saved_result(archive_path=str(stage_reference.path))

    @staticmethod
    def _formal_result(result: MeasurementResult) -> CableLossMeasurementResult:
        """为新正式结果分配自身身份；已带身份的结果保持不变。"""
        if not isinstance(result, CableLossMeasurementResult):
            raise ValueError("CableLossUseCase 只能保存 CableLossMeasurementResult")
        if result.measurement_id is not None:
            return result
        return replace(result, measurement_id=generate_measurement_id())


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
