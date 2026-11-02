# GUI 改动分层规则

本文是 [GUI 业务逻辑重构计划](GUI业务逻辑重构计划.md) 步骤 14“建立长期改动规则”的交付物。
它的目的不是增加流程，而是让重构后的边界长期可执行：**视觉调整不触发业务层连锁修改，
业务规则调整不需要在多个 Qt 页面里复制逻辑。**

规则由 `tests/test_presentation_boundaries.py` 的 `LongTermChangeBoundaryTests` 静态门禁固定；
文档与测试不一致时，以门禁为准并同时修正两者。

## 1. 先判断改动属于哪一层

| 改动类型 | 主要修改位置 | 测试落点 | 建议验证 | 不要改 |
| --- | --- | --- | --- | --- |
| 视觉改动（颜色、间距、字号、按钮排列、留白、静态布局） | `.ui`、`presentation/qt/theme.py`、Qt 页面的布局与样式代码 | `tests/ui` 中与页面外观/交互有关的行为测试 | `tools/run_tests.py --layer ui` | `application` / `domain` 的 DTO、端口、规则与测试 |
| 业务改动（字段校验、设备绑定、供电角色、配置编写、运行契约） | `domain` 与 `application`；页面只通过 ViewModel/表单 Adapter 消费结果 | 先加 `tests/test_*.py` 的 application/domain 用例，再接 `tests/ui`/`tests/integration` | `tools/run_tests.py`（涉及真实接线时先 `--layer integration`） | 页面里的第二份判据、控件名驱动的业务分支 |
| 硬件改动（VISA 连接、仪器动作、供电与安全清理、测量装配） | `app`、`application`、`instrument`；页面只更新反馈 | 仿真/单元测试优先；真实设备只在获授权时跑 `tests/hardware` | 全量离线测试；硬件冒烟按 `AGENTS.md` 的授权流程单独执行 | 在 Qt 页面里直接打开 VISA 资源或组装测量对象 |

判断顺序：先问“这条规则在纯 Python 里成立吗”。成立就属于业务改动，先落 `domain`/`application`；
只有外观、控件状态与文案映射才属于视觉改动。拿不准或改动了 DTO/端口签名时，按业务改动处理并跑全量。

## 2. 新增内容的固定落点

| 新增内容 | 第一落点 | 后续接线 | 禁止 |
| --- | --- | --- | --- |
| 配置字段 | `application.configuration_draft.ConfigurationDraft` + `domain` 模型/规则 | `application.configuration_authoring`、`ConfigurationViewModel`、`config_form_adapter` / `config_projection`、页面控件 | 只在页面字典里加键；页面自己解释字段语义 |
| 字段校验或设备绑定规则 | `domain` 规则函数 + `application` 用例/服务 | authoring/ViewModel 返回结构化 `ValidationResult`，页面只展示消息 | 在 `presentation/qt` 复制判据或常量清单 |
| 页面状态（选中项、已加载结果、频率浏览、按钮可用性） | 对应 ViewModel / Presenter | 页面只读状态、发意图信号 | 页面自己缓存业务状态或跨页共享可变字典 |
| 页面动作 | `ConfigurationViewModel` / application facade 的一个用例入口 | 页面新增一个信号并连接到该入口 | 为单个动作扩张 `PageContext` |
| 结果展示 | 对应 ViewModel/Presenter + Qt 视图 | 只读 DTO | 页面重新计算业务结果或报告内容 |
| 新页面 | 实现 `PageProtocol` 的页面类（统一以 `Page` 结尾），窄依赖通过关键字参数注入 | 在 `presentation/qt/pages.py` 登记顺序，由 `main_window.py` 装配 | 页面构造函数接收另一个页面实例、`*args`/`**kwargs`；页面导入其他页面类型 |

`PageContext` 是三个测量页共享的窄组合接口，已由门禁冻结字段清单。它不是“页面动作总线”：
新增动作应经 ViewModel/Application facade 完成，页面用自己的一个信号表达意图。确需调整
`PageContext` 时，先修改本文说明理由，再更新 `PAGE_CONTEXT_FIELDS` 基线，评审通过后才改代码。

## 3. 新业务行为先测试、后接 Qt

下面是**开发顺序约定**，由评审把关；门禁不做“提交先后”这类无法从代码快照判断的检查。

1. 在 `tests/test_*.py` 为 `domain`/`application` 行为添加可复现用例，覆盖成功、失败和边界。
2. 在 `tests/ui` 只验证表现层行为：控件可见性、文案、页面动作是否把意图交给 mock/端口。
   纯业务判据不在页面测试里重复断言。
3. 需要真实窗口与真实后端时，在 `tests/integration` 增加接线用例；`tests/ui` 不得导入
   `app`、`infrastructure` 或硬件实现。
4. 页面只展示 application 边界返回的结构化结果；用户可修正的问题不是异常，程序缺陷不得被
   改写成“配置有问题”的提示。

“只改 UI”应能稳定地只跑 `--layer ui`：如果一次视觉改动不得不修改 DTO、端口或业务测试，
说明改动已经越过表现层边界，按业务改动重新评估，而不是扩大 UI 门禁的豁免。

## 4. 门禁与例外

`LongTermChangeBoundaryTests` 用静态语法树检查固定四件事：

- **配置业务规则不回流 Qt**：`application.configuration_*`、`application.device_binding` 与
  `domain.configuration.*` 是门禁的模块族，新增模块自动纳入，不需要手工登记模块名。
  存量导入按“文件 -> 模块 -> 名称”精确登记：新增依赖会失败；删除依赖后必须同步删除登记。
  只有页面边界模块（`application.configuration_view_model`、`application.configuration_editing`、
  `domain.configuration.types`）可以直接导入——它们只表达状态与结构化结果，规则仍留在
  实现模块里。字面量 `importlib.import_module(...)` 同样会被识别。
- **页面不持有其他页面**：只有 Qt 根目录的 `pages.py`（注册表）与 `main_window.py`（装配根）
  可以导入页面类型；豁免按相对路径判定，子目录同名模块不继承。其他页面只能依赖端口、
  ViewModel、回调或信号。页面类统一以 `Page` 结尾，共享 mixin 不使用该后缀。
- **页面构造只收窄依赖**：页面构造函数不接受 `*args` / `**kwargs`，也不接受页面类型的
  注解、默认值或形如 `config_page` / `page` 的参数；端口、ViewModel、回调与工厂是允许的
  依赖形态。
- **`PageContext` 不随页面动作扩张**：字段清单、冻结属性与“回调或共享 controller”的注解
  契约由门禁固定。

门禁能可靠判断的只有代码里的依赖方向与构造签名。运行时动态组装——`getattr` 取页面、
把页面放进通用容器再传参、工厂内部回读另一个页面——不追踪，属于评审责任；发现这类写法
按“页面持有其他页面”处理。

例外必须显式登记，不允许用同名转发、放宽断言或改宽基线绕过：

1. 先说明为什么规则不能留在 `application`/`domain`，以及页面需要的最小接口是什么。
2. 修改本文的规则与理由，再更新门禁里的基线，保持“只减不增”的审查方向。
3. 同步更新 `docs/正式API目录清单.md`（涉及公共 API 时）和 `docs/配置领域词汇与模块归属.md`
   （涉及配置规则归属时）。

## 5. 验证命令

```powershell
# 纯视觉/表现层改动
& "D:\Anaconda\envs\Auto_test\python.exe" tools/run_tests.py --layer ui

# 表现层 + 真实窗口/后端接线
& "D:\Anaconda\envs\Auto_test\python.exe" tools/run_tests.py --layer integration

# 业务、硬件、依赖或交付前
& "D:\Anaconda\envs\Auto_test\python.exe" tools/run_tests.py
```

所有范围都会先跑仓库门禁（包括 `tests.test_presentation_boundaries`）。硬件冒烟只在
`HARDWARE_SMOKE_ENABLED=1`、配置与授权条件全部满足时运行，不能为了“让改动跑通”自行设置确认开关。
