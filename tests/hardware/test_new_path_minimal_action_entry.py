import json
import os
import unittest
from pathlib import Path

from hardware.new_path_minimal_action_smoke import execute_and_write_new_path_report


class HardwareNewAssemblyMinimalActionTests(unittest.TestCase):
    """Real-device one-action smoke through the application assembly path."""

    def test_one_bounded_action_and_safe_shutdown(self):
        smoke_path = Path(os.environ["HARDWARE_SMOKE_CONFIG"])
        smoke_config = json.loads(smoke_path.read_text(encoding="utf-8"))
        if smoke_config.get("assembly_path", "legacy") != "new_application":
            self.skipTest("configured smoke path is not new_application")
        if smoke_config.get("smoke_mode") != "minimal_action":
            self.skipTest("configured smoke mode is not minimal_action")
        report = execute_and_write_new_path_report(
            smoke_config,
            smoke_config_path=smoke_path,
            application_config_path=(
                smoke_path.parent / smoke_config.get("application_config_path", "../config.json")
            ).resolve(),
            hardware_root=Path(__file__).resolve().parents[2] / "hardware",
        )
        self.assertEqual(report["action_count"], 1)
        self.assertTrue(report["resources_closed"])
        for name in ("signal_generator", "spectrum_analyzer"):
            self.assertTrue(report["devices"][name]["identity"])
            self.assertTrue(report["devices"][name]["closed"])
            self.assertTrue(report["devices"][name]["safe_after_cleanup"])
        for result in report["devices"]["power_supply"]:
            self.assertTrue(result["identity"])
            self.assertTrue(result["closed"])
            self.assertTrue(result["safe_after_cleanup"])


if __name__ == "__main__":
    unittest.main()
