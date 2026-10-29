"""配置页设备发现面板（MVP）界面测试。

覆盖点击“获取设备”后的可见行为：

1. 扫描结果出现在表格里，已登记设备显示“设备名（测试角色）”，未登记设备标记为
   未登记，身份读不出来的地址标记为无法识别且不能勾选；地址列不再显示。
2. 测试角色来自设备表登记的 ``role``：勾选设备即可应用，不需要用户选择角色。
3. 角色重复、空扫描网段、身份缺失、设备未登记角色都给出明确提示，不静默写入配置。
"""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from domain.instruments.models import DeviceStatus, InstrumentIdentity
from infrastructure.discovery.instrument_discovery import (
    SCAN_MODE_NETWORK,
    SCAN_MODE_SIMULATED,
    DiscoveredDevice,
    InstrumentDiscoveryError,
    ScanResult,
    build_scanner,
)
from infrastructure.discovery.simulated_network import demo_idn_responses
from infrastructure.persistence.instrument_registry import InstrumentRegistry
from presentation.qt.config_page import ConfigPage
from presentation.qt.device_discovery_panel import (
    DeviceDiscoveryPanel,
    binding_device_key,
    subnet_from_address,
)
from presentation.qt.theme import apply_theme

SMW_IDN = "Rohde&Schwarz,SMW200A,1412.0000K02/105462,5.20.043.47"
DP832A_IDN = "RIGOL TECHNOLOGIES,DP832A,DP8B214901519,00.01.16"
CEYEAR_IDN = "Ceyear Technologies,4082F,QZNI000866,1.12.04"

# 设备表里登记的固定角色：界面不再让用户选择，发现结果直接按它显示与写入。
DEVICE_ROLES = {
    SMW_IDN: "signal_generator",
    DP832A_IDN: "PS3",
    CEYEAR_IDN: "spectrum_analyzer",
}


def _registry(roles=None):
    registry = InstrumentRegistry()
    for idn in (SMW_IDN, DP832A_IDN, CEYEAR_IDN):
        registry.upsert(InstrumentIdentity.from_idn(idn), role=(roles or DEVICE_ROLES)[idn])
    return registry


def _scanner_factory(mode):
    if mode == SCAN_MODE_NETWORK:
        raise AssertionError("测试不得发起真实网络扫描")
    return build_scanner(
        registry=_registry(),
        mode=SCAN_MODE_SIMULATED,
        responses=demo_idn_responses(),
        timeout_s=0.02,
    )


class SubnetSuggestionTests(unittest.TestCase):
    def test_visa_address_suggests_its_24_bit_subnet(self):
        self.assertEqual(
            subnet_from_address("TCPIP0::192.168.1.201::inst0::INSTR"), "192.168.1.0/24"
        )

    def test_missing_or_invalid_address_falls_back_to_none(self):
        for value in ("", None, "ASRL1::INSTR", 42, "TCPIP0::999.1.1.1::INSTR"):
            with self.subTest(value=value):
                self.assertIsNone(subnet_from_address(value))


class BindingKeyTests(unittest.TestCase):
    """身份键只由厂商 + 序列号决定；只有序列号时不做猜测。"""

    def test_device_key_is_used_when_present(self):
        self.assertEqual(
            binding_device_key({"device_key": "Rohde&Schwarz:105462"}),
            "rohde&schwarz:105462",
        )

    def test_vendor_and_serial_are_combined_when_key_is_missing(self):
        self.assertEqual(
            binding_device_key({"vendor": "Rohde&Schwarz", "serial": "105462"}),
            "rohde&schwarz:105462",
        )

    def test_serial_alone_or_malformed_input_yields_none(self):
        for record in ({"serial": "105462"}, {}, None, "not-a-record", {"device_key": "  "}):
            with self.subTest(record=record):
                self.assertIsNone(binding_device_key(record))


class DiscoveryPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_panel(self):
        panel = DeviceDiscoveryPanel(scanner_factory=_scanner_factory)
        panel.show()
        self.app.processEvents()
        return panel

    def run_scan(self, panel):
        panel.request_scan()
        self.assertTrue(panel.worker is not None)
        panel.worker.wait(5000)
        self.app.processEvents()
        self.assertIsNone(panel.worker)

    def test_scan_lists_known_and_unknown_devices(self):
        panel = self.make_panel()

        self.run_scan(panel)

        self.assertEqual(panel.table.rowCount(), 4)
        names = {panel.table.item(row, 1).text() for row in range(panel.table.rowCount())}
        statuses = {
            panel.table.item(row, 3).text() for row in range(panel.table.rowCount())
        }
        # 角色和设备名写在同一个单元格里，不再单独占一列。
        self.assertIn("SMW200A-1（信号发生器）", names)
        self.assertIn("DP832A-1（电源 PS3）", names)
        self.assertIn("未知设备 (N9020A)", names)
        self.assertIn("已登记", statuses)
        self.assertIn("未登记", statuses)
        self.assertTrue(panel.apply_btn.isEnabled())
        text = panel.status_label.text()
        self.assertIn("已登记 3 台", text)
        self.assertIn("其中可应用 3 台", text)
        self.assertIn("未登记 1 台", text)

    def test_table_does_not_show_addresses(self):
        """IP 只是本次可达的地址，不是用户需要关心的信息：界面不展示。"""
        panel = self.make_panel()

        self.run_scan(panel)

        for row in range(panel.table.rowCount()):
            for column in range(panel.table.columnCount()):
                self.assertNotIn("192.168.1.", panel.table.item(row, column).text())
        headers = [
            panel.table.horizontalHeaderItem(column).text()
            for column in range(panel.table.columnCount())
        ]
        self.assertNotIn("地址", headers)
        self.assertNotIn("测试角色", headers)

    def test_table_width_follows_column_content(self):
        """设备列不再被拉伸吃掉整行宽度：表格宽度贴着列内容。"""
        panel = self.make_panel()

        self.run_scan(panel)
        self.app.processEvents()

        self.assertLess(panel.table.width(), panel.width())
        self.assertLessEqual(panel.table.width(), panel.table.maximumWidth())
        # 设备列按内容取宽，不会占满剩下的横向空间。
        self.assertLess(
            panel.table.columnWidth(1),
            panel.table.width() - panel.table.columnWidth(0),
        )

    def test_unknown_device_keeps_its_serial_visible(self):
        panel = self.make_panel()

        self.run_scan(panel)

        rows = {
            panel.table.item(row, 1).text(): row
            for row in range(panel.table.rowCount())
        }
        unknown_row = rows["未知设备 (N9020A)"]
        self.assertEqual(panel.table.item(unknown_row, 2).text(), "MY51234567")
        # 型号与厂商在悬浮提示里，避免窄栏挤压设备列。
        self.assertIn("N9020A / Keysight Technologies", panel.table.item(unknown_row, 1).toolTip())
        self.assertIn("MY51234567", panel.table.item(unknown_row, 1).toolTip())
        # 未登记设备没有登记角色，不能勾选应用。
        self.assertFalse(
            panel.table.item(unknown_row, 0).flags() & Qt.ItemIsUserCheckable
        )
        self.assertEqual(panel.table.item(unknown_row, 3).text(), "未登记")

    def test_registered_device_without_role_cannot_be_checked(self):
        """设备表没登记角色的设备：能看到，但不能勾选，也不能被应用。"""
        def factory(_mode):
            registry = InstrumentRegistry()
            registry.upsert(InstrumentIdentity.from_idn(SMW_IDN), role=None)
            return build_scanner(
                registry=registry,
                mode=SCAN_MODE_SIMULATED,
                responses={("192.168.1.201", 5025): SMW_IDN},
                timeout_s=0.02,
            )

        panel = DeviceDiscoveryPanel(scanner_factory=factory)
        panel.show()
        self.app.processEvents()
        self.run_scan(panel)

        self.assertEqual(panel.table.rowCount(), 1)
        self.assertEqual(panel.table.item(0, 1).text(), "SMW200A-1")
        self.assertEqual(panel.table.item(0, 3).text(), "未分配角色")
        self.assertFalse(panel.table.item(0, 0).flags() & Qt.ItemIsUserCheckable)
        self.assertFalse(panel.apply_btn.isEnabled())
        self.assertIn("未分配角色 1 台", panel.status_label.text())

    def test_checking_a_device_is_enough_without_choosing_a_role(self):
        """角色来自设备表：勾选后直接应用，不会出现“未选择测试角色”。"""
        panel = self.make_panel()
        self.run_scan(panel)
        rows = {
            panel.table.item(row, 1).text(): row
            for row in range(panel.table.rowCount())
        }
        panel.table.item(rows["SMW200A-1（信号发生器）"], 0).setCheckState(Qt.Checked)
        applied = []
        panel.apply_requested.connect(applied.append)

        panel.request_apply()

        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0][0]["role"], "signal_generator")

    def test_duplicate_roles_are_rejected(self):
        """两台设备在设备表里登记成同一角色时整批拒绝，而不是静默覆盖。"""
        second_smw = "Rohde&Schwarz,SMW200A,1412.0000K02/200001,5.20.043.47"

        def factory(_mode):
            registry = InstrumentRegistry()
            registry.upsert(InstrumentIdentity.from_idn(SMW_IDN), role="signal_generator")
            registry.upsert(InstrumentIdentity.from_idn(second_smw), role="signal_generator")
            return build_scanner(
                registry=registry,
                mode=SCAN_MODE_SIMULATED,
                responses={
                    ("192.168.1.201", 5025): SMW_IDN,
                    ("192.168.1.203", 5025): second_smw,
                },
                timeout_s=0.02,
            )

        panel = DeviceDiscoveryPanel(scanner_factory=factory)
        panel.show()
        self.app.processEvents()
        self.run_scan(panel)
        for row in range(panel.table.rowCount()):
            panel.table.item(row, 0).setCheckState(Qt.Checked)
        applied = []
        panel.apply_requested.connect(applied.append)

        panel.request_apply()

        self.assertEqual(applied, [])
        self.assertIn("重复选择", panel.status_label.text())

    def test_apply_reports_selected_role_and_identity(self):
        panel = self.make_panel()
        self.run_scan(panel)
        rows = {
            panel.table.item(row, 1).text(): row
            for row in range(panel.table.rowCount())
        }
        signal_row = rows["SMW200A-1（信号发生器）"]
        panel.table.item(signal_row, 0).setCheckState(Qt.Checked)
        applied = []
        panel.apply_requested.connect(applied.append)

        panel.request_apply()

        assignment = applied[0][0]
        self.assertEqual(assignment["role"], "signal_generator")
        self.assertEqual(assignment["serial"], "105462")
        self.assertEqual(assignment["address"], "TCPIP0::192.168.1.201::inst0::INSTR")
        self.assertTrue(assignment["known"])

    def test_invalid_subnet_reports_failure_without_crashing(self):
        panel = self.make_panel()
        panel.subnet_edit.setText("not-a-subnet")

        panel.request_scan()
        panel.worker.wait(5000)
        self.app.processEvents()

        self.assertIn("扫描失败", panel.status_label.text())
        self.assertEqual(panel.table.rowCount(), 0)
        self.assertFalse(panel.apply_btn.isEnabled())

    def test_shutdown_without_running_scan_is_ok(self):
        panel = self.make_panel()
        self.assertTrue(panel.shutdown())

    def test_scan_preselects_devices_bound_in_configuration(self):
        panel = self.make_panel()
        panel.set_device_bindings(
            {"signal_generator": {"serial": "105462", "device_key": "rohde&schwarz:105462"}}
        )

        self.run_scan(panel)

        rows = {
            panel.table.item(row, 1).text(): row
            for row in range(panel.table.rowCount())
        }
        signal_row = rows["SMW200A-1（信号发生器）"]
        self.assertEqual(panel.table.item(signal_row, 0).checkState(), Qt.Checked)
        self.assertIn("预选", panel.status_label.text())
        # 其他行不因预选被勾上：用户仍可自由改。
        self.assertEqual(
            panel.table.item(rows["DP832A-1（电源 PS3）"], 0).checkState(), Qt.Unchecked
        )

    def test_preselection_matches_by_device_key_even_when_ip_changes(self):
        panel = self.make_panel()
        panel.set_device_bindings(
            {"spectrum_analyzer": {"device_key": "ceyear technologies:qzni000866"}}
        )
        # 换一个网段再扫描，设备 IP 变化但身份不变。
        panel.subnet_edit.setText("192.168.1.0/24")

        self.run_scan(panel)

        rows = {
            panel.table.item(row, 1).text(): row
            for row in range(panel.table.rowCount())
        }
        spectrum_row = rows["4082F-1（频谱分析仪）"]
        self.assertEqual(panel.table.item(spectrum_row, 0).checkState(), Qt.Checked)
        self.assertNotIn("配置里是", panel.status_label.text())

    def test_preselection_reports_a_role_change_instead_of_silently_moving_the_device(self):
        """配置里的旧角色与设备表不一致时说明清楚：角色以设备表为准。"""
        panel = self.make_panel()
        panel.set_device_bindings(
            {"PS1": {"device_key": "rigol technologies:dp8b214901519"}}
        )

        self.run_scan(panel)

        rows = {
            panel.table.item(row, 1).text(): row
            for row in range(panel.table.rowCount())
        }
        row = rows["DP832A-1（电源 PS3）"]
        self.assertEqual(panel.table.item(row, 0).checkState(), Qt.Checked)
        text = panel.status_label.text()
        self.assertIn("角色以设备表为准", text)
        self.assertIn("配置里是 电源 PS1", text)

    def test_preselection_uses_vendor_and_serial_when_key_is_missing(self):
        """旧配置只有厂商+序列号时重新拼出同一个身份键，仍然认得出设备。"""
        panel = self.make_panel()
        panel.set_device_bindings(
            {"signal_generator": {"vendor": "Rohde&Schwarz", "serial": "105462"}}
        )

        self.run_scan(panel)

        rows = {
            panel.table.item(row, 1).text(): row
            for row in range(panel.table.rowCount())
        }
        self.assertEqual(
            panel.table.item(rows["SMW200A-1（信号发生器）"], 0).checkState(), Qt.Checked
        )

    def test_preselection_never_matches_on_serial_alone(self):
        """只有序列号时不做猜测：不同厂商可能有相同序列号。"""
        panel = self.make_panel()
        panel.set_device_bindings({"signal_generator": {"serial": "105462"}})

        self.run_scan(panel)

        for row in range(panel.table.rowCount()):
            self.assertEqual(panel.table.item(row, 0).checkState(), Qt.Unchecked)
        self.assertNotIn("预选", panel.status_label.text())

    def test_unidentified_address_is_listed_but_cannot_be_applied(self):
        def factory(_mode):
            return build_scanner(
                registry=_registry(),
                mode=SCAN_MODE_SIMULATED,
                responses={("192.168.1.150", 5025): "GARBAGE"},
                timeout_s=0.02,
            )

        panel = DeviceDiscoveryPanel(scanner_factory=factory)
        panel.show()
        self.app.processEvents()
        self.run_scan(panel)

        self.assertEqual(panel.table.rowCount(), 1)
        self.assertEqual(panel.table.item(0, 3).text(), "无法识别")
        # 身份读不出来就不能应用：勾选框不可勾选。
        self.assertFalse(panel.table.item(0, 0).flags() & Qt.ItemIsUserCheckable)
        self.assertIn("无法识别 1 个地址", panel.status_label.text())

    def test_scan_failure_restores_the_button_and_shows_the_reason(self):
        def factory(_mode):
            raise InstrumentDiscoveryError("设备表损坏")

        panel = DeviceDiscoveryPanel(scanner_factory=factory)
        panel.show()
        self.app.processEvents()

        self.run_scan(panel)

        self.assertIn("无法开始扫描", panel.status_label.text())
        self.assertIn("设备表损坏", panel.status_label.text())
        self.assertTrue(panel.scan_btn.isEnabled())
        self.assertFalse(panel.apply_btn.isEnabled())

    def test_shutdown_during_scan_cancels_and_leaves_no_worker(self):
        panel = self.make_panel()
        panel.request_scan()

        self.assertTrue(panel.shutdown())

        self.assertIsNone(panel.worker)
        self.assertFalse(panel.is_scanning())


class TableFocusFrameTests(unittest.TestCase):
    """点击表格后不再多画一个圆角焦点框。

    设备发现表是勾选列表，不是可编辑网格：普通 ``QTableView`` 在点击后会把单元格设为
    “当前格”，再叠一个焦点框，勾选框旁边就多出一个方框。表格规则关掉 ``outline`` 后，
    “有当前格”与“没有当前格”的渲染必须完全一致。
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        # 主题是全局样式表：用完必须还原，避免影响同进程里的其他界面测试。
        self._previous_stylesheet = self.app.styleSheet()
        apply_theme(self.app)

    def tearDown(self):
        self.app.setStyleSheet(self._previous_stylesheet)

    def test_current_cell_paints_no_extra_frame(self):
        panel = DeviceDiscoveryPanel(scanner_factory=_scanner_factory)
        panel.show()
        self.app.processEvents()
        try:
            panel.request_scan()
            self.assertTrue(panel.worker is not None)
            panel.worker.wait(5000)
            self.app.processEvents()

            table = panel.table
            self.assertGreater(table.rowCount(), 0)
            table.setCurrentCell(-1, -1)
            table.clearFocus()
            self.app.processEvents()
            without_current_cell = table.viewport().grab().toImage()

            table.setFocus()
            table.setCurrentCell(0, 0)
            self.app.processEvents()
            with_current_cell = table.viewport().grab().toImage()

            self.assertEqual(without_current_cell, with_current_cell)
        finally:
            panel.shutdown()


class ConfigPageDiscoveryIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_page(self):
        page = ConfigPage(
            {"test_frequencies": [1.0], "instruments": {"power_supplies": {}}},
            scanner_factory=_scanner_factory,
        )
        page.show()
        self.app.processEvents()
        return page

    def test_page_builds_the_discovery_panel(self):
        page = self.make_page()
        self.assertIsInstance(page.discovery_panel, DeviceDiscoveryPanel)
        self.assertTrue(hasattr(page, "apply_discovered_devices"))

    def test_scan_subnet_defaults_to_the_bound_device_address(self):
        page = ConfigPage(
            {
                "test_frequencies": [1.0],
                "instruments": {
                    "power_supplies": {},
                    "signal_generator": {
                        "address": "TCPIP0::192.168.1.201::inst0::INSTR",
                        "enabled": True,
                    },
                },
                "device_bindings": {
                    "signal_generator": {
                        "visa_address": "TCPIP0::10.2.3.201::inst0::INSTR",
                        "device_key": "rohde&schwarz:105462",
                    }
                },
            },
            scanner_factory=_scanner_factory,
        )
        page.show()
        self.app.processEvents()
        self.addCleanup(page.close)

        self.assertEqual(page.discovery_panel.subnet_edit.text(), "10.2.3.0/24")

    def test_connection_box_has_no_manual_address_widgets(self):
        """仪器连接只有“扫描后勾选”一套逻辑：不再逐台填写地址或勾选启用。"""
        page = self.make_page()

        self.assertEqual(page.discovery_panel.title(), "仪器连接配置")
        for widget in (
            "sg_address", "sg_enabled", "sa_address", "sa_enabled",
            "ps1_address", "ps1_enabled", "ps4_address", "ps4_enabled",
        ):
            self.assertFalse(hasattr(page, widget), f"不应再存在手工地址控件 {widget}")

    def test_applied_device_writes_address_and_identity_into_config(self):
        page = self.make_page()

        page.apply_discovered_devices(
            [
                {
                    "role": "spectrum_analyzer",
                    "address": "TCPIP0::192.168.1.202::inst0::INSTR",
                    "name": "4082F-1",
                    "vendor": "Ceyear Technologies",
                    "model": "4082F",
                    "serial": "QZNI000866",
                    "device_key": "ceyear technologies:qzni000866",
                    "ip": "192.168.1.202",
                    "port": 5025,
                    "known": True,
                }
            ]
        )

        config = page.build_config()
        self.assertEqual(
            config["instruments"]["spectrum_analyzer"]["address"],
            "TCPIP0::192.168.1.202::inst0::INSTR",
        )
        self.assertTrue(config["instruments"]["spectrum_analyzer"]["enabled"])
        binding = config["device_bindings"]["spectrum_analyzer"]
        self.assertEqual(binding["serial"], "QZNI000866")
        self.assertEqual(binding["device_key"], "ceyear technologies:qzni000866")
        self.assertEqual(binding["last_ip"], "192.168.1.202")
        self.assertEqual(binding["source"], "scan")
        # 当前配置直接显示在连接框里，不必先重新扫描。
        self.assertIn("4082F-1", page.discovery_panel.bindings_label.text())

    def test_simulated_device_binding_is_marked_as_demo_data(self):
        page = self.make_page()

        page.apply_discovered_devices(
            [
                {
                    "role": "signal_generator",
                    "address": "TCPIP0::192.168.1.201::inst0::INSTR",
                    "serial": "105462",
                    "device_key": "rohde&schwarz:105462",
                    "ip": "192.168.1.201",
                    "port": 5025,
                    "known": True,
                    "simulated": True,
                }
            ]
        )

        self.assertEqual(page.device_bindings["signal_generator"]["source"], "simulated")

    def test_power_supply_role_is_written_to_the_matching_supply(self):
        page = self.make_page()

        page.apply_discovered_devices(
            [
                {
                    "role": "PS2",
                    "address": "TCPIP0::192.168.1.150::inst0::INSTR",
                    "name": None,
                    "vendor": "Keysight Technologies",
                    "model": "N9020A",
                    "serial": "MY51234567",
                    "device_key": "keysight technologies:my51234567",
                    "ip": "192.168.1.150",
                    "port": 5025,
                    "known": False,
                }
            ]
        )

        self.assertEqual(
            page.build_config()["instruments"]["power_supplies"]["PS2"]["address"],
            "TCPIP0::192.168.1.150::inst0::INSTR",
        )
        self.assertTrue(
            page.build_config()["instruments"]["power_supplies"]["PS2"]["enabled"]
        )
        # 未知设备也能被用户明确使用，但绑定记录里标记为未登记。
        self.assertFalse(page.device_bindings["PS2"]["known"])

    def test_unknown_role_is_rejected(self):
        page = self.make_page()
        messages = []
        page.validation_failed.connect(messages.append)

        page.apply_discovered_devices([{"role": "nope", "address": "TCPIP0::1.2.3.4::inst0::INSTR"}])

        self.assertIn("未知的测试角色", messages[-1])
        self.assertEqual(page.device_bindings, {})

    def test_empty_address_is_rejected(self):
        page = self.make_page()
        messages = []
        page.validation_failed.connect(messages.append)

        page.apply_discovered_devices([{"role": "signal_generator", "address": "  "}])

        self.assertIn("发现地址为空", messages[-1])
        self.assertNotIn("address", page.build_config()["instruments"]["signal_generator"])

    def test_invalid_entry_rejects_the_whole_batch(self):
        page = self.make_page()
        messages = []
        page.validation_failed.connect(messages.append)

        page.apply_discovered_devices(
            [
                {"role": "signal_generator", "address": "TCPIP0::192.168.1.201::inst0::INSTR"},
                {"role": "nope", "address": "TCPIP0::192.168.1.202::inst0::INSTR"},
            ]
        )

        self.assertIn("未知的测试角色", messages[-1])
        # 先校验后写入：第一项也不能被写进连接配置或绑定记录。
        self.assertNotIn("address", page.build_config()["instruments"]["signal_generator"])
        self.assertEqual(page.device_bindings, {})

    def test_duplicate_roles_in_one_batch_are_rejected(self):
        page = self.make_page()
        messages = []
        page.validation_failed.connect(messages.append)

        page.apply_discovered_devices(
            [
                {
                    "role": "signal_generator",
                    "address": "TCPIP0::192.168.1.201::inst0::INSTR",
                    "serial": "105462",
                },
                {
                    "role": "signal_generator",
                    "address": "TCPIP0::192.168.1.202::inst0::INSTR",
                    "serial": "QZNI000866",
                },
            ]
        )

        self.assertIn("重复指定", messages[-1])
        self.assertNotIn("address", page.build_config()["instruments"]["signal_generator"])
        self.assertEqual(page.device_bindings, {})

    def test_missing_serial_is_rejected(self):
        page = self.make_page()
        messages = []
        page.validation_failed.connect(messages.append)

        page.apply_discovered_devices(
            [{"role": "signal_generator", "address": "TCPIP0::192.168.1.201::inst0::INSTR"}]
        )

        self.assertIn("缺少设备身份", messages[-1])
        self.assertNotIn("address", page.build_config()["instruments"]["signal_generator"])

    def test_identity_disagreeing_with_serial_is_rejected(self):
        page = self.make_page()
        messages = []
        page.validation_failed.connect(messages.append)

        page.apply_discovered_devices(
            [
                {
                    "role": "signal_generator",
                    "address": "TCPIP0::192.168.1.201::inst0::INSTR",
                    "vendor": "Rohde&Schwarz",
                    "serial": "105462",
                    "device_key": "keysight technologies:105462",
                }
            ]
        )

        self.assertIn("设备身份与序列号不一致", messages[-1])
        self.assertEqual(page.device_bindings, {})

    def test_clear_button_removes_bindings_and_invalidates_the_connection(self):
        page = self.make_page()
        page.apply_discovered_devices(
            [
                {
                    "role": "signal_generator",
                    "address": "TCPIP0::192.168.1.201::inst0::INSTR",
                    "serial": "105462",
                    "device_key": "rohde&schwarz:105462",
                    "ip": "192.168.1.201",
                    "port": 5025,
                    "known": True,
                }
            ]
        )
        # 相当于连接前已经保存过一次：地址已随配置落盘。
        page.apply_config(page.build_config())

        page.discovery_panel.request_clear()

        self.assertEqual(page.device_bindings, {})
        config = page.build_config()
        self.assertNotIn("device_bindings", config)
        # 绑定是唯一连接来源：清除后地址与启用状态一起失效，不会打到旧设备。
        self.assertNotIn("address", config["instruments"]["signal_generator"])
        self.assertFalse(config["instruments"]["signal_generator"]["enabled"])

    def test_applying_a_subset_replaces_the_previous_bindings(self):
        page = self.make_page()
        page.apply_discovered_devices(
            [
                {
                    "role": "signal_generator",
                    "address": "TCPIP0::192.168.1.201::inst0::INSTR",
                    "serial": "105462",
                    "device_key": "rohde&schwarz:105462",
                    "ip": "192.168.1.201",
                    "port": 5025,
                    "known": True,
                },
                {
                    "role": "PS1",
                    "address": "TCPIP0::192.168.1.101::inst0::INSTR",
                    "serial": "PS1SN",
                    "device_key": "keysight technologies:ps1sn",
                    "ip": "192.168.1.101",
                    "port": 5025,
                    "known": True,
                },
            ]
        )

        page.apply_discovered_devices(
            [
                {
                    "role": "signal_generator",
                    "address": "TCPIP0::192.168.1.211::inst0::INSTR",
                    "serial": "105462",
                    "device_key": "rohde&schwarz:105462",
                    "ip": "192.168.1.211",
                    "port": 5025,
                    "known": True,
                }
            ]
        )

        # 整批替换：没勾选的 PS1 被移除，地址与启用状态一起失效。
        self.assertEqual(set(page.device_bindings), {"signal_generator"})
        config = page.build_config()
        self.assertEqual(
            config["instruments"]["signal_generator"]["address"],
            "TCPIP0::192.168.1.211::inst0::INSTR",
        )
        self.assertNotIn("address", config["instruments"]["power_supplies"]["PS1"])
        self.assertFalse(config["instruments"]["power_supplies"]["PS1"]["enabled"])
        self.assertEqual(page.get_enabled_power_supplies(), [])
        self.assertIn("已移除", page.discovery_panel.status_label.text())

    def test_empty_batch_is_rejected_without_touching_existing_bindings(self):
        page = self.make_page()
        page.apply_discovered_devices(
            [
                {
                    "role": "signal_generator",
                    "address": "TCPIP0::192.168.1.201::inst0::INSTR",
                    "serial": "105462",
                    "device_key": "rohde&schwarz:105462",
                    "ip": "192.168.1.201",
                    "port": 5025,
                    "known": True,
                }
            ]
        )
        messages = []
        page.validation_failed.connect(messages.append)

        page.apply_discovered_devices([])

        self.assertIn("请先在发现结果中勾选", messages[-1])
        self.assertIn("signal_generator", page.device_bindings)

    def test_legacy_config_address_is_migrated_as_an_unconfirmed_binding(self):
        """只有地址的旧配置：加载后仍可连接，但在面板上标明“未扫描确认”。"""
        page = ConfigPage(
            {
                "test_frequencies": [1.0],
                "instruments": {
                    "signal_generator": {
                        "address": "TCPIP0::192.168.1.201::inst0::INSTR",
                        "enabled": True,
                    },
                    "power_supplies": {},
                },
            },
            scanner_factory=_scanner_factory,
        )
        page.show()
        self.app.processEvents()
        self.addCleanup(page.close)

        self.assertEqual(
            page.device_bindings["signal_generator"]["source"], "legacy_config"
        )
        self.assertEqual(
            page.build_config()["instruments"]["signal_generator"]["address"],
            "TCPIP0::192.168.1.201::inst0::INSTR",
        )
        self.assertIn("未扫描确认", page.discovery_panel.bindings_label.text())
        # 扫描框初值取自已保存的地址网段。
        self.assertEqual(page.discovery_panel.subnet_edit.text(), "192.168.1.0/24")

    def test_rejected_batch_is_also_reported_in_the_panel_status(self):
        """整批拒绝的原因要同时出现在面板状态里，不能只写日志。"""
        page = self.make_page()

        page.apply_discovered_devices(
            [{"role": "signal_generator", "address": "TCPIP0::192.168.1.201::inst0::INSTR"}]
        )

        self.assertIn("缺少设备身份", page.discovery_panel.status_label.text())

    def test_loading_a_config_restores_device_bindings(self):
        page = self.make_page()
        config = page.build_config()
        config["device_bindings"] = {
            "signal_generator": {"serial": "105462", "device_key": "rohde&schwarz:105462"}
        }

        page.apply_config(config)

        self.assertEqual(page.device_bindings["signal_generator"]["serial"], "105462")
        self.assertEqual(
            page.build_config()["device_bindings"]["signal_generator"]["serial"], "105462"
        )

    def test_clearing_bindings_drops_them_from_the_saved_config(self):
        page = self.make_page()
        page.apply_discovered_devices(
            [
                {
                    "role": "signal_generator",
                    "address": "TCPIP0::192.168.1.201::inst0::INSTR",
                    "serial": "105462",
                    "device_key": "rohde&schwarz:105462",
                    "ip": "192.168.1.201",
                    "port": 5025,
                    "known": True,
                }
            ]
        )
        page.clear_device_bindings()

        self.assertNotIn("device_bindings", page.build_config())


class DiscoveryEnabledOfflineDefaultsTests(unittest.TestCase):
    """没有设备网络的开发机上，默认扫描器必须是模拟数据，绝不偷偷发真实连接。"""

    def test_composition_factory_simulates_by_default_mode(self):
        from presentation.qt.composition import build_device_scanner_factory

        with patch(
            "infrastructure.discovery.instrument_discovery.TcpIdnProbe.query_idn",
            side_effect=AssertionError("模拟模式不得打开真实连接"),
        ):
            scanner = build_device_scanner_factory()(SCAN_MODE_SIMULATED)
            result = scanner.scan("192.168.1.200/30")

        # /30 的两个可用地址都有演示响应：SMW200A 与 4082F。
        self.assertEqual(len(result.devices), 2)


if __name__ == "__main__":
    unittest.main()
