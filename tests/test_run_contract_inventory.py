r"""门禁：《技术债分阶段重构计划》步骤 4 的无运行契约调用方清单。

本文件与[无运行契约调用方清单](../docs/无运行契约调用方清单.md)一一对应，冻结六类事实：

1. **双向一致且分节严格**：清单第 2.2、3.1、4 节的编号集合与下面的基线集合完全相等
   （既不允许“清单删了代码还在”，也不允许“清单新增了基线没登记”），编号不得重复；
   编号也只能出现在属于它的那一节里，出现在别处同样是清单损坏；
2. **条目身份**：每条无契约分支按“文件 + 符号 + 标记”核对，标记必须是该符号源码里的原文，
   同一符号的多个无契约出口都要列进标记列表，避免“登记了一个出口、漏了另一个”；
3. **语义计数**：底层为无契约对象保留的判据按 **AST 语义** 冻结（``x is None`` /
   ``x is not None`` / ``isinstance(..., Mapping)`` / 指定函数调用），不按文本出现次数，
   因此注释、docstring、字符串拼接不会误伤，新增等价分支也无法藏起来；
4. **生产调用点**：测量链路只能携带正式运行契约进入硬件装配；测量工厂的 ``prepared_run``
   必须是必填关键字参数；调用点必须先经 ``require_prepared_run()`` 校验并把它校验过的对象
   传给装配入口，且监测符号不得经 ``import ... as`` 别名或动态 ``getattr`` 绕过检查；
5. **契约判据单源**：``has_run_contract`` 只有一处实现（``application.run_service``），
   ``PreparedRun.has_run_contract`` 与 ``is_valid_prepared_run`` 只做委托；正式判据要求运行
   标识、运行目录、运行仓储与可调用的冻结快照解析入口，且对契约运行绝无“回退到调用方
   全局配置”的出口（失败断言）；
6. **仅测试调用点**：仍然依赖无契约回退的测试用例按文件、按“无 recorder 的请求构造次数”冻结，
   迁移后必须下降并同步基线。

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
import re
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
WORKER_DISPATCH_MODULE = "presentation/qt/workers.py"
WORKER_DISPATCH_MARKER = "create_measurement(self.config_path, prepared_run=prepared"
WORKER_DISPATCH_BASELINE = {WORKER_DISPATCH_MODULE: 3}
WORKER_CONTRACT_CHECK = "require_prepared_run"
# 动态分发只在 _default_factory 里取名字，因此每个工厂名必须是恰好一处字符串字面量。
WORKER_FACTORY_LITERALS = {name: 1 for name in MEASUREMENT_FACTORIES}

# 生产组合根：唯一的装配模块。断言的是“只有一个模块”，不是“只有一次调用”。
COMPOSITION_ROOT = "app/gui_runtime.py"
# 受控例外（清单第 5 节）：真机冒烟脚本直接调用仪器工厂，不经需要运行契约的连接入口。
CONTROLLED_EXCEPTION_SCRIPT = "hardware/new_path_minimal_action_smoke.py"
MEASUREMENT_PORT_CALLS = {COMPOSITION_ROOT: 2}
# 两种装配模式的调用次数：硬件模式连真机，仿真模式不连设备。
MEASUREMENT_PORT_MODES = {"hardware": 1, "simulation": 1}
MEASUREMENT_PORT_DEFINITION = ("instrument/measurement_factory.py", "create_measurement_port")

# 契约判据必须单源：唯一的判定实现 + 只做委托的正式入口。
CONTRACT_PREDICATE_DEFINITION = ("application/run_service.py", "has_run_contract")
# 判据实现必须覆盖的四项要求（与清单第 8 节 D8 一致）。
CONTRACT_REQUIREMENTS = ("run_id", "run_directory", "run_repository", "hardware_connection_path")
# 委托入口：``PreparedRun.has_run_contract``（对象属性）与 GUI 边界判据。
CONTRACT_PREDICATE_DELEGATES = {
    "app/run_context.py": "PreparedRun.has_run_contract",
    "presentation/qt/run_contract.py": "is_valid_prepared_run",
}
# 其余契约相关判据同样只允许有一个生产定义。
CONTRACT_PREDICATES = {
    "require_prepared_run": "presentation/qt/run_contract.py",
}

# 受监测符号：生产代码不得用 import 别名或动态 getattr 让它们绕过上面的检查。
MONITORED_SYMBOLS = frozenset(
    MEASUREMENT_FACTORIES
    + (
        "connect_instruments",
        "create_measurement_port",
        "prepare_run",
        "require_prepared_run",
        "is_valid_prepared_run",
        "create_measurement",
    )
)
# 动态符号查找（getattr(模块, 变量)）只允许出现在 worker 的分发辅助函数里。
# 只有“从装配模块动态取名字”属于绕过检查的路径；getattr(对象, 字段名) 之类的
# 通用写法与装配无关，不进入本基线。
MONITORED_DISPATCH_MODULES = ("gui_runtime", "run_context", "measurement_factory")
DYNAMIC_LOOKUP_BASELINE = {WORKER_DISPATCH_MODULE: 1}

# 第 4 节：底层无契约分支/默认值。身份是 (清单编号, 文件, 符号, 标记元组)：
# 标记必须全部出现在该符号源码里，一个符号存在多个无契约出口时全部列出。
NO_CONTRACT_SITES = (
    ("B1", "app/run_context.py", "prepare_run", ("write_run_snapshot(",)),
    (
        "B2",
        "app/run_context.py",
        "PreparedRun.hardware_connection_path",
        ("return Path(self.config_path)",),
    ),
    (
        "B3",
        "app/gui_runtime.py",
        "_hardware_config_path",
        # 两个出口：调用方路径回退，以及“有 hardware_config_path 但没有校验入口”时
        # 直接返回未校验快照。后者同样绕过了 hardware_connection_path() 的 manifest 校验。
        ("return config_path", "return str(frozen)"),
    ),
    ("B4", "app/gui_runtime.py", "_require_stage_inputs", ("if run_repository is None:",)),
    (
        "B5",
        "app/gui_runtime.py",
        "_preflight_amplifier_inputs",
        ("if run_repository is None:",),
    ),
    (
        "B6",
        "app/gui_runtime.py",
        "_ensure_run_snapshots_are_intact",
        ("if run_repository is None:",),
    ),
    (
        "B7",
        "app/gui_runtime.py",
        "_require_legacy_explicit_inputs",
        ("_LEGACY_LOSS_INPUT_REQUIRED", "_LEGACY_DRIVER_MAPPING_REQUIRED"),
    ),
    (
        "B8",
        "app/gui_runtime.py",
        "_driver_mode_enabled",
        # 外层判 configuration、内层判 mode：只登记外层会漏掉内层探测。
        ("isinstance(configuration, Mapping)", "isinstance(mode, Mapping)"),
    ),
    ("B9", "app/gui_runtime.py", "_test_frequencies", ("isinstance(configuration, Mapping)",)),
    ("B10", "app/gui_runtime.py", "_request_kwargs", ("if run_repository is not None",)),
    ("B12", "instrument/measurement_factory.py", "create_measurement_port", ("hardware_config_path is None",)),
    (
        # 连接入口仍接受旧兼容来源：契约运行绝不用它（B3 的调用方形态），但参数还在。
        # 这是步骤 5 的**既有遗留**，本次只是补登，不是新增回退。
        "B22",
        "app/gui_runtime.py",
        "connect_instruments",
        ("config_path: str | None = None",),
    ),
    (
        # 仪器连接 worker 同理：prepared_run 是唯一输入，config_path 只服务旧快照。
        "B23",
        "presentation/qt/workers.py",
        "InstrumentWorker",
        ("config_path: str | None = None",),
    ),
    (
        "B16",
        "application/measurements/cable_loss.py",
        "CableLossUseCase",
        ("if self.stage_recorder is None:",),
    ),
    (
        "B17",
        "application/measurements/driver_mapping.py",
        "DriverPowerMappingUseCase",
        ("if self.stage_recorder is None:",),
    ),
    (
        "B18",
        "application/measurements/amplifier_test.py",
        "AmplifierMeasurementUseCase",
        ("if self.stage_recorder is None:",),
    ),
    (
        "B19",
        "application/dto/measurement_requests.py",
        "MeasurementRequest",
        ('stage_recorder: "RunStageRecorder | None" = None',),
    ),
    (
        # 共享的“None 表示旧兼容流程”开关：run_context / gui_runtime / run_service
        # 四处调用都靠它决定是否跳过快照完整性校验，是步骤 6 必须拆除的中枢分支。
        "B21",
        "application/run_service.py",
        "ensure_run_repository_supports_run_contract",
        ("if run_repository is None:",),
    ),
)
# 归属步骤：步骤 5 已完成（装配入口、仪器连接、配置路径回退、测量命令契约都已收敛），
# 因此本表剩余条目全部属于步骤 6；再出现“步骤 5”条目说明收敛被回退。
STEP_FIVE_SITES = ()

# 第 2.2 节：缺少运行契约的生产调用点（符号存在性 + 归属步骤 + 文档登记）。
NO_CONTRACT_CALL_SITES = (
    ("X1", "app/run_context.py", "prepare_run", "步骤 6"),
)

# 语义计数：受监测的“无契约判据”变量与函数。只有这些名字进基线，其余同形判据不在
# 本步骤范围内（例如局部的 config_path 判空），改本基线即可扩展覆盖面。
MONITORED_GUARD_NAMES = ("run_repository", "prepared_run", "stage_recorder", "hardware_config_path")
MONITORED_GUARD_CALLS = ("create_measurement_port", "connect_instruments", "prepare_run", "create_measurement")
# ``isinstance(..., Mapping)`` 探测只在组合根里代表“兼容旧组合配置映射”，因此只在该文件计数；
# 其它包的 Mapping 判断是普通类型收窄，与无契约无关（B8/B9 另有符号级标记冻结）。
MAPPING_PROBE_FILES = (COMPOSITION_ROOT,)

# 按文件冻结的语义计数（只减不增；删除分支时必须同步本基线与清单第 4 节）。
NO_CONTRACT_GUARD_BASELINE = {
    "app/gui_runtime.py": {
        "none:run_repository:is": 3,
        "none:run_repository:is_not": 4,
        "mapping": 3,
        "call:create_measurement_port": 2,
        "call:connect_instruments": 3,
    },
    "app/run_context.py": {
        "none:hardware_config_path:is_not": 1,
        "none:run_repository:is_not": 2,
    },
    "application/measurements/amplifier_test.py": {
        "none:stage_recorder:is": 4,
        "none:stage_recorder:is_not": 4,
    },
    "application/measurements/cable_loss.py": {
        "none:stage_recorder:is": 4,
        "none:stage_recorder:is_not": 1,
    },
    "application/measurements/driver_mapping.py": {
        "none:stage_recorder:is": 4,
        "none:stage_recorder:is_not": 2,
    },
    "application/run_service.py": {
        "none:hardware_config_path:is": 1,
        "none:run_repository:is": 1,
    },
    # 硬件装配入口只接受运行快照路径：缺路径即失败，不再有“调用方配置路径”来源。
    "instrument/measurement_factory.py": {"none:hardware_config_path:is": 1},
    # 连接仪器必须先有运行（无运行即失败），因此这里的 prepared_run 判据只剩一处。
    "presentation/qt/application_coordinator.py": {"none:prepared_run:is": 1},
    # 非回退点，登记在案以免将来被误当成无契约路径或悄悄消失：
    # main_window 只用来提示“配置已冻结”，不是测量入口。
    "presentation/qt/main_window.py": {
        "none:prepared_run:is_not": 1,
        "call:connect_instruments": 1,
    },
    # 测量命令在构造期就拒绝缺少运行契约的命令（D1），因此只保留这一处显式判空。
    "presentation/qt/measurement_controller_contract.py": {"none:prepared_run:is": 1},
    "presentation/qt/workers.py": {"call:connect_instruments": 1, "call:create_measurement": 3},
}

# 第 3.1 节：依赖无契约回退的测试调用点。
# 探针身份是 (种类, 名字, 期望次数)，种类为 def（定义）/ call（调用）/ load（读取）；
# 用 AST 计数而不是子串计数，避免 "AmplifierMeasurementRequest(" 被
# "MeasurementRequest(" 之类的短名字误命中。
TEST_ONLY_FILE_PROBES = {
    "tests/test_gui_runtime.py": (
        ("def", "_prepared_run", 1),
        ("call", "_prepared_run", 8),
        ("load", "_LEGACY_LOSS_PATH", 8),
        ("def", "test_legacy_assembly_still_uses_the_caller_config_path", 1),
        ("def", "test_all_measurement_factories_inject_the_same_external_port", 1),
    ),
    "tests/test_run_service.py": (
        ("def", "test_legacy_prepare_run_remains_non_contract_compatible", 1),
        ("call", "prepare_run", 1),
    ),
    "tests/test_offline_completion.py": (("call", "prepare_run", 1),),
    "tests/test_measurement_requests.py": (
        ("def", "test_legacy_prepared_run_has_no_stage_recorder", 1),
    ),
    "tests/test_cable_loss_use_case.py": (("call", "CableLossMeasurementRequest", 5),),
    "tests/test_driver_mapping_use_case.py": (("call", "DriverPowerMappingRequest", 1),),
    "tests/test_amplifier_use_case.py": (("call", "AmplifierMeasurementRequest", 2),),
    "tests/test_sleep_injection.py": (("call", "AmplifierMeasurementRequest", 1),),
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

# 清单分节：编号只能出现在本节里，越界说明清单被写坏了。
SECTION_HEADINGS = {
    "X": "2.2",
    "T": "3.1",
    "B": "4.",
}
_HEADING_RE = re.compile(r"^(#{1,4})\s+(.*)$")
_ROW_RE = re.compile(r"^\|\s*([XBT])(\d+)\s*\|")

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
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
    return None


def _alias_map(tree: ast.AST) -> dict[str, str]:
    """收集 ``import x as y`` / ``from m import f as g`` 的别名，防止改个名字就绕过检查。"""
    mapping: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.asname:
                    mapping[alias.asname] = alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    mapping[alias.asname] = alias.name.split(".")[-1]
    return mapping


def _resolved_call_name(node: ast.AST, aliases: dict[str, str]) -> str | None:
    name = _called_name(node)
    if name is None:
        return None
    return aliases.get(name, name)


def _side_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _guard_counts(path: Path) -> dict[str, int]:
    """按 AST 统计“无契约判据”的语义出现次数（注释与字符串不计入）。"""
    _text, tree = _tree(path)
    aliases = _alias_map(tree)
    relative = _relative(path)
    counts: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            subject = _side_name(node.left)
            if subject not in MONITORED_GUARD_NAMES:
                continue
            for operator, comparator in zip(node.ops, node.comparators):
                if not (isinstance(comparator, ast.Constant) and comparator.value is None):
                    continue
                if isinstance(operator, ast.Is):
                    key = f"none:{subject}:is"
                elif isinstance(operator, ast.IsNot):
                    key = f"none:{subject}:is_not"
                else:
                    continue
                counts[key] = counts.get(key, 0) + 1
        elif isinstance(node, ast.Call):
            name = _resolved_call_name(node, aliases)
            if (
                name == "isinstance"
                and len(node.args) == 2
                and relative in MAPPING_PROBE_FILES
                and isinstance(node.args[1], ast.Name)
                and node.args[1].id == "Mapping"
            ):
                counts["mapping"] = counts.get("mapping", 0) + 1
            if name in MONITORED_GUARD_CALLS:
                key = f"call:{name}"
                counts[key] = counts.get(key, 0) + 1
    return counts


def _scope_calls(function: ast.AST) -> list[ast.Call]:
    """收集直接属于该函数体的调用，不下钻到嵌套函数/类定义（避免误判顺序）。"""
    calls: list[ast.Call] = []
    stack = list(getattr(function, "body", []))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Call):
            calls.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return calls


def _sections(document: str) -> list[dict[str, object]]:
    """把文档切成标题区块：层级、标题、起止行号。"""
    lines = document.splitlines()
    headings: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        match = _HEADING_RE.match(line)
        if match:
            headings.append((len(match.group(1)), index, match.group(2).strip()))
    sections = []
    for position, (level, start, title) in enumerate(headings):
        end = len(lines)
        for next_level, next_start, _next_title in headings[position + 1 :]:
            if next_level <= level:
                end = next_start
                break
        sections.append(
            {"level": level, "title": title, "start": start, "end": end, "lines": lines[start:end]}
        )
    return sections


def _section(document: str, prefix: str) -> dict[str, object]:
    for section in _sections(document):
        title = str(section["title"])
        if title.startswith(prefix):
            return section
    raise AssertionError(f"清单缺少标题以 {prefix!r} 开头的小节")


def _doc_rows(section: dict[str, object], prefix: str) -> list[tuple[str, str]]:
    """收集某一节里以 ``| <prefix><数字> |`` 开头的表格行，返回 (编号, 原始行)。"""
    rows = []
    for line in section["lines"]:
        stripped = line.strip()
        marker = f"| {prefix}"
        if not stripped.startswith(marker):
            continue
        identifier = stripped[len("| ") :].split("|")[0].strip()
        if not identifier.startswith(prefix) or not identifier[len(prefix) :].isdigit():
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


def _probe_count(tree: ast.AST, kind: str, value: str) -> int:
    """按种类统计 AST 事实：定义 / 调用 / 读取。"""
    count = 0
    for node in ast.walk(tree):
        if kind == "def":
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name == value:
                    count += 1
        elif kind == "call":
            if isinstance(node, ast.Call) and _called_name(node) == value:
                count += 1
        elif kind == "load":
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id == value:
                    count += 1
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                if node.attr == value:
                    count += 1
        else:  # pragma: no cover - 常量写错时明确失败
            raise AssertionError(f"未知的探针种类: {kind}")
    return count


class InventoryParityTests(unittest.TestCase):
    """清单与基线双向一致：编号集合相等、无重复、分节严格、文件/符号/归属步骤可核对。"""

    def setUp(self):
        self.document = _source(INVENTORY_DOC)

    def _assert_parity(self, prefix: str, expected_ids, label: str):
        section = _section(self.document, SECTION_HEADINGS[prefix])
        rows = _doc_rows(section, prefix)
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
        for site_id, relative, symbol, _markers in NO_CONTRACT_SITES:
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
                        TEST_ONLY_FILE_PROBES,
                        f"{site_id} 登记的文件 {relative} 没有对应的文件基线",
                    )

    def test_inventory_ids_do_not_escape_their_own_section(self):
        """编号写错小节同样算清单损坏：X/B/T 行只能各自出现在本节。"""
        escaped = []
        allowed = {}
        for prefix, heading in SECTION_HEADINGS.items():
            section = _section(self.document, heading)
            allowed[prefix] = (int(section["start"]), int(section["end"]))
        for index, line in enumerate(self.document.splitlines()):
            match = _ROW_RE.match(line.strip())
            if not match:
                continue
            prefix = match.group(1)
            start, end = allowed[prefix]
            if not start <= index < end:
                escaped.append(f"{prefix}{match.group(2)} 出现在第 {index + 1} 行（不属于本节）")
        self.assertEqual(
            escaped,
            [],
            "清单编号必须写在对应小节内（X→2.2、T→3.1、B→第 4 节）: " + "; ".join(escaped),
        )

    def test_section_2_1_ids_are_unique_and_contiguous(self):
        section = _section(self.document, "2.1")
        rows = _doc_rows(section, "P")
        ids = [identifier for identifier, _raw in rows]
        self.assertEqual(sorted(ids), sorted(set(ids)), f"清单第 2.1 节存在重复编号: {ids}")
        numbers = sorted(int(identifier[1:]) for identifier in ids)
        self.assertEqual(
            numbers,
            list(range(1, len(numbers) + 1)),
            "清单第 2.1 节的 P 编号必须连续，缺号说明条目被删但编号没重排",
        )


class NoContractSiteTests(unittest.TestCase):
    """第 4 节的无契约分支必须以“符号 + 全部标记”的身份继续存在。"""

    def test_documented_no_contract_sites_still_exist(self):
        missing = []
        for site_id, relative, symbol, markers in NO_CONTRACT_SITES:
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
                for marker in markers:
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

    def test_no_contract_guard_counts_stay_on_the_baseline(self):
        """语义计数：新增等价分支、少删一处、或新增文件都要同步基线。"""
        actual = {}
        for path in _production_sources():
            counts = _guard_counts(path)
            if counts:
                actual[_relative(path)] = counts
        self.assertEqual(
            actual,
            NO_CONTRACT_GUARD_BASELINE,
            "无契约判据的语义计数与基线不一致："
            f"基线缺失 {sorted(set(actual) - set(NO_CONTRACT_GUARD_BASELINE))}，"
            f"基线多余 {sorted(set(NO_CONTRACT_GUARD_BASELINE) - set(actual))}；"
            "同一文件的差异见下方逐项核对。迁移或删除分支后请同步清单第 4 节与本基线",
        )
        drifted = []
        for relative, markers in NO_CONTRACT_GUARD_BASELINE.items():
            counts = actual.get(relative, {})
            for marker, expected in markers.items():
                if counts.get(marker, 0) != expected:
                    drifted.append(
                        f"{relative}: {marker!r} 期望 {expected} 处，实际 {counts.get(marker, 0)} 处"
                    )
        self.assertEqual(
            drifted,
            [],
            "无契约判据出现次数必须与基线一致（只减不增）；"
            "迁移或删除后请同步更新清单第 4 节与本基线: " + "; ".join(drifted),
        )


class ContractPredicateTests(unittest.TestCase):
    """契约判据单源，且契约运行绝无“回退到全局配置”的出口。"""

    def _definitions(self, name: str) -> list[str]:
        found = []
        for path in _production_sources():
            _text, tree = _tree(path)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name == name:
                        found.append(_relative(path))
                elif isinstance(node, ast.ClassDef):
                    continue
        return found

    def test_contract_predicates_are_defined_exactly_once(self):
        for name, expected_module in CONTRACT_PREDICATES.items():
            with self.subTest(predicate=name):
                self.assertEqual(
                    self._definitions(name),
                    [expected_module],
                    f"契约判据 {name} 必须只在 {expected_module} 定义一次；"
                    "新增第二套判据会让不同入口对“是否有运行契约”给出不同答案",
                )

    def test_has_run_contract_has_one_implementation_and_two_delegates(self):
        """判据单源（D8）：唯一实现在 application.run_service，其余入口只做委托。"""
        module, symbol = CONTRACT_PREDICATE_DEFINITION
        same_name_delegates = [
            relative
            for relative, dotted in CONTRACT_PREDICATE_DELEGATES.items()
            if dotted.split(".")[-1] == symbol
        ]
        self.assertEqual(
            sorted(self._definitions(symbol)),
            sorted([module, *same_name_delegates]),
            "运行契约判据只能有一处实现 + 同名的委托入口；"
            "新增实现会让不同入口对“是否有运行契约”给出不同答案",
        )
        # 唯一实现必须真的检查四项要求（运行标识、运行目录、运行仓储、快照解析入口）。
        text, _tree_node = _tree(ROOT / module)
        node = _find_symbol(text, symbol)
        self.assertIsNotNone(node, f"{module} 缺少 {symbol}")
        segment = ast.get_source_segment(text, node) or ""
        for requirement in CONTRACT_REQUIREMENTS:
            with self.subTest(requirement=requirement):
                self.assertIn(
                    requirement,
                    segment,
                    f"契约判据必须要求 {requirement}：缺少它会让残缺替身通过",
                )
        # 委托入口只允许一条 return，返回同一个判据的结果。
        for relative, dotted in CONTRACT_PREDICATE_DELEGATES.items():
            with self.subTest(delegate=relative):
                delegate_text, _delegate_tree = _tree(ROOT / relative)
                delegate = _find_symbol(delegate_text, dotted)
                self.assertIsNotNone(delegate, f"{relative} 缺少 {dotted}")
                body = [
                    statement
                    for statement in delegate.body
                    if not isinstance(statement, ast.Expr)
                ]
                self.assertEqual(
                    len(body),
                    1,
                    f"{dotted} 必须只委托一次，不允许自带第二套检查",
                )
                self.assertEqual(
                    _called_name(body[0].value),
                    symbol,
                    f"{dotted} 必须调用唯一实现 {symbol}",
                )

    def test_a_contract_run_never_falls_back_to_the_caller_config_path(self):
        """契约运行缺少可用快照时必须失败，而不是换成调用方的全局配置。"""
        from app import gui_runtime
        from application.run_service import RunNotResumable

        class _RepositorySpy:
            pass

        class _ResolvedRun:
            run_repository = _RepositorySpy()

            def hardware_connection_path(self):
                return Path("frozen/hardware_config.json")

        class _BrokenSnapshotRun:
            run_repository = _RepositorySpy()

            def hardware_connection_path(self):
                raise RunNotResumable("快照已被删除")

        class _NoResolverRun:
            run_repository = _RepositorySpy()

        caller_path = str(ROOT / "config.json")

        resolved = gui_runtime._hardware_config_path(_ResolvedRun(), caller_path)
        self.assertEqual(
            Path(resolved),
            Path("frozen/hardware_config.json"),
            "契约运行必须使用冻结快照，不能使用调用方传入的全局配置",
        )
        self.assertNotEqual(str(resolved), caller_path)
        for label, prepared in (
            ("快照校验失败", _BrokenSnapshotRun()),
            ("缺少快照解析入口", _NoResolverRun()),
        ):
            with self.subTest(case=label):
                with self.assertRaises(RunNotResumable):
                    gui_runtime._hardware_config_path(prepared, caller_path)

    def test_a_run_without_any_hardware_source_fails_before_connecting(self):
        """既没有运行契约、也没有调用方路径时，连接入口必须明确失败（兜底出口）。"""

        class _BareRun:
            """既无运行仓储，也没有快照解析入口的最小对象。"""

        from app import gui_runtime
        from application.run_service import RunNotResumable

        with self.assertRaises(RunNotResumable) as raised:
            gui_runtime._hardware_config_path(_BareRun(), None)
        self.assertIn("没有可用的硬件配置路径", str(raised.exception))

    def test_create_prepared_run_requires_a_hardware_snapshot(self):
        from app.run_context import create_prepared_run

        with self.assertRaises(ValueError):
            create_prepared_run(None, hardware_config=None)


class ProductionContractTests(unittest.TestCase):
    """生产测量链路只能通过正式运行契约进入硬件装配。"""

    def test_legacy_preflight_entry_has_no_production_caller(self):
        offenders = []
        for path in _production_sources():
            text, tree = _tree(path)
            aliases = _alias_map(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and _resolved_call_name(node, aliases) == "prepare_run":
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
            aliases = _alias_map(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if _resolved_call_name(node, aliases) != "create_measurement_port":
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

    def test_measurement_port_takes_the_run_snapshot_not_a_caller_config_path(self):
        """步骤 5：硬件装配的输入是运行快照路径，不再是调用方的 config_path。"""
        relative, symbol = MEASUREMENT_PORT_DEFINITION
        text, _tree_node = _tree(ROOT / relative)
        node = _find_symbol(text, symbol)
        self.assertIsNotNone(node, f"{relative} 缺少装配入口 {symbol}")
        self.assertIsInstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        arguments = [
            argument.arg
            for argument in (*node.args.posonlyargs, *node.args.args)
        ]
        self.assertEqual(
            arguments[:1],
            ["hardware_config_path"],
            f"{symbol} 的首个参数必须是运行快照的硬件配置路径（步骤 5 已收敛）",
        )
        self.assertNotIn(
            "config_path",
            arguments,
            f"{symbol} 不得再把调用方的 config_path 当作硬件装配来源",
        )

    def test_controlled_exception_script_stays_on_the_instrument_factory(self):
        """清单第 5 节的受控例外必须有可执行形状：只用工厂，不经生产连接入口。"""
        relative = CONTROLLED_EXCEPTION_SCRIPT
        text, tree = _tree(ROOT / relative)
        aliases = _alias_map(tree)
        port_calls = []
        connect_calls = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _resolved_call_name(node, aliases)
            if name == "create_measurement_port":
                port_calls.append(node)
            elif name == "connect_instruments":
                connect_calls.append(node.lineno)
        self.assertEqual(
            len(port_calls),
            1,
            f"{relative} 的受控例外只允许一处硬件装配调用；增减都要同步清单第 5 节",
        )
        modes = [
            keyword.value.value
            for node in port_calls
            for keyword in node.keywords
            if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant)
        ]
        self.assertEqual(
            modes, ["hardware"], f"{relative} 是受控真机冒烟，只允许装配硬件模式"
        )
        self.assertEqual(
            connect_calls,
            [],
            f"{relative} 不得调用生产连接入口 connect_instruments：它要求运行契约",
        )
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        self.assertNotIn(
            "app.gui_runtime",
            imported,
            f"{relative} 属受控例外，不得依赖生产组合根",
        )
        self.assertIn(
            "instrument.measurement_factory",
            imported,
            f"{relative} 应显式使用仪器工厂，声明它不经运行契约",
        )

    def test_connect_instruments_is_bound_to_the_prepared_run(self):
        """步骤 5：连接入口只接受运行对象，硬件路径由运行契约的冻结快照解析。"""
        text, _tree_node = _tree(ROOT / COMPOSITION_ROOT)
        node = _find_symbol(text, "connect_instruments")
        self.assertIsNotNone(node, "app/gui_runtime.py 缺少 connect_instruments")
        self.assertEqual(
            [argument.arg for argument in node.args.args][:1],
            ["prepared_run"],
            "connect_instruments 的首个参数必须是运行契约，不能是配置路径",
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

    def test_measurement_factories_are_dispatched_with_a_prepared_run(self):
        """工厂经动态表分发，因此既要查直接调用点，也要查分发表的字面量。"""
        offenders = []
        for path in _production_sources():
            _text, tree = _tree(path)
            aliases = _alias_map(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if _resolved_call_name(node, aliases) not in MEASUREMENT_FACTORIES:
                    continue
                keywords = {keyword.arg for keyword in node.keywords}
                if "prepared_run" not in keywords:
                    offenders.append(f"{_relative(path)}:{node.lineno} 缺少 prepared_run")
        self.assertEqual(
            offenders,
            [],
            "生产代码调用测量装配入口必须显式携带 prepared_run: " + "; ".join(offenders),
        )
        workers_text = _source(ROOT / WORKER_DISPATCH_MODULE)
        for name, expected in WORKER_FACTORY_LITERALS.items():
            with self.subTest(factory=name):
                self.assertEqual(
                    workers_text.count(f'"{name}"'),
                    expected,
                    f"{WORKER_DISPATCH_MODULE} 里 {name} 的分发字面量数量变化，"
                    "请同步清单第 2.1 节 P7 与本基线",
                )
        for relative, expected in WORKER_DISPATCH_BASELINE.items():
            with self.subTest(file=relative):
                self.assertEqual(_source(ROOT / relative).count(WORKER_DISPATCH_MARKER), expected)

    def test_dispatch_is_preceded_by_the_run_contract_check(self):
        """worker 必须先经 require_prepared_run 校验，并把校验过的对象交给装配入口。"""
        _text, tree = _tree(ROOT / WORKER_DISPATCH_MODULE)
        offenders = []
        checked = 0
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls = _scope_calls(node)
            dispatch = [call for call in calls if _called_name(call) == "create_measurement"]
            if not dispatch:
                continue
            checked += 1
            checks = [call for call in calls if _called_name(call) == WORKER_CONTRACT_CHECK]
            if not checks:
                offenders.append(f"{node.name} 没有调用 {WORKER_CONTRACT_CHECK}")
                continue
            if min(call.lineno for call in checks) > min(call.lineno for call in dispatch):
                offenders.append(f"{node.name} 在运行契约校验前就装配了测量用例")
                continue
            validated = set()
            for statement in ast.walk(node):
                if not isinstance(statement, ast.Assign):
                    continue
                if len(statement.targets) != 1:
                    continue
                target = statement.targets[0]
                if not isinstance(target, ast.Name):
                    continue
                if isinstance(statement.value, ast.Call) and _called_name(
                    statement.value
                ) == WORKER_CONTRACT_CHECK:
                    validated.add(target.id)
            for call in dispatch:
                keyword = next(
                    (item for item in call.keywords if item.arg == "prepared_run"), None
                )
                if keyword is None:
                    offenders.append(f"{node.name} 装配时没有传 prepared_run")
                elif not isinstance(keyword.value, ast.Name) or keyword.value.id not in validated:
                    offenders.append(
                        f"{node.name} 传的 prepared_run 不是 {WORKER_CONTRACT_CHECK} 的返回值"
                    )
        self.assertEqual(checked, 3, "worker 分发点数量变化，请同步更新清单第 2.1 节")
        self.assertEqual(
            offenders,
            [],
            "测量 worker 必须先校验运行契约再把校验结果交给装配入口: " + "; ".join(offenders),
        )

    def test_monitored_symbols_cannot_be_reached_through_aliases(self):
        offenders = []
        for path in _production_sources():
            _text, tree = _tree(path)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name in MONITORED_SYMBOLS and alias.asname:
                            offenders.append(
                                f"{_relative(path)}:{node.lineno} 用别名 {alias.asname} 导入 {alias.name}"
                            )
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        short = alias.name.split(".")[-1]
                        if short in MONITORED_SYMBOLS and alias.asname:
                            offenders.append(
                                f"{_relative(path)}:{node.lineno} 用别名 {alias.asname} 导入 {alias.name}"
                            )
        self.assertEqual(
            offenders,
            [],
            "受监测符号不得以别名导入：改名会绕过装配入口检查: " + "; ".join(offenders),
        )

    def test_dynamic_symbol_lookup_is_confined_to_the_dispatch_helper(self):
        offenders = []
        found = {}
        for path in _production_sources():
            _text, tree = _tree(path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if _called_name(node) != "getattr":
                    continue
                if len(node.args) < 2:
                    continue
                holder, target = node.args[0], node.args[1]
                if isinstance(target, ast.Constant) and target.value in MONITORED_SYMBOLS:
                    offenders.append(
                        f"{_relative(path)}:{node.lineno} 用 getattr 取 {target.value}"
                    )
                    continue
                if not isinstance(target, ast.Name):
                    continue
                holder_name = getattr(holder, "id", None) or getattr(holder, "attr", None)
                if holder_name not in MONITORED_DISPATCH_MODULES:
                    continue
                found[_relative(path)] = found.get(_relative(path), 0) + 1
        self.assertEqual(
            found,
            DYNAMIC_LOOKUP_BASELINE,
            "从装配模块动态取函数名只允许出现在 worker 的分发辅助函数里；新增处要显式登记: "
            f"{found}",
        )
        self.assertEqual(
            offenders,
            [],
            "受监测符号不得用 getattr 动态取用: " + "; ".join(offenders),
        )


class TestOnlyCallSiteTests(unittest.TestCase):
    """第 3.1 节：仍依赖无契约回退的测试调用点按文件冻结。"""

    def test_test_only_no_contract_sites_are_frozen(self):
        drifted = []
        for relative, probes in TEST_ONLY_FILE_PROBES.items():
            path = ROOT / relative
            if not path.is_file():
                drifted.append(f"{relative} 不存在")
                continue
            _text, tree = _tree(path)
            for kind, value, expected in probes:
                actual = _probe_count(tree, kind, value)
                if actual != expected:
                    drifted.append(
                        f"{relative}: {kind} {value!r} 期望 {expected} 处，实际 {actual} 处"
                    )
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
            "D5",
            "D6",
            "D7",
            "D8",
            "D9",
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

    def test_inventory_doc_describes_the_semantic_gate(self):
        document = _source(INVENTORY_DOC)
        for keyword in ("语义计数", "别名", "契约判据单源", "章节"):
            with self.subTest(keyword=keyword):
                self.assertIn(keyword, document)


if __name__ == "__main__":
    unittest.main()
