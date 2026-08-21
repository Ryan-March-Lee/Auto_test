"""
增强的工作线程类，支持实时数据更新和进度报告
"""

import time
import json
import numpy as np
from typing import Dict, Any, Optional
from pathlib import Path
from PySide6.QtCore import QThread, Signal, QObject

from instrument_control import InstrumentControl
from cable_loss_measurement import CableLossMeasurement
from driver_power_mapping import DriverPowerMapping
from amplifier_measurement import AmplifierMeasurement
from project_paths import CONFIG_FILE, resolve_path
from measurement_calculations import (
    calculate_cable_losses,
    calculate_dut_input_power,
    calculate_gain,
    calculate_efficiency,
    calculate_compression_result,
)
from app_logging import get_logger
from measurement_lifecycle import cleanup_measurement

logger = get_logger(__name__)


class EnhancedCableLossMeasurement(CableLossMeasurement):
    """增强的线损测量类，支持进度和消息回调，支持分步骤测量"""
    
    def __init__(self, config_path=None, progress_callback=None, message_callback=None):
        super().__init__(config_path)
        self.progress_callback = progress_callback
        self.message_callback = message_callback
        self.should_stop = False
        self.current_step = 1  # 当前测量步骤：1=路径1, 2=路径2
        self.path1_losses = {}
        self.step_pause_callback = None  # 步骤暂停回调
        
    def set_step_pause_callback(self, callback):
        """设置步骤暂停回调"""
        self.step_pause_callback = callback
        
    def emit_progress(self, value: int):
        if self.progress_callback:
            self.progress_callback(value)
            
    def emit_message(self, message: str):
        if self.message_callback:
            self.message_callback(message)
            
    def stop_measurement(self):
        logger.info("线损测量收到停止请求")
        self.should_stop = True
        
    def continue_to_step2(self):
        """继续执行第二步测量"""
        logger.info("线损测量继续进入路径2")
        self.current_step = 2
        self._measure_step2()
        
    def measure_all_frequencies(self):
        """开始分步骤的测量流程"""
        if self.should_stop:
            return
            
        self.current_step = 1
        self._measure_step1()
        
    def _measure_step1(self):
        """第一步：测量路径1"""
        test_frequencies = self.config['test_frequencies']
        
        self.emit_message("开始路径1测量...")
        self.emit_progress(0)
        
        for i, freq in enumerate(test_frequencies):
            if self.should_stop:
                return
                
            self.emit_message(f"测量路径1 @ {freq} GHz...")
            loss = self.measure_path_loss(freq)
            self.path1_losses[freq] = loss
            
            progress = int(((i + 1) / len(test_frequencies)) * 50)  # 第一步占50%
            self.emit_progress(progress)
            self.emit_message(f"路径1 @ {freq} GHz: {loss:.2f} dB")
            
        if self.should_stop:
            return
            
        self.emit_message("路径1测量完成！请更换连接到路径2...")
        # 通知GUI需要用户操作
        if self.step_pause_callback:
            self.step_pause_callback("请连接路径2 (线①+线③+线④+衰减器+线②)")
            
    def _measure_step2(self):
        """第二步：测量路径2"""
        if self.should_stop:
            return
            
        test_frequencies = self.config['test_frequencies']
        path2_losses = {}
        
        self.emit_message("开始路径2测量...")
        
        for i, freq in enumerate(test_frequencies):
            if self.should_stop:
                return
                
            self.emit_message(f"测量路径2 @ {freq} GHz...")
            loss = self.measure_path_loss(freq)
            path2_losses[freq] = loss
            
            progress = 50 + int(((i + 1) / len(test_frequencies)) * 50)  # 第二步占50%
            self.emit_progress(progress)
            self.emit_message(f"路径2 @ {freq} GHz: {loss:.2f} dB")
            
        if self.should_stop:
            return
            
        # 计算最终结果
        self.emit_message("计算线缆损耗...")
        for freq in test_frequencies:
            p1_loss = self.path1_losses[freq]
            p2_loss = path2_losses[freq]

            self.cable_losses[freq] = calculate_cable_losses(
                path1_loss=p1_loss,
                path2_loss=p2_loss,
                attenuator_value=self.attenuator_value)
            
        self.save_results()
        self.emit_progress(100)
        self.emit_message("线损测量完成！")


class EnhancedDriverPowerMapping(DriverPowerMapping):
    """增强的驱动映射类，支持实时数据更新"""
    
    def __init__(self, config_path=None, loss_data_path=None,
                 progress_callback=None, message_callback=None, data_callback=None):
        super().__init__(config_path, loss_data_path)
        self.progress_callback = progress_callback
        self.message_callback = message_callback
        self.data_callback = data_callback
        self.should_stop = False
        
    def emit_progress(self, value: int):
        if self.progress_callback:
            self.progress_callback(value)
            
    def emit_message(self, message: str):
        if self.message_callback:
            self.message_callback(message)
            
    def emit_data(self, data: Dict[str, Any]):
        if self.data_callback:
            self.data_callback(data)
            
    def stop_measurement(self):
        logger.info("驱动映射测量收到停止请求")
        self.should_stop = True
        
    def measure_power_mapping(self, frequency: float):
        """增强的功率映射测量，支持实时数据更新"""
        if self.should_stop:
            return
            
        self.emit_message(f"测量频率 {frequency} GHz 的功率映射...")
        
        self.inst_ctrl.set_power(-40)
        self.inst_ctrl.set_frequency(frequency)
        self.inst_ctrl.set_center_frequency(frequency)
        self.inst_ctrl.set_span(10)
        
        start_power = self.config['signal_source']['start_power']
        stop_power = self.config['signal_source']['stop_power']
        step = self.config['signal_source']['step']
        
        power_range = np.arange(start_power, stop_power + step, step)
        total_points = len(power_range)
        
        self.power_mapping[str(frequency)] = {}
        
        # 实时数据存储
        sweep_data = {
            'input_power_sg': [],
            'output_power_driver': [],
            'gain': []
        }
        
        self.inst_ctrl.rf_output_on()
        
        for i, input_power in enumerate(power_range):
            if self.should_stop:
                self.inst_ctrl.rf_output_off()
                return
                
            self.inst_ctrl.set_power(input_power)
            time.sleep(3)
            
            measured_power = self.inst_ctrl.measure_power_with_average()
            actual_power = self.calculate_actual_power(frequency, measured_power)
            
            self.power_mapping[str(frequency)][str(input_power)] = actual_power
            
            # 计算增益
            gain = calculate_gain(actual_power, input_power)
            
            # 更新实时数据
            sweep_data['input_power_sg'].append(input_power)
            sweep_data['output_power_driver'].append(actual_power)
            sweep_data['gain'].append(gain)
            
            progress = int(((i + 1) / total_points) * 100)
            self.emit_progress(progress)
            self.emit_message(f"Input: {input_power:.1f} dBm, Output: {actual_power:.1f} dBm, Gain: {gain:.1f} dB")
            
            # 发送实时数据更新
            self.emit_data({
                'frequency': frequency,
                'sweep_data': sweep_data.copy()
            })
            
        self.inst_ctrl.rf_output_off()
        
    def measure_all_frequencies(self):
        """测量所有频率的功率映射"""
        try:
            self.emit_message("设置驱动功放电源...")
            self.inst_ctrl.setup_driver_amplifier_power()
            
            self.emit_message("打开驱动功放电源...")
            self.inst_ctrl.power_on_driver()
            
            frequencies = self.config['test_frequencies']
            for i, freq in enumerate(frequencies):
                if self.should_stop:
                    break
                    
                self.measure_power_mapping(freq)
                
                # 整体进度
                overall_progress = int(((i + 1) / len(frequencies)) * 100)
                self.emit_progress(overall_progress)
                
            if not self.should_stop:
                self.save_results()
                self.emit_message("驱动功放映射测量完成！")
                
        except Exception as e:
            logger.exception("增强驱动映射测量失败: %s", e)
            self.emit_message(f"测量过程中出现错误: {str(e)}")
        finally:
            logger.info("增强驱动映射测量清理: RF 关闭、驱动电源关闭、断开连接")
            self.emit_message("关闭驱动功放电源...")
            cleanup_measurement(self.inst_ctrl, power_cleanup=self.inst_ctrl.power_off_driver)


class EnhancedAmplifierMeasurement(AmplifierMeasurement):
    """增强的功放测量类，支持实时数据更新"""
    
    def __init__(self, config_path=None, loss_data_path=None,
                 driver_mapping_path: Optional[str] = None, progress_callback=None, 
                 message_callback=None, data_callback=None):
        super().__init__(config_path, loss_data_path, driver_mapping_path)
        self.progress_callback = progress_callback
        self.message_callback = message_callback
        self.data_callback = data_callback
        self.should_stop = False
        
    def emit_progress(self, value: int):
        if self.progress_callback:
            self.progress_callback(value)
            
    def emit_message(self, message: str):
        if self.message_callback:
            self.message_callback(message)
            
    def emit_data(self, data: Dict[str, Any]):
        if self.data_callback:
            self.data_callback(data)
            
    def stop_measurement(self):
        logger.info("主功放测量收到停止请求")
        self.should_stop = True
        
    def perform_power_sweep(self, frequency: float) -> Dict:
        """增强的功率扫描，支持实时数据更新"""
        if self.should_stop:
            return {}
            
        self.emit_message(f"开始在 {frequency} GHz 进行功率扫描...")
        
        compression_type = self.config['compression_point']['type']
        compression_value = float(compression_type.replace('dB', ''))
        
        max_dut_input_power = self.config.get('dut_config', {}).get('max_input_power', float('inf'))
        
        self.inst_ctrl.set_power(-40)
        self.inst_ctrl.set_frequency(frequency)
        self.inst_ctrl.set_center_frequency(frequency)
        self.inst_ctrl.set_span(10)
        
        start_power_sg = self.config['signal_source']['start_power']
        stop_power_sg = self.config['signal_source']['stop_power']
        step_sg = self.config['signal_source']['step']
        
        sweep_data = {
            'input_power_dut': [], 'output_power_dut': [], 'gain': [],
            'sg_power': [], 'voltages': [], 'currents': [], 
            'dc_power': [], 'efficiency': []
        }
        
        dut_supply_config = self.config['power_supply_assignment']['dut_amplifier']['supplies']
        
        self.inst_ctrl.rf_output_on()
        small_gain_points = 3
        sg_powers_all = np.arange(start_power_sg, stop_power_sg + step_sg, step_sg)
        small_signal_gains = []
        
        total_points = len(sg_powers_all)
        
        for idx, sg_power in enumerate(sg_powers_all):
            if self.should_stop:
                self.inst_ctrl.rf_output_off()
                return {}
                
            # 计算DUT的实际输入功率
            dut_input_power = calculate_dut_input_power(
                sg_power=sg_power,
                frequency=frequency,
                loss_data=self.loss_data['cable_losses'],
                driver_mapping=self.driver_mapping)
                
            # 安全检查
            if dut_input_power > max_dut_input_power:
                logger.warning(
                    "DUT 输入保护触发: %s GHz 计算输入功率 %.2f dBm 超过最大值 %.2f dBm，停止该频率扫描",
                    frequency, dut_input_power, max_dut_input_power)
                self.emit_message(f"达到DUT最大输入功率限制 {max_dut_input_power} dBm，停止扫描")
                break
                
            # 设置信号源功率
            self.inst_ctrl.set_power(sg_power)
            time.sleep(3)
            
            # 测量DUT输出功率
            measured_power = self.inst_ctrl.measure_power_with_average()
            actual_output_power = self.calculate_actual_power(frequency, measured_power)
            
            # 测量DC功耗
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
                    
            # 计算各项指标
            efficiency = calculate_efficiency(actual_output_power, total_dc_power)
            gain = calculate_gain(actual_output_power, dut_input_power)
            
            # 存储数据
            sweep_data['sg_power'].append(sg_power)
            sweep_data['input_power_dut'].append(dut_input_power)
            sweep_data['output_power_dut'].append(actual_output_power)
            sweep_data['gain'].append(gain)
            sweep_data['voltages'].append(v_reading)
            sweep_data['currents'].append(i_reading)
            sweep_data['dc_power'].append(total_dc_power)
            sweep_data['efficiency'].append(efficiency)
            
            progress = int(((idx + 1) / total_points) * 100)
            self.emit_progress(progress)
            self.emit_message(f"SG: {sg_power:.1f}, DUT Pin: {dut_input_power:.2f}, Pout: {actual_output_power:.1f}, "
                            f"Gain: {gain:.1f}, Eff: {efficiency:.1f}%")
            
            # 发送实时数据更新
            self.emit_data({
                'frequency': frequency,
                'sweep_data': sweep_data.copy(),
                'current_point': {
                    'sg_power': sg_power,
                    'dut_input_power': dut_input_power,
                    'output_power': actual_output_power,
                    'gain': gain,
                    'efficiency': efficiency,
                    'dc_power': total_dc_power
                }
            })
            
            if idx < small_gain_points:
                small_signal_gains.append(gain)
                
            # 压缩点检测
            if idx >= small_gain_points:
                small_signal_gain = np.mean(small_signal_gains)
                compression_gain = calculate_gain(
                    small_signal_gain, gain)
                if compression_gain >= compression_value:
                    self.emit_message(f"达到目标压缩点 {compression_value} dB，停止扫描")
                    break
                    
        self.inst_ctrl.rf_output_off()
        
        if self.should_stop:
            return {}
            
        # 计算压缩点
        if not sweep_data['gain']:
            # 保留原增强入口的提示副作用，再由纯函数在 argmin() 处抛 ValueError。
            self.emit_message(f"警告：未达到目标压缩点 {compression_value} dB")

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
            self.emit_message(f"警告：未达到目标压缩点 {compression_value} dB")
        
        return {
            'compression_type': compression_type,
            'compression_point': compression_point_data,
            'small_signal_gain': small_signal_gain,
            'sweep_data': sweep_data,
            'compression_achieved': compression_achieved
        }
        
    def measure_all_frequencies(self):
        """测量所有配置频率"""
        try:
            self.emit_message("设置电源参数...")
            if self.config['driver_mode']['enabled']:
                self.inst_ctrl.setup_driver_amplifier_power()
            self.inst_ctrl.setup_dut_power()
            
            self.emit_message("执行上电序列...")
            self.inst_ctrl.power_on_sequence()
            time.sleep(2)
            
            frequencies = self.config['test_frequencies']
            for i, freq in enumerate(frequencies):
                if self.should_stop:
                    break
                    
                result = self.perform_power_sweep(freq)
                if result:  # 只有在未停止的情况下才保存结果
                    self.measurement_results[str(freq)] = result
                    
                # 整体进度
                overall_progress = int(((i + 1) / len(frequencies)) * 100)
                self.emit_progress(overall_progress)
                
            if not self.should_stop:
                self.save_results()
                self.emit_message("主功放测量完成！")
                
        except Exception as e:
            logger.exception("增强主功放测量失败: %s", e)
            self.emit_message(f"测量过程中出现错误: {str(e)}")
        finally:
            logger.info("增强主功放测量清理: RF 关闭、掉电序列、断开连接")
            self.emit_message("执行掉电序列...")
            cleanup_measurement(self.inst_ctrl, power_cleanup=self.inst_ctrl.power_off_sequence)


class WorkerSignals(QObject):
    """定义工作线程的信号"""
    finished = Signal()
    error = Signal(str)
    message = Signal(str)
    progress = Signal(int)


class InstrumentWorker(QThread):
    """仪器连接工作线程"""
    
    def __init__(self, config_path=None):
        super().__init__()
        self.config_path = resolve_path(config_path, CONFIG_FILE)
        self.signals = WorkerSignals()
        
    def run(self):
        """执行仪器连接"""
        try:
            self.signals.message.emit("正在连接仪器...")
            self.signals.progress.emit(20)
            
            # 初始化仪器控制
            inst_ctrl = InstrumentControl(self.config_path)
            
            self.signals.progress.emit(60)
            self.signals.message.emit("仪器初始化完成")
            
            # 模拟一些初始化时间
            time.sleep(1)
            
            self.signals.progress.emit(100)
            self.signals.message.emit("所有启用的仪器连接成功")
            self.signals.finished.emit()
            
        except Exception as e:
            error_msg = f"仪器连接失败: {str(e)}"
            self.signals.error.emit(error_msg)
            self.signals.message.emit(error_msg)
