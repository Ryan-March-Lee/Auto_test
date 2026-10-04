import unittest
from pathlib import Path
import ast

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

    def test_offline_acceptance_matrix_is_explicitly_recorded(self):
        plan = (self.ROOT / "docs/第二期重构计划_GUI页面与MeasurementController.md").read_text(
            encoding="utf-8"
        )
        for item in (
            "验收矩阵：",
            "页面生命周期：",
            "结果边界：",
            "离线门槛：",
        ):
            self.assertIn(item, plan)

    def test_main_window_connects_page_lifecycle(self):
        source = (self.ROOT / "enhanced_main_gui.py").read_text(encoding="utf-8-sig")
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
