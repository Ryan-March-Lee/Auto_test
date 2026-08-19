# 旧 `config.json` 兼容转换执行计划

> 文档性质：阶段 1.1 的专项执行计划。
>
> 目标：在不改变旧启动方式、旧测量流程和旧配置文件的前提下，将旧版 `config.json` 转换为新的测试方案对象和单次运行资源映射对象。
>
> 当前状态：`config_models.py` 已提供新模型和离线校验，但尚未实现旧配置转换函数；未接入 GUI、启动器或真实仪器。

## 1. 目标与边界

### 1.1 本步骤要实现的结果

旧配置可以被只读加载，并转换为：

```text
旧 config.json
    -> LegacyConfigConversionResult
       ├── TestPlan
       ├── RunResourceMapping
       ├── warnings
       └── unresolved_fields
```

转换结果必须能够：

- 通过新模型的序列化和离线校验，或明确返回不能运行的错误。
- 保留旧配置中可可靠识别的测试方案参数。
- 将旧仪器地址作为“历史默认运行映射”，不当作永久固定设备。
- 将旧电源分配转换为现场映射的候选值，不自动假定下一次仍使用相同电源。
- 对无法从旧配置可靠推断的供电角色、通道连接和保护语义明确报警。

### 1.2 本步骤不做的事

- 不修改原始 `config.json`。
- 不删除旧字段、旧入口或旧读取逻辑。
- 不自动写回新的 `config.json`。
- 不自动连接 VISA 仪器。
- 不执行电源上电、RF 开关或任何测量。
- 不把 `PS4`、固定 IP 或旧通道名认定为永久现场配置。
- 不修改线损、驱动映射、主功放公式和 SCPI 命令。
- 不在本步骤接入 GUI。

## 2. 当前新旧字段差异

### 2.1 测试方案字段映射

| 旧字段 | 新字段 | 转换方式 | 风险/备注 |
| --- | --- | --- | --- |
| `test_frequencies` | `frequencies.values` | 原样复制数值，单位设为 `GHz` | 需做有限数值、正数、重复值校验 |
| `signal_source.start_power` | `signal_source.start_power` | 原样复制 | 单位设为 `dBm` |
| `signal_source.stop_power` | `signal_source.stop_power` | 原样复制 | 单位设为 `dBm` |
| `signal_source.step` | `signal_source.step` | 原样复制 | 必须大于 0 |
| `compression_point.type` | `compression_point.type` | 原样复制 | 只允许新模型支持的值 |
| `attenuator.type` | `attenuator.value` | 解析如 `30dB` 为数值 `30` | 只允许有限非负 dB 字符串 |
| `dut_config.max_input_power` | `dut.max_input_power` | 原样复制 | 必须是有限数值 |
| `dut_config.power_supply_count` | 不直接进入测试方案 | 仅作历史信息或一致性检查 | 不能代替具体角色和通道映射 |
| `driver_mode.enabled` | `driver_mode.enabled` | 原样复制且严格要求布尔值 | 不可靠的驱动通道需报警 |
| 旧电源通道电气参数 | `dut.power_roles`/`driver_mode.power_roles` | 只有明确角色后才能转换 | 旧配置本身通常只有 CH 名称，没有 gate/drain 角色 |

### 2.2 运行映射字段映射

| 旧字段 | 新字段 | 转换方式 | 风险/备注 |
| --- | --- | --- | --- |
| `instruments.signal_generator.address` | `instruments.signal_generator.visa_address` | 仅当启用且地址非空时复制 | 作为历史默认值 |
| `instruments.spectrum_analyzer.address` | `instruments.spectrum_analyzer.visa_address` | 仅当启用且地址非空时复制 | 作为历史默认值 |
| `instruments.power_supplies.<name>.address` | `instruments.power_supply.visa_address` | 只有明确选择的电源才可复制 | 多个电源不能无提示自动选择 |
| `power_supply_assignment.*.supplies.*.name` | 运行映射电源候选 | 保留设备名和候选地址 | 不能把设备名变成永久配置 |
| `power_supply_assignment.*.supplies.*.channel` | `dut_power_channels[].channel` 或驱动通道 | 仅作为现场映射候选 | 角色需要独立确认 |
| 旧配置无操作者/时间 | `operator`、`run_datetime` | 设为 `null` 或历史来源标识 | 不伪造历史信息 |
| 旧配置无接线确认 | `wiring.confirmed` | 必须为 `false` | 不能自动通过运行前安全校验 |

## 3. 关键设计决策

### 3.1 转换结果对象

建议新增：

```python
@dataclass(frozen=True)
class LegacyConfigConversionResult:
    test_plan: TestPlan
    run_mapping: RunResourceMapping
    warnings: List[ConfigIssue]
    unresolved_fields: List[str]
```

建议新增接口：

```python
legacy_config_to_test_plan(config) -> TestPlan
legacy_config_to_run_mapping(config, *, selected_supply=None) -> RunResourceMapping
convert_legacy_config(config, *, selected_supply=None) -> LegacyConfigConversionResult
convert_legacy_config_file(path, *, selected_supply=None) -> LegacyConfigConversionResult
```

所有接口必须只处理内存或文件，不建立仪器连接。

### 3.2 多电源处理策略

旧配置可能包含多个电源，例如 `PS1` 到 `PS4`，但新运行映射需要知道本次实际使用哪一个电源。

第一版必须采用以下策略之一，并写入代码注释和测试：

1. **显式选择策略，推荐**：调用方传入 `selected_supply="PS4"`，转换器只将该电源作为候选运行设备。
2. **唯一已启用策略**：当且仅当旧配置只有一个 `enabled: true` 的电源时自动选择；否则返回未决警告，不生成可运行映射。
3. **不选择策略**：不填充电源 VISA 地址，只生成包含候选电源列表的转换结果，由后续运行配置步骤填写。

禁止以下行为：

- 按字典顺序自动选择第一个电源。
- 看到 `PS4` 就自动选择 `PS4`。
- 因为旧配置的 `power_supply_count` 为 1 就猜测具体电源。
- 将未启用电源的历史参数当作当前运行参数。

### 3.3 供电角色处理策略

新的测试方案使用稳定角色，例如：

```json
"power_roles": {
  "gate": {"...": "..."},
  "drain": {"...": "..."}
}
```

旧配置只有：

```json
"channel": ["CH1", "CH2"]
```

无法可靠判断哪个通道是 gate、哪个通道是 drain。因此：

- 不按 `CH1`、`CH2` 的顺序猜测角色。
- 不把 `carrier` 直接转换成 DUT gate 或 drain。
- 转换器可以保留旧通道列表作为 `legacy_channel_candidates`。
- 如果调用方显式提供角色映射，例如 `{"gate": "CH1", "drain": "CH2"}`，才生成角色到通道的候选映射。
- 没有角色确认时，转换结果必须包含 `unresolved_fields`，且不能通过正式运行校验。

### 3.4 驱动模式不一致策略

当前旧配置的典型风险是：

```text
driver_mode.enabled = true
driver_amplifier.supplies = {}
```

处理规则：

- 保留 `driver_enabled=true`。
- 不虚构驱动电源通道。
- 增加警告：驱动模式启用但没有驱动供电映射。
- 转换出的运行映射不能通过驱动通道完整性校验。
- 只有现场补充驱动角色、实际通道和接线确认后，才允许进入连接/上电流程。

### 3.5 电源保护参数策略

旧配置中电压和电流保护值的设备单位及语义尚未完全确认。

- 数值可以原样保留到候选方案中。
- 不将保护关系自动升级为安全结论。
- 转换结果增加警告，指出设备单位和阈值语义待确认。
- 负电压保护值不得使用通用的“非负数”规则误判；其合法性应由具体电源适配器或现场设备规则决定。

## 4. 分阶段执行步骤

### C0：冻结旧配置输入和行为基线

- [ ] 复制当前 `config.json` 到测试临时目录，不修改原文件。
- [ ] 记录旧配置的顶层字段、启用设备、地址、分配关系和校验结果。
- [ ] 确认 `python launcher.py --validate-config` 的行为不改变。
- [ ] 保存当前旧配置校验测试结果。
- [ ] 明确本次转换只读，不连接 VISA。

完成标准：有一份只读测试输入和旧行为记录。

### C1：实现旧字段解析辅助函数

建议新增到 `config_models.py` 或独立文件 `legacy_config_conversion.py`：

```python
parse_legacy_attenuator(value) -> Optional[float]
get_legacy_enabled_instruments(config) -> ...
get_legacy_power_supplies(config) -> ...
get_legacy_assignments(config) -> ...
```

要求：

- [ ] `30dB` 能解析为 `30.0`。
- [ ] `30 dB`、大小写差异的 `dB` 按既有规则处理。
- [ ] `attenuator` 缺失、类型错误或负数返回明确错误。
- [ ] `NaN`、无穷数、布尔值不作为数值接受。
- [ ] 旧字段不是对象时不抛出难以定位的 `AttributeError`。

完成标准：辅助函数没有硬件依赖，边界测试先行。

### C2：实现测试方案转换

实现：

```python
legacy_config_to_test_plan(config) -> TestPlan
```

步骤：

1. 检查旧配置为对象。
2. 转换频率和信号源扫描参数。
3. 转换压缩点。
4. 将旧 `attenuator.type` 转换为新 `attenuator.value`。
5. 转换 DUT 最大输入功率。
6. 转换驱动模式开关。
7. 从旧电源配置提取候选电气参数，但不把 CH 名称自动命名为稳定角色。
8. 写入 `schema_version="1.0"`、`template=false` 和来源元数据。
9. 执行 `validate_test_plan`。

对于旧配置无法提供的 DUT 角色参数：

- 允许构造中间 `TestPlan`。
- 必须记录未决字段。
- 不能将中间结果伪装为完整可运行方案。

### C3：实现运行映射转换

实现：

```python
legacy_config_to_run_mapping(config, *, selected_supply=None) -> RunResourceMapping
```

步骤：

1. 转换信号源地址。
2. 转换频谱仪地址。
3. 根据明确的 `selected_supply` 转换电源地址；未明确选择时按多电源策略处理。
4. 保留旧电源名称、地址和通道作为历史候选信息。
5. 转换驱动模式开关。
6. 转换 DUT 和驱动功放的旧通道列表，但不猜测 gate/drain 角色。
7. 设置 `wiring.confirmed=false`。
8. 设置来源标识为 `legacy_config`。
9. 不填充虚构的操作者、时间和接线说明。
10. 执行 `validate_run_mapping`，预期未补充现场信息时不能通过。

### C4：实现组合转换

实现：

```python
convert_legacy_config(config, *, selected_supply=None)
```

要求：

- [ ] 同时返回测试方案和运行映射。
- [ ] 返回所有警告，不只返回第一条。
- [ ] 返回所有未决字段路径。
- [ ] 结果中区分“可转换但需现场确认”和“无法转换”。
- [ ] 不因存在警告就自动判定安全。
- [ ] 不因旧配置能读取就自动允许上电。

建议的状态：

```text
converted      # 字段已转换，但可能有警告
needs_review   # 缺少角色、通道或现场确认
invalid        # 旧字段本身非法，无法可靠转换
```

## 5. 测试计划

建议新增：

```text
tests/test_legacy_config_conversion.py
```

### 5.1 正常转换

- [ ] 当前仓库 `config.json` 能够只读加载。
- [ ] 频率数量和数值保持一致。
- [ ] 起止功率和步进保持一致。
- [ ] `5dB` 保持为 `5dB`。
- [ ] `30dB` 转换为 `30.0`。
- [ ] DUT 最大输入功率保持一致。
- [ ] `driver_mode.enabled` 保持为 `true`。
- [ ] 不启用真实仪器连接。

### 5.2 多电源选择

- [ ] 显式选择 `PS4` 时只使用 `PS4` 地址和通道候选。
- [ ] 选择不存在的电源时返回错误。
- [ ] 不传选择且有多个启用/候选电源时返回未决警告。
- [ ] 不按字典顺序自动选择电源。
- [ ] 未启用的电源不会成为当前运行电源。

### 5.3 角色和通道

- [ ] 旧 `CH1/CH2` 不会自动变成 `gate/drain`。
- [ ] 没有角色确认时记录未决字段。
- [ ] 显式传入角色映射后可以生成 `gate/drain` 绑定。
- [ ] DUT 与驱动通道重复时校验失败。
- [ ] 驱动模式启用但无驱动分配时产生警告并不能运行。
- [ ] 现场接线未确认时运行映射校验失败。

### 5.4 异常输入

- [ ] 根节点不是对象时返回明确错误。
- [ ] 必需顶层字段缺失时返回字段路径。
- [ ] `attenuator.type` 为 `"attenuator"` 时失败。
- [ ] 频率包含 `NaN` 或无穷数时失败。
- [ ] 电源通道结构错误时不静默丢弃。
- [ ] 分配引用不存在的电源时失败。
- [ ] 分配引用不存在的通道时失败或标记未决。
- [ ] `enabled` 为字符串时失败，不进行 Python 真值转换。

### 5.5 回归测试

- [ ] 现有 `tests/test_config_validation.py` 全部通过。
- [ ] 现有启动器测试全部通过。
- [ ] GUI 导入测试全部通过或仅受环境依赖影响。
- [ ] `launcher.py --validate-config` 输出和退出码不变。
- [ ] 转换函数不会导入或调用 `InstrumentControl`。

## 6. 错误、警告和未决字段规则

### 6.1 错误

以下情况属于错误，转换不能生成可信方案：

- JSON 根节点错误。
- 必需测试参数类型错误。
- 数值为非有限数。
- 功率步进小于等于零。
- 起始功率大于终止功率。
- 压缩点或衰减器格式非法。
- 明确选择的电源不存在。
- 明确选择的电源地址非法。

### 6.2 警告

以下情况可以保留中间结果，但不得自动运行：

- 有多个候选电源但没有明确选择。
- 旧通道没有稳定供电角色。
- 驱动模式开启但没有驱动分配。
- 电源保护值语义尚未确认。
- 旧配置缺少操作者、时间或现场接线确认。

### 6.3 未决字段

未决字段必须使用字段路径记录，例如：

```text
run_mapping.instruments.power_supply
run_mapping.dut_power_channels[].role
run_mapping.wiring.confirmed
test_plan.dut.power_roles.gate
```

不得用默认值掩盖未决信息。

## 7. 与旧入口的接入顺序

### 第一步：独立调用

先只在测试和开发脚本中调用转换器：

```python
result = convert_legacy_config_file("config.json", selected_supply="PS4")
```

此时不改 `launcher.py` 和 GUI。

### 第二步：增加只读检查命令

可选增加：

```powershell
python launcher.py --convert-legacy-config --input config.json --output temp/converted
```

要求：

- 默认只输出转换报告和候选文件。
- 不覆盖旧 `config.json`。
- 不连接仪器。
- 输出错误、警告和未决字段。
- 没有完整运行映射时返回非零退出码或明确的 `needs_review` 状态。

### 第三步：接入 GUI 前的默认值

只有转换测试稳定后，才允许 GUI 使用转换结果作为默认填充值：

- GUI 仍允许用户修改设备地址和通道。
- GUI 必须要求现场接线确认。
- 转换结果不能绕过新运行配置校验。
- 旧 GUI 流程仍保留回滚开关。

## 8. 回滚方案

发生以下情况时停止接入并恢复旧流程：

- 旧配置读取失败。
- 旧启动命令退出码改变。
- 转换后测量参数发生变化。
- 转换后设备或通道被错误选择。
- 未确认接线时允许连接或上电。
- 旧结果读取受到影响。

回滚动作：

1. 禁用转换器调用方的功能开关。
2. 保留 `config_models.py` 和转换测试，避免删除证据。
3. 恢复旧 `config.json` 读取路径。
4. 保留转换失败样例和测试输出。
5. 将问题记录到 `重构执行.md` 的风险登记区。

## 9. 完成门槛

旧配置兼容转换只有满足以下条件才算完成：

- [ ] 当前 `config.json` 可以被只读转换。
- [ ] 测试方案字段转换结果经过逐字段对照。
- [ ] 多电源不会被静默选错。
- [ ] 旧物理通道不会被误当成稳定供电角色。
- [ ] 驱动模式不一致时有警告且不能绕过安全校验。
- [ ] 转换结果可以序列化和重新读取。
- [ ] 旧启动器、旧配置校验和旧结果读取回归通过。
- [ ] 全部转换测试通过。
- [ ] 转换过程未连接真实仪器。
- [ ] 保留旧入口和明确回滚方式。

完成后同步更新：

- `重构执行.md` 的阶段 1.1 和 R-005。
- `重构基线.md` 的配置模型和里程碑。
- 本文各项任务状态。

## 10. 建议提交顺序

```text
提交 C-1：增加旧字段解析辅助函数和单元测试
提交 C-2：实现旧配置到 TestPlan 的转换
提交 C-3：实现多电源候选和显式选择策略
提交 C-4：实现旧配置到 RunResourceMapping 的转换
提交 C-5：实现组合转换结果、警告和未决字段
提交 C-6：补齐异常输入和回归测试
提交 C-7：增加只读转换报告命令（可选）
提交 C-8：在 GUI 接入前进行一次离线验收
```

每个提交后执行：

```powershell
conda activate Auto_test
python -m unittest discover -s tests -v
python -m compileall -q -x "__pycache__|test_results" .
python launcher.py --validate-config
git diff --check
```

## 11. 本步骤的第一项实际工作

先不要写转换函数。第一项应是：

1. 将当前 `config.json` 复制到测试临时目录。
2. 编写字段对照测试夹具。
3. 明确多电源选择策略，推荐使用显式 `selected_supply`。
4. 明确旧 `CH1/CH2` 到 `gate/drain` 是否有现场依据。
5. 在没有角色依据时保留未决状态，而不是猜测。

这五项确认后，再实现 `legacy_config_to_test_plan`。
