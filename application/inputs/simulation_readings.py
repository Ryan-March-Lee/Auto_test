"""仿真读数包读取入口：一次读取、一次校验，并把文件事实交给运行标记。

读取器只负责“文件字节 → JSON → :class:`SimulationReadings`”；文件位于数据包内的
边界由 :class:`infrastructure.filesystem.paths.MockDataPathService` 在调用前确认。
返回对象同时携带本次读取的 SHA-256 与字节数，供运行标记记录“本次实际使用了哪一份
数据”。运行期端口只接收冻结 DTO，不再按路径读文件，因此仓库里的 ``readings.json``
在运行中被修改不会改变已进入的运行。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from application.dto.measurement_results import MeasurementType
from application.dto.simulation_readings import (
    SimulationReadings,
    SimulationReadingsError,
)


@dataclass(frozen=True)
class LoadedSimulationReadings:
    """一次成功读取的读数包及其文件事实。"""

    readings: SimulationReadings
    path: Path
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(self.readings, SimulationReadings):
            raise SimulationReadingsError(
                "仿真读数必须是 SimulationReadings，"
                f"收到 {type(self.readings).__name__}"
            )
        object.__setattr__(self, "path", Path(self.path))
        digest = self.sha256
        if not isinstance(digest, str) or not digest:
            raise SimulationReadingsError("仿真读数的 SHA-256 不能为空")
        size = self.size_bytes
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise SimulationReadingsError(
                f"仿真读数的字节数必须是非负整数，收到 {size!r}"
            )


def load_simulation_readings(
    path: str | Path,
    *,
    expected_measurement_type: MeasurementType | str | None = None,
    expected_frequencies_ghz: Sequence[Any] | None = None,
) -> LoadedSimulationReadings:
    """读取并严格校验一份 ``readings.json``。

    解析、字段校验、频率网格与测量类型都由
    :meth:`application.dto.simulation_readings.SimulationReadings.from_document`
    完成；本函数只补上“文件字节、SHA-256 与大小”三项事实，不解释 payload。
    """
    candidate = Path(path)
    try:
        raw = candidate.read_bytes()
    except OSError as error:
        raise SimulationReadingsError(
            f"无法读取仿真读数文件: {candidate}"
        ) from error
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise SimulationReadingsError(
            f"仿真读数文件不是有效的 UTF-8 文本: {candidate}"
        ) from error
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise SimulationReadingsError(
            "仿真读数文件不是有效的 JSON: "
            f"{candidate}（第 {error.lineno} 行第 {error.colno} 列）"
        ) from error
    try:
        readings = SimulationReadings.from_document(
            document,
            expected_measurement_type=expected_measurement_type,
            expected_frequencies_ghz=expected_frequencies_ghz,
        )
    except SimulationReadingsError as error:
        raise SimulationReadingsError(
            f"仿真读数文件 {candidate}: {error}"
        ) from error
    return LoadedSimulationReadings(
        readings=readings,
        path=candidate,
        sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
    )


__all__ = ["LoadedSimulationReadings", "load_simulation_readings"]
