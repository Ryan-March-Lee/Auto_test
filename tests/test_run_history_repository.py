"""步骤 D2：只读运行历史仓储的列表、读取与边界拒绝测试。"""

import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from application.ports.run_history import (
    RESULT_TYPE_AMPLIFIER,
    RESULT_TYPE_CABLE_LOSS,
    RunHistoryRepository,
    RunHistoryState,
)
from application.run_stages import RunStageRecorder
from domain.run.models import (
    AMPLIFIER_RESULT_PATH,
    CABLE_LOSS_INPUT_PATH,
    DRIVER_MAPPING_INPUT_PATH,
    HARDWARE_CONFIG_INPUT_PATH,
    STAGE_AMPLIFIER,
    STAGE_CABLE_LOSS,
    STAGE_DRIVER_MAPPING,
    default_manifest,
    utc_now_iso,
)
from infrastructure.persistence.run_directory import save_run_manifest
from infrastructure.persistence.run_history_repository import FileRunHistoryRepository
from infrastructure.persistence.run_repository import RunRepository


RUN_ID = "20261024T080000Z_a1b2c3d4"
LATER_ID = "20261024T090000Z_deadbeef"
CREATED_AT = datetime(2026, 10, 24, 8, 0, tzinfo=timezone.utc)
LATER_AT = datetime(2026, 10, 24, 9, 0, tzinfo=timezone.utc)

CONFIG = {"run_id": RUN_ID, "test_plan": {"name": "demo"}}


class RunHistoryRepositoryTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.base = Path(self._temporary.name)
        self.runs = RunRepository(self.base / "runs")
        self.history = FileRunHistoryRepository(runs=self.runs)

    # -- 辅助 ---------------------------------------------------------------

    def _create_run(self, run_id=RUN_ID, *, now=CREATED_AT):
        layout, _manifest = self.runs.create_run(CONFIG, run_id=run_id, now=now)
        return layout, RunStageRecorder(self.runs, run_id)

    def _complete_cable_loss(self, run_id=RUN_ID, *, now=CREATED_AT):
        layout, recorder = self._create_run(run_id, now=now)
        recorder.complete(
            STAGE_CABLE_LOSS,
            {"run_id": run_id, "cable_losses": {"1.0": {"cable2": 1.5}}},
            now=now,
        )
        return layout, recorder

    def _write_hardware_config(self, run_id=RUN_ID, *, now=CREATED_AT):
        relative = self.runs.write_run_json(
            run_id, HARDWARE_CONFIG_INPUT_PATH, {"instruments": []}
        )
        self.runs.register_file(run_id, relative, description="硬件组装配置快照", now=now)

    # -- 列表 ---------------------------------------------------------------

    def test_list_runs_reports_useful_metadata_sorted_newest_first(self):
        self._complete_cable_loss(RUN_ID, now=CREATED_AT)
        self._complete_cable_loss(LATER_ID, now=LATER_AT)

        entries = self.history.list_runs()

        self.assertEqual([entry.run_id for entry in entries], [LATER_ID, RUN_ID])
        entry = entries[0]
        self.assertEqual(entry.state, RunHistoryState.USABLE)
        self.assertTrue(entry.browsable)
        self.assertEqual(entry.total_stages, 3)
        self.assertEqual(entry.finished_stages, 1)
        self.assertEqual(entry.result_count, 1)
        self.assertEqual(entry.created_at, LATER_AT)
        self.assertIsNone(entry.problem)

    def test_list_runs_does_not_scan_the_global_results_directory(self):
        self._complete_cable_loss()
        results_dir = self.base / "test_results"
        results_dir.mkdir(parents=True, exist_ok=True)
        (results_dir / "cable_loss_results.json").write_text(
            json.dumps({"cable_losses": {"9.9": 42}}), encoding="utf-8"
        )

        entries = self.history.list_runs()

        self.assertEqual([entry.run_id for entry in entries], [RUN_ID])
        results = self.history.list_results(RUN_ID)
        self.assertEqual([item.result_id for item in results], [CABLE_LOSS_INPUT_PATH])

    def test_incomplete_run_is_listed_but_marked(self):
        layout, _recorder = self._create_run()
        (layout.run_directory / "config.json").unlink()

        entry = self.history.list_runs()[0]

        self.assertEqual(entry.state, RunHistoryState.INCOMPLETE)
        self.assertTrue(entry.browsable)
        self.assertIn("结构不完整", entry.problem or "")

    def test_corrupt_manifest_is_reported_as_invalid_not_hidden(self):
        broken = self.runs.root / RUN_ID
        broken.mkdir(parents=True)
        (broken / "manifest.json").write_text("{not json", encoding="utf-8")

        entry = self.history.list_runs()[0]

        self.assertEqual(entry.state, RunHistoryState.INVALID)
        self.assertFalse(entry.browsable)
        self.assertIn("manifest", entry.problem or "")

    def test_manifest_run_id_mismatch_is_invalid(self):
        mismatch = self.runs.root / RUN_ID
        mismatch.mkdir(parents=True)
        (mismatch / "inputs").mkdir()
        (mismatch / "results").mkdir()
        (mismatch / "config.json").write_text("{}", encoding="utf-8")
        save_run_manifest(
            mismatch / "manifest.json",
            default_manifest("different-run-id", created_at=utc_now_iso(CREATED_AT)),
        )

        entry = self.history.list_runs()[0]

        self.assertEqual(entry.state, RunHistoryState.INVALID)
        self.assertIn("不一致", entry.problem or "")

    # -- 结果列表 -----------------------------------------------------------

    def test_list_results_returns_stage_results_and_excludes_hardware_config(self):
        layout, recorder = self._create_run()
        self._write_hardware_config()
        recorder.complete(
            STAGE_CABLE_LOSS,
            {"run_id": RUN_ID, "cable_losses": {"1.0": {"cable2": 1.5}}},
            now=CREATED_AT,
        )

        results = self.history.list_results(RUN_ID)

        by_id = {item.result_id: item for item in results}
        self.assertIn(CABLE_LOSS_INPUT_PATH, by_id)
        self.assertNotIn(HARDWARE_CONFIG_INPUT_PATH, by_id)
        self.assertEqual(by_id[CABLE_LOSS_INPUT_PATH].stage, STAGE_CABLE_LOSS)
        self.assertEqual(by_id[CABLE_LOSS_INPUT_PATH].result_type, RESULT_TYPE_CABLE_LOSS)
        self.assertGreater(by_id[CABLE_LOSS_INPUT_PATH].size_bytes or 0, 0)

    def test_list_results_includes_amplifier_main_and_split_outputs(self):
        _layout, recorder = self._create_run()
        recorder.complete(
            STAGE_AMPLIFIER,
            {"run_id": RUN_ID, "results": {"4.8": {}}},
            result_path=AMPLIFIER_RESULT_PATH,
            extra_files={"results/amplifier_measurement_2.json": {"run_id": RUN_ID}},
            now=CREATED_AT,
        )

        results = self.history.list_results(RUN_ID)

        ids = {item.result_id for item in results}
        self.assertIn(AMPLIFIER_RESULT_PATH, ids)
        self.assertIn("results/amplifier_measurement_2.json", ids)
        for item in results:
            self.assertEqual(item.result_type, RESULT_TYPE_AMPLIFIER)

    # -- 读取与边界 ---------------------------------------------------------

    def test_load_and_read_bytes_return_verified_results(self):
        layout, recorder = self._complete_cable_loss()
        source = layout.run_directory / CABLE_LOSS_INPUT_PATH

        loaded = self.history.load(RUN_ID, CABLE_LOSS_INPUT_PATH)
        raw = self.history.read_bytes(RUN_ID, CABLE_LOSS_INPUT_PATH)

        self.assertEqual(loaded["cable_losses"], {"1.0": {"cable2": 1.5}})
        self.assertEqual(raw, source.read_bytes())

    def test_unregistered_result_is_rejected(self):
        _layout, _recorder = self._create_run()
        self.runs.write_run_json(RUN_ID, CABLE_LOSS_INPUT_PATH, {"x": 1})
        # 写入但**未登记**到 manifest 的文件不得被历史浏览读取。
        with self.assertRaises(ValueError):
            self.history.load(RUN_ID, CABLE_LOSS_INPUT_PATH)

    def test_tampered_registered_file_is_rejected(self):
        layout, _recorder = self._complete_cable_loss()
        source = layout.run_directory / CABLE_LOSS_INPUT_PATH
        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["cable_losses"] = {"9.9": {"cable2": 999.0}}
        source.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaises(ValueError):
            self.history.load(RUN_ID, CABLE_LOSS_INPUT_PATH)
        with self.assertRaises(ValueError):
            self.history.read_bytes(RUN_ID, CABLE_LOSS_INPUT_PATH)

    def test_cross_run_and_path_traversal_are_rejected(self):
        self._complete_cable_loss(RUN_ID, now=CREATED_AT)
        self._complete_cable_loss(LATER_ID, now=LATER_AT)

        for result_id in (
            "../config.json",
            "../../runs/" + LATER_ID + "/" + CABLE_LOSS_INPUT_PATH,
            "runs/" + LATER_ID + "/" + CABLE_LOSS_INPUT_PATH,
            str(self.runs.layout(RUN_ID).run_directory / CABLE_LOSS_INPUT_PATH),
            "",
        ):
            with self.subTest(result_id=result_id):
                with self.assertRaises(ValueError):
                    self.history.load(RUN_ID, result_id)

    def test_other_runs_result_identifier_is_not_readable_here(self):
        self._complete_cable_loss(RUN_ID, now=CREATED_AT)
        self._complete_cable_loss(LATER_ID, now=LATER_AT)

        # 结果标识本身合法，但未登记到 ``LATER_ID`` 的 manifest。
        self.assertEqual(self.history.load(LATER_ID, CABLE_LOSS_INPUT_PATH)["run_id"], LATER_ID)
        with self.assertRaises(ValueError):
            self.history.load(RUN_ID, DRIVER_MAPPING_INPUT_PATH)

    # -- 结果白名单：非阶段结果输入不可读 -----------------------------------

    def test_hardware_config_input_is_not_a_history_result(self):
        _layout, recorder = self._create_run()
        self._write_hardware_config()
        recorder.complete(
            STAGE_CABLE_LOSS,
            {"run_id": RUN_ID, "cable_losses": {"1.0": {"cable2": 1.5}}},
            now=CREATED_AT,
        )

        results = self.history.list_results(RUN_ID)

        self.assertNotIn(HARDWARE_CONFIG_INPUT_PATH, {item.result_id for item in results})
        with self.assertRaises(ValueError):
            self.history.load(RUN_ID, HARDWARE_CONFIG_INPUT_PATH)
        with self.assertRaises(ValueError):
            self.history.read_bytes(RUN_ID, HARDWARE_CONFIG_INPUT_PATH)

    def test_registered_non_stage_input_is_not_readable(self):
        _layout, _recorder = self._create_run()
        relative = self.runs.write_run_json(RUN_ID, "inputs/notes.json", {"note": 1})
        self.runs.register_file(RUN_ID, relative, description="辅助输入")

        with self.assertRaises(ValueError):
            self.history.load(RUN_ID, "inputs/notes.json")

    # -- 运行目录符号链接边界 -----------------------------------------------

    def _make_directory_link(self, link: Path, target: Path) -> None:
        """创建一个目录重解析点：优先符号链接，Windows 回退目录联接。"""
        try:
            link.symlink_to(target, target_is_directory=True)
            return
        except (OSError, NotImplementedError):
            pass
        if os.name == "nt":
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True,
            )
            if result.returncode == 0:
                return
        self.skipTest("当前平台不支持创建目录符号链接或目录联接")

    def _remove_directory_link(self, link: Path) -> None:
        if os.name == "nt":
            subprocess.run(
                ["cmd", "/c", "rmdir", str(link)], capture_output=True
            )
        else:
            try:
                link.unlink()
            except OSError:
                pass

    def test_symlinked_run_directory_is_rejected(self):
        outside = self.base / "outside_run"
        (outside / "inputs").mkdir(parents=True)
        (outside / "results").mkdir()
        (outside / "config.json").write_text("{}", encoding="utf-8")
        save_run_manifest(
            outside / "manifest.json",
            default_manifest(RUN_ID, created_at=utc_now_iso(CREATED_AT)),
        )
        self.runs.root.mkdir(parents=True, exist_ok=True)
        link = self.runs.root / RUN_ID
        self._make_directory_link(link, outside)
        self.addCleanup(self._remove_directory_link, link)

        entry = next(item for item in self.history.list_runs() if item.run_id == RUN_ID)
        self.assertEqual(entry.state, RunHistoryState.INVALID)
        self.assertIn("拒绝作为历史结果", entry.problem or "")
        with self.assertRaises(ValueError):
            self.history.list_results(RUN_ID)
        with self.assertRaises(ValueError):
            self.history.load(RUN_ID, CABLE_LOSS_INPUT_PATH)

    # -- 只读契约 -----------------------------------------------------------

    def test_repository_exposes_read_only_operations_only(self):
        for forbidden in (
            "save",
            "save_compatibility_copy",
            "write_run_json",
            "register_file",
            "discard_run",
            "latest_path",
        ):
            self.assertFalse(
                hasattr(FileRunHistoryRepository, forbidden),
                f"运行历史仓储不得暴露写入能力: {forbidden}",
            )

    def test_repository_satisfies_the_port_contract(self):
        self.assertIsInstance(self.history, RunHistoryRepository)


if __name__ == "__main__":
    unittest.main()
