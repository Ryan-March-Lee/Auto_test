# --- START OF FILE data_visualization.py ---

import json
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from typing import Dict, List, Optional, Tuple
import pandas as pd
from datetime import datetime
import seaborn as sns
from pathlib import Path

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


class DataVisualization:
    def __init__(self):
        # --- MODIFIED: Set global font to Times New Roman ---
        # 移除黑体设置
        # plt.rcParams['font.sans-serif'] = ['SimHei']

        # 将默认字体设置为 Times New Roman
        plt.rcParams['font.family'] = 'serif'
        plt.rcParams['font.serif'] = ['Times New Roman'] + plt.rcParams['font.serif']

        plt.rcParams['axes.unicode_minus'] = False
        self.output_dir = Path('test_results') / datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        sns.set_style("whitegrid")
        sns.set_context("notebook", font_scale=1.2)
        # 使用更容易区分的颜色调色盘 - 组合多种高对比度颜色
        base_colors = [
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
            '#9edae5'   # 浅青色
        ]
        self.base_color_palette = base_colors

    def load_data(self, file_path: str) -> dict:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _save_plot(self, fig, base_name: str) -> Path:
        save_path_png = self.output_dir / f"{base_name}.png"
        save_path_pdf = self.output_dir / f"{base_name}.pdf"
        fig.savefig(save_path_png, dpi=300, bbox_inches='tight')
        fig.savefig(save_path_pdf, bbox_inches='tight')
        plt.close(fig)
        print(f"图表已保存: {save_path_png.name}, {save_path_pdf.name}")
        return save_path_png

    def _extract_data_for_plotting(self, data: dict) -> Dict[float, pd.DataFrame]:
        dfs = {}
        results = data.get('results', {})
        for freq_str, result in results.items():
            freq = float(freq_str)
            sweep_data = result.get('sweep_data', {})
            if sweep_data:
                df = pd.DataFrame(sweep_data)
                if 'voltages' in df.columns:
                    df = df.drop(columns=['voltages', 'currents']).join(
                        pd.json_normalize(df['voltages']).add_prefix('V_')).join(
                        pd.json_normalize(df['currents']).add_prefix('I_'))
                dfs[freq] = df
        return dfs

    def _find_saturation_points(self, data: dict) -> pd.DataFrame:
        sat_points = []
        results = data.get('results', {})
        for freq_str, result in results.items():
            point_data = result.get('compression_point', {})
            if point_data:
                point_data['frequency'] = float(freq_str)
                sat_points.append(point_data)
        return pd.DataFrame(sat_points)

    # --- MODIFIED: Applied specific fonts to all plotting functions ---

    def plot_p_in_vs_p_out(self, data: dict) -> Path:
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

    def plot_p_out_vs_efficiency(self, data: dict) -> Path:
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

    def plot_p_out_vs_gain(self, data: dict) -> Path:
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

    def plot_freq_vs_saturation_metrics(self, data: dict) -> Path:
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

    def plot_freq_vs_sat_power_and_gain(self, data: dict) -> Path:
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

    def plot_p_out_vs_eff_and_gain_combined(self, data: dict) -> Path:
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

    def plot_freq_vs_all_sat_metrics_combined(self, data: dict) -> Path:
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
    def generate_csv_report(self, data: dict) -> Path:
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

        full_df = pd.concat(all_dfs, ignore_index=True)
        full_df.to_csv(csv_path, index=False, encoding='utf-8-sig')
        print(f"CSV报告已保存: {csv_path.name}")
        return csv_path

    def create_summary_report(self, dut_data_file: str, original_filename: str = None):
        data = self.load_data(dut_data_file)
        report_file = self.output_dir / "test_report.html"
        
        # 使用传入的原始文件名，如果没有则从数据中获取，最后回退到文件路径
        display_filename = original_filename or data.get('original_filename') or Path(dut_data_file).name

        plot_paths = {
            "p_in_vs_p_out": self.plot_p_in_vs_p_out(data),
            "p_out_vs_gain": self.plot_p_out_vs_gain(data),
            "p_out_vs_efficiency": self.plot_p_out_vs_efficiency(data),
            "p_out_vs_eff_and_gain": self.plot_p_out_vs_eff_and_gain_combined(data),
            "freq_vs_sat_efficiency": self.plot_freq_vs_saturation_metrics(data),
            "freq_vs_sat_pout_gain": self.plot_freq_vs_sat_power_and_gain(data),
            "freq_vs_all_sat_metrics": self.plot_freq_vs_all_sat_metrics_combined(data),
        }

        csv_path = self.generate_csv_report(data)

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
                <p>测试时间: {data.get('measurement_time', 'N/A')}</p>
                <p>数据文件: {display_filename}</p>
                {f'<p>数据导出: <a href="{csv_path.name}">{csv_path.name}</a></p>' if csv_path else ''}
            </div>

            <div class="section">
                <h2>详细性能曲线</h2>
                <div class="grid-container">
                    <div class="grid-item"><img src="{plot_paths['p_in_vs_p_out'].name}" alt="Pin vs Pout"></div>
                    <div class="grid-item"><img src="{plot_paths['p_out_vs_gain'].name}" alt="Pout vs Gain"></div>
                    <div class="grid-item"><img src="{plot_paths['p_out_vs_efficiency'].name}" alt="Pout vs Efficiency"></div>
                </div>
            </div>

             <div class="section">
                <h2>组合性能图 (双Y轴)</h2>
                <div class="grid-container">
                     <div class="grid-item"><img src="{plot_paths['p_out_vs_eff_and_gain'].name}" alt="Pout vs Eff/Gain"></div>
                </div>
            </div>

            <div class="section">
                <h2>饱和点性能 vs 频率</h2>
                <div class="grid-container">
                    <div class="grid-item"><img src="{plot_paths['freq_vs_sat_efficiency'].name}" alt="Freq vs Saturation Efficiency"></div>
                    <div class="grid-item"><img src="{plot_paths['freq_vs_sat_pout_gain'].name}" alt="Freq vs Saturation Pout/Gain"></div>
                    <div class="grid-item"><img src="{plot_paths['freq_vs_all_sat_metrics'].name}" alt="Freq vs All Saturation Metrics"></div>
                </div>
            </div>

            <div class="section">
                <h2>测试配置</h2>
                <pre>{json.dumps(data.get('config', {}), indent=4, ensure_ascii=False)}</pre>
            </div>

            </div></body></html>""")
        print(f"\nHTML报告已生成: {report_file}")


def main():
    try:
        visualizer = DataVisualization()
        dut_files = sorted(Path('.').glob('amplifier_measurement_*.json'), key=Path.stat)
        if not dut_files:
            print("错误: 未找到任何 'amplifier_measurement_*.json' 文件进行可视化。")
            return
        latest_dut_file = dut_files[-1]
        print(f"正在为最新的测量文件生成报告: {latest_dut_file}")
        visualizer.create_summary_report(str(latest_dut_file))
        print("\n数据可视化和报告生成已成功完成！")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"发生错误: {str(e)}")


if __name__ == "__main__":
    main()

# --- END OF FILE data_visualization.py ---