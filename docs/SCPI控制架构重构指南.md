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

### 5.0 每个阶段的统一执行规则

为了避免“代码已经拆分，但无法确认是否安全”的情况，每个阶段都按以下顺序执行。阶段内的子步骤可以继续拆成独立提交，但不能跳过验证步骤。

1. **确认范围**：列出本阶段允许新增、修改和不应修改的文件；先检查工作区，避免覆盖其他改动。
2. **建立契约**：先确定接口、参数单位、异常类型、命令格式和清理语义，再开始写实现。
3. **先写离线测试**：优先使用 Mock transport、Mock driver 或仿真 session 固定行为，不以真实设备作为唯一验证手段。
4. **实现最小能力**：一次只增加当前阶段所需的能力，不顺带迁移无关仪器或测试流程。
5. **接入一个调用方**：选择一个最小真实调用链完成接入，确认新旧入口可以并存。
6. **执行分层检查**：先运行新增单元测试，再运行相关模拟测试，最后运行完整离线测试和应用检查。
7. **记录结果和回滚点**：记录变更文件、命令、结果、未执行项目、已知问题和恢复旧入口的方法。
8. **阶段门禁**：只有验收标准全部满足、无新增回归、回滚路径可用时，才进入下一阶段。

每个阶段的实施记录建议额外补充以下字段：

```text
阶段目标：
允许修改范围：
接口或行为变更：
新增测试：
接入的调用方：
回滚开关或旧入口：
未完成项：
```

### 阶段 0：建立基线

目标是确认重构开始前项目状态可追踪。

详细步骤：

0.1. **确认工作区和基线提交**。

- 执行 `git status --short`，记录是否存在未提交、未跟踪或用户正在进行的改动。
- 记录当前分支、提交号和项目版本信息。
- 不在基线阶段清理、覆盖或回退已有改动。

0.2. **确认 Python 环境**。

- 读取项目根目录 `.env` 中的 `AUTO_TEST_PYTHON`。
- 确认该解释器路径存在，并记录 Python 版本和 Conda 环境。
- 后续所有测试均使用该解释器或项目包装脚本，不使用 `base` 环境或裸 `python`。

0.3. **执行完整离线测试**。

- 使用项目规定的命令运行现有测试：

  ```powershell
  ./run_tests.ps1
  ```

- 记录总数、通过数、失败数、错误数、退出码和运行时长。
- 区分测试失败、环境缺失、配置警告和真实设备未连接等不同状态。

0.4. **运行应用检查**：

  ```powershell
  ./start_gui.bat --check
  ./start_gui.bat --validate-config
  ```

0.5. **冻结结果**。

- 将命令、环境、提交号和已知问题写入 `docs/baseline/` 下的基线记录。
- 对已有失败建立清单，后续阶段不得把既有失败误报为重构回归。
- 明确真实设备 smoke 是否执行；未执行时不得把离线通过描述为硬件验证通过。

0.6. **设置阶段边界**。

- 为下一阶段列出允许修改的目录和文件。
- 明确第一处回滚点：删除新增模块或关闭新入口后，旧代码仍能运行。

验收标准：

- 重构前的测试基线已记录。
- 配置校验和应用检查结果明确。
- 未覆盖或未解决的问题已经区分为既有问题和新增问题。

### 阶段 1：建立 transport 层

目标是隔离 VISA 通信，并提供无需真实仪器的测试替身。

详细步骤：

1.1. **盘点旧通信入口**：搜索 `pyvisa.ResourceManager`、`resource.write`、`resource.query`、`resource.timeout` 和 `resource.close` 的调用位置，记录现有超时单位、异常类型和关闭顺序。

1.2. **创建包结构**：创建 `Auto_control/transport/` 及其 `__init__.py`，先只导出协议和实现类，不改动旧入口。

1.3. **定义通信契约**：确定 `write(command: str)`、`query(command: str) -> str`、`close()` 的行为；补充空命令处理、返回值类型、重复关闭和关闭后调用的约定。

1.4. **定义异常边界**：建立通信异常基类，并明确 VISA 原始异常如何包装；异常中保留操作类型、命令摘要和原始异常，避免记录敏感信息或完整大数据返回值。

1.5. **实现 VISA transport**：通过构造函数注入已有 resource；实现超时设置、写入、查询和关闭；禁止在 transport 中判断仪器类型或拼接仪器能力命令。

1.6. **实现 Mock transport**：记录有序操作列表，支持预设查询返回值、预设查询异常、写入异常和重复关闭；提供测试读取记录的方式，但不让测试依赖内部实现细节。

1.7. **编写测试**：覆盖写入顺序、查询返回、超时转换、异常传播、重复关闭、关闭后行为和 Mock/VISA 接口一致性。

1.8. **执行阶段检查**：先运行 transport 单测，再运行完整离线测试；确认旧 `InstrumentControl` 尚未被迫切换到新 transport，保证本阶段可独立回滚。

验收标准：

- Mock transport 可以在无仪器环境下记录命令。
- VISA transport 不包含信号源、频谱仪或电源的业务逻辑。
- `close()` 可以重复调用，且不会破坏测试清理流程。
- 超时和通信异常能够向上层传递或转换为明确异常。

### 阶段 2：建立信号源 driver

这是第一条纵向切片，优先验证完整分层链路。

详细步骤：

2.1. **盘点信号源现状**：从 `instrument_control.py` 提取频率、功率、RF 开关相关旧方法、命令格式、单位转换、范围限制和异常行为，形成迁移对照表。

2.2. **定义 driver 构造契约**：创建 `SignalGeneratorDriver(transport, ...)`，所有通信对象从外部注入；driver 内不得调用 `pyvisa.ResourceManager()`、读取配置文件或依赖 Qt。

2.3. **实现最小能力**：

   ```text
   set_frequency_hz()
   set_power_dbm()
   set_rf_enabled()
   close()
   ```

2.4. **明确单位和格式**：内部统一使用 `frequency_hz` 和 `power_dbm`；集中处理浮点格式化、正负号、单位后缀和命令终止符，避免每个方法各自拼接。

2.5. **实现参数校验**：在发送命令前检查有限数值、频率范围、功率范围和必要的状态约束；校验失败时不得产生任何 transport 操作。

2.6. **实现查询能力时保持边界**：若需要读取当前频率、功率或 RF 状态，单独定义查询方法和返回值解析，不把查询隐式加入设置动作。

2.7. **编写命令级测试**：使用 `MockScpiTransport` 验证命令文本、命令顺序、参数格式、异常时无发送，以及 RF 开关不会改变频率和功率设置。

2.8. **完成第一条纵向链路**：用一个最小 action 调用 driver，验证 `action -> driver -> Mock transport` 链路；此时不迁移现有测量服务。

2.9. **保留回滚点**：新 driver 仅作为新增能力存在；旧信号源方法继续可用，直到后续 action 和兼容适配器验证完成。

验收标准：

- `set_frequency_hz(2.4e9)` 能生成预期 SCPI。
- `set_power_dbm(-20)` 能生成预期 SCPI。
- RF 开关动作不会隐式修改功率设置。
- 非法频率能够在发送命令前被拒绝。
- driver 测试不需要真实仪器。

### 阶段 3：建立频谱仪 driver 和 action

详细步骤：

3.1. 盘点旧频谱仪命令，区分配置命令、触发/等待命令和测量查询命令，记录返回值单位及设备特殊格式。

3.2. 创建 `SpectrumAnalyzerDriver`，注入 `ScpiTransport`，先实现中心频率和 Span 两个最小配置能力。

3.3. 分别实现 RBW、VBW 等可选配置；每个参数使用明确的 Hz 单位，并在发送前验证正数和设备允许范围。

3.4. 实现峰值功率查询：明确是否需要触发、等待稳定和读取结果；将 SCPI 返回字符串解析为 `float`，拒绝空值、非数字值和单位不符合约定的返回值。

3.5. 定义测量异常、查询超时和设备错误的转换规则，保留足够上下文供上层显示和日志记录。

3.6. 创建频谱仪 action，仅组合稳定的 driver 能力；action 不拼接 SCPI，不直接处理 VISA 异常。

3.7. 编写测试：覆盖配置命令顺序、查询命令、返回值解析、异常返回、超时、非法参数不发送和 action 转发。

3.8. 用仿真 transport 或仿真 session 运行一次“配置频谱仪并读取峰值”的最小流程，再决定是否进入阶段 4。

推荐能力：

```text
set_center_frequency_hz()
set_span_hz()
set_resolution_bandwidth_hz()
set_video_bandwidth_hz()
measure_peak_power_dbm()
```

### 阶段 4：建立电源 driver 和 action

详细步骤：

4.1. 盘点电源旧实现，分别列出通道选择、设压、限流、保护、输出和读回命令；确认每个命令的通道参数、单位和设备返回格式。

4.2. 定义单台电源、单个通道的 driver 契约；通道标识作为参数传入，driver 不解释 gate、drain、DUT 等业务角色。

4.3. 依次实现设压、限流、过压保护、过流保护和输出开关；普通设置动作不得自动打开输出。

4.4. 实现电压、电流读回，将设备字符串转换为 `voltage_v`、`current_a`；明确读回失败、越界和非数字返回的异常。

4.5. 实现电源 action，提供稳定的动作命名和日志上下文；业务角色解析、设备分配和上电顺序留给配置组装层及 flow。

4.6. 使用 Mock transport 对每个通道逐项验证读写命令，特别覆盖通道切换不会污染后续命令、非法通道不会发送以及重复关闭输出可安全执行。

4.7. 使用仿真电源运行一次单通道设置、读回和关闭流程，确认 driver 不依赖真实资源或 GUI。

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

详细步骤：

5.1. **创建动作模块**：

   ```text
   signal_generator_actions.py
   spectrum_analyzer_actions.py
   power_supply_actions.py
   ```

5.2. 为每个 action 明确输入、输出、单位、异常和是否允许重复调用；构造函数接收 driver 或端口，禁止接收裸 VISA resource。

5.3. 添加统一动作上下文：动作名称、仪器标识、通道标识和可选的关联测量 ID。日志只能由 action 或上层注入，不能让 driver 依赖 GUI 日志对象。

5.4. 建立 action 与现有端口协议的映射，优先复用 `instrument/ports.py` 中已经存在的能力；发现旧能力名称不一致时，增加适配器而不是立即改动全部调用方。

5.5. 选择一个最小测量服务调用方接入 action，验证输入输出、取消、进度事件和异常行为没有无关变化。

5.6. 逐个迁移其他调用方，每迁移一个调用方就运行对应测试；禁止在同一提交中同时改动多个不相关测量流程。

5.7. 保留 `InstrumentControl` 作为旧代码兼容入口，并记录每个旧方法当前是旧实现、转发到新 action，还是尚未迁移。

5.8. 增加真实 driver、仿真 driver 和 Mock driver 三种组合的测试，确认 action 的行为不依赖具体设备实现。

验收标准：

- 上层代码不需要知道 SCPI 命令文本。
- action 不创建连接、不加载全局配置、不依赖 Qt。
- 同一个 action 可以用于真实 driver 和仿真 driver。
- 原有测量服务的输入输出行为没有无关变化。

### 阶段 6：建立安全 flow

先实现设备安全流程，再实现复杂测量流程。

详细步骤：

6.1. **先定义安全状态机**：列出连接、RF、Gate、Drain 等状态，以及每个状态允许的动作、失败后的补救动作和重复执行语义。

6.2. **实现 `power_on_flow`**：校验配置和通道映射，设置目标电压及限流，确认参数已发送后打开 Gate，等待稳定，再打开 Drain；每一步记录动作结果。

6.3. **实现 `power_off_flow`**：先关闭 Drain，等待稳定，再关闭 Gate；即使第一步失败也继续尝试后续安全动作，并汇总所有错误。

6.4. **实现 RF 安全关闭**：在任何连接关闭前关闭 RF；将 RF 关闭设计为可重复调用，并处理设备已经关闭的返回情况。

6.5. **接入取消和异常清理**：为正常结束、用户取消、driver 异常、超时和部分初始化成功分别编写测试，确认都进入同一清理入口。

6.6. **迁移 `measurement_lifecycle.py`**：先保持现有清理顺序和错误汇总行为，再让生命周期管理器调用新 flow；迁移期间保留旧实现开关。

6.7. **验证不变量**：在任何测试路径中都不得出现 Drain 已打开而清理跳过、RF 仍打开就关闭连接、通道角色重叠或重复关闭导致流程中断。

6.8. **最后实现复杂 flow**：安全 flow 稳定后，再实现频率扫描、功率扫描等流程；每个流程先定义步骤表、取消点、进度事件和失败清理，再编写代码。

安全要求：

- RF 关闭优先于连接关闭。
- 掉电时先关闭 Drain，再关闭 Gate。
- 清理动作应尽力执行并汇总错误。
- 取消、异常和正常结束都必须进入清理路径。
- 重复执行关闭动作不应导致新的硬件错误。

### 阶段 7：迁移测量服务和旧入口

详细步骤：

7.1. **建立调用方清单**：列出 GUI、后台 worker、命令行、测量服务和测试代码对 `InstrumentControl` 的每个调用，标记迁移目标和当前回滚方式。

7.2. **先迁移低风险服务**：让 `measurement_services.py` 通过端口、action 或 flow 获取能力；先迁移输入输出稳定、设备步骤较少的线损测量。

7.3. **逐条迁移业务流程**：依次迁移驱动功率映射、功放功率扫描和其他测量；每条流程分别验证动作顺序、单位换算、结果组织、取消和清理。

7.4. **检查服务边界**：确认服务中不再出现 VISA resource、SCPI 字符串、设备通道硬编码或 GUI 控件访问；这些内容分别移至 transport、driver、配置组装层或应用入口。

7.5. **保留兼容适配**：旧入口继续提供历史方法名和参数格式，通过适配器转发到新能力；兼容层只做参数转换和生命周期衔接，不新增业务逻辑。

7.6. **切换一个应用入口**：先让仿真或离线入口使用新组装路径，确认结果后再切换 GUI 默认组装；真实设备入口必须单独经过硬件 smoke 门禁。

7.7. **完成删除前审计**：搜索旧方法和直接 SCPI 调用，确认没有调用方遗漏；检查配置、日志、异常和清理行为的兼容性。

7.8. **执行删除前验证**：运行完整离线测试、模拟流程、应用检查和必要的只读硬件 smoke；保留一个可恢复旧入口的提交或配置开关。

7.9. **分批删除旧实现**：先删除未使用的重复命令，再删除旧内部方法，最后才考虑缩小 `InstrumentControl`；每次删除后独立验证，不进行无法回滚的大批量清理。

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
