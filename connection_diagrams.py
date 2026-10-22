"""连接示意图的历史兼容入口。

生成逻辑归属已迁移到 :mod:`presentation.qt.connection_diagrams`（只服务 GUI
“连接说明”对话框）。本模块只重新导出历史公开符号，不含图形构建或隐式初始化。
"""

from presentation.qt.connection_diagrams import ConnectionDiagram, create_all_diagrams


__all__ = ["ConnectionDiagram", "create_all_diagrams"]
