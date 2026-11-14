# 电源设备身份 fixture

本目录是电源设备身份与动态配置数据契约（`厂商:完整设备码` 身份、全局唯一的缩写设备码
`label`、动态电源集合 `supply_id`）的样例数据。fixture 只描述当前格式，**不驱动任何运行时
行为**：里面没有被测代码，也没有测试替身。字段名与期望值以本目录样例为准，
`tests/test_power_supply_identity_contract.py` 是它的可执行版本。

旧注册表、旧配置与旧运行结果样例已经退役；当前程序只读取当前 schema。

## 真实数据来源

| 来源 | 快照时间 | 用在哪 |
| --- | --- | --- |
| 仓库根目录 `instruments.json` | 2026-10-28 14:48:20 | 三台 DP832A 的型号、完整设备码、地址与角色 |
| 仓库根目录 `config.json` | 同一时刻 | 单电源/多电源目标配置的字段口径 |

## 清单

| 文件 | 内容 | 用途 |
| --- | --- | --- |
| `registry_migrated_v4.json` | 当前设备表：`schema_version: 4`，三台都按完整设备码得到 `label`（缩写码与角色无关），已登记角色的两台 `role: power_supply`，未登记角色的那台保持 `role: null`；没有旧资产名字段与旧序列号字段 | 注册表当前格式样例 |
| `registry_migrated_v4_all_registered.json` | 在上一份基础上由操作员显式把未登记角色的那台登记为 `power_supply`，得到 `DP8B0950` | 多电源配置样例假设的设备表状态 |
| `config_one_supply.json` | 人工重扫并绑定后的单电源完整配置：`DP8B1519` 的 `CH1 → CH2` 是用户上电计划，`CH2` 是漏极测量路由；信号源、频谱仪与电源的配置条目和运行快照都带 `label` / `model` / `full_code` / `device_key` / 地址；通道不写 `role` | 1 台电源的目标形态 |
| `config_two_supplies.json` | 两台电源：`DP8B1519/CH1` 先上电、`DP8B0885/CH2` 后上电，漏极是 `DP8B0885/CH2` | 2 台电源、上电计划与漏极分属不同电源的目标形态 |
| `config_five_supplies.json` | 5 台电源已绑定入配置，其中 2 台真正参与本次分配与上电计划 | 多台电源的目标形态；运行快照只冻结实际使用的 2 台 |
| `run_mapping_snapshot_two_supplies.json` | 当前运行快照：`instruments.power_supplies` 集合、`dut_power_plan` 冻结物理路由；信号源、频谱仪与每台电源都自带缩写码/型号/完整设备码/设备键/地址 | 多电源快照与历史追溯 |

## 样例设备

`config_five_supplies.json` 里的 `DP8B1191`（完整设备码 `DP8B234501191`）和 `DP8B1216`
（完整设备码 `DP8B234701216`）是为表达“5 台电源”补的样例设备，**不是现场实机**，地址
`192.168.1.110` / `192.168.1.112` 同样是占位。其余设备、完整设备码和地址都取自现场记录。

## 校验

`tests/test_power_supply_identity_contract.py` 读取本目录，检查：

- 每份文件都是可解析的 JSON；
- 注册表里的 `label` 与缩写设备码规则给出的期望值一致，`device_key` 等于
  `厂商:完整设备码` 归一化；
- 配置里的电源键、条目内 `label` 与分配引用的 `supply_id` 三者一致，且分配不引用不存在的电源；
- 1 台 / 2 台 / 5 台三种规模都成立，运行快照只包含实际使用的电源；
- 生产代码不再硬编码固定槽位，也不再出现旧身份字段。
