import unittest
from app.cancellation import CancellationToken
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.gui_runtime import create_cable_loss_measurement
from application.dto import (
    CableLossMeasurementRequest,
    MeasurementResult,
    MeasurementStatus,
)
from application.inputs import ResultInputReader


class MeasurementRequestAssemblyTests(unittest.TestCase):
    def test_runtime_factory_assembles_explicit_request(self):
        port = object()
        prepared = SimpleNamespace(
            configuration=object(),
            context=SimpleNamespace(run_id="run-request"),
            run_directory=Path("run-directory"),
        )
        with patch("app.gui_runtime.connect_instruments", return_value=port), patch(
            "app.gui_runtime.CableLossUseCase", return_value=object()
        ) as measurement:
            create_cable_loss_measurement("config.json", prepared_run=prepared)

        request = measurement.call_args.args[0]
        self.assertIsInstance(request, CableLossMeasurementRequest)
        self.assertIs(request.measurement_port, port)
        self.assertEqual(request.run_id, "run-request")
        self.assertEqual(request.run_directory, Path("run-directory"))
        self.assertEqual(request.config_path, Path("config.json"))
        self.assertIsInstance(request.input_reader, ResultInputReader)

    def test_runtime_factory_creates_port_when_call_does_not_inject_one(self):
        prepared = SimpleNamespace(
            configuration=object(),
            context=SimpleNamespace(run_id="run-request"),
            run_directory=Path("run-directory"),
        )
        with patch("app.gui_runtime.connect_instruments", return_value=object()), patch(
            "app.gui_runtime.CableLossUseCase", return_value=object()
        ) as measurement:
            create_cable_loss_measurement("config.json", prepared_run=prepared)
        self.assertIsNotNone(measurement.call_args.args[0].measurement_port)

    def test_port_factory_failure_preserves_original_exception(self):
        prepared = SimpleNamespace(
            configuration=object(),
            context=SimpleNamespace(run_id="run-request"),
            run_directory=Path("run-directory"),
        )
        with patch("app.gui_runtime.connect_instruments", side_effect=OSError("连接失败")):
            with self.assertRaisesRegex(OSError, "连接失败"):
                create_cable_loss_measurement("config.json", prepared_run=prepared)

    def test_request_carries_injected_runtime_dependencies(self):
        port = object()
        token = CancellationToken()
        sink = object()
        repository = object()
        prepared = SimpleNamespace(
            configuration=object(),
            context=SimpleNamespace(run_id="run-request"),
            run_directory=Path("run-directory"),
        )
        with patch("app.gui_runtime.connect_instruments", return_value=port), patch(
            "app.gui_runtime.CableLossUseCase", return_value=object()
        ) as measurement:
            create_cable_loss_measurement(
                "config.json",
                prepared_run=prepared,
                cancellation_token=token,
                event_sink=sink,
                result_repository=repository,
            )
        request = measurement.call_args.args[0]
        self.assertIs(request.cancellation_token, token)
        self.assertIs(request.event_sink, sink)
        self.assertIs(request.result_repository, repository)

    def test_explicit_input_reader_is_injected_only_into_request(self):
        port = object()
        reader = object()
        prepared = SimpleNamespace(
            configuration=object(),
            context=SimpleNamespace(run_id="run-request"),
            run_directory=Path("run-directory"),
        )
        with patch("app.gui_runtime.connect_instruments", return_value=port), patch(
            "app.gui_runtime.CableLossUseCase", return_value=object()
        ) as measurement:
            create_cable_loss_measurement(
                "config.json",
                prepared_run=prepared,
                input_reader=reader,
            )

        self.assertIs(measurement.call_args.args[0].input_reader, reader)
        self.assertNotIn("input_reader", measurement.call_args.kwargs)

    def test_request_exposes_common_runtime_shape(self):
        configuration = SimpleNamespace(
            test_plan=SimpleNamespace(
                frequencies=(900.0, 1800.0),
                start_power=-20.0,
                stop_power=5.0,
                power_step=1.0,
            )
        )
        request = CableLossMeasurementRequest(
            configuration=configuration,
            context=SimpleNamespace(run_id="run-request"),
            run_directory=Path("run-directory"),
            measurement_port=object(),
        )
        self.assertEqual(request.measurement_type, "cable_loss")
        self.assertEqual(request.frequency_range, (900.0, 1800.0))
        self.assertEqual(request.power_range, (-20.0, 5.0, 1.0))
        self.assertEqual(request.safety_options["owns_measurement_port"], False)
        with self.assertRaises(TypeError):
            request.safety_options["close_rf_on_cleanup"] = False

    def test_runtime_factory_records_owned_port_in_safety_options(self):
        prepared = SimpleNamespace(
            configuration=object(),
            context=SimpleNamespace(run_id="run-request"),
            run_directory=Path("run-directory"),
        )
        with patch("app.gui_runtime.connect_instruments", return_value=object()), patch(
            "app.gui_runtime.CableLossUseCase", return_value=object()
        ) as measurement:
            create_cable_loss_measurement("config.json", prepared_run=prepared)
        self.assertTrue(measurement.call_args.args[0].safety_options["owns_measurement_port"])

    def test_result_dto_has_normalized_fields_and_legacy_mapping_view(self):
        result = MeasurementResult.from_payload(
            "driver_power_mapping",
            {"power_mapping": {"900": {"gain": 10.0}}},
            run_id="run-result",
        )
        self.assertEqual(result.measurement_type, "driver_power_mapping")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.summary["power_mapping"]["900"]["gain"], 10.0)
        self.assertEqual(result["power_mapping"]["900"]["gain"], 10.0)
        self.assertEqual(result.to_dict()["run_id"], "run-result")

    def test_result_dto_freezes_nested_values_and_validates_status(self):
        source = {"power_mapping": {"900": [1.0]}}
        result = MeasurementResult.from_payload(
            "driver_power_mapping", source, run_id="run-result"
        )
        source["power_mapping"]["900"].append(2.0)
        self.assertEqual(result["power_mapping"]["900"], (1.0,))
        with self.assertRaises(TypeError):
            result.summary["new"] = True
        with self.assertRaises(ValueError):
            MeasurementResult(
                "driver_power_mapping",
                MeasurementStatus.FAILED,
                "run-result",
            )

    def test_specialized_request_rejects_mismatched_measurement_type(self):
        with self.assertRaises(ValueError):
            CableLossMeasurementRequest(
                configuration={},
                context=SimpleNamespace(run_id="run-request"),
                run_directory=Path("run-directory"),
                measurement_port=object(),
                measurement_type="amplifier",
            )


if __name__ == "__main__":
    unittest.main()
