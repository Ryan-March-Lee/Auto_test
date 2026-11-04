"""阶段五：运行流程契约的测试单源。

``tests/test_static_quality_gate.py`` 与 ``tests/test_presentation_boundaries.py``
共同约束“仪表配置 → 参数配置 → 完成配置并连接仪器”这一条路径。两个门禁需要同一份
旧入口名称、最终化节点和 AST 扫描规则；放在这里单源维护，避免只更新一个文件后另一个
继续按旧清单通过。

本模块只读源码，不导入生产模块，也不参与正式运行时包。
"""

from __future__ import annotations

import ast
from pathlib import Path

#: 阶段一删除、永远不得复用的旧运行流程入口。
#:
#: 这些名字横跨设备发现页的“连接仪器”按钮（``connect_btn`` /
#: ``connect_requested``）、参数页的独立连接入口（``request_connect``）、窗口旧连接槽
#: （``_set_connect_enabled``），以及后来改名为 ``finalize_run_requested`` 的旧创建
#: 运行信号（``create_run_requested``）。删除后不许以属性、裸名字、声明名或字符串
#: 名字回到正式运行代码。
RETIRED_RUN_FLOW_ENTRY_NAMES = frozenset(
    {
        "connect_btn",
        "connect_requested",
        "request_connect",
        "create_run_requested",
        "_set_connect_enabled",
    }
)

#: 协调器里构成最终化事务、提交、补偿和阶段结算的节点。
#:
#: 两个门禁都要求这些名字继续存在；具体调用关系由各自的 ``calls`` 契约约束。
COORDINATOR_FINALIZATION_ENTRY_NAMES = (
    "finalize_configuration",
    "prepare_pending_run",
    "commit_finalization",
    "fail_finalization",
    "cancel_finalization",
    "abandon_pending_run",
    "has_pending_run",
    "retry_unreleased_ports",
    "_abort_finalization",
    "_compensate_pending_run",
    "_discard_pending_run",
    "release_port_after_measurement",
    "_settle_port_after_stage",
    "settle_instrument_stage",
)

#: 窗口里负责把页面意图接到协调器事务、并在连接结束事件上提交/补偿的槽。
WINDOW_FINALIZATION_ENTRY_NAMES = (
    "_finalize_run",
    "on_instrument_controller_ready",
    "on_instrument_error",
    "on_instrument_stopped",
    "_on_measurement_controller_thread_finished",
)

#: 页面信号 -> 窗口槽。主窗口是唯一允许把最终化意图交给协调器的地方。
FINALIZATION_WINDOW_CONNECTIONS = {
    "finalize_run_requested": "_finalize_run",
}

#: 窗口槽 -> 必须调用的协调器入口。
FINALIZATION_WINDOW_CALLS = {
    "_finalize_run": ("finalize_configuration",),
    "on_instrument_controller_ready": ("commit_finalization",),
    "on_instrument_error": ("fail_finalization",),
    "on_instrument_stopped": ("cancel_finalization",),
    "_on_measurement_controller_thread_finished": (
        "release_port_after_measurement",
    ),
}

#: 只允许由 ``presentation/qt/main_window.py`` 调用的事务入口。
#:
#: 出现第二个调用文件就等于出现了第二个提交端口/补偿的入口，无论它是否叫“连接”。
SINGLE_FINALIZATION_ENTRY_NAMES = (
    "finalize_configuration",
    "commit_finalization",
    "fail_finalization",
    "cancel_finalization",
)


def parse_source(path: Path) -> ast.Module:
    """按仓库约定（UTF-8 BOM 兼容）解析一个 Python 源文件。"""
    return ast.parse(path.read_text(encoding="utf-8-sig"), str(path))


def declared_identifier_names(tree: ast.AST) -> set[str]:
    """返回模块里声明的函数/方法/类名与简单赋值目标名。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def function_definitions(tree: ast.AST) -> dict[str, ast.AST]:
    """返回 ``{函数/方法短名: 节点}``；同名以内层最后出现者为准。"""
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def called_names(function_node: ast.AST) -> set[str]:
    """返回函数体内出现的调用短名（含方法属性名）。"""
    names: set[str] = set()
    for node in ast.walk(function_node):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Name):
            names.add(function.id)
        elif isinstance(function, ast.Attribute):
            names.add(function.attr)
    return names


def call_sites(tree: ast.AST, names: frozenset[str] | set[str] | tuple[str, ...]):
    """返回 ``names`` 中名字作为调用目标的 ``[(名字, 行号)]``，按源码位置排序。"""
    wanted = set(names)
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        name = None
        if isinstance(function, ast.Name):
            name = function.id
        elif isinstance(function, ast.Attribute):
            name = function.attr
        if name in wanted:
            sites.append((name, node.lineno))
    return sorted(sites, key=lambda item: item[1])


def imported_from_pairs(tree: ast.AST) -> set[tuple[str, str]]:
    """返回 ``from 模块 import 名称`` 的 ``(模块, 名称)`` 集合。"""
    pairs: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and not node.level and node.module:
            pairs.update((node.module, alias.name) for alias in node.names)
    return pairs


def missing_contract_items(
    relative: str,
    tree: ast.AST,
    contract: dict[str, tuple],
) -> list[str]:
    """按 ``definitions`` / ``imports`` / ``calls`` 契约返回缺失项。

    契约里的 ``calls`` 是 ``(函数名, 必须直接调用的名字)``。方法仍在但不再调用事务
    节点时，这里会报告“未调用”，而不是仅仅因为定义存在就通过。
    """
    missing: list[str] = []
    definitions = function_definitions(tree)
    for name in contract.get("definitions", ()):
        if name not in definitions:
            missing.append(f"{relative} 缺少定义 {name}")
    imported = imported_from_pairs(tree)
    for module, name in contract.get("imports", ()):
        if (module, name) not in imported:
            missing.append(f"{relative} 缺少导入 {module}.{name}")
    for function, called in contract.get("calls", ()):
        node = definitions.get(function)
        if node is None:
            missing.append(f"{relative} 的调用点 {function} 不存在")
            continue
        if called not in called_names(node):
            missing.append(f"{relative} 的 {function} 未调用 {called}")
    return missing


def signal_connections(tree: ast.AST) -> dict[str, set[str | None]]:
    """返回 ``{信号名: {连接的槽名}}``；槽名取属性或裸名字的最后一段。"""
    connections: dict[str, set[str | None]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if not isinstance(function, ast.Attribute) or function.attr != "connect":
            continue
        signal = function.value
        if not isinstance(signal, ast.Attribute):
            continue
        slot = _reference_tail(node.args[0]) if node.args else None
        connections.setdefault(signal.attr, set()).add(slot)
    return connections


def emitted_signal_names(function_node: ast.AST) -> set[str]:
    """返回函数体内以 ``<信号>.emit(...)`` 形式发出的信号名。"""
    names: set[str] = set()
    for node in ast.walk(function_node):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "emit"
            and isinstance(node.func.value, ast.Attribute)
        ):
            names.add(node.func.value.attr)
    return names


def retired_run_flow_sites(
    tree: ast.AST,
    names: frozenset[str] = RETIRED_RUN_FLOW_ENTRY_NAMES,
):
    """返回旧运行流程入口的引用：``[(名字, 行号)]``，按源码位置排序。

    覆盖属性访问、裸名字、函数/类声明名与字符串常量
    （``getattr(page, "request_connect")``）。注释与 docstring 不参与匹配（字符串只有
    恰好等于入口名时才命中），历史名称留在说明文字里是审计证据，不是运行时引用。
    """
    sites = []
    for node in ast.walk(tree):
        name = None
        if isinstance(node, ast.Attribute) and node.attr in names:
            name = node.attr
        elif (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name in names
        ):
            name = node.name
        elif isinstance(node, ast.Name) and node.id in names:
            name = node.id
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in names
        ):
            name = node.value
        if name is not None:
            sites.append((name, node.lineno))
    return sorted(sites, key=lambda item: item[1])


def _reference_tail(node: ast.AST) -> str | None:
    """取一个表达式引用名字的最后一段：``self._slot`` -> ``_slot``。"""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None
