"""Mock 场景文件读取入口：一次读取、一次校验，失败发生在回放之前。

读取器只负责“文件 → JSON → :class:`MockScenario`”的边界转换；路径越界与文件缺失
中的目录边界仍由 :class:`infrastructure.filesystem.paths.MockDataPathService` 在
调用前确认。任何读取、解码或结构错误都翻译成
:class:`application.dto.mock_scenario.MockScenarioError`，回放线程不需要自己判断
JSON 细节。
"""

from __future__ import annotations

import json
from pathlib import Path

from application.dto.measurement_results import MeasurementType
from application.dto.mock_scenario import (
    MockScenario,
    MockScenarioError,
    mock_scenario_from_document,
)


def load_mock_scenario(
    path: str | Path,
    *,
    expected_measurement_type: MeasurementType | str | None = None,
) -> MockScenario:
    """读取并校验一个 ``scenario.json``。

    ``expected_measurement_type`` 省略时只校验场景自身；给出时场景的
    ``measurement_type`` 必须与之一致，否则拒绝交给回放器。
    """
    candidate = Path(path)
    try:
        text = candidate.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise MockScenarioError(
            f"无法读取 Mock 场景文件: {candidate}"
        ) from error
    except UnicodeDecodeError as error:
        raise MockScenarioError(
            f"Mock 场景文件不是有效的 UTF-8 文本: {candidate}"
        ) from error
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise MockScenarioError(
            "Mock 场景文件不是有效的 JSON: "
            f"{candidate}（第 {error.lineno} 行第 {error.colno} 列）"
        ) from error
    try:
        return mock_scenario_from_document(
            document, expected_measurement_type=expected_measurement_type
        )
    except MockScenarioError as error:
        raise MockScenarioError(
            f"Mock 场景文件 {candidate}: {error}"
        ) from error


__all__ = ["load_mock_scenario"]
