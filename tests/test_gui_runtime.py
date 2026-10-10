import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.gui_runtime import (
    create_amplifier_measurement,
    create_cable_loss_measurement,
    create_driver_mapping_measurement,
    prepare_configuration,
)
from application.dto import AmplifierMeasurementRequest
from application.inputs import ResultInputReader
from application.measurements import (
    AmplifierMeasurementUseCase,
    CableLossUseCase,
)


FIXTURE = Path(__file__).parent / "fixtures" / "config_driver_enabled_no_assignment.json"


class _Port:
    def __init__(self):
        self.close_calls = []

    def close_all(self, *, close_rf=False):
        self.close_calls.append(close_rf)


def _prepared_run():
    return SimpleNamespace(
        configuration=object(),
        context=SimpleNamespace(run_id="run-assembly-test"),
        run_directory=Path("run-directory"),
    )


class GuiRuntimeAssemblyTests(unittest.TestCase):
    def test_application_assembly_does_not_import_legacy_workers(self):
        runtime_source = Path(__file__).parents[1] / "app" / "gui_runtime.py"
        source = runtime_source.read_text(encoding="utf-8")
        self.assertNotIn("enhanced_workers", source)
        self.assertNotIn("EnhancedAmplifierMeasurement", source)
        self.assertNotIn("EnhancedCableLossMeasurement", source)

    def test_default_factories_create_and_inject_hardware_port(self):
        cases = (
            (create_cable_loss_measurement, "CableLossUseCase"),
            (create_driver_mapping_measurement, "DriverPowerMappingUseCase"),
            (create_amplifier_measurement, "AmplifierMeasurementUseCase"),
        )
        for factory, measurement_name in cases:
            with self.subTest(factory=factory.__name__):
                port = _Port()
                expected = object()
                with patch("app.gui_runtime.connect_instruments", return_value=port) as connect, \
                        patch(f"app.gui_runtime.{measurement_name}", return_value=expected) as measurement:
                    result = factory("config.json", prepared_run=_prepared_run())
                self.assertIs(result, expected)
                connect.assert_called_once_with("config.json")
                request = measurement.call_args.args[0]
                self.assertIs(request.measurement_port, port)
                self.assertEqual(request.run_id, "run-assembly-test")
                self.assertEqual(request.run_directory, Path("run-directory"))
                self.assertTrue(request.safety_options["owns_measurement_port"])
                self.assertEqual(port.close_calls, [])

    def test_successful_factory_handoff_does_not_close_port_early(self):
        cases = (
            (create_cable_loss_measurement, "CableLossUseCase"),
            (create_driver_mapping_measurement, "DriverPowerMappingUseCase"),
            (create_amplifier_measurement, "AmplifierMeasurementUseCase"),
        )
        for factory, use_case_name in cases:
            with self.subTest(factory=factory.__name__):
                port = _Port()
                service = SimpleNamespace()
                with patch("app.gui_runtime.connect_instruments", return_value=port), patch(
                    f"app.gui_runtime.{use_case_name}", return_value=service
                ) as use_case:
                    result = factory("config.json", prepared_run=_prepared_run())

                self.assertIs(result, service)
                self.assertIs(use_case.call_args.args[0].measurement_port, port)
                self.assertTrue(use_case.call_args.args[0].safety_options["owns_measurement_port"])
                self.assertEqual(port.close_calls, [])
                port.close_all(close_rf=True)
                self.assertEqual(port.close_calls, [True])

    def test_default_factory_closes_owned_port_when_measurement_construction_fails(self):
        cases = (
            (create_cable_loss_measurement, "CableLossUseCase"),
            (create_driver_mapping_measurement, "DriverPowerMappingUseCase"),
            (create_amplifier_measurement, "AmplifierMeasurementUseCase"),
        )
        for factory, use_case_name in cases:
            with self.subTest(factory=factory.__name__):
                port = _Port()
                with patch("app.gui_runtime.connect_instruments", return_value=port), patch(
                    f"app.gui_runtime.{use_case_name}",
                    side_effect=OSError("use case construction failed"),
                ):
                    with self.assertRaisesRegex(OSError, "use case construction failed"):
                        factory("config.json", prepared_run=_prepared_run())
                self.assertEqual(port.close_calls, [True])

    def test_default_factory_does_not_close_injected_port_on_construction_failure(self):
        cases = (
            (create_cable_loss_measurement, "CableLossUseCase"),
            (create_driver_mapping_measurement, "DriverPowerMappingUseCase"),
            (create_amplifier_measurement, "AmplifierMeasurementUseCase"),
        )
        for factory, use_case_name in cases:
            with self.subTest(factory=factory.__name__):
                port = _Port()
                with patch("app.gui_runtime.connect_instruments") as connect, patch(
                    f"app.gui_runtime.{use_case_name}",
                    side_effect=OSError("use case construction failed"),
                ):
                    with self.assertRaisesRegex(OSError, "use case construction failed"):
                        factory(
                            "config.json",
                            prepared_run=_prepared_run(),
                            measurement_port=port,
                        )
                connect.assert_not_called()
                self.assertEqual(port.close_calls, [])

    def test_all_measurement_factories_inject_the_same_external_port(self):
        port = _Port()
        expected = object()
        cases = (
            (create_cable_loss_measurement, "CableLossUseCase"),
            (create_driver_mapping_measurement, "DriverPowerMappingUseCase"),
            (create_amplifier_measurement, "AmplifierMeasurementUseCase"),
        )
        for factory, measurement_name in cases:
            with self.subTest(factory=factory.__name__):
                with patch("app.gui_runtime.connect_instruments") as connect, patch(
                    f"app.gui_runtime.{measurement_name}", return_value=expected
                ) as measurement:
                    result = factory(
                        "config.json",
                        prepared_run=_prepared_run(),
                        measurement_port=port,
                    )

                self.assertIs(result, expected)
                connect.assert_not_called()
                self.assertIs(measurement.call_args.args[0].measurement_port, port)
                self.assertFalse(measurement.call_args.args[0].safety_options["owns_measurement_port"])
                self.assertEqual(port.close_calls, [])

    def test_explicit_driver_mapping_path_is_carried_into_request(self):
        port = _Port()
        explicit_path = Path("provided") / "mapping.json"
        with patch("app.gui_runtime.connect_instruments") as connect, patch(
            "app.gui_runtime.AmplifierMeasurementUseCase", return_value=object()
        ) as measurement:
            create_amplifier_measurement(
                "config.json",
                prepared_run=_prepared_run(),
                measurement_port=port,
                driver_mapping_path=explicit_path,
            )

        request = measurement.call_args.args[0]
        self.assertIsInstance(request, AmplifierMeasurementRequest)
        self.assertEqual(request.config_path, Path("config.json"))
        self.assertEqual(request.driver_mapping_path, explicit_path)
        self.assertNotIn("driver_mapping_path", measurement.call_args.kwargs)
        connect.assert_not_called()
        self.assertEqual(port.close_calls, [])

    def test_assembled_explicit_driver_mapping_is_read_without_latest_scan(self):
        port = _Port()
        explicit_path = Path("provided") / "mapping.json"
        repository = SimpleNamespace(
            load=lambda path: {"power_mapping": {"1.0": 2.0}},
            latest_path=lambda _result_type: self.fail(
                "显式驱动映射路径不应扫描最新结果"
            ),
        )
        reader = ResultInputReader(repository)
        observed = {}

        def construct_measurement(request, **_legacy_kwargs):
            observed["mapping"] = request.input_reader.read_driver_mapping(
                request.driver_mapping_path
            )
            return request

        with patch("app.gui_runtime.connect_instruments") as connect, patch(
            "app.gui_runtime.AmplifierMeasurementUseCase",
            side_effect=construct_measurement,
        ):
            result = create_amplifier_measurement(
                "config.json",
                prepared_run=_prepared_run(),
                measurement_port=port,
                input_reader=reader,
                driver_mapping_path=explicit_path,
            )

        self.assertEqual(observed["mapping"], {"1.0": 2.0})
        self.assertIs(result.input_reader, reader)
        self.assertEqual(result.driver_mapping_path, explicit_path)
        connect.assert_not_called()
        self.assertEqual(port.close_calls, [])

    def test_assembly_error_from_request_creation_closes_only_owned_port(self):
        owned_port = _Port()
        with patch("app.gui_runtime.connect_instruments", return_value=owned_port), patch(
            "app.gui_runtime.DriverPowerMappingUseCase", return_value=object()
        ), patch(
            "app.gui_runtime.DriverPowerMappingRequest",
            side_effect=RuntimeError("request assembly failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "request assembly failed"):
                create_driver_mapping_measurement(
                    "config.json", prepared_run=_prepared_run()
                )
        self.assertEqual(owned_port.close_calls, [True])

class GuiRuntimePreparationTests(unittest.TestCase):
    def _write_config(self, *, confirmed=True):
        config = json.loads(FIXTURE.read_text(encoding="utf-8"))
        config["wiring"] = {
            "confirmed": confirmed,
            "connection_note": "confirmed" if confirmed else None,
        }
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / "config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        return directory, path

    def test_cable_loss_preparation_uses_operation_specific_validation(self):
        directory, path = self._write_config()
        try:
            prepared = SimpleNamespace(configuration=object(), context=object(), run_directory=Path("run"))
            with patch("app.gui_runtime.prepare_run", return_value=prepared) as prepare_run:
                result = prepare_configuration(str(path), operation="cable_loss")
            self.assertIs(result, prepared)
            loaded = prepare_run.call_args.args[0]
            self.assertTrue(loaded.valid)
        finally:
            directory.cleanup()

    def test_driver_mapping_preparation_allows_external_driver_power(self):
        directory, path = self._write_config()
        try:
            prepared = SimpleNamespace(configuration=object(), context=object(), run_directory=Path("run"))
            with patch("app.gui_runtime.prepare_run", return_value=prepared) as prepare_run:
                result = prepare_configuration(str(path), operation="driver_mapping")
            self.assertIs(result, prepared)
            loaded = prepare_run.call_args.args[0]
            self.assertTrue(loaded.valid)
        finally:
            directory.cleanup()

    def test_cable_loss_preparation_rejects_missing_wiring_confirmation(self):
        directory, path = self._write_config(confirmed=False)
        try:
            with self.assertRaisesRegex(ValueError, "wiring.confirmed"):
                prepare_configuration(str(path), operation="cable_loss")
        finally:
            directory.cleanup()

    def test_unknown_operation_is_rejected(self):
        directory, path = self._write_config()
        try:
            with self.assertRaisesRegex(ValueError, "不支持的测量类型"):
                prepare_configuration(str(path), operation="unknown")
        finally:
            directory.cleanup()

    def test_preparation_accepts_an_injected_configuration_repository(self):
        directory, path = self._write_config()
        try:
            repository = MockConfigurationRepository()
            prepared = SimpleNamespace(configuration=object(), context=object(), run_directory=Path("run"))
            with patch("app.gui_runtime.prepare_run", return_value=prepared):
                result = prepare_configuration(str(path), repository=repository)
            self.assertIs(result, prepared)
            self.assertEqual(repository.loaded_paths, [(str(path), None)])
        finally:
            directory.cleanup()


class MockConfigurationRepository:
    def __init__(self):
        self.loaded_paths = []

    def load(self, path, mapping_path=None):
        from application.ports.config_repository import ConfigurationLoadResult
        from domain.configuration.models import (
            RunConfiguration,
            RunResourceMapping,
            TestPlan,
            validate_run_configuration,
        )

        self.loaded_paths.append((path, mapping_path))
        configuration = RunConfiguration(TestPlan(), RunResourceMapping())
        return ConfigurationLoadResult(configuration, validate_run_configuration(configuration))

    def load_legacy_data(self, value):
        raise AssertionError("应用服务不应直接调用 legacy 转换入口")

    def load_for_run(self, path, mapping_path=None):
        raise AssertionError("应用服务应通过 load 后自行执行运行前门禁")


if __name__ == "__main__":
    unittest.main()
