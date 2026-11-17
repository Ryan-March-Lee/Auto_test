"""用仓库跟踪的 Mock 数据构造真实模拟会话，供页面与启动 Facade 测试复用。

测试只消费已加载的场景 DTO 与已解析的路径，不启动 worker、不打开仪器，也不复制
大 JSON；模拟路径仍指向仓库里的 ``mock_data``。
"""

from __future__ import annotations

from pathlib import Path

from application.dto.measurement_results import MeasurementType
from application.inputs.mock_scenario import load_mock_scenario
from presentation.qt.simulation_session import (
    SimulationCalibrationInput,
    SimulationSession,
    SimulationStage,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MOCK_ROOT = PROJECT_ROOT / "mock_data"

_DIRECTORIES = {
    MeasurementType.CABLE_LOSS: "cable_loss",
    MeasurementType.DRIVER_POWER_MAPPING: "driver_mapping",
    MeasurementType.AMPLIFIER: "amplifier",
}

_ALL_TYPES = (
    MeasurementType.CABLE_LOSS,
    MeasurementType.DRIVER_POWER_MAPPING,
    MeasurementType.AMPLIFIER,
)


def _stage(
    measurement_type: MeasurementType,
    *,
    stages: dict[MeasurementType, SimulationStage],
) -> SimulationStage:
    directory = _DIRECTORIES[measurement_type]
    scenario = load_mock_scenario(MOCK_ROOT / directory / "scenario.json")
    inputs: list[SimulationCalibrationInput] = []
    for field in ("cable_loss_result", "driver_mapping_result"):
        reference = getattr(scenario.source_config, field, None)
        if reference is None:
            continue
        dependency = (
            MeasurementType.CABLE_LOSS
            if field == "cable_loss_result"
            else MeasurementType.DRIVER_POWER_MAPPING
        )
        if dependency not in stages:
            stages[dependency] = _stage(dependency, stages=stages)
        inputs.append(
            SimulationCalibrationInput(
                measurement_type=dependency, path=MOCK_ROOT.parent / reference
            )
        )
    return SimulationStage(
        measurement_type=measurement_type,
        scenario=scenario,
        scenario_path=MOCK_ROOT / directory / "scenario.json",
        result_path=MOCK_ROOT / directory / scenario.result_filename,
        calibration_inputs=tuple(inputs),
    )


def simulation_session(*measurement_types: MeasurementType) -> SimulationSession:
    """构造包含指定阶段的真实模拟会话；默认包含三类阶段。"""
    requested = tuple(measurement_types) or _ALL_TYPES
    stages: dict[MeasurementType, SimulationStage] = {}
    for measurement_type in requested:
        stages[measurement_type] = _stage(measurement_type, stages=stages)
    return SimulationSession(
        snapshot_name="Mock", stages={kind: stages[kind] for kind in requested}
    )


__all__ = ["MOCK_ROOT", "PROJECT_ROOT", "simulation_session"]
