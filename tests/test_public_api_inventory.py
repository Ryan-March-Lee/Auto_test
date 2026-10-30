r"""门禁：正式 API 目录清单与其静态架构断言。

本文件与 [正式 API 目录清单](../docs/正式API目录清单.md) 一一对应：

- ``FORMAL_API``：正式公共 API 的模块路径，以及对调用方承诺的入口函数、端口和 DTO。
- ``INTERNAL_MODULES``：允许正式代码导入、但不承诺公共稳定的内部模块。
- ``FORMAL_IMPORT_ROOTS``：正式 production code 允许导入的第一方顶层包。
- ``LEGACY_MODULES``：旧模块的唯一登记表，包含状态、处理步骤和源文件路径。

门禁覆盖三个层次：

1. 顶层包：不得导入未登记的第一方根模块。
2. 模块路径：正式包内的每个绝对导入都必须落在公共 API 或内部模块清单内，不能只凭根包放行。
3. 旧模块：根目录旧模块与正式包内旧实现不得新增引用；存量引用按文件精确冻结。

本步骤只冻结边界，不删除旧文件。退役一个旧模块时，必须同步修改 ``LEGACY_MODULES`` 和文档，
而不是放宽断言。

验证方式：

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" -m unittest tests.test_public_api_inventory -v
```
"""

import ast
import unittest
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).parents[1]
INVENTORY_DOC = ROOT / "docs" / "正式API目录清单.md"

# 步骤 1 的范围：这些路径中的代码属于正式产品代码。
# ``app`` 是正式装配层，与计划列出的范围一并纳入，避免旧模块从装配层回流。
SCOPE_PACKAGES = (
    "application",
    "domain",
    "infrastructure",
    "instrument",
    "presentation/qt",
    "app",
)
SCOPE_FILES = ("launcher.py",)

# 正式 production code 允许导入的第一方顶层包。
# 新增顶层包属于架构变更：必须先在正式 API 清单中登记，再新增导入。
FORMAL_IMPORT_ROOTS = {
    "domain": "领域模型、配置、计算与运行状态",
    "application": "应用用例、端口与 DTO",
    "infrastructure": "基础设施适配器（配置、持久化、文件系统、日志、运行时）",
    "instrument": "仪器端口、驱动、动作与安全流程",
    "presentation": "Qt 表现层",
    "app": "应用装配：运行上下文、事件、取消与 GUI 运行时",
    "analysis": "只读分析与导出计算",
}

# 正式公共 API：模块路径 -> 对调用方承诺的公开名称。
# 名称必须是该模块顶层定义、赋值或显式再导出的名字；模块声明 __all__ 时还必须在 __all__ 内。
# 删除或改名会立刻使门禁失败。
FORMAL_API = {
    # 启动与装配
    "launcher": ("main", "inspect_run", "list_runs"),
    "app.run_context": (
        "PreparedRun",
        "create_prepared_run",
        "load_prepared_run",
    ),
    "app.gui_runtime": (
        "prepare_configuration",
        "connect_instruments",
        "create_offline_measurement_port",
        "create_cable_loss_measurement",
        "create_driver_mapping_measurement",
        "create_amplifier_measurement",
    ),
    "app.events": (
        "EventSink",
        "MeasurementEvent",
        "ProgressEvent",
        "MessageEvent",
        "RealtimeDataEvent",
        "CheckpointEvent",
        "CompletedEvent",
        "StoppedEvent",
        "FailedEvent",
    ),
    "app.cancellation": ("CancellationMode", "MeasurementCancelled", "CancellationToken"),
    # 应用用例与运行服务
    "application.configuration_service": ("load_for_run",),
    "application.run_service": (
        "RunSnapshot",
        "create_run_snapshot",
        "load_run_configuration",
        "inspect_run",
        "load_hardware_config",
        "find_run_hardware_config",
        "ensure_run_resumable",
    ),
    "application.run_stages": ("RunStageRecorder", "StageInputUnavailable"),
    "application.measurements": (
        "CableLossUseCase",
        "DriverPowerMappingUseCase",
        "AmplifierMeasurementUseCase",
        "AmplifierCalibrationError",
        "validate_amplifier_calibration_inputs",
    ),
    "application.measurements.services": (
        "CableLossService",
        "DriverPowerMappingService",
        "AmplifierMeasurementService",
    ),
    "application.gui_data_service": ("GuiDataService",),
    "application.export_service": ("ExportRequestError", "ResultExportUseCase"),
    "application.archival_service": (
        "ArchivalRequestError",
        "ResultArchivalService",
        "archive_completed_stage",
    ),
    "application.run_history_service": (
        "RunHistoryRequestError",
        "RunHistoryService",
    ),
    "application.errors": (
        "AutoTestError",
        "CleanupError",
        "ConfigurationError",
        "DeviceCapabilityError",
        "DeviceConnectionError",
        "ErrorCategory",
        "FailureRecord",
        "MeasurementError",
        "ProtocolError",
        "cause_of",
        "normalize_failures",
    ),
    "application.lifecycle.measurement_lifecycle": ("MeasurementLifecycle", "StopIntent"),
    "application.lifecycle.measurement_runtime": (
        "MeasurementRuntimeCoordinator",
        "RuntimeDecision",
    ),
    # 应用端口
    "application.ports.config_repository": (
        "ConfigurationLoadResult",
        "ConfigurationRepository",
        "TestPlanRepository",
        "RunMappingRepository",
    ),
    "application.ports.result_repository": (
        "MeasurementResultRepository",
        "SavedMeasurementResult",
    ),
    "application.ports.run_history": (
        "RunHistoryRepository",
        "RunHistoryEntry",
        "RunHistoryState",
        "RunStageResultEntry",
    ),
    "application.ports.run_repository": ("RunRepositoryPort", "RunLayout"),
    "application.ports.result_artifacts": (
        "ArtifactGenerationError",
        "ArtifactGenerationRequest",
        "GeneratedArtifact",
        "ResultArtifactGeneratorPort",
    ),
    "application.ports.result_input_reader": ("ResultInputReader",),
    "application.ports.measurement_session": (
        "CleanupRecord",
        "CleanupStepStatus",
        "MeasurementSessionBinding",
        "MeasurementSessionPort",
        "SafetyCleanupError",
        "SafetyCleanupPort",
    ),
    "application.ports.gui_data_service": (
        "GuiDataService",
        "ResultFileDto",
        "LoadedResultDto",
        "ConfigurationPort",
        "ResultQueryPort",
        "ResultExportPort",
        "ResultExportServicePort",
        "RunArchiveStatusPort",
        "RunHistoryServicePort",
    ),
    "application.ports.device_discovery": (
        "DEMO_SUBNET",
        "DeviceDiscoveryError",
        "SCAN_MODE_NETWORK",
        "SCAN_MODE_SIMULATED",
    ),
    # 跨层 DTO
    "application.dto": (
        "MeasurementRequest",
        "CableLossMeasurementRequest",
        "DriverPowerMappingRequest",
        "AmplifierMeasurementRequest",
        "ExportFormat",
        "ExportOutcome",
        "ExportRequest",
        "MeasurementResult",
        "MeasurementStatus",
        "MeasurementType",
        "CableLossMeasurementResult",
        "DriverPowerMappingMeasurementResult",
        "AmplifierMeasurementResult",
        "measurement_result_from_document",
        "measurement_type_for_result_type",
        "require_formal_result",
    ),
    "application.dto.export_requests": (
        "ExportFormat",
        "ExportOutcome",
        "ExportRequest",
    ),
    "application.dto.measurement_requests": (
        "MeasurementRequest",
        "CableLossMeasurementRequest",
        "DriverPowerMappingRequest",
        "AmplifierMeasurementRequest",
    ),
    "application.dto.measurement_results": (
        "MeasurementResult",
        "MeasurementStatus",
        "MeasurementType",
        "CableLossMeasurementResult",
        "DriverPowerMappingMeasurementResult",
        "AmplifierMeasurementResult",
        "measurement_result_from_document",
        "measurement_type_for_result_type",
        "require_formal_result",
    ),
    "application.dto.archival": (
        "ArchivalOutcome",
        "ArchivalRequest",
        "ArchivalStatus",
        "ArchivalTestStatus",
        "ArchivedArtifact",
    ),
    "application.dto.run_history": (
        "RunArchiveState",
        "RunArtifactSummary",
        "RunHistorySummary",
        "RunStageSummary",
    ),
    # 领域层
    "domain.configuration.models": (
        "TestPlan",
        "InstrumentMapping",
        "PowerSupplyMapping",
        "ChannelMapping",
        "PowerChannelPlan",
        "RunResourceMapping",
        "RunConfiguration",
        "validate_test_plan",
        "validate_run_mapping",
        "validate_run_configuration",
    ),
    "domain.configuration.rules": ("validate_config", "simulated_binding_issues"),
    "domain.configuration.types": ("ConfigIssue", "ConfigValidationResult"),
    "domain.run.models": (
        "ArtifactKind",
        "ArtifactRecord",
        "ArtifactStatus",
        "FileReference",
        "RunManifest",
        "RunStatus",
        "StageRecord",
        "StageStatus",
        "default_artifact_path",
        "default_manifest",
        "generate_run_id",
        "normalize_artifact_path",
        "validate_run_id",
        "utc_now_iso",
    ),
    "domain.measurement.calculations": (
        "calculate_cable_losses",
        "compensate_amplifier_output_power",
        "compensate_driver_output_power",
        "interpolate_driver_output_power",
        "calculate_dut_input_power",
        "calculate_gain",
        "calculate_efficiency",
        "calculate_compression_result",
    ),
    "domain.measurement.values": (
        "MeasurementValueError",
        "MeasurementUnitError",
        "Frequency",
        "Decibels",
        "PowerDbm",
        "PowerWatt",
        "Percent",
        "CableLossProfile",
        "CableLossTable",
        "DriverOutputTable",
        "CompressionPoint",
        "CompressionResult",
    ),
    # 结果读取的唯一正式入口是 application.dto.measurement_results（见 3.2 跨层 DTO）。
    # 基础设施适配器
    "infrastructure.config.json_io": ("load_json_object", "load_config_file"),
    "infrastructure.config.validation": (
        "load_config",
        "validate_config_file",
        "validate_official_config",
    ),
    "infrastructure.config.json_config_repository": (
        "JsonConfigurationRepository",
        "JsonTestPlanRepository",
        "JsonRunMappingRepository",
        "load_json",
        "load_test_plan",
        "load_run_mapping",
        "load_run_configuration",
        "load_combined_configuration",
    ),
    "infrastructure.persistence.result_repository": ("FileMeasurementResultRepository",),
    "infrastructure.persistence.run_repository": ("RunRepository",),
    "infrastructure.persistence.run_history_repository": ("FileRunHistoryRepository",),
    "infrastructure.gui_data_service": (
        "JsonConfigurationAdapter",
        "ResultQueryAdapter",
        "ResultExportAdapter",
    ),
    "infrastructure.logging.app_logging": ("get_logger", "setup_logging"),
    "infrastructure.runtime": (
        "current_runtime",
        "runtime_id",
        "configured_python",
        "python_version_requirement",
        "python_version_supported",
        "interpreter_command",
        "verification_commands",
    ),
    # 仪器层
    "instrument.ports": (
        "DRIVER_POWER_CAPABILITIES",
        "DUT_POWER_CAPABILITIES",
        "SIGNAL_MEASUREMENT_CAPABILITIES",
        "InstrumentCapabilityError",
        "InstrumentSession",
        "InstrumentState",
        "MeasurementInstrumentPort",
        "PowerSupplyPort",
        "SignalGeneratorPort",
        "SpectrumAnalyzerPort",
        "require_measurement_capabilities",
    ),
    "instrument.session": ("ManagedInstrumentSession", "InstrumentSessionError"),
    "instrument.flow.safety_shutdown_flow": ("SafetyShutdownFlow", "SafetyShutdownError"),
    "instrument.flow.cleanup": ("SafetyCleanupAdapter", "cleanup_measurement"),
    # 表现层（页面经 controller/DTO 通信，不得直接组装硬件或存储）
    "presentation.qt.main_window": ("MainWindow",),
    "presentation.qt.pages": ("PageContext", "PageDefinition", "BasePage", "build_pages"),
    "presentation.qt.composition": ("build_gui_data_service",),
    "presentation.qt.measurement_controller": ("MeasurementController",),
    "presentation.qt.run_contract": ("is_valid_prepared_run", "require_prepared_run"),
}

# 正式内部模块：允许被正式代码导入，但不是公共 API 承诺，可随实现调整。
# 内部模块同样按完整路径登记，新增内部模块并跨模块导入时必须先在这里登记。
INTERNAL_MODULES = {
    "analysis.reporting": "分析层 CSV/HTML 报告写入（只接受正式结果 DTO）",
    "analysis.result_source": "分析层只读结果协议与文档展平",
    "analysis.visualization": "分析层只读可视化与报告数据构建",
    "app": "装配包（__init__ 为空）；具体入口见 app.* 公共 API",
    "application.inputs": "结果输入读取包",
    "application.inputs.result_reading": "报告模型构建（内部实现）",
    "application.lifecycle": "测量生命周期包",
    "domain.instruments": "设备表领域包",
    "domain.instruments.labels": "设备短标签生成与显示文本：序列号归一化复用、前缀提取、末四位与冲突扩展（纯计算，不写注册表；标签不含通道分隔符），标签对所有登记设备生效（电源的标签同时是 supply_id），以及电源与其它仪器各自的显示文本、详情与审计用的身份文本、日志里的通道名（短标签/CHx）与身份核对结论（日志与页面共用同一份）",
    "domain.instruments.models": "仪器身份解析（序列号唯一标识）、设备类别与短标签字段、旧槽位识别（只读兼容，不得用于生成电源键）、地址别名与在线状态模型",
    "domain.models": "运行、测试方案与会话生命周期模型；测量结果契约在 application.dto",
    "infrastructure.filesystem.atomic_json": "原子 JSON 写入",
    "infrastructure.filesystem.paths": "路径服务",
    "infrastructure.config.legacy_power_supply_migration": "旧 PS1..PS4 配置迁移器：按设备表登记的身份把电源集合、分配引用、绑定与运行映射改写到 supply_id，输出迁移摘要；未决项或校验失败时不写盘，摘要另存",
    "infrastructure.discovery": "仪器发现包",
    "infrastructure.discovery.instrument_discovery": "网段扫描与 *IDN? 身份解析（序列号匹配设备表；未知设备同样上报）",
    "infrastructure.discovery.simulated_network": "离线演示用的设备网络响应（仅模拟扫描模式）",
    "infrastructure.persistence": "持久化适配器包",
    "infrastructure.persistence.artifact_generator": "自动归档产物（CSV）生成适配器",
    "infrastructure.persistence.instrument_registry": "设备表 JSON 仓储：序列号为唯一标识，IP 只是可漂移的地址别名",
    "infrastructure.persistence.json_encoder": "JSON 编码器",
    "infrastructure.persistence.json_result_repository": "结果 JSON 实现细节",
    "infrastructure.persistence.run_directory": "运行目录文件布局",
    "infrastructure.runtime.dependency_lock": "依赖锁定清单的严格解析与整份环境核对",
    "infrastructure.runtime.runtime_contract": "运行时契约实现；公共入口是 infrastructure.runtime",
    "instrument.flow": "安全流程包；application 只通过 application.ports.measurement_session 依赖清理端口",
    "instrument.measurement_adapter": "测量服务端口的显式适配器；设备方法显式定义，不按方法名转发",
    "instrument.measurement_factory": "硬件/离线测量端口装配；地址、角色和通道在连接前校验",
    "instrument.safety": "设备安全动作与资源所有权边界；关闭回调安全标记契约的显式签名判定",
    "presentation.qt.application_coordinator": "GUI 运行协调器：配置、运行契约与仪器端口所有者",
    "presentation.qt.archival_page": "测量页面共享的归档状态生命周期（等待/保存中/结果/终止/运行切换）",
    "presentation.qt.archival_status": "归档结果到页面文案与按钮状态的映射（无 Qt、无路径判断）",
    "presentation.qt.archival_view": "归档结果分组控件：文案与“打开结果目录/报告”动作",
    "presentation.qt.config_form_state": "配置表单状态",
    "presentation.qt.config_page": "配置页面",
    "presentation.qt.connection_page": "仪器连接配置页：欢迎页选定测试模式后的整屏页面，扫描并应用本次测试所需设备（绑定写入与派生界面归配置页），完成后进入仪器配置页",
    "presentation.qt.device_cards": "设备发现的仪器展示卡片：第一行居中放大的仪器名（型号）、第二行仪器图片（不垫底板不描边）、最下面加粗放大的设备码缩写；点击卡片即选中",
    "presentation.qt.device_discovery_panel": "设备发现面板：扫描网段、以卡片展示按设备表登记的角色可应用的设备（含未知设备标记）",
    "presentation.qt.config_projection": "正式配置到配置页控件的确定性投影",
    "presentation.qt.configuration_authoring": "配置页保存时的正式运行配置编写",
    "presentation.qt.connection_diagrams": "连接图渲染",
    "presentation.qt.connection_dialog": "连接确认对话框",
    "presentation.qt.run_history_page": "历史运行与结果管理页（运行/阶段/产物状态、失败产物重新生成、导出副本）",
    "presentation.qt.history_view_model": "历史查看只读视图模型（结果列表、运行摘要、选中项、频率浏览）",
    "presentation.qt.input_controls": "通用输入控件",
    "presentation.qt.measurement_state": "测量状态对象",
    "presentation.qt.measurement_worker_factories": "测量 worker 工厂",
    "presentation.qt.realtime_buffer": "实时数据缓冲",
    "presentation.qt.realtime_plot": "实时绘图控件",
    "presentation.qt.status_panel": "状态/日志面板",
    "presentation.qt.theme": "统一主题：设计令牌、调色板与全局样式表",
    "presentation.qt.visualization_page": "历史可视化页面",
    "presentation.qt.welcome_page": "启动欢迎页与测试模式选择",
    "presentation.qt.window_geometry": "主窗口初始几何：按屏幕可用区域收缩并居中，避开任务栏与屏幕边缘",
    "presentation.qt.workers": "测量 worker 实现",
}


@dataclass(frozen=True)
class LegacyModule:
    """一个旧模块的登记信息。

    ``status`` 是处理方式：``待删除``（整文件退役）、``待清理``（按步骤重写或收敛）或
    ``已删除``。``path`` 是相对仓库根的源文件；包使用 ``__init__.py``。``present`` 记录
    步骤 1 冻结时该文件是否仍存在，由 ``status`` 与工作树共同校验。
    """

    note: str
    status: str
    step: str
    path: str
    present: bool


# 旧模块唯一登记表：模块名 -> 状态。根目录模块没有点号，正式包内旧实现使用完整模块名。
# 退役模块时把 present 改为 False（或删除条目），并同步文档与发布清单。
LEGACY_MODULES = {
    # 步骤 4：旧测量入口（已整文件退役）
    "cable_loss_measurement": LegacyModule(
        "旧线损测量根入口", "已删除", "步骤 4", "cable_loss_measurement.py", False
    ),
    "driver_power_mapping": LegacyModule(
        "旧驱动功率映射根入口", "已删除", "步骤 4", "driver_power_mapping.py", False
    ),
    "amplifier_measurement": LegacyModule(
        "旧主功放测量根入口", "已删除", "步骤 4", "amplifier_measurement.py", False
    ),
    "app.legacy_amplifier_measurement": LegacyModule(
        "旧主功放测量入口实现", "已删除", "步骤 4", "app/legacy_amplifier_measurement.py", False
    ),
    # 步骤 5：旧配置转换与根目录配置转发（已整文件退役）
    "config_io": LegacyModule("旧配置读写转发", "已删除", "步骤 5", "config_io.py", False),
    "config_models": LegacyModule(
        "旧配置模型转发", "已删除", "步骤 5", "config_models.py", False
    ),
    "config_validation": LegacyModule(
        "旧配置校验转发", "已删除", "步骤 5", "config_validation.py", False
    ),
    "infrastructure.config.legacy_conversion": LegacyModule(
        "旧配置自动转换", "已删除", "步骤 5", "infrastructure/config/legacy_conversion.py", False
    ),
    # 步骤 6：旧结果模型与读取转发（已整文件退役）
    "result_reading": LegacyModule(
        "旧结果读取转发", "已删除", "步骤 6", "result_reading.py", False
    ),
    "application.dto.legacy_result_adapter": LegacyModule(
        "旧结果 DTO 适配器", "已删除", "步骤 6", "application/dto/legacy_result_adapter.py", False
    ),
    "domain.result_reading": LegacyModule(
        "旧领域结果读取层", "已删除", "步骤 6", "domain/result_reading.py", False
    ),
    "application.ports.result_history": LegacyModule(
        "旧全局历史结果端口", "已删除", "步骤 6", "application/ports/result_history.py", False
    ),
    "infrastructure.persistence.result_history_repository": LegacyModule(
        "旧全局历史结果仓储",
        "已删除",
        "步骤 6",
        "infrastructure/persistence/result_history_repository.py",
        False,
    ),
    # 步骤 3：旧结果仓储/写入链
    "result_storage": LegacyModule(
        "旧结果目录扫描与全局结果写入", "已删除", "步骤 3", "result_storage.py", False
    ),
    # 步骤 9：旧动态仪器路由（measurement_adapter / measurement_factory 已收敛为显式装配，
    # 登记为内部模块；legacy_control 与 power_roles 整文件退役）
    "instrument.legacy_control": LegacyModule(
        "旧仪器控制实现", "已删除", "步骤 9", "instrument/legacy_control.py", False
    ),
    "instrument.power_roles": LegacyModule(
        "旧电源角色自动推导", "已删除", "步骤 9", "instrument/power_roles.py", False
    ),
    # 步骤 16：其余纯再导出兼容模块（已整文件退役）
    "measurement_calculations": LegacyModule(
        "旧测量计算转发", "已删除", "步骤 16", "measurement_calculations.py", False
    ),
    "measurement_lifecycle": LegacyModule(
        "旧测量生命周期转发", "已删除", "步骤 16", "measurement_lifecycle.py", False
    ),
    "measurement_services": LegacyModule(
        "旧测量服务转发", "已删除", "步骤 16", "measurement_services.py", False
    ),
    "app_logging": LegacyModule(
        "旧日志转发", "已删除", "步骤 16", "app_logging.py", False
    ),
    "project_paths": LegacyModule(
        "旧全局路径常量与服务转发", "已删除", "步骤 16", "project_paths.py", False
    ),
    # 已删除的历史入口（保留在门禁中防止重新引入）
    "legacy_config_conversion": LegacyModule(
        "已删除的旧配置转换入口", "已删除", "已删除", "legacy_config_conversion.py", False
    ),
    "enhanced_main_gui": LegacyModule(
        "已删除的旧主窗口", "已删除", "已删除", "enhanced_main_gui.py", False
    ),
    "llm": LegacyModule("已删除的根目录 LLM 入口", "已删除", "已删除", "llm.py", False),
    "web_search_tools": LegacyModule(
        "已删除的根目录搜索工具", "已删除", "已删除", "web_search_tools.py", False
    ),
    "instrument_control": LegacyModule(
        "已删除的旧仪器控制入口", "已删除", "已删除", "instrument_control.py", False
    ),
    "data_visualization": LegacyModule(
        "已删除的旧数据可视化入口", "已删除", "已删除", "data_visualization.py", False
    ),
    "connection_diagrams": LegacyModule(
        "已删除的旧连接图入口", "已删除", "已删除", "connection_diagrams.py", False
    ),
    "persistence": LegacyModule(
        "已删除的旧持久化包", "已删除", "已删除", "persistence/__init__.py", False
    ),
}

LEGACY_ROOT_MODULES = frozenset(name for name in LEGACY_MODULES if "." not in name)
LEGACY_DOTTED_MODULES = frozenset(name for name in LEGACY_MODULES if "." in name)

# 步骤 1 冻结的存量旧模块引用：模块 -> 允许继续引用的文件。
# 这是“只禁止新增”的精确基线；删除对应引用时必须同步删除条目，保持清单与代码一致。
# 步骤 9 已把 instrument.measurement_factory 收敛为内部模块、退役 instrument.power_roles，
# 正式 production code 不再引用任何旧模块，基线因此为空。
LEGACY_IMPORT_BASELINE = {}

# 唯一允许的“无法静态判定模块名”的动态导入：launcher 只按固定依赖表探测第三方包。
# 值是该（文件, 函数）允许出现的次数，防止在白名单函数内悄悄增加动态导入。
DYNAMIC_IMPORT_ALLOWLIST = {("launcher.py", "check_packages"): 1}

# 步骤 7 已清除 application 对 instrument.flow 具体实现的依赖：该集合必须保持为空。
# 新增任何 application -> instrument.flow 的绝对导入都会让门禁失败。


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8-sig"), str(path))


def _scope_sources():
    sources = []
    for package in SCOPE_PACKAGES:
        sources.extend(sorted((ROOT / package).rglob("*.py")))
    sources.extend(ROOT / name for name in SCOPE_FILES)
    return sources


def _relative(path):
    return path.relative_to(ROOT).as_posix()


def _first_party_roots():
    """返回仓库根目录下可作为第一方导入根的名字（含非包目录，避免漏检）。"""
    excluded = {".git", ".idea", "__pycache__", "docs", "logs", "test_results", "assets"}
    roots = set()
    for entry in ROOT.iterdir():
        if entry.name in excluded:
            continue
        if entry.is_dir():
            roots.add(entry.name)
        elif entry.suffix == ".py":
            roots.add(entry.stem)
    return roots


def _module_source(module):
    parts = module.split(".")
    module_file = ROOT.joinpath(*parts).with_suffix(".py")
    if module_file.is_file():
        return module_file
    package_file = ROOT.joinpath(*parts, "__init__.py")
    if package_file.is_file():
        return package_file
    return None


def _absolute_imports(tree):
    """返回 (导入的完整模块名, 是否 from 导入) 列表；相对导入不参与根入口判断。

    ``from app import legacy_amplifier_measurement`` 这类导入的模块名只有 ``app``，
    因此对正式根下的 from 导入额外返回真实存在的子模块候选，避免绕过路径门禁。
    """
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((alias.name, False) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            imports.append((node.module, True))
            if node.module.split(".")[0] in FORMAL_IMPORT_ROOTS:
                candidates = [
                    f"{node.module}.{alias.name}"
                    for alias in node.names
                    if alias.name != "*"
                ]
                imports.extend(
                    (candidate, True)
                    for candidate in candidates
                    if _module_source(candidate) is not None
                )
    return imports


def _wildcard_imports(tree):
    """返回 ``from ... import *`` 节点；星号导入会绕过路径登记。"""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and any(alias.name == "*" for alias in node.names)
    ]


def _enclosing_functions(tree):
    """返回调用节点 -> 最近外层函数名（模块级为 None）。"""
    owners = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    owners[child] = node.name
    return owners


def _dynamic_import_sites(tree):
    """返回动态导入调用：[(常量模块名或 None, 外层函数名或 None), ...]。

    识别 ``importlib.import_module``、其 from/别名形式、``__import__`` 及 builtins 别名。
    参数不是字符串常量时模块名为 None，由门禁按动态导入白名单处理。
    """
    importlib_aliases = {"importlib"}
    import_module_aliases = set()
    import_aliases = {"__import__"}
    builtins_aliases = {"builtins"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "importlib":
                    importlib_aliases.add(alias.asname or "importlib")
                elif alias.name == "builtins":
                    builtins_aliases.add(alias.asname or "builtins")
        elif isinstance(node, ast.ImportFrom):
            if node.module == "importlib":
                for alias in node.names:
                    if alias.name == "import_module":
                        import_module_aliases.add(alias.asname or "import_module")
            elif node.module == "builtins":
                for alias in node.names:
                    if alias.name == "__import__":
                        import_aliases.add(alias.asname or "__import__")

    owners = _enclosing_functions(tree)
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_dynamic = False
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id in importlib_aliases and func.attr == "import_module":
                is_dynamic = True
            elif func.value.id in builtins_aliases and func.attr == "__import__":
                is_dynamic = True
        elif isinstance(func, ast.Name):
            if func.id in import_module_aliases or func.id in import_aliases:
                is_dynamic = True
        if not is_dynamic:
            continue
        argument = node.args[0] if node.args else None
        module = (
            argument.value
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
            else None
        )
        sites.append((module, owners.get(node)))
    return sites


def _is_legacy_module(module):
    root = module.split(".")[0]
    if root in LEGACY_ROOT_MODULES:
        return True
    return any(
        module == prefix or module.startswith(prefix + ".")
        for prefix in LEGACY_DOTTED_MODULES
    )


def _top_level_names(tree):
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
    return names


def _declared_exports(tree):
    """返回模块字面量 __all__ 集合；未声明或无法静态解析时返回 None。"""
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        ):
            if isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
                return {
                    element.value
                    for element in node.value.elts
                    if isinstance(element, ast.Constant) and isinstance(element.value, str)
                }
    return None


class FormalApiInventoryTests(unittest.TestCase):
    """正式 API 清单本身必须存在、可解析，且与文档一致。"""

    def test_inventory_document_exists_and_lists_every_module(self):
        self.assertTrue(INVENTORY_DOC.is_file(), f"缺少正式 API 清单文档: {INVENTORY_DOC}")
        document = INVENTORY_DOC.read_text(encoding="utf-8-sig")
        missing = [
            module for module in (*FORMAL_API, *INTERNAL_MODULES) if module not in document
        ]
        self.assertEqual(missing, [], "正式 API 清单文档缺少模块: " + ", ".join(missing))
        missing_roots = [root for root in FORMAL_IMPORT_ROOTS if root not in document]
        self.assertEqual(missing_roots, [], "文档缺少正式导入根: " + ", ".join(missing_roots))

    def test_document_lists_every_declared_symbol(self):
        document = INVENTORY_DOC.read_text(encoding="utf-8-sig")
        missing = [
            f"{module}.{symbol}"
            for module, symbols in FORMAL_API.items()
            for symbol in symbols
            if symbol not in document
        ]
        self.assertEqual(missing, [], "文档缺少正式 API 名称: " + ", ".join(missing))

    def test_document_lists_every_legacy_module_with_status(self):
        document = INVENTORY_DOC.read_text(encoding="utf-8-sig")
        missing = []
        for name, spec in LEGACY_MODULES.items():
            row = f"| `{name}` | `{spec.path}` | {spec.status} | {spec.step} |"
            if row not in document:
                missing.append(f"{name}（期望表格行: {row}）")
        self.assertEqual(missing, [], "文档旧模块表格与登记表不一致: " + "; ".join(missing))

    def test_every_formal_module_lives_under_a_formal_import_root(self):
        # launcher 是唯一允许存在的正式入口模块，不属于可被导入的包根。
        allowed_roots = set(FORMAL_IMPORT_ROOTS) | {"launcher"}
        for module in FORMAL_API:
            with self.subTest(module=module):
                self.assertIn(module.split(".")[0], allowed_roots)

    def test_internal_modules_live_under_a_formal_import_root(self):
        for module in INTERNAL_MODULES:
            with self.subTest(module=module):
                self.assertIn(module.split(".")[0], FORMAL_IMPORT_ROOTS)

    def test_formal_and_internal_module_paths_do_not_overlap(self):
        overlap = set(FORMAL_API) & set(INTERNAL_MODULES)
        self.assertEqual(overlap, set(), "同一模块不能既是公共 API 又是内部模块")

    def test_formal_modules_exist_export_declared_names_and_respect_dunder_all(self):
        for module, symbols in FORMAL_API.items():
            with self.subTest(module=module):
                source = _module_source(module)
                self.assertIsNotNone(source, f"正式 API 模块不存在: {module}")
                tree = _tree(source)
                available = _top_level_names(tree)
                missing = [symbol for symbol in symbols if symbol not in available]
                self.assertEqual(
                    missing,
                    [],
                    f"{source.relative_to(ROOT)} 缺少正式 API 名称: " + ", ".join(missing),
                )
                exports = _declared_exports(tree)
                if exports is not None:
                    not_exported = [symbol for symbol in symbols if symbol not in exports]
                    self.assertEqual(
                        not_exported,
                        [],
                        f"{source.relative_to(ROOT)} 的 __all__ 未导出: "
                        + ", ".join(not_exported),
                    )

    def test_internal_modules_exist(self):
        for module in INTERNAL_MODULES:
            with self.subTest(module=module):
                self.assertIsNotNone(
                    _module_source(module), f"登记的内部模块不存在: {module}"
                )

    def test_formal_import_roots_all_exist_in_the_repository(self):
        for root in FORMAL_IMPORT_ROOTS:
            with self.subTest(root=root):
                self.assertTrue(
                    (ROOT / root / "__init__.py").is_file()
                    or (ROOT / f"{root}.py").is_file(),
                    f"正式导入根不存在: {root}",
                )

    def test_legacy_module_registry_matches_the_working_tree(self):
        """待删除模块必须仍存在，已删除模块必须不存在。"""
        for name, spec in LEGACY_MODULES.items():
            with self.subTest(module=name):
                path = ROOT / spec.path
                if spec.present:
                    self.assertTrue(
                        path.is_file(),
                        f"{spec.path} 应在对应删除步骤执行前保留；若已删除请更新登记表",
                    )
                else:
                    self.assertFalse(
                        path.is_file(), f"{spec.path} 已登记为删除，不得重新出现"
                    )

    def test_legacy_import_baseline_only_references_registered_legacy_modules(self):
        unknown = set(LEGACY_IMPORT_BASELINE) - set(LEGACY_MODULES)
        self.assertEqual(unknown, set(), "基线引用了未登记的旧模块: " + ", ".join(unknown))
        missing_files = [
            file
            for files in LEGACY_IMPORT_BASELINE.values()
            for file in files
            if not (ROOT / file).is_file()
        ]
        self.assertEqual(missing_files, [], "基线引用了不存在的源文件: " + ", ".join(missing_files))


class ProductionImportBoundaryTests(unittest.TestCase):
    """范围内生产代码只能导入已登记的模块路径，不得新增旧模块引用。"""

    def _legacy_import_pairs(self):
        """返回范围内旧模块引用：模块名 -> 引用文件集合。"""
        pairs = {}
        for source in _scope_sources():
            relative = _relative(source)
            tree = _tree(source)
            modules = [module for module, _is_from in _absolute_imports(tree)]
            modules.extend(
                module for module, _function in _dynamic_import_sites(tree) if module
            )
            for module in modules:
                if _is_legacy_module(module):
                    pairs.setdefault(module, set()).add(relative)
        return pairs

    def test_scope_imports_no_new_legacy_module_references(self):
        violations = []
        for module, files in self._legacy_import_pairs().items():
            allowed = LEGACY_IMPORT_BASELINE.get(module, set())
            for file in sorted(files - allowed):
                violations.append(f"{file} imports {module}")
        self.assertEqual(
            violations,
            [],
            "正式 production code 不得新增旧模块引用（先迁移到正式 API）: "
            + "; ".join(violations),
        )

    def test_legacy_import_baseline_matches_the_code_exactly(self):
        actual = self._legacy_import_pairs()
        declared = {
            module: set(files) for module, files in LEGACY_IMPORT_BASELINE.items()
        }
        self.assertEqual(
            actual,
            declared,
            "旧模块引用基线必须与代码严格一致：删除引用时同步删除基线条目，"
            "新增引用不得只靠扩大基线通过",
        )

    def test_scope_imports_only_registered_first_party_packages(self):
        first_party = _first_party_roots()
        violations = []
        for source in _scope_sources():
            for module, _is_from in _absolute_imports(_tree(source)):
                root = module.split(".")[0]
                if root in first_party and root not in FORMAL_IMPORT_ROOTS:
                    violations.append(
                        f"{_relative(source)} imports 未登记的根模块 {module}"
                    )
        self.assertEqual(
            violations,
            [],
            "新增根目录模块导入必须先登记到正式 API 清单: " + "; ".join(violations),
        )

    def test_scope_imports_only_registered_module_paths(self):
        allowed = set(FORMAL_API) | set(INTERNAL_MODULES)
        violations = []
        for source in _scope_sources():
            for module, _is_from in _absolute_imports(_tree(source)):
                if module.split(".")[0] not in FORMAL_IMPORT_ROOTS:
                    # 非正式根由顶层包门禁处理，这里只约束正式包内部路径。
                    continue
                if _is_legacy_module(module):
                    # 旧模块由基线门禁单独处理。
                    continue
                if module not in allowed:
                    violations.append(f"{_relative(source)} imports 未登记模块 {module}")
        self.assertEqual(
            violations,
            [],
            "正式包内导入路径必须登记为公共 API 或内部模块: " + "; ".join(violations),
        )

    def test_scope_has_no_wildcard_imports(self):
        violations = [
            f"{_relative(source)}:{node.lineno}"
            for source in _scope_sources()
            for node in _wildcard_imports(_tree(source))
        ]
        self.assertEqual(
            violations,
            [],
            "星号导入会绕过模块路径登记，正式代码不得使用: " + ", ".join(violations),
        )

    def test_scope_has_no_unregistered_dynamic_imports(self):
        allowed = Counter(DYNAMIC_IMPORT_ALLOWLIST)
        seen = Counter()
        violations = []
        for source in _scope_sources():
            relative = _relative(source)
            for module, function in _dynamic_import_sites(_tree(source)):
                if module is not None:
                    continue
                site = (relative, function)
                seen[site] += 1
                if seen[site] > allowed[site]:
                    violations.append(f"{relative}:{function or '<module>'} 动态导入无法静态校验")
        self.assertEqual(
            violations,
            [],
            "无法静态判定模块名的动态导入必须登记白名单: " + "; ".join(violations),
        )

    def test_dynamic_import_allowlist_matches_the_code_exactly(self):
        actual = Counter(
            (_relative(source), function)
            for source in _scope_sources()
            for module, function in _dynamic_import_sites(_tree(source))
            if module is None
        )
        self.assertEqual(
            actual,
            Counter(DYNAMIC_IMPORT_ALLOWLIST),
            "动态导入白名单必须与代码严格一致：白名单只允许收缩或随代码同步调整",
        )

    def test_application_has_no_instrument_flow_dependency(self):
        """步骤 7 完成：application 不得依赖 instrument.flow 的具体实现。"""
        actual = set()
        for source in (ROOT / "application").rglob("*.py"):
            for module, _is_from in _absolute_imports(_tree(source)):
                if module == "instrument.flow" or module.startswith("instrument.flow."):
                    actual.add(_relative(source))
        self.assertEqual(
            actual,
            set(),
            "application 必须只依赖 application.ports.measurement_session 的安全清理端口",
        )

    def test_launcher_imports_only_formal_boundaries(self):
        first_party = _first_party_roots()
        modules = {
            module
            for module, _is_from in _absolute_imports(_tree(ROOT / "launcher.py"))
            if module.split(".")[0] in first_party
        }
        self.assertTrue(modules, "launcher.py 应导入正式边界模块")
        unregistered = [
            module for module in modules if module.split(".")[0] not in FORMAL_IMPORT_ROOTS
        ]
        self.assertEqual(
            unregistered, [], "launcher.py 导入了未登记的根模块: " + ", ".join(unregistered)
        )


class BoundaryDetectorTests(unittest.TestCase):
    """门禁辅助函数自身的回归：保证新增旧入口时真的能发现。"""

    def test_static_and_dynamic_legacy_imports_are_detected(self):
        source = """
import config_io
from result_storage import save_json_result
from app import legacy_amplifier_measurement
import importlib as il
from importlib import import_module as load_module
import builtins as builtins_alias
il.import_module("measurement_services")
load_module("instrument.legacy_control")
builtins_alias.__import__("app.legacy_amplifier_measurement")
from application.run_service import create_run_snapshot
"""
        tree = ast.parse(source)
        detected = {
            module
            for module, _is_from in _absolute_imports(tree)
            if _is_legacy_module(module)
        } | {
            module
            for module, _function in _dynamic_import_sites(tree)
            if module and _is_legacy_module(module)
        }
        self.assertEqual(
            detected,
            {
                "config_io",
                "result_storage",
                "app.legacy_amplifier_measurement",
                "measurement_services",
                "instrument.legacy_control",
            },
        )

    def test_non_constant_dynamic_import_is_reported_without_a_module(self):
        tree = ast.parse(
            "import importlib\ndef probe(name):\n    return importlib.import_module(name)\n"
        )
        self.assertEqual(_dynamic_import_sites(tree), [(None, "probe")])

    def test_relative_imports_are_not_treated_as_root_entries(self):
        tree = ast.parse("from . import workers\nfrom .workers import CableLossWorker\n")
        self.assertEqual(_absolute_imports(tree), [])

    def test_star_imports_are_detected(self):
        tree = ast.parse("from application.dto import *\nfrom . import *\n")
        self.assertEqual(len(_wildcard_imports(tree)), 2)

    def test_first_party_roots_cover_tests_and_tooling_directories(self):
        roots = _first_party_roots()
        for name in ("tests", "tools", "hardware", "application"):
            self.assertIn(name, roots, f"{name} 应被识别为第一方导入根")


if __name__ == "__main__":
    unittest.main()
