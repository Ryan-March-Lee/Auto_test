"""频点胶片条：把已测频点排成一行可点击的 chip。

驱动映射、主功放和可视化页原先只用“◀ / ▶”两个箭头浏览频点：频点多时看不出整体
进度，也不知道下一个会跳到哪里。胶片条把已经到达的频点排成一条可横向滚动的带：

- 每个 chip 是一个已测量的频点，当前频点用主色描边＋主色文字高亮；
- 点击 chip 直接切到该频点，箭头仍保留用于逐点前后移动；
- 频点较多时自动滚到当前项，不把用户丢在带子中段以外。

控件只消费调用方已经显示的频率列表，不读取结果、不访问配置、不发测量命令；它是
一个受控视图：点击只发出 ``frequency_selected(float)``，真正的切换由页面完成。
颜色全部来自 :data:`presentation.qt.theme.TOKENS`，由主题的
``QPushButton#frequencyChip`` 选择器着色。
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QWidget,
)

__all__ = ["FrequencyFilmstrip", "format_frequency"]


def format_frequency(value: float) -> str:
    """把频率格式化成 chip 文本：最多三位小数，去掉无意义的尾零。"""
    text = f"{float(value):.3f}".rstrip("0").rstrip(".")
    return text or "0"


def _normalise(frequencies: Sequence[float]) -> tuple[float, ...]:
    if isinstance(frequencies, (str, bytes)) or not isinstance(
        frequencies, Sequence
    ):
        raise TypeError("频点必须是数值序列")
    values: list[float] = []
    for value in frequencies:
        if isinstance(value, bool):
            raise TypeError("频点必须是数值")
        try:
            number = float(value)
        except (TypeError, ValueError) as error:
            raise TypeError("频点必须是数值") from error
        if not math.isfinite(number):
            raise ValueError("频点必须是有限数值")
        values.append(number)
    return tuple(values)


class FrequencyFilmstrip(QWidget):
    """一条可横向滚动的频点 chip 带；空列表时整条隐藏。"""

    #: 用户点击某个频点；载荷是频率（GHz），页面负责真正切换。
    frequency_selected = Signal(float)

    #: 固定高度：胶片条只占一行，不参与结果面的纵向伸缩。
    HEIGHT = 30

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("frequencyFilmstrip")
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setObjectName("frequencyFilmstripScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll_area.viewport().setAutoFillBackground(False)

        self._content = QWidget(self.scroll_area)
        self._content.setObjectName("frequencyFilmstripContent")
        self._row = QHBoxLayout(self._content)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(6)
        # 尾部留白吸收多余宽度：chip 保持自身宽度，不把整条带均分拉满。
        self._row.addStretch(1)
        self.scroll_area.setWidget(self._content)
        outer.addWidget(self.scroll_area, 1)

        self._chips: list[QPushButton] = []
        self._frequencies: tuple[float, ...] = ()
        self._current = -1
        self.hide()

    # ---- 读取 ---------------------------------------------------------------

    def frequencies(self) -> tuple[float, ...]:
        """当前显示的频点（只读）。"""
        return self._frequencies

    def current_index(self) -> int:
        """当前高亮项的索引；没有频点时返回 ``-1``。"""
        return self._current

    def chips(self) -> tuple[QPushButton, ...]:
        """当前 chip 控件（只读，供测试与无障碍检查使用）。"""
        return tuple(self._chips)

    # ---- 写入 ---------------------------------------------------------------

    def set_frequencies(
        self, frequencies: Sequence[float], current_index: int = 0
    ) -> None:
        """写入频点与当前项；频点未变化时只移动高亮，不重建控件。"""
        values = _normalise(frequencies)
        if values != self._frequencies:
            self._frequencies = values
            self._rebuild(values)
        if not values:
            self._current = -1
            self.hide()
            return
        if isinstance(current_index, bool) or not isinstance(current_index, int):
            index = -1
        else:
            index = current_index
        index = min(max(index, 0), len(values) - 1)
        self._apply_current(index)
        self.show()

    def clear(self) -> None:
        """清空胶片条并隐藏；清空是幂等的。"""
        if not self._frequencies and self._current == -1 and self.isHidden():
            return
        self._frequencies = ()
        self._current = -1
        self._rebuild(())
        self.hide()

    # ---- 内部 ---------------------------------------------------------------

    def _rebuild(self, values: tuple[float, ...]) -> None:
        for chip in self._chips:
            self._row.removeWidget(chip)
            chip.setParent(None)
            chip.deleteLater()
        self._chips.clear()
        for index, value in enumerate(values):
            chip = QPushButton(self._content)
            chip.setObjectName("frequencyChip")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            # 列表里的频点都已经测量完成：勾号表示“已到达”，不是合格判定。
            chip.setText(f"✓ {format_frequency(value)}")
            chip.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            chip.setProperty("current", False)
            chip.setToolTip(f"{format_frequency(value)} GHz")
            chip.setAccessibleName(f"{format_frequency(value)} GHz 频点")
            chip.clicked.connect(
                lambda _checked=False, frequency=value: self.frequency_selected.emit(
                    frequency
                )
            )
            self._row.insertWidget(index, chip)
            self._chips.append(chip)
        self._current = -1

    def _apply_current(self, index: int) -> None:
        self._current = index
        for position, chip in enumerate(self._chips):
            current = position == index
            if chip.property("current") != current:
                chip.setProperty("current", current)
                chip.style().unpolish(chip)
                chip.style().polish(chip)
                chip.update()
            suffix = "（当前）" if current else ""
            chip.setToolTip(f"{format_frequency(self._frequencies[position])} GHz{suffix}")
            chip.setAccessibleDescription(
                "当前频点" if current else "已测量频点"
            )
        self._ensure_current_visible()

    def _ensure_current_visible(self) -> None:
        if 0 <= self._current < len(self._chips):
            self.scroll_area.ensureWidgetVisible(self._chips[self._current], 20, 0)
