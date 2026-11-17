"""Mock 场景的逐点回放 worker（模拟链路阶段三步骤 8）。

worker 在线程中重新读取并校验场景，按阶段事件顺序发送 ``message`` / ``progress`` /
``data_update``，完成后加载场景指定的正式结果 DTO 并发送 ``result``，再发送独立的
模拟完成载荷。它不调用 ``app.gui_runtime.create_*_measurement``、不创建正式运行、
不打开 VISA，也不写正式结果目录：模拟链路只回放数据包里的既有事实。
"""

from __future__ import annotations

from threading import Event
from typing import Any, Callable

from app.cancellation import CancellationMode, MeasurementCancelled
from application.dto.measurement_results import MeasurementResult, thaw
from application.dto.mock_scenario import (
    MockArchivalCompletion,
    MockDataEvent,
    MockFinishedCompletion,
    MockMessageCompletion,
    MockMessageEvent,
    MockProgressEvent,
    MockResultCompletion,
    MockScenario,
    MockUserConfirmPhaseStart,
)
from application.errors import ConfigurationError
from application.inputs.mock_result import load_mock_result
from application.inputs.mock_scenario import load_mock_scenario

from .mock_replay import MockReplayClock, MockReplayCompletion
from .simulation_session import SimulationStage
from .workers import BaseWorker


class MockReplayWorker(BaseWorker):
    """按阶段事件逐点回放一个 Mock 测量阶段。

    ``stage`` 来自 :class:`presentation.qt.simulation_session.SimulationSession`：它已经
    固定了场景路径、正式结果路径与校准输入。场景与结果仍在线程内重新读取，数据包在
    进入会话后被删除或损坏时会在回放开始前/完成前失败，而不是展示旧缓存。
    """

    def __init__(
        self,
        *,
        stage: SimulationStage,
        clock_factory: Callable[[Event], MockReplayClock] | None = None,
        scenario_loader: Callable[..., MockScenario] | None = None,
        result_loader: Callable[..., MeasurementResult] | None = None,
    ) -> None:
        super().__init__()
        if not isinstance(stage, SimulationStage):
            raise TypeError(
                "模拟回放 worker 必须绑定 SimulationStage，"
                f"收到 {type(stage).__name__}"
            )
        self.stage = stage
        # 模拟链路没有测量端口；保留该属性是为了与正式 worker 的生命周期接口一致。
        self.measurement_port = None
        self._stop_event = Event()
        self._continue_event = Event()
        self._waiting_for_continue = False
        self._continue_requested = False
        self._clock_factory = clock_factory or (
            lambda stop_event: MockReplayClock(stop_event=stop_event)
        )
        self._scenario_loader = scenario_loader or load_mock_scenario
        self._result_loader = result_loader or load_mock_result

    def run(self) -> None:
        try:
            self.emit_message(f"开始模拟回放：{self.stage.title}...")
            self._raise_if_stopped()
            scenario = self._load_scenario()
            completion = self._play(scenario)
            self._raise_if_stopped()
            self.signals.simulation_completed.emit(completion)
            self.signals.finished.emit()
        except Exception as error:  # noqa: BLE001 - 线程边界必须把失败交回页面
            self._failed("模拟回放失败", error)
        finally:
            self._waiting_for_continue = False
            self.measurement_port = None

    def stop(self) -> None:
        """停止回放：同时打断数据点等待与“请连接路径2”确认等待。"""
        super().stop()
        self._stop_event.set()
        self._continue_event.set()

    def continue_stage2_measurement(self) -> None:
        """确认检查点后继续；与正式线损 worker 共用同一页面动作。"""
        if not self._waiting_for_continue or self._continue_requested:
            return
        self._continue_requested = True
        self._continue_event.set()

    # -- 回放 -----------------------------------------------------------------

    def _load_scenario(self) -> MockScenario:
        scenario = self._scenario_loader(
            self.stage.scenario_path,
            expected_measurement_type=self.stage.measurement_type,
        )
        if not isinstance(scenario, MockScenario):
            raise TypeError(
                "场景读取器必须返回 MockScenario，"
                f"收到 {type(scenario).__name__}"
            )
        if scenario.measurement_type is not self.stage.measurement_type:
            raise ConfigurationError(
                "Mock 场景的测量类型与模拟阶段不一致: "
                f"{scenario.measurement_type.value} != "
                f"{self.stage.measurement_type.value}"
            )
        if self.stage.result_path.name != scenario.result_filename:
            raise ConfigurationError(
                "Mock 场景的结果文件与模拟会话不一致: "
                f"{scenario.result_filename} != {self.stage.result_path.name}"
            )
        return scenario

    def _play(self, scenario: MockScenario) -> MockReplayCompletion:
        clock = self._clock_factory(self._stop_event)
        for phase in scenario.phases:
            self._raise_if_stopped()
            if isinstance(phase.start, MockUserConfirmPhaseStart):
                self._wait_for_confirmation(phase.start.prompt)
            for event in phase.events:
                self._raise_if_stopped()
                if isinstance(event, MockDataEvent):
                    if not clock.next_data_point():
                        self._raise_cancelled()
                    # 场景载荷是深冻结的；页面会修改容器，因此交给 Qt 前还原。
                    self.signals.data_update.emit(thaw(event.payload))
                elif isinstance(event, MockMessageEvent):
                    self.emit_message(event.text)
                elif isinstance(event, MockProgressEvent):
                    self.signals.progress.emit(event.percent)
        return self._complete(scenario)

    def _wait_for_confirmation(self, prompt: str) -> None:
        self._waiting_for_continue = True
        self._continue_requested = False
        self._continue_event.clear()
        self.signals.step_pause.emit(prompt)
        self._continue_event.wait()
        self._waiting_for_continue = False
        self._raise_if_stopped()

    def _complete(self, scenario: MockScenario) -> MockReplayCompletion:
        result: MeasurementResult | None = None
        archival_summary = None
        finished = False
        for event in scenario.completion:
            self._raise_if_stopped()
            if isinstance(event, MockResultCompletion):
                result = self._load_result()
                self.signals.result.emit(result)
            elif isinstance(event, MockArchivalCompletion):
                # 模拟链路不发送正式 ArchivalOutcome；摘要只进入模拟完成载荷。
                archival_summary = event.outcome
            elif isinstance(event, MockMessageCompletion):
                self.emit_message(event.text)
            elif isinstance(event, MockFinishedCompletion):
                finished = True
        if result is None:
            raise RuntimeError("Mock 场景完成事件没有提供正式结果")
        if not finished:
            raise RuntimeError("Mock 场景完成事件缺少 finished")
        return MockReplayCompletion(
            measurement_type=self.stage.measurement_type,
            title=scenario.title,
            result_filename=scenario.result_filename,
            result_path=self.stage.result_path,
            archival_summary=archival_summary,
        )

    def _load_result(self) -> MeasurementResult:
        result = self._result_loader(
            self.stage.result_path,
            expected_measurement_type=self.stage.measurement_type,
        )
        if not isinstance(result, MeasurementResult):
            raise TypeError(
                "结果读取器必须返回正式 MeasurementResult，"
                f"收到 {type(result).__name__}"
            )
        return result

    def _raise_if_stopped(self) -> None:
        if self._stop_requested or self._stop_event.is_set():
            self._raise_cancelled()

    @staticmethod
    def _raise_cancelled() -> None:
        raise MeasurementCancelled(CancellationMode.STOP, "用户停止")


__all__ = ["MockReplayWorker"]
