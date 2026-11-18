# Mock 测量数据

本目录是一个**数据包**：在没有真实仪器时，为线损、驱动映射和主功放三个阶段提供
测量所需的输入，以及用作“期望输出”的既有正式结果。

术语在本文件、`docs/运行配置说明.md` 与
`docs/仿真运行链路分阶段重构计划.md` 中统一使用：

| 术语 | 含义 |
| --- | --- |
| 正式运行 | 页面 → 用例 → 服务 → 仪器端口 → 驱动/SCPI/VISA → 真实仪器；结果写入真实 `runs/` 与 `test_results/` |
| 仿真运行 | 同一条正式链路，只把 `MeasurementInstrumentPort` 的读数换成 `readings.json`；运行快照、用例、服务、事件、落盘与归档都是正式代码 |
| 数据包 | 本目录：每个阶段的 `scenario.json`、`readings.json` 与既有正式结果 |
| 原始读数 | 仪器端口会返回的原始 dBm（以及电压/电流），没有经过补偿；补偿、增益、效率、压缩点仍由正式服务计算 |

替换点是**端口读数层**，不是事件层：仿真运行不会“回放 UI 事件”，页面上看到的进度、
实时数据、结果与归档都由正式服务在真实运行里产生。

```text
mock_data/
├─ cable_loss/          # 线损：scenario.json + readings.json + 正式结果
├─ driver_mapping/      # 驱放
└─ amplifier/           # 功放
```

测量配置以根目录 `config.mock.json` 为准；该文件的内容已按本机保存的 Mock 快照覆盖：

```text
test_results/configurations/Mock_20261114_1020/Mock.json
```

配置要点：

- 频点：`1.0` 到 `2.0 GHz`，步进 `0.1 GHz`，共 11 个频点
- 衰减器：`40 dB`
- 压缩点：`5dB`
- 驱动模式：开启（`driver_mode.enabled = true`）
- 驱放预估增益：`40 dB`，驱放最大输入功率：`0 dBm`
- DUT 最大输入功率：`6 dBm`
- 功率扫描计划：`-20 dBm` 到 `0 dBm`（步进 `1 dB`），再 `0 dBm` 到 `5 dBm`
  （步进 `0.5 dB`）
- 信号源功率范围：`-100 ~ 20 dBm`（应用层 `SIGNAL_SOURCE_POWER_MIN_DBM`）

合并后的目标 DUT 输入功率网格共 31 点：

```text
-20, -19, ..., 0, 0.5, 1.0, ..., 5.0 dBm
```

## 仿真运行（方案目标链路）

`docs/仿真运行链路分阶段重构计划.md` 实施到步骤 21～25 之前，当前版本仍执行下面的
“旧回放链路”；本节描述方案目标，而不是当前已经生效的行为。

- **入口**：欢迎页“加载配置”选择显示名称为 `Mock` 的快照 → 配置页保持只读 →
  “连接仪器”进入仿真运行。不打开 VISA、不构造驱动/传输层对象，也不写根
  `config.json` 或命名配置快照。
- **真实运行**：进入时创建真实 `PreparedRun`，运行快照、阶段记录、manifest、归档与
  部分结果保存都走正式实现。运行目录与结果只写入仿真数据根：

  ```text
  temp/simulation_runs/
  ├─ configs/<timestamp>_<short>.json   # 仿真输入草稿
  ├─ runs/<run_id>/                     # 真实运行目录（RunRepository(root=runs)）
  └─ test_results/<stage>/              # 阶段正式结果、CSV、报告
  ```

  真实 `runs/` 与 `test_results/` 在进入、运行、退出前后保持不变；全局历史/可视化
  看不到仿真结果。
- **唯一差异**：`MeasurementInstrumentPort` 的读数来自本目录的 `readings.json`。
  连接、通信、驱动、VISA 不模拟，因此仿真运行**不能**用来验证仪器、驱动或 SCPI。
- **读数包**：进入仿真运行时一次性加载、校验并冻结；运行期间不再读取本目录，运行中
  修改 `readings.json` 不影响已经进入的运行。运行标记记录本次实际使用的摘要。
- **守卫**：仿真运行写入 `inputs/simulation.json` 并在 manifest 登记；
  `connect_instruments()` 在解析硬件快照、打开任何 VISA 资源之前拒绝它，
  `load_prepared_run()` 同样拒绝。仿真运行是一次性的：返回欢迎页或退出后不提供恢复
  入口，需要重跑就重新进入并创建新运行。
- **时序**：服务保留真实时序（线损 1 秒/点、驱放与主功放 5 秒/点），仿真运行默认注入
  `time_scale=50` 的快放，使三段在几分钟内完成；`time_scale=1` 保留为手动验收选项。
  快放只改休眠时长，不改事件顺序、进度分母或结果计算。
- **生成**：三份 `readings.json` 由工具生成，不要手写：

  ```powershell
  & "D:\Anaconda\envs\Auto_test\python.exe" tools/build_simulation_readings.py
  & "D:\Anaconda\envs\Auto_test\python.exe" tools/build_simulation_readings.py --check
  ```

  `--check` 只生成到内存并逐字节比对，数据漂移时退出非 0；全量离线测试也会在内存中
  重建并比较，避免只依赖手动执行。格式见下面的“原始读数包 readings.json”。

## 旧回放链路（方案完成后删除）

本节描述当前版本仍在使用的 **UI 事件回放**，界面文案当前仍显示“模拟”。它不是测量
链路，只能证明“给定这组事件，页面渲染正常”：`MockReplayWorker` 直接发出 `message` /
`progress` / `data_update` / `result` / `simulation_completed`，不创建正式运行、不调用
用例/服务、不写任何结果目录。仿真运行链路取代它之后，这些文件、符号与本节一并删除。

1. 启动界面，在欢迎页点击“加载配置”，选择显示名称为 `Mock` 的已保存快照。
   名称去除首尾空白后大小写不敏感匹配；`Mock 副本` 等其它名称仍走普通配置。
2. 加载后会校验配置摘要与三套场景的 `source_config` 是否一致；不一致会在“连接仪器”
   之前给出可读错误，不会进入回放。
3. 测试配置页在回放模式下只读，参数与保存按钮被锁定；“连接仪器”只提交回放会话，
   不创建 `PreparedRun`、不打开 VISA、不生成 `runs/` 目录。
4. 进入工作区后，三个测量页分别点击“开始测量”。每个页面回放各自阶段：
   线损 `cable_loss/scenario.json`、驱动映射 `driver_mapping/scenario.json`、
   主功放 `amplifier/scenario.json`。
5. 数据按 `data` 事件逐点发送：第一条数据立即出现，之后每 0.5 秒一个数据点；
   场景里的 `at_ms` 只用于数据校验，不决定实际播放速度。
6. 线损路径 1 完成后仍停在“请连接路径2”确认点；确认后继续路径 2，拒绝则按普通
   停止处理，已经显示的数据保留。
7. 驱动映射和主功放不需要手动加载校准结果：回放会话按 `source_config.cable_loss_result`
   与 `driver_mapping_result` 固定绑定本目录内的正式结果文件，页面显示 `mock_data`
   来源并禁用会被忽略的手动加载按钮。
8. 回放完成时按 `completion.result.file` 加载同目录下的正式结果文档并展示，同时显示
   “模拟数据，仅展示，未写入正式结果”。不写 `test_results`、不调用正式归档服务、
   不生成 CSV/测试报告，也不显示虚假的归档路径。
9. 停止后保留已经显示的数据；再次点击“开始测量”会从场景第一个事件重新播放。

回放模式与正式会话严格互斥：已连接正式运行、存在待提交运行或未决端口时不能加载
Mock 快照；返回欢迎页会清除回放会话与只读状态，之后可以正常加载普通配置。

## 原始读数包 readings.json

每个阶段目录下的 `readings.json` 是仿真运行唯一的读数来源，由
`tools/build_simulation_readings.py` 从 `scenario.json` 和同阶段既有正式结果反算生成。
它保存的是**频谱仪原始读数**，不是补偿后的结果：

| 阶段 | 形状 | 换算 |
| --- | --- | --- |
| 线损 | 有序测量序列：路径 1 全频点 → 路径 2 全频点 | `raw_dbm = -路径总损耗` |
| 驱动映射 | 按频点保存 `(source_power_dbm, raw_output_dbm)`，源功率严格递增 | `raw = 补偿后输出 - (衰减器 + 线②)` |
| 主功放 | 按频点保存 `(sg_power_dbm, raw_output_dbm, voltages, currents)`，信号源功率严格递增 | `raw = 补偿后输出 - (线④ + 衰减器 + 线②)` |

顶层字段固定为 `schema_version` / `kind` / `measurement_type` / `source` / `payload`：

- `schema_version` 只支持 `1`，`kind` 固定为 `simulation_readings`；
- `source` 记录生成事实：场景与打包校准结果的文件名和 SHA-256（线损没有上游校准
  结果，相关字段为 `null`）。它不是“本次选中校准结果”的摘要；
- `payload` 由 `measurement_type` 分派，三种形状不能混写；未知字段、非有限数、空点集、
  重复或乱序功率、通道集合变化都会被运行期契约拒绝；
- 驱动映射的 `readings.json` 会额外包含当前自适应搜索实际请求、但打包结果没有记录的
  功率点：生成器按当前算法重放一次真实服务后补入这些点。服务实际探测数仍与打包
  `raw_points` 一致（每频点 33），补点只是端口的可查记录；主功放记录在打包扫描两端
  各多一个 `0.1 dB` 覆盖点，供全链路的新驱动目标表落在记录区间内；
- 文件自身没有“自己的摘要”；读取入口计算 SHA-256 后写入运行标记。

## 回放数据格式（旧回放）

`scenario.json` 的 `kind = measurement_mock_scenario`，描述一次从“开始测量”到
“完成”的完整过程。文件只描述数据事实，不包含任何程序逻辑；仿真运行只把它当作生成
`readings.json` 与核对期望结果的输入，不再在运行时直接播放它。

顶层字段：

| 字段 | 说明 |
| --- | --- |
| `schema_version` | 回放数据格式版本，当前为 `1.0` |
| `kind` | 固定为 `measurement_mock_scenario` |
| `measurement_type` | 与正式结果契约一致：`cable_loss` / `driver_power_mapping` / `amplifier` |
| `title` / `description` | 展示用标题与说明 |
| `source_config` | 该数据对应的测量配置摘要 |
| `run` | 旧回放的运行标识、测试模式与开始时间 |
| `phases` | 按顺序执行的回放阶段 |
| `completion` | 阶段结束后的结果、归档、消息与完成信号 |

`phases[]`：

| 字段 | 说明 |
| --- | --- |
| `name` | 阶段名；线损为 `path1` / `path2`，驱动映射为 `scan` |
| `title` | 展示用名称 |
| `start` | 阶段如何开始 |
| `events` | 阶段内相对时间 `at_ms` 的事件序列 |

`start` 目前有两种：

```json
{"type": "auto"}
```

```json
{
  "type": "user_confirm",
  "signal": "step_pause",
  "checkpoint": "path2",
  "prompt": "请连接路径2",
  "accepted": "continue_stage2",
  "rejected": "stop"
}
```

`user_confirm` 对应真实流程中的“请连接路径2”弹窗：测试模式可以在用户确认后继续，
也可以按数据里的 `accepted` / `rejected` 语义自动继续或停止。

`events[]` 支持的事件类型：

| `type` | 载荷 | 对应真实信号 |
| --- | --- | --- |
| `message` | `text` | 运行日志/状态消息 |
| `progress` | `percent`、`stage` | 进度（0–100） |
| `data` | `payload` | 实时测量数据，结构与该阶段服务发出的事件一致 |

`completion[]` 支持的事件类型：

| `type` | 载荷 | 说明 |
| --- | --- | --- |
| `result` | `file` | 加载同目录下的正式结果文档并作为结果信号发出 |
| `archival` | `outcome` | 自动归档结果 |
| `message` | `text` | 完成日志 |
| `finished` | 无 | 结束信号 |

`completion[]` 的顺序固定为 `result → archival → message → finished`；`result` 和
`finished` 必须各出现一次，`finished` 必须最后，`archival` 最多一次，`message` 可以
省略。`result.file` 必须是阶段目录内的纯文件名，不能包含路径分隔、盘符、`.`/`..`、
NUL 或 Windows 保留设备名。

`at_ms` 是相对于本阶段开始的毫秒数，只用于数据自身的顺序与校验，不决定播放速度。
旧回放按 `data` 事件计时：第一条数据立即发出，之后每个数据点间隔 0.5 秒；线损
数据本身按每个频点约 `1.8 s` 编排，驱动映射按每个目标点 `0.5 s` 编排。

### 契约约束

- `phases` 至少一个；第一个阶段必须以 `auto` 开始，最多一个 `user_confirm`；确认点
  固定 `signal = step_pause`、`accepted = continue_stage2`、`rejected = stop`。
- `events[].at_ms` 是非负整数，阶段内非递减；每个阶段至少一个 `data` 事件。
- `data.payload` 必须包含正数 `frequency`。
- `progress.percent` 是 0–100 的整数，跨阶段不能倒退。
- `completion[]` 按上面的固定顺序和数量校验；未知完成类型直接失败。
- `source_config` 的必需字段是 `template`、`test_frequencies_ghz`、`attenuator_db`、
  `driver_enabled`、`driver_estimated_gain_db`、`driver_max_input_power_dbm`、
  `dut_max_input_power_dbm`、`power_scan_plans`；可选字段为 `copied_from`、
  `compression_type`、`compression_point`、`target_input_powers_dbm`、
  `signal_source_power_range_dbm`、`cable_loss_result`、`driver_mapping_result`、
  `drain_channel`、`dut_channel_settings`、`dut_power_sequence`。其余字段必须失败，
  拼写错误不会被静默忽略。
- `source_config.cable_loss_result` 和 `driver_mapping_result` 必须是
  `mock_data/<stage>/<文件名>` 形式的相对路径，不能是绝对路径或包含 `..`。
- `run.started_at` 必须是带时区的 ISO 8601 时间。

## cable_loss/

```text
cable_loss/
├─ scenario.json                    # 实时回放脚本（旧回放）；读数的生成输入
├─ readings.json                    # 仿真运行的原始读数（生成物）
└─ 20261117T060245Z_9b2e7d10.json  # 正式结果文档（期望输出/对照）
```

- 2 个阶段：路径 1 → “请连接路径2”确认 → 路径 2。
- 11 个频点，每个频点一次路径 1 事件和一次路径 2 事件。
- 路径 1 事件与真实服务一致：此时路径 2 尚未测量，`cable3`/`cable4` 为 `0`，
  `total_path2` 暂等于路径 1 总损耗；路径 2 事件到达后覆盖为正式值。
- 进度值来自 `CableLossService` 的实际计算：路径 1 为
  `4 / 9 / 13 / 18 / 22 / 27 / 31 / 36 / 40 / 45 / 50`，路径 2 为
  `54 / 59 / 63 / 68 / 72 / 77 / 81 / 86 / 90 / 95 / 100`，完成时再发一次 `100`。

线损结果由两条路径的总损耗按现有公式分摊：

```text
cable1 = cable2 = (path1_loss - attenuator) / 2
cable3 = cable4 = (path2_loss - path1_loss) / 2
total_path1     = path1_loss
total_path2     = path2_loss
```

11 个频点的路径总损耗与最终分摊值（dB）：

| 频率 | 路径1 | 路径2 | 线缆1/2 | 线缆3/4 |
| --- | ---: | ---: | ---: | ---: |
| 1.0 GHz | 41.842 | 43.418 | 0.921 | 0.788 |
| 1.1 GHz | 41.888 | 43.490 | 0.944 | 0.801 |
| 1.2 GHz | 41.936 | 43.566 | 0.968 | 0.815 |
| 1.3 GHz | 41.986 | 43.644 | 0.993 | 0.829 |
| 1.4 GHz | 42.038 | 43.726 | 1.019 | 0.844 |
| 1.5 GHz | 42.092 | 43.810 | 1.046 | 0.859 |
| 1.6 GHz | 42.148 | 43.898 | 1.074 | 0.875 |
| 1.7 GHz | 42.206 | 43.988 | 1.103 | 0.891 |
| 1.8 GHz | 42.266 | 44.082 | 1.133 | 0.908 |
| 1.9 GHz | 42.328 | 44.178 | 1.164 | 0.925 |
| 2.0 GHz | 42.392 | 44.278 | 1.196 | 0.943 |

分摊值按界面显示精度（3 位小数）记录；用公式重新计算时十进制结果一致，仅在二进制
浮点末尾存在不超过 `1e-12 dB` 的差异。

## driver_mapping/

```text
driver_mapping/
├─ scenario.json                    # 实时回放脚本（旧回放）；读数的生成输入
├─ readings.json                    # 仿真运行的原始读数（生成物）
└─ 20261117T060600Z_5c1d9e42.json  # 正式结果文档（期望输出/对照）
```

驱动映射使用的线损校准输入是：

```text
mock_data/cable_loss/20261117T060245Z_9b2e7d10.json
```

### 数据规模

- 11 个频点 × 31 个目标 DUT 输入功率点。
- 每个频点先测一个 `-70 dBm` 起点锚点，再按自适应搜索测 31 个目标，因此
  `raw_points` 每个频点 33 行。
- 正式结果中 `raw_points` 共 363 行，`target_mapping` 共 341 行。
- 回放共 1068 个事件（1 条开始消息 + 363 个数据事件 + 704 个进度事件）。

### 结果文档结构

`driver_power_mapping` 使用双层契约（`schema_version = "2.0"`）：

```text
raw_points      每个频点的原始实测点：source_power_dbm -> driver_output_power_dbm
                （含 gain_db = 输出 - 源功率，是审计依据）
target_mapping  每个目标 DUT 输入功率对应的源功率与驱放实际输出
                （正式测试消费的映射表）
```

目标表的 `target_input_power_dbm` 顺序与配置生成的 31 点网格完全一致，每个频点都有
完整覆盖，不允许缺点、重复或换序。目标命中容差为 ±0.2 dB，因此
`target_mapping.driver_output_power_dbm` 不一定精确等于目标输入；当前数据最大偏差
为 0.19 dB。

### 驱放增益模型

实际数据按约 `39–40 dB` 的驱放增益生成，并随输出功率轻微压缩、随频率缓慢下降。
配置里的 `driver_gain_estimate_db = 40` 与实测曲线一致，同时用于扫描起点：

```text
初始源功率 = 最低目标输入 - 预估增益 - 10 dB
           = -20 - 40 - 10 = -70 dBm
```

信号源最低可设功率已按 `-100 dBm` 处理，因此最低目标 `-20 dBm` 所需的 `-60 dBm`
源功率可以正常下发：

| 频率 | 初始锚点源功率 | 目标网格源功率范围 (dBm) | 目标网格增益范围 (dB) |
| --- | ---: | ---: | ---: |
| 1.0 GHz | -70.0 dBm | -60.000 ~ -34.700 | 39.700 ~ 40.000 |
| 1.5 GHz | -70.0 dBm | -59.775 ~ -34.325 | 39.325 ~ 39.775 |
| 2.0 GHz | -70.0 dBm | -59.745 ~ -33.950 | 38.950 ~ 39.555 |

原始点还包含每个频点的 `-70 dBm` 起点锚点和搜索过程的试探点，因此完整源功率范围
从 `-70.0 dBm` 开始；完整数值见结果文件。

### 回放数据

每个 `data` 事件携带该频点截至当前探测点的累计曲线。第一个事件是起点锚点，
之后每个目标点追加一行：

```json
{
  "frequency": 1.0,
  "sweep_data": {
    "input_power_sg": [-70.0, -69.753, -60.0],
    "output_power_driver": [-29.881, -29.637, -20.0],
    "gain": [40.119, 40.116, 40.0]
  },
  "current_point": {"input_power": -60.0, "output_power": -20.0}
}
```

这与驱动映射服务实际发出的 `RealtimeDataEvent` 结构一致，页面会按频点替换为最新
累计曲线。原始结果和回放事件均由真实 `DriverPowerMappingService` 按当前 Mock 配置
扫描得出，包含起点锚点、搜索试探点和每个目标的实测命中点。

进度从 `0` 单调递增到 `100`，最后一个目标点完成时到达 `100`。

## amplifier/

```text
amplifier/
├─ scenario.json                    # 实时回放脚本（旧回放）；读数的生成输入
├─ readings.json                    # 仿真运行的原始读数（生成物）
└─ 20261117T070237Z_7b3d9f21.json  # 正式结果文档（期望输出/对照）
```

主功放测量消费两份已保存的校准输入：

```text
mock_data/cable_loss/20261117T060245Z_9b2e7d10.json
mock_data/driver_mapping/20261117T060600Z_5c1d9e42.json
```

驱动模式直接使用驱动映射的目标表：每个目标 DUT 输入点对应的 `source_power_dbm`
下发到信号源，`driver_output_power_dbm` 作为主功放看到的 DUT 输入。因此
`sweep_data.input_power_dut` 与 `sweep_data.sg_power` 都能在驱动映射结果中逐点对应。

### 数据规模

- 11 个频点，每个频点最多 31 个目标 DUT 输入功率点。
- 增益压缩达到 `5 dB` 后扫描提前停止，因此每个频点实际测 27–29 点，共 `312` 点。
- 回放共 626 个事件（1 条开始消息 + 312 个数据事件 + 312 个进度事件 +
  1 个收尾 `100%` 进度）。
- 正式结果中 `results` 覆盖 11 个频点，`sweep_data` 共 312 行。

### 结果文档结构

`results` 按频点索引，每个频点包含：

```text
compression_type        压缩目标，当前数据为 "5dB"
compression_point       压缩点指标
small_signal_gain_db    前 3 个有效增益点的平均值
compression_achieved    扫描是否达到目标压缩量
sweep_data              列式扫描数据
```

`compression_point` 字段：

```text
input_power_dbm / output_power_dbm / gain_db
efficiency_percent / compression_db / signal_generator_power_dbm
```

`sweep_data` 列：

```text
input_power_dut / output_power_dut / gain / sg_power
voltages / currents / dc_power / efficiency
```

`voltages` 与 `currents` 是按 `supply_id_channel` 键的逐点映射，例如
`DP8B1519_CH2`。当前 Mock 的 DUT 上电序列为：

| 路由 | 电压 (V) | 电流 (A) | 用途 |
| --- | ---: | ---: | --- |
| `DP8B0885_CH1` | 1.0 | 0.12 | DUT 偏置 |
| `DP8B0885_CH2` | 2.0 | 0.08 | DUT 偏置 |
| `DP8B1519_CH1` | 4.0 | 0.25 | DUT 偏置 |
| `DP8B1519_CH2` | 5.0 | 2.2 → 2.7 | 漏极（参与效率计算） |

`dc_power` 只累加漏极路由的有功功率；`efficiency` 按
`Pout(W) / Pdc(W) × 100` 计算。其余通道仍逐点记录电压/电流，但不参与效率。

### 主功放模型

数据由真实的 `AmplifierMeasurementService` 驱动假仪器生成，模型参数如下：

- 小信号增益：`35.0 dB @ 1.0 GHz`，随频率线性下降到 `34.0 dB @ 2.0 GHz`。
- 增益压缩从 `-6 dBm @ 1.0 GHz`（`-7 dBm @ 2.0 GHz`）开始，压缩量按输入功率
  二次增长；增益压缩达到 `5 dB` 后服务提前结束该频点。
- 漏极电流随驱动功率从约 `2.2 A` 上升到 `2.7 A`，DC 功耗约 `13.5 W`。
- 扫描末点的输出功率约 `33.9 dBm @ 1.0 GHz`、`31.9 dBm @ 2.0 GHz`。

各频点的扫描结束位置与压缩点：

| 频率 | 扫描点数 | 末点输入 (dBm) | 末点输出 (dBm) | 小信号增益 (dB) | 压缩点输入 (dBm) | 压缩量 (dB) | 效率 (%) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1.0 GHz | 29 | 4.0 | 33.9 | 35.0 | 4.0 | 5.1 | 18.0 |
| 1.5 GHz | 28 | 3.5 | 32.9 | 34.5 | 3.5 | 5.1 | 14.4 |
| 2.0 GHz | 27 | 3.0 | 31.9 | 34.0 | 3.0 | 5.1 | 11.5 |

压缩点取“压缩量最接近 5 dB”的实测点。由于目标网格在 0 dBm 以上是 `0.5 dB` 步进，
部分频点的最接近点是 `4.8 dB` 而不是 `5.1 dB`，这是搜索精度造成的正常结果，
`compression_achieved` 仍为 `true`。

### 回放数据

每个 `data` 事件携带该频点截至当前点的累计曲线：

```json
{
  "frequency": 1.0,
  "sweep_data": {
    "input_power_dut": [-20.0, -19.0],
    "output_power_dut": [15.0, 16.0],
    "gain": [35.0, 35.0],
    "sg_power": [-60.0, -58.988],
    "voltages": [
      {
        "DP8B0885_CH1": 1.001,
        "DP8B0885_CH2": 2.002,
        "DP8B1519_CH1": 3.998,
        "DP8B1519_CH2": 5.0
      },
      {
        "DP8B0885_CH1": 1.001,
        "DP8B0885_CH2": 2.002,
        "DP8B1519_CH1": 3.998,
        "DP8B1519_CH2": 5.0
      }
    ],
    "currents": [
      {
        "DP8B0885_CH1": 0.12,
        "DP8B0885_CH2": 0.08,
        "DP8B1519_CH1": 0.25,
        "DP8B1519_CH2": 2.2
      },
      {
        "DP8B0885_CH1": 0.12,
        "DP8B0885_CH2": 0.08,
        "DP8B1519_CH1": 0.25,
        "DP8B1519_CH2": 2.222
      }
    ],
    "dc_power": [11.0, 11.11],
    "efficiency": [0.29, 0.36]
  },
  "current_point": {
    "sg_power": -58.988,
    "dut_input_power": -19.0,
    "output_power": 16.0,
    "gain": 35.0,
    "efficiency": 0.36,
    "dc_power": 11.11
  }
}
```

示例只展示前两个点；所有列按同一顺序累计增长。数据结构与该阶段服务发出的
`RealtimeDataEvent` 一致，页面按频点替换为最新累计曲线。

进度按“频点 × 目标点”全局归一化，从 `0` 单调递增到 `100`。

### 归档

主功放是唯一生成 CSV 和测试报告的阶段。完整成功时归档结果不能把两类产物标记为
`not_applicable`：

```json
{
  "stage": "amplifier",
  "test_status": "completed",
  "formal_result_path": "results/amplifier_measurement.json",
  "artifacts": [
    {"kind": "csv", "status": "completed", "path": "artifacts/amplifier.csv"},
    {"kind": "report", "status": "completed", "path": "artifacts/amplifier_report.html"}
  ]
}
```

## 正式结果文档

结果文件以 `measurement_id` 命名（`<measurement_id>.json`），内容与真实测量保存的
正式结果文档一致。批量校验：

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" -c "import json; from application.dto.measurement_results import measurement_result_from_document; [measurement_result_from_document(json.load(open(p, encoding='utf-8'))) for p in [r'mock_data/cable_loss/20261117T060245Z_9b2e7d10.json', r'mock_data/driver_mapping/20261117T060600Z_5c1d9e42.json', r'mock_data/amplifier/20261117T070237Z_7b3d9f21.json']]"
```

界面中的使用方式：

- 旧回放链路：线损、驱动映射和主功放都通过“加载 Mock 快照 → 连接仪器 → 各页开始
  测量”直接回放本目录的 `scenario.json`，不需要先复制结果文件。仿真运行链路完成后，
  同一入口改为创建真实运行、只把端口读数替换为 `readings.json`。
- 旧回放完成后页面按 `completion.result.file` 展示同目录下的正式结果；驱动映射页目前
  仍没有独立的“加载结果”入口，历史曲线由实时回放展示。
- 需要查看历史结果时，仍可把正式结果复制到真实 `test_results/<stage>/`，或由历史运行
  入口打开；旧回放链路与仿真运行本身都不会写入真实 `test_results/`。

## 与真实流程的对应关系

- 线损路径 1 结束后真实流程会停在“请连接路径2”的确认点，因此数据把它表达为
  `path2` 阶段的 `user_confirm` 入口。
- 驱动映射和主功放没有阶段内用户确认点，整个扫描各是一个 `scan` 阶段。
- 线损和驱动映射阶段都没有 CSV/报告交付产物，归档结果中两类产物均为
  `not_applicable`；阶段完成时运行整体尚未结束，因此归档测试状态为 `partial`。
- 主功放是唯一生成 CSV 与测试报告的阶段；本组数据按完整成功编排，归档测试状态为
  `completed`，两类产物都记录了运行目录内的完成路径。
- 结果文档保持真实保存形状：线损/驱动映射的 `configuration_snapshot` /
  `device_summary` 为空，主功放结果携带本次运行的 `test_plan` 与 `run_mapping`
  快照；不额外塞入只有测试模式才认识的字段。
- 驱动映射目标表已用 `AmplifierMeasurementInputs` 与 `require_driver_target_grid()`
  交叉校验，主功放数据逐点消费这份目标表。
