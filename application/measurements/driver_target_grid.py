"""驱动模式目标表与正式 DUT 功率扫描计划的一致性校验（重构步骤 4.4）。

主功放驱动模式不再重新测量驱放，只消费驱动映射阶段已经生成的 ``target_points``：
目标 DUT 输入功率、反查源功率与实际驱放输出功率。因此目标表必须与本次运行配置的
DUT 功率扫描计划**逐点一致**：数量、顺序、测试频率和功率唯一性缺一不可。否则配置
页预览、驱动映射和正式扫描会消费不同的目标点，结果里的 ``input_power_dut`` /
``sg_power`` 语义也会随之漂移。

本模块只做纯校验，不读取配置文件、不接触仪器，也不排序或修补目标点。目标点的唯一
来源是 :attr:`AmplifierMeasurementInputs.target_input_powers`（构造期已由共享点位
生成器按计划顺序合并、去重并应用 DUT 上限）；目标表本身由
:class:`DriverMappingInput` 在读取边界保证非空、按频点唯一且源功率落在原始实测曲线
覆盖范围内。这里补上两者之间的交叉校验：

- 驱动映射包含未配置的测试频率：拒绝，说明目标表来自别的运行或旧配置；
- 某个配置频率缺少目标点：拒绝，不能把“部分映射”当作完整网格；
- 同一频点的目标点数量与配置不一致：拒绝，缺点或多点都不能进入正式扫描；
- 同一频点的目标输入功率重复：拒绝，同一个目标不能映射两次；
- 目标点顺序或功率与配置生成的目标 DUT 点不同：拒绝，不静默排序修复，避免正式
  测量按另一份点列执行。顺序按**扁平点列**比较，因此频点之间的先后也属于契约：
  把整个频点组调到别处同样会被拒绝，不会因为“重新按频率分组”而被掩盖。

校验失败统一抛 :class:`AmplifierInputError`（配置类失败），调用方应在打开 RF 或
执行任何测量动作之前完成校验。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType

from application.dto.amplifier_inputs import (
    AmplifierInputError,
    AmplifierMeasurementInputs,
)
from application.dto.measurement_results import DriverMappingInput, DriverMappingTargetPoint
from domain.measurement.values import Frequency

__all__ = ["require_driver_target_grid"]

# 目标点功率比较容差（dB）。目标表与配置网格来自同一个纯计算入口，正常写入/读取
# 后应完全相同；该容差只吸收 JSON 往返或不同格式化带来的极小数值噪声，不会放过
# 用户真实填写的错点、差点或重复点。
_TARGET_POWER_TOLERANCE_DB = 1e-9


def _same_power(left: float, right: float) -> bool:
    return abs(left - right) <= _TARGET_POWER_TOLERANCE_DB


def _duplicate_power(values: Sequence[float]) -> float | None:
    """返回第一个在容差内重复的目标功率；没有重复时返回 ``None``。

    排序后只比较相邻值：目标点数可能达到数千个，逐对比较会退化成 O(n²)。
    """
    ordered = sorted(values)
    for previous, value in zip(ordered, ordered[1:]):
        if _same_power(previous, value):
            return value
    return None


def _require_same_sequence(
    expected: Sequence[tuple[Frequency, float]],
    actual: Sequence[tuple[Frequency, float]],
) -> None:
    """逐点比较扁平目标点列，频率先后与功率顺序都不允许改变。

    只比较分组后的“每个频点内的功率”会漏掉频点整体的错位（例如 2 GHz 组排到
    1 GHz 组之前）；这里按配置顺序把频率和功率展开成一条点列，再逐项对齐。
    """
    if len(actual) != len(expected):
        relation = "缺少" if len(actual) < len(expected) else "多出"
        raise AmplifierInputError(
            f"驱动映射目标表的点数量与配置不一致：配置生成 {len(expected)} 个目标点，"
            f"目标表有 {len(actual)} 个（{relation} {abs(len(actual) - len(expected))} 个）。"
            "目标表必须完整覆盖本次 DUT 功率扫描计划，请重新执行驱动映射"
        )
    # 错误信息里的“第几个目标点”按该点所属频点内部的位置计数，而不是扁平点列的
    # 全局下标：多频点运行时全局下标会把 2 GHz 的第一个点说成“第 5 个”。
    positions: dict[Frequency, int] = {}
    for index, ((wanted_frequency, wanted), (got_frequency, got)) in enumerate(
        zip(expected, actual), start=1
    ):
        position = positions.get(got_frequency, 0) + 1
        positions[got_frequency] = position
        if wanted_frequency != got_frequency:
            raise AmplifierInputError(
                f"驱动映射目标表的第 {index} 个目标点属于测试频率 "
                f"{got_frequency.key} GHz，配置生成的目标点应属于 "
                f"{wanted_frequency.key} GHz。目标表必须与 DUT 功率扫描计划的"
                "频率和顺序逐点一致，请重新执行驱动映射"
            )
        if not _same_power(wanted, got):
            raise AmplifierInputError(
                f"驱动映射目标表 {got_frequency.key} GHz 的第 {position} 个目标点与"
                f"配置不一致：期望 {wanted:g} dBm，实际 {got:g} dBm。目标表必须与 "
                "DUT 功率扫描计划的数量、顺序和功率逐点一致，请重新执行驱动映射"
            )


def require_driver_target_grid(
    inputs: AmplifierMeasurementInputs,
    driver_input: DriverMappingInput | None,
) -> Mapping[Frequency, tuple[DriverMappingTargetPoint, ...]]:
    """校验驱动映射目标表与正式功率扫描计划一致，并返回按频点分组的点列。

    返回值按 ``inputs.frequencies`` 的顺序组织，每个频点的点列保持目标表里的原始
    顺序；主功放驱动模式直接按这个顺序下发，不再自行过滤或排序。无驱动模式不消费
    目标表，返回空映射。

    驱动模式启用而目标表缺失、或目标表与配置在频率/数量/顺序/功率上不一致时，
    在构造阶段就抛 :class:`AmplifierInputError`。因此不完整的旧映射、混入其他运行
    的频率或旧单计划目标表都不会进入硬件动作。
    """
    if not isinstance(inputs, AmplifierMeasurementInputs):
        raise TypeError(
            "require_driver_target_grid 的 inputs 必须是 "
            f"AmplifierMeasurementInputs，收到 {type(inputs).__name__}"
        )
    if not inputs.driver_enabled:
        # 无驱动模式按目标 DUT 输入反算源功率，不消费驱动映射；即使调用方多传了
        # 目标表也不会被使用，避免把驱动模式的目标表混入无驱动扫描。
        return MappingProxyType({})
    if driver_input is None:
        raise AmplifierInputError(
            "驱动模式启用时必须提供已完成的目标映射：缺少 target_points，"
            "不能回退到无驱动反算路径"
        )
    if not isinstance(driver_input, DriverMappingInput):
        raise AmplifierInputError(
            "驱动模式启用的目标映射必须是 DriverMappingInput，"
            f"收到 {type(driver_input).__name__}"
        )

    configured = inputs.frequencies
    configured_set = set(configured)
    expected_powers = tuple(point.dbm for point in inputs.target_input_powers)
    grouped: dict[Frequency, list[DriverMappingTargetPoint]] = {}
    actual_sequence: list[tuple[Frequency, float]] = []
    for point in driver_input.target_points:
        frequency = Frequency.from_document_key(point.frequency_ghz)
        if frequency not in configured_set:
            raise AmplifierInputError(
                f"驱动映射目标表包含未配置的测试频率 {frequency.key} GHz："
                "目标表与本次运行的 DUT 功率扫描计划不一致，请重新执行驱动映射"
            )
        grouped.setdefault(frequency, []).append(point)
        actual_sequence.append((frequency, point.target_input_power_dbm))

    # 先按频点检查覆盖与唯一性，错误信息能直接指出是哪个频率差点；随后再用扁平
    # 点列校验顺序，二者都通过才认为目标表就是本次配置生成的网格。
    for frequency in configured:
        points = tuple(grouped.get(frequency, ()))
        if not points:
            raise AmplifierInputError(
                f"驱动映射目标表缺少测试频率 {frequency.key} GHz 的目标点："
                f"配置生成 {len(expected_powers)} 个目标 DUT 输入点，目标表为空。"
                "目标表必须完整覆盖本次 DUT 功率扫描计划"
            )
        if len(points) != len(expected_powers):
            relation = "缺少" if len(points) < len(expected_powers) else "多出"
            raise AmplifierInputError(
                f"驱动映射目标表 {frequency.key} GHz 的目标点数量与配置不一致："
                f"配置生成 {len(expected_powers)} 个目标 DUT 输入点，目标表有 "
                f"{len(points)} 个（{relation} {abs(len(points) - len(expected_powers))} 个）。"
                "目标表必须完整覆盖本次 DUT 功率扫描计划，请重新执行驱动映射"
            )
        values = tuple(point.target_input_power_dbm for point in points)
        duplicate = _duplicate_power(values)
        if duplicate is not None:
            raise AmplifierInputError(
                f"驱动映射目标表 {frequency.key} GHz 的目标输入功率重复："
                f"{duplicate:g} dBm。每个目标 DUT 输入点只能出现一次"
            )

    expected_sequence = tuple(
        (frequency, power)
        for frequency in configured
        for power in expected_powers
    )
    _require_same_sequence(expected_sequence, tuple(actual_sequence))
    return MappingProxyType(
        {frequency: tuple(grouped[frequency]) for frequency in configured}
    )
