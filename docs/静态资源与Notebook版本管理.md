# 大体积静态资源与 Notebook 版本管理

本文件固定 `assets/` 与 `DPD_auto_test/` 中二进制资源的归类、压缩规则、版本控制策略和
预算门禁。技术债重构计划步骤 18 的结论以本文件为准；新增或替换资源时先按这里的规则判断
归属，再决定是否入库。

## 原则

1. **界面运行必需的资源随仓库和发布包一起交付。** 不允许为了减小仓库体积改成运行时从
   网络下载：现场可能没有外网，下载失败会直接让界面缺图。
2. **能再生成的数据不进源代码历史。** Notebook 输出、测量产物、日志和归档包属于运行或
   实验产物，只保留在受控归档或本地目录。
3. **不可再生成的数据不因“仓库瘦身”被删除。** 只从当前分支移除副本，并保留可校验的
   归档或历史版本（步骤 17 的现场基线样例沿用同一原则）。
4. **二进制资源的改动单独成提交。** 图片压缩与 Notebook 输出清理分开提交，便于审查差异、
   必要时单独回退。

## 盘点（2026-11-01）

`git ls-files assets DPD_auto_test` 的全部内容，按用途分为四类：

| 资源 | 压缩前 | 用途分类 | 读取方 / 来源 | 处置 |
| --- | ---: | --- | --- | --- |
| `assets/init_background.png` | 1703.7 KB | 运行时必需 | `presentation/qt/theme.py::WELCOME_BACKGROUND_FILE`，欢迎页背景 | 转为 JPEG（质量 90），198.9 KB |
| `assets/instrument_pic/spectrum_analyzer.png` | 4613.9 KB | 运行时必需 | `presentation/qt/device_cards.py`，频谱仪卡片图 | 缩放至长边 800 px，433.6 KB |
| `assets/instrument_pic/signal_source.png` | 1781.8 KB | 运行时必需 | 同上，信号源卡片图 | 缩放至长边 800 px，292.1 KB |
| `assets/instrument_pic/DC_source.png` | 330.0 KB | 运行时必需 | 同上，电源卡片图 | 缩放至长边 800 px，329.9 KB |
| `assets/instrument_pic/cable.png` | 1424.5 KB | 可替代（无引用） | 全仓无生产代码、测试或工具读取；加入于 `9824bca` 的卡片图提交 | 从当前分支删除，可从该提交取回 |
| `assets/icons/*.png`（4 个 16×16） | 合计 1.2 KB | 运行时候选（极小） | `PathService.icons_dir` 暴露目录，当前无生产读取方 | 保留；体积低于预算，不为此改路径 API |
| `DPD_auto_test/ILC_auto_test.ipynb` | 2435.3 KB | 实验 Notebook | 数字预失真实验；10 张内嵌 PNG 占 2358.3 KB | 清空输出，59.9 KB |
| `DPD_auto_test/Tradition_ILA_auto_test.ipynb` | 727.1 KB | 实验 Notebook | 同上；3 张内嵌 PNG 占 656.9 KB | 清空输出，63.1 KB |
| `DPD_auto_test/auto_trans_and_collect.ipynb` | 18.9 KB | 实验 Notebook | 仪器采集脚本的实验编排 | 清空输出，18.2 KB |
| `DPD_auto_test/temp.ipynb` | 3.6 KB | 实验 Notebook | 临时试验记录，保留作使用示例 | 清空输出，3.1 KB |
| `DPD_auto_test/function.py`、`Tradition_function.py`、`README.md` | 合计 39.1 KB | 开发素材 | DPD 实验脚本与子系统说明 | 保留在版本控制 |

结果：`assets/` 从 9.62 MB / 9 个文件降为约 1.23 MB / 7 个文件；`DPD_auto_test/` 从
3.19 MB 降为约 0.18 MB（不含未跟踪的 `__pycache__/`）。两类合计约 12.8 MB → 约 1.4 MB。

## 运行时图片的压缩规则

UI 图片只在两处显示，尺寸决定了压缩上限：

- 设备卡片图片区为 182×104 逻辑像素（`device_cards.py` 的 `CARD_WIDTH`、`_PICTURE_HEIGHT`），
  按仪器主体的包围盒等比放入，不裁切。
- 欢迎页背景按窗口等比绘制，当前源图 1670×942。

因此规则是“按显示尺寸的 3–4 倍保留像素，再按内容选格式”，而不是无损压缩原图：

| 类型 | 规则 | 理由 |
| --- | --- | --- |
| 仪器照片（需要透明留白） | 长边缩到 800 px；在**预乘 alpha**（PIL `RGBa`）空间缩放后转回 `RGBA`，PNG `optimize=True` | 800 px 已超过卡片区域的 4 倍宽度，高 DPI 屏幕不会发虚；预乘缩放避免半透明描边出现黑边；保留透明通道 |
| 欢迎页背景（照片、无透明） | 质量 90、渐进式 JPEG | PNG 无损重压只能省 14%（1459 KB），调色板量化会在天空渐变上产生色带；JPEG 无透明需求，视觉差异不可见 |
| 16×16 图标 | 不改动 | 体积可忽略，重新编码反而可能破坏索引色 |

压缩后的验收项（由 `tests/test_asset_budget.py` 与 `tests/ui/test_device_cards.py` 执行）：

- 每个运行时图片仍能被 `QImage` 读出，格式与扩展名一致；欢迎页背景还要能被 `QPixmap`
  解码（走 `welcome_page` 的实际加载路径，而不只是独立读文件）；
- 仪器图片保留 alpha 通道，透明留白的角像素 alpha 仍为 0；
- 仪器图片长边不超过 800 px，背景不超过 1920×1080；
- `InstrumentImage` 对四类设备图仍能算出非空、且落在图片区内的绘制矩形；
- 单文件与目录总预算不超限（见下节）。

若后续新增设备图，按同一规则处理后再入库：卡片图长边 800 px、透明通道保留、单文件
≤ 512 KB。新增不透明的大面积照片（背景、示意图）优先 JPEG。

## Notebook 策略

- **Notebook 源码入库，输出不入库。** 提交前清空 code cell 的 `outputs` 与
  `execution_count`，并去掉 `metadata.widgets`。当前 4 个 Notebook 的输出（含 13 张
  base64 PNG）占约 3.02 MB，全部是可重新执行得到的图与日志。
- **输出不视为唯一现场证据。** 如果某张实验图需要长期保存，另存到受控归档（`archive/`
  或现场存储），不要依赖 Notebook 内嵌输出。清理前的输出仍可从清理提交之前的 git 历史
  取回（如 `6538b16` 及更早版本），不重写历史。
- **主系统安装与启动不读取 Notebook。** `DPD_auto_test/` 属于独立实验子系统，依赖只在
  `requirements-dpd.txt` 中声明；`Auto_test` 环境不安装 TensorFlow / Jupyter。Notebook
  不属于发布包内容。
- **不拆分仓库。** 清空输出后整个子系统不足 200 KB，且 README 已明确它与主系统的边界；
  只有出现独立发布节奏或依赖冲突时才考虑拆成单独仓库。

## Git LFS 决策

**当前不启用 Git LFS，也不建立 `.gitattributes` 过滤规则。** 依据：

1. 压缩后运行时资源约 1.2 MB，其中最大的单文件 434 KB，普通 git 克隆没有压力；
2. LFS 需要每个克隆环境安装客户端，并依赖远端 LFS 服务；现场机器按“克隆即运行”使用
   本仓库，LFS 指针文件缺失会直接表现为界面缺图；
3. 发布包需要的是实际图片文件，不是 LFS 指针，额外同步步骤没有收益。

重新评估的触发条件（任一满足时在下一步迁移到 LFS 或受控归档）：

- 单个运行时二进制文件超过 5 MB；
- `assets/` 跟踪体积超过 20 MB；
- 同一批大文件频繁变更（每次提交都新增完整 blob，仓库历史快速膨胀）；
- 需要与设计源文件（PSD/AI、原始视频）一起长期保存——这类源文件应放受控归档，
  不进入本仓库。

## 预算与门禁

`tests/test_asset_budget.py` 冻结以下预算，超限即失败：

| 范围 | 预算 | 当前值 |
| --- | ---: | ---: |
| 单个运行时图片 | 512 KB | 433.6 KB |
| `assets/` 跟踪总量 | 2 MB | 约 1.23 MB |
| 单个 Notebook | 256 KB | 63.1 KB |
| `DPD_auto_test/` 跟踪总量 | 512 KB | 约 0.18 MB |

同一测试还检查：

- `assets/instrument_pic/` 只保留被 `device_cards.py` 引用的三张图，没有未引用的大图；
- `theme.WELCOME_BACKGROUND_FILE` 指向的文件存在，且 `instrument_picture_path()`
  对三个角色都能解析到真实文件；
- 每个 Notebook 都没有 code cell 输出；
- `release_manifest.RELEASE_FILES` 含 `assets`，发布清单会验证界面资源随包存在。

## 验证

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" tools/run_tests.py
& "D:\Anaconda\envs\Auto_test\python.exe" launcher.py --check
& "D:\Anaconda\envs\Auto_test\python.exe" launcher.py --validate-config
& "D:\Anaconda\envs\Auto_test\python.exe" tools/export_dependency_lock.py --check

# 资源引用与入库范围
git ls-files assets DPD_auto_test
```

只改图片或 Notebook 输出时仍需跑全量离线测试：资源预算测试位于 `tests/` 根目录，不属于
`--layer ui` 范围。
