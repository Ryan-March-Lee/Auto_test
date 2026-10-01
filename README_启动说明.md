# PA 自动测试系统启动说明

## 唯一应用启动器

项目只保留根目录的 `launcher.py` 作为应用启动入口。项目已经从 VS Code 迁移到 PyCharm，不再维护 VS Code 专用包装器、VS Code 配置或额外的 GUI 批处理启动脚本。

在 PyCharm 中运行时：

1. 将项目解释器设置为 `Auto_test` Conda 环境。
2. 创建或使用指向 `$PROJECT_DIR$/launcher.py` 的 Python 运行配置。
3. 将工作目录设置为项目根目录。

也可以在终端中执行：

```powershell
conda activate Auto_test
python launcher.py
```

启动前检查：

```powershell
python launcher.py --check
python launcher.py --validate-config
```

`--check` 检查 Python 环境和 GUI 依赖；`--validate-config` 只读校验 `config.json`，不会连接仪器或改变仪器状态。两个参数不能同时使用。

## 运行环境

项目必须使用 `Auto_test` 环境，不要使用 Anaconda `base` 环境。若未激活环境，可将项目根目录 `.env` 中的 `AUTO_TEST_PYTHON` 配置为该环境的 `python.exe`，然后在 PyCharm 的解释器设置中选择同一个解释器。

## 文件说明

- `launcher.py`：唯一的应用启动器。
- `enhanced_main_gui.py`：由启动器加载的 GUI 模块，不应作为独立启动入口。
- `run_tests.ps1` / `run_tests.bat`：测试工具，不是应用启动器。

## 自动化测试

不要直接使用未激活环境的 `python -m unittest`。请使用项目测试入口：

```powershell
./run_tests.ps1
```

或：

```powershell
./run_tests.bat
```
