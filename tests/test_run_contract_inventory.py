r"""门禁：《技术债分阶段重构计划》步骤 4 的无运行契约调用方清单。

本文件与[无运行契约调用方清单](../docs/无运行契约调用方清单.md)一一对应，冻结四类事实：

1. **双向一致**：清单第 2.2、3.1、4 节的编号集合与下面的基线集合完全相等（既不允许
   “清单删了代码还在”，也不允许“清单新增了基线没登记”），且编号不得重复；
2. **生产调用点**：测量链路只能携带正式运行契约进入硬件装配；测量工厂的 ``prepared_run``
   必须是必填关键字参数，调用点必须先经 ``require_prepared_run()`` 校验；
3. **仅测试调用点**：仍然依赖无契约回退的测试用例按文件、按“无 recorder 的请求构造次数”冻结；
4. **底层无契约分支**：为无契约对象保留的分支与默认值以“符号 + 标记 + 出现次数”冻结
   （只减不增；减少时必须同步更新清单与本基线）。

唯一生产契约是“每次测量绑定同一个 ``runs/<run_id>/`` 运行目录（``config.json`` +
``manifest.json`` + ``inputs/hardware_config.json``）”。``prepare_run()`` 是旧兼容前置校验入口，
没有任何生产调用方；硬件/仿真装配只允许经 ``app.gui_runtime`` 这一个生产组合根进入
``instrument.measurement_factory``。

验证方式：

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" -m unittest tests.test_run_contract_inventory -v
```
"""

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
INVENTORY_DOC = ROOT / "docs" / "无运行契约调用方清单.md"
INVENTORY_GATE = "tests.test_run_contract_inventory"

# 正式 production code 的导入面：无契约装配不得在这里出现。
RUNTIME_PACKAGES = (
    "app",
    "application",
    "domain",
    "infrastructure",
    "instrument",
    "presentation",
    "analysis",
)
ROOT_MODULES = ("launcher.py", "release_manifest.py")

# 三类测量装配工厂：生产调用必须显式携带 prepared_run，且参数必须必填。
MEASUREMENT_FACTORIES = (
    "create_cable_loss_measurement",
    "create_driver_mapping_measurement",
    "create_amplifier_measurement",
)
# 生产代码里唯一允许的工厂分发形态（worker 注入 + 动态取工厂名，且带 prepared_run）。
WORKER_DISPATCH_MARKER = "create_measurement(self.config_path, prepared_run=prepared"
WORKER_DISPATCH_BASELINE = {"presentation/qt/workers.py": 3}
WORKER_DISPATCH_MODULE = "presentation/qt/workers.py"
WORKER_CONTRACT_CHECK = "require_prepared_run"

# 生产组合根：唯一的装配模块。断言的是“只有一个模块”，不是“只有一次调用”。
COMPOSITION_ROOT = "app/gui_runtime.py"
MEASUREMENT_PORT_CALLS = {COMPOSITION_ROOT: 2}
# 两种装配模式的调用次数：硬件模式连真机，仿真模式不连设备。
MEASUREMENT_PORT_MODES = {"hardware": 1, "simulation": 1}
MEASUREMENT_PORT_DEFINITION = ("instrument/measurement_factory.py", "create_measurement_port")

# 第 4 节：底层无契约分支/默认值。条目身份是 (清单编号, 文件, 符号, 标记)。
NO_CONTRACT_SITES = (
    ("B1", "app/run_context.py", "prepare_run", "write_run_snapshot("),
    (
        "B2",
        "app/run_context.py",
        "PreparedRun.hardware_connection_path",
        "return Path(self.config_path)",
    ),
    ("B3", "app/gui_runtime.py", "_hardware_config_path", "return config_path"),
    ("B4", "app/gui_runtime.py", "_require_stage_inputs", "if run_repository is None:"),
    ("B5", "app/gui_runtime.py", "_preflight_amplifier_inputs", "if run_repository is None:"),
    (
        "B6",
        "app/gui_runtime.py",
        "_ensure_run_snapshots_are_intact",
        "if run_repository is None:",
    ),
    (
        "B7",
        "app/gui_runtime.py",
        "_require_legacy_explicit_inputs",
        "_LEGACY_LOSS_INPUT_REQUIRED",
    ),
    ("B8", "app/gui_runtime.py", "_driver_mode_enabled", "isinstance(configuration, Mapping)"),
    ("B9", "app/gui_runtime.py", "_test_frequencies", "isinstance(configuration, Mapping)"),
    ("B10", "app/gui_runtime.py", "_request_kwargs", "if run_repository is not None"),
    (
        "B11",
        "app/gui_runtime.py",
        "connect_instruments",
        'create_measurement_port(config_path, mode="hardware"',
    ),
    ("B12", "instrument/measurement_factory.py", "create_measurement_port", "config_path is None"),
    (
        "B13",
        "presentation/qt/workers.py",
        "InstrumentWorker",
        "connect_instruments(self.config_path)",
    ),
    (
        "B14",
        "presentation/qt/application_coordinator.py",
        "ApplicationCoordinator.measurement_config_path",
        "return str(self.config_path)",
    ),
    (
        "B15",
        "presentation/qt/application_coordinator.py",
        "ApplicationCoordinator.connect_instruments",
        "if self.prepared_run is None:",
    ),
    (
        "B16",
        "application/measurements/cable_loss.py",
        "CableLossUseCase",
        "if self.stage_recorder is None:",
    ),
    (
        "B17",
        "application/measurements/driver_mapping.py",
        "DriverPowerMappingUseCase",
        "if self.stage_recorder is None:",
    ),
    (
        "B18",
        "application/measurements/amplifier_test.py",
        "AmplifierMeasurementUseCase",
        "if self.stage_recorder is None:",
    ),
    (
        "B19",
        "application/dto/measurement_requests.py",
        "MeasurementRequest",
        'stage_recorder: "RunStageRecorder | None" = None',
    ),
    (
        "B20",
        "presentation/qt/measurement_controller_contract.py",
        "MeasurementCommand",
        "prepared_run: Any = None",
    ),
)
# 归属步骤：步骤 5 只收敛装配入口（B11–B15 的连接与路径回退），其余留给步骤 6 删除。
STEP_FIVE_SITES = ("B11", "B12", "B13", "B14", "B15")

# 第 2.2 节：缺少运行契约的生产调用点（符号存在性 + 归属步骤 + 文档登记）。
NO_CONTRACT_CALL_SITES = (
    ("X1", "app/run_context.py", "prepare_run", "步骤 6"),
    ("X2", "app/gui_runtime.py", "connect_instruments", "步骤 5"),
    ("X3", "instrument/measurement_factory.py", "create_measurement_port", "步骤 5"),
    ("X4", "presentation/qt/workers.py", "InstrumentWorker", "步骤 5"),
    (
        "X5",
        "presentation/qt/application_coordinator.py",
        "ApplicationCoordinator.connect_instruments",
        "步骤 5",
    ),
    (
        "X6",
        "presentation/qt/application_coordinator.py",
        "ApplicationCoordinator.measurement_config_path",
        "步骤 5",
    ),
    ("X7", "presentation/qt/measurement_controller_contract.py", "MeasurementCommand", "步骤 5"),
)

# 无契约标记只减不增：按文件固定当前出现次数（注释/docstring 里的说法不计入契约，但计数按原文）。
NO_CONTRACT_MARKER_COUNTS = {
    "app/gui_runtime.py": {
        "if run_repository is None:": 3,
        "if run_repository is not None": 4,
        "isinstance(configuration, Mapping)": 2,
        "create_measurement_port(": 2,
        "connect_instruments(": 4,
    },
    "app/run_context.py": {"if run_repository is not None": 2},
    "presentation/qt/application_coordinator.py": {"if self.prepared_run is None:": 2},
    "application/measurements/cable_loss.py": {"if self.stage_recorder is None:": 2},
    "application/measurements/driver_mapping.py": {"if self.stage_recorder is None:": 2},
    "application/measurements/amplifier_test.py": {"if self.stage_recorder is None:": 2},
}

# 第 3.1 节：依赖无契约回退的测试调用点（文件 -> ((标记, 期望次数), ...)）。
TEST_ONLY_FILE_MARKERS = {
    "tests/test_gui_runtime.py": (
        ("def _prepared_run(", 1),
        ("_prepared_run()", 9),
        ("_LEGACY_LOSS_PATH", 9),
        ("test_legacy_assembly_still_uses_the_caller_config_path", 1),
    ),
    "tests/test_run_service.py": (
        ("test_legacy_prepare_run_remains_non_contract_compatible", 1),
        ("prepare_run(", 1),
    ),
    "tests/test_offline_completion.py": (("prepare_run(", 1),),
    "tests/integration/test_gui_two_phase_flow.py": (
        ("test_legacy_flow_still_uses_global_config", 1),
        ("test_measurement_config_path_prefers_frozen_run_config", 1),
    ),
    "tests/test_measurement_requests.py": (("test_legacy_prepared_run_has_no_stage_recorder", 1),),
    "tests/test_cable_loss_use_case.py": (("CableLossMeasurementRequest(", 5),),
    "tests/test_driver_mapping_use_case.py": (("DriverPowerMappingRequest(", 1),),
    "tests/test_amplifier_use_case.py": (("AmplifierMeasurementRequest(", 2),),
    "tests/test_sleep_injection.py": (("MeasurementRequest(", 1),),
}

# 第 3.1 节条目身份：(清单编号, 归属步骤, 对应文件, 该条目在文件里的代表标记)。
TEST_ONLY_CALL_SITES = (
    ("T1", "步骤 6", ("tests/test_gui_runtime.py",), "_prepared_run()"),
    (
        "T2",
        "步骤 6",
        ("tests/test_gui_runtime.py",),
        "test_legacy_assembly_still_uses_the_caller_config_path",
    ),
    (
        "T3",
        "步骤 6",
        ("tests/test_gui_runtime.py",),
        "test_all_measurement_factories_inject_the_same_external_port",
    ),
    (
        "T4",
        "步骤 6",
        ("tests/test_run_service.py",),
        "test_legacy_prepare_run_remains_non_contract_compatible",
    ),
    ("T5", "步骤 6", ("tests/test_offline_completion.py",), "prepare_run("),
    (
        "T6",
        "步骤 5/6",
        ("tests/integration/test_gui_two_phase_flow.py",),
        "test_legacy_flow_still_uses_global_config",
    ),
    (
        "T7",
        "步骤 5/6",
        ("tests/integration/test_gui_two_phase_flow.py",),
        "test_measurement_config_path_prefers_frozen_run_config",
    ),
    (
        "T8",
        "步骤 6",
        (
            "tests/test_measurement_requests.py",
            "tests/test_cable_loss_use_case.py",
            "tests/test_driver_mapping_use_case.py",
            "tests/test_amplifier_use_case.py",
            "tests/test_sleep_injection.py",
        ),
        "stage_recorder",
    ),
)

# T8 的可执行形式：仍在不注入 stage_recorder 的情况下构造测量请求的次数（迁移后必须下降并同步基线）。
NO_CONTRACT_REQUEST_BASELINE = {
    "tests/test_measurement_requests.py": 2,
    "tests/test_cable_loss_use_case.py": 5,
    "tests/test_driver_mapping_use_case.py": 1,
    "tests/test_amplifier_use_case.py": 2,
    "tests/test_sleep_injection.py": 1,
}

_SOURCE_CACHE: dict[Path, str] = {}
_TREE_CACHE: dict[Path, ast.AST] = {}


def _source(path: Path) -> str:
    resolved = Path(path)
    text = _SOURCE_CACHE.get(resolved)
    if text is None:
        text = resolved.read_text(encoding="utf-8-sig")
        _SOURCE_CACHE[resolved] = text
    return text


def _tree(path: Path) -> tuple[str, ast.AST]:
    resolved = Path(path)
    text = _source(resolved)
    tree = _TREE_CACHE.get(resolved)
    if tree is None:
        tree = ast.parse(text, str(resolved))
        _TREE_CACHE[resolved] = tree
    return text, tree


def _production_sources() -> list[Path]:
    sources = []
    for package in RUNTIME_PACKAGES:
        sources.extend(sorted((ROOT / package).rglob("*.py")))
    for module in ROOT_MODULES:
        path = ROOT / module
        if path.is_file():
            sources.append(path)
    return sorted(set(sources))


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _find_symbol(text: str, dotted: str):
    """按点分名字解析模块级/类内定义；找不到返回 ``None``。"""
    nodes = ast.parse(text).body
    node = None
    for part in dotted.split("."):
        node = next(
            (
                item
                for item in nodes
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and item.name == part
            ),
            None,
        )
        if node is None:
            return None
        nodes = node.body
    return node


def _called_name(node: ast.AST) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _doc_rows(document: str, prefix: str) -> list[tuple[str, str]]:
    """收集清单里以 ``| <prefix><数字> |`` 开头的表格行，返回 (编号, 原始行)。"""
    rows = []
    for line in document.splitlines():
        stripped = line.strip()
        marker = f"| {prefix}"
        if not stripped.startswith(marker):
            continue
        identifier = stripped[len("| "):].split("|")[0].strip()
        if not identifier.startswith(prefix) or not identifier[len(prefix):].isdigit():
            continue
        rows.append((identifier, stripped))
    return rows


def _requests_without_stage_recorder(path: Path) -> int:
    """统计“未注入 stage_recorder 的测量请求构造”次数。"""
    _text, tree = _tree(path)
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _called_name(node) or ""
        if not name.endswith("Request"):
            continue
        if "stage_recorder" not in {keyword.arg for keyword in node.keywords}:
            count += 1
    return count


class InventoryParityTests(unittest.TestCase):
    """清单与基线双向一致：编号集合相等、无重复、文件/符号/归属步骤可核对。"""

    def setUp(self):
        self.document = _source(INVENTORY_DOC)

    def _assert_parity(self, prefix: str, expected_ids, label: str):
        rows = _doc_rows(self.document, prefix)
        ids = [identifier for identifier, _raw in rows]
        duplicated = sorted({identifier for identifier in ids if ids.count(identifier) > 1})
        self.assertEqual(
            duplicated,
            [],
            f"清单{label}存在重复编号: {duplicated}",
        )
        self.assertEqual(
            sorted(ids),
            sorted(expected_ids),
            f"清单{label}与基线不一致：清单多出 {sorted(set(ids) - set(expected_ids))}，"
            f"基线多出 {sorted(set(expected_ids) - set(ids))}；"
            "删除条目要同时改清单与本基线，新增条目同样",
        )
        return dict(rows)

    def test_section_2_2_lists_exactly_the_frozen_call_sites(self):
        rows = self._assert_parity(
            "X", [site[0] for site in NO_CONTRACT_CALL_SITES], "第 2.2 节"
        )
        for site_id, relative, symbol, step in NO_CONTRACT_CALL_SITES:
            with self.subTest(site=site_id):
                raw = rows[site_id]
                self.assertIn(f"`{relative}`", raw)
                self.assertIn(f"`{symbol}`", raw)
                self.assertIn(step, raw)

    def test_section_4_lists_exactly_the_frozen_branches(self):
        rows = self._assert_parity("B", [site[0] for site in NO_CONTRACT_SITES], "第 4 节")
        site_ids = {site[0] for site in NO_CONTRACT_SITES}
        self.assertEqual(
            sorted(set(STEP_FIVE_SITES) - site_ids),
            [],
            "步骤 5 条目已不在清单里，请同步 STEP_FIVE_SITES",
        )
        for site_id, relative, symbol, _marker in NO_CONTRACT_SITES:
            with self.subTest(site=site_id):
                raw = rows[site_id]
                self.assertIn(f"`{relative}`", raw)
                self.assertIn(f"`{symbol}`", raw)
                expected_step = "步骤 5" if site_id in STEP_FIVE_SITES else "步骤 6"
                self.assertIn(expected_step, raw)

    def test_section_3_1_lists_exactly_the_frozen_test_call_sites(self):
        rows = self._assert_parity(
            "T", [site[0] for site in TEST_ONLY_CALL_SITES], "第 3.1 节"
        )
        for site_id, step, files, marker in TEST_ONLY_CALL_SITES:
            with self.subTest(site=site_id):
                raw = rows[site_id]
                self.assertIn(step, raw)
                self.assertIn(marker, raw)
                for relative in files:
                    self.assertIn(relative, raw)
                    self.assertIn(
                        relative,
                        TEST_ONLY_FILE_MARKERS,
                        f"{site_id} 登记的文件 {relative} 没有对应的文件基线",
                    )


class NoContractSiteTests(unittest.TestCase):
    """第 4 节的无契约分支必须以“符号 + 标记”的身份继续存在。"""

    def test_documented_no_contract_sites_still_exist(self):
        missing = []
        for site_id, relative, symbol, marker in NO_CONTRACT_SITES:
            with self.subTest(site=site_id):
                path = ROOT / relative
                if not path.is_file():
                    missing.append(f"{site_id}: {relative} 不存在")
                    continue
                text, _tree_node = _tree(path)
                node = _find_symbol(text, symbol)
                if node is None:
                    missing.append(f"{site_id}: {relative}::{symbol} 已不存在")
                    continue
                segment = ast.get_source_segment(text, node) or ""
                if marker not in segment:
                    missing.append(f"{site_id}: {relative}::{symbol} 缺少标记 {marker!r}")
        self.assertEqual(
            missing,
            [],
            "无契约分支已变化，请按《技术债分阶段重构计划》步骤 5/6 同步更新清单与本基线: "
            + "; ".join(missing),
        )

    def test_no_contract_call_sites_are_still_the_documented_ones(self):
        missing = []
        for site_id, relative, symbol, _step in NO_CONTRACT_CALL_SITES:
            text, _tree_node = _tree(ROOT / relative)
            if _find_symbol(text, symbol) is None:
                missing.append(f"{site_id}: {relative}::{symbol}")
        self.assertEqual(
            missing,
            [],
            "无契约调用点已迁移，请同步更新清单第 2.2 节与本基线: " + "; ".join(missing),
        )

    def test_no_contract_marker_counts_do_not_grow(self):
        drifted = []
        for relative, markers in NO_CONTRACT_MARKER_COUNTS.items():
            text = _source(ROOT / relative)
            for marker, expected in markers.items():
                actual = text.count(marker)
                if actual != expected:
                    drifted.append(f"{relative}: {marker!r} 期望 {expected} 处，实际 {actual} 处")
        self.assertEqual(
            drifted,
            [],
            "无契约分支的出现次数必须与清单一致（只减不增）；"
            "迁移或删除后请同步更新清单第 4 节与本基线: " + "; ".join(drifted),
        )


class ProductionContractTests(unittest.TestCase):
    """生产测量链路只能通过正式运行契约进入硬件装配。"""

    def test_legacy_preflight_entry_has_no_production_caller(self):
        offenders = []
        for path in _production_sources():
            text, tree = _tree(path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and _called_name(node) == "prepare_run":
                    offenders.append(f"{_relative(path)}:{node.lineno} 调用了 prepare_run")
                elif isinstance(node, ast.ImportFrom) and any(
                    alias.name == "prepare_run" for alias in node.names
                ):
                    offenders.append(f"{_relative(path)}:{node.lineno} 导入了 prepare_run")
                elif isinstance(node, ast.Attribute) and node.attr == "prepare_run":
                    offenders.append(f"{_relative(path)}:{node.lineno} 引用了 prepare_run")
        self.assertEqual(
            offenders,
            [],
            "prepare_run 是无契约前置校验入口，生产代码不得使用: " + "; ".join(offenders),
        )
        definitions = [
            _relative(path)
            for path in _production_sources()
            if any(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "prepare_run"
                for node in ast.walk(_tree(path)[1])
            )
        ]
        self.assertEqual(definitions, ["app/run_context.py"])

    def test_measurement_port_is_only_assembled_by_the_composition_root(self):
        calls = {}
        modes = {"hardware": 0, "simulation": 0}
        for path in _production_sources():
            _text, tree = _tree(path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if _called_name(node) != "create_measurement_port":
                    continue
                calls[_relative(path)] = calls.get(_relative(path), 0) + 1
                for keyword in node.keywords:
                    if keyword.arg != "mode":
                        continue
                    value = getattr(keyword.value, "value", None)
                    if value in modes:
                        modes[value] += 1
        self.assertEqual(
            calls,
            MEASUREMENT_PORT_CALLS,
            "测量端口只允许经 app.gui_runtime 这一个生产组合根装配；新增调用点属于架构变更，"
            "请先更新清单第 1.1 节与正式 API 清单",
        )
        self.assertEqual(
            modes,
            MEASUREMENT_PORT_MODES,
            "组合根里必须各有一处硬件模式与仿真模式装配：硬件模式连真机并受运行契约约束，"
            "仿真模式不连设备且不得冒充生产测量入口",
        )

    def test_measurement_port_definition_still_takes_a_config_path(self):
        relative, symbol = MEASUREMENT_PORT_DEFINITION
        text, _tree_node = _tree(ROOT / relative)
        node = _find_symbol(text, symbol)
        self.assertIsNotNone(node, f"{relative} 缺少装配入口 {symbol}")
        self.assertIsInstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        self.assertEqual(
            [argument.arg for argument in node.args.args][:1],
            ["config_path"],
            f"{symbol} 仍以 config_path 为第一参数：步骤 5 需把硬件模式收敛为 PreparedRun",
        )

    def test_measurement_factories_require_a_prepared_run(self):
        for name in MEASUREMENT_FACTORIES:
            with self.subTest(factory=name):
                text, _tree_node = _tree(ROOT / "app" / "gui_runtime.py")
                node = _find_symbol(text, name)
                self.assertIsNotNone(node, f"app/gui_runtime.py 缺少 {name}")
                self.assertIsInstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                self.assertEqual(
                    [argument.arg for argument in node.args.args][:1],
                    ["config_path"],
                    f"{name} 的首个位置参数仍应是 config_path（步骤 5 收敛硬件来源）",
                )
                keyword_only = [argument.arg for argument in node.args.kwonlyargs]
                self.assertIn(
                    "prepared_run",
                    keyword_only,
                    f"{name} 必须把 prepared_run 声明为关键字参数",
                )
                index = keyword_only.index("prepared_run")
                self.assertIsNone(
                    node.args.kw_defaults[index],
                    f"{name} 的 prepared_run 不得有默认值：无契约调用必须在调用点就失败",
                )

    def test_measurement_factories_are_only_dispatched_with_a_prepared_run(self):
        offenders = []
        for path in _production_sources():
            _text, tree = _tree(path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if _called_name(node) not in MEASUREMENT_FACTORIES:
                    continue
                keywords = {keyword.arg for keyword in node.keywords}
                if "prepared_run" not in keywords:
                    offenders.append(f"{_relative(path)}:{node.lineno} 缺少 prepared_run")
        self.assertEqual(
            offenders,
            [],
            "生产代码调用测量装配入口必须显式携带 prepared_run: " + "; ".join(offenders),
        )
        for relative, expected in WORKER_DISPATCH_BASELINE.items():
            with self.subTest(file=relative):
                self.assertEqual(_source(ROOT / relative).count(WORKER_DISPATCH_MARKER), expected)

    def test_dispatch_is_preceded_by_the_run_contract_check(self):
        """worker 必须先经 require_prepared_run 校验，再装配测量用例。"""
        _text, tree = _tree(ROOT / WORKER_DISPATCH_MODULE)
        offenders = []
        checked = 0
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            dispatch = [
                child.lineno
                for child in ast.walk(node)
                if isinstance(child, ast.Call) and _called_name(child) == "create_measurement"
            ]
            if not dispatch:
                continue
            checked += 1
            checks = [
                child.lineno
                for child in ast.walk(node)
                if isinstance(child, ast.Call) and _called_name(child) == WORKER_CONTRACT_CHECK
            ]
            if not checks:
                offenders.append(f"{node.name} 没有调用 {WORKER_CONTRACT_CHECK}")
            elif min(checks) > min(dispatch):
                offenders.append(f"{node.name} 在运行契约校验前就装配了测量用例")
        self.assertEqual(checked, 3, "worker 分发点数量变化，请同步更新清单第 2.1 节")
        self.assertEqual(
            offenders,
            [],
            "测量 worker 必须先校验运行契约再装配用例: " + "; ".join(offenders),
        )


class TestOnlyCallSiteTests(unittest.TestCase):
    """第 3.1 节：仍依赖无契约回退的测试调用点按文件冻结。"""

    def test_test_only_no_contract_sites_are_frozen(self):
        drifted = []
        for relative, markers in TEST_ONLY_FILE_MARKERS.items():
            path = ROOT / relative
            if not path.is_file():
                drifted.append(f"{relative} 不存在")
                continue
            text = _source(path)
            for marker, expected in markers:
                actual = text.count(marker)
                if actual != expected:
                    drifted.append(f"{relative}: {marker!r} 期望 {expected} 处，实际 {actual} 处")
        self.assertEqual(
            drifted,
            [],
            "仅测试无契约调用点已变化：迁移为契约替身后请同步更新清单第 3 节与本基线: "
            + "; ".join(drifted),
        )

    def test_requests_without_a_stage_recorder_stay_on_the_baseline(self):
        drifted = []
        for relative, expected in NO_CONTRACT_REQUEST_BASELINE.items():
            path = ROOT / relative
            if not path.is_file():
                drifted.append(f"{relative} 不存在")
                continue
            actual = _requests_without_stage_recorder(path)
            if actual != expected:
                drifted.append(f"{relative}: 期望 {expected} 处，实际 {actual} 处")
        self.assertEqual(
            drifted,
            [],
            "仍在无 stage_recorder 情况下构造测量请求的处数必须与清单一致（只减不增）；"
            "迁移后请同步更新清单第 3.1 节 T8 与本基线: " + "; ".join(drifted),
        )


class InventoryDocumentationTests(unittest.TestCase):
    """清单本身要能说明判定口径、边界和迁移顺序。"""

    def test_inventory_doc_states_the_single_production_contract(self):
        document = _source(INVENTORY_DOC)
        for keyword in (
            "runs/<run_id>/",
            "manifest.json",
            "inputs/hardware_config.json",
            "PreparedRun",
            "hardware_connection_path()",
        ):
            with self.subTest(keyword=keyword):
                self.assertIn(keyword, document)

    def test_inventory_doc_separates_composition_root_and_assembly_modes(self):
        document = _source(INVENTORY_DOC)
        for keyword in (
            "### 1.1",
            "生产组合根",
            "硬件模式",
            "仿真模式",
            "唯一的装配模块",
        ):
            with self.subTest(keyword=keyword):
                self.assertIn(keyword, document)

    def test_inventory_doc_states_the_test_boundary_rule(self):
        document = _source(INVENTORY_DOC)
        for keyword in (
            "### 3.0",
            "契约替身",
            "instrument 层",
            "test_measurement_factory.py",
            "test_multi_supply_connection.py",
        ):
            with self.subTest(keyword=keyword):
                self.assertIn(keyword, document)

    def test_inventory_doc_splits_step_6_into_two_phases(self):
        document = _source(INVENTORY_DOC)
        for keyword in ("6a", "6b", "先迁移测试", "再删除分支"):
            with self.subTest(keyword=keyword):
                self.assertIn(keyword, document)

    def test_inventory_doc_records_the_frozen_technical_decisions(self):
        document = _source(INVENTORY_DOC)
        for keyword in (
            "## 8. 技术决定",
            "D1",
            "D2",
            "D3",
            "D4",
            "构造即满足契约",
            "MeasurementCommand",
            "MeasurementRequest",
        ):
            with self.subTest(keyword=keyword):
                self.assertIn(keyword, document)

    def test_inventory_doc_records_controlled_exceptions(self):
        document = _source(INVENTORY_DOC)
        for keyword in (
            "hardware/new_path_minimal_action_smoke.py",
            "smoke_gate.py",
            "launcher.py",
            "tools/",
        ):
            with self.subTest(keyword=keyword):
                self.assertIn(keyword, document)

    def test_inventory_doc_points_at_this_gate_and_the_repository_gates(self):
        document = _source(INVENTORY_DOC)
        self.assertIn("tests/test_run_contract_inventory.py", document)
        self.assertIn(INVENTORY_GATE, document)


if __name__ == "__main__":
    unittest.main()
