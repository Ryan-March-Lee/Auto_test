import unittest
from pathlib import Path

from release_manifest import REAL_DEVICE_ACCEPTANCE_STATES, build_manifest


class SecondRefactorAcceptanceTests(unittest.TestCase):
    """第二期阶段 6 的离线验收和现场门槛回归。"""

    ROOT = Path(__file__).resolve().parents[1]

    def test_real_device_acceptance_remains_an_independent_gate(self):
        manifest = build_manifest(self.ROOT)
        self.assertIn(manifest["real_device_acceptance"], REAL_DEVICE_ACCEPTANCE_STATES)
        self.assertEqual(build_manifest(self.ROOT, real_device_acceptance="passed")["real_device_acceptance"], "passed")
        with self.assertRaises(ValueError):
            build_manifest(self.ROOT, real_device_acceptance="approved")
        plan = (self.ROOT / "docs/第二期重构计划_GUI页面与MeasurementController.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Hardware smoke", plan)
        self.assertIn("不以离线测试通过替代真实设备验收", plan)


if __name__ == "__main__":
    unittest.main()
