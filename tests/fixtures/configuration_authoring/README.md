# 正式配置编写迁移基线

`official_configuration_golden.json` 是 GUI 重构步骤 4 的**行为回归基准**：每一条
`expected` 都由迁移前的 `presentation/qt/configuration_authoring.py`（commit
`7310fa2`，步骤 3 完成时的版本）对同一条 `input` 生成，整个文档逐字节比较（含
`test_plan` / `run_mapping` 与顶层编辑段的透传）。

生成方式（一次性，不在测试中执行）：

```powershell
git show 7310fa2:presentation/qt/configuration_authoring.py > <临时目录>/old_authoring.py
# 用 importlib 从临时文件加载旧模块，对下方用例逐一调用
#   author_official_configuration(input)
# 把结果写成 {"name", "input", "expected"} 三元组
```

用例覆盖：最小表单、两电源拆分 gate/drain、驱放与 DUT 各一台电源、驱放外接供电、
驱放供电方式推断、未绑定电源引用、重复物理通道、未知通道角色、仪器身份缺失与未启用、
旧槽位名回落、通道参数过滤、未知顶层字段透传、空文档与非对象输入。

约定：

- **不要用被测实现重新生成期望值**。期望值只来自迁移前的实现（或经评审确认的正式样例）；
- 修改编写规则时，先确认这是业务变更，再更新本文件的 `expected`，并在提交说明里写明原因；
- 用例是纯 Python 数据（JSON），不依赖 Qt、不启动 `QApplication`；
- `tests/test_configuration_authoring.py::GoldenConfigurationTests` 读取本文件。
