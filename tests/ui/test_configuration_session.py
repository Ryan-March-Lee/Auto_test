"""配置会话协调器（GUI 重构步骤 8）：只做转发，不依赖具体页面或 Qt。

连接页只依赖 ``DeviceBindingPort``；配置会话把连接页与配置页接起来。这里用纯 Python
替身验证会话的三条职责：转发只读视图、转发写入命令、回写发现面板。
"""

import unittest
from pathlib import Path

from application.configuration_editing import ValidationIssue, ValidationResult
from application.configuration_view_model import ConfigurationViewState
from application.device_binding import ApplyDeviceSelectionRequest, DeviceBindingResult
from presentation.qt.configuration_session import ConfigurationSession

ROOT = Path(__file__).resolve().parents[2]


class _Signal:
    """最小信号替身：会话只调用 ``connect`` / ``emit``，不需要 Qt。"""

    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in list(self._slots):
            slot(*args)


class _StubTarget:
    """配置页替身：只实现会话用到的窄接口。"""

    def __init__(self, state=None, result=None):
        self.state = state or ConfigurationViewState()
        self.result = result or DeviceBindingResult()
        self.binding_problems = []
        self.discovery_message = _Signal()
        self.device_selection_finished = _Signal()
        self.requests = []

    def view_state(self):
        return self.state

    def apply_device_selection(self, request):
        self.requests.append(request)
        return self.result


class _StubPanel:
    """设备发现面板替身：只记录回写的状态行。"""

    def __init__(self):
        self.log_message = _Signal()
        self.messages = []

    def show_message(self, message):
        self.messages.append(message)


class ConfigurationSessionTests(unittest.TestCase):
    def test_view_state_is_forwarded_from_the_target(self):
        state = ConfigurationViewState(
            instrument_addresses=("TCPIP0::192.168.9.1::inst0::INSTR",)
        )
        session = ConfigurationSession(_StubTarget(state=state))

        self.assertIs(session.view_state(), state)

    def test_apply_selection_forwards_the_explicit_request(self):
        result = DeviceBindingResult(bindings={"signal_generator": {"visa_address": "sim::sg"}})
        target = _StubTarget(result=result)
        session = ConfigurationSession(target)
        request = ApplyDeviceSelectionRequest.from_assignments(
            [{"role": "signal_generator", "address": "sim::sg"}]
        )

        self.assertIs(session.apply_selection(request), result)
        self.assertEqual(target.requests, [request])

    def test_apply_selection_rejects_anything_but_the_command_dto(self):
        target = _StubTarget()
        session = ConfigurationSession(target)

        with self.assertRaises(TypeError):
            session.apply_selection([{"role": "signal_generator"}])
        self.assertEqual(target.requests, [])

    def test_attaching_the_panel_forwards_scan_logs_and_binding_problems(self):
        target = _StubTarget()
        target.binding_problems = ["电源绑定不可用：缺少设备身份"]
        panel = _StubPanel()
        logs = []
        target.discovery_message.connect(logs.append)
        session = ConfigurationSession(target)

        session.attach_discovery_panel(panel)
        panel.log_message.emit("正在扫描")

        self.assertIn("缺少设备身份", panel.messages[-1])
        self.assertEqual(logs, ["正在扫描"])

    def test_a_successful_selection_reports_its_message(self):
        target = _StubTarget()
        panel = _StubPanel()
        session = ConfigurationSession(target)
        session.attach_discovery_panel(panel)

        target.device_selection_finished.emit(DeviceBindingResult(), "已应用发现设备: 信号发生器")

        self.assertEqual(panel.messages[-1], "已应用发现设备: 信号发生器")

    def test_a_rejected_selection_only_reports_the_message(self):
        rejected = DeviceBindingResult(
            validation=ValidationResult.of(
                ValidationIssue(
                    path="device_bindings.role",
                    code="device_binding.duplicate_role",
                    message="角色被重复选择",
                )
            )
        )
        target = _StubTarget()
        panel = _StubPanel()
        session = ConfigurationSession(target)
        session.attach_discovery_panel(panel)

        target.device_selection_finished.emit(rejected, "角色被重复选择")

        self.assertEqual(panel.messages[-1], "角色被重复选择")

    def test_attaching_the_same_panel_is_idempotent(self):
        target = _StubTarget()
        panel = _StubPanel()
        logs = []
        target.discovery_message.connect(logs.append)
        session = ConfigurationSession(target)

        session.attach_discovery_panel(panel)
        session.attach_discovery_panel(panel)
        panel.log_message.emit("正在扫描")

        self.assertEqual(logs, ["正在扫描"], "同一个面板只接一次日志转发")

    def test_attaching_a_second_panel_is_refused(self):
        session = ConfigurationSession(_StubTarget())
        session.attach_discovery_panel(_StubPanel())

        with self.assertRaises(RuntimeError):
            session.attach_discovery_panel(_StubPanel())


class ConfigurationSessionBoundaryTests(unittest.TestCase):
    """静态边界：会话只认 Protocol，不绑具体页面或 Qt 类型。"""

    def test_module_has_no_qt_or_test_config_page_dependency(self):
        source = (
            ROOT / "presentation" / "qt" / "configuration_session.py"
        ).read_text(encoding="utf-8-sig")

        self.assertNotIn("from PySide6", source)
        self.assertNotIn("import PySide6", source)
        self.assertNotIn("from .test_configuration_page", source)
        self.assertNotIn("test_config_page.", source)


if __name__ == "__main__":
    unittest.main()
