# PA自动测试系统 GUI版本

## 概述

这是一个基于PySide6的功放自动测试系统GUI版本，将原有的命令行测试脚本转换为用户友好的图形界面应用。

## 主要功能

### 1. 仪器配置和连接
- **设备发现（MVP）**: 配置页“获取设备”按网段扫描并读取每台设备的 `*IDN?`，把结果列在表格中；已登记设备按角色显示（电源显示冻结的短标签，如 `DP8B1519（DP832A，电源）`；信号源与频谱仪显示 `型号（短标签）`，如 `SMW200A（5462，信号发生器）`——短标签由序列号派生、与角色无关，因此界面上不再出现 `型号-序号` 这类资产名后缀），未在设备表登记的显示为未登记，身份读不出来的标记为无法识别。勾选本次测试所需设备后，可一键把当前地址与设备身份（序列号）写入配置；下次扫描即使 IP 变化也会按 `厂商:序列号` 自动预选。模拟扫描得到的绑定带 `source: simulated`，不能用于创建真实运行（可用“清除设备绑定”解除）
- **仪表配置（独立页面）**: 选好测试模式后先进入整屏的“仪表配置”页（不占标签栏，页面标题就是“仪表配置”）：扫描网段、勾选并应用本次测试所需的设备，框外右下角的“连接仪器”连接本次应用的仪器，点“下一步”才进入“仪器配置”页配置测试参数。连接只通过设备发现结果勾选并应用，两页都不提供手工填写 VISA 地址或逐台勾选启用的入口。连接只来自已应用的设备绑定，清除绑定或应用新一批设备时，旧地址会随之失效；只有地址没有绑定的旧配置在加载时一次性迁移，并在面板上标记为“未扫描确认”
- **测试参数配置**: 设置测试频率、功率范围、压缩点等参数
- **自动连接**: 一键连接所有测试仪器
- **配置保存**: 保存当前配置到config.json文件

### 2. 线损测量
- **连接引导**: 提供详细的连接说明和示意图
- **两步测量**: 
  - 路径1: 信号源 → 线缆① → 衰减器 → 线缆② → 频谱仪
  - 路径2: 信号源 → 线缆① → 线缆③ → 线缆④ → 衰减器 → 线缆② → 频谱仪
- **自动计算**: 自动计算各线缆的损耗值
- **结果显示**: 以表格形式显示所有频率点的线损结果

### 3. 驱动功放映射
- **连接引导**: 显示驱动功放测试连接图
- **功率映射**: 测量信号源输入功率与驱动功放输出功率的映射关系
- **实时可视化**: 在测量过程中实时显示功率映射曲线
- **数据保存**: 自动保存映射数据供后续主功放测试使用

### 4. 主功放测试
- **完整测试**: 支持有驱动和无驱动两种模式
- **安全保护**: 设置DUT最大输入功率保护，防止器件损坏
- **压缩点检测**: 自动检测1dB/3dB/5dB压缩点
- **实时监控**: 
  - 输入vs输出功率曲线
  - 输出功率vs增益曲线
  - 输出功率vs效率曲线
  - DC功耗监控
- **紧急停止**: 提供紧急停止功能确保测试安全

### 5. 数据可视化
- **多频率对比**: 同时显示多个频率点的性能曲线
- **饱和特性**: 分析各频率点的饱和输出功率、增益和效率
- **交互式图表**: 支持缩放、平移等交互操作
- **报告副本**: 完整报告已在测试完成时自动归档；这里可对当前已加载的历史结果生成报告副本（报告内容取自该结果归档时的配置快照与测量时间；重新生成失败产物请用“历史运行”页）

### 6. 历史运行与结果管理
- **自动归档**: 测试完成后正式结果与 CSV/测试报告自动保存到本次运行目录，无需手动导出
- **运行状态**: 显示每次运行的阶段进度、正式结果与交付产物状态（已完成/失败/不适用）
- **失败重试**: 对失败的 CSV 或测试报告一键重新生成，写回原运行目录
- **导出副本**: 需要另外一份文件时，可按 JSON、CSV、测试报告格式导出副本；只导出所选结果所属运行的结果，不可用（manifest 损坏）的运行不会被导出

> 正常测试流程不需要打开任何导出页面：正式结果与交付产物在测试完成时自动归档。手动
> 导出只用于生成副本、重新生成失败产物与历史管理，不是保存结果的必经步骤。

### 7. 实时日志
- **测量进度**: 实时显示测量进度条
- **状态日志**: 显示详细的测量状态和错误信息
- **时间戳**: 所有日志条目都包含时间戳
- **显示范围**: 进度与日志面板在工作区右侧，只在测量与结果页出现；“仪器配置”页专注于配置与连接仪器，不显示该面板（日志仍然照常记录）

## 技术特性

### 多线程设计
- **主线程隔离**: 所有耗时的测量任务都在独立的工作线程中运行
- **UI响应**: 确保用户界面始终保持响应状态
- **安全停止**: 支持安全的线程停止机制

### 实时数据更新
- **进度回调**: 实时更新测量进度
- **数据流**: 支持实时数据流更新图表
- **状态同步**: 工作线程与UI线程的状态同步

### 用户体验
- **连接图示**: 提供清晰的连接示意图
- **确认对话框**: 在关键操作前进行确认
- **错误处理**: 完善的错误处理和用户提示
- **现代UI**: 采用Fusion样式和自定义样式表

## 安装和使用

### 环境要求
```bash
conda create -n Auto_test python=3.11
conda activate Auto_test
```

已验证的项目运行环境：

- Conda 环境：`Auto_test`
- Python：3.11（只支持 3.11.x，其他版本会被启动器和测试入口拒绝）
- 环境路径：`D:\Anaconda\envs\Auto_test`

`VISA_demo` 是历史文档中引用的环境名，当前机器上不存在。请使用 `Auto_test`，不要在 `base` 环境中安装或运行本项目依赖。

### 必需的Python包

主系统直接依赖固定在 `requirements.txt`，完整版本（含传递依赖）由
`requirements.lock.txt` 锁定。先把新环境的 `python.exe` 写入项目根目录 `.env` 的
`AUTO_TEST_PYTHON`（复制 `.env.example` 后填写），再用同一个解释器安装：

```powershell
& $env:AUTO_TEST_PYTHON -m pip install -r requirements.lock.txt
& $env:AUTO_TEST_PYTHON tools/export_dependency_lock.py --check
```

- PySide6 == 6.11.2
- matplotlib == 3.11.2
- numpy == 2.4.6
- pandas == 3.0.6
- seaborn == 0.13.2
- PyVISA == 1.16.2

依赖变更后不要手工编辑锁文件，也不要用裸 `pip freeze` 覆盖：

```powershell
& $env:AUTO_TEST_PYTHON tools/export_dependency_lock.py
```

依赖、解释器和命令口径的完整约定见[依赖与运行时契约](docs/依赖与运行时契约.md)。
`DPD_auto_test` 的可选依赖（TensorFlow、Jupyter 等）在 `requirements-dpd.txt`，
与主系统隔离，主系统启动与三类测量都不需要安装它们。

### 可选能力边界

- `format_convert.csv_to_MDIF` 是独立命令行工具，输入和输出路径由调用者明确指定。
- `format_convert.svg_to_png_transparent` 仅在调用时加载可选依赖，产物写入调用者指定的目录。
- 连接图由 `presentation/qt/connection_diagrams.py` 按需生成；不可用时界面会说明原因，不能将空图当作有效接线图确认。
- `DPD_auto_test` 是独立子系统，不读取或修改主系统的配置、结果和运行目录；跨系统交换数据必须显式传入脱敏文件路径。
- DPD 依赖只安装在其独立环境中，不进入主系统的 `requirements.txt` 或 `requirements.lock.txt`。

### 启动方法

项目只保留 `launcher.py` 作为应用启动器。请在 PyCharm 中将项目解释器设置为 `Auto_test`，并运行根目录下的 `launcher.py`；也可以在终端中执行：
```powershell
# 先复制 .env.example 为 .env，并填写 Auto_test 环境的 python.exe
& $env:AUTO_TEST_PYTHON launcher.py --check
& $env:AUTO_TEST_PYTHON launcher.py --validate-config
& $env:AUTO_TEST_PYTHON launcher.py
```

项目命令统一使用 `.env` 中的 `AUTO_TEST_PYTHON`，启动器、离线测试和 Hardware smoke
都会验证当前解释器与该配置一致。不要用 `base` 环境或裸 `python` 替代项目解释器。

#### 启动前检查
```powershell
& $env:AUTO_TEST_PYTHON launcher.py --check
& $env:AUTO_TEST_PYTHON launcher.py --validate-config
```

`--validate-config` 只读取并校验 `config.json`，不会连接 VISA 仪器，也不会改变射频或电源状态。发现错误时请先修正配置，再开始测量；只有警告时仍可继续启动，但应由操作者确认。

生产装配只使用配置中显式声明的地址、角色和物理通道：被供电分配引用的每个电源通道必须声明
`role: gate` 或 `role: drain`（`instruments.power_supplies.<name>.channels.<channel>.role`），
不再按 `CH1`/`CH2` 自动推导。配置页为每个通道提供 `gate`/`drain` 选择（初值按现场固定
接线预置），保存时写入控件当前选择；只在 `CH1`/`CH2` 接线但未声明角色的旧硬件配置会在
`--validate-config` 或连接前失败。分配内跨电源重复角色、缺少 `gate` 或 `drain`、以及
DUT 与驱动功放占用同一通道，都会在连接前报错。

`--check` 检查当前 Python 环境是否具备启动 GUI 所需的依赖，并按 `requirements.lock.txt`
核对全部已安装发行版（含传递依赖）；`--validate-config` 只检查配置内容，不能替代依赖检查。
两个命令不能同时使用。未知命令行参数会返回退出码 `2`。

项目启动器会拒绝非 `Auto_test` 环境。PyCharm 的运行配置应直接指向 `launcher.py`，测试使用 `tools/run_tests.py`。不要再使用已经移除的 VS Code 包装器或 GUI 批处理入口。

### 离线测试与现场交付

```powershell
& $env:AUTO_TEST_PYTHON tools/run_tests.py
& $env:AUTO_TEST_PYTHON tools/export_dependency_lock.py --check
& $env:AUTO_TEST_PYTHON -m unittest tests.test_static_quality_gate tests.test_exception_taxonomy -v
& $env:AUTO_TEST_PYTHON launcher.py --check
& $env:AUTO_TEST_PYTHON launcher.py --validate-config
```

`tools/run_tests.py` 取代了原先的 `run_tests.ps1` / `run_tests.bat`：先校验 `.env` 中的解释器、
把 conda 的原生依赖目录前置到 `PATH`（否则未 activate 时 Qt/matplotlib 会延迟加载崩溃），
再执行内存编译检查、依赖核对，最后按改动面跑测试；任一步失败即终止。

静态质量门禁（裸 `except`、静默宽泛捕获、生产 `print`、已退役模块导入、动态设备路由）
与异常分类契约在窄范围运行中先于测试执行，失败即终止本次验证，细节见
[异常分类与静态质量门禁](docs/异常分类与静态质量门禁.md)。

`tests/` 按改动面分层：纯界面改动只需 `tools/run_tests.py --layer ui`（表现层测试 + 全部门禁，
约 45 秒），涉及真实窗口与后端接线用 `--layer integration`；全量离线测试仍是交付前的
唯一完整验证。选择规则见 `AGENTS.md`。

普通测试只执行离线和仿真路径，不会初始化真实设备。Hardware smoke 必须使用
`tools/run_tests.py --layer hardware --config hardware/smoke_config.local.json --confirm-hardware-smoke`，
并由 `hardware/smoke_gate.py` 在打开任何 VISA 资源之前检查：配置位于 `hardware/` 且不是示例、
`environment` 为 `hardware_smoke`、明确要求用户确认与空载确认、不含占位符地址、
`HARDWARE_SMOKE_ENABLED=1`、不在 CI 中、操作者显式确认。门禁随测试执行，不依赖某一个入口脚本，
因此也不能用直接跑 unittest 的方式绕过；它不属于普通
测试发现范围，也不能用离线测试替代现场验收。真实 Hardware Smoke 与三类硬件测量的现场
现场操作顺序与判定标准由受控现场验收流程维护；现场报告、配置和回滚决定仅进入受控归档。

发布清单使用 `release_manifest.py` 生成。可通过 `AUTO_TEST_RUN_ID` 固定运行标识，
使配置快照、结果快照、清理记录和现场报告能够关联到同一次运行。清单同时记录
`python_requirement`、`interpreter`、`dependencies.lock_sha256` 与统一后的
`verification` 命令，保证发布验证使用同一解释器和同一份依赖锁定结果。现场设备身份、报告
和回滚决定仅进入受控归档，不提交版本库；真实设备验收只有在证据完整后才能标记为
`passed`。

## 文件结构

```
pa_auto_test/
├── launcher.py                     # 唯一应用启动器
├── release_manifest.py             # 发布清单（绑定解释器与依赖锁，含回滚决定摘要）
├── requirements.txt                # 主系统直接依赖（精确版本）
├── requirements.lock.txt           # 主系统完整依赖锁定（含传递依赖）
├── requirements-dpd.txt            # DPD_auto_test 专用依赖（与主系统隔离）
├── tools/export_dependency_lock.py # 锁定清单导出与核对（禁止裸 pip freeze 覆盖）
├── config.json                     # 配置文件
├── presentation/qt/                # GUI 页面、controller、主窗口、绘图与连接图
├── application/                    # 测量用例、端口与 DTO
├── infrastructure/                 # 配置、结果与路径实现（含 discovery/ 设备发现）
├── instrument/                     # 设备会话、driver、transport 与安全流程
└── README.md                       # 本文档
```

根目录不再保留任何兼容再导出模块：日志、路径、测量计算、测量服务与测量清理只从正式模块
（`infrastructure.logging.app_logging`、`infrastructure.filesystem.paths`、
`domain.measurement.calculations`、`application.measurements.services`、
`instrument.flow.cleanup`）导入。三类旧测量根入口（`cable_loss_measurement.py`、
`driver_power_mapping.py`、`app/legacy_amplifier_measurement.py` 及转发
`amplifier_measurement.py`）已在技术债步骤 4 删除，五个纯再导出模块在步骤 16 删除，测量只能
通过 `app.gui_runtime.create_*_measurement()` 与正式 application 用例启动。

生产环境请通过 `& $env:AUTO_TEST_PYTHON launcher.py` 启动，不直接运行 GUI 模块。

## 使用流程

### 运行目录与旧数据迁移

新测量应从配置校验和运行创建流程开始，并在后续阶段始终使用同一个 `run_id`。运行配置冻结在 `runs/<run_id>/config.json`，硬件装配配置位于 `inputs/hardware_config.json`，线损和驱动映射等阶段输入由当前运行目录持有，结果位置以 `manifest.json` 登记为准。不得以全局 `config.json`、固定兼容结果文件或目录中修改时间最新的结果替代当前运行输入。

根目录 `config.json` 由配置页面编辑和保存，保存时直接写出正式 `test_plan` / `run_mapping` 段，可被 `JsonConfigurationRepository` 直接读取；旧版扁平配置的自动转换（`load_legacy_data()` / `unresolved_fields`）已在技术债步骤 5 删除。缺少正式配置段的文件会在创建运行前明确失败，应先在配置页面中补全并保存。`launcher.py --validate-config` 同样执行正式 schema 与硬件装配段两套校验，因此不会再对缺正式段的文件报告“通过”。正式 `run_mapping` 只登记一个本机供电电源，配置页保存时若供电分配引用了多个本机电源会明确报错并说明处理方式，不会写出无法创建运行的配置。历史全局结果（如 `test_results/cable_loss_results.json` 和带时间戳的映射/功放 JSON）在技术债步骤 6 之后不再被应用发现或读取：历史查看、导出与报告只读正式运行目录结果。需要迁移校准数据时，应核验来源和 `run_id`，将所需文件复制到目标运行 `inputs/` 并登记 manifest；无法确认归属的数据不得作为新运行输入。

旧结果仓储的 `latest_path()`、GUI `latest()` 查询及历史主功放入口均已弃用。`latest_path()` 兼容链、`LegacyResultInputReader`（无路径 `read_legacy_cable_loss()` / `read_latest_legacy_driver_mapping()`）已在重构步骤 B3 删除：输入读取不再回退到按修改时间扫描的“最新结果”。驱动模式的历史主功放入口要求显式传入 `driver_mapping_path`。测量输入端口 `ResultInputReader` 只接受显式路径，省略路径会立即失败；旧兼容流程缺少显式 `loss_data_path` / `driver_mapping_path`（或运行契约输入）时，在连接仪器或执行硬件动作前失败。历史查看与导出只读正式运行目录结果（`run::<run_id>::<相对路径>`，由运行 manifest 校验），不复用正式测量结果仓储，也不再扫描 `test_results/` 根目录的旧文件。新流程必须从当前运行 manifest 读取阶段输入。

### 1. 系统配置
1. 在欢迎页选择本次测试模式（有驱放测试 / 无驱放测试）
2. 进入“仪表配置”页：扫描网段与扫描方式居中排在标题下面，点结果框正中的“查找设备”（未查找时框内是浅灰底，点下去按钮与灰底一起消失、开始加载设备），勾选本次测试所需的设备，点击“应用所选设备到配置”；设备显示在页面中间的圆角结果框里，框外右下角的“连接仪器”连接本次应用的仪器
3. 点击“下一步”进入“仪器配置”选项卡；仪器地址只来自上一步应用的发现结果，不再手工填写；需要更换设备时回到前一步重新扫描并应用（应用是整批替换，没勾选的角色会被移除）
4. 设置测试参数（频率、功率范围等）；电源分配只能选本次已应用的电源，引用了未绑定电源时页面会拒绝保存
5. 点击"保存配置"
6. 点击"连接仪器"测试连接

### 2. 线损测量
1. 打开"线损测量"选项卡
2. 查看连接说明和示意图
3. 按提示连接路径1
4. 点击"开始线损测量"
5. 根据提示重新连接路径2
6. 等待测量完成，查看结果

### 3. 驱动映射（如果启用驱动模式）
1. 打开"驱动映射"选项卡
2. 查看连接说明
3. 确认驱动功放已正确供电和连接
4. 点击"开始驱动映射"
5. 观察实时测量曲线

### 4. 主功放测试
1. 打开"功放测试"选项卡
2. 查看连接说明
3. 确认所有设备正确连接和供电
4. 点击"开始功放测试"
5. 观察实时测量数据
6. 如需要可点击"紧急停止"

### 5. 数据分析
1. 打开"数据可视化"选项卡
2. 加载历史测试数据
3. 查看分析图表
4. 需要报告副本时点击"生成报告"：只针对当前已加载的历史结果生成副本，未加载结果不会自动挑选文件

### 6. 历史运行与结果管理
1. 打开"历史运行"选项卡
2. 正常测试流程已经自动归档结果与交付产物，这里只用于查看历史
3. 在运行列表查看阶段、正式结果与产物状态
4. 产物失败时可选中该运行并点击"重新生成失败的产物"
5. 需要另外一份文件时，选择历史结果并导出 JSON/CSV/测试报告副本

## 注意事项

### 安全事项
- 在进行任何连接操作前，确保所有设备已断电
- 设置合适的DUT最大输入功率保护值
- 测试过程中如发现异常立即使用紧急停止功能
- 定期检查线缆连接是否牢固

### 测量精度
- 确保所有连接器清洁无腐蚀
- 使用高质量的RF线缆
- 在测量前预热仪器至少30分钟
- 环境温度保持稳定

### 故障排除
- 如果仪器连接失败，检查VISA地址是否正确
- 如果测量数据异常，检查线缆连接
- 如果GUI无响应，可能是测量任务卡住，重启应用
- 如果图表显示异常，检查测试数据文件格式

## 开发信息

### 版本历史
- v1.0: 初始GUI版本，包含所有基本功能
- 计划v1.1: 增加更多自定义配置选项
- 计划v1.2: 支持更多仪器型号

正式公共 API、正式内部模块和旧模块清单记录在 `docs/正式API目录清单.md`；正式 production
code 只能导入登记的模块路径，旧模块禁止新增引用，由 `tests/test_public_api_inventory.py`
静态门禁固定。兼容层已全部删除；历史弃用与删除过程的审计证据见 git 提交历史，不描述现行
运行时机制。

电源设备身份（`厂商:序列号`）与动态电源配置的现行数据契约：
新配置的电源键是 `supply_id`（= 序列号派生的短标签），1 台、2 台、5 台都只是同一个集合的大小
不同；旧槽位 `PS1..PS4` 只在读取旧配置/旧运行快照与迁移器里有效，生产代码不再引用槽位编号，由
`tests/test_power_supply_identity_contract.py` 静态守住，样例数据在
`tests/fixtures/power_supply_identity/`（见该目录 README）。

现行架构要点：

- 运行契约是测量唯一入口：每次测量绑定同一个 `runs/<run_id>/` 运行目录（`config.json`、
  `manifest.json`、`inputs/hardware_config.json`），硬件连接只从冻结快照取配置。仍存在的无契约
  分支按[无运行契约调用方清单](docs/无运行契约调用方清单.md)逐项登记，由
  `tests/test_run_contract_inventory.py` 冻结（仓库门禁 `GATE_MODULES` 成员，清单与基线双向一致、
  只减不增）。
- 正式结果只写 `runs/<run_id>/`；历史查看与导出只读运行目录 manifest 登记的正式结果，不再
  扫描全局结果目录。
- 三类测量结果只有唯一显式 DTO（`CableLossMeasurementResult` /
  `DriverPowerMappingMeasurementResult` / `AmplifierMeasurementResult`，定义在
  `application/dto/measurement_results.py`）；写入端口只接受正式 DTO，结果文件与 canonical
  model 属于同一次提交，历史列表只列出能解析为正式结果的文件，查询返回结果 DTO。
- 配置页面保存时直接写出正式 `test_plan` / `run_mapping` 段，缺少正式段的文件在创建运行前
  明确失败。
- 仪器装配使用显式地址、角色与物理通道，不按运行时名字转发设备能力。
- 根目录只保留 `launcher.py`、`release_manifest.py` 与脚本；兼容再导出模块与旧测量入口均已
  删除，旧导入路径在任何进程中都会 `ModuleNotFoundError`。
- 发布清单只登记正式发布边界与依赖锁，不再维护兼容/回滚文件列表。

### 技术支持
如遇到问题，请检查：
1. conda环境是否正确激活
2. 所有依赖包是否已安装
3. config.json文件格式是否正确
4. 仪器VISA驱动是否正确安装

## 许可证

本软件仅供内部测试使用。
