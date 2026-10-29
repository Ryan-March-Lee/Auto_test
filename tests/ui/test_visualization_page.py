"""步骤 8：可视化页只保留浏览，报告副本走唯一的应用层导出用例。

覆盖：

- 报告只针对**当前已加载结果**执行：页面提交 ``ExportRequest(format=REPORT)``，来源是
  已加载结果的历史标识，不再回退到“最新结果”；
- 未加载结果、未装配导出用例、导出用例拒绝（例如非主功放结果）都明确提示并返回失败，
  页面自己不再比较结果类型、也不再拼接报告规则；
- 加载、频率浏览与绘图仍按视图模型工作，加载失败回到空状态。
"""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from application.dto import (
    AmplifierFrequencyResult,
    AmplifierMeasurementResult,
    DriverPowerPoint,
    DriverPowerMappingMeasurementResult,
)
from application.dto.export_requests import ExportFormat, ExportOutcome
from application.ports.gui_data_service import LoadedResultDto
from presentation.qt.history_view_model import HistoryViewModel
from presentation.qt.visualization_page import VisualizationPage


RUN_ID = "20261028T010203Z_abcdef12"
RESULT_ID = f"run::{RUN_ID}::results/amplifier_measurement.json"


def _amplifier_result() -> AmplifierMeasurementResult:
    return AmplifierMeasurementResult(
        run_id=RUN_ID,
        frequencies=(
            AmplifierFrequencyResult(
                frequency_ghz="4.8",
                sweep_data={"input_power_dut": [-20.0], "output_power_dut": [-9.0]},
            ),
            AmplifierFrequencyResult(
                frequency_ghz="5.2",
                sweep_data={"input_power_dut": [-10.0], "output_power_dut": [1.0]},
            ),
        ),
    )


def _driver_result() -> DriverPowerMappingMeasurementResult:
    return DriverPowerMappingMeasurementResult(
        run_id=RUN_ID,
        points=(
            DriverPowerPoint(
                frequency_ghz="4.8", input_power_dbm=-20.0, output_power_dbm=-10.0
            ),
        ),
    )


def _amplifier_dto(result_id: str = RESULT_ID) -> LoadedResultDto:
    return LoadedResultDto(result_id, "amplifier_measurement.json", _amplifier_result())


class _FakeQuery:
    """可视化页不按列表/标识读取结果，只通过 ``chooser`` 得到已加载 DTO。"""

    def list_results(self):  # pragma: no cover - 页面不读列表
        raise AssertionError("可视化页不读历史列表")

    def load(self, result_id):  # pragma: no cover - 页面不按标识读取
        raise AssertionError("可视化页不按标识读取结果")

    def read_bytes(self, result_id):  # pragma: no cover
        raise AssertionError("可视化页不逐字节读取结果")

    def choose_and_load(self):  # pragma: no cover - 文件选择由窗口注入
        raise AssertionError("可视化页不自己打开文件对话框")

    def latest_result(self, result_type):  # pragma: no cover - 步骤 8 已移除
        raise AssertionError("可视化页不得加载“最新结果”")


class _RecordingExport:
    """记录导出请求；按需返回结构化结果或抛出导出用例的错误。"""

    def __init__(self, *, outcome=None, error=None):
        self.outcome = outcome
        self.error = error
        self.requests: list[object] = []

    def execute(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.outcome


class VisualizationPageTests(unittest.TestCase):
    def setUp(self):
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])
        # 离屏环境下模态对话框会永久阻塞：统一替身并记录调用。
        for name in ("warning", "information", "critical"):
            patcher = patch(f"presentation.qt.visualization_page.QMessageBox.{name}")
            setattr(self, f"{name}_dialog", patcher.start())
            self.addCleanup(patcher.stop)
        self.logs: list[str] = []

    def _page(self, *, chooser=None, export_service=None) -> VisualizationPage:
        return VisualizationPage(
            history=HistoryViewModel(_FakeQuery()),
            chooser=chooser,
            export_service=export_service,
            log_callback=self.logs.append,
        )

    def _page_with_loaded_result(self, export_service) -> VisualizationPage:
        page = self._page(
            chooser=lambda: _amplifier_dto(), export_service=export_service
        )
        self.assertTrue(page.load_test_data())
        return page

    # -- 报告：只针对已加载结果 ---------------------------------------------

    def test_report_requests_the_loaded_result_from_the_export_use_case(self):
        export = _RecordingExport(
            outcome=ExportOutcome(
                result_id=RESULT_ID,
                format=ExportFormat.REPORT,
                filename="amplifier_measurement.json",
                artifact_path="test_results/report-abc/report.html",
            )
        )
        page = self._page_with_loaded_result(export)

        self.assertTrue(page.generate_report())

        request = export.requests[0]
        self.assertEqual(request.result_id, RESULT_ID)
        self.assertIs(request.format, ExportFormat.REPORT)
        self.assertIsNone(request.target)
        message = self.information_dialog.call_args.args[2]
        self.assertIn("report.html", message)

    def test_report_without_a_loaded_result_never_falls_back_to_the_latest_result(self):
        export = _RecordingExport(outcome=None)
        page = self._page(export_service=export)

        self.assertFalse(page.generate_report())

        self.assertEqual(export.requests, [])
        self.warning_dialog.assert_called_once()
        self.assertIn("先加载", self.warning_dialog.call_args.args[2])
        self.assertIsNone(page.loaded_result_id)

    def test_report_without_an_export_service_is_refused(self):
        page = self._page_with_loaded_result(None)

        self.assertFalse(page.generate_report())

        self.warning_dialog.assert_called_once()
        self.assertIn("未配置报告生成服务", self.warning_dialog.call_args.args[2])

    def test_export_refusal_is_surfaced_without_page_level_report_rules(self):
        """非主功放结果由导出用例拒绝；页面只转述原因，不自己判断类型。"""
        page = self._page(
            chooser=lambda: LoadedResultDto(
                RESULT_ID, "driver_mapping.json", _driver_result()
            ),
            export_service=_RecordingExport(
                error=ValueError("只有功放测试结果支持 测试报告 导出")
            ),
        )
        self.assertTrue(page.load_test_data())

        self.assertFalse(page.generate_report())

        self.assertEqual(page.loaded_result_id, RESULT_ID)
        self.warning_dialog.assert_called_once()
        self.assertIn("只有功放测试结果支持", self.warning_dialog.call_args.args[2])
        self.assertTrue(any("报告生成失败" in line for line in self.logs))

    def test_unexpected_export_outcome_is_not_reported_as_success(self):
        export = _RecordingExport(
            outcome=ExportOutcome(
                result_id=RESULT_ID,
                format=ExportFormat.CSV,
                filename="amplifier_measurement.json",
                artifact_path="somewhere/full_sweep_data.csv",
            )
        )
        page = self._page_with_loaded_result(export)

        self.assertFalse(page.generate_report())

        self.information_dialog.assert_not_called()
        self.warning_dialog.assert_called_once()

    # -- 浏览 ---------------------------------------------------------------

    def test_loading_keeps_the_history_identifier_and_first_frequency(self):
        page = self._page(chooser=lambda: _amplifier_dto())

        self.assertTrue(page.load_test_data())

        self.assertEqual(page.loaded_result_id, RESULT_ID)
        self.assertEqual(page.loaded_filename, "amplifier_measurement.json")
        self.assertEqual(page.frequency_list, [4.8, 5.2])
        self.assertEqual(page.current_freq_index, 0)
        self.assertEqual(page.freq_label.text(), "4.8 GHz (1/2)")
        self.assertFalse(page.freq_prev_btn.isEnabled())
        self.assertTrue(page.freq_next_btn.isEnabled())

    def test_frequency_navigation_updates_the_label(self):
        page = self._page(chooser=lambda: _amplifier_dto())
        self.assertTrue(page.load_test_data())

        page.next_frequency()

        self.assertEqual(page.current_freq_index, 1)
        self.assertEqual(page.freq_label.text(), "5.2 GHz (2/2)")
        self.assertTrue(page.freq_prev_btn.isEnabled())
        self.assertFalse(page.freq_next_btn.isEnabled())

        page.prev_frequency()
        self.assertEqual(page.current_freq_index, 0)

    def test_cancelled_chooser_keeps_the_page_without_data(self):
        page = self._page(chooser=lambda: None)

        self.assertFalse(page.load_test_data())

        self.assertIsNone(page.loaded_result_id)
        self.assertEqual(page.freq_label.text(), "未加载数据")
        self.assertFalse(page.freq_next_btn.isEnabled())

    def test_load_failure_returns_to_the_empty_state(self):
        page = self._page(chooser=lambda: _amplifier_dto())
        self.assertTrue(page.load_test_data())

        page.chooser = self._raise
        self.assertFalse(page.load_test_data())

        self.assertIsNone(page.loaded_result)
        self.assertIsNone(page.loaded_result_id)
        self.assertEqual(page.freq_label.text(), "数据加载失败")
        self.assertTrue(any("数据加载失败" in line for line in self.logs))

    @staticmethod
    def _raise():
        raise ValueError("结果文件不可读")


if __name__ == "__main__":
    unittest.main()
