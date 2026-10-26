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
- 步骤 D 已把 GUI 历史列表/查看/导出迁移到运行目录 manifest 视图，旧的全局兼容副本
  保持只读可查看；运行历史只读端口与仓储必须保持存在，且历史浏览不得复用正式测量
  结果仓储。
- 步骤 E1 已完成旧兼容测量根入口盘点：``release_manifest`` 的 ``rollback_files`` /
  ``compatibility_files`` 并集必须与盘点表**双向相等**，正式流程不得导入这些旧入口。
- 旧测量根入口（``cable_loss_measurement.py`` / ``driver_power_mapping.py`` /
  ``app/legacy_amplifier_measurement.py``）在步骤 4 前仍存在，但不得再引用已删除的
  兼容写入 API。
- 步骤 E5 是门禁复核（更新测试/说明、跑全量离线测试与应用检查），非删除动作，本轮执行。
- 说明必须为每个待删除接口写出删除条件、迁移方式和目标版本，并保留步骤 A 执行
  记录（外部调用方台账与回滚版本）。

旧兼容/回滚入口本身（``cable_loss_measurement.py``、``driver_power_mapping.py``、
``app/legacy_amplifier_measurement.py``）在说明 3.5 节记录为**本次保留**，因此这里
只校验调用方集合与盘点一致性，不要求它们消失。
"""

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
EXCLUDED_PARTS = {".git", "__pycache__", "tests"}
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
    "app/legacy_amplifier_measurement.py",
    "application/measurements/amplifier_test.py",
    "application/measurements/cable_loss.py",
    "application/measurements/driver_mapping.py",
    "cable_loss_measurement.py",
    "driver_power_mapping.py",
}

# 发布说明 3.5 / 第 10 节盘点表：旧兼容测量根入口（真实写旧运行快照/兼容副本）。
LEGACY_MEASUREMENT_DATA_ENTRIES = (
    "cable_loss_measurement.py",
    "driver_power_mapping.py",
    "app/legacy_amplifier_measurement.py",
)

# 主功放旧入口的公开转发模块（随 E4 数据类批次一并退役）。
LEGACY_AMPLIFIER_FORWARDER = "amplifier_measurement.py"

# 配置/结果/日志/服务/生命周期等纯再导出模块（纯代码路径，独立退役，不受步骤 F 阻断）。
LEGACY_REEXPORT_MODULES = (
    "measurement_services.py",
    "measurement_lifecycle.py",
    "measurement_calculations.py",
    "result_reading.py",
    "app_logging.py",
    "project_paths.py",
    "config_io.py",
    "config_models.py",
    "config_validation.py",
)

# 步骤 E1 盘点的完整兼容面 = release_manifest.ROLLBACK_FILES ∪ COMPATIBILITY_FILES。
LEGACY_AUDITED_SURFACE = (
    LEGACY_MEASUREMENT_DATA_ENTRIES
    + (LEGACY_AMPLIFIER_FORWARDER,)
    + LEGACY_REEXPORT_MODULES
)

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
        "config_io",
        "config_models",
        "config_validation",
    }
)


def _production_sources():
    """返回 tests 之外的全部项目 Python 源文件。"""
    return sorted(
        path
        for path in ROOT.rglob("*.py")
        if not (set(path.relative_to(ROOT).parts) & EXCLUDED_PARTS)
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
        """步骤 3 删除兼容写入接口后，旧入口只写运行目录结果，文件本体留待步骤 4 退役。"""
        removed = (
            "save_compatibility_copy",
            "write_compatibility_copy",
            "write_legacy_run_snapshot",
            "create_legacy_run_snapshot",
        )
        for relative in LEGACY_MEASUREMENT_DATA_ENTRIES:
            with self.subTest(module=relative):
                source = (ROOT / relative).read_text(encoding="utf-8-sig")
                for name in removed:
                    self.assertNotIn(
                        name,
                        source,
                        f"{relative} 不得再引用已删除的兼容 API {name}",
                    )


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

    def test_legacy_amplifier_entry_is_registered_for_rollback(self):
        """主功放旧入口实现随 ``amplifier_measurement.py`` 转发，必须一并可回滚。"""
        import release_manifest

        self.assertIn(
            "app/legacy_amplifier_measurement.py", release_manifest.ROLLBACK_FILES
        )

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


class StepEBlockedTests(unittest.TestCase):
    """步骤 3 后：旧测量根入口不再写已删除的兼容副本/旧运行快照。

    这三个入口的文件本体留待步骤 4 退役；本类只固定它们不再引用已删除接口，
    且仍通过结果仓储写运行目录结果。
    """

    REMOVED_APIS = (
        "save_compatibility_copy",
        "write_compatibility_copy",
        "write_legacy_run_snapshot",
        "create_legacy_run_snapshot",
        "legacy_path",
    )

    def _assert_no_removed_api(self, relative):
        source = (ROOT / relative).read_text(encoding="utf-8-sig")
        for name in self.REMOVED_APIS:
            self.assertNotIn(name, source, f"{relative} 不得引用 {name}")

    def test_cable_loss_entry_only_writes_the_run_result(self):
        self._assert_no_removed_api("cable_loss_measurement.py")
        source = (ROOT / "cable_loss_measurement.py").read_text(encoding="utf-8-sig")
        self.assertIn("repository.save(", source)

    def test_driver_mapping_entry_only_writes_the_run_result(self):
        self._assert_no_removed_api("driver_power_mapping.py")
        source = (ROOT / "driver_power_mapping.py").read_text(encoding="utf-8-sig")
        self.assertIn("repository.save(", source)

    def test_legacy_amplifier_entry_only_writes_the_run_result(self):
        self._assert_no_removed_api("app/legacy_amplifier_measurement.py")
        source = (
            ROOT / "app" / "legacy_amplifier_measurement.py"
        ).read_text(encoding="utf-8-sig")
        self.assertIn("repository.save(", source)

    def test_amplifier_forwarder_still_reexports_the_legacy_entry(self):
        source = (ROOT / "amplifier_measurement.py").read_text(encoding="utf-8-sig")
        self.assertIn(
            "from app.legacy_amplifier_measurement import AmplifierMeasurement",
            source,
        )


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


if __name__ == "__main__":
    unittest.main()
