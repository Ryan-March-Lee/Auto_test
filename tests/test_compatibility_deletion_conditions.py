"""步骤 16 门禁：剩余死代码已退役，发布清单与文档同步收口。

本文件取代此前按接口逐项冻结“兼容层删除条件”的旧门禁。兼容层已全部删除，因此这里只固定
删除后的现行状态（[技术债分阶段重构计划](../docs/技术债分阶段重构计划.md) **步骤 16：删除
剩余死代码和更新文档**）：

- 五个根目录纯再导出模块不存在、无字节码残留、在任何进程都无法导入；
- 生产可执行代码与测试都不再导入这些模块（静态导入与常量字符串动态导入一并检查）；
- 旧兼容接口标识不再以名字或字符串属性访问的形式出现在生产代码中；
- 发布清单不再登记 `rollback` / `compatibility` 文件列表；
- 正式替代入口可导入且导出预期符号，README、正式 API 清单与发布说明与实际目录结构一致。

历史执行记录（步骤 A 至步骤 E 的现场证据）保留在
[兼容层弃用与删除发布说明](../docs/兼容层弃用与删除发布说明.md)，但不作为运行时兼容机制：
门禁只约束可执行代码，注释、docstring 与历史文档中保留的旧名称属于审计证据。
"""

import ast
import importlib
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
RELEASE_NOTES = ROOT / "docs" / "兼容层弃用与删除发布说明.md"
INVENTORY_DOC = ROOT / "docs" / "正式API目录清单.md"
README = ROOT / "README.md"
GUIDE = ROOT / "docs" / "测试运行目录与配置结果解耦重构指南.md"
RETIREMENT_STEP = "步骤 16"

# 步骤 16 整文件退役的根目录纯再导出模块（相对路径，导入名同名）。
RETIRED_ROOT_MODULES = (
    "app_logging.py",
    "project_paths.py",
    "measurement_calculations.py",
    "measurement_services.py",
    "measurement_lifecycle.py",
)
RETIRED_MODULE_NAMES = tuple(Path(relative).stem for relative in RETIRED_ROOT_MODULES)

# 退役模块的正式继任者：模块名 -> 必须仍然可导入的符号。
FORMAL_SUCCESSORS = {
    "infrastructure.logging.app_logging": ("get_logger", "setup_logging"),
    "infrastructure.filesystem.paths": ("PathService", "resolve_path"),
    "domain.measurement.calculations": ("calculate_cable_losses",),
    "application.measurements.services": (
        "CableLossService",
        "DriverPowerMappingService",
        "AmplifierMeasurementService",
    ),
    "instrument.flow.cleanup": ("cleanup_measurement",),
}

# 已删除兼容接口的标识符：生产代码零引用，防止以别名、转发或字符串属性访问回归。
RETIRED_IDENTIFIERS = (
    "latest_path",
    "latest_compatibility_path",
    "LegacyResultInputReader",
    "LegacyResultQueryPort",
    "read_legacy_cable_loss",
    "read_latest_legacy_driver_mapping",
    "latest_legacy_driver_mapping_path",
    "save_compatibility_copy",
    "write_compatibility_copy",
    "create_legacy_run_snapshot",
    "write_legacy_run_snapshot",
    "legacy_path",
    "prepare_factory",
    "to_legacy_payload",
    "from_payload",
    "legacy_payload",
    "legacy_copy_path",
    "load_legacy_data",
    "unresolved_fields",
    "source_format",
    "conversion_errors",
)
# 按名字访问属性的内建函数：``getattr(module, "legacy_path")`` 与直接引用等价。
NAME_ACCESS_FUNCTIONS = frozenset({"getattr", "hasattr", "setattr", "delattr"})

ALL_EXCLUDED_PARTS = {".git", "__pycache__"}
PRODUCTION_EXCLUDED_PARTS = ALL_EXCLUDED_PARTS | {"tests"}


def _sources(excluded_parts):
    return sorted(
        path
        for path in ROOT.rglob("*.py")
        if not (set(path.relative_to(ROOT).parts) & excluded_parts)
    )


def _production_sources():
    return _sources(PRODUCTION_EXCLUDED_PARTS)


def _test_sources():
    return sorted((ROOT / "tests").rglob("*.py"))


def _relative(path):
    return path.relative_to(ROOT).as_posix()


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8-sig"), str(path))


def _imported_modules(tree):
    """绝对导入的模块名列表；相对导入不可能是根目录再导出模块。"""
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.append(node.module)
    return modules


def _imports_retired_module(module):
    return module.split(".")[0] in RETIRED_MODULE_NAMES


def _dynamic_import_sites(tree):
    """返回常量字符串的动态导入 [(模块名, 行号)]。

    覆盖 ``importlib.import_module``（含 ``from ... import`` 与别名）、``__import__``
    及 ``builtins`` 别名形式。参数不是字符串常量时不返回，避免把依赖探测误判为回归。
    """
    import_module_aliases = {"import_module"}
    import_aliases = {"__import__"}
    library_aliases = {"importlib", "builtins"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in ("importlib", "builtins"):
                    library_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            if node.module == "importlib":
                import_module_aliases.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "import_module"
                )
            elif node.module == "builtins":
                import_aliases.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "__import__"
                )
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        if isinstance(func, ast.Name):
            matches = func.id in import_module_aliases or func.id in import_aliases
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            matches = func.value.id in library_aliases and func.attr in (
                "import_module",
                "__import__",
            )
        else:
            matches = False
        argument = node.args[0]
        if matches and isinstance(argument, ast.Constant) and isinstance(
            argument.value, str
        ):
            sites.append((argument.value, node.lineno))
    return sites


def _retired_dynamic_imports(tree):
    return [
        (module, lineno)
        for module, lineno in _dynamic_import_sites(tree)
        if _imports_retired_module(module)
    ]


def _is_importable_bytecode(path):
    """是否为 Python 3 可直接导入的 sourceless 字节码。

    只有 ``<目录>/<模块>.pyc``（legacy 布局）在没有源码时仍可导入；
    ``__pycache__/<模块>.cpython-311.pyc`` 必须配合同名源码，源码删除后是死文件，
    不能据此判定回归（同名正式模块也可能存在，例如 ``infrastructure/logging``）。
    """
    return path.suffix == ".pyc" and path.parent.name != "__pycache__"


def _string_attribute_sites(tree):
    """返回按字符串名字访问属性的旧标识：[(标识, 行号)]。

    ``getattr(module, "legacy_path")`` 与 ``module.legacy_path`` 等价，必须同样拦截。
    """
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or len(node.args) < 2:
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name not in NAME_ACCESS_FUNCTIONS:
            continue
        target = node.args[1]
        if isinstance(target, ast.Constant) and target.value in RETIRED_IDENTIFIERS:
            sites.append((target.value, node.lineno))
    return sites


class RetiredRootModuleTests(unittest.TestCase):
    """五个根目录再导出模块必须保持删除状态，不能再被导入。"""

    def test_retired_root_files_do_not_exist(self):
        for relative in RETIRED_ROOT_MODULES:
            with self.subTest(module=relative):
                self.assertFalse((ROOT / relative).exists(), relative)

    def test_no_retired_module_sourceless_bytecode(self):
        """源码删除后不得留下 Python 3 可直接导入的 sourceless 字节码。"""
        residual = []
        for module in RETIRED_MODULE_NAMES:
            residual.extend(
                _relative(path)
                for path in ROOT.rglob(f"{module}.pyc")
                if ".git" not in path.relative_to(ROOT).parts
                and _is_importable_bytecode(path)
            )
        self.assertEqual(residual, [], "不得残留可导入的退役模块字节码: " + ", ".join(residual))

    def test_retired_modules_cannot_be_imported(self):
        """独立子进程验证，避免本进程 ``sys.modules`` 缓存掩盖回归。"""
        for module in RETIRED_MODULE_NAMES:
            with self.subTest(module=module):
                sys.modules.pop(module, None)
                completed = subprocess.run(
                    [sys.executable, "-c", f"import {module}"],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                self.assertNotEqual(
                    completed.returncode,
                    0,
                    f"{module} 仍可导入: {completed.stdout}{completed.stderr}",
                )
                self.assertIn("ModuleNotFoundError", completed.stderr)

    def test_production_code_does_not_import_retired_modules(self):
        offenders = [
            f"{_relative(path)} imports {module}"
            for path in _production_sources()
            for module in _imported_modules(_tree(path))
            if _imports_retired_module(module)
        ]
        self.assertEqual(
            offenders, [], "生产代码不得导入已退役模块: " + "; ".join(offenders)
        )

    def test_production_code_has_no_dynamic_import_of_retired_modules(self):
        offenders = [
            f"{_relative(path)}:{lineno} imports {module}"
            for path in _production_sources()
            for module, lineno in _retired_dynamic_imports(_tree(path))
        ]
        self.assertEqual(
            offenders,
            [],
            "生产代码不得用 import_module/__import__ 动态导入已退役模块: "
            + "; ".join(offenders),
        )

    def test_tests_do_not_import_retired_modules(self):
        offenders = [
            f"{_relative(path)} imports {module}"
            for path in _test_sources()
            for module in _imported_modules(_tree(path))
            if _imports_retired_module(module)
        ]
        offenders.extend(
            f"{_relative(path)}:{lineno} imports {module}"
            for path in _test_sources()
            for module, lineno in _retired_dynamic_imports(_tree(path))
        )
        self.assertEqual(
            offenders, [], "测试不得导入已退役模块: " + "; ".join(offenders)
        )

    def test_formal_successors_are_importable(self):
        """退役再导出模块不得连带删除正式入口：按模块契约校验可导入的符号。"""
        for module, names in FORMAL_SUCCESSORS.items():
            with self.subTest(module=module):
                imported = importlib.import_module(module)
                for name in names:
                    self.assertTrue(
                        hasattr(imported, name),
                        f"{module} 缺少正式入口 {name}",
                    )


class RetiredInterfaceIdentifierTests(unittest.TestCase):
    """旧兼容接口标识不得以任何形式回到生产代码。"""

    def test_production_code_has_no_retired_identifier(self):
        offenders = set()
        for path in _production_sources():
            for node in ast.walk(_tree(path)):
                if isinstance(node, ast.Name) and node.id in RETIRED_IDENTIFIERS:
                    offenders.add(f"{_relative(path)}:{node.lineno}:{node.id}")
                elif (
                    isinstance(node, ast.Attribute)
                    and node.attr in RETIRED_IDENTIFIERS
                ):
                    offenders.add(f"{_relative(path)}:{node.lineno}:{node.attr}")
        self.assertEqual(
            offenders,
            set(),
            "生产代码不得再出现已删除的兼容接口: " + ", ".join(sorted(offenders)),
        )

    def test_production_code_has_no_string_attribute_access_to_retired_identifiers(self):
        offenders = [
            f"{_relative(path)}:{lineno}:{name}"
            for path in _production_sources()
            for name, lineno in _string_attribute_sites(_tree(path))
        ]
        self.assertEqual(
            offenders,
            [],
            "生产代码不得用 getattr 等按字符串访问已删除接口: " + ", ".join(offenders),
        )

    def test_production_code_never_passes_the_removed_run_contract_switch(self):
        """``run_contract`` 只允许作为正式模块名存在，不得再作为调用开关。"""
        offenders = [
            f"{_relative(path)}:{node.lineno}"
            for path in _production_sources()
            for node in ast.walk(_tree(path))
            if isinstance(node, ast.Call)
            for keyword in node.keywords
            if keyword.arg == "run_contract"
        ]
        self.assertEqual(
            offenders, [], "生产代码不得再使用 run_contract 开关: " + ", ".join(offenders)
        )


class DetectorSelfTests(unittest.TestCase):
    """门禁辅助函数自身的回归：新增旧入口时检测器必须真的能发现。"""

    def test_static_and_dynamic_retired_imports_are_detected(self):
        tree = ast.parse(
            "import measurement_services\n"
            "from app_logging import get_logger\n"
            "import importlib as il\n"
            "from importlib import import_module as load\n"
            "il.import_module('project_paths')\n"
            "load('measurement_lifecycle')\n"
        )
        self.assertEqual(
            sorted(
                module
                for module in _imported_modules(tree)
                if _imports_retired_module(module)
            ),
            ["app_logging", "measurement_services"],
        )
        self.assertEqual(
            sorted(module for module, _lineno in _retired_dynamic_imports(tree)),
            ["measurement_lifecycle", "project_paths"],
        )

    def test_dynamic_names_are_not_treated_as_retired_imports(self):
        """依赖探测使用变量名，不是常量旧模块名，不得误判。"""
        tree = ast.parse(
            "import importlib\n"
            "def check_packages():\n"
            "    return importlib.import_module(name)\n"
        )
        self.assertEqual(_dynamic_import_sites(tree), [])
        self.assertEqual(_retired_dynamic_imports(tree), [])

    def test_string_attribute_access_is_detected(self):
        tree = ast.parse(
            "getattr(module, 'legacy_path')\n"
            "setattr(module, 'latest_path', None)\n"
            "getattr(module, 'get_logger')\n"
        )
        self.assertEqual(
            [name for name, _lineno in _string_attribute_sites(tree)],
            ["legacy_path", "latest_path"],
        )

    def test_comments_and_docstrings_are_not_treated_as_imports(self):
        """历史名称留在注释/docstring 中是审计证据，不是运行时引用。"""
        tree = ast.parse(
            '"""历史：``measurement_services`` 曾是根目录再导出模块。"""\n'
            "# from measurement_services import create_cable_loss_measurement\n"
            "VALUE = 1\n"
        )
        self.assertEqual(
            [module for module in _imported_modules(tree) if _imports_retired_module(module)],
            [],
        )
        self.assertEqual(_retired_dynamic_imports(tree), [])

    def test_retired_identifier_list_is_not_empty(self):
        self.assertTrue(RETIRED_IDENTIFIERS)
        self.assertTrue(set(RETIRED_MODULE_NAMES).isdisjoint(RETIRED_IDENTIFIERS))

    def test_only_sourceless_bytecode_counts_as_a_regression(self):
        """``__pycache__`` 残留在源码删除后不可导入，不得当作回归（同名正式模块也会命中）。"""
        self.assertTrue(_is_importable_bytecode(ROOT / "app_logging.pyc"))
        self.assertTrue(
            _is_importable_bytecode(ROOT / "application" / "app_logging.pyc")
        )
        self.assertFalse(
            _is_importable_bytecode(
                ROOT / "__pycache__" / "app_logging.cpython-311.pyc"
            )
        )
        self.assertFalse(
            _is_importable_bytecode(
                ROOT / "infrastructure" / "logging" / "__pycache__"
                / "app_logging.cpython-311.pyc"
            )
        )


class ReleaseManifestSurfaceTests(unittest.TestCase):
    """发布清单只登记正式发布边界，不再维护兼容/回滚文件列表。"""

    def test_release_manifest_has_no_compatibility_or_rollback_lists(self):
        import release_manifest

        self.assertFalse(hasattr(release_manifest, "ROLLBACK_FILES"))
        self.assertFalse(hasattr(release_manifest, "COMPATIBILITY_FILES"))
        manifest = release_manifest.build_manifest(ROOT)
        self.assertNotIn("compatibility_files", manifest)
        self.assertNotIn("rollback_files", manifest)

    def test_release_manifest_entries_exist_and_exclude_retired_modules(self):
        import release_manifest

        manifest = release_manifest.build_manifest(ROOT)
        for entry in manifest["release_files"]:
            with self.subTest(entry=entry):
                self.assertTrue((ROOT / entry).exists(), f"发布清单条目不存在: {entry}")
        for module in RETIRED_MODULE_NAMES:
            with self.subTest(module=module):
                self.assertNotIn(module, release_manifest.RELEASE_FILES)


class DocumentationConsistencyTests(unittest.TestCase):
    """文档以当前正式架构为准，不再把已删除的兼容模块描述为现行文件。"""

    def test_readme_file_tree_does_not_list_retired_modules(self):
        readme = README.read_text(encoding="utf-8")
        for module in RETIRED_MODULE_NAMES:
            for branch in ("├──", "└──"):
                with self.subTest(module=module, branch=branch):
                    self.assertNotIn(f"{branch} {module}", readme)
        self.assertNotIn("尚未收口的兼容再导出入口", readme)

    def test_api_inventory_marks_the_retired_modules_as_deleted(self):
        document = INVENTORY_DOC.read_text(encoding="utf-8-sig")
        for module in RETIRED_MODULE_NAMES:
            row = f"| `{module}` | `{module}.py` | 已删除 | {RETIREMENT_STEP} |"
            with self.subTest(module=module):
                self.assertIn(row, document)

    def test_release_notes_record_the_step_16_retirement(self):
        document = RELEASE_NOTES.read_text(encoding="utf-8")
        for marker in (
            "### 3.9 纯再导出模块（步骤 16 已退役）",
            "步骤 16 执行记录",
            "不再登记兼容/回滚文件列表",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, document)
        for module in RETIRED_MODULE_NAMES:
            with self.subTest(module=module):
                self.assertIn(module, document)

    def test_guide_records_step_16(self):
        guide = GUIDE.read_text(encoding="utf-8")
        self.assertIn("步骤 16 已整文件退役", guide)


if __name__ == "__main__":
    unittest.main()
