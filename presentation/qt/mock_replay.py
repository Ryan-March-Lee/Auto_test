"""Mock 逐点回放的纯 Python 时钟与完成载荷。

模拟回放以 ``data`` 事件为计时单位：第一条数据点立即发出，之后每个数据点间隔
0.5 秒；消息与进度不额外占用时间，按场景顺序紧挨着对应数据点发出。``at_ms`` 只用于
场景自身校验，不控制首期播放速度。

本模块不导入 Qt，也不读文件：等待函数与停止事件都由调用方注入，便于用 fake clock
验证时序而不真的等待 181 秒。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from types import MappingProxyType
from typing import Any, Callable, Mapping

from application.dto.measurement_results import MeasurementType

#: 相邻两个数据点之间的固定间隔；场景里的 ``at_ms`` 不参与首期播放速度。
MOCK_DATA_POINT_INTERVAL_S = 0.5

#: 模拟完成提示的统一文案：结果只展示，不写入正式结果目录。
MOCK_SIMULATION_NOTICE = "模拟数据，仅展示，未写入正式结果"

_RESERVED_WINDOWS_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)


class MockReplayClock:
    """按数据点计时的可中断回放时钟。

    第一次调用 :meth:`next_data_point` 立即放行（点击开始后马上看到反馈），之后每次
    等待 ``interval_s``。等待由 ``wait_fn`` 或 ``stop_event.wait`` 完成，二者都可被打断。
    """

    def __init__(
        self,
        *,
        interval_s: float = MOCK_DATA_POINT_INTERVAL_S,
        stop_event: Event | None = None,
        wait_fn: Callable[[float], bool] | None = None,
    ) -> None:
        if isinstance(interval_s, bool) or not isinstance(interval_s, (int, float)):
            raise TypeError("回放间隔必须是数字")
        interval = float(interval_s)
        if not math.isfinite(interval) or interval < 0:
            raise ValueError("回放间隔必须是有限非负数")
        if stop_event is None:
            stop_event = Event()
        if not callable(getattr(stop_event, "is_set", None)):
            raise TypeError("stop_event 必须提供 is_set()")
        if not callable(getattr(stop_event, "wait", None)):
            raise TypeError("stop_event 必须提供 wait()")
        if wait_fn is not None and not callable(wait_fn):
            raise TypeError("wait_fn 必须可调用")
        self._interval_s = interval
        self._stop_event = stop_event
        self._wait_fn = wait_fn
        self._data_points = 0
        self._elapsed_s = 0.0

    @property
    def interval_s(self) -> float:
        return self._interval_s

    @property
    def emitted_data_points(self) -> int:
        return self._data_points

    @property
    def elapsed_s(self) -> float:
        """已完成的等待时长；第一条数据点不等待，所以初始为 0。"""
        return self._elapsed_s

    @property
    def stop_event(self) -> Event:
        return self._stop_event

    def is_stopped(self) -> bool:
        return bool(self._stop_event.is_set())

    def request_stop(self) -> None:
        self._stop_event.set()

    def next_data_point(self) -> bool:
        """放行下一个数据点；返回 ``False`` 表示等待被打断或已请求停止。"""
        if self._stop_event.is_set():
            return False
        if self._data_points:
            if not self._wait(self._interval_s):
                return False
        self._data_points += 1
        return True

    def reset(self) -> None:
        """从头开始计时；再次播放时数据点仍然依次间隔 ``interval_s``。"""
        self._data_points = 0
        self._elapsed_s = 0.0

    def _wait(self, seconds: float) -> bool:
        if self._wait_fn is not None:
            completed = bool(self._wait_fn(seconds))
        else:
            completed = not self._stop_event.wait(seconds)
        if completed and not self._stop_event.is_set():
            self._elapsed_s += seconds
            return True
        return False


@dataclass(frozen=True)
class MockReplayCompletion:
    """一次模拟回放完成后交给界面的只读载荷。

    它不携带 ``ArchivalOutcome``：模拟链路不执行正式归档，也不生成 CSV/报告或虚假
    文件路径。``archival_summary`` 只是场景记录的摘要，用于说明“正式链路本应怎么
    归档”，界面必须同时展示 :data:`MOCK_SIMULATION_NOTICE`。
    """

    measurement_type: MeasurementType
    title: str
    result_filename: str
    result_path: Path
    archival_summary: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.measurement_type, MeasurementType):
            raise TypeError("measurement_type 必须是 MeasurementType")
        title = self.title
        if not isinstance(title, str) or not title.strip():
            raise ValueError("模拟完成载荷的 title 不能为空")
        filename = self.result_filename
        if not isinstance(filename, str) or not filename.strip():
            raise ValueError("模拟完成载荷的 result_filename 不能为空")
        if filename != filename.strip() or "\0" in filename:
            raise ValueError(
                "模拟完成载荷的 result_filename 不能包含首尾空白或空字符"
            )
        if (
            filename in {".", ".."}
            or "/" in filename
            or "\\" in filename
            or ":" in filename
        ):
            raise ValueError(
                f"模拟完成载荷的 result_filename 必须是纯文件名: {filename!r}"
            )
        path = self.result_path
        if not isinstance(path, Path):
            raise TypeError("result_path 必须是 Path")
        if (
            filename.endswith(".")
            or filename.split(".", 1)[0].upper() in _RESERVED_WINDOWS_NAMES
        ):
            raise ValueError(f"模拟完成载荷的 result_filename 非法: {filename!r}")
        if path.name != filename:
            raise ValueError(
                "模拟完成载荷的 result_path 文件名必须与 result_filename 一致"
            )
        summary = self.archival_summary
        if summary is not None:
            if not isinstance(summary, Mapping):
                raise TypeError("archival_summary 必须是映射或 None")
            summary = _freeze(summary)
        object.__setattr__(self, "title", title.strip())
        object.__setattr__(self, "result_filename", filename)
        object.__setattr__(self, "archival_summary", summary)

    @property
    def simulated(self) -> bool:
        """模拟载荷的显式身份：页面据此选择模拟完成分支。"""
        return True

    @property
    def notice(self) -> str:
        return MOCK_SIMULATION_NOTICE


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return value


__all__ = [
    "MOCK_DATA_POINT_INTERVAL_S",
    "MOCK_SIMULATION_NOTICE",
    "MockReplayClock",
    "MockReplayCompletion",
]
