import json
import tempfile
import unittest
from pathlib import Path

from persistence.config_repository import ConfigurationRepository


FIXTURE = Path(__file__).parent / "fixtures" / "config_driver_enabled_no_assignment.json"


class ConfigurationRepositoryTests(unittest.TestCase):
    def test_loads_legacy_config_with_conversion_report_without_hardware_access(self):
        result = ConfigurationRepository().load(FIXTURE)

        self.assertEqual(result.source_format, "legacy")
        with FIXTURE.open("r", encoding="utf-8") as source:
            legacy = json.load(source)
        self.assertEqual(result.configuration.test_plan.frequencies, legacy["test_frequencies"])
        self.assertIn("run_mapping.wiring.confirmed", result.unresolved_fields)
        self.assertTrue(result.warnings)
        self.assertFalse(result.valid)

    def test_loads_split_models_and_validates_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / "plan.json"
            mapping_path = Path(directory) / "mapping.json"
            plan_path.write_text(json.dumps({"schema_version": "9.0"}), encoding="utf-8")
            mapping_path.write_text(json.dumps({"schema_version": "1.0"}), encoding="utf-8")

            result = ConfigurationRepository().load(plan_path, mapping_path)

        self.assertEqual(result.source_format, "modern")
        self.assertFalse(result.valid)
        self.assertTrue(any(issue.path == "schema_version" for issue in result.validation.errors))


if __name__ == "__main__":
    unittest.main()
