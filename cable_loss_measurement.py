# --- START OF FILE cable_loss_measurement.py ---

import json
import time
from typing import Dict
from instrument_control import InstrumentControl
# from mock_instrument_control import MockInstrumentControl as InstrumentControl

class CableLossMeasurement:
    def __init__(self, config_path: str = "config.json"):
        """初始化线损测量类

        Args:
            config_path: 配置文件路径
        """
        with open(config_path, 'r') as f:
            self.config = json.load(f)

        self.inst_ctrl = InstrumentControl(config_path)

        self.attenuator_value = float(self.config['attenuator']['type'].replace('dB', ''))
        self.cable_losses: Dict[float, Dict[str, float]] = {}

    # def initialize_instruments(self):
    #     """初始化信号源和频谱仪的基本设置"""
    #     self.inst_ctrl.set_power(0)
    #     self.inst_ctrl.rf_output_off()
    #     print("Instruments initialized via InstrumentControl.")

    def measure_power(self, frequency: float) -> float:
        """测量指定频率下的功率"""
        self.inst_ctrl.set_center_frequency(frequency)
        self.inst_ctrl.set_span(10)
        power = self.inst_ctrl.measure_power_with_average()
        time.sleep(1)
        return power

    def measure_path_loss(self, frequency: float) -> float:
        self.inst_ctrl.set_frequency(frequency)
        self.inst_ctrl.set_power(0)
        self.inst_ctrl.rf_output_on()
        measured_power = self.measure_power(frequency)
        self.inst_ctrl.rf_output_off()
        return abs(0 - measured_power)

    # --- MODIFIED: The main measurement logic is now completely refactored ---
    def measure_all_frequencies(self):
        """测量所有配置频率下的线损，优化流程，减少硬件更换次数"""
        # self.initialize_instruments()

        test_frequencies = self.config['test_frequencies']
        path1_losses = {}
        path2_losses = {}

        # --- 第1步: 测量路径1在所有频率下的损耗 ---
        print("\n" + "=" * 50)
        input("第一步: 请连接路径1 (线①+衰减器+线②)，然后按 Enter 继续...")
        print("=" * 50)
        for freq in test_frequencies:
            print(f"正在测量路径1 @ {freq} GHz...")
            loss = self.measure_path_loss(freq)
            path1_losses[freq] = loss
            print(f"  -> 路径1损耗: {loss:.2f} dB")
        print("\n路径1所有频率测量完成！\n")

        # --- 第2步: 测量路径2在所有频率下的损耗 ---
        print("=" * 50)
        input("第二步: 请连接路径2 (线①+线③+线④+衰减器+线②)，然后按 Enter 继续...")
        print("=" * 50)
        for freq in test_frequencies:
            print(f"正在测量路径2 @ {freq} GHz...")
            loss = self.measure_path_loss(freq)
            path2_losses[freq] = loss
            print(f"  -> 路径2损耗: {loss:.2f} dB")
        print("\n路径2所有频率测量完成！\n")

        # --- 第3步: 计算并整理所有结果 ---
        print("=" * 50)
        print("正在计算所有线缆的最终损耗...")
        for freq in test_frequencies:
            p1_loss = path1_losses[freq]
            p2_loss = path2_losses[freq]

            # 计算线①和线②的损耗（假设它们相等）
            cable12_loss = (p1_loss - self.attenuator_value) / 2

            # 计算线③和线④的损耗（假设它们相等）
            cable34_loss = (p2_loss - p1_loss) / 2

            self.cable_losses[freq] = {
                'cable1': cable12_loss,
                'cable2': cable12_loss,
                'cable3': cable34_loss,
                'cable4': cable34_loss,
                'total_path1': p1_loss,
                'total_path2': p2_loss
            }
            print(f"  {freq} GHz: Cable1/2={cable12_loss:.2f} dB, Cable3/4={cable34_loss:.2f} dB")
        print("=" * 50)

        self.save_results()

    def save_results(self):
        """保存线损测量结果到JSON文件"""
        results = {
            'measurement_time': time.strftime('%Y-%m-%d %H:%M:%S'),
            'attenuator_value': self.attenuator_value,
            'cable_losses': {str(k): v for k, v in self.cable_losses.items()}  # 确保key是字符串
        }

        filename = 'cable_loss_results.json'
        with open(filename, 'w') as f:
            json.dump(results, f, indent=4)
        print(f"\n线损测量结果已保存至 '{filename}'")

    def close(self):
        """关闭仪器连接"""
        self.inst_ctrl.close_all()


def main():
    loss_measurement = None
    try:
        loss_measurement = CableLossMeasurement()
        loss_measurement.measure_all_frequencies()
        print("\n线损测量已成功完成!")
    except Exception as e:
        print(f"\n发生错误: {str(e)}")
    finally:
        if loss_measurement:
            loss_measurement.close()


if __name__ == "__main__":
    main()

# --- END OF FILE cable_loss_measurement.py ---