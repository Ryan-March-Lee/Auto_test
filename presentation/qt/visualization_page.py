"""历史结果加载、频率浏览和报告入口。"""

from __future__ import annotations

import json
import math
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from result_reading import (
    normalize_frequency_key, load_measurement_result, parse_result_model,
    get_sweep_dataframe_data,
)

from .realtime_plot import RealTimePlotWidget


def _canonicalize_records(records: dict[Any, Any]) -> dict[str, Any]:
    canonical: dict[str, Any] = {}
    for frequency, record in records.items():
        key = normalize_frequency_key(frequency)
        if key is None or not math.isfinite(float(key)):
            raise ValueError(f"包含非法频率: {frequency}")
        if not isinstance(record, dict):
            raise ValueError(f"频率 {frequency} 的结果必须是对象")
        if key in canonical:
            raise ValueError(f"重复频率: {frequency}")
        canonical[key] = record
    return canonical


def convert_power_mapping(data: dict[str, Any]) -> tuple[dict[str, Any], list[float]]:
    """将历史驱动映射格式转换为绘图使用的统一频率记录。"""
    mapping = data.get("power_mapping")
    if not isinstance(mapping, dict):
        raise ValueError("不支持的数据格式")
    _canonicalize_records(mapping)
    model = parse_result_model(data)
    converted: dict[str, Any] = {}
    for point in model.points:
        key = normalize_frequency_key(point.frequency_hz)
        values = (point.input_power_dbm, point.compensated_output_power_dbm)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("驱动映射包含非有限数值")
        sweep = converted.setdefault(key, {"sweep_data": {
            "input_power_sg": [], "output_power_driver": [], "gain": [],
        }})["sweep_data"]
        sweep["input_power_sg"].append(values[0])
        sweep["output_power_driver"].append(values[1])
        sweep["gain"].append(values[1] - values[0])
    return converted, sorted(float(key) for key in converted)


def normalize_visualization_data(data: Any) -> tuple[dict[str, Any], list[float], str]:
    """规范化功放或驱动映射结果，并返回结果类型。"""
    if not isinstance(data, dict):
        raise ValueError("数据内容必须是对象")
    if isinstance(data.get("legacy_payload"), dict):
        data = data["legacy_payload"]
    results = data.get("results")
    if isinstance(results, dict):
        canonical = _canonicalize_records(results)
        return canonical, sorted(float(key) for key in canonical), "amplifier"
    converted, frequencies = convert_power_mapping(data)
    return converted, frequencies, "driver_mapping"


class VisualizationPage(QWidget):
    """独立持有历史结果状态，不访问仪器或 measurement worker。"""

    def __init__(
        self,
        *,
        config_provider: Callable[[], dict[str, Any]] | None = None,
        report_factory: Callable[[], Any] | None = None,
        temp_dir: Path | None = None,
        results_dir: Path | None = None,
        log_callback: Callable[[str], Any] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.config_provider = config_provider or (lambda: {})
        self.report_factory = report_factory
        self.temp_dir = Path(temp_dir) if temp_dir else None
        self.results_dir = Path(results_dir) if results_dir else None
        self.log_callback = log_callback or (lambda _message: None)
        self.loaded_data: dict[str, Any] | None = None
        self.loaded_result_type: str | None = None
        self.frequency_list: list[float] = []
        self.current_freq_index = 0
        self.loaded_filename = ""
        self.loaded_source = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        controls = QGroupBox("可视化控制")
        control_layout = QVBoxLayout(controls)
        file_layout = QHBoxLayout()
        self.load_data_btn = QPushButton("加载测试数据")
        self.load_data_btn.clicked.connect(lambda _checked=False: self.load_test_data())
        file_layout.addWidget(self.load_data_btn)
        self.generate_report_btn = QPushButton("生成报告")
        self.generate_report_btn.clicked.connect(self.generate_report)
        file_layout.addWidget(self.generate_report_btn)
        control_layout.addLayout(file_layout)

        frequency_layout = QHBoxLayout()
        frequency_layout.addWidget(QLabel("频率切换:"))
        self.freq_prev_btn = QPushButton("◀ 上一个")
        self.freq_prev_btn.setEnabled(False)
        self.freq_prev_btn.clicked.connect(self.prev_frequency)
        frequency_layout.addWidget(self.freq_prev_btn)
        self.freq_label = QLabel("未加载数据")
        self.freq_label.setAlignment(Qt.AlignCenter)
        frequency_layout.addWidget(self.freq_label)
        self.freq_next_btn = QPushButton("下一个 ▶")
        self.freq_next_btn.setEnabled(False)
        self.freq_next_btn.clicked.connect(self.next_frequency)
        frequency_layout.addWidget(self.freq_next_btn)
        frequency_layout.addStretch()
        control_layout.addLayout(frequency_layout)
        layout.addWidget(controls)

        self.data_plot_widget = RealTimePlotWidget()
        layout.addWidget(self.data_plot_widget)

    def _log(self, message: str):
        self.log_callback(message)

    def _set_empty_state(self, message: str):
        self.loaded_data = None
        self.loaded_result_type = None
        self.frequency_list = []
        self.current_freq_index = 0
        self.loaded_filename = ""
        self.loaded_source = None
        for axis in self.data_plot_widget.figure.axes:
            axis.clear()
        self.data_plot_widget.canvas.draw_idle()
        self.freq_label.setText(message)
        self.freq_prev_btn.setEnabled(False)
        self.freq_next_btn.setEnabled(False)

    def load_test_data(self, file_path: str | None = None):
        if file_path is None:
            file_path, _ = QFileDialog.getOpenFileName(
                self, "选择测试数据文件", "", "JSON files (*.json);;All files (*.*)"
            )
        if not file_path:
            return False
        try:
            source = load_measurement_result(file_path)
            data, frequencies, result_type = normalize_visualization_data(source)
            if not frequencies:
                raise ValueError("未找到有效的频率数据")
            self.loaded_data = data
            self.loaded_result_type = result_type
            self.frequency_list = frequencies
            self.current_freq_index = 0
            self.loaded_filename = Path(file_path).name
            self.loaded_source = source
            self.update_frequency_display()
            self.display_current_frequency_data()
            label = "功放测试" if result_type == "amplifier" else "驱动映射"
            self._log(f"已加载{label}数据: {self.loaded_filename}")
            return True
        except Exception as error:
            self._log(f"数据加载失败: {error}")
            self._set_empty_state("数据加载失败")
            return False

    def update_frequency_display(self):
        if self.frequency_list:
            frequency = self.frequency_list[self.current_freq_index]
            self.freq_label.setText(
                f"{frequency} GHz ({self.current_freq_index + 1}/{len(self.frequency_list)})"
            )
            self.freq_prev_btn.setEnabled(self.current_freq_index > 0)
            self.freq_next_btn.setEnabled(self.current_freq_index < len(self.frequency_list) - 1)
        else:
            self.freq_label.setText("未加载数据")

    def display_current_frequency_data(self):
        if self.loaded_data is None or not self.frequency_list:
            return
        key = normalize_frequency_key(self.frequency_list[self.current_freq_index])
        record = self.loaded_data.get(key) if key else None
        if isinstance(record, dict):
            sweep = record.get("sweep_data", {})
            if isinstance(sweep, list):
                rows = get_sweep_dataframe_data({"results": {key: record}}).get(key, [])
                columns = {column for row in rows for column in row}
                sweep = {column: [row.get(column) for row in rows] for column in columns}
            self.data_plot_widget.update_plot(
                {"frequency": self.frequency_list[self.current_freq_index], "sweep_data": sweep}
            )
            self._log(f"显示频率 {self.frequency_list[self.current_freq_index]} GHz 的数据")

    def prev_frequency(self):
        if self.frequency_list and self.current_freq_index > 0:
            self.current_freq_index -= 1
            self.update_frequency_display()
            self.display_current_frequency_data()

    def next_frequency(self):
        if self.frequency_list and self.current_freq_index < len(self.frequency_list) - 1:
            self.current_freq_index += 1
            self.update_frequency_display()
            self.display_current_frequency_data()

    def generate_report(self):
        if self.loaded_result_type == "driver_mapping":
            QMessageBox.warning(self, "报告生成", "驱动映射数据不支持功放测试报告")
            return False
        try:
            if self.report_factory is None:
                from data_visualization import DataVisualization
                factory = DataVisualization
            else:
                factory = self.report_factory
            visualizer = factory()
            if self.loaded_data is not None:
                if not self.loaded_data:
                    raise ValueError("结果中没有可生成报告的扫描点")
                report_data = self.loaded_source or {
                    "results": self.loaded_data,
                    "measurement_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "config": self.config_provider(),
                    "original_filename": self.loaded_filename,
                }
                temp_dir = self.temp_dir or Path.cwd()
                temp_dir.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=temp_dir, suffix=".json", delete=False) as stream:
                    temp_file = Path(stream.name)
                try:
                    with open(temp_file, "w", encoding="utf-8") as stream:
                        json.dump(report_data, stream, indent=2, ensure_ascii=False)
                    visualizer.create_summary_report(str(temp_file), self.loaded_filename)
                finally:
                    temp_file.unlink(missing_ok=True)
                self._log("基于已加载数据生成测试报告")
            else:
                results_dir = self.results_dir or Path.cwd()
                files = sorted(
                    results_dir.glob("amplifier_measurement_*.json"),
                    key=lambda path: path.stat().st_mtime,
                )
                if not files:
                    QMessageBox.warning(self, "报告生成", "未找到测试数据文件，请先加载数据或进行测试")
                    return False
                visualizer.create_summary_report(str(files[-1]))
                self._log(f"基于文件 {files[-1].name} 生成测试报告")
            QMessageBox.information(self, "报告生成", "测试报告已生成完成")
            return True
        except Exception as error:
            self._log(f"报告生成失败: {error}")
            QMessageBox.warning(self, "报告生成", f"报告生成失败: {error}")
            return False
