"""设备发现集成测试的共享 fixture。

设备表、模拟扫描器和面板测试基类只服务 ``tests/integration/device_discovery`` 这一组
行为测试：共享状态留在这组用例的局部，不放进跨测试目录的公共位置，避免一个文件的改动
悄悄改变另一个文件的通过条件。
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from application.device_binding import ApplyDeviceSelectionRequest
from domain.instruments.models import InstrumentIdentity
from infrastructure.discovery.instrument_discovery import (
    SCAN_MODE_NETWORK,
    SCAN_MODE_SIMULATED,
    build_scanner,
)
from infrastructure.discovery.simulated_network import demo_idn_responses
from infrastructure.persistence.instrument_registry import InstrumentRegistry
from presentation.qt.config_page import ConfigPage
from presentation.qt.configuration_session import ConfigurationSession
from presentation.qt.connection_page import ConnectionPage
from presentation.qt.device_discovery_panel import DeviceDiscoveryPanel


SMW_IDN = "Rohde&Schwarz,SMW200A,1412.0000K02/105462,5.20.043.47"
DP832A_IDN = "RIGOL TECHNOLOGIES,DP832A,DP8B214901519,00.01.16"
DP832A_SECOND_IDN = "RIGOL TECHNOLOGIES,DP832A,DP8B174400885,00.01.16"
CEYEAR_IDN = "Ceyear Technologies,4082F,QZNI000866,1.12.04"

# 设备表里登记的固定角色：界面不再让用户选择，发现结果直接按它显示与写入。
DEVICE_ROLES = {
    SMW_IDN: "signal_generator",
    DP832A_IDN: "PS3",
    CEYEAR_IDN: "spectrum_analyzer",
}

# 迁移到 schema 4 之后的电源记录：类别是 ``power_supply``，缩写设备码已冻结，没有旧槽位。
MIGRATED_POWER_SUPPLIES = (
    {
        "vendor": "RIGOL TECHNOLOGIES",
        "model": "DP832A",
        "full_code": "DP8B214901519",
        "label": "DP8B1519",
        "role": "power_supply",
        "status": "online",
        "known_ips": [],
    },
    {
        "vendor": "RIGOL TECHNOLOGIES",
        "model": "DP832A",
        "full_code": "DP8B174400885",
        "label": "DP8B0885",
        "role": "power_supply",
        "status": "online",
        "known_ips": [],
    },
)


def registry(roles=None):
    registry = InstrumentRegistry()
    for idn in (SMW_IDN, DP832A_IDN, CEYEAR_IDN):
        registry.upsert(InstrumentIdentity.from_idn(idn), role=(roles or DEVICE_ROLES)[idn])
    return registry


def migrated_registry() -> InstrumentRegistry:
    """迁移后的设备表：两台电源都是 ``power_supply`` 带缩写设备码，没有旧槽位。"""
    return InstrumentRegistry(
        document={"schema_version": 4, "instruments": [dict(item) for item in MIGRATED_POWER_SUPPLIES]}
    )


def scanner_factory(mode):
    if mode == SCAN_MODE_NETWORK:
        raise AssertionError("测试不得发起真实网络扫描")
    return build_scanner(
        registry=registry(),
        mode=SCAN_MODE_SIMULATED,
        responses=demo_idn_responses(),
        timeout_s=0.02,
    )


def two_supply_factory(_mode):
    """两台电源都已登记为 ``power_supply`` 且带短标签（迁移后的形态）。"""
    return build_scanner(
        registry=migrated_registry(),
        mode=SCAN_MODE_SIMULATED,
        responses={
            ("192.168.1.102", 5555): DP832A_IDN,
            ("192.168.1.108", 5555): DP832A_SECOND_IDN,
        },
        timeout_s=0.02,
    )


def apply_devices(page, assignments):
    """提交一次设备勾选，返回 ``DeviceBindingResult``。

    页面只接受命令 DTO（步骤 13 删除了字典列表兼容入口），测试与连接页走同一条路径。
    """
    return page.apply_device_selection(
        ApplyDeviceSelectionRequest.from_assignments(assignments)
    )


class DiscoveryPanelTestCase(unittest.TestCase):
    """面板用例的共同前置：一个 QApplication、面板构造与“等扫描结束”。"""

    # 需要别的设备表/扫描方式的类只改这一项。
    default_factory = staticmethod(scanner_factory)
    # 只有要按具体尺寸验证布局的用例才设置：卡片换行与结果区尺寸都依赖真实宽度。
    panel_size = None

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_panel(self, factory=None, bindings=None, size=None):
        panel = DeviceDiscoveryPanel(scanner_factory=factory or self.default_factory)
        if bindings is not None:
            panel.set_device_bindings(bindings)
        size = size or self.panel_size
        if size is not None:
            panel.resize(*size)
        panel.show()
        self.app.processEvents()
        self.addCleanup(panel.shutdown)
        return panel

    def finish_scan(self, panel):
        """把等待动画走到一轮结束（牌堆复原）：扫描结果这时才换进结果区。"""
        panel.wait_stack.finish_cycle()
        self.app.processEvents()
        self.app.processEvents()

    def run_scan(self, panel):
        """点“查找设备”，等到后台扫描线程结束、等待动画也走完一轮后返回。"""
        panel.request_scan()
        self.assertTrue(panel.worker is not None)
        panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()
        self.assertIsNone(panel.worker)
        self.finish_scan(panel)
        return panel


class ConnectionPageTestCase(unittest.TestCase):
    """同时持有配置页与连接页：绑定写在配置页，扫描与勾选在连接页的面板上。

    ``page`` 是配置页（绑定写入与其派生界面：电源标签页、电源分配可选项），
    ``self.connection`` 是连接页（面板入口）。
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def finish_scan(self, panel):
        """把等待动画走到一轮结束（牌堆复原）：扫描结果这时才换进结果区。"""
        panel.wait_stack.finish_cycle()
        self.app.processEvents()
        self.app.processEvents()

    def run_scan(self, panel):
        """点“查找设备”，等到后台扫描线程结束、等待动画也走完一轮后返回。"""
        panel.request_scan()
        self.assertTrue(panel.worker is not None)
        panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()
        self.assertIsNone(panel.worker)
        self.finish_scan(panel)
        return panel

    def make_page(self, config=None):
        page = ConfigPage(
            config
            if config is not None
            else {"test_frequencies": [1.0], "instruments": {"power_supplies": {}}}
        )
        session = ConfigurationSession(page)
        connection = ConnectionPage(session, scanner_factory=scanner_factory)
        # 与主窗口同一套装配：连接页只发出“连接仪器”的意图，面板回写仍归配置页。
        connection.connect_requested.connect(page.request_connect)
        page.discovery_message.connect(connection.log_message.emit)
        session.attach_discovery_panel(connection.discovery_panel)
        # 连接页是整屏页面：按窗口尺寸布局，卡片网格才按真实宽度换行。
        connection.resize(1200, 900)
        page.show()
        connection.show()
        self.app.processEvents()
        self.connection = connection
        self.addCleanup(page.close)
        self.addCleanup(connection.close)
        return page
