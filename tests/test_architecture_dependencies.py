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
    def test_domain_configuration_does_not_depend_on_infrastructure(self):
        forbidden = {"infrastructure", "json", "pathlib", "pyvisa", "PySide6", "PyQt5"}
        violations = []
        for source_path in (ROOT / "domain" / "configuration").rglob("*.py"):
            tree = _tree(source_path)
            for module, _alias, _name in _imported_modules(tree):
                root = module.split(".")[0]
                if root in forbidden:
                    violations.append(f"{source_path.relative_to(ROOT)} imports {module}")
        self.assertEqual(violations, [], "配置领域层不得依赖基础设施或文件格式: " + "; ".join(violations))

    def test_application_configuration_service_depends_on_port_not_json_repository(self):
        source_path = ROOT / "application" / "configuration_service.py"
        imports = _imported_modules(_tree(source_path))
        modules = {module for module, _alias, _name in imports}
        self.assertIn("application.ports.config_repository", modules)
        self.assertNotIn("infrastructure.config.json_config_repository", modules)

    def test_production_code_does_not_use_legacy_configuration_modules(self):
        forbidden = {
            "config_models",
            "config_io",
            "config_validation",
            "infrastructure.persistence.json_config_repository",
        }
        compatibility = {
            ROOT / "config_models.py",
            ROOT / "config_io.py",
            ROOT / "config_validation.py",
            ROOT / "infrastructure" / "persistence" / "json_config_repository.py",
        }
        violations = []
        for source_path in _production_sources():
            if source_path in compatibility:
                continue
            for module, _alias, _name in _imported_modules(_tree(source_path)):
                if module in forbidden:
                    violations.append(f"{source_path.relative_to(ROOT)} imports {module}")
        self.assertEqual(violations, [])

    def test_configuration_legacy_persistence_path_is_reexport_only(self):
        source_path = ROOT / "infrastructure" / "persistence" / "json_config_repository.py"
        definitions = {
            node.name
            for node in _tree(source_path).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        self.assertEqual(definitions, set())

    def test_configuration_compatibility_modules_are_explicit_reexports(self):
        modules = (
            ROOT / "app_logging.py",
            ROOT / "project_paths.py",
            ROOT / "config_io.py",
            ROOT / "config_models.py",
            ROOT / "config_validation.py",
            ROOT / "legacy_config_conversion.py",
        )
        for source_path in modules:
            tree = _tree(source_path)
            definitions = [
                node.name
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            ]
            self.assertEqual(definitions, [], str(source_path.relative_to(ROOT)))
            self.assertTrue(
                any(isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == "__all__"
                    for target in node.targets
                ) for node in tree.body),
                str(source_path.relative_to(ROOT)) + " 必须显式声明 __all__",
            )

    def test_configuration_compatibility_exports_match_canonical_objects(self):
        import app_logging
        import config_io
        import config_models
        import config_validation
        import infrastructure.config.json_io as canonical_io
        import infrastructure.logging.app_logging as canonical_logging
        import project_paths
        from domain.configuration import models as canonical_models
        from domain.configuration import rules as canonical_rules

        self.assertIs(app_logging.get_logger, canonical_logging.get_logger)
        self.assertIs(config_io.load_config_file, canonical_io.load_config_file)
        self.assertIs(project_paths.resolve_path, __import__(
            "infrastructure.filesystem.paths", fromlist=["resolve_path"]
        ).resolve_path)
        self.assertIs(config_models.TestPlan, canonical_models.TestPlan)
        self.assertIs(config_validation.validate_config, canonical_rules.validate_config)

    def test_persistence_implementations_have_one_infrastructure_boundary(self):
        legacy_config = _tree(ROOT / "persistence" / "config_repository.py")
        result_adapter = _tree(ROOT / "infrastructure" / "persistence" / "result_repository.py")
        legacy_results = _tree(ROOT / "result_storage.py")

        legacy_business_definitions = {
            node.name
            for node in legacy_config.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        self.assertEqual(legacy_business_definitions, set())
        self.assertNotIn(
            "result_storage",
            {
                module.split(".")[0]
                for module, _alias, _name in _imported_modules(result_adapter)
            },
        )
        legacy_result_definitions = {
            node.name
            for node in legacy_results.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        self.assertEqual(
            legacy_result_definitions,
            {
                "_canonical_path_scope",
                "load_json_result",
                "save_json_result",
                "create_run_directory",
                "validate_run_id",
                "new_run_id",
                "write_run_snapshot",
                "write_legacy_run_snapshot",
                "save_measurement_result",
                "save_measurement_model",
            },
        )
        production_legacy_imports = []
        for source_path in _production_sources():
            if source_path in {ROOT / "result_storage.py", ROOT / "persistence" / "config_repository.py"}:
                continue
            tree = _tree(source_path)
            if any(
                module == "result_storage" or module == "persistence.config_repository"
                for module, _alias, _name in _imported_modules(tree)
            ):
                production_legacy_imports.append(str(source_path.relative_to(ROOT)))
        self.assertEqual(production_legacy_imports, [])
        self.assertTrue((ROOT / "infrastructure" / "persistence" / "json_config_repository.py").is_file())
        self.assertTrue((ROOT / "infrastructure" / "persistence" / "json_result_repository.py").is_file())

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

    def test_application_inputs_do_not_import_infrastructure(self):
        violations = []
        for source_path in (ROOT / "application").rglob("*.py"):
            tree = _tree(source_path)
            for module, _alias, _name in _imported_modules(tree):
                if module == "infrastructure" or module.startswith("infrastructure."):
                    violations.append(f"{source_path.relative_to(ROOT)} imports {module}")
        self.assertEqual(violations, [], "应用层不得反向依赖基础设施: " + "; ".join(violations))

    def test_result_persistence_does_not_import_application_reader(self):
        source = ROOT / "infrastructure" / "persistence" / "json_result_repository.py"
        tree = _tree(source)
        modules = {module for module, _alias, _name in _imported_modules(tree)}
        self.assertNotIn("application.inputs.result_reading", modules)

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
