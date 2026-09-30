# SCPI 控制架构重构状态与收尾清单

## 1. 验收结论

本次重构尚未最终收敛，本指南保留作为阶段 8 的收尾清单。

截至 2026-09-30，阶段 1 至阶段 6 的离线代码能力、阶段 7 的当前离线接入切片已经落地，但最终验收仍被以下事项阻塞：

1. `minimal_action` 已完成一次真实受限动作，但多电源断言修正后的硬件入口回归和 Simulation 回归仍需重跑并留存记录。
2. `InstrumentControl` 仍是真实应用入口的默认控制器，GUI、worker 和三个测量包装器仍保留直接构造或隐式回退；阶段 7 的生产入口迁移和删除前审计尚未完成。
3. 阶段 8 的最终验收、现场记录归档和旧实现清理不能仅凭已有离线测试通过而宣告完成。

因此当前正确状态是：**离线重构能力和一次真实动作验收已完成，生产入口迁移及阶段 8 收尾待完成**。

## 2. 已完成部分

### 2.1 分层实现

当前代码已具备目标架构的主要组成：

```text
应用入口 / 测量服务
        ↓
      action
        ↓
      driver
        ↓
    transport
        ↓
 VISA 或仿真设备
```

- `instrument/transport/`：包含 `ScpiTransport`、`MockScpiTransport` 和 `VisaScpiTransport`，负责通信、超时、异常转换和幂等关闭。
- `instrument/drivers/`：包含信号源、频谱仪和电源 driver，负责 SCPI 命令、单位、参数校验和查询解析。
- `instrument/action/`：提供信号源、频谱仪和电源的稳定动作入口，不拼接 SCPI、不创建 VISA 连接。
- `instrument/flow/`：包含安全上电、掉电和统一清理流程，保持 `RF 关闭 → 电源关闭 → 连接关闭`，以及 `Drain → Gate` 的安全顺序。
- `instrument/simulation.py`、`instrument/measurement_adapter.py` 和 `instrument/measurement_factory.py`：提供仿真 session、端口适配和离线组装路径。

### 2.2 已验证能力

- transport 的写入、查询、超时、异常、空命令、重复关闭和关闭后调用已覆盖测试。
- 三类 driver 的命令格式、单位、参数边界、查询解析和设备错误已覆盖测试。
- action/flow 不直接创建 VISA 连接，也不读取全局配置。
- 仿真设备已覆盖正常流程、连接/准备/测量失败、取消、紧急停止和重复清理。
- 测量服务支持注入 `measurement_port`，旧 `InstrumentControl` 路径仍可回滚。
- 硬件 smoke 已有 `read_only`、`safe_prepare` 和 `minimal_action` 入口及显式安全门禁；三者均已有现场证据，`minimal_action` 已完成一次真实 RF 动作、测量和安全清理。

### 2.3 本次自动化验收

使用 `.env` 中的 `AUTO_TEST_PYTHON`：

```text
解释器：C:\My_Document\Anaconda\envs\Auto_test\python.exe
Python：3.11.15
Conda：Auto_test
```

执行结果：

```text
./run_tests.ps1                         427 项通过
./start_gui.bat --check                 通过
./start_gui.bat --validate-config       通过
```

配置校验仍有已知警告：驱动功放已启用但未配置本机供电分配，需要确认现场由外部供电。该警告不等同于测试失败，但在真实动作前必须确认。

## 3. 当前未完成项

### 3.1 真实设备最小动作验收（已完成，需保留证据）

已使用现场配置执行一次受限动作。后续仅在新组装路径或硬件 smoke 代码发生影响行为的变更时，按同一安全门禁复验：

```powershell
$env:HARDWARE_SMOKE_ENABLED = "1"
./run_hardware_smoke.ps1 -ConfigPath "hardware/smoke_config.local.json" -ConfirmHardwareSmoke
```

执行前必须确认：

- 配置为 `smoke_mode: minimal_action`，`max_action_count` 为 1；
- 设备空载，操作者明确授权一次 RF 动作；
- 信号源、频谱仪、电源身份与配置匹配；
- 信号源使用现场批准的低功率，当前准备值为 2.3 GHz、-30 dBm；
- 频谱仪使用已确认可用的 `CALC:MARK1:Y?`，并配置合理的读数范围；
- 电源初始状态为关闭，掉电顺序保持 `CH2 OFF -> CH1 OFF`；
- 配置校验中的驱动功放供电警告已经得到现场解释。

执行后必须保存未跟踪的现场报告和配置快照，并核对：

- 只执行一次 RF 开启动作；
- `CALC:MARK1:MAX` 在 `CALC:MARK1:Y?` 之前执行；
- 测量完成或异常后立即发送 `OUTP OFF`；
- 电源按 `Drain CH2 OFF -> Gate CH1 OFF` 清理；
- RF 和电源输出均关闭；
- 所有 VISA resource 和 ResourceManager 均已释放；
- 报告记录动作数、测量值、设备身份、清理事件、最终状态和异常。

报告已确认动作预算、测量顺序、RF 和电源关闭以及 VISA 资源释放。该结论不自动覆盖尚未迁移的新应用默认入口；在新路径验收前不得删除兼容入口。

### 3.2 真实应用入口迁移

按以下顺序完成，并为每一步保留可回滚路径：

1. 在应用组装层创建真实设备的 `transport -> driver -> action -> flow/session -> measurement_port` 路径。
2. 让 `app/gui_runtime.py`、`enhanced_main_gui.py` 和 `enhanced_workers.py` 通过组装层获得端口，不再由应用入口直接构造 `InstrumentControl`。
3. 让 `cable_loss_measurement.py`、`driver_power_mapping.py` 和 `amplifier_measurement.py` 的生产调用方显式注入端口；默认回退只保留在兼容层，不作为新入口的隐式组装逻辑。
4. 审计全部 `InstrumentControl` 调用方、旧方法名、参数单位、日志、异常、取消和清理路径；补齐新旧路径行为对照测试。
5. 完成真实硬件 smoke 后，逐批删除未使用的旧 SCPI 方法和旧内部实现；每批删除后运行完整离线测试和应用检查。

当前仍可见的直接旧入口包括：

- `app/gui_runtime.py:connect_instruments()`；
- `enhanced_main_gui.py` 的默认构造路径；
- `enhanced_workers.py` 的默认构造路径；
- 三个测量包装器中的 `measurement_port or InstrumentControl(config_path)` 回退。

## 4. 完成判据

只有同时满足以下条件，才能删除本指南及其他仅用于记录本次重构过程的文档：

1. `minimal_action` 真实 smoke 报告通过，最终 RF、电源和连接状态安全，报告已在本机归档。
2. 真实应用默认路径使用新组装链路，旧 `InstrumentControl` 只作为明确的兼容/回滚入口。
3. 调用方审计无遗漏，服务层不包含 VISA resource、SCPI 文本、设备通道硬编码或 GUI 控件访问。
4. 完整离线测试、相关模拟测试、应用检查和必要的硬件 smoke 均通过，且没有未解释的新回归。
5. 旧实现已分批删除或明确保留为兼容适配器，并有可恢复的提交或配置开关。

完成后应同步更新 `docs/三层测试架构重构计划.md`、`docs/阶段7完成记录.md` 和 `docs/阶段7调用方清单.md` 的状态；在此之前，这些文档中的“真实最小动作待执行”和“旧入口保留”结论仍然有效。

## 5. 维护规则

- 修改 transport、driver、action、flow、硬件配置、SCPI 命令或清理逻辑后，运行相关 Unit/Simulation 测试，并安排相应硬件 smoke。
- 默认离线测试不得访问真实网络、VISA resource 或真实地址。
- 普通设置动作不得隐式打开 RF 或电源。
- 所有异常、取消、部分初始化成功和程序退出路径都必须进入可重复的安全清理。
- 真实设备配置、地址、凭据和现场报告不得提交到版本库。
