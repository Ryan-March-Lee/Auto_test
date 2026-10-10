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
        self.assertEqual(inputs.__all__, ["ResultInputReader"])
        self.assertIsNotNone(inputs.ResultInputReader)


if __name__ == "__main__":
    unittest.main()
