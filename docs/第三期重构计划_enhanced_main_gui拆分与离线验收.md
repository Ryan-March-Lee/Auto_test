# 第三期重构计划：`enhanced_main_gui` 拆分与离线验收

## 1. 文档目的

本文档是第三期重构的执行指南，目标是将 `enhanced_main_gui.py` 从“大型 GUI 综合宿主”逐步收敛为应用组装入口和窗口生命周期协调器。

第三期的范围是 GUI 外围职责拆分，不重新设计第一期已经完成的 worker 清理，也不重新设计第二期已经完成的测量页面和 `MeasurementController` 边界。

本文档约定：

- 每个阶段独立提交、独立验证。
- 优先迁移代码和保持兼容，后续再删除旧入口。
- 第三期执行期间只做离线验证和仿真验证。
- 完整真实 Hardware smoke 放在第三期全部完成后，作为独立现场验收门槛。

## 2. 当前基线

### 2.1 已完成的前两期能力

当前代码已经完成以下重构结果：

1. `enhanced_workers` 不再被生产代码依赖。
2. `CableLossPage`、`DriverMappingPage`、`AmplifierPage` 已拆分到 `presentation/qt/`。
3. `MeasurementController` 统一三类测量的启动、停止、紧急停止、线损继续、状态转发和 worker 终态清理。
4. `MainWindow` 不再直接实例化三类测量 worker，也不直接调用三类测量组装函数。
5. 测量页面不直接依赖 VISA、SCPI、结果存储和具体测量服务。
6. 真实 Hardware smoke 仍是独立门槛，离线测试和历史 `minimal_action` 结果不能替代现场验收。

### 2.2 当前问题

`enhanced_main_gui.py` 当前约 3428 行、约 120 个方法，并同时包含：

- `MainWindow` 应用组装和关闭生命周期；
- 配置页及电源通道、电源分配控件；
- 配置表单读取、配置合并和文件保存；
- 仪器连接 worker 的生命周期处理；
- 状态面板、进度条和日志；
- 实时测量数据缓存和频点浏览；
- 可视化页面、历史数据加载和频率切换；
- 文件列表、JSON/CSV/PDF 导出；
- `ConnectionDialog`、`RealTimePlotWidget`；
- AI 聊天面板、历史对话和聊天设置；
- 应用启动、样式表和日志初始化。

这使得窗口模块承担了过多不同变化原因。后续修改配置、可视化、导出或 AI 助手时，容易触碰测量页面、仪器连接和关闭清理逻辑。

### 2.3 现有依赖和兼容风险

现有测试和外部入口可能直接引用 `enhanced_main_gui` 中的名称或 `MainWindow` 的属性，包括：

- `MainWindow`；
- `ChatPanel`；
- `RealTimePlotWidget`；
- `ConnectionDialog`；
- `MeasurementKind` 等当前模块重新导出的名称；
- 配置读取方法和 `MainWindow` 的控件属性；
- `tests/test_gui_config_readers.py` 对 `MainWindow.__new__` 的纯方法测试。

因此第三期必须使用兼容导出和渐进迁移，不允许一次性删除原模块中的类和符号。

## 3. 第三期目标与非目标

### 3.1 目标

第三期完成后，`MainWindow` 应主要负责：

1. 创建应用级依赖；
2. 构造和注册页面；
3. 连接全局信号和回调；
4. 协调仪器连接与测量 controller 的生命周期；
5. 管理窗口关闭和最终资源清理。

配置、可视化、导出、聊天、连接图、绘图和日志等职责应有清晰的独立模块边界。

### 3.2 非目标

第三期不包含以下事项：

- 不重新实现三类测量服务；
- 不改变 `MeasurementController` 的公开信号和状态契约；
- 不改变 worker 的测量语义和停止语义；
- 不替换 VISA、SCPI、仪器驱动或 `SafetyInstrumentSession`；
- 不在重构期间执行真实 RF 输出或电源动作；
- 不以界面美化、样式重做或新增业务功能作为第三期交付内容。

## 4. 目标目录结构

建议逐步形成如下结构，实际文件名可以在阶段开始前根据现有命名微调，但职责边界不得扩大：

```text
presentation/qt/
├── main_window.py                 # MainWindow 和窗口级组装
├── config_page.py                 # 配置页 UI
├── config_form_state.py           # 配置表单读取、组装和持久化适配
├── visualization_page.py          # 数据加载、频率切换、图表展示
├── export_page.py                 # 文件列表和导出操作
├── status_panel.py                # 进度、日志和日志滚动
├── connection_dialog.py           # ConnectionDialog
├── realtime_plot.py               # RealTimePlotWidget
├── realtime_buffer.py             # 实时频点缓存和浏览状态
└── assistant/
    ├── chat_panel.py              # ChatPanel
    ├── chat_dialogs.py            # 历史记录和设置对话框
    └── chat_worker.py             # ChatWorker 和可选助手适配
```

`enhanced_main_gui.py` 在迁移完成前继续作为兼容入口：

```python
from presentation.qt.main_window import MainWindow
from presentation.qt.realtime_plot import RealTimePlotWidget
from presentation.qt.connection_dialog import ConnectionDialog
from presentation.qt.assistant.chat_panel import ChatPanel
```

兼容入口可以继续重新导出测试需要的类型，但不应继续新增业务实现。

## 5. 执行原则

### 5.1 迁移优先于重写

第一步复制或移动现有实现到目标模块，保持行为不变；第二步调整依赖；第三步补测试；最后才进行小范围清理。禁止在同一个提交中同时迁移、改行为和删除兼容入口。

### 5.2 单向依赖

建议依赖方向如下：

```text
enhanced_main_gui.py
        ↓
presentation.qt.main_window
        ↓
presentation.qt 页面、组件和适配器
        ↓
application / domain / infrastructure
```

页面和组件不得反向 import `enhanced_main_gui`。如果页面需要窗口能力，应通过 callback、协议或显式 context 注入。

### 5.3 保持测量边界

第三期不得把 `MeasurementController` 的内部实现搬回页面或窗口。测量页面继续通过现有 `PageContext` 和 controller 交互；配置、状态和导出拆分不得直接创建 measurement worker。

### 5.4 每阶段可回退

每个阶段结束时都必须满足：

- 应用仍可导入；
- 旧启动入口仍可解析；
- 离线测试可运行；
- 新模块可以单独导入；
- 该阶段不依赖现场设备。

## 6. 分阶段执行计划

## 阶段 3.0：修复基线并建立第三期门禁

### 目标

在开始代码迁移前，保证测试、文档和工具入口描述的是当前仓库，而不是已删除的二期文件。

### 步骤

1. 检查 `git status`，确认没有未说明的工作区改动。
2. 检查 `.env` 中的 `AUTO_TEST_PYTHON`，所有项目命令都使用该解释器。
3. 检查二期验收测试对已删除文档的引用，更新为现存的收尾文档或新的第三期基线文档。
4. 确定测试执行方式：优先使用 `run_tests.ps1`；如果指定环境没有 `pytest`，必须记录为环境阻塞，不得切换到 `base` 环境。
5. 增加第三期架构测试，至少检查：
   - 新模块不反向依赖 `enhanced_main_gui`；
   - 页面不实例化测量 worker；
   - `MainWindow` 不直接调用三类测量组装函数；
   - 兼容入口仍导出必要类型；
   - 配置、可视化、导出和聊天模块不引入不必要的硬件依赖。
6. 记录第三期开始前的离线测试、导入检查、配置校验和编译检查结果。

### 完成条件

- 二期测试不再引用不存在的计划文件；
- 第三期基线测试可以独立运行；
- 测试解释器和依赖状态已记录；
- 没有真实设备动作。

## 阶段 3.1：迁移纯 UI 组件

### 目标

先拆分与 `MainWindow` 业务状态耦合最低的组件，降低后续修改主窗口的风险。

### 建议顺序

1. `RealTimePlotWidget`；
2. `ConnectionDialog`；
3. `ChatWorker`；
4. `ChatHistoryDialog`；
5. `ChatSettingsDialog`；
6. `ChatPanel`；
7. `_UnavailableAssistant` 及可选助手适配。

### 步骤

1. 将类实现迁移到目标模块，保留类名、构造参数、Qt signal 名称和公开控件属性。
2. 将类内部直接依赖的 import 一并迁移，删除无效的模块级依赖。
3. 在 `enhanced_main_gui.py` 中改为兼容导入和重新导出。
4. 更新 `MainWindow` 的构造位置，使其从新模块导入组件。
5. 检查 `ConnectionDialog` 的连接图类型和父窗口行为不变。
6. 检查 `RealTimePlotWidget` 的绘图、导航按钮、滚轮和事件过滤行为不变。
7. 检查 AI 可选依赖不可用时仍可以导入 GUI 模块。

### 离线验证

- 兼容入口导入测试；
- 目标模块单独导入测试；
- `ChatPanel` 可选依赖降级测试；
- `RealTimePlotWidget` 数据更新测试；
- 连接图类型映射测试；
- 现有 GUI worker 和页面测试。

### 完成条件

- 纯 UI 类的实现不再位于 `enhanced_main_gui.py`；
- 旧模块仍能导出原有公共类名；
- 新模块不 import `enhanced_main_gui`；
- 不改变测量 controller、worker 或仪器连接行为。

## 阶段 3.2：拆分配置页面和配置状态

### 目标

将配置控件构造和配置数据组装从 `MainWindow` 中分离，并为配置合并逻辑建立纯 Python 测试边界。

### 迁移范围

- `create_config_tab`；
- `create_power_supply_config`；
- `create_power_assignment_config`；
- `on_pa_unit_count_changed`；
- `update_pa_unit_ui`；
- `on_driver_mode_toggled`；
- `on_driver_power_toggled`；
- `update_driver_power_ui`；
- `on_instrument_enabled_changed`；
- `get_enabled_power_supplies`；
- `update_power_supply_options`；
- `_read_instrument_config_from_ui`；
- `_read_test_parameters_from_ui`；
- `_read_power_supply_config_from_ui`；
- `_read_power_assignment_from_ui`；
- `_build_config_from_ui`。

### 步骤

1. 创建配置页面对象，页面只负责创建控件、显示状态和发出保存/连接请求。
2. 为配置页面定义显式 callback 或 context，不直接调用窗口的任意方法。
3. 创建 `ConfigFormState` 或等价适配器，负责从控件读取值并生成配置片段。
4. 保留现有配置中的未知字段、model 字段、扩展字段和电源 channels。
5. 将 wiring confirmation 清除规则作为显式配置组装行为保留。
6. 将频率文本解析从 `eval` 改为安全的结构化解析，并为非法输入添加测试。
7. 迁移配置保存和连接前的保存流程，确保保存失败时仍恢复按钮状态并记录错误。
8. 在兼容期保留 `MainWindow` 上现有测试需要的方法，或者提供明确代理方法；代理不应重新实现配置逻辑。

### 必须保护的行为

- 只修改 UI 管理的字段，不覆盖未知配置字段；
- 电源地址和启用状态与电源通道配置保持一致；
- PA 单元数量变化正确显示和隐藏 Unit2、Unit3；
- 驱动模式和驱动电源启用状态正确控制下拉框；
- 配置任意变化后现场接线确认失效；
- 保存失败不启动测量；
- 连接测量前仍要求仪器已连接。

### 离线验证

- 配置表单到配置字典的单元测试；
- 未知字段保留测试；
- wiring confirmation 重置测试；
- 频率非法输入测试；
- 电源分配显示状态测试；
- 现有 `test_gui_config_readers.py` 和配置模型测试。

### 完成条件

- 配置逻辑可以不创建完整 `MainWindow` 而进行测试；
- 配置页面不直接依赖 `MeasurementController` 的实现；
- 配置保存和连接行为保持原有语义；
- `enhanced_main_gui.py` 不再包含配置页的大段控件构造代码。

## 阶段 3.3：拆分可视化和导出

### 目标

将历史结果加载、频率浏览、图表展示、文件列表和导出操作从窗口协调逻辑中分离。

### 迁移范围

可视化页面：

- `create_visualization_tab`；
- `load_test_data`；
- `update_frequency_display`；
- `display_current_frequency_data`；
- `prev_frequency`；
- `next_frequency`；
- `generate_report`。

导出页面：

- `create_data_export_tab`；
- `refresh_file_list`；
- `export_json`；
- `export_csv`；
- `export_pdf`。

### 步骤

1. 创建可视化页面，明确输入是文件路径或结构化结果，而不是整个 `MainWindow`。
2. 将功放测试结果和驱动映射结果的历史格式转换抽成可测试的转换函数。
3. 页面通过 `result_reading`、`DataVisualization` 或分析报告服务访问数据。
4. 保持频率排序、当前索引、空数据和错误状态行为。
5. 创建导出页面，将文件列表刷新和选中文件导出操作封装起来。
6. 导出页面不得直接访问仪器控制或 measurement worker。
7. 保留当前文件命名规则和 CSV/PDF 支持范围。
8. `MeasurementController.finished` 仍然触发文件列表刷新，不能因页面迁移而丢失。

### 离线验证

- 功放结果格式加载测试；
- 驱动映射格式转换测试；
- 空数据和非法数据测试；
- 频率前后切换边界测试；
- 文件列表排序测试；
- JSON/CSV/PDF 导出测试；
- 测量完成后文件列表刷新的回归测试。

### 完成条件

- 可视化和导出页面可独立导入；
- 页面不直接依赖仪器或测量 worker；
- 结果读取和报告生成行为与迁移前一致；
- `MainWindow` 不再保存可视化页面的全部内部状态。

## 阶段 3.4：拆分状态面板和实时数据协调

### 目标

消除 `MainWindow` 中重复的日志、实时数据和频点浏览状态，使页面绘图数据、聊天上下文数据和历史浏览状态有明确边界。

### 迁移范围

- `create_status_panel`；
- `add_log_message`；
- `on_log_scroll_changed`；
- `clear_log`；
- `update_status`；
- `_store_driver_mapping_realtime_data`；
- `_store_amplifier_realtime_data`；
- `store_real_time_data`；
- `display_current_rt_frequency_data`；
- `rt_prev_frequency`；
- `rt_next_frequency`；
- `update_rt_nav_buttons`；
- `clear_real_time_data`。

### 步骤

1. 创建 `StatusPanel`，封装进度条、日志文本框、日志滚动状态和清除操作。
2. 创建 `RealtimeMeasurementBuffer`，封装按频率保存数据、排序、当前索引和浏览状态。
3. 明确实时数据的三个用途：
   - 当前测量页面绘图；
   - AI 聊天上下文；
   - 用户浏览历史频点。
4. 页面通过已有 realtime callback 更新自己的图表；窗口或上下文对象只保留聊天所需的最小数据。
5. 删除重复的 driver/amplifier 数据缓存实现，统一通过 buffer 或明确的 context 更新。
6. 保留 `clear_realtime_callback` 在新页面生命周期中的行为。
7. 检查页面切换、测量完成、停止、异常和重新开始时 buffer 是否清理。

### 离线验证

- 日志自动滚动和用户手动滚动测试；
- 实时数据按频率排序测试；
- 用户浏览历史频点时不被新数据强制跳转测试；
- 到达最新频点后恢复自动跟随测试；
- 清除实时数据测试；
- 页面切换和测量结束后的数据清理测试。

### 完成条件

- `MainWindow` 不再直接维护多套实时频点状态；
- 日志面板有明确独立所有者；
- realtime callback 的数据方向和清理行为有测试；
- 不改变页面实时绘图和聊天上下文的现有功能。

## 阶段 3.5：收敛 `MainWindow` 和应用入口

### 目标

完成最终组装边界，把 `enhanced_main_gui.py` 降为兼容入口，或将其明确变成薄启动模块。

### 迁移范围

- `MainWindow.__init__`；
- `init_ui`；
- `_register_pages`；
- `_on_page_changed`；
- 仪器连接相关回调；
- controller 线程结束和端口清理；
- `closeEvent`；
- `main` 和应用样式初始化。

### 步骤

1. 将窗口实现迁移到 `presentation/qt/main_window.py`。
2. 将全局依赖通过构造参数、context 或 factory 注入，避免新模块隐式读取大量全局变量。
3. 保持页面生命周期调用顺序：先停用旧页面，再激活新页面；关闭时断开 tab 信号并关闭页面。
4. 保持 `MeasurementController` 的 `finished`、`message`、`rejected` 和线程结束回调连接。
5. 保持仪器连接 worker 的 `finished`、`result`、`stopped`、`error`、`message` 和 `progress` 信号连接。
6. 保持连接失败、测量异常、普通停止、紧急停止和窗口关闭时的资源清理。
7. 保持外部注入的 measurement port 不由 controller 越权关闭的现有契约。
8. 将应用样式和日志初始化从业务窗口方法中移出到启动组装函数。
9. 让 `enhanced_main_gui.py` 仅保留兼容导出和必要的历史启动兼容逻辑。
10. 在确认所有引用迁移后，再考虑删除兼容代理；删除必须单独提交并先更新测试。

### 离线验证

- GUI 模块导入不连接真实仪器测试；
- MainWindow 组装测试；
- 页面注册和页面生命周期测试；
- controller 线程完成后端口清理测试；
- 仪器连接错误和停止回调测试；
- 窗口关闭超时和资源释放测试；
- 架构依赖 AST 审计。

### 完成条件

- `MainWindow` 主要承担组装和生命周期协调；
- `enhanced_main_gui.py` 不再包含大段页面和组件实现；
- 新模块无反向依赖；
- 所有现有离线测试通过；
- 真实 Hardware smoke 仍未在开发阶段执行。

## 7. 测试和验收矩阵

### 7.1 每个阶段都要执行

1. 新模块导入检查；
2. 兼容入口导入检查；
3. 相关单元测试；
4. 架构依赖测试；
5. Python 编译检查；
6. `launcher.py --check`；
7. `launcher.py --validate-config`；
8. 检查 git diff，确认没有无关文件和配置泄漏。

### 7.2 第三期最终离线门槛

最终离线验收至少应覆盖：

- GUI import 不打开仪器；
- 配置读取、合并、保存和非法输入；
- 页面构造和生命周期；
- controller 启动、停止、异常和线程清理；
- 三类测量页面回归；
- 可视化数据加载和频率切换；
- 文件列表和导出；
- AI 可选依赖不可用时的降级；
- 关闭窗口时的线程和端口清理；
- 生产模块依赖边界审计；
- 不存在对 `enhanced_workers` 的生产依赖。

### 7.3 测试环境规则

所有项目命令必须使用 `.env` 中的 `AUTO_TEST_PYTHON`。如果解释器不存在，或缺少测试依赖，应记录环境阻塞并停止，不得使用 `base` 环境或裸 `python` 规避问题。

## 8. 真实 Hardware smoke 的后置安排

真实 Hardware smoke 不属于 3.1 至 3.5 的中间步骤，应在第三期最终离线门槛通过后单独安排。

### 8.1 前置条件

现场执行前必须具备：

1. 现场授权和安全窗口；
2. 正式本地配置、设备身份和 VISA 地址；
3. 生产电源角色、通道和上下电顺序；
4. DUT 功率限制和保护条件；
5. `HARDWARE_SMOKE_ENABLED=1`；
6. `run_hardware_smoke.ps1 -ConfirmHardwareSmoke` 所需确认；
7. 现场原始资料不进入版本库。

### 8.2 执行顺序

按低风险到高风险执行：

1. 基础连接、设备身份和安全清理 smoke；
2. 线损测量；
3. 驱动功率映射；
4. 主功放测量。

每类测量至少覆盖正常完成、普通停止、紧急停止、异常和重新连接。每次结束都必须确认 RF 关闭、电源按规定顺序关闭、VISA 资源释放，下一次运行不复用已清理端口。

### 8.3 完成标准

只有同时满足以下条件，第三期涉及的现场验收才能标记完成：

- 三类完整测量均通过新应用组装路径完成；
- 正常、停止、紧急停止、异常和重新连接路径均有现场证据；
- 现场确认设备、角色、通道、功放供电和电源拓扑；
- 没有未解释的 smoke 或测量失败；
- 结果、配置快照、操作顺序、清理证据和回滚决定完成归档。

## 9. 提交和回退策略

建议提交顺序：

1. `docs/test`: 修复二期基线和第三期门禁；
2. `refactor(gui): extract pure qt components`；
3. `refactor(gui): extract configuration page and form state`；
4. `refactor(gui): extract visualization and export pages`；
5. `refactor(gui): extract status and realtime coordination`；
6. `refactor(gui): reduce main window to composition lifecycle`；
7. `test(gui): complete phase three offline acceptance`；
8. 现场 smoke 完成后再单独提交验收记录。

每个提交都应能独立回退。不得把现场配置、设备地址、凭据、原始报告或临时结果提交到版本库。

## 10. 第三期完成定义

第三期代码重构完成需要同时满足：

1. `enhanced_main_gui.py` 已不再承载完整 GUI 实现；
2. 配置、可视化、导出、状态、实时缓存、连接图、绘图和聊天职责均有清晰模块；
3. `MainWindow` 只保留应用组装和生命周期协调；
4. 测量页面和 `MeasurementController` 的第二期边界未被破坏；
5. 所有离线测试、导入检查、配置校验、编译检查和架构审计通过；
6. 兼容入口和必要公共符号已明确保留或经过单独迁移；
7. 真实 Hardware smoke 已被安排为独立后置验收，且没有将离线结果冒充现场通过。

第三期的最终状态应描述为：**GUI 外围职责拆分完成，离线验收通过，真实 Hardware smoke 独立待执行或已完成。**
