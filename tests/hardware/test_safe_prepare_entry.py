import unittest
from pathlib import Path

import pyvisa

from hardware.safe_prepare_smoke import execute_and_write_safe_prepare_report
from hardware.smoke_gate import load_gated_smoke_config


class HardwareSafePrepareTests(unittest.TestCase):
    """Real-device preparation smoke; this module is outside ordinary discovery."""

    def test_prepare_devices_without_enabling_outputs(self):
        config_path, config = load_gated_smoke_config()
        if config.get("smoke_mode") != "safe_prepare":
            self.skipTest("configured smoke mode is not safe_prepare")
        report = execute_and_write_safe_prepare_report(
            config,
            pyvisa.ResourceManager(),
            config_path=config_path,
            hardware_root=Path(__file__).resolve().parents[2] / "hardware",
        )
        self.assertTrue(report["resources_closed"])
        self.assertEqual(set(report["devices"]), {"signal_generator", "spectrum_analyzer", "power_supply"})
        for result in report["devices"].values():
            self.assertTrue(result["identity"])
            self.assertTrue(result["closed"])
            self.assertTrue(result["safe_after_cleanup"])


if __name__ == "__main__":
    unittest.main()
