"""阶段四：协调器仿真运行状态机（重构计划步骤 18～20）。

UI 层用例只使用表现层与 application DTO/端口替身：真实运行装配在
``tests/test_simulation_runtime.py`` 与 ``tests/integration`` 覆盖。这里验证：

- 进入仿真运行后一次性提交 ``prepared_run`` 与仿真上下文，状态为 ``CONNECTED``；
- 装配失败不留下半提交状态；
- 端口丢失后的“重连”只重建端口，不创建第二个运行、不走正式仪器连接；
- 返回欢迎页统一关闭端口并清除仿真身份；
- 公开的 ``connect_instruments`` 拒绝为仿真运行打开真机端口。
"""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from application.dto.measurement_results import MeasurementType
from application.errors import ConfigurationError
from application.simulation_run import SimulationMarker, SimulationReadingsDigest
from presentation.qt import application_coordinator as coordinator_module
from presentation.qt.application_coordinator import ApplicationCoordinator, RunFlowState
from presentation.qt.simulated_run import SimulatedRunContext

PROJECT_ROOT = Path(__file__).parents[2]
MOCK_CONFIG_PATH = PROJECT_ROOT / "config.mock.json"


class _Page:
    def __init__(self):
        self.config = {}
        self.statuses = []
        self.read_only_reasons = []
        self.simulation_modes = []

    def validate(self):
        return True

    def build_config(self, base=None):
        return dict(base or {})

    def apply_config(self, config):
        self.config = dict(config)

    def set_run_status(self, text):
        self.statuses.append(text)

    def set_simulation_mode(self, active, reason=None):
        self.simulation_modes.append((bool(active), reason))

    def set_read_only(self, reason):
        self.read_only_reasons.append(reason)

    def driver_mode_requested(self):
        return False


class _RaisingPage(_Page):
    """在指定的页面回调上失败，用来证明提交失败会整体回滚。

    ``armed`` 让页面在绑定阶段正常工作，只在进入仿真运行的提交段开始失败。
    """

    def __init__(self, fail_on):
        super().__init__()
        self.fail_on = fail_on
        self.armed = False

    def set_run_status(self, text):
        if self.fail_on == "status" and self.armed:
            raise RuntimeError("状态栏不可用")
        super().set_run_status(text)

    def set_simulation_mode(self, active, reason=None):
        if self.fail_on == "simulation" and self.armed:
            raise RuntimeError("仿真徽标不可用")
        super().set_simulation_mode(active, reason)

    def set_read_only(self, reason):
        if self.fail_on == "read_only" and self.armed:
            raise RuntimeError("只读样式不可用")
        super().set_read_only(reason)


class _Signal:
    def connect(self, slot):
        self.slot = slot


class _Signals:
    def __init__(self):
        self.stage_start_failed = _Signal()


class _Controller:
    def __init__(self):
        self.ports = []
        self.state = SimpleNamespace(is_active=False)
        self.signals = _Signals()

    def set_measurement_port(self, port):
        self.ports.append(port)


class _Port:
    def __init__(self):
        self.close_calls = 0

    def close_all(self, *, close_rf=False):
        self.close_calls += 1
        return None

    def end_stage(self, *, timeout_s=30.0):
        return None


class _MockPaths:
    def require_scenario_path_for(self, measurement_type):
        return Path("mock_data") / measurement_type / "scenario.json"


def _marker(run_id="run-simulated"):
    return SimulationMarker(
        schema_version=1,
        run_id=run_id,
        created_at="2026-11-18T00:00:00Z",
        time_scale=50,
        snapshot_name="Mock",
        readings=(
            SimulationReadingsDigest(
                measurement_type=MeasurementType.CABLE_LOSS,
                readings_path="mock_data/cable_loss/readings.json",
                readings_sha256="a" * 64,
                readings_size_bytes=10,
                scenario_path="mock_data/cable_loss/scenario.json",
                scenario_sha256="b" * 64,
            ),
        ),
    )


def _context(port=None):
    port = port or _Port()
    return SimulatedRunContext(
        prepared_run=SimpleNamespace(
            run_id="run-simulated",
            run_directory=Path("temp/simulation_runs/runs/run-simulated"),
        ),
        readings={MeasurementType.CABLE_LOSS: object()},
        port=port,
        paths=object(),
        results=object(),
        marker=_marker(),
        time_scale=50,
        port_factory=lambda readings: _Port(),
    )


def _assembly(context):
    return SimpleNamespace(
        prepared_run=context.prepared_run,
        readings=context.readings,
        paths=context.paths,
        results=context.results,
        marker=context.marker,
        time_scale=context.time_scale,
        config_draft=Path("temp/simulation_runs/configs/draft.json"),
        run_repository=object(),
    )


class _DiscardOutcome:
    """``discard_simulated_run`` 的轻量替身：UI 测试不导入 app 装配层。"""

    def __init__(self, *, ok=True, run_removed=True, draft_removed=True):
        self.ok = ok
        self.run_removed = run_removed
        self.draft_removed = draft_removed
        self.errors = () if ok else (OSError("仿真根目录被占用"),)

    def describe(self):
        if self.ok:
            return "仿真运行 run-simulated 已撤销"
        residual = "、".join(
            name
            for name, removed in (
                ("运行目录", self.run_removed),
                ("输入草稿", self.draft_removed),
            )
            if not removed
        )
        return f"仿真运行 run-simulated 撤销未完成（残留: {residual}）"


def _discard_outcome(*, ok=True, run_removed=True, draft_removed=True):
    return _DiscardOutcome(
        ok=ok, run_removed=run_removed, draft_removed=draft_removed
    )


def _coordinator(*, page=None):
    page = page or _Page()
    controller = _Controller()
    messages = []
    warnings = []
    coordinator = ApplicationCoordinator(
        log=messages.append,
        warn=lambda title, message: warnings.append((title, message)),
        error=messages.append,
        gui_data_service=MagicMock(),
        controller=controller,
        config_path=Path("config.json"),
        mock_paths=_MockPaths(),
    )
    coordinator.bind_test_config_page(page)
    coordinator._mock_snapshot_name = "Mock"
    coordinator.config = json.loads(MOCK_CONFIG_PATH.read_text(encoding="utf-8"))
    coordinator.messages = messages
    coordinator.warnings = warnings
    coordinator.page = page
    return coordinator


def _enter(coordinator, *, context=None, assembly=None):
    context = context or _context()
    assembly = assembly or _assembly(context)
    match = SimpleNamespace(snapshot_name="Mock")
    with patch.object(
        coordinator_module, "prepare_simulated_run", return_value=assembly
    ) as prepare, patch.object(
        coordinator_module, "load_mock_scenario", return_value=object()
    ), patch.object(
        coordinator_module, "MockConfigurationMatcher"
    ) as matcher, patch.object(
        SimulatedRunContext, "from_assembly", return_value=context
    ):
        matcher.return_value.match.return_value = match
        entered = coordinator._enter_simulated_run()
    return entered, prepare, context


class SimulatedRunEntryTests(unittest.TestCase):
    def test_entry_commits_run_and_context_atomically(self):
        coordinator = _coordinator()
        entered, prepare, context = _enter(coordinator)

        self.assertTrue(entered)
        self.assertIs(coordinator.state, RunFlowState.CONNECTED)
        self.assertIs(coordinator.prepared_run, context.prepared_run)
        self.assertIs(coordinator.simulated_run, context)
        self.assertIs(coordinator.instrument_ctrl, context.port)
        self.assertIn(context.port, coordinator.controller.ports)
        self.assertTrue(coordinator.measurement_session_ready())
        self.assertIn("仿真运行", coordinator.run_status_text())
        self.assertIn("不连接真实仪器", coordinator.run_status_text())
        self.assertIn("仿真运行", coordinator.measurement_entry_message())
        self.assertIs(coordinator.current_result_paths(), context.paths)
        self.assertIs(coordinator.current_result_query(), context.results)
        prepare.assert_called_once()

    def test_entry_failure_leaves_state_and_run_unchanged(self):
        coordinator = _coordinator()
        before = coordinator.state
        with patch.object(
            coordinator_module,
            "prepare_simulated_run",
            side_effect=ConfigurationError("读数损坏"),
        ), patch.object(
            coordinator_module, "load_mock_scenario", return_value=object()
        ), patch.object(
            coordinator_module, "MockConfigurationMatcher"
        ) as matcher:
            matcher.return_value.match.return_value = SimpleNamespace(
                snapshot_name="Mock"
            )
            entered = coordinator._enter_simulated_run()

        self.assertFalse(entered)
        self.assertIs(coordinator.state, before)
        self.assertIsNone(coordinator.prepared_run)
        self.assertIsNone(coordinator.simulated_run)
        self.assertIsNone(coordinator.instrument_ctrl)
        self.assertTrue(any("读数损坏" in message for _, message in coordinator.warnings))

    def test_second_entry_is_refused_without_creating_another_run(self):
        coordinator = _coordinator()
        self.assertTrue(_enter(coordinator)[0])
        with patch.object(coordinator_module, "prepare_simulated_run") as prepare:
            self.assertFalse(coordinator._enter_simulated_run())
        prepare.assert_not_called()


class SimulatedRunCommitRollbackTests(unittest.TestCase):
    """提交段（端口接管之后）失败必须回滚，不能留下半提交运行。"""

    def _enter_with_failing_page(self, page, *, discard):
        coordinator = _coordinator(page=page)
        page.armed = True
        context = _context()
        assembly = _assembly(context)
        with patch.object(
            coordinator_module, "prepare_simulated_run", return_value=assembly
        ), patch.object(
            coordinator_module, "load_mock_scenario", return_value=object()
        ), patch.object(
            coordinator_module, "MockConfigurationMatcher"
        ) as matcher, patch.object(
            SimulatedRunContext, "from_assembly", return_value=context
        ), patch.object(
            coordinator_module, "discard_simulated_run", return_value=discard
        ) as discard_mock:
            matcher.return_value.match.return_value = SimpleNamespace(
                snapshot_name="Mock"
            )
            entered = coordinator._enter_simulated_run()
        return coordinator, context, entered, discard_mock

    def test_publish_failure_restores_identity_and_closes_the_port(self):
        page = _RaisingPage("read_only")
        coordinator, context, entered, discard = self._enter_with_failing_page(
            page, discard=_discard_outcome()
        )

        self.assertFalse(entered)
        self.assertIsNone(coordinator.prepared_run)
        self.assertIsNone(coordinator.background_run)
        self.assertIsNone(coordinator.simulated_run)
        self.assertIsNone(coordinator.instrument_ctrl)
        self.assertIs(coordinator.state, RunFlowState.DEVICE_SELECTION)
        self.assertEqual(
            coordinator.config,
            json.loads(MOCK_CONFIG_PATH.read_text(encoding="utf-8")),
        )
        self.assertEqual(page.config, {})
        self.assertEqual(context.port.close_calls, 1)
        discard.assert_called_once()
        self.assertTrue(
            any("已恢复" in message for _, message in coordinator.warnings),
            coordinator.warnings,
        )
        self.assertFalse(coordinator.has_unreleased_ports())

    def test_publish_failure_that_leaves_residue_requires_manual_cleanup(self):
        page = _RaisingPage("read_only")
        coordinator, context, entered, _discard = self._enter_with_failing_page(
            page,
            discard=_discard_outcome(ok=False, run_removed=False, draft_removed=False),
        )

        self.assertFalse(entered)
        self.assertIsNone(coordinator.simulated_run)
        self.assertIsNone(coordinator.instrument_ctrl)
        self.assertIs(coordinator.state, RunFlowState.MANUAL_CLEANUP_REQUIRED)
        self.assertEqual(context.port.close_calls, 1)

    def test_status_callback_failure_does_not_escape_the_rollback(self):
        page = _RaisingPage("status")
        coordinator, context, entered, _discard = self._enter_with_failing_page(
            page, discard=_discard_outcome()
        )

        self.assertFalse(entered)
        self.assertIsNone(coordinator.simulated_run)
        self.assertIsNone(coordinator.instrument_ctrl)
        self.assertIs(coordinator.state, RunFlowState.DEVICE_SELECTION)
        self.assertEqual(context.port.close_calls, 1)

    def test_port_handoff_failure_with_residue_requires_manual_cleanup(self):
        coordinator = _coordinator()
        context = _context()
        assembly = _assembly(context)
        with patch.object(
            coordinator_module, "prepare_simulated_run", return_value=assembly
        ), patch.object(
            coordinator_module, "load_mock_scenario", return_value=object()
        ), patch.object(
            coordinator_module, "MockConfigurationMatcher"
        ) as matcher, patch.object(
            SimulatedRunContext, "from_assembly", return_value=context
        ), patch.object(
            coordinator_module, "discard_simulated_run",
            return_value=_discard_outcome(ok=False, run_removed=False),
        ), patch.object(
            coordinator, "accept_instrument_controller", return_value=False
        ):
            matcher.return_value.match.return_value = SimpleNamespace(
                snapshot_name="Mock"
            )
            entered = coordinator._enter_simulated_run()

        self.assertFalse(entered)
        self.assertIs(coordinator.state, RunFlowState.MANUAL_CLEANUP_REQUIRED)


class SimulatedRunReconnectTests(unittest.TestCase):
    def test_reconnect_rebuilds_only_the_port(self):
        coordinator = _coordinator()
        entered, prepare, _context_value = _enter(coordinator)
        self.assertTrue(entered)
        prepare.reset_mock()

        old_port = coordinator.instrument_ctrl
        self.assertTrue(coordinator.release_instrument_port())
        coordinator._set_state(RunFlowState.RECONNECT_REQUIRED)

        with patch.object(coordinator_module, "prepare_simulated_run") as prepare_again:
            self.assertTrue(coordinator.finalize_configuration())

        prepare_again.assert_not_called()
        self.assertIsNot(coordinator.instrument_ctrl, old_port)
        self.assertIs(coordinator.simulated_run.port, coordinator.instrument_ctrl)
        self.assertEqual(
            coordinator.simulated_run.run_id, coordinator.prepared_run.run_id
        )
        self.assertIs(coordinator.state, RunFlowState.CONNECTED)

    def test_public_connect_instruments_refuses_a_simulated_run(self):
        coordinator = _coordinator()
        self.assertTrue(_enter(coordinator)[0])
        with patch.object(coordinator, "_start_connection_worker") as worker:
            self.assertFalse(coordinator.connect_instruments())
        worker.assert_not_called()
        self.assertTrue(any("仿真运行" in message for message in coordinator.messages))

    def test_reopening_a_run_is_refused_while_simulated(self):
        coordinator = _coordinator()
        self.assertTrue(_enter(coordinator)[0])
        self.assertIsNone(coordinator.load_prepared_run("another-run"))
        self.assertTrue(any("仿真运行" in message for message in coordinator.messages))


class SimulatedRunAbandonTests(unittest.TestCase):
    def test_return_to_welcome_closes_the_port_and_clears_the_identity(self):
        coordinator = _coordinator()
        entered, _prepare, context = _enter(coordinator)
        self.assertTrue(entered)

        self.assertTrue(coordinator.abandon_current_session())

        self.assertEqual(context.port.close_calls, 1)
        self.assertIsNone(coordinator.prepared_run)
        self.assertIsNone(coordinator.simulated_run)
        self.assertIsNone(coordinator._mock_snapshot_name)
        self.assertIsNone(coordinator.instrument_ctrl)
        self.assertIs(coordinator.state, RunFlowState.DEVICE_SELECTION)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
