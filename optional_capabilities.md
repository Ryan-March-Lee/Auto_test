# 可选能力边界与调用者盘点

本文件记录阶段 7 的边界。可选能力不参与测量服务初始化、运行快照、普通日志或硬件生命周期。

## AI、搜索与聊天历史

- 实现位于 `assistant/llm.py` 和 `assistant/web_search_tools.py`。
- 聊天历史和搜索配置的路径、JSON 读写由 `assistant/storage.py` 管理，GUI 不直接访问这些文件。
- `enhanced_main_gui.py` 仅在创建聊天面板时按需导入；初始化失败时使用不可用占位面板，主 GUI 和测量页面仍可启动。
- `chat_history*.json`、`chat_settings.json` 和 `search_api_config*.json` 只属于 assistant 配置，不写入测量快照。
- 根目录 `llm.py` 与 `web_search_tools.py` 是过渡兼容导入，当前没有主测量调用者；至少经过一个稳定生产周期后再评估删除。

## 格式转换与连接图

- `format_convert.csv_to_MDIF` 的独立入口是 `python -m format_convert.csv_to_MDIF INPUT OUTPUT`，导入不会读写文件。
- `format_convert.svg_to_png_transparent` 只在函数调用时加载 Selenium/Pillow，并由调用者明确指定输入和输出路径。
- `connection_diagrams.py` 只由 GUI 的连接说明对话框按需导入，不进入测量服务初始化。
- 格式转换产物、图片和临时文件必须写入调用者指定的输出目录；不得写入运行快照目录，除非未来建立显式报告导出协议。

## DPD

DPD 的输入输出和依赖见 `DPD_auto_test/README.md` 与 `requirements-dpd.txt`。当前主系统没有导入者，禁止其隐式读取或修改主系统配置、结果和运行目录。

## 当前调用者盘点

| 能力 | 当前调用者 | 过渡状态 |
| --- | --- | --- |
| `assistant.llm` | GUI `ChatPanel` 按需导入 | 保留根目录 shim |
| `assistant.web_search_tools` | `assistant.llm` | 仅 AI 功能使用 |
| 聊天历史和搜索配置 | GUI 聊天设置 | 不进入测量快照 |
| 连接图 | `ConnectionDialog` 按需导入 | 保留旧模块位置 |
| `format_convert` | 无生产调用者 | 独立 CLI |
| `DPD_auto_test` | 无 PA 主系统调用者 | 独立子系统 |

删除过渡实现的门槛：无调用者、至少一个稳定生产周期、历史数据仍可读、回滚包可用，并完成必要的真实设备验收。
