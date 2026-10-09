import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from presentation.qt.export_page import ExportPage, list_result_files
from presentation.qt.visualization_page import convert_power_mapping, normalize_visualization_data


class Phase33DataBoundaryTests(unittest.TestCase):
    def test_amplifier_results_are_sorted_by_numeric_frequency(self):
        data, frequencies, kind = normalize_visualization_data(
            {"results": {"5.2": {}, "4.8": {}, "10": {}}}
        )
        self.assertEqual(frequencies, [4.8, 5.2, 10.0])
        self.assertEqual(kind, "amplifier")
        self.assertEqual(set(data), {"5.2", "4.8", "10.0"})

    def test_driver_mapping_is_converted_to_sweep_data(self):
        data, frequencies = convert_power_mapping(
            {"power_mapping": {"4.8": {"-20": -10, "-10": 1}}}
        )
        self.assertEqual(frequencies, [4.8])
        self.assertEqual(data["4.8"]["sweep_data"]["gain"], [10.0, 11.0])

    def test_driver_mapping_accepts_nested_output_and_canonicalizes_frequency(self):
        data, frequencies = convert_power_mapping(
            {"power_mapping": {"4.800": {"-20": {"actual_output_power": -9.5}}}}
        )
        self.assertEqual(frequencies, [4.8])
        self.assertEqual(data["4.8"]["sweep_data"]["output_power_driver"], [-9.5])

    def test_invalid_and_empty_data_are_rejected(self):
        with self.assertRaises(ValueError):
            normalize_visualization_data({"unknown": {}})
        with self.assertRaises(ValueError):
            normalize_visualization_data({"results": {"invalid": {}}})
        data, frequencies, _ = normalize_visualization_data({"results": {}})
        self.assertEqual(data, {})
        self.assertEqual(frequencies, [])

    def test_result_files_are_grouped_and_sorted_by_mtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cable = root / "cable_loss.json"
            cable.write_text("{}", encoding="utf-8")
            older = root / "amplifier_measurement_old.json"
            newer = root / "amplifier_measurement_new.json"
            older.write_text("{}", encoding="utf-8")
            newer.write_text("{}", encoding="utf-8")
            os.utime(older, (100, 100))
            os.utime(newer, (200, 200))
            files = list_result_files(root, cable)
            self.assertEqual(
                [path.name for path, _ in files],
                ["cable_loss.json", "amplifier_measurement_new.json", "amplifier_measurement_old.json"],
            )

    def test_result_file_listing_ignores_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cable = root / "cable_loss.json"
            cable.write_text("{}", encoding="utf-8")
            (root / "amplifier_measurement_directory.json").mkdir()
            files = list_result_files(root, cable)
            self.assertEqual([item.path.name for item in files], ["cable_loss.json"])


class Phase33ExportTests(unittest.TestCase):
    def test_fake_visualizer_exports_csv_and_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "amplifier_measurement_1.json"
            source.write_text('{"results": {"4.8": {}}}', encoding="utf-8")
            output = root / "output.csv"

            class FakeVisualizer:
                def __init__(self):
                    self.output_dir = root / "generated"
                    self.output_dir.mkdir(exist_ok=True)
                    self.csv_called = False
                    self.pdf_called = False

                def generate_csv_report(self, path):
                    self.csv_called = path == str(source)
                    (self.output_dir / "full_sweep_data.csv").write_text("ok", encoding="utf-8")

                def create_summary_report(self, path):
                    self.pdf_called = path == str(source)

            visualizers = []

            def factory():
                visualizer = FakeVisualizer()
                visualizers.append(visualizer)
                return visualizer

            from PySide6.QtWidgets import QApplication

            app = QApplication.instance() or QApplication([])
            page = ExportPage(results_dir=root, cable_loss_file=root / "cable_loss.json", visualizer_factory=factory)
            page.file_table.selectRow(0)
            with patch("presentation.qt.export_page.QFileDialog.getSaveFileName", return_value=(str(output), "CSV files (*.csv)")), patch("presentation.qt.export_page.QMessageBox.information"):
                self.assertTrue(page.export_csv())
            self.assertEqual(output.read_text(encoding="utf-8"), "ok")
            self.assertTrue(visualizers[0].csv_called)
            with patch("presentation.qt.export_page.QMessageBox.information"), patch("presentation.qt.export_page.QMessageBox.warning"):
                self.assertTrue(page.export_pdf())
            self.assertTrue(visualizers[-1].pdf_called)


if __name__ == "__main__":
    unittest.main()
