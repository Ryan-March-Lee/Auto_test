# GUI 业务逻辑重构计划

## 目标

在不更换 PySide6/Qt 技术栈的前提下，降低 GUI 页面调整的改动半径：

- 页面只负责控件、布局、渲染和转发用户意图；
- 配置编辑、设备绑定、正式配置编写和业务校验由 application/domain 承担；
- 页面状态有单一来源，不再让 Qt 控件、页面配置字典、协调器配置和运行快照互相回读；
- 视觉调整不需要修改业务逻辑；
- 业务规则调整不需要修改大量 QWidget 代码。

## 不改变的约束

1. 保留 PySide6、Qt Widgets、现有硬件连接方式和实时绘图方式。
2. 保留 `PreparedRun` 运行契约：连接仪器和正式测量必须绑定冻结运行。
3. 保留现有配置文件兼容边界，不在本次重构中扩大旧格式兼容范围。
4. 保留现有测试分层和仓库架构门禁；每一步都先通过对应门禁，再继续下一步。
5. 不进行一次性“大搬家”。旧入口在迁移期间保留兼容壳，确认新路径稳定后再删除。

## 总体技术决定

### 1. 采用单向数据流，不引入大型 GUI 框架

目标流程：

```text
Qt 控件事件
    -> Command / 用户意图
    -> ViewModel 或 Application Use Case
    -> 新的业务状态
    -> Qt 页面 render(state)
```

不引入 Redux、全局事件总线或通用 MVVM 框架。只使用普通 Python `dataclass`、Protocol 和显式方法，避免为了重构再制造一层复杂基础设施。

### 2. 用类型化草稿替代页面里的配置字典

新增 `ConfigurationDraft` 作为配置编辑阶段的唯一数据模型。它表示“用户正在编辑的配置”，不等同于最终的 `TestPlan`、`RunResourceMapping` 或 `PreparedRun`。

建议包含：

- 测试频率和信号源扫描参数；
- 压缩点、衰减器、驱动模式；
- DUT 供电数量和电源分配；
- 电源通道参数；
- 设备绑定。

转换关系固定为：

```text
ConfigurationDraft
    -> ConfigurationAuthoringService
    -> 正式配置文档
    -> 现有运行创建/校验流程
```

### 3. 用户可修正的问题返回结构化结果

配置校验、设备选择和电源分配错误统一返回 `ValidationIssue` / `ValidationResult`，至少包含路径、错误码和用户文案。

- 用户输入错误：返回结构化问题，不用异常控制正常流程；
- 文件、硬件和程序错误：继续使用异常；
- Qt 页面只负责把结构化问题显示出来，不重新解释业务规则。

### 4. 纯 Python ViewModel 优先

ViewModel 不导入 PySide6，不直接读写文件，不创建仪器，不弹 Qt 对话框。它保存页面展示所需状态，并把动作转换成应用调用或页面 Effect。

现有 `history_view_model.py` 的无 Qt 设计作为参考。

### 5. 以配置页为第一条迁移样板

配置页同时包含表单、设备绑定、动态电源、正式配置编写和运行创建，是当前改动成本最高且最能验证方案的页面。先把它改好，再把同样模式推广到测量页面。

---

## 阶段一：建立基线和新边界

### 步骤 1：冻结当前行为清单

**目的**：在移动代码前，明确哪些行为必须保持不变。

**执行策略**：

1. 整理配置页的用户动作：加载配置、编辑参数、应用设备、切换驱动模式、保存、创建运行、加载运行。
2. 为每个动作记录输入、输出、失败提示和是否产生文件/运行副作用。
3. 从现有 UI 测试、集成测试和 `docs/正式API目录清单.md` 提取已有契约，不新增业务规则。
4. 只补缺少的纯 Python 行为测试；不为了提高覆盖率批量重写现有 Qt 测试。

**技术决定**：

- 以现有测试和正式 API 文档为行为基准；
- 不以当前页面的内部控件名称作为长期契约；
- 本步骤不移动生产代码。

**完成标准**：

- 有一份配置编辑行为表；
- 现有 `tools/run_tests.py --layer ui` 通过；
- 后续每一步都能对照行为表判断是否回归。

**工作范围控制**：只修改一个文档和必要的测试说明。

**完成状态**：✅ 已完成。行为表见[配置编辑行为基线](配置编辑行为基线.md)；只补了两组
无 `QApplication` 的行为测试（共 4 个用例：`load_configuration`、`apply_test_mode`），
未移动生产代码。

### 步骤 2：建立配置领域词汇和模块归属表

**目的**：先解决“同一个概念在多处用字典表达”的问题。

**执行策略**：

1. 列出 `test_frequencies`、`driver_mode`、`power_supply_assignment`、`device_bindings` 等字段的正式名称、类型和所属阶段。
2. 区分三类对象：编辑草稿、正式配置文档、冻结运行对象。
3. 为每个现有函数标注归属：domain、application、presentation adapter 或 Qt view。
4. 不在此步骤重命名大量旧字段，只建立新模型使用的术语。

**技术决定**：

- `ConfigurationDraft` 属于 application 配置编辑边界；
- 配置规则和值对象属于 domain；
- JSON 字段转换属于 application authoring service；
- Qt 控件读写属于 presentation adapter；
- 规则所有者不等于唯一函数：domain 可同时持有编写文档入口（`validate_config`）与正式模型入口
  （`validate_test_plan`），但语义只能有一份定义，页面不得复制判据；
- 只影响控件初值的缺省（如缺省电源选择）登记为显示策略，不是业务规则，不进正式文档；
- 持有呈现层状态并编排 application 用例的协调器登记为 presentation controller，不是纯数据 adapter。

**完成标准**：

- `docs/正式API目录清单.md` 中相关职责与新归属一致；
- 没有一项业务规则同时登记为页面规则和应用规则。

**工作范围控制**：只改文档和模块注释，不改变运行行为。

**完成状态**：✅ 已完成。词汇、三阶段对象和函数归属见[配置领域词汇与模块归属](配置领域词汇与模块归属.md)；
`docs/正式API目录清单.md` 的配置相关职责已按新归属补充说明。只改了文档与模块注释，未重命名字段、
未移动生产代码。

初稿完成后又做了一次归属复核，修订见该文第 9 节：补登 `_formal_run_errors` 这处页面侧的正式校验
副本；把“唯一实现”改为“规则定义与入口”；拆分身份语义（domain）与记录级一致性判定（application，
步骤 5）；把缺省电源选择移出业务规则表、改登记为显示策略；新增 `presentation controller` 分类；
完成标准改为入口级标注。修订只动文档、API 清单与模块注释，未改任何运行逻辑。

---

## 阶段二：抽取配置业务对象

### 步骤 3：新增 `ConfigurationDraft` 和配置编辑结果对象

**目的**：为页面状态建立稳定的数据边界。

**执行策略**：

1. 在 `application` 下新增配置编辑模型，先覆盖当前配置页最常用字段。
2. 使用 `dataclass` 表达结构；集合使用 tuple 或受控副本，避免外部随意修改内部状态。
3. 提供 `from_document()` / `to_document()` 的最小转换能力，但暂时不替换旧页面。
4. 为缺失、非法类型和空引用保留明确状态，不在模型中偷偷挑选默认设备。

**技术决定**：

- 编辑模型不包含 Qt、文件路径、仪器对象和运行 ID；
- 单位和关键约束优先复用现有 domain 值对象；
- 不直接复用 `AmplifierMeasurementInputs`，因为它是测量输入，不是可编辑草稿。

**完成标准**：

- 可以用纯 Python 构造、复制和比较草稿；
- 草稿序列化后的关键字段与当前配置格式一致；
- 新增模型测试不需要启动 `QApplication`。

**工作范围控制**：新增 1～3 个纯 Python 文件，不修改 Qt 页面。

**完成状态**：✅ 已完成。新增两个纯 Python 模块：

- `application.configuration_editing`：`ValidationIssue` / `ValidationResult`，问题包含路径、稳定错误码和用户文案；
- `application.configuration_draft`：`ConfigurationDraft`、`ConfigurationDraftResult`，以及
  `PowerSupplyDraft`、`PowerChannelDraft`、`PowerAssignmentDraft`、`DeviceBindingDraft`、
  `DeviceBindingsDraft`。

草稿只覆盖配置编辑阶段：频率/扫描/压缩点/衰减器/DUT 输入限制复用 domain 值对象，集合为
`tuple`，电源绑定为受控键值对；`from_document()` 返回草稿和结构化问题，`to_document()`
只输出编辑段，不生成正式 `test_plan` / `run_mapping`，也不写 `schema_version`。缺失字段
（含 `driver_mode.enabled` 与通道保护开关）保持 `None`，空引用保持 `None`；非法类型、
非整数数量、非字符串电源引用与非有限数值都返回结构化问题（直接构造时为构造错误），
旧槽位绑定返回警告而不是猜设备。新增测试 `tests/test_configuration_draft.py`
不启动 `QApplication`；未修改 Qt 页面、未改变运行行为。

### 步骤 4：抽取 `ConfigurationAuthoringService`

**目的**：把正式配置编写从 `presentation.qt.configuration_authoring` 移到 application 边界。

**执行策略**：

1. 将现有 `author_official_configuration()` 的行为原样搬到 application 新服务。
2. 保留旧函数作为兼容转发壳，内部委托新服务。
3. 让新服务只接受 `ConfigurationDraft` 或明确的配置编辑 DTO。
4. 继续调用现有 domain 校验，不在搬迁时改变字段含义。
5. 逐步把现有测试从旧模块入口迁移到新模块入口。

**技术决定**：

- 正式配置文档只允许由一个 application 服务生成；
- Qt 页面不再直接 import `domain.configuration.models` 做正式运行校验；
- 兼容壳只保留一个迁移周期，不能继续增加新逻辑。

**完成标准**：

- `ConfigPage` 不再直接调用 `author_official_configuration`；
- 新旧入口对同一输入生成完全相同的正式文档；
- architecture gate、配置相关 UI 测试和配置单元测试通过。

**工作范围控制**：先只移动 authoring，不同时移动所有表单控件读取逻辑。

### 步骤 5：抽取 `DeviceBindingService`

**目的**：把设备选择、角色唯一性、身份一致性和整批替换规则从 `ConfigPage.apply_discovered_devices()` 移出。

**执行策略**：

1. 定义 `SelectedDevice`、`ApplyDeviceSelectionRequest` 和 `DeviceBindingResult`。
2. 将现有三轮校验和整批替换逻辑原样迁移到 application/domain 边界。
3. `DeviceBindingService` 只返回新的绑定集合和结构化问题，不更新 Qt 控件。
4. `ConfigPage` 暂时保留同名方法，改成调用 service 后更新页面状态。
5. 为成功、空选择、重复角色、身份不一致、重复设备和模拟设备分别补纯 Python 测试。

**技术决定**：

- “本次勾选结果替换旧绑定”是业务规则，由 service 负责；
- 卡片选中状态是 Qt 细节，不进入 service；
- 用户输入失败返回 `DeviceBindingResult`，不使用异常；
- 设备表读取和扫描仍由现有 composition/infrastructure 负责。

**完成标准**：

- `ConfigPage.apply_discovered_devices()` 不再包含业务校验循环；
- 设备绑定逻辑可以在无 Qt、无 QApplication 下完整测试；
- 现有设备发现 UI 测试保持通过。

**工作范围控制**：只迁移设备绑定，不同时改扫描卡片和扫描线程。

---

## 阶段三：建立配置页面状态流

### 步骤 6：新增 `ConfigurationViewModel`

**目的**：让页面从“控件是状态源”改为“状态驱动控件”。

**执行策略**：

1. ViewModel 持有 `ConfigurationDraft`、设备绑定、校验问题、运行状态和 dirty 状态。
2. 提供小而明确的方法，例如：

   - `load(document)`
   - `update_test_parameters(...)`
   - `apply_device_selection(...)`
   - `validate()`
   - `save()`
   - `create_run()`

3. 每个方法返回新的页面状态或结构化结果，不直接访问 Qt 控件。
4. 初期允许旧 `ConfigFormState` 作为控件适配器，把控件值转换成 ViewModel 输入。

**技术决定**：

- ViewModel 使用纯 Python，不继承 `QObject`；
- 状态更新采用显式方法，不引入全局事件总线；
- 文件保存和运行创建仍通过 `ApplicationCoordinator`/应用端口完成；
- ViewModel 不弹窗，页面根据结果决定显示日志、提示框还是字段错误。

**完成标准**：

- 可以在无 QApplication 下测试编辑、dirty、验证失败和状态切换；
- `ApplicationCoordinator` 不需要读取页面控件；
- 配置页的主要业务状态不再散落在 `self.config`、控件和多个临时变量中。

**工作范围控制**：先只接入“测试参数 + 设备绑定”，电源动态标签页下一步接入。

### 步骤 7：把控件读写收拢为一个 Qt Adapter

**目的**：隔离 Qt 控件协议和 ViewModel。

**执行策略**：

1. 新增一个配置表单 adapter，集中负责：控件读取、控件赋值、组合框业务值读取、错误显示。
2. `ConfigPage` 的 `build_config()`、`apply_config()` 改为调用 adapter + ViewModel。
3. 去掉页面中对 `ConfigFormState` 的多处直接调用。
4. 保留 `ConfigPage` 对外的 `ConfigurationPagePort`，避免协调器一次性改动。

**技术决定**：

- 控件显示文本和业务值继续分离，电源引用只从 `itemData` 读取；
- adapter 可以依赖 PySide6，但不能包含设备绑定或正式配置业务规则；
- 页面只保留控件注册和信号连接。

**完成标准**：

- 配置业务测试不再依赖真实 Qt 控件；
- 更换控件类型时只需要修改 adapter；
- `ConfigPage` 的核心方法变成短委托方法。

**工作范围控制**：先覆盖现有控件，不引入通用表单框架。

### 步骤 8：解耦 `ConnectionPage` 和 `ConfigPage`

**目的**：消除页面之间直接互相调用内部实现。

**执行策略**：

1. 定义 `DeviceBindingPort` 或由 ViewModel 提供连接页需要的窄接口。
2. `ConnectionPage` 只提交 `ApplyDeviceSelectionRequest`，不直接访问 `config_page.device_bindings`。
3. MainWindow 或配置会话协调器负责把连接页结果交给 ViewModel。
4. 扫描框默认网段、旧绑定提示等查询改为读取只读的 `ConfigurationViewState`。

**技术决定**：

- 页面之间不传递另一个页面对象；
- 页面导航信号只表示用户意图，不携带配置业务决策；
- 连接页仍然不写文件、不连接仪器。

**完成标准**：

- `ConnectionPage` 构造函数不再接受 `ConfigPage` 实例；
- 设备发现页面可以用替身 port 单独测试；
- 仪表配置流程的集成测试继续通过。

**工作范围控制**：只改页面接口和装配，不改变扫描视觉和卡片布局。

---

## 阶段四：收敛测量业务入口

### 步骤 9：抽取测量启动 Facade

**目的**：减少三个测量页面重复组装“检查运行、接线确认、创建命令、启动 controller”的代码。

**执行策略**：

1. 定义统一的 `StartMeasurementRequest`，包含测量类型和当前 `PreparedRun`。
2. 把运行契约检查、启动前置条件和 `MeasurementCommand` 构造集中到一个 facade。
3. 页面只负责收集用户点击、显示接线对话框，然后提交启动意图。
4. 保留线损测量的“第二步继续”和不同结果显示作为专属行为。

**技术决定**：

- 运行契约仍由现有 `run_contract`/`PreparedRun` 判据决定，不复制判据；
- facade 不创建 Qt 对话框；
- controller 继续负责 worker 生命周期和 Qt signal 适配；
- 不把所有三个页面强行合并成一个巨型页面。

**完成标准**：

- 三个页面不再各自构造相同的 `MeasurementCommand` 流程；
- 启动失败、无运行、已有任务等行为由同一入口测试；
- 现有 controller 和页面测试通过。

**工作范围控制**：先只抽取启动流程，不改实时图和结果表。

### 步骤 10：抽取测量页面公共状态绑定

**目的**：减少按钮启用状态、停止、错误和完成处理的重复代码。

**执行策略**：

1. 定义通用的测量页面展示状态到按钮状态的映射。
2. 把开始、停止、取消、紧急停止的可用性更新集中到一个公共控件/Presenter。
3. 页面继续保留各自的结果展示、接线图和特殊提示。
4. `ArchivalPageMixin` 继续保留，避免重复实现归档状态机。

**技术决定**：

- 公共状态使用现有 `MeasurementViewState` 和 `ControllerState`，不新增第二套状态机；
- 页面只声明自己支持哪些动作；
- 不把线损的暂停流程抽象成所有页面都必须理解的通用分支。

**完成标准**：

- 测量页面的状态按钮逻辑集中；
- 新增一个测量动作时不需要复制三组信号处理代码；
- 不改变实际测量线程和硬件清理流程。

**工作范围控制**：只处理公共控制状态，不碰硬件业务和结果 DTO。

---

## 阶段五：让视觉改动停留在表现层

### 步骤 11：整理静态布局和主题边界

**目的**：在业务边界稳定后，降低页面视觉调整的改动量。

**执行策略**：

1. 评估欢迎页、连接页和配置页中的静态布局，优先迁移到 Qt Designer `.ui` 文件。
2. 动态电源标签页、设备卡片和实时图继续保留 Python 构建。
3. 颜色、字号、间距继续使用 `theme.py` 设计令牌；页面不新增散落的颜色和尺寸常量。
4. 自绘卡片保留为独立控件，不让页面承担绘制细节。

**技术决定**：

- 不为了“少写几行 Python”引入 QML；
- `.ui` 只承担静态结构，不承担业务规则；
- 动态列表优先使用明确的模型/数据源，而不是靠页面手工操作大量子控件。

**完成标准**：

- 常规布局调整主要修改 `.ui` 或主题令牌；
- 页面业务模块不再因为颜色、间距和按钮排列变化而修改；
- 自绘组件有独立的输入和展示契约。

**工作范围控制**：一次只迁移一个页面，优先连接页。

### 步骤 12：替换页面内部实现测试为行为测试

**目的**：避免以后改控件名称或布局时大规模修改测试。

**执行策略**：

1. 纯 Python 业务规则测试迁移到 application/domain 测试。
2. Qt 测试重点保留用户可见行为：点击、显示、禁用、导航和错误提示。
3. 减少直接访问内部控件的测试；必要时通过页面公开的语义查询方法断言。
4. 删除已经被新 ViewModel 测试完全覆盖的重复 Qt 细节测试。

**技术决定**：

- 不追求删除所有 UI 测试；Qt 交互和视觉契约仍然需要测试；
- 测试通过公开行为验证页面，不把控件变量名当公共 API；
- 测试重构必须保持现有架构门禁顺序。

**完成标准**：

- 配置业务变更主要影响纯 Python 测试；
- 视觉布局变更不会要求修改大量业务测试；
- `tools/run_tests.py --layer ui` 和必要的 `--layer integration` 通过。

**工作范围控制**：每次只迁移一个测试模块，不集中重写全部 UI 测试。

---

## 阶段六：清理兼容层并固化边界

### 步骤 13：删除旧逻辑和兼容壳

**目的**：避免新旧两套业务路径长期并存。

**执行策略**：

1. 搜索旧入口的全部调用方。
2. 确认新 ViewModel、application service 和页面 adapter 已覆盖调用方。
3. 删除旧的页面业务实现、重复 authoring 入口和无调用兼容函数。
4. 同步更新 `正式API目录清单.md`、架构测试登记和相关文档。

**技术决定**：

- 兼容壳只在迁移阶段存在，不作为长期 API；
- 删除前必须有调用方清单和行为测试；
- 不删除仍被历史数据读取边界使用的兼容逻辑。

**完成标准**：

- 每项配置业务规则只有一个正式实现；
- `presentation.qt` 不再生成正式运行配置或执行设备绑定决策；
- 全部 repository gates、UI 测试和 integration 测试通过。

**工作范围控制**：按模块逐个删除，不做一次性目录重命名。

### 步骤 14：建立长期改动规则

**目的**：防止重构完成后业务逻辑重新回到页面。

**执行策略**：

1. 在架构门禁中增加简单静态检查：Qt 页面不能导入新的配置 authoring/domain 校验实现。
2. 新增页面必须提供窄依赖接口，不允许直接持有其他页面实例。
3. 新业务行为必须先添加 application/domain 测试，再接入 Qt。
4. 在开发文档中增加“视觉改动、业务改动、硬件改动”的分层规则。

**技术决定**：

- 用现有测试门禁维护边界，不引入新的运行时框架；
- 规则优先检查依赖方向和公共入口，不限制页面内部的普通布局代码；
- 对确需例外的代码使用显式登记，而不是静默绕过门禁。

**完成标准**：

- 后续新增字段有固定落点；
- 后续新增页面动作不会扩张 `PageContext`；
- “只改 UI”能够稳定使用 `--layer ui` 验证。

---

## 推荐执行顺序

建议按以下顺序连续推进，不要先做 Qt/QML 或大规模目录重命名：

```text
1 行为基线
2 领域词汇
3 ConfigurationDraft
4 ConfigurationAuthoringService
5 DeviceBindingService
6 ConfigurationViewModel
7 Qt Form Adapter
8 解耦 ConnectionPage
9 测量启动 Facade
10 测量公共状态绑定
11 静态布局与主题整理
12 测试行为化
13 删除兼容层
14 固化架构门禁
```

## 每一步的交付纪律

每完成一个步骤，都执行：

1. 运行该步骤涉及的纯 Python 测试；
2. UI 相关步骤运行：

   ```powershell
   & "D:\Anaconda\envs\Auto_test\python.exe" tools/run_tests.py --layer ui
   ```

3. 涉及真实页面接线时再运行：

   ```powershell
   & "D:\Anaconda\envs\Auto_test\python.exe" tools/run_tests.py --layer integration
   ```

4. 更新正式 API、内部模块登记和本计划的完成状态；
5. 一个步骤只解决一个边界问题，测试通过后再开始下一个步骤。

## 预期结果

重构完成后，典型改动应符合以下规则：

| 改动类型 | 主要修改位置 |
| --- | --- |
| 改颜色、间距、按钮排列 | `.ui`、`theme.py`、Qt 页面布局 |
| 改字段校验或设备绑定规则 | `domain` / `application` + 纯 Python 测试 |
| 新增配置字段 | `ConfigurationDraft`、authoring service、ViewModel adapter |
| 新增页面动作 | ViewModel/Application facade + 页面一个信号 |
| 改硬件连接流程 | `app`、`application`、`instrument`，页面只更新反馈 |
| 改结果展示 | 对应 ViewModel/Presenter 和 Qt 视图 |

核心验收标准是：**视觉调整不再触发业务层连锁修改，业务规则调整不再需要在多个 Qt 页面里复制逻辑。**
