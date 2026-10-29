import ast
import unittest
from pathlib import Path

from tests.test_architecture_dependencies import _worker_construction_violations


ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "presentation" / "qt" / "main_window.py"
QT_ROOT = ROOT / "presentation" / "qt"
UI_TESTS = ROOT / "tests" / "ui"
FORBIDDEN_REVERSE_IMPORTS = {"enhanced_main_gui", "enhanced_workers"}
HARDWARE_ROOTS = {
    "instrument",
    "instrument_control",
    "pyvisa",
    "visa",
    "scpi",
    "hardware",
}
# 表现层只允许装配根连接基础设施；日志是各层共用的横切工具，不参与业务耦合。
COMPOSITION_ROOT = "composition.py"
CROSS_CUTTING_INFRASTRUCTURE = "infrastructure.logging"
# UI 测试只覆盖表现层与 DTO/端口，装配真实后端的场景属于 tests/integration。
BACKEND_IMPLEMENTATION_ROOTS = HARDWARE_ROOTS | {"app", "infrastructure"}


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8-sig"), str(path))


def _imported_modules(tree):
    """收集模块级与函数级的绝对导入模块名，用于静态边界判定。"""
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def _imported_roots(tree):
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


class PresentationBoundaryTests(unittest.TestCase):
    """presentation 层与 Qt 模块的静态边界门禁。"""

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

    def test_presentation_reaches_infrastructure_only_through_the_composition_root(self):
        """表现层不装配基础设施：路径、存储与扫描器都由 composition/app 注入。

        例外只有两处：``composition.py`` 是 Qt 进程的装配根，负责把应用服务与
        基础设施实现接在一起；``infrastructure.logging`` 是各层共用的日志工具。
        """
        violations = []
        for path in QT_ROOT.rglob("*.py"):
            modules = _imported_modules(_tree(path))
            infrastructure = {
                module
                for module in modules
                if module.split(".")[0] == "infrastructure"
            }
            if not infrastructure or path.name == COMPOSITION_ROOT:
                continue
            unexpected = {
                module
                for module in infrastructure
                if module != CROSS_CUTTING_INFRASTRUCTURE
                and not module.startswith(f"{CROSS_CUTTING_INFRASTRUCTURE}.")
            }
            if unexpected:
                violations.append(
                    f"{path.relative_to(ROOT)}: {sorted(unexpected)}"
                )
        self.assertEqual(
            violations,
            [],
            "表现层只能通过 composition.py 或 application 端口触碰基础设施: "
            + ", ".join(violations),
        )

    def test_ui_tests_do_not_import_backend_implementations(self):
        """``tests/ui`` 是纯表现层测试：装配真实后端的场景必须放在 tests/integration。"""
        violations = []
        for path in sorted(UI_TESTS.rglob("test_*.py")):
            modules = _imported_modules(_tree(path))
            backend = {
                module
                for module in modules
                if module.split(".")[0] in BACKEND_IMPLEMENTATION_ROOTS
            }
            if backend:
                violations.append(
                    f"{path.relative_to(ROOT)}: {sorted(backend)}"
                )
        self.assertEqual(
            violations,
            [],
            "UI 测试不得导入后端实现（app/infrastructure/硬件）: "
            + ", ".join(violations),
        )


class QtMigrationResidueTests(unittest.TestCase):
    """步骤 12：生产 Qt 代码不留迁移残留。

    覆盖四类残留：旧式 ``print`` 与 logger 混用、手动 ``sys.path`` 修改、静默吞掉
    业务失败的 ``except Exception: pass``、以及把私有名字当作兼容导出转发。
    """

    def _production_sources(self):
        return sorted(QT_ROOT.rglob("*.py"))

    def test_production_qt_code_logs_instead_of_printing(self):
        violations = []
        for path in self._production_sources():
            tree = _tree(path)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "print"
                ):
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(
            violations,
            [],
            "UI 代码的日志统一走 get_logger，界面提示走状态栏/对话框: "
            + ", ".join(violations),
        )

    def test_production_qt_code_does_not_modify_sys_path(self):
        violations = []
        for path in self._production_sources():
            tree = _tree(path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Attribute):
                    continue
                chain = ast.unparse(node)
                if chain.startswith("sys.path"):
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno} {chain}")
        self.assertEqual(violations, [], "正式链路不得修改 sys.path: " + ", ".join(violations))

    def test_production_qt_code_does_not_swallow_failures(self):
        """``except Exception: pass`` 会把业务失败静默丢掉，必须改成日志/状态/对话框。

        只拦截宽泛处理器（裸 ``except`` / ``Exception`` / ``BaseException``）：窄类型的
        ``except (RuntimeError, TypeError): pass`` 属于幂等断开 signal 之类的固定模式。
        """
        broad = {"Exception", "BaseException"}
        violations = []
        for path in self._production_sources():
            tree = _tree(path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.ExceptHandler):
                    continue
                body = list(node.body)
                if not (len(body) == 1 and isinstance(body[0], ast.Pass)):
                    continue
                caught = ast.unparse(node.type) if node.type else None
                if caught is None or caught in broad:
                    violations.append(
                        f"{path.relative_to(ROOT)}:{node.lineno} ({caught or '裸 except'})"
                    )
        self.assertEqual(
            violations,
            [],
            "业务失败不得被 except Exception: pass 掩盖: " + ", ".join(violations),
        )

    def test_pages_module_does_not_export_private_names(self):
        """``__all__`` 曾把私有转发器当兼容导出，私有名字不得再出现在导出表里。"""
        from presentation.qt import pages

        private = [name for name in pages.__all__ if name.startswith("_")]
        self.assertEqual(private, [], "pages.__all__ 不得导出私有名字: " + ", ".join(private))

    def test_production_qt_code_never_terminates_a_thread(self):
        """超时必须保持引用让用户重试，``QThread.terminate()`` 会让线程带锁退出。"""
        violations = []
        for path in self._production_sources():
            for node in ast.walk(_tree(path)):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "terminate"
                ):
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(
            violations,
            [],
            "生产 Qt 代码不得强杀线程: " + ", ".join(violations),
        )

if __name__ == "__main__":
    unittest.main()
