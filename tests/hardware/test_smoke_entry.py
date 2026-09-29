import os
import unittest
from pathlib import Path

import pyvisa

from hardware.read_only_smoke import (
    execute_and_write_report,
)


class HardwareSmokeReadOnlyTests(unittest.TestCase):
    """Read-only hardware smoke: queries only, with deterministic cleanup."""

    def test_device_identity_and_safe_read_only_access(self):
        import json

        config_path = Path(os.environ["HARDWARE_SMOKE_CONFIG"])
        config = json.loads(config_path.read_text(encoding="utf-8"))
        hardware_root = Path(__file__).resolve().parents[2] / "hardware"
        result_path = config.get("result_path")
        report = execute_and_write_report(
            config,
            pyvisa.ResourceManager(),
            config_path=config_path,
            hardware_root=hardware_root,
        )
        for name, result in report["devices"].items():
            self.assertTrue(result["identity"], f"{name} returned an empty identity")
        self.assertTrue(report["resources_closed"])

    def test_failure_writes_report_before_propagating(self):
        import json
        import tempfile
        from unittest.mock import patch

        from hardware.read_only_smoke import SmokeExecutionError

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hardware = root / "hardware"
            hardware.mkdir()
            config_path = hardware / "smoke_config.local.json"
            report_path = hardware / "failure.json"
            config_path.write_text(
                json.dumps({"result_path": "failure.json", "devices": {}}), encoding="utf-8"
            )
            failure_report = {"devices": {}, "resources_closed": True, "error": "RuntimeError: failed"}
            with patch(
                "hardware.read_only_smoke.run_read_only_smoke",
                side_effect=SmokeExecutionError("failed", failure_report),
            ):
                with self.assertRaises(SmokeExecutionError):
                    execute_and_write_report(
                        {"result_path": "failure.json", "devices": {}},
                        object(),
                        config_path=config_path,
                        hardware_root=hardware,
                    )
            self.assertTrue(report_path.is_file())


if __name__ == "__main__":
    unittest.main()
