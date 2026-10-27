# PA自动测试系统 GUI版本

## 概述

这是一个基于PySide6的功放自动测试系统GUI版本，将原有的命令行测试脚本转换为用户友好的图形界面应用。

## 主要功能

### 1. 仪器配置和连接
- **仪器地址配置**: 配置信号发生器、频谱分析仪和电源的VISA地址
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
- **报告生成**: 自动生成HTML格式的测试报告

### 6. 数据导出
- **多格式支持**: 支持JSON、CSV、PDF格式导出
- **文件管理**: 显示所有测试数据文件及修改时间
- **快速导出**: 一键导出选中的测试数据

### 7. 实时日志
- **测量进度**: 实时显示测量进度条
- **状态日志**: 显示详细的测量状态和错误信息
- **时间戳**: 所有日志条目都包含时间戳

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
conda activate Auto_test
```

已验证的项目运行环境：

- Conda 环境：`Auto_test`
- Python：3.11.15
- 环境路径：`D:\Anaconda\envs\Auto_test`

`VISA_demo` 是历史文档中引用的环境名，当前机器上不存在。请使用 `Auto_test`，不要在 `base` 环境中安装或运行本项目依赖。

### 必需的Python包
- PySide6 >= 6.6.0
- matplotlib >= 3.10.0
- numpy >= 1.26.4
- pandas >= 2.3.1
- seaborn >= 0.13.2
- PyVISA >= 1.14.1

### 启动方法

项目只保留 `launcher.py` 作为应用启动器。请在 PyCharm 中将项目解释器设置为 `Auto_test`，并运行根目录下的 `launcher.py`；也可以在终端中执行：
```bash
# 先复制 .env.example 为 .env，并填写 Auto_test 环境的 python.exe
python launcher.py --check
python launcher.py --validate-config
python launcher.py
```

项目命令统一使用 `.env` 中的 `AUTO_TEST_PYTHON`，启动器、离线测试和 Hardware smoke
都会验证当前解释器与该配置一致。不要用 `base` 环境或裸 `python` 替代项目解释器。

#### 启动前检查
```bash
python launcher.py --check
python launcher.py --validate-config
```

`--validate-config` 只读取并校验 `config.json`，不会连接 VISA 仪器，也不会改变射频或电源状态。发现错误时请先修正配置，再开始测量；只有警告时仍可继续启动，但应由操作者确认。

生产装配只使用配置中显式声明的地址、角色和物理通道：被供电分配引用的每个电源通道必须声明
`role: gate` 或 `role: drain`（`instruments.power_supplies.<name>.channels.<channel>.role`），
不再按 `CH1`/`CH2` 自动推导。配置页面保存时会按现场固定接线写入角色；只在 `CH1`/`CH2`
接线但未声明角色的旧硬件配置会在 `--validate-config` 或连接前失败。

`--check` 检查当前 Python 环境是否具备启动 GUI 所需的依赖；`--validate-config` 只检查配置内容，不能替代依赖检查。两个命令不能同时使用。未知命令行参数会返回退出码 `2`。

项目启动器会拒绝非 `Auto_test` 环境。PyCharm 的运行配置应直接指向 `launcher.py`，测试仍使用 `run_tests.bat` 或 `run_tests.ps1`。不要再使用已经移除的 VS Code 包装器或 GUI 批处理入口。

### 离线测试与现场交付

```powershell
./run_tests.ps1
& $env:AUTO_TEST_PYTHON launcher.py --check
& $env:AUTO_TEST_PYTHON launcher.py --validate-config
```

普通测试只执行离线和仿真路径，不会初始化真实设备。Hardware smoke 必须使用
`run_hardware_smoke.ps1`，同时满足配置位于 `hardware/`、明确用户确认、空载确认、
`HARDWARE_SMOKE_ENABLED=1` 和 `-ConfirmHardwareSmoke` 等独立安全门禁；它不属于普通
测试发现范围，也不能用离线测试替代现场验收。

发布清单使用 `release_manifest.py` 生成。可通过 `AUTO_TEST_RUN_ID` 固定运行标识，
使配置快照、结果快照、清理记录和现场报告能够关联到同一次运行。现场设备身份、报告
和回滚决定仅进入受控归档，不提交版本库；真实设备验收只有在证据完整后才能标记为
`passed`。

## 文件结构

```
pa_auto_test/
├── launcher.py                     # 唯一应用启动器
├── config.json                     # 配置文件
├── measurement_services.py         # 测量服务兼容入口（实现在 application/measurements/services.py）
├── measurement_lifecycle.py        # 测量清理兼容入口（实现在 instrument/flow/cleanup.py）
├── presentation/qt/                # GUI 页面、controller、主窗口、绘图与连接图
├── application/                    # 测量用例、端口与 DTO
├── infrastructure/                 # 配置、结果与路径实现
├── instrument/                     # 设备会话、driver、transport 与安全流程
└── README.md                       # 本文档
```

`measurement_services.py`、`measurement_lifecycle.py` 是尚未收口的兼容再导出入口，将在后续纯代码路径步骤统一退役；三类旧测量根入口（`cable_loss_measurement.py`、`driver_power_mapping.py`、`app/legacy_amplifier_measurement.py` 及转发 `amplifier_measurement.py`）已在技术债步骤 4 删除，测量只能通过 `app.gui_runtime.create_*_measurement()` 与正式 application 用例启动。

生产环境请通过 `python launcher.py` 启动，不直接运行 GUI 模块。

## 使用流程

### 运行目录与旧数据迁移

新测量应从配置校验和运行创建流程开始，并在后续阶段始终使用同一个 `run_id`。运行配置冻结在 `runs/<run_id>/config.json`，硬件装配配置位于 `inputs/hardware_config.json`，线损和驱动映射等阶段输入由当前运行目录持有，结果位置以 `manifest.json` 登记为准。不得以全局 `config.json`、固定兼容结果文件或目录中修改时间最新的结果替代当前运行输入。

根目录 `config.json` 由配置页面编辑和保存，保存时直接写出正式 `test_plan` / `run_mapping` 段，可被 `JsonConfigurationRepository` 直接读取；旧版扁平配置的自动转换（`load_legacy_data()` / `unresolved_fields`）已在技术债步骤 5 删除。缺少正式配置段的文件会在创建运行前明确失败，应先在配置页面中补全并保存。`launcher.py --validate-config` 同样执行正式 schema 与硬件装配段两套校验，因此不会再对缺正式段的文件报告“通过”。正式 `run_mapping` 只登记一个本机供电电源，配置页保存时若供电分配引用了多个本机电源会明确报错并说明处理方式，不会写出无法创建运行的配置。历史全局结果（如 `test_results/cable_loss_results.json` 和带时间戳的映射/功放 JSON）在技术债步骤 6 之后不再被应用发现或读取：历史查看、导出与报告只读正式运行目录结果。需要迁移校准数据时，应核验来源和 `run_id`，将所需文件复制到目标运行 `inputs/` 并登记 manifest；无法确认归属的数据不得作为新运行输入。

旧结果仓储的 `latest_path()`、GUI `latest()` 查询及历史主功放入口均已弃用。`latest_path()` 兼容链、`LegacyResultInputReader`（无路径 `read_legacy_cable_loss()` / `read_latest_legacy_driver_mapping()`）已在重构步骤 B3 删除：输入读取不再回退到按修改时间扫描的“最新结果”。驱动模式的历史主功放入口要求显式传入 `driver_mapping_path`。测量输入端口 `ResultInputReader` 只接受显式路径，省略路径会立即失败；旧兼容流程缺少显式 `loss_data_path` / `driver_mapping_path`（或运行契约输入）时，在连接仪器或执行硬件动作前失败。历史查看与导出只读正式运行目录结果（`run::<run_id>::<相对路径>`，由运行 manifest 校验），不复用正式测量结果仓储，也不再扫描 `test_results/` 根目录的旧文件。新流程必须从当前运行 manifest 读取阶段输入。

### 1. 系统配置
1. 打开"仪器配置"选项卡
2. 填入所有仪器的VISA地址
3. 设置测试参数（频率、功率范围等）
4. 点击"保存配置"
5. 点击"连接仪器"测试连接

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
2. 加载测试数据文件
3. 查看分析图表
4. 生成测试报告

### 6. 数据导出
1. 打开"数据导出"选项卡
2. 刷新文件列表
3. 选择要导出的文件
4. 选择导出格式进行导出

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

兼容层的弃用、删除条件、迁移方式和目标版本记录在 `docs/兼容层弃用与删除发布说明.md`。
正式公共 API、正式内部模块和旧模块清单记录在 `docs/正式API目录清单.md`；正式 production
code 只能导入登记的模块路径，旧模块禁止新增引用，由 `tests/test_public_api_inventory.py`
静态门禁固定。
旧 GUI 测量入口、`latest_path()` 兼容链与无路径映射读取已在重构步骤 A、B 删除（外部调用方
已确认零调用方）；正式测量的全局结果兼容副本写入已在 `docs/技术债分阶段重构计划.md` 步骤 2
停止，正式结果只写 `runs/<run_id>/`。步骤 3 已删除 `save_compatibility_copy()`、`legacy_path()`、
`create_legacy_run_snapshot()`、`write_compatibility_copy()`、`write_legacy_run_snapshot()` 和
`result_storage.py`；三个旧兼容测量根入口已停用并立即失败，文件本体随步骤 4 退役。
步骤 5 已删除旧配置转换与根目录配置转发；步骤 6 已把三类测量结果收敛为唯一显式 DTO
（`CableLossMeasurementResult` / `DriverPowerMappingMeasurementResult` /
`AmplifierMeasurementResult`，定义在 `application/dto/measurement_results.py`），删除根
`result_reading.py`、`domain/result_reading.py`、`application/dto/legacy_result_adapter.py`、
旧全局历史端口/仓储，旧结果 JSON 形状和旧字段别名不再被解析；`domain.models` 不再定义结果
模型，展示/导出只使用 DTO 的显式属性。写入端口只接受正式 DTO，结果文件与 canonical model
属于同一次提交；历史列表只列出能解析为正式结果的文件（功放拆分明细只留在运行目录里，不再
出现在历史列表），查询返回的已经是结果 DTO。

### 技术支持
如遇到问题，请检查：
1. conda环境是否正确激活
2. 所有依赖包是否已安装
3. config.json文件格式是否正确
4. 仪器VISA驱动是否正确安装

## 许可证

本软件仅供内部测试使用。
