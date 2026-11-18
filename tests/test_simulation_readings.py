r"""契约：``application/dto/simulation_readings.py``（重构计划步骤 2）。

覆盖 ``readings.json`` 的严格解析：顶层与嵌套字段、数值有限性、线损有序序列、
驱动/主功放按频点的功率记录点、通道集合一致性，以及频率网格与冻结配置的逐点
一致性。所有失败都必须带字段名，且统一是
:class:`application.dto.simulation_readings.SimulationReadingsError`。

验证方式：

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" -m unittest tests.test_simulation_readings -v
```
"""

import json
import unittest

from application.dto.measurement_results import MeasurementType
from application.dto.simulation_readings import (
    SIMULATION_READINGS_KIND,
    SIMULATION_READINGS_SCHEMA_VERSION,
    SimulationAmplifierPayload,
    SimulationCableLossPayload,
    SimulationDriverMappingPayload,
    SimulationReadings,
    SimulationReadingsError,
    simulation_readings_from_document,
)
from application.errors import ConfigurationError
from domain.measurement.values import Frequency


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _source(**overrides):
    document = {
        "scenario": "scenario.json",
        "scenario_sha256": SHA_A,
        "cable_loss_result": None,
        "cable_loss_sha256": None,
        "driver_mapping_result": None,
        "driver_mapping_sha256": None,
    }
    document.update(overrides)
    return document


def _cable_loss_document():
    return {
        "schema_version": SIMULATION_READINGS_SCHEMA_VERSION,
        "kind": SIMULATION_READINGS_KIND,
        "measurement_type": "cable_loss",
        "source": _source(),
        "payload": {
            "measurements": [
                {"frequency_ghz": 1.0, "path": 1, "raw_dbm": -41.842},
                {"frequency_ghz": 2.0, "path": 1, "raw_dbm": -42.392},
                {"frequency_ghz": 1.0, "path": 2, "raw_dbm": -43.418},
                {"frequency_ghz": 2.0, "path": 2, "raw_dbm": -44.278},
            ]
        },
    }


def _driver_document():
    return {
        "schema_version": SIMULATION_READINGS_SCHEMA_VERSION,
        "kind": SIMULATION_READINGS_KIND,
        "measurement_type": "driver_power_mapping",
        "source": _source(
            cable_loss_result="20261117T060245Z_9b2e7d10.json",
            cable_loss_sha256=SHA_B,
        ),
        "payload": {
            "frequencies": {
                "1.0": {
                    "probes": [
                        {"source_power_dbm": -70.0, "raw_output_dbm": -70.802},
                        {"source_power_dbm": -60.0, "raw_output_dbm": -60.802},
                    ]
                }
            }
        },
    }


def _amplifier_document():
    return {
        "schema_version": SIMULATION_READINGS_SCHEMA_VERSION,
        "kind": SIMULATION_READINGS_KIND,
        "measurement_type": "amplifier",
        "source": _source(
            cable_loss_result="20261117T060245Z_9b2e7d10.json",
            cable_loss_sha256=SHA_B,
            driver_mapping_result="20261117T060600Z_5c1d9e42.json",
            driver_mapping_sha256=SHA_C,
        ),
        "payload": {
            "frequencies": {
                "1.0": {
                    "points": [
                        {
                            "sg_power_dbm": -60.0,
                            "raw_output_dbm": -26.709,
                            "voltages": {"DP8B0885_CH1": 1.001},
                            "currents": {"DP8B0885_CH1": 0.12},
                        },
                        {
                            "sg_power_dbm": -59.0,
                            "raw_output_dbm": -25.709,
                            "voltages": {"DP8B0885_CH1": 1.002},
                            "currents": {"DP8B0885_CH1": 0.13},
                        },
                    ]
                }
            }
        },
    }


def _parse_cable_loss(document=None, **kwargs):
    return simulation_readings_from_document(
        _cable_loss_document() if document is None else document, **kwargs
    )


def _parse_driver(document=None, **kwargs):
    return simulation_readings_from_document(
        _driver_document() if document is None else document, **kwargs
    )


def _parse_amplifier(document=None, **kwargs):
    return simulation_readings_from_document(
        _amplifier_document() if document is None else document, **kwargs
    )


class SimulationReadingsContractTests(unittest.TestCase):
    """合法文档通过，并解析成按阶段分派的只读 payload。"""

    def test_cable_loss_document_keeps_the_service_scan_order(self):
        readings = _parse_cable_loss()
        self.assertIs(readings.measurement_type, MeasurementType.CABLE_LOSS)
        self.assertIsInstance(readings.payload, SimulationCableLossPayload)
        self.assertEqual(readings.schema_version, SIMULATION_READINGS_SCHEMA_VERSION)
        self.assertEqual(
            [
                (item.frequency_ghz, item.path)
                for item in readings.payload.measurements
            ],
            [(1.0, 1), (2.0, 1), (1.0, 2), (2.0, 2)],
        )
        self.assertEqual(
            [frequency.key for frequency in readings.frequency_grid],
            ["1.0", "2.0"],
        )

    def test_driver_document_maps_frequency_keys_to_probes(self):
        readings = _parse_driver()
        self.assertIsInstance(readings.payload, SimulationDriverMappingPayload)
        self.assertEqual(
            [frequency.key for frequency in readings.frequency_grid], ["1.0"]
        )
        probes = readings.payload.frequencies[Frequency.from_ghz(1.0)].probes
        self.assertEqual(
            [probe.source_power_dbm for probe in probes], [-70.0, -60.0]
        )
        self.assertEqual([probe.raw_output_dbm for probe in probes], [-70.802, -60.802])

    def test_amplifier_document_keeps_voltages_and_currents_per_point(self):
        readings = _parse_amplifier()
        self.assertIsInstance(readings.payload, SimulationAmplifierPayload)
        points = readings.payload.frequencies[Frequency.from_ghz(1.0)].points
        self.assertEqual([point.sg_power_dbm for point in points], [-60.0, -59.0])
        self.assertEqual(points[0].voltages["DP8B0885_CH1"], 1.001)
        self.assertEqual(points[0].currents["DP8B0885_CH1"], 0.12)

    def test_amplifier_allows_a_missing_driver_mapping_reference(self):
        """驱动关闭时主功放不引用打包驱动映射；两字段同时为 null 合法。"""
        readings = _parse_amplifier(
            _amplifier_document()
            | {
                "source": _source(
                    cable_loss_result="cable.json", cable_loss_sha256=SHA_B
                )
            }
        )
        self.assertIsNone(readings.source.driver_mapping_result)

    def test_payload_mappings_are_read_only_and_measurements_are_a_tuple(self):
        readings = _parse_driver()
        with self.assertRaises(TypeError):
            readings.payload.frequencies[Frequency.from_ghz(3.0)] = None
        cable = _parse_cable_loss()
        self.assertIsInstance(cable.payload.measurements, tuple)
        with self.assertRaises(AttributeError):
            cable.payload.measurements = ()

    def test_frequency_keys_are_matched_by_value_not_text(self):
        """``1.0`` 与 ``1.00`` 是同一个频率，不能在同一个 payload 里重复。"""
        document = _driver_document()
        document["payload"]["frequencies"]["1.00"] = {
            "probes": [{"source_power_dbm": -60.0, "raw_output_dbm": -60.802}]
        }
        with self.assertRaises(SimulationReadingsError) as raised:
            _parse_driver(document)
        self.assertIn("重复频率", str(raised.exception))


class TopLevelValidationTests(unittest.TestCase):
    """顶层 kind / schema_version / 字段集合与类型分派。"""

    def test_unknown_kind_is_rejected(self):
        document = _cable_loss_document() | {"kind": "mock_scenario"}
        with self.assertRaises(SimulationReadingsError) as raised:
            _parse_cable_loss(document)
        self.assertIn("kind", str(raised.exception))
        self.assertIn(SIMULATION_READINGS_KIND, str(raised.exception))

    def test_schema_version_only_supports_one(self):
        for version in (0, 2, "1", 1.0, True):
            with self.subTest(version=version):
                document = _cable_loss_document() | {"schema_version": version}
                with self.assertRaises(SimulationReadingsError) as raised:
                    _parse_cable_loss(document)
                self.assertIn("schema_version", str(raised.exception))

    def test_missing_and_unknown_top_level_fields_are_named(self):
        document = _cable_loss_document()
        del document["payload"]
        with self.assertRaises(SimulationReadingsError) as raised:
            _parse_cable_loss(document)
        self.assertIn("payload", str(raised.exception))

        document = _cable_loss_document() | {"extra": 1}
        with self.assertRaises(SimulationReadingsError) as raised:
            _parse_cable_loss(document)
        self.assertIn("extra", str(raised.exception))

    def test_root_must_be_an_object(self):
        for document in (None, [], "text", 3):
            with self.subTest(document=document):
                with self.assertRaises(SimulationReadingsError):
                    simulation_readings_from_document(document)

    def test_measurement_type_must_match_the_payload_shape(self):
        document = _cable_loss_document()
        document["measurement_type"] = "driver_power_mapping"
        with self.assertRaises(SimulationReadingsError) as raised:
            _parse_driver(document)
        self.assertIn("payload", str(raised.exception))

    def test_expected_measurement_type_must_match_the_document(self):
        with self.assertRaises(SimulationReadingsError) as raised:
            _parse_cable_loss(
                expected_measurement_type=MeasurementType.AMPLIFIER
            )
        self.assertIn("测量类型", str(raised.exception))

    def test_unknown_measurement_type_is_rejected(self):
        document = _cable_loss_document() | {"measurement_type": "unknown"}
        with self.assertRaises(SimulationReadingsError) as raised:
            simulation_readings_from_document(document)
        self.assertIn("measurement_type", str(raised.exception))


class SourceValidationTests(unittest.TestCase):
    """``source`` 的生成事实：纯文件名、SHA-256 与阶段依赖。"""

    def test_source_requires_all_six_fields(self):
        document = _cable_loss_document()
        del document["source"]["scenario_sha256"]
        with self.assertRaises(SimulationReadingsError) as raised:
            _parse_cable_loss(document)
        self.assertIn("scenario_sha256", str(raised.exception))

    def test_scenario_must_be_the_stage_scenario_file(self):
        for scenario in ("other.json", "../scenario.json", "C:\\scenario.json"):
            with self.subTest(scenario=scenario):
                document = _cable_loss_document()
                document["source"]["scenario"] = scenario
                with self.assertRaises(SimulationReadingsError) as raised:
                    _parse_cable_loss(document)
                self.assertIn("source.scenario", str(raised.exception))

    def test_sha256_must_be_64_lowercase_hex_characters(self):
        for digest in ("a" * 63, "A" * 64, "g" * 64, "", None):
            with self.subTest(digest=digest):
                document = _cable_loss_document()
                document["source"]["scenario_sha256"] = digest
                with self.assertRaises(SimulationReadingsError) as raised:
                    _parse_cable_loss(document)
                self.assertIn("scenario_sha256", str(raised.exception))

    def test_upstream_result_and_digest_must_appear_together(self):
        document = _driver_document()
        document["source"]["cable_loss_sha256"] = None
        with self.assertRaises(SimulationReadingsError) as raised:
            _parse_driver(document)
        self.assertIn("cable_loss_result", str(raised.exception))

        document = _driver_document()
        document["source"]["cable_loss_result"] = None
        with self.assertRaises(SimulationReadingsError) as raised:
            _parse_driver(document)
        self.assertIn("cable_loss_sha256", str(raised.exception))

    def test_cable_loss_readings_have_no_upstream_calibration(self):
        document = _cable_loss_document()
        document["source"]["cable_loss_result"] = "cable.json"
        document["source"]["cable_loss_sha256"] = SHA_B
        with self.assertRaises(SimulationReadingsError) as raised:
            _parse_cable_loss(document)
        self.assertIn("线损读数", str(raised.exception))

    def test_driver_readings_require_cable_loss_and_reject_driver_upstream(self):
        document = _driver_document()
        document["source"]["cable_loss_result"] = None
        document["source"]["cable_loss_sha256"] = None
        with self.assertRaises(SimulationReadingsError) as raised:
            _parse_driver(document)
        self.assertIn("cable_loss_result", str(raised.exception))

        document = _driver_document()
        document["source"]["driver_mapping_result"] = "driver.json"
        document["source"]["driver_mapping_sha256"] = SHA_C
        with self.assertRaises(SimulationReadingsError) as raised:
            _parse_driver(document)
        self.assertIn("driver_mapping_result", str(raised.exception))

    def test_upstream_reference_must_be_a_plain_file_name(self):
        for reference in ("../cable.json", "mock_data/cable_loss/cable.json", "NUL"):
            with self.subTest(reference=reference):
                document = _driver_document()
                document["source"]["cable_loss_result"] = reference
                with self.assertRaises(SimulationReadingsError) as raised:
                    _parse_driver(document)
                self.assertIn("cable_loss_result", str(raised.exception))


class CableLossPayloadTests(unittest.TestCase):
    """线损是有序序列：路径 1 全频点 → 路径 2 全频点。"""

    def test_empty_measurements_are_rejected(self):
        document = _cable_loss_document()
        document["payload"]["measurements"] = []
        with self.assertRaises(SimulationReadingsError) as raised:
            _parse_cable_loss(document)
        self.assertIn("measurements", str(raised.exception))

    def test_path_two_before_path_one_is_rejected(self):
        document = _cable_loss_document()
        document["payload"]["measurements"] = [
            {"frequency_ghz": 1.0, "path": 2, "raw_dbm": -43.418},
            {"frequency_ghz": 1.0, "path": 1, "raw_dbm": -41.842},
        ]
        with self.assertRaises(SimulationReadingsError) as raised:
            _parse_cable_loss(document)
        self.assertIn("路径 1", str(raised.exception))

    def test_interleaved_paths_are_rejected(self):
        document = _cable_loss_document()
        document["payload"]["measurements"] = [
            {"frequency_ghz": 1.0, "path": 1, "raw_dbm": -41.842},
            {"frequency_ghz": 1.0, "path": 2, "raw_dbm": -43.418},
            {"frequency_ghz": 2.0, "path": 1, "raw_dbm": -42.392},
            {"frequency_ghz": 2.0, "path": 2, "raw_dbm": -44.278},
        ]
        with self.assertRaises(SimulationReadingsError):
            _parse_cable_loss(document)

    def test_missing_path_is_rejected(self):
        document = _cable_loss_document()
        document["payload"]["measurements"] = [
            {"frequency_ghz": 1.0, "path": 1, "raw_dbm": -41.842},
        ]
        with self.assertRaises(SimulationReadingsError) as raised:
            _parse_cable_loss(document)
        self.assertIn("路径 2", str(raised.exception))

    def test_path_frequencies_must_match_point_by_point(self):
        document = _cable_loss_document()
        document["payload"]["measurements"][2]["frequency_ghz"] = 1.1
        with self.assertRaises(SimulationReadingsError) as raised:
            _parse_cable_loss(document)
        self.assertIn("逐点一致", str(raised.exception))

    def test_frequencies_must_strictly_increase_within_a_path(self):
        document = _cable_loss_document()
        document["payload"]["measurements"][1]["frequency_ghz"] = 1.0
        with self.assertRaises(SimulationReadingsError) as raised:
            _parse_cable_loss(document)
        self.assertIn("严格递增", str(raised.exception))

    def test_path_must_be_one_or_two(self):
        for path in (0, 3, "1", True):
            with self.subTest(path=path):
                document = _cable_loss_document()
                document["payload"]["measurements"][0]["path"] = path
                with self.assertRaises(SimulationReadingsError) as raised:
                    _parse_cable_loss(document)
                self.assertIn("path", str(raised.exception))

    def test_measurement_point_rejects_unknown_fields(self):
        document = _cable_loss_document()
        document["payload"]["measurements"][0]["gain_db"] = 1.0
        with self.assertRaises(SimulationReadingsError) as raised:
            _parse_cable_loss(document)
        self.assertIn("gain_db", str(raised.exception))


class DriverPayloadTests(unittest.TestCase):
    """驱动映射按频点保存源功率记录点，严格递增且不得重复。"""

    def test_empty_probes_are_rejected(self):
        document = _driver_document()
        document["payload"]["frequencies"]["1.0"]["probes"] = []
        with self.assertRaises(SimulationReadingsError) as raised:
            _parse_driver(document)
        self.assertIn("probes", str(raised.exception))

    def test_duplicate_source_power_is_rejected(self):
        document = _driver_document()
        document["payload"]["frequencies"]["1.0"]["probes"][1][
            "source_power_dbm"
        ] = -70.0
        with self.assertRaises(SimulationReadingsError) as raised:
            _parse_driver(document)
        self.assertIn("严格递增", str(raised.exception))

    def test_decreasing_source_power_is_rejected(self):
        document = _driver_document()
        document["payload"]["frequencies"]["1.0"]["probes"][1][
            "source_power_dbm"
        ] = -71.0
        with self.assertRaises(SimulationReadingsError) as raised:
            _parse_driver(document)
        self.assertIn("严格递增", str(raised.exception))

    def test_frequency_bucket_requires_probes(self):
        document = _driver_document()
        document["payload"]["frequencies"]["1.0"] = {}
        with self.assertRaises(SimulationReadingsError) as raised:
            _parse_driver(document)
        self.assertIn("probes", str(raised.exception))

    def test_empty_frequency_mapping_is_rejected(self):
        document = _driver_document()
        document["payload"]["frequencies"] = {}
        with self.assertRaises(SimulationReadingsError) as raised:
            _parse_driver(document)
        self.assertIn("frequencies", str(raised.exception))

    def test_frequency_key_must_be_a_positive_string(self):
        for key in ("0", "-1", "abc", "nan"):
            with self.subTest(key=key):
                document = _driver_document()
                document["payload"]["frequencies"] = {
                    key: {
                        "probes": [
                            {"source_power_dbm": -70.0, "raw_output_dbm": -70.8}
                        ]
                    }
                }
                with self.assertRaises(SimulationReadingsError) as raised:
                    _parse_driver(document)
                self.assertIn("频率", str(raised.exception))

    def test_near_duplicate_frequency_keys_are_rejected(self):
        """端口按 1e-9 GHz 容差匹配频率：容差内的两个键会让查找出现歧义。"""
        document = _driver_document()
        document["payload"]["frequencies"]["1.0000000005"] = {
            "probes": [{"source_power_dbm": -60.0, "raw_output_dbm": -60.802}]
        }
        with self.assertRaises(SimulationReadingsError) as raised:
            _parse_driver(document)
        message = str(raised.exception)
        self.assertIn("重复频率", message)
        self.assertIn("1.0000000005", message)

    def test_distinct_frequencies_outside_the_tolerance_are_accepted(self):
        """相距超过容差的两个频点是两个不同频点，不能误判成重复。"""
        document = _driver_document()
        document["payload"]["frequencies"]["1.000000002"] = {
            "probes": [{"source_power_dbm": -60.0, "raw_output_dbm": -60.802}]
        }
        readings = _parse_driver(document)
        self.assertEqual(len(readings.frequency_grid), 2)


class AmplifierPayloadTests(unittest.TestCase):
    """主功放每点保存 V/I，同一频点内通道集合必须完全一致。"""

    def test_empty_points_are_rejected(self):
        document = _amplifier_document()
        document["payload"]["frequencies"]["1.0"]["points"] = []
        with self.assertRaises(SimulationReadingsError) as raised:
            _parse_amplifier(document)
        self.assertIn("points", str(raised.exception))

    def test_power_must_strictly_increase(self):
        document = _amplifier_document()
        document["payload"]["frequencies"]["1.0"]["points"][1][
            "sg_power_dbm"
        ] = -60.0
        with self.assertRaises(SimulationReadingsError) as raised:
            _parse_amplifier(document)
        self.assertIn("严格递增", str(raised.exception))

    def test_channel_set_must_stay_constant_within_a_frequency(self):
        document = _amplifier_document()
        document["payload"]["frequencies"]["1.0"]["points"][1]["voltages"] = {
            "DP8B0885_CH1": 1.002,
            "DP8B1519_CH2": 5.0,
        }
        document["payload"]["frequencies"]["1.0"]["points"][1]["currents"] = {
            "DP8B0885_CH1": 0.13,
            "DP8B1519_CH2": 2.2,
        }
        with self.assertRaises(SimulationReadingsError) as raised:
            _parse_amplifier(document)
        self.assertIn("通道集合", str(raised.exception))

    def test_voltages_and_currents_must_share_the_same_channels(self):
        document = _amplifier_document()
        document["payload"]["frequencies"]["1.0"]["points"][0]["currents"] = {
            "DP8B0885_CH2": 0.12
        }
        with self.assertRaises(SimulationReadingsError) as raised:
            _parse_amplifier(document)
        self.assertIn("通道集合", str(raised.exception))

    def test_empty_channel_map_is_rejected(self):
        document = _amplifier_document()
        document["payload"]["frequencies"]["1.0"]["points"][0]["voltages"] = {}
        document["payload"]["frequencies"]["1.0"]["points"][0]["currents"] = {}
        with self.assertRaises(SimulationReadingsError) as raised:
            _parse_amplifier(document)
        self.assertIn("不能为空", str(raised.exception))


class NumberValidationTests(unittest.TestCase):
    """非数值、布尔与非有限数一律拒绝，错误信息带字段名。"""

    def test_non_finite_and_non_numeric_values_are_rejected(self):
        cases = (
            (float("nan"), "raw_dbm"),
            (float("inf"), "raw_dbm"),
            (-float("inf"), "raw_dbm"),
            ("-41.842", "raw_dbm"),
            (True, "raw_dbm"),
        )
        for value, field in cases:
            with self.subTest(value=value):
                document = _cable_loss_document()
                document["payload"]["measurements"][0][field] = value
                with self.assertRaises(SimulationReadingsError) as raised:
                    _parse_cable_loss(document)
                self.assertIn(field, str(raised.exception))

    def test_frequency_must_be_positive(self):
        for frequency in (0.0, -1.0):
            with self.subTest(frequency=frequency):
                document = _cable_loss_document()
                document["payload"]["measurements"][0]["frequency_ghz"] = frequency
                with self.assertRaises(SimulationReadingsError) as raised:
                    _parse_cable_loss(document)
                self.assertIn("frequency_ghz", str(raised.exception))


class FrequencyGridTests(unittest.TestCase):
    """频率集合必须与冻结配置逐点一致。"""

    def test_expected_grid_matches_the_payload(self):
        readings = _parse_cable_loss(expected_frequencies_ghz=(1.0, 2.0))
        self.assertEqual(
            [frequency.key for frequency in readings.frequency_grid], ["1.0", "2.0"]
        )

    def test_missing_or_extra_frequency_fails(self):
        for grid in ((1.0,), (1.0, 2.0, 3.0), (1.0, 1.1)):
            with self.subTest(grid=grid):
                with self.assertRaises(SimulationReadingsError) as raised:
                    _parse_cable_loss(expected_frequencies_ghz=grid)
                self.assertIn("频点", str(raised.exception))

    def test_frequency_match_uses_numeric_tolerance(self):
        """同一频率的浮点噪声（<= 1e-9 GHz）不算不一致。"""
        readings = _parse_driver(expected_frequencies_ghz=(1.0 + 1e-12,))
        self.assertEqual(len(readings.frequency_grid), 1)
        with self.assertRaises(SimulationReadingsError):
            _parse_driver(expected_frequencies_ghz=(1.0 + 1e-6,))

    def test_expected_grid_must_be_a_non_empty_numeric_sequence(self):
        for grid in (None, [], "1.0", (1.0, "2.0"), (1.0, float("nan"))):
            with self.subTest(grid=grid):
                if grid is None:
                    # None 表示不校验，不在这里测试。
                    continue
                with self.assertRaises(SimulationReadingsError):
                    _parse_cable_loss(expected_frequencies_ghz=grid)


class ErrorTaxonomyTests(unittest.TestCase):
    """读数错误归入配置类异常，并保留 ``ValueError`` 语义。"""

    def test_error_is_a_configuration_error_and_value_error(self):
        self.assertTrue(issubclass(SimulationReadingsError, ConfigurationError))
        self.assertTrue(issubclass(SimulationReadingsError, ValueError))
        with self.assertRaises(ValueError):
            raise SimulationReadingsError("读数无效")


class RepositoryDataTests(unittest.TestCase):
    """仓库里的三份 readings.json 必须能被本契约直接接受。"""

    def setUp(self):
        from infrastructure.filesystem.paths import (
            DEFAULT_MOCK_DATA_PATHS,
            MockStage,
        )

        self.paths = DEFAULT_MOCK_DATA_PATHS
        self.stage = MockStage

    def test_repository_readings_parse_with_the_document_contract(self):
        from application.ports.stage_result_repository import (
            measurement_type_for_stage,
        )
        from application.inputs.mock_scenario import load_mock_scenario

        for stage in self.stage:
            with self.subTest(stage=stage.value):
                path = self.paths.require_readings_path(stage)
                document = json.loads(path.read_text(encoding="utf-8-sig"))
                scenario = load_mock_scenario(
                    self.paths.require_scenario_path(stage),
                    expected_measurement_type=measurement_type_for_stage(
                        stage.value
                    ),
                )
                readings = simulation_readings_from_document(
                    document,
                    expected_measurement_type=measurement_type_for_stage(
                        stage.value
                    ),
                    expected_frequencies_ghz=scenario.source_config.test_frequencies_ghz,
                )
                self.assertEqual(
                    [frequency.ghz for frequency in readings.frequency_grid],
                    list(scenario.source_config.test_frequencies_ghz),
                )


if __name__ == "__main__":
    unittest.main()
