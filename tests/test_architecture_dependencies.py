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
COORDINATOR_SOURCE = ROOT / "presentation" / "qt" / "application_coordinator.py"
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
# 任务 6 已删除的历史兼容根入口；生产包与测试都不得再导入或重新引入。
DELETED_ROOT_MODULES = {
    "enhanced_main_gui",
    "legacy_config_conversion",
    "llm",
    "web_search_tools",
    "instrument_control",
    "data_visualization",
    "connection_diagrams",
    "persistence",
    # 技术债步骤 4 已整文件退役的旧测量入口（含主功放旧实现）。
    "cable_loss_measurement",
    "driver_power_mapping",
    "amplifier_measurement",
    "app.legacy_amplifier_measurement",
    # 技术债步骤 6 已退役的旧结果解析转发入口。
    "result_reading",
    # 技术债步骤 16 已退役的纯再导出兼容模块（日志、路径、计算、服务、生命周期）。
    "app_logging",
    "project_paths",
    "measurement_calculations",
    "measurement_services",
    "measurement_lifecycle",
}
FORBIDDEN_CONTROLLER_IMPORT_ROOTS = FORBIDDEN_PAGE_IMPORT_ROOTS | {
    "cable_loss_measurement",
    "driver_power_mapping",
    "amplifier_measurement",
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
    return _class_node(tree, "MainWindow")


def _class_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"{name} 未找到")


def _self_attr_writes(class_node):
    """返回类中 ``self.<attr> = ...`` 形式写入的属性名集合。"""
    writes = set()
    for node in ast.walk(class_node):
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
                ):
                    writes.add(child.attr)
    return writes


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


def _package_root_import_violations(packages, forbidden_roots):
    """Return production-package imports of forbidden legacy root modules."""
    violations = []
    for package in packages:
        for source_path in (ROOT / package).rglob("*.py"):
            for node in ast.walk(_tree(source_path)):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    modules = [node.module or ""]
                else:
                    # 相对导入始终停留在同一个正式包内，不是根入口转发。
                    continue
                for module in modules:
                    if module.split(".")[0] in forbidden_roots:
                        violations.append(f"{source_path.relative_to(ROOT)} imports {module}")
    return violations


class ProductionDependencyTests(unittest.TestCase):
    def test_production_code_uses_path_service_instead_of_global_path_constants(self):
        forbidden_names = {
            "PROJECT_ROOT", "TEST_RESULTS_DIR", "CONFIG_FILE", "CABLE_LOSS_FILE",
            "LOGS_DIR", "ASSETS_DIR", "ICONS_DIR", "IMAGES_DIR", "TEMP_DIR",
        }
        allowed = {
            ROOT / "infrastructure" / "filesystem" / "paths.py",
            ROOT / "infrastructure" / "config" / "json_io.py",
            ROOT / "infrastructure" / "persistence" / "json_result_repository.py",
            ROOT / "infrastructure" / "logging" / "app_logging.py",
            ROOT / "tests" / "test_project_paths.py",
            ROOT / "tests" / "test_config_io.py",
            ROOT / "tests" / "test_json_result_repository.py",
        }
        violations = []
        for source_path in _production_sources():
            if source_path in allowed:
                continue
            tree = _tree(source_path)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "infrastructure.filesystem.paths":
                    names = {alias.name for alias in node.names}
                    violations.extend(
                        f"{source_path.relative_to(ROOT)} imports {name}"
                        for name in names & forbidden_names
                    )
        self.assertEqual(violations, [])

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

    def test_persistence_implementations_have_one_infrastructure_boundary(self):
        result_adapter_path = ROOT / "infrastructure" / "persistence" / "result_repository.py"
        result_adapter = _tree(result_adapter_path)
        storage_source = (
            ROOT / "infrastructure" / "persistence" / "json_result_repository.py"
        ).read_text(encoding="utf-8-sig")
        history_path = ROOT / "infrastructure" / "persistence" / "result_history_repository.py"

        self.assertFalse(
            (ROOT / "result_storage.py").exists(),
            "步骤 3 已删除 result_storage 兼容转发模块，不得重新引入",
        )
        self.assertNotIn(
            "result_storage",
            {
                module.split(".")[0]
                for module, _alias, _name in _imported_modules(result_adapter)
            },
        )
        # 正式结果存储不得承载旧历史目录扫描；它只负责运行目录结果。
        for forbidden in (
            "history_result_files",
            "latest_history_result_file",
            "HISTORY_RESULT_TYPES",
            "FIXED_RESULT_FILENAMES",
            "TIMESTAMPED_RESULT_PREFIXES",
        ):
            self.assertNotIn(forbidden, storage_source)
        # 旧全局历史适配器已整文件退役，历史查看只读正式运行目录结果。
        self.assertFalse(
            history_path.exists(),
            "步骤 6 已删除旧的全局历史仓储，历史查看不得再扫描 test_results 根目录",
        )
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
        self.assertIn("build_run_history=self.create_run_history_tab", source)
        self.assertIn("self.tab_widget.addTab(page, definition.title)", source)

    def test_main_window_keeps_measurement_completion_refresh(self):
        source = MAIN_WINDOW_SOURCE.read_text(encoding="utf-8-sig")
        self.assertIn(
            "self.measurement_controller.signals.finished.connect(self.refresh_history)",
            source,
            "MeasurementController finished 必须刷新历史运行与历史结果列表",
        )
        self.assertIn(
            "else page.refresh()",
            source,
            "完成信号必须刷新页面两份列表，而不是只刷新结果表",
        )

    def test_main_window_separates_instrument_worker_callbacks(self):
        source = MAIN_WINDOW_SOURCE.read_text(encoding="utf-8-sig")
        self.assertIn(
            "worker.signals.error.connect(self.on_instrument_error)",
            source,
        )
        self.assertIn(
            "worker.signals.stopped.connect(self.on_instrument_stopped)",
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

    def test_analysis_layer_does_not_import_infrastructure(self):
        violations = []
        for source_path in (ROOT / "analysis").rglob("*.py"):
            tree = _tree(source_path)
            for module, _alias, _name in _imported_modules(tree):
                if module == "infrastructure" or module.startswith("infrastructure."):
                    violations.append(f"{source_path.relative_to(ROOT)} imports {module}")
        self.assertEqual(violations, [], "分析层不得依赖基础设施: " + "; ".join(violations))

    def test_deleted_root_modules_are_absent_and_unimported(self):
        for module in DELETED_ROOT_MODULES:
            module_path = ROOT / (module.replace(".", "/") + ".py")
            self.assertFalse(
                module_path.exists(), f"{module} 已删除，不应再存在源文件"
            )
            self.assertFalse(
                (ROOT / module / "__init__.py").exists(),
                f"{module} 包已删除，不应再存在",
            )

        packages = ("app", "application", "presentation", "infrastructure", "instrument")
        violations = _package_root_import_violations(packages, DELETED_ROOT_MODULES)
        self.assertEqual(
            violations,
            [],
            "生产包不得导入已删除的历史入口: " + "; ".join(violations),
        )

        test_violations = []
        for source_path in (ROOT / "tests").rglob("*.py"):
            for module, _alias, _name in _imported_modules(_tree(source_path)):
                if module.split(".")[0] in DELETED_ROOT_MODULES:
                    test_violations.append(
                        f"{source_path.relative_to(ROOT)} imports {module}"
                    )
        self.assertEqual(
            test_violations,
            [],
            "测试不得导入已删除的历史入口: " + "; ".join(test_violations),
        )

    def test_gui_data_pages_do_not_access_storage_implementations(self):
        sources = (
            ROOT / "presentation" / "qt" / "config_page.py",
            ROOT / "presentation" / "qt" / "config_projection.py",
            ROOT / "presentation" / "qt" / "history_view_model.py",
            ROOT / "presentation" / "qt" / "visualization_page.py",
            ROOT / "presentation" / "qt" / "run_history_page.py",
        )
        forbidden_imports = ("infrastructure", "result_storage", "json_result_repository")
        forbidden_calls = {"open", "glob", "stat", "unlink", "copy2", "mkdir"}
        violations = []
        for source in sources:
            tree = _tree(source)
            for module, _alias, _name in _imported_modules(tree):
                if any(module == root or module.startswith(root + ".") for root in forbidden_imports):
                    violations.append(f"{source.relative_to(ROOT)} imports {module}")
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if node.func.attr in forbidden_calls:
                        violations.append(f"{source.relative_to(ROOT)} calls {node.func.attr}")
        self.assertEqual(violations, [], "; ".join(violations))

    def test_gui_history_browsing_depends_only_on_the_read_only_history_port(self):
        source_path = ROOT / "infrastructure" / "gui_data_service.py"
        source = source_path.read_text(encoding="utf-8-sig")
        modules = {module for module, _alias, _name in _imported_modules(_tree(source_path))}
        self.assertNotIn(
            "infrastructure.persistence.result_repository",
            modules,
            "历史查看/导出不得复用正式测量结果仓储",
        )
        self.assertIn("application.ports.run_history", modules)
        self.assertNotIn(
            "application.ports.result_history",
            modules,
            "旧全局历史端口已退役，历史查看只读运行目录结果",
        )
        self.assertNotIn("latest_path(", source)
        self.assertNotIn("FileMeasurementResultRepository", source)

    def test_gui_composition_does_not_inject_measurement_result_repository(self):
        source_path = ROOT / "presentation" / "qt" / "composition.py"
        source = source_path.read_text(encoding="utf-8-sig")
        modules = {module for module, _alias, _name in _imported_modules(_tree(source_path))}
        self.assertNotIn("infrastructure.persistence.result_repository", modules)
        self.assertIn("FileRunHistoryRepository", source)
        self.assertNotIn("FileResultHistoryRepository", source)

    def test_gui_history_browsing_uses_the_run_history_port(self):
        """步骤 D3：历史浏览以运行目录历史为首选来源，仍只依赖只读端口。"""
        source_path = ROOT / "infrastructure" / "gui_data_service.py"
        modules = {module for module, _alias, _name in _imported_modules(_tree(source_path))}
        self.assertIn("application.ports.run_history", modules)
        self.assertNotIn("infrastructure.persistence.run_repository", modules)
        composition = (ROOT / "presentation" / "qt" / "composition.py").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("FileRunHistoryRepository", composition)

    def test_formal_input_reader_has_no_implicit_result_lookup(self):
        source = (
            ROOT / "application" / "inputs" / "result_input_reader.py"
        ).read_text(encoding="utf-8-sig")
        self.assertNotIn("latest_path", source)
        self.assertNotIn("legacy_path", source)
        port = (
            ROOT / "application" / "ports" / "result_input_reader.py"
        ).read_text(encoding="utf-8-sig")
        self.assertNotIn(
            "path: PathLike | None",
            port,
            "正式输入端口不得声明可省略的路径参数",
        )

    def test_history_port_exposes_result_ids_instead_of_paths(self):
        source = (
            ROOT / "application" / "ports" / "run_history.py"
        ).read_text(encoding="utf-8-sig")
        self.assertNotIn("-> Path", source)
        self.assertNotIn("PathLike", source)
        self.assertIn("result_id: str", source)
        self.assertFalse(
            (ROOT / "application" / "ports" / "result_history.py").exists(),
            "旧全局历史端口已在步骤 6 退役",
        )

    def test_gui_result_dto_does_not_expose_paths(self):
        source = ROOT / "application" / "ports" / "gui_data_service.py"
        tree = _tree(source)
        annotated = {
            node.target.id
            for node in ast.walk(tree)
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        }
        self.assertNotIn("path", annotated)
        self.assertNotIn("file_path", annotated)

    def test_main_window_does_not_assemble_storage_implementations(self):
        tree = _tree(MAIN_WINDOW_SOURCE)
        modules = {module for module, _alias, _name in _imported_modules(tree)}
        self.assertFalse(any(module.startswith("infrastructure.persistence") for module in modules))
        self.assertFalse(any(module.startswith("infrastructure.config") for module in modules))

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

    def test_run_coordinator_talks_to_the_config_page_through_a_narrow_port(self):
        """步骤 10：协调器不读控件，只依赖配置页的五个窄接口。"""
        source = COORDINATOR_SOURCE.read_text(encoding="utf-8-sig")
        tree = _tree(COORDINATOR_SOURCE)
        for widget in (
            "driver_power_source_combo",
            "freq_edit",
            "sg_address",
            "power_config_widgets",
            "power_assignment_widgets",
            "widget_host",
        ):
            self.assertNotIn(
                widget,
                source,
                f"协调器不得引用控件 {widget}；配置页应通过 ConfigurationPagePort 交互",
            )

        protocol = _class_node(tree, "ConfigurationPagePort")
        members = {
            node.name for node in protocol.body if isinstance(node, ast.FunctionDef)
        }
        self.assertEqual(
            members,
            {"validate", "build_config", "apply_config", "set_run_status", "driver_mode_requested"},
            "配置页端口能力集合变化时必须同步更新协调器与页面，而不是放开断言",
        )
        page = _class_node(_tree(ROOT / "presentation" / "qt" / "config_page.py"), "ConfigPage")
        page_methods = {
            node.name for node in page.body if isinstance(node, ast.FunctionDef)
        }
        self.assertEqual(
            members - page_methods,
            set(),
            "ConfigPage 必须实现配置页端口的全部能力",
        )

    def test_run_coordinator_requires_explicit_user_feedback_callbacks(self):
        """失败路径不允许静默：日志/告警/错误回调必须显式注入。"""
        coordinator = _class_node(_tree(COORDINATOR_SOURCE), "ApplicationCoordinator")
        init = next(
            node
            for node in coordinator.body
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
        )
        defaults = dict(zip((arg.arg for arg in init.args.kwonlyargs), init.args.kw_defaults))
        for name in ("log", "warn", "error"):
            self.assertIn(name, defaults, f"协调器缺少 {name} 回调参数")
            self.assertIsNone(
                defaults[name],
                f"{name} 必须由调用方注入，默认静默会让失败路径丢消息",
            )

    def test_main_window_measurement_state_writes_stay_within_legacy_baseline(self):
        writes = _main_window_measurement_writes(_tree(MAIN_WINDOW_SOURCE))
        added_writes = writes - LEGACY_MAIN_WINDOW_MEASUREMENT_WRITES
        self.assertEqual(
            added_writes,
            set(),
            "MainWindow 新增测量业务状态写入: " + ", ".join(sorted(added_writes)),
        )

    def test_run_coordinator_owns_configuration_run_contract_and_instrument_port(self):
        """步骤 10：配置、运行契约和端口只有协调器一个所有者。"""
        coordinator = _class_node(_tree(COORDINATOR_SOURCE), "ApplicationCoordinator")
        owned = {"config", "prepared_run", "instrument_ctrl", "instrument_worker"}
        self.assertTrue(
            owned <= _self_attr_writes(coordinator),
            "ApplicationCoordinator 必须持有配置、运行契约和端口: "
            + ", ".join(sorted(owned - _self_attr_writes(coordinator))),
        )
        window_writes = _self_attr_writes(_main_window_class(_tree(MAIN_WINDOW_SOURCE)))
        self.assertEqual(
            window_writes & owned,
            set(),
            "MainWindow 不得重新持有配置/运行/端口状态: "
            + ", ".join(sorted(window_writes & owned)),
        )

    def test_main_window_cannot_create_runs_without_the_coordinator(self):
        tree = _tree(MAIN_WINDOW_SOURCE)
        modules = {module for module, _alias, _name in _imported_modules(tree)}
        self.assertNotIn("app.gui_runtime", modules)
        self.assertIn("presentation.qt.application_coordinator", modules)
        # 运行创建/加载只允许出现在协调器。
        for source_path in (MAIN_WINDOW_SOURCE, COORDINATOR_SOURCE):
            calls = {
                _called_name(node.func)
                for node in ast.walk(_tree(source_path))
                if isinstance(node, ast.Call)
            }
            expected = {"prepare_configuration", "load_prepared_run"}
            if source_path is COORDINATOR_SOURCE:
                self.assertTrue(
                    expected <= calls,
                    "协调器必须承担创建/加载运行: " + ", ".join(sorted(expected - calls)),
                )
            else:
                self.assertEqual(
                    calls & expected,
                    set(),
                    "主窗口不得直接创建或加载运行: " + ", ".join(sorted(calls & expected)),
                )

    def test_main_window_does_not_construct_instrument_workers(self):
        tree = _tree(MAIN_WINDOW_SOURCE)
        self.assertEqual(
            _worker_construction_violations(tree),
            [],
            "仪器连接 worker 的构造属于协调器，主窗口只连接信号",
        )
        coordinator_tree = _tree(COORDINATOR_SOURCE)
        self.assertIn(
            "InstrumentWorker",
            {
                alias.asname or alias.name
                for node in ast.walk(coordinator_tree)
                if isinstance(node, ast.ImportFrom)
                and (node.module or "").split(".")[-1] == "workers"
                for alias in node.names
            },
        )

    def test_history_pages_use_the_read_only_view_model(self):
        """步骤 11：历史列表/选中/已加载状态只在只读视图模型里。"""
        view_model_source = (
            ROOT / "presentation" / "qt" / "history_view_model.py"
        ).read_text(encoding="utf-8-sig")
        self.assertNotIn("PySide6", view_model_source)
        self.assertNotIn("infrastructure", view_model_source)

        cached_state = (
            "self._row_files =",
            "self.loaded_series =",
            "self.loaded_result =",
            "self.current_freq_index =",
            "self.frequency_list =",
        )
        for name in ("run_history_page.py", "visualization_page.py"):
            source = (ROOT / "presentation" / "qt" / name).read_text(
                encoding="utf-8-sig"
            )
            with self.subTest(module=name):
                self.assertIn("HistoryViewModel", source)
                for attribute in cached_state:
                    self.assertNotIn(
                        attribute,
                        source,
                        f"{name} 不得自己缓存历史列表/频率状态（归 HistoryViewModel）",
                    )
                self.assertNotIn("legacy_payload", source)

    def test_run_history_page_only_builds_export_requests(self):
        """步骤 11：导出由 application 用例执行，页面只描述意图并生成文案。"""
        source = (ROOT / "presentation" / "qt" / "run_history_page.py").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("ExportRequest(", source)
        self.assertIn("ExportFormat", source)
        self.assertIn("self.export_service.execute(", source)
        self.assertIn("ResultExportServicePort", source)
        self.assertIn("_success_message", source)
        for direct_port_call in (
            "self.result_service",
            ".export_json(",
            ".export_csv(",
            ".export_report(",
            "export_json(source",
        ):
            self.assertNotIn(
                direct_port_call,
                source,
                "导出页面不得直接调用导出端口；必须构造 ExportRequest",
            )

    def test_visualization_page_only_requests_reports_through_the_export_use_case(self):
        """步骤 8：报告只有一个应用层实现，可视化页不再自己生成报告。"""
        source = (ROOT / "presentation" / "qt" / "visualization_page.py").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("ExportRequest(", source)
        self.assertIn("ExportFormat.REPORT", source)
        self.assertIn("self.export_service.execute(", source)
        self.assertIn("ResultExportServicePort", source)
        self.assertIn("loaded.result_id", source)
        # “最新结果”与页面级报告服务都是步骤 8 删除的重复入口，不得重新引入。
        for removed_entry in (
            "latest_result",
            "load_latest",
            "report_service",
            "create_summary_report",
            "build_report_result",
        ):
            self.assertNotIn(
                removed_entry,
                source,
                "可视化页不得自己决定报告来源或复制报告生成规则",
            )

    def test_report_copies_are_rendered_from_the_archived_result_only(self):
        """步骤 8：报告副本只按归档结果契约渲染，不再有第二套报告适配器。"""
        source = (ROOT / "infrastructure" / "gui_data_service.py").read_text(
            encoding="utf-8-sig"
        )
        adapter = source[source.index("class ResultExportAdapter") :]
        # 报告输入由应用层构造：配置快照、测量时间、来源文件名都来自归档结果本身。
        self.assertIn("build_report_result(", adapter)
        self.assertNotIn("datetime.now(", adapter)
        self.assertNotIn("configuration=", adapter)
        # 页面级报告适配器与门面槽位已删除，不得重新装配。
        self.assertNotIn("class ReportAdapter", source)
        self.assertNotIn(
            "ReportPort",
            (ROOT / "application" / "ports" / "gui_data_service.py").read_text(
                encoding="utf-8-sig"
            ),
            "门面不得重新提供独立的报告端口",
        )
        composition = (ROOT / "presentation" / "qt" / "composition.py").read_text(
            encoding="utf-8-sig"
        )
        self.assertNotIn("ReportAdapter", composition)
        self.assertNotIn("reports=", composition)

    def test_export_use_case_lives_in_the_application_layer(self):
        source_path = ROOT / "application" / "export_service.py"
        self.assertTrue(source_path.is_file(), "缺少 application 导出用例")
        modules = {module for module, _alias, _name in _imported_modules(_tree(source_path))}
        roots = {module.split(".")[0] for module in modules}
        self.assertNotIn("infrastructure", roots)
        self.assertNotIn("PySide6", roots)
        self.assertIn("application.ports.gui_data_service", modules)
        self.assertIn("application.dto.export_requests", modules)

    def test_configuration_page_delegates_backfill_to_the_projection_module(self):
        """步骤 11：配置页只编辑配置编写文档，回填规则在 config_projection。"""
        page_source = (ROOT / "presentation" / "qt" / "config_page.py").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("project_authoring_config(self, config)", page_source)
        tree = _tree(ROOT / "presentation" / "qt" / "config_page.py")
        page = _class_node(tree, "ConfigPage")
        page_methods = {
            node.name for node in page.body if isinstance(node, ast.FunctionDef)
        }
        for helper in (
            "_reset_control_widgets",
            "_apply_instrument_widgets",
            "_apply_test_parameter_widgets",
            "_apply_power_supply_widgets",
            "_apply_channel_widgets",
            "_apply_power_assignment_widgets",
            "_text_or_empty",
            "_bool_or_default",
            "_number_or_none",
            "_set_combo",
        ):
            self.assertNotIn(
                helper,
                page_methods,
                f"逐字段回填规则必须留在 config_projection，不得回到 {helper}",
            )

    def test_production_sources_have_no_placeholder_text(self):
        """源码注释/文档不得以占位问号代替说明（回填被截断时能立刻发现）。"""
        offenders = []
        for source_path in _production_sources():
            if "????" in source_path.read_text(encoding="utf-8-sig"):
                offenders.append(str(source_path.relative_to(ROOT)))
        self.assertEqual(
            offenders,
            [],
            "生产源码出现占位问号文本，说明注释或文档被截断: " + ", ".join(offenders),
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


# 技术债步骤 13 的单位边界：dBm↔W、%↔比值的换算与线缆字段名都归测量值对象所有。
MEASUREMENT_VALUE_MODULE = ROOT / "domain" / "measurement" / "values.py"
CABLE_FIELD_NAMES = frozenset(
    {"cable1", "cable2", "cable3", "cable4", "total_path1", "total_path2"}
)


def _main_system_sources():
    """主系统的生产源码；DPD 子系统依赖与代码按步骤 14 隔离，不参与主系统单位门禁。"""
    return [
        path
        for path in _production_sources()
        if path.relative_to(ROOT).parts[0] != "DPD_auto_test"
    ]


class MeasurementValueBoundaryTests(unittest.TestCase):
    """单位换算与线缆字段名只能出现在测量值对象里。"""

    def test_power_conversions_live_only_in_the_measurement_value_objects(self):
        offenders = []
        for path in _main_system_sources():
            if path == MEASUREMENT_VALUE_MODULE:
                continue
            for node in ast.walk(_tree(path)):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "log10"
                ):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
                if (
                    isinstance(node, ast.BinOp)
                    and isinstance(node.op, ast.Pow)
                    and isinstance(node.left, ast.Constant)
                    and node.left.value == 10
                ):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(
            offenders,
            [],
            "dBm↔W 换算只能出现在 domain/measurement/values.py: "
            + ", ".join(offenders),
        )

    def test_cable_field_names_are_confined_to_the_value_objects(self):
        offenders = []
        for path in _main_system_sources():
            if path == MEASUREMENT_VALUE_MODULE:
                continue
            for node in ast.walk(_tree(path)):
                if isinstance(node, ast.Constant) and node.value in CABLE_FIELD_NAMES:
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(
            offenders,
            [],
            "cable1 等线缆字段名只能出现在 domain/measurement/values.py: "
            + ", ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
