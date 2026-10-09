"""增强版 GUI 的历史兼容入口。

窗口实现和应用级组装已经迁移到 :mod:`presentation.qt.main_window`。
此模块只保留旧入口和公共符号的重新导出，避免历史脚本和插件立即失效。
"""

from presentation.qt.main_window import (
    MainWindow,
    build_application,
    run_application,
    main,
    ConnectionDialog,
    RealTimePlotWidget,
    ChatPanel,
    ChatWorker,
    ChatHistoryDialog,
    ChatSettingsDialog,
    ConfigFormState,
    ConfigPage,
    VisualizationPage,
    ExportPage,
    StatusPanel,
    RealtimeMeasurementBuffer,
    normalize_frequency,
    MeasurementKind,
    MeasurementResultReference,
)
from presentation.qt.assistant.chat_worker import UnavailableAssistant as _UnavailableAssistant
from PySide6.QtWidgets import QMessageBox


if __name__ == "__main__":
    main()
