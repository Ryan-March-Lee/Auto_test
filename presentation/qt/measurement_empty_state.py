"""测量结果空态：标题 + 一句说明，不伪造任何测量数据。

线损测量页在“还没有结果”时用一块文字空态说明“开始测量后这里会出现什么”。它只做
展示，不持有测量状态、不发信号、也不提供第二个“开始测量”入口——主操作仍然只在
左栏控制面出现，避免同一动作在页面上有两个主按钮。

模块不导入页面、不读配置、不访问结果 DTO，因此可以在离屏测试里单独构造。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

__all__ = ["MeasurementEmptyState"]


class MeasurementEmptyState(QWidget):
    """结果面的空态：标题 + 说明；由页面决定何时显示。"""

    def __init__(
        self,
        title: str = "尚无测量结果",
        description: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("measurementEmptyState")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.title_label = QLabel(self)
        self.title_label.setObjectName("measurementEmptyStateTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.title_label)

        self.description_label = QLabel(self)
        self.description_label.setObjectName("measurementEmptyStateDescription")
        self.description_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.description_label.setWordWrap(True)
        self.description_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.description_label)

        self.set_texts(title, description)

    def title(self) -> str:
        """当前标题文本；只读，供测试与无障碍描述使用。"""
        return self.title_label.text()

    def description(self) -> str:
        """当前说明文本；只读，供测试与无障碍描述使用。"""
        return self.description_label.text()

    def set_texts(self, title: str, description: str) -> None:
        """写入标题与说明；空标题回落为固定文案，避免出现没有标题的空态。"""
        cleaned_title = str(title).strip() or "尚无测量结果"
        self.title_label.setText(cleaned_title)
        self.description_label.setText(str(description))
        self.setAccessibleName(cleaned_title)
        self.setAccessibleDescription(str(description))
