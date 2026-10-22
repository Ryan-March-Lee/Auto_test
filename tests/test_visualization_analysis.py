"""数据可视化迁移后的行为回归。

覆盖纯分析入口（analysis.visualization）与历史根入口（data_visualization）的
CSV/HTML 报告装配、HTML 转义、唯一输出目录、结果文件读取与转发身份。

注意：当前测试环境中的 matplotlib/numpy 在渲染图像时会触发原生崩溃，因此这里的
用例替换绘图方法，只验证报告装配与数据契约；真实渲染由
``tests/test_visualization_rendering.py`` 在可用环境中验证。
"""

import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from analysis.visualization import DataVisualization as AnalysisDataVisualization
from infrastructure.filesystem.paths import PathService


PLOT_METHODS = (
    "plot_p_in_vs_p_out",
    "plot_p_out_vs_gain",
    "plot_p_out_vs_efficiency",
    "plot_p_out_vs_eff_and_gain_combined",
    "plot_freq_vs_saturation_metrics",
    "plot_freq_vs_sat_power_and_gain",
    "plot_freq_vs_all_sat_metrics_combined",
)


def _sample_data(run_id: str = "viz-run", original_filename: str = "amplifier_measurement_sample.json") -> dict:
    return {
        "run_id": run_id,
        "result_type": "amplifier_measurement",
        "measurement_time": "2026-01-01 00:00:00",
        "original_filename": original_filename,
        "config": {"frequencies": [1.0]},
        "results": {
            "1.0": {
                "sweep_data": [
                    {"input_power_dut": -10.0, "output_power_dut": 0.0, "gain": 10.0, "efficiency": 20.0},
                    {"input_power_dut": -5.0, "output_power_dut": 5.0, "gain": 10.0, "efficiency": 30.0},
                ],
                "compression_point": {
                    "output_power": 5.0,
                    "input_power": -5.0,
                    "gain": 10.0,
                    "efficiency": 30.0,
                },
            }
        },
    }


def _fake_plot(base_name):
    def method(self, data):
        self._ensure_output_dir()
        target = self.output_dir / f"{base_name}.png"
        target.write_bytes(b"png")
        return target

    return method


def _patch_plots():
    """替换全部绘图方法，返回上下文管理器。"""
    stack = ExitStack()
    for name in PLOT_METHODS:
        stack.enter_context(
            patch.object(AnalysisDataVisualization, name, _fake_plot(name))
        )
    return stack


class VisualizationAnalysisTests(unittest.TestCase):
    def _visualizer(self, directory):
        paths = PathService(Path(directory))
        return AnalysisDataVisualization(output_dir=paths.test_results_dir / "out")

    def test_output_dir_is_not_created_until_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "not_yet"
            visualizer = AnalysisDataVisualization(output_dir=target)
            self.assertFalse(target.exists())
            visualizer.generate_csv_report(_sample_data())
            self.assertTrue(target.exists())

    def test_csv_report_is_generated_from_in_memory_data(self):
        with tempfile.TemporaryDirectory() as directory:
            visualizer = self._visualizer(directory)
            csv_path = visualizer.generate_csv_report(_sample_data())
            self.assertTrue(csv_path.is_file())
            text = csv_path.read_text(encoding="utf-8-sig")
            self.assertIn("frequency_ghz", text)
            self.assertIn("1.0", text)

    def test_csv_report_returns_none_without_sweep_data(self):
        with tempfile.TemporaryDirectory() as directory:
            visualizer = self._visualizer(directory)
            self.assertIsNone(visualizer.generate_csv_report({"results": {}}))

    def test_summary_report_assembles_html_from_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            visualizer = self._visualizer(directory)
            with _patch_plots():
                report = visualizer.create_summary_report(_sample_data())
            self.assertTrue(report.is_file())
            html = report.read_text(encoding="utf-8")
            self.assertIn("amplifier_measurement_sample.json", html)
            self.assertIn("full_sweep_data.csv", html)
            self.assertIn("frequencies", html)
            self.assertTrue((visualizer.output_dir / "plot_p_in_vs_p_out.png").is_file())

    def test_summary_report_uses_domain_model_input(self):
        from domain.result_reading import parse_result_model

        with tempfile.TemporaryDirectory() as directory:
            visualizer = self._visualizer(directory)
            model = parse_result_model(_sample_data("model-run"))
            with _patch_plots():
                report = visualizer.create_summary_report(model)
            html = report.read_text(encoding="utf-8")
            self.assertEqual(model.run_id, "model-run")
            self.assertIn("amplifier_measurement_sample.json", html)

    def test_summary_report_escapes_unsafe_html(self):
        with tempfile.TemporaryDirectory() as directory:
            visualizer = self._visualizer(directory)
            with _patch_plots():
                report = visualizer.create_summary_report(
                    _sample_data(original_filename='<img src=x onerror=alert(1)>.json'),
                    original_filename='<img src=x onerror=alert(1)>.json',
                )
            html = report.read_text(encoding="utf-8")
            self.assertNotIn("<img src=x", html)
            self.assertIn("&lt;img", html)

    def test_summary_report_rejects_empty_points(self):
        with tempfile.TemporaryDirectory() as directory:
            visualizer = self._visualizer(directory)
            with self.assertRaises(ValueError):
                visualizer.create_summary_report({"run_id": "empty", "results": {}})

    def test_summary_report_does_not_trigger_baseline_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            visualizer = self._visualizer(directory)
            with patch("collect_baseline.collect_baseline") as baseline, _patch_plots():
                visualizer.create_summary_report(_sample_data())
            baseline.assert_not_called()

    def test_analysis_module_has_no_infrastructure_imports(self):
        source = (
            Path(__file__).resolve().parents[1] / "analysis" / "visualization.py"
        ).read_text(encoding="utf-8-sig")
        self.assertNotIn("from infrastructure", source)
        self.assertNotIn("import infrastructure", source)
        self.assertNotIn("collect_baseline", source)


class ReportInputTests(unittest.TestCase):
    def test_report_model_builder_normalizes_and_overrides(self):
        from application.inputs.result_reading import build_report_model
        from domain.models import MeasurementResult

        model = build_report_model(
            _sample_data(),
            configuration={"freq": 4.8},
            original_filename="amp.json",
            measurement_time="2026-02-02 00:00:00",
        )
        self.assertIsInstance(model, MeasurementResult)
        self.assertTrue(model.points)
        self.assertEqual(model.metadata.measurement_time, "2026-02-02 00:00:00")
        self.assertEqual(dict(model.plan_snapshot), {"freq": 4.8})
        self.assertEqual(model.raw_readings.get("original_filename"), "amp.json")

    def test_report_model_builder_accepts_domain_model(self):
        from application.inputs.result_reading import build_report_model
        from domain.result_reading import parse_result_model

        model = parse_result_model(_sample_data())
        rebuilt = build_report_model(model, original_filename="amp.json")
        self.assertTrue(rebuilt.points)
        self.assertEqual(rebuilt.raw_readings.get("original_filename"), "amp.json")

    def test_report_model_builder_rejects_invalid_input(self):
        from application.inputs.result_reading import build_report_model

        with self.assertRaises(ValueError):
            build_report_model({"results": {}})
        with self.assertRaises(ValueError):
            build_report_model("not-a-mapping")

    def test_unique_directory_name_avoids_collisions(self):
        from infrastructure.filesystem.paths import unique_directory_name

        names = {unique_directory_name("report") for _ in range(50)}
        self.assertEqual(len(names), 50)


class VisualizationCompatibilityTests(unittest.TestCase):
    def test_legacy_root_forwards_to_assembly_adapter(self):
        import app.legacy_visualization
        import data_visualization

        self.assertEqual(
            data_visualization.__all__,
            ["DataVisualization", "load_measurement_result", "load_result_model"],
        )
        self.assertIs(
            data_visualization.DataVisualization,
            app.legacy_visualization.DataVisualization,
        )
        self.assertIs(
            data_visualization.load_measurement_result,
            app.legacy_visualization.load_measurement_result,
        )

    def test_legacy_adapter_uses_composition_not_inheritance(self):
        import app.legacy_visualization

        self.assertFalse(
            issubclass(
                app.legacy_visualization.DataVisualization,
                AnalysisDataVisualization,
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            paths = PathService(Path(directory))
            visualizer = app.legacy_visualization.DataVisualization(paths=paths)
            self.assertIsInstance(visualizer._analysis, AnalysisDataVisualization)

    def test_legacy_adapter_allocates_unique_output_dirs(self):
        import app.legacy_visualization

        with tempfile.TemporaryDirectory() as directory:
            paths = PathService(Path(directory))
            first = app.legacy_visualization.DataVisualization(paths=paths)
            second = app.legacy_visualization.DataVisualization(paths=paths)
            self.assertNotEqual(first.output_dir, second.output_dir)

    def test_legacy_adapter_reads_result_files_and_reports(self):
        import data_visualization

        with tempfile.TemporaryDirectory() as directory:
            paths = PathService(Path(directory))
            source = paths.test_results_dir / "amplifier_measurement_sample.json"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(json.dumps(_sample_data()), encoding="utf-8")

            visualizer = data_visualization.DataVisualization(
                paths=paths, output_dir=paths.test_results_dir / "out"
            )
            self.assertEqual(visualizer.load_data(str(source))["run_id"], "viz-run")
            self.assertEqual(visualizer.load_result(str(source)).run_id, "viz-run")
            with _patch_plots():
                report = visualizer.create_summary_report(str(source))
            self.assertTrue(report.is_file())
            self.assertIn(
                "amplifier_measurement_sample.json",
                report.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
