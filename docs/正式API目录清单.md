# 正式 API 目录清单

本文冻结正式公共 API、正式内部模块和已退役模块的目录边界。

本清单只冻结边界，不删除旧文件：旧模块按步骤 3 至 6、9、16 分批退役。标为“已删除”的模块
必须保持不存在；标为“待删除”“待清理”的模块在对应步骤执行前仍存在。

步骤 3（2026-10-26）已退役 `result_storage`，步骤 4（2026-10-26）已退役三类旧测量入口及其
主功放转发模块，因此这四个条目在本文与测试中均为“已删除”。

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
| `app.run_context` | `PreparedRun`、`create_prepared_run`、`load_prepared_run` | 运行契约对象与创建/重载入口；旧的 `prepare_run` 兼容前置校验入口已在技术债步骤 6 删除 |
| `app.gui_runtime` | `prepare_configuration`、`connect_instruments`、`create_offline_measurement_port`、`create_cable_loss_measurement`、`create_driver_mapping_measurement`、`create_amplifier_measurement` | GUI 侧装配入口；页面不得直接调用测量组装函数。`connect_instruments(prepared_run, *, recorder=None)` 只接受运行契约对象，硬件配置取自运行目录冻结的 `inputs/hardware_config.json`，路径在该入口内解析一次，**不接受任何配置路径参数**（技术债步骤 6 已删除 `config_path` 与 `resolved_config_path` 透传）；三个测量工厂没有位置参数，`prepared_run` 是必填关键字参数，也不解析或转发硬件配置路径 |
| `app.events` | `EventSink`、`MeasurementEvent`、`ProgressEvent`、`MessageEvent`、`RealtimeDataEvent`、`CheckpointEvent`、`CompletedEvent`、`StoppedEvent`、`FailedEvent` | 测量事件与事件接收端口 |
| `app.cancellation` | `CancellationMode`、`MeasurementCancelled`、`CancellationToken` | 取消语义 |

### 3.2 应用用例

| 模块 | 正式公开名称 | 说明 |
| --- | --- | --- |
| `application.configuration_service` | `load_for_run` | 正式配置加载入口 |
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

## 4. 正式 DTO

| 模块 | 正式公开名称 | 说明 |
| --- | --- | --- |
| `application.dto` | `MeasurementRequest`、`CableLossMeasurementRequest`、`DriverPowerMappingRequest`、`AmplifierMeasurementRequest`、`AMPLIFIER_COMPRESSION_TARGETS_DB`、`AmplifierInputError`、`AmplifierMeasurementInputs`、`PowerSweep`、`SupplyChannel`、`ExportFormat`、`ExportOutcome`、`ExportRequest`、`MeasurementResult`、`MeasurementStatus`、`MeasurementType`、`CableLossMeasurementResult`、`DriverPowerMappingMeasurementResult`、`AmplifierMeasurementResult`、`CableLossRow`、`DriverPowerPoint`、`AmplifierCompressionPoint`、`AmplifierFrequencyResult`、`measurement_result_from_document`、`measurement_type_for_result_type`、`require_formal_result`、`normalize_frequency_key` | DTO 汇总再导出路径；旧兼容导出已在步骤 6 删除 |
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
  上限的网格点，不生成越过终点的点；应用服务迁移到该模型后必须使用此语义。
- 配置文件边界目前只接受 `frequencies.unit=GHz`、`signal_source.unit=dBm` 和
  `attenuator.unit=dB`；不支持的单位在配置校验阶段拒绝，不作静默换算。供电 `supply_id`
  是否存在于本次运行资源映射由运行配置/应用边界校验，DTO 本身不携带设备地址或设备对象。
- `SupplyChannel` 只接受显式路由（`supply_id` + `channel`，或经 `from_routing()` 解析
  `"<supply_id>/<channel>"`）；裸通道名 `CH1` 不被猜成“唯一那台电源”，按单电源假设的
  历史兼容留给步骤 10 单独收敛。`dut_supplies()` 提供按电源分组的只读投影。
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
| `domain.instruments.labels` | 设备短标签生成与显示文本：序列号归一化复用、前缀提取、末四位与冲突扩展（纯计算，不写注册表；标签不含通道分隔符），标签对所有登记设备生效（电源的标签同时是 `supply_id`），以及电源与其它仪器各自的显示文本、详情与审计用的身份文本、日志里的通道名（短标签/CHx）与身份核对结论（日志、页面与发现卡片共用同一份） |
| `domain.instruments.models` | 仪器身份解析（序列号唯一标识）、设备类别与短标签字段、旧槽位识别（`is_legacy_power_supply_slot`，只读兼容，不得用于生成电源键）、地址别名与在线状态模型 |
| `domain.models` | 运行、测试方案与会话生命周期模型；测量结果契约在 `application.dto` |
| `infrastructure.filesystem.atomic_json` | 原子 JSON 写入 |
| `infrastructure.filesystem.paths` | 路径服务 |
| `infrastructure.config.legacy_power_supply_migration` | 旧 `PS1..PS4` 配置迁移器：按设备表登记的身份把它们改写成 `supply_id`（电源集合、分配引用、设备绑定与运行映射），输出逐条迁移摘要；只有每条都能识别且引用都落地时才写盘（写前必须过文件级校验），写盘委托配置仓储的 `save_document`，未决项用 `save_migration_summary` 的摘要文件留存 |
| `infrastructure.discovery` | 仪器发现包 |
| `infrastructure.discovery.instrument_discovery` | 网段扫描与 `*IDN?` 身份解析：序列号匹配设备表，未知设备同样上报 |
| `infrastructure.discovery.simulated_network` | 离线演示用的设备网络响应（仅模拟扫描模式，不会被当成现场结果） |
| `infrastructure.persistence` | 持久化适配器包 |
| `infrastructure.persistence.artifact_generator` | 自动归档产物（CSV 与测试报告）生成适配器：只读 manifest 已登记并校验过的正式结果，产物的受控路径固定在运行目录 `artifacts/` 下；`ArchiveArtifactGenerator` 把两类实现组合成归档服务持有的唯一生成端口 |
| `infrastructure.persistence.instrument_registry` | 设备表 JSON 仓储：序列号为唯一标识，IP 只是可漂移的地址别名，固定测试角色随设备登记 |
| `infrastructure.persistence.json_encoder` | JSON 编码器 |
| `infrastructure.persistence.json_result_repository` | 结果 JSON 实现细节 |
| `infrastructure.persistence.run_directory` | 运行目录文件布局 |
| `infrastructure.runtime.dependency_lock` | 锁定清单的严格解析、整份环境核对与漂移分类；公共入口是 `infrastructure.runtime` |
| `infrastructure.runtime.runtime_contract` | 运行时契约实现；公共入口是 `infrastructure.runtime` |
| `instrument.assembly_plan` | 硬件装配计划：把配置解析成一份**只读的计划对象**（信号源/频谱仪地址与能力参数、每台电源的地址/短标签/身份字段/声明通道、`gate`/`drain` 角色与 `supply_id/CHx` 路由 token、日志文本、急停顺序），不带原始配置段；只读配置不接触 VISA，地址重复/角色缺失或重复/通道不存在/引用歧义都在连接任何仪器之前失败 |
| `instrument.flow` | 安全流程包；application 只通过 `application.ports.measurement_session` 依赖清理端口 |
| `instrument.measurement_adapter` | 测量服务端口的显式适配器：设备方法显式定义，不按方法名转发，也不从注入对象推导路由 |
| `instrument.measurement_factory` | 硬件/离线测量端口装配：按装配计划打开 VISA 资源、构造驱动与会话（电源身份与初始输出检查在这里），装配失败时逆序释放已打开资源再抛原始异常；不解释任何配置键，配置解释在 `instrument.assembly_plan` |
| `instrument.power_routing` | 供电分配解析：把分配里的电源引用（`supply_id`、旧 `label`、残留 `visual` 槽位）解析成唯一的已启用电源键，并把被分配的通道解析成 `gate`/`drain` 角色 token、电压电流设置、日志写法（`短标签/CHx`）与急停顺序；纯计算：不看仪器地址、不接触 VISA |
| `instrument.safety` | 设备安全动作与资源所有权边界；关闭回调的安全标记按显式签名判定调用形式 |
| `presentation.qt.application_coordinator` | GUI 运行协调器：配置、运行契约与仪器端口所有者（经 `ConfigurationPagePort` 与配置页交互） |
| `presentation.qt.archival_page` | 测量页面共享的归档状态生命周期：等待归档、保存中、结果展示、终止事件与运行切换后的旧摘要清理；不读文件、不判断产物可用性 |
| `presentation.qt.archival_status` | 归档结果到页面文案与按钮状态的映射：无 Qt、不读文件、不判断路径存在，四类状态各有独立提示 |
| `presentation.qt.archival_view` | 归档结果分组控件：显示标题/说明并按提示启用“打开结果目录/测试报告”；位置由窗口注入；动态文案按纯文本渲染 |
| `presentation.qt.config_form_bindings` | 设备绑定的读取与规范化：`device_bindings` 到 `supply_id` 集合、步骤 5 过渡结构与只有地址的旧配置折叠、不可用记录的问题清单（记录里声明出的厂商/序列号都必须是 `device_key` 的一部分，任一片段对不上即不可用；纯数据：不读控件、不写配置、不碰 Qt） |
| `presentation.qt.config_form_state` | 配置表单状态：按控件最小读取协议读出配置片段、合并为配置、给出页面自己的校验说明（设备绑定的读取与规范化在 `config_form_bindings`；正式 schema 由 `configuration_authoring` 构造） |
| `presentation.qt.config_page` | 配置页面（编辑配置编写文档；向协调器提供 `ConfigurationPagePort` 窄接口） |
| `presentation.qt.config_page_widgets` | 配置页面控件构建辅助（固定操作栏布局 + 页面共用的数值框/下拉框工厂；控件仍登记在页面上） |
| `presentation.qt.config_power_section` | 配置页电源分区的控件构建：按电源键分页的通道参数标签页与电源分配设置表单，控件仍登记在页面上（页面负责信号连接、派生界面状态与配置读写；条目、可选项与分配引用以显式声明的类型传入，不从控件或文件里取状态） |
| `presentation.qt.connection_page` | 仪表配置页：欢迎页选定测试模式后的整屏页面（不登记进标签栏），大字号页标题、标题下居中的扫描网段与扫描方式、勾选并应用本次测试所需的设备（“查找设备”是结果区正中的落点，结果区不画外框）、结果区右下的“连接仪器”，应用后由“下一步”进入仪器配置页；设备绑定的写入与派生界面（电源标签页、电源分配可选项）仍归 config_page |
| `presentation.qt.device_cards` | 设备发现的仪器展示卡片：第一行居中放大的仪器名（型号，如 `DP832A`、`SMW200A`），第二行仪器图片（不加底板、不描边，等比缩放到同一视觉体积），最下面是加粗放大的设备码缩写（电源用注册表短标签 `DP8B1519`，其它仪器 `角色缩写/型号/序列号`）；圆角矩形，点击卡片即选中 |
| `presentation.qt.device_discovery_panel` | 仪器连接配置（设备发现）：扫描网段、结果区（不画外框：无边框、无底色，未查找时“查找设备”卡片按钮浮在正中，点下去后按钮收走、开始加载设备）、以卡片展示已登记/未分配角色/未登记/无法识别结果、点击卡片即选中并应用（角色取自设备表）、显示当前已绑定设备、清除设备绑定；扫描线程只读探测，不改配置也不连接仪器 |
| `presentation.qt.scan_card` | “查找设备”的卡片按钮：按钮本体就是自绘的 190×254 深色卡片（渐变卡面、四角角标、靛蓝柔光），正中的渐变标题写着“查找设备”，卡片内部有横向光带、扫描线与外飘粒子在动；悬停时卡面上的两行字在 0.6s 里淡到看不见，同一时间轴里卡片底下那张紫→蓝渐变底卡转出去（``rotate(-90deg) scaleX(1.34) scaleY(0.77)``）、霓虹光斑的模糊同时加厚；要露在卡片外面的三层（投影、光斑、底卡）画在同级、不进布局、不吃鼠标的透明覆盖层上，按钮因此与卡片一样大，点击行为仍由设备发现面板接手 |
| `presentation.qt.config_projection` | 配置编写文档（authoring document）到配置页控件的确定性投影：重置、逐字段投影、非法类型回落；正式 `test_plan` / `run_mapping` 只由 `configuration_authoring` 生成 |
| `presentation.qt.configuration_authoring` | 配置页保存时的正式运行配置编写 |
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

旧模块的唯一登记表是测试中的 `LEGACY_MODULES`，包含状态、处理步骤和源文件。本步骤只冻结
边界：状态为“待删除”或“待清理”的模块仍然存在，状态为“已删除”的模块必须保持不存在。
“待删除”指整文件退役，“待清理”指按步骤重写或收敛，不保证整文件删除。

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

旧模块禁止新增引用。下列存量引用在对应删除步骤前冻结，清单必须与代码严格一致；删除引用时
同步删除基线条目，新增引用不得靠扩大基线通过。基线统计跨模块绝对导入；包内相对再导出
（例如 `application/dto/__init__.py` 对旧适配器的再导出）随对应旧模块在步骤 6 一并删除。
步骤 6 退役根目录结果解析转发、旧结果 DTO 适配器、旧领域结果读取层与旧全局历史端口/仓储；
历史查看只读正式运行目录结果。步骤 9 把 `instrument.measurement_adapter` /
`instrument.measurement_factory` 收敛为内部模块，并退役 `instrument.legacy_control` 与
`instrument.power_roles`，基线因此为空。

| 旧模块 | 允许继续引用的文件 |
| --- | --- |
| （无） | 步骤 9 之后正式 production code 不再引用任何旧模块；`instrument.measurement_factory` 已登记为内部模块，`instrument.power_roles` 已退役 |

跨层依赖：`application` 对 `instrument.flow` 具体实现的依赖已在步骤 7 清零，
`application` 只导入 `application.ports.measurement_session` 的安全清理协议；
`SafetyCleanupAdapter` 位于设备安全层并实现该端口。

无法静态判定模块名的动态导入默认禁止。唯一例外是 `launcher.py` 的 `check_packages`：它只按
固定依赖表探测第三方包，不导入项目模块。白名单同样与代码严格一致，必须随代码同步调整，
默认只减不增。

## 7. 门禁与验证

`tests/test_public_api_inventory.py` 固化为以下断言：

1. 本文存在，且列出每个公共模块、内部模块、正式导入根；每个公开名称都出现在文档中。
2. 每个旧模块的表格行（模块名、源文件、状态、处理步骤）与登记表完全一致。
3. 公共模块存在，导出清单声明的名称；声明 `__all__` 时名称必须在其内。
4. 内部模块存在；公共模块与内部模块不得重叠；两类路径都位于登记的导入根下。
5. 旧模块登记表与工作树一致：待删除/待清理文件存在，已删除文件不存在。
6. 引用基线只引用已登记的旧模块和真实存在的文件。
7. 范围内代码不导入未登记的第一方顶层包。
8. 范围内代码不导入未登记的正式包内模块路径。
9. 范围内代码不使用 `from ... import *`。
10. 范围内代码的动态导入使用字符串常量；无法静态判定的调用必须命中白名单。
11. 旧模块引用与基线严格相等，新增引用立即失败。
12. `application` 不得导入 `instrument.flow` 具体实现，只能依赖安全清理端口。
13. 门禁辅助函数自身有回归测试（静态导入、`from package import module`、动态导入别名、
    非常量动态导入、星号导入）。

步骤 15 另有独立的静态质量门禁 `tests/test_static_quality_gate.py`（裸 `except`、静默宽泛
捕获、生产 `print`、旧模块导入、动态设备路由），与本文的导入登记门禁互补：本文冻结“允许导入
什么”，步骤 15 冻结“用什么写法处理失败、往哪里输出”。

定向验证命令：

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" -m unittest tests.test_public_api_inventory -v
```

完整验证执行全量 unittest、`launcher.py --check`、
`launcher.py --validate-config` 和 `tools/compile_check.py`。

## 8. 步骤 1 验收与现行清单规模

- 正式公共 API：56 个模块路径、244 个公开名称，已在本文与测试中固定（步骤 5 新增
  `infrastructure.config.validation.validate_official_config`，用于校验已解析的正式配置文档；
  步骤 6 收敛结果 DTO；步骤 7 新增 `application.ports.measurement_session`；步骤 13 新增
  `domain.measurement.values` 的测量值对象；步骤 15 新增 `application.errors` 的跨层异常分类
  与失败项归一化 `normalize_failures`；归档重构步骤 2/3 新增 `application.dto.archival`、
  `application.archival_service` 与 `application.ports.result_artifacts`，步骤 4 新增
  `application.archival_service.archive_completed_stage`；归档重构步骤 7 新增
  `application.dto.run_history`、`application.run_history_service` 与
  `application.ports.gui_data_service` 的 `RunArchiveStatusPort` / `RunHistoryServicePort`）。
- 正式内部模块：60 个路径已登记（以测试中的 `INTERNAL_MODULES` 为准），公共与内部边界不重叠
  （步骤 7 补登记 `instrument.safety`：安全标记的显式签名判定是跨模块契约；归档重构步骤 4
  补登记 `infrastructure.persistence.artifact_generator` 与 `analysis.reporting`：自动归档 CSV
  产物的适配器及其复用的分析层写入能力；步骤 5 在同一适配器模块内补上报告产物与组合端口，
  报告复用 `analysis.reporting.render_html_report`，不新增模块路径；归档重构步骤 7 未新增
  内部模块——历史管理页沿用已登记的 `presentation.qt.history_view_model`；归档重构步骤 10
  把页面模块与类名收口为 `presentation.qt.run_history_page` / `RunHistoryPage`，路由 key 改为
  `run_history`，用户可见标题仍为“历史运行”；技术债计划步骤 13 把配置页的控件构建拆成
  `presentation.qt.config_page_widgets`（固定操作栏与页面共用的数值框/下拉框工厂）与
  `presentation.qt.config_power_section`（电源分区的通道参数标签页与分配表单），页面只负责
  组合与交互协调；技术债计划步骤 14 把配置表单状态的绑定读取与规范化拆到
  `presentation.qt.config_form_bindings`（`device_bindings` 到 `supply_id` 集合、旧结构
  折叠与问题清单），`config_form_state` 只保留控件读数和配置合并；技术债计划步骤 15 把
  `instrument.measurement_factory` 的配置解析职责拆成两个纯模块：
  `instrument.assembly_plan`（把配置装订成**只读计划对象**：仪器地址与能力参数、每台电源的
  地址与声明通道、短标签与审计文本、地址唯一性）与 `instrument.power_routing`（供电分配的
  引用解析、`gate`/`drain` 角色 token、设置与急停顺序），两者都不接触 VISA；工厂只按计划
  打开资源、构造驱动与会话，并在装配失败时按原顺序释放已打开资源，且不再解释任何配置键）。
- 旧模块：29 个条目（21 个根模块、8 个正式包内实现），状态与工作树一致。步骤 16
  （2026-10-27）已把最后五个根目录纯再导出模块（`measurement_calculations` /
  `measurement_lifecycle` / `measurement_services` / `app_logging` / `project_paths`）
  整文件退役，登记表全部为“已删除”。
- 存量引用基线：空（步骤 4 与步骤 9 已把旧模块引用清零，新增引用直接失败）。
- 跨层依赖基线：`application` → `instrument.flow` 已由步骤 7 清零（仅保留安全清理端口）。
- 动态导入白名单：1 处（`launcher.py` 的 `check_packages` 依赖探测）。
- 本步骤未删除或改写任何旧实现文件。
- 全量 unittest、`launcher.py --check`、`launcher.py --validate-config` 和编译检查通过。

## 9. 后续步骤维护约定

- 退役旧模块：整文件删除的把 `LEGACY_MODULES` 的 `present` 改为 `False`（或删除条目），
  重写后保留的从登记表删除；同步删除本文对应行、引用基线条目和发布清单登记，不要只放宽
  断言。步骤 16 之后发布清单只登记正式发布边界，不再维护兼容/回滚文件列表。
- 删除存量引用：同步删除 `LEGACY_IMPORT_BASELINE` 的对应文件；基线必须与代码严格相等。
- 新增公共模块或公开名称：先登记到本文和 `FORMAL_API`，再写调用方。
- 新增内部模块并被跨模块导入：先登记到本文和 `INTERNAL_MODULES`。
- 正式模块改名、删除公开名称或改变公共/内部层级：视同不兼容变更，单独提交。
