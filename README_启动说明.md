# PA自动测试系统 - 使用说明

## VSCode中的使用方法

### 推荐方法：使用VSCode专用启动器
在VSCode中点击运行 `vscode_launcher.py` 文件，而不是直接运行 `launcher.py`。

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
conda activate VISA_demo
python launcher.py
```

#### 3. 系统终端
直接在PowerShell或CMD中：
```powershell
cd "D:\All_Projects\VsCode_projects\pa_auto_test"
conda activate VISA_demo  
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
- `start_gui.bat` - Windows批处理启动文件