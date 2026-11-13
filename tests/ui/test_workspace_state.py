"""阶段运行状态模型的契约（重构计划步骤 8）。

这里不构造 Qt 控件：``WorkspaceRunState`` 是纯 Python，只把
``ControllerState`` 的 ``kind`` / ``status`` 折叠成阶段条的 ``StageState``。真实窗口
的信号接线在 ``tests/integration/test_main_window_flow.py``。

锁定的行为：

- 只登记三个测量阶段，``visualization`` 与 ``test_configuration`` 不参与状态；
- ``PREPARING`` / ``RUNNING`` / ``WAITING_FOR_CONTINUE`` / ``STOPPING`` 都显示进行中；
- 完成、失败、取消/急停分别落到完成、失败、已停止；
- 终态在 run 重置前保持不变，只有同一阶段重新开始才回到进行中；
- 换 run（或回到欢迎页）时全部回到未开始，并记录新的 run 标识。
"""

import unittest

from presentation.qt.measurement_controller_contract import ControllerState
from presentation.qt.measurement_stage import (
    MEASUREMENT_STAGES,
    StageState,
    stage_for_key,
)
from presentation.qt.measurement_state import (
    MeasurementKind,
    MeasurementResultReference,
    MeasurementStatus,
    MeasurementViewState,
)
from presentation.qt.workspace_state import WorkspaceRunState


def _view_state(kind, status):
    """按 controller 会产生的状态构造一个自洽的 ``MeasurementViewState``。"""
    base = MeasurementViewState(kind)
    if status is MeasurementStatus.PREPARING:
        return base.prepare()
    running = base.prepare().run()
    if status is MeasurementStatus.RUNNING:
        return running
    if status is MeasurementStatus.WAITING_FOR_CONTINUE:
        return running.wait_for_continue("等待继续")
    if status is MeasurementStatus.STOPPING:
        return running.stop("用户停止", stopping=True)
    if status is MeasurementStatus.COMPLETED:
        return running.finish(
            MeasurementResultReference("result-id", kind, {"value": 1})
        )
    if status is MeasurementStatus.FAILED:
        return running.fail("测量失败")
    if status is MeasurementStatus.CANCELLED:
        return running.stop("用户停止")
    if status is MeasurementStatus.EMERGENCY_STOPPED:
        return MeasurementViewState(
            kind,
            status=MeasurementStatus.EMERGENCY_STOPPED,
            stop_reason="急停",
        )
    raise AssertionError(f"未覆盖的状态: {status}")


def _state(kind, status):
    return ControllerState(kind, status, _view_state(kind, status))


class WorkspaceRunStateTests(unittest.TestCase):
    def setUp(self):
        self.run_state = WorkspaceRunState()

    # ---- 阶段集合 -----------------------------------------------------------

    def test_tracks_only_the_measurement_stages_in_order(self):
        self.assertEqual(
            self.run_state.stage_keys(),
            ("cable_loss", "driver_mapping", "amplifier"),
            "可视化是只读视图，测试配置不是阶段，两者都不进入运行状态",
        )
        for key in self.run_state.stage_keys():
            with self.subTest(key=key):
                self.assertIs(self.run_state.state_for(key), StageState.PENDING)

    def test_read_only_and_utility_stages_are_not_tracked(self):
        for key in ("visualization", "test_configuration", "not_a_stage"):
            with self.subTest(key=key):
                with self.assertRaises(KeyError):
                    self.run_state.state_for(key)

    def test_stage_lookup_rejects_non_strings(self):
        for value in (None, 1, object()):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    self.run_state.state_for(value)

    def test_snapshot_is_an_immutable_copy(self):
        snapshot = self.run_state.snapshot()

        self.assertEqual(
            dict(snapshot),
            {
                "cable_loss": StageState.PENDING,
                "driver_mapping": StageState.PENDING,
                "amplifier": StageState.PENDING,
            },
        )
        with self.assertRaises(TypeError):
            snapshot["cable_loss"] = StageState.COMPLETED

        self.run_state.observe(
            _state(MeasurementKind.CABLE_LOSS, MeasurementStatus.RUNNING)
        )
        self.assertIs(
            snapshot["cable_loss"],
            StageState.PENDING,
            "快照是复制，不是实时视图",
        )

    def test_stage_set_is_validated(self):
        with self.assertRaises(TypeError):
            WorkspaceRunState((object(),))
        with self.assertRaises(ValueError):
            WorkspaceRunState((MEASUREMENT_STAGES[0], MEASUREMENT_STAGES[0]))

    # ---- 状态映射 -----------------------------------------------------------

    def test_in_flight_statuses_map_to_active(self):
        for status in (
            MeasurementStatus.PREPARING,
            MeasurementStatus.RUNNING,
            MeasurementStatus.WAITING_FOR_CONTINUE,
            MeasurementStatus.STOPPING,
        ):
            with self.subTest(status=status):
                run_state = WorkspaceRunState()
                changes = run_state.observe(
                    _state(MeasurementKind.CABLE_LOSS, status)
                )
                self.assertEqual(dict(changes), {"cable_loss": StageState.ACTIVE})
                self.assertIs(
                    run_state.state_for("cable_loss"), StageState.ACTIVE
                )

    def test_terminal_statuses_map_to_completed_failed_and_stopped(self):
        cases = (
            (MeasurementStatus.COMPLETED, StageState.COMPLETED),
            (MeasurementStatus.FAILED, StageState.FAILED),
            (MeasurementStatus.CANCELLED, StageState.STOPPED),
            (MeasurementStatus.EMERGENCY_STOPPED, StageState.STOPPED),
        )
        for status, expected in cases:
            with self.subTest(status=status):
                run_state = WorkspaceRunState()
                run_state.observe(
                    _state(MeasurementKind.CABLE_LOSS, status)
                )
                self.assertIs(run_state.state_for("cable_loss"), expected)

    def test_measurement_kinds_map_to_their_own_stage(self):
        for kind in MeasurementKind:
            with self.subTest(kind=kind):
                run_state = WorkspaceRunState()
                changes = run_state.observe(
                    _state(kind, MeasurementStatus.RUNNING)
                )
                self.assertEqual(dict(changes), {kind.value: StageState.ACTIVE})
                self.assertEqual(
                    dict(run_state.snapshot()),
                    {
                        key: (
                            StageState.ACTIVE
                            if key == kind.value
                            else StageState.PENDING
                        )
                        for key in run_state.stage_keys()
                    },
                )

    def test_idle_state_changes_nothing(self):
        self.assertEqual(dict(self.run_state.observe(ControllerState())), {})

    def test_observe_rejects_non_controller_states(self):
        for value in (None, object(), SimpleState()):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    self.run_state.observe(value)

    # ---- 终态保留与 run 重置 -------------------------------------------------

    def test_completed_stage_is_kept_when_the_next_stage_starts(self):
        self.run_state.observe(
            _state(MeasurementKind.CABLE_LOSS, MeasurementStatus.COMPLETED)
        )

        self.assertEqual(
            dict(
                self.run_state.observe(
                    _state(MeasurementKind.AMPLIFIER, MeasurementStatus.RUNNING)
                )
            ),
            {"amplifier": StageState.ACTIVE},
        )

        self.assertIs(
            self.run_state.state_for("cable_loss"),
            StageState.COMPLETED,
            "controller 已经开始下一阶段时，上一阶段的完成态必须留在模型里",
        )
        self.assertIs(self.run_state.state_for("amplifier"), StageState.ACTIVE)

    def test_terminal_state_is_not_overwritten_by_another_terminal(self):
        self.run_state.observe(
            _state(MeasurementKind.CABLE_LOSS, MeasurementStatus.COMPLETED)
        )

        changes = self.run_state.observe(
            _state(MeasurementKind.CABLE_LOSS, MeasurementStatus.FAILED)
        )

        self.assertEqual(dict(changes), {})
        self.assertIs(
            self.run_state.state_for("cable_loss"),
            StageState.COMPLETED,
            "终态保留到 run 重置，迟到的另一个终态不能改写它",
        )

    def test_restarting_a_completed_stage_returns_it_to_active(self):
        self.run_state.observe(
            _state(MeasurementKind.CABLE_LOSS, MeasurementStatus.COMPLETED)
        )

        changes = self.run_state.observe(
            _state(MeasurementKind.CABLE_LOSS, MeasurementStatus.PREPARING)
        )

        self.assertEqual(dict(changes), {"cable_loss": StageState.ACTIVE})
        self.assertIs(
            self.run_state.state_for("cable_loss"), StageState.ACTIVE
        )

    def test_repeated_identical_state_produces_no_change(self):
        running = _state(MeasurementKind.CABLE_LOSS, MeasurementStatus.RUNNING)
        self.run_state.observe(running)

        self.assertEqual(dict(self.run_state.observe(running)), {})

    def test_reset_run_clears_terminal_states_and_records_the_new_run(self):
        self.run_state.observe(
            _state(MeasurementKind.CABLE_LOSS, MeasurementStatus.COMPLETED)
        )
        self.run_state.observe(
            _state(MeasurementKind.AMPLIFIER, MeasurementStatus.FAILED)
        )

        changes = self.run_state.reset_run("run-2")

        self.assertEqual(
            dict(changes),
            {"cable_loss": StageState.PENDING, "amplifier": StageState.PENDING},
            "只返回真正变化的状态，阶段条不必为重写 PENDING 付代价",
        )
        self.assertEqual(self.run_state.run_id(), "run-2")
        self.assertEqual(
            dict(self.run_state.snapshot()),
            {
                "cable_loss": StageState.PENDING,
                "driver_mapping": StageState.PENDING,
                "amplifier": StageState.PENDING,
            },
        )

    def test_reset_run_without_changes_returns_nothing(self):
        self.assertEqual(
            dict(self.run_state.reset_run("run-1")), {}
        )
        self.assertEqual(self.run_state.run_id(), "run-1")

    def test_reset_run_to_none_clears_the_run_identity(self):
        self.run_state.reset_run("run-1")
        self.run_state.observe(
            _state(MeasurementKind.CABLE_LOSS, MeasurementStatus.CANCELLED)
        )

        changes = self.run_state.reset_run(None)

        self.assertEqual(dict(changes), {"cable_loss": StageState.PENDING})
        self.assertIsNone(self.run_state.run_id())

    def test_run_identity_is_normalized_and_validated(self):
        self.assertEqual(self.run_state.reset_run("   "), {})
        self.assertIsNone(self.run_state.run_id())

        for value in (1, object()):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    self.run_state.reset_run(value)

    def test_custom_stage_sets_ignore_stages_they_do_not_track(self):
        run_state = WorkspaceRunState((stage_for_key("amplifier"),))

        self.assertEqual(
            dict(
                run_state.observe(
                    _state(MeasurementKind.CABLE_LOSS, MeasurementStatus.RUNNING)
                )
            ),
            {},
            "自定义阶段集合不记录未登记阶段，但不能抛异常打断信号分发",
        )
        self.assertEqual(
            dict(
                run_state.observe(
                    _state(MeasurementKind.AMPLIFIER, MeasurementStatus.RUNNING)
                )
            ),
            {"amplifier": StageState.ACTIVE},
        )


class SimpleState:
    """有 ``kind`` / ``status`` 但不是 ``ControllerState`` 的对象。"""

    kind = MeasurementKind.CABLE_LOSS
    status = MeasurementStatus.RUNNING


if __name__ == "__main__":
    unittest.main()
