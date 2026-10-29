import importlib
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class OptionalCapabilityBoundaryTests(unittest.TestCase):
    def test_csv_converter_has_no_import_time_file_access(self):
        with patch("pathlib.Path.open", side_effect=AssertionError("import accessed a file")):
            module = importlib.import_module("format_convert.csv_to_MDIF")
        self.assertTrue(callable(module.convert_csv_to_mdif))

    def test_dpd_does_not_import_main_system_modules(self):
        source = (PROJECT_ROOT / "DPD_auto_test" / "Tradition_function.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("project_paths", source)
        self.assertNotIn("config_io", source)
        self.assertNotIn("result_storage", source)

    def test_dpd_dependency_manifest_covers_script_imports(self):
        requirements = (PROJECT_ROOT / "requirements-dpd.txt").read_text(encoding="utf-8")
        for dependency in ("numpy", "matplotlib", "scipy", "tensorflow", "nptdms", "pyvisa", "jupyter"):
            self.assertIn(dependency, requirements)

if __name__ == "__main__":
    unittest.main()
