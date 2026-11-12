import ast
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]


class MainWindowTests(unittest.TestCase):
    def test_build_application_reuses_qapplication(self):
        from PySide6.QtWidgets import QApplication
        from presentation.qt.main_window import build_application

        first = build_application([])
        second = build_application([])
        self.assertIs(first, second)
        self.assertIs(first, QApplication.instance())

    def test_build_application_forces_light_palette(self):
        """系统深色模式不得把未设置样式的容器底色变成深色（表现为界面“黑框”）。"""
        from PySide6.QtGui import QPalette
        from presentation.qt.main_window import build_application

        app = build_application([])
        for role in (QPalette.Window, QPalette.Base, QPalette.Button):
            color = app.palette().color(role)
            self.assertGreater(
                color.lightness(),
                200,
                f"{role} 颜色为 {color.name()}，深色调色板会让分组框边距和容器间隙露出黑底",
            )

    def test_launcher_uses_new_main_window_module(self):
        import launcher

        fake_module = type(sys)("presentation.qt.main_window")
        fake_module.main = Mock()
        with patch.dict(sys.modules, {"presentation.qt.main_window": fake_module}):
            with patch.object(launcher, "PROJECT_ROOT", ROOT):
                self.assertTrue(launcher.launch_gui(silent=True))
        fake_module.main.assert_called_once_with()

    def test_new_main_window_has_no_reverse_compatibility_import(self):
        source = (ROOT / "presentation" / "qt" / "main_window.py").read_text(
            encoding="utf-8-sig"
        )
        tree = ast.parse(source)
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        self.assertNotIn("enhanced_main_gui", imported)


class RemovedHistoryPageWiringTests(unittest.TestCase):
    """步骤 18：历史运行标签页已移除，窗口不再构造或刷新它。"""

    def test_main_window_has_no_history_page_wiring(self):
        source = (ROOT / "presentation" / "qt" / "main_window.py").read_text(
            encoding="utf-8-sig"
        )
        for removed in (
            "run_history_page",
            "create_run_history_tab",
            "refresh_history",
            "build_run_history",
        ):
            with self.subTest(removed=removed):
                self.assertNotIn(removed, source)


class MainWindowCloseEventTests(unittest.TestCase):
    """关闭窗口：确认只做一次，之后的停止动作视为已提交。"""

    def _window(self, *, measurement_active=False, worker_running=False,
                worker_reference=False, shutdown_ok=True, stop_ok=True,
                release_ok=True, unreleased=False, pending_run=False,
                pending_discard_ok=True):
        from PySide6.QtWidgets import QMessageBox

        from presentation.qt.application_coordinator import RunFlowState
        from presentation.qt.main_window import MainWindow

        controller = Mock()
        controller.state.is_active = measurement_active
        controller.shutdown.return_value = shutdown_ok
        coordinator = Mock()
        coordinator.state = RunFlowState.CONFIGURING
        coordinator.instrument_worker_running.return_value = worker_running
        coordinator.has_instrument_worker.return_value = worker_running or worker_reference
        coordinator.stop_instrument_worker.return_value = stop_ok
        coordinator.release_instrument_port.return_value = release_ok
        coordinator.has_unreleased_ports.return_value = unreleased
        coordinator.has_pending_run.return_value = pending_run
        coordinator.abandon_pending_run.return_value = pending_discard_ok
        coordinator.cancel_finalization.return_value = True

        tab_signal = Mock()
        pages = {"first": Mock(), "second": Mock()}
        window = SimpleNamespace(
            measurement_controller=controller,
            coordinator=coordinator,
            tab_widget=SimpleNamespace(currentChanged=tab_signal),
            pages=pages,
            add_log_message=Mock(),
        )
        window._on_page_changed = Mock()
        # 未通过 __init__ 构造的替身窗口需要显式绑定确认/收尾方法。
        window._confirm_exit = lambda: MainWindow._confirm_exit(window)
        window._keep_window_open = lambda event, message: MainWindow._keep_window_open(
            window, event, message
        )
        window._disconnect_page_signals = lambda: MainWindow._disconnect_page_signals(
            window
        )
        event = Mock()
        self.Yes = QMessageBox.Yes
        self.No = QMessageBox.No
        return MainWindow, window, event, pages, tab_signal

    def test_close_event_disconnects_tabs_and_closes_pages(self):
        MainWindow, window, event, pages, tab_signal = self._window()

        MainWindow.closeEvent(window, event)

        window.coordinator.release_instrument_port.assert_called_once_with()
        tab_signal.disconnect.assert_called_once_with(window._on_page_changed)
        for page in pages.values():
            page.close.assert_called_once_with()
        event.accept.assert_called_once_with()

    def test_close_event_asks_only_once_before_stopping_everything(self):
        MainWindow, window, event, _pages, _tab = self._window(
            measurement_active=True, worker_running=True
        )

        with patch(
            "presentation.qt.main_window.QMessageBox.question", return_value=self.Yes
        ) as question:
            MainWindow.closeEvent(window, event)

        # 确认只在开头做一次：此前的实现会在停掉测量后再问一遍，
        # 用户第二次拒绝就会留下“测量已停、窗口还在”的半提交状态。
        self.assertEqual(question.call_count, 1)
        window.measurement_controller.shutdown.assert_called_once_with(timeout_ms=5000)
        window.coordinator.stop_instrument_worker.assert_called_once_with(5000)
        event.accept.assert_called_once_with()

    def test_close_event_cancelled_confirmation_changes_nothing(self):
        MainWindow, window, event, pages, _tab = self._window(
            measurement_active=True, worker_running=True
        )

        with patch(
            "presentation.qt.main_window.QMessageBox.question", return_value=self.No
        ):
            MainWindow.closeEvent(window, event)

        window.measurement_controller.shutdown.assert_not_called()
        window.coordinator.stop_instrument_worker.assert_not_called()
        window.coordinator.release_instrument_port.assert_not_called()
        for page in pages.values():
            page.close.assert_not_called()
        event.ignore.assert_called_once_with()
        event.accept.assert_not_called()

    def test_close_event_keeps_window_open_when_measurement_will_not_stop(self):
        MainWindow, window, event, _pages, _tab = self._window(
            measurement_active=True, shutdown_ok=False
        )

        with patch(
            "presentation.qt.main_window.QMessageBox.question", return_value=self.Yes
        ):
            MainWindow.closeEvent(window, event)

        event.ignore.assert_called_once_with()
        event.accept.assert_not_called()
        self.assertTrue(
            any(
                "未能在关闭期限内停止" in str(call.args[0])
                for call in window.add_log_message.call_args_list
            )
        )

    def test_close_event_keeps_window_open_when_instrument_worker_will_not_stop(self):
        MainWindow, window, event, _pages, _tab = self._window(
            worker_running=True, stop_ok=False
        )

        with patch(
            "presentation.qt.main_window.QMessageBox.question", return_value=self.Yes
        ):
            MainWindow.closeEvent(window, event)

        window.coordinator.stop_instrument_worker.assert_called_once_with(5000)
        event.ignore.assert_called_once_with()
        event.accept.assert_not_called()

    def test_close_event_stays_open_when_the_port_cannot_be_cleaned(self):
        MainWindow, window, event, pages, _tab = self._window(
            release_ok=False, unreleased=True
        )

        MainWindow.closeEvent(window, event)

        # 阶段三：端口未确认清理时没有“确认后带病退出”的旁路，窗口必须保持打开。
        event.ignore.assert_called_once_with()
        event.accept.assert_not_called()
        for page in pages.values():
            page.close.assert_not_called()
        self.assertTrue(
            any(
                "仪器端口未能确认清理" in str(call.args[0])
                for call in window.add_log_message.call_args_list
            )
        )

    def test_close_event_stays_open_while_unreleased_ports_remain(self):
        MainWindow, window, event, pages, _tab = self._window(
            release_ok=True, unreleased=True
        )

        MainWindow.closeEvent(window, event)

        event.ignore.assert_called_once_with()
        event.accept.assert_not_called()
        for page in pages.values():
            page.close.assert_not_called()
        self.assertTrue(
            any(
                "仍有未完成的仪器清理" in str(call.args[0])
                for call in window.add_log_message.call_args_list
            )
        )

    def test_close_event_stays_open_when_the_pending_run_cannot_be_discarded(self):
        MainWindow, window, event, pages, _tab = self._window(
            pending_run=True, pending_discard_ok=False
        )

        with patch(
            "presentation.qt.main_window.QMessageBox.question", return_value=self.Yes
        ):
            MainWindow.closeEvent(window, event)

        window.coordinator.abandon_pending_run.assert_called_once_with()
        event.ignore.assert_called_once_with()
        event.accept.assert_not_called()
        for page in pages.values():
            page.close.assert_not_called()

    def test_close_event_cancels_the_finalization_before_discarding(self):
        MainWindow, window, event, _pages, _tab = self._window()
        from presentation.qt.application_coordinator import RunFlowState

        window.coordinator.state = RunFlowState.FINALIZING
        pending = {"value": True}
        window.coordinator.has_pending_run.side_effect = lambda: pending["value"]

        def _cancel(*_args):
            pending["value"] = False
            return True

        window.coordinator.cancel_finalization.side_effect = _cancel

        with patch(
            "presentation.qt.main_window.QMessageBox.question", return_value=self.Yes
        ):
            MainWindow.closeEvent(window, event)

        window.coordinator.cancel_finalization.assert_called_once_with("窗口关闭")
        window.coordinator.abandon_pending_run.assert_not_called()
        event.accept.assert_called_once_with()

    def test_close_event_stops_a_finished_worker_reference_before_discarding(self):
        """worker 已跑完但引用还在时也要先收尾，不能直接跳过连接线程这一步。"""
        MainWindow, window, event, _pages, _tab = self._window(
            worker_reference=True, pending_run=True
        )

        with patch(
            "presentation.qt.main_window.QMessageBox.question", return_value=self.Yes
        ):
            MainWindow.closeEvent(window, event)

        window.coordinator.stop_instrument_worker.assert_called_once_with(5000)
        called = [entry[0] for entry in window.coordinator.mock_calls]
        self.assertLess(
            called.index("stop_instrument_worker"),
            called.index("abandon_pending_run"),
            "撤销运行目录只能在连接线程停止之后",
        )
        event.accept.assert_called_once_with()

    def test_close_event_never_discards_the_run_before_the_worker_stops(self):
        MainWindow, window, event, _pages, _tab = self._window(
            worker_running=True, stop_ok=False, pending_run=True
        )

        with patch(
            "presentation.qt.main_window.QMessageBox.question", return_value=self.Yes
        ):
            MainWindow.closeEvent(window, event)

        window.coordinator.abandon_pending_run.assert_not_called()
        window.coordinator.release_instrument_port.assert_not_called()
        event.ignore.assert_called_once_with()
        event.accept.assert_not_called()

    def test_close_event_does_not_cancel_a_finalization_that_finished_meanwhile(self):
        """确认框期间连接已经成功：已提交的运行不能被退出流程当成待撤销事务。"""
        MainWindow, window, event, _pages, _tab = self._window()
        from presentation.qt.application_coordinator import RunFlowState

        window.coordinator.state = RunFlowState.FINALIZING
        window.coordinator.has_pending_run.return_value = False

        def _commit_during_close(*_args):
            window.coordinator.state = RunFlowState.CONNECTED
            return True

        window.coordinator.stop_instrument_worker.side_effect = _commit_during_close

        with patch(
            "presentation.qt.main_window.QMessageBox.question", return_value=self.Yes
        ):
            MainWindow.closeEvent(window, event)

        window.coordinator.cancel_finalization.assert_not_called()
        window.coordinator.abandon_pending_run.assert_not_called()
        event.accept.assert_called_once_with()

class StatusPanelPlacementTests(unittest.TestCase):
    """唯一的进度与日志面板跟着当前测量页走：测量页挂进左栏，浏览/配置页收起。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _window(self, current_page, *, pages=None):
        from presentation.qt.main_window import MainWindow
        from presentation.qt.status_panel import StatusPanel

        panel = StatusPanel()
        window = SimpleNamespace(
            status_panel=panel,
            pages=pages or {},
            tab_widget=SimpleNamespace(currentWidget=lambda: current_page),
            _status_panel_slot=None,
        )
        window._sync_status_panel_visibility = (
            lambda: MainWindow._sync_status_panel_visibility(window)
        )
        window._current_status_slot = (
            lambda: MainWindow._current_status_slot(window)
        )
        return window, panel

    @staticmethod
    def _page(slot=None):
        return SimpleNamespace(status_slot=slot)

    def test_measurement_page_receives_the_panel_in_its_status_slot(self):
        from presentation.qt.status_panel import StatusSlot

        slot = StatusSlot()
        page = self._page(slot)
        window, panel = self._window(page, pages={"cable_loss": page})

        window._sync_status_panel_visibility()

        self.assertIs(panel.parentWidget(), slot)
        self.assertFalse(panel.isHidden())

    def test_browsing_page_hides_the_panel_but_the_log_keeps_receiving(self):
        from presentation.qt.status_panel import StatusSlot

        slot = StatusSlot()
        page = self._page(slot)
        window, panel = self._window(page, pages={"cable_loss": page})
        window._sync_status_panel_visibility()
        panel.add_log_message("配置页日志")

        browsing = SimpleNamespace()
        window.tab_widget = SimpleNamespace(currentWidget=lambda: browsing)
        window.pages = {"cable_loss": page, "visualization": browsing}
        window._sync_status_panel_visibility()

        self.assertTrue(panel.isHidden())
        self.assertIn("配置页日志", panel.log_text.toPlainText())
        self.assertEqual(slot.layout().count(), 0)

    def test_switching_measurement_pages_moves_the_same_panel(self):
        from presentation.qt.status_panel import StatusSlot

        first_slot, second_slot = StatusSlot(), StatusSlot()
        first, second = self._page(first_slot), self._page(second_slot)
        window, panel = self._window(
            first, pages={"cable_loss": first, "amplifier": second}
        )
        window._sync_status_panel_visibility()

        window.tab_widget = SimpleNamespace(currentWidget=lambda: second)
        window._sync_status_panel_visibility()

        self.assertIs(panel.parentWidget(), second_slot)
        self.assertEqual(first_slot.layout().count(), 0)
        self.assertEqual(second_slot.layout().indexOf(panel), 0)

    def test_syncing_the_same_page_again_does_not_mount_twice(self):
        from presentation.qt.status_panel import StatusSlot

        slot = StatusSlot()
        page = self._page(slot)
        window, panel = self._window(page, pages={"cable_loss": page})

        window._sync_status_panel_visibility()
        window._sync_status_panel_visibility()

        self.assertEqual(slot.layout().count(), 1)
        self.assertIs(panel.parentWidget(), slot)


class MainWindowAssemblyBoundaryTests(unittest.TestCase):
    """步骤 10：主窗口只做组装，配置、运行和硬件入口都在协调器。"""

    SOURCE = ROOT / "presentation" / "qt" / "main_window.py"

    def setUp(self):
        self.source = self.SOURCE.read_text(encoding="utf-8-sig")
        self.tree = ast.parse(self.source)

    def _main_window(self):
        for node in self.tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
                return node
        raise AssertionError("main_window.py 中未找到 MainWindow 类")

    def test_main_window_does_not_load_configuration_json_directly(self):
        methods = {node.name for node in self._main_window().body if isinstance(node, ast.FunctionDef)}
        for name in ("load_config", "update_and_save_config", "_save_config_file",
                     "_build_config_from_ui", "_read_instrument_config_from_ui",
                     "_measurement_config_path"):
            self.assertNotIn(
                name, methods,
                f"{name} 已迁移到 ApplicationCoordinator，主窗口不得重新持有配置/运行逻辑",
            )

    def test_main_window_does_not_modify_sys_path(self):
        self.assertNotIn("sys.path", self.source)
        self.assertNotIn("project_root =", self.source)

    def test_main_window_has_no_dead_legacy_implementation_blocks(self):
        # 旧实现不得以文档字符串形式残留在源码里。
        self.assertNotIn("Legacy configuration-page implementation", self.source)
        self.assertNotIn("Legacy configuration helpers removed", self.source)
        self.assertNotIn("create_power_supply_config", self.source)

    def test_main_window_stays_small_and_assembly_focused(self):
        line_count = len(self.source.splitlines())
        # 步骤 13 起窗口装配两个配置页（测试配置页 + 独立电源页）；仪器排布与电源配置
        # 是第三个、第四个整屏步骤，窗口只多出创建、接线与切换几行。上限随这次装配
        # 增加，但不再放宽：业务读数/校验/保存仍在页面与协调器。
        self.assertLessEqual(
            line_count, 740, f"main_window.py 有 {line_count} 行，超出窗口装配上限"
        )

    def test_main_window_uses_the_coordinator_for_gui_state(self):
        for literal in (
            "ApplicationCoordinator(",
            "run_provider=self.coordinator.resolve_measurement_run",
            "config_path_provider=self.coordinator.measurement_config_path",
            "driver_mode_provider=self.coordinator.amplifier_driver_mode",
        ):
            self.assertIn(literal, self.source)

    def test_rack_overview_receives_the_selected_test_mode(self):
        """仪器排布页要知道欢迎页选的模式：无驱放测试的链路里不能出现 Driver。"""
        self.assertIn(
            "driver_enabled=self.coordinator.amplifier_driver_mode()",
            self.source,
        )

    def test_main_window_does_not_read_config_widgets(self):
        for widget in ("driver_mode_check", "freq_start", "freq_stop", "freq_step", "freq_extra", "sg_address", "power_config_widgets"):
            self.assertNotIn(
                widget, self.source,
                f"主窗口不得直接读取配置控件 {widget}；应使用配置页的窄接口",
            )


class MainWindowMigrationResidueTests(unittest.TestCase):
    """步骤 12：窗口不再保留迁移期的旧实现与空占位方法。"""

    SOURCE = ROOT / "presentation" / "qt" / "main_window.py"

    def setUp(self):
        self.source = self.SOURCE.read_text(encoding="utf-8-sig")
        self.tree = ast.parse(self.source)

    def _method(self, name):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        raise AssertionError(f"main_window.py 中未找到 {name}")

    def test_dead_instruction_text_implementation_is_gone(self):
        # 测量页的线路文字说明已删除，窗口不再需要转发任何说明文本。
        methods = {
            node.name
            for node in self.tree.body if isinstance(node, ast.FunctionDef)
        }
        methods |= {
            node.name
            for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef)
        }
        self.assertNotIn("update_amplifier_instruction_text", methods)
        self.assertNotIn("_refresh_amplifier_instruction_text", methods)
        self.assertNotIn("self.instruction_text", self.source)
        self.assertNotIn("driver_mode_changed", self.source)

    def test_stage_result_loading_declares_its_expected_failures(self):
        path = ROOT / "presentation" / "qt" / "stage_result_selection.py"
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        function = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "choose_stage_result"
        )
        handlers = [
            ast.unparse(handler.type) if handler.type else None
            for handler in ast.walk(function)
            if isinstance(handler, ast.ExceptHandler)
        ]
        self.assertEqual(
            handlers,
            ["(OSError, ValueError)"],
            "显式选择结果文件只处理“文件不可读/不是正式结果”这一类明确失败",
        )

    def test_window_uses_logging_instead_of_print(self):
        self.assertNotIn("print(", self.source)


class CableLossResultFileSelectionTests(unittest.TestCase):
    """步骤 10：线损页按钮改为显式结果文件选择器。"""

    def _window(self, results):
        from presentation.qt.main_window import MainWindow

        logs = []
        page = Mock()
        window = SimpleNamespace(
            paths=SimpleNamespace(
                cable_loss_results_dir=Path("test_results") / "cable_loss"
            ),
            gui_data_service=SimpleNamespace(results=results),
            cable_loss_page=page,
            add_log_message=logs.append,
        )
        return MainWindow, window, page, logs

    def test_cancelled_selection_keeps_the_current_display(self):
        results = Mock()
        MainWindow, window, page, logs = self._window(results)

        with patch(
            "presentation.qt.stage_result_selection.QFileDialog.getOpenFileName",
            return_value=("", ""),
        ):
            MainWindow._load_cable_loss_result(window)

        results.load_stage_result.assert_not_called()
        page.show_loaded_result.assert_not_called()
        self.assertEqual(logs, ["已取消选择线损测量结果"])

    def test_selected_file_is_loaded_through_the_application_service(self):
        from application.ports.stage_result_repository import MeasurementStage
        from presentation.qt.measurement_state import MeasurementKind

        loaded = SimpleNamespace(
            result_id="stage::cable_loss::20261111T010203Z_a1b2c3d4",
            filename="20261111T010203Z_a1b2c3d4.json",
            result=object(),
        )
        results = Mock()
        results.load_stage_result.return_value = loaded
        MainWindow, window, page, logs = self._window(results)
        selected = str(Path("test_results") / "cable_loss" / loaded.filename)

        with patch(
            "presentation.qt.stage_result_selection.QFileDialog.getOpenFileName",
            return_value=(selected, "JSON 文件 (*.json)"),
        ) as dialog:
            MainWindow._load_cable_loss_result(window)

        dialog.assert_called_once()
        _parent, title, directory, filters = dialog.call_args.args
        self.assertEqual(title, "选择线损测量结果")
        self.assertEqual(directory, str(window.paths.cable_loss_results_dir))
        self.assertIn("*.json", filters)
        results.load_stage_result.assert_called_once_with(
            MeasurementStage.CABLE_LOSS, selected
        )
        args, kwargs = page.show_loaded_result.call_args
        reference = args[0]
        self.assertEqual(reference.result_id, loaded.result_id)
        self.assertIs(reference.kind, MeasurementKind.CABLE_LOSS)
        self.assertIs(reference.value, loaded.result)
        self.assertEqual(kwargs["filename"], loaded.filename)
        self.assertEqual(logs, [])

    def test_unreadable_or_wrong_result_keeps_the_current_display(self):
        results = Mock()
        results.load_stage_result.side_effect = ValueError(
            "阶段结果路径必须直接位于 cable_loss 阶段目录"
        )
        MainWindow, window, page, logs = self._window(results)

        with patch(
            "presentation.qt.stage_result_selection.QFileDialog.getOpenFileName",
            return_value=("test_results/cable_loss/broken.json", "JSON"),
        ):
            MainWindow._load_cable_loss_result(window)

        page.show_loaded_result.assert_not_called()
        self.assertEqual(len(logs), 1)
        self.assertIn("加载线损测量结果失败", logs[0])
        self.assertIn("cable_loss 阶段目录", logs[0])


class DriverMappingCableLossSelectionTests(unittest.TestCase):
    """步骤 11：驱动映射页通过文件选择器显式选择线损结果。"""

    def _window(self, results):
        from presentation.qt.main_window import MainWindow

        logs = []
        page = Mock()
        window = SimpleNamespace(
            paths=SimpleNamespace(
                cable_loss_results_dir=Path("test_results") / "cable_loss"
            ),
            gui_data_service=SimpleNamespace(results=results),
            driver_mapping_page=page,
            add_log_message=logs.append,
        )
        return MainWindow, window, page, logs

    def test_result_without_file_facts_does_not_become_a_selection(self):
        """缺少摘要的结果无法在启动时复核，不能留下“看起来能开始”的选择。"""
        loaded = SimpleNamespace(
            result_id="stage::cable_loss::20261111T010203Z_a1b2c3d4",
            filename="20261111T010203Z_a1b2c3d4.json",
            result=SimpleNamespace(created_at="2026-11-11T01:02:03+00:00"),
        )
        results = Mock()
        results.load_stage_result.return_value = loaded
        MainWindow, window, page, logs = self._window(results)
        selected = str(Path("test_results") / "cable_loss" / loaded.filename)

        with patch(
            "presentation.qt.stage_result_selection.QFileDialog.getOpenFileName",
            return_value=(selected, "JSON 文件 (*.json)"),
        ):
            MainWindow._load_driver_mapping_cable_loss(window)

        page.show_loaded_cable_loss.assert_not_called()
        self.assertEqual(len(logs), 1)
        self.assertIn("缺少文件摘要", logs[0])

    def test_cancelled_selection_keeps_the_page_unchanged(self):
        results = Mock()
        MainWindow, window, page, logs = self._window(results)

        with patch(
            "presentation.qt.stage_result_selection.QFileDialog.getOpenFileName",
            return_value=("", ""),
        ):
            MainWindow._load_driver_mapping_cable_loss(window)

        results.load_stage_result.assert_not_called()
        page.show_loaded_cable_loss.assert_not_called()
        self.assertEqual(logs, ["已取消选择驱动映射使用的线损测量结果"])

    def test_selected_file_is_loaded_and_its_source_is_shown(self):
        from application.ports.stage_result_repository import MeasurementStage

        loaded = SimpleNamespace(
            result_id="stage::cable_loss::20261111T010203Z_a1b2c3d4",
            filename="20261111T010203Z_a1b2c3d4.json",
            result=SimpleNamespace(created_at="2026-11-11T01:02:03+00:00"),
            sha256="a" * 64,
            size_bytes=128,
        )
        results = Mock()
        results.load_stage_result.return_value = loaded
        MainWindow, window, page, logs = self._window(results)
        selected = str(Path("test_results") / "cable_loss" / loaded.filename)

        with patch(
            "presentation.qt.stage_result_selection.QFileDialog.getOpenFileName",
            return_value=(selected, "JSON 文件 (*.json)"),
        ) as dialog:
            MainWindow._load_driver_mapping_cable_loss(window)

        _parent, title, directory, filters = dialog.call_args.args
        self.assertEqual(title, "选择驱动映射使用的线损测量结果")
        self.assertEqual(directory, str(window.paths.cable_loss_results_dir))
        self.assertIn("*.json", filters)
        results.load_stage_result.assert_called_once_with(
            MeasurementStage.CABLE_LOSS, selected
        )
        page.show_loaded_cable_loss.assert_called_once_with(
            path=selected,
            filename=loaded.filename,
            created_at=loaded.result.created_at,
            sha256=loaded.sha256,
            size_bytes=loaded.size_bytes,
        )
        self.assertEqual(logs, [])

    def test_unreadable_or_wrong_result_keeps_the_page_unchanged(self):
        results = Mock()
        results.load_stage_result.side_effect = ValueError(
            "阶段结果路径必须直接位于 cable_loss 阶段目录"
        )
        MainWindow, window, page, logs = self._window(results)

        with patch(
            "presentation.qt.stage_result_selection.QFileDialog.getOpenFileName",
            return_value=("test_results/cable_loss/broken.json", "JSON"),
        ):
            MainWindow._load_driver_mapping_cable_loss(window)

        page.show_loaded_cable_loss.assert_not_called()
        self.assertEqual(len(logs), 1)
        self.assertIn("加载驱动映射使用的线损测量结果失败", logs[0])
        self.assertIn("cable_loss 阶段目录", logs[0])


class AmplifierCalibrationInputSelectionTests(unittest.TestCase):
    """步骤 12：主功放页通过文件选择器显式选择线损与驱动映射。"""

    def _window(self, results):
        from presentation.qt.main_window import MainWindow

        logs = []
        page = Mock()
        window = SimpleNamespace(
            paths=SimpleNamespace(
                cable_loss_results_dir=Path("test_results") / "cable_loss",
                driver_mapping_results_dir=Path("test_results") / "driver_mapping",
            ),
            gui_data_service=SimpleNamespace(results=results),
            amplifier_page=page,
            add_log_message=logs.append,
        )
        return MainWindow, window, page, logs

    def test_cancelled_cable_loss_selection_keeps_the_page_unchanged(self):
        results = Mock()
        MainWindow, window, page, logs = self._window(results)

        with patch(
            "presentation.qt.stage_result_selection.QFileDialog.getOpenFileName",
            return_value=("", ""),
        ):
            MainWindow._load_amplifier_cable_loss(window)

        results.load_stage_result.assert_not_called()
        page.show_loaded_cable_loss.assert_not_called()
        self.assertEqual(logs, ["已取消选择主功放使用的线损测量结果"])

    def test_selected_cable_loss_is_loaded_and_its_source_is_shown(self):
        from application.ports.stage_result_repository import MeasurementStage

        loaded = SimpleNamespace(
            result_id="stage::cable_loss::20261111T010203Z_a1b2c3d4",
            filename="20261111T010203Z_a1b2c3d4.json",
            result=SimpleNamespace(created_at="2026-11-11T01:02:03+00:00"),
            sha256="a" * 64,
            size_bytes=128,
        )
        results = Mock()
        results.load_stage_result.return_value = loaded
        MainWindow, window, page, logs = self._window(results)
        selected = str(Path("test_results") / "cable_loss" / loaded.filename)

        with patch(
            "presentation.qt.stage_result_selection.QFileDialog.getOpenFileName",
            return_value=(selected, "JSON 文件 (*.json)"),
        ) as dialog:
            MainWindow._load_amplifier_cable_loss(window)

        _parent, title, directory, filters = dialog.call_args.args
        self.assertEqual(title, "选择主功放使用的线损测量结果")
        self.assertEqual(directory, str(window.paths.cable_loss_results_dir))
        self.assertIn("*.json", filters)
        results.load_stage_result.assert_called_once_with(
            MeasurementStage.CABLE_LOSS, selected
        )
        page.show_loaded_cable_loss.assert_called_once_with(
            path=selected,
            filename=loaded.filename,
            created_at=loaded.result.created_at,
            sha256=loaded.sha256,
            size_bytes=loaded.size_bytes,
        )
        page.show_loaded_driver_mapping.assert_not_called()
        self.assertEqual(logs, [])

    def test_broken_cable_loss_file_keeps_the_page_unchanged(self):
        results = Mock()
        results.load_stage_result.side_effect = ValueError(
            "阶段结果路径必须直接位于 cable_loss 阶段目录"
        )
        MainWindow, window, page, logs = self._window(results)

        with patch(
            "presentation.qt.stage_result_selection.QFileDialog.getOpenFileName",
            return_value=("test_results/cable_loss/broken.json", "JSON"),
        ):
            MainWindow._load_amplifier_cable_loss(window)

        page.show_loaded_cable_loss.assert_not_called()
        self.assertEqual(len(logs), 1)
        self.assertIn("加载主功放使用的线损测量结果失败", logs[0])
        self.assertIn("cable_loss 阶段目录", logs[0])

    def test_selected_driver_mapping_is_loaded_from_its_own_stage_directory(self):
        from application.ports.stage_result_repository import MeasurementStage

        loaded = SimpleNamespace(
            result_id="stage::driver_mapping::20261110T050607Z_0f1e2d3c",
            filename="20261110T050607Z_0f1e2d3c.json",
            result=SimpleNamespace(created_at="2026-11-10T05:06:07+00:00"),
            sha256="b" * 64,
            size_bytes=256,
        )
        results = Mock()
        results.load_stage_result.return_value = loaded
        MainWindow, window, page, logs = self._window(results)
        selected = str(Path("test_results") / "driver_mapping" / loaded.filename)

        with patch(
            "presentation.qt.stage_result_selection.QFileDialog.getOpenFileName",
            return_value=(selected, "JSON 文件 (*.json)"),
        ) as dialog:
            MainWindow._load_amplifier_driver_mapping(window)

        _parent, title, directory, filters = dialog.call_args.args
        self.assertEqual(title, "选择主功放使用的驱动映射结果")
        self.assertEqual(directory, str(window.paths.driver_mapping_results_dir))
        self.assertIn("*.json", filters)
        results.load_stage_result.assert_called_once_with(
            MeasurementStage.DRIVER_MAPPING, selected
        )
        page.show_loaded_driver_mapping.assert_called_once_with(
            path=selected,
            filename=loaded.filename,
            created_at=loaded.result.created_at,
            sha256=loaded.sha256,
            size_bytes=loaded.size_bytes,
        )
        # 两个入口互不覆盖：加载驱动映射不会改写线损选择。
        page.show_loaded_cable_loss.assert_not_called()
        self.assertEqual(logs, [])

    def test_cancelled_driver_mapping_selection_keeps_the_page_unchanged(self):
        results = Mock()
        MainWindow, window, page, logs = self._window(results)

        with patch(
            "presentation.qt.stage_result_selection.QFileDialog.getOpenFileName",
            return_value=("", ""),
        ):
            MainWindow._load_amplifier_driver_mapping(window)

        results.load_stage_result.assert_not_called()
        page.show_loaded_driver_mapping.assert_not_called()
        self.assertEqual(logs, ["已取消选择主功放使用的驱动映射结果"])

    def test_broken_driver_mapping_file_keeps_the_page_unchanged(self):
        results = Mock()
        results.load_stage_result.side_effect = ValueError(
            "阶段结果路径必须直接位于 driver_mapping 阶段目录"
        )
        MainWindow, window, page, logs = self._window(results)

        with patch(
            "presentation.qt.stage_result_selection.QFileDialog.getOpenFileName",
            return_value=("test_results/driver_mapping/broken.json", "JSON"),
        ):
            MainWindow._load_amplifier_driver_mapping(window)

        page.show_loaded_driver_mapping.assert_not_called()
        self.assertEqual(len(logs), 1)
        self.assertIn("加载主功放使用的驱动映射结果失败", logs[0])
        self.assertIn("driver_mapping 阶段目录", logs[0])


if __name__ == "__main__":
    unittest.main()
