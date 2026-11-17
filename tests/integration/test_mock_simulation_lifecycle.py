"""阶段五步骤 14：模拟会话的完整生命周期与真机资源门禁。

本模块用真实主窗口、真实配置页、真实 ``MeasurementController`` 和三套仓库 Mock
数据走完整状态链：

```text
加载 Mock 快照 → 连接仪器 → 进入线损页
  → 线损在路径 2 确认点停止（保留已显示数据）→ 重新开始并完成
  → 驱动映射逐点回放完成 → 主功放逐点回放完成
  → 返回欢迎页 → 加载普通配置 → 再次进入 Mock 模拟会话
```

回放时钟被替换为 0 间隔，只跳过真实 0.5 秒的等待，场景、正式结果、worker 和
controller 都是生产实现。最底层的 VISA/端口入口被替换成会立即失败的替身：模拟
链路一旦尝试连接真实仪器或执行正式端口清理，用例就会失败。
"""

import json
import os
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QDialog

from application.dto.measurement_results import MeasurementType
from infrastructure.filesystem.paths import MockDataPathService, PathService
from infrastructure.persistence.configuration_snapshot_repository import (
    FileConfigurationSnapshotRepository,
)
from presentation.qt.application_coordinator import RunFlowState
from presentation.qt.measurement_state import MeasurementStatus
from presentation.qt.mock_replay import MOCK_SIMULATION_NOTICE, MockReplayClock
from tests.integration.test_mock_simulation_flow import (
    _destroy_window,
    _shared_application,
)
from tests.ui.test_gui_import import gui_dependencies_available

PROJECT_ROOT = Path(__file__).parents[2]
MOCK_CONFIG = PROJECT_ROOT / "config.mock.json"
EXPECTED_FREQUENCIES = [round(1.0 + index * 0.1, 1) for index in range(11)]

# 模拟链路不允许触达的真机/正式资源入口。任一处被调用都说明模拟状态落进了正式流程。
FORBIDDEN_CALLS = (
    "app.gui_runtime.connect_instruments",
    "app.gui_runtime.prepare_configuration",
    "app.gui_runtime.create_cable_loss_measurement",
    "app.gui_runtime.create_driver_mapping_measurement",
    "app.gui_runtime.create_amplifier_measurement",
    "instrument.measurement_factory.create_measurement_port",
    "presentation.qt.application_coordinator.close_instrument_port",
    "presentation.qt.application_coordinator.settle_instrument_stage",
)


class _Dialog:
    """固定返回值的接线确认对话框替身；``exec()`` 立即返回，不进入嵌套事件循环。"""

    def __init__(self, result):
        self._result = result

    def exec(self):
        return self._result


def _instant_factories(controller) -> None:
    """把模拟 worker 工厂包一层 0 间隔时钟，其余仍是生产 worker。

    只替换 ``MockReplayClock`` 的等待时间；场景加载、事件顺序、结果读取、停止语义
    和 controller 生命周期都保持生产行为，因此用例不需要等待 181 秒的回放。
    """

    for kind, factory in tuple(controller._simulation_worker_factories.items()):

        def fast_factory(
            command, *, measurement_port=None, _factory=factory
        ):
            worker = _factory(command, measurement_port=measurement_port)
            worker._clock_factory = lambda stop_event: MockReplayClock(
                interval_s=0, stop_event=stop_event
            )
            return worker

        controller._simulation_worker_factories[kind] = fast_factory


@unittest.skipUnless(gui_dependencies_available(), "当前环境缺少完整 GUI 依赖")
class MockSimulationLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = _shared_application()

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.paths = PathService(self.root)
        self.document = json.loads(MOCK_CONFIG.read_text(encoding="utf-8-sig"))
        self.paths.config_file.write_text(
            json.dumps(self.document), encoding="utf-8"
        )
        self.repository = FileConfigurationSnapshotRepository(paths=self.paths)
        self.mock_snapshot = self.repository.save("Mock", self.document)
        self.normal_snapshot = self.repository.save("普通配置", self.document)
        self._cable_dialog_result = QDialog.Accepted

        # 对话框全部静默；接线确认由页面自己的工厂替身返回固定结果。
        for name in ("critical", "warning", "information"):
            patcher = patch(f"presentation.qt.main_window.QMessageBox.{name}")
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch(
            "presentation.qt.main_window.QMessageBox.question",
            return_value=None,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        # 真机与正式端口入口：只要被调用就说明模拟链路越界。
        self.forbidden = ExitStack()
        self.addCleanup(self.forbidden.close)
        for target in FORBIDDEN_CALLS:
            self.forbidden.enter_context(
                patch(
                    target,
                    side_effect=AssertionError(f"模拟链路不得调用 {target}"),
                )
            )

    def _window(self):
        from presentation.qt.main_window import MainWindow

        window = MainWindow(paths=self.paths)
        self.addCleanup(_destroy_window, window)
        # 窗口按临时根目录装配路径服务；Mock 数据仍读仓库跟踪的数据包。
        window.coordinator._mock_paths = MockDataPathService(PROJECT_ROOT)
        _instant_factories(window.measurement_controller)
        # 线损路径 1 的接线确认在模拟模式不出现；这里只控制路径 2 的确认结果。
        window.cable_loss_page._connection_dialog_factory = (
            lambda _kind, _parent: _Dialog(self._cable_dialog_result)
        )
        return window

    def _load_snapshot(self, window, snapshot):
        with patch(
            "presentation.qt.configuration_snapshot_actions."
            "choose_configuration_snapshot",
            return_value=snapshot,
        ):
            window.load_configuration_from_welcome()

    def _pump_until(self, predicate, timeout=30.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.005)
        self.app.processEvents()
        return bool(predicate())

    def _wait_for_status(self, window, status, timeout=30.0):
        controller = window.measurement_controller
        reached = self._pump_until(
            lambda: controller.state.status is status
            and controller.current_worker is None,
            timeout,
        )
        self.assertTrue(
            reached,
            f"测量未到达 {status}；当前状态 {controller.state.status}",
        )

    def _assert_mock_session_has_no_formal_resources(self, window):
        coordinator = window.coordinator
        self.assertIs(coordinator.state, RunFlowState.SIMULATING)
        self.assertIsNone(coordinator.prepared_run)
        self.assertIsNone(coordinator.background_run)
        self.assertIsNone(coordinator.instrument_ctrl)
        self.assertIsNone(coordinator.instrument_worker)
        self.assertIsNone(coordinator._pending_run)
        self.assertFalse(coordinator.has_unreleased_ports())
        self.assertIsNone(window.measurement_controller.current_worker)
        self.assertFalse(coordinator.has_pending_run())

    def test_mock_lifecycle_replays_stops_restarts_and_returns_clean(self):
        window = self._window()
        self._load_snapshot(window, self.mock_snapshot)
        coordinator = window.coordinator
        self.assertTrue(coordinator.is_mock_mode())
        config_before = self.paths.config_file.read_bytes()

        window._finalize_run()
        self.assertTrue(coordinator.simulation_session_active())
        self._assert_mock_session_has_no_formal_resources(window)
        self.assertIs(window.view_stack.currentWidget(), window.workspace_page)
        self.assertIs(
            window.tab_widget.currentWidget(), window.cable_loss_page
        )

        # 模拟态也拒绝正式连接入口，且不会启动连接 worker。
        with patch.object(
            coordinator, "_instrument_worker_factory"
        ) as worker_factory:
            self.assertFalse(coordinator.connect_instruments())
            worker_factory.assert_not_called()

        controller = window.measurement_controller
        self.assertIsNone(controller._measurement_port)

        # 线损：确认点拒绝 → 停止；已显示的路径 1 数据保留，不产生完成/归档。
        self._cable_dialog_result = QDialog.Rejected
        window.cable_loss_page.start_measurement()
        self._wait_for_status(window, MeasurementStatus.CANCELLED)
        self.assertEqual(window.cable_loss_page.cable_loss_table.rowCount(), 11)
        logs = window.log_text.toPlainText()
        self.assertIn("线损模拟回放已停止", logs)
        self.assertNotIn("线损模拟数据已加载", logs)
        self.assertNotIn("自动归档", logs)
        self._assert_mock_session_has_no_formal_resources(window)

        # 线损：重新开始 → 自动确认路径 2 → 完整回放并展示正式结果 DTO。
        self._cable_dialog_result = QDialog.Accepted
        window.cable_loss_page.start_measurement()
        self._wait_for_status(window, MeasurementStatus.COMPLETED)
        self.assertEqual(window.cable_loss_page.cable_loss_table.rowCount(), 11)
        logs = window.log_text.toPlainText()
        self.assertIn("线损模拟数据已加载", logs)
        self.assertIn(MOCK_SIMULATION_NOTICE, logs)
        self.assertNotIn("[归档", logs)
        self._assert_mock_session_has_no_formal_resources(window)

        # 驱动映射：模拟会话固定线损来源，不需要手动选择；逐点曲线与读数更新。
        window.driver_mapping_page.on_activated()
        window.driver_mapping_page.start_measurement()
        self._wait_for_status(window, MeasurementStatus.COMPLETED)
        driver_page = window.driver_mapping_page
        self.assertEqual(driver_page.realtime_buffer.frequencies, EXPECTED_FREQUENCIES)
        self.assertIn("2.0", driver_page.readout.frequency_text())
        logs = window.log_text.toPlainText()
        self.assertIn("驱动映射模拟数据已加载", logs)
        self.assertNotIn("[归档", logs)
        self._assert_mock_session_has_no_formal_resources(window)

        # 主功放：两份固定 Mock 校准输入，逐点功率/电压/电流/效率都到达页面。
        window.amplifier_page.on_activated()
        window.amplifier_page.start_measurement()
        self._wait_for_status(window, MeasurementStatus.COMPLETED)
        amplifier_page = window.amplifier_page
        self.assertEqual(
            amplifier_page.realtime_buffer.frequencies, EXPECTED_FREQUENCIES
        )
        self.assertIn("2.0", amplifier_page.readout.frequency_text())
        logs = window.log_text.toPlainText()
        self.assertIn("主功放模拟数据已加载", logs)
        self.assertNotIn("[归档", logs)
        self._assert_mock_session_has_no_formal_resources(window)

        # 模拟回放从不创建正式运行目录，也不改写 config.json。
        self.assertFalse((self.root / "runs").exists())
        self.assertEqual(self.paths.config_file.read_bytes(), config_before)

        # 返回欢迎页：模拟身份、会话、页面只读态全部清除，端口生命周期未被触碰。
        self.assertIsNone(window._return_to_welcome())
        self.assertIs(window.view_stack.currentWidget(), window.welcome_page)
        self.assertFalse(coordinator.is_mock_mode())
        self.assertIsNone(coordinator.simulation_session)
        self.assertIsNone(coordinator.mock_configuration_name())
        # controller 只保留上一阶段的终态视图，没有活动线程；新测量会重新建立状态。
        self.assertFalse(window.measurement_controller.state.is_active)
        self.assertIsNone(window.measurement_controller.current_worker)
        self.assertEqual(window.mode_badge.text(), "未选择测试模式")

        # 再进入普通配置：没有残留会话、worker、端口或 Mock 输入来源。
        self._load_snapshot(window, self.normal_snapshot)
        self.assertFalse(coordinator.is_mock_mode())
        self.assertIsNone(coordinator.simulation_session)
        self.assertFalse(coordinator.measurement_session_ready())
        self.assertTrue(coordinator.run_status_text().startswith("当前执行: 未连接"))
        window.driver_mapping_page.on_activated()
        window.amplifier_page.on_activated()
        self.assertTrue(window.driver_mapping_page.load_cable_loss_btn.isEnabled())
        self.assertTrue(window.amplifier_page.load_cable_loss_btn.isEnabled())
        self.assertTrue(window.amplifier_page.load_driver_mapping_btn.isEnabled())
        self.assertIn(
            "未选择线损结果",
            window.driver_mapping_page.cable_loss_source_label.text(),
        )

        # 普通配置尚未“连接仪器”：测量入口拒绝，且仍未触达任何真机资源。
        window.cable_loss_page.start_measurement()
        self.assertFalse(window.measurement_controller.state.is_active)

        # 可重复进入模拟会话：再次加载 Mock 并连接，仍不创建运行目录。
        self.assertIsNone(window._return_to_welcome())
        self._load_snapshot(window, self.mock_snapshot)
        window._finalize_run()
        self.assertTrue(coordinator.simulation_session_active())
        self.assertEqual(
            [kind.value for kind in coordinator.simulation_session.measurement_types],
            [
                MeasurementType.CABLE_LOSS.value,
                MeasurementType.DRIVER_POWER_MAPPING.value,
                MeasurementType.AMPLIFIER.value,
            ],
        )
        self.assertFalse((self.root / "runs").exists())
        self.assertEqual(self.paths.config_file.read_bytes(), config_before)

        self.assertIsNone(window._return_to_welcome())
        self.assertFalse(coordinator.is_mock_mode())
        self.assertIsNone(coordinator.simulation_session)


if __name__ == "__main__":
    unittest.main()
