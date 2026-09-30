# SCPI 控制架构重构状态与收尾清单

## 1. 验收结论

本次重构尚未完成，本指南继续作为阶段 8 的唯一收尾清单。

截至 2026-09-30，阶段 1 至阶段 7 的离线能力和现代 GUI 的主要组装入口已经落地，但最终验收仍被以下事项阻塞：

1. 2026-09-30 已用本地 `minimal_action` 配置分别完成独立 smoke 入口和新应用组装路径的一次受限动作；本次联网复验成功，报告确认 RF、两台发现到的电源和 VISA 资源均安全关闭，频谱仪读数为约 `-50.56 dBm`，新路径记录了初始状态、设备身份、测量顺序、最终状态和真实 SCPI 操作。
2. 现代 GUI/worker 的主要入口通过硬件组装器注入 `measurement_port`；旧 GUI worker 通过明确命名的 legacy 组装函数调用。三个增强测量包装器和三个历史测量类现已拒绝缺失端口，`InstrumentControl` 只由明确的 legacy 组装入口创建。
3. 代码审查确认硬件工厂实际组装的是 `transport -> driver -> SafetyInstrumentSession -> PortMeasurementAdapter`；当前将 `SafetyInstrumentSession` 明确为默认硬件协调边界，独立 action/flow 作为可复用安全组件保留。GUI 测量完成、异常和取消路径均清理并清空端口，启动测量前拒绝使用已释放端口。
4. 新组装路径的最小动作现场验收和本机报告归档已完成；但完整测量类型验收、旧实现最终处置、默认 action/flow 边界以及多台电源的生产语义确认仍未完成，尚不满足全部删除条件。

因此当前正确状态是：**离线重构能力、现代 GUI 的主要端口注入路径、新应用组装路径的真实最小动作验收、历史调用方端口收束和 GUI 端口生命周期收束已完成；硬件默认路径当前采用 `SafetyInstrumentSession` 兼容协调边界，尚未正式接入独立 action/flow，完整测量类型验收、旧实现最终处置和多电源生产语义确认仍未完成，阶段 8 最终验收仍未通过**。

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
- `instrument/action/`：提供信号源、频谱仪和电源的稳定动作入口，不拼接 SCPI、不创建 VISA 连接；当前默认硬件组装仍由 `SafetyInstrumentSession` 兼容协调动作和清理，尚未统一迁移到这些独立 action。
- `instrument/flow/`：包含安全上电、掉电和统一清理流程，保持 `RF 关闭 → 电源关闭 → 连接关闭`，以及 `Drain → Gate` 的安全顺序。
- `instrument/simulation.py`、`instrument/measurement_adapter.py` 和 `instrument/measurement_factory.py`：提供仿真 session、端口适配和离线组装路径。

### 2.2 已验证能力

- transport 的写入、查询、超时、异常、空命令、重复关闭和关闭后调用已覆盖测试。
- 三类 driver 的命令格式、单位、参数边界、查询解析和设备错误已覆盖测试。
- action/flow 不直接创建 VISA 连接，也不读取全局配置。
- 仿真设备已覆盖正常流程、连接/准备/测量失败、取消、紧急停止和重复清理。
- 测量服务、三个增强测量包装器和三个历史测量类均要求显式注入 `measurement_port`，不再隐式创建 `InstrumentControl`。`app.gui_runtime.create_legacy_*_measurement()` 是明确的兼容组装入口。
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
./run_tests.ps1                         450 项通过
./start_gui.bat --check                 通过
./start_gui.bat --validate-config       通过
```

配置校验仍有已知警告：驱动功放已启用但未配置本机供电分配，需要确认现场由外部供电。该警告不等同于测试失败，但在真实动作前必须确认。

## 3. 当前未完成项

### 3.0 代码审查新增阻塞项

- `instrument/measurement_factory.py` 默认直接组装 driver 与 `SafetyInstrumentSession`。当前将 session 明确作为硬件协调边界：其内部负责连接状态、RF/电源安全顺序和资源关闭，独立 action/flow 作为可复用的通用安全组件保留；已用新应用真实 smoke 和仿真安全测试覆盖等价安全行为，不再把“必须由工厂逐个实例化 action/flow”作为本次收尾阻塞项。
- 三个历史测量类已强制显式注入 `measurement_port`；`InstrumentControl` 仅由 `app.gui_runtime.create_legacy_*_measurement()` 明确组装。仍需决定何时删除这些 legacy 适配器及其历史测试。
- GUI 组装函数会区分自有端口和调用方注入端口，并在构造失败时清理自有端口；正式 worker 的端口生命周期已由 GUI 连接/测量流程收束。仍需用重复测量场景的实际 GUI 验收证明端口不会被复用。
- `enhanced_workers.py` 的功放计算已直接依赖 `measurement_calculations` 纯计算模块；legacy 连接 worker 已改为调用 `app.gui_runtime.connect_instruments()`，并补齐连接结果、停止和失败清理契约。
- 多台 DP832A 的写操作广播到所有设备，而读数只取第一台；需确认并记录生产语义，或按设备/角色分别建模。

### 3.1 真实设备最小动作验收（独立 smoke 和新组装路径均已完成）

本机已分别归档独立 smoke 和新应用组装路径的 `minimal_action` 报告。新路径通过 `app.gui_runtime.connect_instruments()` 进入应用组装链路，按 VISA 枚举动态发现所有空载 DP832A，配置中的电源地址仅保留为模板字段，不再作为连接目标；初始安全状态、设备身份、一次 RF 动作、测量顺序、最终状态和 VISA 资源释放均已通过。该结果仍不替代 cable loss、driver mapping 和 amplifier 等完整测量类型验收：

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
- 执行前所有待发现 DP832A 均空载且 CH1/CH2 输出关闭；测试发现并纳入所有匹配电源，掉电顺序保持每台 `CH2 OFF -> CH1 OFF`；
- 配置校验中的驱动功放供电警告已经得到现场解释。

执行后必须保存未跟踪的现场报告和配置快照，并核对：

- 只执行一次 RF 开启动作；
- `CALC:MARK1:MAX` 在 `CALC:MARK1:Y?` 之前执行；
- 测量完成或异常后立即发送 `OUTP OFF`；
- 每台发现电源均按 `Drain CH2 OFF -> Gate CH1 OFF` 清理；
- RF 和电源输出均关闭；
- 所有 VISA resource 和 ResourceManager 均已释放；
- 报告记录动作数、测量值、设备身份、清理事件、最终状态和异常。

已有报告确认独立 smoke 和新应用组装路径的动作预算、测量顺序、RF 和电源关闭、每台发现电源的最终状态以及 VISA 资源释放。该结论不替代具体测量动作验收；在完整应用动作验收、历史调用方收束和 GUI 生命周期闭合前不得删除兼容入口。

### 3.2 真实应用入口迁移与调用方审计（离线部分已完成）

按以下顺序完成，并为每一步保留可回滚路径：

1. 已有应用组装层真实设备的 `transport -> driver -> session -> measurement_port` 路径；代码审查确认默认工厂尚未接入独立 action/flow，需完成统一或明确架构边界，并保留连接失败、异常清理和资源关闭测试。
2. 已有 `app/gui_runtime.py`、`enhanced_main_gui.py` 和 worker 的新组装调用链，但必须逐项确认所有生产测量调用方都显式传入端口。
3. 已将三个增强测量包装器的隐式回退收束到 `app.gui_runtime.create_legacy_*_measurement()`；正式 GUI worker 使用新组装入口。
4. 已按 `docs/阶段7调用方清单.md` 核查现代调用方、legacy 调用方、直接 VISA 层次和服务层边界；历史测量类已改为显式端口契约，GUI 组装层已落实端口所有权和失败清理，连接 worker 的旧 GUI 信号契约也已补齐。
5. 在新路径真实 smoke 通过后，逐批删除未使用的旧 SCPI 方法和旧内部实现；每批删除后运行完整离线测试和应用检查。

当前仍可见、需要审计和明确边界的兼容入口包括：

- `app/gui_runtime.py:connect_instruments_legacy()` 及三个 `create_legacy_*_measurement()` 显式兼容入口；
- 三个增强测量包装器本身拒绝缺失的 `measurement_port`；
- `instrument_control.py` 及其历史测试调用方；
- 三个历史测量类的 legacy 组装入口；独立脚本入口已删除，避免绕过应用组装层。
- `enhanced_main_gui.py` 中并存的 legacy worker。

## 4. 完成判据

只有同时满足以下条件，才能删除本指南及其他仅用于记录本次重构过程的文档：

1. 新应用组装路径的 `minimal_action` 真实 smoke 报告通过，最终 RF、电源和连接状态安全，报告已在本机归档；旧 smoke 报告不能替代该证据。
2. 真实应用默认路径使用新组装链路，所有 `InstrumentControl` 回退都只存在于明确的兼容/回滚入口。
3. 调用方审计无遗漏，服务层不包含 VISA resource、SCPI 文本、设备通道硬编码或 GUI 控件访问。
4. 完整离线测试、相关模拟测试、应用检查和必要的硬件 smoke 均通过，且没有未解释的新回归。
5. 默认硬件路径实际使用约定的 action/flow，或文档明确调整架构边界并有等价安全测试。
6. 历史测量类不再通过普通构造隐式创建 `InstrumentControl`；旧实现已分批删除或明确保留为兼容适配器，并有可恢复的提交或配置开关。
7. GUI 端口生命周期已闭合，重复测量不会复用已清理 session。

现场门槛和上述代码收尾条件全部通过后再将本指南标记完成，并评估归档过程性文档；当前应同步保持相关阶段文档状态一致。新路径真实最小动作已完成，`SafetyInstrumentSession` 已明确为默认硬件协调边界，兼容入口仍需保留；完整测量类型验收和多电源生产语义确认仍未完成。

## 5. 维护规则

- 修改 transport、driver、action、flow、硬件配置、SCPI 命令或清理逻辑后，运行相关 Unit/Simulation 测试，并安排相应硬件 smoke。
- 默认离线测试不得访问真实网络、VISA resource 或真实地址。
- 普通设置动作不得隐式打开 RF 或电源。
- 所有异常、取消、部分初始化成功和程序退出路径都必须进入可重复的安全清理。
- 真实设备配置、地址、凭据和现场报告不得提交到版本库。
