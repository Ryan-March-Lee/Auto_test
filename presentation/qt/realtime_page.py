"""测量页面共享的实时缓存和频点导航适配。"""

from collections.abc import Mapping
from typing import Any

from .realtime_buffer import RealtimeMeasurementBuffer


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
        self._update_navigation()

    def update_realtime(self, data: Any) -> None:
        if not isinstance(data, Mapping) or "frequency" not in data:
            return
        if not self.realtime_buffer.store(data):
            return
        self._update_plot(dict(data))
        self._update_navigation()
        self._realtime_data_callback(dict(data))

    def previous_frequency(self) -> None:
        self.realtime_buffer.previous()
        self._display_current_frequency()

    def next_frequency(self) -> None:
        self.realtime_buffer.next()
        self._display_current_frequency()

    def _display_current_frequency(self) -> None:
        data = self.realtime_buffer.current()
        if data is not None:
            self._update_plot(data)
        self._update_navigation()

    def _update_navigation(self) -> None:
        buffer = self.realtime_buffer
        widget = self._realtime_plot_widget
        previous = getattr(widget, "nav_prev_btn", None)
        following = getattr(widget, "nav_next_btn", None)
        if previous is not None:
            previous.setEnabled(buffer.current_index > 0)
        if following is not None:
            following.setEnabled(buffer.current_index < len(buffer.frequencies) - 1)
