"""Mock 配置匹配：把“Mock 快照 + 配置文档”收敛为一次模拟模式判定。

Mock 数据固定对应一份配置摘要（``scenario.json`` 的 ``source_config``）。本模块只做
纯 Python 比较，不读文件、不依赖窗口或协调器、不修改配置文档：

- 快照名称只是**入口**：去除首尾空白后大小写不敏感等于
  :data:`MOCK_CONFIGURATION_NAME` 才算候选。名称不匹配不是错误，调用方先用
  :func:`is_mock_configuration_name` 或 :meth:`MockConfigurationMatcher.matches_name`
  分流，普通配置继续走正式连接流程。
- 名称为 Mock 之后，配置摘要必须与每个 Mock 场景的事实一致，否则拒绝进入模拟模式。
  比较只覆盖影响回放数据的关键事实（频点与单位、衰减器与单位、压缩点、驱放参数、
  DUT 上限、功率扫描计划与单位、功率基准），不比较设备 IP、VISA 地址、配置模板
  文件名和电源通道设置。扫描计划与旧标量同时出现时直接拒绝，不替调用方选一个来源。
- ``document`` 必须是已保存或已按正式格式规范化的配置文档（``test_plan`` 已由编辑段
  重新生成）；界面应在匹配前先走正式保存门禁，不要把用户编辑中的原始文档直接交给
  这里，否则可能读到旧的 ``test_plan`` 摘要。
- 同一份 Mock 数据包的多个场景共享同一份配置身份；匹配器按传入的全部场景逐一校验，
  任一场景不匹配都会给出带场景名与字段名的可读错误。各场景的 ``source_config`` 仍
  可能包含阶段特有字段，调用方按测量类型取用，不跨场景合并。

配置读取复用 :class:`domain.configuration.models.TestPlan`：它同时理解 1.2 的
``power_scan_plans`` 与 1.1 的单段标量形状，匹配器不另写一套配置文档格式。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from application.dto.measurement_results import MeasurementType
from application.dto.mock_scenario import MockScenario, MockSourceConfig
from application.errors import ConfigurationError
from domain.configuration.models import (
    INPUT_POWER_REFERENCE,
    LEGACY_POWER_SCAN_FIELDS,
    TestPlan,
)


MOCK_CONFIGURATION_NAME = "Mock"

# 只吸收浮点表述误差（例如 40.00000000000001），不掩盖用户真实修改的最小步长。
_NUMBER_RELATIVE_TOLERANCE = 1e-9
_NUMBER_ABSOLUTE_TOLERANCE = 1e-12


class MockConfigurationError(ConfigurationError):
    """快照名称或配置摘要不能被当作 Mock 模拟配置。

    与配置类错误一致：同时是 ``ValueError``，调用方在进入模拟模式前失败，不需要靠
    字符串匹配判断原因。
    """


@dataclass(frozen=True)
class MockConfigurationMatch:
    """一次成功的 Mock 配置匹配结果。

    ``scenarios`` 是同一份 Mock 数据包的全部场景；调用方按测量类型取用，不需要再读
    场景文件。各场景的 ``source_config`` 可能包含阶段特有字段（压缩点、校准结果
    路径），应通过 :meth:`scenario_for` 取用，不要跨场景合并。

    ``snapshot_name`` 是去首尾空白后的快照显示名称（保留大小写）；是否为 Mock 一律
    用 :func:`is_mock_configuration_name` 判断，不要拿它和字面量比较。结果只证明
    “配置摘要与 Mock 数据一致”，不放宽任何真机连接门禁。
    """

    snapshot_name: str
    scenarios: tuple[MockScenario, ...]

    @property
    def measurement_types(self) -> tuple[MeasurementType, ...]:
        """本数据包可以回放的测量类型，顺序与传入场景一致。"""
        return tuple(scenario.measurement_type for scenario in self.scenarios)

    def scenario_for(self, measurement_type: MeasurementType | str) -> MockScenario:
        """按正式测量类型取回放场景；数据包不包含该类型时给出可用列表。"""
        expected = _coerce_measurement_type(measurement_type)
        for scenario in self.scenarios:
            if scenario.measurement_type is expected:
                return scenario
        allowed = "、".join(kind.value for kind in self.measurement_types)
        raise MockConfigurationError(
            f"当前 Mock 数据不包含 {expected.value} 场景；可用场景: {allowed}"
        )


@dataclass(frozen=True)
class _ConfigurationFacts:
    """从配置文档 ``test_plan`` 段提取的、可直接与场景摘要比较的事实。

    不可用或非法的值保持 ``None``，由比较函数翻译成“未配置有效数值”的可读错误；
    提取阶段不因为单字段非法就丢掉其它字段的比较结果。
    """

    frequencies_ghz: tuple[float, ...] | None
    frequency_unit: str | None
    attenuator_db: float | None
    attenuator_unit: str | None
    compression_point: str | None
    driver_enabled: bool | None
    driver_gain_db: float | None
    driver_max_input_dbm: float | None
    dut_max_input_dbm: float | None
    power_scan_plans: tuple[tuple[float, float, float], ...] | None
    power_unit: str | None
    power_reference: str | None


def is_mock_configuration_name(display_name: Any) -> bool:
    """快照名称是否是 Mock 模拟模式的候选入口。

    去除首尾空白后大小写不敏感地等于 ``Mock``；其余名称（包括“Mock 副本”）都按普通
    配置处理，避免一份改过名的快照悄悄进入模拟模式。
    """
    if not isinstance(display_name, str):
        return False
    return display_name.strip().casefold() == MOCK_CONFIGURATION_NAME.casefold()


class MockConfigurationMatcher:
    """按 Mock 场景的 ``source_config`` 校验一份配置快照。

    匹配器与窗口、协调器和文件系统无关：调用方负责读取场景（步骤 1/2 的路径服务与
    场景读取器）和配置快照，再把纯数据交给这里。构造一次即可复用，也可以只为单个
    场景构造（例如线损页面单独验证）。
    """

    def __init__(
        self, scenarios: MockScenario | Sequence[MockScenario]
    ) -> None:
        self._scenarios = _normalize_scenarios(scenarios)

    @property
    def scenarios(self) -> tuple[MockScenario, ...]:
        """匹配器覆盖的全部场景，顺序与构造时一致。"""
        return self._scenarios

    @staticmethod
    def matches_name(display_name: Any) -> bool:
        """名称是否是 Mock 候选入口；等价于 :func:`is_mock_configuration_name`。"""
        return is_mock_configuration_name(display_name)

    def match(
        self,
        display_name: Any,
        document: Mapping[str, Any],
    ) -> MockConfigurationMatch:
        """校验名称与配置摘要；成功返回 :class:`MockConfigurationMatch`。

        ``document`` 是已保存快照的完整配置文档（含 ``test_plan`` 段）。比较只读，
        不修改文档；失败时一次性列出所有场景、所有不一致字段，便于用户定位。
        """
        snapshot_name = _require_mock_name(display_name)
        facts = _extract_configuration(document)
        problems: list[str] = []
        for scenario in self._scenarios:
            mismatches = _mismatched_facts(scenario, facts)
            if not mismatches:
                continue
            problems.append(
                f"场景「{scenario.title}」"
                f"（{scenario.measurement_type.value}）: "
                + "；".join(mismatches)
            )
        if problems:
            raise MockConfigurationError(
                f"快照「{snapshot_name}」的配置与 Mock 数据不一致，"
                "不能进入模拟模式：" + "\n".join(problems)
            )
        return MockConfigurationMatch(
            snapshot_name=snapshot_name, scenarios=self._scenarios
        )


def _normalize_scenarios(
    scenarios: MockScenario | Sequence[MockScenario],
) -> tuple[MockScenario, ...]:
    """接受单个场景或场景序列，拒绝空集合、非场景元素与重复测量类型。"""
    if isinstance(scenarios, MockScenario):
        candidates: tuple[Any, ...] = (scenarios,)
    elif isinstance(scenarios, (str, bytes)) or not isinstance(
        scenarios, Sequence
    ):
        raise MockConfigurationError(
            "MockConfigurationMatcher 需要 MockScenario 或其序列，"
            f"收到 {_type_name(scenarios)}"
        )
    else:
        candidates = tuple(scenarios)
    normalized: list[MockScenario] = []
    seen: set[MeasurementType] = set()
    for index, scenario in enumerate(candidates):
        if not isinstance(scenario, MockScenario):
            raise MockConfigurationError(
                f"第 {index + 1} 个 Mock 场景必须是 MockScenario，"
                f"收到 {_type_name(scenario)}"
            )
        if scenario.measurement_type in seen:
            raise MockConfigurationError(
                "同一个 Mock 配置匹配器中不能出现重复的测量类型: "
                f"{scenario.measurement_type.value}"
            )
        seen.add(scenario.measurement_type)
        normalized.append(scenario)
    if not normalized:
        raise MockConfigurationError(
            "MockConfigurationMatcher 至少需要一个 Mock 场景"
        )
    return tuple(normalized)


def _require_mock_name(display_name: Any) -> str:
    """确认名称是 Mock 候选入口，并返回去空白后的名称。"""
    if not isinstance(display_name, str):
        raise MockConfigurationError(
            "配置快照名称必须是字符串，"
            f"收到 {_type_name(display_name)}"
        )
    snapshot_name = display_name.strip()
    if not is_mock_configuration_name(snapshot_name):
        raise MockConfigurationError(
            f"配置快照「{display_name}」不是 Mock 模拟配置；"
            f"只有名称去除首尾空白后等于 {MOCK_CONFIGURATION_NAME!r}"
            "（大小写不敏感）的快照可以进入模拟模式"
        )
    return snapshot_name


def _extract_configuration(document: Any) -> _ConfigurationFacts:
    """从已保存配置文档提取比较事实；结构不完整时在比较前失败。"""
    test_plan_document = _require_test_plan_document(document)
    _require_unambiguous_scan_source(test_plan_document)
    test_plan = TestPlan.from_dict(test_plan_document)
    return _ConfigurationFacts(
        frequencies_ghz=_number_sequence(test_plan.frequencies),
        frequency_unit=_optional_text(test_plan.frequency_unit),
        attenuator_db=_optional_number(test_plan.attenuator_value),
        attenuator_unit=_optional_text(test_plan.attenuator_unit),
        compression_point=_optional_text(test_plan.compression_point),
        driver_enabled=(
            test_plan.driver_enabled
            if isinstance(test_plan.driver_enabled, bool)
            else None
        ),
        driver_gain_db=_optional_number(test_plan.driver_gain_estimate_db),
        driver_max_input_dbm=_optional_number(
            test_plan.driver_max_input_power
        ),
        dut_max_input_dbm=_optional_number(test_plan.max_input_power),
        power_scan_plans=_scan_plan_triples(test_plan.power_scan_plans),
        power_unit=_optional_text(test_plan.power_unit),
        power_reference=_optional_text(test_plan.power_reference),
    )


def _require_test_plan_document(document: Any) -> Mapping[str, Any]:
    """要求配置文档是对象，并带有可比较的 ``test_plan`` 段。"""
    if not isinstance(document, Mapping):
        raise MockConfigurationError(
            f"配置文档必须是 JSON 对象，收到 {_type_name(document)}"
        )
    if "test_plan" not in document:
        raise MockConfigurationError(
            "配置文档缺少 test_plan 段，无法与 Mock 数据比较；"
            "请先保存配置快照再进入模拟模式"
        )
    test_plan = document["test_plan"]
    if not isinstance(test_plan, Mapping):
        raise MockConfigurationError(
            "配置文档的 test_plan 段必须是 JSON 对象，"
            f"收到 {_type_name(test_plan)}"
        )
    return test_plan


def _require_unambiguous_scan_source(test_plan: Mapping[str, Any]) -> None:
    """拒绝同时出现 1.2 计划列表与 1.1 旧标量的扫描配置。

    检查原始键，而不是只看 ``TestPlan.conflicting_scan_scalars``：
    ``power_scan_plans`` 为空列表时后者不会报告冲突，但文档仍然同时声明了两个
    来源，匹配器不能替调用方选一个继续比较。
    """
    signal_source = test_plan.get("signal_source")
    if not isinstance(signal_source, Mapping):
        return
    legacy = [
        key for key in LEGACY_POWER_SCAN_FIELDS if key in signal_source
    ]
    if "power_scan_plans" not in signal_source or not legacy:
        return
    raise MockConfigurationError(
        "配置文档的 signal_source 同时包含 power_scan_plans 与旧字段 "
        f"{'、'.join(legacy)}；功率扫描只能有一个来源，"
        "请按正式配置重新保存"
    )


def _type_name(value: Any) -> str:
    return type(value).__name__


def _optional_number(value: Any) -> float | None:
    """有限数字或 ``None``；布尔值不算数字，与场景契约保持一致。

    JSON 整数可能超出浮点范围（例如几百位数字），``float()`` 会抛
    :class:`OverflowError`。这类值按“不可比较”处理，不能让异常逃出匹配器。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _number_sequence(values: Any) -> tuple[float, ...] | None:
    """把配置里的数值数组转换为浮点元组；任一项非法时整体不可比较。"""
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        return None
    numbers: list[float] = []
    for value in values:
        number = _optional_number(value)
        if number is None:
            return None
        numbers.append(number)
    return tuple(numbers)


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def _scan_plan_triples(
    plans: Any,
) -> tuple[tuple[float, float, float], ...] | None:
    """把配置的功率扫描计划转换为 ``(start, step, stop)`` 元组序列。"""
    if isinstance(plans, (str, bytes)) or not isinstance(plans, Sequence):
        return None
    triples: list[tuple[float, float, float]] = []
    for plan in plans:
        start = _optional_number(plan.start_power)
        step = _optional_number(plan.step)
        stop = _optional_number(plan.stop_power)
        if start is None or step is None or stop is None:
            return None
        triples.append((start, step, stop))
    return tuple(triples)


def _mismatched_facts(
    scenario: MockScenario, facts: _ConfigurationFacts
) -> tuple[str, ...]:
    """返回该场景下所有不一致字段；空元组表示配置摘要匹配。"""
    source = scenario.source_config
    problems: list[str] = []
    problems.extend(_frequency_problems(source, facts))
    problems.extend(_attenuator_problems(source, facts))
    if (
        source.compression_point is not None
        and facts.compression_point != source.compression_point
    ):
        problems.append(
            "压缩点不一致：配置为 "
            f"{_describe_text(facts.compression_point)}，"
            f"Mock 数据为 {source.compression_point}"
        )
    problems.extend(_driver_problems(source, facts))
    problems.extend(_scan_plan_problems(source, facts))
    if facts.power_reference != INPUT_POWER_REFERENCE:
        problems.append(
            f"功率基准必须是 {INPUT_POWER_REFERENCE}（主功放输入功率），配置为 "
            f"{_describe_text(facts.power_reference)}；"
            "请按“主功放输入功率”核对并重新保存配置"
        )
    return tuple(problems)


def _frequency_problems(
    source: MockSourceConfig, facts: _ConfigurationFacts
) -> list[str]:
    if facts.frequency_unit != "GHz":
        return [
            "频率单位必须是 GHz，配置为 "
            f"{_describe_text(facts.frequency_unit)}"
        ]
    if _same_numbers(facts.frequencies_ghz, source.test_frequencies_ghz):
        return []
    return [
        "测试频点不一致：配置为 "
        f"{_describe_numbers(facts.frequencies_ghz)}，"
        f"Mock 数据为 {_describe_numbers(source.test_frequencies_ghz)}"
    ]


def _attenuator_problems(
    source: MockSourceConfig, facts: _ConfigurationFacts
) -> list[str]:
    if facts.attenuator_unit != "dB":
        return [
            "衰减器单位必须是 dB，配置为 "
            f"{_describe_text(facts.attenuator_unit)}"
        ]
    if _same_number(facts.attenuator_db, source.attenuator_db):
        return []
    return [
        _number_problem(
            "衰减器",
            source.attenuator_db,
            facts.attenuator_db,
            unit="dB",
        )
    ]


def _driver_problems(
    source: MockSourceConfig, facts: _ConfigurationFacts
) -> list[str]:
    problems: list[str] = []
    expected_switch = "开启" if source.driver_enabled else "关闭"
    if facts.driver_enabled is None:
        problems.append(
            f"驱放开关未配置有效布尔值；Mock 数据要求{expected_switch}"
        )
    elif facts.driver_enabled != source.driver_enabled:
        actual_switch = "开启" if facts.driver_enabled else "关闭"
        problems.append(
            f"驱放开关不一致：配置为{actual_switch}，"
            f"Mock 数据为{expected_switch}"
        )
    if not _same_number(
        facts.driver_gain_db, source.driver_estimated_gain_db
    ):
        problems.append(
            _number_problem(
                "驱放预估增益",
                source.driver_estimated_gain_db,
                facts.driver_gain_db,
                unit="dB",
            )
        )
    if not _same_number(
        facts.driver_max_input_dbm, source.driver_max_input_power_dbm
    ):
        problems.append(
            _number_problem(
                "驱放最大输入功率",
                source.driver_max_input_power_dbm,
                facts.driver_max_input_dbm,
                unit="dBm",
            )
        )
    if not _same_number(
        facts.dut_max_input_dbm, source.dut_max_input_power_dbm
    ):
        problems.append(
            _number_problem(
                "DUT 最大输入功率",
                source.dut_max_input_power_dbm,
                facts.dut_max_input_dbm,
                unit="dBm",
            )
        )
    return problems


def _scan_plan_problems(
    source: MockSourceConfig, facts: _ConfigurationFacts
) -> list[str]:
    if facts.power_unit != "dBm":
        return [
            "功率扫描单位必须是 dBm，配置为 "
            f"{_describe_text(facts.power_unit)}"
        ]
    expected = tuple(
        (plan.start_power_dbm, plan.step_db, plan.stop_power_dbm)
        for plan in source.power_scan_plans
    )
    if _same_plans(facts.power_scan_plans, expected):
        return []
    return [
        "功率扫描计划不一致：配置为 "
        f"{_describe_plans(facts.power_scan_plans)}，"
        f"Mock 数据为 {_describe_plans(expected)}"
    ]


def _same_number(actual: float | None, expected: float) -> bool:
    if actual is None:
        return False
    return math.isclose(
        actual,
        expected,
        rel_tol=_NUMBER_RELATIVE_TOLERANCE,
        abs_tol=_NUMBER_ABSOLUTE_TOLERANCE,
    )


def _same_numbers(
    actual: Sequence[float] | None, expected: Sequence[float]
) -> bool:
    if actual is None or len(actual) != len(expected):
        return False
    return all(
        _same_number(item, target) for item, target in zip(actual, expected)
    )


def _same_plans(
    actual: tuple[tuple[float, float, float], ...] | None,
    expected: tuple[tuple[float, float, float], ...],
) -> bool:
    if actual is None or len(actual) != len(expected):
        return False
    return all(
        _same_number(got[0], want[0])
        and _same_number(got[1], want[1])
        and _same_number(got[2], want[2])
        for got, want in zip(actual, expected)
    )


def _number_problem(
    name: str,
    expected: float,
    actual: float | None,
    *,
    unit: str,
) -> str:
    if actual is None:
        return (
            f"{name}未配置有效数值；"
            f"Mock 数据为 {_describe_number(expected, unit)}"
        )
    return (
        f"{name}不一致：配置为 {_describe_number(actual, unit)}，"
        f"Mock 数据为 {_describe_number(expected, unit)}"
    )


def _describe_number(value: float, unit: str) -> str:
    return f"{value:g} {unit}"


def _describe_text(value: str | None) -> str:
    return value if value is not None else "未配置"


def _describe_numbers(values: Sequence[float] | None) -> str:
    if values is None:
        return "未配置或包含非法数值"
    return "[" + ", ".join(f"{value:g}" for value in values) + "]"


def _describe_plans(
    plans: tuple[tuple[float, float, float], ...] | None,
) -> str:
    if plans is None:
        return "未配置或包含非法数值"
    if not plans:
        return "未配置"
    return "、".join(
        f"{start:g}→{stop:g} dBm（步进 {step:g} dB）"
        for start, step, stop in plans
    )


def _coerce_measurement_type(value: Any) -> MeasurementType:
    if isinstance(value, MeasurementType):
        return value
    try:
        return MeasurementType(value)
    except (TypeError, ValueError) as error:
        allowed = "、".join(kind.value for kind in MeasurementType)
        raise MockConfigurationError(
            f"未知测量类型: {value!r}；可用类型: {allowed}"
        ) from error


__all__ = [
    "MOCK_CONFIGURATION_NAME",
    "MockConfigurationError",
    "MockConfigurationMatch",
    "MockConfigurationMatcher",
    "is_mock_configuration_name",
]
