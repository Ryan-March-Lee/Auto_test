"""运行快照、统一保存和结果读取的测试。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from infrastructure.persistence.json_result_repository import (
    load_json_result,
    new_run_id,
    save_measurement_result,
    validate_result_filename,
    write_run_snapshot,
)
from domain.result_reading import (
    get_config_snapshot,
    get_result_metadata,
    get_saturation_points,
    get_sweep_dataframe_data,
    get_sweep_results,
    normalize_frequency_key,
    parse_result_model,
)
from infrastructure.persistence.json_result_repository import load_json_result


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
        """统一保存只写入运行目录内的权威结果，不再写全局兼容副本。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("infrastructure.persistence.json_result_repository.TEST_RESULTS_DIR", root / "test_results"):
                result = {"measurement_time": "2026-08-21T10:00:00", "data": [1, 2, 3]}
                run_id = "test-save-001"

                archive_path = save_measurement_result(
                    result,
                    result_type="test_result",
                    filename="measurement_result.json",
                    run_id=run_id,
                )

                self.assertTrue(archive_path.exists())
                self.assertEqual(archive_path, root / "test_results" / run_id / "measurement_result.json")

                archive_data = load_json_result(archive_path)
                self.assertEqual(archive_data["data"], [1, 2, 3])
                self.assertEqual(archive_data["run_id"], run_id)
                self.assertFalse((root / "measurement_result.json").exists())

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
                result = {"data": "test"}
                run_id = "reuse-run"

                # 第一次保存
                archive1 = save_measurement_result(
                    result,
                    result_type="test_result",
                    filename="measurement_result.json",
                    run_id=run_id,
                )

                with self.assertRaises(FileExistsError):
                    save_measurement_result(
                        result,
                        result_type="test_result",
                        filename="measurement_result.json",
                        run_id=run_id,
                    )
                self.assertTrue(archive1.exists())

    def test_save_measurement_result_with_explicit_directory(self):
        """显式提供 run_directory 时应使用提供的目录。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_directory = root / "custom_run_dir"
            run_directory.mkdir()

            result = {"data": "test"}
            run_id = "explicit-dir-run"

            archive_path = save_measurement_result(
                result,
                result_type="test_result",
                filename="measurement_result.json",
                run_id=run_id,
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
                            {"data": 1},
                            result_type="test_result",
                            filename=filename,
                            run_id="unsafe-file",
                            run_directory=run_directory,
                        )
                    self.assertFalse(run_directory.exists())

    def test_validate_result_filename_accepts_a_plain_json_name(self):
        self.assertEqual(
            validate_result_filename("measurement_result.json"),
            "measurement_result.json",
        )


class ResultReadingTests(unittest.TestCase):
    """结果读取层测试。"""

    def test_normalize_frequency_key(self):
        """频率键规范化应处理各种输入格式。"""
        self.assertEqual(normalize_frequency_key(1.0), "1.0")
        self.assertEqual(normalize_frequency_key("1.0"), "1.0")
        self.assertEqual(normalize_frequency_key(1), "1.0")
        self.assertEqual(normalize_frequency_key("1"), "1.0")
        self.assertIsNone(normalize_frequency_key(None))
        self.assertIsNone(normalize_frequency_key("invalid"))

    def test_get_sweep_results_extracts_and_normalizes(self):
        """get_sweep_results 应提取扫描数据并规范化频率键。"""
        data = {
            "results": {
                "1.0": {"sweep_data": [1, 2, 3]},
                2.0: {"sweep_data": [4, 5, 6]},
                "invalid": {"sweep_data": [7, 8, 9]},
            }
        }

        sweep_results = get_sweep_results(data)

        self.assertIn("1.0", sweep_results)
        self.assertIn("2.0", sweep_results)
        self.assertNotIn("invalid", sweep_results)
        self.assertEqual(sweep_results["1.0"]["sweep_data"], [1, 2, 3])

    def test_get_sweep_results_handles_missing_data(self):
        """get_sweep_results 应处理缺失或空数据。"""
        self.assertEqual(get_sweep_results({}), {})
        self.assertEqual(get_sweep_results({"results": None}), {})
        self.assertEqual(get_sweep_results({"results": "not a dict"}), {})

    def test_get_saturation_points_extracts_compression_data(self):
        """get_saturation_points 应提取压缩点数据。"""
        data = {
            "results": {
                "1.0": {
                    "compression_point": {
                        "output_power": 30.0,
                        "gain": 10.0,
                        "efficiency": 50.0,
                    }
                },
                "2.0": {
                    "compression_point": {
                        "output_power": 32.0,
                        "gain": 12.0,
                        "efficiency": 55.0,
                    }
                },
            }
        }

        sat_points = get_saturation_points(data)

        self.assertEqual(len(sat_points), 2)
        self.assertEqual(sat_points[0]["frequency"], "1.0")
        self.assertEqual(sat_points[0]["output_power"], 30.0)
        self.assertEqual(sat_points[1]["frequency"], "2.0")
        self.assertEqual(sat_points[1]["output_power"], 32.0)

    def test_get_saturation_points_handles_missing_compression(self):
        """get_saturation_points 应处理缺少压缩点的情况。"""
        data = {
            "results": {
                "1.0": {"sweep_data": [1, 2, 3]},  # 没有 compression_point
                "2.0": {"compression_point": {}},  # 空的 compression_point
            }
        }

        sat_points = get_saturation_points(data)
        self.assertEqual(sat_points, [])

    def test_get_sweep_dataframe_data_extracts_and_flattens(self):
        """get_sweep_dataframe_data 应提取并展平列式扫描数据。"""
        data = {
            "results": {
                "1.0": {
                    "sweep_data": {
                        "input_power_dut": [0.0],
                        "output_power_dut": [10.0],
                        "gain": [10.0],
                        "efficiency": [20.0],
                        "voltages": [{"CH1": 5.0, "CH2": 10.0}],
                        "currents": [{"CH1": 1.0, "CH2": 2.0}],
                    }
                }
            }
        }

        df_data = get_sweep_dataframe_data(data)

        self.assertIn("1.0", df_data)
        self.assertEqual(len(df_data["1.0"]), 1)

        point = df_data["1.0"][0]
        self.assertEqual(point["input_power_dut"], 0.0)
        self.assertEqual(point["output_power_dut"], 10.0)
        self.assertEqual(point["gain"], 10.0)
        self.assertEqual(point["efficiency"], 20.0)
        self.assertEqual(point["V_CH1"], 5.0)
        self.assertEqual(point["V_CH2"], 10.0)
        self.assertEqual(point["I_CH1"], 1.0)
        self.assertEqual(point["I_CH2"], 2.0)

    def test_get_result_metadata_extracts_fields(self):
        """get_result_metadata 应提取元数据字段。"""
        data = {
            "schema_version": "1.0",
            "result_type": "amplifier_measurement",
            "saved_at": "2026-08-21T10:00:00",
            "measurement_time": "2026-08-21T09:55:00",
            "original_filename": "test.json",
        }

        metadata = get_result_metadata(data)

        self.assertEqual(metadata["schema_version"], "1.0")
        self.assertEqual(metadata["result_type"], "amplifier_measurement")
        self.assertEqual(metadata["saved_at"], "2026-08-21T10:00:00")
        self.assertEqual(metadata["measurement_time"], "2026-08-21T09:55:00")
        self.assertEqual(metadata["original_filename"], "test.json")

    def test_get_config_snapshot_extracts_config(self):
        """get_config_snapshot 只读取正式 configuration_snapshot。"""
        data = {
            "configuration_snapshot": {
                "test_frequencies": [1.0, 2.0],
                "signal_source": {"start_power": 0, "stop_power": 10},
            }
        }

        config = get_config_snapshot(data)

        self.assertEqual(config["test_frequencies"], [1.0, 2.0])
        self.assertEqual(config["signal_source"]["start_power"], 0)

    def test_get_config_snapshot_handles_missing_config(self):
        """get_config_snapshot 应处理缺失配置的情况。"""
        self.assertEqual(get_config_snapshot({}), {})
        self.assertEqual(get_config_snapshot({"configuration_snapshot": None}), {})
        self.assertEqual(get_config_snapshot({"configuration_snapshot": "not a dict"}), {})
        # 旧字段别名 config 不再被读取。
        self.assertEqual(get_config_snapshot({"config": {"frequency": 1.0}}), {})

    def test_amplifier_result_document_adapts_to_versioned_model(self):
        document = {
            "run_id": "amp-run",
            "measurement_time": "2026-08-21 10:00:00",
            "results": {
                "1.0": {
                    "sweep_data": {
                        "input_power_dut": [1.0, 2.0],
                        "output_power_dut": [10.0, 19.0],
                        "gain": [9.0, 17.0],
                        "efficiency": [30.0, 40.0],
                    },
                    "compression_point": {"output_power": 19.0, "gain": 17.0},
                }
            },
        }
        model = parse_result_model(document)
        self.assertEqual(model.run_id, "amp-run")
        self.assertEqual(model.schema_version, "1.0")
        self.assertEqual(len(model.points), 2)
        self.assertEqual(model.points[1].output_power_dbm, 19.0)
        self.assertEqual(get_sweep_dataframe_data(model)["1.0"][0]["gain"], 9.0)
        self.assertEqual(get_saturation_points(model)[0]["gain"], 17.0)

    def test_model_analysis_does_not_require_raw_amplifier_results(self):
        from domain.models import AmplifierMeasurementResult, CompressionPoint

        model = AmplifierMeasurementResult(
            run_id="run-1",
            points=(),
            raw_readings={},
            compression_points={"1.0": CompressionPoint(output_power_dbm=25.0, gain_db=12.0)},
        )
        points = get_saturation_points(model)
        self.assertEqual(points[0]["output_power"], 25.0)
        self.assertEqual(points[0]["gain"], 12.0)

    def test_cable_and_driver_results_use_specific_models(self):
        from domain.models import CableLossResult, DriverPowerMappingResult

        cable = parse_result_model({"run_id": "cable-run", "cable_losses": {"1.0": {
            "cable1": 1.2, "total_path1": 30.0, "total_path2": 31.0,
        }}})
        driver = parse_result_model({"run_id": "driver-run", "power_mapping": {"1.0": {
            "0.0": 11.0, "1.0": 12.0,
        }}})
        self.assertIsInstance(cable, CableLossResult)
        self.assertEqual(cable.points[0].cable_losses_db["cable1"], 1.2)
        self.assertIsInstance(driver, DriverPowerMappingResult)
        self.assertEqual(len(driver.points), 2)
        self.assertEqual(driver.points[0].compensated_output_power_dbm, 11.0)
        self.assertEqual(cable.points[0].path1_output_power_dbm, None)
        self.assertEqual(cable.points[0].path1_loss_db, 30.0)

    def test_driver_legacy_object_mapping_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_result_model({"run_id": "driver-run", "power_mapping": {"1.0": {
                "input_power": 0.0, "output_power": 10.0, "actual_output_power": 11.0,
            }}})

    def test_parse_result_model_reads_formal_result_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            path.write_text(json.dumps({
                "run_id": "driver-run",
                "power_mapping": {"2.0": {"1.0": 5.0}},
            }), encoding="utf-8")
            model = parse_result_model(load_json_result(path))
        self.assertEqual(model.points[0].frequency_hz, 2.0)


if __name__ == "__main__":
    unittest.main()
