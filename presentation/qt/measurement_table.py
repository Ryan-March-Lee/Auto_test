"""测量结果表：等宽数字、行内相对数据条与“当前频点”行轨道。

测量页的原始 ``QTableWidget`` 只负责装数据，视觉上是默认表格：数字左对齐、行高偏
小、交替行几乎看不出差别，用户只能逐个读数字。本模块把表格的可见部分收在两层：

- :class:`MeasurementTable`：行高、行选择、表头色点与当前行状态；
- :class:`MeasurementValueDelegate`：数值列右对齐 + 等宽字体，并在数字后面画一条
  按“本列相对大小”归一化的浅色数据条。

数据条只回答“这一列里哪个值更大”，**不推断合格/不合格**：测量配置里没有线损限值，
颜色和长度都不能被读成通过/失败。需要限值的判定留在业务层，表格不新增第二个真相源。

模块只读表格里已经显示的文本，不访问结果 DTO、配置或测量服务；颜色全部取自
:data:`presentation.qt.theme.TOKENS`，页面不再为单元格写内联样式。
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHeaderView,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from .theme import TOKENS, base_font

__all__ = [
    "MeasurementRowDelegate",
    "MeasurementTable",
    "header_item",
]


def _dot_pixmap(color: str) -> QPixmap:
    """表头的小圆点：用系列色标出“这一列是哪条线缆”。"""
    pixmap = QPixmap(10, 10)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(2, 2, 6, 6)
    painter.end()
    return pixmap


def header_item(text: str, color: str | None = None) -> QTableWidgetItem:
    """构造一个表头条目；``color`` 非空时在前面放一个系列色圆点。"""
    item = QTableWidgetItem(str(text))
    item.setTextAlignment(
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    )
    if color is not None:
        item.setData(Qt.ItemDataRole.DecorationRole, _dot_pixmap(color))
    return item


def _read_number(text: str) -> float | None:
    """把单元格文本读成有限数值；空文本、非数值与 NaN/±inf 都返回 ``None``。"""
    try:
        value = float(str(text).strip())
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


class MeasurementRowDelegate(QStyledItemDelegate):
    """结果表的行绘制：当前行轨道、数值列数据条与等宽右对齐。"""

    def __init__(
        self,
        table: "MeasurementTable",
        *,
        value_columns: Sequence[int],
    ) -> None:
        super().__init__(table)
        self._table = table
        self._value_columns = frozenset(value_columns)
        self._ranges: dict[int, tuple[float, float]] = {}
        self._mono_font = QFont(str(TOKENS["font_mono"]))
        self._mono_font.setPointSizeF(9.0)
        self._mono_font.setWeight(QFont.Weight.Medium)
        self._text_font = base_font()

    def set_ranges(self, ranges: dict[int, tuple[float, float]]) -> None:
        """写入每列的相对范围；没有数据的列不画数据条。"""
        self._ranges = dict(ranges)

    def value_range(self, column: int) -> tuple[float, float] | None:
        """某一列的相对范围；没有数据时返回 ``None``。"""
        return self._ranges.get(column)

    def paint(self, painter, option, index) -> None:  # noqa: N802 - Qt 覆盖方法
        """先让样式表画行底与选中态，再画当前行轨道、数据条与文字。"""
        view_option = QStyleOptionViewItem(option)
        self.initStyleOption(view_option, index)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        view_option.text = ""
        widget = option.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(
            QStyle.ControlElement.CE_ItemViewItem,
            view_option,
            painter,
            widget,
        )

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        current = self._table.current_row()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if index.row() == current and not selected:
            tint = QColor(TOKENS["primary"])
            tint.setAlpha(10)
            painter.fillRect(QRectF(option.rect), tint)
        if index.row() == current and index.column() == 0:
            painter.fillRect(
                QRectF(
                    float(option.rect.left()),
                    float(option.rect.top()),
                    3.0,
                    float(option.rect.height()),
                ),
                QColor(TOKENS["primary"]),
            )

        if not text:
            painter.restore()
            return

        if index.column() in self._value_columns:
            self._paint_value(
                painter, option.rect, index.column(), text, selected
            )
        else:
            self._paint_text(painter, option.rect, text, selected)
        painter.restore()

    def _paint_value(
        self,
        painter: QPainter,
        rect,
        column: int,
        text: str,
        selected: bool,
    ) -> None:
        value = _read_number(text)
        if value is not None:
            low_high = self._ranges.get(column)
            if low_high is None or low_high[1] <= low_high[0]:
                ratio = 0.55
            else:
                low, high = low_high
                ratio = 0.25 + 0.75 * ((value - low) / (high - low))
            bar_width = max(8, int((rect.width() - 18) * ratio))
            bar = QRectF(
                float(rect.right() - 9 - bar_width),
                float(rect.center().y() - 6),
                float(bar_width),
                12.0,
            )
            color = QColor(TOKENS["primary"])
            color.setAlpha(54 if selected else 38)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(bar, 3.0, 3.0)

        painter.setFont(self._mono_font)
        painter.setPen(
            QColor(TOKENS["primary_pressed"] if selected else TOKENS["text"])
        )
        painter.drawText(
            QRectF(rect.adjusted(8, 0, -9, 0)),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            text,
        )

    def _paint_text(
        self,
        painter: QPainter,
        rect,
        text: str,
        selected: bool,
    ) -> None:
        painter.setFont(self._text_font)
        painter.setPen(
            QColor(TOKENS["primary_pressed"] if selected else TOKENS["text"])
        )
        painter.drawText(
            QRectF(rect.adjusted(9, 0, -9, 0)),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            text,
        )


class MeasurementTable(QTableWidget):
    """测量结果表：共享行高、当前行与数值列编码。

    ``value_columns`` 是画数据条的列号；其它列仍按普通文本绘制。当前行由页面写入
    :meth:`set_current_row`，表格只负责显示那 3px 主色轨，不改变选中项，也不触发
    任何测量逻辑。
    """

    #: 行高：默认 QTableWidget 的 30px 太紧，读数表需要更松的竖向节奏。
    ROW_HEIGHT = 34
    #: 数值列数据条的列号：线损表前四列是频率，后四列才是 dB 读数。
    DEFAULT_VALUE_COLUMNS = (1, 2, 3, 4)

    def __init__(
        self,
        *,
        value_columns: Sequence[int] = DEFAULT_VALUE_COLUMNS,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("measurementResultTable")
        self._value_columns = tuple(value_columns)
        self._current_row = -1
        self._delegate = MeasurementRowDelegate(
            self, value_columns=self._value_columns
        )
        self.setItemDelegate(self._delegate)

        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCornerButtonEnabled(False)

        vertical = self.verticalHeader()
        vertical.setVisible(False)
        vertical.setDefaultSectionSize(self.ROW_HEIGHT)
        vertical.setMinimumSectionSize(self.ROW_HEIGHT)

        horizontal = self.horizontalHeader()
        horizontal.setHighlightSections(False)
        horizontal.setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        horizontal.setIconSize(QSize(10, 10))
        horizontal.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

    def current_row(self) -> int:
        """当前频点所在行；``-1`` 表示没有当前行。"""
        return self._current_row

    def value_range(self, column: int) -> tuple[float, float] | None:
        """某一数值列当前的相对范围；没有数据或不是数值列时返回 ``None``。"""
        return self._delegate.value_range(column)

    def set_current_row(self, row: int) -> None:
        """只更新“当前行”标记；越界行按 ``-1`` 处理，不抛异常打断数据刷新。"""
        if isinstance(row, bool) or not isinstance(row, int):
            raise TypeError("当前行必须是整数")
        resolved = row if 0 <= row < self.rowCount() else -1
        if resolved == self._current_row:
            return
        self._current_row = resolved
        self.viewport().update()

    def set_headers(self, headers: Sequence[tuple[str, str | None]]) -> None:
        """写入表头文本与可选的系列色点；数量必须与列数一致。"""
        if len(headers) != self.columnCount():
            raise ValueError("表头数量必须与列数一致")
        for column, (text, color) in enumerate(headers):
            self.setHorizontalHeaderItem(column, header_item(text, color))

    def refresh_value_ranges(self) -> None:
        """按当前显示文本重算每列的最小/最大，供数据条按相对大小归一化。"""
        ranges: dict[int, tuple[float, float]] = {}
        for column in self._value_columns:
            values: list[float] = []
            for row in range(self.rowCount()):
                item = self.item(row, column)
                if item is None:
                    continue
                value = _read_number(item.text())
                if value is not None:
                    values.append(value)
            if values:
                ranges[column] = (min(values), max(values))
        self._delegate.set_ranges(ranges)
        self.viewport().update()
