import ast
import collections.abc
import re
import unittest
from dataclasses import fields
from pathlib import Path
from typing import get_origin, get_type_hints

from tests.support.run_flow_contract import (
    COORDINATOR_FINALIZATION_ENTRY_NAMES,
    FINALIZATION_WINDOW_CALLS,
    FINALIZATION_WINDOW_CONNECTIONS,
    WINDOW_FINALIZATION_ENTRY_NAMES,
    called_names,
    declared_identifier_names,
    emitted_signal_names,
    function_definitions,
    parse_source,
    retired_run_flow_sites,
    signal_connections,
)
from tests.test_architecture_dependencies import _worker_construction_violations


ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "presentation" / "qt" / "main_window.py"
QT_ROOT = ROOT / "presentation" / "qt"
UI_TESTS = ROOT / "tests" / "ui"
FORBIDDEN_REVERSE_IMPORTS = {"enhanced_main_gui", "enhanced_workers"}
HARDWARE_ROOTS = {
    "instrument",
    "instrument_control",
    "pyvisa",
    "visa",
    "scpi",
    "hardware",
}
# 表现层只允许装配根连接基础设施；日志是各层共用的横切工具，不参与业务耦合。
COMPOSITION_ROOT = "composition.py"
CROSS_CUTTING_INFRASTRUCTURE = "infrastructure.logging"
# UI 测试只覆盖表现层与 DTO/端口，装配真实后端的场景属于 tests/integration。
BACKEND_IMPLEMENTATION_ROOTS = HARDWARE_ROOTS | {"app", "infrastructure"}

# 步骤 14：配置 authoring、设备绑定与 domain 校验的唯一实现留在 application/domain。
# 门禁按模块族判定：新增 ``application.configuration_*`` 或 ``domain.configuration.*``
# 模块会自动纳入，不依赖手工维护的模块清单。存量依赖按“文件 -> 模块 -> 名称”显式登记。
CONFIGURATION_BUSINESS_MODULE_PREFIXES = (
    "application.configuration_",
    "domain.configuration.",
)
CONFIGURATION_BUSINESS_MODULE_NAMES = frozenset(
    {"application.device_binding", "domain.configuration"}
)
# 页面可以消费的配置边界：面向页面的 ViewModel/facade 与结构化结果值对象。
# 它们只表达“结果与状态”，规则本身仍在 application/domain 的实现模块里。
CONFIGURATION_BOUNDARY_MODULES = frozenset(
    {
        "application.configuration_editing",
        "application.configuration_view_model",
        "domain.configuration.types",
    }
)
# ``import application.configuration_authoring`` 或 ``from application import
# configuration_authoring`` 没有具体名称可登记，用这个标记替代名称。
WHOLE_MODULE_IMPORT = "<module>"
# 这份基线是重构完成时的现状，不是允许继续扩张的白名单：
# - 新增文件、模块或导入名称都会让门禁失败；
# - 删除依赖后必须同步删除对应条目（精确匹配，不接受“只加不减”的静默变化）；
# - 确需新增时必须说明为什么规则不能留在 application/domain，并同步更新本文件的长期规则用例。
CONFIGURATION_BUSINESS_IMPORT_BASELINE = {
    "application_coordinator.py": {
        "application.configuration_authoring": {
            "ConfigurationAuthoringService",
            "ConfigurationEditingDocument",
        },
    },
    "config_form_bindings.py": {
        "application.device_binding": {
            "LEGACY_BINDING_SOURCE",
            "POWER_SUPPLY_BINDINGS",
            "binding_device_key",
            "binding_identity_problem",
            "supply_id_problem",
        },
    },
    "config_form_state.py": {
        "application.configuration_authoring": {
            "ConfigurationAuthoringService",
            "ConfigurationEditingDocument",
        },
        "domain.configuration.rules": {"POWER_CHANNEL_ROLES"},
    },
    "configuration_page.py": {
        "application.device_binding": {"ApplyDeviceSelectionRequest"},
    },
    "config_power_section.py": {
        "application.configuration_authoring": {"DUT_CHANNEL_ROLES"},
    },
    "config_projection.py": {
        "application.configuration_authoring": {"DUT_CHANNEL_ROLES"},
        "domain.configuration.models": {"resolve_driver_power_source"},
        "domain.configuration.rules": {
            "POWER_CHANNEL_ROLES",
            "assignment_supply_id",
        },
    },
    "configuration_session.py": {
        "application.device_binding": {
            "ApplyDeviceSelectionRequest",
            "DeviceBindingResult",
        },
    },
    "instrument_setup_page.py": {
        "application.device_binding": {"ApplyDeviceSelectionRequest"},
    },
}

# 已登记的用户可见页面模块。只有页面注册表（pages.py）与窗口装配根（main_window.py）
# 可以导入页面类型；其他页面必须通过窄接口/端口拿依赖，不能持有另一个页面实例。
REGISTERED_PAGE_MODULES = frozenset(
    {
        "configuration_page",
        "instrument_setup_page",
        "cable_loss_page",
        "driver_mapping_page",
        "amplifier_page",
        "visualization_page",
        "run_history_page",
        "welcome_page",
    }
)
PAGE_ASSEMBLY_PATHS = frozenset({"main_window.py", "pages.py"})

# 步骤 13 的命名迁移兼容期：这些旧模块只再导出新模块的页面类型，不再承载实现。
# 它们允许导入新页面模块（否则兼容入口无法工作），迁移完成后随旧模块一起删除。
COMPATIBILITY_PAGE_MODULES = frozenset({"config_page.py", "connection_page.py"})

# 页面构造的唯一上下文。新增页面动作应经 ViewModel/Application facade 与页面自己的
# 一个信号完成，不得把新回调塞进 PageContext；确需调整契约时先更新分层规则文档。
PAGE_CONTEXT_FIELDS = (
    "controller",
    "config_path_provider",
    "run_provider",
    "driver_mode_provider",
    "connection_dialog_factory",
    "plot_widget_factory",
    "log_callback",
    "progress_callback",
    "error_callback",
    "archival_location_provider",
    "archival_run_id_provider",
    "driver_realtime_data_callback",
    "amplifier_realtime_data_callback",
    "clear_realtime_callback",
    "build_configuration",
    "build_visualization",
    "build_run_history",
)

# 阶段五：运行流程入口的长期基线。
#
# 阶段一删除了“先创建运行、再单独连接”的旧用户入口：设备发现页的“连接仪器”按钮、
# 参数页的 ``request_connect``，以及窗口里的旧连接接线。旧入口名称与 AST 扫描规则
# 单源在 ``tests/support/run_flow_contract.py``；这里冻结 Qt 页面与窗口的接线。
#
# 文件 -> 必须具备的入口名（信号与方法都按声明名匹配）。只冻结契约与事务编排，
# 不冻结页面外观；按钮文案由页面测试与 README 各自维护。
FINALIZATION_ENTRY_BASELINE = {
    "configuration_page.py": (
        "finalize_run_requested",
        "set_finalizing",
        "set_reconnect_required",
    ),
    "instrument_setup_page.py": (
        "next_requested",
        "return_welcome_requested",
        "request_next",
        "request_select_all",
        "page_actions_visible",
    ),
    "configuration_session.py": (
        "apply_selection",
        "view_state",
        "attach_discovery_panel",
    ),
    "application_coordinator.py": COORDINATOR_FINALIZATION_ENTRY_NAMES,
}


def _tree(path):
    return parse_source(path)


def _imported_modules(tree):
    """收集模块级与函数级的绝对导入模块名，用于静态边界判定。"""
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def _imported_roots(tree):
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _is_configuration_business_module(module):
    """判断模块是否是配置业务实现（页面不得直接依赖）。"""
    if not module:
        return False
    for boundary in CONFIGURATION_BOUNDARY_MODULES:
        if module == boundary or module.startswith(boundary + "."):
            return False
    for name in CONFIGURATION_BUSINESS_MODULE_NAMES:
        if module == name or module.startswith(name + "."):
            return True
    return any(
        module.startswith(prefix) for prefix in CONFIGURATION_BUSINESS_MODULE_PREFIXES
    )


def _module_file(module):
    """按仓库布局判断一个点分名字是否是模块，避免把类名误当成子模块。"""
    base = ROOT.joinpath(*module.split("."))
    package_init = base / "__init__.py"
    if package_init.is_file():
        return package_init
    candidate = base.with_suffix(".py")
    return candidate if candidate.is_file() else None


def _literal_import_argument(node):
    """取出 ``import_module("x")`` / ``__import__("x")`` 的字面量模块名。"""
    if not isinstance(node, ast.Call) or not node.args:
        return None
    function = node.func
    if isinstance(function, ast.Name):
        name = function.id
    elif isinstance(function, ast.Attribute):
        name = function.attr
    else:
        return None
    if name not in {"import_module", "__import__"}:
        return None
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def _configuration_business_imports_in_tree(tree):
    """返回语法树中从配置业务实现导入的名称：``{模块: {名称}}``。

    ``import 模块``、``from 包 import 模块`` 与含字面量模块名的
    ``importlib.import_module(...)`` 没有可登记的具体名称，记为
    ``WHOLE_MODULE_IMPORT``，同样必须显式登记才能通过。
    """
    found = {}

    def record(module, names=None):
        found.setdefault(module, set()).update(names or {WHOLE_MODULE_IMPORT})

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_configuration_business_module(alias.name):
                    record(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # 相对导入只可能指向 presentation.qt 内部；配置业务实现都是绝对包。
                continue
            module = node.module or ""
            named = set()
            for alias in node.names:
                full = f"{module}.{alias.name}" if module else alias.name
                if _module_file(full) and _is_configuration_business_module(full):
                    # ``from domain.configuration import rules`` 导入的是子模块本身。
                    record(full)
                elif _is_configuration_business_module(module):
                    named.add(alias.name)
            if named:
                record(module, named)
        elif isinstance(node, ast.Call):
            module = _literal_import_argument(node)
            if module and _is_configuration_business_module(module):
                record(module)
    return found


def _configuration_business_imports(path):
    """返回 Qt 模块从配置业务实现导入的名称：``{模块: {名称}}``。"""
    return _configuration_business_imports_in_tree(_tree(path))


def _module_relative_path(path):
    """返回相对 Qt 根目录的 POSIX 路径，例如 ``ui/pages.py``。"""
    return path.relative_to(QT_ROOT).as_posix()


def _is_page_assembly_module(path):
    """只有 Qt 根目录下的页面注册表与窗口装配根可以导入页面类型。

    按相对路径而不是文件名判定：子目录里的同名模块不继承装配根豁免。
    """
    try:
        return _module_relative_path(path) in PAGE_ASSEMBLY_PATHS
    except ValueError:
        return False


def _declared_page_classes_in_tree(tree):
    """返回模块里按约定声明的页面类名（``*Page`` 或显式继承 ``BasePage``）。"""
    found = set()
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {ast.unparse(base).split(".")[-1] for base in node.bases}
        if node.name.endswith("Page") or "BasePage" in bases:
            found.add(node.name)
    return found


def _declared_page_modules():
    """扫描全部 Qt 模块，返回 ``{模块路径: {页面类名}}``。

    页面模块不局限于 ``*_page.py``：页面类换个文件名也不能绕过页面导入门禁。
    """
    declared = {}
    for path in sorted(QT_ROOT.rglob("*.py")):
        if _is_page_assembly_module(path):
            continue
        classes = _declared_page_classes_in_tree(_tree(path))
        if classes:
            declared[path] = classes
    return declared


_PAGE_PARAMETER_ANNOTATION = re.compile(r"(?:^|[^A-Za-z0-9_])[A-Za-z0-9_]*Page\b")


def _page_constructor_violations(page_class, page_names):
    """页面构造函数不得吞入任意依赖，也不得收下另一个页面实例。"""
    violations = []
    names = set(page_names)
    for node in page_class.body:
        if not isinstance(node, ast.FunctionDef) or node.name != "__init__":
            continue
        arguments = node.args
        if arguments.vararg or arguments.kwarg:
            violations.append(
                f"{page_class.name}.__init__ 接受 *args/**kwargs，无法约束窄依赖"
            )
        positional = [*arguments.posonlyargs, *arguments.args]
        positional_defaults = [None] * (
            len(positional) - len(arguments.defaults)
        ) + list(arguments.defaults)
        parameters = list(zip(positional, positional_defaults)) + list(
            zip(arguments.kwonlyargs, arguments.kw_defaults)
        )
        for argument, default in parameters:
            if argument.arg == "self":
                continue
            lowered = argument.arg.lower()
            if re.fullmatch(r"(?:.*_)?page", lowered):
                violations.append(
                    f"{page_class.name}.__init__ 参数 {argument.arg} 看起来是另一个页面"
                )
            annotations = ast.unparse(argument.annotation) if argument.annotation else ""
            values = [annotations]
            if default is not None:
                values.append(ast.unparse(default))
            for text in values:
                if not text:
                    continue
                if _PAGE_PARAMETER_ANNOTATION.search(text) or any(
                    re.search(rf"\b{re.escape(name)}\b", text) for name in names
                ):
                    violations.append(
                        f"{page_class.name}.__init__ 参数 {argument.arg} 依赖页面类型: "
                        f"{text}"
                    )
                    break
    return violations



def _imported_page_modules(tree):
    """返回模块导入的已登记页面模块，例如 ``presentation.qt.configuration_page``。"""
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 0:
                base = module
            elif node.level == 1:
                base = f"presentation.qt.{module}" if module else "presentation.qt"
            elif node.level == 2:
                base = f"presentation.{module}" if module else "presentation"
            else:
                continue
            if not base:
                continue
            for alias in node.names:
                imported.add(f"{base}.{alias.name}")
    found = set()
    for module in imported:
        parts = module.split(".")
        if len(parts) >= 3 and parts[:2] == ["presentation", "qt"]:
            if parts[2] in REGISTERED_PAGE_MODULES:
                found.add(".".join(parts[:3]))
    return found


class PresentationBoundaryTests(unittest.TestCase):
    """presentation 层与 Qt 模块的静态边界门禁。"""

    def test_new_qt_modules_do_not_import_legacy_gui(self):
        violations = []
        for path in QT_ROOT.rglob("*.py"):
            roots = _imported_roots(_tree(path))
            reverse = roots & FORBIDDEN_REVERSE_IMPORTS
            if reverse:
                violations.append(f"{path.relative_to(ROOT)}: {sorted(reverse)}")
        self.assertEqual(violations, [])

    def test_measurement_pages_do_not_construct_workers(self):
        page_paths = tuple(QT_ROOT / name for name in (
            "cable_loss_page.py",
            "driver_mapping_page.py",
            "amplifier_page.py",
        ))
        violations = []
        for path in page_paths:
            violations.extend(
                f"{path.relative_to(ROOT)} constructs a measurement worker"
                for _ in _worker_construction_violations(_tree(path))
            )
        self.assertEqual(violations, [])

    def test_main_window_does_not_call_measurement_assembly(self):
        forbidden = {
            "create_cable_loss_measurement",
            "create_driver_mapping_measurement",
            "create_amplifier_measurement",
        }
        calls = []
        for node in ast.walk(_tree(MAIN_WINDOW)):
            if isinstance(node, ast.Call):
                called = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
                if called in forbidden:
                    calls.append(called)
        self.assertEqual(calls, [])

    def test_qt_peripheral_modules_have_no_hardware_imports(self):
        peripheral_paths = (
            QT_ROOT / "pages.py",
            QT_ROOT / "measurement_state.py",
            QT_ROOT / "measurement_controller_contract.py",
        )
        violations = []
        for path in peripheral_paths:
            if not path.exists():
                continue
            roots = _imported_roots(_tree(path))
            hardware = roots & HARDWARE_ROOTS
            if hardware:
                violations.append(f"{path.relative_to(ROOT)}: {sorted(hardware)}")
        self.assertEqual(violations, [])

    def test_presentation_reaches_infrastructure_only_through_the_composition_root(self):
        """表现层不装配基础设施：路径、存储与扫描器都由 composition/app 注入。

        例外只有两处：``composition.py`` 是 Qt 进程的装配根，负责把应用服务与
        基础设施实现接在一起；``infrastructure.logging`` 是各层共用的日志工具。
        """
        violations = []
        for path in QT_ROOT.rglob("*.py"):
            modules = _imported_modules(_tree(path))
            infrastructure = {
                module
                for module in modules
                if module.split(".")[0] == "infrastructure"
            }
            if not infrastructure or path.name == COMPOSITION_ROOT:
                continue
            unexpected = {
                module
                for module in infrastructure
                if module != CROSS_CUTTING_INFRASTRUCTURE
                and not module.startswith(f"{CROSS_CUTTING_INFRASTRUCTURE}.")
            }
            if unexpected:
                violations.append(
                    f"{path.relative_to(ROOT)}: {sorted(unexpected)}"
                )
        self.assertEqual(
            violations,
            [],
            "表现层只能通过 composition.py 或 application 端口触碰基础设施: "
            + ", ".join(violations),
        )

    def test_ui_tests_do_not_import_backend_implementations(self):
        """``tests/ui`` 是纯表现层测试：装配真实后端的场景必须放在 tests/integration。"""
        violations = []
        for path in sorted(UI_TESTS.rglob("test_*.py")):
            modules = _imported_modules(_tree(path))
            backend = {
                module
                for module in modules
                if module.split(".")[0] in BACKEND_IMPLEMENTATION_ROOTS
            }
            if backend:
                violations.append(
                    f"{path.relative_to(ROOT)}: {sorted(backend)}"
                )
        self.assertEqual(
            violations,
            [],
            "UI 测试不得导入后端实现（app/infrastructure/硬件）: "
            + ", ".join(violations),
        )


class QtMigrationResidueTests(unittest.TestCase):
    """步骤 12：生产 Qt 代码不留迁移残留。

    覆盖四类残留：旧式 ``print`` 与 logger 混用、手动 ``sys.path`` 修改、静默吞掉
    业务失败的 ``except Exception: pass``、以及把私有名字当作兼容导出转发。
    """

    def _production_sources(self):
        return sorted(QT_ROOT.rglob("*.py"))

    def test_production_qt_code_logs_instead_of_printing(self):
        violations = []
        for path in self._production_sources():
            tree = _tree(path)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "print"
                ):
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(
            violations,
            [],
            "UI 代码的日志统一走 get_logger，界面提示走状态栏/对话框: "
            + ", ".join(violations),
        )

    def test_production_qt_code_does_not_modify_sys_path(self):
        violations = []
        for path in self._production_sources():
            tree = _tree(path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Attribute):
                    continue
                chain = ast.unparse(node)
                if chain.startswith("sys.path"):
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno} {chain}")
        self.assertEqual(violations, [], "正式链路不得修改 sys.path: " + ", ".join(violations))

    def test_production_qt_code_does_not_swallow_failures(self):
        """``except Exception: pass`` 会把业务失败静默丢掉，必须改成日志/状态/对话框。

        只拦截宽泛处理器（裸 ``except`` / ``Exception`` / ``BaseException``）：窄类型的
        ``except (RuntimeError, TypeError): pass`` 属于幂等断开 signal 之类的固定模式。
        """
        broad = {"Exception", "BaseException"}
        violations = []
        for path in self._production_sources():
            tree = _tree(path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.ExceptHandler):
                    continue
                body = list(node.body)
                if not (len(body) == 1 and isinstance(body[0], ast.Pass)):
                    continue
                caught = ast.unparse(node.type) if node.type else None
                if caught is None or caught in broad:
                    violations.append(
                        f"{path.relative_to(ROOT)}:{node.lineno} ({caught or '裸 except'})"
                    )
        self.assertEqual(
            violations,
            [],
            "业务失败不得被 except Exception: pass 掩盖: " + ", ".join(violations),
        )

    def test_pages_module_does_not_export_private_names(self):
        """``__all__`` 曾把私有转发器当兼容导出，私有名字不得再出现在导出表里。"""
        from presentation.qt import pages

        private = [name for name in pages.__all__ if name.startswith("_")]
        self.assertEqual(private, [], "pages.__all__ 不得导出私有名字: " + ", ".join(private))

    def test_production_qt_code_never_terminates_a_thread(self):
        """超时必须保持引用让用户重试，``QThread.terminate()`` 会让线程带锁退出。"""
        violations = []
        for path in self._production_sources():
            for node in ast.walk(_tree(path)):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "terminate"
                ):
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(
            violations,
            [],
            "生产 Qt 代码不得强杀线程: " + ", ".join(violations),
        )


class LongTermChangeBoundaryTests(unittest.TestCase):
    """步骤 14：重构完成后的长期改动规则。

    这里冻结的不是页面外观，而是三件事：配置业务规则只能由 application/domain 实现；
    页面之间只能通过窄接口协作；页面构造上下文不得被新动作持续扩张。
    """

    def test_configuration_business_import_baseline_is_exact(self):
        actual = {}
        for path in sorted(QT_ROOT.rglob("*.py")):
            imports = _configuration_business_imports(path)
            if imports:
                actual[_module_relative_path(path)] = imports
        declared = {
            file: {module: set(names) for module, names in modules.items()}
            for file, modules in CONFIGURATION_BUSINESS_IMPORT_BASELINE.items()
        }

        added = []
        removed = []
        for file in sorted(set(actual) | set(declared)):
            actual_modules = actual.get(file, {})
            declared_modules = declared.get(file, {})
            for module in sorted(set(actual_modules) | set(declared_modules)):
                new_names = actual_modules.get(module, set()) - declared_modules.get(
                    module, set()
                )
                stale_names = declared_modules.get(module, set()) - actual_modules.get(
                    module, set()
                )
                if new_names:
                    added.append(f"{file} imports {module}: {sorted(new_names)}")
                if stale_names:
                    removed.append(
                        f"{file} no longer imports {module}: {sorted(stale_names)}"
                    )

        self.assertEqual(
            added,
            [],
            "Qt 不得新增配置业务实现（application.configuration_* / "
            "domain.configuration.* / device_binding）的依赖；规则应留在 "
            "application/domain，页面只消费 ViewModel、DTO 与结果: " + "; ".join(added),
        )
        self.assertEqual(
            removed,
            [],
            "配置业务导入基线必须与代码精确一致；删除依赖后同步删除登记条目: "
            + "; ".join(removed),
        )

    def test_only_the_page_registry_and_window_assemble_page_types(self):
        """页面不持有另一个页面实例：跨页协作走端口、ViewModel 或窗口转发的信号。"""
        violations = []
        for path in sorted(QT_ROOT.rglob("*.py")):
            if _is_page_assembly_module(path):
                continue
            if _module_relative_path(path) in COMPATIBILITY_PAGE_MODULES:
                continue
            imported = _imported_page_modules(_tree(path))
            if imported:
                violations.append(
                    f"{path.relative_to(ROOT)} imports {sorted(imported)}"
                )
        self.assertEqual(
            violations,
            [],
            "只有 Qt 根目录的 pages.py 与 main_window.py 可以导入页面类型；其它模块"
            "必须使用窄依赖接口，而不是直接持有另一个页面: " + "; ".join(violations),
        )

    def test_page_assembly_exemption_is_path_exact(self):
        """装配根豁免只覆盖 Qt 根目录的两个模块，不按文件名扩散到子目录。"""
        self.assertTrue(_is_page_assembly_module(QT_ROOT / "pages.py"))
        self.assertTrue(_is_page_assembly_module(QT_ROOT / "main_window.py"))
        self.assertFalse(_is_page_assembly_module(QT_ROOT / "ui" / "pages.py"))
        self.assertFalse(_is_page_assembly_module(QT_ROOT / "nested" / "main_window.py"))

    def test_configuration_import_scan_detects_new_import_forms(self):
        """门禁本身要能识别模块族、包内模块、具名导入与字面量动态导入。"""
        tree = ast.parse(
            "import application.configuration_authoring\n"
            "from application import device_binding\n"
            "from domain.configuration import rules\n"
            "from application.device_binding import DeviceBindingService\n"
            "import application.configuration_draft\n"
            "from importlib import import_module\n"
            "import_module('domain.configuration.custom_rules')\n"
            "from application.configuration_view_model import ConfigurationViewModel\n"
        )
        self.assertEqual(
            _configuration_business_imports_in_tree(tree),
            {
                "application.configuration_authoring": {WHOLE_MODULE_IMPORT},
                "application.device_binding": {
                    WHOLE_MODULE_IMPORT,
                    "DeviceBindingService",
                },
                "domain.configuration.rules": {WHOLE_MODULE_IMPORT},
                "application.configuration_draft": {WHOLE_MODULE_IMPORT},
                "domain.configuration.custom_rules": {WHOLE_MODULE_IMPORT},
            },
        )

    def test_page_import_scan_detects_relative_and_absolute_forms(self):
        tree = ast.parse(
            "from .configuration_page import ConfigurationPage\n"
            "import presentation.qt.instrument_setup_page\n"
        )
        self.assertEqual(
            _imported_page_modules(tree),
            {
                "presentation.qt.configuration_page",
                "presentation.qt.instrument_setup_page",
            },
        )

    def test_page_module_registry_covers_every_declared_page_class(self):
        """新增页面必须登记，页面导入门禁才能覆盖它。"""
        declared = {path.stem for path in _declared_page_modules()}
        self.assertEqual(
            declared,
            set(REGISTERED_PAGE_MODULES),
            "新增页面必须在 REGISTERED_PAGE_MODULES 登记，并提供窄依赖接口；"
            "页面类以 Page 结尾（换文件名也不豁免），共享 mixin 不应以 Page 结尾命名。",
        )

    def test_page_constructors_take_narrow_dependencies(self):
        """页面只接收端口、ViewModel、回调与工厂，不接收另一个页面实例。"""
        declared = _declared_page_modules()
        page_names = set().union(*declared.values())
        violations = []
        for path, classes in sorted(declared.items()):
            tree = _tree(path)
            for node in tree.body:
                if isinstance(node, ast.ClassDef) and node.name in classes:
                    violations.extend(
                        f"{path.relative_to(ROOT)}: {violation}"
                        for violation in _page_constructor_violations(node, page_names)
                    )
        self.assertEqual(
            violations,
            [],
            "页面构造只能接收窄依赖（端口、ViewModel、回调、工厂）；不接受 "
            "*args/**kwargs、页面类型注解或另一个页面实例: " + "; ".join(violations),
        )

    def test_page_constructor_scan_rejects_page_dependencies(self):
        """门禁自身要能识别 *args/**kwargs、页面类型注解与页面实例参数。"""
        tree = ast.parse(
            "class DemoPage:\n"
            "    def __init__(self, config_page, parent=None, **options):\n"
            "        pass\n"
            "class TypedPage:\n"
            "    def __init__(self, other: 'ConfigurationPage') -> None:\n"
            "        pass\n"
            "class NarrowPage:\n"
            "    def __init__(self, *, port, parent=None):\n"
            "        pass\n"
            "class DefaultPage:\n"
            "    def __init__(self, factory=ConfigurationPage):\n"
            "        pass\n"
        )
        names = {"ConfigurationPage"}
        demo = _page_constructor_violations(tree.body[0], names)
        typed = _page_constructor_violations(tree.body[1], names)
        narrow = _page_constructor_violations(tree.body[2], names)
        defaulted = _page_constructor_violations(tree.body[3], names)
        self.assertTrue(
            any("**kwargs" in violation for violation in demo), demo
        )
        self.assertTrue(
            any("config_page" in violation for violation in demo), demo
        )
        self.assertTrue(
            any("ConfigurationPage" in violation for violation in typed), typed
        )
        self.assertTrue(any("factory" in violation for violation in defaulted), defaulted)
        self.assertEqual(narrow, [])

    def test_page_context_stays_frozen_and_narrow(self):
        """新增页面动作经 ViewModel/Application facade，不扩张 PageContext。"""
        from presentation.qt.pages import PageContext

        self.assertTrue(
            PageContext.__dataclass_params__.frozen,
            "PageContext 必须是冻结的构造契约，页面不能回写共享上下文",
        )
        actual = tuple(field.name for field in fields(PageContext))
        self.assertEqual(
            actual,
            PAGE_CONTEXT_FIELDS,
            "PageContext 是页面的窄组合接口。新增动作应加在 ViewModel/Application facade "
            "并用页面自己的一个信号表达；确需调整此契约时，先更新 "
            "本文件的长期规则用例与本基线。",
        )
        hints = get_type_hints(PageContext)
        for field in fields(PageContext):
            if field.name == "controller":
                continue
            self.assertIs(
                get_origin(hints[field.name]),
                collections.abc.Callable,
                f"PageContext.{field.name} 必须是回调或共享 controller，不能塞入页面对象",
            )


class RunFlowEntryBoundaryTests(unittest.TestCase):
    """阶段五：单一运行流程的页面入口基线。

    旧流程的“连接仪器”按钮与独立连接入口必须保持删除；最终化、重连和补偿只能经
    “页面动作 → 窗口 → 协调器最终化事务”这一条链完成，不能重新出现第二个提交端口
    的入口。
    """

    def test_retired_run_flow_entries_stay_deleted(self):
        offenders = []
        for path in sorted(QT_ROOT.rglob("*.py")):
            for name, lineno in retired_run_flow_sites(_tree(path)):
                offenders.append(f"{_module_relative_path(path)}:{lineno} {name}")
        self.assertEqual(
            offenders,
            [],
            "设备发现页的连接按钮与参数页的独立连接入口已在阶段一删除，不得以属性、"
            "名字或字符串访问回到生产 Qt 代码: " + ", ".join(offenders),
        )

    def test_finalization_and_compensation_entries_are_frozen(self):
        missing = []
        for file, names in FINALIZATION_ENTRY_BASELINE.items():
            path = QT_ROOT / file
            if not path.is_file():
                missing.append(f"{file} 不存在")
                continue
            declared = declared_identifier_names(_tree(path))
            missing.extend(
                f"{file} 缺少最终化/补偿入口 {name}"
                for name in names
                if name not in declared
            )
        self.assertEqual(
            missing,
            [],
            "最终化、重连与失败补偿的入口必须保留在同一条链上；删除或改名会让"
            "“运行已创建但端口未连接”重新有缝可钻。新增入口先更新本基线: "
            + "; ".join(missing),
        )

    def test_run_flow_entry_scan_detects_all_reference_forms(self):
        tree = ast.parse(
            "page.connect_btn.setEnabled(True)\n"
            "page.connect_requested.emit()\n"
            "getattr(page, 'request_connect')()\n"
            "class Page:\n"
            "    create_run_requested = Signal()\n"
            "    def _set_connect_enabled(self):\n"
            "        pass\n"
        )
        self.assertEqual(
            [name for name, _lineno in retired_run_flow_sites(tree)],
            [
                "connect_btn",
                "connect_requested",
                "request_connect",
                "create_run_requested",
                "_set_connect_enabled",
            ],
        )

    def test_page_requests_finalization_only_through_its_signal(self):
        """参数页不直接调用协调器：最终动作只经页面自己的信号交给窗口。"""
        page = function_definitions(_tree(QT_ROOT / "configuration_page.py"))
        request = page.get("request_finalize_run")
        self.assertIsNotNone(
            request, "参数页必须保留唯一的最终动作请求入口 request_finalize_run"
        )
        self.assertIn(
            "finalize_run_requested",
            emitted_signal_names(request),
            "最终动作请求必须发出 finalize_run_requested，由窗口转给协调器",
        )

    def test_window_wires_the_finalization_signal_and_transaction(self):
        """窗口必须把页面信号接到唯一最终化入口，并接住成功/失败/取消。"""
        window = _tree(MAIN_WINDOW)
        connections = signal_connections(window)
        definitions = function_definitions(window)
        missing = []
        for signal, slot in FINALIZATION_WINDOW_CONNECTIONS.items():
            if slot not in connections.get(signal, set()):
                missing.append(f"{signal} 未连接到 {slot}")
        for function in WINDOW_FINALIZATION_ENTRY_NAMES:
            if function not in definitions:
                missing.append(f"窗口缺少入口 {function}")
        for function, calls in FINALIZATION_WINDOW_CALLS.items():
            node = definitions.get(function)
            if node is None:
                continue
            actual = called_names(node)
            missing.extend(
                f"{function} 未调用 {called}"
                for called in calls
                if called not in actual
            )
        self.assertEqual(
            missing,
            [],
            "最终化只能经“页面信号 → 窗口入口 → 协调器事务”这一条链；"
            "成功、失败与取消必须分别落到提交、补偿和取消入口: "
            + "; ".join(missing),
        )


if __name__ == "__main__":
    unittest.main()
