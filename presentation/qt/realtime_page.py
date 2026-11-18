"""测量页面共享的实时缓存和频点导航适配。"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from .realtime_buffer import RealtimeMeasurementBuffer, normalize_frequency

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
        # 用户浏览某个已测频点时，新到达的频点只进缓存、更新胶片条，不抢占画面；
        # 只有到达的数据属于当前正在看的频点时，才刷新它的曲线与读数。
        if self.realtime_buffer.user_browsing:
            if self._incoming_is_current(data):
                current = self.realtime_buffer.current()
                if current is not None:
                    self._update_plot(dict(current))
                    self._update_readout(dict(current))
            self._update_navigation()
            self._realtime_data_callback(dict(data))
            return
        self._update_plot(dict(data))
        self._update_readout(dict(data))
        self._update_navigation()
        self._realtime_data_callback(dict(data))

    def _incoming_is_current(self, data: Mapping[str, Any]) -> bool:
        """到达的数据是否属于当前浏览的频点；频率无效时按“否”处理。"""
        incoming = normalize_frequency(data.get("frequency"))
        current = self.realtime_buffer.current()
        if incoming is None or current is None:
            return False
        return incoming == normalize_frequency(current.get("frequency"))

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
            self._update_readout(data, animate=True)
        self._update_navigation()

    def _update_readout(
        self, data: Mapping[str, Any], *, animate: bool = False
    ) -> None:
        """页面可选覆盖：把当前频点写进读数条；默认不做任何事。

        ``animate`` 只在用户主动切换频点（胶片条/箭头）时为 ``True``；实时数据流
        到达时保持 ``False``，读数条不逐点重播淡入动画。
        """
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
            # 浏览状态下停在最后一个频点时“下一个”仍可用：它是回到自动跟随的入口。
            at_last = buffer.current_index >= len(buffer.frequencies) - 1
            following.setEnabled(not at_last or buffer.user_browsing)
            following.setToolTip(
                "回到自动跟随最新频点" if at_last and buffer.user_browsing else "下一个频点"
            )
        # 页面自己的绘图控件可能不是 RealTimePlotWidget（测试替身、历史页）：
        # 只有真正提供胶片条入口时才同步，缺失时静默跳过。
        setter = getattr(widget, "set_frequencies", None)
        if callable(setter):
            setter(buffer.frequencies, buffer.current_index)
