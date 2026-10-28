import unittest
from pathlib import Path
import ast

from release_manifest import REAL_DEVICE_ACCEPTANCE_STATES, build_manifest


class OfflineAcceptanceTests(unittest.TestCase):
    """离线验收矩阵与真实设备现场门槛回归。"""

    ROOT = Path(__file__).resolve().parents[1]

    def test_real_device_acceptance_remains_an_independent_gate(self):
        manifest = build_manifest(self.ROOT)
        self.assertIn(manifest["real_device_acceptance"], REAL_DEVICE_ACCEPTANCE_STATES)
        self.assertEqual(build_manifest(self.ROOT, real_device_acceptance="passed")["real_device_acceptance"], "passed")
        with self.assertRaises(ValueError):
            build_manifest(self.ROOT, real_device_acceptance="approved")

    def test_main_window_connects_page_lifecycle(self):
        source = (self.ROOT / "presentation" / "qt" / "main_window.py").read_text(encoding="utf-8-sig")
        self.assertIn("currentChanged.connect(self._on_page_changed)", source)
        self.assertIn("currentChanged.disconnect(self._on_page_changed)", source)
        self.assertIn("pages[previous].on_deactivated()", source)
        self.assertIn("pages[index].on_activated()", source)

    def test_controller_does_not_read_worker_service_results(self):
        source = (self.ROOT / "presentation/qt/measurement_controller.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        self.assertNotIn("last_result", source)
        self.assertFalse(
            any(
                isinstance(node, ast.Attribute) and node.attr == "service"
                for node in ast.walk(tree)
            )
        )


if __name__ == "__main__":
    unittest.main()
