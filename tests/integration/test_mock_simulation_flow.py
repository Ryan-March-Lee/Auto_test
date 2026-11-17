"""阶段二集成：Mock 快照 → “连接仪器” → 模拟会话 → 返回欢迎页。

真实窗口、真实配置页、真实 Mock 路径服务；只把可能打开硬件的入口替换成会立即
失败的替身，断言模拟链路从未创建正式运行目录、从未启动连接 worker、从未打开 VISA。
"""

import gc
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from infrastructure.filesystem.paths import MockDataPathService, PathService
from infrastructure.persistence.configuration_snapshot_repository import (
    FileConfigurationSnapshotRepository,
)
from presentation.qt.application_coordinator import RunFlowState
from tests.ui.test_gui_import import gui_dependencies_available

PROJECT_ROOT = Path(__file__).parents[2]
MOCK_CONFIG = PROJECT_ROOT / "config.mock.json"

_APPLICATION = None


def _shared_application():
    global _APPLICATION
    if _APPLICATION is not None:
        return _APPLICATION
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        from presentation.qt.main_window import build_application

        app = build_application([])
    _APPLICATION = app
    return app


def _destroy_window(window) -> None:
    from PySide6.QtCore import QCoreApplication, QEvent

    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


@unittest.skipUnless(gui_dependencies_available(), "当前环境缺少完整 GUI 依赖")
class MockSimulationWindowTests(unittest.TestCase):
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

    def _window(self):
        from presentation.qt.main_window import MainWindow

        window = MainWindow(paths=self.paths)
        self.addCleanup(_destroy_window, window)
        # 窗口按临时根目录装配 Mock 路径服务；Mock 数据仍读仓库跟踪的数据包。
        window.coordinator._mock_paths = MockDataPathService(PROJECT_ROOT)
        return window

    def _load_snapshot(self, window, snapshot):
        with patch(
            "presentation.qt.configuration_snapshot_actions."
            "choose_configuration_snapshot",
            return_value=snapshot,
        ):
            window.load_configuration_from_welcome()

    def _silence_dialogs(self):
        from PySide6.QtWidgets import QMessageBox

        for name in ("critical", "warning", "information"):
            patcher = patch(f"presentation.qt.main_window.QMessageBox.{name}")
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch(
            "presentation.qt.main_window.QMessageBox.question",
            return_value=QMessageBox.Yes,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_mock_snapshot_connects_into_a_simulation_session_without_a_run(self):
        window = self._window()
        self._silence_dialogs()

        self._load_snapshot(window, self.mock_snapshot)

        coordinator = window.coordinator
        self.assertTrue(coordinator.is_mock_mode())
        self.assertEqual(coordinator.mock_configuration_name(), "Mock")
        self.assertIsNone(coordinator.prepared_run)
        self.assertIs(window.view_stack.currentWidget(), window.workspace_page)
        self.assertEqual(window.mode_badge.text(), "Mock 模拟模式")
        self.assertIn("Mock", coordinator.run_status_text())
        self.assertTrue(window.test_config_page.simulation_notice.isVisibleTo(
            window.test_config_page
        ))
        self.assertFalse(window.test_config_page.save_config_btn.isEnabled())
        self.assertTrue(window.test_config_page.finalize_run_btn.isEnabled())

        config_before = self.paths.config_file.read_bytes()
        with patch(
            "presentation.qt.application_coordinator.prepare_configuration",
            side_effect=AssertionError("模拟模式不得创建正式运行"),
        ) as create, patch(
            "app.gui_runtime.connect_instruments",
            side_effect=AssertionError("模拟模式不得打开 VISA"),
        ) as visa, patch.object(
            window.coordinator,
            "_instrument_worker_factory",
            side_effect=AssertionError("模拟模式不得启动连接 worker"),
        ) as worker_factory, patch.object(
            window.coordinator.gui_data_service.configuration, "save"
        ) as save_configuration:
            window._finalize_run()

        self.assertIs(coordinator.state, RunFlowState.SIMULATING)
        self.assertTrue(coordinator.simulation_session_active())
        session = coordinator.simulation_session
        self.assertEqual(session.snapshot_name, "Mock")
        self.assertEqual(
            [kind.value for kind in session.measurement_types],
            ["cable_loss", "driver_power_mapping", "amplifier"],
        )
        self.assertEqual(
            [item.path.name for item in session.calibration_inputs_for("amplifier")],
            ["20261117T060245Z_9b2e7d10.json", "20261117T060600Z_5c1d9e42.json"],
        )
        # 三个页面共享同一个模拟会话：启动入口读取统一会话，页面展示 mock_data 来源。
        self.assertIs(
            window.cable_loss_page._start_facade.simulation_session(), session
        )
        self.assertIs(
            window.driver_mapping_page._start_facade.simulation_session(), session
        )
        self.assertIs(
            window.amplifier_page._start_facade.simulation_session(), session
        )
        self.assertTrue(coordinator.measurement_config_path())
        window.driver_mapping_page.on_activated()
        window.amplifier_page.on_activated()
        cable_source = session.calibration_input_for(
            "driver_power_mapping", "cable_loss"
        )
        self.assertIn(
            cable_source.path.name,
            window.driver_mapping_page.cable_loss_source_label.text(),
        )
        self.assertIn(
            "Mock 模拟数据",
            window.driver_mapping_page.cable_loss_source_label.text(),
        )
        for dependency, label in (
            ("cable_loss", window.amplifier_page.cable_loss_source_label),
            ("driver_power_mapping", window.amplifier_page.driver_mapping_source_label),
        ):
            source = session.calibration_input_for("amplifier", dependency)
            self.assertIn(source.path.name, label.text())
            self.assertIn("Mock 模拟数据", label.text())
        self.assertIsNone(coordinator.instrument_ctrl)
        self.assertIsNone(coordinator.instrument_worker)
        create.assert_not_called()
        visa.assert_not_called()
        worker_factory.assert_not_called()
        save_configuration.assert_not_called()
        # 模拟配置只读：连接模拟会话不重写 config.json。
        self.assertEqual(self.paths.config_file.read_bytes(), config_before)
        # 模拟模式不创建运行目录：临时根目录下只有配置与快照。
        self.assertFalse((self.root / "runs").exists())
        self.assertIs(window.view_stack.currentWidget(), window.workspace_page)
        self.assertIs(
            window.tab_widget.currentWidget(), window.cable_loss_page
        )
        self.assertEqual(window.mode_badge.text(), "Mock 模拟模式")

        self.assertIsNone(window._return_to_welcome())
        self.assertIs(window.view_stack.currentWidget(), window.welcome_page)
        self.assertFalse(coordinator.is_mock_mode())
        self.assertIsNone(coordinator.simulation_session)
        self.assertEqual(window.mode_badge.text(), "未选择测试模式")
        self.assertFalse(
            window.test_config_page.simulation_notice.isVisibleTo(
                window.test_config_page
            )
        )

    def test_returning_to_welcome_then_loading_a_normal_snapshot_has_no_mock_state(self):
        window = self._window()
        self._silence_dialogs()
        normal_snapshot = self.repository.save("普通配置", self.document)

        self._load_snapshot(window, self.mock_snapshot)
        window._return_to_welcome()
        self._load_snapshot(window, normal_snapshot)

        coordinator = window.coordinator
        self.assertFalse(coordinator.is_mock_mode())
        self.assertIsNone(coordinator.simulation_session)
        self.assertIsNone(coordinator.mock_configuration_name())
        self.assertNotEqual(window.mode_badge.text(), "Mock 模拟模式")
        self.assertFalse(
            window.test_config_page.simulation_notice.isVisibleTo(
                window.test_config_page
            )
        )
        self.assertTrue(window.test_config_page.save_config_btn.isEnabled())
        self.assertTrue(window.test_config_page.finalize_run_btn.isEnabled())


    def test_default_mock_paths_point_at_the_repository_data_package(self):
        from application.dto.measurement_results import MeasurementType
        from presentation.qt.composition import build_mock_data_paths

        mock_paths = build_mock_data_paths(PathService(PROJECT_ROOT))
        for measurement_type in (
            MeasurementType.CABLE_LOSS,
            MeasurementType.DRIVER_POWER_MAPPING,
            MeasurementType.AMPLIFIER,
        ):
            with self.subTest(measurement_type=measurement_type.value):
                self.assertTrue(
                    mock_paths.require_scenario_path_for(
                        measurement_type.value
                    ).is_file()
                )


if __name__ == "__main__":
    unittest.main()
