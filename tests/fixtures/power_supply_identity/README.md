# 电源设备身份 fixture

本目录是[电源设备身份与配置数据契约](../../../docs/电源设备身份与配置数据契约.md)的样例数据，
对应重构计划步骤 1。fixture 只描述目标字段与旧新对应关系，**不驱动任何运行时行为**：里面没有
被测代码，也没有测试替身。

## 真实数据来源

| 来源 | 快照时间 | 用在哪 |
| --- | --- | --- |
| 仓库根目录 `instruments.json` | 2026-10-28 14:48:20 | 三台 DP832A（`DP832A-1` / `-2` / `-3`）的资产字段、地址和旧角色 |
| 仓库根目录 `config.json` | 同一时刻 | `config_slots_legacy.json` 逐字节复制，作为旧配置迁移输入 |

`config_slots_legacy.json` 是 `config.json` 的原样副本，不手工编辑。要换成更新一点的现场样本时，
整份重新复制，不要就地改字段——迁移样例一旦被手改，就不再能证明“现场旧配置能迁移”。

## 清单

| 文件 | 内容 | 用途 |
| --- | --- | --- |
| `registry_legacy_v2.json` | 设备表 `schema_version: 2`，三台 DP832A 的真实记录，`role` 为 `PS3` / `null` / `PS4` | 注册表迁移的输入 |
| `registry_migrated_v3.json` | 同一份设备表迁移后：`schema_version: 3`，已登记角色的两台得到 `label` 与 `role: power_supply`，未登记角色的那台保持 `role: null`、`label: null` | 自动迁移的期望结果（不含任何人工登记） |
| `registry_migrated_v3_all_registered.json` | 在上一份基础上由操作员显式把 `DP832A-2` 登记为 `power_supply`，得到 `DP8B0950` | 多电源配置样例假设的设备表状态 |
| `config_slots_legacy.json` | `config.json` 逐字节副本：`PS1`..`PS4` 四个槽位、单数 `run_mapping.instruments.power_supply`、`device_bindings.PS1` | 迁移输入（同上，不要手改） |
| `config_slots_migrated_partial.json` | 上述旧配置的迁移结果：`PS3`→`DP8B1519`、`PS4`→`DP8B0885` 自动成型并保持未启用未绑定；`PS1`/`PS2` 无法识别，DUT 分配写成 `supply_id: null`，运行映射的电源段为空 | “自动能迁移什么、之后必须人工补什么” |
| `migration_summary_legacy.json` | 四个旧槽位的迁移摘要：`status` / `reason` / 仍引用它的分配 / 旧通道参数（含电压、电流、保护值），以及只作线索的 `legacy_address`、`address_hint` | 无法迁移电源的参数留存与人工重填依据 |
| `config_one_supply.json` | 人工重扫并绑定后的单电源完整配置：`DP8B1519` 一路承担 gate 与 drain | 1 台电源的目标形态，也是当前现场的等价形态 |
| `config_two_supplies.json` | 两台电源：`DP8B1519/CH1` 供 gate，`DP8B0885/CH2` 供 drain | 2 台电源、gate 与 drain 分属不同电源 |
| `config_five_supplies.json` | 5 台电源已绑定入配置，其中 2 台真正参与本次分配 | 多台电源的目标形态；运行快照只冻结实际使用的 2 台 |
| `run_mapping_legacy_single.json` | 旧运行快照：`instruments.power_supply` 单数、通道写 `CH1`、连接写 `PS1-CH1` | 证明历史运行目录仍按旧结构读取 |
| `run_mapping_snapshot_two_supplies.json` | 新运行快照：`instruments.power_supplies` 集合、通道写 `DP8B1519/CH1`、条目自带标签/序列号/型号/地址 | 多电源快照与历史追溯 |

## 样例设备

`config_five_supplies.json` 里的 `DP8B1191`（序列号 `DP8B234501191`）和 `DP8B1216`
（序列号 `DP8B234701216`）是为表达“5 台电源”补的样例设备，**不是现场实机**，地址
`192.168.1.110` / `192.168.1.112` 同样是占位。其余设备、序列号和地址都取自上面两个现场文件。

## 校验

`tests/test_power_supply_identity_contract.py` 读取本目录，检查：

- 每份文件都是可解析的 JSON，字段名只用契约里固定的那几个；
- 注册表里的 `label` 与契约给出的期望标签一致，`device_key` 等于 `vendor:serial` 归一化；
- 配置里的电源键、条目内 `label` 与分配引用的 `supply_id` 三者一致，且分配不引用不存在的电源；
- 1 台 / 2 台 / 5 台三种规模都成立，运行快照只包含实际使用的电源；
- 迁移摘要覆盖每一个旧槽位，无法迁移条目的通道参数与旧配置逐字段相等，且不会变成目标配置里的电源条目；
- 旧槽位只出现在迁移输入和旧运行快照里。

该门禁只校验数据，不实现标签生成器、注册表迁移或配置页面——那些属于步骤 2 及之后。
