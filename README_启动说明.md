# PA自动测试系统 - 使用说明

## VSCode中的使用方法

### 推荐方法：使用主启动器
日常使用建议在已激活 `Auto_test` 环境的终端中运行 `python launcher.py`。VSCode 中如需避免输出缓冲，再运行 `vscode_launcher.py`。

### 已验证运行环境

```text
Conda 环境：Auto_test
Python：3.11.15
环境路径：C:\My_Document\Anaconda\envs\Auto_test
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
cd "C:\My_Document\Python_project\pa_auto_test"
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

## 直接解释器启动

无需激活 Conda 环境时，可直接使用已验证解释器：

```powershell
& "C:\My_Document\Anaconda\envs\Auto_test\python.exe" launcher.py --check
& "C:\My_Document\Anaconda\envs\Auto_test\python.exe" launcher.py --validate-config
& "C:\My_Document\Anaconda\envs\Auto_test\python.exe" launcher.py
```
