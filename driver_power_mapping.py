# --- START OF FILE driver_power_mapping.py ---

import json
import numpy as np
from typing import Dict, List, Optional
import time
from datetime import datetime
from instrument_control import InstrumentControl
from project_paths import CABLE_LOSS_FILE, CONFIG_FILE, PROJECT_ROOT, resolve_path
from measurement_calculations import compensate_driver_output_power
# from mock_instrument_control import MockInstrumentControl as InstrumentControl


class DriverPowerMapping:
    def __init__(self, config_path=None, loss_data_path=None):
        """初始化驱动功放功率映射测量类"""
        config_path = resolve_path(config_path, CONFIG_FILE)
        loss_data_path = resolve_path(loss_data_path, CABLE_LOSS_FILE)
        with open(config_path, 'r') as f:
            self.config = json.load(f)
            
        with open(loss_data_path, 'r') as f:
            self.loss_data = json.load(f)
            
        self.inst_ctrl = InstrumentControl(config_path)
        self.power_mapping: Dict[str, Dict[str, float]] = {}
        
    # --- MODIFIED: CRITICAL FIX in power calculation logic ---
    def calculate_actual_power(self, frequency: float, measured_power: float) -> float:
        """
        计算实际功率（补偿线损）。
        目标: "线③出来这个点的功率"。
        路径: SG -> 线① -> 驱动 -> 线③ -> 衰减器 -> 线② -> 频谱仪.

        兼容包装入口，实际计算委托给纯函数
        :func:`compensate_driver_output_power`。
        """
        attenuator_value = float(self.config['attenuator']['type'].replace('dB', ''))
        return compensate_driver_output_power(
            measured_power=measured_power,
            frequency=frequency,
            loss_data=self.loss_data['cable_losses'],
            attenuator_value=attenuator_value)


        
    def measure_power_mapping(self, frequency: float):
        """测量指定频率下的功率映射关系"""
        print(f"\nMeasuring power mapping at {frequency} GHz...")
        self.inst_ctrl.set_power(-40)
        self.inst_ctrl.set_frequency(frequency)
        self.inst_ctrl.set_center_frequency(frequency)
        self.inst_ctrl.set_span(10)
        
        start_power = self.config['signal_source']['start_power']
        stop_power = self.config['signal_source']['stop_power']
        step = self.config['signal_source']['step']
        
        self.power_mapping[str(frequency)] = {}
        
        self.inst_ctrl.rf_output_on()
        for input_power in np.arange(start_power, stop_power + step, step):
            self.inst_ctrl.set_power(input_power)
            time.sleep(5)
            
            measured_power = self.inst_ctrl.measure_power_with_average()
            actual_power = self.calculate_actual_power(frequency, measured_power)
            
            # Use string keys for JSON compatibility
            self.power_mapping[str(frequency)][str(input_power)] = actual_power
            
            print(f"Input: {input_power:.1f} dBm, Output: {actual_power:.1f} dBm")
            
        self.inst_ctrl.rf_output_off()
        
    def measure_all_frequencies(self):
        """测量所有配置频率下的功率映射关系"""
        try:
            print("Setting up driver amplifier power supplies...")
            self.inst_ctrl.setup_driver_amplifier_power()
            
            # --- MODIFIED: Use granular power control ---
            print("Powering on driver amplifier...")
            self.inst_ctrl.power_on_driver()
            
            for freq in self.config['test_frequencies']:
                self.measure_power_mapping(freq)
                
            self.save_results()
            
        except Exception as e:
            print(f"Error during measurement: {str(e)}")
        finally:
            print("Shutting down...")
            self.inst_ctrl.rf_output_off()
            # --- MODIFIED: Use granular power control ---
            self.inst_ctrl.power_off_driver()
            self.inst_ctrl.close_all()
            
    def save_results(self):
        """保存功率映射测量结果"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = PROJECT_ROOT / f'driver_power_mapping_{timestamp}.json'
        
        results = {
            'measurement_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'power_mapping': self.power_mapping,
            'config': {
                'start_power': self.config['signal_source']['start_power'],
                'stop_power': self.config['signal_source']['stop_power'],
                'step': self.config['signal_source']['step']
            }
        }
        
        with open(filename, 'w') as f:
            json.dump(results, f, indent=4)
        print(f"\nResults saved to {filename}")

    # --- MODIFIED: Removed plot_mapping_curves method ---

def main():
    """主函数"""
    try:
        # 提示用户连接硬件
        input("请将信号源连接到线缆①，线缆①连接驱动功放输入，驱动功放输出连接线缆③，线缆③连接衰减器，衰减器连接线缆②，线缆②连接频谱仪。然后按 Enter 继续...")
        mapping = DriverPowerMapping()
        mapping.measure_all_frequencies()
        print("\nDriver power mapping measurement completed successfully!")
    except Exception as e:
        print(f"\nError occurred: {str(e)}")

if __name__ == "__main__":
    main()

# --- END OF FILE driver_power_mapping.py ---
