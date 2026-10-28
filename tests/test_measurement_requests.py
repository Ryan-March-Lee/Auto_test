import unittest
from app.cancellation import CancellationToken
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.gui_runtime import create_cable_loss_measurement
from instrument.flow.cleanup import SafetyCleanupAdapter
from application.dto import (
    CableLossMeasurementRequest,
    CableLossMeasurementResult,
    DriverPowerMappingMeasurementResult,
    MeasurementResult,
    MeasurementStatus,
    MeasurementType,
)
from application.inputs import ResultInputReader
from application.run_service import configuration_snapshot
from domain.configuration.models import (
    ChannelMapping,
    InstrumentMapping,
    PowerChannelPlan,
    RunConfiguration,
    RunResourceMapping,
    TestPlan,
)
from domain.run.models import HARDWARE_CONFIG_INPUT_PATH


def _valid_domain_snapshot(run_id: str) -> dict:
    """构造一份可通过完整运行校验的领域配置快照（供契约替身返回）。"""
    plan = TestPlan(
        frequencies=[2.4],
        start_power=-10.0,
        stop_power=0.0,
        power_step=1.0,
        compression_point="5dB",
        attenuator_value=30.0,
        max_input_power=30.0,
        dut_power_channels={
            "gate": PowerChannelPlan(role="gate", voltage=2.0, current=1.0)
        },
        driver_enabled=False,
    )
    mapping = RunResourceMapping(
        instruments={
            "signal_generator": InstrumentMapping("SG", "sim::sg"),
            "spectrum_analyzer": InstrumentMapping("SA", "sim::sa"),
            "power_supply": InstrumentMapping("PS", "sim::ps"),
        },
        dut_power_channels=[
            ChannelMapping(channel="CH1", role="gate", connection="gate")
        ],
        wiring_confirmed=True,
        connection_note="测试接线已确认",
    )
    return configuration_snapshot(RunConfiguration(plan, mapping), run_id=run_id)


class _VerifiedContractRepository:
    """组合层测试用的最小运行契约仓储替身：校验能力完整，返回固定快照内容。

    运行契约仓储必须具备 ``read_config`` / ``read_run_json`` / ``load_manifest`` /
    ``verify_file``，否则组合层会拒绝继续；归档服务还要求 ``update_manifest`` 与
    ``verify_artifact`` 可调用。这里只用于验证组合层在快照可用时能正常组装，因此直接
    返回已校验的内容而不触碰磁盘。
    """

    def __init__(self, *, config_payload, hardware_payload):
        self.config_payload = config_payload
        self.hardware_payload = hardware_payload
        self.read_run_json_paths = []

    def read_config(self, run_id, *, verify=True):
        return self.config_payload

    def read_run_json(self, run_id, relative_path, *, verify=True):
        self.read_run_json_paths.append(relative_path)
        return self.hardware_payload

    def load_manifest(self, run_id):
        raise AssertionError("该替身不通过 manifest 读取硬件快照")

    def update_manifest(self, run_id, manifest, *, now=None):
        raise AssertionError("该替身不写回 manifest；本测试只验证组装")

    def verify_file(self, run_id, reference):
        raise AssertionError("该替身不通过 manifest 校验文件")

    def verify_artifact(self, run_id, artifact):
        raise AssertionError("该替身不校验交付产物；本测试只验证组装")



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
        self.assertIsInstance(request.safety_cleanup, SafetyCleanupAdapter)
        self.assertIs(request.safety_cleanup.instrument_control, port)
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

    def test_run_contract_prepared_run_injects_stage_recorder(self):
        # 运行契约准备对象必须由能力完整的仓储提供，组合层才会执行快照校验并组装。
        repository = _VerifiedContractRepository(
            config_payload=_valid_domain_snapshot("run-contract"),
            hardware_payload={"instruments": {}},
        )
        prepared = SimpleNamespace(
            configuration=object(),
            context=SimpleNamespace(run_id="run-contract"),
            run_directory=Path("runs") / "run-contract",
            run_repository=repository,
            hardware_config_path=Path("runs") / "run-contract" / "inputs" / "hardware_config.json",
        )
        with patch("app.gui_runtime.connect_instruments", return_value=object()), patch(
            "app.gui_runtime.CableLossUseCase", return_value=object()
        ) as measurement:
            create_cable_loss_measurement("config.json", prepared_run=prepared)

        recorder = measurement.call_args.args[0].stage_recorder
        self.assertIsNotNone(recorder)
        self.assertEqual(recorder.run_id, "run-contract")
        # 运行契约运行同时装配自动归档服务，旧兼容流程不装配。
        self.assertIsNotNone(measurement.call_args.args[0].archival_service)
        # 硬件快照通过统一入口按已登记 JSON 读取，而不是再次解析领域配置。
        self.assertEqual(repository.read_run_json_paths, [HARDWARE_CONFIG_INPUT_PATH])

    def test_legacy_prepared_run_has_no_stage_recorder(self):
        prepared = SimpleNamespace(
            configuration=object(),
            context=SimpleNamespace(run_id="run-compat-snapshot"),
            run_directory=Path("run-directory"),
        )
        with patch("app.gui_runtime.connect_instruments", return_value=object()), patch(
            "app.gui_runtime.CableLossUseCase", return_value=object()
        ) as measurement:
            create_cable_loss_measurement("config.json", prepared_run=prepared)

        self.assertIsNone(measurement.call_args.args[0].stage_recorder)
        self.assertIsNone(measurement.call_args.args[0].archival_service)

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

    def test_result_dto_has_explicit_fields_without_mapping_view(self):
        result = DriverPowerMappingMeasurementResult.from_service_payload(
            {"power_mapping": {"900.0": {"-40.0": 10.0}}},
            run_id="run-result",
        )
        self.assertIs(result.measurement_type, MeasurementType.DRIVER_POWER_MAPPING)
        self.assertIs(result.status, MeasurementStatus.COMPLETED)
        self.assertEqual(result.points[0].frequency_ghz, "900.0")
        self.assertEqual(result.points[0].input_power_dbm, -40.0)
        self.assertEqual(result.points[0].output_power_dbm, 10.0)
        self.assertEqual(result.to_dict()["run_id"], "run-result")
        with self.assertRaises(TypeError):
            result["power_mapping"]

    def test_result_dto_freezes_nested_values_and_validates_status(self):
        source = {"power_mapping": {"900.0": {"-40.0": 1.0}}}
        result = DriverPowerMappingMeasurementResult.from_service_payload(
            source, run_id="run-result"
        )
        source["power_mapping"]["900.0"]["-40.0"] = 2.0
        self.assertEqual(result.points[0].output_power_dbm, 1.0)
        with self.assertRaises(TypeError):
            result.configuration_snapshot["new"] = True
        with self.assertRaises(ValueError):
            MeasurementResult(
                measurement_type="driver_power_mapping",
                status=MeasurementStatus.FAILED,
                run_id="run-result",
            )
        with self.assertRaises(ValueError):
            MeasurementResult(
                measurement_type="driver_power_mapping",
                status="stopped",
                run_id="run-result",
            )

    def test_result_dto_records_structured_termination(self):
        result = MeasurementResult(
            measurement_type="amplifier",
            status=MeasurementStatus.CANCELLED,
            run_id="run-result",
            termination={"intent": "cancel", "reason": "任务已取消"},
        )
        self.assertEqual(result.termination["intent"], "cancel")
        self.assertEqual(result.to_dict()["termination"]["reason"], "任务已取消")
        emergency = MeasurementResult(
            measurement_type="amplifier",
            status=MeasurementStatus.EMERGENCY_STOPPED,
            run_id="run-emergency",
            termination={"intent": "emergency_stop", "reason": "过流保护"},
        )
        self.assertEqual(emergency.termination["intent"], "emergency_stop")

    def test_result_dto_keeps_explicit_references(self):
        result = MeasurementResult(
            measurement_type="amplifier",
            status=MeasurementStatus.COMPLETED,
            run_id="run-reference",
            raw_data_reference={"raw_data_path": "raw.csv"},
            result_reference={"archive_path": "archive.json"},
            errors=("warning-1", "warning-2"),
            cleanup_records=({"power_off": "ok"}, {"close": "ok"}),
        )
        self.assertEqual(result.raw_data_reference["raw_data_path"], "raw.csv")
        self.assertEqual(result.result_reference["archive_path"], "archive.json")
        self.assertEqual(result.errors, ("warning-1", "warning-2"))
        self.assertEqual(len(result.cleanup_records), 2)
        saved = result.with_saved_result(archive_path="new-archive.json")
        self.assertEqual(saved.result_reference["archive_path"], "new-archive.json")
        self.assertEqual(saved.raw_data_reference["raw_data_path"], "raw.csv")

    def test_service_status_alias_is_normalized_by_the_result_builder(self):
        waiting = CableLossMeasurementResult.from_service_payload(
            {"status": "waiting"}, run_id="run-result"
        )
        self.assertIs(waiting.status, MeasurementStatus.WAITING_FOR_CONTINUE)

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
