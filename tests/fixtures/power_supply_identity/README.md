# 电源设备身份 fixture

本目录是电源设备身份与动态配置数据契约（`厂商:完整设备码` 身份、全局唯一的缩写设备码
`label`、动态电源集合 `supply_id`）的样例数据，对应重构计划步骤 1 与步骤 20。fixture 只描述
目标字段与旧新对应关系，**不驱动任何运行时行为**：里面没有被测代码，也没有测试替身。字段名
与期望值以本目录样例为准，`tests/test_power_supply_identity_contract.py` 是它的可执行版本。

## 真实数据来源

| 来源 | 快照时间 | 用在哪 |
| --- | --- | --- |
| 仓库根目录 `instruments.json` | 2026-10-28 14:48:20 | 三台 DP832A 的型号、完整设备码、地址和旧角色 |
| 仓库根目录 `config.json` | 同一时刻 | `config_slots_legacy.json` 的现场形态，作为旧配置迁移输入 |

`config_slots_legacy.json` 是现场旧配置的原样副本，不手工编辑。要换成更新一点的现场样本时，
整份重新复制，不要就地改字段——迁移样例一旦被手改，就不再能证明“现场旧配置能迁移”。

## 清单

| 文件 | 内容 | 用途 |
| --- | --- | --- |
| `registry_legacy_v2.json` | 设备表 `schema_version: 2`，三台 DP832A 的真实记录，`role` 为 `PS3` / `null` / `PS4`，完整设备码仍写在 `serial`，并带旧资产名 `name` / `model_index` | 注册表迁移的输入（历史读取边界） |
| `registry_migrated_v4.json` | 同一份设备表迁移后：`schema_version: 4`，三台都按完整设备码得到 `label`（缩写码与角色无关），已登记角色的两台 `role: power_supply`，未登记角色的那台保持 `role: null`；不再有 `name` / `model_index` / `serial` | 自动迁移的期望结果（不含任何人工登记） |
| `registry_migrated_v4_all_registered.json` | 在上一份基础上由操作员显式把未登记角色的那台登记为 `power_supply`，得到 `DP8B0950` | 多电源配置样例假设的设备表状态 |
| `config_slots_legacy.json` | 现场旧配置副本：`PS1`..`PS4` 四个槽位、单数 `run_mapping.instruments.power_supply`、`device_bindings.PS1` | 迁移输入（不要手改） |
| `config_slots_migrated_partial.json` | 上述旧配置的迁移结果（1.1）：`PS3`→`DP8B1519`、`PS4`→`DP8B0885` 自动成型并保持未启用未绑定；`PS1`/`PS2` 无法识别，DUT 分配写成 `supply_id: null`，运行映射的电源段为空，每组 `power_plan` 是空计划，漏极为 `null` 待确认；电源条目与绑定记录只写 `full_code`，不再带 `name` / `serial` / 通道 `role` | “自动能迁移什么、之后必须人工补什么” |
| `migration_summary_legacy.json` | 四个旧槽位的迁移摘要（schema 3）：`status` / `reason` / 仍引用它的分配 / 旧通道参数（含电压、电流、保护值），以及只作线索的 `legacy_address`、`address_hint`；身份字段是 `full_code`。另记每组空计划与候选路由、`channel_settings` 映射状态、`drain_channel` 迁移状态 | 无法迁移电源的参数留存与人工重填依据 |
| `config_one_supply.json` | 人工重扫并绑定后的单电源完整配置（1.1）：`DP8B1519` 的 `CH1 → CH2` 是用户上电计划，`CH2` 是漏极测量路由；信号源、频谱仪与电源的配置条目和运行快照都带 `label` / `model` / `full_code` / `device_key` / 地址；通道不再写 `role` | 1 台电源的目标形态，也是当前现场的等价形态 |
| `config_two_supplies.json` | 两台电源（1.1）：`DP8B1519/CH1` 先上电、`DP8B0885/CH2` 后上电，漏极是 `DP8B0885/CH2` | 2 台电源、上电计划与漏极分属不同电源的目标形态 |
| `config_five_supplies.json` | 5 台电源已绑定入配置（1.1），其中 2 台真正参与本次分配与上电计划 | 多台电源的目标形态；运行快照只冻结实际使用的 2 台 |
| `run_mapping_legacy_single.json` | 旧运行快照（1.0）：`instruments.power_supply` 单数、通道写 `CH1`、连接写 `PS1-CH1` | 证明历史运行目录仍按旧结构读取 |
| `run_mapping_snapshot_two_supplies.json` | 新运行快照（1.1）：`instruments.power_supplies` 集合、`dut_power_plan` 冻结物理路由；信号源、频谱仪与每台电源都自带缩写码/型号/完整设备码/设备键/地址 | 多电源快照与历史追溯 |

`config_slots_migrated_partial.json` 只是**期望值**，不是“可以写回现场的配置”：`PS1`/`PS2` 认不出身份，
迁移器因此拒绝写盘，它们的通道参数只留在 `migration_summary_legacy.json` 里，
重扫补全后才得到 `config_one_supply.json` 那样的完整配置。

## 样例设备

`config_five_supplies.json` 里的 `DP8B1191`（完整设备码 `DP8B234501191`）和 `DP8B1216`
（完整设备码 `DP8B234701216`）是为表达“5 台电源”补的样例设备，**不是现场实机**，地址
`192.168.1.110` / `192.168.1.112` 同样是占位。其余设备、完整设备码和地址都取自上面两个现场文件。

## 校验

`tests/test_power_supply_identity_contract.py` 读取本目录，检查：

- 每份文件都是可解析的 JSON，字段名只用样例里固定的那几个；
- 注册表里的 `label` 与缩写设备码规则给出的期望值一致，`device_key` 等于
  `厂商:完整设备码` 归一化；
- 配置里的电源键、条目内 `label` 与分配引用的 `supply_id` 三者一致，且分配不引用不存在的电源；
- 1 台 / 2 台 / 5 台三种规模都成立，运行快照只包含实际使用的电源；
- 迁移摘要覆盖每一个旧槽位，无法迁移条目的通道参数与旧配置逐字段相等，且不会变成目标配置里的电源条目；
- 旧槽位只出现在迁移输入和旧运行快照里；旧 `serial` / `name` / `model_index` 只出现在
  `registry_legacy_v2.json` 这个历史读取边界样例里，新样例只写 `full_code` / `label`。
