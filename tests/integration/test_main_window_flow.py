"""步骤 10 收尾：真实 MainWindow + ApplicationCoordinator 的离屏流程回归。

这些用例不使用协调器替身：它们构造真实窗口、真实 ConfigurationPage 和真实运行契约，
固定“页面信号 → 协调器 → 运行目录 / 状态栏”的端到端接线，避免只靠属性写入的
静态门禁来判断所有权。
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from domain.run.models import RunStatus
from infrastructure.filesystem.paths import PathService
from infrastructure.persistence.run_repository import RunRepository
from presentation.qt.application_coordinator import RunFlowState
from tests.integration.test_gui_two_phase_flow import _fully_valid_gui_config
from tests.ui.test_gui_import import gui_dependencies_available


_APPLICATION = None


class _RecordingConnectionWorker:
    """连接 worker 替身：只记录启动，不打开任何设备。"""

    def __init__(self, run):
        self.run = run
        self.started = False
        self.stopped = False
        self.unreleased_port = None
        self.signals = SimpleNamespace(
            finished=MagicMock(),
            result=MagicMock(),
            stopped=MagicMock(),
            error=MagicMock(),
            message=MagicMock(),
            progress=MagicMock(),
        )

    def start(self):
        self.started = True

    def isRunning(self):
        return False

    def stop(self):
        self.stopped = True

    def wait(self, timeout_ms=None):
        return True


class _ConnectedPort:
    """可正常关闭的测量端口替身。"""

    def __init__(self):
        self.close_calls = 0

    def close_all(self, *, close_rf=False):
        self.close_calls += 1
        return []


class _UnconfirmedClosePort:
    """关闭返回逐项错误的端口替身（模拟 RF 未确认关闭）。"""

    def close_all(self, *, close_rf=False):
        return [RuntimeError("RF 未确认关闭")]


def _shared_application():
    """整个模块共用一个 QApplication。

    重建应用会再次 ``setStyleSheet``，而 Qt 会给当时存活的每个控件重新 polish；
    多个测试类各建一次应用时，第二类要为上一类留下的窗口付出整段重样式开销。
    """
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
    """关闭并销毁窗口，别把上百个控件留在应用里。

    ``close()`` 只隐藏窗口：C++ 对象仍在 ``QApplication.allWidgets()`` 里，之后每次
    全应用 ``setStyleSheet`` / ``processEvents`` 都要为它付代价。用例之间累积几百个
    这样的窗口时，同一个离屏用例会从毫秒级涨到秒级。
    """
    from PySide6.QtCore import QCoreApplication, QEvent

    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@unittest.skipUnless(gui_dependencies_available(), "当前环境缺少完整 GUI 依赖")
class MainWindowCoordinatorFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = _shared_application()

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.paths = PathService(self.root)
        self.config_path = self.paths.config_file
        self.config_path.write_text(
            json.dumps(_fully_valid_gui_config()), encoding="utf-8"
        )

    def _silence_dialogs(self):
        """离屏环境里任何模态对话框都会永久阻塞，这里统一替身并记录调用。"""
        from PySide6.QtWidgets import QMessageBox

        dialogs = {}
        for name in ("critical", "warning", "information"):
            patcher = patch(f"presentation.qt.main_window.QMessageBox.{name}")
            dialogs[name] = patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch(
            "presentation.qt.main_window.QMessageBox.question",
            return_value=QMessageBox.Yes,
        )
        dialogs["question"] = patcher.start()
        self.addCleanup(patcher.stop)
        return dialogs

    def _window(self):
        from presentation.qt.main_window import MainWindow

        # 构造窗口本身不应弹窗；构造后再安装对话框替身，保证失败路径可断言。
        window = MainWindow(paths=self.paths)
        self.addCleanup(_destroy_window, window)
        return window

    def _scan_and_select(self, window):
        """在仪表配置页完成一次确定性的模拟查找，并勾选一台设备。

        窗口默认的扫描器会读临时目录里的设备表；这里换成集成测试使用的模拟设备表，
        使“查找 → 勾选 → 下一步”的整条页面流程可以离屏复现，不依赖现场网络。
        """
        from tests.integration.device_discovery.support import scanner_factory

        page = window.connection_page
        panel = page.discovery_panel
        panel.scanner_factory = scanner_factory
        panel.request_scan()
        self.assertIsNotNone(panel.worker)
        panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()
        self.assertIsNone(panel.worker)
        panel.wait_stack.finish_cycle()
        self.app.processEvents()
        self.app.processEvents()
        if panel.deal_animation.is_running():
            panel.deal_animation.finish()
            self.app.processEvents()
        for card in panel.cards():
            if card.is_checked():
                panel.set_card_checked(card.data.heading, False)
        heading = next(
            card.data.heading
            for card in panel.cards()
            if card.checkable and card.data.role == "信号发生器"
        )
        panel.set_card_checked(heading, True)
        self.app.processEvents()
        return panel

    def _finalize_with_fake_worker(self, window):
        """走真实最终化事务，只把连接 worker 换成不打开设备的替身。"""
        prepared = SimpleNamespace(
            run_id="20261104T010000Z_final001",
            run_directory=self.root / "runs" / "20261104T010000Z_final001",
            run_repository=RunRepository(paths=self.paths),
            hardware_connection_path=lambda: self.root / "frozen-hardware.json",
        )
        window.coordinator._instrument_worker_factory = _RecordingConnectionWorker
        with patch(
            "presentation.qt.application_coordinator.prepare_configuration",
            return_value=prepared,
        ):
            window._finalize_run()
        return prepared

    def _prepare_and_commit(self, window):
        """走真实事务步骤冻结并提交运行（用替身端口代替硬件连接）。"""
        prepared = window.coordinator.prepare_pending_run()
        self.assertIsNotNone(prepared)
        self.assertTrue(window.coordinator.commit_finalization(_ConnectedPort()))
        return prepared

    def test_window_exposes_the_coordinator_state_and_initial_run_status(self):
        self._silence_dialogs()
        window = self._window()

        self.assertIs(window.coordinator.gui_data_service, window.gui_data_service)
        self.assertIs(window.coordinator.controller, window.measurement_controller)
        self.assertIs(window.config, window.coordinator.config)
        self.assertIsNone(window.prepared_run)
        self.assertEqual(
            window.config_page.run_status_label.text(), "当前运行: 未创建"
        )

    def test_a_10_configuration_opens_both_pages_read_only(self):
        """步骤 13：1.0 编辑文档只读并提示迁移；两条编辑入口都不能写盘。"""
        self._silence_dialogs()
        config = _fully_valid_gui_config()
        config["schema_version"] = "1.0"
        self.config_path.write_text(json.dumps(config), encoding="utf-8")
        window = self._window()

        # 构造即同步：不经过欢迎页也必须已经只读，避免出现可编辑窗口。
        self.assertIn("迁移器", window.config_page.read_only_reason)
        self.assertIn("迁移器", window.power_page.read_only_reason)

        window._enter_test_mode(False)
        window._open_configuration_page()

        self.assertIn("迁移器", window.config_page.read_only_reason)
        self.assertIn("迁移器", window.power_page.read_only_reason)
        self.assertFalse(window.config_page.read_only_notice.isHidden())
        self.assertFalse(window.power_page.read_only_notice.isHidden())
        self.assertFalse(window.config_page.save_config_btn.isEnabled())
        self.assertFalse(window.config_page.finalize_run_btn.isEnabled())
        self.assertFalse(window.power_page.power_section.isEnabled())
        self.assertTrue(window.config_page.return_welcome_btn.isEnabled())
        # 配置页被登记成协调器的窄接口，而不是控件宿主。
        self.assertIs(window.coordinator.config_page, window.config_page)

    def test_finalize_enters_the_workspace_only_after_the_connection_succeeds(self):
        """最终按钮承诺“连接仪器”：运行冻结后还要等连接成功才离开参数页。"""
        self._silence_dialogs()
        window = self._window()
        window._enter_test_mode(False)

        prepared = self._finalize_with_fake_worker(window)

        self.assertIs(window.coordinator.state, RunFlowState.FINALIZING)
        self.assertIsNone(window.prepared_run, "待提交运行不能提前成为当前运行")
        self.assertIs(window.view_stack.currentWidget(), window.connection_page)
        self.assertFalse(window.config_page.finalize_run_btn.isEnabled(), "连接中不能重复提交")

        port = _ConnectedPort()
        window.on_instrument_controller_ready(port)
        window.on_instrument_connected()

        self.assertIs(window.prepared_run, prepared)
        self.assertIs(window.coordinator.instrument_ctrl, port)
        self.assertIs(window.coordinator.state, RunFlowState.CONNECTED)
        self.assertIs(window.view_stack.currentWidget(), window.workspace_page)
        self.assertTrue(window.config_page.finalize_run_btn.isEnabled())

    def test_finalize_stays_on_the_configuration_page_when_the_connection_fails(self):
        self._silence_dialogs()
        window = self._window()
        window._enter_test_mode(False)
        self._finalize_with_fake_worker(window)

        window.on_instrument_error("仪器连接失败")

        # 失败补偿：待提交运行被放弃，当前运行不会被半提交为“已创建但无端口”。
        self.assertIsNone(window.prepared_run)
        self.assertIsNone(window.coordinator._pending_run)
        self.assertIs(window.coordinator.state, RunFlowState.CONFIGURING)
        self.assertIs(window.view_stack.currentWidget(), window.connection_page)
        self.assertTrue(window.config_page.finalize_run_btn.isEnabled(), "失败后允许重试")

    def test_user_stop_compensates_the_pending_run_without_navigating(self):
        self._silence_dialogs()
        window = self._window()
        window._enter_test_mode(False)
        self._finalize_with_fake_worker(window)

        window.on_instrument_stopped("用户取消")

        self.assertIsNone(window.prepared_run)
        self.assertIsNone(window.coordinator._pending_run)
        self.assertIs(window.coordinator.state, RunFlowState.CONFIGURING)
        self.assertIs(window.view_stack.currentWidget(), window.connection_page)
        self.assertTrue(window.config_page.finalize_run_btn.isEnabled())

    def test_failed_connection_removes_the_unstarted_run_directory(self):
        self._silence_dialogs()
        window = self._window()
        window._enter_test_mode(False)

        prepared = window.coordinator.prepare_pending_run()
        self.assertIsNotNone(prepared)
        self.assertTrue(prepared.run_directory.is_dir())

        window.coordinator.fail_finalization("电源型号不符")

        # 阶段三：连接失败不留下可被误认为有效运行的目录。
        self.assertFalse(prepared.run_directory.exists())
        self.assertIsNone(window.prepared_run)
        self.assertIsNone(window.coordinator._pending_run)
        self.assertIs(window.coordinator.state, RunFlowState.CONFIGURING)

    def test_abandoning_a_started_run_is_refused_and_keeps_the_directory(self):
        self._silence_dialogs()
        window = self._window()
        window._enter_test_mode(False)

        prepared = window.coordinator.prepare_pending_run()
        self.assertIsNotNone(prepared)
        repository = RunRepository(paths=self.paths)
        manifest = repository.load_manifest(prepared.run_id).with_status(
            RunStatus.RUNNING
        )
        repository.update_manifest(prepared.run_id, manifest)

        self.assertFalse(window.coordinator.abandon_pending_run())

        # 阶段出口：已进入测量的运行永远不会被放弃流程删除。
        self.assertTrue(prepared.run_directory.is_dir())
        self.assertTrue(window.coordinator.has_pending_run())
        self.assertIs(window.coordinator.state, RunFlowState.MANUAL_CLEANUP_REQUIRED)

    def test_user_stop_with_unconfirmed_cleanup_requires_manual_cleanup(self):
        dialogs = self._silence_dialogs()
        window = self._window()
        window._enter_test_mode(False)
        self._finalize_with_fake_worker(window)
        window.coordinator.instrument_worker.unreleased_port = _UnconfirmedClosePort()

        window.on_instrument_stopped("用户取消")

        # 连接线程未能确认关闭端口：必须进入人工清理，且不允许再次最终化。
        self.assertIs(
            window.coordinator.state, RunFlowState.MANUAL_CLEANUP_REQUIRED
        )
        self.assertTrue(window.coordinator.has_unreleased_ports())
        self.assertIs(window.view_stack.currentWidget(), window.connection_page)
        self.assertTrue(window.config_page.finalize_run_btn.isEnabled())
        self.assertTrue(dialogs["warning"].called, "清理未完成必须提示用户")

    def test_finished_event_does_not_enter_the_workspace_before_commit(self):
        self._silence_dialogs()
        window = self._window()
        window._enter_test_mode(False)
        self._finalize_with_fake_worker(window)

        window.on_instrument_connected()

        # 只有 result 已经原子提交后才导航；单独的完成事件不能宣称成功。
        self.assertIs(window.coordinator.state, RunFlowState.FINALIZING)
        self.assertIs(window.view_stack.currentWidget(), window.connection_page)
        self.assertTrue(window.config_page.finalize_run_btn.isEnabled())

    def test_finalize_restores_the_page_when_the_connection_cannot_start(self):
        self._silence_dialogs()
        window = self._window()
        window._enter_test_mode(False)

        with patch.object(
            window.coordinator, "finalize_configuration", return_value=False
        ):
            window._finalize_run()

        self.assertIs(window.view_stack.currentWidget(), window.connection_page)
        self.assertTrue(window.config_page.finalize_run_btn.isEnabled())

    def test_finalize_signal_routes_to_the_coordinator_once(self):
        """参数页信号必须由主窗口转给唯一最终化入口，页面不直接调用协调器。"""
        self._silence_dialogs()
        window = self._window()
        window._enter_test_mode(False)

        with patch.object(
            window.coordinator, "finalize_configuration", return_value=False
        ) as finalize:
            window.config_page.finalize_run_requested.emit()

        finalize.assert_called_once()

    def test_startup_shows_welcome_and_selection_applies_mode(self):
        self._silence_dialogs()
        window = self._window()

        self.assertIs(window.view_stack.currentWidget(), window.welcome_page)
        window._enter_test_mode(True)
        # 选好测试模式后先进“仪器连接配置”页（整屏，不占标签），再进仪器配置页。
        self.assertIs(window.view_stack.currentWidget(), window.connection_page)
        self.assertTrue(window.config_page.driver_mode_requested())
        self.assertTrue(window.config["driver_mode"]["enabled"])

        self._scan_and_select(window)
        window.connection_page.next_btn.click()
        self.assertIs(window.view_stack.currentWidget(), window.rack_page)
        self.assertEqual(
            [label for label, _ in window.rack_page.board._amplifiers()],
            ["Driver", "DUT"],
            "有驱放测试的链路里 Driver 和 DUT 都要在",
        )
        window.rack_page.finish_transition()
        window.rack_page.next_btn.click()
        # 排布页先把除电源卡片外的内容淡出，再进整屏电源配置页展开通道卡片。
        window.rack_page.finish_transition()
        self.assertIs(window.view_stack.currentWidget(), window.power_page)
        self.assertNotIn(
            "电源配置",
            [
                window.tab_widget.tabText(index)
                for index in range(window.tab_widget.count())
            ],
            "电源配置已经是整屏步骤，不再是标签页",
        )
        window.power_page.finish_card_morph()
        self.assertTrue(window.power_page.intro_visible())

        # 电源配置的下一步才进入工作区的“仪器配置”标签页。
        window.power_page.next_btn.click()
        self.assertIs(window.view_stack.currentWidget(), window.workspace_page)
        self.assertIs(window.tab_widget.currentWidget(), window.config_page)

    def test_first_entry_to_the_power_step_starts_from_default_channels(self):
        """上次保存的电源配置不再带入页面：首次进入全是 OFF / 0 / 2s。"""
        from presentation.qt.power_card import DEFAULT_POWER_ON_DELAY_S

        self._silence_dialogs()
        window = self._window()
        window._enter_test_mode(False)
        self.assertEqual(
            window.power_page.card_wall.supply_card("DP8B1519").channel_mode("CH1"),
            "dut",
            "用例前提：保存的配置里 CH1/CH2 都是 DUT",
        )

        window._open_configuration_page()
        window.power_page.finish_card_morph()

        card = window.power_page.card_wall.supply_card("DP8B1519")
        self.assertIsNotNone(card)
        for channel in ("CH1", "CH2"):
            with self.subTest(channel=channel):
                self.assertEqual(card.channel_mode(channel), "off")
                self.assertEqual(
                    card.value_field(channel, "voltage").value(), 0.0
                )
                self.assertEqual(
                    card.value_field(channel, "current").value(), 0.0
                )
                self.assertFalse(
                    card.protection_check(channel, "voltage").isChecked()
                )
                self.assertFalse(
                    card.protection_check(channel, "current").isChecked()
                )
                self.assertAlmostEqual(
                    card.channel_power_delay(channel),
                    DEFAULT_POWER_ON_DELAY_S,
                )
        self.assertEqual(window.power_page.read_drain_channels(), [])
        self.assertEqual(
            window.power_page.build_config()["power_supply_assignment"][
                "dut_amplifier"
            ]["power_plan"]["power_sequence"],
            [],
        )

    def test_connection_page_is_not_a_tab_and_hides_the_status_panel(self):
        """仪器连接配置页占满整个界面：既不在标签栏里，也不显示进度与日志。"""
        self._silence_dialogs()
        window = self._window()

        window._enter_test_mode(False)

        pages = [window.tab_widget.widget(index) for index in range(window.tab_widget.count())]
        self.assertNotIn(window.connection_page, pages)
        self.assertTrue(window.status_panel.isHidden())

    def test_previous_step_returns_to_the_connection_page(self):
        """仪器配置页的“上一步”回到仪器连接配置页，“下一步”再回来。"""
        self._silence_dialogs()
        window = self._window()

        window._enter_test_mode(True)
        self._scan_and_select(window)
        window.connection_page.next_btn.click()
        self.assertIs(window.view_stack.currentWidget(), window.rack_page)
        window.rack_page.finish_transition()
        window.rack_page.next_btn.click()
        window.rack_page.finish_transition()
        self.assertIs(window.view_stack.currentWidget(), window.power_page)
        window.power_page.finish_card_morph()
        window.power_page.next_btn.click()
        self.assertIs(window.view_stack.currentWidget(), window.workspace_page)

        window.config_page.previous_step_btn.click()
        self.assertIs(window.view_stack.currentWidget(), window.connection_page)

        # 回到仪表配置页会清空上一次结果：必须重新查找并选择，不能沿用旧绑定直接推进。
        self._scan_and_select(window)
        window.connection_page.next_btn.click()
        self.assertIs(window.view_stack.currentWidget(), window.rack_page)
        window.rack_page.finish_transition()
        window.rack_page.next_btn.click()
        window.rack_page.finish_transition()
        self.assertIs(window.view_stack.currentWidget(), window.power_page)
        window.power_page.finish_card_morph()
        window.power_page.next_btn.click()
        self.assertIs(window.view_stack.currentWidget(), window.workspace_page)
        self.assertIs(window.tab_widget.currentWidget(), window.config_page)

    def test_rack_previous_step_returns_to_the_same_selection_result(self):
        """仪器排布页的“上一步”回到本次选择结果，卡片与勾选都还在。"""
        self._silence_dialogs()
        window = self._window()

        window._enter_test_mode(True)
        panel = self._scan_and_select(window)
        checked = {card.data.heading for card in panel.cards() if card.is_checked()}
        self.assertTrue(checked, "用例前提：本次结果里至少勾选一台设备")

        window.connection_page.next_btn.click()
        self.assertIs(window.view_stack.currentWidget(), window.rack_page)
        window.rack_page.finish_transition()

        window.rack_page.back_btn.click()
        # 反向动画播完前仍停在排布页。
        self.assertIs(window.view_stack.currentWidget(), window.rack_page)
        window.rack_page.finish_transition()

        self.assertIs(window.view_stack.currentWidget(), window.connection_page)
        self.assertEqual(panel.box_state(), "results", "返回后不能退回“查找设备”画面")
        self.assertTrue(panel.cards(), "本次扫描出的卡片要留在结果区")
        self.assertEqual(
            {card.data.heading for card in panel.cards() if card.is_checked()},
            checked,
            "返回后勾选保持原样，用户可以直接改选再进入排布",
        )

    def test_two_supplies_in_one_run_are_both_frozen_with_their_identity(self):
        """步骤 10：DUT 与驱动功放各用一台电源时，两台都进运行快照（各带身份）。

        分配下拉框只选电源（步骤 9 的页面形状），通道是这台电源的固定 CH1/CH2；
        因此这里让驱动功放走``DP8B0885``、DUT 走 ``DP8B1519``，快照里两条独立条目。
        """
        self._silence_dialogs()
        saved = _fully_valid_gui_config()
        saved["driver_mode"] = {"enabled": True}
        saved["instruments"]["power_supplies"]["DP8B0885"] = {
            "label": "DP8B0885",
            "model": "DP832A",
            "full_code": "DP8B174400885",
            "device_key": "rigol technologies:dp8b174400885",
            "address": "sim::ps2",
            "enabled": True,
            "channels": {"CH1": {"voltage": {"value": 5.0}, "current": {"value": 0.5}}},
        }
        saved["device_bindings"]["power_supplies"]["DP8B0885"] = {
            "device_key": "rigol technologies:dp8b174400885",
            "full_code": "DP8B174400885",
            "model": "DP832A",
            "visa_address": "sim::ps2",
            "source": "scan",
        }
        # 页面的连接只来自设备绑定：把 DUT 那台的地址落到绑定里，两台都能写进快照。
        saved["device_bindings"]["power_supplies"]["DP8B1519"]["visa_address"] = "sim::ps1"
        saved["device_bindings"]["power_supplies"]["DP8B1519"]["model"] = "DP832A"
        saved["power_supply_assignment"]["driver_amplifier"] = {
            "power_plan": {
                "power_sequence": [
                    {"supply_id": "DP8B0885", "channel": "CH1"},
                    {"supply_id": "DP8B0885", "channel": "CH2"},
                ],
                "inter_channel_delay_s": 0.0,
            }
        }
        self.config_path.write_text(json.dumps(saved), encoding="utf-8")

        window = self._window()
        # 创建运行需要驱放模式打开（页面只在驱放模式下提交驱动功放上电计划）。
        window._enter_test_mode(True)
        prepared = self._prepare_and_commit(window)

        self.assertIsNotNone(prepared)
        mapping = prepared.configuration.run_mapping
        self.assertEqual(sorted(mapping.power_supplies), ["DP8B0885", "DP8B1519"])
        self.assertEqual(mapping.power_supplies["DP8B0885"].full_code, "DP8B174400885")
        self.assertEqual(mapping.power_supplies["DP8B0885"].visa_address, "sim::ps2")
        self.assertEqual(
            mapping.dut_power_plan["power_sequence"],
            [
                {"supply_id": "DP8B1519", "channel": "CH1"},
                {"supply_id": "DP8B1519", "channel": "CH2"},
            ],
        )

    def test_no_driver_mode_drops_a_saved_driver_plan_and_still_creates_a_run(self):
        self._silence_dialogs()
        saved = _fully_valid_gui_config()
        saved["driver_mode"] = {"enabled": True}
        saved["power_supply_assignment"]["driver_amplifier"] = {
            "power_plan": {
                "power_sequence": [{"supply_id": "DP8B1519", "channel": "CH1"}],
                "inter_channel_delay_s": 0.0,
            },
        }
        self.config_path.write_text(json.dumps(saved), encoding="utf-8")

        window = self._window()
        window._enter_test_mode(False)

        self.assertFalse(window.power_page.driver_power_plan_editor.is_active)
        self.assertEqual(
            window.power_page.build_config()["power_supply_assignment"][
                "driver_amplifier"
            ]["power_plan"]["power_sequence"],
            [],
            "关闭驱放后不再提交驱动功放计划",
        )
        prepared = self._prepare_and_commit(window)

        self.assertIsNotNone(prepared)
        self.assertFalse(prepared.configuration.test_plan.driver_enabled)

    def test_driver_mode_activates_an_empty_plan_meaning_external_power(self):
        self._silence_dialogs()
        window = self._window()

        window._enter_test_mode(True)

        self.assertTrue(window.power_page.driver_power_plan_editor.is_active)
        self.assertEqual(
            window.power_page.driver_power_plan_editor.read_plan()["power_sequence"],
            [],
            "现场配置没有本机驱动供电通道：计划留空即驱放自带供电",
        )
        self.assertIn(
            "驱动功放", window.amplifier_page.instruction_text.toPlainText()
        )

    def test_return_to_welcome_confirms_only_when_configuration_is_dirty(self):
        dialogs = self._silence_dialogs()
        window = self._window()
        window._enter_test_mode(False)

        window._return_to_welcome()
        dialogs["question"].assert_not_called()
        self.assertIs(window.view_stack.currentWidget(), window.welcome_page)

        window._enter_test_mode(False)
        window.config_page.freq_edit.setText("[9.0]")
        window._return_to_welcome()
        dialogs["question"].assert_called_once()
        self.assertIs(window.view_stack.currentWidget(), window.welcome_page)

    def test_no_driver_mode_hides_the_driver_mapping_tab(self):
        self._silence_dialogs()
        window = self._window()
        index = window._driver_mapping_tab_index
        visible_titles = lambda: [
            window.tab_widget.tabText(position)
            for position in range(window.tab_widget.count())
            if window.tab_widget.isTabVisible(position)
        ]

        window._enter_test_mode(True)
        self.assertTrue(window.tab_widget.isTabVisible(index))
        self.assertIn("驱动映射", visible_titles())

        window._enter_test_mode(False)
        self.assertFalse(window.tab_widget.isTabVisible(index))
        # 页面实例保留并继续绑定 controller，切回有驱放模式时不需要重建窗口。
        self.assertIn("driver_mapping", window.pages)
        self.assertNotIn("驱动映射", visible_titles())
        self.assertIn("线损测量", visible_titles())
        self.assertIn("功放测试", visible_titles())

    def test_configuration_page_hides_progress_and_log_panel(self):
        """仪器配置页只做配置与连接：右侧进度与实时日志不出现，切页后再回来。"""
        self._silence_dialogs()
        window = self._window()
        window._enter_test_mode(True)

        self.assertIs(window.tab_widget.currentWidget(), window.config_page)
        self.assertTrue(window.status_panel.isHidden())

        # 面板隐藏期间仍是全部日志的接收方：配置页的日志不会因为看不见而丢。
        window.add_log_message("配置页日志")
        self.assertIn("配置页日志", window.log_text.toPlainText())

        window.tab_widget.setCurrentIndex(
            window.tab_widget.indexOf(window.pages["cable_loss"])
        )
        self.assertFalse(window.status_panel.isHidden())

        window.tab_widget.setCurrentIndex(window.tab_widget.indexOf(window.config_page))
        self.assertTrue(window.status_panel.isHidden())

    def test_config_page_save_signal_writes_the_official_configuration(self):
        self._silence_dialogs()
        window = self._window()
        self.config_path.unlink()

        window.save_config()

        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertIn("test_plan", saved)
        self.assertIn("run_mapping", saved)
        self.assertIn("test_plan", window.config)

    def test_power_configuration_page_is_a_full_screen_step_in_the_shared_editor(self):
        self._silence_dialogs()
        window = self._window()

        self.assertIs(window.power_page, window.configuration_editor.power_page)
        self.assertNotIn(window.power_page, [
            window.tab_widget.widget(index)
            for index in range(window.tab_widget.count())
        ])
        self.assertNotIn("power_configuration", window.pages)
        self.assertEqual(
            window.configuration_editor.pages(),
            [window.config_page, window.power_page],
        )
        self.assertIs(
            window.power_page.parent(),
            window.view_stack,
            "电源配置页挂在整屏页面堆栈里",
        )

    def test_unsaved_power_edit_survives_step_switching_and_saves_once(self):
        """电源页与工作区往返不丢未保存修改；一次保存把整份草稿写入同一文件。"""
        self._silence_dialogs()
        window = self._window()
        window._enter_test_mode(False)
        window._open_configuration_page()
        window.power_page.finish_card_morph()
        channel = window.power_page.power_config_widgets["DP8B1519"]["channels"]["CH1"]
        channel["voltage"].setValue(7.5)
        self.assertTrue(window.configuration_editor.is_dirty())

        window.power_page.next_btn.click()
        self.app.processEvents()
        self.assertIs(window.view_stack.currentWidget(), window.workspace_page)
        self.assertEqual(
            window.power_page.power_config_widgets["DP8B1519"]["channels"]["CH1"][
                "voltage"
            ].value(),
            7.5,
        )

        window.save_config()

        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(
            saved["instruments"]["power_supplies"]["DP8B1519"]["channels"]["CH1"][
                "voltage"
            ]["value"],
            7.5,
        )
        self.assertFalse(window.configuration_editor.is_dirty())

    def test_applying_devices_refreshes_the_power_page_options(self):
        """绑定回写刷新电源页；首次进入电源页也不能把新绑定退回旧配置。

        回归：排布三台电源后点击“下一步”，首次进入的字段重置曾经用页面里尚未更新
        的配置副本重建绑定，结果只剩保存配置里的那一台。
        """
        from application.device_binding import ApplyDeviceSelectionRequest

        self._silence_dialogs()
        window = self._window()
        supplies = (
            ("DP8B1519", "DP8B214901519"),
            ("DP8B0885", "DP8B174400885"),
            ("DP8B1234", "DP8B174401234"),
        )

        result = window.config_page.apply_device_selection(
            ApplyDeviceSelectionRequest.from_assignments(
                [
                    {
                        "role": "power_supply",
                        "supply_id": supply_id,
                        "address": f"TCPIP0::192.168.1.{index}::inst0::INSTR",
                        "vendor": "RIGOL TECHNOLOGIES",
                        "model": "DP832A",
                        "full_code": full_code,
                        "device_key": f"rigol technologies:{full_code.casefold()}",
                    }
                    for index, (supply_id, full_code) in enumerate(supplies, 1)
                ]
            )
        )

        self.assertTrue(result.ok)
        expected = [supply_id for supply_id, _full_code in supplies]
        self.assertEqual(window.power_page.get_enabled_power_supplies(), expected)
        for supply_id in expected:
            self.assertIn(supply_id, window.power_page.power_config_widgets)

        window.power_page.enter()
        window.power_page.set_rack_slots(
            {supply_id: index for index, supply_id in enumerate(expected)}
        )

        self.assertEqual(
            [cell.supply_id for cell in window.power_page.card_wall.cards()],
            expected,
        )

    def test_rebinding_power_supplies_keeps_unsaved_power_edits(self):
        """绑定回写是派生刷新：仍有效的未保存通道/计划/漏极编辑必须保留。"""
        from application.device_binding import ApplyDeviceSelectionRequest

        self._silence_dialogs()
        window = self._window()
        window._enter_test_mode(False)
        power = window.power_page
        power.power_config_widgets["DP8B1519"]["channels"]["CH1"]["voltage"].setValue(
            7.5
        )
        power.power_config_widgets["DP8B1519"]["channels"]["CH1"][
            "current_protection_enabled"
        ].setChecked(True)
        power.power_config_widgets["DP8B1519"]["channels"]["CH1"][
            "power_on_delay"
        ].setValue(1.5)
        expected_plan = power.dut_power_plan_editor.read_plan()
        expected_drain = power.read_drain_channels()
        self.assertTrue(window.configuration_editor.is_dirty())

        window.config_page.apply_device_selection(
            ApplyDeviceSelectionRequest.from_assignments(
                [
                    {
                        "role": "power_supply",
                        "supply_id": "DP8B1519",
                        "address": "TCPIP0::192.168.1.102::inst0::INSTR",
                        "vendor": "RIGOL TECHNOLOGIES",
                        "model": "DP832A",
                        "full_code": "DP8B214901519",
                        "device_key": "rigol technologies:dp8b214901519",
                    },
                    {
                        "role": "power_supply",
                        "supply_id": "DP8B0885",
                        "address": "TCPIP0::192.168.1.108::inst0::INSTR",
                        "vendor": "RIGOL TECHNOLOGIES",
                        "model": "DP832A",
                        "full_code": "DP8B174400885",
                        "device_key": "rigol technologies:dp8b174400885",
                    },
                ]
            )
        )

        channel = power.power_config_widgets["DP8B1519"]["channels"]["CH1"]
        self.assertEqual(channel["voltage"].value(), 7.5)
        self.assertTrue(channel["current_protection_enabled"].isChecked())
        self.assertEqual(power.dut_power_plan_editor.read_plan(), expected_plan)
        self.assertEqual(power.read_drain_channels(), expected_drain)
        self.assertEqual(
            power.power_config_widgets["DP8B1519"]["channels"]["CH1"][
                "power_on_delay"
            ].value(),
            1.5,
            "绑定回写不能把卡面尚未提交的通道间隔覆盖掉",
        )
        self.assertEqual(
            power.get_enabled_power_supplies(), ["DP8B1519", "DP8B0885"]
        )

        # 旧电源失去绑定：未提交的编辑仍留在页面上，引用保持空选择并交给校验报告。
        window.config_page.apply_device_selection(
            ApplyDeviceSelectionRequest.from_assignments(
                [
                    {
                        "role": "power_supply",
                        "supply_id": "DP8B0885",
                        "address": "TCPIP0::192.168.1.108::inst0::INSTR",
                        "vendor": "RIGOL TECHNOLOGIES",
                        "model": "DP832A",
                        "full_code": "DP8B174400885",
                        "device_key": "rigol technologies:dp8b174400885",
                    }
                ]
            )
        )
        channel = power.power_config_widgets["DP8B1519"]["channels"]["CH1"]
        self.assertEqual(channel["voltage"].value(), 7.5)
        self.assertIn(
            ("DP8B1519", "CH1"),
            power.dut_power_plan_editor.routes(),
            "失去绑定的电源路由仍留在计划里，交给校验报告",
        )
        failures = []
        power.validation_failed.connect(failures.append)

        self.assertFalse(power.validate())
        self.assertIn("没有已应用的设备", "；".join(failures))

    def test_driver_mode_switch_updates_both_configuration_pages(self):
        self._silence_dialogs()
        window = self._window()

        window._enter_test_mode(False)
        self.assertFalse(window.config_page.driver_mode_requested())
        self.assertFalse(window.power_page.driver_power_plan_editor.is_active)

        window._enter_test_mode(True)
        self.assertTrue(window.config_page.driver_mode_requested())
        self.assertTrue(window.power_page.driver_power_plan_editor.is_active)

    def test_create_run_freezes_the_run_and_updates_the_page(self):
        self._silence_dialogs()
        window = self._window()

        prepared = self._prepare_and_commit(window)

        self.assertIsNotNone(prepared)
        self.assertIs(window.prepared_run, prepared)
        self.assertTrue(prepared.run_directory.is_dir())
        self.assertEqual(
            window.config_page.run_status_label.text(), f"当前运行: {prepared.run_id}"
        )
        # 创建运行只冻结参数：接线不再是配置字段，冻结快照与保存的配置里都没有它。
        frozen = json.loads(prepared.config_path.read_text(encoding="utf-8"))
        self.assertNotIn("wiring", frozen["run_mapping"])
        self.assertNotIn("wiring", window.config)

    def test_run_switch_keeps_the_current_run_when_the_old_port_cannot_be_released(self):
        dialogs = self._silence_dialogs()
        window = self._window()

        class _FailingPort:
            def __init__(self):
                self.close_calls = 0

            def close_all(self, *, close_rf=False):
                self.close_calls += 1
                return [RuntimeError("RF 未确认关闭")]

        port = _FailingPort()
        window.coordinator.instrument_ctrl = port

        prepared = window.coordinator.prepare_pending_run()

        self.assertIsNone(prepared)
        self.assertIsNone(window.prepared_run)
        self.assertEqual(port.close_calls, 1)
        self.assertTrue(window.coordinator.has_unreleased_ports())
        self.assertEqual(
            window.config_page.run_status_label.text(), "当前运行: 未创建"
        )
        dialogs["warning"].assert_called_once()

    def test_measurement_start_is_blocked_until_a_port_is_connected(self):
        dialogs = self._silence_dialogs()
        window = self._window()

        # 没有运行、也没有端口时，页面拿不到运行契约，且失败必须可见。
        self.assertIsNone(window.coordinator.resolve_measurement_run())
        self.assertFalse(window.coordinator.amplifier_driver_mode())
        dialogs["critical"].assert_called_once()

        class _Port:
            def close_all(self, *, close_rf=False):
                return []

        window.coordinator.prepare_pending_run()

        # 已冻结待提交运行但还没有提交端口：仍然拒绝开始测量。
        self.assertIsNone(window.prepared_run)
        self.assertIsNone(window.coordinator.resolve_measurement_run())

        self.assertTrue(window.coordinator.commit_finalization(_Port()))
        self.assertIs(
            window.coordinator.resolve_measurement_run(), window.prepared_run
        )


@unittest.skipUnless(gui_dependencies_available(), "当前环境缺少完整 GUI 依赖")
class MainWindowMigrationResidueFlowTests(unittest.TestCase):
    """步骤 12：驱动模式说明刷新在真实窗口里的行为。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = _shared_application()

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.paths = PathService(self.root)
        self.paths.config_file.write_text(
            json.dumps(_fully_valid_gui_config()), encoding="utf-8"
        )
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

    def _window(self):
        from presentation.qt.main_window import MainWindow

        window = MainWindow(paths=self.paths)
        self.addCleanup(_destroy_window, window)
        return window

    def test_driver_mode_switch_updates_the_amplifier_page_instruction(self):
        window = self._window()

        window.config_page.driver_mode_changed.emit(True)
        self.assertIn("驱动功放", window.amplifier_page.instruction_text.toPlainText())

        window.config_page.driver_mode_changed.emit(False)
        self.assertNotIn("驱动功放", window.amplifier_page.instruction_text.toPlainText())


if __name__ == "__main__":
    unittest.main()
