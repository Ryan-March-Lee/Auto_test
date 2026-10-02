# 第二期重构计划：GUI 页面拆分与 `MeasurementController`

## 1. 文档目的

本文是 PA 自动测试系统第二期重构的执行指南。第二期建立在第一期已经完成的应用测量用例、结果仓储、Qt worker 和应用组装边界之上，目标是降低 `enhanced_main_gui.py` 的职责密度，建立统一的 Qt 测量控制入口。

第二期的目标状态是：

```text
enhanced_main_gui.py
    ├── MainWindow：窗口布局、导航、全局服务和页面注册
    ├── ConfigurationPage
    ├── CableLossPage
    ├── DriverMappingPage
    ├── AmplifierPage
    ├── VisualizationPage / ExportPage（本期只保留稳定接入边界）
    └── MeasurementController
             ↓
      presentation.qt.workers
             ↓
      app.gui_runtime / application.measurements
```

本期继续保持第一期的方向：页面和 controller 不实现测量算法、不直接保存 JSON、不导入 VISA 或 SCPI，不恢复 `enhanced_workers.py`。

真实 Hardware smoke 暂不作为本期离线开发的前置条件，但仍是涉及真实设备控制链路变更后的独立验收门槛。

---

## 2. 当前基线和问题

### 2.1 当前实现事实

- `enhanced_main_gui.py` 中的 `MainWindow` 仍然包含配置页面、测量页面、状态面板、结果展示、实时数据、可视化、导出和聊天面板的创建与状态管理。
- `presentation/qt/pages.py` 当前只有 `PageDefinition` 和 builder 注册表，页面 builder 仍调用 `MainWindow.create_*_tab()`，没有真正的页面对象边界。
- `presentation/qt/workers.py` 已提供 `InstrumentWorker`、`CableLossWorker`、`DriverMappingWorker` 和 `AmplifierWorker`，并拥有稳定的 `WorkerSignals`。
- `app.gui_runtime` 已负责配置预检、运行快照、测量用例组装、结果仓储和 measurement port 所有权。
- 当前窗口仍通过 `current_worker`、`instrument_ctrl`、多个实时数据字段和各测量专用字段直接协调生命周期。
- 现有 GUI 测试主要覆盖模块导入、配置读取、worker 生命周期和 runtime 组装，尚未形成页面对象和 controller 的独立测试层。

### 2.2 第二期要解决的问题

1. `MainWindow` 直接拥有三类测量控件和测量状态，页面边界不清晰。
2. 三类测量的启动、停止、错误、完成和状态清理逻辑分散在窗口方法中。
3. `current_worker` 只能表达单一隐式测量状态，无法明确描述当前测量类型、生命周期和所有权。
4. 页面必须通过明确的 view-model 或状态对象接收结果，不能继续依赖窗口的隐式属性。
5. 页面拆分后需要保持现有用户工作流、Qt signal 名称、配置兼容和结果显示行为。

---

## 3. 范围和非范围

### 3.1 本期范围

- 把线损、驱动映射、主功放三个测量 tab 拆为独立 Qt 页面对象。
- 定义页面使用的请求、状态和结果转换边界。
- 建立统一的 `MeasurementController`，管理 worker 创建、启动、停止、紧急停止、线损继续和生命周期状态。
- 将测量相关状态从 `MainWindow` 迁移到页面或明确的 view-model。
- 保持 `presentation/qt/workers.py` 为唯一 worker 实现。
- 为页面、controller、窗口接入和失败清理补充离线测试及架构测试。
- 在每个批次记录实际修改文件、验证命令、测试数量、未解决问题和回滚点。

### 3.2 本期不做

- 不修改 SCPI 命令、仪器动作顺序、RF 或电源时序。
- 不修改 `measurement_services.py` 中三类测量算法。
- 不修改 `app.gui_runtime` 的硬件组装规则和 measurement port 所有权规则，除非该改动是 controller 接入所必需且有独立回归测试。
- 不重构 `result_storage.py`、结果模型或配置 schema。
- 不把配置页面、数据可视化、数据导出和聊天助手强行拆成完整新架构；本期只为它们保留明确的窗口集成边界。
- 不删除 `enhanced_main_gui.py`，不要求本期完成所有主窗口职责清零。
- 不执行未经授权的真实 RF 测量。
- 不以模拟测试替代真实 Hardware smoke 结论。

---

## 4. 目标职责边界

### 4.1 `MainWindow`

只负责：

- 创建主窗口、中央布局、导航和全局状态栏。
- 注册和切换页面。
- 创建 `MeasurementController` 及其共享依赖。
- 提供配置服务、日志服务、结果查看服务等全局依赖。
- 处理应用级关闭事件和全局窗口状态。

不负责：

- 直接创建三类 measurement worker。
- 直接解析测量结果字段。
- 直接响应某一测量的 progress、data、step pause 和 error 细节。
- 直接决定测量端口的关闭时机。

### 4.2 测量页面

每个页面负责：

- 创建本页面控件和布局。
- 读取本页面所需的用户输入，形成明确的页面请求。
- 调用 controller 的测量命令。
- 订阅 controller 的状态和结果事件。
- 将结果转换为表格、图表或页面状态。
- 在页面销毁或切换时释放页面级 signal 连接。

页面不负责：

- 直接导入 `app.gui_runtime` 的硬件组装函数。
- 直接实例化 `QThread` 或具体 worker。
- 直接访问 `result_storage`、`TEST_RESULTS_DIR` 或兼容文件路径。
- 实现测量算法、取消令牌或端口清理。

### 4.3 `MeasurementController`

controller 负责：

- 统一创建或接收 `CableLossWorker`、`DriverMappingWorker`、`AmplifierWorker`。
- 保存当前测量类型、worker 状态和页面关联信息。
- 统一转发 progress、message、data、result、finished、stopped、error 和 step pause 事件。
- 拒绝并发启动不允许的测量。
- 转发普通停止、紧急停止和线损第二步继续请求。
- 在 worker 完成、失败或取消后清理引用，确保可以重新启动。
- 处理 controller 自己拥有的 worker 引用，不拥有外部注入的 measurement port。

controller 不负责：

- 业务配置解析。
- 结果文件发现和 JSON 读取。
- 结果保存策略。
- 硬件动作和安全清理实现。

### 4.4 View-model 和状态对象

建议使用普通 Python `dataclass` 或不可变值对象表达：

- `MeasurementKind`：`cable_loss`、`driver_mapping`、`amplifier`。
- `MeasurementStatus`：`idle`、`preparing`、`running`、`waiting_for_continue`、`stopping`、`finished`、`failed`、`stopped`。
- `MeasurementViewState`：当前类型、状态、进度、消息、错误和结果引用。
- 三类页面结果状态：只保存页面显示所需的结构化数据，不复制持久化格式。

状态对象不得依赖 PySide6、VISA、具体文件路径或 `enhanced_main_gui.py`。

---

## 5. 分阶段、分步骤执行计划

每个阶段都必须独立可验证。除非阶段记录明确说明原因，不开始下一阶段。每批次开始前记录工作区状态，不覆盖用户未提交改动。

### 阶段 0：冻结 GUI 基线和拆分清单

#### 目标

记录当前窗口行为、控件归属、测量入口、信号连接和回滚边界。

#### 步骤

1. 记录 `MainWindow` 的 tab 顺序、每个 tab 的 builder、公共控件和页面专用控件。
2. 搜索并登记三类测量的启动、停止、紧急停止、继续、错误、完成和结果显示方法。
3. 登记窗口字段中与测量有关的状态，包括 `current_worker`、`instrument_ctrl`、实时数据、结果表格和进度字段。
4. 登记 `presentation.qt.workers.WorkerSignals` 的所有连接方和现有测试 patch 目标。
5. 记录关闭窗口、切换页面和重复启动时的当前行为。
6. 执行 `.env` 中 `AUTO_TEST_PYTHON` 指定解释器的全量离线测试、应用检查和配置校验。
7. 新增或更新第二期专用 GUI 基线记录，不修改生产代码。

#### 通过标准

- 每个计划迁移的窗口方法都有明确目标页面或 controller。
- 每个页面的输入、输出和生命周期行为有记录。
- 可以区分生产 GUI 依赖、测试依赖和历史兼容依赖。
- 基线测试数量、解释器路径和工作区状态已记录。

---

### 阶段 1：定义页面、状态和 controller 契约

#### 目标

先固定对象边界，避免页面拆分过程中继续复制窗口隐式状态。

#### 步骤 1.1：定义页面接口

1. 在 `presentation/qt/pages.py` 或新的 `presentation/qt/pages/` 包中定义页面基类或最小协议。
2. 约定页面生命周期方法，例如 `build_ui()`、`bind_controller()`、`on_activated()`、`on_deactivated()`。
3. 约定页面不得直接创建 worker，统一使用 controller。
4. 约定页面关闭时解除 controller signal 连接，避免页面销毁后收到事件。

#### 步骤 1.2：定义状态对象

1. 新增纯 Python 的测量状态和结果引用对象。
2. 为状态转换定义合法路径，至少覆盖准备、运行、等待第二步、停止、异常、取消和完成。
3. 明确错误文本、停止原因和结果对象的来源。
4. 为线损第二步定义唯一的 `waiting_for_continue` 状态，不允许页面通过线程细节判断是否暂停。

#### 步骤 1.3：定义 controller 契约

1. 定义启动入口：`start_cable_loss()`、`start_driver_mapping()`、`start_amplifier()`。
2. 定义控制入口：`stop()`、`emergency_stop()`、`continue_cable_loss()`。
3. 定义只读状态访问和事件信号，保持现有 GUI 需要的信号语义。
4. 定义并发规则：已有测量处于非终态时，新的启动请求必须被拒绝并给出明确原因。
5. 定义 worker 工厂注入点，使 controller 测试不依赖真实 Qt worker 和硬件。

#### 通过标准

- 契约测试可以使用 fake worker 完成，不导入 VISA、SCPI 或真实配置文件。
- 状态对象和 controller 契约不依赖 `enhanced_main_gui.py`。
- 线损暂停/继续和停止语义被明确表达。

---

### 阶段 2：实现 `MeasurementController` 并接入现有 worker

#### 目标

统一三类测量生命周期，同时不改变 worker 内部硬件调用和应用用例调用行为。

#### 步骤 2.1：实现 controller 核心生命周期

1. 新增 controller 模块，优先放在 `presentation/qt/measurement_controller.py`。
2. 注入 worker factory、配置路径提供者和可选日志 sink。
3. 实现单一活动测量约束、worker 引用保存和终态清理。
4. 将 worker signal 统一转换为 controller signal 或状态更新。
5. 明确 controller 不关闭外部注入的 measurement port；端口仍由既有运行控制者负责。

#### 步骤 2.2：接入停止和异常路径

1. 普通停止调用 worker 的普通停止入口。
2. 紧急停止必须保留现有 emergency stop 语义，不允许降级为普通停止。
3. worker 构造失败、线程启动失败、运行异常和取消分别形成可测试状态。
4. 完成、异常和停止后清空 worker 引用，允许下一次测量重新启动。
5. 线损等待第二步期间只能通过 controller 继续或停止，不直接访问 worker 内部事件。

#### 步骤 2.3：补充 controller 测试

覆盖至少：

- 三类测量选择正确的 worker factory。
- 测量运行期间拒绝第二次启动。
- 完成、失败、取消后回到可重新启动状态。
- 普通停止和紧急停止的请求保持区分。
- 线损进入等待、继续和等待期间停止。
- worker 构造失败不会启动测量。
- controller 不会关闭外部注入端口。

#### 通过标准

- 现有 `tests/test_gui_workers.py` 继续通过。
- 新 controller 测试完全使用 fake worker 或离线 worker factory。
- 不修改 SCPI、硬件动作顺序和 `app.gui_runtime` 的端口所有权行为。

---

### 阶段 3：拆分线损、驱动映射和主功放页面

按页面逐个迁移，每个页面完成后再迁移下一个。

#### 步骤 3.1：拆分 `CableLossPage`

1. 将线损 tab 的控件创建从 `MainWindow.create_cable_loss_tab()` 迁移到页面类。
2. 将线损参数读取、开始按钮、停止按钮和继续按钮接到 controller。
3. 将进度、日志、实时数据、暂停提示和结果表格更新迁移到页面。
4. 页面只接收结构化结果和状态，不读取结果文件。
5. 保留两步测量的用户可见行为和现有 signal 名称。
6. 增加页面构造、controller 连接、暂停/继续和结果显示测试。

#### 步骤 3.2：拆分 `DriverMappingPage`

1. 迁移驱动映射 tab 的控件和参数读取。
2. 通过 controller 启动、停止和显示进度。
3. 将 `power_mapping` 转换为页面显示模型。
4. 保持显式输入和缺失输入的错误展示行为。
5. 增加正常完成、停止、异常和重复启动测试。

#### 步骤 3.3：拆分 `AmplifierPage`

1. 迁移主功放 tab 的控件和参数读取。
2. 通过 controller 启动、停止和显示功率扫描进度。
3. 将 `measurement_results` 转换为页面显示模型，不暴露持久化 JSON 结构。
4. 保持驱动模式、线损输入、结果表格和实时图显示行为。
5. 增加正常完成、取消、异常和结果更新测试。

#### 每个页面的完成标准

- 页面可以在没有真实设备的情况下实例化和测试。
- 页面不直接导入 `app.gui_runtime`、`result_storage`、VISA 或具体 worker 类。
- 页面不再依赖 `MainWindow` 的测量专用字段。
- 旧 tab 顺序、按钮行为、信号语义和错误反馈保持不变。
- 页面完成后删除对应的窗口测量实现，避免双重入口。

---

### 阶段 4：收敛 `MainWindow` 集成边界

#### 目标

让主窗口只负责组合页面和全局 UI，不再协调具体测量流程。

#### 步骤

1. 修改 `presentation.qt.pages` 的 page definition，使 builder 返回真正的页面对象，而不是调用窗口方法。
2. 在 `MainWindow` 初始化阶段创建一个共享 `MeasurementController`。
3. 将 controller 注入三个测量页面。
4. 将页面对象登记到窗口的页面容器，保留现有 tab 顺序和标题。
5. 将公共状态栏、全局消息和窗口级日志绑定到 controller 的公共事件。
6. 删除已迁移的 `current_worker`、测量专用回调和重复的结果更新方法。
7. 保留配置、可视化、导出和聊天功能的现有窗口入口，但禁止它们重新依赖测量 worker 细节。
8. 增加 GUI 组合测试，验证导入不连接硬件、页面顺序稳定、controller 只创建一次。

#### 通过标准

- `MainWindow` 不直接实例化 `CableLossWorker`、`DriverMappingWorker` 或 `AmplifierWorker`。
- 三类页面通过 controller 启动测量。
- `presentation/qt/pages.py` 不再只是对窗口方法的转发壳。
- 关闭窗口时 controller 能停止活动测量并释放自身线程引用。
- 现有 GUI 导入 smoke 和配置兼容测试继续通过。

---

### 阶段 5：离线验收、架构审计和兼容清理

#### 步骤 5.1：离线测试矩阵

至少覆盖：

- 页面独立实例化和控件默认值。
- 页面与 controller 的 signal 连接和断开。
- controller 的三类启动、并发拒绝、停止、紧急停止、异常和重新启动。
- 线损第二步等待、继续和等待期间停止。
- 页面只消费结构化状态和结果，不读取结果路径。
- 主窗口导入不打开 VISA 资源，不创建硬件端口。
- 既有 worker、runtime、配置读取和结果仓储测试全部通过。

#### 步骤 5.2：架构依赖审计

增加或更新 AST/源码审计，禁止：

- 页面导入 `enhanced_workers`、`result_storage`、VISA 或 SCPI。
- 页面直接创建具体 measurement worker。
- `MainWindow` 直接调用 `app.gui_runtime.create_*_measurement()`。
- controller 直接导入测量服务、硬件驱动或结果存储实现。
- 新代码把业务状态写回 `enhanced_main_gui.py` 的全局字段。

#### 步骤 5.3：删除迁移残留

1. 删除已迁移页面的旧窗口 builder 和无调用方的测量辅助方法。
2. 删除重复 signal 连接和重复结果转换逻辑。
3. 更新 GUI 调用方清单、测试 patch 目标和模块说明。
4. 保留仍有真实调用方的配置、可视化、导出和聊天逻辑，不做无关清理。
5. 记录本期实际删除项及其回滚方式。

#### 通过标准

- 全量离线测试、应用检查、配置校验和编译检查通过。
- 生产 GUI 依赖审计通过。
- 没有遗留的第二套测量启动入口。
- 页面、controller、worker 和 runtime 的依赖方向符合本文定义。

---

### 阶段 6：第二期验收记录和现场门槛管理

#### 步骤

1. 在本文件末尾追加每个阶段的完成日期、修改文件、验证命令、测试数量和回滚点。
2. 记录未执行 Hardware smoke 的理由：若本期只改变 Qt 页面、controller 和离线组合，且未改变 SCPI、设备时序、资源所有权、安全清理顺序或生产配置，则不触发硬件动作。
3. 如果 controller 接入导致 worker 生命周期、端口所有权、硬件配置解析或安全清理逻辑变化，暂停发布并安排受控 Hardware smoke。
4. 真实硬件条件具备后，按第一期计划和 `docs/SCPI与三层测试架构重构收尾计划.md` 的现场清单执行三类完整测量。
5. Hardware smoke 结果独立记录，不用“离线测试通过”替代真实设备验收。

#### 第二期完成标准

- 三类测量页面是独立对象。
- `MeasurementController` 是 GUI 测量生命周期的唯一协调入口。
- `MainWindow` 不直接拥有测量 worker 和测量结果业务状态。
- 页面、controller、worker、runtime 的依赖方向稳定。
- 全部离线回归通过。
- 未执行的 Hardware smoke 已明确记录；如本期没有硬件链路变化，可以将其作为独立待办，不阻塞离线重构合并。

---

## 6. 回滚策略

### 阶段 0 回滚

删除或恢复基线记录，不涉及生产代码。

### 阶段 1 回滚

删除新增契约和状态对象，保留现有 worker 和窗口调用方式。不得修改 worker 的硬件行为。

### 阶段 2 回滚

将 GUI 入口恢复为现有 worker 调用方式，保留 controller 的独立测试；不得恢复 `enhanced_workers.py`。

### 阶段 3 回滚

按页面逐个恢复对应 tab 的窗口 builder。已经通过的页面和 controller 测试保留，不能同时恢复两套活动测量入口。

### 阶段 4 回滚

按窗口集成边界回滚页面注册和 controller 注入，但不回滚第一期的应用用例、结果仓储或端口所有权设计。

### 阶段 5 回滚

只回滚架构审计或无调用方清理，保留通过测试的页面和 controller 实现。若发现硬件行为变化，停止回滚后的生产验证并安排现场审查。

---

## 7. 每批次审查清单

- [ ] 是否只修改了本批次列出的文件？
- [ ] 是否保留现有 tab 顺序、按钮行为和 signal 语义？
- [ ] 页面是否直接依赖了 worker、runtime、VISA、SCPI 或结果文件路径？
- [ ] `MainWindow` 是否仍直接协调具体测量 worker？
- [ ] controller 是否拒绝非法并发启动并清理终态引用？
- [ ] 普通停止、紧急停止、异常和取消是否语义明确？
- [ ] 线损第二步等待期间的继续和停止是否覆盖？
- [ ] 外部注入的 measurement port 是否没有被错误关闭？
- [ ] 页面销毁后是否不会继续收到 controller signal？
- [ ] 是否新增了第二套测量启动入口？
- [ ] 是否更新了 GUI 测试、架构测试和调用方清单？
- [ ] 是否使用 `.env` 中的 `AUTO_TEST_PYTHON` 执行验证？
- [ ] 是否记录了未执行 Hardware smoke 的理由？

---

## 8. 推荐执行顺序

建议严格按以下顺序推进：

1. 阶段 0 基线记录。
2. 阶段 1 契约和状态对象。
3. 阶段 2 controller 与 fake worker 测试。
4. 阶段 3.1 线损页面。
5. 阶段 3.2 驱动映射页面。
6. 阶段 3.3 主功放页面。
7. 阶段 4 主窗口集成收敛。
8. 阶段 5 离线验收和兼容清理。
9. 阶段 6 记录第二期结果，并单独管理 Hardware smoke 门槛。

每个页面迁移完成后先通过该页面的离线测试，再开始下一个页面。不要同时拆三个页面并在最后一次性处理所有生命周期问题。

---

## 9. 当前状态

本文于 2026-10-02 建立，当前状态为“计划已建立，阶段 0 待执行”。

第一期已确认：`enhanced_workers.py` 已删除，三类应用测量用例、结果仓储、输入读取器、Qt worker 和 runtime 组装边界已经稳定。第二期后续工作应以本文件为指南，不得把持久化、硬件组装或测量算法重新放回页面或 `MainWindow`。

真实 Hardware smoke 当前仍待现场条件具备。只要后续批次不改变 SCPI、设备时序、资源所有权、安全清理顺序或生产配置，即可继续完成离线 GUI 重构并单独记录该现场验收门槛。

---

## 10. 阶段 0 基线记录（2026-10-02）

### 10.1 工作区和验证基线

- 执行前工作区：`git status --short` 无输出，未发现用户未提交改动。
- 项目解释器：`.env` 中的 `AUTO_TEST_PYTHON` 为 `C:\My_Document\Anaconda\envs\Auto_test\python.exe`。
- 解释器检查：路径存在；运行环境为 Python 3.11.15，Conda 环境为 `Auto_test`。
- 全量离线测试命令：`./run_tests.ps1`（使用上述解释器）。
- 全量离线测试结果：`Ran 511 tests`，`OK`。
- 应用检查命令：`& 'C:\My_Document\Anaconda\envs\Auto_test\python.exe' launcher.py --check`，通过；必需依赖均已安装。
- 配置校验命令：`& 'C:\My_Document\Anaconda\envs\Auto_test\python.exe' launcher.py --validate-config`，通过。
- 本阶段未执行真实 Hardware smoke；本阶段仅记录和冻结 GUI/测试基线，未修改 SCPI、设备时序、资源所有权或安全清理逻辑。

### 10.2 当前页面顺序和 builder 归属

`MainWindow.init_ui()` 创建 `QTabWidget` 后调用 `presentation.qt.pages.build_pages(self)`。当前 `PageDefinition` 仍将页面 builder 转发到窗口方法，页面对象尚未独立化。

| 顺序 | key | 标题 | 当前 builder | 阶段 3 目标 |
| --- | --- | --- | --- | --- |
| 1 | `configuration` | 仪器配置 | `MainWindow.create_config_tab()` | 本期保留窗口集成边界 |
| 2 | `cable_loss` | 线损测量 | `MainWindow.create_cable_loss_tab()` | `CableLossPage` |
| 3 | `driver_mapping` | 驱动映射 | `MainWindow.create_driver_mapping_tab()` | `DriverMappingPage` |
| 4 | `amplifier` | 功放测试 | `MainWindow.create_amplifier_test_tab()` | `AmplifierPage` |
| 5 | `visualization` | 数据可视化 | `MainWindow.create_visualization_tab()` | 本期保留窗口集成边界 |
| 6 | `export` | 数据导出 | `MainWindow.create_data_export_tab()` | 本期保留窗口集成边界 |

### 10.3 公共控件和页面专用控件

- 公共窗口控件：`tab_widget`、`progress_bar`、`connect_btn`、状态/日志面板、`chat_panel`、全局日志和文件刷新入口。
- 配置页控件：仪器地址和启用开关、功率分配、测试参数、配置保存/连接仪器控件；由 `create_config_tab()` 创建。
- 线损页控件：`show_path1_btn`、`show_path2_btn`、`cable_loss_btn`、`load_cable_results_btn`、`cable_loss_table`；结果表为 5 列：频率及 `cable1` 至 `cable4`。
- 驱动映射页控件：`show_driver_diagram_btn`、`driver_mapping_btn`、`driver_emergency_stop_btn`、`driver_plot_widget`。
- 主功放页控件：`instruction_text`、`show_amp_diagram_btn`、`amplifier_test_btn`、`emergency_stop_btn`、`amplifier_plot_widget`。
- 可视化/导出页控件：当前继续由窗口创建和管理，不在阶段 0 迁移。

### 10.4 三类测量入口、回调和目标归属

| 当前方法/行为 | 当前行为摘要 | 后续目标 |
| --- | --- | --- |
| `start_cable_loss_measurement()` | 检查 `current_worker` 和 `instrument_ctrl`；保存配置；弹出路径 1 接线确认；写回 wiring 元数据；清空线损表；实例化 `CableLossWorker` 并连接信号后启动 | 页面负责输入、接线确认和显示；`MeasurementController.start_cable_loss()` 负责 worker 生命周期 |
| `on_cable_loss_step_pause()` | 依赖当前 worker 的私有 `_waiting_for_continue`；弹出路径 2 接线确认；接受时调用 `continue_measurement()`，取消时调用 `worker.stop()` | controller 暴露唯一 `waiting_for_continue` 状态、`continue_cable_loss()` 和停止入口 |
| `start_driver_mapping()` | 检查端口；保存配置和接线确认；清空实时数据；实例化 `DriverMappingWorker` 并启动；启用紧急停止按钮 | `DriverMappingPage` 组装请求；controller 创建和管理 worker |
| `start_amplifier_test()` | 检查端口；保存配置和接线确认；清空实时数据；设置 `emergency_stop=False`；实例化 `AmplifierWorker` 并启动 | `AmplifierPage` 组装请求；controller 创建和管理 worker |
| `emergency_stop_test()` | 二次确认后设置窗口 `emergency_stop=True`，调用当前 worker 的普通 `stop()`，清零进度 | controller 的 `emergency_stop()`，必须保留与普通停止的语义区别 |
| `emergency_stop_driver_mapping()` | 二次确认后设置窗口 `emergency_stop=True`，调用当前 worker 的普通 `stop()`，恢复驱动映射开始按钮 | controller 的 `emergency_stop()`；按钮状态由页面状态事件驱动 |
| `on_measurement_finished(button)` | 恢复对应开始按钮；设置进度 100；按按钮禁用紧急停止；线损完成时从文件加载结果；刷新文件列表；当前仅清空窗口中的 `instrument_ctrl` 引用，不在此处显式调用 `_close_instrument_port()` | controller 发布完成状态和结构化结果；页面只转换显示模型；需单独验证完成后的端口清理责任 |
| `on_worker_error(error_message)` | 记录并弹框；恢复全部按钮；清零进度；关闭并清空 `instrument_ctrl` | controller 发布失败状态；端口关闭仍由既有运行控制者负责 |
| `on_worker_stopped(reason)` | 记录停止原因；恢复按钮；清零进度；关闭并清空 `instrument_ctrl` | controller 发布 stopped 状态，并区分普通/紧急停止原因 |
| `load_cable_loss_results()` | 直接读取 `CABLE_LOSS_FILE` JSON 并填充表格 | `CableLossPage` 只消费结构化结果；不得保留页面文件读取 |
| `clear_cable_loss_results()` / `update_cable_loss_realtime()` | 清空表格并按 `data_update` 逐频点更新 | `CableLossPage` 的结果显示和实时数据转换 |

### 10.5 当前测量状态字段和所有权

- `current_worker`：初始化于 `MainWindow`，是被聊天、仪器连接和三类测量流程轮流复用的单一字段；新 worker 会覆盖旧引用，不能表示多个 worker 的所有权，也不是可靠的测量类型状态。后续 `MeasurementController` 不得接管聊天 worker，仪器连接 worker 也应与测量生命周期区分。
- `instrument_ctrl`：窗口持有外部注入的 measurement port。失败、停止和窗口关闭路径会由窗口调用 `_close_instrument_port()`；完成路径当前只将窗口引用置空，实际资源清理由测量服务生命周期负责，需在后续 controller 接入时单独验证。当前测量 worker 接收该 port，但 worker 自身不关闭注入 port。
- `emergency_stop`：窗口级布尔标志，主功放和驱动映射启动/紧急停止时使用；当前未形成明确的 controller 状态。
- 实时数据：`real_time_data`、`rt_frequency_list`、`rt_current_freq_index`、`rt_user_browsing`；主要服务驱动映射和主功放图表及频点导航。
- 线损结果：`cable_loss_table` 及其 `clear_cable_loss_results()`、`update_cable_loss_realtime()`、`load_cable_loss_results()` 方法直接由窗口持有。
- 进度和日志：公共 `progress_bar`、`add_log_message()`；当前三类 worker 的 progress/message 直接连接到窗口。
- 页面专用实时图：`driver_plot_widget` 和 `amplifier_plot_widget` 连接窗口的频点导航及实时数据方法。

### 10.6 `WorkerSignals` 连接基线

`presentation.qt.workers.WorkerSignals` 当前稳定信号为：`finished()`、`error(str)`、`stopped(str)`、`result(object)`、`progress(int)`、`message(str)`、`data_update(dict)`、`step_pause(str)`。

- 仪器连接入口：`InstrumentWorker` 的 `finished` -> `on_instrument_connected`，`result` -> `on_instrument_controller_ready`，`stopped` -> `on_worker_stopped`，`error` -> `on_worker_error`，`message` -> `add_log_message`，`progress` -> `progress_bar.setValue`。
- 线损入口：`finished` -> `on_measurement_finished(cable_loss_btn)`，`error` -> `on_worker_error`，`stopped` -> `on_worker_stopped`，`message` -> `add_log_message`，`progress` -> `progress_bar.setValue`，`data_update` -> `update_cable_loss_realtime`，`step_pause` -> `on_cable_loss_step_pause`。
- 驱动映射入口：`finished` -> `on_measurement_finished(driver_mapping_btn)`，`error` -> `on_worker_error`，`stopped` -> `on_worker_stopped`，`message` -> `add_log_message`，`progress` -> `progress_bar.setValue`，`data_update` -> `store_real_time_data`。
- 主功放入口：`finished` -> `on_measurement_finished(amplifier_test_btn)`，`error` -> `on_worker_error`，`message` -> `add_log_message`，`progress` -> `progress_bar.setValue`，`data_update` -> `store_real_time_data`；当前未显式连接 `stopped`，这是后续 controller 接入必须覆盖的生命周期缺口。
- 测试 patch 目标：`tests/test_gui_workers.py` 主要 patch `app.gui_runtime.prepare_configuration` 和三类 `create_*_measurement`；`tests/test_gui_import.py` patch `pyvisa.ResourceManager` 验证 GUI 导入不打开硬件；`tests/test_gui_config_readers.py` patch `enhanced_main_gui.CONFIG_FILE` 验证配置读写。

### 10.7 关闭、切换和重复启动现状

- 关闭窗口：`closeEvent()` 检查 `current_worker.isRunning()`；运行中弹出确认，确认后调用 `worker.stop()`、`worker.wait()`，再关闭 `instrument_ctrl`；未运行时直接关闭端口。当前没有独立 controller 的关闭协议。
- 页面切换：当前仅由 `QTabWidget` 切换 widget，没有页面 `on_activated()` / `on_deactivated()` 生命周期，也没有解除页面级 signal 连接的逻辑。
- 重复启动：线损入口显式检查 `current_worker.isRunning()`；驱动映射和主功放入口未做同样的统一并发检查，只依赖按钮禁用和当前窗口状态。三类测量没有统一的“活动测量类型/状态”拒绝机制。
- 线损暂停：worker 发出 `step_pause` 后，窗口通过 worker 私有 `_waiting_for_continue` 判断是否有效；接受/取消对话框分别继续/停止，当前没有显式 `waiting_for_continue` view state。

### 10.8 阶段 0 清单、通过判断和回滚点

- [x] 已记录 tab 顺序、builder、公共控件和页面专用控件。
- [x] 已登记三类测量的启动、停止相关现状/紧急停止、线损继续、错误、完成和结果显示方法；当前没有独立的普通停止 GUI 入口，线损取消、紧急停止和窗口关闭均直接调用 worker 的 `stop()`。
- [x] 已登记 `current_worker`、`instrument_ctrl`、实时数据、结果表格、进度和紧急停止字段。
- [x] 已登记 `WorkerSignals` 全部信号及当前连接方、测试 patch 目标。
- [x] 已记录关闭窗口、页面切换和重复启动的当前行为。
- [x] 已使用 `.env` 指定解释器执行全量离线测试、应用检查和配置校验。
- [x] 本阶段仅修改本计划文档，未修改生产代码、worker、runtime、配置 schema 或测试实现。

阶段 0 回滚点：删除本节基线记录即可恢复文档状态；不涉及生产代码回滚。后续阶段开始前，应以本节记录作为 tab 顺序、signal 语义、端口所有权和现有用户行为的对照基线。

## 11. 阶段 1 步骤 1.1 记录（2026-10-02）

### 11.1 实际修改

- 在 `presentation/qt/pages.py` 增加无 Qt 依赖的 `PageProtocol`，固定页面的 `build_ui()`、`bind_controller()`、`on_activated()`、`on_deactivated()` 和 `close()` 生命周期接口。
- 在 `presentation/qt/pages.py` 增加 `BasePage`，统一保存 controller 引用、登记 signal 连接、重新绑定时解除旧连接，以及关闭时幂等清理连接。
- 保留现有 `PageDefinition`、页面顺序和 `build_pages(window)` 兼容 builder；本步骤未改变 `MainWindow` 的页面创建行为。
- 新增 `tests/test_gui_pages.py`，使用纯 Python fake signal/controller 验证页面协议、重新绑定、关闭清理和非法绑定行为。

### 11.2 验证和边界

- 页面契约不导入 PySide6、VISA、SCPI、worker、`app.gui_runtime` 或 `enhanced_main_gui`。
- 定向验证：`& 'C:\My_Document\Anaconda\envs\Auto_test\python.exe' -m unittest tests.test_gui_pages tests.test_gui_import tests.test_gui_config_readers`，`Ran 33 tests`，`OK`。
- 编译验证：`& 'C:\My_Document\Anaconda\envs\Auto_test\python.exe' -m py_compile presentation\qt\pages.py tests\test_gui_pages.py`，通过。
- 本步骤未定义测量状态对象、状态转换或 `MeasurementController`，这些内容保留给步骤 1.2 和 1.3。

步骤 1.1 回滚点：删除 `PageProtocol`、`BasePage` 和 `tests/test_gui_pages.py`，恢复 `pages.py` 原有页面注册表；不涉及 worker、runtime、硬件端口或主窗口测量行为。

## 12. 阶段 1 步骤 1.2 记录（2026-10-02）

### 12.1 实际修改

- 新增 `presentation/qt/measurement_state.py`，定义无 Qt 依赖的 `MeasurementKind`、`MeasurementStatus`、`MeasurementResultReference` 和不可变 `MeasurementViewState`。
- 为状态对象定义 `idle -> preparing -> running`、线损专用 `waiting_for_continue -> running`、停止、失败和完成等合法转换；终态不能再次转换。
- `MeasurementViewState` 明确保存进度、消息、错误文本、停止原因和结构化结果引用；失败必须有错误文本，停止必须有停止原因，完成结果的测量类型必须匹配。
- 新增 `PageResultState` 及线损、驱动映射、主功放三类页面结果状态，只保存页面显示所需的只读结构化行数据和元数据，不保存文件路径或持久化 JSON。
- 新增 `tests/test_measurement_state.py`，覆盖线损等待/继续/完成、停止与失败信息、非法转换、结果类型校验和页面结果只读约束。

### 12.2 验证和边界

- 状态契约不导入 PySide6、VISA、SCPI、worker、`app.gui_runtime` 或 `enhanced_main_gui`。
- 定向验证：`& 'C:\My_Document\Anaconda\envs\Auto_test\python.exe' -m unittest tests.test_measurement_state tests.test_gui_pages tests.test_gui_import tests.test_gui_config_readers`，`Ran 40 tests`，`OK`。
- 编译验证：`& 'C:\My_Document\Anaconda\envs\Auto_test\python.exe' -m py_compile presentation\qt\measurement_state.py tests\test_measurement_state.py`，通过。
- 本步骤未实现 `MeasurementController`，未修改 worker、runtime、测量算法、硬件端口所有权或主窗口行为。

步骤 1.2 回滚点：删除 `presentation/qt/measurement_state.py`、`tests/test_measurement_state.py` 及本节记录；不涉及 worker、runtime、硬件端口或主窗口测量行为。
