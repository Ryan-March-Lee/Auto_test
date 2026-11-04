"""设备发现集成测试的共享 fixture。

设备表、模拟扫描器和面板测试基类只服务 ``tests/integration/device_discovery`` 这一组
行为测试：共享状态留在这组用例的局部，不放进跨测试目录的公共位置，避免一个文件的改动
悄悄改变另一个文件的通过条件。
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
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
from presentation.qt.configuration_page import ConfigurationPage
from presentation.qt.configuration_session import ConfigurationSession
from presentation.qt.instrument_setup_page import InstrumentSetupPage
from presentation.qt.instrument_discovery_panel import InstrumentDiscoveryPanel


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


def six_device_factory(_mode):
    """六台设备的模拟网络：验证卡片一行最多排 6 张，不再在 4 台处换行。"""
    return build_scanner(
        registry=registry(),
        mode=SCAN_MODE_SIMULATED,
        responses={
            ("192.168.1.201", 5025): SMW_IDN,
            ("192.168.1.202", 5025): CEYEAR_IDN,
            ("192.168.1.102", 5555): DP832A_IDN,
            ("192.168.1.103", 5555): DP832A_SECOND_IDN,
            ("192.168.1.104", 5555): "RIGOL TECHNOLOGIES,DP832A,DP8B174700950,00.01.14",
            ("192.168.1.150", 5025): "Keysight Technologies,N9020A,MY51234567,A.18.21",
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


def _flush_deferred_deletes() -> None:
    """把 ``clear_results()`` 排进 DeferredDelete 的卡片删除真正执行掉。

    ``InstrumentCard`` 用 ``deleteLater()`` 删除，避免 ``setParent(None)`` 之后 Python
    包装对象被回收、C++ 控件提前销毁造成双重释放。测试在用例结束时同步丢掉顶层面板
    （没有父控件，Python 回收包装对象会立刻销毁 C++ 控件），这些排队删除还没执行；
    下一个用例跑 ``QTest.qWait`` / 事件循环时再去删除已经不存在的对象，Qt 会直接 abort。
    这里在丢弃面板前把 DeferredDelete 投递完。
    """
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _teardown_panel(panel) -> None:
    """停掉面板后立刻投递排队的删除；``panel`` 参数保证清理期间面板仍被持有。"""
    panel.shutdown()
    _flush_deferred_deletes()


class DiscoveryPanelTestCase(unittest.TestCase):
    """面板用例的共同前置：一个 QApplication、面板构造与“等扫描结束”。"""

    # 需要别的设备表/扫描方式的类只改这一项。
    default_factory = staticmethod(scanner_factory)
    # 只有要按具体尺寸验证布局的用例才设置：卡片换行与结果区尺寸都依赖真实宽度。
    panel_size = None

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_panel(self, factory=None, size=None):
        panel = InstrumentDiscoveryPanel(scanner_factory=factory or self.default_factory)
        size = size or self.panel_size
        if size is not None:
            panel.resize(*size)
        panel.show()
        self.app.processEvents()
        # 清理期间由参数持有面板：先 shutdown（停线程/动画），再把排队的卡片删除
        # 投递掉；之后局部变量释放时不会留下指向已销毁对象的 DeferredDelete 事件。
        self.addCleanup(_teardown_panel, panel)
        return panel

    def finish_scan(self, panel):
        """把等待动画走到一轮结束（牌堆复原），再把发牌/翻牌动画直接走完。"""
        panel.wait_stack.finish_cycle()
        self.app.processEvents()
        self.app.processEvents()
        if panel.deal_animation.is_running():
            panel.deal_animation.finish()
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


class InstrumentSetupPageTestCase(unittest.TestCase):
    """同时持有配置页与连接页：绑定写在配置页，扫描与勾选在连接页的面板上。

    ``page`` 是配置页（绑定写入与其派生界面：电源标签页、电源分配可选项），
    ``self.connection`` 是连接页（面板入口）。
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def finish_scan(self, panel):
        """把等待动画走到一轮结束（牌堆复原），再把发牌/翻牌动画直接走完。"""
        panel.wait_stack.finish_cycle()
        self.app.processEvents()
        self.app.processEvents()
        if panel.deal_animation.is_running():
            panel.deal_animation.finish()
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
        page = ConfigurationPage(
            config
            if config is not None
            else {"test_frequencies": [1.0], "instruments": {"power_supplies": {}}}
        )
        session = ConfigurationSession(page)
        connection = InstrumentSetupPage(session, scanner_factory=scanner_factory)
        page.discovery_message.connect(connection.log_message.emit)
        session.attach_discovery_panel(connection.discovery_panel)
        # 连接页是整屏页面：按窗口尺寸布局，卡片网格才按真实宽度换行。
        connection.resize(1200, 900)
        page.show()
        connection.show()
        self.app.processEvents()
        self.connection = connection
        # LIFO：先关闭连接页与配置页（关闭连接页会停扫描/动画），最后投递排队的
        # 卡片删除，避免 DeferredDelete 落到下一个用例的事件循环里。
        self.addCleanup(_flush_deferred_deletes)
        self.addCleanup(page.close)
        self.addCleanup(connection.close)
        return page
