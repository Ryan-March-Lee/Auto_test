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
        plan = (self.ROOT / "docs/重构收尾与真实HardwareSmoke验收.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("第二期", plan)
        self.assertIn("不得将离线测试或历史 `minimal_action` 结果视为完整现场验收", plan)

    def test_offline_acceptance_matrix_is_explicitly_recorded(self):
        plan = (self.ROOT / "docs/重构收尾与真实HardwareSmoke验收.md").read_text(
            encoding="utf-8"
        )
        for item in (
            "页面、状态对象、controller、worker、窗口组合和架构依赖均已完成离线回归",
            "每类测量都必须覆盖正常完成、普通停止、紧急停止和测量异常后的重新连接",
            "Hardware smoke 及必要的真实测量没有未解释失败",
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
