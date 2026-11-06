# 正式配置编写基线（v1.1）

`official_configuration_golden.json` 是电源配置独立化步骤 6 的**行为回归基准**：
每一条 `expected` 都是当前 `application/configuration_authoring.py` 对同一条 1.1 `input`
生成的完整文档，按 Python 对象逐项比较（含 `test_plan` / `run_mapping` 与顶层编辑段的
透传）。

步骤 6 的业务变更（相对步骤 4 的 1.0 基线）：

- 正式段版本改为 `1.1`；
- `test_plan.dut` 只写 `drain_channel` 与 `channel_settings`，不再写 `power_roles`；
- `test_plan.driver_mode` 写 `channel_settings`，不再写 `power_roles`；
- `driver_mode` 不再写 `power_source`：驱动功放计划为空即自带供电，计划非空即由本机
  直流电源供电；
- `run_mapping` 写 `dut_power_plan` / `driver_mode.power_plan`，不再写
  `dut_power_channels` / `driver_mode.power_channels`；
- 编辑段的通道 `role` 与旧槽位 `name` 引用不再进入 1.1 文档；1.0 输入由 authoring
  版本门禁拒绝（见 `tests/test_configuration_authoring.py` 的门禁用例）。

用例覆盖：最小表单、两电源拆分通道、驱放与 DUT 各一台电源、驱放自带供电（空计划）、
驱动计划（非空）、未绑定电源引用、重复物理通道、未知物理通道、仪器身份缺失与未启用、
通道参数过滤、未知顶层字段透传、空文档与非对象输入。

约定：

- 期望值只在经评审的契约变更时一次性更新，并在提交说明里写明原因；不要为了“跑绿”
  用被测实现反复重生成；
- 用例是纯 Python 数据（JSON），不依赖 Qt、不启动 `QApplication`；
- `tests/test_configuration_authoring.py::GoldenConfigurationTests` 读取本文件，并断言
  输出不再包含 1.0 的角色化字段。
