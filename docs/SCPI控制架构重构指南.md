# SCPI 控制架构重构指南

## 1. 文档目的

本文档记录项目 SCPI 控制架构的重构目标、分层原则、实施步骤和验收标准，作为后续重构的执行依据。

本次重构的核心目标是：

> 用 `transport` 封装通信，用 `driver` 封装仪器 SCPI 能力，用 `action` 封装可复用动作，用 `flow` 组合完整测试流程。

重构应保持现有测试能力可用，并采用渐进迁移方式。新代码验证通过后，才能替换对应的旧实现。

## 2. 当前项目状态

当前项目已经具备部分目标架构的基础：

- `instrument_control.py` 集中了 VISA 连接、SCPI 命令、仪器能力、电源角色解析和上下电安全流程。
- `instrument/ports.py` 已定义 `SignalGeneratorPort`、`SpectrumAnalyzerPort`、`PowerSupplyPort`、`InstrumentSession` 等能力接口。
- `instrument/simulation.py` 已提供仿真和安全会话相关实现，可作为后续离线验证基础。
- `measurement_services.py` 已将部分测量编排从 GUI 中分离，并通过端口协议依赖仪器能力。
- `measurement_lifecycle.py` 已统一 RF 关闭、电源清理和连接关闭的安全清理顺序。

当前主要问题是 `instrument_control.py` 职责过重，底层通信、仪器驱动、业务动作和安全流程耦合在一起。后续重构应逐步拆分这些职责，同时保留兼容入口，避免一次性迁移导致测量功能失效。

## 3. 目标目录结构

目标结构如下：

```text
Auto_control/
    __init__.py
    transport/
        __init__.py
        scpi_transport.py
        visa_transport.py
        mock_transport.py
    driver/
        __init__.py
        signal_generator.py
        spectrum_analyzer.py
        power_supply.py
    action/
        __init__.py
        signal_generator_actions.py
        spectrum_analyzer_actions.py
        power_supply_actions.py
    flow/
        __init__.py
        power_on_flow.py
        power_off_flow.py
        frequency_sweep_flow.py
        power_sweep_flow.py
```

目录名称统一使用小写，包名 `Auto_control` 保留项目当前确定的命名。Python 模块名使用小写下划线形式。

### 3.1 transport 层

`transport` 只负责通信，不理解具体仪器能力和测试业务。

职责包括：

- 打开、写入、查询和关闭 VISA/SCPI 资源。
- 设置通信超时。
- 统一处理通信层异常。
- 提供真实 VISA 实现和离线 Mock 实现。
- 可选记录实际发送的命令和查询返回值。

transport 不负责：

- 生成“设置频率”或“设置功率”等仪器命令。
- 解释业务参数。
- 自动打开 RF 或电源输出。
- 实现功放上电、掉电顺序。

基础接口建议如下：

```python
from typing import Protocol


class ScpiTransport(Protocol):
    def write(self, command: str) -> None:
        ...

    def query(self, command: str) -> str:
        ...

    def close(self) -> None:
        ...
```

### 3.2 driver 层

`driver` 负责把具体仪器能力转换为 SCPI 命令，是 SCPI 命令的唯一主要归属层。

建议按仪器类型拆分：

- `SignalGeneratorDriver`
- `SpectrumAnalyzerDriver`
- `PowerSupplyDriver`

driver 应负责：

- 校验仪器能力参数。
- 统一输入单位。
- 生成并发送具体 SCPI。
- 解析查询返回值。
- 抛出明确的设备或参数异常。

driver 不负责：

- 读取 `config.json`。
- 组合完整测量流程。
- 决定所有电源的业务上电顺序。
- 处理 GUI 事件。

内部接口统一使用明确单位：

| 参数 | 单位 | 推荐命名 |
| --- | --- | --- |
| 频率 | Hz | `frequency_hz` |
| 功率 | dBm | `power_dbm` |
| 带宽 | Hz | `bandwidth_hz` |
| 电压 | V | `voltage_v` |
| 电流 | A | `current_a` |
| 时间 | s | `timeout_s`、`settle_time_s` |

### 3.3 action 层

`action` 为上层提供稳定、可复用的设备动作入口。动作可以调用一个或多个同类 driver 能力，但不应承担跨设备测试流程。

典型动作包括：

```text
set_frequency_hz()
set_power_dbm()
set_rf_enabled()
set_center_frequency_hz()
set_span_hz()
measure_peak_power_dbm()
set_voltage_v()
set_current_limit_a()
set_output_enabled()
read_voltage_v()
read_current_a()
```

动作层约束：

- 不直接读取全局配置。
- 不依赖 GUI。
- 不直接创建 VISA 连接。
- 不在普通设置动作中隐式打开 RF 或电源。
- 参数和单位必须明确。
- 失败时抛出可识别异常。
- 可以被 Mock driver 或 Mock transport 单独验证。

如果 action 只是简单转发 driver 方法，也可以保持很薄。action 层的价值是提供面向应用和工作流的稳定入口，而不是重复 SCPI 命令。

### 3.4 flow 层

`flow` 负责按照业务、安全策略和设备依赖组合多个 action。

设备流程示例：

- `power_on_flow`
- `power_off_flow`
- 信号源准备流程
- 频谱仪准备流程

测试流程示例：

- 宽频扫频
- 功率扫描
- 驱动功率映射
- 线损测量

flow 可以调用多个仪器的 action，但不应直接拼接 SCPI。功放“先栅后漏”上电和“先漏后栅”掉电属于 flow 或安全策略，不属于普通电源 driver。

## 4. 依赖方向

依赖方向必须保持单向：

```text
flow
  ↓
action
  ↓
driver
  ↓
transport
```

测量服务可以依赖 action、flow 或端口协议，但不应直接依赖 VISA 资源。GUI、CLI 和后台 worker 属于应用入口，只调用服务或 flow，不拼接 SCPI。

以下依赖关系禁止出现：

- transport 依赖 flow 或 GUI。
- driver 读取业务配置并启动完整测试。
- action 直接创建 `pyvisa.ResourceManager()`。
- flow 直接调用 `resource.write()` 或 `resource.query()`。
- GUI 直接发送 SCPI。

## 5. 分阶段实施计划

### 阶段 0：建立基线

目标是确认重构开始前项目状态可追踪。

步骤：

1. 检查工作区状态，避免覆盖已有用户改动。
2. 使用项目规定的 Python 环境运行现有测试。
3. 运行应用检查：

   ```powershell
   ./start_gui.bat --check
   ./start_gui.bat --validate-config
   ```

4. 记录当前测试结果和已知失败项。
5. 后续每个阶段只修改该阶段范围内的文件。

验收标准：

- 重构前的测试基线已记录。
- 配置校验和应用检查结果明确。
- 未覆盖或未解决的问题已经区分为既有问题和新增问题。

### 阶段 1：建立 transport 层

目标是隔离 VISA 通信，并提供无需真实仪器的测试替身。

步骤：

1. 创建 `Auto_control/transport/`。
2. 定义 `ScpiTransport` 协议。
3. 实现 `VisaScpiTransport`：包装已有 VISA resource，支持 `write`、`query`、`close` 和超时。
4. 实现 `MockScpiTransport`：记录写入、查询和关闭操作，并支持预设查询返回值。
5. 明确通信异常类型和关闭行为。
6. 编写 transport 单元测试。

验收标准：

- Mock transport 可以在无仪器环境下记录命令。
- VISA transport 不包含信号源、频谱仪或电源的业务逻辑。
- `close()` 可以重复调用，且不会破坏测试清理流程。
- 超时和通信异常能够向上层传递或转换为明确异常。

### 阶段 2：建立信号源 driver

这是第一条纵向切片，优先验证完整分层链路。

步骤：

1. 创建 `SignalGeneratorDriver`。
2. 注入 `ScpiTransport`，禁止 driver 内部创建 VISA 资源。
3. 实现：

   ```text
   set_frequency_hz()
   set_power_dbm()
   set_rf_enabled()
   close()
   ```

4. 将频率、功率和状态参数转换为设备要求的 SCPI 格式。
5. 验证参数范围和 RF 状态约束。
6. 使用 `MockScpiTransport` 检查实际命令。

验收标准：

- `set_frequency_hz(2.4e9)` 能生成预期 SCPI。
- `set_power_dbm(-20)` 能生成预期 SCPI。
- RF 开关动作不会隐式修改功率设置。
- 非法频率能够在发送命令前被拒绝。
- driver 测试不需要真实仪器。

### 阶段 3：建立频谱仪 driver 和 action

步骤：

1. 创建 `SpectrumAnalyzerDriver`。
2. 实现中心频率、带宽或 Span 配置。
3. 实现峰值功率测量和返回值解析。
4. 处理查询失败、非数字返回值和超时。
5. 使用 Mock transport 验证写入和查询命令。

推荐能力：

```text
set_center_frequency_hz()
set_span_hz()
set_resolution_bandwidth_hz()
set_video_bandwidth_hz()
measure_peak_power_dbm()
```

### 阶段 4：建立电源 driver 和 action

步骤：

1. 创建 `PowerSupplyDriver`。
2. 实现通道电压和限流设置。
3. 实现过压、过流保护设置。
4. 实现输出开关。
5. 实现电压、电流读取。
6. 保持通道名称由配置传入，不在通用 driver 中硬编码 `CH1` 为栅极、`CH2` 为漏极。
7. 使用 Mock transport 验证所有读写命令。

推荐能力：

```text
set_voltage_v()
set_current_limit_a()
set_voltage_protection()
set_current_protection()
set_output_enabled()
read_voltage_v()
read_current_a()
```

电源 driver 只负责单通道设备能力。通道角色解析、设备分配以及先后顺序交给配置和 flow。

### 阶段 5：建立 action 层的稳定入口

当三个主要 driver 可以独立工作后，再正式整理 action 层。

步骤：

1. 创建三个 action 模块：

   ```text
   signal_generator_actions.py
   spectrum_analyzer_actions.py
   power_supply_actions.py
   ```

2. action 接收 driver 或对应端口，而不是裸 VISA resource。
3. 为动作增加统一日志、动作名称和必要的事件接口。
4. 将 `measurement_services.py` 中直接依赖旧能力名称的部分逐步改为依赖 action 或端口。
5. 保留 `InstrumentControl` 作为旧代码兼容入口。

验收标准：

- 上层代码不需要知道 SCPI 命令文本。
- action 不创建连接、不加载全局配置、不依赖 Qt。
- 同一个 action 可以用于真实 driver 和仿真 driver。
- 原有测量服务的输入输出行为没有无关变化。

### 阶段 6：建立安全 flow

先实现设备安全流程，再实现复杂测量流程。

建议顺序：

1. `power_on_flow`：设置参数、打开 Gate、等待稳定、打开 Drain。
2. `power_off_flow`：关闭 Drain、等待稳定、关闭 Gate。
3. RF 安全关闭流程。
4. 统一异常时的清理流程。
5. 再实现频率扫描和功率扫描。

安全要求：

- RF 关闭优先于连接关闭。
- 掉电时先关闭 Drain，再关闭 Gate。
- 清理动作应尽力执行并汇总错误。
- 取消、异常和正常结束都必须进入清理路径。
- 重复执行关闭动作不应导致新的硬件错误。

### 阶段 7：迁移测量服务和旧入口

步骤：

1. 让 `measurement_services.py` 依赖新端口、action 或 flow。
2. 将线损测量迁移到新 action 和 flow。
3. 将驱动功率映射迁移到新 action 和 flow。
4. 将功放功率扫描迁移到新 action 和 flow。
5. 保留旧入口的兼容适配，直到所有调用方完成迁移。
6. 删除旧实现前运行完整测试和应用检查。

`instrument_control.py` 在迁移期间可以作为组装器或兼容适配器存在，但不应继续增加新的业务逻辑。

## 6. 第一阶段建议的最小实现

第一条可执行纵向切片只实现信号源：

```text
Auto_control/
    __init__.py
    transport/
        __init__.py
        scpi_transport.py
        visa_transport.py
        mock_transport.py
    driver/
        __init__.py
        signal_generator.py
    action/
        __init__.py
        signal_generator_actions.py
```

最小功能范围：

```text
set_frequency_hz()
set_power_dbm()
set_rf_enabled()
```

验证示例：

```python
transport = MockScpiTransport()
signal_generator = SignalGeneratorDriver(transport)

signal_generator.set_frequency_hz(2.4e9)
signal_generator.set_power_dbm(-20)
signal_generator.set_rf_enabled(False)
```

验证重点是命令顺序、命令格式、参数单位和异常行为。第一阶段不修改现有测量流程，避免把架构验证和业务迁移绑定在一起。

## 7. 与现有代码的迁移关系

### `instrument_control.py`

作为旧兼容入口逐步缩小职责：

- 连接组装逻辑可迁移到 session 或应用组装层。
- 信号源 SCPI 迁移到 `SignalGeneratorDriver`。
- 频谱仪 SCPI 迁移到 `SpectrumAnalyzerDriver`。
- 电源 SCPI 迁移到 `PowerSupplyDriver`。
- 上下电顺序迁移到安全 flow。
- 旧方法暂时通过适配器转发到新 driver。

### `instrument/ports.py`

继续作为能力协议基础。新 driver 应优先实现或兼容其中定义的仪器端口。随着迁移推进，可以逐步减少 `MeasurementInstrumentPort` 中的旧兼容方法，并使用更细的端口协议。

### `measurement_services.py`

继续负责测量业务编排、取消、进度事件和结果组织。它不应包含 VISA 细节或 SCPI 字符串。

### `measurement_lifecycle.py`

继续承担统一安全清理，后续可以改为调用新的安全 flow，但清理顺序和“尽力执行并汇总错误”的原则必须保留。

## 8. 测试和验证要求

所有新层都必须优先支持离线测试。

建议测试范围：

- transport 是否正确记录写入和查询。
- driver 是否生成正确 SCPI。
- driver 是否正确解析查询返回值。
- 非法参数是否在发送前被拒绝。
- action 是否调用正确的 driver 能力。
- flow 是否保持正确动作顺序。
- 异常和取消时是否执行 RF 关闭及电源清理。
- 仿真设备能否运行完整的基础流程。

项目命令必须使用 `.env` 中 `AUTO_TEST_PYTHON` 指定的解释器。完成阶段性修改后，至少运行：

```powershell
./run_tests.ps1
./start_gui.bat --check
./start_gui.bat --validate-config
```

如果某个阶段只增加底层单元测试，也应运行与该阶段相关的测试，并在提交或阶段记录中说明未运行的检查及原因。

## 9. 设计约束和禁止事项

以下约束在整个重构期间保持有效：

1. 不在 action 或 flow 中直接拼接 SCPI。
2. 不在 driver 中读取 `config.json`。
3. 不在 transport 中实现仪器业务语义。
4. 不在普通设置动作中隐式打开 RF 或电源。
5. 不用固定的 `CH1`、`CH2` 代替电源通道角色配置。
6. 不为了新架构一次性删除可工作的旧入口。
7. 不把 GUI 事件、打印输出和硬件控制强耦合在底层层中。
8. 不使用无明确职责的 `utils` 文件承载不断增长的控制逻辑。
9. 所有内部接口都必须明确参数单位。
10. 所有硬件清理路径都必须考虑异常、取消和重复调用。

## 10. 阶段完成记录模板

每完成一个阶段，应记录以下内容：

```text
阶段：
完成日期：
变更文件：
新增能力：
迁移的旧调用方：
测试命令：
测试结果：
已知问题：
回滚方式：
下一阶段：
```

只有当本阶段的验收标准满足、测试结果明确、且旧功能没有新增回归时，才能进入下一阶段。

## 11. 最终目标

重构完成后，项目应满足以下使用方式：

```text
应用入口
    ↓
测试服务或测试 flow
    ↓
action
    ↓
仪器 driver
    ↓
SCPI transport
    ↓
VISA 或仿真设备
```

新增仪器型号时，只需要实现对应 driver 或 transport 适配；新增测试项目时，主要组合已有 action 和 flow；更换 GUI 或命令行入口时，不需要修改底层 SCPI 控制逻辑。

重构的第一项实际任务是完成阶段 1 和阶段 2 的信号源纵向切片：建立 Mock/VISA transport，完成信号源 driver，并用离线测试验证频率、功率和 RF 开关动作。
