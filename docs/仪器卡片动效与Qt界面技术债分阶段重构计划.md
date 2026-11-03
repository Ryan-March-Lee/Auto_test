# 仪器卡片动效与 Qt 界面技术债分阶段重构计划

## 目标

解决仪器发现页发牌/翻牌动画不可见、动画与页面布局耦合过深、真实卡片控件依赖运行时截图等问题，并为其他页面建立可复用的界面状态与过渡基础。

本计划遵循以下约束：

- 每一步只改变一个主要边界，完成后可以独立运行和验证。
- 先建立可观察、可复用的基础资产，再接入扫描业务流程。
- 动画不读取扫描线程、页面布局或真实交互卡片的内部状态。
- 不在重构期间增加新的复杂视觉效果；先保证动效稳定可见。
- 每一步都保留现有功能回退路径，避免一次性重写发现页。

## 当前问题摘要

重构前（步骤 1 之前）的发现页结果动画链路为：

```text
扫描线程完成
→ 等待牌堆完整播放一轮
→ 切换结果区布局
→ 创建并重新布局 InstrumentCard
→ grab() 截图
→ 隐藏真实卡片
→ DealRevealLayer 发牌和翻牌
→ 恢复真实卡片控件
```

主要技术债：

1. 动画时间轴依赖扫描结果、页头隐藏、滚动区和布局重排。
2. 动画正面来自 ``card.grab()``，不是稳定的卡片资产。
3. 动画层挂在结果区内部，需要不断处理父子坐标、滚动和裁剪。
4. 翻牌是二维宽度压缩模拟，正反面切换的视觉反馈较弱。
5. 发牌测试主要验证时间和最终状态，没有充分验证中间帧实际可见。
6. ``ConnectionPage``、``ConfigPage`` 的用户概念和代码命名不完全一致。
7. 其他页面虽然没有同等复杂的动画，但普遍通过 ``setVisible``、布局重排和局部样式直接驱动状态，暂时不适合直接叠加过渡动画。

## 目标架构

```text
设备结果模型
    ↓
卡片资产渲染器
    ├── 正面 pixmap
    └── 牌背 pixmap

页面状态机 ──→ 动画 Overlay ──→ 目标槽位
    ↓                 ↓
真实 InstrumentCard   发牌/翻牌画面
```

职责划分：

- ``DeviceDiscoveryPanel``：负责扫描、结果和交互状态，不负责绘制飞行中的牌。
- ``InstrumentCard``：负责最终可交互卡片，不作为动画截图来源。
- ``CardAsset``：负责正面、牌背、尺寸、圆角和阴影等稳定视觉资源。
- ``CardDealAnimation``：只接收起点、目标矩形和资产，负责发牌、翻牌、取消和完成。
- 页面级 Overlay：提供稳定的动画坐标系，不依赖 ``QScrollArea`` 内部滚动坐标。

# 阶段一：冻结现状并建立可观察性

## 步骤 1：记录现有状态机和视觉契约

**执行状态：已完成**。基线文档：[`仪器发现页状态与视觉契约.md`](仪器发现页状态与视觉契约.md)。该文档区分逻辑流程状态与现有 `boxState`，记录等待动画与扫描结果的同步条件、结果分支、控件可用性与用户可见行为。

### 执行策略

新增一份发现页状态说明，明确以下状态及转移：

```text
IDLE → SCANNING → RESULT_PENDING → DEALING → INTERACTIVE
                        │
                        ├─ completed 且设备数为 0 → 回到 IDLE 布局
                        ├─ failed                 → 显示失败说明（IDLE 布局）
                        └─ cancelled              → 显示取消说明（IDLE 布局）

任意未关闭状态 ──页面 shutdown──→ CLOSED
```

`ERROR`、`CANCELLED` 是 `RESULT_PENDING` 消费后的结果分支，不是与发牌并行的持久状态，也不新增 `boxState` 值。

同时记录当前用户可见行为：按钮位置、等待牌堆、结果出现时机、返回行为、空结果行为和失败行为；扫描设置可用性与“能否开始新扫描”分开记录。

### 技术决定

- 本步骤只写文档和状态命名，不修改运行逻辑。
- 后续代码以状态名替代“某个控件是否可见”作为设计依据。
- 暂不引入通用状态机框架，使用页面内部的明确枚举或常量即可。

### 完成标准

- 能从文档判断本次重构涉及的关键异步事件（扫描启动/进度/结果、等待周期结束、发牌完成、返回、页面关闭）到达时页面应处于哪个状态；不要求穷举所有信号顺序。
- 明确等待动画、扫描结果和发牌动画之间的依赖关系。
- 区分扫描设置的可用性与“能否开始新扫描”：前者由 worker 生命周期决定，后者由 pending 守卫和当前 `boxState` 共同决定。

### 工作量

小，预计 0.5 天以内。

## 步骤 2：增加动画运行时诊断信息

**执行状态：已完成**。诊断模块：`presentation/qt/animation_diagnostics.py`；发牌层的内部快照：`DealRevealLayer.diagnostics()`（步骤 7 后同一快照由 `CardDealAnimation.diagnostics()` 提供，字段不变）。日志写在项目的 `logs/pa_auto_test_*.log`，检索 `animation event=` 即可看到一个固定字段序列。

### 执行策略

在开发模式或日志中记录：

- 动画开始和结束时间。
- 起点坐标、目标矩形、卡片数量。
- Overlay 的可见性、尺寸和父控件。
- 当前动画阶段：``dealing``、``settling``、``flipping``。
- 取消原因和恢复结果。

### 技术决定

- 诊断信息使用日志或内部调试属性，不直接显示给最终用户。
- 不依赖截图来判断动画是否开始。
- 日志字段使用固定名称，方便后续复现“动画运行但不可见”的问题。

### 已实现的事件与字段

日志行形如：

```text
animation event=start card_count=6 deck_x=480.0 deck_y=620.0 phase=dealing ...
animation event=first_paint drawn_cards=3 first_paint_ms=12.0 ...
animation event=phase phase=settling ...
animation event=finish ended_at=...
animation event=cancel cancel_reason=results-cleared restore_result=hidden ...
```

固定字段：

| 字段 | 含义 |
| --- | --- |
| `event` | `start` / `skip` / `phase` / `first_paint` / `finish` / `cancel` |
| `started_at` / `ended_at` | 开始、结束的本地时间（ISO 8601） |
| `elapsed_ms` / `duration_ms` | 已走时长与整段时长 |
| `phase` | `dealing` / `settling` / `flipping` / `finished` / `idle` |
| `card_count` | 本次要发的卡片数 |
| `front_source` | 发牌正面的来源：`asset`（默认，卡片资产）或 `screenshot`（排障回退的旧截图） |
| `deck_x` / `deck_y` | 发牌起点（覆盖层坐标） |
| `target_rects` | 每张卡的落点 `(x,y,w,h)` |
| `overlay_visible` / `overlay_width` / `overlay_height` / `overlay_parent` | 覆盖层可见性、尺寸与父控件 |
| `paint_frames` / `first_paint_ms` / `drawn_cards` | 已绘制的帧数（每个 `paintEvent` 计一次）、首帧延迟、首帧里实际画到可见区域（覆盖层与卡片裁剪区之内）的牌面数 |
| `revealed_count` | 已翻开的卡片数 |
| `cancel_reason` / `restore_result` / `was_running` | 取消原因、取消后卡片是恢复显示还是随结果清空、取消时是否仍在播放 |
| `skip_reason` | 动画没有启动的原因（例如 `no-cards`） |

`CardDealAnimation.diagnostics()`（步骤 7 之前是 `DealRevealLayer.diagnostics()`，字段不变）运行中返回当前快照，完成或取消后返回那次动画收尾时的快照：`card_count`、`elapsed_ms`、`phase`、`revealed_count` 与 `event=finish` / `event=cancel` 同一时刻，不会出现“卡片已清空却还留着取消原因”。

`skip` 只由动画对象的 `skip(reason=...)` 记录一次（步骤 7 之前是 `DealRevealLayer.skip`），调用方只提供原因，不再各自拼日志。

排查顺序：

- 只有 `skip`、没有 `start`：动画没有启动；
- 有 `start`、没有 `first_paint`：动画启动了，但覆盖层没有绘制出来；
- `first_paint` 里 `overlay_visible=false`：画了，但覆盖层当时没显示（父控件隐藏、未加入窗口或被层级盖住）；
- 有 `first_paint` 但 `drawn_cards` 为 0：画了但画面是空的（贴图为空或全在可见区域之外）；
- 有 `start` 和 `first_paint`、`drawn_cards` 也大于 0 但用户仍看不到：问题在覆盖层之后的可见性、裁剪或层级，而不是时间轴。

面板取消发牌的既有入口分别写入 `results-cleared`（返回/清空结果）、`new-scan`（开始新扫描）、`cards-refilled`（换一批卡片）、`page-shutdown`（页面关闭）和 `surface-changed`（页面根容器尺寸、页面滚动或卡片区视口/滚动变化，步骤 6）；`overlay-attached` 只在动画进行中把覆盖层重挂到另一个宿主时出现。

### 完成标准

- 用户再次运行时，可以区分“动画没有启动”和“动画启动但没有绘制出来”。

### 工作量

小，预计 0.5 天以内。

# 阶段二：建立卡片资产边界

## 步骤 3：提取卡片数据模型和视觉输入

**执行状态：已完成**。数据对象：`presentation/qt/device_cards.py` 的 `InstrumentCardData`（只读 dataclass，只有字符串/路径/布尔值）；`InstrumentCard` 改为只由它构建，面板在 `_fill_cards` 里一次算定内容，`DeviceDiscoveryPanel.card_data(heading)` 返回这张卡片此刻的同一份快照。

### 执行策略

定义一个仅包含显示所需数据的轻量对象，例如：

```text
InstrumentCardData
├── heading
├── model
├── role
├── address
├── status
├── checked
└── picture_path
```

让 ``DeviceDiscoveryPanel`` 生成该对象，并把它同时交给真实卡片和资产渲染器。

### 技术决定

- 数据模型不依赖 Qt 控件。
- 选择状态仍由页面保存，动画资产只读取动画开始时的快照。
- 不在本步骤改变卡片外观。

### 已实现

`InstrumentCardData` 的字段与卡片上的位置一一对应：`heading`（结果里的设备名，面板按它
找卡片，卡片本身不画）、`code`（第一行缩写设备码）、`model`、`role`（测试角色的界面
文案，例如 `电源`，由面板把领域角色折叠成标签）、`address`、`status`、`checked`、
`picture_path`、`tooltip`、`checkable`。`caption()` 只在这里拼一次“型号·角色”，真实
卡片与之后的动画正面共用。

`DeviceDiscoveryPanel` 在 `_fill_cards` 里为每张卡片生成这份数据并交给 `InstrumentCard`：
卡片持有它作为“此刻显示的内容”，`set_checked` 与 `set_status` 用 `dataclasses.replace`
原子更新同一份快照（不另存一份选中状态或状态说明），不能应用的卡片在构造时就把快照
规整成实际显示的样子。`DeviceDiscoveryPanel.card_data(heading)` 原样返回这张卡片的快照，
面板不再另存一份：动画读到的就是卡片上写着、选中的东西。真实卡片通过
`InstrumentCard(data, tag=..., parent=...)` 构建，不再接收零散文字参数，也没有第二条
“从扫描结果拼卡片”的路径。

### 完成标准

- ``InstrumentCard`` 可以由 ``InstrumentCardData`` 构建。
- 动画模块不需要读取设备扫描 DTO 或调用卡片控件方法获取内容。

### 工作量

小，预计 0.5～1 天。

## 步骤 4：实现 CardAsset 和正面渲染器

**执行状态：已完成**。新增 `presentation/qt/card_assets.py`：`CardAsset` 提供 `front` / `back`、逻辑尺寸、`radius` 与 `shadow_margin`，由 `render_card_asset()` 从 `InstrumentCardData` 一次渲染，不需要 `QWidget.grab()`。正面与真实卡片共用同一份**内容、行位置和字体**：`presentation/qt/device_cards.py` 的 `card_row_plan()` 一次算定设备码/图片/状态说明/型号·角色/地址的位置（`CARD_*` 常量与 `card_code_rect()` / `card_picture_rect()` / `card_address_rect()` 是同一份几何的单行查询），真实卡片按它钉住每行高度、资产按它画字；字体取自 `presentation/qt/theme.py` 的 `card_code_font()` / `card_caption_font()` / `card_status_font()` / `card_address_font()` / `card_placeholder_font()`，这些函数读样式表里同一个字号令牌，因此同名同号同字重；图片落点仍由 `picture_target_rect()` 统一计算。选中、不可应用与空字段的底色、描边、留白也与样式表一致；状态说明按宽度折行后要占的高度由图片让出（真实卡片的状态说明都只有一行，图片因此始终 104px），行距与底部两行不动。渲染接口的尺寸只接受正整数（`card_size()`），屏幕像素比不是正数时按 1.0；位图是 `QPixmap`，因此约定在 GUI 线程、`Application` 创建之后调用。牌背是一张独立的深色卡背（渐变、斜纹、内框、中央菱形），暂不承接“查找设备”卡面的装饰动效。登记测试：`tests/ui/test_card_assets.py`（`CARD_WIDTH` / `CARD_MIN_WIDTH` / 175 三种宽度 × 九种内容的行位置等于行计划；两种宽度 × 八种内容在边框以内逐像素一致；尺寸契约、屏幕像素比兜底、元数据与位图一致、选中不推动行位置、长状态由图片让位、空字段、图片落点、高 DPI）。本步不改变面板现有的 `card.grab()` 路径，切换资产在步骤 5 完成。

### 执行策略

新增独立模块，例如：

```text
presentation/qt/card_assets.py
```

提供：

- ``CardAsset.front``
- ``CardAsset.back``
- 逻辑尺寸
- 圆角半径
- 阴影边距

正面由 ``InstrumentCardData`` 渲染，使用与真实卡片相同的图片选择和文字布局规则。

### 技术决定

- 初期使用 ``QPixmap`` 作为动画资产，避免动画期间依赖控件重绘。
- 正面渲染器和真实 ``InstrumentCard`` 共用数据和资源选择规则，但不要求共用同一个 ``paintEvent``。
- 暂不追求三维真实翻转，先保证正面和牌背稳定可见。

### 完成标准

- 不调用 ``QWidget.grab()`` 也能得到完整卡片正面。
- 正面资产和真实卡片在尺寸、文字和图片内容上保持一致。
- 增加资产尺寸、非空内容和高 DPI 的单元/渲染测试。

### 工作量

中小，预计 1 天左右。

## 步骤 5：让真实卡片和动画资产并行使用

**执行状态：已完成**。发牌正面改由 `presentation/qt/card_assets.py` 的
`render_card_asset()` 按卡片此刻的内容渲染（`CardAsset.front`），动画的 `start()`
（步骤 7 后为 `CardDealAnimation.start(items, ...)`）只接收每张卡的 `CardAsset`（随
`CardDealItem` 传入），绘制时贴 `asset.front`；面板在 `_start_deal_reveal` 里先落定
最终版面、再用 `_deal_assets()` 为每张卡渲染资产，随后用透明度效果藏起真实卡片。发牌不再
调用 `card.grab()`，也不依赖卡片控件是否显示过、是否完成重排。旧的截图路径只在
`device_discovery_panel._USE_SCREENSHOT_FRONTS = True` 时用于排障对照（面板常量，不接配置
和界面，也不作为第二条长期实现）；两种来源在诊断里记为 `front_source=asset|screenshot`，
`front_source` 已加入步骤 2 的固定字段。牌背当时仍取按钮卡面的纹样，是否改成
`CardAsset.back` 留给步骤 7 的独立动画对象统一决定（步骤 7 已完成：发牌背面改用
`CardAsset.back`）；本步不改变覆盖层位置与时间轴。资产与真实卡片的一致性
由 `tests/ui/test_card_assets.py` 逐像素比对，`tests/ui/test_scan_card.py` 验证正面确实来自
资产、诊断记录来源，`tests/integration/device_discovery/test_panel_presentation.py` 在
`InstrumentCard.grab` 被替换成抛错时仍能正常发牌。

资产与卡片**按位置一一对应**：`_deal_assets(cards, front_source)` 遍历的正是随后交给
`start()` 的那份卡片列表，每张卡读自己的 `data` 快照。不按 `heading` 回查——卡片标题
不保证唯一（同型号、同缩写码或未登记设备都可能重名），用标题当关联键会把某一台的画面
套到另一台上。动画的 `start()` 因此把这份对应关系当成契约：`len(assets)` 与
`len(cards)` 不等、或 `front_source` 不是 `asset` / `screenshot` 时直接抛 `ValueError`
（没有卡片仍走 `skip(reason="no-cards")`）。步骤 7 后这条对应关系由 `CardDealItem`
承载：每张卡一个 item，资产、落点与 `index` 一起传入，`start()` 不再接收单独的
cards/assets 列表。回退来源只由 `_deal_front_source()` 决定一次
并同时用于渲染与诊断，字段与实际画面不会不一致。回退路径的集成测试除了断言
`front_source=screenshot`，还断言 `InstrumentCard.grab` 为每张卡片各调用一次；默认路径
断言它一次也没被调用。

### 执行策略

在发现页结果生成时，同时生成：

```text
InstrumentCardData
├── InstrumentCard        # 最终交互控件
└── CardAsset             # 动画使用
```

暂时保留旧的 ``grab()`` 路径作为回退，但默认改用 ``CardAsset.front``。

### 技术决定

- 不在本步骤改变动画层的位置和时间轴。
- 先验证“资产替代截图”本身可行，再处理 Overlay 和状态机。
- 回退路径只用于排障，不能成为长期双实现。

### 完成标准

- 发牌动画不再依赖 ``card.grab()``。
- 现有集成测试仍通过。
- 能单独验证资产画面与真实卡片画面的一致性。

### 工作量

小，预计 0.5 天以内。

# 阶段三：拆出独立动画舞台

## 步骤 6：建立页面级 AnimationOverlay

**执行状态：已完成**。新增 `presentation/qt/animation_overlay.py` 的
`AnimationOverlay`：它挂在连接页（`InstrumentSetupPage`/`ConnectionPage`）新建的根容器
`overlay_host` 上，与普通内容（`.ui` 骨架，含滚动区与发现面板）同级，不进任何布局、
`WA_TransparentForMouseEvents`、`NoFocus`，几何始终等于宿主矩形。页面结构因此是
`ConnectionPage → overlay_host → {normal_content, animation_overlay}`；发牌层被重挂到
覆盖层里（`DeviceDiscoveryPanel.attach_animation_overlay()`），不再是 `results_box` 的
子控件，`QScrollArea` 的滚动与结果区内部重排都不再参与动画坐标。

`DealRevealLayer.start()` 新增 `target_rects` / `clip_rect`（步骤 7 后合并为
`CardDealAnimation.start()` 的 `clip_rect` 参数：`target_rects` 随每个
`CardDealItem.target_rect` 传入）：面板在动画开始前把每张卡的
落点和卡片区裁剪矩形一次性换算到覆盖层坐标（`_deal_target_rect()` /
`_deal_clip_rect()`），动画期间不再逐帧追踪卡片控件。覆盖层与卡片分属页面根容器的两个
分支，`QWidget.mapTo()` 只对祖先关系可靠，因此换算统一走
`animation_overlay.widget_rect_in()`（经屏幕坐标中转），窗口在桌面上的位置不会混进落点。
落点或资产份数与卡片数不等（步骤 7 后是 item 数、`index` 与卡片对不上）时直接抛错，
不会退回“按控件现取”；面板在抛出后把卡片恢复原样，不会留下透明且不可点的半状态。

页面根容器尺寸变化（`AnimationOverlay.resized`）或页面滚动条位置变化时，连接页调用
`DeviceDiscoveryPanel.cancel_deal_reveal(reveal=True, reason="surface-changed")`：取消
这一次发牌、恢复卡片为最终版面并写一条固定原因的诊断，而不是在动画中途追踪复杂布局。
卡片区自己的视口尺寸与滚动条位置由面板看护：先重算一次当前落点与冻结值比较，卡片真的
挪了才按同一原因取消；只是可见区域变了（布局落定后滚动条出现/消失、首帧排版微调）就
用 `CardDealAnimation.refresh_clip_rect()`（步骤 7 前为 `DealRevealLayer`）换掉裁剪遮罩，落点保持冻结。重新开始动画留给
步骤 8 的页面状态机统一决定。页头文字淡出只改透明度、不改版面，因此不会移动已经开始的
坐标；页头整块收放本身发生在发牌开始前，若发牌中因状态切换发生，则同样按
`surface-changed` 收尾。

完成标准对应验证：`tests/ui/test_animation_overlay.py` 验证覆盖层铺满宿主、不进布局、
不吃鼠标/不占焦点、跟随尺寸变化并发出通知、压在普通内容之上，以及跨分支换算
（`widget_rect_in()`）在窗口挪位后仍给出覆盖层坐标；`tests/ui/test_scan_card.py` 新增
落点在开始时冻结（含窗口挪位）、覆盖层坐标由调用方给出、裁剪遮罩可单独刷新、落点/资产
份数不符即报错四组用例（步骤 7 后这些用例随动画一起迁到
`tests/ui/test_card_deal_animation.py`）；`tests/integration/device_discovery/test_connection_page.py`
验证覆盖层不在滚动区/结果区里、落点等于卡片在页面根容器里的位置、窗口挪位不改变落点、
淡出页头文字不移动已开始的坐标、页面缩放与卡片区重排分别以 `surface-changed` 取消并
恢复卡片。

### 执行策略

在仪器发现页的稳定根容器上增加一个 Overlay，不放入卡片滚动区布局。Overlay 只负责覆盖绘制和鼠标穿透。

目标结构：

```text
InstrumentSetupPage
└── overlay_host
    ├── normal_content
    └── animation_overlay
```

页面在动画开始前一次性计算所有目标矩形，并转换到 Overlay 坐标系。

### 技术决定

- 动画期间不通过 ``mapTo`` 每帧追踪卡片控件。
- 不让 Overlay 成为 ``QScrollArea`` 的子控件。
- 窗口缩放或滚动时，优先取消并重新开始动画，不在动画中途追踪复杂布局。

### 完成标准

- Overlay 不依赖 ``results_box`` 的内部布局细节。
- 动画起点和目标位置在开始时可记录、可测试。
- 页面头部隐藏不会改变已经开始的动画坐标。

### 工作量

中小，预计 1 天左右。

## 步骤 7：把发牌/翻牌封装成独立 CardDealAnimation

**执行状态：已完成**。新增 `presentation/qt/card_deal_animation.py`：`CardDealItem(asset, target_rect, index)` 是只读的输入项，`CardDealAnimation` 只保留时间轴与绘制，接口为 `start(items, deck_origin)` / `cancel(reveal=True)` / `finish()` / `is_running()` / `finished`；`start()` 另接两个关键字参数 `clip_rect`（步骤 6 的裁剪遮罩）与 `front_source`（步骤 2 的诊断来源），默认值不影响窄接口。动画按 `index` 排序、翻到哪张就发 `card_revealed(index)`，由面板撤掉那张卡片的透明度效果；`finished` 只表示整段结束，面板据此恢复交互与状态行。动画本身不再持有任何 `QWidget`：`DealRevealLayer` 里的 `self._cards`、`setGraphicsEffect(None)` 与 `QWidget.grab()` 调用全部移出。

发牌背面改为 `CardAsset.back`（步骤 4 已经渲染好的稳定资产），不再是按钮卡面的截图/纹样；`scan_card.py` 因此只保留“查找设备”按钮与扫描等待牌堆，`_render_deal_back`、发牌常量与 `DealRevealLayer` 一并迁走。面板字段仍叫 `deal_layer`（步骤 13 再统一命名），但类型换成 `CardDealAnimation`。

输入校验在改动运行状态之前完成：`index` 必须是 `0..N-1` 的连续编号（面板直接拿它当卡片列表下标，重复、负数或缺口都会恢复错卡片），`front_source` 只接受 `asset` / `screenshot`，项目必须是 `CardDealItem`；任何一项不合格都直接抛错，上一次动画（若有）保持原样，不会留下“卡片已藏起、计时器还在走”的半状态。上一次发牌还在播时 `start()` 直接抛 `RuntimeError`（先 `cancel()` / `finish()`），避免静默丢掉上一组卡片的恢复信号；空输入走 `skip()`，它先把还在播的一次按完成路径收尾（每张 `card_revealed` + `finished`），再记录“这一次没有牌”，因此空结果不会留下透明卡片。`finish()` 与 `skip()` 都幂等：已经收尾过的一次不会重复发 `finished`。`cancel()` 会停表、隐藏覆盖层并清空本次输入；`reveal=True` 与自然完成走同一条 `card_revealed` 路径，因此中断、返回和关闭页面都不会留下对卡片的悬空引用或未撤掉的透明度效果。面板把“藏卡片 + 禁用卡片区 + `start()`”放在同一个保护范围里，任何启动异常都经 `_abort_deal_reveal()` 撤销效果、恢复卡片区后原样抛出；`finished` 时再兜底清理一次残留效果。

验证：`tests/ui/test_card_deal_animation.py` 覆盖独立播放（无扫描线程、无发现页、item 里没有控件）、资产正反面、`card_revealed` / `finished` 时序、取消与完成后的清理、空输入先收尾再 `skip`、运行中重复 `start()` 被拒绝、稀疏 `index` 被拒绝、重复 `finish()` 只发一次 `finished`、输入校验、落点冻结、裁剪遮罩刷新、固定诊断字段与首帧计数；`tests/integration/device_discovery/` 的发牌、取消、返回与页面关闭用例继续通过，并新增“启动发牌抛错后卡片仍可见可点”的用例。诊断字段与步骤 2 保持不变。

### 执行策略

将 ``DealRevealLayer`` 的时间轴和绘制逻辑迁移到独立对象，接口保持窄化：

```text
start(items, deck_origin)
cancel(reveal=True)
finish()
is_running()
finished
```

每个 item 只包含：

```text
asset
target_rect
index
```

### 技术决定

- 动画对象不持有 ``InstrumentCard``。
- 动画对象不访问扫描结果、不读页面状态、不修改页面布局。
- 先保留当前二维翻牌算法，后续再单独评估真实三维效果。
- 统一取消、完成和异常清理行为。

### 完成标准

- 可以在没有扫描线程、没有发现页的情况下独立播放演示动画。
- 动画完成后由页面统一显示真实卡片。
- 中断、返回和关闭页面不会留下定时器、透明度效果或悬空引用。

### 工作量

中等但可控，预计 1～1.5 天。

## 步骤 8：重接发现页状态机

**执行状态：已完成**。`presentation/qt/device_discovery_panel.py` 现在有显式的 `DiscoveryPhase`（`IDLE` / `SCANNING` / `RESULT_PENDING` / `DEALING` / `INTERACTIVE` / `CLOSED`）：`_set_phase()` 是唯一的转移入口，`_render_phase()` 是唯一的渲染入口，`results_box.boxState` 只由当前状态推导（扫描/等待 → `busy`，发牌/可交互 → `results`，其余 → `idle`）。扫描结果不再各自调用 `setVisible` 驱动发牌：worker 的结果、失败与取消都先挂进 `_pending_outcome` 并进入 `RESULT_PENDING`，`_consume_pending_outcome()` 再按结论分流——有设备时先切到 `DEALING`、在可见的卡片区里生成卡片与资产，再由 `CardDealAnimation` 播放；空结果跳过发牌（`deal_layer.skip(reason="no-cards")`），直接回 `IDLE` 并显示“发现 0 台设备 …”；失败与取消回 `IDLE`（`outcome=failed` / `cancelled`）并保留状态行说明。

等待牌堆仍是扫描期间独立的 loading 动效。产品要求的“结果到达时先播完这一轮”由模块常量 `WAIT_FOR_FULL_CYCLE` 明确表达：为真时结果停在 `RESULT_PENDING`，等 `ScanWaitStack.cycle_finished`（牌堆复原）再消费；为假时扫描结论一到就直接进入 `DEALING` / 空结果，不再依赖等待周期。真实卡片只在 `INTERACTIVE` 可交互：`DEALING` 期间卡片区禁用、状态行隐藏，翻完最后一张才恢复；发牌启动抛错时面板先恢复卡片与卡片区，再把状态推进到 `INTERACTIVE`，不留下停在发牌中的半状态。面板同时提供 `phase()` / `is_interactive()` 与 `phase_changed` 信号，每次转移写一条固定字段日志 `animation event=discovery_phase previous=... phase=... box_state=... pending=... cards=... outcome=...`，成功、空结果、失败、取消、返回与关闭都能按同一条序列复现。

状态机的异步边界也在这一步收紧：`_set_phase` 按 `_PHASE_TRANSITIONS` 校验跳转，`CLOSED` 是终态；每次 `request_scan` 递增扫描代次并交给 `DiscoveryWorker`，`clear_results` / `shutdown` 作废当前代次并请求协作取消，因此旧扫描的结果、进度、失败与取消只会释放线程，不会把结果画回页面；`QThread.start()` 失败时回到 `IDLE` 并报告原因（异常继续传播），旧线程的 `finished` 也不会动新的扫描。

验证：`tests/integration/device_discovery/test_discovery_phase.py` 覆盖成功路径（`scanning → result-pending → dealing → interactive`）、空结果跳过发牌、失败回 `IDLE`、返回/关闭状态、显式关闭 `WAIT_FOR_FULL_CYCLE` 后结果直接发牌（成功、空结果、失败三条分支）、发牌启动失败仍可交互、扫描线程启动失败回 `IDLE`、未定义跳转被拒绝、清空结果作废进行中/挂起的扫描、关闭后迟到信号不改变状态，以及状态日志字段；`test_panel_presentation.py` / `test_connection_page.py` / `test_scan_lifecycle.py` 的既有可见行为保持不变。

### 执行策略

将扫描流程改成：

```text
扫描完成
→ 生成 CardAsset 和目标槽位
→ 进入 DEALING
→ Overlay 播放
→ 进入 INTERACTIVE
```

等待牌堆动画继续作为扫描中的独立 loading 动效。扫描结果不再直接通过多个 ``setVisible`` 调用驱动发牌。

### 技术决定

- 等待动画和发牌动画保持两个独立组件。
- 发牌不再要求等待动画完成一整轮才能启动；如产品仍需要完整一轮，作为明确的状态策略保留，而不是隐含在多个回调中（`WAIT_FOR_FULL_CYCLE`）。
- 结果为空时跳过发牌，直接进入空结果状态。
- 状态转移只走 `_set_phase`，并由允许转移表校验；`CLOSED` 是终态。
- 扫描结果按代次归属：清空、重扫、关闭都会作废旧扫描，迟到信号只释放资源。
- 扫描线程启动失败回到 `IDLE` 并提示，异常不吞；发牌启动失败让结果立即可用（`INTERACTIVE`），异常不吞。

### 完成标准

- 动画流程不依赖扫描速度。
- 页面状态可以通过日志和测试清楚复现。
- 真实卡片只在翻牌完成后进入可交互状态。

### 工作量

中等，预计 1 天左右。

# 阶段四：补齐视觉验证和回归保护

## 步骤 9：补充动效中间帧测试

### 执行策略

为卡片资产和发牌动画增加固定时间点的渲染测试：

1. 第一张牌刚离开牌堆。
2. 多张牌同时飞行。
3. 所有牌落位但尚未翻牌。
4. 第一张牌翻到中间。
5. 最后一张牌翻完。
6. 取消动画后恢复状态。

### 技术决定

- 优先验证非空绘制、位置变化、正反面变化和最终可见性。
- 不把所有测试都做成逐像素完全相等，降低抗锯齿和平台差异造成的脆弱性。
- 保留少量关键截图作为视觉基准。

### 完成标准

- 测试能够发现“动画计时器在运行但覆盖层是空的”这类问题。
- 能覆盖无设备、单设备、多设备、窄窗口和取消场景。

### 工作量

中小，预计 1 天左右。

## 步骤 10：增加一次真实窗口手动验收

### 执行策略

在 Windows 实际窗口环境中验收：

- 1366×768 窗口。
- 常规 DPI 和高 DPI。
- 模拟扫描快速返回。
- 扫描失败。
- 扫描结果为空。
- 发牌期间点击返回。
- 发牌期间调整窗口大小。

### 技术决定

- ``offscreen`` 测试继续保留，用于逻辑和渲染回归。
- 真实窗口验收作为交付前固定步骤，不强行自动化所有系统级行为。
- 如果窗口缩放会破坏目标位置，采用取消并重启动画的确定策略。

### 完成标准

- 用户能够稳定看到等待、发牌和翻牌三个阶段。
- 不出现空白覆盖层、卡片闪现、卡片重复、残留定时器或不可点击状态。

### 工作量

小，预计 0.5 天以内。

# 阶段五：清理其他页面的共同技术债

## 步骤 11：统一页面状态渲染入口

### 执行策略

选择一个低风险页面先试点，例如 ``RunHistoryPage`` 或 ``ConfigPage``，将分散的：

```text
setText
setVisible
setEnabled
setProperty
```

集中到一个 ``render_state()`` 或同等职责的方法中。

### 技术决定

- 不引入大型 MVVM 或状态管理框架。
- 状态对象保持简单、不可变或只读。
- 先改一个页面，验证模式后再推广。

### 完成标准

- 页面状态变化有单一渲染入口。
- 后续增加淡入、禁用或局部过渡时，不需要追踪多个回调。

### 工作量

小，预计 0.5～1 天。

## 步骤 12：建立通用过渡和 Overlay 约定

### 执行策略

根据仪器卡片重构的实际结果，提取最小公共能力：

- Overlay 生命周期。
- 动画取消协议。
- 完成信号。
- 页面隐藏时自动停止。
- 窗口变化时的处理策略。

### 技术决定

- 只抽取已经出现两次以上的稳定模式。
- 不提前建立通用“万能动画框架”。
- 欢迎页的按钮动画暂时保持不动，避免无收益重写。

### 完成标准

- 新增页面动效可以复用 Overlay 和生命周期协议。
- 不再由每个页面自行处理定时器清理和覆盖层层级。

### 工作量

小，预计 0.5～1 天。

## 步骤 13：逐步统一页面命名和模块边界

### 执行策略

先补充公开接口和文档中的领域命名，再在不影响外部调用的情况下逐步引入别名或迁移：

```text
ConnectionPage → InstrumentSetupPage
DeviceDiscoveryPanel → InstrumentDiscoveryPanel
ConfigPage → ConfigurationPage
```

### 技术决定

- 不在动效重构的同一个提交中大范围改名。
- 先使用新名称作为文档和新接口的首选，旧名称保留兼容期。
- 每次只迁移一个模块，并运行完整 UI/集成测试。

### 完成标准

- 用户概念、页面名称和代码模块逐渐一致。
- 新功能不再继续使用含义模糊的旧命名。

### 工作量

每次小步迁移，预计每个模块 0.5 天以内。

# 验证策略

## 每一步的最小验证

- 资产或绘制改动：运行 UI 测试和资源预算检查。
- 发现页 wiring 改动：运行 integration 层测试。
- 页面状态或公共主题改动：运行对应 UI 测试，并检查静态质量门禁。
- 阶段性交付前：运行完整测试、``launcher.py --check`` 和 ``launcher.py --validate-config``。

## 推荐交付顺序

```text
步骤 1～2：可观察性
步骤 3～5：卡片资产
步骤 6～8：独立动画舞台与发现页接入
步骤 9～10：视觉验证
步骤 11～13：推广到其他页面并清理命名
```

## 不纳入本轮重构的内容

- 不重写欢迎页已经稳定的悬停动画。
- 不引入完整的第三方动画框架。
- 不把所有页面一次性改成同一种布局技术。
- 不在没有视觉验证的情况下继续增加扫描牌堆的复杂装饰。

## 预计总工作量

按小步执行，核心仪器卡片重构约 6～8 个开发日；包含测试、真实窗口验收和首个其他页面的状态渲染试点，约 8～10 个开发日。每一步均可独立提交和回归，不要求一次完成全部阶段。
