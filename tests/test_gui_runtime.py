import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.gui_runtime import (
    _preflight_amplifier_inputs,
    _preflight_driver_mapping_inputs,
    connect_instruments,
    create_amplifier_measurement,
    create_cable_loss_measurement,
    create_driver_mapping_measurement,
    prepare_configuration,
)
from application.dto import (
    AmplifierCalibrationInputs,
    AmplifierMeasurementRequest,
)
from application.errors import ConfigurationError
from application.inputs import ResultInputReader
from application.measurements import (
    AmplifierMeasurementUseCase,
    CableLossUseCase,
)
from application.ports.stage_result_repository import MeasurementStage
from application.run_service import RunNotResumable
from domain.configuration.models import RunConfiguration, RunResourceMapping, TestPlan
from domain.run.models import STAGE_CABLE_LOSS, STAGE_DRIVER_MAPPING
from infrastructure.filesystem.paths import DEFAULT_PATHS
from infrastructure.persistence.run_repository import RunRepository
from application.configuration_authoring import author_official_configuration





FULLY_VALID_CONFIG = author_official_configuration({
    "schema_version": "1.1",
    "test_frequencies": [2.4],
    "signal_source": {"start_power": -10.0, "stop_power": 0.0, "step": 1.0},
    "compression_point": {"type": "5dB"},
    "driver_mode": {"enabled": False},
    "attenuator": {"type": "30dB"},
    "dut_config": {
        "max_input_power": 30.0,
        "power_supply_count": 1,
        "drain_channel": {"supply_id": "PS1", "channel": "CH2"},
    },
    "instruments": {
        "signal_generator": {"address": "sim::sg", "model": "SG", "enabled": True},
        "spectrum_analyzer": {"address": "sim::sa", "model": "SA", "enabled": True},
        "power_supplies": {
            "PS1": {
                "address": "sim::ps1",
                "label": "PS1",
                "model": "DP832A",
                "full_code": "SIM1519",
                "device_key": "rigol technologies:ps1",
                "enabled": True,
                "channels": {
                    "CH1": {"voltage": {"value": 2.8}, "current": {"value": 0.1}},
                    "CH2": {"voltage": {"value": 28.0}, "current": {"value": 2.0}},
                },
            }
        },
    },
    "power_supply_assignment": {
        "driver_amplifier": {
            "power_supply_count": 0,
            "supplies": {},
            "power_plan": {"power_sequence": [], "inter_channel_delay_s": 0.0},
        },
        "dut_amplifier": {
            "power_supply_count": 1,
            "supplies": {"carrier": {"supply_id": "PS1", "channel": ["CH1", "CH2"]}},
            "power_plan": {
                "power_sequence": [
                    {"supply_id": "PS1", "channel": "CH1"},
                    {"supply_id": "PS1", "channel": "CH2"},
                ],
                "inter_channel_delay_s": 2.0,
            },
        },
    },
})


class _Port:
    def __init__(self):
        self.close_calls = []

    def close_all(self, *, close_rf=False):
        self.close_calls.append(close_rf)


class _RecordingInputReader:
    """输入读取器替身：记录显式路径，返回预置的输入文档。"""

    def __init__(self, *, cable_loss=None, driver_mapping=None):
        self.cable_loss = cable_loss
        self.driver_mapping = driver_mapping
        self.paths = []

    def read_cable_loss(self, path):
        self.paths.append(Path(path))
        return self.cable_loss

    def read_driver_mapping(self, path):
        self.paths.append(Path(path))
        return self.driver_mapping


SNAPSHOT = Path("runs") / "run-assembly-test" / "inputs" / "hardware_config.json"


def _run_configuration() -> RunConfiguration:
    """由完整 GUI 文档构造运行配置，供主功放正式输入转换使用。"""
    return RunConfiguration(
        test_plan=TestPlan.from_dict(FULLY_VALID_CONFIG["test_plan"]),
        run_mapping=RunResourceMapping.from_dict(FULLY_VALID_CONFIG["run_mapping"]),
    )


def _prepared_run():
    """运行契约替身：冻结快照解析为固定路径，用于断言装配行为。

    判据与生产一致（运行标识、运行目录、运行仓储、解析入口四项齐备），只有
    ``hardware_connection_path()`` 换成固定值，避免装配测试依赖真实运行目录的
    manifest 校验（那部分由 tests/test_run_service.py 与门禁覆盖）。
    """
    return type(
        "ContractRunStub",
        (),
        {
            "run_id": "run-assembly-test",
            "context": SimpleNamespace(run_id="run-assembly-test"),
            "configuration": _run_configuration(),
            "run_directory": Path("runs") / "run-assembly-test",
            "run_repository": object(),
            "hardware_connection_path": lambda self=None: SNAPSHOT,
        },
    )()


def _bare_run():
    """既没有运行仓储、也没有冻结快照解析入口的对象（不满足运行契约）。"""
    return SimpleNamespace(
        configuration=object(),
        context=SimpleNamespace(run_id="run-without-contract"),
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
            "configuration": _run_configuration(),
            "run_directory": Path("runs") / "run-contract",
            "run_repository": run_repository if run_repository is not None else object(),
            "has_run_contract": PreparedRun.has_run_contract,
            "hardware_config_path": hardware_config_path,
            "hardware_connection_path": PreparedRun.hardware_connection_path,
            "_verified_hardware_snapshot": PreparedRun._verified_hardware_snapshot,
        },
    )()


class GuiRuntimeAssemblyTests(unittest.TestCase):
    """组合根装配：运行契约是唯一的硬件配置来源。"""

    def setUp(self):
        # 三处前置校验各有专门用例（tests/test_*_run_stage.py 与门禁覆盖），装配测试
        # 只断言“端口与请求拿到同一份快照”，因此把它们替换成空动作。
        self.enterContext(patch("app.gui_runtime._ensure_run_snapshots_are_intact"))
        self.require_stage_inputs = self.enterContext(
            patch("app.gui_runtime._require_stage_inputs")
        )
        # 预检替身必须返回真实校准输入 DTO：正式请求会携带它交给用例复用。
        self.preflight_amplifier = self.enterContext(
            patch(
                "app.gui_runtime._preflight_amplifier_inputs",
                return_value=AmplifierCalibrationInputs(loss_data={"cable_losses": {}}),
            )
        )
        # 自动归档服务要求仓储具备 read_run_json 等能力；装配测试用的替身仓储没有
        # 这些能力，也不在本用例的验证范围内。
        self.enterContext(patch("app.gui_runtime.ArchiveArtifactGenerator"))
        self.enterContext(patch("app.gui_runtime.ResultArchivalService"))

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
                prepared = _prepared_run()
                with patch("app.gui_runtime.connect_instruments", return_value=port) as connect, \
                        patch(f"app.gui_runtime.{measurement_name}", return_value=expected) as measurement:
                    result = factory(prepared_run=prepared)
                self.assertIs(result, expected)
                connect.assert_called_once()
                self.assertIs(connect.call_args.args[0], prepared)
                # 连接入口只拿运行对象：没有配置路径关键字可传（步骤 6，D17）。
                self.assertEqual(
                    connect.call_args.kwargs,
                    {},
                    "测量工厂调用连接入口时不得附加任何配置路径",
                )
                request = measurement.call_args.args[0]
                self.assertIs(request.measurement_port, port)
                self.assertEqual(request.run_id, "run-assembly-test")
                self.assertEqual(request.run_directory, Path("runs") / "run-assembly-test")
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
                    result = factory(prepared_run=_prepared_run())

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
                        factory(prepared_run=_prepared_run())
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
                        factory(prepared_run=_prepared_run(), measurement_port=port)
                connect.assert_not_called()
                self.assertEqual(port.close_calls, [])

    def test_cable_loss_factory_does_not_invent_explicit_input_paths(self):
        with patch("app.gui_runtime.connect_instruments", return_value=_Port()), patch(
            "app.gui_runtime.CableLossUseCase", return_value=object()
        ) as use_case:
            create_cable_loss_measurement(prepared_run=_prepared_run())

        request = use_case.call_args.args[0]
        self.assertIsNone(request.cable_loss_path)
        self.assertIsNone(request.driver_mapping_path)

    def test_driver_mapping_factory_uses_the_explicit_cable_loss_path(self):
        cable = Path("selected") / "cable_loss" / "20260101T000000Z_abcd.json"
        verified = Path("resolved") / "20260101T000000Z_abcd.json"
        loss_data = {"cable_losses": {"2.4": {"cable2": 1.0}}}
        reader = _RecordingInputReader(cable_loss=loss_data)
        expected = object()
        with patch("app.gui_runtime.connect_instruments", return_value=_Port()), patch(
            "app.gui_runtime.DriverPowerMappingUseCase", return_value=expected
        ) as use_case, patch(
            "app.gui_runtime._verified_explicit_stage_path", return_value=verified
        ) as verify:
            result = create_driver_mapping_measurement(
                prepared_run=_prepared_run(),
                cable_loss_path=str(cable),
                input_reader=reader,
            )

        self.assertIs(result, expected)
        # 显式路径先经阶段结果仓库校验（边界 + 正式结果契约），再按路径读取。
        verify.assert_called_once_with(cable, MeasurementStage.CABLE_LOSS, DEFAULT_PATHS)
        self.assertEqual(reader.paths, [verified])
        self.require_stage_inputs.assert_not_called()
        request = use_case.call_args.args[0]
        # 路径与预检读取的线损文档一起进入请求：与运行身份无关，也不从运行目录推断。
        self.assertEqual(request.cable_loss_path, cable)
        self.assertIs(request.loss_data, loss_data)
        self.assertIsNone(request.driver_mapping_path)
        self.assertEqual(request.run_id, "run-assembly-test")

    def test_driver_mapping_factory_keeps_the_run_reference_without_an_explicit_path(self):
        prepared = _prepared_run()
        with patch("app.gui_runtime.connect_instruments", return_value=_Port()), patch(
            "app.gui_runtime.DriverPowerMappingUseCase", return_value=object()
        ) as use_case:
            create_driver_mapping_measurement(prepared_run=prepared)

        request = use_case.call_args.args[0]
        self.assertIsNone(request.cable_loss_path)
        self.assertIsNone(request.loss_data)
        # 省略显式来源的旧调用方仍走运行引用的已校验读取。
        self.require_stage_inputs.assert_called_once_with(
            prepared, STAGE_CABLE_LOSS, paths=DEFAULT_PATHS
        )

    def test_amplifier_factory_forwards_explicit_calibration_paths(self):
        cable = Path("selected") / "cable_loss" / "20260101T000000Z_abcd.json"
        mapping = Path("selected") / "driver_mapping" / "20260102T000000Z_ef01.json"
        expected = object()
        with patch("app.gui_runtime.connect_instruments", return_value=_Port()), patch(
            "app.gui_runtime.AmplifierMeasurementUseCase", return_value=expected
        ) as use_case:
            result = create_amplifier_measurement(
                prepared_run=_prepared_run(),
                cable_loss_path=cable,
                driver_mapping_path=str(mapping),
            )

        self.assertIs(result, expected)
        request = use_case.call_args.args[0]
        self.assertIsInstance(request, AmplifierMeasurementRequest)
        self.assertEqual(request.cable_loss_path, cable)
        self.assertEqual(request.driver_mapping_path, mapping)

    def test_factories_reject_blank_input_paths_before_opening_instruments(self):
        cases = (
            (create_driver_mapping_measurement, "cable_loss_path", "   "),
            (create_amplifier_measurement, "cable_loss_path", ""),
            (create_amplifier_measurement, "driver_mapping_path", "  "),
        )
        for factory, field, value in cases:
            with self.subTest(factory=factory.__name__, field=field):
                with patch("app.gui_runtime.connect_instruments") as connect:
                    with self.assertRaisesRegex(ValueError, field):
                        factory(prepared_run=_prepared_run(), **{field: value})
                connect.assert_not_called()

    def test_amplifier_factory_hands_explicit_paths_to_the_preflight(self):
        cable = Path("selected") / "cable_loss" / "20260101T000000Z_abcd.json"
        mapping = Path("selected") / "driver_mapping" / "20260102T000000Z_ef01.json"
        with patch("app.gui_runtime.connect_instruments", return_value=_Port()), patch(
            "app.gui_runtime.AmplifierMeasurementUseCase", return_value=object()
        ):
            create_amplifier_measurement(
                prepared_run=_prepared_run(),
                cable_loss_path=cable,
                driver_mapping_path=str(mapping),
            )

        kwargs = self.preflight_amplifier.call_args.kwargs
        self.assertEqual(kwargs["cable_loss_path"], cable)
        self.assertEqual(kwargs["driver_mapping_path"], mapping)

    def test_invalid_explicit_cable_loss_fails_before_opening_instruments(self):
        with patch("app.gui_runtime.connect_instruments") as connect, patch(
            "app.gui_runtime._verified_explicit_stage_path",
            side_effect=ValueError("阶段结果路径必须直接位于 cable_loss 阶段目录"),
        ), patch("app.gui_runtime.DriverPowerMappingUseCase") as use_case:
            with self.assertRaisesRegex(ValueError, "阶段目录"):
                create_driver_mapping_measurement(
                    prepared_run=_prepared_run(),
                    cable_loss_path="elsewhere/cable_loss.json",
                    input_reader=_RecordingInputReader(cable_loss={}),
                )
        connect.assert_not_called()
        use_case.assert_not_called()

    def test_invalid_explicit_calibration_inputs_fail_before_opening_instruments(self):
        self.preflight_amplifier.side_effect = ValueError(
            "线损输入必须是 cable_loss 结果"
        )
        with patch("app.gui_runtime.connect_instruments") as connect, patch(
            "app.gui_runtime.AmplifierMeasurementUseCase"
        ) as use_case:
            with self.assertRaisesRegex(ValueError, "cable_loss"):
                create_amplifier_measurement(
                    prepared_run=_prepared_run(),
                    cable_loss_path="test_results/cable_loss/broken.json",
                )
        connect.assert_not_called()
        use_case.assert_not_called()

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
                        prepared_run=_prepared_run(),
                        measurement_port=port,
                    )

                self.assertIs(result, expected)
                connect.assert_not_called()
                self.assertIs(measurement.call_args.args[0].measurement_port, port)
                self.assertFalse(measurement.call_args.args[0].safety_options["owns_measurement_port"])
                self.assertEqual(port.close_calls, [])

    def test_run_without_a_contract_is_rejected_before_connecting(self):
        """T2/B3：不带运行契约的对象没有硬件配置来源，必须在连接前失败。"""
        prepared = _bare_run()
        cases = (
            ("create_cable_loss_measurement", create_cable_loss_measurement, "CableLossUseCase"),
            (
                "create_driver_mapping_measurement",
                create_driver_mapping_measurement,
                "DriverPowerMappingUseCase",
            ),
            ("create_amplifier_measurement", create_amplifier_measurement, "AmplifierMeasurementUseCase"),
        )
        for label, factory, use_case_name in cases:
            with self.subTest(factory=label):
                with patch("app.gui_runtime.connect_instruments") as connect, patch(
                    f"app.gui_runtime.{use_case_name}", return_value=object()
                ):
                    with self.assertRaisesRegex(RunNotResumable, "运行契约"):
                        factory(prepared_run=prepared)
                connect.assert_not_called()

    def test_contract_run_without_hardware_snapshot_is_rejected_before_connecting(self):
        # 运行契约存在却没有冻结硬件路径：组合层必须明确失败，绝不回退全局配置。
        prepared = _contract_run_stub(hardware_config_path=None)
        with patch("app.gui_runtime.create_measurement_port") as port_factory:
            with self.assertRaisesRegex(RunNotResumable, "硬件配置快照"):
                create_cable_loss_measurement(prepared_run=prepared)
        port_factory.assert_not_called()

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
                    with patch("app.gui_runtime.create_measurement_port") as port_factory, patch(
                        f"app.gui_runtime.{use_case_name}", return_value=object()
                    ):
                        with self.assertRaisesRegex(RunNotResumable, "硬件配置快照"):
                            factory(prepared_run=prepared)
                    port_factory.assert_not_called()

    def test_measurement_factories_open_the_port_at_the_snapshot_they_put_in_the_request(self):
        """端口按“已解析”的路径打开：连接入口不再二次解析（D12）。

        这里放行真实的 :func:`app.gui_runtime.connect_instruments`，只把最底层的
        ``create_measurement_port`` 换成替身，因此断言的是生产路径的实际行为：
        ``hardware_connection_path()`` 每次装配只被调用一次，端口与测量请求拿到的是
        同一个值。
        """
        snapshot = Path("runs") / "run-contract" / "inputs" / "hardware_config.json"
        cases = (
            (create_cable_loss_measurement, "CableLossUseCase"),
            (create_driver_mapping_measurement, "DriverPowerMappingUseCase"),
            (create_amplifier_measurement, "AmplifierMeasurementUseCase"),
        )
        for factory, use_case_name in cases:
            with self.subTest(factory=factory.__name__):
                resolutions = []

                def resolver(self=None):
                    resolutions.append(snapshot)
                    return snapshot

                prepared = type(
                    "ContractRun",
                    (),
                    {
                        "run_id": "run-contract",
                        "context": SimpleNamespace(run_id="run-contract"),
                        "configuration": _run_configuration(),
                        "run_directory": Path("runs") / "run-contract",
                        "run_repository": object(),
                        "hardware_connection_path": resolver,
                    },
                )()
                port = _Port()
                with patch("app.gui_runtime.ArchiveArtifactGenerator"), patch(
                    "app.gui_runtime.ResultArchivalService"
                ), patch(
                    "app.gui_runtime.create_measurement_port", return_value=port
                ) as port_factory, patch(
                    f"app.gui_runtime.{use_case_name}"
                ):
                    factory(prepared_run=prepared)

                self.assertEqual(
                    len(resolutions), 1, "一次装配只解析一次硬件配置路径"
                )
                self.assertEqual(Path(port_factory.call_args.args[0]), snapshot)
                self.assertEqual(port_factory.call_args.kwargs["mode"], "hardware")

    def test_connect_instruments_resolves_the_snapshot_and_accepts_no_path(self):
        """连接入口自己解析一次，且不接受任何配置路径覆盖（步骤 6，D17）。"""
        snapshot = Path("runs") / "run-contract" / "inputs" / "hardware_config.json"
        resolutions = []

        def resolver(self=None):
            resolutions.append(snapshot)
            return snapshot

        prepared = type(
            "ContractRun",
            (),
            {
                "run_id": "run-contract",
                "context": SimpleNamespace(run_id="run-contract"),
                "run_directory": Path("runs") / "run-contract",
                "run_repository": object(),
                "hardware_connection_path": resolver,
            },
        )()
        port = _Port()
        with patch("app.gui_runtime.create_measurement_port", return_value=port) as factory:
            self.assertIs(connect_instruments(prepared), port)

        self.assertEqual(len(resolutions), 1, "连接入口只解析一次运行快照")
        self.assertEqual(Path(factory.call_args.args[0]), snapshot)
        self.assertEqual(factory.call_args.kwargs["mode"], "hardware")

        # 没有“调用方已解析好的路径”这种入口：想连硬件只能交运行对象。
        for keyword in ("resolved_config_path", "config_path", "hardware_config_path"):
            with self.subTest(keyword=keyword):
                with self.assertRaises(TypeError):
                    connect_instruments(prepared, **{keyword: str(snapshot)})

    def test_connect_instruments_rejects_an_object_without_contract(self):
        """没有运行契约的对象连一次解析都到不了（不存在回退到配置文件的出口）。"""
        with patch("app.gui_runtime.create_measurement_port") as factory:
            with self.assertRaisesRegex(RunNotResumable, "运行契约"):
                connect_instruments(_bare_run())
        factory.assert_not_called()

    def test_assembly_error_from_request_creation_closes_only_owned_port(self):
        owned_port = _Port()
        with patch("app.gui_runtime.connect_instruments", return_value=owned_port), patch(
            "app.gui_runtime.DriverPowerMappingUseCase", return_value=object()
        ), patch(
            "app.gui_runtime.DriverPowerMappingRequest",
            side_effect=RuntimeError("request assembly failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "request assembly failed"):
                create_driver_mapping_measurement(prepared_run=_prepared_run())
        self.assertEqual(owned_port.close_calls, [True])


class ExplicitStageInputPreflightTests(unittest.TestCase):
    """显式选择的阶段文件优先于运行引用，并在连接仪器前完成读取。"""

    LOSS_DOCUMENT = {
        "cable_losses": {
            "2.4": {"cable1": 1.0, "cable2": 0.5, "cable3": 0.0, "cable4": 0.5}
        }
    }

    @staticmethod
    def _recorder_that_must_not_be_read():
        def verified_result_path(stage):
            raise AssertionError(f"显式输入不应读取运行引用: {stage}")

        return SimpleNamespace(verified_result_path=verified_result_path)

    def test_amplifier_preflight_reads_the_explicit_loss_path(self):
        explicit = Path("test_results") / "cable_loss" / "20260101T000000Z_abcd.json"
        verified = Path("resolved") / explicit.name
        reader = _RecordingInputReader(cable_loss=self.LOSS_DOCUMENT)
        with patch(
            "app.gui_runtime._stage_recorder",
            return_value=self._recorder_that_must_not_be_read(),
        ), patch(
            "app.gui_runtime._verified_explicit_stage_path", return_value=verified
        ) as verify:
            inputs = _preflight_amplifier_inputs(
                _prepared_run(), reader, None, cable_loss_path=explicit
            )

        self.assertIs(inputs.loss_data, self.LOSS_DOCUMENT)
        self.assertIsNone(inputs.driver_mapping)
        verify.assert_called_once_with(explicit, MeasurementStage.CABLE_LOSS, None)
        self.assertEqual(reader.paths, [verified])

    def test_amplifier_preflight_reads_explicit_mapping_only_in_driver_mode(self):
        cable = Path("test_results") / "cable_loss" / "20260101T000000Z_abcd.json"
        mapping = (
            Path("test_results") / "driver_mapping" / "20260102T000000Z_ef01.json"
        )
        mapping_input = object()
        reader = _RecordingInputReader(
            cable_loss=self.LOSS_DOCUMENT, driver_mapping=mapping_input
        )
        verified_paths = []

        def verify(path, stage, paths):
            verified_paths.append((path, stage))
            return Path("resolved") / Path(path).name

        with patch(
            "app.gui_runtime._stage_recorder",
            return_value=self._recorder_that_must_not_be_read(),
        ), patch("app.gui_runtime._driver_mode_enabled", return_value=True), patch(
            "app.gui_runtime._verified_explicit_stage_path", side_effect=verify
        ), patch("app.gui_runtime.validate_amplifier_calibration_inputs"):
            inputs = _preflight_amplifier_inputs(
                _prepared_run(),
                reader,
                None,
                cable_loss_path=cable,
                driver_mapping_path=mapping,
            )

        self.assertIs(inputs.driver_mapping, mapping_input)
        self.assertEqual(
            verified_paths,
            [
                (cable, MeasurementStage.CABLE_LOSS),
                (mapping, MeasurementStage.DRIVER_MAPPING),
            ],
        )
        self.assertEqual(
            reader.paths,
            [Path("resolved") / cable.name, Path("resolved") / mapping.name],
        )

    def test_driver_mapping_preflight_uses_the_explicit_loss_without_stage_registration(self):
        explicit = Path("test_results") / "cable_loss" / "20260101T000000Z_abcd.json"
        verified = Path("resolved") / explicit.name
        loss_data = {"cable_losses": {"2.4": {"cable2": 1.0}}}
        reader = _RecordingInputReader(cable_loss=loss_data)
        with patch("app.gui_runtime._require_stage_inputs") as require, patch(
            "app.gui_runtime._verified_explicit_stage_path", return_value=verified
        ) as verify:
            result = _preflight_driver_mapping_inputs(
                _prepared_run(), reader, None, cable_loss_path=explicit
            )

        self.assertIs(result, loss_data)
        require.assert_not_called()
        verify.assert_called_once_with(explicit, MeasurementStage.CABLE_LOSS, None)
        self.assertEqual(reader.paths, [verified])

    def test_driver_mapping_preflight_keeps_the_run_reference_without_a_path(self):
        prepared = _prepared_run()
        with patch("app.gui_runtime._require_stage_inputs") as require:
            result = _preflight_driver_mapping_inputs(
                prepared, _RecordingInputReader(), None, cable_loss_path=None
            )

        self.assertIsNone(result)
        require.assert_called_once_with(prepared, STAGE_CABLE_LOSS, paths=None)


class GuiRuntimePreparationTests(unittest.TestCase):
    def _write_config(self):
        # 用一份完整配置作为“参数校验”的基线；旧的不完整 fixture 属于 1.0，
        # 按步骤 6 的版本门禁不能再进入 authoring。
        config = json.loads(json.dumps(FULLY_VALID_CONFIG))
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
            # 去掉 DUT 的通道参数、漏极与上电顺序：线损阶段专用校验不关心这些，
            # 因此仍然通过；完整运行校验会拒绝。
            config["test_plan"]["dut"]["channel_settings"] = []
            config["test_plan"]["dut"]["drain_channel"] = None
            config["run_mapping"]["dut_power_plan"] = {
                "power_sequence": [],
                "inter_channel_delay_s": 0.0,
            }
            config["power_supply_assignment"]["dut_amplifier"] = {
                "power_supply_count": 0,
                "supplies": {},
                "power_plan": {"power_sequence": [], "inter_channel_delay_s": 0.0},
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

    def test_preparation_rejects_simulated_device_bindings(self):
        """模拟扫描的绑定不能冻结成真实运行：一次演示点击不该产生可运行的快照。"""
        directory, path = self._write_fully_valid_config()
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
            config["device_bindings"] = {
                "signal_generator": {
                    "full_code": "105462",
                    "device_key": "rohde&schwarz:105462",
                    "source": "simulated",
                }
            }
            path.write_text(json.dumps(config), encoding="utf-8")
            runs_root = Path(directory.name) / "runs"

            with self.assertRaisesRegex(
                ConfigurationError, "模拟扫描"
            ):
                prepare_configuration(
                    str(path), run_repository=RunRepository(runs_root)
                )
            # 拒绝必须发生在创建运行目录之前。
            self.assertFalse(runs_root.exists() and any(runs_root.iterdir()))
        finally:
            directory.cleanup()

    def test_preparation_accepts_real_scan_bindings(self):
        directory, path = self._write_fully_valid_config()
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
            config["device_bindings"] = {
                "signal_generator": {
                    "full_code": "105462",
                    "device_key": "rohde&schwarz:105462",
                    "source": "scan",
                }
            }
            path.write_text(json.dumps(config), encoding="utf-8")
            prepared = SimpleNamespace(
                configuration=object(), context=object(), run_directory=Path("runs") / "x"
            )
            with patch(
                "app.gui_runtime.create_prepared_run", return_value=prepared
            ):
                self.assertIs(prepare_configuration(str(path)), prepared)
        finally:
            directory.cleanup()

    def test_preparation_rejects_invalid_parameters(self):
        """创建运行只做参数校验：参数不合法时在建运行目录前失败。"""
        directory, path = self._write_config()
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
            config["test_plan"]["frequencies"]["values"] = []
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "frequencies.values"):
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

    def load_for_run(self, path, mapping_path=None):
        raise AssertionError("应用服务应通过 load 后自行执行运行前门禁")


if __name__ == "__main__":
    unittest.main()
