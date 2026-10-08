import ast
import importlib.util
import unittest
from pathlib import Path

from tests.test_architecture_dependencies import _worker_construction_violations


ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "enhanced_main_gui.py"
QT_ROOT = ROOT / "presentation" / "qt"
FORBIDDEN_REVERSE_IMPORTS = {"enhanced_main_gui", "enhanced_workers"}
HARDWARE_ROOTS = {
    "instrument",
    "instrument_control",
    "pyvisa",
    "visa",
    "scpi",
    "hardware",
}
COMPATIBLE_EXPORTS = {
    "MainWindow",
    "ChatPanel",
    "RealTimePlotWidget",
    "ConnectionDialog",
    "MeasurementKind",
}


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8-sig"), str(path))


def _imported_roots(tree):
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


class ThirdRefactorBaselineTests(unittest.TestCase):
    """第三期 3.0 的静态门禁，确保迁移从可回退基线开始。"""

    def test_new_qt_modules_do_not_import_legacy_gui(self):
        violations = []
        for path in QT_ROOT.rglob("*.py"):
            roots = _imported_roots(_tree(path))
            reverse = roots & FORBIDDEN_REVERSE_IMPORTS
            if reverse:
                violations.append(f"{path.relative_to(ROOT)}: {sorted(reverse)}")
        self.assertEqual(violations, [])

    def test_measurement_pages_do_not_construct_workers(self):
        page_paths = tuple(QT_ROOT / name for name in (
            "cable_loss_page.py",
            "driver_mapping_page.py",
            "amplifier_page.py",
        ))
        violations = []
        for path in page_paths:
            violations.extend(
                f"{path.relative_to(ROOT)} constructs a measurement worker"
                for _ in _worker_construction_violations(_tree(path))
            )
        self.assertEqual(violations, [])

    def test_main_window_does_not_call_measurement_assembly(self):
        forbidden = {
            "create_cable_loss_measurement",
            "create_driver_mapping_measurement",
            "create_amplifier_measurement",
        }
        calls = []
        for node in ast.walk(_tree(MAIN_WINDOW)):
            if isinstance(node, ast.Call):
                called = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
                if called in forbidden:
                    calls.append(called)
        self.assertEqual(calls, [])

    def test_compatibility_entrypoint_exports_required_names(self):
        if not all(importlib.util.find_spec(name) for name in ("PySide6", "matplotlib")):
            self.skipTest("当前环境缺少 GUI 导入依赖")
        module = __import__("enhanced_main_gui")
        missing = [name for name in COMPATIBLE_EXPORTS if not hasattr(module, name)]
        self.assertEqual(missing, [])

    def test_third_refactor_plan_records_phase_3_0_gate(self):
        plan = (ROOT / "docs/第三期重构计划_enhanced_main_gui拆分与离线验收.md").read_text(
            encoding="utf-8"
        )
        for item in (
            "## 阶段 3.0：修复基线并建立第三期门禁",
            "## 7. 测试和验收矩阵",
            "### 7.2 第三期最终离线门槛",
            "真实 Hardware smoke 不属于 3.1 至 3.5 的中间步骤",
        ):
            self.assertIn(item, plan)

    def test_qt_peripheral_modules_have_no_hardware_imports(self):
        peripheral_paths = (
            QT_ROOT / "pages.py",
            QT_ROOT / "measurement_state.py",
            QT_ROOT / "measurement_controller_contract.py",
        )
        violations = []
        for path in peripheral_paths:
            if not path.exists():
                continue
            roots = _imported_roots(_tree(path))
            hardware = roots & HARDWARE_ROOTS
            if hardware:
                violations.append(f"{path.relative_to(ROOT)}: {sorted(hardware)}")
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
