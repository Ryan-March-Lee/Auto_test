import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from infrastructure.persistence.result_repository import FileMeasurementResultRepository
from infrastructure.persistence.json_encoder import NumpyJSONEncoder
from application.ports.result_repository import SavedMeasurementResult
from infrastructure.persistence.json_result_repository import load_json_result


def _cable_document(**overrides):
    document = {
        "measurement_type": "cable_loss",
        "status": "completed",
        "attenuator_value": 20.0,
        "cable_losses": {},
    }
    document.update(overrides)
    return document


class FileMeasurementResultRepositoryTests(unittest.TestCase):
    def test_save_maps_result_type_and_returns_persistence_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            run_directory = Path(directory) / "run-1"
            expected_archive = run_directory / "cable_loss_results.json"
            with patch(
                "infrastructure.persistence.result_repository.json_result_repository.save_measurement_result",
                return_value=expected_archive,
            ) as save:
                saved = FileMeasurementResultRepository(results_directory=directory).save(
                    _cable_document(),
                    result_type="cable_loss",
                    run_id="run-1",
                    run_directory=run_directory,
                )

        self.assertIsInstance(saved, SavedMeasurementResult)
        self.assertEqual(saved.archive_path, expected_archive)
        self.assertEqual(saved.run_directory, run_directory)
        self.assertEqual(saved.run_id, "run-1")
        save.assert_called_once_with(
            _cable_document(),
            result_type="cable_loss",
            filename="cable_loss_results.json",
            run_id="run-1",
            run_directory=run_directory,
            encoder=NumpyJSONEncoder,
        )

    def test_custom_results_directory_controls_archive_location_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            saved = FileMeasurementResultRepository(results_directory=root).save(
                _cable_document(), result_type="cable_loss", run_id="run-1"
            )

            self.assertEqual(saved.run_directory, root / "run-1")
            self.assertEqual(saved.archive_path, root / "run-1" / "cable_loss_results.json")
            self.assertTrue(saved.archive_path.is_file())
            # 正式写入只产生运行目录内的权威结果，不落全局兼容目录。
            self.assertFalse((root / "cable_loss_results.json").exists())
            self.assertEqual(load_json_result(saved.archive_path)["run_id"], "run-1")

    def test_real_write_encodes_numpy_scalars_and_arrays(self):
        with tempfile.TemporaryDirectory() as directory:
            saved = FileMeasurementResultRepository(results_directory=directory).save(
                _cable_document(
                    attenuator_value=np.float64(4.5),
                    configuration_snapshot={
                        "levels": np.array([1, 2]),
                        "integer": np.int64(3),
                    },
                ),
                result_type="cable_loss",
                run_id="numpy-run",
            )

            payload = load_json_result(saved.archive_path)
            self.assertEqual(payload["configuration_snapshot"]["levels"], [1, 2])
            self.assertEqual(payload["configuration_snapshot"]["integer"], 3)
            self.assertEqual(payload["attenuator_value"], 4.5)

    def test_explicit_run_directory_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            explicit = root / "provided-run"
            saved = FileMeasurementResultRepository(results_directory=root / "results").save(
                _cable_document(),
                result_type="cable_loss",
                run_id="explicit-run",
                run_directory=explicit,
            )

            self.assertEqual(saved.run_directory, explicit)
            self.assertEqual(saved.archive_path, explicit / "cable_loss_results.json")
            self.assertTrue(saved.archive_path.is_file())

    def test_real_write_propagates_encoding_failures(self):
        class Unsupported:
            pass

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(TypeError):
                FileMeasurementResultRepository(results_directory=directory).save(
                    _cable_document(configuration_snapshot={"value": Unsupported()}),
                    result_type="cable_loss",
                    run_id="failed-run",
                )

    def test_result_types_use_fixed_run_directory_file_names(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = FileMeasurementResultRepository(results_directory=directory)
            driver = repository.save(
                {
                    "measurement_type": "driver_power_mapping",
                    "status": "completed",
                    "power_mapping": {},
                },
                result_type="driver_power_mapping",
                run_id="driver-run",
            )
            amplifier = repository.save(
                {
                    "measurement_type": "amplifier",
                    "status": "completed",
                    "results": {},
                },
                result_type="amplifier_measurement",
                run_id="amplifier-run",
            )

        self.assertEqual(driver.archive_path.name, "driver_power_mapping_results.json")
        self.assertEqual(amplifier.archive_path.name, "amplifier_measurement_results.json")

    def test_save_passes_through_storage_failures(self):
        error = OSError("写入失败")
        with patch(
            "infrastructure.persistence.result_repository.json_result_repository.save_measurement_result",
            side_effect=error,
        ):
            with self.assertRaises(OSError) as raised:
                FileMeasurementResultRepository().save(
                    _cable_document(), result_type="cable_loss", run_id="run-1"
                )

        self.assertIs(raised.exception, error)

    def test_repository_implements_the_result_repository_port(self):
        from application.ports.result_repository import MeasurementResultRepository as Port

        repository = FileMeasurementResultRepository()
        required = {
            name for name in dir(Port) if not name.startswith("_")
        }
        missing = {name for name in required if not hasattr(repository, name)}
        self.assertEqual(missing, set())

    def test_deprecated_compatibility_interfaces_are_removed(self):
        """步骤 3：全局兼容副本与旧运行快照接口已删除。"""
        from application.ports.result_repository import MeasurementResultRepository

        repository = FileMeasurementResultRepository()
        for name in (
            "legacy_path",
            "save_compatibility_copy",
            "create_legacy_run_snapshot",
        ):
            with self.subTest(interface=name):
                self.assertFalse(hasattr(repository, name))
                self.assertFalse(hasattr(MeasurementResultRepository, name))

    def test_load_delegates_to_json_repository(self):
        with patch(
            "infrastructure.persistence.result_repository.json_result_repository.load_json_result",
            return_value={"value": 1},
        ) as load:
            result = FileMeasurementResultRepository().load("result.json")

        self.assertEqual(result, {"value": 1})
        load.assert_called_once_with("result.json")

    def test_non_formal_result_is_rejected_before_leaving_an_archive(self):
        """步骤 6：写入路径只接受正式结果 schema。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "measurement_type"):
                FileMeasurementResultRepository(results_directory=root).save(
                    {"value": 1}, result_type="cable_loss", run_id="bad-run"
                )

            self.assertFalse((root / "bad-run" / "cable_loss_results.json").exists())

    def test_legacy_alias_result_is_rejected_before_leaving_an_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "旧字段"):
                FileMeasurementResultRepository(results_directory=root).save(
                    _cable_document(legacy_payload={"cable_losses": {}}),
                    result_type="cable_loss",
                    run_id="legacy-run",
                )

            self.assertFalse((root / "legacy-run" / "cable_loss_results.json").exists())

    def test_unknown_result_type_is_rejected_before_writing(self):
        with self.assertRaises(ValueError):
            FileMeasurementResultRepository().save({}, result_type="unknown", run_id="run-1")

    def test_deprecated_latest_lookup_interfaces_are_removed(self):
        """步骤 B3：按时间扫描的 ``latest_path`` 兼容链已删除。"""
        from application.ports.result_repository import MeasurementResultRepository

        repository = FileMeasurementResultRepository()
        self.assertFalse(hasattr(repository, "latest_path"))
        self.assertFalse(hasattr(repository, "latest_compatibility_path"))
        self.assertFalse(hasattr(MeasurementResultRepository, "latest_path"))


if __name__ == "__main__":
    unittest.main()
