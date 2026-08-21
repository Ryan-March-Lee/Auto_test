# --- START OF FILE amplifier_measurement.py (REFACTORED BASED ON NEW LOGIC) ---

import json
import numpy as np
from typing import Dict, List, Optional, Tuple
import time
from datetime import datetime
from instrument_control import InstrumentControl 
from pathlib import Path
from project_paths import CABLE_LOSS_FILE, CONFIG_FILE, PROJECT_ROOT, resolve_path
from measurement_calculations import (
    compensate_amplifier_output_power,
    calculate_dut_input_power,
    calculate_gain,
    calculate_efficiency,
    calculate_compression_result,
)
from app_logging import get_logger
from measurement_lifecycle import cleanup_measurement
from result_storage import create_run_directory, load_json_result, new_run_id, save_json_result

logger = get_logger(__name__)


# --- ADDED: Custom JSON Encoder to handle NumPy types ---
class NumpyJSONEncoder(json.JSONEncoder):
    """
    自定义的JSON Encoder，可以处理NumPy的数据类型，防止序列化错误。
    """
    def default(self, obj):
        if isinstance(obj, (np.int_, np.intc, np.intp, np.int8,
                            np.int16, np.int32, np.int64, np.uint8,
                            np.uint16, np.uint32, np.uint64)):
            return int(obj)
        elif isinstance(obj, (np.float_, np.float16, np.float32, np.float64)):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist() # 将NumPy数组转换为Python列表
        return super(NumpyJSONEncoder, self).default(obj)

class AmplifierMeasurement:
    def __init__(self, config_path=None,
                 loss_data_path=None,
                 driver_mapping_path: Optional[str] = None,
                 run_id: Optional[str] = None):
        """初始化主功放测量类"""
        config_path = resolve_path(config_path, CONFIG_FILE)
        loss_data_path = resolve_path(loss_data_path, CABLE_LOSS_FILE)
        with open(config_path, 'r') as f:
            self.config = json.load(f)

        self.loss_data = load_json_result(loss_data_path)

        self.inst_ctrl = InstrumentControl(config_path)
        self.run_id = run_id or new_run_id()
        self.run_directory = None

        if self.config['driver_mode']['enabled']:
            if driver_mapping_path is None:
                driver_files = sorted(PROJECT_ROOT.glob('driver_power_mapping_*.json'), key=lambda p: p.stat().st_mtime)
                if not driver_files:
                    raise FileNotFoundError("驱动模式已开启，但未找到任何 'driver_power_mapping_*.json' 文件!")
                driver_mapping_path = str(driver_files[-1])
                print(f"自动加载最新的驱动映射文件: {driver_mapping_path}")

            self.driver_mapping = load_json_result(driver_mapping_path)['power_mapping']
        else:
            self.driver_mapping = None

        self.measurement_results: Dict[str, Dict] = {}

    def calculate_actual_power(self, frequency: float, measured_power: float) -> float:
        """计算DUT的实际输出功率（补偿线损）

        兼容包装入口，实际计算委托给纯函数
        :func:`compensate_amplifier_output_power`。
        """
        attenuator_loss = float(self.config['attenuator']['type'].replace('dB', ''))
        return compensate_amplifier_output_power(
            measured_power=measured_power,
            frequency=frequency,
            loss_data=self.loss_data['cable_losses'],
            attenuator_value=attenuator_loss)

    # --- NEW: Function to get driver's OUTPUT power based on SG input ---
    def get_driver_output_power(self, frequency: float, sg_power_input: float) -> float:
        """
        根据信号源的输入功率，通过插值正向计算驱动功放的输出功率。
        这个输出功率就是DUT的输入功率。

        兼容包装入口，实际计算委托给纯函数
        :func:`interpolate_driver_output_power`。
        """
        from measurement_calculations import interpolate_driver_output_power
        return interpolate_driver_output_power(
            frequency=frequency,
            sg_power=sg_power_input,
            driver_mapping=self.driver_mapping)

    # --- REFACTORED: The core power sweep logic is now completely changed ---
    def perform_power_sweep(self, frequency: float) -> Dict:
        """
        在指定频率下执行功率扫描，同时测量RF和DC参数。
        此版本假设config.json中的功率范围是针对信号源(SG)的。
        """
        print(f"\n开始在 {frequency} GHz 进行功率扫描...")
        logger.info("功率扫描开始: %s GHz", frequency)

        compression_type = self.config['compression_point']['type']
        compression_value = float(compression_type.replace('dB', ''))

        max_dut_input_power = self.config.get('dut_config', {}).get('max_input_power', float('inf'))
        if max_dut_input_power == float('inf'):
            print("警告：未在config.json中设置 'dut_config.max_input_power'，无输入功率保护。")
        else:
            print(f"  - DUT最大输入功率限制: {max_dut_input_power} dBm")

        self.inst_ctrl.set_power(-40)
        self.inst_ctrl.set_frequency(frequency) #应该是设置信号源的信号频率
        self.inst_ctrl.set_center_frequency(frequency)
        self.inst_ctrl.set_span(10)

        # 扫描范围是信号源的功率
        start_power_sg = self.config['signal_source']['start_power']
        stop_power_sg = self.config['signal_source']['stop_power']
        step_sg = self.config['signal_source']['step']

        sweep_data = {
            'input_power_dut': [], 'output_power_dut': [], 'gain': [],
            'sg_power': [],  # Also store sg_power for reference
            'voltages': [], 'currents': [], 'dc_power': [], 'efficiency': []
        }

        dut_supply_config = self.config['power_supply_assignment']['dut_amplifier']['supplies']

        self.inst_ctrl.rf_output_on() #打开信号源RF输出开关
        small_gain_points = 3
        sg_powers_all = np.arange(start_power_sg, stop_power_sg + step_sg, step_sg)
        small_signal_gains = []

        # 循环变量是信号源功率 (sg_power)
        for idx, sg_power in enumerate(sg_powers_all):

            #1. 计算DUT的实际输入功率
            dut_input_power = calculate_dut_input_power(
                sg_power=sg_power,
                frequency=frequency,
                loss_data=self.loss_data['cable_losses'],
                driver_mapping=self.driver_mapping)

            # --- 安全检查逻辑 ---
            if dut_input_power > max_dut_input_power:
                logger.warning(
                    "DUT 输入保护触发: %s GHz 计算输入功率 %.2f dBm 超过最大值 %.2f dBm，停止该频率扫描",
                    frequency, dut_input_power, max_dut_input_power)
                print(f"  - 保护！计算出的DUT输入功率 {dut_input_power:.2f} dBm "
                      f"超过了设定的最大值 {max_dut_input_power} dBm。")
                print(f"  - 在 {frequency} GHz 的扫描已停止以保护DUT。")
                break  # 立即退出当前频率的扫描循环

            # 2. 设置信号源功率
            self.inst_ctrl.set_power(sg_power)
            time.sleep(5) #设置完功率输出后等待直流源及频谱仪读数稳定


            # 3. 测量DUT输出功率
            measured_power = self.inst_ctrl.measure_power_with_average()
            actual_output_power = self.calculate_actual_power(frequency, measured_power)

            # 4. 测量DC功耗
            total_dc_power = 0
            v_reading, i_reading = {}, {}
            for supply_name, info in dut_supply_config.items():
                ps_name, channels = info['name'], info['channel']
                for ch in channels:
                    v = self.inst_ctrl.read_voltage(ps_name, ch)
                    i = self.inst_ctrl.read_current(ps_name, ch)
                    total_dc_power += v * i
                    v_reading[f"{supply_name}_{ch}"] = v
                    i_reading[f"{supply_name}_{ch}"] = i

            # 5. 计算各项指标
            efficiency = calculate_efficiency(actual_output_power, total_dc_power)
            gain = calculate_gain(actual_output_power, dut_input_power)

            # 6. 存储数据
            sweep_data['sg_power'].append(sg_power)
            sweep_data['input_power_dut'].append(dut_input_power)
            sweep_data['output_power_dut'].append(actual_output_power)
            sweep_data['gain'].append(gain)
            sweep_data['voltages'].append(v_reading)
            sweep_data['currents'].append(i_reading)
            sweep_data['dc_power'].append(total_dc_power)
            sweep_data['efficiency'].append(efficiency)

            print(f"SG Power: {sg_power:.1f}, DUT Pin: {dut_input_power:.2f}, Pout: {actual_output_power:.1f}, Gain: {gain:.1f}, Eff: {efficiency:.1f}%")
            if idx < small_gain_points:
                small_signal_gains.append(gain)

                # 第四个点及以后，实时检测压缩点
            if idx >= small_gain_points:
                small_signal_gain = np.mean(small_signal_gains)
                compression_gain = calculate_gain(
                    small_signal_gain, gain)
                if compression_gain >= compression_value:
                    logger.info("达到目标压缩点 %.1f dB，停止 %s GHz 扫描", compression_value, frequency)
                    print(f"达到目标压缩点 {compression_value} dB，停止扫描。")
                    break
        self.inst_ctrl.rf_output_off()
        logger.info("功率扫描结束: %s GHz (点数=%d)", frequency, len(sweep_data['gain']))

        # 7. 计算压缩点 (基于DUT的输入和增益)
        if not sweep_data['gain']:
            # 保留原同步入口在 compression_gains.max() 处抛出的 ValueError。
            np.array(sweep_data['gain']).max()

        comp_result = calculate_compression_result(
            gains=sweep_data['gain'],
            input_powers=sweep_data['input_power_dut'],
            output_powers=sweep_data['output_power_dut'],
            efficiencies=sweep_data['efficiency'],
            sg_powers=sweep_data['sg_power'],
            compression_value=compression_value,
            small_gain_points=small_gain_points)

        small_signal_gain = comp_result['small_signal_gain']
        compression_achieved = comp_result['compression_achieved']
        compression_point_data = comp_result['compression_point']

        if not compression_achieved:
            print(f"\n⚠️ 警告：未达到目标压缩点 {compression_value} dB，"
                  f"最大压缩仅为 {comp_result['max_compression']:.2f} dB。")

        return {
            'compression_type': compression_type,
            'compression_point': compression_point_data,
            'small_signal_gain': small_signal_gain,
            'sweep_data': sweep_data,
            'compression_achieved': compression_achieved
        }

    # measure_all_frequencies, save_results, main 等函数无需修改，因为它们调用的是顶层方法
    def measure_all_frequencies(self):
        """测量所有配置频率"""
        try:
            logger.info("主功放测量开始: 频率=%s", self.config['test_frequencies'])
            print("Setting up power supplies...")
            if self.config['driver_mode']['enabled']:
                self.inst_ctrl.setup_driver_amplifier_power()
            self.inst_ctrl.setup_dut_power()

            print("Powering on devices...")
            self.inst_ctrl.power_on_sequence()
            time.sleep(2)

            for freq in self.config['test_frequencies']:
                self.measurement_results[str(freq)] = self.perform_power_sweep(freq)

            self.save_results()

        except Exception as e:
            logger.exception("主功放测量失败: %s", e)
            print(f"Error during measurement: {e}")
            import traceback
            traceback.print_exc()
        finally:
            logger.info("主功放测量清理: RF 关闭、掉电序列、断开连接")
            print("Shutting down...")
            cleanup_measurement(self.inst_ctrl, power_cleanup=self.inst_ctrl.power_off_sequence)

    def save_results(self):
        """保存测量结果"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = PROJECT_ROOT / f'amplifier_measurement_{timestamp}.json'

        results = {
            'measurement_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'config': self.config,
            'results': self.measurement_results
        }

        if self.run_directory is None:
            self.run_directory = create_run_directory(self.run_id)
        run_filename = save_json_result(
            self.run_directory / filename.name,
            results,
            result_type="amplifier_measurement",
            encoder=NumpyJSONEncoder,
        )
        save_json_result(
            filename,
            results,
            result_type="amplifier_measurement",
            encoder=NumpyJSONEncoder,
        )
        logger.info("主功放结果已保存: 兼容路径=%s，运行路径=%s", filename, run_filename)
        print(f"\nResults saved to {filename}; archived to {run_filename}")


def main():
    """主函数"""
    try:
        input("请按测试要求连接好主功放测试链路，然后按 Enter 继续...")
        # 确保构造函数名是 __init__
        amp_measurement = AmplifierMeasurement()
        amp_measurement.measure_all_frequencies()
        print("\nAmplifier measurement completed successfully!")
    except Exception as e:
        print(f"\nError occurred: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()

# --- END OF FILE amplifier_measurement.py (REFACTORED BASED ON NEW LOGIC) ---
