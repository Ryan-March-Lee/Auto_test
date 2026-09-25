# PA自动测试系统 - 使用说明

## VSCode中的使用方法

### 推荐方法：使用主启动器
日常使用建议在已激活 `Auto_test` 环境的终端中运行 `python launcher.py`。VSCode 中如需避免输出缓冲，再运行 `vscode_launcher.py`。

### 已验证运行环境

```text
Conda 环境：Auto_test
Python：3.11.15
环境路径：D:\Anaconda\envs\Auto_test
```

历史文档中的 `VISA_demo` 环境当前不存在。不要在 `base` 环境中运行本项目；`base` 缺少 GUI 和仪器控制依赖。

**优势**：
- ✅ 输出立即显示，不会被缓冲
- ✅ 中文显示正常，无乱码
- ✅ 使用正确的conda环境激活方式
- ✅ 在GUI启动前就能看到所有检查信息

### 其他启动方法

#### 1. 批处理文件
双击 `start_gui.bat` 或在终端中运行：
```
./start_gui.bat
```

#### 2. 手动终端命令
在VSCode终端中执行：
```powershell
conda activate Auto_test
python launcher.py --check
python launcher.py --validate-config
python launcher.py
```

#### 3. 系统终端
直接在PowerShell或CMD中：
```powershell
cd "D:\Python_project\Auto_test"
conda activate Auto_test
python launcher.py --validate-config
python launcher.py
```

## 问题说明

**为什么不直接运行launcher.py？**
- VSCode的"Run Python File"使用`conda run`命令
- `conda run`会缓冲所有输出直到程序结束
- 导致GUI启动时看不到检查信息，关闭GUI后才显示（可能乱码）

**解决方案**
- `vscode_launcher.py`使用`conda activate`代替`conda run`
- 设置正确的环境变量确保输出立即显示
- 使用PowerShell执行，避免编码问题

## 文件说明

- `launcher.py` - 主启动器（推荐在终端中使用）
- `vscode_launcher.py` - VSCode专用启动器（推荐在VSCode中使用）
- `enhanced_main_gui.py` - 主GUI程序
- `start_gui.bat` - Windows批处理启动文件，会激活 `Auto_test` 并将命令行参数转发给 `launcher.py`

## 启动检查

- `python launcher.py --check`：检查 Python 和 GUI 依赖。
- `python launcher.py --validate-config`：只读检查 `config.json`，不会连接仪器或改变仪器状态。
- 配置存在错误时，启动器会返回非零退出码并阻止 GUI 启动；配置只有警告时仍允许继续，但应先确认警告内容。
- `--check` 与 `--validate-config` 不能同时使用；未知参数会返回退出码 `2`。
- `--validate-config` 不检查 GUI 依赖，启动前应分别执行这两个检查。

## 自动化测试环境

不要直接使用未激活环境的 `python -m unittest`，因为 Windows 上的 `python` 可能指向 Anaconda base 环境。
项目提供了固定环境入口，会优先使用 `Auto_test` 解释器：

```powershell
./run_tests.bat
```

或在 PowerShell 中运行：

```powershell
./run_tests.ps1
```

默认解释器为 `D:\Anaconda\envs\Auto_test\python.exe`。如环境路径变化，可设置：

```powershell
$env:AUTO_TEST_PYTHON = "D:\path\to\Auto_test\python.exe"
./run_tests.ps1
```

代理或编辑器执行命令时也应使用 `run_tests.bat`/`run_tests.ps1`，不要直接调用裸 `python`。工具进程不会继承其他终端中的 Conda 激活状态，Windows 的 `python` 还可能解析到 `base` 环境。项目脚本会打印实际解释器、执行编译检查，并固定使用 `Auto_test`。

脚本会先执行编译检查，再运行完整 unittest 测试集，并在开始时打印实际使用的解释器路径。

## 直接解释器启动

无需激活 Conda 环境时，可直接使用已验证解释器：

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" launcher.py --check
& "D:\Anaconda\envs\Auto_test\python.exe" launcher.py --validate-config
& "D:\Anaconda\envs\Auto_test\python.exe" launcher.py
```

## 阶段 0.1 基线整理

完成一次完整测试并成功生成 HTML 报告后，程序会自动在 `baseline/collected/` 下创建一份基线样例，包含配置、运行快照、测量结果、报告索引和 Python/依赖版本。

如果报告生成前中断，或需要重新整理最近一次结果，可在项目根目录执行：

```powershell
python collect_baseline.py
```

也可以只整理指定运行目录：

```powershell
python collect_baseline.py --run-id <运行目录名>
```

脚本只复制和汇总已有文件，不覆盖 `test_results/` 中的原始结果。没有某类测量结果时，该类会在 `baseline_manifest.json` 中标记为 `not_found`，不代表测试失败。
