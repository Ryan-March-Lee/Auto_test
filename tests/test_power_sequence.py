"""步骤 8/9：按用户计划逐通道上电、等待、失败停止与逆序清理。"""

import unittest

from application.errors import ErrorCategory
from instrument.measurement_adapter import PortMeasurementAdapter
from instrument.ports import InstrumentState
from instrument.power_control import (
    PowerChannelStep,
    PowerControlError,
    PowerController,
    PowerGroupPlan,
    PowerOnControlError,
    cleanup_failures_of,
)
from instrument.session import ManagedInstrumentSession
from instrument.simulation import (
    CommandRecorder,
    SimulatedPowerSupply,
    SimulatedSignalGenerator,
    SimulatedSpectrumAnalyzer,
)


class _FakePowerSupply:
    """记录顺序并按通道注入失败的假电源。"""

    def __init__(self, *, fail_on=None, fail_off=None, events=None):
        self.commands = []
        self.fail_on = set(fail_on or ())
        self.fail_off = set(fail_off or ())
        self.events = events

    def set_output_enabled(self, channel, enabled, *, timeout_s=5.0):
        self.commands.append((channel, bool(enabled), timeout_s))
        if self.events is not None:
            self.events.append(("on" if enabled else "off", channel))
        failures = self.fail_on if enabled else self.fail_off
        if channel in failures:
            raise RuntimeError(f"simulated output failure: {channel}")


class _Cancel(RuntimeError):
    pass


class PowerControllerSequenceTests(unittest.TestCase):
    def test_power_on_follows_the_plan_order_and_waits_between_channels(self):
        events = []
        supply = _FakePowerSupply(events=events)
        controller = PowerController(
            supply,
            ["S1/CH2", "S2/CH1", "S1/CH1"],
            inter_channel_delay_s=1.5,
            sleep_fn=lambda seconds: events.append(("sleep", seconds)),
        )

        powered = controller.power_on()

        self.assertEqual(powered, ("S1/CH2", "S2/CH1", "S1/CH1"))
        # 等待必须发生在前一通道开启成功之后、下一通道开启之前，而不是只在末尾。
        self.assertEqual(
            events,
            [
                ("on", "S1/CH2"),
                ("sleep", 1.5),
                ("on", "S2/CH1"),
                ("sleep", 1.5),
                ("on", "S1/CH1"),
            ],
        )
        self.assertEqual(
            [(channel, enabled) for channel, enabled, _timeout in supply.commands],
            [("S1/CH2", True), ("S2/CH1", True), ("S1/CH1", True)],
        )

    def test_per_channel_delays_override_the_group_interval(self):
        events = []
        controller = PowerController(
            _FakePowerSupply(events=events),
            ["A", "B", "C"],
            inter_channel_delay_s=5.0,
            channel_delays_s={"A": 0.5},
            sleep_fn=lambda seconds: events.append(("sleep", seconds)),
        )

        controller.power_on()

        self.assertEqual(
            events,
            [
                ("on", "A"),
                ("sleep", 0.5),
                ("on", "B"),
                ("sleep", 5.0),
                ("on", "C"),
            ],
            "A 用通道自己的 0.5s，B 没有单独值就回落到组的 5s，C 是最后一档不等待",
        )

    def test_power_channel_step_validates_its_delay(self):
        step = PowerChannelStep("A", power_on_delay_s=2)
        self.assertEqual(step.power_on_delay_s, 2.0)
        for bad in (-1, 61):
            with self.subTest(delay=bad):
                with self.assertRaises(ValueError):
                    PowerChannelStep("A", power_on_delay_s=bad)

    def test_single_channel_plan_never_waits(self):
        events = []
        controller = PowerController(
            _FakePowerSupply(),
            ["S1/CH1"],
            inter_channel_delay_s=5.0,
            sleep_fn=lambda seconds: events.append(seconds),
        )

        controller.power_on()

        self.assertEqual(events, [])

    def test_zero_delay_does_not_call_sleep(self):
        events = []
        controller = PowerController(
            _FakePowerSupply(),
            ["A", "B"],
            inter_channel_delay_s=0.0,
            sleep_fn=lambda seconds: events.append(seconds),
        )

        controller.power_on()

        self.assertEqual(events, [])

    def test_failure_stops_later_channels_and_reverses_cleanup(self):
        supply = _FakePowerSupply(fail_on={"C"}, fail_off={"B"})
        controller = PowerController(supply, ["A", "B", "C", "D"])

        with self.assertRaises(PowerOnControlError) as raised:
            controller.power_on()

        # 只开启到失败项为止；没有开启 D。失败通道 C 的命令已经发出，可能已经
        # 在设备侧生效，因此同样要尝试关闭；逆序为 C、B、A。
        self.assertEqual(
            [(channel, enabled) for channel, enabled, _timeout in supply.commands],
            [
                ("A", True),
                ("B", True),
                ("C", True),
                ("C", False),
                ("B", False),
                ("A", False),
            ],
        )
        self.assertEqual(
            [failure.action for failure in raised.exception.failures],
            ["C", "B"],
        )
        self.assertIs(raised.exception.category, ErrorCategory.MEASUREMENT)

    def test_channel_left_on_after_failed_rollback_can_be_retried(self):
        """关断失败的通道保留在控制器里，下一次关断只重试这些通道。"""
        supply = _FakePowerSupply(fail_on={"B"}, fail_off={"B"})
        controller = PowerController(supply, ["A", "B"])

        with self.assertRaises(PowerOnControlError):
            controller.power_on()

        self.assertEqual(controller.remaining_channels, ("B",))
        supply.fail_off = set()
        self.assertEqual(controller.power_off(), ("B",))
        self.assertEqual(controller.remaining_channels, ())
        self.assertEqual(
            [(channel, enabled) for channel, enabled, _timeout in supply.commands],
            [("A", True), ("B", True), ("B", False), ("A", False), ("B", False)],
        )

    def test_power_off_attempts_every_channel_after_one_failure(self):
        supply = _FakePowerSupply(fail_off={"B"})
        controller = PowerController(supply, ["A", "B", "C"])
        controller.power_on()

        with self.assertRaises(PowerControlError) as raised:
            controller.power_off()

        self.assertEqual(
            [(channel, enabled) for channel, enabled, _timeout in supply.commands],
            [
                ("A", True),
                ("B", True),
                ("C", True),
                ("C", False),
                ("B", False),
                ("A", False),
            ],
        )
        self.assertEqual(
            [failure.action for failure in raised.exception.failures], ["B"]
        )

    def test_power_off_uses_the_caller_timeout_for_every_channel(self):
        supply = _FakePowerSupply()
        controller = PowerController(supply, ["A", "B"])
        controller.power_on()

        controller.power_off(timeout_s=7.5)

        off_timeouts = [
            timeout for channel, enabled, timeout in supply.commands if not enabled
        ]
        self.assertEqual(off_timeouts, [7.5, 7.5])

    def test_cancellation_before_a_channel_cleans_the_channels_already_on(self):
        calls = []

        def check():
            calls.append(len(calls))
            if len(calls) == 3:  # 第一次通道开启后的等待结束时取消
                raise _Cancel("user stop")

        supply = _FakePowerSupply()
        controller = PowerController(
            supply, ["A", "B"], sleep_fn=lambda _seconds: None
        )

        with self.assertRaises(_Cancel):
            controller.power_on(check_cancelled=check)

        self.assertEqual(
            [(channel, enabled) for channel, enabled, _timeout in supply.commands],
            [("A", True), ("A", False)],
        )

    def test_cancellation_before_the_first_channel_touches_no_output(self):
        supply = _FakePowerSupply()
        controller = PowerController(supply, ["A", "B"])

        def check():
            raise _Cancel("stop")

        with self.assertRaises(_Cancel):
            controller.power_on(check_cancelled=check)

        self.assertEqual(supply.commands, [])

    def test_cancellation_cleanup_failure_is_attached_to_the_original_exception(self):
        calls = []

        def check():
            calls.append(len(calls))
            if len(calls) == 2:  # 第一通道开启后取消
                raise _Cancel("user stop")

        supply = _FakePowerSupply(fail_off={"A"})
        controller = PowerController(supply, ["A"], sleep_fn=lambda _seconds: None)

        with self.assertRaises(_Cancel) as raised:
            controller.power_on(check_cancelled=check)

        # 取消类型不被包装；清理失败以结构化记录附加，调用方仍能逐项报告。
        self.assertIsInstance(raised.exception, _Cancel)
        failures = cleanup_failures_of(raised.exception)
        self.assertEqual([failure.action for failure in failures], ["A"])

    def test_plan_rejects_duplicate_routes_and_out_of_range_delay(self):
        with self.assertRaisesRegex(ValueError, "不能重复"):
            PowerGroupPlan.from_channels(["S1/CH1", "S1/CH1"])
        with self.assertRaisesRegex(ValueError, "不能超过"):
            PowerGroupPlan.from_channels(["S1/CH1"], inter_channel_delay_s=61)


class AdapterPlanTests(unittest.TestCase):
    def _session(self):
        recorder = CommandRecorder()
        session = ManagedInstrumentSession(
            SimulatedSignalGenerator(recorder),
            SimulatedSpectrumAnalyzer(recorder=recorder),
            SimulatedPowerSupply(recorder),
        )
        session.validate()
        session.connect()
        session.prepare()
        return session, recorder

    def test_setup_and_power_use_the_plan_in_order(self):
        session, recorder = self._session()
        sleeps = []
        adapter = PortMeasurementAdapter(
            session,
            dut_power_plan=PowerGroupPlan(
                channels=(
                    PowerChannelStep("S1/CH2", 28.0, 2.0),
                    PowerChannelStep("S2/CH1", 2.8, 0.1),
                ),
                inter_channel_delay_s=1.0,
            ),
            sleep_fn=lambda seconds: sleeps.append(seconds),
        )

        adapter.setup_dut_power()
        adapter.power_on_sequence()
        adapter.power_off_sequence()

        actions = [
            (action, value)
            for device, action, value in recorder.commands
            if device == "power_supply" and action.startswith(("set_", "output_"))
        ]
        self.assertEqual(
            actions,
            [
                ("set_voltage", ("S1/CH2", 28.0)),
                ("set_current_limit", ("S1/CH2", 2.0)),
                ("set_voltage", ("S2/CH1", 2.8)),
                ("set_current_limit", ("S2/CH1", 0.1)),
                ("output_on", "S1/CH2"),
                ("output_on", "S2/CH1"),
                ("output_off", "S2/CH1"),
                ("output_off", "S1/CH2"),
            ],
        )
        self.assertEqual(sleeps, [1.0])

    def test_adapter_uses_the_injected_cancellation_check(self):
        session, recorder = self._session()
        adapter = PortMeasurementAdapter(
            session,
            dut_power_plan=PowerGroupPlan.from_channels(["S1/CH1"]),
            cancellation_check=lambda: (_ for _ in ()).throw(_Cancel("stop")),
        )

        with self.assertRaises(_Cancel):
            adapter.power_on_sequence()

        self.assertNotIn(
            ("power_supply", "output_on", "S1/CH1"), recorder.commands
        )

    def test_cancellation_before_first_channel_still_registers_the_group_for_cleanup(self):
        """首个通道动作前取消也要让会话知道本组可能有输出，避免状态不一致。"""
        session, recorder = self._session()
        adapter = PortMeasurementAdapter(
            session,
            dut_power_plan=PowerGroupPlan.from_channels(["S1/CH1"]),
            cancellation_check=lambda: (_ for _ in ()).throw(_Cancel("stop")),
        )

        with self.assertRaises(_Cancel):
            adapter.power_on_sequence()

        self.assertEqual(session.state, InstrumentState.POWERED)
        self.assertNotIn(("power_supply", "output_on", "S1/CH1"), recorder.commands)
        # 会话清理按登记的计划通道做幂等关断；取消前未真正上电也要关一次。
        self.assertEqual(adapter.close_all(), [])
        self.assertIn(("power_supply", "output_off", "S1/CH1"), recorder.commands)
        self.assertEqual(session.state, InstrumentState.CLEANED)

    def test_stage_cleanup_closes_groups_in_reverse_global_order_once(self):
        session, recorder = self._session()
        adapter = PortMeasurementAdapter(
            session,
            driver_power_plan=PowerGroupPlan.from_channels(["S3/CH1", "S4/CH2"]),
            dut_power_plan=PowerGroupPlan.from_channels(["S1/CH2", "S2/CH1"]),
        )

        adapter.power_on_driver()
        adapter.power_on_sequence()
        adapter.end_stage()

        off = [
            value
            for device, action, value in recorder.commands
            if device == "power_supply" and action == "output_off"
        ]
        # 全局上电顺序是 driver -> DUT；逆序清理必须先 DUT（组内逆序）再 driver。
        self.assertEqual(off, ["S2/CH1", "S1/CH2", "S4/CH2", "S3/CH1"])

    def test_cancellation_cleanup_failure_can_be_retried_by_port_cleanup(self):
        session, recorder = self._session()
        supply = session.power_supply
        adapter = PortMeasurementAdapter(
            session,
            dut_power_plan=PowerGroupPlan.from_channels(["A"]),
            cancellation_check=lambda: (_ for _ in ()).throw(_Cancel("stop"))
            if supply.outputs.get("A")
            else None,
        )
        supply.inject_failure("output_off", once=True)

        with self.assertRaises(_Cancel) as raised:
            adapter.power_on_sequence()

        self.assertEqual(
            [failure.action for failure in cleanup_failures_of(raised.exception)],
            ["A"],
        )
        # 计划组在失败/取消时仍被登记，端口收尾会重试所有可能已有输出的通道。
        self.assertEqual(adapter.close_all(), [])
        self.assertFalse(supply.outputs["A"])
        off_actions = [
            value
            for device, action, value in recorder.commands
            if device == "power_supply" and action == "output_off"
        ]
        self.assertEqual(off_actions, ["A", "A"])

    def test_close_all_uses_the_requested_timeout_for_every_group(self):
        class _TimeoutPowerSupply(SimulatedPowerSupply):
            def __init__(self, recorder):
                super().__init__(recorder)
                self.off_timeouts = []

            def set_output_enabled(self, channel, enabled, *, timeout_s=5.0):
                if not enabled:
                    self.off_timeouts.append(timeout_s)
                return super().set_output_enabled(
                    channel, enabled, timeout_s=timeout_s
                )

        recorder = CommandRecorder()
        session = ManagedInstrumentSession(
            SimulatedSignalGenerator(recorder),
            SimulatedSpectrumAnalyzer(recorder=recorder),
            supply := _TimeoutPowerSupply(recorder),
        )
        session.validate()
        session.connect()
        session.prepare()
        adapter = PortMeasurementAdapter(
            session,
            dut_power_plan=PowerGroupPlan.from_channels(["S1/CH1"]),
        )
        adapter.power_on_sequence()

        self.assertEqual(adapter.close_all(timeout_s=7.5), [])

        self.assertEqual(supply.off_timeouts, [7.5])


if __name__ == "__main__":
    unittest.main()
