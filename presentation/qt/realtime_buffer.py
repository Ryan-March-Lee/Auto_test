"""实时测量数据的纯 Python 缓存和频点浏览状态。"""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from typing import Any


def normalize_frequency(value: Any) -> float | None:
    """将频率转换为稳定的有限浮点标识。"""
    try:
        frequency = float(value)
    except (TypeError, ValueError):
        return None
    return frequency if isfinite(frequency) else None


class RealtimeMeasurementBuffer:
    """按频率保存实时数据，并维护用户浏览与自动跟随语义。"""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}
        self._frequencies: list[float] = []
        self.current_index = 0
        self.user_browsing = False

    @property
    def data(self) -> dict[str, dict[str, Any]]:
        return {key: value.copy() for key, value in self._data.items()}

    @property
    def frequencies(self) -> list[float]:
        return list(self._frequencies)

    def store(self, value: Mapping[str, Any]) -> bool:
        if "frequency" not in value:
            return False
        frequency = normalize_frequency(value["frequency"])
        if frequency is None:
            return False
        current_frequency = (
            self._frequencies[self.current_index]
            if self._frequencies and 0 <= self.current_index < len(self._frequencies)
            else None
        )
        key = str(frequency)
        normalized = dict(value)
        normalized["frequency"] = frequency
        self._data[key] = normalized
        if frequency not in self._frequencies:
            self._frequencies.append(frequency)
            self._frequencies.sort()
        if not self.user_browsing and self._frequencies:
            self.current_index = len(self._frequencies) - 1
        elif current_frequency is not None:
            self.current_index = self._frequencies.index(current_frequency)
        return True

    def current(self) -> dict[str, Any] | None:
        if not self._frequencies or not 0 <= self.current_index < len(self._frequencies):
            return None
        frequency = str(self._frequencies[self.current_index])
        value = self._data.get(frequency)
        return value.copy() if value is not None else None

    def previous(self) -> dict[str, Any] | None:
        if self.current_index <= 0:
            return self.current()
        self.current_index -= 1
        self.user_browsing = True
        return self.current()

    def next(self) -> dict[str, Any] | None:
        if self.current_index >= len(self._frequencies) - 1:
            return self.current()
        self.current_index += 1
        self.user_browsing = self.current_index != len(self._frequencies) - 1
        return self.current()

    def clear(self) -> None:
        self._data.clear()
        self._frequencies.clear()
        self.current_index = 0
        self.user_browsing = False


__all__ = ["RealtimeMeasurementBuffer", "normalize_frequency"]
