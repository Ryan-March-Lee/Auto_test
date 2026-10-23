"""步骤 E 门禁：兼容层删除条件与生产调用方清单证据。

本文件把 [兼容层弃用与删除发布说明](../docs/兼容层弃用与删除发布说明.md) 记录的
证据固化为可执行断言：

- 每个待删除接口在生产代码中的调用文件集合必须与该说明一致；新增调用方会失败。
  清单包含仍在使用的正式流程和旧兼容调用，不等同于“零生产引用”。
- 步骤 A 已删除的旧 GUI 测量入口（``prepare_configuration`` 的兼容快照开关、
  worker 的 ``prepare_factory`` 后备）必须保持消失状态，不允许以任何形式回归。
- 生产代码不得再使用 ``run_contract`` 开关，也不得注入旧 ``prepare_factory`` 后备工厂。
- 说明必须为每个待删除接口写出删除条件、迁移方式和目标版本。

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
    # 3.1 按时间扫描全局结果的兼容入口
    "latest_path": {"application/inputs/legacy_result_input_reader.py"},
    "latest_compatibility_path": {"infrastructure/persistence/result_repository.py"},
    # 3.2 无路径兼容读取
    "read_legacy_cable_loss": {
        "application/measurements/amplifier_test.py",
        "application/measurements/driver_mapping.py",
    },
    "read_latest_legacy_driver_mapping": {
        "application/measurements/amplifier_test.py",
    },
    "latest_legacy_driver_mapping_path": {
        "application/inputs/legacy_result_input_reader.py",
    },
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

    def test_deprecated_gui_query_alias_has_no_production_caller(self):
        """``ResultQueryAdapter.latest()`` 只为外部脚本保留，生产代码不得调用。"""
        implementation = (
            ROOT / "infrastructure" / "gui_data_service.py"
        ).read_text(encoding="utf-8-sig")
        self.assertIn("def latest(self", implementation)

        callers = {
            _relative(path)
            for path in _production_sources()
            for node in ast.walk(_tree(path))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "latest"
        }
        self.assertEqual(callers, set())

    def test_deprecated_query_port_is_not_imported_by_production_code(self):
        """``LegacyResultQueryPort`` 只记录弃用别名，不得被生产模块导入。"""
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
        # 只允许端口模块自身的类定义，任何其它文件出现该名字都是新的生产调用方。
        self.assertEqual(mentioners, {"application/ports/gui_data_service.py"})


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


if __name__ == "__main__":
    unittest.main()
