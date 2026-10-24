"""步骤 D1：运行目录历史列表模型与只读端口契约。

本文件只校验端口层的纯数据模型和结果类型推断，不依赖基础设施实现：
基础设施的读取与边界拒绝由 ``tests/test_run_history_repository.py`` 覆盖。
"""

import unittest
from datetime import datetime, timezone

from application.ports.run_history import (
    RESULT_TYPE_AMPLIFIER,
    RESULT_TYPE_CABLE_LOSS,
    RESULT_TYPE_DRIVER_MAPPING,
    RESULT_TYPE_UNKNOWN,
    RunHistoryEntry,
    RunHistoryRepository,
    RunHistoryState,
    RunStageResultEntry,
    result_type_for_result,
)
from domain.run.models import (
    AMPLIFIER_RESULT_PATH,
    CABLE_LOSS_INPUT_PATH,
    DRIVER_MAPPING_INPUT_PATH,
    STAGE_AMPLIFIER,
    STAGE_CABLE_LOSS,
    STAGE_DRIVER_MAPPING,
)


CREATED_AT = datetime(2026, 10, 24, 8, 0, tzinfo=timezone.utc)


class RunHistoryResultTypeTests(unittest.TestCase):
    def test_stage_name_takes_precedence_for_split_amplifier_files(self):
        self.assertEqual(
            result_type_for_result(STAGE_AMPLIFIER, "results/amplifier_measurement_2.json"),
            RESULT_TYPE_AMPLIFIER,
        )

    def test_known_result_paths_map_to_domain_types(self):
        self.assertEqual(
            result_type_for_result(None, CABLE_LOSS_INPUT_PATH), RESULT_TYPE_CABLE_LOSS
        )
        self.assertEqual(
            result_type_for_result(None, DRIVER_MAPPING_INPUT_PATH),
            RESULT_TYPE_DRIVER_MAPPING,
        )
        self.assertEqual(
            result_type_for_result(None, AMPLIFIER_RESULT_PATH), RESULT_TYPE_AMPLIFIER
        )

    def test_standard_stages_map_even_with_unusual_paths(self):
        self.assertEqual(
            result_type_for_result(STAGE_CABLE_LOSS, "inputs/anything.json"),
            RESULT_TYPE_CABLE_LOSS,
        )
        self.assertEqual(
            result_type_for_result(STAGE_DRIVER_MAPPING, "inputs/anything.json"),
            RESULT_TYPE_DRIVER_MAPPING,
        )

    def test_unknown_files_are_reported_as_unknown_instead_of_guessed(self):
        self.assertEqual(
            result_type_for_result(None, "results/mystery.json"), RESULT_TYPE_UNKNOWN
        )
        self.assertEqual(result_type_for_result(None, "notes.txt"), RESULT_TYPE_UNKNOWN)


class RunHistoryEntryTests(unittest.TestCase):
    def test_usable_entry_reports_partial_progress_without_a_problem(self):
        entry = RunHistoryEntry(
            run_id="20261024T080000Z_a1b2c3d4",
            created_at=CREATED_AT,
            status="running",
            state=RunHistoryState.USABLE,
            total_stages=3,
            finished_stages=1,
            result_count=1,
        )
        self.assertTrue(entry.browsable)
        self.assertLess(entry.finished_stages, entry.total_stages)
        self.assertIsNone(entry.problem)

    def test_incomplete_entry_is_still_browsable_but_records_the_problem(self):
        entry = RunHistoryEntry(
            run_id="20261024T080000Z_a1b2c3d4",
            created_at=CREATED_AT,
            status="completed",
            state=RunHistoryState.INCOMPLETE,
            problem="运行目录结构不完整",
        )
        self.assertTrue(entry.browsable)
        self.assertEqual(entry.problem, "运行目录结构不完整")

    def test_invalid_entry_is_not_browsable(self):
        entry = RunHistoryEntry(
            run_id="20261024T080000Z_a1b2c3d4",
            created_at=CREATED_AT,
            status="invalid",
            state=RunHistoryState.INVALID,
            problem="manifest 无法解析",
        )
        self.assertFalse(entry.browsable)

    def test_entries_are_frozen_value_objects(self):
        entry = RunHistoryEntry(
            run_id="20261024T080000Z_a1b2c3d4",
            created_at=CREATED_AT,
            status="created",
        )
        with self.assertRaises(Exception):
            entry.status = "completed"  # type: ignore[misc]


class RunStageResultEntryTests(unittest.TestCase):
    def test_entry_is_frozen_and_defaults_state_to_usable(self):
        entry = RunStageResultEntry(
            run_id="20261024T080000Z_a1b2c3d4",
            result_id=CABLE_LOSS_INPUT_PATH,
            stage=STAGE_CABLE_LOSS,
            result_type=RESULT_TYPE_CABLE_LOSS,
            modified_at=CREATED_AT,
        )
        self.assertIsNone(entry.size_bytes)
        with self.assertRaises(Exception):
            entry.result_id = "other.json"  # type: ignore[misc]


class RunHistoryPortContractTests(unittest.TestCase):
    def test_port_exposes_only_read_only_operations(self):
        for forbidden in (
            "save",
            "save_compatibility_copy",
            "write",
            "delete",
            "latest_path",
            "legacy_path",
        ):
            self.assertFalse(
                hasattr(RunHistoryRepository, forbidden),
                f"运行历史端口不得暴露写入/兼容能力: {forbidden}",
            )

    def test_port_is_a_runtime_checkable_protocol(self):
        class FakeRepository:
            def list_runs(self):
                return ()

            def list_results(self, run_id):
                return ()

            def load(self, run_id, result_id):
                return {}

            def read_bytes(self, run_id, result_id):
                return b""

        self.assertIsInstance(FakeRepository(), RunHistoryRepository)
        self.assertNotIsInstance(object(), RunHistoryRepository)


if __name__ == "__main__":
    unittest.main()
