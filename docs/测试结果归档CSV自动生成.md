# 测试结果归档：CSV 自动生成（步骤 4）

本文对应 [测试结果自动归档与导出流程重构计划](测试结果自动归档与导出流程重构计划.md) 的
**步骤 4：把 CSV 生成接入成功完成路径**。它建立在
[测试结果归档契约](测试结果归档契约.md)（步骤 2）与
[测试结果归档服务](测试结果归档服务.md)（步骤 3）冻结的路径、状态和幂等规则之上，
不再重新定义契约。

> 步骤 5 已把测试报告接入同一条成功路径：组合根现在装配
> `ArchiveArtifactGenerator`（CSV + 报告），主功放完整成功的归档结果由
> `csv=completed / report=pending` 收敛为两者都 `completed`。报告侧规则见
> [测试结果归档：报告自动生成](测试结果归档报告自动生成.md)；本文其余内容仍描述
> CSV 侧的契约，二者共用同一套输入、目标与失败边界。

本步骤完成两件事：

1. 基础设施实现自动产物生成端口，把**已登记的正式结果**渲染成运行目录 `artifacts/`
   下的 CSV（复用 `analysis.reporting.export_csv`）；
2. 把归档服务接到三类测试的**成功提交边界**：主功放完整成功自动产出 CSV，线损与驱动映射
   按契约登记 `not_applicable`。

---

## 1. 入口与模块

| 模块 | 名称 | 职责 |
| --- | --- | --- |
| `infrastructure.persistence.artifact_generator` | `CsvArtifactGenerator` | 产物生成端口实现：只读 manifest 已登记并校验过的正式结果，写 `artifacts/<stage>.csv`，返回实际路径 + SHA-256 + 大小 |
| `application.archival_service` | `archive_completed_stage()` | 阶段完成边界的归档入口：可预期失败只写错误日志，不改变已保存的结果 |
| `application.archival_service` | `ResultArchivalService.supported_kinds` | 按端口声明的能力决定真正生成哪些产物；未声明的保持 `pending` |
| `application.dto.measurement_requests` | `MeasurementRequest.archival_service` | 组装层注入的归档服务；未注入（旧兼容流程）时不做任何归档 |
| `application.ports.run_repository` | `RunRepositoryPort.verify_artifact()` | 复用已完成产物前复核文件仍存在且与 manifest 登记一致（缺失、越界、摘要/大小不一致都失败） |
| `infrastructure.persistence.run_repository` | `RunRepository.verify_artifact()`、`verify_artifact_files()` | 单条产物校验与整份 manifest 的产物校验；后者逐条委托前者 |

调用形状：

```python
service = ResultArchivalService(
    runs=run_repository,
    generator=CsvArtifactGenerator(run_repository),
)
outcome = service.archive(ArchivalRequest(run_id, STAGE_AMPLIFIER))
```

组合根（`app.gui_runtime._request_kwargs`）在**运行契约运行**里完成上述装配；旧兼容快照
（`prepared_run.run_repository is None`）不装配，保持“不生成交付产物”的旧行为。

## 2. CSV 生成契约

| 维度 | 规则 |
| --- | --- |
| 目标路径 | 只允许 `artifacts/` 下的受控相对路径；默认值由 `domain.run.models.default_artifact_path(stage, csv)` 给出（`artifacts/amplifier.csv`），适配器不自己拼文件名 |
| 目标边界 | 除字符串前缀外，按**真实路径**复核：`artifacts/` 的真实位置必须在运行目录内，目标文件的真实位置必须在 `artifacts/` 内；POSIX 符号链接与 Windows junction 都被挡住 |
| 目标阶段 | 只有 `amplifier` 有 CSV 交付产物；线损/驱动映射请求会被明确拒绝（它们应登记 `not_applicable`） |
| 输入来源 | `RunRepositoryPort.read_run_json(run_id, source_result)`：缺登记、缺文件、SHA-256/大小不一致一律拒绝；解析只走 `measurement_result_from_document()`，且解析出的 DTO `run_id` 必须等于归档请求的 `run_id`（内容自称属于另一个运行同样拒绝） |
| 序列化 | 复用 `analysis.reporting.export_csv(result, destination)`，字段是结果行字段的并集（含 `frequency_ghz`、`measurement_type`、`run_id`） |
| 写入方式 | 先写同目录临时文件（文件名带随机后缀，并发归档不会互相覆盖）再 `os.replace` 原子替换，失败时清理临时文件，不留下被 manifest 引用的半截产物 |
| 校验值 | 写入后单次读取计算 SHA-256 与字节数，随 `GeneratedArtifact` 返回并由归档服务登记进 manifest |
| 空数据 | 结果没有可导出行时抛 `ArtifactGenerationError`（显式失败），不写空文件 |
| 文件缺失 | `RunRepository.verify_artifact()` / `verify_artifact_files()` 对 `completed` 产物重新计算摘要/大小，缺失或不一致直接失败 |

失败语义与步骤 3 一致：适配器把所有**可预期**失败包成 `ArtifactGenerationError`，归档服务
只把该产物登记为 `failed` 并保留正式结果；其它异常上抛，不伪装成“产物生成失败”。

## 3. 成功完成路径的接线

三处接线都在“正式结果写入 + manifest 登记 + 阶段状态确定”之后，即应用层测量用例的
`_save_to_run_directory()` 里、`_record_complete()` 之后：

| 测试 | 接线位置 | 归档结果 |
| --- | --- | --- |
| 线损 | `CableLossUseCase.continue_to_step2()` → `_save()` → `_save_to_run_directory()` | `partial`，CSV/报告 `not_applicable` |
| 驱动映射 | `DriverPowerMappingUseCase.measure_all_frequencies()` → `_save()` → `_save_to_run_directory()` | `partial`，CSV/报告 `not_applicable` |
| 主功放 | `AmplifierMeasurementUseCase.measure_all_frequencies()` → `_save()` → `_save_to_run_directory()` | 完整成功时 CSV 与报告都 `completed`（报告见步骤 5） |

主功放的归档发生在 `complete()` 与 `complete_run()` **都成功之后**：`complete_run()` 失败时
归档不会执行，运行仍停在 `running`，与步骤 1 盘点的状态语义一致。

用例把本次归档结果保存在 `last_archival_outcome`，供计划步骤 6 的页面状态反馈消费；页面侧
显示见 [测试结果归档：主测试页面显示归档结果](测试结果归档结果展示.md)。

### 3.1 归档失败与测量失败分开

`archive_completed_stage()` 的边界：

| 场景 | 行为 |
| --- | --- |
| 未注入归档服务（旧兼容流程） | 直接返回 `None`，不产生任何产物 |
| 请求不合法（`ArchivalRequestError`）、manifest 写回失败（`OSError`） | 记录 `ERROR` 日志并返回 `None`；正式结果、阶段状态、运行状态、测量返回值都不变 |
| 已完成产物复核失败（文件缺失/被替换，`ArchivalRequestError`） | 同上：只写错误日志并返回 `None`，manifest 的 `completed` 记录不被就地改判 |
| 产物生成失败（空数据、写入失败、结果不可读等） | 由服务登记为 `failed` 并返回结构化 `ArchivalOutcome`，测量照常成功 |
| 端口/服务实现的未预期异常 | 不兜底、向上抛出：正式结果已提交且状态不受影响，但缺陷必须暴露，不能被日志吞掉 |

因此“CSV 失败不删除正式 JSON，也不把测量状态改成失败”由三件事共同保证：产物失败只在
manifest 的 `artifacts` 记录里、正式结果永不覆盖、异常分级不混用。

## 4. 产物状态与端口能力声明

端口可以声明可选属性 `supported_kinds`：

- 声明了的产物才会真正调用 `generate()`；
- 未声明的产物保持 `pending`，既不会被伪造成 `failed`，也不会被当作已完成；
- 未声明 `supported_kinds` 的端口（含只实现 `generate()` 的替身）按“支持全部交付产物”
  处理，步骤 3 的行为不变；声明了未登记类型则在构造归档服务时直接 `TypeError`。

本步骤的适配器只声明 `frozenset({ArtifactKind.CSV})`，所以当时主功放完整成功的归档结果是
`csv=completed / report=pending`、`archive_status=pending`——那是“报告生成能力尚未接入”的
诚实表达。步骤 5 已补齐：组合根装配的 `ArchiveArtifactGenerator` 声明 CSV 与报告，同一份
运行收敛为 `report=completed`、`archive_status=completed`。

已完成产物的复用不是“相信 manifest 的一句话”：再次归档时归档服务对每条 `completed` 记录
调用 `RunRepositoryPort.verify_artifact()` 复核文件，缺失、被改写或解析后越出 `artifacts/`
都按 `ArchivalRequestError` 拒绝本次归档（`archive_completed_stage()` 只写错误日志），
manifest 记录与正式结果都不被改写；重新生成留给计划步骤 7/9 的显式重试入口。

同一运行的归档在本进程内串行执行（按 `run_id` 的互斥锁）：manifest 是“读 manifest -> 改
产物记录 -> 写回”，并发调用会互相覆盖产物记录。跨进程并发不在保证范围内——一个运行目录
只由一个实例负责，同一 `run_id` 的重复触发由该锁合并。

## 5. 明确不属于本步骤

- ~~不实现测试报告与图表生成（计划步骤 5）~~：报告已在步骤 5 接入，见
  [测试结果归档：报告自动生成](测试结果归档报告自动生成.md)；图表仍不生成。
- ~~不实现页面状态展示（步骤 6）~~：三个测量页已在步骤 6 显示归档结果，见
  [测试结果归档：主测试页面显示归档结果](测试结果归档结果展示.md)；历史页重试入口（步骤 7/9）
  仍未实现，`retry_failed` 目前只有应用层消费者。
- 不改变 `ExportPage`、可视化页与 `ResultExportUseCase` 的手动导出语义。
- 不改变阶段结果路径、manifest 校验顺序、取消/异常路径；失败与取消的归档语义仍按步骤 9 补齐。
- 兼容快照（`app.run_context.prepare_run()`）不进入归档，也不生成产物。

## 6. 本步骤验证

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" -m unittest tests.test_csv_artifact_generator tests.test_archival_success_path -v
& "D:\Anaconda\envs\Auto_test\python.exe" -m unittest discover -s tests -v
& "D:\Anaconda\envs\Auto_test\python.exe" tools/export_dependency_lock.py --check
& "D:\Anaconda\envs\Auto_test\python.exe" launcher.py --check
& "D:\Anaconda\envs\Auto_test\python.exe" launcher.py --validate-config
```

`tests/test_csv_artifact_generator.py`（25 项）覆盖：

- 适配器：CSV 落在 `artifacts/amplifier.csv`、返回路径与摘要/大小与文件一致、内容含结果行；
- 显式失败：空数据、未登记结果、被替换结果、运行标识不一致（步骤 5 补充的共享输入校验）、
  非主功放阶段、报告类型的请求（CSV 适配器只生成 CSV）、写入失败（目标被同名目录占住）
  ——都不产生半截产物，正式结果保持原样；
- 目标边界：真实路径守卫直接拒绝越出 `artifacts/` 的目标；`artifacts/` 或其子目录被重定向
  （符号链接 / Windows junction）到运行目录外时拒绝生成，目录外不留下任何文件；
- 临时文件：每次写入使用带随机后缀的独立临时文件，写失败后不残留；
- 服务集成：`completed` 产物登记真实 SHA-256/大小、`verify_artifact_files()` 通过、历史
  (`read_run_json`) 仍可读取正式结果；重复归档只生成一次且文件不被重写；已完成 CSV 被删除
  或改写后再次归档按 `ArchivalRequestError` 拒绝复用，manifest 记录不被就地改判；
- 并发：4 个线程同时归档同一运行只留下一条 CSV 记录，摘要与文件一致、无残留临时文件；
- 历史视图：产物登记后运行仍可浏览，阶段结果仍是 CSV 报告之外的正式结果；
- 产物文件缺失/被篡改时 `verify_artifact_files()` 分别报 `FileNotFoundError` / `ValueError`；
- 端口声明：未知产物类型在构造服务时被拒绝；未声明时按“全部交付产物”处理。

`tests/test_archival_success_path.py`（11 项）覆盖：

- 主功放成功路径自动产出 CSV；报告生成在步骤 5 接入后同样是 `completed`，阶段/运行状态与
  结果路径不受影响（见[报告自动生成](测试结果归档报告自动生成.md)）；
- 未注入归档服务时不生成任何产物、manifest 无产物记录；
- 线损与驱动映射成功路径登记两条 `not_applicable`，运行仍是 `running`；
- CSV 写不进去时只有该产物 `failed`，测量仍成功、正式 JSON 仍在；
- `ArchivalRequestError` / manifest 写回失败只写错误日志，测量结果与状态不变；
- 已完成 CSV 被删除后再次触发归档只写错误日志（`archive_completed_stage()` 返回 `None`），
  阶段与运行状态、正式结果都不变；
- 未预期端口缺陷向上抛出（正式结果仍已提交，状态不被改写）；
- 组合根：运行契约运行装配归档服务并产出 CSV；旧兼容快照不装配。

执行结果（2026-10-28，含步骤 5 的同步更新）：`tests/test_csv_artifact_generator.py` 25 项、
`tests/test_archival_success_path.py` 11 项通过；`tests/test_result_archival_service.py` 44 项
通过；全量 `unittest discover -s tests` 1600 项通过（3 项跳过）；依赖锁、`launcher --check`、
`launcher --validate-config` 与 `tools/compile_check.py .` 均通过。
