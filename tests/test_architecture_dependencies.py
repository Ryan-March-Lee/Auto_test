import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


def _production_sources():
    """Return every project Python source outside tests and tooling helpers."""
    excluded_parts = {".git", "__pycache__", "tests"}
    return sorted(
        path
        for path in ROOT.rglob("*.py")
        if not (set(path.relative_to(ROOT).parts) & excluded_parts)
    )


def _legacy_imports(tree):
    """Find statically identifiable imports of the legacy worker module."""
    violations = []
    dynamic_import_names = {"import_module"}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "importlib":
            dynamic_import_names.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "import_module"
            )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name == "enhanced_workers"
                or alias.name.startswith("enhanced_workers.")
                for alias in node.names
            ):
                violations.append(node)
        elif isinstance(node, ast.ImportFrom):
            if node.module == "enhanced_workers" or any(
                alias.name == "enhanced_workers" for alias in node.names
            ):
                violations.append(node)
        elif isinstance(node, ast.Call):
            is_dynamic_import = (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "importlib"
                and node.func.attr == "import_module"
            ) or (
                isinstance(node.func, ast.Name)
                and node.func.id in dynamic_import_names | {"__import__"}
            )
            if is_dynamic_import and node.args:
                module_name = node.args[0]
                if isinstance(module_name, ast.Constant) and isinstance(module_name.value, str):
                    if module_name.value == "enhanced_workers" or module_name.value.startswith(
                        "enhanced_workers."
                    ):
                        violations.append(node)
    return violations


class ProductionDependencyTests(unittest.TestCase):
    def test_production_modules_do_not_import_enhanced_workers(self):
        violations = []
        for source_path in _production_sources():
            tree = ast.parse(source_path.read_text(encoding="utf-8-sig"), str(source_path))
            if _legacy_imports(tree):
                violations.append(str(source_path.relative_to(ROOT)))

        self.assertEqual(violations, [], "生产模块不得依赖 enhanced_workers: " + ", ".join(violations))

    def test_application_assembly_has_no_hardware_or_qt_imports(self):
        forbidden = {"pyvisa", "PySide6", "PyQt5"}
        violations = []
        for package in ("app", "application"):
            for source_path in (ROOT / package).rglob("*.py"):
                tree = ast.parse(source_path.read_text(encoding="utf-8-sig"), str(source_path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        names = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        names = [node.module or ""]
                    else:
                        continue
                    if any(name == value or name.startswith(value + ".") for name in names for value in forbidden):
                        violations.append(str(source_path.relative_to(ROOT)))

        self.assertEqual(violations, [], "应用层不得直接依赖硬件或 Qt: " + ", ".join(violations))


if __name__ == "__main__":
    unittest.main()
