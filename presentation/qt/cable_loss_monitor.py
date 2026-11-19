"""线损总览监控台：结果面顶部的四等分实时统计带。

线损页原来的读数条回答“最新频点、四条线缆各是多少”，但这些值在频点胶片条与
曲线上都已经出现；真正需要一眼看到的是本轮测试到目前为止的总体表现。本模块把
一条深色玻璃带等分成四格，每格是一条线缆的 ``Max`` / ``Min`` / ``Avg``：

- 统计口径是页面已经显示的频点（最大值、最小值与算术平均），缺数据时写“—”；
- 组件只排版调用方算好的 :class:`CableLossStats`，不读结果 DTO、不读文件，
  也不判断损耗是否合格；
- 底色沿用读数条的深色玻璃语言（:func:`paint_readout_surface`），文字由样式表
  按语义 objectName 着色，颜色全部取自 :data:`presentation.qt.theme.TOKENS`。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from statistics import fmean

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .readout_surface import paint_readout_surface

__all__ = ["CableLossMonitor", "CableLossStats"]

#: 每格里的三行统计；顺序固定，界面与无障碍文本共用同一组标签。
_STAT_ROWS = (("Max", "maximum"), ("Min", "minimum"), ("Avg", "average"))

#: 没有数值时统一写一个破折号，不伪造 0.000。
_PLACEHOLDER = "—"


@dataclass(frozen=True)
class CableLossStats:
    """一条线缆到目前为止的损耗统计；三项要么都有值，要么都没有。"""

    maximum: float | None = None
    minimum: float | None = None
    average: float | None = None

    def __post_init__(self) -> None:
        values = (self.maximum, self.minimum, self.average)
        present = 0
        for value in values:
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError("线缆损耗统计必须是数值或 None")
            if not math.isfinite(float(value)):
                raise ValueError("线缆损耗统计只接受有限数值")
            present += 1
        if present not in (0, len(values)):
            raise ValueError("最大、最小与平均损耗必须同时有值或同时为空")
        if present and not (self.minimum <= self.average <= self.maximum):
            raise ValueError("线缆损耗统计的大小顺序不符：Min ≤ Avg ≤ Max")

    @classmethod
    def from_values(cls, values: Iterable[float]) -> "CableLossStats":
        """按一组已测量的损耗计算统计量；空序列返回全空占位。"""
        samples: list[float] = []
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError("线缆损耗统计只能由数值构成")
            number = float(value)
            if not math.isfinite(number):
                raise ValueError("线缆损耗统计只接受有限数值")
            samples.append(number)
        if not samples:
            return cls()
        return cls(
            maximum=max(samples),
            minimum=min(samples),
            average=fmean(samples),
        )


def _format_value(value: float | None) -> str:
    """统一三位小数；真值与负零都写成 ``0.000``，避免出现 ``-0.000``。"""
    if value is None:
        return _PLACEHOLDER
    number = float(value)
    if number == 0.0:
        number = 0.0
    return f"{number:.3f}"


class _CableStatCell(QFrame):
    """一格线缆统计：线缆名与单位在上，Max/Min/Avg 三行在下。"""

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("cableLossMonitorCell")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._label = label
        self._stats = CableLossStats()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)
        self.name_label = QLabel(label, self)
        self.name_label.setObjectName("cableLossMonitorCable")
        self.name_label.setTextFormat(Qt.TextFormat.PlainText)
        header.addWidget(self.name_label)
        header.addStretch(1)
        self.unit_label = QLabel("dB", self)
        self.unit_label.setObjectName("cableLossMonitorUnit")
        self.unit_label.setTextFormat(Qt.TextFormat.PlainText)
        header.addWidget(
            self.unit_label, 0, Qt.AlignmentFlag.AlignRight
            | Qt.AlignmentFlag.AlignBottom
        )
        layout.addLayout(header)
        layout.addSpacing(6)

        hairline = QFrame(self)
        hairline.setObjectName("cableLossMonitorHairline")
        hairline.setFixedHeight(1)
        layout.addWidget(hairline)
        layout.addSpacing(5)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(2)
        self._values: dict[str, QLabel] = {}
        for row, (caption_text, field_name) in enumerate(_STAT_ROWS):
            caption = QLabel(caption_text, self)
            caption.setObjectName("cableLossMonitorMetricLabel")
            caption.setTextFormat(Qt.TextFormat.PlainText)
            value = QLabel(_PLACEHOLDER, self)
            value.setObjectName("cableLossMonitorValue")
            value.setTextFormat(Qt.TextFormat.PlainText)
            value.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            grid.addWidget(caption, row, 0)
            grid.addWidget(value, row, 1)
            self._values[field_name] = value
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        layout.addStretch(1)
        self.set_stats(CableLossStats())

    def set_stats(self, stats: CableLossStats) -> None:
        if not isinstance(stats, CableLossStats):
            raise TypeError("线缆统计必须是 CableLossStats")
        self._stats = stats
        for _caption, field_name in _STAT_ROWS:
            self._values[field_name].setText(
                _format_value(getattr(stats, field_name))
            )
        self.setAccessibleName(self._accessible_text(stats))

    def stats(self) -> CableLossStats:
        return self._stats

    def value_text(self, field_name: str) -> str:
        """某一行的显示文本，供页面与测试核对；“maximum”等字段名与统计对象一致。"""
        try:
            return self._values[field_name].text()
        except KeyError as error:
            raise ValueError(f"未知的统计行: {field_name}") from error

    def name_text(self) -> str:
        return self.name_label.text()

    def _accessible_text(self, stats: CableLossStats) -> str:
        parts = [
            f"{caption} {_format_value(getattr(stats, field_name))}"
            for caption, field_name in _STAT_ROWS
        ]
        return f"{self._label}，" + "，".join(parts)


class CableLossMonitor(QFrame):
    """四等分的线损总览带；只展示调用方已经算好的统计量。"""

    #: 统计带最小高度：线缆名、单位与三行统计都要放得下。
    MINIMUM_HEIGHT = 108

    def __init__(
        self,
        labels: Sequence[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if isinstance(labels, (str, bytes)) or not isinstance(labels, Sequence):
            raise TypeError("线缆标签必须是字符串序列")
        names = tuple(labels)
        if not names:
            raise ValueError("线缆标签不能为空")
        if not all(isinstance(name, str) and name.strip() for name in names):
            raise ValueError("线缆标签必须是非空字符串")

        self.setObjectName("cableLossMonitor")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.setMinimumHeight(self.MINIMUM_HEIGHT)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)
        self._cells: list[_CableStatCell] = []
        for name in names:
            cell = _CableStatCell(name, self)
            layout.addWidget(cell, 1)
            self._cells.append(cell)
        self._stats = tuple(CableLossStats() for _ in self._cells)

    # ---- 读取 ---------------------------------------------------------------

    def stats(self) -> tuple[CableLossStats, ...]:
        return self._stats

    def cell_count(self) -> int:
        return len(self._cells)

    def cell(self, index: int) -> _CableStatCell:
        return self._cells[index]

    def value_text(self, index: int, field_name: str) -> str:
        """第 ``index`` 格某一行的显示文本。"""
        return self._cells[index].value_text(field_name)

    # ---- 写入 ---------------------------------------------------------------

    def set_stats(self, stats: Sequence[CableLossStats]) -> None:
        """按线缆顺序写入统计；必须与构造时的标签数量一致。"""
        if isinstance(stats, (str, bytes)) or not isinstance(stats, Sequence):
            raise TypeError("线缆统计必须是 CableLossStats 序列")
        validated = tuple(stats)
        for item in validated:
            if not isinstance(item, CableLossStats):
                raise TypeError("线缆统计必须是 CableLossStats 序列")
        if len(validated) != len(self._cells):
            raise ValueError(
                f"线缆统计数量 {len(validated)} 与监控台格数 "
                f"{len(self._cells)} 不一致"
            )
        for cell, item in zip(self._cells, validated):
            cell.set_stats(item)
        self._stats = validated

    def clear(self) -> None:
        """回到空态：四格都写“—”，不保留上一轮的数字。"""
        self.set_stats(tuple(CableLossStats() for _ in self._cells))

    # ---- 绘制 ---------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 覆盖方法
        """深色玻璃底与细网格；四格由子控件绘制，父控件不再铺第二层卡片。"""
        painter = QPainter(self)
        paint_readout_surface(painter, self)
