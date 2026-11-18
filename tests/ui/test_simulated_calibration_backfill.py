"""阶段五步骤 23：仿真运行阶段结果的自动回填规则。

本模块只验证“阶段完成后是否把本次结果交给下游页面”的判定与副作用，不跑测量：
真实窗口与三段链路由 ``tests/integration/test_simulated_run_lifecycle.py`` 覆盖。
页面、结果查询和协调器都是记录型替身，断言集中在三件事：

1. 只有仿真运行内才回填，正式运行不会悄悄替换用户的校准输入；
2. 当前选择为空或正好指向本次结果时才写入，其它手动选择必须保留；
3. 回填失败只记录日志，不抛出、不改写页面现有选择。
"""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from application.dto.measurement_requests import StageResultDigest
from presentation.qt.measurement_state import (
    MeasurementKind,
    MeasurementResultReference,
    StageResultSelection,
)
from presentation.qt.stage_result_selection import backfill_simulated_calibration

ARCHIVE_PATH = str(
    Path("temp") / "simulation_runs" / "test_results" / "cable_loss"
    / "20261118T090000Z_a1b2c3d4.json"
)


def _selection(path: str, filename: str = "manual.json") -> StageResultSelection:
    return StageResultSelection(
        path=path,
        filename=filename,
        created_at="2026-11-18T08:00:00+00:00",
        digest=StageResultDigest(sha256="a" * 64, size_bytes=10),
    )


class _Page:
    """页面替身：记录回填写入的事实，并像真实页面一样更新选择。"""

    def __init__(self) -> None:
        self.cable_loss_selection: StageResultSelection | None = None
        self.driver_mapping_selection: StageResultSelection | None = None
        self.cable_loss_writes: list[dict] = []
        self.driver_mapping_writes: list[dict] = []

    def show_loaded_cable_loss(self, **facts) -> None:
        self.cable_loss_writes.append(facts)
        self.cable_loss_selection = StageResultSelection(
            path=facts["path"],
            filename=facts["filename"],
            created_at=facts["created_at"],
            digest=StageResultDigest(
                sha256=facts["sha256"], size_bytes=facts["size_bytes"]
            ),
        )

    def show_loaded_driver_mapping(self, **facts) -> None:
        self.driver_mapping_writes.append(facts)
        self.driver_mapping_selection = StageResultSelection(
            path=facts["path"],
            filename=facts["filename"],
            created_at=facts["created_at"],
            digest=StageResultDigest(
                sha256=facts["sha256"], size_bytes=facts["size_bytes"]
            ),
        )


class _Query:
    """仿真作用域结果查询替身；默认按路径返回可复核的文件事实。"""

    def __init__(
        self,
        *,
        error: Exception | None = None,
        sha256: str | None = "b" * 64,
        size_bytes: int | None = 5,
    ) -> None:
        self.error = error
        self.sha256 = sha256
        self.size_bytes = size_bytes
        self.calls: list[tuple[object, str]] = []

    def load_stage_result(self, stage, path):
        self.calls.append((stage, path))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            filename=Path(path).name,
            sha256=self.sha256,
            size_bytes=self.size_bytes,
            result=SimpleNamespace(created_at="2026-11-18T09:00:00+00:00"),
        )


def _window(*, simulated: bool = True, query: _Query | None = None):
    logs: list[str] = []
    window = SimpleNamespace(
        coordinator=SimpleNamespace(
            is_simulated_run_active=lambda: simulated,
            current_result_query=lambda: query,
        ),
        driver_mapping_page=_Page(),
        amplifier_page=_Page(),
    )
    window.add_log_message = logs.append
    return window, logs


def _reference(
    kind: MeasurementKind,
    *,
    archive_path: str | None = ARCHIVE_PATH,
    result_reference=None,
):
    if result_reference is None:
        result_reference = (
            {"archive_path": archive_path} if archive_path is not None else {}
        )
    value = SimpleNamespace(result_reference=result_reference)
    return MeasurementResultReference(result_id="result-1", kind=kind, value=value)


class SimulationBackfillScopeTests(unittest.TestCase):
    def test_a_formal_run_never_replaces_the_users_calibration_input(self):
        query = _Query()
        window, logs = _window(simulated=False, query=query)

        backfill_simulated_calibration(
            window, _reference(MeasurementKind.CABLE_LOSS)
        )

        self.assertEqual(query.calls, [])
        self.assertEqual(window.driver_mapping_page.cable_loss_writes, [])
        self.assertEqual(window.amplifier_page.cable_loss_writes, [])
        self.assertEqual(logs, [])

    def test_a_result_without_an_archive_path_is_reported_and_ignored(self):
        query = _Query()
        window, logs = _window(query=query)

        backfill_simulated_calibration(
            window,
            _reference(MeasurementKind.CABLE_LOSS, archive_path="  "),
        )

        self.assertEqual(query.calls, [])
        self.assertEqual(window.driver_mapping_page.cable_loss_writes, [])
        self.assertEqual(len(logs), 1)
        self.assertIn("缺少阶段结果路径", logs[0])

    def test_a_result_without_a_reference_mapping_is_reported_and_ignored(self):
        query = _Query()
        window, logs = _window(query=query)
        value = SimpleNamespace()

        backfill_simulated_calibration(
            window, MeasurementResultReference(
                result_id="result-1",
                kind=MeasurementKind.CABLE_LOSS,
                value=value,
            )
        )

        self.assertEqual(query.calls, [])
        self.assertEqual(len(logs), 1)
        self.assertIn("缺少结果引用", logs[0])


class SimulationBackfillWriteTests(unittest.TestCase):
    def test_an_empty_selection_is_backfilled_for_both_downstream_pages(self):
        query = _Query()
        window, logs = _window(query=query)

        backfill_simulated_calibration(
            window, _reference(MeasurementKind.CABLE_LOSS)
        )

        driver = window.driver_mapping_page
        amplifier = window.amplifier_page
        self.assertEqual(
            [call[1] for call in query.calls], [ARCHIVE_PATH, ARCHIVE_PATH]
        )
        self.assertEqual(
            driver.cable_loss_writes,
            [
                {
                    "path": ARCHIVE_PATH,
                    "filename": "20261118T090000Z_a1b2c3d4.json",
                    "created_at": "2026-11-18T09:00:00+00:00",
                    "sha256": "b" * 64,
                    "size_bytes": 5,
                }
            ],
        )
        self.assertEqual(driver.cable_loss_selection.path, ARCHIVE_PATH)
        self.assertEqual(amplifier.cable_loss_writes, driver.cable_loss_writes)
        self.assertEqual(
            len([item for item in logs if "已按本次仿真结果自动回填" in item]), 2
        )

    def test_a_driver_mapping_result_only_backfills_the_amplifier(self):
        query = _Query()
        window, logs = _window(query=query)

        backfill_simulated_calibration(
            window, _reference(MeasurementKind.DRIVER_MAPPING)
        )

        self.assertEqual(window.driver_mapping_page.driver_mapping_writes, [])
        self.assertEqual(window.driver_mapping_page.cable_loss_writes, [])
        self.assertEqual(len(window.amplifier_page.driver_mapping_writes), 1)
        self.assertEqual(
            window.amplifier_page.driver_mapping_selection.path, ARCHIVE_PATH
        )
        self.assertEqual(len(logs), 1)

    def test_an_amplifier_result_has_no_downstream_target(self):
        query = _Query()
        window, logs = _window(query=query)

        backfill_simulated_calibration(
            window, _reference(MeasurementKind.AMPLIFIER)
        )

        self.assertEqual(query.calls, [])
        self.assertEqual(window.amplifier_page.cable_loss_writes, [])
        self.assertEqual(window.amplifier_page.driver_mapping_writes, [])
        self.assertEqual(logs, [])

    def test_a_selection_pointing_at_the_same_path_is_refreshed(self):
        query = _Query()
        window, logs = _window(query=query)
        window.driver_mapping_page.cable_loss_selection = _selection(ARCHIVE_PATH)

        backfill_simulated_calibration(
            window, _reference(MeasurementKind.CABLE_LOSS)
        )

        self.assertEqual(len(window.driver_mapping_page.cable_loss_writes), 1)
        self.assertEqual(len(window.amplifier_page.cable_loss_writes), 1)
        self.assertEqual(len([item for item in logs if "自动回填" in item]), 2)


class SimulationBackfillProtectionTests(unittest.TestCase):
    def test_a_manual_selection_of_another_file_is_preserved(self):
        query = _Query()
        window, logs = _window(query=query)
        manual = _selection(str(Path("test_results") / "cable_loss" / "manual.json"))
        window.driver_mapping_page.cable_loss_selection = manual
        window.amplifier_page.cable_loss_selection = manual

        backfill_simulated_calibration(
            window, _reference(MeasurementKind.CABLE_LOSS)
        )

        self.assertEqual(query.calls, [])
        self.assertEqual(window.driver_mapping_page.cable_loss_writes, [])
        self.assertEqual(window.amplifier_page.cable_loss_writes, [])
        self.assertIs(window.driver_mapping_page.cable_loss_selection, manual)
        self.assertIs(window.amplifier_page.cable_loss_selection, manual)
        self.assertEqual(len(logs), 2)
        self.assertTrue(all("保留当前手动选择" in item for item in logs))

    def test_a_manual_selection_is_kept_when_the_other_page_is_backfilled(self):
        query = _Query()
        window, logs = _window(query=query)
        manual = _selection(str(Path("test_results") / "cable_loss" / "manual.json"))
        window.driver_mapping_page.cable_loss_selection = manual

        backfill_simulated_calibration(
            window, _reference(MeasurementKind.CABLE_LOSS)
        )

        self.assertEqual(window.driver_mapping_page.cable_loss_writes, [])
        self.assertIs(window.driver_mapping_page.cable_loss_selection, manual)
        self.assertEqual(len(window.amplifier_page.cable_loss_writes), 1)
        self.assertTrue(any("保留当前手动选择" in item for item in logs))
        self.assertTrue(any("自动回填" in item for item in logs))

    def test_a_load_failure_keeps_the_existing_selection(self):
        for error in (
            ValueError("阶段结果摘要与文件不一致"),
            OSError("文件不存在"),
        ):
            with self.subTest(error=type(error).__name__):
                query = _Query(error=error)
                window, logs = _window(query=query)

                backfill_simulated_calibration(
                    window, _reference(MeasurementKind.CABLE_LOSS)
                )

                self.assertEqual(len(query.calls), 2)
                self.assertEqual(window.driver_mapping_page.cable_loss_writes, [])
                self.assertEqual(window.amplifier_page.cable_loss_writes, [])
                self.assertIsNone(
                    window.driver_mapping_page.cable_loss_selection
                )
                self.assertEqual(len(logs), 2)
                self.assertTrue(
                    all("自动回填失败" in item for item in logs)
                )

    def test_a_result_without_a_digest_is_not_accepted_as_a_selection(self):
        for sha256, size_bytes in ((None, 5), ("b" * 64, None)):
            with self.subTest(sha256=sha256, size_bytes=size_bytes):
                query = _Query(sha256=sha256, size_bytes=size_bytes)
                window, logs = _window(query=query)

                backfill_simulated_calibration(
                    window, _reference(MeasurementKind.CABLE_LOSS)
                )

                self.assertEqual(window.driver_mapping_page.cable_loss_writes, [])
                self.assertIsNone(
                    window.driver_mapping_page.cable_loss_selection
                )
                self.assertEqual(len(logs), 2)
                self.assertTrue(
                    all("结果缺少文件摘要" in item for item in logs)
                )


if __name__ == "__main__":
    unittest.main()
