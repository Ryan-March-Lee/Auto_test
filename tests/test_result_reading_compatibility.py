import importlib
import unittest


class ResultReadingCompatibilityTests(unittest.TestCase):
    def test_legacy_module_imports_and_reexports_public_api(self):
        legacy = importlib.import_module("result_reading")
        canonical = importlib.import_module("domain.result_reading")

        expected = set(canonical.__all__) | {"load_measurement_result", "load_result_model"}
        self.assertEqual(set(legacy.__all__), expected)
        for name in canonical.__all__:
            self.assertIs(getattr(legacy, name), getattr(canonical, name))

    def test_application_input_package_keeps_reader_export(self):
        inputs = importlib.import_module("application.inputs")
        # 正式读取器保持导出；兼容读取器是步骤 D 新增的独立导出。
        self.assertEqual(
            inputs.__all__, ["LegacyResultInputReader", "ResultInputReader"]
        )
        self.assertIsNotNone(inputs.ResultInputReader)
        self.assertIsNotNone(inputs.LegacyResultInputReader)


if __name__ == "__main__":
    unittest.main()
