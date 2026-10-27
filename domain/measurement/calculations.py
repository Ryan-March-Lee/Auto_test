"""纯测量领域计算：线损、功率补偿、驱动映射和测量指标。

技术债步骤 13 的单位边界：本模块的纯函数只接受
:mod:`domain.measurement.values` 的值对象，不接受裸浮点数——GHz/Hz、dBm/W、dB/%
的换算在值对象里完成，错误单位无法静默进入计算。内部数值计算与既有公式逐位一致，
本模块不改变任何公式结果。

线损拓扑由 :class:`~domain.measurement.values.CableLossProfile` 表达（线①/线②相等、
线③/线④相等），字符串频率键与 ``cable1`` 等字段名只出现在值对象和结果文档转换处。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np

from domain.measurement.values import (
    CableLossProfile,
    CableLossTable,
    CompressionPoint,
    CompressionResult,
    Decibels,
    DriverOutputTable,
    Frequency,
    Percent,
    PowerDbm,
    PowerWatt,
    _require,
)

__all__ = [
    "calculate_cable_losses", "compensate_amplifier_output_power",
    "compensate_driver_output_power", "interpolate_driver_output_power",
    "calculate_dut_input_power", "calculate_gain", "calculate_efficiency",
    "calculate_compression_result",
]


def _sequence(values: Any, expected: type, label: str) -> tuple[Any, ...]:
    """把扫描列收敛为值对象序列；裸数值或其他单位一律拒绝。"""
    if isinstance(values, (str, bytes, Mapping)):
        raise TypeError(f"{label} 必须是 {expected.__name__} 序列")
    try:
        items = tuple(values)
    except TypeError:
        raise TypeError(f"{label} 必须是 {expected.__name__} 序列") from None
    for index, item in enumerate(items):
        _require(item, expected, f"{label}[{index}]")
    return items


def calculate_cable_losses(path1_loss: Decibels,
                           path2_loss: Decibels,
                           attenuator_value: Decibels) -> CableLossProfile:
    """根据路径1、路径2测量损耗和衰减器值计算各线缆分摊损耗。

    公式（与历史线损测量入口一致，数值不变）::

        cable12_loss = (path1_loss - attenuator_value) / 2
        cable34_loss = (path2_loss - path1_loss) / 2

    线①和线②假设相等，线③和线④假设相等；所有输入输出都是 dB 值对象。

    Args:
        path1_loss: 路径1总损耗（线①+衰减器+线②）。
        path2_loss: 路径2总损耗（线①+线③+线④+衰减器+线②）。
        attenuator_value: 衰减器损耗值。

    Returns:
        线损拓扑 :class:`CableLossProfile`（含 ``cable1`` 至 ``cable4`` 与两条路径总损耗）。
    """
    path1_db = _require(path1_loss, Decibels, "path1_loss").db
    path2_db = _require(path2_loss, Decibels, "path2_loss").db
    attenuator_db = _require(attenuator_value, Decibels, "attenuator_value").db

    cable12_loss = (path1_db - attenuator_db) / 2
    cable34_loss = (path2_db - path1_db) / 2

    return CableLossProfile(
        cable1=Decibels(cable12_loss),
        cable2=Decibels(cable12_loss),
        cable3=Decibels(cable34_loss),
        cable4=Decibels(cable34_loss),
        total_path1=path1_loss,
        total_path2=path2_loss,
    )


def compensate_amplifier_output_power(measured_power: PowerDbm,
                                      frequency: Frequency,
                                      loss_table: CableLossTable,
                                      attenuator_value: Decibels) -> PowerDbm:
    """补偿 DUT 输出端到频谱仪之间的线损。

    路径: DUT -> 线④ -> 衰减器 -> 线② -> 频谱仪。

    公式（数值不变）::

        actual_power = measured_power + cable4 + attenuator + cable2

    Args:
        measured_power: 频谱仪读数。
        frequency: 测试频率（用于查线损表）。
        loss_table: 按频率索引的线损表。
        attenuator_value: 衰减器损耗值。

    Returns:
        补偿后的 DUT 实际输出功率。
    """
    measured = _require(measured_power, PowerDbm, "measured_power")
    loss = _require(loss_table, CableLossTable, "loss_table").profile_at(
        _require(frequency, Frequency, "frequency")
    ).dut_output_path_loss(_require(attenuator_value, Decibels, "attenuator_value"))
    return PowerDbm(measured.dbm + loss.db)


def compensate_driver_output_power(measured_power: PowerDbm,
                                   frequency: Frequency,
                                   loss_table: CableLossTable,
                                   attenuator_value: Decibels) -> PowerDbm:
    """补偿驱动功放输出端到频谱仪之间的线损。

    路径: 驱动输出 -> 线③ -> 衰减器 -> 线② -> 频谱仪。

    公式（数值不变）::

        actual_power = measured_power + attenuator + cable2

    与 :func:`compensate_amplifier_output_power` 的区别：驱动功放输出端不经过线④，
    因此不补偿 ``cable4``。

    Args:
        measured_power: 频谱仪读数。
        frequency: 测试频率（用于查线损表）。
        loss_table: 按频率索引的线损表。
        attenuator_value: 衰减器损耗值。

    Returns:
        补偿后的驱动功放实际输出功率。
    """
    measured = _require(measured_power, PowerDbm, "measured_power")
    loss = _require(loss_table, CableLossTable, "loss_table").profile_at(
        _require(frequency, Frequency, "frequency")
    ).driver_output_path_loss(_require(attenuator_value, Decibels, "attenuator_value"))
    return PowerDbm(measured.dbm + loss.db)


# ---------------------------------------------------------------------------
# 阶段 2：驱动插值和 DUT 输入功率
# ---------------------------------------------------------------------------

def interpolate_driver_output_power(frequency: Frequency,
                                    sg_power: PowerDbm,
                                    driver_mapping: DriverOutputTable) -> PowerDbm:
    """按驱动映射执行线性插值，保持 ``np.interp`` 端点行为。

    映射点已在 :class:`DriverOutputTable` 构造时规范化为浮点数并按输入功率升序排序。

    保留现有行为：
        - 乱序键自动排序。
        - 范围外输入采用端点值（``np.interp`` 默认行为）。
        - 重复输入点保留 NumPy 当前行为。

    Args:
        frequency: 测试频率。
        sg_power: 信号源输出功率。
        driver_mapping: 驱动映射表。

    Returns:
        插值后的驱动功放输出功率。
    """
    return _require(driver_mapping, DriverOutputTable, "driver_mapping").interpolate(
        _require(frequency, Frequency, "frequency"),
        _require(sg_power, PowerDbm, "sg_power"),
    )


def calculate_dut_input_power(sg_power: PowerDbm,
                              frequency: Frequency,
                              loss_table: CableLossTable,
                              driver_mapping: DriverOutputTable | None) -> PowerDbm:
    """统一有驱动和无驱动两条 DUT 输入功率路径。

    - 有驱动时：通过插值计算驱动功放输出功率，即 DUT 输入功率。
    - 无驱动时：DUT 输入功率 = sg_power - cable1。

    Args:
        sg_power: 信号源输出功率。
        frequency: 测试频率。
        loss_table: 按频率索引的线损表。
        driver_mapping: 驱动映射表；无驱动（``None`` 或空表）时走无驱动路径。

    Returns:
        DUT 输入功率。
    """
    source_power = _require(sg_power, PowerDbm, "sg_power")
    if driver_mapping is not None:
        table = _require(driver_mapping, DriverOutputTable, "driver_mapping")
        if not table.is_empty():
            return table.interpolate(
                _require(frequency, Frequency, "frequency"), source_power
            )
    cable_loss_1 = _require(loss_table, CableLossTable, "loss_table").profile_at(
        _require(frequency, Frequency, "frequency")
    ).dut_input_path_loss()
    return PowerDbm(source_power.dbm - cable_loss_1.db)


# ---------------------------------------------------------------------------
# 阶段 3：增益、效率和压缩点
# ---------------------------------------------------------------------------

def calculate_gain(output_power: PowerDbm, input_power: PowerDbm) -> Decibels:
    """计算增益（dB）。

    Args:
        output_power: DUT 输出功率。
        input_power: DUT 输入功率。

    Returns:
        增益。
    """
    output = _require(output_power, PowerDbm, "output_power")
    input_value = _require(input_power, PowerDbm, "input_power")
    return Decibels(output.dbm - input_value.dbm)


def calculate_efficiency(output_power: PowerDbm, dc_power: PowerWatt) -> Percent:
    """计算效率。

    公式（数值不变）::

        efficiency = 10 ** ((output_power_dbm - 30) / 10) / dc_power * 100

    DC 功耗不为正时返回 0 %。

    Args:
        output_power: DUT 输出功率。
        dc_power: DC 功耗。

    Returns:
        效率。
    """
    output = _require(output_power, PowerDbm, "output_power")
    dc = _require(dc_power, PowerWatt, "dc_power")
    if dc.watts <= 0:
        return Percent(0)
    return Percent.from_ratio(output.to_watt().watts / dc.watts)


def calculate_compression_result(gains: Iterable[Decibels],
                                 input_powers: Iterable[PowerDbm],
                                 output_powers: Iterable[PowerDbm],
                                 efficiencies: Iterable[Percent],
                                 sg_powers: Iterable[PowerDbm],
                                 compression_value: Decibels,
                                 small_gain_points: int = 3) -> CompressionResult:
    """计算压缩点结果。

    以扫描前 ``small_gain_points`` 个有效增益点的平均值为小信号增益，
    压缩量为 ``small_signal_gain - gain``，取最接近目标值的点。

    空扫描点是无效输入：直接抛 :class:`ValueError`，不再依赖 ``np.argmin()`` 的
    隐式异常。各列长度必须一致。

    Args:
        gains: 增益列。
        input_powers: DUT 输入功率列。
        output_powers: DUT 输出功率列。
        efficiencies: 效率列。
        sg_powers: 信号源功率列。
        compression_value: 目标压缩量。
        small_gain_points: 小信号增益取平均的点数，默认 3。

    Returns:
        :class:`~domain.measurement.values.CompressionResult`；``to_mapping()`` 输出
        与既有结果文档一致的字典（``compression_point`` / ``small_signal_gain`` /
        ``compression_achieved`` / ``max_compression``）。
    """
    gain_values = _sequence(gains, Decibels, "gains")
    input_values = _sequence(input_powers, PowerDbm, "input_powers")
    output_values = _sequence(output_powers, PowerDbm, "output_powers")
    efficiency_values = _sequence(efficiencies, Percent, "efficiencies")
    sg_values = _sequence(sg_powers, PowerDbm, "sg_powers")
    target = _require(compression_value, Decibels, "compression_value")

    lengths = {
        len(gain_values),
        len(input_values),
        len(output_values),
        len(efficiency_values),
        len(sg_values),
    }
    if len(lengths) != 1:
        raise ValueError("压缩点计算要求增益、输入/输出功率、效率和信号源功率列等长")
    if not gain_values:
        raise ValueError("压缩点计算至少需要一个有效扫描点")

    gains_arr = np.array([value.db for value in gain_values])

    small_signal_gain = (
        float(np.mean(gains_arr[:small_gain_points]))
        if len(gains_arr) >= small_gain_points
        else float(gains_arr[0])
    )

    compression_gains = small_signal_gain - gains_arr
    compression_achieved = bool(np.any(compression_gains >= target.db))

    idx = int(np.abs(compression_gains - target.db).argmin())

    compression_point = CompressionPoint(
        input_power=input_values[idx],
        output_power=output_values[idx],
        gain=gain_values[idx],
        efficiency=efficiency_values[idx],
        compression=Decibels(float(compression_gains[idx])),
        sg_power=sg_values[idx],
    )

    return CompressionResult(
        compression_point=compression_point,
        small_signal_gain=Decibels(small_signal_gain),
        compression_achieved=compression_achieved,
        max_compression=Decibels(float(compression_gains.max())),
    )
