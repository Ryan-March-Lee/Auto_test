"""测量结果空态：一张轻量线稿 + 一句说明，不伪造任何测量数据。

三个测量页在“还没有结果”时不再是表格上方的一行灰字，而是一块有构图的空态：
两块仪器轮廓、一根线缆和一条扫描标记，说明“开始测量后这里会出现什么”。它只做
展示，不持有测量状态、不发信号、也不提供第二个“开始测量”入口——主操作仍然只在
左栏控制面出现，避免同一动作在页面上有两个主按钮。

线稿用 :class:`QPainter` 直接画，颜色全部取自 :data:`presentation.qt.theme.TOKENS`；
模块不导入页面、不读配置、不访问结果 DTO，因此可以在离屏测试里单独构造。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from .theme import TOKENS

__all__ = ["MeasurementEmptyState"]


class _EmptySchematic(QWidget):
    """空态线稿：信号源 → 线缆 → 频谱仪，只解释“结果从哪里来”。"""

    WIDTH = 232
    HEIGHT = 78

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAccessibleName("测量链路示意")

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 覆盖方法
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        border = QColor(TOKENS["border_strong"])
        ink = QColor(TOKENS["text_muted"])
        primary = QColor(TOKENS["primary"])
        surface = QColor(TOKENS["surface"])
        screen = QColor(TOKENS["surface_sunken"])
        accent = QColor(TOKENS["success"])

        self._paint_instrument(painter, QRectF(20, 18, 46, 36), border, surface, screen, ink, accent)
        self._paint_instrument(painter, QRectF(166, 18, 46, 36), border, surface, screen, ink, accent)

        # 线缆：一条轻微下垂的曲线，中点放一个小圆点，表示“连接已建立”。
        cable = QPainterPath(QPointF(68, 36))
        cable.cubicTo(104, 20, 128, 54, 164, 36)
        painter.setPen(QPen(primary, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(cable)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(TOKENS["primary_soft"]))
        painter.drawEllipse(QPointF(116, 36), 5.0, 5.0)
        painter.setBrush(primary)
        painter.drawEllipse(QPointF(116, 36), 2.0, 2.0)

        # 底部扫描标记：三条短虚线只做节奏，不代表任何数据。
        painter.setPen(QPen(QColor(TOKENS["border"]), 1.0, Qt.PenStyle.DashLine))
        for x in (88, 116, 144):
            painter.drawLine(QPointF(x, 64), QPointF(x + 12, 64))

    @staticmethod
    def _paint_instrument(
        painter: QPainter,
        body: QRectF,
        border: QColor,
        surface: QColor,
        screen: QColor,
        ink: QColor,
        accent: QColor,
    ) -> None:
        painter.setPen(QPen(border, 1.2))
        painter.setBrush(surface)
        painter.drawRoundedRect(body, 6.0, 6.0)

        display = body.adjusted(7, 7, -7, -15)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(screen)
        painter.drawRoundedRect(display, 2.0, 2.0)

        painter.setPen(QPen(ink, 1.0))
        painter.drawLine(
            QPointF(display.left() + 4, display.center().y()),
            QPointF(display.right() - 4, display.center().y()),
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        painter.drawEllipse(QPointF(body.right() - 7, body.bottom() - 6), 1.6, 1.6)


class MeasurementEmptyState(QWidget):
    """结果面的空态：线稿 + 标题 + 说明；由页面决定何时显示。"""

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

        self._schematic = _EmptySchematic(self)
        layout.addWidget(
            self._schematic, 0, Qt.AlignmentFlag.AlignHCenter
        )

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
        """写入标题与说明；空标题回落为固定文案，避免出现只有线稿的空态。"""
        cleaned_title = str(title).strip() or "尚无测量结果"
        self.title_label.setText(cleaned_title)
        self.description_label.setText(str(description))
        self.setAccessibleName(cleaned_title)
        self.setAccessibleDescription(str(description))
