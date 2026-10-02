# 第一期阶段 0：基线与 `enhanced_workers` 调用方清单

阶段：0，建立基线和调用方清单  
完成日期：2026-10-01  
对应计划：`docs/第一期重构计划_清理enhanced_workers职责.md`

## 1. 批次边界与回滚点

- 本阶段只新增本记录，不修改生产代码、测试代码或结果文件。
- 开始前工作区无未提交改动；当前基线提交：`1a9d819 docs(refactor): stage 1 plan to consolidate enhanced_workers responsibilities`。
- 本阶段回滚范围：删除本记录即可；不涉及生产代码回滚。
- `.env` 中的 `AUTO_TEST_PYTHON` 已验证存在且可执行：
  `C:\My_Document\Anaconda\envs\Auto_test\python.exe`
- 实际解释器版本：Python 3.11.15；Conda 环境：`Auto_test`。

## 2. 验证基线

以下命令均使用 `.env` 中的解释器执行：

| 命令 | 结果 |
| --- | --- |
| `./run_tests.ps1` | 通过，456 项通过 |
| `& $python launcher.py --check` | 通过，必需和可选依赖均可用 |
| `& $python launcher.py --validate-config` | 通过 |

本次未执行真实 Hardware smoke。阶段 0 未修改 SCPI 命令、设备时序、资源所有权或安全清理逻辑，因此不触发硬件验收。

## 3. 阶段 0 历史基线：`enhanced_workers.py` 公开名称和职责

本节及第 4、6 节记录阶段 0 建立清单时的历史事实，不代表阶段 6 之后的当前接口或职责所有者。`enhanced_workers.py` 已在阶段 6 删除，旧名称仅用于解释迁移来源。

本清单将名称分为两类：

- **兼容 API 名称**：当前代码或历史调用方实际依赖、后续迁移必须审计的类和入口。
- **模块命名空间名称**：由于模块级导入或赋值而可被外部访问，但不应视为稳定 API；阶段 6 处置文件前仍需确认没有外部依赖。

### 3.1 兼容 API 导出

| 名称 | 类型 | 当前职责 | 迁移目标 |
| --- | --- | --- | --- |
| `InstrumentWorker` | 类 | 从 `presentation.qt.workers.InstrumentWorker` 兼容导出，并保留可选 `config_path` 默认值转换 | 保留为兼容别名或迁移到明确的 Qt worker 导出层 |
| `EnhancedCableLossMeasurement` | 类 | 配置加载、运行快照、取消令牌、事件 callback 转换、线损两步流程、结果保存及 GUI 兼容属性 | `application.measurements.cable_loss.CableLossUseCase` + Qt worker + 结果仓储 |
| `EnhancedDriverPowerMapping` | 类 | 配置和线损结果读取、运行快照、驱动映射流程、callback 转换、结果保存 | `application.measurements.driver_mapping.DriverPowerMappingUseCase` + 输入读取器 + 结果仓储 |
| `EnhancedAmplifierMeasurement` | 类 | 配置和输入结果读取、最新驱动映射发现、主功放流程、callback 转换、结果保存及 GUI 兼容属性 | `application.measurements.amplifier_test.AmplifierMeasurementUseCase` + 输入读取器 + 结果仓储 |
| `WorkerSignals` | 类 | 当前文件中的重复 signal 声明；当前 worker 实现使用 `presentation.qt.workers.WorkerSignals` | 评估外部导入后保留 shim 或删除重复导出 |

### 3.2 模块命名空间名称

以下名称在模块中可访问，但当前没有登记为稳定兼容 API。它们来自模块级导入或变量赋值，删除或拆分文件前仍需完成一次外部依赖确认：

| 名称 | 来源或用途 | 当前判断 |
| --- | --- | --- |
| `json`、`time`、`np` | 标准库、时间和 NumPy 实现依赖 | 实现细节，不应作为外部 API |
| `Any` | 类型标注依赖 | 实现细节，不应作为外部 API |
| `QThread`、`Signal`、`QObject` | PySide6 类型和 signal 依赖 | Qt 兼容导出实现细节；Qt worker 应统一使用 `presentation.qt.workers` |
| `CancellationToken` | 取消令牌类型 | 被增强包装器内部创建，应用层迁移时应由用例依赖注入或组装 |
| `CheckpointEvent`、`MessageEvent`、`ProgressEvent`、`RealtimeDataEvent` | 应用事件类型 | 仅用于 `_CallbackEventSink` 的内部事件转换 |
| `RunContext`、`ConfigurationRepository` | 运行上下文和配置转换依赖 | 属于旧适配器内部依赖 |
| `CableLossService`、`DriverPowerMappingService`、`AmplifierMeasurementService` | 三类测量服务 | 由增强包装器内部编排，不应从本模块继续导出 |
| `measurement_calculations`、`calculate_cable_losses` | 计算模块及线损计算函数 | `calculate_cable_losses` 已被历史测试 patch，迁移时必须更新测试目标 |
| `get_logger`、`load_config_file`、`resolve_path` | 日志、配置加载和路径解析函数 | 兼容包装器内部依赖 |
| `CONFIG_FILE`、`CABLE_LOSS_FILE`、`TEST_RESULTS_DIR` | 默认配置和结果路径常量 | 兼容包装器内部依赖，阶段 2 应移入应用组装或持久化边界 |
| `load_json_result`、`new_run_id`、`save_measurement_result`、`write_legacy_run_snapshot` | 结果读取、运行 ID、结果保存和快照函数 | 持久化依赖，阶段 1 应由结果仓储隔离 |
| `logger` | 模块日志对象 | 模块内部实现细节 |

本节是基于当前模块级导入和赋值建立的命名空间盘点，不表示这些名称都需要长期兼容。阶段 6 处置前必须再次搜索仓库、发布脚本和外部兼容入口。

以下名称虽为内部名称，但已被测试或历史代码触达，迁移时必须单独审计：

| 名称 | 当前职责 |
| --- | --- |
| `_require_measurement_port` | 拒绝未显式注入 `measurement_port` 的生产构造路径 |
| `_NumpyJSONEncoder` | 将 `numpy.ndarray`、整数和浮点标量编码为 JSON |
| `_CallbackEventSink` | 将 `app.events` 转换为 progress/message/data/checkpoint callback |
| `_LegacyResultAdapter` | 加载旧配置、创建运行 ID、写入运行快照、调用结果归档和旧路径副本 |

## 4. 阶段 0 历史基线：三个增强包装器接口清单

### 4.1 `EnhancedCableLossMeasurement`

构造参数：`config_path=None`、`progress_callback=None`、`message_callback=None`、`data_callback=None`、`sleep_fn=None`、`run_id=None`、`run_directory=None`、`measurement_port=None`。

公开属性：`inst_ctrl`、`path1_losses`、`cable_losses`、`step_pause_callback`。继承或初始化后还持有 `config`、`run_id`、`context`、`run_directory`。

公开方法：

- `measure_path_loss(frequency)`：执行一次路径损耗测量。
- `stop_measurement()`：请求 emergency stop；在线损等待第二步时触发清理。
- `measure_all_frequencies()`：执行第一步并进入等待状态。
- `continue_to_step2()`：执行第二步、更新 `cable_losses` 并保存结果。
- `set_step_pause_callback(callback)`：设置第二步暂停回调。

历史内部入口：`_pause(message)`、`_measure_step2()`。事件 callback 映射为：`ProgressEvent -> progress_callback(int(fraction * 100))`、`MessageEvent -> message_callback(message)`、`RealtimeDataEvent -> data_callback(dict(data))`、`CheckpointEvent -> step_pause_callback(prompt)`。

### 4.2 `EnhancedDriverPowerMapping`

构造参数：`config_path=None`、`loss_data_path=None`、`progress_callback=None`、`message_callback=None`、`data_callback=None`、`sleep_fn=None`、`run_id=None`、`run_directory=None`、`measurement_port=None`。

公开属性：`inst_ctrl`、`power_mapping`。另持有 `config`、`run_id`、`context`、`run_directory`、`sleep_fn`。

公开方法：

- `stop_measurement()`：请求 emergency stop。
- `measure_all_frequencies()`：执行映射测量、更新 `power_mapping` 并保存结果。

输入行为：默认读取 `CABLE_LOSS_FILE`；结果路径默认为 `TEST_RESULTS_DIR / driver_power_mapping_YYYYMMDD_HHMMSS.json`。

### 4.3 `EnhancedAmplifierMeasurement`

构造参数：`config_path=None`、`loss_data_path=None`、`driver_mapping_path=None`、`progress_callback=None`、`message_callback=None`、`data_callback=None`、`sleep_fn=None`、`run_id=None`、`run_directory=None`、`measurement_port=None`。

公开属性：`inst_ctrl`、`loss_data`、`driver_mapping`、`measurement_results`。另持有 `config`、`run_id`、`context`、`run_directory`、`sleep_fn`。

公开方法：

- `stop_measurement()`：请求 emergency stop。
- `calculate_actual_power(frequency, measured_power)`：计算补偿后的实际功率。
- `perform_power_sweep(frequency)`：执行单频功率扫描。
- `measure_all_frequencies()`：执行主功放测量、更新 `measurement_results` 并保存结果。

输入行为：驱动模式开启且未显式给出 `driver_mapping_path` 时扫描 `TEST_RESULTS_DIR / driver_power_mapping_*.json`，按修改时间选择最新文件；缺少文件时抛出 `FileNotFoundError`。

## 5. 调用方分类

### 5.1 生产调用方（阶段 5.2 更新）

| 调用方 | 位置 | 关系 |
| --- | --- | --- |
| 应用组装层 | `app/gui_runtime.py` | 组装三个应用用例，注入 `prepared_run`、结果仓储、输入读取器和 measurement port；不导入 `enhanced_workers` |
| Qt 线损 worker | `presentation/qt/workers.py` | 通过 `app.gui_runtime.create_cable_loss_measurement()` 构造，并调用线损两步方法 |
| Qt 驱动映射 worker | `presentation/qt/workers.py` | 通过 runtime 工厂构造并调用 `measure_all_frequencies()` |
| Qt 主功放 worker | `presentation/qt/workers.py` | 通过 runtime 工厂构造并调用 `measure_all_frequencies()` |

正式 GUI 的当前链路为：`enhanced_main_gui.py -> presentation/qt/workers.py -> app/gui_runtime.py -> application/measurements/* -> measurement_services.py -> measurement_port`。阶段 6 后，`enhanced_workers.py` 已删除，不再保留测试或历史兼容入口。

### 5.2 阶段 6 前的测试调用方、patch 目标和历史兼容调用方

| 文件 | 类型 | 具体依赖 |
| --- | --- | --- |
| `tests/test_gui_runtime.py` | 测试 patch | `app.gui_runtime.*UseCase`；覆盖三类用例组装、外部注入端口、构造失败时端口清理 |
| `tests/test_gui_workers.py` | 兼容导入测试 | `from enhanced_workers import InstrumentWorker as LegacyInstrumentWorker`，验证旧 worker 导入仍可用 |
| `tests/test_measurement_calculations.py` | 历史对象测试 | 直接实例化 `EnhancedAmplifierMeasurement`、`EnhancedCableLossMeasurement`；patch `enhanced_workers.calculate_cable_losses` |
| `tests/test_measurement_factory.py` | 工厂契约测试 | 直接导入三个增强类，验证缺少 `measurement_port` 时拒绝构造 |
| `tests/test_measurement_loop_regression.py` | 回归测试 | 直接导入 `EnhancedAmplifierMeasurement`，patch `enhanced_workers.time.sleep`，并与历史 `AmplifierMeasurement` 比较动作和结果 |
| `tests/test_offline_refactor_completion.py` | 回滚清单测试 | 断言 `release_manifest` 的 `rollback_files` 包含 `enhanced_workers.py` |
| `release_manifest.py` | 历史回滚边界 | 将 `enhanced_workers.py` 列入 `ROLLBACK_FILES`，不属于生产运行时导入 |
| `measurement_calculations.py` | 文档引用 | docstring 提及 `enhanced_workers.py`，不是运行时调用 |

阶段 0 未发现仓库内对 `_LegacyResultAdapter` 的外部直接导入或 patch；该职责随后已由结果仓储和应用用例接管，阶段 6 删除了旧适配器及其专用测试。

## 6. 阶段 0 历史基线：结果、快照和兼容路径

| 内容 | 当前位置或模式 | 当前所有者 |
| --- | --- | --- |
| 运行目录 | `TEST_RESULTS_DIR / run_id` | `result_storage.write_run_snapshot()` / `_LegacyResultAdapter` 初始化 |
| 测试方案快照 | `run_directory/test_plan_snapshot.json` | `result_storage.write_run_snapshot()` |
| 运行映射快照 | `run_directory/run_mapping_snapshot.json` | `result_storage.write_run_snapshot()` |
| 运行元数据 | `run_directory/run_metadata.json` | `result_storage.write_run_snapshot()` |
| 旧配置转换审查 | `run_directory/conversion_review.json` | `result_storage.write_legacy_run_snapshot()` |
| 线损结果 | 归档 `run_directory/cable_loss_results.json`；兼容副本 `TEST_RESULTS_DIR/cable_loss_results.json` | `EnhancedCableLossMeasurement._save()` -> `save_measurement_result()` |
| 驱动映射结果 | 归档和兼容副本均使用 `driver_power_mapping_YYYYMMDD_HHMMSS.json` 文件名 | `EnhancedDriverPowerMapping._save()` -> `save_measurement_result()` |
| 主功放结果 | 归档和兼容副本均使用 `amplifier_measurement_YYYYMMDD_HHMMSS.json` 文件名 | `EnhancedAmplifierMeasurement._save()` -> `save_measurement_result()` |
| 版本化模型 | 各运行目录下 `cable_loss_model.json`、`driver_power_mapping_model.json`、`amplifier_measurement_model.json` | `result_storage.save_measurement_result()` |
| 输入读取 | `load_json_result(CABLE_LOSS_FILE)`、显式驱动映射路径，或最新 `driver_power_mapping_*.json` | 三个增强包装器构造函数 |

`save_measurement_result()` 的现有行为是先写运行目录归档，再写旧路径兼容副本，并按结果类型写入版本化模型；阶段 0 基线要求后续阶段保持这一格式和失败语义。

### 6.1 阶段 6 后的当前职责归属

| 能力 | 当前所有者 |
| --- | --- |
| 测量流程 | `application/measurements/cable_loss.py`、`driver_mapping.py`、`amplifier_test.py` |
| Qt 线程、停止和 signal 转换 | `presentation/qt/workers.py` |
| 结果保存接口 | `application.ports.result_repository.MeasurementResultRepository` |
| 文件结果仓储实现 | `infrastructure.persistence.result_repository.FileMeasurementResultRepository` |
| 结果输入读取和最新文件发现 | `application.inputs.ResultInputReader` |
| 旧结果格式、兼容副本和版本化模型底层实现 | `result_storage.py` |

驱动映射和主功放的旧路径文件名使用秒级时间戳；同一秒内重复运行可能生成相同文件名，归档路径已存在时由 `save_measurement_result()` 抛出 `FileExistsError`，不会覆盖已有归档。该行为属于当前基线，不是本阶段引入的问题。阶段 1 的结果仓储测试必须明确覆盖：文件名策略、归档碰撞、旧路径副本不被错误覆盖，以及后续是否保持或有计划地改变该行为。

## 7. 阶段 0 结论与后续迁移目标

- 可以区分生产调用方、测试调用方、历史兼容导入和文档/回滚引用。
- 三个增强类的迁移目标分别为 `CableLossUseCase`、`DriverPowerMappingUseCase`、`AmplifierMeasurementUseCase`；结果保存统一迁移到明确的结果仓储接口。
- `InstrumentWorker` 的现代实现已经位于 `presentation/qt/workers.py`；`enhanced_workers.InstrumentWorker` 仅是兼容导出候选。
- 阶段 1 应先抽取结果仓储边界，覆盖运行归档、旧路径副本、`numpy` 编码、版本化模型和显式 `run_directory` 行为。
- 阶段 2 应把配置加载、运行上下文和驱动映射文件发现移入 `app.gui_runtime` 或明确的输入读取器。
- 生产调用方清理前不得删除 `enhanced_workers.py`；测试 patch 目标应在每次生产切换批次同步更新。

## 8. 阶段 4 收敛记录

- 阶段 0 基线链路为：`enhanced_main_gui.py -> presentation/qt/workers.py -> app.gui_runtime.py -> enhanced_workers.py -> measurement_services.py -> measurement_port`。
- 阶段 4 完成后，正式 GUI 默认链路已收敛为：`enhanced_main_gui.py -> presentation/qt/workers.py -> app.gui_runtime.py -> application/measurements/* -> measurement_services.py -> measurement_port`。Qt worker 负责线程生命周期、取消控制、应用用例调用编排以及应用事件到 Qt signal 的转换，不承载测量算法和结果持久化逻辑；`enhanced_workers.py` 不再是生产默认链路的一部分。
- `presentation/qt/workers.py` 不导入 `enhanced_workers`、`result_storage`，也不访问 `TEST_RESULTS_DIR` 或 `CABLE_LOSS_FILE`。
- 三个测量 worker 支持显式注入 `prepare_factory` 和 `measurement_factory`；默认工厂只在 worker 运行时延迟解析应用组装入口。
- 应用用例构造失败统一通过 `error` signal 报告；测量取消继续通过 `stopped` signal 报告，线损第二步通过既有 `step_pause` signal 暂停并等待确认。
- 阶段 4 验证：重点 worker/runtime 测试及完整测试套件通过。
- 阶段 5.2 审计：`app`、`application`、`infrastructure` 和 `presentation` 生产包通过 AST 依赖检查，均不导入 `enhanced_workers`；应用层不直接导入 VISA 或 Qt。三类 runtime 工厂的外部注入与工厂创建端口所有权回归保留在 `tests/test_gui_runtime.py`。

## 9. 阶段 6 处置结果（2026-10-02）

- 再次搜索仓库、测试和发布脚本，未发现生产调用方或已登记的仓库外兼容调用方。
- 已删除 `enhanced_workers.py`，不保留 shim。三个增强测量类、Qt worker 兼容导出和 `_LegacyResultAdapter` 均不再是受支持入口。
- 已删除旧适配器持久化测试，更新历史增强类测试和回滚清单测试；现代应用用例、Qt worker 和结果仓储测试继续覆盖对应行为。
- 结果保存仍通过 `MeasurementResultRepository` 和 `FileMeasurementResultRepository`，旧结果格式及兼容副本由现有持久化实现继续维护。
- 本阶段只做离线结构和测试清理，未改变 SCPI、设备时序、清理顺序或生产配置，因此不执行真实 Hardware smoke。
- 第一期开启第二期条件满足：生产调用方已脱离该文件，应用用例和 Qt worker 边界稳定，结果保存经过明确仓储接口，端口所有权与安全清理回归保留。
