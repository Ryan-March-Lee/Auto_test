r"""门禁：异常写法、输出、旧模块导入与动态设备路由。

本文件与[异常分类与静态质量门禁](../docs/异常分类与静态质量门禁.md)一一对应。门禁覆盖六类静态问题：

1. **裸 ``except``**：生产代码一处都不允许，必须写明捕获类型。
2. **静默宽泛捕获**：``except Exception`` / ``except BaseException`` 的处理器不得只有
   ``pass`` / ``...``；失败必须被记录、降级或重新抛出。
3. **宽泛捕获基线**：存量宽泛捕获按“文件 + 所在作用域”精确冻结（只减不增）。每个位置
   都有稳定身份，删掉一处再在别的函数里新增一处不会因为处数相同而蒙混过关。
4. **生产 ``print``**：正式运行包不向标准输出打印，诊断走日志或事件。
5. **旧模块导入**：生产代码不得导入已删除的旧入口；第一方模块路径必须真实存在。
6. **动态设备路由**：不得按运行时名字访问设备能力（``getattr(<设备>, <变量>)``）、
   不得 ``getattr(<设备>, <名字>)(...)`` 立即转发、不得保存后调用设备方法、不得对设备
   对象 ``setattr``。

验证方式：

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" -m unittest tests.test_static_quality_gate -v
```
"""

import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]

# 正式运行时包：异常写法与输出都受门禁约束。
RUNTIME_PACKAGES = (
    "app",
    "application",
    "domain",
    "infrastructure",
    "instrument",
    "presentation",
)
# 额外的第一方包：只参与异常写法/导入检查（分析层可打印报告）。
EXTRA_PACKAGES = ("analysis",)
# 根目录正式入口脚本：CLI 输出用 print 是它的接口，因此不参与 print 门禁。
ROOT_MODULES = ("launcher.py", "release_manifest.py")

# 宽泛捕获的存量基线：文件 -> 站点身份元组，必须与代码严格相等（只减不增）。
#
# 站点身份是 ``<作用域>#<该作用域内的序号>``（模块级作用域写作 ``<module>``），例如
# ``SafetyCleanupAdapter.cleanup#2``。它只随“把处理器换到另一个函数”或“在同一函数里
# 增删处理器”变化，不随行号移动变化：既不会因为整文件挪动而失效，也不会允许在别的
# 函数里新增一处宽泛捕获来顶替被删除的一处。
#
# 允许存在的三类边界：
#   - 清理聚合：无论设备抛出什么异常都要继续执行后续安全动作，最后一次性上报；
#   - 边界翻译：把第三方库异常翻译成本项目的分类异常，并保留原始异常链；
#   - 尽力记录：失败进入日志/阶段状态/事件，不改变原始返回值或异常。
# DPD_auto_test、hardware/、format_convert/ 与 tools/ 不属于正式测量链路。
BROAD_CATCH_BASELINE = {
    # 边界翻译 / 尽力记录（装配与运行记录）
    "app/gui_runtime.py": (
        "_close_owned_measurement_port#1",
        "_ensure_domain_snapshot_is_intact#1",
        "create_cable_loss_measurement#1",
        "create_driver_mapping_measurement#1",
        "create_amplifier_measurement#1",
    ),
    "application/measurements/amplifier_test.py": (
        "AmplifierMeasurementUseCase.measure_all_frequencies#1",
        "AmplifierMeasurementUseCase._try_record#1",
    ),
    "application/measurements/cable_loss.py": (
        "CableLossUseCase.measure_all_frequencies#1",
        "CableLossUseCase.continue_to_step2#1",
        "CableLossUseCase._try_record#1",
    ),
    "application/measurements/driver_mapping.py": (
        "DriverPowerMappingUseCase.measure_all_frequencies#1",
        "DriverPowerMappingUseCase._try_record#1",
    ),
    "application/run_service.py": (
        "create_run_snapshot#1",
        "_discard_created_run#1",
    ),
    "application/run_stages.py": (
        "RunStageRecorder.complete#1",
        "RunStageRecorder._discard_quietly#1",
    ),
    "infrastructure/filesystem/atomic_json.py": (
        "write_json_atomic#1",
    ),
    "infrastructure/persistence/json_result_repository.py": (
        "save_json_result#1",
        "write_run_snapshot#1",
        "save_measurement_result#1",
    ),
    "infrastructure/persistence/result_repository.py": (
        "FileMeasurementResultRepository.save#1",
    ),
    "infrastructure/persistence/run_directory.py": (
        "create_empty_run#1",
    ),
    "infrastructure/persistence/run_repository.py": (
        "RunRepository._create_run#1",
    ),
    # 清理聚合（安全动作必须全部尝试后统一上报）
    "instrument/action/context.py": (
        "ActionBase._call#1",
        "ActionBase._emit#1",
    ),
    "instrument/flow/cleanup.py": (
        "cleanup_measurement#1",
        "cleanup_measurement#2",
        "cleanup_measurement#3",
        "SafetyCleanupAdapter.cleanup.rf_off#1",
        "SafetyCleanupAdapter.cleanup.power_off#1",
        "SafetyCleanupAdapter.cleanup.close#1",
    ),
    "instrument/flow/power_off_flow.py": (
        "PowerOffFlow.run#1",
    ),
    "instrument/flow/power_on_flow.py": (
        "PowerOnFlow.run#1",
        "PowerOnFlow.run#2",
        "PowerOnFlow._step#1",
    ),
    "instrument/flow/safety_shutdown_flow.py": (
        "SafetyShutdownFlow.run#1",
        "SafetyShutdownFlow.run#2",
        "SafetyShutdownFlow.run#3",
    ),
    "instrument/power_control.py": (
        "PowerController.power_off#1",
    ),
    "instrument/safety.py": (
        "shutdown_instruments#1",
        "shutdown_instruments#2",
        "shutdown_instruments#3",
    ),
    "instrument/session.py": (
        "ManagedInstrumentSession.connect#1",
        "ManagedInstrumentSession.connect#2",
        "ManagedInstrumentSession.prepare#1",
        "ManagedInstrumentSession.prepare#2",
        "ManagedInstrumentSession.power_on#1",
        "ManagedInstrumentSession.power_on#2",
        "ManagedInstrumentSession.close.close_owned#1",
    ),
    # 边界翻译（VISA/SCPI 第三方异常、装配失败后的资源释放、仿真替身）
    "instrument/measurement_adapter.py": (
        "PortMeasurementAdapter.emergency_power_off_all#1",
        "PortMeasurementAdapter.close_all#1",
        "PortMeasurementAdapter.close_all#2",
        "PortMeasurementAdapter.close_all#3",
    ),
    "instrument/measurement_factory.py": (
        "_ConfiguredPowerSupply.close#1",
        "_create_hardware_measurement_port#1",
        "_create_hardware_measurement_port#2",
        "_create_hardware_measurement_port#3",
    ),
    "instrument/simulation.py": (
        "_LegacySafetyInstrumentSession.connect#1",
        "_LegacySafetyInstrumentSession.connect#2",
        "_LegacySafetyInstrumentSession.prepare#1",
        "_LegacySafetyInstrumentSession.prepare#2",
        "_LegacySafetyInstrumentSession.power_on#1",
        "_LegacySafetyInstrumentSession.power_on#2",
        "_LegacySafetyInstrumentSession.power_off#1",
        "_LegacySafetyInstrumentSession.close#1",
        "_LegacySafetyInstrumentSession.close#2",
        "_LegacySafetyInstrumentSession.close#3",
    ),
    "instrument/transport/visa_transport.py": (
        "VisaScpiTransport.__init__#1",
        "VisaScpiTransport.write#1",
        "VisaScpiTransport.set_timeout_s#1",
        "VisaScpiTransport.query#1",
        "VisaScpiTransport.close#1",
    ),
    # 表现层：错误必须经由状态、对话框或日志呈现给用户
    "presentation/qt/application_coordinator.py": (
        "close_instrument_port#1",
        "close_instrument_port#2",
        "ApplicationCoordinator.load_configuration#1",
        "ApplicationCoordinator.save_configuration#1",
        "ApplicationCoordinator.create_prepared_run#1",
        "ApplicationCoordinator.load_prepared_run#1",
        "ApplicationCoordinator.apply_run_configuration#1",
    ),
    "presentation/qt/run_history_page.py": (
        "RunHistoryPage.export#1",
    ),
    "presentation/qt/measurement_controller.py": (
        "MeasurementController._start#1",
        "MeasurementController._request_stop#1",
        "MeasurementController.continue_cable_loss#1",
    ),
    "presentation/qt/pages.py": (
        "PageControllerBindingMixin.bind_controller#1",
        "PageControllerBindingMixin.close#1",
        "PageControllerBindingMixin._disconnect_controller_signals#1",
    ),
    "presentation/qt/realtime_plot.py": (
        "RealTimePlotWidget.update_plot#1",
    ),
    "presentation/qt/visualization_page.py": (
        "VisualizationPage.load_test_data#1",
        "VisualizationPage.generate_report#1",
    ),
    "presentation/qt/workers.py": (
        "InstrumentWorker.run#1",
        "InstrumentWorker.run#2",
        "CableLossWorker.run#1",
        "CableLossWorker.run#2",
        "DriverMappingWorker._run_measurement#1",
        "DriverMappingWorker._run_measurement#2",
        "AmplifierWorker.run#1",
        "AmplifierWorker.run#2",
    ),
    # CLI 启动器的边界处理（解释器、依赖、配置、GUI 启动）
    "launcher.py": (
        "list_runs#1",
        "check_packages#1",
        "launch_gui_version#1",
        "main#1",
        "main#2",
    ),
}


# 打印门禁只约束正式运行包；launcher/release_manifest/analysis 等入口脚本有自己的输出契约。
PRINT_FORBIDDEN_PACKAGES = RUNTIME_PACKAGES

# 设备对象命名线索：这些线索出现即视为设备/仪器对象（区分大小写无关的子串匹配）。
DEVICE_OBJECT_HINTS = (
    "inst_ctrl",
    "instrument",
    "signal_gen",
    "signal_generator",
    "spectrum",
    "power_supply",
)
# 通用段名：只有整段名字恰好是这些词时才视为设备对象，避免 ``driver_plot_widget``
# 这类控件名被误判成设备。
DEVICE_OBJECT_TOKENS = frozenset({"device", "driver", "dut", "instrument", "session"})

# 按名字访问设备能力唯一允许的边界：能力检查函数需要按名字探测设备能力，并在缺失时
# 抛出 ``InstrumentCapabilityError``（构造阶段失败，而不是硬件动作中途 AttributeError）。
# 新增条目等于新增一处“按名字访问设备”，必须在评审中说明理由；集合必须与代码严格相等。
DYNAMIC_ROUTING_BOUNDARIES = frozenset(
    {
        ("instrument/ports.py", "require_measurement_capabilities"),
    }
)


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8-sig"), str(path))


def _package_sources(*packages):
    sources = []
    for package in packages:
        sources.extend(sorted((ROOT / package).rglob("*.py")))
    return sources


def _production_sources():
    return _package_sources(*RUNTIME_PACKAGES, *EXTRA_PACKAGES) + [
        ROOT / name for name in ROOT_MODULES
    ]


def _relative(path):
    return path.relative_to(ROOT).as_posix()


def _is_broad_handler(node):
    """处理器是否捕获 ``Exception`` / ``BaseException``（含元组形式）。"""
    if not isinstance(node, ast.ExceptHandler) or node.type is None:
        return False
    names = []
    if isinstance(node.type, ast.Tuple):
        names = [element.id for element in node.type.elts if isinstance(element, ast.Name)]
    elif isinstance(node.type, ast.Name):
        names = [node.type.id]
    return bool({"Exception", "BaseException"} & set(names))


def _broad_catches(tree):
    """返回 ``except Exception`` / ``except BaseException`` / ``except (…)`` 处理器。"""
    return [node for node in ast.walk(tree) if _is_broad_handler(node)]


def _bare_catches(tree):
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler) and node.type is None
    ]


def _scope_bodies(tree):
    """按作用域产出 ``(作用域名, 该作用域自身的节点)``，不进入嵌套函数/类。

    作用域名用 ``类.函数`` 表示（方法内的嵌套函数为 ``类.方法.内层函数``），模块级
    作用域名是 ``<module>``。外层作用域先于内层出现，每个节点的归属唯一，因此同一个
    处理器不会被统计两次。
    """
    results = []

    def collect(node, scope):
        nodes = []
        nested = []

        def walk(current):
            nodes.append(current)
            for child in ast.iter_child_nodes(current):
                if isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    name = f"{scope}.{child.name}" if scope != "<module>" else child.name
                    nested.append((child, name))
                    continue
                walk(child)

        walk(node)
        results.append((scope, nodes))
        for child, child_scope in nested:
            collect(child, child_scope)

    collect(tree, "<module>")
    return results


def broad_catch_sites(tree):
    """返回宽泛捕获的站点身份元组，元素形如 ``<作用域>#<序号>``，按源代码顺序。"""
    sites = []
    for scope, nodes in _scope_bodies(tree):
        ordinal = 0
        for node in nodes:
            if _is_broad_handler(node):
                ordinal += 1
                sites.append(f"{scope}#{ordinal}")
    return tuple(sites)


def _is_silent(handler):
    """处理器是否只有 ``pass`` / ``...`` / ``continue``，即静默吞掉失败。"""
    for statement in handler.body:
        if isinstance(statement, ast.Pass):
            continue
        if isinstance(statement, ast.Continue):
            continue
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
            continue
        return False
    return bool(handler.body)


def _printed_calls(tree):
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
    ]


def _first_party_roots():
    excluded = {
        ".git",
        ".idea",
        "__pycache__",
        "docs",
        "logs",
        "test_results",
        "baseline",
        "assets",
    }
    roots = set()
    for entry in ROOT.iterdir():
        if entry.name in excluded:
            continue
        if entry.is_dir() and (entry / "__init__.py").is_file():
            roots.add(entry.name)
        elif entry.is_file() and entry.suffix == ".py":
            roots.add(entry.stem)
    return roots


def _module_exists(module):
    parts = module.split(".")
    if (ROOT.joinpath(*parts).with_suffix(".py")).is_file():
        return True
    return ROOT.joinpath(*parts, "__init__.py").is_file()


def _absolute_import_names(tree):
    """返回全部绝对导入的模块名（不按包根筛选），用于旧模块登记表检查。"""
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            names.append(node.module)
    return names


def _first_party_imports(tree, path=None):
    """返回第一方模块名：绝对导入按实际存在的包根筛选，相对导入按文件位置解析。"""
    roots = _first_party_roots()
    absolute = []
    relative = []
    package_parts = [] if path is None else list(path.relative_to(ROOT).parent.parts)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            absolute.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.module:
            # ``from . import x`` 的 x 可能是模块也可能是名字，交给包内登记表检查。
            continue
        elif isinstance(node, ast.ImportFrom) and node.level:
            base = package_parts[: len(package_parts) - (node.level - 1)]
            relative.append(".".join([*base, node.module]))
        elif isinstance(node, ast.ImportFrom):
            absolute.append(node.module)
    return [
        module for module in absolute if module.split(".")[0] in roots
    ] + relative


def _retired_module_names():
    """旧模块登记表来自步骤 1 的门禁，避免第二份“旧入口”清单。"""
    from tests.test_public_api_inventory import LEGACY_MODULES

    return frozenset(LEGACY_MODULES)


def _retired_imports(modules, retired):
    return [
        module
        for module in modules
        if any(module == name or module.startswith(name + ".") for name in retired)
    ]


def _dotted_name(node):
    """把 ``self.inst_ctrl`` 之类的表达式还原成小写点号名，供设备对象匹配。"""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr.lower())
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id.lower())
    return ".".join(reversed(parts))


def _matches_device(name):
    """``name`` 是否像设备对象：``self.inst_ctrl``、``self._driver``、``session``。

    线索（``instrument`` 等）按子串匹配；通用段名（``driver`` 等）只有整段等于该词时
    才算设备，避免把 ``driver_plot_widget``、``report`` 之类的控件名误判成设备。
    """
    if not name:
        return False
    normalized = name.lower()
    if any(hint in normalized for hint in DEVICE_OBJECT_HINTS):
        return True
    last_segment = re.split(r"\.", normalized)[-1].strip("_")
    return last_segment in DEVICE_OBJECT_TOKENS


def _device_aliases(nodes):
    """收集“设备对象别名”：``alias = <设备表达式>`` 之后 ``alias`` 也算设备对象。"""
    aliases = set()
    for node in nodes:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, (ast.Name, ast.Attribute)):
            continue
        if not _is_device_ref(node.value, aliases):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                aliases.add(target.id)
    return aliases


def _is_device_ref(node, aliases):
    if isinstance(node, ast.Name):
        return node.id in aliases or _matches_device(node.id.lower())
    if isinstance(node, ast.Attribute):
        return _matches_device(_dotted_name(node))
    return False


def _literal_name(node):
    """返回 getattr/setattr 的名字实参（仅当它是字符串字面量）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _getattr_device_arg(node, aliases):
    """若 ``node`` 是 ``getattr(<设备>, ...)``，返回设备实参，否则 ``None``。"""
    if not isinstance(node, ast.Call):
        return None
    if not (isinstance(node.func, ast.Name) and node.func.id == "getattr" and node.args):
        return None
    return node.args[0] if _is_device_ref(node.args[0], aliases) else None


def _saved_device_methods(nodes, aliases):
    """收集 ``method = getattr(<设备>, <名字>)`` 保存下来的变量名。"""
    saved = {}
    for node in nodes:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        if _getattr_device_arg(node.value, aliases) is None:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                saved[target.id] = node.value
    return saved


def dynamic_routing_sites(tree, relative_path):
    """返回按名字访问设备的节点，按源码位置排序。

    四类违规：

    - ``getattr(<设备>, <变量>)``：用运行时名字选设备能力（含只查询不调用）；
    - ``getattr(<设备>, <名字>)()``：立即转发；
    - ``method = getattr(<设备>, <名字>)`` 之后 ``method(...)``：保存后转发；
    - ``setattr(<设备>, ...)``：按名字改写设备状态。

    ``DYNAMIC_ROUTING_BOUNDARIES`` 中的能力检查函数允许按名字“探测”（不调用）。
    """
    violations = []
    for scope, nodes in _scope_bodies(tree):
        allowed = (relative_path, scope) in DYNAMIC_ROUTING_BOUNDARIES
        aliases = _device_aliases(nodes)
        saved = _saved_device_methods(nodes, aliases)
        # 立即转发的 getattr 节点由外层调用计入一次，避免同一处算成两条。
        dispatched = {
            id(node.func)
            for node in nodes
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Call)
            and _getattr_device_arg(node.func, aliases) is not None
        }
        for node in nodes:
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Call) and _getattr_device_arg(func, aliases) is not None:
                violations.append(node)
                continue
            if isinstance(func, ast.Name) and func.id in saved:
                violations.append(node)
                continue
            if (
                isinstance(func, ast.Name)
                and func.id == "setattr"
                and node.args
                and _is_device_ref(node.args[0], aliases)
            ):
                violations.append(node)
                continue
            if allowed or id(node) in dispatched:
                continue
            if _getattr_device_arg(node, aliases) is None:
                continue
            if len(node.args) > 1 and _literal_name(node.args[1]) is None:
                violations.append(node)
    return sorted(violations, key=lambda item: (item.lineno, item.col_offset))


class BareExceptTests(unittest.TestCase):
    """裸 ``except`` 无法区分失败类别，生产代码一处都不允许。"""

    def test_production_code_has_no_bare_except(self):
        offenders = [
            f"{_relative(path)}:{node.lineno}"
            for path in _production_sources()
            for node in _bare_catches(_tree(path))
        ]
        self.assertEqual(
            offenders,
            [],
            "生产代码不得使用裸 except，必须写明捕获的异常类型: " + ", ".join(offenders),
        )


class BroadExceptTests(unittest.TestCase):
    """宽泛捕获必须被记录，且存量只减不增。"""

    def test_broad_catch_baseline_matches_production_code(self):
        actual = {}
        for path in _production_sources():
            sites = broad_catch_sites(_tree(path))
            if sites:
                actual[_relative(path)] = tuple(sites)
        self.assertEqual(
            actual,
            BROAD_CATCH_BASELINE,
            "宽泛捕获基线必须与代码严格相等：新增一处宽泛捕获等于把失败类别重新混在一起；"
            "收窄或删除后同步收缩基线（不是扩大基线）",
        )

    def test_broad_except_never_silently_swallows(self):
        offenders = [
            f"{_relative(path)}:{handler.lineno}"
            for path in _production_sources()
            for handler in _broad_catches(_tree(path))
            if _is_silent(handler)
        ]
        self.assertEqual(
            offenders,
            [],
            "宽泛捕获不得静默吞掉失败（pass/...），必须记录、降级或重新抛出: "
            + ", ".join(offenders),
        )

    def test_detectors_recognise_broad_and_bare_handlers(self):
        tree = ast.parse(
            "try:\n    run()\n"
            "except Exception:\n    pass\n"
            "except BaseException as error:\n    raise\n"
            "except (ValueError, Exception):\n    log(error)\n"
            "except:\n    raise\n"
        )
        self.assertEqual(len(_broad_catches(tree)), 3)
        self.assertEqual(len(_bare_catches(tree)), 1)
        silent = [handler for handler in _broad_catches(tree) if _is_silent(handler)]
        self.assertEqual(len(silent), 1)

    def test_site_identity_tracks_the_enclosing_scope(self):
        """同文件内“删一处、在别的函数里加一处”必须被识别为不同站点。"""
        tree = ast.parse(
            "def first():\n"
            "    try:\n        run()\n    except Exception:\n        log()\n"
            "class Runner:\n"
            "    def second(self):\n"
            "        try:\n            run()\n        except Exception:\n            log()\n"
            "        try:\n            run()\n        except Exception:\n            log()\n"
        )
        self.assertEqual(
            broad_catch_sites(tree),
            ("first#1", "Runner.second#1", "Runner.second#2"),
        )

        moved = ast.parse(
            "def first():\n"
            "    try:\n        run()\n    except Exception:\n        log()\n"
            "    try:\n        run()\n    except Exception:\n        log()\n"
        )
        swapped = ast.parse(
            "def first():\n"
            "    try:\n        run()\n    except Exception:\n        log()\n"
            "def third():\n"
            "    try:\n        run()\n    except Exception:\n        log()\n"
        )
        # 处数相同、位置不同：按文件计数的旧基线会放行，按作用域的站点会拦住。
        self.assertEqual(len(broad_catch_sites(moved)), len(broad_catch_sites(swapped)))
        self.assertEqual(broad_catch_sites(moved), ("first#1", "first#2"))
        self.assertEqual(broad_catch_sites(swapped), ("first#1", "third#1"))


class ProductionPrintTests(unittest.TestCase):
    """正式运行包通过日志/事件报告，不向标准输出打印。"""

    def test_runtime_packages_do_not_print(self):
        offenders = [
            f"{_relative(path)}:{node.lineno}"
            for path in _package_sources(*PRINT_FORBIDDEN_PACKAGES)
            for node in _printed_calls(_tree(path))
        ]
        self.assertEqual(
            offenders,
            [],
            "正式运行包不得使用 print；诊断走 logging 或事件回调: " + ", ".join(offenders),
        )

    def test_print_detector_ignores_attribute_calls(self):
        tree = ast.parse("print('x')\nconsole.print('y')\n")
        self.assertEqual(len(_printed_calls(tree)), 1)


class LegacyImportTests(unittest.TestCase):
    """旧入口已删除：生产代码不得再导入它们，也不得导入不存在的模块路径。"""

    def test_production_code_imports_no_retired_modules(self):
        retired = _retired_module_names()
        offenders = []
        for path in _production_sources():
            for module in _retired_imports(_absolute_import_names(_tree(path)), retired):
                offenders.append(f"{_relative(path)} imports {module}")
        self.assertEqual(
            offenders,
            [],
            "生产代码不得导入已退役的旧模块（旧入口由步骤 1 的登记表唯一登记）: "
            + "; ".join(offenders),
        )

    def test_production_imports_resolve_to_existing_modules(self):
        missing = []
        for path in _production_sources():
            for module in _first_party_imports(_tree(path), path):
                if not _module_exists(module):
                    missing.append(f"{_relative(path)} imports {module}")
        self.assertEqual(
            missing,
            [],
            "生产代码导入了不存在的第一方模块路径（含已退役的包内旧实现）: "
            + "; ".join(missing),
        )

    def test_detectors_report_retired_roots_and_missing_submodules(self):
        tree = ast.parse(
            "import config_io\n"
            "from measurement_services import create_cable_loss_measurement\n"
            "from application.ports.result_history import ResultHistoryRepository\n"
            "from application.run_service import create_run_snapshot\n"
        )
        retired = _retired_module_names()
        self.assertEqual(
            sorted(_retired_imports(_absolute_import_names(tree), retired)),
            [
                "application.ports.result_history",
                "config_io",
                "measurement_services",
            ],
        )
        self.assertEqual(
            sorted(
                module
                for module in _first_party_imports(tree, ROOT / "test_probe.py")
                if not _module_exists(module)
            ),
            ["application.ports.result_history"],
        )


class DynamicDeviceRoutingTests(unittest.TestCase):
    """设备能力必须由显式端口方法表达，不按名字转发。"""

    def test_production_code_has_no_dynamic_device_routing(self):
        offenders = [
            f"{_relative(path)}:{node.lineno}"
            for path in _package_sources(*RUNTIME_PACKAGES)
            for node in dynamic_routing_sites(_tree(path), _relative(path))
        ]
        self.assertEqual(
            offenders,
            [],
            "不得按名字访问或转发设备能力；请显式声明端口方法: " + ", ".join(offenders),
        )

    def test_routing_boundaries_stay_minimal(self):
        """按名字访问设备的豁免只能是登记过的能力检查函数。"""
        self.assertEqual(
            DYNAMIC_ROUTING_BOUNDARIES,
            frozenset({("instrument/ports.py", "require_measurement_capabilities")}),
        )

    def test_detector_flags_device_forwarding_only(self):
        tree = ast.parse(
            "getattr(self.inst_ctrl, name)()\n"
            "setattr(self.session, 'state', value)\n"
            "getattr(self._worker, method)()\n"
            "getattr(control, 'signal_gen', None)\n"
        )
        flagged = dynamic_routing_sites(tree, "probe.py")
        self.assertEqual(len(flagged), 2)
        self.assertEqual([node.lineno for node in flagged], [1, 2])

    def test_detector_flags_indirect_and_dynamic_lookups(self):
        tree = ast.parse(
            "method = getattr(self.device, name)\n"
            "method(1)\n"
            "probe = getattr(self.inst_ctrl, name, None)\n"
            "alias = self.signal_generator\n"
            "getattr(alias, 'set_power')(-10)\n"
            "value = getattr(self.spectrum_analyzer, 'center_frequency_hz', None)\n"
        )
        flagged = dynamic_routing_sites(tree, "probe.py")
        self.assertEqual([node.lineno for node in flagged], [1, 2, 3, 5])

    def test_detector_allows_presence_probes_and_widget_lookups(self):
        tree = ast.parse(
            "getattr(port, 'close_all', None)\n"
            "getattr(self.driver_plot_widget, 'update_plot', None)\n"
            "has_rf = getattr(instrument_control, 'signal_gen', None) is not None\n"
        )
        self.assertEqual(dynamic_routing_sites(tree, "probe.py"), [])

    def test_detector_honours_the_capability_checker_boundary(self):
        tree = ast.parse(
            "def require_measurement_capabilities(instrument, capabilities):\n"
            "    return [name for name in capabilities if not callable(getattr(instrument, name, None))]\n"
        )
        self.assertEqual(
            len(
                dynamic_routing_sites(
                    tree, "instrument/ports.py"
                )
            ),
            0,
            "登记的能力检查函数允许按名字探测能力",
        )
        self.assertEqual(
            len(dynamic_routing_sites(tree, "instrument/other.py")),
            1,
            "同样的写法出现在别的模块必须被拦截",
        )


if __name__ == "__main__":
    unittest.main()
