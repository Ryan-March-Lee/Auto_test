import unittest

from instrument.power_control import PowerController, PowerControlError
from instrument.ports import InstrumentState
from instrument.session import InstrumentSessionError, ManagedInstrumentSession
from instrument.simulation import CommandRecorder, SimulatedPowerSupply, SimulatedSignalGenerator, SimulatedSpectrumAnalyzer
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

    def test_end_stage_settles_the_session_and_allows_reuse(self):
        """成功阶段结束回到 PREPARED：同一会话可直接开始下一阶段。"""
        recorder = CommandRecorder()
        signal = SimulatedSignalGenerator(recorder)
        supply = SimulatedPowerSupply(recorder)
        session = ManagedInstrumentSession(
            signal,
            SimulatedSpectrumAnalyzer(recorder=recorder),
            supply,
            {"gate": "CH1", "drain": "CH2"},
        )
        session.validate()
        session.connect()
        session.prepare()
        session.power_on()
        session.start_measurement()
        signal.set_rf_enabled(True)

        session.end_stage()

        self.assertEqual(session.state, InstrumentState.PREPARED)
        self.assertFalse(signal.rf_enabled)
        self.assertEqual(supply.outputs, {"CH1": False, "CH2": False})

        # 下一阶段复用同一会话：不重新 connect，前置条件仍然成立。
        session.require_stage_ready()
        session.power_on()
        session.start_measurement()
        self.assertEqual(session.state, InstrumentState.MEASURING)
        power_actions = [
            action
            for device, action, _value in recorder.commands
            if device == "power_supply" and action in {"output_on", "output_off"}
        ]
        self.assertEqual(
            power_actions,
            [
                "output_on",
                "output_on",
                "output_off",
                "output_off",
                "output_on",
                "output_on",
            ],
        )

    def test_prepared_stage_can_enable_rf_without_power_on(self):
        """线损阶段不上电也要能开 RF；结算后仍回到可复用的 PREPARED。"""
        recorder = CommandRecorder()
        signal = SimulatedSignalGenerator(recorder)
        session = ManagedInstrumentSession(
            signal,
            SimulatedSpectrumAnalyzer(recorder=recorder),
            SimulatedPowerSupply(recorder),
            {"gate": "CH1", "drain": "CH2"},
        )
        session.validate()
        session.connect()
        session.prepare()

        session.require_stage_ready()
        session.set_rf_enabled(True)
        self.assertTrue(signal.rf_enabled)

        session.end_stage()

        self.assertEqual(session.state, InstrumentState.PREPARED)
        self.assertFalse(signal.rf_enabled)

    def test_end_stage_failure_blocks_reuse(self):
        """安全清理失败时不得进入 PREPARED；只有关闭端口仍被允许。"""
        recorder = CommandRecorder()
        signal = SimulatedSignalGenerator(recorder)
        supply = SimulatedPowerSupply(recorder)
        session = ManagedInstrumentSession(
            signal,
            SimulatedSpectrumAnalyzer(recorder=recorder),
            supply,
            {"gate": "CH1", "drain": "CH2"},
        )
        session.validate()
        session.connect()
        session.prepare()
        session.power_on()
        supply.inject_failure("output_off", once=True)

        with self.assertRaises(InstrumentSessionError) as raised:
            session.end_stage()

        self.assertEqual(raised.exception.actions, ("power_supplies",))
        self.assertEqual(session.state, InstrumentState.STOPPING)
        with self.assertRaises(ValueError):
            session.require_stage_ready()
        with self.assertRaises(ValueError):
            session.power_on()
        with self.assertRaises(ValueError):
            session.start_measurement()

        # 唯一允许的收尾动作仍然是关闭端口。
        session.close()
        self.assertEqual(session.state, InstrumentState.CLEANED)

    def test_end_stage_fails_when_rf_cannot_be_disabled(self):
        """不能发出 RF 关闭命令就无法确认关闭：结算必须失败而不是当成已安全。"""
        recorder = CommandRecorder()
        signal = SimulatedSignalGenerator(recorder)
        session = ManagedInstrumentSession(
            signal,
            SimulatedSpectrumAnalyzer(recorder=recorder),
            SimulatedPowerSupply(recorder),
            {"gate": "CH1", "drain": "CH2"},
            can_disable_rf=False,
        )
        session.validate()
        session.connect()
        session.prepare()
        session.power_on()
        signal.rf_enabled = True

        with self.assertRaises(InstrumentSessionError) as raised:
            session.end_stage()

        self.assertEqual(raised.exception.actions, ("rf_output",))
        self.assertEqual(session.state, InstrumentState.STOPPING)
        with self.assertRaises(ValueError):
            session.require_stage_ready()
        # 收尾仍然只允许关闭；关闭时不能确认 RF 的责任留给调用方处理。
        session.close()

    def test_end_stage_fails_when_power_cannot_be_disabled(self):
        """会话管理着已连接电源却不能关闭时，同样不能进入可复用的 PREPARED。"""
        recorder = CommandRecorder()
        signal = SimulatedSignalGenerator(recorder)
        supply = SimulatedPowerSupply(recorder)
        session = ManagedInstrumentSession(
            signal,
            SimulatedSpectrumAnalyzer(recorder=recorder),
            supply,
            {"gate": "CH1", "drain": "CH2"},
            can_disable_power=False,
        )
        session.validate()
        session.connect()
        session.prepare()
        session.power_on()
        session.start_measurement()
        session.set_rf_enabled(True)

        with self.assertRaises(InstrumentSessionError) as raised:
            session.end_stage()

        self.assertEqual(raised.exception.actions, ("power_supplies",))
        self.assertEqual(session.state, InstrumentState.STOPPING)
        # RF 关闭动作仍然执行过，失败只来自无法确认的电源输出。
        self.assertFalse(signal.rf_enabled)
        self.assertIn(("power_supply", "output_on", "CH1"), recorder.commands)
        self.assertNotIn(("power_supply", "output_off", "CH1"), recorder.commands)

    def test_end_stage_does_not_require_power_confirmation_without_managed_outputs(self):
        """没有受管电源输出时不存在“应关闭而无法确认”的项，结算可以成功。"""
        recorder = CommandRecorder()
        session = ManagedInstrumentSession(
            SimulatedSignalGenerator(recorder),
            SimulatedSpectrumAnalyzer(recorder=recorder),
            SimulatedPowerSupply(recorder),
            {},
            can_disable_power=False,
        )
        session.validate()
        session.connect()
        session.prepare()

        session.end_stage()

        self.assertEqual(session.state, InstrumentState.PREPARED)
        session.close()

    def test_require_stage_ready_rejects_open_rf_and_closed_session(self):
        recorder = CommandRecorder()
        signal = SimulatedSignalGenerator(recorder)
        session = ManagedInstrumentSession(
            signal,
            SimulatedSpectrumAnalyzer(recorder=recorder),
            SimulatedPowerSupply(recorder),
            {"gate": "CH1", "drain": "CH2"},
        )
        session.validate()
        session.connect()
        session.prepare()
        signal.rf_enabled = True

        with self.assertRaises(ValueError):
            session.require_stage_ready()

        signal.rf_enabled = False
        session.require_stage_ready()
        session.close()

        with self.assertRaises(ValueError):
            session.end_stage()
        with self.assertRaises(ValueError):
            session.require_stage_ready()


if __name__ == "__main__":
    unittest.main()
