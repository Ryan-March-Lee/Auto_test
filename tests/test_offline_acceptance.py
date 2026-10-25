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
        plan = (self.ROOT / "docs/重构收尾与真实HardwareSmoke验收.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("不得将离线测试或历史 `minimal_action` 结果视为完整现场验收", plan)

    def test_offline_acceptance_matrix_is_recorded(self):
        plan = (self.ROOT / "docs/重构收尾与真实HardwareSmoke验收.md").read_text(
            encoding="utf-8"
        )
        for item in (
            "页面、状态对象、controller、worker、窗口组合和架构依赖均已完成离线回归",
            "每类测量都必须覆盖正常完成、普通停止、紧急停止和测量异常后的重新连接",
            "Hardware smoke 及必要的真实测量没有未解释失败",
        ):
            self.assertIn(item, plan)

    def test_real_device_acceptance_sections_remain_documented(self):
        plan = (self.ROOT / "docs/重构收尾与真实HardwareSmoke验收.md").read_text(
            encoding="utf-8"
        )
        for item in (
            "## 3. 现场验收前置条件",
            "## 4. 真实 Hardware smoke 执行顺序",
            "不得执行真实 RF 或电源动作",
        ):
            self.assertIn(item, plan)

    def test_step_f_is_the_single_acceptance_standard(self):
        guide = (self.ROOT / "docs/测试运行目录与配置结果解耦重构指南.md").read_text(
            encoding="utf-8"
        )
        record = (self.ROOT / "docs/重构收尾与真实HardwareSmoke验收.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("唯一验收口径", guide)
        self.assertIn("以本步骤为准", guide)
        self.assertIn("以指南步骤 F 为准", record)
        for marker in ("F1", "F2", "F3", "F4", "F5", "F6"):
            self.assertIn(marker, guide)
        self.assertIn("F 归档后", guide)
        self.assertIn("F 归档不等于全部重构收尾完成", record)
        self.assertIn("C1 至 C4", record)
        self.assertIn("E2 至 E4", record)

    def test_merged_record_keeps_the_current_run_contract(self):
        guide = (self.ROOT / "docs/测试运行目录与配置结果解耦重构指南.md").read_text(
            encoding="utf-8"
        )
        record = (self.ROOT / "docs/重构收尾与真实HardwareSmoke验收.md").read_text(
            encoding="utf-8"
        )
        for document in (guide, record):
            for item in (
                "runs/<run_id>/",
                "manifest",
                "不创建运行目录",
                "旧兼容测量入口",
                "无路径",
            ):
                self.assertIn(item, document)

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
