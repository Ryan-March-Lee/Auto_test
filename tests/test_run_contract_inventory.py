r"""门禁：《技术债分阶段重构计划》步骤 4 的无运行契约调用方清单。

本文件与[无运行契约调用方清单](../docs/无运行契约调用方清单.md)一一对应，冻结三类事实：

1. **生产调用点**：测量链路的生产调用点必须携带正式运行契约，清单登记的装配入口不得新增；
2. **仅测试调用点**：仍然依赖无契约回退的测试用例按文件精确冻结，迁移后必须同步收口；
3. **底层无契约分支**：`app/gui_runtime.py`、`app/run_context.py` 等为无契约对象保留的分支以
   “符号 + 标记”身份冻结（只减不增），并被 `PreparedRun` 之外的路径继续引用时立刻失败。

唯一生产契约是“每次测量绑定同一个 `runs/<run_id>/` 运行目录（`config.json` + `manifest.json` +
`inputs/hardware_config.json`）”。`prepare_run()` 是旧兼容前置校验入口，没有任何生产调用方；
硬件装配只允许经 `app.gui_runtime` 一条路径进入 `instrument.measurement_factory`。

步骤 5/6 完成迁移后，删除本文列出的条目并同步更新本基线；基线只允许随清单一起收缩，不允许
在别处新增等价的无契约分支来顶替被删除的一处。

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

# 三类测量装配工厂：生产调用必须显式携带 prepared_run。
MEASUREMENT_FACTORIES = (
    "create_cable_loss_measurement",
    "create_driver_mapping_measurement",
    "create_amplifier_measurement",
)
# 生产代码里唯一允许的工厂分发形态（worker 注入 + 动态取工厂名，且带 prepared_run）。
WORKER_DISPATCH_MARKER = "create_measurement(self.config_path, prepared_run=prepared"
WORKER_DISPATCH_BASELINE = {"presentation/qt/workers.py": 3}

# 生产装配入口：`create_measurement_port()` 只能由 app.gui_runtime 装配层调用。
ASSEMBLY_ENTRY_CALLS = {"app/gui_runtime.py": 2}
ASSEMBLY_ENTRY_DEFINITION = ("instrument/measurement_factory.py", "create_measurement_port")

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

# 第 2.2 节：缺少运行契约的生产调用点（符号存在性 + 文档登记）。
NO_CONTRACT_CALL_SITES = (
    ("X1", "app/run_context.py", "prepare_run"),
    ("X2", "app/gui_runtime.py", "connect_instruments"),
    ("X3", "instrument/measurement_factory.py", "create_measurement_port"),
    ("X4", "presentation/qt/workers.py", "InstrumentWorker"),
    (
        "X5",
        "presentation/qt/application_coordinator.py",
        "ApplicationCoordinator.connect_instruments",
    ),
    (
        "X6",
        "presentation/qt/application_coordinator.py",
        "ApplicationCoordinator.measurement_config_path",
    ),
    ("X7", "presentation/qt/measurement_controller_contract.py", "MeasurementCommand"),
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
TEST_ONLY_NO_CONTRACT_SITES = {
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
}


def _source(path):
    return path.read_text(encoding="utf-8-sig")


def _tree(path):
    text = _source(path)
    return text, ast.parse(text, str(path))


def _production_sources():
    sources = []
    for package in RUNTIME_PACKAGES:
        sources.extend(sorted((ROOT / package).rglob("*.py")))
    for module in ROOT_MODULES:
        path = ROOT / module
        if path.is_file():
            sources.append(path)
    return sorted(set(sources))


def _relative(path):
    return path.relative_to(ROOT).as_posix()


def _find_symbol(text, dotted):
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


def _called_name(node):
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


class NoContractSiteTests(unittest.TestCase):
    """第 4 节的无契约分支必须以“符号 + 标记”的身份继续存在，并与清单一一对应。"""

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
                else:
                    self.assertIn(marker, segment)
        self.assertEqual(
            missing,
            [],
            "无契约分支已变化，请按《技术债分阶段重构计划》步骤 5/6 同步更新清单与本基线: "
            + "; ".join(missing),
        )

    def test_inventory_doc_lists_every_no_contract_site(self):
        document = _source(INVENTORY_DOC)
        for site_id, relative, symbol, _marker in NO_CONTRACT_SITES:
            with self.subTest(site=site_id):
                row = next(
                    (line for line in document.splitlines() if line.startswith(f"| {site_id} |")),
                    None,
                )
                self.assertIsNotNone(row, f"清单缺少第 4 节条目 {site_id}")
                self.assertIn(f"`{symbol}`", row)
                self.assertIn(f"`{relative}`", row)
        for site_id, relative, symbol in NO_CONTRACT_CALL_SITES:
            with self.subTest(call_site=site_id):
                row = next(
                    (line for line in document.splitlines() if line.startswith(f"| {site_id} |")),
                    None,
                )
                self.assertIsNotNone(row, f"清单缺少第 2.2 节条目 {site_id}")
                self.assertIn(f"`{symbol}`", row)
                self.assertIn(f"`{relative}`", row)

    def test_no_contract_call_sites_are_still_the_documented_ones(self):
        missing = []
        for site_id, relative, symbol in NO_CONTRACT_CALL_SITES:
            text, _tree_node = _tree(ROOT / relative)
            if _find_symbol(text, symbol) is None:
                missing.append(f"{site_id}: {relative}::{symbol}")
        self.assertEqual(
            missing,
            [],
            "无契约调用点已迁移，请同步更新清单第 2.2 节: " + "; ".join(missing),
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

    def test_hardware_assembly_has_a_single_production_entry(self):
        calls = {}
        for path in _production_sources():
            _text, tree = _tree(path)
            count = sum(
                1
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and _called_name(node) == "create_measurement_port"
            )
            if count:
                calls[_relative(path)] = count
        self.assertEqual(
            calls,
            ASSEMBLY_ENTRY_CALLS,
            "硬件装配只允许经 app.gui_runtime 的生产装配层调用；新增调用点属于架构变更，"
            "请先更新清单与正式 API 清单",
        )
        relative, symbol = ASSEMBLY_ENTRY_DEFINITION
        text, _tree_node = _tree(ROOT / relative)
        node = _find_symbol(text, symbol)
        self.assertIsNotNone(node, f"{relative} 缺少装配入口 {symbol}")
        self.assertIsInstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        self.assertEqual(
            [argument.arg for argument in node.args.args][:1],
            ["config_path"],
            f"{symbol} 仍以 config_path 为第一参数：步骤 5 需收敛为 PreparedRun",
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


class TestOnlyCallSiteTests(unittest.TestCase):
    """第 3.1 节：仍依赖无契约回退的测试调用点按文件冻结。"""

    def test_test_only_no_contract_sites_are_frozen(self):
        drifted = []
        for relative, markers in TEST_ONLY_NO_CONTRACT_SITES.items():
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


class InventoryDocumentationTests(unittest.TestCase):
    """清单本身要能说明为什么列举、哪些是受控例外。"""

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

    def test_inventory_doc_points_at_this_gate(self):
        document = _source(INVENTORY_DOC)
        self.assertIn("tests/test_run_contract_inventory.py", document)


if __name__ == "__main__":
    unittest.main()
