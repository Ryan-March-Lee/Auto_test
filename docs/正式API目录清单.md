# 正式 API 目录清单

本文是 [技术债分阶段重构计划](技术债分阶段重构计划.md) **步骤 1：建立正式 API 目录清单** 的产出。

本步骤只冻结边界，不删除旧文件：旧模块按步骤 3 至 6、9、16 分批退役。标为“已删除”的模块
必须保持不存在；标为“待删除”“待清理”的模块在对应步骤执行前仍存在。

清单分为三个层次，避免把“可导入”误当成“公共承诺”：

1. **正式公共 API**：对调用方承诺稳定的模块路径和公开名称。
2. **正式内部模块**：允许正式代码依赖，但不承诺公共稳定性，可随实现调整。
3. **旧模块**：不再视为公共 API。禁止新增引用；存量引用按文件和模块精确冻结为基线。

清单的可执行形式是 [`tests/test_public_api_inventory.py`](../tests/test_public_api_inventory.py)。
本文与测试中的 `FORMAL_API`、`INTERNAL_MODULES`、`FORMAL_IMPORT_ROOTS`、`LEGACY_MODULES`、
`LEGACY_IMPORT_BASELINE`、`DYNAMIC_IMPORT_ALLOWLIST` 一一对应；修改本文必须同步修改测试，
否则门禁失败。

## 1. 设计前提

本计划采用不考虑向后兼容的技术决策（见重构计划第 1 节）：

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
| `assistant` | 可选助手能力 | 仅表现层可用，不得进入正式测量链路 |

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
| `app.run_context` | `PreparedRun`、`create_prepared_run`、`load_prepared_run` | 运行契约对象与创建/重载入口；`prepare_run` 属旧兼容路径，不在正式清单内 |
| `app.gui_runtime` | `prepare_configuration`、`connect_instruments`、`create_offline_measurement_port`、`create_cable_loss_measurement`、`create_driver_mapping_measurement`、`create_amplifier_measurement` | GUI 侧装配入口；页面不得直接调用测量组装函数 |
| `app.events` | `EventSink`、`MeasurementEvent`、`ProgressEvent`、`MessageEvent`、`RealtimeDataEvent`、`CheckpointEvent`、`CompletedEvent`、`StoppedEvent`、`FailedEvent` | 测量事件与事件接收端口 |
| `app.cancellation` | `CancellationMode`、`MeasurementCancelled`、`CancellationToken` | 取消语义 |

### 3.2 应用用例

| 模块 | 正式公开名称 | 说明 |
| --- | --- | --- |
| `application.configuration_service` | `load_for_run` | 正式配置加载入口 |
| `application.run_service` | `RunSnapshot`、`create_run_snapshot`、`load_run_configuration`、`inspect_run`、`load_hardware_config`、`find_run_hardware_config`、`ensure_run_resumable` | 运行创建、快照校验与可恢复性判断；正式测量必须绑定 `run_id` |
| `application.run_stages` | `RunStageRecorder`、`StageInputUnavailable` | 阶段输入/结果登记 |
| `application.measurements` | `CableLossUseCase`、`DriverPowerMappingUseCase`、`AmplifierMeasurementUseCase`、`AmplifierCalibrationError`、`validate_amplifier_calibration_inputs` | 三类正式测量用例入口 |
| `application.measurements.services` | `CableLossService`、`DriverPowerMappingService`、`AmplifierMeasurementService` | 测量服务（依赖注入端口，不导入具体仪器 flow） |
| `application.gui_data_service` | `GuiDataService` | 只读历史/导出应用服务 |
| `application.lifecycle.measurement_lifecycle` | `MeasurementLifecycle`、`StopIntent` | 测量生命周期 |
| `application.lifecycle.measurement_runtime` | `MeasurementRuntimeCoordinator`、`RuntimeDecision` | 运行协调与决策 |

### 3.3 应用端口

| 模块 | 正式公开名称 |
| --- | --- |
| `application.ports.config_repository` | `ConfigurationLoadResult`、`ConfigurationRepository`、`TestPlanRepository`、`RunMappingRepository` |
| `application.ports.result_repository` | `MeasurementResultRepository`、`SavedMeasurementResult` |
| `application.ports.result_history` | `ResultHistoryRepository`、`HistoryResultEntry` |
| `application.ports.run_history` | `RunHistoryRepository`、`RunHistoryEntry`、`RunHistoryState`、`RunStageResultEntry` |
| `application.ports.run_repository` | `RunRepositoryPort`、`RunLayout` |
| `application.ports.result_input_reader` | `ResultInputReader` |
| `application.ports.gui_data_service` | `GuiDataService`、`ResultFileDto`、`LoadedResultDto`、`ConfigurationPort`、`ResultQueryPort`、`ResultExportPort`、`ReportPort` |

正式配置入口为 `domain.configuration` 和 `infrastructure.config`；正式结果仓储入口为
`application.ports.result_repository` 及其基础设施实现 `infrastructure.persistence.result_repository`；
正式测量入口为 `application.measurements` / `application.run_service`。

### 3.4 基础设施适配器

| 模块 | 正式公开名称 |
| --- | --- |
| `infrastructure.config.json_io` | `load_json_object`、`load_config_file` |
| `infrastructure.config.validation` | `load_config`、`validate_config_file` |
| `infrastructure.config.json_config_repository` | `JsonConfigurationRepository`、`JsonTestPlanRepository`、`JsonRunMappingRepository`、`load_json`、`load_test_plan`、`load_run_mapping`、`load_run_configuration` |
| `infrastructure.persistence.result_repository` | `FileMeasurementResultRepository` |
| `infrastructure.persistence.run_repository` | `RunRepository` |
| `infrastructure.persistence.run_history_repository` | `FileRunHistoryRepository` |
| `infrastructure.persistence.result_history_repository` | `FileResultHistoryRepository` |
| `infrastructure.gui_data_service` | `JsonConfigurationAdapter`、`ResultQueryAdapter`、`ResultExportAdapter`、`ReportAdapter` |
| `infrastructure.logging.app_logging` | `get_logger`、`setup_logging` |
| `infrastructure.runtime` | `current_runtime`、`runtime_id`、`configured_python` |

### 3.5 领域层

| 模块 | 正式公开名称 |
| --- | --- |
| `domain.configuration.models` | `TestPlan`、`InstrumentMapping`、`ChannelMapping`、`PowerChannelPlan`、`RunResourceMapping`、`RunConfiguration`、`validate_test_plan`、`validate_run_mapping`、`validate_run_configuration` |
| `domain.configuration.rules` | `validate_config` |
| `domain.configuration.types` | `ConfigIssue`、`ConfigValidationResult` |
| `domain.run.models` | `FileReference`、`RunManifest`、`RunStatus`、`StageRecord`、`StageStatus`、`default_manifest`、`generate_run_id`、`validate_run_id`、`utc_now_iso` |
| `domain.measurement.calculations` | `calculate_cable_losses`、`compensate_amplifier_output_power`、`compensate_driver_output_power`、`interpolate_driver_output_power`、`calculate_dut_input_power`、`calculate_gain`、`calculate_efficiency`、`calculate_compression_result` |
| `domain.result_reading` | `normalize_frequency_key`、`parse_result_model`、`get_sweep_results`、`get_saturation_points` |

### 3.6 仪器层

| 模块 | 正式公开名称 |
| --- | --- |
| `instrument.ports` | `InstrumentSession`、`InstrumentState`、`PowerSupplyPort`、`SignalGeneratorPort`、`SpectrumAnalyzerPort`、`MeasurementInstrumentPort` |
| `instrument.session` | `ManagedInstrumentSession`、`InstrumentSessionError` |
| `instrument.flow.safety_shutdown_flow` | `SafetyShutdownFlow`、`SafetyShutdownError` |
| `instrument.flow.cleanup` | `cleanup_measurement` |

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
| `application.dto` | `MeasurementRequest`、`CableLossMeasurementRequest`、`DriverPowerMappingRequest`、`AmplifierMeasurementRequest`、`MeasurementResult`、`MeasurementStatus`、`MeasurementType` | DTO 汇总再导出路径；其中的旧兼容导出在步骤 6 删除 |
| `application.dto.measurement_requests` | `MeasurementRequest`、`CableLossMeasurementRequest`、`DriverPowerMappingRequest`、`AmplifierMeasurementRequest` | 测量请求 |
| `application.dto.measurement_results` | `MeasurementResult`、`MeasurementStatus`、`MeasurementType` | 正式测量结果 |

领域层结果模型位于 `domain.models`、`domain.result_reading`、`domain.run.models`；正式展示与导出
只能消费显式 DTO/领域模型字段，不得依赖 `Mapping[str, Any]` 或旧字段别名。

## 5. 正式内部模块

这些模块允许正式代码导入，但**不是**公共 API：不承诺名称与签名稳定，不得被外部脚本依赖。
新增内部模块并在其他模块中导入时，必须先在测试的 `INTERNAL_MODULES` 登记。

| 模块 | 说明 |
| --- | --- |
| `analysis.visualization` | 分析层只读可视化与报告数据构建 |
| `app` | 装配包（`__init__` 为空）；具体入口见 `app.*` 公共 API |
| `application.inputs` | 结果输入读取包 |
| `application.inputs.result_reading` | 报告模型构建（内部实现） |
| `application.lifecycle` | 测量生命周期包 |
| `assistant.llm` | 可选助手 LLM 客户端 |
| `assistant.storage` | 可选助手历史存储 |
| `domain.models` | 旧领域模型模块；跨层新代码应使用正式 DTO |
| `infrastructure.filesystem.atomic_json` | 原子 JSON 写入 |
| `infrastructure.filesystem.paths` | 路径服务 |
| `infrastructure.persistence` | 持久化适配器包 |
| `infrastructure.persistence.json_encoder` | JSON 编码器 |
| `infrastructure.persistence.json_result_repository` | 结果 JSON 实现细节 |
| `infrastructure.persistence.run_directory` | 运行目录文件布局 |
| `infrastructure.runtime.runtime_contract` | 运行时契约实现；公共入口是 `infrastructure.runtime` |
| `instrument.flow` | 安全流程包；`application` 的引用属步骤 7 待移除的存量依赖 |
| `presentation.qt.assistant.chat_dialogs` | 助手对话对话框 |
| `presentation.qt.assistant.chat_panel` | 助手聊天面板 |
| `presentation.qt.assistant.chat_worker` | 助手后台 worker |
| `presentation.qt.config_form_state` | 配置表单状态 |
| `presentation.qt.config_page` | 配置页面 |
| `presentation.qt.connection_diagrams` | 连接图渲染 |
| `presentation.qt.connection_dialog` | 连接确认对话框 |
| `presentation.qt.export_page` | 导出页面 |
| `presentation.qt.input_controls` | 通用输入控件 |
| `presentation.qt.measurement_state` | 测量状态对象 |
| `presentation.qt.measurement_worker_factories` | 测量 worker 工厂 |
| `presentation.qt.realtime_buffer` | 实时数据缓冲 |
| `presentation.qt.realtime_plot` | 实时绘图控件 |
| `presentation.qt.status_panel` | 状态/日志面板 |
| `presentation.qt.visualization_page` | 历史可视化页面 |
| `presentation.qt.workers` | 测量 worker 实现 |

## 6. 旧模块清单（非公共 API）

旧模块的唯一登记表是测试中的 `LEGACY_MODULES`，包含状态、处理步骤和源文件。本步骤只冻结
边界：状态为“待删除”或“待清理”的模块仍然存在，状态为“已删除”的模块必须保持不存在。
“待删除”指整文件退役，“待清理”指按步骤重写或收敛，不保证整文件删除。

### 6.1 根目录旧模块

| 旧模块 | 源文件 | 状态 | 处理步骤 |
| --- | --- | --- | --- |
| `cable_loss_measurement` | `cable_loss_measurement.py` | 待删除 | 步骤 4 |
| `driver_power_mapping` | `driver_power_mapping.py` | 待删除 | 步骤 4 |
| `amplifier_measurement` | `amplifier_measurement.py` | 待删除 | 步骤 4 |
| `config_io` | `config_io.py` | 待删除 | 步骤 5 |
| `config_models` | `config_models.py` | 待删除 | 步骤 5 |
| `config_validation` | `config_validation.py` | 待删除 | 步骤 5 |
| `result_reading` | `result_reading.py` | 待删除 | 步骤 6 |
| `result_storage` | `result_storage.py` | 已删除 | 步骤 3 |
| `measurement_calculations` | `measurement_calculations.py` | 待删除 | 步骤 16 |
| `measurement_lifecycle` | `measurement_lifecycle.py` | 待删除 | 步骤 16 |
| `measurement_services` | `measurement_services.py` | 待删除 | 步骤 16 |
| `app_logging` | `app_logging.py` | 待删除 | 步骤 16 |
| `project_paths` | `project_paths.py` | 待删除 | 步骤 16 |
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
| `app.legacy_amplifier_measurement` | `app/legacy_amplifier_measurement.py` | 待删除 | 步骤 4 |
| `infrastructure.config.legacy_conversion` | `infrastructure/config/legacy_conversion.py` | 待删除 | 步骤 5 |
| `application.dto.legacy_result_adapter` | `application/dto/legacy_result_adapter.py` | 待删除 | 步骤 6 |
| `instrument.legacy_control` | `instrument/legacy_control.py` | 待清理 | 步骤 9 |
| `instrument.measurement_adapter` | `instrument/measurement_adapter.py` | 待清理 | 步骤 9 |
| `instrument.measurement_factory` | `instrument/measurement_factory.py` | 待清理 | 步骤 9 |
| `instrument.power_roles` | `instrument/power_roles.py` | 待清理 | 步骤 9 |

### 6.3 存量引用基线

旧模块禁止新增引用。下列存量引用在对应删除步骤前冻结，清单必须与代码严格一致；删除引用时
同步删除基线条目，新增引用不得靠扩大基线通过。基线统计跨模块绝对导入；包内相对再导出
（例如 `application/dto/__init__.py` 对旧适配器的再导出）随对应旧模块在步骤 6 一并删除。

| 旧模块 | 允许继续引用的文件 |
| --- | --- |
| `application.dto.legacy_result_adapter` | `presentation/qt/amplifier_page.py`、`presentation/qt/cable_loss_page.py`、`presentation/qt/driver_mapping_page.py` |
| `infrastructure.config.legacy_conversion` | `infrastructure/config/json_config_repository.py` |
| `instrument.measurement_factory` | `app/gui_runtime.py` |
| `instrument.power_roles` | `instrument/legacy_control.py` |

跨层依赖同样冻结：`application` 不得新增对 `instrument.flow` 具体实现的依赖，现存
`application/measurements/services.py` 一处由步骤 7 清除。

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
12. `application` 对 `instrument.flow` 的跨层依赖与冻结基线严格相等。
13. 门禁辅助函数自身有回归测试（静态导入、`from package import module`、动态导入别名、
    非常量动态导入、星号导入）。

定向验证命令：

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" -m unittest tests.test_public_api_inventory -v
```

完整验证按重构计划 4.2 节执行全量 unittest、`launcher.py --check`、
`launcher.py --validate-config` 和 `tools/compile_check.py`。

## 8. 步骤 1 验收

- 正式公共 API：48 个模块路径、160 个公开名称，已在本文与测试中固定。
- 正式内部模块：32 个路径已登记，公共与内部边界不重叠。
- 旧模块：28 个条目（21 个根模块、7 个正式包内实现），状态与工作树一致。
- 存量引用基线：4 个旧模块、6 处文件引用，只减不增。
- 跨层依赖基线：`application` → `instrument.flow` 仅 1 处，步骤 7 清除。
- 动态导入白名单：1 处（`launcher.py` 的 `check_packages` 依赖探测）。
- 本步骤未删除或改写任何旧实现文件。
- 全量 unittest、`launcher.py --check`、`launcher.py --validate-config` 和编译检查通过。

## 9. 后续步骤维护约定

- 退役旧模块：整文件删除的把 `LEGACY_MODULES` 的 `present` 改为 `False`（或删除条目），
  重写后保留的从登记表删除；同步删除本文对应行、引用基线条目和 `release_manifest.py`
  登记，不要只放宽断言。
- 删除存量引用：同步删除 `LEGACY_IMPORT_BASELINE` 的对应文件；基线必须与代码严格相等。
- 新增公共模块或公开名称：先登记到本文和 `FORMAL_API`，再写调用方。
- 新增内部模块并被跨模块导入：先登记到本文和 `INTERNAL_MODULES`。
- 正式模块改名、删除公开名称或改变公共/内部层级：视同不兼容变更，按重构计划的阶段顺序
  单独提交。
