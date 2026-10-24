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
- 步骤 C 属**数据类删除**（停止写全局兼容副本），在步骤 D、F 完成前被禁止执行；
  兼容副本写入必须保持存在，并由说明第 8 节登记阻断结论。
- 步骤 D 已把 GUI 历史列表/查看/导出迁移到运行目录 manifest 视图，旧的全局兼容副本
  保持只读可查看；运行历史只读端口与仓储必须保持存在，且历史浏览不得复用正式测量
  结果仓储。
- 说明必须为每个待删除接口写出删除条件、迁移方式和目标版本，并保留步骤 A 执行
  记录（外部调用方台账与回滚版本）。

旧兼容/回滚入口本身（``cable_loss_measurement.py``、``driver_power_mapping.py``、
``app/legacy_amplifier_measurement.py``）在说明 3.5 节记录为**本次保留**，因此这里
只校验调用方集合，不要求它们消失。
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
    "save_compatibility_copy": {
        "application/measurements/amplifier_test.py",
        "application/measurements/cable_loss.py",
        "application/measurements/driver_mapping.py",
    },
    "create_legacy_run_snapshot": set(),
    "write_legacy_run_snapshot": {
        "app/legacy_amplifier_measurement.py",
        "cable_loss_measurement.py",
        "driver_power_mapping.py",
        "infrastructure/persistence/result_repository.py",
        "result_storage.py",
    },
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

    def test_repository_save_callers_match_the_legacy_copy_inventory(self):
        """三个用例旧分支与三个旧入口通过仓储 save() 写全局兼容副本。"""
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


class StepCBlockedTests(unittest.TestCase):
    """步骤 C 是数据类删除，在步骤 F 归档前禁止执行。

    门禁固定两点：兼容副本写入必须**仍然存在**（防止未经授权提前删除），
    且发布说明登记了阻断结论（防止靠改文字消解门槛冲突）。
    """

    WRITING_USE_CASES = (
        "application/measurements/amplifier_test.py",
        "application/measurements/cable_loss.py",
        "application/measurements/driver_mapping.py",
    )

    def test_measurement_use_cases_still_write_the_compatibility_copy(self):
        for relative in self.WRITING_USE_CASES:
            with self.subTest(module=relative):
                source = (ROOT / relative).read_text(encoding="utf-8-sig")
                self.assertIn(
                    "save_compatibility_copy(",
                    source,
                    f"{relative} 的兼容副本写入不得在步骤 D/F 完成前删除",
                )

    def test_repository_still_exposes_the_compatibility_copy_path(self):
        source = (
            ROOT / "infrastructure" / "persistence" / "result_repository.py"
        ).read_text(encoding="utf-8-sig")
        self.assertIn("def save_compatibility_copy(", source)
        self.assertIn("write_compatibility_copy(", source)


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


if __name__ == "__main__":
    unittest.main()
