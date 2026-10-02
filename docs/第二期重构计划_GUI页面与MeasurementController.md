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
