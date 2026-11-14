r"""门禁：异常写法、输出、旧模块导入、动态设备路由与运行流程入口。

本文件是静态质量门禁的契约测试，覆盖七类静态问题：

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
7. **旧运行流程入口与最终化链**：独立“连接仪器”按钮与 ``request_connect`` 链不得回到
   生产代码；最终化、提交、补偿与阶段结算的直接调用关系必须保持，未开始运行的撤销和
   阶段端口结算必须继续走唯一守卫，事务入口只允许由主窗口调用。

验证方式：

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" -m unittest tests.test_static_quality_gate -v
```
"""

import ast
import re
import unittest
from pathlib import Path

from tests.support.run_flow_contract import (
    COORDINATOR_FINALIZATION_ENTRY_NAMES,
    SINGLE_FINALIZATION_ENTRY_NAMES,
    WINDOW_FINALIZATION_ENTRY_NAMES,
    call_sites,
    missing_contract_items,
    parse_source,
    retired_run_flow_sites,
)


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
# hardware/、format_convert/ 与 tools/ 不属于正式测量链路。
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
        # 与线损/驱动映射同一类边界：阶段文件已提交后的运行记账失败要翻译成
        # StageResultNotRecorded；交付产物失败只记录日志，不改变正式结果。
        "AmplifierMeasurementUseCase._save_stage_result#1",
        "AmplifierMeasurementUseCase._generate_stage_artifacts#1",
    ),
    "application/measurements/cable_loss.py": (
        "CableLossUseCase.measure_all_frequencies#1",
        "CableLossUseCase.continue_to_step2#1",
        "CableLossUseCase._try_record#1",
        "CableLossUseCase._save#1",
    ),
    "application/measurements/driver_mapping.py": (
        "DriverPowerMappingUseCase.measure_all_frequencies#1",
        "DriverPowerMappingUseCase._try_record#1",
        "DriverPowerMappingUseCase._save#1",
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
        "_discard_temporary_file#1",
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
    "infrastructure/persistence/stage_result_repository.py": (
        "FileStageResultRepository._discard#1",
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
    "instrument/flow/safety_shutdown_flow.py": (
        "SafetyShutdownFlow.run#1",
        "SafetyShutdownFlow.run#2",
        "SafetyShutdownFlow.run#3",
    ),
    "instrument/power_control.py": (
        # 上电失败与取消路径都要先逆序关闭已成功通道，再保留原始失败/取消。
        "PowerController.power_on#1",
        "PowerController._cancellation_guard#1",
        "PowerController._shutdown#1",
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
        "ManagedInstrumentSession.power_off#1",
        "ManagedInstrumentSession.close.close_owned#1",
    ),
    # 边界翻译（VISA/SCPI 第三方异常、装配失败后的资源释放、仿真替身）
    "instrument/measurement_adapter.py": (
        "PortMeasurementAdapter.end_stage#1",
        "PortMeasurementAdapter.emergency_power_off_all#1",
        "PortMeasurementAdapter.close_all#1",
        "PortMeasurementAdapter.close_all#2",
    ),
    "instrument/measurement_factory.py": (
        "_ConfiguredPowerSupply.close#1",
        "_create_hardware_measurement_port#1",
        "_create_hardware_measurement_port#2",
        "_create_hardware_measurement_port#3",
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
        "settle_instrument_stage#1",
        "ApplicationCoordinator.load_configuration#1",
        "ApplicationCoordinator.save_configuration#1",
        "ApplicationCoordinator._freeze_pending_run#1",
        "ApplicationCoordinator._compensate_pending_run#1",
        "ApplicationCoordinator.load_prepared_run#1",
        "ApplicationCoordinator.apply_run_configuration#1",
        "ApplicationCoordinator._start_connection_worker#1",
    ),
    "presentation/qt/measurement_controller.py": (
        "MeasurementController._start#1",
        "MeasurementController._request_stop#1",
        "MeasurementController.continue_cable_loss_stage2#1",
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
        "VisualizationPage.export#1",
    ),
    "presentation/qt/workers.py": (
        "InstrumentWorker._close_own_port#1",
        "InstrumentWorker.run#1",
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
        "launch_gui#1",
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
    return parse_source(path)


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


# 已退役的旧入口模块名：删除后不得重新引入。
RETIRED_MODULE_NAMES = frozenset(
    {
        "cable_loss_measurement",
        "driver_power_mapping",
        "amplifier_measurement",
        "app.legacy_amplifier_measurement",
        "config_io",
        "config_models",
        "config_validation",
        "infrastructure.config.legacy_conversion",
        "result_reading",
        "application.dto.legacy_result_adapter",
        "domain.result_reading",
        "application.ports.result_history",
        "infrastructure.persistence.result_history_repository",
        "result_storage",
        "instrument.legacy_control",
        "instrument.power_roles",
        "presentation.qt.configuration_authoring",
        "measurement_calculations",
        "measurement_lifecycle",
        "measurement_services",
        "app_logging",
        "project_paths",
        "legacy_config_conversion",
        "enhanced_main_gui",
        "llm",
        "web_search_tools",
        "instrument_control",
        "data_visualization",
        "connection_diagrams",
        "persistence",
    }
)

# 最终化与失败补偿的调用链基线：文件 -> 必须存在的定义、导入与直接调用。
# - 未开始运行只能经 ``application.run_service.discard_unstarted_run`` 撤销，协调器
#   不得直接删除运行目录；
# - 阶段成功复用只经 ``end_stage`` 结算，异常阶段必须关闭端口；
# - 最终化入口与补偿步骤保持在同一事务里，不能被第二个提交入口绕过。
#
# 只冻结“谁直接调用谁”，不做控制流推理：方法仍在、但事务链断开是本门禁要拦的回归。
# 入口名称集合与 AST 辅助函数单源在 ``tests/support/run_flow_contract.py``。
FINALIZATION_COMPENSATION_CHAIN = {
    "application/run_service.py": {
        "definitions": ("discard_unstarted_run",),
    },
    "presentation/qt/application_coordinator.py": {
        "definitions": COORDINATOR_FINALIZATION_ENTRY_NAMES,
        "imports": (("application.run_service", "discard_unstarted_run"),),
        "calls": (
            ("finalize_configuration", "prepare_pending_run"),
            ("finalize_configuration", "_start_connection_worker"),
            ("prepare_pending_run", "_save_configuration_for_finalize"),
            ("prepare_pending_run", "_release_previous_port_for_finalize"),
            ("prepare_pending_run", "_freeze_pending_run"),
            ("commit_finalization", "accept_instrument_controller"),
            ("_abort_finalization", "_compensate_pending_run"),
            ("_compensate_pending_run", "_discard_pending_run"),
            ("_discard_pending_run", "discard_unstarted_run"),
            ("release_port_after_measurement", "_settle_port_after_stage"),
            ("release_port_after_measurement", "_close_port_after_stage"),
            ("_settle_port_after_stage", "settle_instrument_stage"),
            ("settle_instrument_stage", "end_stage"),
        ),
    },
    "presentation/qt/main_window.py": {
        "definitions": WINDOW_FINALIZATION_ENTRY_NAMES,
        "calls": (
            ("_finalize_run", "finalize_configuration"),
            ("on_instrument_controller_ready", "commit_finalization"),
            ("on_instrument_error", "fail_finalization"),
            ("on_instrument_stopped", "cancel_finalization"),
            (
                "_on_measurement_controller_thread_finished",
                "release_port_after_measurement",
            ),
        ),
    },
    "instrument/ports.py": {"definitions": ("end_stage",)},
    "instrument/session.py": {"definitions": ("end_stage",)},
    "instrument/measurement_adapter.py": {"definitions": ("end_stage",)},
}


def _retired_module_names():
    """旧入口已删除：模块名在这里冻结，避免重新引入。"""
    return RETIRED_MODULE_NAMES


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






class RunFlowEntryGateTests(unittest.TestCase):
    """阶段五：旧运行流程入口保持删除，最终化与补偿链保持冻结。"""

    def test_production_code_has_no_retired_run_flow_entry(self):
        offenders = [
            f"{_relative(path)}:{lineno}:{name}"
            for path in _production_sources()
            for name, lineno in retired_run_flow_sites(_tree(path))
        ]
        self.assertEqual(
            offenders,
            [],
            "生产代码不得再出现已删除的运行流程入口（连接按钮/独立连接链）: "
            + ", ".join(offenders),
        )

    def test_finalization_and_compensation_chain_is_frozen(self):
        """事务节点不仅要存在，还必须保持正确的直接调用关系。"""
        missing = []
        for relative, contract in FINALIZATION_COMPENSATION_CHAIN.items():
            path = ROOT / relative
            if not path.is_file():
                missing.append(f"{relative} 不存在")
                continue
            missing.extend(missing_contract_items(relative, _tree(path), contract))
        self.assertEqual(
            missing,
            [],
            "最终化/补偿链必须保持存在且守卫唯一：删除、改名或断开调用关系会重新打开"
            "“运行已创建但端口未连接”与“已开始运行被当成未开始撤销”的缺口: "
            + "; ".join(missing),
        )


    def test_only_the_window_opens_the_finalization_transaction(self):
        """事务入口只允许由主窗口调用；第二个调用文件就是第二个提交端口入口。"""
        entry_names = set(SINGLE_FINALIZATION_ENTRY_NAMES)
        callers: dict[str, set[str]] = {}
        for path in _production_sources():
            for name, _lineno in call_sites(_tree(path), entry_names):
                callers.setdefault(name, set()).add(_relative(path))
        problems = []
        for name in SINGLE_FINALIZATION_ENTRY_NAMES:
            actual = callers.get(name, set())
            if actual == {"presentation/qt/main_window.py"}:
                continue
            problems.append(f"{name} 的调用文件为 {sorted(actual)}")
        self.assertEqual(
            problems,
            [],
            "最终化/提交/失败补偿只能由主窗口转发到协调器；出现第二个调用入口时"
            "必须先在评审中说明为什么它不会绕过待提交运行与端口事务: "
            + "; ".join(problems),
        )



if __name__ == "__main__":
    unittest.main()
