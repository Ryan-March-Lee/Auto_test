"""Mock 正式结果读取入口：只接受数据包里的正式结果契约。"""

import json
import tempfile
import unittest
from pathlib import Path

from application.dto.measurement_results import MeasurementType
from application.errors import ConfigurationError
from application.inputs.mock_result import load_mock_result

PROJECT_ROOT = Path(__file__).parents[1]
MOCK_DATA = PROJECT_ROOT / "mock_data"
_RESULTS = {
    MeasurementType.CABLE_LOSS: MOCK_DATA
    / "cable_loss"
    / "20261117T060245Z_9b2e7d10.json",
    MeasurementType.DRIVER_POWER_MAPPING: MOCK_DATA
    / "driver_mapping"
    / "20261117T060600Z_5c1d9e42.json",
    MeasurementType.AMPLIFIER: MOCK_DATA
    / "amplifier"
    / "20261117T070237Z_7b3d9f21.json",
}


class MockResultReaderTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.directory = Path(self._temporary.name)

    def test_repository_results_load_with_their_expected_type(self):
        for measurement_type, path in _RESULTS.items():
            with self.subTest(measurement_type=measurement_type.value):
                result = load_mock_result(
                    path, expected_measurement_type=measurement_type
                )
                self.assertIs(result.measurement_type, measurement_type)

    def test_a_different_expected_type_is_rejected(self):
        with self.assertRaisesRegex(ConfigurationError, "测量类型与目标阶段不一致"):
            load_mock_result(
                _RESULTS[MeasurementType.CABLE_LOSS],
                expected_measurement_type=MeasurementType.AMPLIFIER,
            )

    def test_unknown_expected_type_is_rejected(self):
        with self.assertRaisesRegex(ConfigurationError, "期望的 Mock 测量类型非法"):
            load_mock_result(
                _RESULTS[MeasurementType.CABLE_LOSS],
                expected_measurement_type="unknown",
            )

    def test_missing_or_broken_files_fail_as_configuration_errors(self):
        missing = self.directory / "missing.json"
        with self.assertRaisesRegex(ConfigurationError, "无法读取"):
            load_mock_result(missing)

        broken = self.directory / "broken.json"
        broken.write_text("{ not json", encoding="utf-8")
        with self.assertRaisesRegex(ConfigurationError, "不是有效的 JSON"):
            load_mock_result(broken)

        not_an_object = self.directory / "array.json"
        not_an_object.write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(ConfigurationError, "必须是 JSON 对象"):
            load_mock_result(not_an_object)

        not_formal = self.directory / "informal.json"
        not_formal.write_text(
            json.dumps({"unexpected": True}), encoding="utf-8"
        )
        with self.assertRaisesRegex(ConfigurationError, "不符合正式结果契约"):
            load_mock_result(not_formal)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
