import unittest

from instrument.power_control import PowerController, PowerControlError
from instrument.simulation import CommandRecorder, SimulatedPowerSupply, SimulatedSignalGenerator, SimulatedSpectrumAnalyzer
from instrument.session import ManagedInstrumentSession
from infrastructure.logging import app_logging

# 模块 logger 挂在专用 ``pa_auto_test`` 下：assertLogs 必须点名同一个 logger。
POWER_CONTROL_LOGGER = app_logging.get_logger("instrument.power_control").name


class InstrumentBoundaryTests(unittest.TestCase):
    def test_power_controller_uses_gate_then_drain_on_and_drain_then_gate_off(self):
        recorder = CommandRecorder()
        supply = SimulatedPowerSupply(recorder)
        supply.connect()
        controller = PowerController(supply, {"gate": "CH1", "drain": "CH2"})

        controller.power_on()
        controller.power_off()

        actions = [(action, value) for device, action, value in recorder.commands
                   if device == "power_supply" and action in {"output_on", "output_off"}]
        self.assertEqual(actions, [
            ("output_on", "CH1"), ("output_on", "CH2"),
            ("output_off", "CH2"), ("output_off", "CH1"),
        ])

    def test_power_controller_attempts_gate_after_drain_failure(self):
        recorder = CommandRecorder()
        supply = SimulatedPowerSupply(recorder)
        supply.connect()
        supply.inject_failure("output_off", once=True)
        controller = PowerController(supply, {"gate": "CH1", "drain": "CH2"})

        with self.assertRaises(PowerControlError):
            controller.power_off()

        self.assertIn(("power_supply", "output_off", "CH1"), recorder.commands)

    def test_power_controller_logs_the_short_label_and_routes_the_token(self):
        """日志写 ``短标签/CHx``，设备收到的仍是路由 token；失败记录同样写短标签。"""
        recorder = CommandRecorder()
        supply = SimulatedPowerSupply(recorder)
        supply.connect()
        controller = PowerController(
            supply,
            {"gate": "DP8B1519/CH1", "drain": "DP8B1519/CH2"},
            channel_labels={
                "DP8B1519/CH1": "现场 A 电源/CH1",
                "DP8B1519/CH2": "现场 A 电源/CH2",
            },
        )

        with self.assertLogs(POWER_CONTROL_LOGGER, level="INFO") as captured:
            controller.power_on()
        self.assertIn("打开电源通道 现场 A 电源/CH1（gate）", captured.output[0])
        self.assertNotIn("DP8B1519/CH1", "\n".join(captured.output))
        # 路由没被显示文本改掉。
        self.assertIn(("power_supply", "output_on", "DP8B1519/CH1"), recorder.commands)

        supply.inject_failure("output_off", once=True)
        with self.assertLogs(POWER_CONTROL_LOGGER, level="ERROR") as captured:
            with self.assertRaises(PowerControlError) as raised:
                controller.power_off()

        self.assertIn("关闭电源通道失败 现场 A 电源/CH2（drain）", captured.output[0])
        self.assertEqual(
            [failure.action for failure in raised.exception.failures],
            ["现场 A 电源/CH2"],
        )

    def test_power_controller_without_labels_logs_the_token_verbatim(self):
        """没有登记显示文本时退回 token：日志宁可粗糙，也不能指向另一台电源。"""
        supply = SimulatedPowerSupply(CommandRecorder())
        supply.connect()
        controller = PowerController(supply, {"gate": "PS1/CH1", "drain": "PS1/CH2"})

        with self.assertLogs(POWER_CONTROL_LOGGER, level="INFO") as captured:
            controller.power_on()

        self.assertIn("打开电源通道 PS1/CH1（gate）", captured.output[0])

    def test_formal_session_uses_explicit_resource_ownership(self):
        recorder = CommandRecorder()
        signal = SimulatedSignalGenerator(recorder)
        analyzer = SimulatedSpectrumAnalyzer(recorder=recorder)
        supply = SimulatedPowerSupply(recorder)
        session = ManagedInstrumentSession(
            signal, analyzer, supply, {"gate": "CH1", "drain": "CH2"},
            owns_signal_generator=False,
            owns_spectrum_analyzer=False,
            owns_power_supply=False,
        )
        session.validate()
        session.connect()
        session.prepare()
        session.close()

        self.assertTrue(signal.connected)
        self.assertTrue(analyzer.connected)
        self.assertTrue(supply.connected)
        self.assertEqual(session.state.value, "cleaned")

    def test_formal_session_closes_devices_it_owns(self):
        recorder = CommandRecorder()
        signal = SimulatedSignalGenerator(recorder)
        analyzer = SimulatedSpectrumAnalyzer(recorder=recorder)
        supply = SimulatedPowerSupply(recorder)
        session = ManagedInstrumentSession(
            signal, analyzer, supply, {"gate": "CH1", "drain": "CH2"},
        )
        session.validate()
        session.connect()
        session.prepare()
        session.close()

        self.assertFalse(signal.connected)
        self.assertFalse(analyzer.connected)
        self.assertFalse(supply.connected)
        self.assertEqual(session.state.value, "cleaned")


if __name__ == "__main__":
    unittest.main()
