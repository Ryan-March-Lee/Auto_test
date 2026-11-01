# 正式 API 目录清单

本文冻结正式公共 API、正式内部模块和已退役模块的目录边界。

本清单只冻结边界，不删除旧文件。第 6 节登记的全部旧模块均已删除，必须保持不存在；表中的
“处理步骤”是退役当时的执行记录，不代表现行计划的步骤编号。

清单分为三个层次，避免把“可导入”误当成“公共承诺”：

1. **正式公共 API**：对调用方承诺稳定的模块路径和公开名称。
2. **正式内部模块**：允许正式代码依赖，但不承诺公共稳定性，可随实现调整。
3. **旧模块**：不再视为公共 API。禁止新增引用；存量引用按文件和模块精确冻结为基线。

清单的可执行形式是 [`tests/test_public_api_inventory.py`](../tests/test_public_api_inventory.py)。
本文与测试中的 `FORMAL_API`、`INTERNAL_MODULES`、`FORMAL_IMPORT_ROOTS`、`LEGACY_MODULES`、
`LEGACY_IMPORT_BASELINE`、`DYNAMIC_IMPORT_ALLOWLIST` 一一对应；修改本文必须同步修改测试，
否则门禁失败。

## 1. 设计前提

本项目采用不考虑向后兼容的技术决策：

- 不保留旧 Python 导入入口，不为外部旧脚本提供迁移适配层。
- 调用方必须直接迁移到本文登记的正式 API。
- 新增顶层包、公共模块、内部模块或公开名称，都是架构变更：先改本文和测试，再改代码。

## 2. 正式分层与允许的导入路径

正式 production code（`application/`、`domain/`、`infrastructure/`、`instrument/`、
`presentation/qt/`、`app/`、`launcher.py`）只允许导入下列第一方顶层包：

| 导入根 | 责任 | 边界要求 |
| --- | --- | --- |
| `domain` | 领域模型、配置 schema、测量计算、运行状态 | 不依赖 Qt、VISA、文件格式和基础设施实现 |
| `application` | 应用用例、端口、DTO、运行服务 | 不依赖基础设施实现、`instrument.flow` 具体实现和 Qt |
| `infrastructure` | 配置、持久化、文件系统、日志、运行时的适配器 | 实现 application 端口；不反向定义业务规则 |
| `instrument` | 仪器端口、驱动、动作与安全清理流程 | 只通过端口/会话暴露能力，生产路径不使用自动发现推导角色 |
| `presentation` | Qt 表现层 | 只消费 DTO、controller 和只读端口；不组装硬件或存储 |
| `app` | 应用装配：运行上下文、事件、取消、GUI 运行时 | 允许装配，不承载业务规则 |
| `analysis` | 只读分析与导出计算 | 不依赖基础设施 |

导入约束分三层执行：

1. **顶层包**：不得导入未登记的第一方根模块；新增顶层包必须先登记。
2. **模块路径**：正式包内的绝对导入必须命中第 3 节的公共 API 或第 5 节的内部模块；
   仅顶层包已登记不代表包内任意子模块可用。
3. **导入形式**：禁止 `from ... import *`；动态导入的模块名必须是字符串常量，唯一例外是
   第 6.3 节登记的 `launcher.py` 依赖探测。

`launcher.py` 是唯一 CLI 启动入口，只负责环境检查、配置校验、运行检查和启动 GUI。
`hardware/` 与 `tools/` 属于受控 smoke 与工具脚本，不在正式测量链路的导入面上。

## 3. 正式公共 API

### 3.1 启动、运行与装配

| 模块 | 正式公开名称 | 说明 |
| --- | --- | --- |
| `launcher` | `main`、`inspect_run`、`list_runs` | CLI 启动、运行目录检查与列表 |
| `app.run_context` | `PreparedRun`、`create_prepared_run`、`load_prepared_run` | 运行契约对象与创建/重载入口；每次正式测量绑定同一个运行目录与冻结快照 |
| `app.gui_runtime` | `prepare_configuration`、`connect_instruments`、`create_offline_measurement_port`、`create_cable_loss_measurement`、`create_driver_mapping_measurement`、`create_amplifier_measurement` | GUI 侧装配入口；页面不得直接调用测量组装函数。`connect_instruments(prepared_run, *, recorder=None)` 只接受运行契约对象，硬件配置取自运行目录冻结的 `inputs/hardware_config.json`，路径在该入口内解析一次，**不接受任何配置路径参数**；三个测量工厂没有位置参数，`prepared_run` 是必填关键字参数，也不解析或转发硬件配置路径 |
| `app.events` | `EventSink`、`MeasurementEvent`、`ProgressEvent`、`MessageEvent`、`RealtimeDataEvent`、`CheckpointEvent`、`CompletedEvent`、`StoppedEvent`、`FailedEvent` | 测量事件与事件接收端口 |
| `app.cancellation` | `CancellationMode`、`MeasurementCancelled`、`CancellationToken` | 取消语义 |

### 3.2 应用用例

| 模块 | 正式公开名称 | 说明 |
| --- | --- | --- |
| `application.configuration_service` | `load_for_run` | 正式配置加载入口 |
| `application.configuration_draft` | `ConfigurationDraft`、`ConfigurationDraftResult`、`DeviceBindingDraft`、`DeviceBindingsDraft`、`PowerAssignmentDraft`、`PowerChannelDraft`、`PowerSupplyDraft` | 配置编辑草稿（GUI 重构步骤 3）：测试频率、功率扫描、压缩点、衰减器、DUT 输入限制、驱放模式、DUT/驱动供电分配、电源通道参数与设备绑定。频率/功率/衰减与 DUT 输入使用 domain 值对象，集合字段为 `tuple`，电源绑定为受控键值对；`from_document` 返回草稿 + 结构化转换问题，`to_document` 只输出配置编辑段（不生成 `test_plan` / `run_mapping`，不写 `schema_version`）。缺失/非法类型/空引用保留为 `None`/空集合，不自动挑选默认设备；不含 Qt、文件路径、仪器对象或运行标识 |
| `application.configuration_editing` | `ValidationIssue`、`ValidationResult` | 配置编辑的结构化问题与结果（GUI 重构步骤 3）：问题至少包含路径、稳定错误码和用户文案，结果区分错误/警告且没有错误即有效；用户可修正的配置问题用它返回，不用异常控制正常流程；不含 Qt、文件路径或仪器对象 |
| `application.configuration_authoring` | `ConfigurationAuthoringError`、`ConfigurationAuthoringService`、`ConfigurationEditingDocument`、`DUT_CHANNEL_ROLES`、`OFFICIAL_SCHEMA_VERSION`、`author_official_configuration`、`parse_db_value` | 正式配置文档的唯一生成服务（GUI 重构步骤 4）：`author(draft)` 接受 `ConfigurationDraft`，`author_document(ConfigurationEditingDocument)` 接受明确的配置编辑 DTO；两者走同一套规则，补齐 `schema_version` 并生成正式 `test_plan` / `run_mapping`。`validate_document(ConfigurationEditingDocument)` 在同一编排里调用 domain 的 `validate_run_configuration` 与 `validate_hardware_connections`，把问题转成带路径、来源错误码和严重级别的 `ValidationResult`，表现层只展示结果。只做结构与字段的忠实转换，不读控件、不碰文件、不创建仪器；缺失或非法字段保持原样，由 domain 的正式校验在连接前报告。`presentation.qt.configuration_authoring` 只保留了转发壳 |
| `application.device_binding` | `AppliedDeviceBinding`、`ApplyDeviceSelectionRequest`、`DeviceBindingResult`、`DeviceBindingService`、`POWER_SUPPLY_BINDINGS`、`SUPPLY_ID_SEPARATOR`、`SelectedDevice`、`binding_device_key`、`binding_identity_problem`、`supply_id_problem` | 设备选择与设备绑定的唯一写入边界（GUI 重构步骤 5）：`SelectedDevice` 是发现结果的一条字段快照，`ApplyDeviceSelectionRequest` 是“应用本次勾选结果”的命令 DTO，`DeviceBindingService.apply(request)` 负责角色唯一性、电源引用可用性、地址/身份完整性、同一台设备不重复绑定与**整批替换**，成功时返回新的 `device_bindings` 集合（`DeviceBindingResult.bindings`）与显示摘要，失败时返回结构化 `ValidationResult`（不写半份绑定、不使用异常）。`binding_device_key` / `binding_identity_problem` 是绑定记录的读取边界身份判定（R13 的记录级部分），`supply_id_problem` 是 R14 的唯一实现；`presentation.qt.config_form_bindings` 只导入复用，不再定义第二份。错误边界：用户可修正的问题返回结构化 `ValidationResult`；调用方契约错误（请求类型不对、设备记录不是对象）抛 `TypeError`。可写角色取自 `domain.instruments.DEVICE_ROLES`，服务不维护第二份角色清单。服务不读控件、不写文件、不创建仪器、不更新 Qt 控件 |
| `application.configuration_view_model` | `ConfigurationAction`、`ConfigurationBindingOutcome`、`ConfigurationPageState`、`ConfigurationTestParameters`、`ConfigurationViewModel`、`CREATE_RUN`、`DEFAULT_RUN_STATUS`、`SAVE_CONFIGURATION` | 配置编辑页的状态视图模型（GUI 重构步骤 6；步骤 7 收口整份表单）：纯 Python，不导入 PySide6、不读控件、不读写文件、不创建仪器、不弹窗。`ConfigurationPageState` 持有 `ConfigurationDraft`、设备绑定快照、最近一次的 `ValidationResult`、运行状态文本与 dirty；`load` / `update_form` / `update_test_parameters` / `apply_device_selection` / `validate` / `set_run_status` / `mark_clean` 返回新的不可变状态（绑定等嵌套容器也是只读的，需要可写副本时用 `bindings_snapshot()` / `to_document()`）；`update_form` 用页面经表单 Adapter 读出的整份文档同步草稿（含电源通道参数与供电分配）但不重置干净基线，`load` 才重置基线。`save` / `create_run` 只返回 `ConfigurationAction`（完整编辑文档；实际保存与运行创建仍由 `ApplicationCoordinator`/应用端口执行）。`apply_device_selection` 委托 `application.device_binding.DeviceBindingService`，失败时保留旧绑定；`validate` 委托 `application.configuration_authoring` 的编写与正式校验。错误边界：用户可修正的问题返回结构化结果，调用方契约错误（文档/参数/方法参数类型不对）抛 `TypeError`/`ValueError` |
| `application.run_service` | `RunSnapshot`、`create_run_snapshot`、`load_run_configuration`、`inspect_run`、`load_hardware_config`、`find_run_hardware_config`、`ensure_run_resumable`、`has_run_contract` | 运行创建、快照校验与可恢复性判断；正式测量必须绑定 `run_id`。`has_run_contract` 是运行契约的唯一判据（运行标识 + 运行目录 + 运行仓储 + 冻结快照解析入口），`PreparedRun.has_run_contract` 与 GUI 边界判据都委托它 |
| `application.run_stages` | `RunStageRecorder`、`StageInputUnavailable` | 阶段输入/结果登记 |
| `application.measurements` | `CableLossUseCase`、`DriverPowerMappingUseCase`、`AmplifierMeasurementUseCase`、`AmplifierCalibrationError`、`validate_amplifier_calibration_inputs` | 三类正式测量用例入口 |
| `application.measurements.services` | `CableLossService`、`DriverPowerMappingService`、`AmplifierMeasurementService` | 测量服务（依赖注入端口，不导入具体仪器 flow） |
| `application.gui_data_service` | `GuiDataService` | 只读历史/导出应用服务 |
| `application.errors` | `AutoTestError`、`ErrorCategory`、`FailureRecord`、`ConfigurationError`、`DeviceConnectionError`、`ProtocolError`、`DeviceCapabilityError`、`MeasurementError`、`CleanupError`、`cause_of`、`normalize_failures` | 跨层异常分类：连接、协议、设备能力、测量、清理与配置；清理聚合异常逐项记录失败动作 |
| `application.export_service` | `ExportRequestError`、`ResultExportUseCase` | 导出用例：执行 `ExportRequest`，只接受历史列表中的结果标识；报告端口必须返回真实产物位置 |
| `application.archival_service` | `ArchivalRequestError`、`ResultArchivalService`、`archive_completed_stage` | 自动归档用例：只接受 `ArchivalRequest`（`run_id` + 阶段），按 manifest 推导测试状态，按 `(source_result, kind)` 幂等登记产物；正式结果永不改写。`archive_completed_stage` 是阶段完成边界的调用入口：归档失败只写日志，不改变已保存的结果 |
| `application.run_history_service` | `RunHistoryRequestError`、`RunHistoryService` | 历史运行用例：列出运行/阶段/产物状态（只读端口），并对**已登记**运行重新生成失败的交付产物（`retry_failed=True`，写回原运行目录）；不接受任意运行标识或路径 |
| `application.lifecycle.measurement_lifecycle` | `MeasurementLifecycle`、`StopIntent` | 测量生命周期 |
| `application.lifecycle.measurement_runtime` | `MeasurementRuntimeCoordinator`、`RuntimeDecision` | 运行协调与决策 |

### 3.3 应用端口

| 模块 | 正式公开名称 |
| --- | --- |
| `application.ports.config_repository` | `ConfigurationLoadResult`、`ConfigurationRepository`、`TestPlanRepository`、`RunMappingRepository` |
| `application.ports.result_repository` | `MeasurementResultRepository`、`SavedMeasurementResult` |
| `application.ports.run_history` | `RunHistoryRepository`、`RunHistoryEntry`、`RunHistoryState`、`RunStageResultEntry` |
| `application.ports.run_repository` | `RunRepositoryPort`、`RunLayout` |
| `application.ports.result_artifacts` | `ArtifactGenerationError`、`ArtifactGenerationRequest`、`GeneratedArtifact`、`ResultArtifactGeneratorPort` |
| `application.ports.result_input_reader` | `ResultInputReader` |
| `application.ports.measurement_session` | `SafetyCleanupPort`、`MeasurementSessionPort`、`MeasurementSessionBinding`、`CleanupRecord`、`CleanupStepStatus`、`SafetyCleanupError` |
| `application.ports.gui_data_service` | `GuiDataService`、`ResultFileDto`、`LoadedResultDto`、`ConfigurationPort`、`ResultQueryPort`、`ResultExportPort`、`ResultExportServicePort`、`RunArchiveStatusPort`、`RunHistoryServicePort` |
| `application.ports.device_discovery` | `DEMO_SUBNET`、`DeviceDiscoveryError`、`SCAN_MODE_NETWORK`、`SCAN_MODE_SIMULATED` |

正式配置入口为 `domain.configuration` 和 `infrastructure.config`；正式结果仓储入口为
`application.ports.result_repository` 及其基础设施实现 `infrastructure.persistence.result_repository`；
正式测量入口为 `application.measurements` / `application.run_service`。安全清理端口
`application.ports.measurement_session` 是 application 感知设备清理的唯一入口，具体
`SafetyShutdownFlow` 由 `instrument.flow.cleanup.SafetyCleanupAdapter` 适配。

正式用户配置只有一个写入路径：`ConfigurationPort.save` ->
`infrastructure.gui_data_service.JsonConfigurationAdapter` ->
`JsonConfigurationRepository.save_document`。序列化格式（`indent=4`、UTF-8、非 ASCII 原样）
与“临时文件 + fsync + 原子替换”的失败语义只在这一个方法里定义，GUI 适配器不打开文件、
不管理临时文件；旧电源配置迁移器写同样的配置文件时也委托它。运行目录内的
`runs/<run_id>/config.json` 与 manifest 是独立用途的快照，仍由
`infrastructure.persistence.run_repository` / `run_directory` 写入，不经过配置仓储。

正式用户配置的读取同样只有一条路径：`ConfigurationPort.load` ->
`infrastructure.gui_data_service.JsonConfigurationAdapter` ->
`infrastructure.config.json_io.load_config_file` -> `load_json_object`；启动器校验
（`infrastructure.config.validation`）与 `JsonConfigurationRepository.load()` 复用同一个
`load_json_object` 读取边界，配置仓储因此只有 `save_document` 一个持久化方法。写入链路与
读取链路的边界由 [`tests/test_config_write_boundary.py`](../tests/test_config_write_boundary.py)
静态钉死。

### 3.4 基础设施适配器

| 模块 | 正式公开名称 |
| --- | --- |
| `infrastructure.config.json_io` | `load_json_object`、`load_config_file` |
| `infrastructure.config.validation` | `load_config`、`validate_config_file`、`validate_official_config` |
| `infrastructure.config.json_config_repository` | `JsonConfigurationRepository`（`save_document` 是正式用户配置的唯一写入口，也是本仓储唯一的持久化方法；正式配置文档的读取统一走 `infrastructure.config.json_io`）、`JsonTestPlanRepository`、`JsonRunMappingRepository`、`load_json`、`load_test_plan`、`load_run_mapping`、`load_run_configuration`、`load_combined_configuration` |
| `infrastructure.persistence.result_repository` | `FileMeasurementResultRepository` |
| `infrastructure.persistence.run_repository` | `RunRepository` |
| `infrastructure.persistence.run_history_repository` | `FileRunHistoryRepository` |
| `infrastructure.gui_data_service` | `JsonConfigurationAdapter`（只把配置文件路径绑定到配置仓储：读取走 `json_io`，写入只委托 `save_document`，不打开文件、不掌握格式）、`ResultQueryAdapter`、`ResultExportAdapter` |
| `infrastructure.logging.app_logging` | `get_logger`、`setup_logging` |
| `infrastructure.runtime` | `current_runtime`、`runtime_id`、`configured_python`、`python_version_requirement`、`python_version_supported`、`interpreter_command`、`verification_commands` |

### 3.5 领域层

| 模块 | 正式公开名称 |
| --- | --- |
| `domain.configuration.models` | `TestPlan`、`InstrumentMapping`、`PowerSupplyMapping`、`ChannelMapping`、`PowerChannelPlan`、`RunResourceMapping`、`RunConfiguration`、`validate_test_plan`、`validate_run_mapping`、`validate_run_configuration` |
| `domain.configuration.rules` | `validate_config`、`simulated_binding_issues` |
| `domain.configuration.types` | `ConfigIssue`、`ConfigValidationResult` |
| `domain.run.models` | `FileReference`、`RunManifest`、`RunStatus`、`StageRecord`、`StageStatus`、`ArtifactKind`、`ArtifactRecord`、`ArtifactStatus`、`default_manifest`、`default_artifact_path`、`normalize_artifact_path`、`generate_run_id`、`validate_run_id`、`utc_now_iso` |
| `domain.measurement.calculations` | `calculate_cable_losses`、`compensate_amplifier_output_power`、`compensate_driver_output_power`、`interpolate_driver_output_power`、`calculate_dut_input_power`、`calculate_gain`、`calculate_efficiency`、`calculate_compression_result` |
| `domain.measurement.values` | `MeasurementValueError`、`MeasurementUnitError`、`Frequency`、`Decibels`、`PowerDbm`、`PowerWatt`、`Percent`、`CableLossProfile`、`CableLossTable`、`DriverOutputTable`、`CompressionPoint`、`CompressionResult` |

正式结果读取的唯一入口是 `application.dto.measurement_results`（见第 4 节）；领域层不再
定义结果模型或结果读取模块。

### 3.6 仪器层

| 模块 | 正式公开名称 |
| --- | --- |
| `instrument.ports` | `InstrumentSession`、`InstrumentState`、`PowerSupplyPort`、`SignalGeneratorPort`、`SpectrumAnalyzerPort`、`MeasurementInstrumentPort`、`SIGNAL_MEASUREMENT_CAPABILITIES`、`DRIVER_POWER_CAPABILITIES`、`DUT_POWER_CAPABILITIES`、`InstrumentCapabilityError`、`require_measurement_capabilities` |
| `instrument.session` | `ManagedInstrumentSession`、`InstrumentSessionError` |
| `instrument.flow.safety_shutdown_flow` | `SafetyShutdownFlow`、`SafetyShutdownError` |
| `instrument.flow.cleanup` | `cleanup_measurement`、`SafetyCleanupAdapter` |

### 3.7 表现层

| 模块 | 正式公开名称 | 说明 |
| --- | --- | --- |
| `presentation.qt.main_window` | `MainWindow` | 窗口生命周期、页面注册与信号连接 |
| `presentation.qt.pages` | `PageContext`、`PageDefinition`、`BasePage`、`build_pages` | 页面注册契约 |
| `presentation.qt.composition` | `build_gui_data_service` | 表现层依赖装配 |
| `presentation.qt.measurement_controller` | `MeasurementController` | 测量意图转发 |
| `presentation.qt.run_contract` | `is_valid_prepared_run`、`require_prepared_run` | 正式运行契约校验 |

页面之间不得互相导入内部实现来绕过 controller；页面使用的其他表现层模块登记在第 5 节。

配置编辑的分层归属统一登记在[配置领域词汇与模块归属](配置领域词汇与模块归属.md)：配置规则与值对象属于
`domain`，配置编辑用例、正式配置编写与设备绑定决策属于 `application`，控件读写的纯 Python 适配属于
presentation adapter，持有呈现层状态并编排用例的协调器属于 presentation controller，控件与布局属于
Qt 视图。第 5 节里 `presentation.qt.*` 的路径只表示**今天的实现位置**，不等于业务规则归属；表中标注
“迁移期”的模块在对应重构步骤完成后只保留转发壳或转为纯视图。只影响控件初值的决定（如缺省电源选择）
登记为显示策略，不是业务规则，也不进正式文档。

## 4. 正式 DTO

| 模块 | 正式公开名称 | 说明 |
| --- | --- | --- |
| `application.dto` | `MeasurementRequest`、`CableLossMeasurementRequest`、`DriverPowerMappingRequest`、`AmplifierMeasurementRequest`、`AMPLIFIER_COMPRESSION_TARGETS_DB`、`AmplifierInputError`、`AmplifierMeasurementInputs`、`PowerSweep`、`SupplyChannel`、`ExportFormat`、`ExportOutcome`、`ExportRequest`、`MeasurementResult`、`MeasurementStatus`、`MeasurementType`、`CableLossMeasurementResult`、`DriverPowerMappingMeasurementResult`、`AmplifierMeasurementResult`、`CableLossRow`、`DriverPowerPoint`、`AmplifierCompressionPoint`、`AmplifierFrequencyResult`、`measurement_result_from_document`、`measurement_type_for_result_type`、`require_formal_result`、`normalize_frequency_key` | 正式 DTO 汇总再导出路径 |
| `application.dto.amplifier_inputs` | `AMPLIFIER_COMPRESSION_TARGETS_DB`、`AmplifierInputError`、`AmplifierMeasurementInputs`、`PowerSweep`、`SupplyChannel` | 主功放用例的正式输入模型（技术债步骤 7）：频率、功率扫描、压缩目标、衰减器、DUT 输入限制、驱动模式与 DUT 供电通道；字段均为显式值对象（GHz/dB/dBm 由类型表达），构造即校验，非法输入抛 `AmplifierInputError`（配置失败）；不复刻旧配置字典键名，不含仪器地址、运行标识或校准输入 |
| `application.dto.export_requests` | `ExportFormat`、`ExportRequest`、`ExportOutcome` | 导出请求与结构化结果：格式为 JSON / CSV / 测试报告；JSON、CSV 必须带目标文件，报告位置由导出端口决定；`ExportOutcome` 只含 `result_id`、`format`、`filename`、`artifact_path`，不含界面文案 |
| `application.dto.measurement_requests` | `MeasurementRequest`、`CableLossMeasurementRequest`、`DriverPowerMappingRequest`、`AmplifierMeasurementRequest` | 测量请求 |
| `application.dto.measurement_results` | `MeasurementResult`、`MeasurementStatus`、`MeasurementType`、`CableLossMeasurementResult`、`DriverPowerMappingMeasurementResult`、`AmplifierMeasurementResult`、`measurement_result_from_document`、`measurement_type_for_result_type`、`require_formal_result` | 三类正式测量结果、显式行视图、结果文档唯一解析入口与正式结果断言 |
| `application.dto.archival` | `ArchivalRequest`、`ArchivalOutcome`、`ArchivalStatus`、`ArchivalTestStatus`、`ArchivedArtifact` | 自动归档 DTO：请求只带 `run_id` 与阶段；结果区分测试状态与产物状态，只携带运行内相对路径；不含 Qt、文件系统访问或页面文案 |
| `application.dto.run_history` | `RunArchiveState`、`RunStageSummary`、`RunArtifactSummary`、`RunHistorySummary` | 历史运行只读视图：运行可用性（可用/结构不完整/不可用）、阶段状态与正式结果标识、交付产物状态；只带运行内相对路径，不读文件、不判断文件是否存在，不含页面文案；`RunArtifactSummary.missing` 只承载适配器填写的“已生成但文件不在”诊断 |

测量结果的唯一正式模型是 `application.dto.measurement_results` 的三个 DTO；`domain.models`
只保留运行、方案与会话模型，`domain.run.models` 保留运行 manifest。正式展示与导出只消费
DTO 的显式属性（`report_rows()`、`sweep_rows()`、`saturation_points()`）或分析层的
`analysis.result_source.ReportResult` 结构契约，不得依赖 `Mapping[str, Any]` 或旧字段别名。

- `measurement_result_from_document()` 是唯一读取入口；`require_formal_result()` 断言输入
  已是三个具体正式结果之一，共享基类 `MeasurementResult` 只是契约载体，不能持久化或展示。
- `measurement_type_for_result_type()` 是结果类型标签（`cable_loss` /
  `driver_power_mapping` / `amplifier` / `amplifier_measurement`）到正式测量类型的唯一映射，
  写入、历史查询和门禁都使用同一份映射，避免标签与模型各自解释。
- 历史查询端口 `ResultQueryPort.load()` / `latest_result()` 返回的 `LoadedResultDto.result`
  已经是正式结果 DTO；原始 JSON 只通过 `read_bytes()` 逐字节导出。
- 实时事件的 `RealtimeDataEvent.data` / `controller` 的 `data_update` 是界面刷新用的临时
  事件载荷，不是结果模型：它们不进 manifest、不落盘，也不参与历史查看或报告生成。

主功放测量的**正式输入**是 `application.dto.amplifier_inputs`（技术债步骤 7）：

- `AmplifierMeasurementInputs` 只有七个必填字段：`frequencies`（`Frequency`，内部 GHz）、
  `sweep`（`PowerSweep`：起始/终止 `PowerDbm` + 步进 `Decibels`）、`compression_target`
  （`Decibels`，只接受 `AMPLIFIER_COMPRESSION_TARGETS_DB`，其值由测试方案 schema 的
  `SUPPORTED_COMPRESSION_POINTS` 同源解析）、`attenuator`（`Decibels`，非负）、
  `max_dut_input_power`（`PowerDbm`，必填，不再默认“不限制”）、`driver_enabled`（严格
  布尔）与 `dut_power_channels`（`SupplyChannel` 序列，非空且不重复）。
- 单位只由值对象表达：不接受裸数值、“30dBm”这类文本，也不接受把步进当成绝对功率。
- `PowerSweep.points()` 将 `stop` 定义为扫描上限：步进不能整除区间时只返回不超过
  上限的网格点，不生成越过终点的点；主功放测量服务（技术债步骤 8）直接使用该语义。
- `AmplifierMeasurementInputs.from_run_configuration()` 是运行配置到正式输入的唯一转换
  入口（技术债步骤 8/9/10）：只读取 `TestPlan` / `RunResourceMapping` 的类型化字段，非法或
  缺失字段统一收敛为 `AmplifierInputError`。DUT 通道必须已经包含显式
  `supply_id/channel` 路由；裸通道名即使来自带单数电源条目的旧映射也会被拒绝。
- 配置文件边界目前只接受 `frequencies.unit=GHz`、`signal_source.unit=dBm` 和
  `attenuator.unit=dB`；不支持的单位在配置校验阶段拒绝，不作静默换算。供电 `supply_id`
  是否存在于本次运行资源映射由运行配置/应用边界校验，DTO 本身不携带设备地址或设备对象。
- `SupplyChannel` 只接受显式路由（`supply_id` + `channel`，或经 `from_routing()` 解析
  `"<supply_id>/<channel>"`）；裸通道名 `CH1` 不被猜成“唯一那台电源”。旧格式读取或迁移
  与主功放正式输入转换是不同边界，不在此 DTO 中做旧路由推断。`dut_supplies()`
  提供按电源分组的只读投影。
- 非法输入统一抛 `AmplifierInputError`（`ConfigurationError` 子类，配置失败），在打开任何
  设备之前失败；模型不做文件读取、不接触仪器、不生成结果文档，仪器地址与传输对象仍由
  instrument 端口/运行上下文提供。

## 5. 正式内部模块

这些模块允许正式代码导入，但**不是**公共 API：不承诺名称与签名稳定，不得被外部脚本依赖。
新增内部模块并在其他模块中导入时，必须先在测试的 `INTERNAL_MODULES` 登记。

| 模块 | 说明 |
| --- | --- |
| `analysis.reporting` | 分析层 CSV/HTML 报告写入适配器：只接受正式结果 DTO，不解析原始文档 |
| `analysis.result_source` | 分析层只读结果协议与文档展平 |
| `analysis.visualization` | 分析层只读可视化与报告数据构建 |
| `app` | 装配包（`__init__` 为空）；具体入口见 `app.*` 公共 API |
| `application.inputs` | 结果输入读取包 |
| `application.inputs.result_reading` | 报告参数叠加（内部实现；输入必须是正式结果 DTO） |
| `application.lifecycle` | 测量生命周期包 |
| `domain.instruments` | 设备表领域包 |
| `domain.instruments.labels` | 设备缩写设备码生成与显示文本：完整设备码归一化复用、前缀提取、末四位与冲突扩展（纯计算，不写注册表；缩写码不含通道分隔符），缩写码对所有登记设备生效（电源的缩写码同时是 `supply_id`），以及电源与其它仪器统一的 `型号（缩写设备码）` 显示、详情与审计用的身份文本、日志里的通道名（缩写码/CHx）与身份核对结论（日志、页面与发现卡片共用同一份） |
| `domain.instruments.models` | 仪器身份解析（`device_key` = 厂商 + 完整设备码是唯一标识）、可重复型号、缩写设备码字段、旧槽位识别（`is_legacy_power_supply_slot`，只读兼容，不得用于生成电源键）、地址别名与在线状态模型；不生成、不校验 `name` / `model_index` |
| `domain.models` | 运行、测试方案与会话生命周期模型；测量结果契约在 `application.dto` |
| `infrastructure.filesystem.atomic_json` | 原子 JSON 写入 |
| `infrastructure.filesystem.paths` | 路径服务 |
| `infrastructure.config.legacy_power_supply_migration` | 旧 `PS1..PS4` 配置迁移器：按设备表登记的身份把它们改写成 `supply_id`（电源集合、分配引用、设备绑定与运行映射），输出逐条迁移摘要；只有每条都能识别且引用都落地时才写盘（写前必须过文件级校验），写盘委托配置仓储的 `save_document`，未决项用 `save_migration_summary` 的摘要文件留存 |
| `infrastructure.discovery` | 仪器发现包 |
| `infrastructure.discovery.instrument_discovery` | 网段扫描与 `*IDN?` 身份解析：按 `device_key` 匹配设备表，未知设备同样上报 |
| `infrastructure.discovery.simulated_network` | 离线演示用的设备网络响应（仅模拟扫描模式，不会被当成现场结果） |
| `infrastructure.persistence` | 持久化适配器包 |
| `infrastructure.persistence.artifact_generator` | 自动归档产物（CSV 与测试报告）生成适配器：只读 manifest 已登记并校验过的正式结果，产物的受控路径固定在运行目录 `artifacts/` 下；`ArchiveArtifactGenerator` 把两类实现组合成归档服务持有的唯一生成端口 |
| `infrastructure.persistence.instrument_registry` | 设备表 JSON 仓储：`device_key`（厂商 + 完整设备码）为唯一标识，型号可重复、缩写设备码全局唯一，IP 只是可漂移的地址别名，固定测试角色随设备登记；旧 schema 的 `serial` / `name` / `model_index` 只在读取边界投影为 `full_code` |
| `infrastructure.persistence.json_encoder` | JSON 编码器 |
| `infrastructure.persistence.json_result_repository` | 结果 JSON 实现细节 |
| `infrastructure.persistence.run_directory` | 运行目录文件布局 |
| `infrastructure.runtime.dependency_lock` | 锁定清单的严格解析、整份环境核对与漂移分类；公共入口是 `infrastructure.runtime` |
| `infrastructure.runtime.runtime_contract` | 运行时契约实现；公共入口是 `infrastructure.runtime` |
| `instrument.assembly_plan` | 硬件装配计划：把配置解析成一份**只读的计划对象**（信号源/频谱仪地址与能力参数、每台电源的地址/缩写设备码/完整设备码/声明通道、`gate`/`drain` 角色与 `supply_id/CHx` 路由 token、日志文本、急停顺序），不带原始配置段；只读配置不接触 VISA，地址重复/角色缺失或重复/通道不存在/引用歧义都在连接任何仪器之前失败 |
| `instrument.flow` | 安全流程包；application 只通过 `application.ports.measurement_session` 依赖清理端口 |
| `instrument.measurement_adapter` | 测量服务端口的显式适配器：设备方法显式定义，不按方法名转发，也不从注入对象推导路由 |
| `instrument.measurement_factory` | 硬件/离线测量端口装配：按装配计划打开 VISA 资源、构造驱动与会话（电源身份与初始输出检查在这里），装配失败时逆序释放已打开资源再抛原始异常；不解释任何配置键，配置解释在 `instrument.assembly_plan` |
| `instrument.power_routing` | 供电分配解析：把分配里的电源引用（`supply_id`、旧 `label`、残留 `visual` 槽位）解析成唯一的已启用电源键，并把被分配的通道解析成 `gate`/`drain` 角色 token、电压电流设置、日志写法（`缩写设备码/CHx`）与急停顺序；纯计算：不看仪器地址、不接触 VISA |
| `instrument.safety` | 设备安全动作与资源所有权边界；关闭回调的安全标记按显式签名判定调用形式 |
| `presentation.qt.application_coordinator` | GUI 运行协调器：配置、运行契约与仪器端口所有者（经 `ConfigurationPagePort` 与配置页交互）。属于 presentation controller：持有呈现层配置状态并编排配置保存、运行创建/加载与端口生命周期，不读控件、不定义业务规则；正式配置编写委托 `application.configuration_authoring`（步骤 4） |
| `presentation.qt.archival_page` | 测量页面共享的归档状态生命周期：等待归档、保存中、结果展示、终止事件与运行切换后的旧摘要清理；不读文件、不判断产物可用性 |
| `presentation.qt.archival_status` | 归档结果到页面文案与按钮状态的映射：无 Qt、不读文件、不判断路径存在，四类状态各有独立提示 |
| `presentation.qt.archival_view` | 归档结果分组控件：显示标题/说明并按提示启用“打开结果目录/测试报告”；位置由窗口注入；动态文案按纯文本渲染 |
| `presentation.qt.config_form_bindings` | 设备绑定的读取与规范化：`device_bindings` 到 `supply_id` 集合、早期绑定结构与只有地址的旧配置折叠、不可用记录的问题清单（记录里声明出的厂商/完整设备码都必须是 `device_key` 的一部分，任一片段对不上即不可用；旧 `serial` 只在读取边界投影为 `full_code`；纯数据：不读控件、不写配置、不碰 Qt）。属于 presentation adapter：只做读取与折叠，**记录级**身份一致性判定、`supply_id` 可用性、角色/设备唯一性与整批替换规则都从 `application.device_binding` 导入复用（步骤 5 已完成），不再定义第二份 |
| `presentation.qt.config_form_state` | 配置表单状态：按控件最小读取协议读出配置片段、合并为配置、给出页面自己的校验说明（设备绑定的读取与规范化在 `config_form_bindings`；正式 schema 由 `application.configuration_authoring` 构造）。属于 presentation adapter：把控件值转成文档片段，读数后的合法性判据由 `domain` 判定；页面不直接调用本模块，控件读写统一经 `presentation.qt.config_form_adapter`（步骤 7）。页面侧的供电角色/分配提示仍是迁移期副本（步骤 13 收敛），但正式运行校验已改走 `ConfigurationAuthoringService.validate_document`，本模块不再导入 `domain.configuration.models` 或直接调用 `validate_hardware_connections` |
| `presentation.qt.config_form_adapter` | 配置页表单 Adapter（GUI 重构步骤 7）：控件读写与配置组装的唯一边界。`read_form` 用一次控件读数同时给出完整配置文档与测试参数快照，集中 `ConfigFormState` 的读数与校验说明、`config_projection` 的回填投影、电源分配下拉框的业务值（只看 `itemData`）与刷新，以及把一组用户文案合并交给页面错误通道；页面只保留控件注册、信号连接与派生界面状态。本身不导入 PySide6，可用控件替身测试；不含设备绑定或正式配置业务规则，也不生成正式 `test_plan` / `run_mapping` |
| `presentation.qt.config_page` | 配置页面（编辑配置编写文档；向协调器提供 `ConfigurationPagePort` 窄接口）。属于 Qt 视图：控件、信号与派生界面状态；`apply_discovered_devices` 只提交设备勾选结果并把 `application.device_binding` 返回的绑定集合投影到页面（步骤 5 已完成），不再包含绑定校验循环；整份控件读数经 `presentation.qt.config_form_adapter` 收口后写入 `application.configuration_view_model.ConfigurationViewModel`（步骤 6/7），`build_config` / `apply_config` / `validate` 只是 adapter + 视图模型的短委托 |
| `presentation.qt.config_page_widgets` | 配置页面控件构建辅助（固定操作栏布局 + 页面共用的数值框/下拉框工厂；控件仍登记在页面上） |
| `presentation.qt.config_power_section` | 配置页电源分区的控件构建：按电源键分页的通道参数标签页与电源分配设置表单，控件仍登记在页面上（页面负责信号连接、派生界面状态与配置读写；条目、可选项与分配引用以显式声明的类型传入，不从控件或文件里取状态） |
| `presentation.qt.connection_page` | 仪表配置页：欢迎页选定测试模式后的整屏页面（不登记进标签栏），大字号页标题、标题下居中的扫描网段与扫描方式、勾选本次测试所需的设备（“查找设备”是结果区顶部、紧跟扫描行的落点，结果区不画外框）、结果区右下的“连接仪器”，点“下一步”时整批写入勾选结果并进入仪器配置页；设备绑定的写入与派生界面（电源标签页、电源分配可选项）仍归 config_page |
| `presentation.qt.device_cards` | 设备发现的仪器展示卡片：第一行只写加粗放大的缩写设备码（设备表登记过就用它，电源的缩写码同时是 `supply_id`，如 `DP8B1519`、`5462`；还没有缩写码时退回完整设备码，绝不凭空生成），第二行仪器图片（不加底板、不描边，等比缩放到同一视觉体积），最下面一行居中放大的仪器型号（如 `DP832A`、`SMW200A`）；圆角矩形，点击卡片即选中；卡片尺寸 `CARD_WIDTH` × `CARD_HEIGHT` 是唯一来源，“查找设备”按钮也按它取大小 |
| `presentation.qt.device_discovery_panel` | 仪器连接配置（设备发现）：扫描网段、结果区（不画外框：无边框、无底色，未查找时“查找设备”卡片按钮紧跟在扫描行下面，点下去后按钮收走、开始加载设备）、以卡片展示已登记/未分配角色/未登记/无法识别结果、点击卡片即选中（角色取自设备表）、按配置身份预选；扫描线程只读探测，不改配置也不连接仪器 |
| `presentation.qt.scan_card` | “查找设备”的卡片按钮：按钮本体是与仪器卡片同大的自绘深色卡片（尺寸取自 `presentation.qt.device_cards` 的 `CARD_WIDTH` × `CARD_HEIGHT`，渐变卡面、四角角标、卡面内发光），正中的渐变标题写着“查找设备”，卡片内部有横向光带、扫描线与外飘粒子在动；悬停时卡面上的两行字在 0.6s 里淡到看不见（原 CSS 的 `::after` 霓虹光斑与 `::before` 紫→蓝底卡都不保留，卡片外面只有一层投影）；要露在卡片外面的投影画在同级、不进布局、不吃鼠标的透明覆盖层上，按钮因此与卡片一样大，点击行为仍由设备发现面板接手 |
| `presentation.qt.config_projection` | 配置编写文档（authoring document）到配置页控件的确定性投影：重置、逐字段投影、非法类型回落；正式 `test_plan` / `run_mapping` 只由 `application.configuration_authoring` 生成。属于 presentation adapter（无 Qt 依赖）：缺省电源选择与通道初值是显示策略（第 5.3 节），不是业务规则，也不进正式文档 |
| `presentation.qt.configuration_authoring` | 配置页保存的兼容转发壳（GUI 重构步骤 4）：只再导出 `application.configuration_authoring` 的服务、错误、常量与文档入口，不含任何编写规则；迁移期结束后删除 |
| `presentation.qt.connection_diagrams` | 连接图渲染 |
| `presentation.qt.connection_dialog` | 连接确认对话框：`build_diagram()` 按需渲染连接图；连接图不可用时在对话框内说明原因并禁用确认按钮 |
| `presentation.qt.run_history_page` | 历史运行与结果管理页（只构造 `ExportRequest`，按 `ResultExportServicePort` 注入导出用例；运行/阶段/产物状态来自 `HistoryViewModel`，失败产物重新生成走 `RunHistoryServicePort`；用户文案由页面生成） |
| `presentation.qt.history_view_model` | 历史查看只读视图模型：结果列表、运行摘要、选中项、已加载结果与频率浏览（无 Qt、无路径） |
| `presentation.qt.input_controls` | 通用输入控件类（只提供输入行为；页面共用的构建工厂在 `config_page_widgets`） |
| `presentation.qt.measurement_state` | 测量状态对象 |
| `presentation.qt.measurement_worker_factories` | 测量 worker 工厂 |
| `presentation.qt.realtime_buffer` | 实时数据缓冲 |
| `presentation.qt.realtime_plot` | 实时绘图控件 |
| `presentation.qt.status_panel` | 状态/日志面板 |
| `presentation.qt.theme` | 统一主题：设计令牌、调色板与全局样式表（按钮层级与文字语义色的唯一来源） |
| `presentation.qt.visualization_page` | 历史可视化页面（只渲染控件并把动作转发给 `history_view_model`） |
| `presentation.qt.welcome_page` | 启动欢迎页与测试模式选择 |
| `presentation.qt.window_geometry` | 主窗口初始几何：按屏幕可用区域收缩并居中，避开任务栏与屏幕边缘 |
| `presentation.qt.workers` | 测量 worker 实现 |

## 6. 旧模块清单（非公共 API）

旧模块的唯一登记表是测试中的 `LEGACY_MODULES`，包含状态、处理步骤和源文件。登记表中的
全部条目均已删除，必须保持不存在；新增旧模块或恢复已删除文件都会使门禁失败。

### 6.1 根目录旧模块

| 旧模块 | 源文件 | 状态 | 处理步骤 |
| --- | --- | --- | --- |
| `cable_loss_measurement` | `cable_loss_measurement.py` | 已删除 | 步骤 4 |
| `driver_power_mapping` | `driver_power_mapping.py` | 已删除 | 步骤 4 |
| `amplifier_measurement` | `amplifier_measurement.py` | 已删除 | 步骤 4 |
| `config_io` | `config_io.py` | 已删除 | 步骤 5 |
| `config_models` | `config_models.py` | 已删除 | 步骤 5 |
| `config_validation` | `config_validation.py` | 已删除 | 步骤 5 |
| `result_reading` | `result_reading.py` | 已删除 | 步骤 6 |
| `result_storage` | `result_storage.py` | 已删除 | 步骤 3 |
| `measurement_calculations` | `measurement_calculations.py` | 已删除 | 步骤 16 |
| `measurement_lifecycle` | `measurement_lifecycle.py` | 已删除 | 步骤 16 |
| `measurement_services` | `measurement_services.py` | 已删除 | 步骤 16 |
| `app_logging` | `app_logging.py` | 已删除 | 步骤 16 |
| `project_paths` | `project_paths.py` | 已删除 | 步骤 16 |
| `legacy_config_conversion` | `legacy_config_conversion.py` | 已删除 | 已删除 |
| `enhanced_main_gui` | `enhanced_main_gui.py` | 已删除 | 已删除 |
| `llm` | `llm.py` | 已删除 | 已删除 |
| `web_search_tools` | `web_search_tools.py` | 已删除 | 已删除 |
| `instrument_control` | `instrument_control.py` | 已删除 | 已删除 |
| `data_visualization` | `data_visualization.py` | 已删除 | 已删除 |
| `connection_diagrams` | `connection_diagrams.py` | 已删除 | 已删除 |
| `persistence` | `persistence/__init__.py` | 已删除 | 已删除 |

### 6.2 正式包内旧实现

| 旧模块 | 源文件 | 状态 | 处理步骤 |
| --- | --- | --- | --- |
| `app.legacy_amplifier_measurement` | `app/legacy_amplifier_measurement.py` | 已删除 | 步骤 4 |
| `infrastructure.config.legacy_conversion` | `infrastructure/config/legacy_conversion.py` | 已删除 | 步骤 5 |
| `application.dto.legacy_result_adapter` | `application/dto/legacy_result_adapter.py` | 已删除 | 步骤 6 |
| `domain.result_reading` | `domain/result_reading.py` | 已删除 | 步骤 6 |
| `application.ports.result_history` | `application/ports/result_history.py` | 已删除 | 步骤 6 |
| `infrastructure.persistence.result_history_repository` | `infrastructure/persistence/result_history_repository.py` | 已删除 | 步骤 6 |
| `instrument.legacy_control` | `instrument/legacy_control.py` | 已删除 | 步骤 9 |
| `instrument.power_roles` | `instrument/power_roles.py` | 已删除 | 步骤 9 |

### 6.3 存量引用基线

旧模块禁止新增引用。存量引用基线必须与代码严格一致；新增引用不得靠扩大基线通过。基线统计
跨模块绝对导入。

| 旧模块 | 允许继续引用的文件 |
| --- | --- |
| （无） | 正式 production code 不引用任何旧模块；`instrument.measurement_factory` 已登记为内部模块 |

跨层依赖：`application` 只导入 `application.ports.measurement_session` 的安全清理协议，
不导入 `instrument.flow` 的具体实现；`SafetyCleanupAdapter` 位于设备安全层并实现该端口。

无法静态判定模块名的动态导入默认禁止。唯一例外是 `launcher.py` 的 `check_packages`：它只按
固定依赖表探测第三方包，不导入项目模块。白名单同样与代码严格一致，必须随代码同步调整，
默认只减不增。

## 7. 门禁与验证

`tests/test_public_api_inventory.py` 固化为以下断言：

1. 本文存在，且列出每个公共模块、内部模块、正式导入根；每个公开名称都出现在文档中。
2. 每个旧模块的表格行（模块名、源文件、状态、处理步骤）与登记表完全一致。
3. 公共模块存在，导出清单声明的名称；声明 `__all__` 时名称必须在其内。
4. 内部模块存在；公共模块与内部模块不得重叠；两类路径都位于登记的导入根下。
5. 旧模块登记表与工作树一致：已删除文件不存在，登记表与工作树严格对应。
6. 引用基线只引用已登记的旧模块和真实存在的文件。
7. 范围内代码不导入未登记的第一方顶层包。
8. 范围内代码不导入未登记的正式包内模块路径。
9. 范围内代码不使用 `from ... import *`。
10. 范围内代码的动态导入使用字符串常量；无法静态判定的调用必须命中白名单。
11. 旧模块引用与基线严格相等，新增引用立即失败。
12. `application` 不得导入 `instrument.flow` 具体实现，只能依赖安全清理端口。
13. 门禁辅助函数自身有回归测试（静态导入、`from package import module`、动态导入别名、
    非常量动态导入、星号导入）。

另有独立的静态质量门禁 `tests/test_static_quality_gate.py`（裸 `except`、静默宽泛捕获、
生产 `print`、旧模块导入、动态设备路由），与本文的导入登记门禁互补：本文冻结“允许导入什么”，
该门禁冻结“用什么写法处理失败、往哪里输出”。规则与基线见
[异常分类与静态质量门禁](异常分类与静态质量门禁.md)。

定向验证命令：

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" -m unittest tests.test_public_api_inventory -v
```

该命令只运行本文的清单门禁，不替代完整验证。交付前的完整验证以 `tools/run_tests.py` 为唯一
测试入口（内存编译检查 + 依赖核对 + 全部门禁 + 选定范围的测试），再执行 `launcher.py --check`
与 `launcher.py --validate-config`；直接运行 `unittest` 或单独执行 `tools/compile_check.py`
都不构成完整验证。命令见第 8 节。

## 8. 现行清单规模与验证

以下数字与 `tests/test_public_api_inventory.py` 中的 `FORMAL_API`、`INTERNAL_MODULES` 和
`LEGACY_MODULES` 一致：

- 正式公共 API：**57 个模块路径、255 个公开名称**。包括启动与装配入口、运行服务、应用
  端口、配置/结果/运行适配器、领域值对象、仪器端口与安全清理、表现层页面契约，以及主功放
  正式输入 `application.dto.amplifier_inputs`。
- 正式内部模块：**62 个路径**，公共与内部边界不重叠。配置页控件构建拆分到
  `presentation.qt.config_page_widgets` / `config_power_section`，设备绑定读取拆分到
  `presentation.qt.config_form_bindings`，表单控件读写收口到
  `presentation.qt.config_form_adapter`；仪器装配职责拆分到 `instrument.assembly_plan`
  （只读装配计划）与 `instrument.power_routing`（供电引用与角色路由），工厂只按计划打开
  资源、构造驱动与会话。
- 旧模块：**29 个条目**（21 个根模块、8 个正式包内实现），登记表全部为“已删除”，与工作树
  一致；第 6 节的“处理步骤”只是退役记录。
- 存量引用基线：空；正式 production code 不引用任何旧模块，新增引用直接失败。
- 跨层依赖基线：`application` 不导入 `instrument.flow` 具体实现，只依赖
  `application.ports.measurement_session` 的安全清理端口。
- 动态导入白名单：1 处（`launcher.py` 的 `check_packages` 依赖探测）。
- 发布边界：`release_manifest.py` 只登记正式发布文件与 `assets`，不维护兼容/回滚文件列表；
  依赖锁、解释器和验证命令同源记录（见[依赖与运行时契约](依赖与运行时契约.md)）。
- 生成型基线与大资源策略分别见 `baseline/README.md` 和
  [大体积静态资源与 Notebook 版本管理](静态资源与Notebook版本管理.md)。

现行验证命令：

```powershell
# 本清单的定向门禁
& "D:\Anaconda\envs\Auto_test\python.exe" -m unittest tests.test_public_api_inventory -v

# 交付前的完整验证（编译检查 + 依赖核对 + 全部门禁 + 全量离线测试）
& "D:\Anaconda\envs\Auto_test\python.exe" tools/run_tests.py
& "D:\Anaconda\envs\Auto_test\python.exe" launcher.py --check
& "D:\Anaconda\envs\Auto_test\python.exe" launcher.py --validate-config
```

## 9. 后续步骤维护约定

- 退役旧模块：整文件删除的把 `LEGACY_MODULES` 的 `present` 改为 `False`（或删除条目），
  重写后保留的从登记表删除；同步删除本文对应行、引用基线条目和发布清单登记，不要只放宽
  断言。发布清单只登记正式发布边界，不维护兼容/回滚文件列表。
- 删除存量引用：同步删除 `LEGACY_IMPORT_BASELINE` 的对应文件；基线必须与代码严格相等。
- 新增公共模块或公开名称：先登记到本文和 `FORMAL_API`，再写调用方。
- 新增内部模块并被跨模块导入：先登记到本文和 `INTERNAL_MODULES`。
- 正式模块改名、删除公开名称或改变公共/内部层级：视同不兼容变更，单独提交。
