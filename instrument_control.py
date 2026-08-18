# --- START OF FILE instrument_control.py (CORRECTED) ---

import json
import pyvisa
import time
from typing import Dict, List, Union, Optional
from enum import Enum
from project_paths import CONFIG_FILE, resolve_path


class InstrumentControl:
    def __init__(self, config_path=None):
        """初始化仪器控制类"""
        config_path = resolve_path(config_path, CONFIG_FILE)
        with open(config_path, 'r') as f:
            self.config = json.load(f)

        self.rm = pyvisa.ResourceManager()
        self.instruments = {}
        self.power_supplies = {}
        self.initialize_all_instruments()

    # --- HELPER FUNCTION TO GET SCPI CHANNEL NUMBER ---
    def _get_channel_num(self, channel_str: str) -> str:
        """从 'CH1', 'CH2' 等字符串中提取数字"""
        return channel_str.replace("CH", "")

    def initialize_all_instruments(self):
        """初始化所有仪器（只连接启用的仪器）"""
        try:
            # 只连接启用的信号发生器
            sg_config = self.config['instruments']['signal_generator']
            if sg_config.get('enabled', True):
                self.signal_gen = self.rm.open_resource(sg_config['address'])
                self.initialize_signal_generator()
            else:
                self.signal_gen = None

            # 只连接启用的频谱分析仪
            sa_config = self.config['instruments']['spectrum_analyzer']
            if sa_config.get('enabled', True):
                self.spectrum = self.rm.open_resource(sa_config['address'])
                self.initialize_spectrum_analyzer()
            else:
                self.spectrum = None

            # 只连接启用的电源
            for ps_name, ps_config in self.config['instruments']['power_supplies'].items():
                if ps_config.get('enabled', True):
                    ps = self.rm.open_resource(ps_config['address'])
                    self.power_supplies[ps_name] = ps
                    self.initialize_power_supply(ps_name)
        except Exception as e:
            self.close_all()
            raise Exception(f"Failed to initialize instruments: {str(e)}")

    def initialize_signal_generator(self):
        self.signal_gen.write("*RST")
        self.signal_gen.write("*CLS")
        self.set_power(-50)
        self.rf_output_off()

    def initialize_spectrum_analyzer(self):
        self.spectrum.write("*RST")
        self.spectrum.write("*CLS")
        self.spectrum.write("BAND:RES 300kHz")
        self.spectrum.write("BAND:VID 1MHz")
        self.spectrum.write(":DISP:WIND:TRAC:Y:RLEV 20dBm")#参考电平大于信号的最大值
        self.spectrum.write(":DISP:WIND:TRAC:Y:RLEV:OFFS 0dB")
        self.spectrum.write(":POW:ATT:AUTO ON")
        self.spectrum.write(":POW:MIX:RANG -10dBm")#
        self.spectrum.write("CALC:MARK1:MAX")
        self.spectrum.write("CALC:MARK1:CPS ON")


    def initialize_power_supply(self, ps_name: str):
        """初始化指定电源的基本设置"""
        ps = self.power_supplies[ps_name]
        ps.write("*RST")
        ps.write("*CLS")
        ps_config = self.config['instruments']['power_supplies'][ps_name]
        for channel, settings in ps_config['channels'].items():
            # 先打开过压/过流保护功能
            self.set_voltage_protection_state(ps_name, channel, settings['voltage']['protection_enabled'])
            self.set_current_protection_state(ps_name, channel, settings['current']['protection_enabled'])
            # 再设置保护值
            if settings['voltage']['protection_enabled']:
                self.set_voltage_protection(ps_name, channel, settings['voltage']['protection'])
            if settings['current']['protection_enabled']:
                self.set_current_protection(ps_name, channel, settings['current']['protection'])

    # --- CORRECTED POWER SUPPLY METHODS ---

    def set_voltage(self, ps_name: str, channel: str, voltage: float):
        """设置指定电源通道的电压"""
        if ps_name not in self.power_supplies:
            raise Exception(f"Power supply {ps_name} is not connected")
        ch_num = self._get_channel_num(channel)
        # 使用 :SOURce<n>:VOLTage <value> 命令
        self.power_supplies[ps_name].write(f":SOURce{ch_num}:VOLTage {voltage}")

    def set_current(self, ps_name: str, channel: str, current: float):
        """设置指定电源通道的电流"""
        if ps_name not in self.power_supplies:
            raise Exception(f"Power supply {ps_name} is not connected")
        ch_num = self._get_channel_num(channel)
        # 使用 :SOURce<n>:CURRent <value> 命令
        self.power_supplies[ps_name].write(f":SOURce{ch_num}:CURRent {current}")

    def set_voltage_protection_state(self, ps_name: str, channel: str, enabled: bool):
        """设置指定电源通道的过压保护状态"""
        if ps_name not in self.power_supplies:
            raise Exception(f"Power supply {ps_name} is not connected")
        ch_num = self._get_channel_num(channel)
        state = "ON" if enabled else "OFF"
        # 使用 :SOURce<n>:VOLTage:PROTection:STATe <bool> 命令
        self.power_supplies[ps_name].write(f":SOURce{ch_num}:VOLTage:PROTection:STATe {state}")

    def set_voltage_protection(self, ps_name: str, channel: str, voltage: float):
        """设置指定电源通道的过压保护值"""
        if ps_name not in self.power_supplies:
            raise Exception(f"Power supply {ps_name} is not connected")
        ch_num = self._get_channel_num(channel)
        # 使用 :SOURce<n>:VOLTage:PROTection:LEVel <value> 命令
        self.power_supplies[ps_name].write(f":SOURce{ch_num}:VOLTage:PROTection {voltage}")

    def set_current_protection_state(self, ps_name: str, channel: str, enabled: bool):
        """设置指定电源通道的过流保护状态"""
        if ps_name not in self.power_supplies:
            raise Exception(f"Power supply {ps_name} is not connected")
        ch_num = self._get_channel_num(channel)
        state = "ON" if enabled else "OFF"
        # 使用 :SOURce<n>:CURRent:PROTection:STATe <bool> 命令
        self.power_supplies[ps_name].write(f":SOURce{ch_num}:CURRent:PROTection:STATe {state}")

    def set_current_protection(self, ps_name: str, channel: str, current: float):
        """设置指定电源通道的过流保护值"""
        if ps_name not in self.power_supplies:
            raise Exception(f"Power supply {ps_name} is not connected")
        ch_num = self._get_channel_num(channel)
        # 使用 :SOURce<n>:CURRent:PROTection:LEVel <value> 命令
        self.power_supplies[ps_name].write(f":SOURce{ch_num}:CURRent:PROTection {current}")

    def read_voltage(self, ps_name: str, channel: str) -> float:
        """读取指定电源通道的电压"""
        if ps_name not in self.power_supplies:
            raise Exception(f"Power supply {ps_name} is not connected")
        # 使用 :MEASure:VOLTage? <channel> 命令
        return float(self.power_supplies[ps_name].query(f":MEASure:VOLTage? {channel}"))

    def read_current(self, ps_name: str, channel: str) -> float:
        """读取指定电源通道的电流"""
        if ps_name not in self.power_supplies:
            raise Exception(f"Power supply {ps_name} is not connected")
        # 使用 :MEASure:CURRent? <channel> 命令
        return float(self.power_supplies[ps_name].query(f":MEASure:CURRent? {channel}"))

    def power_supply_on(self, ps_name: str, channel: str):
        """打开指定电源通道的输出"""
        if ps_name not in self.power_supplies:
            raise Exception(f"Power supply {ps_name} is not connected")
        # 使用 :OUTPut <channel>,ON 命令
        self.power_supplies[ps_name].write(f":OUTPut {channel},ON")

    def power_supply_off(self, ps_name: str, channel: str):
        """关闭指定电源通道的输出"""
        if ps_name not in self.power_supplies:
            raise Exception(f"Power supply {ps_name} is not connected")
        # 使用 :OUTPut <channel>,OFF 命令
        self.power_supplies[ps_name].write(f":OUTPut {channel},OFF")

    # --- END OF CORRECTED METHODS ---

    def setup_driver_amplifier_power(self):
        """设置驱动功放的电源参数"""
        driver_config = self.config['power_supply_assignment']['driver_amplifier']
        for supply_info in driver_config['supplies'].values():
            ps_name = supply_info['name']
            if ps_name and ps_name in self.power_supplies:  # 只处理已连接的电源
                for channel in supply_info['channel']:
                    channel_config = self.config['instruments']['power_supplies'][ps_name]['channels'][channel]
                    self.set_voltage(ps_name, channel, channel_config['voltage']['value'])
                    self.set_current(ps_name, channel, channel_config['current']['value'])

    def setup_dut_power(self):
        """设置待测功放的电源参数"""
        dut_config = self.config['power_supply_assignment']['dut_amplifier']
        for supply_info in dut_config['supplies'].values():
            ps_name = supply_info['name']
            if ps_name and ps_name in self.power_supplies:  # 只处理已连接的电源
                for channel in supply_info['channel']:
                    channel_config = self.config['instruments']['power_supplies'][ps_name]['channels'][channel]
                    self.set_voltage(ps_name, channel, channel_config['voltage']['value'])
                    self.set_current(ps_name, channel, channel_config['current']['value'])

    def power_on_driver(self):
        """仅打开驱动功放电源"""
        driver_config = self.config['power_supply_assignment']['driver_amplifier']
        for supply_info in driver_config['supplies'].values():
            ps_name = supply_info['name']
            if ps_name and ps_name in self.power_supplies:  # 只处理已连接的电源
                for channel in supply_info['channel']:
                    self.power_supply_on(ps_name, channel)
                    time.sleep(2)

    def power_off_driver(self):
        """仅关闭驱动功放电源"""
        driver_config = self.config['power_supply_assignment']['driver_amplifier']
        for supply_info in reversed(list(driver_config['supplies'].values())):
            ps_name = supply_info['name']
            if ps_name and ps_name in self.power_supplies:  # 只处理已连接的电源
                for channel in reversed(supply_info['channel']):
                    self.power_supply_off(ps_name, channel)
                    time.sleep(2)

    def power_on_dut(self):
        """仅打开待测功放电源"""
        dut_config = self.config['power_supply_assignment']['dut_amplifier']
        for supply_info in dut_config['supplies'].values():
            ps_name = supply_info['name']
            if ps_name and ps_name in self.power_supplies:  # 只处理已连接的电源
                for channel in supply_info['channel']:
                    self.power_supply_on(ps_name, channel)
                    time.sleep(1.5)

    def power_off_dut(self):
        """仅关闭待测功放电源"""
        dut_config = self.config['power_supply_assignment']['dut_amplifier']
        for supply_info in reversed(list(dut_config['supplies'].values())):
            ps_name = supply_info['name']
            if ps_name and ps_name in self.power_supplies:  # 只处理已连接的电源
                for channel in reversed(supply_info['channel']):
                    self.power_supply_off(ps_name, channel)
                    time.sleep(2)

    def _get_all_assigned_supplies(self) -> List[Dict]:
        """辅助函数：获取所有被分配的电源配置（驱动和DUT）"""
        all_supplies = []
        if self.config.get('driver_mode', {}).get('enabled'):
            all_supplies.extend(self.config['power_supply_assignment']['driver_amplifier']['supplies'].values())
        all_supplies.extend(self.config['power_supply_assignment']['dut_amplifier']['supplies'].values())
        return all_supplies

    def power_on_sequence(self):
        """
        按“先栅后漏”的专业顺序打开所有电源。
        假设: CH1为栅压, CH2为漏压。
        """
        print("开始上电序列 (先栅极，后漏极)...")
        all_supplies = self._get_all_assigned_supplies()

        # 1. 打开所有栅极电压 (CH1)
        print("  -> 正在打开所有栅极电源 (CH1)...")
        for supply_info in all_supplies:
            ps_name = supply_info['name']
            for ch in supply_info['channel']:
                if ch == 'CH1':
                    self.power_supply_on(ps_name, ch)
                    print(f"      {ps_name}-{ch} ON")
                    time.sleep(0.5)

        # 稳定延时
        print("  -> 栅极电源已稳定，等待1.5秒...")
        time.sleep(1.5)

        # 2. 打开所有漏极电压 (CH2)
        print("  -> 正在打开所有漏极电源 (CH2)...")
        for supply_info in all_supplies:
            ps_name = supply_info['name']
            for ch in supply_info['channel']:
                if ch == 'CH2':
                    self.power_supply_on(ps_name, ch)
                    print(f"      {ps_name}-{ch} ON")
                    time.sleep(0.5)

        print("上电序列完成。")

    def power_off_sequence(self):
        """
        按“先漏后栅”的安全顺序关闭所有电源。
        假设: CH1为栅压, CH2为漏压。
        """
        print("开始掉电序列 (先漏极，后栅极)...")
        all_supplies = self._get_all_assigned_supplies()

        # 1. 关闭所有漏极电压 (CH2)
        print("  -> 正在关闭所有漏极电源 (CH2)...")
        # 反向迭代以保证安全
        for supply_info in reversed(all_supplies):
            ps_name = supply_info['name']
            # 反向迭代通道
            for ch in reversed(supply_info['channel']):
                if ch == 'CH2':
                    self.power_supply_off(ps_name, ch)
                    print(f"      {ps_name}-{ch} OFF")
                    time.sleep(0.5)

        # 稳定延时
        print("  -> 漏极电源已关闭，等待2秒...")
        time.sleep(2)

        # 2. 关闭所有栅极电压 (CH1)
        print("  -> 正在关闭所有栅极电源 (CH1)...")
        for supply_info in reversed(all_supplies):
            ps_name = supply_info['name']
            for ch in reversed(supply_info['channel']):
                if ch == 'CH1':
                    self.power_supply_off(ps_name, ch)
                    print(f"      {ps_name}-{ch} OFF")
                    time.sleep(0.5)

        print("掉电序列完成。")


    # The rest of the file (Signal Gen, Spectrum Analyzer control) is assumed to be correct
    # for their respective instruments and remains unchanged.

    def rf_output_on(self):
        if self.signal_gen is None:
            raise Exception("Signal generator is not connected")
        self.signal_gen.write("OUTP:STAT ON")

    def rf_output_off(self):
        if self.signal_gen is None:
            raise Exception("Signal generator is not connected")
        self.signal_gen.write("OUTP:STAT OFF")
        self.signal_gen.write('POW -50dBm')
        time.sleep(0.2)

    def set_frequency(self, freq: float):
        if self.signal_gen is None:
            raise Exception("Signal generator is not connected")
        self.signal_gen.write(f"FREQ {freq}E9")

    def set_power(self, power: float):
        if self.signal_gen is None:
            raise Exception("Signal generator is not connected")
        self.signal_gen.write(f"POW:LEV {power}")

    def set_center_frequency(self, freq: float):
        if self.spectrum is None:
            raise Exception("Spectrum analyzer is not connected")
        self.spectrum.write(f"FREQ:CENT {freq}E9")

    def set_span(self, span: float):
        if self.spectrum is None:
            raise Exception("Spectrum analyzer is not connected")
        self.spectrum.write(f"FREQ:SPAN {span}E6")


    def read_peak_power(self) -> float:
        if self.spectrum is None:
            raise Exception("Spectrum analyzer is not connected")
        time.sleep(2)
        self.spectrum.write("CALC:MARK1:MAX")
        return float(self.spectrum.query("CALC:MARK1:Y?"))

    def measure_power_with_average(self, average_count: int = 16) -> float:
        if self.spectrum is None:
            raise Exception("Spectrum analyzer is not connected")
        power = None
        try:
            self.spectrum.write(":DET POSitive")

            power = self.read_peak_power()
        finally:
            return power


    def close_all(self):
        """关闭所有仪器连接。不管理电源状态。"""
        print("Closing all instrument connections...")
        try:
            self.rf_output_off()
        except Exception:
            pass

        for instrument in [self.signal_gen, self.spectrum] + list(self.power_supplies.values()):
            try:
                instrument.close()
            except Exception:
                pass
        try:
            self.rm.close()
        except Exception:
            pass
        print("Connections closed.")


def main():
    inst_ctrl = None
    try:
        inst_ctrl = InstrumentControl()
        print("Setting up power supplies...")
        inst_ctrl.setup_driver_amplifier_power()
        inst_ctrl.setup_dut_power()

        print("Testing power on sequence...")
        inst_ctrl.power_on_sequence()
        time.sleep(1)

        print("Testing signal generator...")
        inst_ctrl.set_frequency(2.4)
        inst_ctrl.set_power(-30)
        inst_ctrl.rf_output_on()
        time.sleep(1)

        print("Testing spectrum analyzer...")
        inst_ctrl.set_center_frequency(2.4)
        inst_ctrl.set_span(10)
        power = inst_ctrl.measure_power_with_average()
        print(f"Measured power: {power:.2f} dBm")

    except Exception as e:
        print(f"Error occurred: {str(e)}")
    finally:
        if inst_ctrl:
            print("Shutting down...")
            inst_ctrl.rf_output_off()
            inst_ctrl.power_off_sequence()
            inst_ctrl.close_all()


if __name__ == "__main__":
    main()

# --- END OF FILE instrument_control.py (CORRECTED) ---
