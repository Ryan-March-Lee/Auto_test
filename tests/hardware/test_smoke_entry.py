import os
import unittest
from pathlib import Path

import pyvisa


class HardwareSmokeReadOnlyTests(unittest.TestCase):
    """Read-only hardware smoke: identity queries only, with deterministic cleanup."""

    def test_device_identity_and_safe_read_only_access(self):
        import json

        config_path = Path(os.environ["HARDWARE_SMOKE_CONFIG"])
        config = json.loads(config_path.read_text(encoding="utf-8"))
        manager = pyvisa.ResourceManager()
        resources = []
        try:
            for name, device in config["devices"].items():
                resource = manager.open_resource(
                    device["address"],
                    open_timeout=int(device.get("timeout_ms", 5000)),
                )
                resources.append(resource)
                resource.timeout = int(device.get("timeout_ms", 5000))
                identity = resource.query("*IDN?").strip()
                self.assertTrue(identity, f"{name} returned an empty identity")
                expected_model = device.get("model", "")
                if expected_model and not expected_model.startswith("REPLACE_WITH_"):
                    self.assertIn(expected_model, identity)
        finally:
            for resource in reversed(resources):
                resource.close()
            manager.close()


if __name__ == "__main__":
    unittest.main()
