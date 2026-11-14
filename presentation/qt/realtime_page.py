"""测量页面共享的实时缓存和频点导航适配。"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from .realtime_buffer import RealtimeMeasurementBuffer

__all__ = [
    "RealtimePageMixin",
    "format_latest_number",
    "latest_number",
]


def latest_number(values: Any) -> float | None:
    """取一段扫频数据的最后一个有限数值；空序列或非数值返回 ``None``。"""
    if not isinstance(values, (list, tuple)) or not values:
        return None
    try:
        number = float(values[-1])
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def format_latest_number(values: Any, digits: int = 1) -> str:
    """把一段扫频数据的最后一个值格式化成读数条文本；没有值时返回 ``—``。"""
    number = latest_number(values)
    return "—" if number is None else f"{number:.{digits}f}"


class RealtimePageMixin:
    """图表和历史浏览属于页面，回调只向外提供聊天上下文。"""

    def clear_realtime_data(self) -> None:
        self.clear_realtime_buffer()
        self._clear_realtime_callback()

    def reset_realtime_data(self) -> None:
        """在新测量开始前清理页面数据和外部聊天上下文。"""
        self.clear_realtime_buffer()
        self._clear_realtime_callback()

    def clear_realtime_buffer(self) -> None:
        self.realtime_buffer.clear()
        self._reset_readout()
        self._update_navigation()

    def update_realtime(self, data: Any) -> None:
        if not isinstance(data, Mapping) or "frequency" not in data:
            return
        if not self.realtime_buffer.store(data):
            return
        self._update_plot(dict(data))
        self._update_readout(dict(data))
        self._update_navigation()
        self._realtime_data_callback(dict(data))

    def previous_frequency(self) -> None:
        self.realtime_buffer.previous()
        self._display_current_frequency()

    def next_frequency(self) -> None:
        self.realtime_buffer.next()
        self._display_current_frequency()

    def select_frequency(self, frequency: Any) -> None:
        """胶片条点击：只接受已经测量过的频点，越界或无效值静默忽略。"""
        index = self.realtime_buffer.index_of(frequency)
        if index is None:
            return
        if not self.realtime_buffer.select(index):
            return
        self._display_current_frequency()

    def _display_current_frequency(self) -> None:
        data = self.realtime_buffer.current()
        if data is not None:
            self._update_plot(data)
            self._update_readout(data)
        self._update_navigation()

    def _update_readout(self, data: Mapping[str, Any]) -> None:
        """页面可选覆盖：把当前频点写进读数条；默认不做任何事。"""
        return None

    def _reset_readout(self) -> None:
        """页面可选覆盖：清空实时数据时把读数条恢复到占位状态。"""
        return None

    def _update_navigation(self) -> None:
        buffer = self.realtime_buffer
        widget = self._realtime_plot_widget
        previous = getattr(widget, "nav_prev_btn", None)
        following = getattr(widget, "nav_next_btn", None)
        if previous is not None:
            previous.setEnabled(buffer.current_index > 0)
        if following is not None:
            following.setEnabled(buffer.current_index < len(buffer.frequencies) - 1)
        # 页面自己的绘图控件可能不是 RealTimePlotWidget（测试替身、历史页）：
        # 只有真正提供胶片条入口时才同步，缺失时静默跳过。
        setter = getattr(widget, "set_frequencies", None)
        if callable(setter):
            setter(buffer.frequencies, buffer.current_index)
