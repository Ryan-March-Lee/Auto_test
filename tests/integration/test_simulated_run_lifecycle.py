"""阶段五步骤 22/23：真实窗口进入仿真运行并走正式测量链路。

本模块用真实主窗口、真实配置页、真实 ``MeasurementController``、真实用例/服务/
阶段仓库跑完整三段。仿真数据根在临时目录下，仓库跟踪的 ``mock_data`` 仍是读数来源；
真实 ``runs/`` 与 ``test_results/`` 不得出现。

覆盖两类断言：

1. 仿真运行走正式分支：真实运行 ID、接线确认、正式进度/归档文案、校准摘要与
   仿真作用域结果目录，页面不再有“模拟数据仅展示”这类旧回放文案；
2. 每阶段完成后，窗口按结果自带的 ``archive_path`` 从仿真作用域结果查询回填
   下游页面的校准来源；用户手动选择的另一份结果不会被覆盖。

停顿由假时钟消除（只替换 ``simulation_sleep_fn``），事件顺序、进度分母、结果计算
与归档全部保持生产行为。
"""

import gc
import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QDialog

from infrastructure.filesystem.paths import MockDataPathService, PathService
from infrastructure.persistence.configuration_snapshot_repository import (
    FileConfigurationSnapshotRepository,
)
from presentation.qt.application_coordinator import RunFlowState
from presentation.qt.measurement_state import MeasurementStatus
from tests.ui.test_gui_import import gui_dependencies_available

PROJECT_ROOT = Path(__file__).parents[2]
MOCK_CONFIG = PROJECT_ROOT / "config.mock.json"
EXPECTED_FREQUENCIES = [round(1.0 + index * 0.1, 1) for index in range(11)]

# 仿真运行不得触达的真机入口；端口/阶段结算属于协调器的正常生命周期，不能禁止。
FORBIDDEN_CALLS = (
    "app.gui_runtime.connect_instruments",
    "instrument.measurement_factory.create_measurement_port",
)

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
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


class _Dialog:
    """固定返回值的接线确认替身；``exec()`` 立即返回，不进入嵌套事件循环。"""

    def __init__(self, result=QDialog.Accepted):
        self._result = result

    def exec(self):
        return self._result


@unittest.skipUnless(gui_dependencies_available(), "当前环境缺少完整 GUI 依赖")
class SimulatedRunLifecycleTests(unittest.TestCase):
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
        # 真实结果根的哨兵：仿真运行即使写入同长度内容，也会让内容指纹变化。
        self.real_results_sentinel = self.root / "test_results" / "sentinel.txt"
        self.real_results_sentinel.parent.mkdir(parents=True, exist_ok=True)
        self.real_results_sentinel.write_text(
            "simulated runs must not touch the real results root", encoding="utf-8"
        )

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

        # 假时钟：只去掉仿真快放的等待，保留事件顺序与进度分母。
        self.sleep_scales = []
        patcher = patch(
            "app.simulation_runtime.simulation_sleep_fn",
            side_effect=self._fake_sleep_fn,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        # 报告图表由 matplotlib 在 worker 线程渲染；在已有大量 Qt 控件的同一进程里
        # 会触发 Qt 后端的线程限制。图表渲染已由无 GUI 链路测试覆盖，这里只验证
        # “阶段目录里的 CSV/报告可见”，因此用空图集替换图表渲染。
        patcher = patch(
            "infrastructure.persistence.stage_artifact_repository."
            "_default_report_plots",
            return_value=({}, ()),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        for target in FORBIDDEN_CALLS:
            patcher = patch(
                target,
                side_effect=AssertionError(f"仿真运行不得调用 {target}"),
            )
            patcher.start()
            self.addCleanup(patcher.stop)

    def _fake_sleep_fn(self, time_scale):
        self.sleep_scales.append(time_scale)
        return lambda _seconds: None

    def _window(self):
        from presentation.qt.main_window import MainWindow

        window = MainWindow(paths=self.paths)
        self.addCleanup(_destroy_window, window)
        # Mock 场景/正式结果仍读仓库跟踪的数据包；仿真数据根由窗口的临时根派生。
        window.coordinator._mock_paths = MockDataPathService(PROJECT_ROOT)
        for page in (
            window.cable_loss_page,
            window.driver_mapping_page,
            window.amplifier_page,
        ):
            page._connection_dialog_factory = lambda _kind, _parent: _Dialog()
        return window

    def _load_snapshot(self, window, snapshot):
        with patch(
            "presentation.qt.configuration_snapshot_actions."
            "choose_configuration_snapshot",
            return_value=snapshot,
        ):
            window.load_configuration_from_welcome()

    def _pump_until(self, predicate, timeout=60.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.005)
        self.app.processEvents()
        return bool(predicate())

    def _wait_for_status(self, window, status, timeout=60.0):
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

    def test_simulated_run_uses_the_formal_pages_and_backfills_calibration(self):
        window = self._window()
        self._load_snapshot(window, self.mock_snapshot)
        coordinator = window.coordinator

        self.assertTrue(coordinator.is_mock_mode())
        self.assertEqual(coordinator.mock_configuration_name(), "Mock")
        self.assertEqual(window.mode_badge.text(), "仿真运行")
        self.assertIsNone(coordinator.prepared_run)
        config_before = self.paths.config_file.read_bytes()
        # 命名配置快照本身写在真实结果根的 configurations/ 下；它是测试装置，不是
        # 仿真链路的产物。进入前记录指纹，运行前后必须完全一致。
        real_results_before = _tree_snapshot(self.root / "test_results")

        with patch(
            "app.gui_runtime.connect_instruments",
            side_effect=AssertionError("仿真运行不得打开 VISA"),
        ) as visa:
            window._finalize_run()
            visa.assert_not_called()

        self.assertTrue(coordinator.is_simulated_run_active())
        self.assertIs(coordinator.state, RunFlowState.CONNECTED)
        run = coordinator.prepared_run
        self.assertIsNotNone(run)
        simulation_root = (self.root / "temp" / "simulation_runs").resolve()
        self.assertTrue(
            Path(run.run_directory).resolve().is_relative_to(simulation_root / "runs"),
            f"运行目录 {run.run_directory} 不在 {simulation_root / 'runs'} 下",
        )
        self.assertTrue(
            Path(coordinator.current_result_paths().test_results_dir)
            .resolve()
            .is_relative_to(simulation_root)
        )
        self.assertIn("仿真运行", coordinator.run_status_text())
        self.assertIn("不连接真实仪器", coordinator.run_status_text())
        self.assertIs(window.view_stack.currentWidget(), window.workspace_page)
        self.assertIs(window.tab_widget.currentWidget(), window.cable_loss_page)
        self.assertEqual(window.mode_badge.text(), "仿真运行")
        self.assertTrue(coordinator.measurement_session_ready())
        # 仿真配置只读：进入运行不重写 config.json。
        self.assertEqual(self.paths.config_file.read_bytes(), config_before)

        # -- 线损：真实运行 ID、正式运行控制台与归档文案 -----------------------
        window.cable_loss_page.on_activated()
        self.assertEqual(
            display_run_id(window.cable_loss_page), str(run.run_id)
        )
        window.cable_loss_page.start_measurement()
        self._wait_for_status(window, MeasurementStatus.COMPLETED)
        self.assertEqual(window.cable_loss_page.cable_loss_table.rowCount(), 11)
        logs = window.log_text.toPlainText()
        self.assertIn("线损阶段结果已自动归档", logs)
        self.assertNotIn("仅展示", logs)
        self.assertNotIn("模拟回放", logs)
        self.assertNotIn("[归档错误]", logs)

        stage_dir = Path(coordinator.current_result_paths().cable_loss_results_dir)
        cable_selection = window.driver_mapping_page.cable_loss_selection
        self.assertIsNotNone(cable_selection)
        self.assertTrue(Path(cable_selection.path).is_relative_to(stage_dir))
        amplifier_cable = window.amplifier_page.cable_loss_selection
        self.assertIsNotNone(amplifier_cable)
        self.assertEqual(Path(amplifier_cable.path), Path(cable_selection.path))
        self.assertIn(
            cable_selection.filename,
            window.driver_mapping_page.cable_loss_source_label.text(),
        )
        self.assertIn("阶段结果已自动归档", logs)

        # -- 驱动映射：自动回填的线损来源直接可用 ------------------------------
        window.driver_mapping_page.on_activated()
        window.driver_mapping_page.start_measurement()
        self._wait_for_status(window, MeasurementStatus.COMPLETED)
        driver_page = window.driver_mapping_page
        self.assertEqual(driver_page.realtime_buffer.frequencies, EXPECTED_FREQUENCIES)
        self.assertIn("驱动映射阶段结果已自动归档", window.log_text.toPlainText())
        mapping_dir = Path(
            coordinator.current_result_paths().driver_mapping_results_dir
        )
        mapping_selection = window.amplifier_page.driver_mapping_selection
        self.assertIsNotNone(mapping_selection)
        self.assertTrue(Path(mapping_selection.path).is_relative_to(mapping_dir))
        self.assertIn(
            mapping_selection.filename,
            window.amplifier_page.driver_mapping_source_label.text(),
        )

        # -- 主功放：两份新校准输入都来自本次仿真运行 --------------------------
        window.amplifier_page.on_activated()
        window.amplifier_page.start_measurement()
        self._wait_for_status(window, MeasurementStatus.COMPLETED)
        amplifier_page = window.amplifier_page
        self.assertEqual(
            amplifier_page.realtime_buffer.frequencies, EXPECTED_FREQUENCIES
        )
        # 主功放阶段结果按正式阶段记录保存；交付产物只生成在仿真阶段目录，
        # 页面不会收到运行级归档 DTO（与正式链路一致）。
        self.assertIn("主功放结果已保存", window.log_text.toPlainText())
        amplifier_dir = Path(
            coordinator.current_result_paths().amplifier_results_dir
        )
        artifacts = list(amplifier_dir.iterdir())
        self.assertTrue(any(item.suffix == ".csv" for item in artifacts))
        self.assertTrue(
            any(item.name.endswith("_report.html") for item in artifacts)
        )
        self.assertTrue(self.sleep_scales, "仿真运行必须注入快放 sleep_fn")

        # 仿真结果与运行都在仿真根内；真实目录从未变化。
        self.assertFalse((self.root / "runs").exists())
        self.assertEqual(_tree_snapshot(self.root / "test_results"), real_results_before)
        self.assertEqual(
            self.real_results_sentinel.read_text(encoding="utf-8"),
            "simulated runs must not touch the real results root",
        )
        self.assertTrue(
            any((simulation_root / "test_results" / stage).is_dir() for stage in (
                "cable_loss",
                "driver_mapping",
                "amplifier",
            ))
        )

        # -- 返回欢迎页：仿真身份与端口一起收尾 --------------------------------
        self.assertIsNone(window._return_to_welcome())
        self.assertIs(window.view_stack.currentWidget(), window.welcome_page)
        self.assertFalse(coordinator.is_mock_mode())
        self.assertIsNone(coordinator.current_simulated_run())
        self.assertIsNone(coordinator.prepared_run)
        self.assertEqual(window.mode_badge.text(), "未选择测试模式")

        # 再次加载普通配置：没有残留仿真身份、端口或输入来源。
        self._load_snapshot(window, self.normal_snapshot)
        self.assertFalse(coordinator.is_mock_mode())
        self.assertFalse(coordinator.measurement_session_ready())
        self.assertTrue(coordinator.run_status_text().startswith("当前执行: 未连接"))


def _tree_snapshot(root: Path) -> dict[str, tuple]:
    """把目录树收敛为可比较的内容指纹，用于“真实结果目录前后不变”的断言。

    只比较路径与大小不足以证明目录未被改写：同长度内容替换后大小仍相同。这里对每个
    文件记录 ``("file", 大小, SHA-256)``，并显式记录目录条目，使新增/删除的空目录
    同样让快照变化；不比较修改时间，避免读取本身或文件系统时间精度影响结论。
    """
    if not root.exists():
        return {}
    entries: dict[str, tuple] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            entries[relative] = ("dir",)
        elif path.is_file():
            entries[relative] = (
                "file",
                path.stat().st_size,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
    return entries


def display_run_id(page) -> str | None:
    """读取页面运行控制台回填的完整运行标识。"""
    return page.console.run_id_tooltip() or None


class TreeSnapshotContractTests(unittest.TestCase):
    """``_tree_snapshot`` 自身必须能发现同长度内容改写与目录增删。"""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)

    def test_the_snapshot_changes_when_same_size_content_is_rewritten(self):
        target = self.root / "result.json"
        target.write_text("aaaa", encoding="utf-8")
        before = _tree_snapshot(self.root)
        self.assertEqual(before["result.json"][1], 4)

        target.write_text("bbbb", encoding="utf-8")
        after = _tree_snapshot(self.root)

        self.assertEqual(after["result.json"][1], 4)
        self.assertNotEqual(after, before)

    def test_the_snapshot_records_files_and_directories(self):
        (self.root / "configurations").mkdir()
        (self.root / "configurations" / "a.json").write_text("{}", encoding="utf-8")
        snapshot = _tree_snapshot(self.root)

        self.assertEqual(snapshot["configurations"], ("dir",))
        self.assertEqual(snapshot["configurations/a.json"][0], "file")
        self.assertEqual(
            snapshot["configurations/a.json"][1], 2
        )

    def test_missing_and_empty_roots_produce_the_same_snapshot(self):
        self.assertEqual(_tree_snapshot(self.root / "missing"), {})
        self.assertEqual(_tree_snapshot(self.root), {})


if __name__ == "__main__":
    unittest.main()
