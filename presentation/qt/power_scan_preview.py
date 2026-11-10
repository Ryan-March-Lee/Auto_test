"""DUT 功率扫描计划的点位预览与逐行错误（纯展示计算）。

本模块只把控件读数整理成“每一行该显示的灰色文本”，点位计算与连续性判定全部复用
:mod:`application.dto.amplifier_inputs` 的共享入口（:class:`PowerSweep`、
:func:`validate_power_scan_schedule`、:func:`target_input_power_grid`）。这里不重新
实现步长网格、端点容差或相邻计划比较，避免页面预览与实际测量出现两套解释。

输出是可直接写进 :class:`presentation.qt.power_sweep_plan_row.PowerSweepPlanRow`
的文本、语义色和 tooltip：

- 合法行显示该段实际会进入目标序列的功率点；点多时只显示首尾与总数，完整点列放进
  tooltip，避免把页面撑高；
- 输入暂时非法（例如起点高于终点、步长不为正）时显示具体错误，不保留上一次的旧预览；
- 读数本身无法解析（长度不对、不是数字）时只给这一行错误，其余行照常预览；
- 相邻计划的空档/重叠标在后一段上：错误信息沿共享校验的原文，包含两段编号与功率值；
- 汇总标签显示合并后的目标点总数与有效上限，供用户确认多段确实首尾相接。

预览作为 Qt 槽函数的实现，不把异常抛回信号处理：任何一行读不出来都收敛为该行的
用户文案。Qt 层也不修改用户输入来修复空档或重叠：这里只回答“现在会生成什么/哪里
不成立”。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from application.dto import (
    AmplifierInputError,
    PowerScanSchedule,
    PowerSweep,
    target_input_power_grid,
    validate_power_scan_schedule,
)
from domain.measurement.values import Decibels, MeasurementValueError, PowerDbm

__all__ = [
    "PowerScanPreview",
    "PowerScanRowPreview",
    "preview_power_scan_plans",
]

#: 单行预览最多显示的功率点数；超过后只显示前 4 个和后 3 个，完整点列放进 tooltip。
_PREVIEW_LIMIT = 8
_PREVIEW_HEAD = 4
_PREVIEW_TAIL = 3

#: 值对象构造期可能抛出的异常：数值层（非有限/不可解析数值）与扫描值对象（步长/起止
#: 顺序）。``MeasurementValueError`` 本身是 ``ValueError``，这里显式列出是为了让调用点
#: 一眼看出收窄范围，不依赖继承关系。
_VALUE_ERRORS = (AmplifierInputError, MeasurementValueError, TypeError, ValueError)


@dataclass(frozen=True)
class PowerScanRowPreview:
    """一行扫描计划的展示结果；``tone`` 只决定语义色，不参与业务判断。"""

    text: str
    tone: str = "muted"
    tooltip: str = ""
    points: tuple[float, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class PowerScanPreview:
    """整份计划列表的展示结果，以及合并后的目标功率点。"""

    rows: tuple[PowerScanRowPreview, ...] = ()
    target_points: tuple[float, ...] = ()
    errors: tuple[str, ...] = ()
    summary: str = ""
    summary_tone: str = "muted"
    summary_tooltip: str = ""


@dataclass(frozen=True)
class _PlanReading:
    """一行读数的解析结果：能构造值对象时带 ``sweep``，否则只带 ``error``。"""

    start: float | None = None
    step: float | None = None
    stop: float | None = None
    sweep: PowerSweep | None = None
    error: str | None = None


def _format_power(value: float) -> str:
    """功率点显示精度：去掉无意义的尾随零，保持 ``-38`` / ``-10.25`` 这类紧凑写法。"""
    return f"{float(value):g}"


def _compact_points(points: Sequence[float]) -> str:
    """一行的点数文本：不多于 8 点时全列，否则只保留首 4 尾 3 并注明总数。"""
    values = [_format_power(point) for point in points]
    if len(values) <= _PREVIEW_LIMIT:
        body = "，".join(values)
    else:
        body = (
            "，".join(values[:_PREVIEW_HEAD])
            + "，…，"
            + "，".join(values[-_PREVIEW_TAIL:])
        )
    return f"共 {len(values)} 点：{body} dBm"


def _full_points(points: Sequence[float]) -> str:
    """tooltip 用的完整点列，不受行内截断限制。"""
    return "，".join(_format_power(point) for point in points) + " dBm"


def _row_tooltip(
    number: int,
    start: float,
    step: float,
    stop: float,
    points: Sequence[float],
    error: str | None = None,
) -> str:
    header = (
        f"第 {number} 段：起始 {_format_power(start)} dBm，"
        f"步长 {_format_power(step)} dB，终止 {_format_power(stop)} dBm"
    )
    if error:
        return f"{header}\n{error}"
    if not points:
        return header
    return f"{header}\n共 {len(points)} 点：{_full_points(points)}"


def _error_preview(number: int, message: str) -> PowerScanRowPreview:
    """整行只有一个错误时使用：不清空原因，也不伪造点位。"""
    return PowerScanRowPreview(
        text=message,
        tone="warning",
        tooltip=f"第 {number} 段\n{message}",
        error=message,
    )


def _read_plan(number: int, plan: Sequence[float]) -> _PlanReading:
    """把一行控件读数解析成值对象；读不出来时只保留错误，不抛给调用方。

    控件读数正常是三个浮点数。这里仍收窄长度与类型：预览由 ``valueChanged`` 直接触发，
    让异常落在 Qt 槽里只会变成一条难以定位的堆栈；转为该行的具体文案更符合“非法输入
    显示具体错误”的显示契约。步长/起止顺序、非有限数值仍由共享值对象判定，不在这里
    重复规则。
    """
    try:
        parts = tuple(plan)
    except TypeError:
        return _PlanReading(error=f"第 {number} 段计划读数格式不正确")
    if len(parts) != 3:
        return _PlanReading(
            error=(
                f"第 {number} 段计划需要起始/步长/终止三个读数，"
                f"收到 {len(parts)} 个"
            )
        )
    try:
        start, step, stop = (float(part) for part in parts)
    except (TypeError, ValueError) as failure:
        return _PlanReading(error=f"第 {number} 段计划读数不是有效数字：{failure}")
    try:
        sweep = PowerSweep(PowerDbm(start), PowerDbm(stop), Decibels(step))
    except _VALUE_ERRORS as failure:
        return _PlanReading(start, step, stop, error=str(failure))
    return _PlanReading(start, step, stop, sweep)


def _read_limit(max_input_power) -> tuple[float | None, str | None]:
    """把 DUT 上限收窄成可比较的数值；非法值保留为用户文案。"""
    try:
        return float(PowerDbm(max_input_power).dbm), None
    except _VALUE_ERRORS as failure:
        return None, f"DUT 最大输入功率不是有效数值：{failure}"


def _single_sweep_points(
    sweep: PowerSweep, max_input_power: float
) -> tuple[float, ...] | None:
    """用共享入口生成单段目标点；整段在有效上限之上时返回 ``None``（合法跳过）。

    ``max_input_power`` 已由 :func:`_read_limit` 收窄为有限数值，因此这里只可能遇到
    共享入口对“起始值高于有效上限”的拒绝；其他失败属于调用契约破坏，不能改写成
    “没有目标点”。
    """
    try:
        grid = target_input_power_grid(sweep, PowerDbm(max_input_power))
    except AmplifierInputError:
        return None
    return tuple(point.dbm for point in grid)


def _row_preview(
    number: int,
    reading: _PlanReading,
    *,
    max_input_power: float,
    row_error: str | None,
    adjacency_error: str | None,
) -> PowerScanRowPreview:
    """一行预览：行内数值错误与相邻计划连续性错误优先于点位文本。"""
    if row_error is not None:
        return _error_preview(number, row_error)
    if reading.sweep is None:
        # 解析结果自洽：没有错误就一定有值对象。这里只兜底，避免预览槽函数抛出。
        return _error_preview(number, reading.error or "该段计划读数不可用")

    start, step, stop = reading.start, reading.step, reading.stop
    points = _single_sweep_points(reading.sweep, max_input_power) or ()

    if adjacency_error is not None:
        # 连续性错误标在后一段：用户一眼能看到“和第 N 段哪里接不上”。本段点位仍放在
        # tooltip 里，避免为了显示错误而完全丢失这块区域会扫出什么。
        return PowerScanRowPreview(
            text=adjacency_error,
            tone="warning",
            tooltip=_row_tooltip(number, start, step, stop, points, adjacency_error),
            points=points,
            error=adjacency_error,
        )

    if not points:
        note = (
            "不产生目标点：起始功率高于 DUT 最大输入 "
            f"{_format_power(max_input_power)} dBm"
        )
        return PowerScanRowPreview(
            text=note,
            tone="muted",
            tooltip=_row_tooltip(number, start, step, stop, ()),
            points=(),
        )

    text = _compact_points(points)
    if max_input_power < stop:
        # 这一段被 DUT 硬上限截断：显示实际会到达的最后一点，而不是用户填写的终点。
        text += (
            f"（受 DUT 最大输入 {_format_power(max_input_power)} dBm 限制，"
            f"止于 {_format_power(points[-1])} dBm）"
        )
    return PowerScanRowPreview(
        text=text,
        tone="muted",
        tooltip=_row_tooltip(number, start, step, stop, points),
        points=points,
    )


def _summary(
    segment_count: int,
    last_stop: float | None,
    target_points: Sequence[float],
    errors: Sequence[str],
    max_input_power: float | None,
) -> tuple[str, str, str]:
    """汇总标签：合法时给出目标点总数与有效上限，非法时给出问题数与第一条原因。"""
    if segment_count == 0:
        return "DUT 功率扫描：尚未配置计划", "warning", "至少需要一段功率扫描计划"
    if errors or not target_points:
        first = errors[0] if errors else "无法生成目标功率点"
        text = f"DUT 功率扫描：{segment_count} 段，{len(errors) or 1} 个问题；{first}"
        return text, "warning", "\n".join(errors) or first
    first_point = _format_power(target_points[0])
    last_point = _format_power(target_points[-1])
    text = (
        f"DUT 功率扫描：{segment_count} 段，共 {len(target_points)} 个目标点"
        f"（{first_point} → {last_point} dBm）"
    )
    if (
        max_input_power is not None
        and last_stop is not None
        and max_input_power < last_stop
    ):
        text += f"；受 DUT 最大输入 {_format_power(max_input_power)} dBm 截断"
    return text, "muted", f"共 {len(target_points)} 点：{_full_points(target_points)}"


def preview_power_scan_plans(
    plans: Iterable[Sequence[float]],
    max_input_power,
) -> PowerScanPreview:
    """把页面行读数整理成逐行预览与汇总。

    ``plans`` 的每一项按控件显示顺序是 ``(起始功率, 步长, 终止功率)``，顺序就是执行
    顺序。函数不排序、不修补、不删除任何段：非法行保留错误，空档/重叠保留原文，
    是否允许保存仍由页面校验决定。

    所有点位与连续性判断都经 ``application.dto`` 的共享入口；本函数只负责把结果
    排版成文本并决定语义色。任何一行读不出来都只影响该行的错误文案。
    """
    readings = [
        _read_plan(number, plan) for number, plan in enumerate(plans, start=1)
    ]

    limit, limit_problem = _read_limit(max_input_power)
    if limit_problem is not None:
        # 上限本身不可用时，每一段都无法回答“会扫出什么”：统一显示同一个原因，
        # 不再按可见行给出看似正常的点数。
        rows = tuple(
            _error_preview(number, limit_problem)
            for number in range(1, len(readings) + 1)
        )
        summary, summary_tone, summary_tooltip = _summary(
            len(readings), None, (), (limit_problem,), None
        )
        return PowerScanPreview(
            rows=rows,
            errors=(limit_problem,),
            summary=summary,
            summary_tone=summary_tone,
            summary_tooltip=summary_tooltip,
        )

    adjacency_errors: dict[int, str] = {}
    for index in range(len(readings) - 1):
        previous = readings[index].sweep
        following = readings[index + 1].sweep
        if previous is None or following is None:
            continue
        try:
            validate_power_scan_schedule(
                PowerScanSchedule(plans=(previous, following)),
                # 逐对校验仍用共享规则；偏移让错误里的编号保持为完整列表的行号。
                first_plan_number=index + 1,
            )
        except AmplifierInputError as failure:
            # 校验把重叠加当成“后一段的问题”，因此错误归属后一段。
            adjacency_errors[index + 1] = str(failure)

    target_points: tuple[float, ...] = ()
    schedule_error: str | None = None
    if (
        readings
        and not adjacency_errors
        and all(reading.sweep is not None for reading in readings)
    ):
        try:
            target_points = tuple(
                point.dbm
                for point in target_input_power_grid(
                    PowerScanSchedule(
                        plans=tuple(reading.sweep for reading in readings)
                    ),
                    PowerDbm(limit),
                )
            )
        except _VALUE_ERRORS as failure:
            # 对合法单段集合，共享入口在这里拒绝的原因就是“首个目标点高于有效上限”：
            # 它描述整份计划的起点，归到第一行，后续行仍按各自读数展示。
            schedule_error = str(failure)

    rows: list[PowerScanRowPreview] = []
    errors: list[str] = []
    for index, reading in enumerate(readings):
        row_error = reading.error
        if row_error is None and index == 0:
            row_error = schedule_error
        preview = _row_preview(
            index + 1,
            reading,
            max_input_power=limit,
            row_error=row_error,
            adjacency_error=adjacency_errors.get(index),
        )
        rows.append(preview)
        if preview.error:
            errors.append(preview.error)

    last_stop = next(
        (
            reading.stop
            for reading in reversed(readings)
            if reading.stop is not None
        ),
        None,
    )
    summary, summary_tone, summary_tooltip = _summary(
        len(readings), last_stop, target_points, errors, limit
    )
    return PowerScanPreview(
        rows=tuple(rows),
        target_points=target_points,
        errors=tuple(errors),
        summary=summary,
        summary_tone=summary_tone,
        summary_tooltip=summary_tooltip,
    )
