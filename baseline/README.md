# baseline 目录说明

本目录保留两类内容：**被测试读取的小型模板夹具与归档校验清单**（纳入版本控制），
以及**基线收集脚本生成的运行产物副本**（仅本地/归档，不纳入版本控制）。

## 纳入版本控制的文件

| 文件 | 用途 | 来源 | 读取方 | 可否重建 |
| --- | --- | --- | --- | --- |
| `test_plan.json` | 测试方案模板，`template: true`，字段全为占位值 | 按 `TestPlan` schema 手工维护 | `tests/test_config_models.py`（`load_test_plan` 解析契约） | 可按 schema 重建，不含现场数据，重建无信息损失 |
| `run_mapping.json` | 运行映射模板，`template: true`，设备地址/运行标识为空 | 按 `RunResourceMapping` schema 手工维护 | `tests/test_config_models.py`（`load_run_mapping` 解析契约） | 同上 |
| `collected_archive.sha256` | 已归档现场样例的路径与 SHA-256 清单 | 归档时对样例目录计算 | `tests/test_collect_baseline.py`（与本地样例逐条比对） | 是（重新计算哈希） |

前两个文件是**脱敏模板**：不含现场地址、设备序列号、通道参数或测量数据，只是配置
schema 的读取契约，不是某次真实运行的基线。它们描述的是“数据长什么样”，不是“某次测到了
什么”，因此可以长期留在源代码历史中。`collected_archive.sha256` 只记录相对路径与哈希值，
同样不含现场内容。

## 生成型数据：`baseline/collected/`

`collect_baseline.py` 把 `test_results/<run_id>` 中的测量结果、快照与报告复制到
`baseline/collected/<baseline_id>/`，并在其中写 `baseline_manifest.json`。该目录：

- **不被测试或发布工具读取**。`tests/test_collect_baseline.py` 只用隔离临时目录验证收集行为；
  `release_manifest.py`、`launcher.py` 与 `tools/` 都不读取它。
- **被 `.gitignore` 忽略**（`baseline/collected/**`），普通收集运行不会污染工作区。
- **不要用 `git add -f` 强行入库**。需要长期保存的基线请归档，而不是塞进源代码历史。

## 归档：`archive/`

现场样例 `collected/20260925-203233-586899/`（8 个文件，manifest、`config.json`、3 份测量结果、
3 份报告）已从版本控制移除，并封成一份归档包：

| 归档物 | 说明 |
| --- | --- |
| `archive/baseline-collected-20260925-203233-586899.zip` | 8 个文件的原始内容；`archive/` 被忽略，不进版本控制、不进发布包 |
| `baseline/collected_archive.sha256` | 同一批文件的 SHA-256 清单（入库，用于确认归档副本仍与记录一致） |

归档包只留在本机，清单随仓库发布。清单入库的意义是**留下可追溯的指纹**：即使归档包被移交到
受控存储（带访问控制与备份的位置），也能确认移交内容就是这里记录的那一份。

样例的表单件信息：

| 文件 | 大小 | 来源 | 说明 | 可否重建 |
| --- | --- | --- | --- | --- |
| `baseline_manifest.json` | 2.4 KB | `collect_baseline.py`（旧版 schema 1.0） | 收集清单；`requested_run_id` 为空、快照缺失，按当前完整性规则属于 `complete=false` | 否（依赖已不存在的来源运行） |
| `config.json` | 6.2 KB | 现场 `config.json` | **含现场数据**：仪器 VISA 地址（`TCPIP0::192.168.1.x`）、通道电压/电流与保护值 | 否（原始配置未入库） |
| `results/cable_loss_results.json` | 2.8 KB | `test_results/cable_loss_results.json` | 线损测量结果副本 | 否（来源运行目录已不存在） |
| `results/driver_power_mapping_20260629_114346.json` | 37 KB | `test_results/driver_power_mapping_20260629_114346.json` | 驱动功率映射结果副本 | 否（同上） |
| `results/amplifier_measurement_20260820_205415.json` | 226 KB | `test_results/amplifier_measurement_20260820_205415.json` | 主功放测量结果副本（`config` + `results`） | 否（同上） |
| `reports/test_report.html` | 9.1 KB | `test_results/20260821_131016/test_report.html` | 现场测试报告副本 | 否（同上） |
| `reports/freq_vs_all_sat_metrics.pdf` | 26 KB | `test_results/20260821_131016/freq_vs_all_sat_metrics.pdf` | 频率-饱和指标图表副本 | 否（同上） |
| `reports/full_sweep_data.csv` | 48 KB | `test_results/20260821_131016/full_sweep_data.csv` | 全扫频原始数据副本 | 否（同上） |

这些来源运行目录在当前工作区已不存在，属于**不可再生成的现场记录**，因此不删除、不重写，
只把版本控制中的副本移除并改为归档保存。它也不作为自动化测试的输入：任何需要基线样例的
测试都应在临时目录里自行构造数据。

## 版本控制历史

移除操作只对当前分支生效。`ea4fc73`（基线整理）到 `02494da` 之间的提交仍包含这 8 个文件，
它们已推送到 `origin/main`，因此 `git log`/`git show` 仍能看到现场 `config.json` 与测量数据。

处置决定：**不改写已推送的历史**。依据是这批数据只含私网 VISA 地址、通道参数与测量结果，
不含账号、口令或密钥；改写历史需要强推远端并让所有克隆重新同步，代价与风险高于收益。
如果以后要求彻底清除，按以下顺序执行，不要单独做第 2 步：

1. 确认归档包与校验清单已移交到受控存储，并能通过 `collected_archive.sha256` 校验。
2. 通知所有克隆与镜像的持有者，暂停推送。
3. 用 `git filter-repo` 剔除 `baseline/collected/20260925-203233-586899/` 的历史，强推 `main`。
4. 请协作者重新克隆，并确认远端、镜像、缓存中该路径已不可达。

## 重新收集基线

```powershell
# 单次运行
& "D:\Anaconda\envs\Auto_test\python.exe" collect_baseline.py --run-id <run_id>

# 允许三类结果来自不同 run_id 的生产批次
& "D:\Anaconda\envs\Auto_test\python.exe" collect_baseline.py --batch `
    --cable-loss-run-id <id> --driver-mapping-run-id <id> --amplifier-run-id <id> `
    --report-dir <dir>
```

输出固定落在 `baseline/collected/<baseline_id>/`，已被忽略。需要长期保存时再归档成
`archive/` 下的包并在 `collected_archive.sha256` 追加对应条目。

## 验证

```powershell
# 目标目录不应有任何版本控制文件
git ls-files baseline/collected
# 新产物与归档包都应命中忽略规则
git check-ignore --no-index -q baseline/collected/example/baseline_manifest.json
git check-ignore --no-index -q archive/example.zip

# 本地样例文件仍与入库的哈希清单一致
& "D:\Anaconda\envs\Auto_test\python.exe" tools/run_tests.py
```

`tests/test_collect_baseline.py::BaselineRepositoryHygieneTests` 把这些检查写成了可执行门禁：
`baseline/collected` 无跟踪文件、新产物与 `archive/` 被忽略、模板仍是脱敏模板且不含 IP/VISA
标识、`collected_archive.sha256` 与本地样例逐条一致（样例不存在时只校验清单格式）。
