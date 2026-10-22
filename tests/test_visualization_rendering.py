"""可视化报告的真实渲染回归。

这些用例真实调用 matplotlib 渲染 PNG/PDF 与 HTML，覆盖 ``analysis.visualization``
的端到端输出。当运行环境无法渲染时（见 ``tests/_rendering.py``）自动跳过，避免原生
崩溃拖垮离线测试；在可渲染环境中它们是必过用例。
"""

import tempfile
import unittest
from pathlib import Path

from tests._rendering import rendering_available
from analysis.visualization import DataVisualization
from infrastructure.filesystem.paths import PathService


SAMPLE_DATA = {
    "run_id": "render-run",
    "result_type": "amplifier_measurement",
    "measurement_time": "2026-01-01 00:00:00",
    "original_filename": "amplifier_measurement_render.json",
    "config": {"frequencies": [1.0, 1.2]},
    "results": {
        "1.0": {
            "sweep_data": [
                {"input_power_dut": -10.0, "output_power_dut": 0.0, "gain": 10.0, "efficiency": 20.0},
                {"input_power_dut": -5.0, "output_power_dut": 5.0, "gain": 10.0, "efficiency": 30.0},
            ],
            "compression_point": {"output_power": 5.0, "input_power": -5.0, "gain": 10.0, "efficiency": 30.0},
        },
        "1.2": {
            "sweep_data": [
                {"input_power_dut": -10.0, "output_power_dut": 1.0, "gain": 11.0, "efficiency": 22.0},
                {"input_power_dut": -5.0, "output_power_dut": 6.0, "gain": 11.0, "efficiency": 32.0},
            ],
            "compression_point": {"output_power": 6.0, "input_power": -5.0, "gain": 11.0, "efficiency": 32.0},
        },
    },
}


@unittest.skipUnless(rendering_available(), "当前环境无法渲染 matplotlib 图像")
class VisualizationRenderingTests(unittest.TestCase):
    def test_summary_report_renders_plots_csv_and_html(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = PathService(Path(directory))
            output_dir = paths.test_results_dir / "out"
            visualizer = DataVisualization(output_dir=output_dir)

            report = visualizer.create_summary_report(SAMPLE_DATA)

            self.assertTrue(report.is_file())
            self.assertGreater(report.stat().st_size, 0)
            self.assertTrue((output_dir / "full_sweep_data.csv").is_file())
            for name in ("p_in_vs_p_out", "p_out_vs_gain", "p_out_vs_efficiency"):
                png = output_dir / f"{name}.png"
                pdf = output_dir / f"{name}.pdf"
                self.assertTrue(png.is_file(), name)
                self.assertGreater(png.stat().st_size, 0)
                self.assertTrue(pdf.is_file(), name)
                self.assertGreater(pdf.stat().st_size, 0)

            html = report.read_text(encoding="utf-8")
            self.assertIn("p_in_vs_p_out.png", html)
            for name, path in {
                "p_in_vs_p_out": output_dir / "p_in_vs_p_out.png",
                "p_out_vs_gain": output_dir / "p_out_vs_gain.png",
                "p_out_vs_efficiency": output_dir / "p_out_vs_efficiency.png",
            }.items():
                self.assertTrue(path.is_file(), name)

    def test_individual_plot_returns_written_png(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = PathService(Path(directory))
            visualizer = DataVisualization(output_dir=paths.test_results_dir / "out")
            png = visualizer.plot_p_in_vs_p_out(SAMPLE_DATA)
            self.assertTrue(png.is_file())
            self.assertGreater(png.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
