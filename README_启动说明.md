# PA 自动测试系统启动说明

## 唯一应用启动器

项目只保留根目录的 `launcher.py` 作为应用启动入口。项目已经从 VS Code 迁移到 PyCharm，不再维护 VS Code 专用包装器、VS Code 配置或额外的 GUI 批处理启动脚本。

在 PyCharm 中运行时：

1. 将项目解释器设置为 `Auto_test` Conda 环境。
2. 创建或使用指向 `$PROJECT_DIR$/launcher.py` 的 Python 运行配置。
3. 将工作目录设置为项目根目录。

也可以在终端中执行（解释器取自 `.env` 的 `AUTO_TEST_PYTHON`，要求 Python 3.11.x）：

```powershell
conda activate Auto_test
& $env:AUTO_TEST_PYTHON launcher.py
```

启动前检查：

```powershell
& $env:AUTO_TEST_PYTHON tools/export_dependency_lock.py --check
& $env:AUTO_TEST_PYTHON launcher.py --check
& $env:AUTO_TEST_PYTHON launcher.py --validate-config
```

`--check` 检查 Python 环境、GUI 依赖，并按 `requirements.lock.txt` 核对全部已安装发行版
（含传递依赖）；`--validate-config` 只读校验 `config.json`，不会连接仪器或改变仪器状态。
两个参数不能同时使用。

`--validate-config` 只读校验正式 `test_plan` / `run_mapping` 段与硬件装配段，不代表已创建运行。新测量应先校验并创建运行快照，随后使用同一个 `run_id` 恢复或执行各阶段；禁止将全局 `config.json` 或“最新结果”作为运行阶段的隐式输入。历史查看与导出只读运行目录 manifest 登记的正式结果，迁移校准输入时须人工核验来源并登记到目标运行的 manifest。旧的“最新结果”查询与全局兼容副本已在重构中删除，不再有兼容窗口或告警降级路径。

## 运行环境

项目必须使用 `Auto_test` 环境，不要使用 Anaconda `base` 环境。若未激活环境，可将项目根目录 `.env` 中的 `AUTO_TEST_PYTHON` 配置为该环境的 `python.exe`，然后在 PyCharm 的解释器设置中选择同一个解释器。解释器版本固定为 Python 3.11.x；依赖按 `requirements.lock.txt` 安装，完整约定见[依赖与运行时契约](docs/依赖与运行时契约.md)。

## 文件说明

- `launcher.py`：唯一的应用启动器；GUI 主窗口位于 `presentation/qt/main_window.py`。
- `run_tests.ps1` / `run_tests.bat`：测试工具，不是应用启动器。
- `requirements.lock.txt`：主系统完整依赖锁定；`requirements-dpd.txt` 只服务 `DPD_auto_test`。
- `tools/export_dependency_lock.py`：锁定清单的唯一导出与核对入口；改了依赖就在 `Auto_test` 环境重新运行，不要手工编辑或裸 `pip freeze` 覆盖。

## 自动化测试

不要直接使用未激活环境的 `python -m unittest`。请使用项目测试入口：

```powershell
./run_tests.ps1
```

或：

```powershell
./run_tests.bat
```
