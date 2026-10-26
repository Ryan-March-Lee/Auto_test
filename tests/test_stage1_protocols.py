import unittest

from app.cancellation import CancellationMode, CancellationToken, MeasurementCancelled
from app.events import CompletedEvent, ProgressEvent
from application.dto import (
    AmplifierFrequencyResult,
    AmplifierMeasurementResult,
    CableLossMeasurementResult,
    CableLossRow,
    DriverPowerMappingMeasurementResult,
    DriverPowerPoint,
    MeasurementResult,
)
from domain.models import (
    MeasurementSession,
    MeasurementState,
    RunContext,
    TestPlan,
)
from instrument.ports import InstrumentSession, InstrumentState


class MinimalInstrumentSession:
    state = InstrumentState.CREATED

    def validate(self, *, timeout_s=10.0):
        pass

    def connect(self, *, timeout_s=30.0):
        pass

    def prepare(self, *, timeout_s=30.0):
        pass

    def close(self, *, emergency=False, timeout_s=30.0):
        pass


class Stage1ProtocolTests(unittest.TestCase):
    def test_domain_models_are_pure_and_versioned(self):
        plan = TestPlan(frequencies_hz=(1_000_000_000.0,))
        context = RunContext(run_id="run-1", software_version="test", wiring_confirmed=True)
        result = CableLossMeasurementResult(
            run_id=context.run_id,
            points=(CableLossRow(frequency_ghz="1.0", cable_losses={"cable1": 0.5}),),
            configuration_snapshot={"schema_version": plan.schema_version},
            device_summary={"run_id": context.run_id},
        )
        self.assertEqual(plan.to_dict()["frequencies_hz"], [1_000_000_000.0])
        self.assertEqual(context.to_dict()["run_id"], "run-1")
        self.assertEqual(result.to_dict()["run_id"], "run-1")
        self.assertEqual(result.to_dict()["measurement_type"], "cable_loss")
        self.assertEqual(InstrumentState.CREATED.value, "created")

    def test_run_context_copies_and_freezes_resource_mappings(self):
        mapping = {"gate": "CH1"}
        context = RunContext(power_channel_mapping=mapping)
        mapping["gate"] = "CH2"
        self.assertEqual(context.power_channel_mapping["gate"], "CH1")
        with self.assertRaises(TypeError):
            context.power_channel_mapping["gate"] = "CH3"

    def test_specific_result_models_keep_measurement_contracts_explicit(self):
        cable = CableLossMeasurementResult(
            run_id="run-1",
            points=(CableLossRow(frequency_ghz="1.0", cable_losses={"cable1": 2.0}),),
        )
        driver = DriverPowerMappingMeasurementResult(
            run_id="run-1",
            points=(DriverPowerPoint(frequency_ghz="1.0", input_power_dbm=0.0, output_power_dbm=19.0),),
        )
        amplifier = AmplifierMeasurementResult(
            run_id="run-1",
            frequencies=(
                AmplifierFrequencyResult(
                    frequency_ghz="1.0",
                    small_signal_gain_db=30.0,
                    sweep_data={"gain": [30.0], "efficiency": [50.0]},
                ),
            ),
        )
        self.assertEqual(cable.to_dict()["measurement_type"], "cable_loss")
        self.assertEqual(driver.points[0].output_power_dbm, 19.0)
        self.assertEqual(amplifier.sweep_rows()["1.0"][0]["efficiency"], 50.0)
        self.assertIsInstance(cable, MeasurementResult)

    def test_cancellation_preserves_emergency_semantics(self):
        token = CancellationToken()
        token.request_stop(reason="用户停止")
        token.request_emergency_stop(reason="保护触发")
        self.assertEqual(token.mode, CancellationMode.EMERGENCY)
        with self.assertRaises(MeasurementCancelled) as raised:
            token.raise_if_cancelled()
        self.assertEqual(raised.exception.reason, "保护触发")

    def test_emergency_cancellation_cannot_be_downgraded(self):
        token = CancellationToken()
        token.request_emergency_stop(reason="窗口关闭")
        token.request_stop(reason="普通停止")
        self.assertEqual(token.mode, CancellationMode.EMERGENCY)

    def test_events_are_plain_python_values(self):
        progress = ProgressEvent(run_id="run-1", fraction=0.5, stage="scan")
        completed = CompletedEvent(run_id="run-1", result_id="result-1")
        self.assertEqual(progress.run_id, completed.run_id)
        self.assertIsInstance(progress, ProgressEvent)

    def test_measurement_session_records_terminal_state(self):
        session = MeasurementSession(run_id="run-1")
        session.validate()
        session.connect()
        session.prepare()
        session.power_on()
        session.start()
        session.stop("用户停止")
        self.assertEqual(session.state, MeasurementState.STOPPING)
        session.clean()
        self.assertEqual(session.state, MeasurementState.CLEANED)

    def test_measurement_session_rejects_invalid_transitions(self):
        session = MeasurementSession(run_id="run-1")
        with self.assertRaises(ValueError):
            session.complete()
        session.validate()
        with self.assertRaises(ValueError):
            session.start()

    def test_measurement_session_completes_then_cleans(self):
        session = MeasurementSession(run_id="run-1")
        session.validate()
        session.connect()
        session.prepare()
        session.start()
        session.complete()
        self.assertEqual(session.state, MeasurementState.COMPLETED)
        session.clean()
        self.assertEqual(session.state, MeasurementState.CLEANED)

    def test_instrument_session_is_a_structural_protocol(self):
        self.assertIsInstance(MinimalInstrumentSession(), InstrumentSession)


if __name__ == "__main__":
    unittest.main()
