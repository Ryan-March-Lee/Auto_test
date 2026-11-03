"""连接页与文档化窄接口（GUI 重构步骤 8）：用替身端口独立验证页面行为。

这里不构造 ``ConfigurationPage``：连接页只依赖
:class:`application.ports.device_binding.DeviceBindingPort`。测试断言页面读的是只读
``ConfigurationViewState``、提交的是 ``ApplyDeviceSelectionRequest``，并且“连接仪器”
只发出用户意图。

步骤 12 起，静态骨架的测试也走页面公开的语义查询（标题、动作文案与可见性），不再按
``.ui`` 的 objectName 找控件；控件层级契约只在 ``tests/ui/test_ui_loader.py`` 里保留。
版心宽度与上下留白属于视觉契约，由本模块直接量取页面呈现出来的几何，不为此在生产
页面上新增测试专用查询方法。
"""

import os
import re
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from application.configuration_editing import ValidationIssue, ValidationResult
from application.configuration_view_model import ConfigurationViewState
from application.device_binding import (
    ApplyDeviceSelectionRequest,
    AppliedDeviceBinding,
    DeviceBindingResult,
)
from presentation.qt.instrument_setup_page import InstrumentSetupPage
from presentation.qt.instrument_discovery_panel import BOX_STATE_RESULTS
from presentation.qt.theme import PAGE_CONTENT_MAX_WIDTH

ROOT = Path(__file__).resolve().parents[2]


def _scanner_factory(_mode):
    """连接页构造时只需要一个扫描器工厂；本组用例从不真正扫描。"""

    class _UnusedScanner:
        def scan(self, *_args, **_kwargs):
            return []

    return _UnusedScanner()


class _StubPort:
    """只实现 DeviceBindingPort 两个方法的替身；记录提交过的请求。"""

    def __init__(self, state=None, result=None):
        self.state = state or ConfigurationViewState()
        self.result = result or DeviceBindingResult()
        self.requests = []

    def view_state(self):
        return self.state

    def apply_selection(self, request):
        self.requests.append(request)
        return self.result


class _PanelStub:
    def __init__(self, assignments=(), problems=()):
        self._assignments = list(assignments)
        self._problems = list(problems)
        self.messages = []

    def box_state(self):
        return BOX_STATE_RESULTS

    def selected_assignments(self):
        return list(self._assignments), list(self._problems)

    def show_message(self, message):
        self.messages.append(message)

    def shutdown(self):
        return True


class InstrumentSetupPagePortTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_page(self, port):
        page = InstrumentSetupPage(port, scanner_factory=_scanner_factory)
        self.addCleanup(page.close)
        return page

    def test_constructor_takes_a_port_instead_of_a_config_page(self):
        page = self.make_page(_StubPort())

        self.assertFalse(hasattr(page, "config_page"))
        self.assertIs(page.binding_port.__class__, _StubPort)

    def test_enter_reads_the_read_only_view_state(self):
        port = _StubPort(
            ConfigurationViewState(
                bindings={
                    "signal_generator": {
                        "visa_address": "TCPIP0::10.2.3.201::inst0::INSTR",
                        "source": "legacy_config",
                    }
                },
                instrument_addresses=("TCPIP0::192.168.9.1::inst0::INSTR",),
                legacy_binding_count=1,
            )
        )
        page = self.make_page(port)
        logs = []
        page.log_message.connect(logs.append)

        page.enter()

        self.assertEqual(page.discovery_panel.subnet_edit.text(), "10.2.3.0/24")
        self.assertTrue(any("未扫描确认" in line for line in logs))
        # 绑定来自只读视图；页面没有回读另一个页面的内部状态。
        self.assertIn("signal_generator", page.discovery_panel.device_bindings)

    def test_enter_falls_back_to_saved_instrument_addresses(self):
        port = _StubPort(
            ConfigurationViewState(
                instrument_addresses=("TCPIP0::192.168.9.1::inst0::INSTR",)
            )
        )
        page = self.make_page(port)

        page.enter()

        self.assertEqual(page.discovery_panel.subnet_edit.text(), "192.168.9.0/24")

    def test_next_submits_an_apply_request_and_navigates_only_on_success(self):
        result = DeviceBindingResult(
            bindings={"signal_generator": {"visa_address": "sim::sg"}},
            applied=(
                AppliedDeviceBinding(
                    role="signal_generator",
                    model="SMW200A",
                    full_code="105462",
                    ip="192.168.1.201",
                ),
            ),
        )
        port = _StubPort(result=result)
        page = self.make_page(port)
        page.discovery_panel = _PanelStub(
            assignments=[
                {
                    "role": "signal_generator",
                    "address": "sim::sg",
                    "full_code": "105462",
                    "device_key": "rohde&schwarz:105462",
                }
            ]
        )
        requested = []
        page.next_requested.connect(lambda: requested.append(True))

        page.request_next()

        self.assertEqual(len(port.requests), 1)
        self.assertIsInstance(port.requests[0], ApplyDeviceSelectionRequest)
        self.assertEqual(port.requests[0].devices[0].role, "signal_generator")
        self.assertEqual(requested, [True])

    def test_next_stays_when_the_port_rejects_the_selection(self):
        rejected = DeviceBindingResult(
            validation=ValidationResult.of(
                ValidationIssue(
                    path="device_bindings.role",
                    code="device_binding.duplicate_role",
                    message="角色被重复选择",
                )
            )
        )
        port = _StubPort(result=rejected)
        page = self.make_page(port)
        page.discovery_panel = _PanelStub(
            assignments=[{"role": "signal_generator", "address": "sim::sg"}]
        )
        requested = []
        page.next_requested.connect(lambda: requested.append(True))

        page.request_next()

        self.assertEqual(len(port.requests), 1)
        self.assertEqual(requested, [])

    def test_next_does_not_submit_when_nothing_is_checked(self):
        port = _StubPort()
        page = self.make_page(port)
        page.discovery_panel = _PanelStub(assignments=[])
        requested = []
        page.next_requested.connect(lambda: requested.append(True))

        page.request_next()

        self.assertEqual(port.requests, [])
        self.assertEqual(requested, [])

    def test_connect_button_only_emits_user_intent(self):
        page = self.make_page(_StubPort())
        requested = []
        page.connect_requested.connect(lambda: requested.append(True))

        page.connect_btn.click()

        self.assertEqual(requested, [True])


class InstrumentSetupPageBoundaryTests(unittest.TestCase):
    """静态边界：连接页不回读配置页，也不自己构造它。"""

    def test_module_never_reaches_into_the_configuration_page(self):
        source = (ROOT / "presentation" / "qt" / "instrument_setup_page.py").read_text(
            encoding="utf-8-sig"
        )

        for fragment in (
            "self.config_page",
            "from .config_page",
            "ConfigurationPage(",
            "config_page.device_bindings",
            "config_page.config",
        ):
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, source)

    def test_static_layout_and_visual_tokens_live_outside_the_page_module(self):
        """步骤 11：页面模块不写颜色、字号或内联样式；静态排列在 ``.ui``。"""
        source = (ROOT / "presentation" / "qt" / "instrument_setup_page.py").read_text(
            encoding="utf-8-sig"
        )

        self.assertIn('load_ui("instrument_setup_page")', source)
        self.assertNotIn("setStyleSheet", source)
        self.assertNotRegex(source, re.compile(r"#[0-9A-Fa-f]{6}"))
        self.assertNotIn("font-size", source)

    def test_page_local_geometry_stays_in_the_designed_skeleton(self):
        """静态边距/间距只能在 ``.ui``：页面模块只把外壳布局归零。

        Qt 的布局尺寸只能长在布局对象上，Designer 是唯一能预览它们的地方，因此页面
        本地的几何归 ``.ui`` 单文件所有；这条门禁防止它慢慢散回 Python 常量。
        """
        source = (ROOT / "presentation" / "qt" / "instrument_setup_page.py").read_text(
            encoding="utf-8-sig"
        )

        self.assertNotIn("addSpacing(", source)
        self.assertNotRegex(source, r"setContentsMargins\((?!0, 0, 0, 0\))")
        self.assertNotRegex(source, r"setSpacing\((?!0\))")
        for call in (
            "setMinimumWidth",
            "setFixedWidth",
            "setMinimumHeight",
            "setFixedHeight",
        ):
            with self.subTest(call=call):
                self.assertNotIn(call, source)
        # 版心宽度是跨页面令牌：页面只引用令牌，不在这里另写数字。
        self.assertIn("setMaximumWidth(", source)
        self.assertIn("PAGE_CONTENT_MAX_WIDTH", source)
        self.assertNotRegex(source, r"setMaximumWidth\(\s*\d")


class InstrumentSetupPageStaticLayoutTests(unittest.TestCase):
    """步骤 12：静态骨架的视觉契约通过页面呈现结果验证。

    ``.ui`` 的控件层级与 objectName 契约只在 ``tests/ui/test_ui_loader.py`` 里保留；
    这里断言用户看得见的结果——标题文案、页面动作、版心宽度与上下留白的比例。视觉几何
    直接量取页面呈现出来的控件位置与尺寸，不要求生产页面新增测试专用查询方法。
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_page(self):
        page = InstrumentSetupPage(_StubPort(), scanner_factory=_scanner_factory)
        self.addCleanup(page.close)
        return page

    def test_page_shows_its_title_and_page_actions(self):
        page = self.make_page()

        self.assertEqual(page.page_title(), "仪表配置")
        self.assertEqual(page.action_labels(), ("返回欢迎页", "下一步"))

    def test_page_actions_are_visible(self):
        page = self.make_page()
        page.show()
        self.app.processEvents()

        self.assertEqual(page.page_actions_visible(), (True, True))

    def test_content_is_constrained_to_the_shared_width_and_stays_centred(self):
        page = self.make_page()
        page.resize(PAGE_CONTENT_MAX_WIDTH + 400, 900)
        page.show()
        self.app.processEvents()
        self.app.processEvents()

        # 版心是标题所在的滚动内容列；它在超宽窗口里应停在主题令牌上并保持居中。
        content = page.title_label.parentWidget()
        self.assertIsNotNone(content)
        self.assertEqual(
            content.width(),
            PAGE_CONTENT_MAX_WIDTH,
            "超宽窗口下版心应被主题令牌收住",
        )
        offset = content.mapTo(page, QPoint(0, 0)).x()
        self.assertAlmostEqual(
            offset,
            (page.width() - content.width()) / 2,
            delta=2,
            msg="版心应居中而不是贴向一侧",
        )

    def test_the_result_cards_sit_above_the_scan_button(self):
        """页面主体靠上：标题靠近顶部，结果卡片那一格在按钮上方。

        “查找设备”按钮被引导间距压到结果卡片下沿之下：结果卡片在页面顶部出现，
        两者不重叠，过渡动画才有干净的起点与终点。
        """
        from presentation.qt.device_cards import CARD_HEIGHT

        page = self.make_page()
        page.resize(1200, 900)
        page.show()
        self.app.processEvents()
        self.app.processEvents()

        title_top = page.title_label.mapTo(
            page, page.title_label.rect().topLeft()
        ).y()
        self.assertLess(title_top, page.height() * 0.2, "页标题应靠近窗口上部")

        # 设备发现面板（扫描行 + 结果区）从页面上部开始。
        panel = page.discovery_panel
        panel_top = panel.mapTo(page, panel.rect().topLeft()).y()
        self.assertLess(panel_top, page.height() * 0.35, "设备发现面板应靠近页面上部")

        # 结果卡片从结果区顶部开始排；按钮被引导间距整体压到下半区，给卡片让位。
        box_top = panel.results_box.mapTo(
            page, panel.results_box.rect().topLeft()
        ).y()
        button_top = panel.scan_btn.mapTo(
            page, panel.scan_btn.rect().topLeft()
        ).y()
        # 等待动画的舞台比卡片高，上沿要伸到按钮上方；按钮至少得下沉这段溢出，
        # 舞台才不会越出结果区上沿（卡片上方的投影留白另算）。
        from presentation.qt.scan_card import WAIT_STACK_HEIGHT

        stage_overhang = max(0, (WAIT_STACK_HEIGHT - CARD_HEIGHT) // 2)
        self.assertGreater(
            button_top - box_top,
            stage_overhang + 8,
            "按钮要给上方卡片与等待动画的舞台让出引导高度",
        )
        self.assertGreater(
            button_top - box_top,
            CARD_HEIGHT // 2,
            "按钮整体落在卡片下沿附近，不贴结果区顶部",
        )
        self.assertGreater(
            panel.scan_btn.mapTo(page, panel.scan_btn.rect().center()).y(),
            page.height() / 2.0,
            "按钮整体应落在页面下半区，卡片在其上方",
        )


if __name__ == "__main__":
    unittest.main()
