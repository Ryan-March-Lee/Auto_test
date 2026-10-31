# baseline 目录说明

本目录只保留两类内容：**被测试读取的小型模板夹具**（纳入版本控制）和
**基线收集脚本生成的结果/报告副本**（仅本地，不纳入版本控制）。

## 纳入版本控制的文件

| 文件 | 用途 | 来源 | 读取方 | 可再生成 |
| --- | --- | --- | --- | --- |
| `test_plan.json` | 测试方案模板，`template: true`，字段全为占位值 | 手工维护的模板 | `tests/test_config_models.py`（`load_test_plan` 解析契约） | 是（模板内容即定义） |
| `run_mapping.json` | 运行映射模板，`template: true`，设备地址/运行标识均为空 | 手工维护的模板 | `tests/test_config_models.py`（`load_run_mapping` 解析契约） | 是（模板内容即定义） |

两个文件都是**脱敏模板**，不含现场地址、设备序列号或测量数据，因此可以长期留在
源代码历史中；它们描述的是配置 schema 的读取契约，不是某次真实运行的基线。

## 生成型数据：`baseline/collected/`

`collect_baseline.py` 把 `test_results/<run_id>` 中的测量结果、快照与报告复制到
`baseline/collected/<baseline_id>/`，并在其中写 `baseline_manifest.json`。该目录：

- **不被测试或发布工具读取**。`tests/test_collect_baseline.py` 只用隔离临时目录验证收集行为；
  `release_manifest.py`、`launcher.py` 与 `tools/` 都不读取它。
- **被 `.gitignore` 忽略**（`baseline/collected/**`），普通收集运行不会污染工作区。
- **不要用 `git add -f` 强行入库**。需要长期保存的基线请归档到受控存储，而不是源代码历史。

本地曾入库过一份样例 `collected/20260925-203233-586899/`（已从版本控制移除，本地文件保留）：

| 文件 | 大小 | 来源 | 说明 | 可再生成 |
| --- | --- | --- | --- | --- |
| `baseline_manifest.json` | 2.4 KB | `collect_baseline.py`（旧版 schema 1.0） | 收集清单；`requested_run_id` 为空、快照缺失，按当前完整性规则属于 `complete=false` | 是（清单随目录重写） |
| `config.json` | 6.2 KB | 现场 `config.json` | **含现场数据**：真实仪器 IP（`192.168.1.x`）、通道电压/电流与保护值 | 否（原始配置未入库） |
| `results/cable_loss_results.json` | 2.8 KB | `test_results/cable_loss_results.json` | 线损测量结果副本 | 否（来源运行目录已不存在） |
| `results/driver_power_mapping_20260629_114346.json` | 37 KB | `test_results/driver_power_mapping_20260629_114346.json` | 驱动功率映射结果副本 | 否（同上） |
| `results/amplifier_measurement_20260820_205415.json` | 226 KB | `test_results/amplifier_measurement_20260820_205415.json` | 主功放测量结果副本（`config` + `results`） | 否（同上） |
| `reports/test_report.html` | 9.1 KB | `test_results/20260821_131016/test_report.html` | 现场测试报告副本 | 否（同上） |
| `reports/freq_vs_all_sat_metrics.pdf` | 26 KB | `test_results/20260821_131016/freq_vs_all_sat_metrics.pdf` | 频率-饱和指标图表副本 | 否（同上） |
| `reports/full_sweep_data.csv` | 48 KB | `test_results/20260821_131016/full_sweep_data.csv` | 全扫频原始数据副本 | 否（同上） |

这份样例引用的来源运行目录在当前工作区已不存在，属于**不可再生成的现场记录**，因此只从
版本控制移除、不删除本地文件。需要清理本地副本时，先把它归档到受控存储再删除
`baseline/collected/`。它也不作为自动化测试的输入：任何需要基线样例的测试都应在临时目录里
自行构造数据。

## 重新收集基线

```powershell
# 单次运行
& "D:\Anaconda\envs\Auto_test\python.exe" collect_baseline.py --run-id <run_id>

# 允许三类结果来自不同 run_id 的生产批次
& "D:\Anaconda\envs\Auto_test\python.exe" collect_baseline.py --batch `
    --cable-loss-run-id <id> --driver-mapping-run-id <id> --amplifier-run-id <id> `
    --report-dir <dir>
```

输出固定落在 `baseline/collected/<baseline_id>/`，已被忽略。

## 验证

```powershell
# 目标目录不应有任何版本控制文件
git ls-files baseline/collected
# 新产物应命忽略规则
git check-ignore --no-index -q baseline/collected/example/baseline_manifest.json
```

`tests/test_collect_baseline.py` 中的 `BaselineRepositoryHygieneTests` 把这两条检查与
“保留夹具仍是模板”写成了可执行门禁。
