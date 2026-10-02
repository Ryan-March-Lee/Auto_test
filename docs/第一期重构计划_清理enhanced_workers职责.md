# 第一期重构计划：清理 `enhanced_workers.py` 的兼容与持久化职责

## 1. 文档目的

本文是 PA 自动测试系统后续重构的第一期执行指南。

第一期只处理 `enhanced_workers.py` 的职责收敛，目标是为后续 GUI 页面拆分和应用层整理建立稳定边界。本文不要求本期完成 `MainWindow` 拆分，也不要求本期重写 `instrument` 四层或三类测量算法。

本期完成后，生产测量链路应满足：

```text
Qt 页面 / Qt Worker
        ↓
Application Measurement Use Case
        ↓
Measurement Port
        ↓
Instrument Adapter / Session / Driver / Transport
```

结果保存应满足：

```text
Measurement Use Case
        ↓
Result Repository / Result Writer
        ↓
result_storage 或其后续持久化实现
```

`enhanced_workers.py` 不再位于上述两条业务链路的中间。

---

## 2. 当前基线与问题

### 2.1 当前调用关系

当前正式 GUI 的主要调用关系如下：

```text
enhanced_main_gui.py
        ↓
presentation/qt/workers.py
        ↓
app/gui_runtime.py
        ↓
enhanced_workers.py
        ├── EnhancedCableLossMeasurement
        ├── EnhancedDriverPowerMapping
        └── EnhancedAmplifierMeasurement
                ↓
        measurement_services.py
                ↓
        measurement_port
```

`app/gui_runtime.py` 当前直接导入三个 `Enhanced*Measurement` 类，并在构造失败时负责关闭本次创建的端口。

### 2.2 `enhanced_workers.py` 当前混合的职责

当前文件同时承担以下职责：

1. Qt worker 的兼容导出：`InstrumentWorker`。
2. 三个测量服务的历史包装：
   - `EnhancedCableLossMeasurement`
   - `EnhancedDriverPowerMapping`
   - `EnhancedAmplifierMeasurement`
3. 历史配置加载和路径默认值处理。
4. `measurement_port` 缺失时的生产路径保护。
5. `CancellationToken` 创建和停止请求转换。
6. 业务事件回调到 GUI callback 的转换。
7. 结果保存和运行快照创建。
8. 旧结果 JSON 的读取、最新驱动映射文件发现。
9. `numpy` 结果到 JSON 的编码兼容。
10. 旧 GUI/API 对象属性兼容，例如 `inst_ctrl`、`path1_losses`、`measurement_results`。

这使得 `enhanced_workers.py` 既不是纯 worker 模块，也不是纯应用服务模块，更不是纯持久化模块。

### 2.3 当前必须保留的行为

第一期不得改变以下行为，除非对应批次明确记录并通过测试：

- 所有生产测量都必须显式注入 `measurement_port`。
- 硬件组装仍通过 `app.gui_runtime.connect_instruments()` 和 `instrument.measurement_factory`。
- `SafetyInstrumentSession` 仍是默认硬件生命周期和安全清理边界。
- 测量完成、异常、取消和紧急停止都必须执行现有安全清理。
- 运行前配置校验和快照仍在硬件创建前完成。
- 结果仍需写入运行归档目录。
- 旧 GUI 读取的兼容结果路径暂时不能无计划删除。
- `result_storage.py` 中已有的运行快照、结果归档、旧路径副本和版本化模型能力不能被本期重构破坏。

---

## 3. 第一期开工前的边界决定

### 3.1 第一期开工范围

本期包含：

- 把三个 `Enhanced*Measurement` 包装器的测量编排职责迁移到应用层测量用例。
- 把结果保存和结果读取依赖从测量包装器中移出。
- 把 Qt callback 转换和线程生命周期保留在 `presentation/qt/workers.py`。
- 让 `app/gui_runtime.py` 直接组装应用测量用例，而不是依赖 `enhanced_workers.py`。
- 为运行快照、结果归档、旧路径兼容副本建立明确的持久化接口。
- 更新相关单元、仿真和 GUI 组装测试。
- 在最后一个批次审计并处置 `enhanced_workers.py` 的生产调用方。

### 3.2 第一期开工范围之外

本期明确不做：

- 不拆 `enhanced_main_gui.py` 的页面。
- 不重写 `measurement_services.py` 的三类测量算法。
- 不改变 SCPI 命令、仪器时序、电源拓扑或安全清理顺序。
- 不把 `instrument/transport`、`drivers`、`action`、`flow` 再次大规模改名。
- 不删除 `InstrumentControl`。
- 不删除历史测量模块，除非最后的调用方审计证明其已经没有本期相关调用方。
- 不把 `result_storage.py` 整体移动或拆成多个文件作为本期硬性要求。
- 不同时进行 GUI 页面拆分、配置 schema 重构和结果模型重构。
- 不执行未经授权的真实 RF 测量作为重构验证。

### 3.3 第一期开工前检查

每个批次开始前必须确认：

1. 工作区中的用户改动已被识别，不覆盖无关改动。
2. `.env` 中的 `AUTO_TEST_PYTHON` 存在且指向可执行文件。
3. 当前基线测试可以运行，或已记录已知失败和原因。
4. `git status`、当前提交和回滚文件已记录到批次记录中。
5. 本批次只修改预先列出的文件。

项目命令必须使用 `.env` 配置的解释器，不使用 `base` 环境或裸 `python`。

建议检查命令：

```powershell
$python = (Get-Content .env | Where-Object { $_ -match '^AUTO_TEST_PYTHON=' }).Split('=', 2)[1]
Test-Path -LiteralPath $python
& $python --version
```

项目统一验证命令：

```powershell
./run_tests.ps1
& $python launcher.py --check
& $python launcher.py --validate-config
```

---

## 4. 目标目录和职责边界

第一期不要求一次性达到最终目录，但新增代码应遵循下列目标边界：

```text
application/
├── measurements/
│   ├── cable_loss.py
│   ├── driver_mapping.py
│   └── amplifier_test.py
├── ports/
│   └── result_repository.py
└── dto/
    └── measurement_requests.py

infrastructure/
└── persistence/
    └── result_repository.py

presentation/qt/
└── workers.py
```

如果当前项目暂时不适合新增完整的 `application/` 或 `infrastructure/` 包，可以先使用兼容过渡目录，但不得把新的业务代码继续添加到 `enhanced_workers.py`。

### 4.1 Qt Worker 的职责

`presentation/qt/workers.py` 只能负责：

- 创建和运行线程。
- 调用一个已经组装好的应用测量用例。
- 把应用事件转换为 Qt signal。
- 转发普通停止和紧急停止请求。
- 等待线损第二步的用户继续信号。
- 在 worker 交接失败时释放自己拥有的端口。

Worker 不应负责：

- 解析业务配置字典。
- 读取结果文件。
- 选择最新驱动映射文件。
- 生成结果文件名。
- 构造运行快照。
- 编码 `numpy` 结果。
- 实现功率扫描、线损计算或压缩点判断。

### 4.2 应用测量用例的职责

应用测量用例负责：

- 接收已校验的运行配置或明确的 request。
- 调用 `measurement_port` 完成测量。
- 发布 `app.events` 中的领域无关测量事件。
- 响应 `CancellationToken`。
- 返回结构化的测量结果。
- 通过抽象的结果仓储保存结果。
- 在完成、异常和取消路径调用既有安全清理边界。

它不应依赖：

- PySide6。
- `QThread`、Qt Signal。
- `enhanced_main_gui.py`。
- `presentation.qt`。
- 具体文件路径常量。
- 具体 JSON 编码器。

### 4.3 持久化接口的职责

第一期建议定义最小的结果仓储协议，例如：

```python
class MeasurementResultRepository(Protocol):
    def save(
        self,
        result: Mapping[str, Any],
        *,
        result_type: str,
        run_id: str,
        run_directory: Path | None = None,
    ) -> SavedMeasurementResult:
        ...

    def load(self, path: PathLike) -> Mapping[str, Any]:
        ...
```

协议只描述应用需要的能力，不暴露 `json.dump`、`TEST_RESULTS_DIR` 或旧路径复制细节。

现阶段可以由一个适配器调用 `result_storage.save_measurement_result()` 和 `result_storage.load_json_result()`。第一期的目标是隔离调用方向，不是立即重写 `result_storage.py`。

### 4.4 兼容层的职责

兼容层只能保留以下类型的内容：

- 已明确登记的外部历史导入路径。
- 已明确登记的旧参数名称转换。
- 一次性的旧结果字段转换。
- 迁移期间需要保留的导出别名。

兼容层不能继续拥有：

- 测量流程。
- 结果保存策略。
- 硬件组装。
- Qt worker 生命周期。
- 文件发现逻辑。
- 业务状态机。

---

## 5. 分阶段、分步骤执行计划

每个批次都必须独立可验证。除非另有说明，完成一个批次后才能开始下一个批次。

### 阶段 0：建立基线和调用方清单

#### 目标

冻结第一期开始前的行为、调用点和回滚边界。

#### 步骤

1. 记录 `enhanced_workers.py` 中所有公开名称和内部职责。
2. 搜索仓库中所有 `enhanced_workers`、`Enhanced*Measurement`、`_LegacyResultAdapter` 的调用点。
3. 记录 `app.gui_runtime.py`、`presentation/qt/workers.py` 和测试中的 patch 目标。
4. 记录三个增强包装器的输入参数、公开属性、公开方法和回调行为。
5. 记录三类结果的归档路径、旧路径副本路径、运行目录文件和版本化模型文件。
6. 运行一次完整离线测试并记录实际数量。
7. 运行应用检查和配置校验。
8. 生成或更新本期专用调用方清单，不把测试 patch 误判为生产调用方。

#### 通过标准

- 可以明确区分生产调用方、测试调用方和历史兼容调用方。
- 每个 `Enhanced*Measurement` 都有明确的迁移目标。
- 所有结果文件的兼容行为已有测试或已有书面说明。
- 基线测试结果和解释器路径已记录。

#### 预期修改

- 优先只修改本文或新增本期状态记录。
- 不在此阶段修改生产代码。

---

### 阶段 1：抽取持久化边界

#### 目标

让测量用例不再直接依赖 `result_storage` 的函数、项目路径常量和 JSON 编码器。

#### 步骤

本阶段同时涉及结果仓储契约、兼容存储实现和三个增强包装器，拆分为三个可独立验证的小步骤。

##### 步骤 1.1：定义仓储契约并建立适配器骨架

1. 定义结果保存返回值，至少包含：
   - 归档路径。
   - 旧路径兼容副本路径。
   - 运行目录。
   - `run_id`。
2. 定义最小结果仓储协议或应用层接口。
3. 创建一个基础文件结果仓储适配器，内部调用现有 `result_storage`。
4. 将旧路径名称和结果类型映射集中到结果仓储或应用组装层。

##### 步骤 1.2：迁移编码与保存实现

1. 将 `_NumpyJSONEncoder` 从 `enhanced_workers.py` 移到持久化实现或通用 JSON 编码模块。
2. 将 `_LegacyResultAdapter._save()` 的保存逻辑迁移到结果仓储适配器。
3. 为仓储适配器增加测试，覆盖：
   - 归档结果写入。
   - 旧路径副本写入。
   - 运行 ID 透传。
   - `numpy` 标量和数组编码。
   - 写入失败时的异常传播。
   - 外部传入的 `run_directory` 不被错误覆盖。
##### 步骤 1.3：接入兼容包装器并完成行为回归

1. 让 `enhanced_workers.py` 暂时通过注入的仓储对象保存结果，以保持行为不变。
2. 补充并运行归档碰撞、旧路径副本、版本化模型和显式 `run_directory` 的回归测试。

#### 重要约束

- 不改变 `result_storage.py` 的兼容文件格式。
- 不改变 `save_measurement_result()` 的原子写入和失败语义。
- 不把旧路径副本误删为“清理兼容代码”。
- 不在本阶段修改 GUI 的结果读取逻辑。

#### 通过标准

- 测量包装器中不再出现 `save_measurement_result`、`write_legacy_run_snapshot` 的直接调用。
- 测量包装器不再导入 `TEST_RESULTS_DIR`、`CABLE_LOSS_FILE` 等结果路径常量。
- 结果存储现有测试全部通过。
- 正常、失败和模拟结果写入行为与基线一致。

---

### 阶段 2：抽取运行上下文和输入装配

#### 目标

去除三个增强包装器中重复的配置加载、运行 ID、运行目录和输入文件发现逻辑。

#### 步骤

本阶段同时处理配置转换、运行快照、结果输入读取和端口所有权，拆分为三个可独立验证的小步骤。

##### 步骤 2.1：建立运行上下文和请求对象

1. 明确应用测量用例的输入对象，例如：
   - 已加载的配置。
   - `PreparedRun`。
   - `measurement_port`。
   - 线损结果输入。
   - 驱动映射输入。
   - 事件 sink。
   - 取消令牌。
   - 结果仓储。
2. 在 `app.gui_runtime` 中完成配置预检、`PreparedRun` 创建和运行 ID/运行目录注入，暂时保留原有输入文件读取路径。

##### 步骤 2.2：抽取结果输入读取器

1. 把“自动选择最新驱动映射文件”的行为移到明确的输入解析器或结果读取服务。
2. 把默认文件路径转换集中到应用组装层，不放入测量用例。
3. 为线损结果、显式驱动映射文件和最新驱动映射发现增加读取测试。

##### 步骤 2.3：完成组装注入和端口所有权回归

1. 在 `app.gui_runtime` 中完成所有默认组装：
   - 配置预检。
   - `PreparedRun` 创建。
   - 测量端口创建或接收。
   - 结果仓储注入。
   - 输入结果加载器注入。
2. 测试外部显式提供驱动映射文件时不会扫描目录。
3. 测试缺少驱动映射文件时仍产生原有错误类型和信息。
4. 测试测量用例不会自行创建硬件端口。
5. 测试组装失败时只关闭本次创建的端口，不关闭外部注入端口。

#### 通过标准

- 三个测量用例不再调用 `load_config_file()`。
- 三个测量用例不再读取 `project_paths` 中的结果文件常量。
- 应用组装层拥有输入依赖，测量用例拥有业务流程。
- 缺少 `measurement_port` 仍然立即拒绝构造或执行。
- 组装失败时只关闭本次创建的端口，不关闭外部注入端口。

---

### 阶段 3：迁移三个增强测量包装器

#### 目标

把 `EnhancedCableLossMeasurement`、`EnhancedDriverPowerMapping` 和 `EnhancedAmplifierMeasurement` 的测量职责迁移到应用层用例。

#### 推荐目标

```text
application/measurements/cable_loss.py
    CableLossUseCase

application/measurements/driver_mapping.py
    DriverPowerMappingUseCase

application/measurements/amplifier_test.py
    AmplifierMeasurementUseCase
```

#### 步骤

本阶段一次迁移三个不同生命周期和输入依赖的测量流程，且还要同步处理状态兼容和应用组装，改动量最大。按测量类型拆成三个可独立回归的小步骤；每个步骤都必须保持事件、取消和安全清理行为不变。

##### 步骤 3.1：迁移线损用例

1. 以现有 `CableLossService` 的行为为基线，建立 `CableLossUseCase` 外部接口。
2. 保持线损两步暂停、继续、停止和清理行为不变。
3. 把 `path1_losses`、`cable_losses` 等 GUI 所需状态改为用例结果或明确转换结果。
4. 为正常、异常、取消和等待第二步增加独立测试。

##### 步骤 3.2：迁移驱动映射用例

1. 以现有 `DriverPowerMappingService` 的行为为基线，建立 `DriverPowerMappingUseCase`。
2. 保持结果事件字段、取消行为、清理行为和结果仓储调用不变。
3. 将 `power_mapping` 改为明确的用例结果，不继续隐藏在兼容类内部。
4. 为显式输入、缺失输入、正常完成和取消增加独立测试。

##### 步骤 3.3：迁移主功放用例并完成三类用例组装

1. 以现有 `AmplifierMeasurementService` 的行为为基线，建立 `AmplifierMeasurementUseCase`。
2. 保持驱动映射输入、功率扫描、事件类型、取消行为和安全清理行为不变。
3. 将 `measurement_results` 改为明确的用例结果或结果转换器。
4. 统一三个用例的事件 sink、取消令牌和结果 DTO 约定。
5. 更新 `app.gui_runtime.py`，使三个 `create_*_measurement()` 函数能够返回新的应用对象或明确的应用 facade；保留旧增强类作为临时兼容入口。
6. 让 `presentation/qt/workers.py` 在对应测量流程完成回归后切换到新的应用对象。

#### 兼容处理原则

- 不为了兼容旧属性而把旧包装器继续变成业务对象。
- GUI 当前若依赖某个属性，先在 worker 或 view-model 中建立明确的转换，不把属性继续添加到新用例。
- 旧构造函数参数若必须保留，只保留在适配器中，不进入核心用例接口。
- 兼容类应标记为临时，注明移除条件和当前调用方。

#### 通过标准

- 正式 GUI 默认调用路径不再导入 `enhanced_workers`。
- 三个应用用例不依赖 PySide6。
- 三个应用用例不直接保存 JSON。
- 正常完成、异常、取消、线损两步暂停和重新连接测试全部通过。
- 仿真测量的仪器动作顺序和安全清理顺序与基线一致。

---

### 阶段 4：收敛 Qt Worker

#### 目标

使 `presentation/qt/workers.py` 成为唯一的 Qt worker 实现，使 worker 只承担线程、取消和信号转换。

#### 步骤

1. 保留公共 `BaseWorker` 和 `WorkerSignals`。
2. 保留 `InstrumentWorker` 的端口连接、交接和失败清理逻辑。
3. 让 `CableLossWorker`、`DriverMappingWorker`、`AmplifierWorker` 接收应用用例工厂或已组装依赖。
4. 删除 worker 中针对历史包装器的业务属性访问。
5. 删除 worker 中对结果文件路径、文件名和 JSON 结构的判断。
6. 统一处理应用事件到 Qt signal 的转换。
7. 统一处理以下状态：
   - 完成。
   - 普通停止。
   - 紧急停止。
   - 应用用例构造失败。
   - worker 交接失败。
   - 线损等待第二步确认。
8. 保留现有 GUI 信号名称，减少本期对 `enhanced_main_gui.py` 的影响。

#### 通过标准

- `presentation/qt/workers.py` 不导入 `enhanced_workers`。
- Qt worker 不直接导入 `result_storage`。
- Qt worker 不访问 `TEST_RESULTS_DIR`、`CABLE_LOSS_FILE`。
- worker 测试覆盖停止、失败、交接和线损第二步。
- worker 完成后不会持有已清理的端口。

---

### 阶段 5：切换应用组装层并清理生产依赖

#### 目标

让 `app/gui_runtime.py` 成为测量用例和端口的正式组装入口。

#### 步骤

本阶段的生产切换和所有权测试会同时影响三个工厂入口，但不再承担测量流程迁移。拆分为两个可独立验证的小步骤。

##### 步骤 5.1：切换应用组装入口

1. 将三个 `create_*_measurement()` 函数切换到新的应用用例。
2. 保留端口所有权规则：
   - 外部注入端口由调用方拥有。
   - 工厂创建的端口在构造失败时由工厂清理。
   - 成功交接后由明确的运行控制者清理。
3. 删除 `app.gui_runtime` 对 `Enhanced*Measurement` 的导入。

##### 步骤 5.2：完成生产依赖审计和所有权回归

1. 更新 GUI runtime 测试的 patch 目标和断言。
2. 增加架构测试，禁止生产应用模块重新导入 `enhanced_workers`。
3. 更新 `presentation/qt/workers.py` 的模块说明和依赖注释。
4. 更新调用方清单，记录 `enhanced_workers.py` 已不再是生产默认链路。
5. 回归外部注入端口、工厂创建端口、构造失败和成功交接后的清理行为。

#### 通过标准

- `app/gui_runtime.py` 不导入 `enhanced_workers`。
- 默认硬件组装仍集中在 `instrument.measurement_factory`。
- 应用层不导入 VISA、SCPI 或 Qt。
- 所有权、失败清理和重复测量测试通过。

---

### 阶段 6：兼容文件处置和第一期验收

#### 目标

决定 `enhanced_workers.py` 是删除、保留为纯兼容 shim，还是暂时保留在历史目录，并完成第一期归档。

#### 步骤

1. 再次搜索仓库和发布脚本中的 `enhanced_workers` 调用。
2. 区分：
   - 生产调用方。
   - 测试调用方。
   - 历史回滚清单。
   - 文档引用。
3. 如果仍有外部兼容需求，将文件改为纯 shim：
   - 只导出明确的兼容别名。
   - 不包含测量流程。
   - 不包含结果保存。
   - 不包含 Qt worker 实现。
   - 在模块文档中写明移除条件。
4. 如果没有兼容需求，则删除文件，并同步更新：
   - `release_manifest.py`。
   - 相关测试。
   - 调用方清单。
   - 阶段记录。
5. 运行全量离线测试、应用检查和配置校验。
6. 不因为本期纯离线结构变化自动执行真实 RF；若修改了硬件命令、时序、资源清理或生产配置，才安排受控 Hardware smoke。
7. 记录第一期实际修改文件、测试数量、遗留兼容项和第二期入口。

#### 第一期开启第二期的条件

- `enhanced_workers.py` 已经不承载测量和持久化职责。
- 生产调用方不再依赖该文件。
- 三个应用测量用例边界稳定。
- Qt worker 只承担线程和 signal 转换。
- 结果保存通过明确的仓储或持久化接口完成。
- 所有权和安全清理测试通过。
- 遗留兼容入口已有删除条件。

---

## 6. 测试与验收矩阵

### 6.1 单元测试

必须覆盖：

- 结果仓储调用 `result_storage` 的正确参数。
- 结果类型和运行 ID 透传。
- `numpy` 数值编码。
- 归档文件和旧路径副本同时生成。
- 写入失败时不产生错误的成功状态。
- 测量用例接收注入的 `measurement_port`。
- 测量用例不会自行创建端口。
- 取消令牌触发后不继续打开 RF。
- 业务事件字段与当前 GUI 约定一致。
- 新应用用例不导入 PySide6、VISA 或具体 GUI 模块。

### 6.2 GUI Worker 测试

必须覆盖：

- 连接成功并将端口交接给 GUI。
- 交接前停止会关闭端口。
- 交接失败会关闭端口并清空引用。
- 线损第一步完成后等待继续信号。
- 线损等待期间停止能够释放 worker。
- 应用用例构造失败不会启动测量。
- 应用事件正确映射为 Qt signal。

### 6.3 模拟集成测试

必须覆盖：

- 三类测量正常流程。
- 设备动作顺序没有变化。
- RF 关闭、电源关闭、连接关闭顺序没有变化。
- 结果归档与兼容副本内容一致或保持现有约定。
- 测量异常和取消后可以重新创建新端口并重新测量。

### 6.4 应用级检查

每个生产切换批次至少执行：

```powershell
./run_tests.ps1
& $python launcher.py --check
& $python launcher.py --validate-config
```

如果项目脚本执行器不在当前 PowerShell 变量中，应先从 `.env` 读取并验证路径，不得改用其他 Python 环境。

### 6.5 硬件验收

第一期本身不要求真实硬件验收，因为目标是职责和依赖重构。

以下变化才需要安排受控 Hardware smoke：

- 修改 SCPI 命令。
- 修改仪器连接或 VISA 资源所有权。
- 修改 RF、电源或安全清理时序。
- 修改生产电源拓扑。
- 修改硬件配置解析或默认地址。

如果只是把已有结果保存调用移动到仓储适配器，且模拟测试证明硬件控制链路未变，则在记录中说明不触发 Hardware smoke 的理由。

---

## 7. 回滚策略

第一期必须按批次回滚，不允许跨批次整体回退。

### 阶段 0 回滚

只回滚文档或基线记录，不涉及生产代码。

### 阶段 1 回滚

恢复原有 `enhanced_workers.py` 的保存调用，同时保留新增仓储测试。不得删除 `result_storage.py` 中原有能力。

### 阶段 2 回滚

恢复包装器中的配置加载和输入路径处理，但保留已通过测试的持久化接口。回滚时必须确认不会造成结果重复写入。

### 阶段 3 回滚

将 `app.gui_runtime` 重新指向原增强包装器，恢复前必须保留新应用用例的独立测试。不得恢复旧的隐式端口创建路径。

### 阶段 4 回滚

恢复原 worker 的调用方式，但不恢复已经从 worker 移出的 JSON 保存逻辑，除非明确记录短期回滚原因。

### 阶段 5 回滚

按应用组装层切换回滚，不恢复生产 legacy 工厂，不恢复 `InstrumentControl` 的隐式 fallback。

### 阶段 6 回滚

如果删除或改名 `enhanced_workers.py` 后发现外部兼容调用方，优先恢复纯兼容 shim，而不是恢复其测量和持久化职责。

---

## 8. 代码审查清单

每个批次合并前检查：

- [ ] 是否只修改了本批次范围内的文件？
- [ ] 是否改变了 SCPI 命令、设备时序或清理顺序？
- [ ] 是否新增了对 `enhanced_workers.py` 的生产依赖？
- [ ] 应用层是否导入了 PySide6、VISA 或 GUI 模块？
- [ ] worker 是否开始读取 JSON 或结果目录？
- [ ] 结果保存是否仍然经过明确的持久化边界？
- [ ] 外部注入端口是否被错误关闭？
- [ ] 构造失败时本批次创建的端口是否一定清理？
- [ ] 完成、异常、取消和紧急停止是否都覆盖？
- [ ] 旧结果文件是否仍然可被现有读取方使用？
- [ ] 是否更新了相关测试和调用方清单？
- [ ] 是否使用了 `.env` 中的 `AUTO_TEST_PYTHON` 验证？
- [ ] 是否记录了未执行 Hardware smoke 的理由？

---

## 9. 第一阶段结束后的项目状态

第一期完成后的合理状态如下：

```text
enhanced_main_gui.py
        ↓
presentation/qt/workers.py
        ↓
app.gui_runtime / application.measurements
        ↓
application result repository port
        ↓
infrastructure result repository
        ↓
result_storage compatibility implementation
```

允许 `result_storage.py` 仍然是现阶段的底层实现，也允许历史结果格式继续存在。关键是调用方向已经改变：测量用例和 Qt worker 不再直接依赖文件格式和兼容保存细节。

第一期结束后，下一期的自然入口是：

1. 从 `enhanced_main_gui.py` 拆出 `CableLossPage`、`DriverMappingPage` 和 `AmplifierPage`。
2. 建立统一的 Qt `MeasurementController`。
3. 将 GUI 页面状态从主窗口迁移到页面或 view-model。

下一期不应重新把持久化和历史兼容职责加回 `enhanced_workers.py`。

---

## 10. 当前状态

阶段 6 已于 2026-10-02 执行完成；本文以下历史基线描述保留为计划编写时记录，最终处置以本文末尾的阶段 6 记录为准。

截至本文编写时，已知事实如下：

- `instrument` 四层和 `SafetyInstrumentSession` 已作为默认硬件边界使用。
- `app.gui_runtime` 当前仍直接导入三个 `Enhanced*Measurement`。
- `enhanced_workers.py` 当前仍同时承载测量包装、结果保存和历史兼容。
- `presentation/qt/workers.py` 已承担现代 Qt worker，但其测量对象仍通过 `app.gui_runtime` 间接进入 `enhanced_workers.py`。
- `result_storage.py` 仍是现有运行快照和结果兼容存储实现，不应在第一期整体删除。
- 现有文档记录过 Unit、Simulation 和全量离线测试通过；真实三类完整测量验收仍然是独立门槛。

第一期每完成一个阶段，应在本文末尾或对应阶段记录中追加：完成日期、实际改动文件、验证命令、测试数量、未解决问题和回滚点。

### 阶段 6 实际记录（2026-10-02）

- 处置决定：删除 `enhanced_workers.py`，不保留兼容 shim。仓库内没有生产调用方或已登记的仓库外兼容需求；剩余引用均为内部测试、历史回滚清单或文档引用。
- 实际改动：删除 `enhanced_workers.py` 及其专用持久化测试；移除旧增强类、旧 worker 兼容导入测试；更新测量循环回归、工厂测试、发布清单、README 和调用方文档。
- 现代职责归属：测量流程由 `application/measurements/*` 承担，Qt 生命周期和 signal 转换由 `presentation/qt/workers.py` 承担，结果保存由 `application.ports.result_repository` 和 `infrastructure.persistence.result_repository` 承担。
- 兼容保留：`result_storage.py` 的旧结果格式、运行归档、旧路径副本和版本化模型能力继续保留；删除的是职责混合的旧入口，不是结果文件兼容能力。
- 回滚点：如发现外部兼容导入，只恢复纯兼容 shim；不得恢复旧测量流程、结果保存或 Qt worker 实现。
- 验收命令：`./run_tests.ps1`、使用 `.env` 中 `AUTO_TEST_PYTHON` 的 `launcher.py --check` 和 `launcher.py --validate-config`。
- 硬件验收：未执行真实 Hardware smoke。本批未修改 SCPI 命令、设备时序、资源所有权、安全清理顺序或生产配置。
