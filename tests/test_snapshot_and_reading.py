"""运行快照、统一保存和正式结果读取的测试。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from application.dto import measurement_result_from_document, normalize_frequency_key
from infrastructure.persistence.json_result_repository import (
    load_json_result,
    save_measurement_result,
    save_measurement_model,
    validate_result_filename,
    write_run_snapshot,
)


def _cable_loss_document(run_id: str = "reading-run") -> dict:
    return {
        "run_id": run_id,
        "measurement_type": "cable_loss",
        "status": "completed",
        "attenuator_value": 20.0,
        "cable_losses": {"1.0": {"total_path1": 10.0, "cable1": 1.0}},
    }


def _amplifier_document(run_id: str = "reading-run") -> dict:
    return {
        "run_id": run_id,
        "measurement_type": "amplifier",
        "status": "completed",
        "measurement_time": "2026-08-21 10:00:00",
        "results": {
            "1.0": {
                "sweep_data": {
                    "input_power_dut": [-10.0, -5.0],
                    "output_power_dut": [0.0, 5.0],
                    "gain": [10.0, 10.0],
                    "efficiency": [20.0, 30.0],
                },
                "compression_point": {"output_power_dbm": 5.0, "gain_db": 10.0},
                "compression_achieved": True,
            }
        },
    }


class RunSnapshotTests(unittest.TestCase):
    """运行快照写入测试。"""

    def test_write_run_snapshot_creates_directory_and_files(self):
        """快照写入应创建运行目录和三个快照文件。"""
        with tempfile.TemporaryDirectory() as directory:
            with patch("infrastructure.persistence.json_result_repository.TEST_RESULTS_DIR", Path(directory)):
                run_id = "test-run-001"
                test_plan = {"frequencies": [1.0, 2.0], "template": False}
                run_mapping = {"instruments": {}, "template": False}

                run_directory = write_run_snapshot(
                    run_id=run_id,
                    test_plan=test_plan,
                    run_mapping=run_mapping,
                    software_version="1.0.0",
                    git_commit="abc123",
                    status="running",
                )

                self.assertEqual(run_directory, Path(directory) / run_id)
                self.assertTrue(run_directory.exists())

                # 验证三个快照文件存在
                self.assertTrue((run_directory / "test_plan_snapshot.json").exists())
                self.assertTrue((run_directory / "run_mapping_snapshot.json").exists())
                self.assertTrue((run_directory / "run_metadata.json").exists())

    def test_write_run_snapshot_contains_correct_data(self):
        """快照文件应包含正确的数据内容。"""
        with tempfile.TemporaryDirectory() as directory:
            with patch("infrastructure.persistence.json_result_repository.TEST_RESULTS_DIR", Path(directory)):
                run_id = "test-run-002"
                test_plan = {"frequencies": [1.0, 2.0], "template": False}
                run_mapping = {"instruments": {"signal_generator": "SG1"}, "template": False}

                run_directory = write_run_snapshot(
                    run_id=run_id,
                    test_plan=test_plan,
                    run_mapping=run_mapping,
                    software_version="1.0.0",
                    git_commit="abc123",
                    start_time="2026-08-21T10:00:00",
                    status="running",
                )

                # 读取并验证测试方案快照
                plan_snapshot = load_json_result(run_directory / "test_plan_snapshot.json")
                self.assertEqual(plan_snapshot["frequencies"], [1.0, 2.0])
                self.assertEqual(plan_snapshot["run_id"], run_id)
                self.assertEqual(plan_snapshot["result_type"], "test_plan_snapshot")

                # 读取并验证运行映射快照
                mapping_snapshot = load_json_result(run_directory / "run_mapping_snapshot.json")
                self.assertEqual(mapping_snapshot["instruments"]["signal_generator"], "SG1")
                self.assertEqual(mapping_snapshot["run_id"], run_id)
                self.assertEqual(mapping_snapshot["result_type"], "run_mapping_snapshot")

                # 读取并验证运行元数据
                metadata = load_json_result(run_directory / "run_metadata.json")
                self.assertEqual(metadata["run_id"], run_id)
                self.assertEqual(metadata["software_version"], "1.0.0")
                self.assertEqual(metadata["git_commit"], "abc123")
                self.assertEqual(metadata["start_time"], "2026-08-21T10:00:00")
                self.assertEqual(metadata["status"], "running")
                self.assertIsNone(metadata["end_time"])
                self.assertIsNone(metadata["error"])

    def test_write_run_snapshot_rejects_duplicate_run_id(self):
        """重复的运行 ID 应抛出 FileExistsError。"""
        with tempfile.TemporaryDirectory() as directory:
            with patch("infrastructure.persistence.json_result_repository.TEST_RESULTS_DIR", Path(directory)):
                run_id = "duplicate-run"
                test_plan = {"template": False}
                run_mapping = {"template": False}

                # 第一次写入成功
                write_run_snapshot(run_id, test_plan, run_mapping)

                # 第二次写入应失败
                with self.assertRaises(FileExistsError):
                    write_run_snapshot(run_id, test_plan, run_mapping)

    def test_write_run_snapshot_removes_partial_directory_on_failure(self):
        """任一快照文件写入失败时，不应留下半成品运行目录。"""
        with tempfile.TemporaryDirectory() as directory:
            with patch("infrastructure.persistence.json_result_repository.TEST_RESULTS_DIR", Path(directory)), patch(
                "infrastructure.persistence.json_result_repository.save_json_result",
                side_effect=[None, OSError("写入失败")],
            ):
                with self.assertRaises(OSError):
                    write_run_snapshot(
                        "partial-run",
                        {"template": False},
                        {"template": False},
                    )
            self.assertFalse((Path(directory) / "partial-run").exists())


class SaveMeasurementResultTests(unittest.TestCase):
    """统一保存测量结果测试。"""

    def test_save_measurement_result_writes_only_the_run_result(self):
        """统一保存只写入运行目录内的权威结果，并生成同一 DTO 的模型工件。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("infrastructure.persistence.json_result_repository.TEST_RESULTS_DIR", root / "test_results"):
                document = _cable_loss_document("test-save-001")

                archive_path = save_measurement_result(
                    document,
                    result_type="cable_loss",
                    filename="cable_loss_results.json",
                    run_id="test-save-001",
                )

                self.assertTrue(archive_path.exists())
                self.assertEqual(
                    archive_path,
                    root / "test_results" / "test-save-001" / "cable_loss_results.json",
                )

                archive_data = load_json_result(archive_path)
                self.assertEqual(archive_data["run_id"], "test-save-001")
                self.assertEqual(
                    archive_data["cable_losses"]["1.0"]["cable1"], 1.0
                )
                self.assertNotIn("legacy_payload", archive_data)

                model_document = load_json_result(
                    archive_path.parent / "cable_loss_model.json"
                )
                canonical = model_document["canonical_model"]
                self.assertEqual(
                    measurement_result_from_document(model_document).to_dict(),
                    measurement_result_from_document(canonical).to_dict(),
                )

    def test_save_measurement_result_rejects_non_formal_schema(self):
        """非正式 schema 在落盘前报错，不会留下归档或模型文件。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_directory = root / "run"
            run_directory.mkdir()
            with self.assertRaises(ValueError):
                save_measurement_result(
                    {"run_id": "non-formal", "cable_losses": {}},
                    result_type="cable_loss",
                    filename="cable_loss_results.json",
                    run_id="non-formal",
                    run_directory=run_directory,
                )
            self.assertEqual(list(run_directory.iterdir()), [])

    def test_snapshot_rejects_mismatched_run_id_before_creating_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("infrastructure.persistence.json_result_repository.TEST_RESULTS_DIR", Path(directory)):
                with self.assertRaisesRegex(ValueError, "run_id 不一致"):
                    write_run_snapshot("run-expected", {"run_id": "run-other"}, {})
                self.assertFalse((Path(directory) / "run-expected").exists())

    def test_save_measurement_result_rejects_implicit_directory_collision(self):
        """未显式传入运行目录时，重复 run_id 不得覆盖已有归档。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("infrastructure.persistence.json_result_repository.TEST_RESULTS_DIR", root / "test_results"):
                document = _cable_loss_document("reuse-run")

                archive1 = save_measurement_result(
                    document,
                    result_type="cable_loss",
                    filename="cable_loss_results.json",
                    run_id="reuse-run",
                )

                with self.assertRaises(FileExistsError):
                    save_measurement_result(
                        document,
                        result_type="cable_loss",
                        filename="cable_loss_results.json",
                        run_id="reuse-run",
                    )
                self.assertTrue(archive1.exists())

    def test_save_measurement_result_with_explicit_directory(self):
        """显式提供 run_directory 时应使用提供的目录。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_directory = root / "custom_run_dir"
            run_directory.mkdir()

            archive_path = save_measurement_result(
                _cable_loss_document("explicit-dir-run"),
                result_type="cable_loss",
                filename="cable_loss_results.json",
                run_id="explicit-dir-run",
                run_directory=run_directory,
            )

            self.assertEqual(archive_path.parent, run_directory)

    def test_save_measurement_result_rejects_unsafe_filenames(self):
        """结果文件名必须是运行目录内的纯 JSON 文件名，且校验先于目录创建。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_directory = root / "run"
            for filename in (
                "",
                "   ",
                " nested.json",
                "nested.json ",
                "nested/result.json",
                "nested\\result.json",
                "C:result.json",
                ".",
                "..",
                "result.txt",
                "result",
                "result\x00.json",
            ):
                with self.subTest(filename=filename):
                    with self.assertRaises(ValueError):
                        save_measurement_result(
                            _cable_loss_document("unsafe-file"),
                            result_type="cable_loss",
                            filename=filename,
                            run_id="unsafe-file",
                            run_directory=run_directory,
                        )
                    self.assertFalse(run_directory.exists())

    def test_save_measurement_result_rejects_unknown_result_type(self):
        with tempfile.TemporaryDirectory() as directory:
            run_directory = Path(directory) / "run"
            run_directory.mkdir()
            with self.assertRaises(ValueError):
                save_measurement_result(
                    _cable_loss_document("unknown-type"),
                    result_type="test_result",
                    filename="result.json",
                    run_id="unknown-type",
                    run_directory=run_directory,
                )
            self.assertEqual(list(run_directory.iterdir()), [])

    def test_validate_result_filename_accepts_a_plain_json_name(self):
        self.assertEqual(
            validate_result_filename("measurement_result.json"),
            "measurement_result.json",
        )


class FormalResultReadingTests(unittest.TestCase):
    """正式结果文档读取与显式行视图测试。"""

    def test_normalize_frequency_key(self):
        self.assertEqual(normalize_frequency_key(1.0), "1.0")
        self.assertEqual(normalize_frequency_key("1.0"), "1.0")
        self.assertEqual(normalize_frequency_key(1), "1.0")
        self.assertEqual(normalize_frequency_key("1"), "1.0")
        self.assertIsNone(normalize_frequency_key(None))
        self.assertIsNone(normalize_frequency_key("invalid"))
        self.assertIsNone(normalize_frequency_key(True))

    def test_amplifier_document_exposes_sweep_rows(self):
        model = measurement_result_from_document(_amplifier_document())

        sweep = model.sweep_rows()["1.0"]
        self.assertEqual(len(sweep), 2)
        self.assertEqual(sweep[1]["gain"], 10.0)
        self.assertEqual(model.saturation_points()[0]["output_power"], 5.0)
        self.assertEqual(model.point_count, 2)
        self.assertEqual(model.measurement_time, "2026-08-21 10:00:00")

    def test_cable_loss_document_exposes_report_rows(self):
        model = measurement_result_from_document(_cable_loss_document())

        self.assertEqual(model.report_rows()[0]["cable1"], 1.0)
        self.assertEqual(model.sweep_rows(), {})

    def test_formal_result_json_is_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "amplifier_measurement_results.json"
            path.write_text(
                json.dumps(_amplifier_document("file-run")), encoding="utf-8"
            )

            model = measurement_result_from_document(load_json_result(path))

        self.assertEqual(model.run_id, "file-run")
        self.assertEqual(model.sweep_rows()["1.0"][0]["input_power_dut"], -10.0)

    def test_canonical_model_artifact_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = measurement_result_from_document(_amplifier_document("artifact-run"))
            path = save_measurement_model(model, run_directory=root)

            restored = measurement_result_from_document(load_json_result(path))

        self.assertEqual(restored.to_dict(), model.to_dict())


if __name__ == "__main__":
    unittest.main()
