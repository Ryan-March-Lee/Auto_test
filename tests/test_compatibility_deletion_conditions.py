"""步骤 E 门禁：兼容层删除条件与生产调用方清单证据。

本文件把 [兼容层弃用与删除发布说明](../docs/兼容层弃用与删除发布说明.md) 记录的
证据固化为可执行断言：

- 每个待删除接口在生产代码中的调用文件集合必须与该说明一致；新增调用方会失败。
  清单包含仍在使用的正式流程和旧兼容调用，不等同于“零生产引用”。
- 步骤 A 已删除的旧 GUI 测量入口（``prepare_configuration`` 的兼容快照开关、
  worker 的 ``prepare_factory`` 后备、``ResultQueryAdapter.latest()`` 与
  ``LegacyResultQueryPort``）必须保持消失状态，不允许以任何形式回归。
- 步骤 B1/B2 已删除 ``DriverPowerMappingUseCase`` / ``AmplifierMeasurementUseCase``
  的无路径兼容输入分支，两个用例不得再引用 ``LegacyResultInputReader`` 系列读取，
  且组装层必须在连接仪器前拒绝旧快照缺少显式输入。
- 步骤 B3 已删除兼容读取器与 ``latest_path()`` 兼容链；该纯代码删除按说明 6.6 的
  分级授权执行。
- 步骤 3（技术债分阶段重构计划，2026-10-26）已删除兼容副本/旧运行快照写入接口；
  三个旧测量根入口停止写兼容副本，只写运行目录结果，文件本体留待步骤 4 退役。
- 步骤 4（技术债分阶段重构计划，2026-10-26）已整文件退役三个旧测量根入口及其公开
  转发：根目录与 ``app/`` 中不再存在旧测量入口，旧默认运行 ID、隐式配置路径与隐式
  结果输入一并删除。
- 步骤 5（技术债分阶段重构计划，2026-10-26）已删除旧配置自动转换与根目录配置转发：
  ``config_io.py`` / ``config_models.py`` / ``config_validation.py`` 与
  ``infrastructure/config/legacy_conversion.py`` 退役，``load_legacy_data()`` /
  ``unresolved_fields`` 流程不再存在；配置页面保存时直接写出正式 schema。
- 步骤 D 已把 GUI 历史列表/查看/导出迁移到运行目录 manifest 视图，旧的全局兼容副本
  保持只读可查看；运行历史只读端口与仓储必须保持存在，且历史浏览不得复用正式测量
  结果仓储。
- 步骤 E1 已完成旧兼容测量根入口盘点：``release_manifest`` 的 ``rollback_files`` /
  ``compatibility_files`` 并集必须与盘点表**双向相等**，正式流程不得导入这些旧入口。
- 旧测量根入口（``cable_loss_measurement.py`` / ``driver_power_mapping.py`` /
  ``app/legacy_amplifier_measurement.py`` 与转发 ``amplifier_measurement.py``）已在
  步骤 4 退役：文件、类名、导入路径都不得重新出现。
- 步骤 E5 是门禁复核（更新测试/说明、跑全量离线测试与应用检查），非删除动作，本轮执行。
- 说明必须为每个待删除接口写出删除条件、迁移方式和目标版本，并保留步骤 A 执行
  记录（外部调用方台账与回滚版本）。

旧兼容/回滚入口的**再导出**模块（``measurement_services.py`` 等）仍按说明 3.5 节
保留，因此这里只校验调用方集合与盘点一致性，不要求它们消失；三个旧测量根入口已
在步骤 4 删除，改由“文件不存在且无法导入”的门禁固定。
"""

import ast
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
EXCLUDED_PARTS = {".git", "__pycache__", "tests"}
ALL_EXCLUDED_PARTS = {".git", "__pycache__"}
RELEASE_NOTES = ROOT / "docs" / "兼容层弃用与删除发布说明.md"
GUIDE = ROOT / "docs" / "测试运行目录与配置结果解耦重构指南.md"

# 发布说明 3.1 至 3.4 记录的“允许出现的生产调用文件”。
# 任何新增条目都必须先更新发布说明，再更新这里。
DEPRECATED_INTERFACE_CALLERS = {
    # 3.1 按时间扫描全局结果的兼容入口：步骤 B3 删除后生产代码为零引用。
    "latest_path": set(),
    "latest_compatibility_path": set(),
    # 3.2 无路径兼容读取：步骤 B1/B2/B3 删除后生产代码为零引用。
    "read_legacy_cable_loss": set(),
    "read_latest_legacy_driver_mapping": set(),
    "latest_legacy_driver_mapping_path": set(),
    # 3.3 旧结果写入
    # 技术债分阶段重构计划步骤 2（2026-10-26）已删除正式用例与仓储 save() 的兼容副本
    # 写入；步骤 3（2026-10-26）删除接口本身，生产代码零引用。
    "save_compatibility_copy": set(),
    "write_compatibility_copy": set(),
    "create_legacy_run_snapshot": set(),
    "write_legacy_run_snapshot": set(),
    "legacy_path": set(),
    # 3.4 旧 GUI 测量入口的后备工厂：步骤 A1 删除后生产代码中必须彻底消失
    "prepare_factory": set(),
}

REQUIRED_NOTE_MARKERS = (
    "latest_path()",
    "latest_compatibility_path()",
    "LegacyResultInputReader",
    "save_compatibility_copy()",
    "write_legacy_run_snapshot()",
    "create_legacy_run_snapshot()",
    "prepare_configuration(..., run_contract=False)",
    "prepare_factory",
    "ResultQueryAdapter.latest()",
)

REQUIRED_NOTE_SECTIONS = (
    "不兼容变更",
    "删除条件",
    "迁移方式",
    "回滚与查看",
    "目标版本",
    "继续保留的接口与原因",
)

REPOSITORY_SAVE_CALLERS = {
    "application/measurements/amplifier_test.py",
    "application/measurements/cable_loss.py",
    "application/measurements/driver_mapping.py",
}

# 发布说明 3.5 / 第 10 节盘点表：三类旧测量根入口（曾真实写旧运行快照/兼容副本）。
# 步骤 4 已整文件退役，因此这里只作为“必须不存在”的名单保留。
RETIRED_LEGACY_MEASUREMENT_ENTRIES = (
    "cable_loss_measurement.py",
    "driver_power_mapping.py",
    "app/legacy_amplifier_measurement.py",
    "amplifier_measurement.py",
)

# 已退役旧入口的导入名（用于断言任何位置都无法再导入）。
RETIRED_LEGACY_MODULE_NAMES = (
    "cable_loss_measurement",
    "driver_power_mapping",
    "amplifier_measurement",
    "app.legacy_amplifier_measurement",
)

# 旧入口的默认运行 ID。用拼接构造，避免本文件自身被“源码不得出现该字面量”的门禁命中。
RETIRED_LEGACY_RUN_ID = "run" + "-" + "legacy"

# 配置/结果/日志/服务/生命周期等纯再导出模块（纯代码路径，独立退役，不受步骤 F 阻断）。
# 步骤 5 已退役三个配置再导出模块；这里只保留仍未退役的条目。
LEGACY_REEXPORT_MODULES = (
    "measurement_services.py",
    "measurement_lifecycle.py",
    "measurement_calculations.py",
    "result_reading.py",
    "app_logging.py",
    "project_paths.py",
)

# 步骤 E1 盘点的**剩余**兼容面 = release_manifest.ROLLBACK_FILES ∪ COMPATIBILITY_FILES。
LEGACY_AUDITED_SURFACE = LEGACY_REEXPORT_MODULES

# 上述模块的导入名（用于断言正式流程不导入旧入口）。
LEGACY_MODULE_NAMES = frozenset(
    {
        "cable_loss_measurement",
        "driver_power_mapping",
        "amplifier_measurement",
        "app.legacy_amplifier_measurement",
        "measurement_services",
        "measurement_lifecycle",
        "measurement_calculations",
        "result_reading",
        "app_logging",
        "project_paths",
    }
)


def _production_sources():
    """返回 tests 之外的全部项目 Python 源文件。"""
    return sorted(
        path
        for path in ROOT.rglob("*.py")
        if not (set(path.relative_to(ROOT).parts) & EXCLUDED_PARTS)
    )


def _project_sources():
    """返回仓库内全部 Python 源文件（含 tests），排除 VCS 与字节码缓存。"""
    return sorted(
        path
        for path in ROOT.rglob("*.py")
        if not (set(path.relative_to(ROOT).parts) & ALL_EXCLUDED_PARTS)
    )


def _relative(path):
    return path.relative_to(ROOT).as_posix()


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8-sig"), str(path))


def _called_name(node):
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _reference_files(name):
    """生产代码中引用该标识符（属性访问或名字引用）的文件集合。"""
    found = set()
    for path in _production_sources():
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Name) and node.id == name:
                found.add(_relative(path))
            elif isinstance(node, ast.Attribute) and node.attr == name:
                found.add(_relative(path))
    return found


def _calls(name):
    """返回生产代码中该函数的调用点：(文件, 关键字参数值字典) 列表。"""
    sites = []
    for path in _production_sources():
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Call) and _called_name(node.func) == name:
                keywords = {
                    keyword.arg: ast.unparse(keyword.value)
                    for keyword in node.keywords
                    if keyword.arg is not None
                }
                sites.append((_relative(path), keywords))
    return sites


class DeprecatedInterfaceCallerTests(unittest.TestCase):
    """生产调用方清单证据：调用集合必须与发布说明记录完全一致。"""

    def test_production_callers_match_the_published_evidence(self):
        for interface, expected in DEPRECATED_INTERFACE_CALLERS.items():
            with self.subTest(interface=interface):
                self.assertEqual(
                    _reference_files(interface),
                    expected,
                    f"{interface} 的生产调用方与发布说明不一致："
                    "新增调用方必须先把迁移方案写进发布说明",
                )

    def test_repository_save_callers_match_the_result_write_inventory(self):
        """兼容副本写入已删除：save() 只用于运行目录结果写入。"""
        callers = set()
        for path in _production_sources():
            for node in ast.walk(_tree(path)):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "save"
                    and {keyword.arg for keyword in node.keywords}
                    >= {"result_type", "run_id", "run_directory"}
                ):
                    callers.add(_relative(path))
        self.assertEqual(
            callers, REPOSITORY_SAVE_CALLERS
        )

    def test_deprecated_gui_query_alias_is_removed(self):
        """``ResultQueryAdapter.latest()`` 已在步骤 A3 删除，生产代码不得再调用。"""
        implementation = (
            ROOT / "infrastructure" / "gui_data_service.py"
        ).read_text(encoding="utf-8-sig")
        self.assertNotIn("def latest(self", implementation)

        callers = {
            _relative(path)
            for path in _production_sources()
            for node in ast.walk(_tree(path))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "latest"
        }
        self.assertEqual(callers, set())

    def test_deprecated_query_port_is_removed(self):
        """``LegacyResultQueryPort`` 已在步骤 A3 删除，任何位置都不应再出现。"""
        mentioners = set()
        for path in _production_sources():
            for node in ast.walk(_tree(path)):
                if isinstance(node, ast.ImportFrom) and any(
                    alias.name == "LegacyResultQueryPort" for alias in node.names
                ):
                    mentioners.add(_relative(path))
                elif isinstance(node, ast.Name) and node.id == "LegacyResultQueryPort":
                    mentioners.add(_relative(path))
                elif (
                    isinstance(node, ast.ClassDef)
                    and node.name == "LegacyResultQueryPort"
                ):
                    mentioners.add(_relative(path))
        self.assertEqual(mentioners, set())


class LegacyGuiEntryTests(unittest.TestCase):
    """旧 GUI 测量入口在生产代码中必须已删除且不再回归。"""

    def test_production_code_never_requests_the_compatibility_snapshot(self):
        offenders = []
        for path in _production_sources():
            for node in ast.walk(_tree(path)):
                if not isinstance(node, ast.Call):
                    continue
                for keyword in node.keywords:
                    if keyword.arg == "run_contract":
                        offenders.append(f"{_relative(path)}:{node.lineno}")
        self.assertEqual(
            offenders, [], "生产代码不得再使用 run_contract 开关: " + ", ".join(offenders)
        )

    def test_prepare_configuration_has_no_compatibility_snapshot_switch(self):
        """``prepare_configuration`` 只剩“创建运行”语义，参数表中不得有旧开关。"""
        runtime = ROOT / "app" / "gui_runtime.py"
        signature = None
        for node in ast.walk(_tree(runtime)):
            if isinstance(node, ast.FunctionDef) and node.name == "prepare_configuration":
                signature = node
                break
        self.assertIsNotNone(signature, "缺少 prepare_configuration 定义")
        parameters = {
            argument.arg
            for argument in (
                list(signature.args.args)
                + list(signature.args.kwonlyargs)
            )
        }
        self.assertNotIn("run_contract", parameters)
        self.assertNotIn("operation", parameters)

    def test_prepare_configuration_is_only_called_from_the_gui_entry(self):
        sites = _calls("prepare_configuration")
        self.assertEqual([site[0] for site in sites], ["presentation/qt/main_window.py"])
        self.assertEqual(
            sites[0][1], {"paths": "self.paths", "hardware_config": "self.config"}
        )

    def test_production_code_never_injects_the_legacy_prepare_factory(self):
        offenders = [
            f"{relative}:{node.lineno}"
            for path in _production_sources()
            for node in ast.walk(_tree(path))
            if isinstance(node, ast.Call)
            for relative in [_relative(path)]
            if any(keyword.arg == "prepare_factory" for keyword in node.keywords)
        ]
        self.assertEqual(
            offenders, [], "生产代码不得注入旧 prepare_factory: " + ", ".join(offenders)
        )

    def test_workers_keep_no_prepare_factory_fallback(self):
        workers = (ROOT / "presentation" / "qt" / "workers.py").read_text(
            encoding="utf-8-sig"
        )
        self.assertNotIn("prepare_factory", workers)
        # 三类正式 worker 都必须显式校验注入的 PreparedRun。
        self.assertEqual(workers.count("require_prepared_run(self.prepared_run"), 3)


class StepBCompatibilityInputTests(unittest.TestCase):
    """步骤 B1/B2：两个正式用例不得再依赖无路径兼容输入读取。"""

    def test_use_cases_no_longer_reference_the_legacy_reader(self):
        for relative in (
            "application/measurements/driver_mapping.py",
            "application/measurements/amplifier_test.py",
        ):
            with self.subTest(module=relative):
                source = (ROOT / relative).read_text(encoding="utf-8-sig")
                self.assertNotIn("LegacyResultInputReader", source)
                self.assertNotIn("read_legacy_cable_loss", source)
                self.assertNotIn("read_latest_legacy_driver_mapping", source)


class StepB3RemovalTests(unittest.TestCase):
    """步骤 B3：兼容读取器与 latest_path 兼容链必须已删除且不再回归。"""

    def test_legacy_reader_modules_are_removed(self):
        for relative in (
            "application/inputs/legacy_result_input_reader.py",
            "application/ports/legacy_result_input_reader.py",
        ):
            with self.subTest(module=relative):
                self.assertFalse((ROOT / relative).exists(), relative)

    def test_production_code_has_no_reference_to_removed_interfaces(self):
        for interface in (
            "LegacyResultInputReader",
            "latest_path",
            "latest_compatibility_path",
            "read_legacy_cable_loss",
            "read_latest_legacy_driver_mapping",
            "latest_legacy_driver_mapping_path",
        ):
            with self.subTest(interface=interface):
                self.assertEqual(_reference_files(interface), set())


class StepBPreConnectValidationTests(unittest.TestCase):
    """B1/B2：旧快照缺显式输入必须在连接仪器前失败（组装层结构门禁）。"""

    def test_gui_runtime_guards_legacy_inputs_before_connecting(self):
        source = (ROOT / "app" / "gui_runtime.py").read_text(encoding="utf-8-sig")
        self.assertIn("_require_legacy_explicit_inputs(", source)
        for head in (
            "def create_driver_mapping_measurement",
            "def create_amplifier_measurement",
        ):
            with self.subTest(factory=head):
                start = source.index(head)
                next_def = source.find("\ndef ", start + 1)
                body = source[start: next_def if next_def >= 0 else len(source)]
                guard_at = body.index("_require_legacy_explicit_inputs(")
                connect_at = body.index("connect_instruments(")
                self.assertLess(
                    guard_at,
                    connect_at,
                    f"{head} 必须在 connect_instruments() 之前校验旧快照显式输入",
                )


class StepCExecutionTests(unittest.TestCase):
    """步骤 2（2026-10-26）：正式流程不再写全局兼容副本。

    门禁固定四点：三类正式测量用例与正式测量包不得引用兼容写入 API；仓储 ``save()``
    不得写全局副本；``legacy_copy_path`` 只允许存在于结果 DTO；兼容副本只能由旧测量
    根入口显式写入（步骤 4 范围），接口本身保留到后续删除步骤。
    """

    WRITING_USE_CASES = (
        "application/measurements/amplifier_test.py",
        "application/measurements/cable_loss.py",
        "application/measurements/driver_mapping.py",
    )

    def test_measurement_use_cases_no_longer_write_the_compatibility_copy(self):
        for relative in self.WRITING_USE_CASES:
            with self.subTest(module=relative):
                source = (ROOT / relative).read_text(encoding="utf-8-sig")
                self.assertNotIn(
                    "save_compatibility_copy(",
                    source,
                    f"{relative} 不得再写全局兼容副本",
                )
                self.assertNotIn("_write_compatibility_copy", source)

    def test_repository_save_writes_only_the_run_result(self):
        source = (
            ROOT / "infrastructure" / "persistence" / "result_repository.py"
        ).read_text(encoding="utf-8-sig")
        self.assertNotIn("legacy_copy_path", source)
        self.assertNotIn("write_compatibility_copy", source)
        self.assertNotIn("save_compatibility_copy", source)
        self.assertNotIn("legacy_path", source)

    def test_storage_function_has_no_global_copy_write(self):
        source = (
            ROOT / "infrastructure" / "persistence" / "json_result_repository.py"
        ).read_text(encoding="utf-8-sig")
        save_start = source.index("def save_measurement_result(")
        save_end = source.index("def _load_snapshot(", save_start)
        body = source[save_start:save_end]
        self.assertNotIn("legacy_path", body)
        self.assertNotIn("compatibility_copy", body)

    def test_formal_measurement_package_never_references_compatibility_apis(self):
        """正式测量包不得访问兼容写入/路径 API，兼容调用只属于旧入口。"""
        package = ROOT / "application" / "measurements"
        forbidden = (
            "save_compatibility_copy",
            "write_compatibility_copy",
            "legacy_path",
        )
        for path in sorted(package.glob("*.py")):
            source = path.read_text(encoding="utf-8-sig")
            for name in forbidden:
                self.assertNotIn(
                    name,
                    source,
                    f"{_relative(path)} 不得引用兼容 API {name}",
                )

    def test_legacy_result_field_is_confined_to_the_result_dto(self):
        """``legacy_copy_path`` 已停止由保存流程产生，字段清理留待步骤 6。"""
        offenders = {
            _relative(path)
            for path in _production_sources()
            if "legacy_copy_path" in path.read_text(encoding="utf-8-sig")
        }
        self.assertEqual(
            offenders,
            {"application/dto/measurement_results.py"},
            "legacy_copy_path 只允许作为步骤 6 前的结果模型兼容字段存在",
        )

    def test_old_entry_points_stop_using_the_removed_compatibility_apis(self):
        """步骤 4 已退役旧入口文件；兼容写入 API 不得借旧入口回归。"""
        removed = (
            "save_compatibility_copy",
            "write_compatibility_copy",
            "write_legacy_run_snapshot",
            "create_legacy_run_snapshot",
        )
        for relative in RETIRED_LEGACY_MEASUREMENT_ENTRIES:
            with self.subTest(module=relative):
                self.assertFalse((ROOT / relative).exists(), relative)
        for name in removed:
            with self.subTest(interface=name):
                self.assertEqual(_reference_files(name), set())


class StepDMigrationTests(unittest.TestCase):
    """步骤 D：历史浏览迁移到运行目录，旧兼容副本保持只读可查看。"""

    def test_run_history_port_and_repository_exist(self):
        for relative in (
            "application/ports/run_history.py",
            "infrastructure/persistence/run_history_repository.py",
        ):
            with self.subTest(module=relative):
                self.assertTrue((ROOT / relative).is_file(), relative)

    def test_gui_history_adapter_uses_the_run_history_port(self):
        source = (
            ROOT / "infrastructure" / "gui_data_service.py"
        ).read_text(encoding="utf-8-sig")
        self.assertIn("application.ports.run_history", source)
        # 历史浏览不得复用正式测量结果仓储。
        self.assertNotIn("result_repository", source)
        self.assertNotIn("FileMeasurementResultRepository", source)

    def test_composition_injects_the_run_history_repository(self):
        source = (
            ROOT / "presentation" / "qt" / "composition.py"
        ).read_text(encoding="utf-8-sig")
        self.assertIn("FileRunHistoryRepository", source)

    def test_legacy_global_history_remains_read_only_viewable(self):
        path = ROOT / "infrastructure" / "persistence" / "result_history_repository.py"
        self.assertTrue(path.is_file(), "旧的只读全局历史仓储必须保留")
        source = path.read_text(encoding="utf-8-sig")
        for forbidden in ("def save", "def delete", "def unlink", "def write"):
            self.assertNotIn(forbidden, source)


class StepEAuditTests(unittest.TestCase):
    """步骤 E1：旧兼容测量入口盘点与门禁清单必须与实际代码一致。

    门禁固定两点：``release_manifest`` 的 ``rollback_files`` / ``compatibility_files``
    并集与盘点表模块集合**双向相等**（清单不得有未盘点模块，盘点不得有遗漏模块），
    且正式流程（非 tests）不导入这些旧入口，避免“盘点表与代码脱节”。
    """

    def test_every_audited_module_exists(self):
        for relative in LEGACY_AUDITED_SURFACE:
            with self.subTest(module=relative):
                self.assertTrue((ROOT / relative).is_file(), relative)

    def test_manifest_surface_equals_the_audited_inventory(self):
        import release_manifest

        manifest_surface = set(release_manifest.ROLLBACK_FILES) | set(
            release_manifest.COMPATIBILITY_FILES
        )
        self.assertEqual(
            manifest_surface,
            set(LEGACY_AUDITED_SURFACE),
            "release_manifest 清单必须与第 10 节盘点表完全一致（双向比较）",
        )

    def test_legacy_amplifier_entry_is_no_longer_registered_for_rollback(self):
        """步骤 4 已退役主功放入口，回滚清单不得再引用它。"""
        import release_manifest

        self.assertNotIn(
            "app/legacy_amplifier_measurement.py", release_manifest.ROLLBACK_FILES
        )
        self.assertNotIn("amplifier_measurement.py", release_manifest.ROLLBACK_FILES)

    def test_production_code_has_no_import_of_legacy_measurement_entries(self):
        audited = set(LEGACY_AUDITED_SURFACE)
        offenders = set()
        for path in _production_sources():
            if _relative(path) in audited:
                continue
            for node in ast.walk(_tree(path)):
                modules = []
                if isinstance(node, ast.ImportFrom):
                    # 跳过相对导入：同一 basename 的模块（如 application.lifecycle.
                    # measurement_lifecycle）不是根目录兼容再导出模块。
                    if node.level == 0 and node.module:
                        modules.append(node.module)
                elif isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                if any(
                    module in LEGACY_MODULE_NAMES
                    or module.split(".")[0] in LEGACY_MODULE_NAMES
                    for module in modules
                ):
                    offenders.add(_relative(path))
        self.assertEqual(
            offenders,
            set(),
            "正式流程不得导入旧兼容测量入口/再导出: " + ", ".join(sorted(offenders)),
        )


class Step4RetirementTests(unittest.TestCase):
    """步骤 4：三类旧测量根入口已整文件退役，不得以任何形式回归。

    门禁固定六点：源文件与可直接导入的 sourceless 字节码都不存在；任何位置都无法导入
    该模块（在独立子进程中验证，不受本进程 ``sys.modules`` 缓存影响）；生产代码不再
    定义旧入口类名；三条正式测量入口与用例仍在；发布清单不再把旧入口登记为回滚或
    兼容文件；源码与测试都不再出现旧入口的默认运行 ID。
    """

    RETIRED_CLASS_NAMES = (
        "CableLossMeasurement",
        "DriverPowerMapping",
        "AmplifierMeasurement",
    )

    def test_retired_entry_files_are_gone(self):
        for relative in RETIRED_LEGACY_MEASUREMENT_ENTRIES:
            with self.subTest(module=relative):
                self.assertFalse((ROOT / relative).exists(), relative)

    def test_retired_entry_sourceless_bytecode_does_not_exist(self):
        """源文件删除后不得留下可直接导入的 sourceless ``.pyc``。"""
        for relative in RETIRED_LEGACY_MEASUREMENT_ENTRIES:
            with self.subTest(module=relative):
                sourceless = ROOT / f"{relative}c"
                self.assertFalse(sourceless.exists(), f"{sourceless} 不得存在")

    def test_retired_entry_modules_cannot_be_imported(self):
        """在独立子进程中验证导入失败，避免本进程缓存掩盖回归。"""
        for module in RETIRED_LEGACY_MODULE_NAMES:
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

    def test_retired_entry_classes_are_not_defined_anywhere(self):
        offenders = set()
        for path in _production_sources():
            for node in ast.walk(_tree(path)):
                if (
                    isinstance(node, ast.ClassDef)
                    and node.name in self.RETIRED_CLASS_NAMES
                ):
                    offenders.add(f"{_relative(path)}:{node.lineno}")
        self.assertEqual(
            offenders,
            set(),
            "旧测量入口类不得以转发形式回归: " + ", ".join(sorted(offenders)),
        )

    def test_formal_measurement_entries_still_exist(self):
        """退役旧入口不得连带删除正式入口：三条路径仍由正式 API 提供。"""
        runtime = _tree(ROOT / "app" / "gui_runtime.py")
        functions = {
            node.name for node in runtime.body if isinstance(node, ast.FunctionDef)
        }
        for factory in (
            "create_cable_loss_measurement",
            "create_driver_mapping_measurement",
            "create_amplifier_measurement",
        ):
            with self.subTest(factory=factory):
                self.assertIn(factory, functions)

        package = (
            ROOT / "application" / "measurements" / "__init__.py"
        ).read_text(encoding="utf-8-sig")
        for use_case in (
            "CableLossUseCase",
            "DriverPowerMappingUseCase",
            "AmplifierMeasurementUseCase",
        ):
            with self.subTest(use_case=use_case):
                self.assertIn(use_case, package)

    def test_release_manifest_drops_the_retired_entries(self):
        """回滚/兼容清单不得再登记已退役入口，发布文件也不得引用它们。"""
        import release_manifest

        self.assertEqual(release_manifest.ROLLBACK_FILES, ())
        manifest = release_manifest.build_manifest(ROOT)
        self.assertEqual(manifest["rollback_files"], [])
        for relative in RETIRED_LEGACY_MEASUREMENT_ENTRIES:
            with self.subTest(module=relative):
                self.assertNotIn(relative, manifest["rollback_files"])
                self.assertNotIn(relative, manifest["compatibility_files"])
                self.assertNotIn(relative, release_manifest.RELEASE_FILES)

    def test_no_source_file_keeps_the_retired_run_id(self):
        """旧默认运行 ID 不得留在源码或测试里（历史文档除外）。"""
        offenders = {
            _relative(path)
            for path in _project_sources()
            if RETIRED_LEGACY_RUN_ID in path.read_text(encoding="utf-8-sig")
        }
        self.assertEqual(
            offenders,
            set(),
            "源码/测试仍引用旧默认运行 ID: " + ", ".join(sorted(offenders)),
        )

    def test_measurement_services_have_no_implicit_run_id(self):
        """旧入口删除后不再有默认运行 ID 兜底：服务必须显式接收 run_id。"""
        source = (
            ROOT / "application" / "measurements" / "services.py"
        ).read_text(encoding="utf-8-sig")
        self.assertNotIn(RETIRED_LEGACY_RUN_ID, source)
        self.assertIn("*, run_id: str", source)
        self.assertNotIn("run_id: str =", source)
        for builder in (
            "application/measurements/cable_loss.py",
            "application/measurements/driver_mapping.py",
            "application/measurements/amplifier_test.py",
        ):
            with self.subTest(module=builder):
                self.assertIn(
                    "run_id=self.run_id",
                    (ROOT / builder).read_text(encoding="utf-8-sig"),
                )


class Step5RetirementTests(unittest.TestCase):
    """步骤 5：旧配置自动转换与根目录配置转发已整文件退役。

    门禁固定：四个源文件不存在且无法导入；生产代码不再出现 ``load_legacy_data`` /
    ``unresolved_fields`` / ``source_format`` / ``conversion_errors``；正式配置仓储
    仍提供组合与拆分两种正式 schema 读取入口。
    """

    RETIRED_CONFIG_FILES = (
        "config_io.py",
        "config_models.py",
        "config_validation.py",
        "infrastructure/config/legacy_conversion.py",
    )
    RETIRED_CONFIG_MODULES = (
        "config_io",
        "config_models",
        "config_validation",
        "infrastructure.config.legacy_conversion",
    )
    RETIRED_IDENTIFIERS = (
        "load_legacy_data",
        "unresolved_fields",
        "source_format",
        "conversion_errors",
    )

    def test_retired_config_files_are_gone(self):
        for relative in self.RETIRED_CONFIG_FILES:
            with self.subTest(module=relative):
                self.assertFalse((ROOT / relative).exists(), relative)

    def test_retired_config_modules_cannot_be_imported(self):
        for module in self.RETIRED_CONFIG_MODULES:
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

    def test_production_code_has_no_legacy_conversion_flow(self):
        offenders = set()
        for path in _production_sources():
            tree = _tree(path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id in self.RETIRED_IDENTIFIERS:
                    offenders.add(f"{_relative(path)}:{node.lineno}:{node.id}")
                elif (
                    isinstance(node, ast.Attribute)
                    and node.attr in self.RETIRED_IDENTIFIERS
                ):
                    offenders.add(f"{_relative(path)}:{node.lineno}:{node.attr}")
        self.assertEqual(
            offenders,
            set(),
            "生产代码不得再出现旧配置转换流程: " + ", ".join(sorted(offenders)),
        )

    def test_formal_configuration_repository_still_reads_official_schema(self):
        source = (
            ROOT / "infrastructure" / "config" / "json_config_repository.py"
        ).read_text(encoding="utf-8-sig")
        for marker in ("load_combined_configuration", "JsonConfigurationRepository"):
            self.assertIn(marker, source)
        self.assertNotIn("legacy", source.lower())


class ReleaseNotesTests(unittest.TestCase):
    """删除条件、迁移方式和目标版本必须留在发布说明里，而不是只存在于讨论中。"""

    def setUp(self):
        self.assertTrue(RELEASE_NOTES.is_file(), "缺少兼容层弃用与删除发布说明")
        self.document = RELEASE_NOTES.read_text(encoding="utf-8")

    def test_release_notes_document_every_deprecated_interface(self):
        missing = [item for item in REQUIRED_NOTE_MARKERS if item not in self.document]
        self.assertEqual(missing, [], "发布说明缺少接口: " + ", ".join(missing))

    def test_release_notes_keep_the_four_deletion_condition_blocks(self):
        missing = [item for item in REQUIRED_NOTE_SECTIONS if item not in self.document]
        self.assertEqual(missing, [], "发布说明缺少章节: " + ", ".join(missing))

    def test_each_deletion_section_has_conditions_migration_rollback_and_version(self):
        headings = ("### 3.1 ", "### 3.2 ", "### 3.3 ", "### 3.4 ")
        for heading in headings:
            with self.subTest(section=heading):
                start = self.document.index(heading)
                next_heading = self.document.find("### 3.", start + len(heading))
                end = next_heading if next_heading >= 0 else self.document.find("## 4.", start)
                section = self.document[start:end]
                for marker in ("删除条件", "迁移方式", "回滚与查看", "目标版本"):
                    self.assertIn(marker, section, f"{heading} 缺少 {marker}")

    def test_release_notes_record_the_target_versions(self):
        for version in ("v1.1", "v1.2"):
            self.assertIn(version, self.document)

    def test_guide_records_step_e_and_links_the_release_notes(self):
        guide = GUIDE.read_text(encoding="utf-8")
        # 指南收敛为“未完成小步清单”后，步骤 E 以标题形式保留，并链接发布说明。
        self.assertIn("### E. ", guide)
        self.assertIn("兼容层弃用与删除发布说明", guide)

    def test_release_notes_record_step_a_execution_evidence(self):
        """步骤 A 的删除必须有可审计记录：外部调用方台账与回滚基线。"""
        for marker in (
            "步骤 A 执行记录",
            "外部调用方台账",
            "回滚基线",
            "代码已删除（步骤 A",
        ):
            self.assertIn(marker, self.document)

    def test_release_notes_record_the_completed_external_confirmation(self):
        """外部调用方确认完成后必须显式登记结论、日期与来源。"""
        self.assertIn("已确认（2026-10-24 / 任务授权人）", self.document)
        self.assertIn("无外部调用方", self.document)
        self.assertIn("偏差登记", self.document)

    def test_release_notes_record_the_b3_authorization(self):
        """纯代码路径删除（B3）必须有授权记录与边界，不能只靠改文字消解门槛冲突。"""
        for marker in (
            "删除影响面分级与 B3 授权",
            "授权日期",
            "禁止任何数据类删除",
        ):
            self.assertIn(marker, self.document)

    def test_guide_marks_a4_complete(self):
        guide = GUIDE.read_text(encoding="utf-8")
        self.assertIn("4. [x] **A4", guide)

    def test_release_notes_record_step_b_evidence(self):
        """步骤 B 的删除必须有可审计记录：执行记录、删除前清单与回滚基线。"""
        for marker in (
            "步骤 B 执行记录",
            "步骤 B1/B2 执行记录",
            "步骤 B3 执行记录",
            "删除前调用方清单",
            "步骤 B3",
        ):
            self.assertIn(marker, self.document)

    def test_release_notes_record_the_step_c_blocker(self):
        """步骤 C 属数据类删除，须登记在 F 归档前被阻断，不能被静默执行或删除记录。"""
        for marker in (
            "步骤 C 执行评估与阻塞登记",
            "数据类删除",
            "步骤 F",
            "不得执行 C1 至 C4",
        ):
            self.assertIn(marker, self.document)

    def test_release_notes_record_step_d_evidence(self):
        """步骤 D 必须有执行记录：运行目录历史、数据保留期限与回滚方案。"""
        for marker in (
            "步骤 D 执行记录",
            "运行目录历史",
            "数据保留期限",
            "回滚方案",
        ):
            self.assertIn(marker, self.document)

    def test_guide_marks_d_complete(self):
        guide = GUIDE.read_text(encoding="utf-8")
        for marker in ("1. [x] **D1", "2. [x] **D2", "3. [x] **D3", "4. [x] **D4"):
            self.assertIn(marker, guide)

    def test_release_notes_record_step_d_review_fixes(self):
        """步骤 D 的边界/回退修复须登记，避免文档与实现不一致。"""
        for marker in (
            "审查修复记录",
            "符号链接",
            "阶段结果",
            "损坏",
            "回退",
        ):
            self.assertIn(marker, self.document)

    def test_release_notes_record_the_step_e_blocker(self):
        """数据类入口（E2 至 E4）须登记在 F 归档前被阻断，不能被静默退役。"""
        for marker in (
            "步骤 E 执行评估与收尾",
            "数据类删除",
            "旧兼容测量根入口",
            "不得执行 E2",
        ):
            self.assertIn(marker, self.document)

    def test_release_notes_record_the_reexport_pure_code_classification(self):
        """再导出模块必须登记为纯代码路径、独立退役，而非数据类删除。"""
        for marker in ("纯代码路径", "独立退役"):
            self.assertIn(marker, self.document)

    def test_release_notes_contain_the_step_e_audit_table(self):
        """步骤 E1 盘点表必须逐模块登记，含所有者、替代入口与迁移/退役结论。"""
        for module in (
            "cable_loss_measurement.py",
            "driver_power_mapping.py",
            "app/legacy_amplifier_measurement.py",
            "amplifier_measurement.py",
            "result_storage.py",
            "measurement_services.py",
            "measurement_lifecycle.py",
            "measurement_calculations.py",
            "result_reading.py",
            "app_logging.py",
            "project_paths.py",
            "config_io.py",
            "config_models.py",
            "config_validation.py",
        ):
            self.assertIn(module, self.document)
        for column in ("所有者", "替代入口", "迁移/退役结论"):
            self.assertIn(column, self.document)

    def test_release_notes_record_the_step_e5_verification(self):
        """E5 门禁复核本轮已执行（非删除动作），须登记命令与结果。"""
        for marker in (
            "E5 门禁复核",
            "全量离线测试",
            "launcher.py --check",
            "validate-config",
        ):
            self.assertIn(marker, self.document)

    def test_guide_marks_e1_complete(self):
        guide = GUIDE.read_text(encoding="utf-8")
        self.assertIn("1. [x] **E1", guide)

    def test_guide_marks_e5_complete(self):
        guide = GUIDE.read_text(encoding="utf-8")
        self.assertIn("5. [x] **E5", guide)

    def test_release_notes_record_step_4_retirement_and_current_state(self):
        """步骤 4 必须有执行记录，且现行状态不得再声称旧入口等待 F 归档后删除。"""
        for marker in (
            "步骤 4 执行记录",
            "已从清单移除",
            "步骤 4/5 之后的现行状态",
        ):
            self.assertIn(marker, self.document)
        self.assertNotIn(
            "旧测量根入口文件本体按步骤 E2 至 E4 在 F 归档后退役", self.document
        )

    def test_guide_describes_step_4_as_completed(self):
        """指南现行结论不得把 E2 至 E4 描述为待执行项。"""
        guide = GUIDE.read_text(encoding="utf-8")
        self.assertIn("步骤 4 已整文件退役", guide)
        self.assertNotIn("E2 至 E4 只差删除文件本体", guide)


if __name__ == "__main__":
    unittest.main()
