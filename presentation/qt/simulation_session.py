"""Mock 模拟会话的只读状态模型。

模拟会话只描述“可以回放哪些阶段、每个阶段用哪个场景和哪份正式结果、启动前必须
准备哪些校准结果”。它不读场景文件、不创建运行目录、不连接仪器，也不属于
``prepared_run``：正式运行与模拟会话是两种互斥的会话事实。

场景索引按正式测量类型取用，各阶段的 ``source_config`` 不跨阶段合并。场景与结果
文件的可读性由调用方在构建前通过 Mock 路径服务确认；本模块只接受已加载的场景 DTO
和已解析的路径，便于在纯 Python 测试里验证状态与校准绑定。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from application.dto.measurement_results import MeasurementType
from application.dto.mock_scenario import MockScenario
from application.errors import ConfigurationError
from application.mock_configuration_matcher import MockConfigurationMatch


#: 每个阶段允许声明的校准引用字段 -> 该引用对应的上游正式测量类型。
#:
#: 这是 Mock 数据的固定依赖契约：线损是链路起点，不能声明任何校准引用；驱动
#: 映射只依赖线损；主功放同时依赖线损与驱动映射。缺少、额外或自引用都在进入
#: 会话前失败，避免损坏的数据包生成一条看似可用的回放链路。
_CALIBRATION_REFERENCES: Mapping[
    MeasurementType, tuple[tuple[str, MeasurementType], ...]
] = {
    MeasurementType.CABLE_LOSS: (),
    MeasurementType.DRIVER_POWER_MAPPING: (
        ("cable_loss_result", MeasurementType.CABLE_LOSS),
    ),
    MeasurementType.AMPLIFIER: (
        ("cable_loss_result", MeasurementType.CABLE_LOSS),
        ("driver_mapping_result", MeasurementType.DRIVER_POWER_MAPPING),
    ),
}

#: 场景契约里全部校准引用字段；用于发现不属于当前阶段的额外声明。
_ALL_CALIBRATION_FIELDS = (
    "cable_loss_result",
    "driver_mapping_result",
)


@dataclass(frozen=True)
class SimulationCalibrationInput:
    """模拟阶段启动前自动绑定的固定校准结果。"""

    measurement_type: MeasurementType
    path: Path

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "measurement_type",
            _coerce_measurement_type(self.measurement_type),
        )
        object.__setattr__(self, "path", _require_path(self.path, "模拟校准结果路径"))


@dataclass(frozen=True)
class SimulationStage:
    """一个测量阶段在模拟会话里的完整只读事实。"""

    measurement_type: MeasurementType
    scenario: MockScenario
    scenario_path: Path
    result_path: Path
    calibration_inputs: tuple[SimulationCalibrationInput, ...] = ()

    def __post_init__(self) -> None:
        measurement_type = _coerce_measurement_type(self.measurement_type)
        if not isinstance(self.scenario, MockScenario):
            raise ConfigurationError(
                "模拟阶段必须持有 MockScenario，"
                f"收到 {type(self.scenario).__name__}"
            )
        if self.scenario.measurement_type is not measurement_type:
            raise ConfigurationError(
                "模拟阶段测量类型与场景不一致: "
                f"{measurement_type.value} != "
                f"{self.scenario.measurement_type.value}"
            )
        inputs = tuple(self.calibration_inputs)
        for item in inputs:
            if not isinstance(item, SimulationCalibrationInput):
                raise ConfigurationError(
                    "模拟阶段的校准输入必须是 SimulationCalibrationInput，"
                    f"收到 {type(item).__name__}"
                )
        object.__setattr__(self, "measurement_type", measurement_type)
        object.__setattr__(self, "scenario_path", _require_path(self.scenario_path, "Mock 场景路径"))
        object.__setattr__(self, "result_path", _require_path(self.result_path, "Mock 正式结果路径"))
        object.__setattr__(self, "calibration_inputs", inputs)

    @property
    def title(self) -> str:
        """场景展示标题；来自场景 DTO，不在这里另写字面量。"""
        return self.scenario.title


@dataclass(frozen=True)
class SimulationSession:
    """一次 Mock 模拟会话：Mock 配置身份 + 按测量类型组织的阶段索引。

    ``snapshot_name`` 是加载时的快照显示名称（去首尾空白），只用于展示与日志；
    是否为模拟模式由协调器的独立状态判定，不依赖与字面量比较。
    """

    snapshot_name: str
    stages: Mapping[MeasurementType, SimulationStage]

    def __post_init__(self) -> None:
        snapshot_name = self.snapshot_name
        if not isinstance(snapshot_name, str):
            raise ConfigurationError(
                "模拟会话的快照名称必须是字符串，"
                f"收到 {type(snapshot_name).__name__}"
            )
        cleaned = snapshot_name.strip()
        if not cleaned:
            raise ConfigurationError("模拟会话的快照名称不能为空")
        if not isinstance(self.stages, Mapping):
            raise ConfigurationError(
                "模拟会话的阶段索引必须是映射，"
                f"收到 {type(self.stages).__name__}"
            )
        normalized: dict[MeasurementType, SimulationStage] = {}
        for key, stage in self.stages.items():
            measurement_type = _coerce_measurement_type(key)
            if not isinstance(stage, SimulationStage):
                raise ConfigurationError(
                    "模拟会话的阶段索引值必须是 SimulationStage，"
                    f"收到 {type(stage).__name__}"
                )
            if stage.measurement_type is not measurement_type:
                raise ConfigurationError(
                    "模拟会话阶段索引的键与阶段测量类型不一致: "
                    f"{measurement_type.value} != {stage.measurement_type.value}"
                )
            normalized[measurement_type] = stage
        if not normalized:
            raise ConfigurationError("模拟会话至少需要一个测量阶段")
        object.__setattr__(self, "snapshot_name", cleaned)
        object.__setattr__(self, "stages", MappingProxyType(normalized))

    @property
    def measurement_types(self) -> tuple[MeasurementType, ...]:
        """本会话可以回放的测量类型，顺序与 Mock 数据包一致。"""
        return tuple(self.stages)

    def has_stage(self, measurement_type: MeasurementType | str) -> bool:
        """会话是否包含该测量阶段；未知类型不抛异常，只返回 ``False``。"""
        try:
            return _coerce_measurement_type(measurement_type) in self.stages
        except ConfigurationError:
            return False

    def stage_for(self, measurement_type: MeasurementType | str) -> SimulationStage:
        """按正式测量类型取阶段；缺失时给出可用阶段。"""
        expected = _coerce_measurement_type(measurement_type)
        stage = self.stages.get(expected)
        if stage is None:
            allowed = "、".join(kind.value for kind in self.measurement_types)
            raise ConfigurationError(
                f"当前 Mock 模拟会话不包含 {expected.value} 阶段；可用阶段: {allowed}"
            )
        return stage

    def calibration_inputs_for(
        self, measurement_type: MeasurementType | str
    ) -> tuple[SimulationCalibrationInput, ...]:
        """该阶段启动前自动绑定的固定校准结果（按场景声明的顺序）。"""
        return self.stage_for(measurement_type).calibration_inputs

    def calibration_input_for(
        self,
        measurement_type: MeasurementType | str,
        dependency: MeasurementType | str,
    ) -> SimulationCalibrationInput | None:
        """按“阶段 + 上游测量类型”取一份固定校准输入；不需要该上游时返回 ``None``。

        页面只按正式测量类型查找，不解析场景 JSON，也不自行拼接 Mock 路径。
        """
        expected = _coerce_measurement_type(dependency)
        for item in self.calibration_inputs_for(measurement_type):
            if item.measurement_type is expected:
                return item
        return None


def build_simulation_session(
    match: MockConfigurationMatch, *, mock_paths: Any
) -> SimulationSession:
    """把一次成功的配置匹配收敛为模拟会话。

    ``mock_paths`` 是 Mock 路径服务（由 composition 注入）：它按测量类型解析场景与
    正式结果路径，并解析场景里的校准结果引用。任何文件缺失、越界或引用与同阶段
    结果不一致都在进入模拟会话前失败，不留下半成品会话。
    """
    if not isinstance(match, MockConfigurationMatch):
        raise ConfigurationError(
            "构建模拟会话需要 MockConfigurationMatch，"
            f"收到 {type(match).__name__}"
        )
    if mock_paths is None:
        raise ConfigurationError("未装配 Mock 数据路径服务，不能进入模拟会话")
    results: dict[MeasurementType, Path] = {}
    for scenario in match.scenarios:
        measurement_type = scenario.measurement_type
        results[measurement_type] = mock_paths.require_result_path_for(
            measurement_type.value, scenario.result_filename
        )
    stages: dict[MeasurementType, SimulationStage] = {}
    for scenario in match.scenarios:
        measurement_type = scenario.measurement_type
        inputs = _resolve_calibration_inputs(
            scenario, results=results, mock_paths=mock_paths
        )
        stages[measurement_type] = SimulationStage(
            measurement_type=measurement_type,
            scenario=scenario,
            scenario_path=mock_paths.require_scenario_path_for(measurement_type.value),
            result_path=results[measurement_type],
            calibration_inputs=inputs,
        )
    return SimulationSession(snapshot_name=match.snapshot_name, stages=stages)


def _resolve_calibration_inputs(
    scenario: MockScenario,
    *,
    results: Mapping[MeasurementType, Path],
    mock_paths: Any,
) -> tuple[SimulationCalibrationInput, ...]:
    """按阶段固定依赖解析校准引用，并确认它指向数据包内的同一份结果。

    阶段允许的引用与上游测量类型由 :data:`_CALIBRATION_REFERENCES` 固定；场景
    声明了不属于该阶段的引用、缺少必需引用、引用了数据包不包含的上游阶段，或
    引用与同阶段结果文件不一致，都在进入会话前失败。
    """
    measurement_type = scenario.measurement_type
    allowed = _CALIBRATION_REFERENCES.get(measurement_type)
    if allowed is None:
        available = "、".join(kind.value for kind in _CALIBRATION_REFERENCES)
        raise ConfigurationError(
            f"未知的 Mock 模拟测量类型: {measurement_type.value}；"
            f"可用类型: {available}"
        )
    allowed_fields = {field for field, _dependency in allowed}
    unexpected = [
        field
        for field in _ALL_CALIBRATION_FIELDS
        if field not in allowed_fields
        and getattr(scenario.source_config, field) is not None
    ]
    if unexpected:
        raise ConfigurationError(
            f"Mock {measurement_type.value} 场景不能声明校准结果引用: "
            f"{'、'.join(unexpected)}；该阶段只允许 "
            f"{'、'.join(sorted(allowed_fields)) or '无校准引用'}"
        )
    inputs: list[SimulationCalibrationInput] = []
    for field, dependency in allowed:
        reference = getattr(scenario.source_config, field)
        if reference is None:
            raise ConfigurationError(
                f"Mock {measurement_type.value} 场景缺少 {field}；"
                f"模拟模式无法自动绑定 {dependency.value} 上游结果"
            )
        declared = results.get(dependency)
        if declared is None:
            raise ConfigurationError(
                f"Mock {measurement_type.value} 场景引用了 {dependency.value} "
                "结果，但当前 Mock 数据包不包含该阶段；"
                "不能自动绑定校准结果"
            )
        path = mock_paths.require_reference_path(reference)
        if declared != path:
            raise ConfigurationError(
                f"Mock {measurement_type.value} 场景的 {field} 指向 "
                f"{path.name}，但同阶段结果文件是 {declared.name}；"
                "Mock 数据包不一致，不能自动绑定校准结果"
            )
        inputs.append(
            SimulationCalibrationInput(measurement_type=dependency, path=path)
        )
    return tuple(inputs)


def _coerce_measurement_type(value: Any) -> MeasurementType:
    if isinstance(value, MeasurementType):
        return value
    try:
        return MeasurementType(value)
    except (TypeError, ValueError) as error:
        allowed = "、".join(kind.value for kind in MeasurementType)
        raise ConfigurationError(
            f"未知测量类型: {value!r}；可用类型: {allowed}"
        ) from error


def _require_path(value: Any, description: str) -> Path:
    if not isinstance(value, (str, Path)):
        raise ConfigurationError(
            f"{description}必须是路径，收到 {type(value).__name__}"
        )
    if isinstance(value, str) and not value.strip():
        raise ConfigurationError(f"{description}不能为空")
    path = Path(value)
    if not str(path).strip():
        raise ConfigurationError(f"{description}不能为空")
    return path


__all__ = [
    "SimulationCalibrationInput",
    "SimulationSession",
    "SimulationStage",
    "build_simulation_session",
]
