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
- 生产代码里**每一处 JSON 文档写入调用点**都登记了用途与类别，其中“用户配置”类别只有
  ``save_document`` 一项；
- 正式配置路径（``config_file`` / ``CONFIG_FILE``）只被登记过的少量作用域引用，且这些
  作用域自己都不直接写文件（写入只能经 ``save_document``）；
- ``ConfigurationPort`` 不出现第二个保存入口。

门禁的边界：它是**调用点与路径引用的静态登记**。它不能证明任意动态路径写入不可能，
但任何新增的 JSON 写入点、新增的配置路径引用或新增的保存入口都会让本文件失败，评审时
必须先解释用途才能继续。

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
# ``analysis`` 虽然不写配置文件，但属于正式导入根：把配置写到分析层同样是违规的，因此
# 一并纳入扫描。``hardware/`` 与 ``tools/`` 是受控 smoke/工具脚本，不在正式导入面上。
RUNTIME_PACKAGES = ("analysis", "app", "application", "domain", "infrastructure", "instrument", "presentation")
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

# 写入类 API：一旦出现在生产代码里，就说明某处正在落盘。``write_text``/``write_bytes``
# 只在载荷来自 ``json.dumps`` 时才算 JSON 写入点，其余（CSV/HTML/报告）不在本步骤范围。
FILE_WRITE_APIS = {
    "write_text",
    "write_bytes",
    "write_json_atomic",
    "to_csv",
    "FileHandler",
    "RotatingFileHandler",
    "NamedTemporaryFile",
    "mkstemp",
}
QUALIFIED_FILE_WRITE_APIS = {
    "json.dump",
    "shutil.copy",
    "shutil.copy2",
    "shutil.copyfile",
    "shutil.move",
    "os.replace",
    "os.rename",
}

CATEGORY_USER_CONFIGURATION = "user-configuration"
CATEGORY_RUN_SNAPSHOT = "run-snapshot"
CATEGORY_OTHER_JSON = "other-json"

# 生产代码里全部 JSON 文档写入调用点 -> (用途说明, 类别)。这三类用途刻意不合并：
# 用户配置只能有一处，运行快照留在运行持久化里，其余 JSON 文档各归各的仓储。
JSON_WRITE_SITES = {
    OFFICIAL_CONFIGURATION_FORMAT_SITE: (
        "正式用户配置 config.json（唯一用户配置写入点）",
        CATEGORY_USER_CONFIGURATION,
    ),
    ("infrastructure/config/legacy_power_supply_migration.py", "save_migration_summary"): (
        "旧电源配置迁移摘要（不是配置文件）",
        CATEGORY_OTHER_JSON,
    ),
    ("infrastructure/filesystem/atomic_json.py", "write_json_atomic"): (
        "原子 JSON 写入原语自身的 json.dump",
        CATEGORY_OTHER_JSON,
    ),
    ("infrastructure/persistence/instrument_registry.py", "InstrumentRegistry.save"): (
        "设备表 instruments.json",
        CATEGORY_OTHER_JSON,
    ),
    ("infrastructure/persistence/json_result_repository.py", "save_json_result"): (
        "测量结果明细 JSON",
        CATEGORY_OTHER_JSON,
    ),
    ("infrastructure/persistence/run_directory.py", "save_run_manifest"): (
        "运行 manifest",
        CATEGORY_RUN_SNAPSHOT,
    ),
    ("infrastructure/persistence/run_repository.py", "RunRepository._create_run"): (
        "运行目录 config.json 冻结快照",
        CATEGORY_RUN_SNAPSHOT,
    ),
    ("infrastructure/persistence/run_repository.py", "RunRepository.write_run_json"): (
        "运行阶段结果 JSON",
        CATEGORY_OTHER_JSON,
    ),
    ("collect_baseline.py", "collect_baseline"): ("基线收集清单", CATEGORY_OTHER_JSON),
    ("collect_baseline.py", "collect_batch_baseline"): ("基线批量收集清单", CATEGORY_OTHER_JSON),
    ("release_manifest.py", "main"): ("发布清单", CATEGORY_OTHER_JSON),
}

RUN_SNAPSHOT_MODULES = {
    "infrastructure/persistence/run_directory.py",
    "infrastructure/persistence/run_repository.py",
}

# 引用正式配置路径（``.config_file`` / ``CONFIG_FILE``）的生产作用域 -> 用途。这些作用域
# 自己都不直接写文件：写入只能经配置仓储的 ``save_document``。
CONFIG_PATH_REFERENCE_SITES = {
    ("infrastructure/filesystem/paths.py", ""): "定义默认配置路径（只读常量）",
    ("infrastructure/config/json_io.py", "load_config_file"): "读取默认配置路径",
    (
        "infrastructure/gui_data_service.py",
        "JsonConfigurationAdapter.__init__",
    ): "把配置路径绑定到配置仓储（写入只委托 save_document）",
    (
        "presentation/qt/application_coordinator.py",
        "ApplicationCoordinator.__init__",
    ): "组装界面数据服务与测量配置路径",
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


def _method_node(class_node, name):
    for node in class_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{class_node.name} 中未找到方法 {name}")


def _statements(function_node):
    """返回函数体语句，跳过开头的文档字符串。"""
    body = list(function_node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return body


def _called_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _call_name(node):
    """返回调用的完整名字（``os.replace``、``self.repository.save_document``）。"""
    try:
        return ast.unparse(node.func)
    except Exception:  # noqa: BLE001 - 无法反解析时按“非写入调用”处理
        return ""


def _scopes(tree):
    """返回 ``id(节点) -> 最近外层的 类.函数 / 函数名``（模块级为空串）。

    以 ``id()`` 为键，因此任意节点（调用、属性访问、名字）都能查到所属作用域；
    ``ast.walk`` 复用同一批对象，键可直接查询。
    """
    owners = {}

    def walk(node, scope):
        for child in ast.iter_child_nodes(node):
            owners[id(child)] = scope
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                walk(child, f"{scope}.{child.name}" if scope else child.name)
                continue
            walk(child, scope)

    walk(tree, "")
    return owners


def _scope_of(owners, node):
    return owners.get(id(node), "")


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
        owners = _scopes(tree)
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
                writers.add((_relative(source), _scope_of(owners, node)))
    return writers


def _opened_for_writing(node):
    """``open(...)`` / ``path.open(...)`` 是否以写模式打开。"""
    name = _call_name(node)
    positional = 1 if name in {"open", "io.open"} else 0
    mode = node.args[positional] if len(node.args) > positional else None
    for keyword in node.keywords:
        if keyword.arg == "mode":
            mode = keyword.value
    return (
        isinstance(mode, ast.Constant)
        and isinstance(mode.value, str)
        and any(flag in mode.value for flag in "wax")
    )


def _write_api(node):
    """返回该调用使用的落盘 API 名；不落盘时返回 ``None``。"""
    name = _call_name(node)
    if not name:
        return None
    tail = name.rsplit(".", 1)[-1]
    if tail in FILE_WRITE_APIS:
        return name
    if name in QUALIFIED_FILE_WRITE_APIS:
        return name
    if tail == "open" and _opened_for_writing(node):
        return name
    return None


def _is_json_write(node):
    """是否是写入 JSON 文档的调用（``json.dump`` / 原子原语 / ``json.dumps`` 落盘）。

    ``write_text`` / ``write_bytes`` 只在载荷来自 ``*.dumps(...)`` 时才算 JSON 写入点；
    ``import json as j`` 这类别名无法静态还原，因此按调用名后缀判断（略偏保守）。
    """
    name = _call_name(node)
    if name in {"json.dump", "write_json_atomic"}:
        return True
    if name.rsplit(".", 1)[-1] in {"write_text", "write_bytes"}:
        return any(
            isinstance(argument, ast.Call)
            and _call_name(argument).rsplit(".", 1)[-1] == "dumps"
            for argument in node.args
        )
    return False


def _call_sites(predicate):
    """返回生产代码里满足 ``predicate`` 的调用点 ``(模块, 作用域)`` 集合。"""
    sites = set()
    for source in _production_sources():
        tree = _tree(source)
        owners = _scopes(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and predicate(node):
                sites.add((_relative(source), _scope_of(owners, node)))
    return sites


def _file_write_sites():
    return _call_sites(lambda node: _write_api(node) is not None)


def _json_write_sites():
    return _call_sites(_is_json_write)


def _config_path_reference_sites():
    """引用正式配置路径（``.config_file`` / ``CONFIG_FILE``）的 ``(模块, 作用域)``。"""
    sites = set()
    for source in _production_sources():
        tree = _tree(source)
        owners = _scopes(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "config_file":
                sites.add((_relative(source), _scope_of(owners, node)))
            elif isinstance(node, ast.Name) and node.id == "CONFIG_FILE":
                sites.add((_relative(source), _scope_of(owners, node)))
    return sites


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
        adapter_writes = sorted(
            site
            for site in _file_write_sites()
            if site[0] == _relative(ADAPTER_SOURCE)
            and site[1].startswith("JsonConfigurationAdapter")
        )
        self.assertEqual(
            adapter_writes,
            [],
            "适配器的方法体内不得出现任何落盘 API：写入只能经配置仓储的 save_document",
        )

    def test_repository_exposes_only_one_persistence_method(self):
        repository = _class_node(REPOSITORY_SOURCE, "JsonConfigurationRepository")
        persistence_methods = {
            name
            for name in _method_names(repository)
            if not name.startswith("_") and name not in {"load", "load_for_run"}
        }
        self.assertEqual(
            persistence_methods,
            {"save_document"},
            "配置仓储只允许一个持久化方法；第二个读写入口会让落盘/读取路径重新分叉",
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

    def test_every_json_write_site_is_registered_with_a_purpose(self):
        self.assertEqual(
            _json_write_sites(),
            set(JSON_WRITE_SITES),
            "生产代码里每一处 JSON 文档写入都必须登记用途与类别；新增写入点先确认它"
            "不是第二条用户配置写入路径",
        )

    def test_only_the_configuration_repository_writes_the_user_configuration(self):
        user_configuration_sites = {
            site
            for site, (_purpose, category) in JSON_WRITE_SITES.items()
            if category == CATEGORY_USER_CONFIGURATION
        }
        self.assertEqual(
            user_configuration_sites,
            {OFFICIAL_CONFIGURATION_FORMAT_SITE},
            "正式用户配置只允许一处 JSON 写入点，且必须是配置仓储的 save_document",
        )

    def test_run_snapshot_writes_are_registered_only_to_run_persistence(self):
        snapshot_modules = {
            site[0]
            for site, (_purpose, category) in JSON_WRITE_SITES.items()
            if category == CATEGORY_RUN_SNAPSHOT
        }
        self.assertEqual(
            snapshot_modules,
            RUN_SNAPSHOT_MODULES,
            "运行快照/清单写入只属于运行仓储与运行目录布局，不能出现在配置写入路径上",
        )
        self.assertTrue(
            snapshot_modules <= set(ATOMIC_WRITE_IMPORTERS),
            "运行快照写入模块必须在原子写入用途清单里登记",
        )

    def test_the_user_configuration_path_is_referenced_only_by_registered_roles(self):
        self.assertEqual(
            _config_path_reference_sites(),
            set(CONFIG_PATH_REFERENCE_SITES),
            "正式配置路径只允许被登记过的少量作用域引用；新增引用必须先说明它是读、写还是"
            "只做路径绑定",
        )

    def test_no_scope_that_touches_the_configuration_path_writes_files(self):
        offenders = sorted(_file_write_sites() & set(CONFIG_PATH_REFERENCE_SITES))
        self.assertEqual(
            offenders,
            [],
            "引用正式配置路径的作用域不得自己落盘：写入只能经配置仓储的 save_document",
        )

    def test_adapter_save_is_a_single_delegation_to_the_repository(self):
        adapter = _class_node(ADAPTER_SOURCE, "JsonConfigurationAdapter")
        body = _statements(_method_node(adapter, "save"))
        self.assertEqual(len(body), 1, "适配器的 save 只允许一条委托语句")
        call = body[0].value if isinstance(body[0], ast.Expr) else None
        self.assertIsInstance(call, ast.Call, "适配器的 save 只允许一次调用")
        self.assertEqual(_call_name(call), "self.repository.save_document")
        self.assertEqual(len(call.args), 2, "委托必须显式传入配置文件路径与配置文档")

    def test_migration_module_writes_only_its_summary_document(self):
        module = "infrastructure/config/legacy_power_supply_migration.py"
        self.assertEqual(
            {scope for path, scope in _file_write_sites() if path == module},
            {"save_migration_summary"},
            "迁移器写回配置必须委托 save_document，自己只写迁移摘要文件",
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
        owners = _scopes(tree)
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        self.assertEqual([_scope_of(owners, node) for node in calls], ["save"] * 3)
        formatted = [
            node
            for node in calls
            if len(node.keywords) == 2
            and {keyword.arg for keyword in node.keywords} == set(OFFICIAL_FORMAT_KWARGS)
        ]
        self.assertEqual(len(formatted), 2, "两条带全格式参数的调用应被锁定，第三条不算")

    def test_scope_owner_tracks_class_and_method(self):
        tree = ast.parse("class Repo:\n    def save(self):\n        write_json_atomic(p, d)\n")
        owners = _scopes(tree)
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        self.assertEqual([_scope_of(owners, node) for node in calls], ["Repo.save"])

    def test_json_write_detector_requires_json_payload_not_plain_text(self):
        tree = ast.parse(
            "def save():\n"
            "    path.write_text(json.dumps(document), encoding='utf-8')\n"
            "    path.write_text('纯文本报告', encoding='utf-8')\n"
            "    json.dump(document, handle)\n"
            "    write_json_atomic(path, document)\n"
        )
        sites = {_scope_of(_scopes(tree), node) for node in ast.walk(tree) if isinstance(node, ast.Call)}
        writes = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and _is_json_write(node)
        ]
        self.assertEqual(len(writes), 3, "写 JSON 的三条调用应被识别，纯文本写入不算")
        self.assertEqual(sites, {"save"})

    def test_write_api_detector_covers_text_json_and_write_modes(self):
        tree = ast.parse(
            "def save():\n"
            "    path.write_text('x')\n"
            "    json.dump({}, handle)\n"
            "    destination.open('w')\n"
            "    os.replace(temporary, target)\n"
            "    shutil.copy2(source, target)\n"
            "    with Path(p).open('r') as handle:\n"
            "        handle.read()\n"
        )
        detected = sorted(
            _write_api(node)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and _write_api(node) is not None
        )
        self.assertEqual(
            detected,
            ["destination.open", "json.dump", "os.replace", "path.write_text", "shutil.copy2"],
            "写入类 API 必须被识别；只读 open 不得计入",
        )

    def test_statements_skips_the_docstring(self):
        tree = ast.parse('def save(self):\n    """doc"""\n    return 1\n')
        function = tree.body[0]
        self.assertEqual(len(_statements(function)), 1)
        self.assertIsInstance(_statements(function)[0], ast.Return)


if __name__ == "__main__":
    unittest.main()
