"""步骤 11：导出请求与导出用例的独立测试（不需要 Qt）。

覆盖：``ExportRequest`` 的结构约束、导出只能针对历史列表中的结果标识、格式适用性基于
正式结果 DTO 判断、报告端口必须返回真实产物位置，以及结果 DTO 不承载界面文案。
"""

import ast
import unittest
from dataclasses import fields
from datetime import datetime
from pathlib import Path

from application.dto.export_requests import (
    ExportFormat,
    ExportOutcome,
    ExportRequest,
)
from application.dto.measurement_results import (
    AmplifierFrequencyResult,
    AmplifierMeasurementResult,
    CableLossMeasurementResult,
)
from application.export_service import ExportRequestError, ResultExportUseCase
from application.ports.gui_data_service import LoadedResultDto, ResultFileDto

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "20261027T010203Z_abcdef12"
AMPLIFIER_ID = f"run::{RUN_ID}::inputs/amplifier_measurement.json"
CABLE_LOSS_ID = f"run::{RUN_ID}::inputs/cable_loss.json"


def _row(result_id: str, name: str) -> ResultFileDto:
    return ResultFileDto(
        result_id=result_id,
        filename=name,
        file_type="功放测试",
        modified_at=datetime(2026, 10, 27, 1, 2, 3),
        run_id=RUN_ID,
    )


def _amplifier() -> AmplifierMeasurementResult:
    return AmplifierMeasurementResult(
        run_id=RUN_ID,
        frequencies=(
            AmplifierFrequencyResult(
                frequency_ghz="5.2",
                sweep_data={"input_power_dut": [-10.0], "output_power_dut": [1.0]},
            ),
        ),
    )


def _cable_loss() -> CableLossMeasurementResult:
    return CableLossMeasurementResult(run_id=RUN_ID)


class _FakeHistory:
    def __init__(self, results):
        self.results = dict(results)
        self.loaded_ids: list[str] = []

    def list_results(self):
        return [
            _row(result_id, result_id.rsplit("/", 1)[-1])
            for result_id in self.results
        ]

    def load(self, result_id):
        self.loaded_ids.append(result_id)
        try:
            return self.results[result_id]
        except KeyError as error:
            raise FileNotFoundError(result_id) from error

    def read_bytes(self, result_id):  # pragma: no cover - 导出走端口
        raise AssertionError("导出用例不直接读取结果字节")

    def choose_and_load(self):  # pragma: no cover
        raise AssertionError("导出用例不打开文件对话框")

    def latest_result(self, result_type):  # pragma: no cover
        raise AssertionError("导出用例不查询最新结果")


class _FakeExports:
    def __init__(self, report_path="reports/report/test_report.html"):
        self.calls: list[tuple[str, ...]] = []
        self.report_path = report_path

    def export_json(self, result_id, target):
        self.calls.append(("json", result_id, target))

    def export_csv(self, result_id, target):
        self.calls.append(("csv", result_id, target))

    def export_report(self, result_id):
        self.calls.append(("report", result_id))
        return self.report_path


class ExportRequestTests(unittest.TestCase):
    def test_json_and_csv_require_a_target_file(self):
        for export_format in (ExportFormat.JSON, ExportFormat.CSV):
            with self.subTest(format=export_format):
                for target in (None, "", "   "):
                    with self.assertRaises(ValueError):
                        ExportRequest(
                            result_id=AMPLIFIER_ID, format=export_format, target=target
                        )

    def test_report_must_not_carry_a_target(self):
        with self.assertRaises(ValueError):
            ExportRequest(
                result_id=AMPLIFIER_ID,
                format=ExportFormat.REPORT,
                target="report.html",
            )

    def test_result_id_and_format_are_validated(self):
        with self.assertRaises(ValueError):
            ExportRequest(result_id="", format=ExportFormat.REPORT)
        with self.assertRaises(ValueError):
            ExportRequest(result_id=AMPLIFIER_ID, format="report")

    def test_format_metadata_matches_the_actual_artifact(self):
        self.assertEqual(ExportFormat.JSON.suffix, "json")
        self.assertEqual(ExportFormat.CSV.suffix, "csv")
        self.assertIsNone(ExportFormat.REPORT.suffix)
        self.assertFalse(ExportFormat.REPORT.needs_target)
        self.assertEqual(ExportFormat.REPORT.label, "测试报告")
        # 报告产物是 HTML 汇总报告，格式名称不再借用 PDF。
        self.assertNotIn("pdf", {item.value for item in ExportFormat})

    def test_outcome_carries_structured_data_not_ui_wording(self):
        self.assertEqual(
            [field.name for field in fields(ExportOutcome)],
            ["result_id", "format", "filename", "artifact_path"],
        )
        with self.assertRaises(ValueError):
            ExportOutcome(
                result_id=AMPLIFIER_ID,
                format=ExportFormat.REPORT,
                filename="amplifier_measurement.json",
                artifact_path="  ",
            )


class ResultExportUseCaseTests(unittest.TestCase):
    def _use_case(self, results, exports=None):
        exports = exports or _FakeExports()
        return ResultExportUseCase(history=_FakeHistory(results), exports=exports), exports

    def test_json_export_copies_the_history_result(self):
        use_case, exports = self._use_case(
            {AMPLIFIER_ID: LoadedResultDto(AMPLIFIER_ID, "amplifier_measurement.json", _amplifier())}
        )

        outcome = use_case.execute(
            ExportRequest(
                result_id=AMPLIFIER_ID,
                format=ExportFormat.JSON,
                target="C:/out/result.json",
            )
        )

        self.assertEqual(exports.calls, [("json", AMPLIFIER_ID, "C:/out/result.json")])
        self.assertIsInstance(outcome, ExportOutcome)
        self.assertEqual(outcome.filename, "amplifier_measurement.json")
        self.assertEqual(outcome.artifact_path, "C:/out/result.json")

    def test_csv_export_requires_an_amplifier_result(self):
        use_case, exports = self._use_case(
            {
                AMPLIFIER_ID: LoadedResultDto(AMPLIFIER_ID, "amplifier_measurement.json", _amplifier()),
                CABLE_LOSS_ID: LoadedResultDto(CABLE_LOSS_ID, "cable_loss.json", _cable_loss()),
            }
        )

        with self.assertRaises(ExportRequestError):
            use_case.execute(
                ExportRequest(
                    result_id=CABLE_LOSS_ID,
                    format=ExportFormat.CSV,
                    target="C:/out/result.csv",
                )
            )
        self.assertEqual(exports.calls, [])

        use_case.execute(
            ExportRequest(
                result_id=AMPLIFIER_ID,
                format=ExportFormat.CSV,
                target="C:/out/result.csv",
            )
        )
        self.assertEqual(exports.calls, [("csv", AMPLIFIER_ID, "C:/out/result.csv")])

    def test_report_export_returns_the_actual_artifact_path(self):
        use_case, exports = self._use_case(
            {AMPLIFIER_ID: LoadedResultDto(AMPLIFIER_ID, "amplifier_measurement.json", _amplifier())}
        )

        outcome = use_case.execute(
            ExportRequest(result_id=AMPLIFIER_ID, format=ExportFormat.REPORT)
        )

        self.assertEqual(exports.calls, [("report", AMPLIFIER_ID)])
        self.assertEqual(outcome.artifact_path, "reports/report/test_report.html")

    def test_report_port_must_return_a_real_location(self):
        for value in (None, "", "   ", 42):
            exports = _FakeExports(report_path=value)
            use_case = ResultExportUseCase(
                history=_FakeHistory(
                    {
                        AMPLIFIER_ID: LoadedResultDto(
                            AMPLIFIER_ID, "amplifier_measurement.json", _amplifier()
                        )
                    }
                ),
                exports=exports,
            )
            with self.subTest(returned=value):
                with self.assertRaises(ExportRequestError):
                    use_case.execute(
                        ExportRequest(
                            result_id=AMPLIFIER_ID, format=ExportFormat.REPORT
                        )
                    )

    def test_results_outside_the_history_list_are_rejected(self):
        use_case, exports = self._use_case({})

        with self.assertRaises(ExportRequestError):
            use_case.execute(
                ExportRequest(
                    result_id="C:/any/path/result.json",
                    format=ExportFormat.JSON,
                    target="C:/out/result.json",
                )
            )
        self.assertEqual(exports.calls, [])

    def test_only_export_requests_are_accepted(self):
        use_case, _exports = self._use_case({})

        with self.assertRaises(ExportRequestError):
            use_case.execute({"result_id": AMPLIFIER_ID, "format": "json"})

    def test_history_read_errors_become_export_errors(self):
        class _BrokenHistory(_FakeHistory):
            def load(self, result_id):
                raise ValueError("结果文件类型与 manifest 登记不一致")

        exports = _FakeExports()
        use_case = ResultExportUseCase(history=_BrokenHistory({AMPLIFIER_ID: None}), exports=exports)

        with self.assertRaises(ExportRequestError):
            use_case.execute(
                ExportRequest(
                    result_id=AMPLIFIER_ID,
                    format=ExportFormat.JSON,
                    target="C:/out/result.json",
                )
            )
        self.assertEqual(exports.calls, [])


class ExportLayeringTests(unittest.TestCase):
    """应用层只产出结构化结果：用户可见文案由界面层生成。"""

    @staticmethod
    def _runtime_strings(path):
        """返回模块里非文档字符串的字符串常量（即真正参与逻辑的文本）。"""
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = set()
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                continue
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
        return {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        }

    def test_export_service_does_not_build_ui_wording(self):
        runtime = self._runtime_strings(ROOT / "application" / "export_service.py")

        # 异常说明属于应用层；成功/失败提示与“产物已生成/已导出”文案属于界面层。
        for wording in ("导出成功", "导出失败", "已导出到", "报告已生成"):
            offenders = [text for text in runtime if wording in text]
            self.assertEqual(
                offenders, [], f"应用层不得拼接界面文案（{wording}）: {offenders}"
            )

    def test_export_page_owns_the_user_wording(self):
        runtime = self._runtime_strings(ROOT / "presentation" / "qt" / "export_page.py")

        self.assertTrue(
            any("导出到" in text for text in runtime),
            "导出成功文案应由页面生成",
        )
        self.assertTrue(
            any("测试报告已生成" in text for text in runtime),
            "报告成功文案应由页面生成",
        )

    def test_outcome_dto_has_no_message_field(self):
        tree = ast.parse(
            (ROOT / "application" / "dto" / "export_requests.py").read_text(
                encoding="utf-8"
            )
        )
        outcome = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "ExportOutcome"
        )
        annotated = [
            node.target.id
            for node in outcome.body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        ]

        self.assertNotIn("message", annotated)


if __name__ == "__main__":
    unittest.main()
