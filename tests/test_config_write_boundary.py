"""门禁：正式用户配置只有一个写入入口（重构计划步骤 3）。

配置 JSON 的生产写入点盘点后只剩三类用途，本文件把它们各自钉死：

1. **正式用户配置**（``config.json``）：唯一实现是
   ``JsonConfigurationRepository.save_document``——配置页面保存（
   :class:`infrastructure.gui_data_service.JsonConfigurationAdapter`）与旧电源配置迁移器
   都只委托它；
2. **运行目录快照**（``runs/<run_id>/config.json`` 与 manifest）：仍由运行仓储与运行
   目录布局负责，配置仓储与 GUI 适配器不写运行目录；
3. **其它 JSON 文档**（设备表、迁移摘要、测量结果）：各自仓储/工具负责，与配置文件
   互不混用。

因此这里断言的可观察契约是：

- 适配器与迁移器都不再自己序列化或打开文件，只要委托链断掉就失败；
- 正式配置的序列化格式（``indent=4`` + 非 ASCII 原样）只由配置仓储声明一次；
- 导入原子写入原语的模块是一张带用途说明的固定清单，新增写入点必须先改清单；
- ``ConfigurationPort`` 不出现第二个保存入口。

验证方式：

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" -m unittest tests.test_config_write_boundary -v
```
"""

import ast
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, patch

from infrastructure.config.json_config_repository import JsonConfigurationRepository
from infrastructure.config.legacy_power_supply_migration import (
    LegacyMigrationResult,
    save_migrated_configuration,
)
from infrastructure.gui_data_service import JsonConfigurationAdapter
from tests.config_fixtures import complete_combined_config


ROOT = Path(__file__).parents[1]
RUNTIME_PACKAGES = ("app", "application", "domain", "infrastructure", "instrument", "presentation")
SCOPE_FILES = ("launcher.py", "release_manifest.py", "collect_baseline.py")
ADAPTER_SOURCE = ROOT / "infrastructure" / "gui_data_service.py"
REPOSITORY_SOURCE = ROOT / "infrastructure" / "config" / "json_config_repository.py"
PORT_SOURCE = ROOT / "application" / "ports" / "gui_data_service.py"
RUN_REPOSITORY_SOURCE = ROOT / "infrastructure" / "persistence" / "run_repository.py"

# 生产代码里允许导入原子写入原语的模块 -> 用途。写死清单的目的：新增一处“顺手写 JSON”
# 必须先在这里登记用途，评审时才能看出它是否偷偷变成了第二条配置写入路径。
ATOMIC_WRITE_IMPORTERS = {
    "infrastructure/config/json_config_repository.py": "正式用户配置的唯一写入口 save_document",
    "infrastructure/config/legacy_power_supply_migration.py": "迁移摘要文件（不是配置文件）",
    "infrastructure/persistence/instrument_registry.py": "设备表 instruments.json",
    "infrastructure/persistence/run_directory.py": "运行 manifest",
    "infrastructure/persistence/run_repository.py": "运行目录快照与阶段结果",
}

# 正式配置的序列化格式：``indent=4`` + 非 ASCII 原样保留。
OFFICIAL_FORMAT_KWARGS = {"indent": 4, "ensure_ascii": False}
SERIALIZER_CALLS = {"write_json_atomic", "dump"}
OFFICIAL_CONFIGURATION_FORMAT_SITE = (
    "infrastructure/config/json_config_repository.py",
    "JsonConfigurationRepository.save_document",
)
# 结果 JSON 明细写入复用同样的缩进/非 ASCII 策略，但写的是测量结果文档，与用户配置无关；
# 结果格式的收敛不属于本步骤，因此单独登记为已知声明点而不是放宽断言。
RESULT_DOCUMENT_FORMAT_SITES = {
    ("infrastructure/persistence/json_result_repository.py", "save_json_result"),
}

# 适配器不该出现的“自己动手写文件”的痕迹。
FILE_WRITE_TRACES = {
    "open",
    "write_json_atomic",
    "NamedTemporaryFile",
    "mkstemp",
    "fsync",
    "replace",
    "rename",
    "tempfile",
    "os",
    "json",
    "ensure_directory",
}


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8-sig"), str(path))


def _relative(path):
    return path.relative_to(ROOT).as_posix()


def _production_sources():
    sources = []
    for package in RUNTIME_PACKAGES:
        sources.extend(sorted((ROOT / package).rglob("*.py")))
    sources.extend(ROOT / name for name in SCOPE_FILES)
    return sources


def _class_node(path, name):
    for node in _tree(path).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"{_relative(path)} 中未找到类 {name}")


def _method_names(class_node):
    return {
        node.name
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _called_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _enclosing_scopes(tree):
    """返回 调用节点 -> 最近外层的 ``类.函数`` / 函数名（模块级为空串）。"""
    owners = {}

    def walk(node, scope):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                walk(child, f"{scope}.{child.name}" if scope else child.name)
                continue
            if isinstance(child, ast.Call):
                owners[child] = scope
            walk(child, scope)

    walk(tree, "")
    return owners


def _imported_modules(tree):
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            modules.add(node.module)
    return modules


def _atomic_write_importers():
    """返回导入**写入**原语的生产模块（只读的 ``read_json`` 导入不算）。

    ``import infrastructure.filesystem.atomic_json`` 这类模块级导入无法静态区分用途，
    因此一律计入，由清单强制说明。
    """
    importers = set()
    for source in _production_sources():
        for node in ast.walk(_tree(source)):
            if isinstance(node, ast.Import):
                if any(
                    alias.name == "infrastructure.filesystem.atomic_json"
                    for alias in node.names
                ):
                    importers.add(_relative(source))
            elif (
                isinstance(node, ast.ImportFrom)
                and not node.level
                and node.module == "infrastructure.filesystem.atomic_json"
                and any(alias.name == "write_json_atomic" for alias in node.names)
            ):
                importers.add(_relative(source))
    return sorted(importers)


def _official_format_writers():
    """返回生产代码里显式写出正式配置格式的 ``(模块, 作用域)`` 集合。"""
    writers = set()
    for source in _production_sources():
        tree = _tree(source)
        owners = _enclosing_scopes(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _called_name(node.func) not in SERIALIZER_CALLS:
                continue
            keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
            if set(OFFICIAL_FORMAT_KWARGS) - set(keywords):
                continue
            if all(
                isinstance(keywords[name], ast.Constant)
                and keywords[name].value == value
                for name, value in OFFICIAL_FORMAT_KWARGS.items()
            ):
                writers.add((_relative(source), owners.get(node, "")))
    return writers


class ConfigurationWriteEntranceTests(unittest.TestCase):
    """适配器与迁移器都只委托配置仓储的写入口，自己不写文件。"""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.path = self.directory / "config.json"

    def test_adapter_save_delegates_to_the_configuration_repository(self):
        configuration = complete_combined_config()
        adapter = JsonConfigurationAdapter(self.path)

        with patch.object(
            JsonConfigurationRepository, "save_document", autospec=True
        ) as save_document:
            adapter.save(configuration)

        save_document.assert_called_once_with(adapter.repository, self.path, configuration)
        self.assertFalse(
            self.path.exists(), "适配器不得自己落盘：写入只经由配置仓储的 save_document"
        )

    def test_adapter_load_reads_through_the_shared_read_boundary(self):
        adapter = JsonConfigurationAdapter(self.path)
        self.path.write_text(json.dumps({"schema_version": "1.0"}), encoding="utf-8")

        self.assertEqual(adapter.load(), {"schema_version": "1.0"})

    def test_migration_save_uses_the_same_entrance(self):
        configuration = complete_combined_config()
        result = LegacyMigrationResult(
            config=configuration, entries=(), old_keys=("PS1",)
        )

        def validator(_document):
            return SimpleNamespace(valid=True)

        with patch.object(
            JsonConfigurationRepository, "save_document", autospec=True
        ) as save_document:
            written = save_migrated_configuration(
                self.path, result, validator=validator
            )

        save_document.assert_called_once_with(ANY, self.path, configuration)
        self.assertEqual(written, save_document.return_value)
        self.assertFalse(self.path.exists(), "迁移器不得另起一套写入实现")

    def test_repository_save_is_the_official_format_and_is_reloadable(self):
        configuration = dict(complete_combined_config(), note="现场配置")

        JsonConfigurationRepository().save_document(self.path, configuration)

        self.assertEqual(
            self.path.read_text(encoding="utf-8"),
            json.dumps(configuration, indent=4, ensure_ascii=False),
        )
        self.assertEqual(JsonConfigurationAdapter(self.path).load(), configuration)


class ConfigurationWriteSourceBoundaryTests(unittest.TestCase):
    """静态边界：写入口只有一处，适配器不掌握文件与格式。"""

    def test_gui_adapter_owns_no_file_or_formatting_logic(self):
        adapter = _class_node(ADAPTER_SOURCE, "JsonConfigurationAdapter")
        traces = {
            node.id for node in ast.walk(adapter) if isinstance(node, ast.Name)
        } | {node.attr for node in ast.walk(adapter) if isinstance(node, ast.Attribute)}
        self.assertEqual(
            sorted(traces & FILE_WRITE_TRACES),
            [],
            "配置适配器只能委托配置仓储，不得自己打开文件、序列化或管理临时文件",
        )
        self.assertNotIn(
            "infrastructure.filesystem.atomic_json",
            _imported_modules(_tree(ADAPTER_SOURCE)),
            "适配器不得直接依赖原子写入原语；写入细节归配置仓储",
        )

    def test_official_configuration_format_is_declared_once(self):
        self.assertEqual(
            _official_format_writers(),
            RESULT_DOCUMENT_FORMAT_SITES | {OFFICIAL_CONFIGURATION_FORMAT_SITE},
            "正式配置的序列化格式只允许在配置仓储的 save_document 里声明；"
            "新增声明点必须先确认它不是第二条配置写入路径",
        )

    def test_atomic_write_importers_match_the_registered_purposes(self):
        self.assertEqual(
            _atomic_write_importers(),
            sorted(ATOMIC_WRITE_IMPORTERS),
            "原子 JSON 写入点必须与登记清单严格一致（新增先登记用途，删除后同步收缩）；"
            "正式配置文件只能由配置仓储写入",
        )

    def test_configuration_port_exposes_a_single_save_entry(self):
        port = _class_node(PORT_SOURCE, "ConfigurationPort")
        self.assertEqual(
            _method_names(port),
            {"load", "save"},
            "配置端口只保留一个保存入口；第二个写入口会让保存路径重新分叉",
        )

    def test_run_snapshot_write_stays_in_the_run_repository(self):
        for path in (REPOSITORY_SOURCE, ADAPTER_SOURCE):
            run_persistence_imports = [
                module
                for module in _imported_modules(_tree(path))
                if module.startswith("infrastructure.persistence")
            ]
            self.assertEqual(
                run_persistence_imports,
                [],
                f"{_relative(path)} 不得参与运行快照写入：快照由运行仓储负责",
            )

        snapshot_writes = [
            node
            for node in ast.walk(_tree(RUN_REPOSITORY_SOURCE))
            if isinstance(node, ast.Call)
            and _called_name(node.func) == "write_json_atomic"
            and node.args
            and isinstance(node.args[0], ast.Attribute)
            and node.args[0].attr == "config_path"
        ]
        self.assertEqual(
            len(snapshot_writes),
            1,
            "运行目录内的 config.json 快照仍由运行仓储写入，且只有一处",
        )


class BoundaryDetectorTests(unittest.TestCase):
    """门禁辅助函数自身的回归：换一种写法也必须被识别。"""

    def test_official_format_detector_requires_both_format_keywords(self):
        tree = ast.parse(
            "def save():\n"
            "    write_json_atomic(path, doc, indent=4, ensure_ascii=False)\n"
            "    write_json_atomic(path, doc, indent=2, ensure_ascii=False)\n"
            "    write_json_atomic(path, doc)\n"
        )
        owners = _enclosing_scopes(tree)
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        self.assertEqual([owners.get(node) for node in calls], ["save"] * 3)
        formatted = [
            node
            for node in calls
            if len(node.keywords) == 2
            and {keyword.arg for keyword in node.keywords} == set(OFFICIAL_FORMAT_KWARGS)
        ]
        self.assertEqual(len(formatted), 2, "两条带全格式参数的调用应被锁定，第三条不算")

    def test_scope_owner_tracks_class_and_method(self):
        tree = ast.parse("class Repo:\n    def save(self):\n        write_json_atomic(p, d)\n")
        owners = _enclosing_scopes(tree)
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        self.assertEqual([owners.get(node) for node in calls], ["Repo.save"])


if __name__ == "__main__":
    unittest.main()
