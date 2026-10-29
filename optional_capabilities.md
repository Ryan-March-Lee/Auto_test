# 可选能力边界与调用者盘点

本文件记录阶段 7 的边界。可选能力不参与测量服务初始化、运行快照、普通日志或硬件生命周期。

## 格式转换与连接图

- `format_convert.csv_to_MDIF` 的独立入口是 `& $env:AUTO_TEST_PYTHON -m format_convert.csv_to_MDIF INPUT OUTPUT`，导入不会读写文件。
- `format_convert.svg_to_png_transparent` 只在函数调用时加载 Selenium/Pillow，并由调用者明确指定输入和输出路径。
- 连接图生成逻辑位于 `presentation/qt/connection_diagrams.py`，由 GUI 的连接说明对话框按需导入，不进入测量服务初始化；根目录 `connection_diagrams.py` 兼容入口已删除。
- 连接图不可用（可选依赖缺失、图工厂失败、未知图类型）时 `ConnectionDialog.build_diagram()` 返回空图加原因，对话框禁用“我已正确连接”，只允许取消：空图不得当作有效接线图用于现场确认。
- 连接图的设备布局与标题集中在 `presentation.qt.connection_diagrams.DIAGRAM_DEVICES` / `DIAGRAM_TITLES`，回归测试直接断言这些数据。
- 连接图的字体样式由 `presentation.qt.connection_diagrams.configure_diagram_style()` 在创建图形时按需应用；模块导入不改动全局 matplotlib 配置。
- 格式转换产物、图片和临时文件必须写入调用者指定的输出目录；不得写入运行快照目录，除非未来建立显式报告导出协议。

## DPD

DPD 的输入输出和依赖见 `DPD_auto_test/README.md` 与 `requirements-dpd.txt`。当前主系统没有导入者，禁止其隐式读取或修改主系统配置、结果和运行目录。DPD 依赖（TensorFlow、Jupyter、SciPy、nptdms 等）只在 DPD 自有环境安装，不进入 `requirements.txt` / `requirements.lock.txt`，详见[依赖与运行时契约](docs/依赖与运行时契约.md)。

## 当前调用者盘点

| 能力 | 当前调用者 | 过渡状态 |
| --- | --- | --- |
| 连接图 | `ConnectionDialog` 按需导入 `presentation.qt.connection_diagrams` | 正式实现，无根目录入口 |
| `format_convert` | 无生产调用者 | 独立 CLI |
| `DPD_auto_test` | 无 PA 主系统调用者 | 独立子系统 |

删除过渡实现的门槛：无调用者、至少一个稳定生产周期、历史数据仍可读、回滚包可用，并完成必要的真实设备验收。
