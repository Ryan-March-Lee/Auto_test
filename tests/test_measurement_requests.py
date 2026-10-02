import unittest
from app.cancellation import CancellationToken
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.gui_runtime import create_cable_loss_measurement
from application.dto import CableLossMeasurementRequest
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


if __name__ == "__main__":
    unittest.main()
