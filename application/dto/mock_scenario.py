"""Mock 场景的纯 Python 契约：阶段、事件、完成语义与配置摘要。

本模块定义 ``mock_data/<stage>/scenario.json`` 的唯一读取形状。回放器、配置匹配器和
界面适配都消费这里的数据对象，不直接读取 JSON 字典；读取与解析入口见
:mod:`application.inputs.mock_scenario`。契约只描述“回放需要看到什么”，不启动线程、
不读取正式结果文件、不判断 Mock 路径边界（由
:mod:`infrastructure.filesystem.paths` 负责）。

固定约束：

- ``kind`` 只能是 ``measurement_mock_scenario``，``schema_version`` 只接受 ``1.0``；
- 阶段内事件按 ``at_ms`` 非递减，跨阶段的进度值不能倒退；
- 阶段事件只支持 ``message`` / ``progress`` / ``data``，未知类型或多余字段直接拒绝；
- 完成事件按 ``result → archival → message → finished`` 排列；``result`` 必须带
  纯文件名，``finished`` 必须最后且只有一个；
- ``source_config`` 保留原始摘要，并把配置匹配器要比较的事实（频点、衰减器、压缩点、
  驱放开关/增益、扫描计划等）解析成显式字段。

``data`` 事件的 ``payload`` 被深冻结（嵌套列表变成元组），便于跨线程传递；需要交给
Qt 信号或会修改载荷的页面时，用
:func:`application.dto.measurement_results.thaw` 还原成普通 ``dict`` / ``list``。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, ClassVar

from application.dto.measurement_results import MeasurementType, freeze
from application.errors import ConfigurationError
from domain.run.models import validate_run_id


MOCK_SCENARIO_KIND = "measurement_mock_scenario"
MOCK_SCENARIO_SCHEMA_VERSION = "1.0"
MOCK_SCENARIO_SCHEMA_VERSIONS = (MOCK_SCENARIO_SCHEMA_VERSION,)

# 线损“请连接路径2”确认点沿用现有页面信号名；接受后继续当前阶段，拒绝按普通停止处理。
MOCK_CONFIRM_SIGNAL = "step_pause"
MOCK_CONFIRM_ACCEPTED = "continue_stage2"
MOCK_CONFIRM_REJECTED = "stop"

# source_config 的键名固定：必需字段参与配置匹配，可选字段保留阶段差异。
# 未声明键必须失败，避免 compression_typ 这类拼写错误让匹配器静默跳过关键事实。
MOCK_SOURCE_CONFIG_REQUIRED_FIELDS = (
    "template",
    "test_frequencies_ghz",
    "attenuator_db",
    "driver_enabled",
    "driver_estimated_gain_db",
    "driver_max_input_power_dbm",
    "dut_max_input_power_dbm",
    "power_scan_plans",
)
MOCK_SOURCE_CONFIG_OPTIONAL_FIELDS = (
    "copied_from",
    "compression_type",
    "compression_point",
    "target_input_powers_dbm",
    "signal_source_power_range_dbm",
    "cable_loss_result",
    "driver_mapping_result",
    "drain_channel",
    "dut_channel_settings",
    "dut_power_sequence",
)

# Windows 保留设备名：契约层也拒绝，避免结果文件在 Windows 上被解析成设备。
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


class MockScenarioError(ConfigurationError):
    """Mock 场景结构、顺序或关键配置摘要不满足契约。

    与配置类错误一致：同时是 ``ValueError``，调用方在播放前失败，不需要靠字符串
    匹配判断原因。
    """


class MockEventType(str, Enum):
    """阶段内回放事件的语义；对应 README 中数据文件的 ``type``。"""

    MESSAGE = "message"
    PROGRESS = "progress"
    DATA = "data"


class MockCompletionType(str, Enum):
    """阶段结束后的完成事件语义。"""

    RESULT = "result"
    ARCHIVAL = "archival"
    MESSAGE = "message"
    FINISHED = "finished"


class MockPhaseStartType(str, Enum):
    """阶段如何开始：自动开始，或在用户确认接线后开始。"""

    AUTO = "auto"
    USER_CONFIRM = "user_confirm"


def _type_name(value: Any) -> str:
    return type(value).__name__


def _require_mapping(value: Any, description: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MockScenarioError(
            f"{description} 必须是 JSON 对象，收到 {_type_name(value)}"
        )
    return value


def _require_object(
    value: Any,
    *,
    description: str,
    required: Sequence[str],
    optional: Sequence[str] = (),
) -> Mapping[str, Any]:
    """校验 JSON 对象：必需字段齐全，且不接受未声明字段。"""
    document = _require_mapping(value, description)
    missing = [field for field in required if field not in document]
    if missing:
        raise MockScenarioError(f"{description} 缺少必需字段: {', '.join(missing)}")
    allowed = set(required) | set(optional)
    unknown = sorted(key for key in document if key not in allowed)
    if unknown:
        raise MockScenarioError(
            f"{description} 包含未知字段: {', '.join(unknown)}；"
            f"允许字段: {', '.join(sorted(allowed))}"
        )
    return document


def _require_sequence(value: Any, description: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(
        value, (list, tuple)
    ):
        raise MockScenarioError(
            f"{description} 必须是数组，收到 {_type_name(value)}"
        )
    return value


def _require_text(value: Any, description: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise MockScenarioError(
            f"{description} 必须是字符串，收到 {_type_name(value)}"
        )
    if not allow_empty and not value.strip():
        raise MockScenarioError(f"{description} 不能为空")
    return value


def _require_identifier(value: Any, description: str) -> str:
    """标识类文本：非空、不允许首尾空白。"""
    text = _require_text(value, description)
    if text != text.strip():
        raise MockScenarioError(f"{description} 不能包含首尾空白: {value!r}")
    return text


def _require_plain_file_name(value: Any, description: str) -> str:
    """要求纯文件名：拒绝路径分隔、盘符、相对目录标记和 Windows 设备名。

    这一层只做字符串安全校验；符号链接、目录目标和文件存在性仍由
    :class:`infrastructure.filesystem.paths.MockDataPathService` 在读取前确认。
    """
    text = _require_identifier(value, description)
    if text in (".", ".."):
        raise MockScenarioError(
            f"{description} 不能是相对目录标记: {text!r}"
        )
    for separator in ("/", "\\", ":", "\0"):
        if separator in text:
            raise MockScenarioError(
                f"{description} 必须是纯文件名，不能包含路径分隔符或盘符: {text!r}"
            )
    if text.endswith("."):
        raise MockScenarioError(f"{description} 不能以点结尾: {text!r}")
    if text.split(".", 1)[0].upper() in _RESERVED_WINDOWS_NAMES:
        raise MockScenarioError(
            f"{description} 不能是 Windows 保留设备名: {text!r}"
        )
    return text


def _require_mock_data_reference(
    value: Any, *, stage: str, description: str
) -> str:
    """要求校准结果引用是 ``mock_data/<stage>/<文件名>`` 相对路径。

    只校验字符串形状和阶段前缀，不读取文件；步骤 12 通过 Mock 路径服务解析并
    确认目标仍是阶段目录内的普通文件。
    """
    text = _require_identifier(value, description)
    expected = f"mock_data/{stage}/<文件名>"
    if text.startswith("/") or "\\" in text or ":" in text or "\0" in text:
        raise MockScenarioError(
            f"{description} 必须是 {expected} 形式的相对路径，收到 {text!r}"
        )
    parts = text.split("/")
    if len(parts) != 3 or parts[0] != "mock_data" or parts[1] != stage:
        raise MockScenarioError(
            f"{description} 必须是 {expected} 形式的相对路径，收到 {text!r}"
        )
    _require_plain_file_name(
        parts[2], f"{description}（{expected}）的文件名"
    )
    return text


def _require_bool(value: Any, description: str) -> bool:
    if not isinstance(value, bool):
        raise MockScenarioError(
            f"{description} 必须是布尔值，收到 {_type_name(value)}"
        )
    return value


def _require_number(
    value: Any,
    description: str,
    *,
    minimum: float | None = None,
    exclusive_minimum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MockScenarioError(
            f"{description} 必须是数字，收到 {_type_name(value)}"
        )
    try:
        number = float(value)
    except (OverflowError, ValueError) as error:
        raise MockScenarioError(f"{description} 必须是有限数字") from error
    if not math.isfinite(number):
        raise MockScenarioError(f"{description} 必须是有限数字")
    if minimum is not None and number < minimum:
        raise MockScenarioError(f"{description} 不能小于 {minimum}，收到 {number}")
    if exclusive_minimum is not None and number <= exclusive_minimum:
        raise MockScenarioError(
            f"{description} 必须大于 {exclusive_minimum}，收到 {number}"
        )
    return number


def _require_int(value: Any, description: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MockScenarioError(
            f"{description} 必须是整数，收到 {_type_name(value)}"
        )
    if minimum is not None and value < minimum:
        raise MockScenarioError(f"{description} 不能小于 {minimum}，收到 {value}")
    return value


def _require_number_tuple(
    value: Any,
    description: str,
    *,
    non_empty: bool,
    positive: bool = False,
    strictly_increasing: bool = False,
) -> tuple[float, ...]:
    items = _require_sequence(value, description)
    if non_empty and not items:
        raise MockScenarioError(f"{description} 不能为空数组")
    numbers = tuple(
        _require_number(
            item,
            f"{description}[{index}]",
            exclusive_minimum=0.0 if positive else None,
        )
        for index, item in enumerate(items)
    )
    if strictly_increasing:
        for index in range(1, len(numbers)):
            if numbers[index] <= numbers[index - 1]:
                raise MockScenarioError(
                    f"{description} 必须严格递增: "
                    f"{numbers[index - 1]} -> {numbers[index]}"
                )
    return numbers


def _coerce_measurement_type(
    value: Any, description: str = "measurement_type"
) -> MeasurementType:
    if isinstance(value, MeasurementType):
        return value
    text = _require_identifier(value, description)
    try:
        return MeasurementType(text)
    except ValueError as error:
        allowed = "、".join(kind.value for kind in MeasurementType)
        raise MockScenarioError(
            f"{description} 非法: {value!r}；可用测量类型: {allowed}"
        ) from error


@dataclass(frozen=True)
class MockPowerScanPlan:
    """``source_config.power_scan_plans`` 的一条功率扫描计划（dBm / dB）。"""

    start_power_dbm: float
    stop_power_dbm: float
    step_db: float

    def __post_init__(self) -> None:
        start = _require_number(self.start_power_dbm, "扫描计划 start_power")
        stop = _require_number(self.stop_power_dbm, "扫描计划 stop_power")
        step = _require_number(
            self.step_db, "扫描计划 step", exclusive_minimum=0.0
        )
        if stop < start:
            raise MockScenarioError(
                f"扫描计划的 stop_power 不能低于 start_power: {stop} < {start}"
            )
        object.__setattr__(self, "start_power_dbm", start)
        object.__setattr__(self, "stop_power_dbm", stop)
        object.__setattr__(self, "step_db", step)


@dataclass(frozen=True)
class MockSourceConfig:
    """Mock 数据对应的配置事实摘要。

    显式字段只覆盖回放识别需要比较的事实；``document`` 保留完整摘要（冻结），
    供审计和后续步骤取用校准结果文件名、电源通道等未纳入匹配的字段。
    """

    template: str
    test_frequencies_ghz: tuple[float, ...]
    attenuator_db: float
    driver_enabled: bool
    driver_estimated_gain_db: float
    driver_max_input_power_dbm: float
    dut_max_input_power_dbm: float
    power_scan_plans: tuple[MockPowerScanPlan, ...]
    document: Mapping[str, Any]
    compression_point: str | None = None
    target_input_powers_dbm: tuple[float, ...] = ()
    signal_source_power_range_dbm: tuple[float, float] | None = None
    cable_loss_result: str | None = None
    driver_mapping_result: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "template", _require_identifier(self.template, "source_config.template")
        )
        object.__setattr__(
            self,
            "test_frequencies_ghz",
            _require_number_tuple(
                self.test_frequencies_ghz,
                "source_config.test_frequencies_ghz",
                non_empty=True,
                positive=True,
                strictly_increasing=True,
            ),
        )
        object.__setattr__(
            self,
            "attenuator_db",
            _require_number(
                self.attenuator_db,
                "source_config.attenuator_db",
                minimum=0.0,
            ),
        )
        object.__setattr__(
            self,
            "driver_enabled",
            _require_bool(self.driver_enabled, "source_config.driver_enabled"),
        )
        object.__setattr__(
            self,
            "driver_estimated_gain_db",
            _require_number(
                self.driver_estimated_gain_db,
                "source_config.driver_estimated_gain_db",
            ),
        )
        object.__setattr__(
            self,
            "driver_max_input_power_dbm",
            _require_number(
                self.driver_max_input_power_dbm,
                "source_config.driver_max_input_power_dbm",
            ),
        )
        object.__setattr__(
            self,
            "dut_max_input_power_dbm",
            _require_number(
                self.dut_max_input_power_dbm,
                "source_config.dut_max_input_power_dbm",
            ),
        )
        plans = tuple(
            _require_sequence(
                self.power_scan_plans, "source_config.power_scan_plans"
            )
        )
        if not plans:
            raise MockScenarioError("source_config.power_scan_plans 不能为空数组")
        for index, plan in enumerate(plans):
            if not isinstance(plan, MockPowerScanPlan):
                raise MockScenarioError(
                    "source_config.power_scan_plans"
                    f"[{index}] 必须是 MockPowerScanPlan，"
                    f"收到 {_type_name(plan)}"
                )
        object.__setattr__(self, "power_scan_plans", plans)
        if self.compression_point is not None:
            object.__setattr__(
                self,
                "compression_point",
                _require_identifier(
                    self.compression_point, "source_config.compression_point"
                ),
            )
        object.__setattr__(
            self,
            "target_input_powers_dbm",
            _require_number_tuple(
                self.target_input_powers_dbm,
                "source_config.target_input_powers_dbm",
                non_empty=False,
                strictly_increasing=True,
            ),
        )
        if self.signal_source_power_range_dbm is not None:
            power_range = _require_number_tuple(
                self.signal_source_power_range_dbm,
                "source_config.signal_source_power_range_dbm",
                non_empty=True,
            )
            if len(power_range) != 2:
                raise MockScenarioError(
                    "source_config.signal_source_power_range_dbm 必须包含"
                    f" 2 个值，收到 {len(power_range)}"
                )
            if power_range[1] <= power_range[0]:
                raise MockScenarioError(
                    "source_config.signal_source_power_range_dbm"
                    " 的最大值必须大于最小值"
                )
            object.__setattr__(self, "signal_source_power_range_dbm", power_range)
        if self.cable_loss_result is not None:
            object.__setattr__(
                self,
                "cable_loss_result",
                _require_mock_data_reference(
                    self.cable_loss_result,
                    stage="cable_loss",
                    description="source_config.cable_loss_result",
                ),
            )
        if self.driver_mapping_result is not None:
            object.__setattr__(
                self,
                "driver_mapping_result",
                _require_mock_data_reference(
                    self.driver_mapping_result,
                    stage="driver_mapping",
                    description="source_config.driver_mapping_result",
                ),
            )
        object.__setattr__(
            self,
            "document",
            freeze(_require_mapping(self.document, "source_config")),
        )


@dataclass(frozen=True)
class MockRunInfo:
    """模拟运行标识；只用于展示与日志，不创建正式运行目录。"""

    run_id: str
    mode: str
    started_at: str

    def __post_init__(self) -> None:
        try:
            run_id = validate_run_id(self.run_id)
        except (TypeError, ValueError) as error:
            raise MockScenarioError(f"run.run_id 非法: {error}") from error
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "mode", _require_identifier(self.mode, "run.mode"))
        started_at = _require_identifier(self.started_at, "run.started_at")
        try:
            moment = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise MockScenarioError(
                f"run.started_at 必须是 ISO 8601 时间: {started_at!r}"
            ) from error
        if moment.tzinfo is None:
            raise MockScenarioError(
                "run.started_at 必须包含时区信息（ISO 8601）: "
                f"{started_at!r}"
            )
        object.__setattr__(self, "started_at", started_at)


@dataclass(frozen=True)
class MockScenarioEvent:
    """阶段内单个回放事件；``at_ms`` 只用于校验顺序，不控制首期播放速度。"""

    at_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "at_ms", _require_int(self.at_ms, "事件 at_ms", minimum=0)
        )

    @property
    def type(self) -> MockEventType:  # pragma: no cover - 子类覆盖
        raise NotImplementedError


@dataclass(frozen=True)
class MockMessageEvent(MockScenarioEvent):
    """运行日志/状态消息。"""

    text: str
    type: ClassVar[MockEventType] = MockEventType.MESSAGE

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "text", _require_text(self.text, "message 事件 text"))


@dataclass(frozen=True)
class MockProgressEvent(MockScenarioEvent):
    """测量进度；``percent`` 为 0–100 的整数。"""

    percent: int
    stage: str
    type: ClassVar[MockEventType] = MockEventType.PROGRESS

    def __post_init__(self) -> None:
        super().__post_init__()
        percent = _require_int(self.percent, "progress 事件 percent", minimum=0)
        if percent > 100:
            raise MockScenarioError(
                f"progress 事件 percent 必须在 0–100 之间，收到 {percent}"
            )
        object.__setattr__(self, "percent", percent)
        object.__setattr__(
            self, "stage", _require_identifier(self.stage, "progress 事件 stage")
        )


@dataclass(frozen=True)
class MockDataEvent(MockScenarioEvent):
    """单个实时数据点；结构与对应阶段服务发出的 ``RealtimeDataEvent`` 一致。"""

    payload: Mapping[str, Any]
    type: ClassVar[MockEventType] = MockEventType.DATA

    def __post_init__(self) -> None:
        super().__post_init__()
        payload = _require_mapping(self.payload, "data 事件 payload")
        if not payload:
            raise MockScenarioError("data 事件 payload 不能为空对象")
        _require_number(
            payload.get("frequency"),
            "data 事件 payload.frequency",
            exclusive_minimum=0.0,
        )
        object.__setattr__(self, "payload", freeze(payload))


@dataclass(frozen=True)
class MockPhaseStart:
    """阶段起点语义的基类。"""

    @property
    def type(self) -> MockPhaseStartType:  # pragma: no cover - 子类覆盖
        raise NotImplementedError


@dataclass(frozen=True)
class MockAutoPhaseStart(MockPhaseStart):
    """阶段自动开始。"""

    type: ClassVar[MockPhaseStartType] = MockPhaseStartType.AUTO


@dataclass(frozen=True)
class MockUserConfirmPhaseStart(MockPhaseStart):
    """阶段在用户确认接线后开始；线损路径 2 使用该语义。"""

    signal: str
    checkpoint: str
    prompt: str
    accepted: str
    rejected: str
    type: ClassVar[MockPhaseStartType] = MockPhaseStartType.USER_CONFIRM

    def __post_init__(self) -> None:
        signal = _require_identifier(self.signal, "user_confirm.signal")
        if signal != MOCK_CONFIRM_SIGNAL:
            raise MockScenarioError(
                f"user_confirm.signal 必须是 {MOCK_CONFIRM_SIGNAL!r}，收到 {signal!r}"
            )
        object.__setattr__(self, "signal", signal)
        object.__setattr__(
            self,
            "checkpoint",
            _require_identifier(self.checkpoint, "user_confirm.checkpoint"),
        )
        object.__setattr__(
            self, "prompt", _require_text(self.prompt, "user_confirm.prompt")
        )
        accepted = _require_identifier(self.accepted, "user_confirm.accepted")
        if accepted != MOCK_CONFIRM_ACCEPTED:
            raise MockScenarioError(
                "user_confirm.accepted 必须是 "
                f"{MOCK_CONFIRM_ACCEPTED!r}，收到 {accepted!r}"
            )
        object.__setattr__(self, "accepted", accepted)
        rejected = _require_identifier(self.rejected, "user_confirm.rejected")
        if rejected != MOCK_CONFIRM_REJECTED:
            raise MockScenarioError(
                "user_confirm.rejected 必须是 "
                f"{MOCK_CONFIRM_REJECTED!r}，收到 {rejected!r}"
            )
        object.__setattr__(self, "rejected", rejected)


@dataclass(frozen=True)
class MockPhase:
    """按顺序执行的回放阶段。"""

    name: str
    title: str
    start: MockPhaseStart
    events: tuple[MockScenarioEvent, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "name", _require_identifier(self.name, "阶段 name")
        )
        object.__setattr__(
            self, "title", _require_text(self.title, "阶段 title")
        )
        if not isinstance(
            self.start, (MockAutoPhaseStart, MockUserConfirmPhaseStart)
        ):
            raise MockScenarioError(
                f"阶段 {self.name} 的 start 必须是 MockAutoPhaseStart 或 "
                f"MockUserConfirmPhaseStart，收到 {_type_name(self.start)}"
            )
        events = tuple(
            _require_sequence(self.events, f"阶段 {self.name} 的 events")
        )
        if not events:
            raise MockScenarioError(f"阶段 {self.name} 的 events 不能为空数组")
        previous_at_ms = -1
        for index, event in enumerate(events):
            if not isinstance(
                event, (MockMessageEvent, MockProgressEvent, MockDataEvent)
            ):
                raise MockScenarioError(
                    f"阶段 {self.name} 的 events[{index}] 必须是"
                    " MockMessageEvent、MockProgressEvent 或 MockDataEvent，"
                    f"收到 {_type_name(event)}"
                )
            if event.at_ms < previous_at_ms:
                raise MockScenarioError(
                    f"阶段 {self.name} 的事件 at_ms 必须非递减: "
                    f"{previous_at_ms} -> {event.at_ms}"
                )
            previous_at_ms = event.at_ms
        if not any(isinstance(event, MockDataEvent) for event in events):
            raise MockScenarioError(
                f"阶段 {self.name} 至少需要一个 data 事件作为回放计时单位"
            )
        object.__setattr__(self, "events", events)

    @property
    def data_event_count(self) -> int:
        return sum(1 for event in self.events if isinstance(event, MockDataEvent))


@dataclass(frozen=True)
class MockCompletionEvent:
    """完成事件的基类。"""

    @property
    def type(self) -> MockCompletionType:  # pragma: no cover - 子类覆盖
        raise NotImplementedError


@dataclass(frozen=True)
class MockResultCompletion(MockCompletionEvent):
    """加载同阶段目录下的正式结果并交给页面展示；不写入正式结果目录。"""

    file: str
    type: ClassVar[MockCompletionType] = MockCompletionType.RESULT

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "file", _require_plain_file_name(self.file, "result 事件 file")
        )


@dataclass(frozen=True)
class MockArchivalCompletion(MockCompletionEvent):
    """场景记录的归档摘要；模拟链路只展示，不执行正式归档。"""

    outcome: Mapping[str, Any]
    type: ClassVar[MockCompletionType] = MockCompletionType.ARCHIVAL

    def __post_init__(self) -> None:
        outcome = _require_mapping(self.outcome, "archival 事件 outcome")
        missing = [field for field in ("stage", "test_status") if field not in outcome]
        if missing:
            raise MockScenarioError(
                f"archival.outcome 缺少必需字段: {', '.join(missing)}"
            )
        _require_identifier(outcome.get("stage"), "archival.outcome.stage")
        _require_identifier(
            outcome.get("test_status"), "archival.outcome.test_status"
        )
        object.__setattr__(self, "outcome", freeze(outcome))


@dataclass(frozen=True)
class MockMessageCompletion(MockCompletionEvent):
    """完成日志。"""

    text: str
    type: ClassVar[MockCompletionType] = MockCompletionType.MESSAGE

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "text", _require_text(self.text, "完成 message 事件 text")
        )


@dataclass(frozen=True)
class MockFinishedCompletion(MockCompletionEvent):
    """结束信号；必须是最后一个完成事件。"""

    type: ClassVar[MockCompletionType] = MockCompletionType.FINISHED


_COMPLETION_RANK = {
    MockCompletionType.RESULT: 0,
    MockCompletionType.ARCHIVAL: 1,
    MockCompletionType.MESSAGE: 2,
    MockCompletionType.FINISHED: 3,
}


@dataclass(frozen=True)
class MockScenario:
    """一次 Mock 回放的完整只读描述。"""

    kind: ClassVar[str] = MOCK_SCENARIO_KIND

    schema_version: str
    measurement_type: MeasurementType | str
    title: str
    description: str
    source_config: MockSourceConfig
    run: MockRunInfo
    phases: tuple[MockPhase, ...]
    completion: tuple[MockCompletionEvent, ...]

    def __post_init__(self) -> None:
        version = _require_identifier(self.schema_version, "schema_version")
        if version not in MOCK_SCENARIO_SCHEMA_VERSIONS:
            supported = "、".join(MOCK_SCENARIO_SCHEMA_VERSIONS)
            raise MockScenarioError(
                f"不支持的 Mock 场景 schema_version: {version!r}；"
                f"当前支持: {supported}"
            )
        object.__setattr__(self, "schema_version", version)
        object.__setattr__(
            self, "measurement_type", _coerce_measurement_type(self.measurement_type)
        )
        object.__setattr__(
            self, "title", _require_text(self.title, "场景 title")
        )
        object.__setattr__(
            self,
            "description",
            _require_text(self.description, "场景 description", allow_empty=True),
        )
        if not isinstance(self.source_config, MockSourceConfig):
            raise MockScenarioError(
                "场景 source_config 必须是 MockSourceConfig，"
                f"收到 {_type_name(self.source_config)}"
            )
        if not isinstance(self.run, MockRunInfo):
            raise MockScenarioError(
                f"场景 run 必须是 MockRunInfo，收到 {_type_name(self.run)}"
            )
        phases = tuple(_require_sequence(self.phases, "phases"))
        if not phases:
            raise MockScenarioError("场景必须至少包含一个回放阶段")
        names: set[str] = set()
        confirm_count = 0
        previous_percent: int | None = None
        previous_location = ""
        for index, phase in enumerate(phases):
            if not isinstance(phase, MockPhase):
                raise MockScenarioError(
                    f"phases[{index}] 必须是 MockPhase，收到 {_type_name(phase)}"
                )
            if phase.name in names:
                raise MockScenarioError(f"阶段名重复: {phase.name!r}")
            names.add(phase.name)
            if index == 0 and not isinstance(phase.start, MockAutoPhaseStart):
                raise MockScenarioError(
                    f"第一个回放阶段 {phase.name!r} 必须以 auto 开始"
                )
            if isinstance(phase.start, MockUserConfirmPhaseStart):
                confirm_count += 1
            for event_index, event in enumerate(phase.events):
                if not isinstance(event, MockProgressEvent):
                    continue
                location = f"阶段 {phase.name} 的 events[{event_index}]"
                if (
                    previous_percent is not None
                    and event.percent < previous_percent
                ):
                    raise MockScenarioError(
                        "场景进度不能倒退: "
                        f"{location} 的 {event.percent} < {previous_location}"
                        f" 的 {previous_percent}"
                    )
                previous_percent = event.percent
                previous_location = location
        if confirm_count > 1:
            raise MockScenarioError(
                "场景最多只能有一个 user_confirm 阶段，"
                f"收到 {confirm_count} 个"
            )
        object.__setattr__(self, "phases", phases)
        completion = tuple(_require_sequence(self.completion, "completion"))
        self._validate_completion(completion)
        object.__setattr__(self, "completion", completion)

    @staticmethod
    def _validate_completion(completion: tuple[MockCompletionEvent, ...]) -> None:
        if not completion:
            raise MockScenarioError(
                "场景 completion 必须包含 result 与 finished 事件"
            )
        for index, event in enumerate(completion):
            if not isinstance(
                event,
                (
                    MockResultCompletion,
                    MockArchivalCompletion,
                    MockMessageCompletion,
                    MockFinishedCompletion,
                ),
            ):
                raise MockScenarioError(
                    f"completion[{index}] 必须是具体完成事件"
                    "（MockResultCompletion、MockArchivalCompletion、"
                    "MockMessageCompletion 或 MockFinishedCompletion），"
                    f"收到 {_type_name(event)}"
                )
        types = [event.type for event in completion]
        ranks: list[int] = []
        for kind in types:
            rank = _COMPLETION_RANK.get(kind)
            if rank is None:
                raise MockScenarioError(
                    f"不支持的完成事件类型: {kind!r}"
                )
            ranks.append(rank)
        if ranks != sorted(ranks):
            received = " → ".join(kind.value for kind in types)
            raise MockScenarioError(
                "完成事件顺序必须是 result → archival → message → finished，"
                f"收到: {received}"
            )
        result_count = types.count(MockCompletionType.RESULT)
        if result_count != 1:
            raise MockScenarioError(
                "场景 completion 必须包含且只包含一个 result 事件，"
                f"收到 {result_count} 个"
            )
        finished_count = types.count(MockCompletionType.FINISHED)
        if finished_count != 1:
            raise MockScenarioError(
                "场景 completion 必须包含且只包含一个 finished 事件，"
                f"收到 {finished_count} 个"
            )
        archival_count = types.count(MockCompletionType.ARCHIVAL)
        if archival_count > 1:
            raise MockScenarioError(
                "场景 completion 最多只能有一个 archival 事件，"
                f"收到 {archival_count} 个"
            )

    @property
    def result_filename(self) -> str:
        """完成时加载的正式结果文件名（已由契约保证只有一个）。"""
        for event in self.completion:
            if isinstance(event, MockResultCompletion):
                return event.file
        raise AssertionError("MockScenario 契约保证存在一个 result 完成事件")

    @property
    def user_confirm_phase(self) -> MockPhase | None:
        """需要用户确认后继续的阶段；没有确认点时为 ``None``。"""
        for phase in self.phases:
            if isinstance(phase.start, MockUserConfirmPhaseStart):
                return phase
        return None

    @property
    def data_event_count(self) -> int:
        return sum(phase.data_event_count for phase in self.phases)


def _parse_phase_start(value: Any, description: str) -> MockPhaseStart:
    document = _require_mapping(value, f"{description}.start")
    start_type = _require_identifier(document.get("type"), f"{description}.start.type")
    if start_type == MockPhaseStartType.AUTO.value:
        _require_object(
            document,
            description=f"{description}.start",
            required=("type",),
        )
        return MockAutoPhaseStart()
    if start_type == MockPhaseStartType.USER_CONFIRM.value:
        document = _require_object(
            document,
            description=f"{description}.start",
            required=(
                "type",
                "signal",
                "checkpoint",
                "prompt",
                "accepted",
                "rejected",
            ),
        )
        return MockUserConfirmPhaseStart(
            signal=document["signal"],
            checkpoint=document["checkpoint"],
            prompt=document["prompt"],
            accepted=document["accepted"],
            rejected=document["rejected"],
        )
    supported = "、".join(kind.value for kind in MockPhaseStartType)
    raise MockScenarioError(
        f"{description}.start.type 不支持的阶段起点: {start_type!r}；"
        f"支持: {supported}"
    )


def _parse_event(value: Any, description: str) -> MockScenarioEvent:
    document = _require_mapping(value, description)
    raw_type = document.get("type")
    if isinstance(raw_type, MockEventType):
        event_type = raw_type
    elif isinstance(raw_type, str):
        try:
            event_type = MockEventType(raw_type)
        except ValueError as error:
            supported = "、".join(kind.value for kind in MockEventType)
            raise MockScenarioError(
                f"{description}.type 不支持的事件类型: {raw_type!r}；"
                f"支持: {supported}"
            ) from error
    else:
        supported = "、".join(kind.value for kind in MockEventType)
        raise MockScenarioError(
            f"{description}.type 必须是字符串，收到 {_type_name(raw_type)}；"
            f"支持: {supported}"
        )

    if event_type is MockEventType.MESSAGE:
        document = _require_object(
            document,
            description=description,
            required=("at_ms", "type", "text"),
        )
        return MockMessageEvent(at_ms=document["at_ms"], text=document["text"])
    if event_type is MockEventType.PROGRESS:
        document = _require_object(
            document,
            description=description,
            required=("at_ms", "type", "percent", "stage"),
        )
        return MockProgressEvent(
            at_ms=document["at_ms"],
            percent=document["percent"],
            stage=document["stage"],
        )
    document = _require_object(
        document,
        description=description,
        required=("at_ms", "type", "payload"),
    )
    return MockDataEvent(at_ms=document["at_ms"], payload=document["payload"])


def _parse_phase(value: Any, index: int) -> MockPhase:
    description = f"phases[{index}]"
    document = _require_object(
        value,
        description=description,
        required=("name", "title", "start", "events"),
    )
    events_document = _require_sequence(document["events"], f"{description}.events")
    if not events_document:
        raise MockScenarioError(f"{description}.events 不能为空数组")
    events = tuple(
        _parse_event(event, f"{description}.events[{event_index}]")
        for event_index, event in enumerate(events_document)
    )
    return MockPhase(
        name=document["name"],
        title=document["title"],
        start=_parse_phase_start(document["start"], description),
        events=events,
    )


def _parse_phases(value: Any) -> tuple[MockPhase, ...]:
    phases_document = _require_sequence(value, "phases")
    if not phases_document:
        raise MockScenarioError("场景必须至少包含一个回放阶段")
    return tuple(
        _parse_phase(phase, index) for index, phase in enumerate(phases_document)
    )


def _parse_completion_event(value: Any, index: int) -> MockCompletionEvent:
    description = f"completion[{index}]"
    document = _require_mapping(value, description)
    raw_type = _require_identifier(
        document.get("type"), f"{description}.type"
    )
    try:
        completion_type = MockCompletionType(raw_type)
    except ValueError as error:
        supported = "、".join(kind.value for kind in MockCompletionType)
        raise MockScenarioError(
            f"{description}.type 不支持的完成事件类型: {raw_type!r}；"
            f"支持: {supported}"
        ) from error

    if completion_type is MockCompletionType.RESULT:
        document = _require_object(
            document,
            description=description,
            required=("type", "file"),
        )
        return MockResultCompletion(file=document["file"])
    if completion_type is MockCompletionType.ARCHIVAL:
        document = _require_object(
            document,
            description=description,
            required=("type", "outcome"),
        )
        return MockArchivalCompletion(outcome=document["outcome"])
    if completion_type is MockCompletionType.MESSAGE:
        document = _require_object(
            document,
            description=description,
            required=("type", "text"),
        )
        return MockMessageCompletion(text=document["text"])
    _require_object(
        document,
        description=description,
        required=("type",),
    )
    return MockFinishedCompletion()


def _parse_completion(value: Any) -> tuple[MockCompletionEvent, ...]:
    completion_document = _require_sequence(value, "completion")
    if not completion_document:
        raise MockScenarioError(
            "场景 completion 必须包含 result 与 finished 事件"
        )
    return tuple(
        _parse_completion_event(event, index)
        for index, event in enumerate(completion_document)
    )


def _parse_source_config(value: Any) -> MockSourceConfig:
    document = _require_object(
        value,
        description="source_config",
        required=MOCK_SOURCE_CONFIG_REQUIRED_FIELDS,
        optional=MOCK_SOURCE_CONFIG_OPTIONAL_FIELDS,
    )
    plans_document = _require_sequence(
        document["power_scan_plans"], "source_config.power_scan_plans"
    )
    plans = []
    for index, plan in enumerate(plans_document):
        description = f"source_config.power_scan_plans[{index}]"
        plan_document = _require_object(
            plan,
            description=description,
            required=("start_power", "step", "stop_power"),
        )
        plans.append(
            MockPowerScanPlan(
                start_power_dbm=plan_document["start_power"],
                stop_power_dbm=plan_document["stop_power"],
                step_db=plan_document["step"],
            )
        )
    compression_point = _parse_compression_point(document)
    return MockSourceConfig(
        template=document["template"],
        test_frequencies_ghz=document["test_frequencies_ghz"],
        attenuator_db=document["attenuator_db"],
        driver_enabled=document["driver_enabled"],
        driver_estimated_gain_db=document["driver_estimated_gain_db"],
        driver_max_input_power_dbm=document["driver_max_input_power_dbm"],
        dut_max_input_power_dbm=document["dut_max_input_power_dbm"],
        power_scan_plans=tuple(plans),
        compression_point=compression_point,
        target_input_powers_dbm=document.get("target_input_powers_dbm", ()),
        signal_source_power_range_dbm=document.get(
            "signal_source_power_range_dbm"
        ),
        cable_loss_result=document.get("cable_loss_result"),
        driver_mapping_result=document.get("driver_mapping_result"),
        document=document,
    )


def _parse_compression_point(document: Mapping[str, Any]) -> str | None:
    """两种阶段的摘要键名不同：线损/驱动映射写 ``compression_type``，主功放写
    ``compression_point``。两者同时出现时必须一致，否则场景自身已经自相矛盾。"""
    values = [
        value
        for value in (
            document.get("compression_type"),
            document.get("compression_point"),
        )
        if value is not None
    ]
    if not values:
        return None
    if len(values) == 2 and values[0] != values[1]:
        raise MockScenarioError(
            "source_config.compression_type 与 compression_point 不一致: "
            f"{values[0]!r} != {values[1]!r}"
        )
    return values[0]


def mock_scenario_from_document(
    document: Any,
    *,
    expected_measurement_type: MeasurementType | str | None = None,
) -> MockScenario:
    """校验一个已解析的 JSON 文档并返回 :class:`MockScenario`。

    ``expected_measurement_type`` 由调用方给出该场景应当服务的测量类型（通常来自
    阶段索引）。类型一致后场景才会交给回放器，避免把线损场景放进驱动映射页面。
    """
    root = _require_object(
        document,
        description="Mock 场景",
        required=(
            "schema_version",
            "kind",
            "measurement_type",
            "title",
            "description",
            "source_config",
            "run",
            "phases",
            "completion",
        ),
    )
    kind = _require_identifier(root["kind"], "kind")
    if kind != MOCK_SCENARIO_KIND:
        raise MockScenarioError(
            f"Mock 场景的 kind 必须是 {MOCK_SCENARIO_KIND!r}，收到 {kind!r}"
        )
    run_document = _require_object(
        root["run"],
        description="run",
        required=("run_id", "mode", "started_at"),
    )
    scenario = MockScenario(
        schema_version=root["schema_version"],
        measurement_type=root["measurement_type"],
        title=root["title"],
        description=root["description"],
        source_config=_parse_source_config(root["source_config"]),
        run=MockRunInfo(
            run_id=run_document["run_id"],
            mode=run_document["mode"],
            started_at=run_document["started_at"],
        ),
        phases=_parse_phases(root["phases"]),
        completion=_parse_completion(root["completion"]),
    )
    if expected_measurement_type is not None:
        expected = _coerce_measurement_type(
            expected_measurement_type, "期望的 Mock 测量类型"
        )
        if scenario.measurement_type is not expected:
            raise MockScenarioError(
                "Mock 场景的测量类型与目标阶段不一致: "
                f"{scenario.measurement_type.value} != {expected.value}"
            )
    return scenario


__all__ = [
    "MOCK_CONFIRM_ACCEPTED",
    "MOCK_CONFIRM_REJECTED",
    "MOCK_CONFIRM_SIGNAL",
    "MOCK_SCENARIO_KIND",
    "MOCK_SCENARIO_SCHEMA_VERSION",
    "MOCK_SCENARIO_SCHEMA_VERSIONS",
    "MOCK_SOURCE_CONFIG_OPTIONAL_FIELDS",
    "MOCK_SOURCE_CONFIG_REQUIRED_FIELDS",
    "MockArchivalCompletion",
    "MockAutoPhaseStart",
    "MockCompletionEvent",
    "MockCompletionType",
    "MockDataEvent",
    "MockEventType",
    "MockFinishedCompletion",
    "MockMessageCompletion",
    "MockMessageEvent",
    "MockPhase",
    "MockPhaseStart",
    "MockPhaseStartType",
    "MockPowerScanPlan",
    "MockProgressEvent",
    "MockResultCompletion",
    "MockRunInfo",
    "MockScenario",
    "MockScenarioError",
    "MockScenarioEvent",
    "MockSourceConfig",
    "MockUserConfirmPhaseStart",
    "mock_scenario_from_document",
]
