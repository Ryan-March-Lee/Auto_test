import json
import os
import unittest
from pathlib import Path

from hardware.minimal_action_smoke import execute_and_write_minimal_action_report


class HardwareMinimalActionTests(unittest.TestCase):
    """Real-device one-action smoke; this module is outside ordinary discovery."""

    def test_one_bounded_action_and_safe_shutdown(self):
        config_path = Path(os.environ["HARDWARE_SMOKE_CONFIG"])
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if config.get("smoke_mode") != "minimal_action":
            self.skipTest("configured smoke mode is not minimal_action")
        import pyvisa

        report = execute_and_write_minimal_action_report(
            config,
            pyvisa.ResourceManager(),
            config_path=config_path,
            hardware_root=Path(__file__).resolve().parents[2] / "hardware",
        )
        self.assertEqual(report["action_count"], 1)
        self.assertTrue(report["resources_closed"])
        for result in report["devices"].values():
            self.assertTrue(result["identity"])
            self.assertTrue(result["closed"])
            self.assertTrue(result["safe_after_cleanup"])


if __name__ == "__main__":
    unittest.main()
