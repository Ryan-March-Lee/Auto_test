"""
measurement_calculations.py
===========================

测量纯计算函数模块。

本模块集中放置跨模块共享且无仪器依赖的计算函数。
不导入 InstrumentControl、Qt 或项目配置文件，只接收数字、映射字典和列表。

阶段 1-3 实现：
    - calculate_cable_losses()
    - compensate_amplifier_output_power()
    - compensate_driver_output_power()
    - interpolate_driver_output_power()
    - calculate_dut_input_power()
    - calculate_gain()
    - calculate_efficiency()
    - calculate_compression_result()
"""

import numpy as np

def calculate_cable_losses(path1_loss: float,
                           path2_loss: float,
                           attenuator_value: float) -> dict:
    """根据路径1、路径2测量损耗和衰减器值计算各线缆分摊损耗。

    公式（与现有 cable_loss_measurement.py 和 enhanced_workers.py 一致）::

        cable12_loss = (path1_loss - attenuator_value) / 2
        cable34_loss = (path2_loss - path1_loss) / 2

    线①和线②假设相等，线③和线④假设相等。

    Args:
        path1_loss: 路径1总损耗（线①+衰减器+线②），单位 dB。
        path2_loss: 路径2总损耗（线①+线③+线④+衰减器+线②），单位 dB。
        attenuator_value: 衰减器损耗值，单位 dB。

    Returns:
        包含 ``cable1``、``cable2``、``cable3``、``cable4``、
        ``total_path1``、``total_path2`` 六个键的字典。
    """
    cable12_loss = (path1_loss - attenuator_value) / 2
    cable34_loss = (path2_loss - path1_loss) / 2

    return {
        'cable1': cable12_loss,
        'cable2': cable12_loss,
        'cable3': cable34_loss,
        'cable4': cable34_loss,
        'total_path1': path1_loss,
        'total_path2': path2_loss,
    }


def compensate_amplifier_output_power(measured_power: float,
                                      frequency: float,
                                      loss_data: dict,
                                      attenuator_value: float) -> float:
    """补偿 DUT 输出端到频谱仪之间的线损。

    路径: DUT -> 线④ -> 衰减器 -> 线② -> 频谱仪。

    公式（与 AmplifierMeasurement.calculate_actual_power() 一致）::

        actual_power = measured_power + cable4 + attenuator + cable2

    Args:
        measured_power: 频谱仪读数，单位 dBm。
        frequency: 测试频率，单位 GHz（用于查 loss_data）。
        loss_data: 线损结果字典，键为字符串频率。
        attenuator_value: 衰减器损耗值，单位 dB。

    Returns:
        补偿后的 DUT 实际输出功率，单位 dBm。
    """
    freq_losses = loss_data[str(frequency)]
    loss_after_dut = (freq_losses['cable4'] +
                      attenuator_value + freq_losses['cable2'])
    return measured_power + loss_after_dut


def compensate_driver_output_power(measured_power: float,
                                   frequency: float,
                                   loss_data: dict,
                                   attenuator_value: float) -> float:
    """补偿驱动功放输出端到频谱仪之间的线损。

    路径: 驱动输出 -> 线③ -> 衰减器 -> 线② -> 频谱仪。

    公式（与 DriverPowerMapping.calculate_actual_power() 一致）::

        actual_power = measured_power + attenuator + cable2

    与 :func:`compensate_amplifier_output_power` 的区别：
    驱动功放输出端不经过线④，因此不补偿 ``cable4``。

    Args:
        measured_power: 频谱仪读数，单位 dBm。
        frequency: 测试频率，单位 GHz（用于查 loss_data）。
        loss_data: 线损结果字典，键为字符串频率。
        attenuator_value: 衰减器损耗值，单位 dB。

    Returns:
        补偿后的驱动功放实际输出功率，单位 dBm。
    """
    freq_losses = loss_data[str(frequency)]
    loss_after_cable3 = (attenuator_value + freq_losses['cable2'])
    return measured_power + loss_after_cable3


# ---------------------------------------------------------------------------
# 阶段 2：驱动插值和 DUT 输入功率
# ---------------------------------------------------------------------------

def interpolate_driver_output_power(frequency: float,
                                    sg_power: float,
                                    driver_mapping: dict) -> float:
    """按 JSON 映射排序后执行线性插值，保持 np.interp 端点行为。

    从 ``driver_mapping`` 中取出指定频率的子映射，将 JSON 字符串键值
    转换为浮点数，按输入功率升序排序后调用 ``np.interp``。

    保留现有行为：
        - 乱序键自动排序。
        - 范围外输入采用端点值（``np.interp`` 默认行为）。
        - 重复输入点保留 NumPy 当前行为。

    Args:
        frequency: 测试频率，单位 GHz。
        sg_power: 信号源输出功率，单位 dBm。
        driver_mapping: 驱动映射字典，外层键为字符串频率，
            内层键为字符串信号源功率，值为驱动输出功率。

    Returns:
        插值后的驱动功放输出功率，单位 dBm。
    """
    freq_mapping = driver_mapping[str(frequency)]

    input_powers = np.array(list(map(float, freq_mapping.keys())))
    output_powers = np.array(list(map(float, freq_mapping.values())))

    sorted_indices = np.argsort(input_powers)
    input_powers_sorted = input_powers[sorted_indices]
    output_powers_sorted = output_powers[sorted_indices]

    return np.interp(sg_power, input_powers_sorted, output_powers_sorted)


def calculate_dut_input_power(sg_power: float,
                              frequency: float,
                              loss_data: dict,
                              driver_mapping: dict | None) -> float:
    """统一有驱动和无驱动两条 DUT 输入功率路径。

    - 有驱动时：通过插值计算驱动功放输出功率，即 DUT 输入功率。
    - 无驱动时：DUT 输入功率 = sg_power - cable1。

    Args:
        sg_power: 信号源输出功率，单位 dBm。
        frequency: 测试频率，单位 GHz。
        loss_data: 线损结果字典（``cable_losses`` 子字典），键为字符串频率。
        driver_mapping: 驱动映射字典，无驱动时传 ``None``。

    Returns:
        DUT 输入功率，单位 dBm。
    """
    if driver_mapping:
        return interpolate_driver_output_power(frequency, sg_power,
                                               driver_mapping)
    cable_loss_1 = loss_data[str(frequency)]['cable1']
    return sg_power - cable_loss_1


# ---------------------------------------------------------------------------
# 阶段 3：增益、效率和压缩点
# ---------------------------------------------------------------------------

def calculate_gain(output_power_dbm: float, input_power_dbm: float) -> float:
    """计算增益。

    Args:
        output_power_dbm: DUT 输出功率，单位 dBm。
        input_power_dbm: DUT 输入功率，单位 dBm。

    Returns:
        增益，单位 dB。
    """
    return output_power_dbm - input_power_dbm


def calculate_efficiency(output_power_dbm: float, dc_power: float) -> float:
    """计算效率。

    公式::

        efficiency = 10 ** ((output_power_dbm - 30) / 10) / dc_power * 100

    DC 功耗不为正时返回 0。

    Args:
        output_power_dbm: DUT 输出功率，单位 dBm。
        dc_power: DC 功耗，单位 W。

    Returns:
        效率，单位 %。
    """
    if dc_power <= 0:
        return 0
    output_power_watts = 10 ** ((output_power_dbm - 30) / 10)
    return (output_power_watts / dc_power) * 100


def calculate_compression_result(gains: list[float],
                                 input_powers: list[float],
                                 output_powers: list[float],
                                 efficiencies: list[float],
                                 sg_powers: list[float],
                                 compression_value: float,
                                 small_gain_points: int = 3) -> dict:
    """计算压缩点结果。

    以扫描前 ``small_gain_points`` 个有效增益点的平均值为小信号增益，
    压缩量为 ``small_signal_gain - gain``，取最接近目标值的点。

    空列表行为：保留当前抛异常行为（``np.array([])`` 上调用
    ``argmin()`` 会抛 ``ValueError``），不静默改变。

    Args:
        gains: 增益列表，单位 dB。
        input_powers: DUT 输入功率列表，单位 dBm。
        output_powers: DUT 输出功率列表，单位 dBm。
        efficiencies: 效率列表，单位 %。
        sg_powers: 信号源功率列表，单位 dBm。
        compression_value: 目标压缩量，单位 dB。
        small_gain_points: 小信号增益取平均的点数，默认 3。

    Returns:
        包含以下键的字典::

            {
                'compression_point': {
                    'input_power', 'output_power', 'gain',
                    'efficiency', 'compression_dB', 'sg_power_at_compression'
                },
                'small_signal_gain': float,
                'compression_achieved': bool,
                'max_compression': float,
            }
    """
    gains_arr = np.array(gains)

    small_signal_gain = (
        np.mean(gains_arr[:small_gain_points])
        if len(gains_arr) >= small_gain_points
        else (gains_arr[0] if len(gains_arr) > 0 else 0)
    )

    compression_gains = small_signal_gain - gains_arr
    compression_achieved = bool(np.any(compression_gains >= compression_value))

    idx = np.abs(compression_gains - compression_value).argmin()

    compression_point_data = {
        'input_power': input_powers[idx],
        'output_power': output_powers[idx],
        'gain': gains[idx],
        'efficiency': efficiencies[idx],
        'compression_dB': compression_gains[idx],
        'sg_power_at_compression': sg_powers[idx],
    }

    return {
        'compression_point': compression_point_data,
        'small_signal_gain': small_signal_gain,
        'compression_achieved': compression_achieved,
        'max_compression': float(compression_gains.max()),
    }
