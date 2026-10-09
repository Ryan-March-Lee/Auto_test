import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]

PAGE_SOURCES = tuple(
    ROOT / "presentation" / "qt" / name
    for name in ("cable_loss_page.py", "driver_mapping_page.py", "amplifier_page.py")
)
CONTROLLER_SOURCE = ROOT / "presentation" / "qt" / "measurement_controller.py"
MAIN_WINDOW_SOURCE = ROOT / "presentation" / "qt" / "main_window.py"
MEASUREMENT_STATE_FIELDS = {
    "current_worker",
    "instrument_ctrl",
    "instrument_worker",
    "emergency_stop",
    "real_time_data",
    "rt_frequency_list",
    "rt_current_freq_index",
    "rt_user_browsing",
}
LEGACY_MAIN_WINDOW_MEASUREMENT_WRITES = {
    "instrument_ctrl",
    "instrument_worker",
    "emergency_stop",
    "real_time_data",
    "rt_frequency_list",
    "rt_current_freq_index",
    "rt_user_browsing",
}
FORBIDDEN_PAGE_IMPORT_ROOTS = {
    "enhanced_workers",
    "result_storage",
    "pyvisa",
    "visa",
    "scpi",
    "hardware",
    "instrument",
    "instrument_control",
}
FORBIDDEN_CONTROLLER_IMPORT_ROOTS = FORBIDDEN_PAGE_IMPORT_ROOTS | {
    "measurement_services",
    "cable_loss_measurement",
    "driver_power_mapping",
    "amplifier_measurement",
    "result_reading",
    "app",
    "infrastructure",
    "workers",
}
FORBIDDEN_MEASUREMENT_CALLS = {
    "create_cable_loss_measurement",
    "create_driver_mapping_measurement",
    "create_amplifier_measurement",
}
MEASUREMENT_WORKERS = {
    "CableLossWorker",
    "DriverMappingWorker",
    "AmplifierWorker",
}


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8-sig"), str(path))


def _imported_modules(tree):
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((alias.name, alias.asname, alias.name.split(".")[0]) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.extend(
                (module, alias.asname or alias.name, alias.name)
                for alias in node.names
            )
    return tuple(imports)


def _module_has_forbidden_root(module, forbidden_roots):
    parts = module.lower().split(".")
    return any(part in {root.lower() for root in forbidden_roots} for part in parts)


def _called_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _worker_aliases(tree):
    aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[-1] == "workers":
                aliases.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name in MEASUREMENT_WORKERS
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.endswith(".workers"):
                    aliases.add(alias.asname or alias.name.split(".")[-1])
    return aliases


def _worker_construction_violations(tree):
    aliases = _worker_aliases(tree)
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = _called_name(node.func)
        if called in MEASUREMENT_WORKERS or called in aliases:
            violations.append(node)
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            if node.func.value.id in aliases and node.func.attr in MEASUREMENT_WORKERS:
                violations.append(node)
    return violations


def _main_window_class(tree):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            return node
    raise AssertionError("enhanced_main_gui.py 中未找到 MainWindow 类")


def _main_window_measurement_writes(tree):
    writes = set()
    for node in ast.walk(_main_window_class(tree)):
        targets = []
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        elif isinstance(node, ast.NamedExpr):
            targets = [node.target]
        for target in targets:
            for child in ast.walk(target):
                if (
                    isinstance(child, ast.Attribute)
                    and isinstance(child.value, ast.Name)
                    and child.value.id == "self"
                    and child.attr in MEASUREMENT_STATE_FIELDS
                ):
                    writes.add(child.attr)
    return writes


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
    def test_configuration_page_owns_configuration_ui(self):
        source = MAIN_WINDOW_SOURCE.read_text(encoding="utf-8-sig")
        tree = _tree(MAIN_WINDOW_SOURCE)
        methods = {
            node.name
            for node in _main_window_class(tree).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertNotIn("create_power_supply_config", methods)
        self.assertNotIn("create_power_assignment_config", methods)
        self.assertNotIn("on_pa_unit_count_changed", methods)

    def test_configuration_modules_do_not_depend_on_main_window(self):
        for path in (Path("presentation/qt/config_page.py"), Path("presentation/qt/config_form_state.py")):
            source = path.read_text(encoding="utf-8-sig")
            self.assertNotIn("enhanced_main_gui", source)
            self.assertNotIn("MeasurementController", source)

    def test_main_window_registers_pages_without_legacy_builder(self):
        source = MAIN_WINDOW_SOURCE.read_text(encoding="utf-8-sig")
        self.assertNotIn("_legacy_tab", source)
        self.assertNotIn("on_measurement_finished", source)
        self.assertIn("build_configuration=self.create_config_tab", source)
        self.assertIn("build_visualization=self.create_visualization_tab", source)
        self.assertIn("build_export=self.create_data_export_tab", source)
        self.assertIn("self.tab_widget.addTab(page, definition.title)", source)

    def test_main_window_keeps_measurement_completion_refresh(self):
        source = MAIN_WINDOW_SOURCE.read_text(encoding="utf-8-sig")
        self.assertIn(
            "self.measurement_controller.signals.finished.connect(self.refresh_file_list)",
            source,
            "MeasurementController finished 必须恢复导出文件列表刷新",
        )

    def test_main_window_separates_instrument_worker_callbacks(self):
        source = MAIN_WINDOW_SOURCE.read_text(encoding="utf-8-sig")
        self.assertIn(
            "self.instrument_worker.signals.error.connect(self.on_instrument_error)",
            source,
        )
        self.assertIn(
            "self.instrument_worker.signals.stopped.connect(self.on_instrument_stopped)",
            source,
        )
        self.assertIn(
            "error_callback=lambda message: self.on_measurement_error(message)",
            source,
        )
        for callback in ("on_instrument_error", "on_instrument_stopped"):
            method = next(
                node
                for node in _main_window_class(_tree(MAIN_WINDOW_SOURCE)).body
                if isinstance(node, ast.FunctionDef) and node.name == callback
            )
            callback_source = ast.get_source_segment(source, method)
            self.assertNotIn("cable_loss_btn", callback_source)
            self.assertNotIn("driver_mapping_btn", callback_source)
            self.assertNotIn("amplifier_test_btn", callback_source)

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

    def test_measurement_pages_have_no_forbidden_architecture_dependencies(self):
        violations = []
        for source_path in PAGE_SOURCES:
            tree = _tree(source_path)
            for module, _alias, _name in _imported_modules(tree):
                if _module_has_forbidden_root(module, FORBIDDEN_PAGE_IMPORT_ROOTS):
                    violations.append(f"{source_path.relative_to(ROOT)} imports {module}")
                if module == "app.gui_runtime" or module.startswith("app.gui_runtime."):
                    violations.append(f"{source_path.relative_to(ROOT)} imports {module}")
                if module.endswith("workers"):
                    violations.append(f"{source_path.relative_to(ROOT)} imports worker module {module}")
            violations.extend(
                f"{source_path.relative_to(ROOT)} constructs a measurement worker"
                for _ in _worker_construction_violations(tree)
            )
        self.assertEqual(violations, [], "测量页面存在禁止依赖: " + "; ".join(violations))

    def test_measurement_controller_has_no_implementation_dependencies(self):
        tree = _tree(CONTROLLER_SOURCE)
        violations = [
            f"{CONTROLLER_SOURCE.relative_to(ROOT)} imports {module}"
            for module, _alias, _name in _imported_modules(tree)
            if _module_has_forbidden_root(module, FORBIDDEN_CONTROLLER_IMPORT_ROOTS)
            or module == "instrument"
            or module.startswith("instrument.")
        ]
        self.assertEqual(violations, [], "controller 存在实现层依赖: " + "; ".join(violations))

    def test_main_window_does_not_directly_assemble_measurements(self):
        tree = _tree(MAIN_WINDOW_SOURCE)
        main_window = _main_window_class(tree)
        imported_aliases = {
            alias
            for module, alias, name in _imported_modules(tree)
            if module == "app.gui_runtime" and name in FORBIDDEN_MEASUREMENT_CALLS
        }
        runtime_module_aliases = {
            alias
            for module, alias, _name in _imported_modules(tree)
            if module == "app.gui_runtime"
        }
        forbidden_names = FORBIDDEN_MEASUREMENT_CALLS | imported_aliases
        calls = set()
        for node in ast.walk(main_window):
            if not isinstance(node, ast.Call):
                continue
            called = _called_name(node.func)
            if called in forbidden_names:
                calls.add(called)
            elif (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in runtime_module_aliases
                and node.func.attr in FORBIDDEN_MEASUREMENT_CALLS
            ):
                calls.add(f"{node.func.value.id}.{node.func.attr}")
        self.assertEqual(
            calls,
            set(),
            "MainWindow 不得直接调用 app.gui_runtime 测量组装函数: " + ", ".join(sorted(calls)),
        )

    def test_main_window_measurement_state_writes_stay_within_legacy_baseline(self):
        writes = _main_window_measurement_writes(_tree(MAIN_WINDOW_SOURCE))
        added_writes = writes - LEGACY_MAIN_WINDOW_MEASUREMENT_WRITES
        self.assertEqual(
            added_writes,
            set(),
            "MainWindow 新增测量业务状态写入: " + ", ".join(sorted(added_writes)),
        )

    def test_worker_aliases_and_attribute_construction_are_detected(self):
        source = """
from presentation.qt.workers import CableLossWorker as Worker
import presentation.qt.workers as worker_module
Worker()
worker_module.AmplifierWorker()
"""
        tree = ast.parse(source)
        self.assertEqual(len(_worker_construction_violations(tree)), 2)

    def test_runtime_module_alias_calls_are_detected(self):
        source = """
import app.gui_runtime as runtime

class MainWindow:
    def start(self):
        runtime.create_amplifier_measurement()
"""
        tree = ast.parse(source)
        main_window = _main_window_class(tree)
        runtime_aliases = {
            alias
            for module, alias, _name in _imported_modules(tree)
            if module == "app.gui_runtime"
        }
        calls = [
            node
            for node in ast.walk(main_window)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in runtime_aliases
            and node.func.attr in FORBIDDEN_MEASUREMENT_CALLS
        ]
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
