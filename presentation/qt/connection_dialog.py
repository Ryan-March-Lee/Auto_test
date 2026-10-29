"""连接说明对话框：按需渲染连接示意图。

连接图是可选能力（依赖 matplotlib 绘图实现），因此图渲染失败不会抛出到窗口：
对话框退化为空图并在窗口内给出原因，同时把原因写入日志。
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from infrastructure.logging.app_logging import get_logger

from .theme import set_button_variant

logger = get_logger(__name__)


def _diagram_factories(connection_diagram: Any) -> dict[str, Callable[[], Figure]]:
    """连接图类型 -> 显式实现方法；不使用方法名字符串路由。"""
    return {
        "cable_loss_path1": connection_diagram.create_cable_loss_path1,
        "cable_loss_path2": connection_diagram.create_cable_loss_path2,
        "driver_mapping": connection_diagram.create_driver_mapping,
        "amplifier_test": connection_diagram.create_amplifier_test,
        "amplifier_test_no_driver": connection_diagram.create_amplifier_test_no_driver,
    }


def build_diagram(diagram_type: str) -> tuple[Figure, str | None]:
    """返回 ``(连接图, 不可用原因)``；不可用时给空图，原因供对话框显示。"""
    try:
        from presentation.qt.connection_diagrams import ConnectionDiagram
    except ImportError as error:
        logger.info("连接图功能未启用（缺少可选依赖）: %s", error)
        return Figure(), f"连接图功能未启用: {error}"
    factory = _diagram_factories(ConnectionDiagram).get(diagram_type)
    if factory is None:
        logger.warning("未知的连接图类型: %s", diagram_type)
        return Figure(), f"未知的连接图类型: {diagram_type}"
    try:
        return factory(), None
    except (OSError, RuntimeError, ValueError, TypeError) as error:
        logger.warning("连接图渲染失败: %s", error)
        return Figure(), f"连接图渲染失败: {error}"


class ConnectionDialog(QDialog):
    """现场接线确认对话框：显示连接图并让用户明确确认。

    连接图不可用（可选依赖缺失、图工厂失败或未知图类型）时**禁用确认按钮**：
    看不到接线图就确认接线等于绕过现场核对，只允许取消。
    """

    def __init__(self, diagram_type: str, parent: Any = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("连接说明")
        self.setModal(True)
        self.resize(880, 640)
        self.figure, self.unavailable_reason = build_diagram(diagram_type)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(12)
        layout.addWidget(FigureCanvas(self.figure), 1)
        if self.unavailable_reason:
            reason_label = QLabel(
                f"{self.unavailable_reason}\n"
                "连接图不可用时不能确认接线，请先修复后重新打开该对话框。"
            )
            reason_label.setWordWrap(True)
            reason_label.setProperty("tone", "danger")
            layout.addWidget(reason_label)
        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.cancel_btn = QPushButton("取消")
        set_button_variant(self.cancel_btn, "secondary")
        self.ok_btn = QPushButton("我已正确连接")
        set_button_variant(self.ok_btn, "primary")
        self.cancel_btn.clicked.connect(self.reject)
        self.ok_btn.clicked.connect(self.accept)
        self.ok_btn.setEnabled(self.unavailable_reason is None)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.ok_btn)
        layout.addLayout(buttons)


__all__ = ["ConnectionDialog", "build_diagram"]
