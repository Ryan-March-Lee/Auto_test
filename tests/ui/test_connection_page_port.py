"""连接页与文档化窄接口（GUI 重构步骤 8）：用替身端口独立验证页面行为。

这里不构造 ``ConfigPage``：连接页只依赖
:class:`application.ports.device_binding.DeviceBindingPort`。测试断言页面读的是只读
``ConfigurationViewState``、提交的是 ``ApplyDeviceSelectionRequest``，并且“连接仪器”
只发出用户意图。
"""

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from application.configuration_editing import ValidationIssue, ValidationResult
from application.configuration_view_model import ConfigurationViewState
from application.device_binding import (
    ApplyDeviceSelectionRequest,
    AppliedDeviceBinding,
    DeviceBindingResult,
)
from presentation.qt.connection_page import ConnectionPage
from presentation.qt.device_discovery_panel import BOX_STATE_RESULTS

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


class ConnectionPagePortTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_page(self, port):
        page = ConnectionPage(port, scanner_factory=_scanner_factory)
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


class ConnectionPageBoundaryTests(unittest.TestCase):
    """静态边界：连接页不回读配置页，也不自己构造它。"""

    def test_module_never_reaches_into_the_configuration_page(self):
        source = (ROOT / "presentation" / "qt" / "connection_page.py").read_text(
            encoding="utf-8-sig"
        )

        for fragment in (
            "self.config_page",
            "from .config_page",
            "ConfigPage(",
            "config_page.device_bindings",
            "config_page.config",
        ):
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, source)


if __name__ == "__main__":
    unittest.main()
