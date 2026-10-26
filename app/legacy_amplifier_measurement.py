"""主功放测量的历史装配适配器。

正式的测量逻辑位于 :class:`application.measurements.services.AmplifierMeasurementService`。
本模块只服务仍需历史 ``AmplifierMeasurement(config_path, ...)`` 入口的外部脚本与
回滚场景：把旧单文件配置、线损数据、驱动映射和运行快照装配成正式服务可用的对象，
并把历史公开方法转发给正式实现，不新增任何测量计算。
"""

from __future__ import annotations

import time
import warnings
from datetime import datetime
from typing import Dict, Optional

import domain.measurement.calculations as measurement_calculations
from infrastructure.config.json_io import load_config_file
from infrastructure.filesystem.paths import DEFAULT_PATHS, PathService, resolve_path
from infrastructure.logging.app_logging import get_logger
from infrastructure.persistence.json_result_repository import (
    load_json_result,
    new_run_id,
    write_legacy_run_snapshot,
)
from infrastructure.persistence.result_repository import FileMeasurementResultRepository
from application.measurements.services import AmplifierMeasurementService

logger = get_logger(__name__)


class AmplifierMeasurement:
    """兼容旧 ``amplifier_measurement.AmplifierMeasurement`` 的装配适配器。"""

    def __init__(self, config_path=None,
                 loss_data_path=None,
                 driver_mapping_path: Optional[str] = None,
                 run_id: Optional[str] = None,
                 sleep_fn=None, measurement_port=None, paths: PathService | None = None):
        """初始化主功放测量适配器；测量实现全部委托给正式服务。"""
        warnings.warn(
            "app.legacy_amplifier_measurement.AmplifierMeasurement 已弃用；"
            "请通过运行目录和 app.gui_runtime 创建正式测量请求",
            DeprecationWarning,
            stacklevel=2,
        )
        logger.warning(
            "使用已弃用的主功放兼容入口；请迁移到运行目录契约和正式测量服务"
        )
        self.paths = paths or DEFAULT_PATHS
        config_path = resolve_path(config_path, self.paths.config_file)
        loss_data_path = resolve_path(loss_data_path, self.paths.cable_loss_file)
        self.config = load_config_file(config_path, paths=self.paths)
        if measurement_port is None:
            raise ValueError(
                "AmplifierMeasurement 必须显式传入 measurement_port；"
                "请通过 app.gui_runtime.create_amplifier_measurement() 由应用组装层注入端口"
            )

        self.loss_data = load_json_result(loss_data_path)

        self.run_id = run_id or new_run_id()
        self.sleep_fn = sleep_fn or time.sleep
        self.run_directory = write_legacy_run_snapshot(
            self.run_id,
            self.config,
            status="created",
            paths=self.paths,
        )
        self.inst_ctrl = measurement_port

        if self.config['driver_mode']['enabled']:
            if driver_mapping_path is None:
                raise ValueError(
                    "驱动模式已开启，兼容入口必须显式传入 driver_mapping_path；"
                    "不会再自动加载全局目录中的最新映射结果"
                )

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
        return measurement_calculations.compensate_amplifier_output_power(
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
        from domain.measurement.calculations import interpolate_driver_output_power
        return interpolate_driver_output_power(
            frequency=frequency,
            sg_power=sg_power_input,
            driver_mapping=self.driver_mapping)

    def perform_power_sweep(self, frequency: float) -> Dict:
        """
        在指定频率下执行功率扫描，同时测量RF和DC参数。

        兼容入口：实际扫描委托给正式实现
        :class:`application.measurements.services.AmplifierMeasurementService`。
        """
        service = AmplifierMeasurementService(
            self.config, self.inst_ctrl, self.loss_data, self.driver_mapping,
            run_id=getattr(self, 'run_id', 'run-legacy'), sleep_fn=getattr(self, 'sleep_fn', time.sleep),
        )
        return service.perform_power_sweep(frequency)

    def measure_all_frequencies(self):
        """测量所有配置频率；实际测量委托给正式实现。"""
        service = AmplifierMeasurementService(
            self.config, self.inst_ctrl, self.loss_data, self.driver_mapping,
            run_id=self.run_id, sleep_fn=self.sleep_fn,
        )
        result = service.run()
        self.measurement_results = result.get('results', {})
        self.save_results()
        return result

    def save_results(self):
        """保存旧入口契约的测量结果（正式用例另有结构化结果保存）。"""
        results = {
            'measurement_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'config': self.config,
            'results': self.measurement_results
        }

        repository = FileMeasurementResultRepository(paths=getattr(self, "paths", DEFAULT_PATHS))
        saved = repository.save(
            results,
            result_type="amplifier_measurement",
            run_id=self.run_id,
            run_directory=self.run_directory,
        )
        archive_path = saved.archive_path
        try:
            # 旧入口保留显式全局兼容副本，供回滚与旧脚本读取；正式测量流程不再写该副本。
            legacy_path = repository.save_compatibility_copy(
                results,
                result_type="amplifier_measurement",
                run_id=self.run_id,
            )
        except Exception:
            archive_path.unlink(missing_ok=True)
            raise
        self.run_directory = archive_path.parent
        logger.info("主功放结果已保存: 兼容路径=%s，运行路径=%s", legacy_path, archive_path)
        print(f"\nResults saved to {legacy_path}; archived to {archive_path}")
