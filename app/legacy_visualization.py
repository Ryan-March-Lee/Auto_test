"""数据可视化的历史入口装配适配器。

正式的可视化与报告生成位于 :mod:`analysis.visualization`，只消费领域结果模型或
已规范化的结果映射，不依赖基础设施。本模块只服务仍以结果文件路径调用
``DataVisualization.create_summary_report(path)`` 的仓库外脚本与回滚场景：负责
结果文件读取、默认输出目录与旧方法转发，不新增任何绘图或报告逻辑。

兼容层使用组合而非继承，避免正式实现新增的方法被隐式暴露为历史 API。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from analysis.visualization import DataVisualization as _AnalysisDataVisualization
from domain.result_reading import parse_result_model
from infrastructure.filesystem.paths import (
    DEFAULT_PATHS,
    PathService,
    unique_directory_name,
)
from infrastructure.persistence.json_result_repository import load_json_result


def load_measurement_result(path) -> Mapping[str, Any]:
    """读取结果文件原始映射（兼容旧 ``data_visualization.load_measurement_result``）。"""
    return load_json_result(path)


def load_result_model(path):
    """读取结果文件并返回稳定领域模型（兼容旧 ``data_visualization.load_result_model``）。"""
    return parse_result_model(load_json_result(path))


def default_output_dir(paths: PathService | None = None) -> Path:
    """为一次报告生成返回唯一输出目录，避免同秒多次导出互相覆盖。"""
    base = (paths or DEFAULT_PATHS).test_results_dir
    return base / unique_directory_name("report")


class DataVisualization:
    """兼容旧路径式调用的可视化入口；数据分析仍委托给正式实现。"""

    def __init__(
        self,
        *,
        paths: PathService | None = None,
        output_dir: str | Path | None = None,
    ):
        self.output_dir = Path(output_dir) if output_dir is not None else default_output_dir(paths)
        self._analysis = _AnalysisDataVisualization(output_dir=self.output_dir)

    # --- 结果读取（历史入口职责） ---
    def load_data(self, file_path: str) -> Mapping[str, Any]:
        return load_measurement_result(file_path)

    def load_result(self, file_path: str):
        """返回稳定领域结果模型，供分析/导出入口使用。"""
        return load_result_model(file_path)

    def create_summary_report(self, data, original_filename: str | None = None):
        """兼容旧签名：接受结果文件路径或已加载的结果映射/模型。"""
        if isinstance(data, (str, Path)):
            source = load_measurement_result(data)
            original_filename = (
                original_filename
                or source.get("original_filename")
                or Path(data).name
            )
        else:
            source = data
        return self._analysis.create_summary_report(source, original_filename)

    # --- 显式转发的分析入口 ---
    def generate_csv_report(self, data):
        return self._analysis.generate_csv_report(data)

    def export_model_reports(self, result, *, csv_path, html_path):
        return self._analysis.export_model_reports(result, csv_path=csv_path, html_path=html_path)

    def plot_p_in_vs_p_out(self, data):
        return self._analysis.plot_p_in_vs_p_out(data)

    def plot_p_out_vs_efficiency(self, data):
        return self._analysis.plot_p_out_vs_efficiency(data)

    def plot_p_out_vs_gain(self, data):
        return self._analysis.plot_p_out_vs_gain(data)

    def plot_freq_vs_saturation_metrics(self, data):
        return self._analysis.plot_freq_vs_saturation_metrics(data)

    def plot_freq_vs_sat_power_and_gain(self, data):
        return self._analysis.plot_freq_vs_sat_power_and_gain(data)

    def plot_p_out_vs_eff_and_gain_combined(self, data):
        return self._analysis.plot_p_out_vs_eff_and_gain_combined(data)

    def plot_freq_vs_all_sat_metrics_combined(self, data):
        return self._analysis.plot_freq_vs_all_sat_metrics_combined(data)


__all__ = [
    "DataVisualization",
    "default_output_dir",
    "load_measurement_result",
    "load_result_model",
]
