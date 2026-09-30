# SCPI 控制架构重构状态与收尾清单

## 1. 验收结论

本次重构尚未完成，本指南继续作为阶段 8 的唯一收尾清单。

截至 2026-09-30，阶段 1 至阶段 7 的离线能力和应用组装切片已经落地，但最终验收仍被以下事项阻塞：

1. 已有的 `minimal_action` 现场报告证明旧 smoke 入口完成过一次受限动作，但未证明新应用组装路径完成硬件验收。
2. GUI/worker 的连接入口已经调用硬件组装器，但三个测量包装器仍保留 `measurement_port` 为空时的 `InstrumentControl` 兼容回退；调用方边界和回退策略尚未最终冻结。
3. 阶段 8 的新路径现场验收、调用方审计、现场记录归档和旧实现处置仍未完成。

因此当前正确状态是：**离线重构能力和应用组装切片已完成，一次旧 smoke 入口真实动作验收已完成；新应用组装路径 smoke、兼容回退审计和阶段 8 收尾待完成**。

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
- 测量服务支持注入 `measurement_port`；三个测量包装器仍可在未注入时回退到 `InstrumentControl`，该回退尚未从生产边界移除。
- 硬件 smoke 已有 `read_only`、`safe_prepare` 和 `minimal_action` 入口及显式安全门禁；三者均已有现场证据，`minimal_action` 已完成一次真实 RF 动作、测量和安全清理。

### 2.3 本次自动化验收

使用 `.env` 中的 `AUTO_TEST_PYTHON`，最近一次记录结果为：

```text
解释器：C:\My_Document\Anaconda\envs\Auto_test\python.exe
Python：3.11.15
Conda：Auto_test
```

执行结果：

```text
./run_tests.ps1                         431 项通过
./start_gui.bat --check                 通过
./start_gui.bat --validate-config       通过
```

配置校验仍有已知警告：驱动功放已启用但未配置本机供电分配，需要确认现场由外部供电。该警告不等同于测试失败，但在真实动作前必须确认。

## 3. 当前未完成项

### 3.1 真实设备最小动作验收（旧 smoke 入口已完成，新组装路径待完成）

已有本地 `minimal_action` 报告记录一次受限动作、测量和安全清理；该报告没有标识或证明应用组装路径，因此不能作为阶段 8 的最终验收证据。新组装路径必须在现场授权后按同一安全门禁复验：

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

已有报告确认旧 smoke 入口的动作预算、测量顺序、RF 和电源关闭以及 VISA 资源释放。该结论不覆盖新应用组装入口；在新路径验收前不得删除兼容入口。

### 3.2 真实应用入口迁移与调用方审计

按以下顺序完成，并为每一步保留可回滚路径：

1. 已有应用组装层真实设备的 `transport -> driver -> action -> flow/session -> measurement_port` 路径；保留并测试连接失败、异常清理和资源关闭行为。
2. 已有 `app/gui_runtime.py`、`enhanced_main_gui.py` 和 worker 的新组装调用链，但必须逐项确认所有生产测量调用方都显式传入端口。
3. 将 `cable_loss_measurement.py`、`driver_power_mapping.py`、`amplifier_measurement.py` 中的隐式 `InstrumentControl` 回退限定到明确的兼容入口，或在确认无回滚需求后删除；不得把当前兼容回退误记为迁移完成。
4. 更新 `docs/阶段7调用方清单.md`，审计全部 `InstrumentControl` 调用方、旧方法名、直接 VISA 使用、参数单位、日志、异常、取消和清理路径；补齐新旧路径行为对照测试。
5. 在新路径真实 smoke 通过后，逐批删除未使用的旧 SCPI 方法和旧内部实现；每批删除后运行完整离线测试和应用检查。

当前仍可见、需要审计和明确边界的兼容入口包括：

- `app/gui_runtime.py:connect_instruments_legacy()`；
- `enhanced_workers.py` 及三个测量包装器在未注入 `measurement_port` 时的兼容回退；
- `instrument_control.py` 及其历史测试/脚本调用方；

## 4. 完成判据

只有同时满足以下条件，才能删除本指南及其他仅用于记录本次重构过程的文档：

1. 新应用组装路径的 `minimal_action` 真实 smoke 报告通过，最终 RF、电源和连接状态安全，报告已在本机归档；旧 smoke 报告不能替代该证据。
2. 真实应用默认路径使用新组装链路，所有 `InstrumentControl` 回退都只存在于明确的兼容/回滚入口。
3. 调用方审计无遗漏，服务层不包含 VISA resource、SCPI 文本、设备通道硬编码或 GUI 控件访问。
4. 完整离线测试、相关模拟测试、应用检查和必要的硬件 smoke 均通过，且没有未解释的新回归。
5. 旧实现已分批删除或明确保留为兼容适配器，并有可恢复的提交或配置开关。

完成后应同步更新 `docs/三层测试架构重构计划.md`、`docs/阶段7完成记录.md` 和 `docs/阶段7调用方清单.md` 的状态；在此之前，这些文档中的“新路径真实最小动作待执行”和“兼容入口保留”结论仍然有效。

## 5. 维护规则

- 修改 transport、driver、action、flow、硬件配置、SCPI 命令或清理逻辑后，运行相关 Unit/Simulation 测试，并安排相应硬件 smoke。
- 默认离线测试不得访问真实网络、VISA resource 或真实地址。
- 普通设置动作不得隐式打开 RF 或电源。
- 所有异常、取消、部分初始化成功和程序退出路径都必须进入可重复的安全清理。
- 真实设备配置、地址、凭据和现场报告不得提交到版本库。
