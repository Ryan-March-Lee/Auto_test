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
from application.run_service import RunNotResumable


FIXTURE = Path(__file__).parent / "fixtures" / "config_driver_enabled_no_assignment.json"


FULLY_VALID_CONFIG = {
    "test_frequencies": [2.4],
    "signal_source": {"start_power": -10.0, "stop_power": 0.0, "step": 1.0},
    "compression_point": {"type": "5dB"},
    "driver_mode": {"enabled": False},
    "attenuator": {"type": "30dB"},
    "dut_config": {"max_input_power": 30.0, "power_supply_count": 1},
    "instruments": {
        "signal_generator": {"address": "sim::sg", "model": "SG", "enabled": True},
        "spectrum_analyzer": {"address": "sim::sa", "model": "SA", "enabled": True},
        "power_supplies": {
            "PS1": {
                "address": "sim::ps1",
                "enabled": True,
                "channels": {
                    "CH1": {"voltage": {"value": 2.8}, "current": {"value": 0.1}},
                    "CH2": {"voltage": {"value": 28.0}, "current": {"value": 2.0}},
                },
            }
        },
    },
    "power_supply_assignment": {
        "driver_amplifier": {"power_supply_count": 0, "supplies": {}},
        "dut_amplifier": {
            "power_supply_count": 1,
            "supplies": {"carrier": {"name": "PS1", "channel": ["CH1", "CH2"]}},
        },
    },
    "wiring": {"confirmed": True, "connection_note": "现场接线已确认"},
}


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


def _contract_run_stub(*, hardware_config_path, run_repository=None):
    """运行契约替身：把 :class:`PreparedRun` 的方法作为类属性绑定，行为与生产一致。"""
    from app.run_context import PreparedRun

    return type(
        "ContractRunStub",
        (),
        {
            "run_id": "run-contract",
            "context": SimpleNamespace(run_id="run-contract"),
            "run_directory": Path("runs") / "run-contract",
            "run_repository": run_repository if run_repository is not None else object(),
            "has_run_contract": PreparedRun.has_run_contract,
            "hardware_config_path": hardware_config_path,
            "hardware_connection_path": PreparedRun.hardware_connection_path,
            "_verified_hardware_snapshot": PreparedRun._verified_hardware_snapshot,
        },
    )()


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

    def test_contract_run_without_hardware_snapshot_is_rejected_before_connecting(self):
        # 运行契约存在却没有冻结硬件路径：组合层必须明确失败，绝不回退全局配置。
        prepared = SimpleNamespace(
            configuration=object(),
            context=SimpleNamespace(run_id="run-contract"),
            run_directory=Path("runs") / "run-contract",
            run_repository=object(),
            hardware_config_path=None,
        )
        with patch("app.gui_runtime.connect_instruments") as connect:
            with self.assertRaisesRegex(RunNotResumable, "硬件配置快照"):
                create_cable_loss_measurement("config.json", prepared_run=prepared)
        connect.assert_not_called()

    def test_unusable_contract_snapshot_blocks_every_measurement_factory(self):
        cases = (
            (create_cable_loss_measurement, "CableLossUseCase"),
            (create_driver_mapping_measurement, "DriverPowerMappingUseCase"),
            (create_amplifier_measurement, "AmplifierMeasurementUseCase"),
        )
        snapshots = {
            "空路径": Path(""),
            "快照文件不存在": Path("missing-hardware.json"),
            "快照路径指向目录": Path(__file__).parent,
        }
        for factory, use_case_name in cases:
            for label, snapshot in snapshots.items():
                with self.subTest(factory=factory.__name__, case=label):
                    prepared = _contract_run_stub(hardware_config_path=snapshot)
                    with patch("app.gui_runtime.connect_instruments") as connect, patch(
                        f"app.gui_runtime.{use_case_name}", return_value=object()
                    ):
                        with self.assertRaisesRegex(RunNotResumable, "硬件配置快照"):
                            factory("config.json", prepared_run=prepared)
                    connect.assert_not_called()

    def test_legacy_assembly_still_uses_the_caller_config_path(self):
        # 不带运行契约的旧兼容快照继续使用调用方传入的全局配置路径。
        prepared = SimpleNamespace(
            configuration=object(),
            context=SimpleNamespace(run_id="run-legacy"),
            run_directory=Path("run-directory"),
        )
        port = _Port()
        with patch("app.gui_runtime.connect_instruments", return_value=port) as connect, patch(
            "app.gui_runtime.CableLossUseCase", return_value=object()
        ):
            create_cable_loss_measurement("config.json", prepared_run=prepared)
        connect.assert_called_once_with("config.json")

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

    def _write_fully_valid_config(self):
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / "config.json"
        path.write_text(json.dumps(FULLY_VALID_CONFIG), encoding="utf-8")
        return directory, path

    def test_preparation_creates_a_run_contract_with_frozen_hardware_config(self):
        directory, path = self._write_fully_valid_config()
        try:
            prepared = SimpleNamespace(
                configuration=object(),
                context=object(),
                run_directory=Path("runs") / "run-contract",
            )
            with patch(
                "app.gui_runtime.create_prepared_run", return_value=prepared
            ) as create_prepared:
                result = prepare_configuration(str(path), run_id="run-contract")

            self.assertIs(result, prepared)
            kwargs = create_prepared.call_args.kwargs
            loaded = create_prepared.call_args.args[0]
            self.assertTrue(loaded.valid)
            self.assertEqual(kwargs["run_id"], "run-contract")
            self.assertEqual(kwargs["config_path"], path)
            # 未显式给出硬件配置时，从源配置读取并冻结进运行目录。
            self.assertEqual(
                kwargs["hardware_config"],
                json.loads(path.read_text(encoding="utf-8")),
            )
        finally:
            directory.cleanup()

    def test_preparation_requires_the_complete_run_validation(self):
        """只通过阶段专用校验的配置不再产生兼容快照，必须按完整运行校验拒绝。"""
        directory, path = self._write_fully_valid_config()
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
            # 去掉 DUT 电源分配：线损阶段专用校验不关心 DUT 角色，因此仍然通过；
            # 完整运行校验会拒绝，旧兼容快照正是把这种配置当作“预检通过”。
            config["power_supply_assignment"]["dut_amplifier"] = {
                "power_supply_count": 0,
                "supplies": {},
            }
            path.write_text(json.dumps(config), encoding="utf-8")

            from application.configuration_service import load_for_run
            from domain.configuration.models import validate_cable_loss_configuration
            from infrastructure.config.json_config_repository import (
                JsonConfigurationRepository,
            )
            from infrastructure.persistence.run_repository import RunRepository

            loaded = load_for_run(JsonConfigurationRepository(), str(path))
            self.assertTrue(
                validate_cable_loss_configuration(loaded.configuration).valid,
                "该配置应该仍通过线损阶段专用校验（差异证明点）",
            )
            self.assertFalse(loaded.valid, "完整运行校验必须拒绝该配置")

            runs_root = Path(directory.name) / "runs"
            with self.assertRaisesRegex(ValueError, "运行配置校验失败"):
                prepare_configuration(
                    str(path), run_repository=RunRepository(runs_root)
                )
            # 校验失败不得留下任何运行目录。
            self.assertFalse(runs_root.exists() and any(runs_root.iterdir()))
        finally:
            directory.cleanup()

    def test_preparation_rejects_missing_wiring_confirmation(self):
        directory, path = self._write_config(confirmed=False)
        try:
            with self.assertRaisesRegex(ValueError, "wiring.confirmed"):
                prepare_configuration(str(path))
        finally:
            directory.cleanup()

    def test_legacy_preparation_switches_are_removed(self):
        """旧的 ``run_contract`` / ``operation`` 兼容快照开关必须明确失败。"""
        directory, path = self._write_config()
        try:
            with self.assertRaises(TypeError):
                prepare_configuration(str(path), run_contract=True)
            with self.assertRaises(TypeError):
                prepare_configuration(str(path), operation="cable_loss")
        finally:
            directory.cleanup()

    def test_preparation_accepts_an_injected_configuration_repository(self):
        directory, path = self._write_config()
        try:
            repository = MockConfigurationRepository()
            prepared = SimpleNamespace(
                configuration=object(), context=object(), run_directory=Path("run")
            )
            with patch("app.gui_runtime.create_prepared_run", return_value=prepared):
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
