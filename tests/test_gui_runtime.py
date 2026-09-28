import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.gui_runtime import prepare_configuration


FIXTURE = Path(__file__).parent / "fixtures" / "config_driver_enabled_no_assignment.json"


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
            with patch("app.gui_runtime.prepare_run", return_value="prepared") as prepare_run:
                result = prepare_configuration(str(path), operation="cable_loss")
            self.assertEqual(result, "prepared")
            loaded = prepare_run.call_args.args[0]
            self.assertTrue(loaded.valid)
        finally:
            directory.cleanup()

    def test_driver_mapping_preparation_allows_external_driver_power(self):
        directory, path = self._write_config()
        try:
            with patch("app.gui_runtime.prepare_run", return_value="prepared") as prepare_run:
                result = prepare_configuration(str(path), operation="driver_mapping")
            self.assertEqual(result, "prepared")
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


if __name__ == "__main__":
    unittest.main()
