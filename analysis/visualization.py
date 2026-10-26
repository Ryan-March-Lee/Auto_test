"""测量结果的可视化与报告生成（纯分析边界）。

该模块只消费领域结果模型或已规范化的结果映射，生成图表、CSV 与 HTML 报告内容，
不读取结果文件、不依赖基础设施或 Qt、也不创建输出目录之外的运行副作用。
输出目录由调用方注入，目录在真正写文件时才创建；结果读取与报告触发属于适配器、
应用服务或命令入口的职责。

真实图像渲染（PNG/PDF）依赖 matplotlib；渲染可用性由调用环境决定。
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Dict

import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
import seaborn as sns

from analysis.result_source import ReportResult, plain_document, require_report_result


ResultSource = ReportResult


# --- MODIFIED: Define font properties globally for easy access ---
# 确保你的系统中有这些字体文件，并且路径正确
try:
    chinese_font = FontProperties(fname='C:/Windows/Fonts/simsun.ttc')  # 宋体
    english_font = FontProperties(fname='C:/Windows/Fonts/times.ttf')  # Times New Roman
except FileNotFoundError:
    print("警告: 未在C:/Windows/Fonts/找到simsun.ttc或times.ttf字体。图表将使用默认字体。")
    # 提供一个备用方案，避免程序崩溃
    chinese_font = FontProperties(family='sans-serif')
    english_font = FontProperties(family='serif')

BASE_COLOR_PALETTE = [
    '#1f77b4',  # 蓝色
    '#ff7f0e',  # 橙色  
    '#2ca02c',  # 绿色
    '#d62728',  # 红色
    '#9467bd',  # 紫色
    '#8c564b',  # 棕色
    '#e377c2',  # 粉色
    '#7f7f7f',  # 灰色
    '#bcbd22',  # 橄榄色
    '#17becf',  # 青色
    '#aec7e8',  # 浅蓝色
    '#ffbb78',  # 浅橙色
    '#98df8a',  # 浅绿色
    '#ff9896',  # 浅红色
    '#c5b0d5',  # 浅紫色
    '#c49c94',  # 浅棕色
    '#f7b6d3',  # 浅粉色
    '#c7c7c7',  # 浅灰色
    '#dbdb8d',  # 浅橄榄色
    '#9edae5',  # 浅青色
]


def configure_plot_style() -> None:
    """设置绘图使用的全局字体与样式；由可视化入口显式调用。"""
    plt.rcParams['font.family'] = 'serif'
    plt.rcParams['font.serif'] = ['Times New Roman'] + list(plt.rcParams['font.serif'])
    plt.rcParams['axes.unicode_minus'] = False
    sns.set_style("whitegrid")
    sns.set_context("notebook", font_scale=1.2)


def _coerce_result(data: ResultSource) -> ReportResult:
    """校验并返回正式结果对象；原始映射与旧结果模型都会被拒绝。"""
    return require_report_result(data)


class DataVisualization:
    """面向扫描结果的纯分析入口：生成图表、CSV 与 HTML 报告。"""

    def __init__(self, *, output_dir: str | Path):
        configure_plot_style()
        self.output_dir = Path(output_dir)
        self.base_color_palette = list(BASE_COLOR_PALETTE)

    def _ensure_output_dir(self) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        return self.output_dir

    def _save_plot(self, fig, base_name: str) -> Path:
        self._ensure_output_dir()
        save_path_png = self.output_dir / f"{base_name}.png"
        save_path_pdf = self.output_dir / f"{base_name}.pdf"
        fig.savefig(save_path_png, dpi=300, bbox_inches='tight')
        fig.savefig(save_path_pdf, bbox_inches='tight')
        plt.close(fig)
        print(f"图表已保存: {save_path_png.name}, {save_path_pdf.name}")
        return save_path_png

    def export_model_reports(self, result, *, csv_path: str | Path, html_path: str | Path):
        """Export a domain result through the analysis boundary."""
        from analysis.reporting import export_csv, render_html_report

        return (
            export_csv(result, csv_path),
            render_html_report(result, html_path),
        )

    def _extract_data_for_plotting(self, data: ResultSource) -> Dict[float, pd.DataFrame]:
        result = _coerce_result(data)
        return {
            float(freq): pd.DataFrame(list(points))
            for freq, points in result.sweep_rows().items()
            if points
        }

    def _find_saturation_points(self, data: ResultSource) -> pd.DataFrame:
        result = _coerce_result(data)
        points = [dict(point) for point in result.saturation_points()]
        for point in points:
            point["frequency"] = float(point["frequency"])
        return pd.DataFrame(points)

    # --- MODIFIED: Applied specific fonts to all plotting functions ---

    def plot_p_in_vs_p_out(self, data: ResultSource) -> Path:
        dfs = self._extract_data_for_plotting(data)
        fig, ax = plt.subplots(figsize=(8, 6))
        # 使用高对比度颜色，确保容易区分
        if len(dfs) <= len(self.base_color_palette):
            colors = self.base_color_palette[:len(dfs)]
        else:
            # 如果频率数量超过预定义颜色，使用多种色系组合
            colors = (self.base_color_palette + 
                     sns.color_palette("Set1", 9) + 
                     sns.color_palette("Set2", 8) + 
                     sns.color_palette("Dark2", 8))[:len(dfs)]
        for i, (freq, df) in enumerate(dfs.items()):
            ax.plot(df['input_power_dut'], df['output_power_dut'], 'o-', label=f'{freq} GHz',
                    color=colors[i])
        ax.set_xlabel('输入功率 (dBm)', fontproperties=chinese_font)
        ax.set_ylabel('输出功率 (dBm)', fontproperties=chinese_font)
        ax.set_title('功放输入 vs 输出功率曲线', fontproperties=chinese_font)
        ax.legend(prop=english_font)  # Legend labels are English/numbers
        ax.grid(True, which='both', linestyle='--')
        return self._save_plot(fig, 'p_in_vs_p_out')

    def plot_p_out_vs_efficiency(self, data: ResultSource) -> Path:
        dfs = self._extract_data_for_plotting(data)
        fig, ax = plt.subplots(figsize=(8, 6))
        # 使用高对比度颜色，确保容易区分
        if len(dfs) <= len(self.base_color_palette):
            colors = self.base_color_palette[:len(dfs)]
        else:
            # 如果频率数量超过预定义颜色，使用多种色系组合
            colors = (self.base_color_palette + 
                     sns.color_palette("Set1", 9) + 
                     sns.color_palette("Set2", 8) + 
                     sns.color_palette("Dark2", 8))[:len(dfs)]
        for i, (freq, df) in enumerate(dfs.items()):
            ax.plot(df['output_power_dut'], df['efficiency'], 'o-', label=f'{freq} GHz', color=colors[i])
        ax.set_xlabel('输出功率 (dBm)', fontproperties=chinese_font)
        ax.set_ylabel('漏极效率 (%)', fontproperties=chinese_font)
        ax.set_title('功放输出功率 vs 效率曲线', fontproperties=chinese_font)
        ax.legend(prop=english_font)
        ax.grid(True, which='both', linestyle='--')
        return self._save_plot(fig, 'p_out_vs_efficiency')

    def plot_p_out_vs_gain(self, data: ResultSource) -> Path:
        dfs = self._extract_data_for_plotting(data)
        fig, ax = plt.subplots(figsize=(8, 6))
        # 使用高对比度颜色，确保容易区分
        if len(dfs) <= len(self.base_color_palette):
            colors = self.base_color_palette[:len(dfs)]
        else:
            # 如果频率数量超过预定义颜色，使用多种色系组合
            colors = (self.base_color_palette + 
                     sns.color_palette("Set1", 9) + 
                     sns.color_palette("Set2", 8) + 
                     sns.color_palette("Dark2", 8))[:len(dfs)]
        for i, (freq, df) in enumerate(dfs.items()):
            ax.plot(df['output_power_dut'], df['gain'], 'o-', label=f'{freq} GHz', color=colors[i])
        ax.set_xlabel('输出功率 (dBm)', fontproperties=chinese_font)
        ax.set_ylabel('增益 (dB)', fontproperties=chinese_font)
        ax.set_title('功放输出功率 vs 增益曲线', fontproperties=chinese_font)
        ax.legend(prop=english_font)
        ax.grid(True, which='both', linestyle='--')
        return self._save_plot(fig, 'p_out_vs_gain')

    def plot_freq_vs_saturation_metrics(self, data: ResultSource) -> Path:
        sat_df = self._find_saturation_points(data)
        if sat_df.empty: return None
        fig, ax = plt.subplots(figsize=(8, 6))
        ax.plot(sat_df['frequency'], sat_df['efficiency'], 'o-', label='饱和效率', color='r')
        ax.set_xlabel('工作频率 (GHz)', fontproperties=chinese_font)
        ax.set_ylabel('饱和效率 (%)', fontproperties=chinese_font, color='r')
        ax.tick_params(axis='y', labelcolor='r')
        ax.legend(prop=chinese_font)
        ax.set_title('频率 vs 饱和状态性能', fontproperties=chinese_font)
        ax.grid(True, which='both', linestyle='--')
        return self._save_plot(fig, 'freq_vs_sat_efficiency')

    def plot_freq_vs_sat_power_and_gain(self, data: ResultSource) -> Path:
        sat_df = self._find_saturation_points(data)
        if sat_df.empty: return None
        fig, ax1 = plt.subplots(figsize=(8, 6))

        ax1.plot(sat_df['frequency'], sat_df['output_power'], 'o-', color='b', label='饱和输出功率')
        ax1.set_xlabel('工作频率 (GHz)', fontproperties=chinese_font)
        ax1.set_ylabel('饱和输出功率 (dBm)', fontproperties=chinese_font, color='b')
        ax1.tick_params(axis='y', labelcolor='b')
        ax1.legend(loc='upper left', prop=chinese_font)

        ax2 = ax1.twinx()
        ax2.plot(sat_df['frequency'], sat_df['gain'], 's--', color='g', label='饱和增益')
        ax2.set_ylabel('饱和增益 (dB)', fontproperties=chinese_font, color='g')
        ax2.tick_params(axis='y', labelcolor='g')
        ax2.legend(loc='upper right', prop=chinese_font)

        ax1.set_title('频率 vs 饱和功率和增益', fontproperties=chinese_font)
        fig.tight_layout()
        return self._save_plot(fig, 'freq_vs_sat_pout_gain')

    def plot_p_out_vs_eff_and_gain_combined(self, data: ResultSource) -> Path:
        dfs = self._extract_data_for_plotting(data)
        num_freqs = len(dfs)
        if num_freqs == 0: return None
        fig, axes = plt.subplots(nrows=(num_freqs + 1) // 2, ncols=2, figsize=(16, 5 * ((num_freqs + 1) // 2)),
                                 squeeze=False)
        axes = axes.flatten()

        for i, (freq, df) in enumerate(dfs.items()):
            ax1 = axes[i]
            p1, = ax1.plot(df['output_power_dut'], df['efficiency'], 'o-', color='b', label='效率')
            ax1.set_xlabel('输出功率 (dBm)', fontproperties=chinese_font)
            ax1.set_ylabel('效率 (%)', fontproperties=chinese_font, color='b')
            ax1.tick_params(axis='y', labelcolor='b')

            ax2 = ax1.twinx()
            p2, = ax2.plot(df['output_power_dut'], df['gain'], 's--', color='g', label='增益')
            ax2.set_ylabel('增益 (dB)', fontproperties=chinese_font, color='g')
            ax2.tick_params(axis='y', labelcolor='g')

            ax1.set_title(f'{freq} GHz', fontproperties=english_font)
            ax1.legend(handles=[p1, p2], loc='best', prop=chinese_font)
            ax1.grid(True)

        for j in range(i + 1, len(axes)):
            fig.delaxes(axes[j])

        fig.suptitle('输出功率 vs (效率 + 增益) 组合图', fontsize=16, fontproperties=chinese_font)
        fig.tight_layout(rect=[0, 0.03, 1, 0.95])
        return self._save_plot(fig, 'p_out_vs_eff_gain_combined')

    def plot_freq_vs_all_sat_metrics_combined(self, data: ResultSource) -> Path:
        sat_df = self._find_saturation_points(data)
        if sat_df.empty: return None
        fig, ax1 = plt.subplots(figsize=(10, 7))

        p1, = ax1.plot(sat_df['frequency'], sat_df['efficiency'], 'o-', color='r', label='饱和效率')
        ax1.set_xlabel('工作频率 (GHz)', fontproperties=chinese_font)
        ax1.set_ylabel('饱和效率 (%)', fontproperties=chinese_font, color='r')
        ax1.tick_params(axis='y', labelcolor='r')

        ax2 = ax1.twinx()
        p2, = ax2.plot(sat_df['frequency'], sat_df['output_power'], 's--', color='b', label='饱和输出功率')
        p3, = ax2.plot(sat_df['frequency'], sat_df['gain'], 'd-.', color='g', label='饱和增益')
        ax2.set_ylabel('功率 (dBm) / 增益 (dB)', fontproperties=chinese_font)

        plots = [p1, p2, p3]
        labels = [p.get_label() for p in plots]
        ax1.legend(plots, labels, loc='best', prop=chinese_font)

        ax1.set_title('频率 vs 饱和性能 (效率/功率/增益)', fontproperties=chinese_font)
        fig.tight_layout()
        return self._save_plot(fig, 'freq_vs_all_sat_metrics')

    # generate_csv_report 和 create_summary_report 函数不需要修改字体设置
    def generate_csv_report(self, data: ResultSource) -> Path | None:
        dfs = self._extract_data_for_plotting(data)
        csv_path = self.output_dir / "full_sweep_data.csv"

        all_dfs = []
        for freq, df in dfs.items():
            df_copy = df.copy()
            df_copy['frequency_ghz'] = freq
            all_dfs.append(df_copy)

        if not all_dfs:
            print("没有可导出的数据。")
            return None

        self._ensure_output_dir()
        full_df = pd.concat(all_dfs, ignore_index=True)
        full_df.to_csv(csv_path, index=False, encoding='utf-8-sig')
        print(f"CSV报告已保存: {csv_path.name}")
        return csv_path

    def create_summary_report(
        self, data: ResultSource, original_filename: str | None = None
    ) -> Path:
        """从正式结果对象生成图表、CSV 和 HTML 汇总报告。"""
        result = _coerce_result(data)
        if not result.point_count:
            raise ValueError("结果中没有可生成报告的扫描点")
        self._ensure_output_dir()
        report_file = self.output_dir / "test_report.html"

        # 报告展示的数据来源文件名与测试时间都来自正式结果契约。
        display_filename = (
            original_filename
            or result.source_filename
            or result.run_id
        )

        plot_paths = {
            "p_in_vs_p_out": self.plot_p_in_vs_p_out(result),
            "p_out_vs_gain": self.plot_p_out_vs_gain(result),
            "p_out_vs_efficiency": self.plot_p_out_vs_efficiency(result),
            "p_out_vs_eff_and_gain": self.plot_p_out_vs_eff_and_gain_combined(result),
            "freq_vs_sat_efficiency": self.plot_freq_vs_saturation_metrics(result),
            "freq_vs_sat_pout_gain": self.plot_freq_vs_sat_power_and_gain(result),
            "freq_vs_all_sat_metrics": self.plot_freq_vs_all_sat_metrics_combined(result),
        }
        plot_paths = {name: path for name, path in plot_paths.items() if path is not None}
        combined_plots = {
            name: path
            for name, path in plot_paths.items()
            if name in {"p_out_vs_eff_and_gain", "freq_vs_all_sat_metrics"}
        }
        saturation_plots = {
            name: path
            for name, path in plot_paths.items()
            if name in {"freq_vs_sat_efficiency", "freq_vs_sat_pout_gain"}
        }

        csv_path = self.generate_csv_report(result)

        safe_time = html.escape(str(result.measurement_time or 'N/A'))
        safe_filename = html.escape(str(display_filename))
        plan_snapshot_json = json.dumps(
            plain_document(result.configuration_snapshot), indent=4, ensure_ascii=False
        )
        csv_section = (
            f'<p>数据导出: <a href="{html.escape(csv_path.name, quote=True)}">'
            f'{html.escape(csv_path.name)}</a></p>'
            if csv_path
            else ''
        )

        # HTML report generation remains the same
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write(f"""
            <html><head><title>功放测试报告</title>
            <style>
                body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 20px; background-color: #f4f4f9; }}
                h1, h2 {{ color: #333; border-bottom: 2px solid #667eea; padding-bottom: 5px;}}
                .container {{ max-width: 1200px; margin: auto; background: white; padding: 20px; box-shadow: 0 0 10px rgba(0,0,0,0.1); }}
                .grid-container {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(500px, 1fr)); gap: 20px;}}
                .grid-item img {{ max-width: 100%; height: auto; border: 1px solid #ddd; border-radius: 4px;}}
                .section {{ margin-bottom: 30px; }}
                pre {{ background: #eee; padding: 15px; border-radius: 5px; white-space: pre-wrap; word-wrap: break-word; }}
            </style></head><body><div class="container">
            <h1>功放测试报告</h1>
            <div class="section">
                <h2>测试信息</h2>
                <p>测试时间: {safe_time}</p>
                <p>数据文件: {safe_filename}</p>
                {csv_section}
            </div>

            <div class="section">
                <h2>详细性能曲线</h2>
                <div class="grid-container">
                    {''.join(f'<div class="grid-item"><img src="{html.escape(path.name, quote=True)}" alt="{html.escape(name, quote=True)}"></div>' for name, path in plot_paths.items())}
                </div>
            </div>

             <div class="section">
                <h2>组合性能图 (双Y轴)</h2>
                 <div class="grid-container">
                    {''.join(f'<div class="grid-item">{html.escape(path.name)}</div>' for name, path in combined_plots.items())}
                 </div>
            </div>

            <div class="section">
                <h2>饱和点性能 vs 频率</h2>
                <div class="grid-container">
                    {''.join(f'<div class="grid-item">{html.escape(path.name)}</div>' for name, path in saturation_plots.items())}
                </div>
            </div>

            <div class="section">
                <h2>测试配置</h2>
                 <pre>{html.escape(plan_snapshot_json)}</pre>
            </div>

            </div></body></html>""")
        print(f"\nHTML报告已生成: {report_file}")

        return report_file
