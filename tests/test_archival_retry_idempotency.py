"""步骤 9：自动归档的失败重试与幂等测试。

覆盖重构计划「步骤 9：补齐失败重试和幂等测试」的三条边界：

1. **归档服务**（``ResultArchivalService``）在真实运行目录上：
   正式结果已存在时重复归档不产生第二份正式结果、不重写已登记文件；CSV 失败与报告
   失败只影响该产物，显式 ``retry_failed=True`` 只重生成失败项；重复重试不累积产物
   记录、不丢失败原因；端口不支持的产物类型不会被“重试”退化成 ``pending``；取消的
   运行重复归档不写 manifest、不生成产物。
2. **应用层端口返回值**：空路径、非 ``GeneratedArtifact`` 的错误类型、不受控/越界路径
   一律登记为该产物 ``failed`` 且不写入路径；端口实现缺陷仍然上抛，产物停在等待复查的
   状态。
3. **归档边界与正式结果的关系**：任何产物失败、重试或取消都不改写正式结果 JSON、
   manifest 输入/输出登记与运行/阶段状态。
4. **应用日志路由**：产物生成失败（``WARNING``）与重试跳过（``INFO``）必须挂在
   ``pa_auto_test`` logger 树下，才能真正写进 ``setup_logging()`` 的日志文件（裸
   ``logging.getLogger(__name__)`` 只会落到 stderr）。

历史页面的无效运行、部分运行与产物缺失展示由 ``tests/test_run_history_page.py`` 覆盖；
产物生成适配器自身的输入/输出边界由 ``tests/test_csv_artifact_generator.py`` 与
``tests/test_report_artifact_generator.py`` 覆盖，本文件不重复实现层细节。
"""

import hashlib
import logging
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from application.archival_service import ArchivalRequestError, ResultArchivalService
from application.dto.archival import (
    ArchivalRequest,
    ArchivalStatus,
    ArchivalTestStatus,
)
from application.ports.result_artifacts import (
    ArtifactGenerationError,
    ArtifactGenerationRequest,
    GeneratedArtifact,
)
from application.run_stages import RunStageRecorder
from domain.run.models import (
    AMPLIFIER_RESULT_PATH,
    STAGE_AMPLIFIER,
    STAGE_CABLE_LOSS,
    STAGE_DRIVER_MAPPING,
    ArtifactKind,
    ArtifactStatus,
    RunStatus,
    StageStatus,
    default_artifact_path,
)
from infrastructure.logging.app_logging import reset_logging, setup_logging
from infrastructure.persistence import artifact_generator as artifact_generator_module
from infrastructure.persistence.run_repository import RunRepository


RUN_ID = "20261028T010203Z_abcdef12"
CREATED_AT = datetime(2026, 10, 28, 1, 2, 3, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 28, 2, 3, 4, tzinfo=timezone.utc)
CSV_PATH = "artifacts/amplifier.csv"
REPORT_PATH = "artifacts/amplifier_report.html"
# 应用日志树：``setup_logging()`` 只给这棵树装配文件处理器。
APPLICATION_LOGGER_ROOT = "pa_auto_test"
ARCHIVAL_LOGGER = "pa_auto_test.application.archival_service"


class RecordingGenerator:
    """把产物真正写进运行目录的生成端口替身，可按产物类型脚本化失败。

    ``failures`` 是 ``{产物类型: [异常或 None, ...]}``：每次生成取队首一项，``None``
    表示这一次故意成功。队列耗尽后该产物一律成功，便于表达“失败一次后恢复”。
    """

    supported_kinds = frozenset({ArtifactKind.CSV, ArtifactKind.REPORT})

    def __init__(self, layout, *, failures=None, kinds=None) -> None:
        self.layout = layout
        self.calls = []
        self.failures = {
            ArtifactKind(kind): list(errors)
            for kind, errors in (failures or {}).items()
        }
        if kinds is not None:
            self.supported_kinds = frozenset(kinds)

    def generate(self, request):
        self.calls.append(request)
        queue = self.failures.get(request.kind)
        if queue:
            error = queue.pop(0)
            if error is not None:
                raise error
        target = self.layout.run_directory / request.target_path
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = f"{request.kind.value}:{request.source_result}".encode("utf-8")
        target.write_bytes(payload)
        return GeneratedArtifact(
            path=request.target_path,
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
        )


class RunDirectoryFixture(unittest.TestCase):
    """创建一次完整成功的主功放运行，并提供归档与快照辅助方法。"""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.base = Path(self._temporary.name)
        self.runs = RunRepository(self.base / "runs")
        self.layout, _manifest = self.runs.create_run(
            {"run_id": RUN_ID}, run_id=RUN_ID, now=CREATED_AT
        )
        self.recorder = RunStageRecorder(self.runs, RUN_ID)

    # -- 夹具 ---------------------------------------------------------------

    def _complete_amplifier_run(self) -> None:
        """把一次运行推进到“所有阶段完成 + 运行完成”，但不触发归档。"""
        self.recorder.skip(STAGE_CABLE_LOSS, now=CREATED_AT)
        self.recorder.skip(STAGE_DRIVER_MAPPING, now=CREATED_AT)
        self.recorder.start(STAGE_AMPLIFIER, now=CREATED_AT)
        self.recorder.complete(
            STAGE_AMPLIFIER,
            {"run_id": RUN_ID, "results": {}},
            result_path=AMPLIFIER_RESULT_PATH,
            now=CREATED_AT,
        )
        self.recorder.complete_run(now=CREATED_AT)

    def _archive(self, generator=None, *, retry_failed=False):
        return ResultArchivalService(runs=self.runs, generator=generator).archive(
            ArchivalRequest(RUN_ID, STAGE_AMPLIFIER),
            retry_failed=retry_failed,
            now=NOW,
        )

    def _manifest(self):
        return self.runs.load_manifest(RUN_ID)

    def _artifacts(self) -> dict[ArtifactKind, object]:
        return {record.kind: record for record in self._manifest().artifacts}

    def _result_file(self) -> Path:
        return self.layout.run_directory / AMPLIFIER_RESULT_PATH

    def _artifact_file(self, kind: ArtifactKind) -> Path:
        return self.layout.run_directory / default_artifact_path(STAGE_AMPLIFIER, kind)

    def _formal_snapshot(self) -> dict:
        """正式结果与运行状态的快照：任何归档动作都不得改变它。

        产物记录（``artifacts``）本来就会随归档推进，因此不纳入这份快照；这里只固定
        “正式结果文件 + 输入/输出登记 + 阶段/运行状态”，它们在任何失败、取消或重试
        路径上都必须保持原样。
        """
        manifest = self._manifest()
        return {
            "result_bytes": self._result_file().read_bytes(),
            "inputs": tuple(
                (item.path, item.sha256, item.size_bytes) for item in manifest.inputs
            ),
            "outputs": tuple(
                (item.path, item.sha256, item.size_bytes) for item in manifest.outputs
            ),
            "stages": tuple(
                (name, record.status, record.result)
                for name, record in manifest.stages.items()
            ),
            "status": manifest.status,
        }


# -- 正式结果已存在：重复归档与重复完成 -----------------------------------


class ExistingFormalResultTests(RunDirectoryFixture):
    def test_repeated_archive_keeps_one_formal_result_and_one_record_per_kind(self):
        self._complete_amplifier_run()
        generator = RecordingGenerator(self.layout)
        before = self._formal_snapshot()

        first = self._archive(generator)
        second = self._archive(generator)

        self.assertIs(first.archive_status, ArchivalStatus.COMPLETED)
        self.assertIs(second.archive_status, ArchivalStatus.COMPLETED)
        # 正式结果、输入/输出登记与阶段/运行状态都没有被归档改写。
        self.assertEqual(self._formal_snapshot(), before)
        # 结果目录里只有一份正式结果，不产生“第二份”结果文件。
        results = sorted(
            path.name
            for path in (self.layout.run_directory / "results").iterdir()
            if path.is_file()
        )
        self.assertEqual(results, [Path(AMPLIFIER_RESULT_PATH).name])
        self.assertEqual(len(self._manifest().outputs), 1)
        self.assertEqual(
            {(record.kind, record.source_result) for record in self._manifest().artifacts},
            {
                (ArtifactKind.CSV, AMPLIFIER_RESULT_PATH),
                (ArtifactKind.REPORT, AMPLIFIER_RESULT_PATH),
            },
        )
        # 每个产物只生成一次：第二次归档复用已完成产物。
        self.assertEqual(
            [call.kind for call in generator.calls],
            [ArtifactKind.CSV, ArtifactKind.REPORT],
        )

    def test_completed_stage_cannot_rewrite_the_existing_formal_result(self):
        self._complete_amplifier_run()
        before = self._formal_snapshot()

        with self.assertRaisesRegex(ValueError, "不允许重新写入结果"):
            self.recorder.complete(
                STAGE_AMPLIFIER,
                {"run_id": RUN_ID, "results": {"9.9": {}}},
                result_path=AMPLIFIER_RESULT_PATH,
                now=CREATED_AT,
            )

        self.assertEqual(self._formal_snapshot(), before)
        manifest = self._manifest()
        self.assertIs(manifest.stage(STAGE_AMPLIFIER).status, StageStatus.COMPLETED)
        self.assertIs(manifest.status, RunStatus.COMPLETED)
        self.assertEqual(manifest.artifacts, ())

    def test_unregistered_result_file_is_not_adopted_as_a_formal_result(self):
        """文件存在但 manifest 没有登记时，归档不能把它当成正式结果。"""
        manifest = self._manifest()
        manifest = manifest.transition_stage(
            STAGE_AMPLIFIER, StageStatus.RUNNING, now=CREATED_AT
        )
        manifest = manifest.transition_stage(
            STAGE_AMPLIFIER,
            StageStatus.COMPLETED,
            result=AMPLIFIER_RESULT_PATH,
            now=CREATED_AT,
        )
        manifest = manifest.with_status(RunStatus.RUNNING, now=CREATED_AT)
        self.runs.update_manifest(RUN_ID, manifest, now=CREATED_AT)
        self.runs.write_run_json(RUN_ID, AMPLIFIER_RESULT_PATH, {"run_id": RUN_ID})
        before = self._result_file().read_bytes()

        with self.assertRaisesRegex(ArchivalRequestError, "未登记"):
            self._archive(RecordingGenerator(self.layout))

        self.assertEqual(self._result_file().read_bytes(), before)
        self.assertEqual(self._manifest().artifacts, ())


# -- 产物失败与显式重试 ---------------------------------------------------


class ArtifactRetryTests(RunDirectoryFixture):
    def test_csv_failure_keeps_the_result_and_the_explicit_retry_completes_it(self):
        self._complete_amplifier_run()
        before = self._formal_snapshot()
        generator = RecordingGenerator(
            self.layout,
            failures={ArtifactKind.CSV: [ArtifactGenerationError("磁盘已满")]},
        )

        first = self._archive(generator)

        self.assertIs(first.csv.status, ArtifactStatus.FAILED)
        self.assertEqual(first.csv.reason, "磁盘已满")
        self.assertIs(first.report.status, ArtifactStatus.COMPLETED)
        self.assertIs(first.archive_status, ArchivalStatus.PARTIAL)
        self.assertEqual(self._formal_snapshot(), before)
        records = self._artifacts()
        self.assertIs(records[ArtifactKind.CSV].status, ArtifactStatus.FAILED)
        self.assertIsNone(records[ArtifactKind.CSV].path)

        calls_before = len(generator.calls)
        second = self._archive(generator, retry_failed=True)

        self.assertIs(second.csv.status, ArtifactStatus.COMPLETED)
        self.assertEqual(second.csv.path, CSV_PATH)
        self.assertIs(second.archive_status, ArchivalStatus.COMPLETED)
        # 只重生成失败项，已完成的报告不被重写。
        self.assertEqual(
            [call.kind for call in generator.calls[calls_before:]],
            [ArtifactKind.CSV],
        )
        self.assertEqual(self._formal_snapshot(), before)
        self.assertEqual(len(self._manifest().artifacts), 2)
        self.assertTrue(self._artifact_file(ArtifactKind.CSV).is_file())

    def test_report_failure_keeps_the_result_and_the_explicit_retry_completes_it(self):
        self._complete_amplifier_run()
        before = self._formal_snapshot()
        generator = RecordingGenerator(
            self.layout,
            failures={ArtifactKind.REPORT: [ArtifactGenerationError("模板缺失")]},
        )

        first = self._archive(generator)

        self.assertIs(first.csv.status, ArtifactStatus.COMPLETED)
        self.assertIs(first.report.status, ArtifactStatus.FAILED)
        self.assertEqual(first.report.reason, "模板缺失")
        self.assertIs(first.archive_status, ArchivalStatus.PARTIAL)
        self.assertEqual(self._formal_snapshot(), before)

        calls_before = len(generator.calls)
        second = self._archive(generator, retry_failed=True)

        self.assertIs(second.report.status, ArtifactStatus.COMPLETED)
        self.assertEqual(second.report.path, REPORT_PATH)
        self.assertEqual(
            [call.kind for call in generator.calls[calls_before:]],
            [ArtifactKind.REPORT],
        )
        self.assertEqual(self._formal_snapshot(), before)
        self.assertTrue(self._artifact_file(ArtifactKind.REPORT).is_file())

    def test_repeated_failed_retries_keep_one_record_and_the_latest_reason(self):
        self._complete_amplifier_run()
        before = self._formal_snapshot()
        generator = RecordingGenerator(
            self.layout,
            failures={
                ArtifactKind.REPORT: [
                    ArtifactGenerationError("第一次渲染失败"),
                    ArtifactGenerationError("第二次渲染失败"),
                    ArtifactGenerationError("第三次渲染失败"),
                ]
            },
        )
        self._archive(generator)

        outcome = None
        for _ in range(2):
            outcome = self._archive(generator, retry_failed=True)

        self.assertIs(outcome.report.status, ArtifactStatus.FAILED)
        self.assertEqual(outcome.report.reason, "第三次渲染失败")
        self.assertIs(outcome.archive_status, ArchivalStatus.PARTIAL)
        # 重复失败只更新同一条记录，不累积、不产生第二份正式结果。
        self.assertEqual(len(self._manifest().artifacts), 2)
        records = self._artifacts()
        self.assertIs(records[ArtifactKind.REPORT].status, ArtifactStatus.FAILED)
        self.assertEqual(records[ArtifactKind.REPORT].error, "第三次渲染失败")
        self.assertIs(records[ArtifactKind.CSV].status, ArtifactStatus.COMPLETED)
        self.assertEqual(self._formal_snapshot(), before)

    def test_retry_never_regenerates_completed_artifacts(self):
        self._complete_amplifier_run()
        generator = RecordingGenerator(self.layout)
        self._archive(generator)
        calls_before = len(generator.calls)
        csv_bytes = self._artifact_file(ArtifactKind.CSV).read_bytes()
        records_before = self._manifest().artifacts

        outcome = self._archive(generator, retry_failed=True)

        self.assertEqual(len(generator.calls), calls_before)
        self.assertEqual(
            [item.status for item in outcome.artifacts],
            [ArtifactStatus.COMPLETED, ArtifactStatus.COMPLETED],
        )
        self.assertEqual(self._artifact_file(ArtifactKind.CSV).read_bytes(), csv_bytes)
        self.assertEqual(self._manifest().artifacts, records_before)

    def test_retry_keeps_a_failure_the_port_cannot_generate(self):
        """端口没有声明某类产物时，重试不能把它退化成“待生成”。"""
        self._complete_amplifier_run()
        full = RecordingGenerator(
            self.layout,
            failures={
                ArtifactKind.CSV: [ArtifactGenerationError("磁盘已满")],
                ArtifactKind.REPORT: [ArtifactGenerationError("模板缺失")],
            },
        )
        self._archive(full)
        before = self._formal_snapshot()
        csv_only = RecordingGenerator(self.layout, kinds={ArtifactKind.CSV})

        outcome = self._archive(csv_only, retry_failed=True)

        self.assertIs(outcome.csv.status, ArtifactStatus.COMPLETED)
        self.assertIs(outcome.report.status, ArtifactStatus.FAILED)
        self.assertEqual(outcome.report.reason, "模板缺失")
        self.assertIs(outcome.archive_status, ArchivalStatus.PARTIAL)
        records = self._artifacts()
        self.assertIs(records[ArtifactKind.REPORT].status, ArtifactStatus.FAILED)
        self.assertEqual(records[ArtifactKind.REPORT].error, "模板缺失")
        self.assertEqual(self._formal_snapshot(), before)

    def test_retry_without_a_generator_keeps_the_failure(self):
        self._complete_amplifier_run()
        full = RecordingGenerator(
            self.layout,
            failures={ArtifactKind.REPORT: [ArtifactGenerationError("模板缺失")]},
        )
        self._archive(full)
        before = self._formal_snapshot()

        outcome = self._archive(None, retry_failed=True)

        self.assertIs(outcome.report.status, ArtifactStatus.FAILED)
        self.assertEqual(outcome.report.reason, "模板缺失")
        records = self._artifacts()
        self.assertIs(records[ArtifactKind.REPORT].status, ArtifactStatus.FAILED)
        self.assertIs(records[ArtifactKind.CSV].status, ArtifactStatus.COMPLETED)
        self.assertEqual(self._formal_snapshot(), before)


# -- 运行取消 -------------------------------------------------------------


class CancelledRunTests(RunDirectoryFixture):
    def test_cancelled_run_archive_is_idempotent_and_registers_no_artifacts(self):
        self.recorder.skip(STAGE_CABLE_LOSS, now=CREATED_AT)
        self.recorder.skip(STAGE_DRIVER_MAPPING, now=CREATED_AT)
        self.recorder.start(STAGE_AMPLIFIER, now=CREATED_AT)
        self.recorder.cancel(STAGE_AMPLIFIER, "现场急停", now=CREATED_AT)
        manifest_json = self.layout.manifest_path.read_bytes()
        generator = RecordingGenerator(self.layout)

        first = self._archive(generator)
        second = self._archive(generator)

        for outcome in (first, second):
            self.assertIs(outcome.test_status, ArchivalTestStatus.CANCELLED)
            self.assertIsNone(outcome.formal_result_path)
            self.assertIs(outcome.csv.status, ArtifactStatus.NOT_APPLICABLE)
            self.assertIs(outcome.report.status, ArtifactStatus.NOT_APPLICABLE)
            self.assertIs(outcome.archive_status, ArchivalStatus.NOT_APPLICABLE)
        # 取消路径不写 manifest、不生成产物、不留下正式结果。
        self.assertEqual(self.layout.manifest_path.read_bytes(), manifest_json)
        self.assertEqual(self._manifest().artifacts, ())
        self.assertEqual(generator.calls, [])
        self.assertFalse(self._result_file().exists())
        self.assertFalse(self._artifact_file(ArtifactKind.CSV).exists())
        self.assertFalse(self._artifact_file(ArtifactKind.REPORT).exists())

    def test_cancelled_run_never_gets_a_report_from_a_retry(self):
        self.recorder.skip(STAGE_CABLE_LOSS, now=CREATED_AT)
        self.recorder.skip(STAGE_DRIVER_MAPPING, now=CREATED_AT)
        self.recorder.start(STAGE_AMPLIFIER, now=CREATED_AT)
        self.recorder.cancel(STAGE_AMPLIFIER, "操作员停止", now=CREATED_AT)
        generator = RecordingGenerator(self.layout)

        outcome = ResultArchivalService(
            runs=self.runs, generator=generator
        ).archive(
            ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), retry_failed=True, now=NOW
        )

        self.assertIs(outcome.test_status, ArchivalTestStatus.CANCELLED)
        self.assertEqual(generator.calls, [])
        self.assertEqual(self._manifest().artifacts, ())


# -- 应用层端口返回值边界 -------------------------------------------------


class PortReturnBoundaryTests(RunDirectoryFixture):
    def test_port_returning_an_empty_path_is_a_failed_artifact(self):
        self._complete_amplifier_run()
        before = self._formal_snapshot()

        class EmptyPathGenerator:
            supported_kinds = frozenset({ArtifactKind.CSV, ArtifactKind.REPORT})

            def generate(self, request):
                if request.kind is ArtifactKind.CSV:
                    return GeneratedArtifact("", "b" * 64, 0)
                raise ArtifactGenerationError("报告未生成")

        outcome = self._archive(EmptyPathGenerator())

        self.assertIs(outcome.csv.status, ArtifactStatus.FAILED)
        self.assertIn("不能为空", outcome.csv.reason)
        self.assertIs(outcome.report.status, ArtifactStatus.FAILED)
        for record in self._manifest().artifacts:
            self.assertIsNone(record.path)
        self.assertEqual(self._formal_snapshot(), before)

    def test_port_returning_an_uncontrolled_path_is_a_failed_artifact(self):
        self._complete_amplifier_run()
        before = self._formal_snapshot()

        class EscapingGenerator:
            supported_kinds = frozenset({ArtifactKind.CSV, ArtifactKind.REPORT})

            def generate(self, request):
                if request.kind is ArtifactKind.CSV:
                    # 合法备份文件名，但不是请求的受控目标。
                    return GeneratedArtifact(CSV_PATH + ".bak", "b" * 64, 1)
                # 运行目录之外的绝对路径在 DTO 构造阶段就被拒绝。
                return GeneratedArtifact("C:/outside/amplifier_report.html", "b" * 64, 1)

        outcome = self._archive(EscapingGenerator())

        self.assertIs(outcome.csv.status, ArtifactStatus.FAILED)
        self.assertIn(CSV_PATH, outcome.csv.reason)
        self.assertIn(CSV_PATH + ".bak", outcome.csv.reason)
        self.assertIs(outcome.report.status, ArtifactStatus.FAILED)
        self.assertIn("盘符", outcome.report.reason)
        for record in self._manifest().artifacts:
            self.assertIsNone(record.path)
        self.assertFalse((self.layout.run_directory / (CSV_PATH + ".bak")).exists())
        self.assertEqual(self._formal_snapshot(), before)

    def test_port_returning_a_non_dto_object_is_a_failed_artifact(self):
        self._complete_amplifier_run()

        class DuckArtifact:
            path = CSV_PATH
            sha256 = "b" * 64
            size_bytes = 1

        class WrongTypeGenerator:
            supported_kinds = frozenset({ArtifactKind.CSV})

            def generate(self, request):
                return DuckArtifact()

        outcome = self._archive(WrongTypeGenerator())

        self.assertIs(outcome.csv.status, ArtifactStatus.FAILED)
        self.assertIn("GeneratedArtifact", outcome.csv.reason)
        self.assertIsNone(self._artifacts()[ArtifactKind.CSV].path)

    def test_port_defect_propagates_and_leaves_the_artifact_pending(self):
        self._complete_amplifier_run()
        before = self._formal_snapshot()

        class DefectiveGenerator:
            supported_kinds = frozenset({ArtifactKind.CSV, ArtifactKind.REPORT})

            def generate(self, request):
                raise RuntimeError("端口实现缺陷")

        with self.assertRaisesRegex(RuntimeError, "端口实现缺陷"):
            self._archive(DefectiveGenerator())

        for record in self._manifest().artifacts:
            self.assertIs(record.status, ArtifactStatus.PENDING)
            self.assertIsNone(record.path)
        self.assertEqual(self._formal_snapshot(), before)

    def test_generation_request_rejects_targets_outside_the_artifacts_directory(self):
        for target in (
            "results/amplifier.csv",
            "artifacts/../results/amplifier.csv",
            "C:/outside/amplifier.csv",
            "/artifacts/amplifier.csv",
        ):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    ArtifactGenerationRequest(
                        run_id=RUN_ID,
                        stage=STAGE_AMPLIFIER,
                        kind=ArtifactKind.CSV,
                        source_result=AMPLIFIER_RESULT_PATH,
                        target_path=target,
                    )


# -- 应用日志路由 ---------------------------------------------------------


class RecordingLogHandler(logging.Handler):
    """抓取 ``pa_auto_test`` 树上的记录，但不替换已装配的文件处理器。

    ``assertLogs()`` 会临时接管目标 logger 的处理器，块内记录因此写不进日志文件；这里
    自己挂一个 handler，才能在同一个断言里同时验证“记录来自哪棵树”和“文件里查得到”。
    """

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def names_for(self, marker: str) -> list[str]:
        return [
            record.name for record in self.records if marker in record.getMessage()
        ]


class ApplicationLogRoutingTests(RunDirectoryFixture):
    """自动生成的错误必须进应用日志文件，而不只是 stderr。

    ``setup_logging()`` 只给 ``pa_auto_test`` logger 树装配文件处理器，并让 root 保持
    原样；因此归档服务与产物生成适配器必须挂在该树下。这里同时断言“日志树里的记录”
    和“真正写进日志文件的文本”，避免只满足前者却在文件里查不到。
    """

    def setUp(self):
        super().setUp()
        self.log_path = setup_logging(self.base / "logs")
        # 清理顺序为 LIFO：先摘掉记录器，再关闭文件处理器，最后删临时目录；否则
        # Windows 上打开着的日志文件删不掉。
        self.addCleanup(reset_logging)
        self.captured = RecordingLogHandler()
        logging.getLogger(APPLICATION_LOGGER_ROOT).addHandler(self.captured)
        self.addCleanup(self._detach_capture)

    def _detach_capture(self) -> None:
        logging.getLogger(APPLICATION_LOGGER_ROOT).removeHandler(self.captured)
        self.captured.close()

    def _log_text(self) -> str:
        return Path(self.log_path).read_text(encoding="utf-8")

    def test_generation_failure_is_written_to_the_application_log_file(self):
        self._complete_amplifier_run()
        generator = RecordingGenerator(
            self.layout,
            failures={ArtifactKind.CSV: [ArtifactGenerationError("磁盘已满")]},
        )

        outcome = self._archive(generator)

        self.assertIs(outcome.csv.status, ArtifactStatus.FAILED)
        self.assertEqual(
            self.captured.names_for("自动归档产物生成失败"), [ARCHIVAL_LOGGER]
        )
        content = self._log_text()
        self.assertIn("自动归档产物生成失败", content)
        self.assertIn("kind=csv", content)
        self.assertIn("磁盘已满", content)

    def test_retry_skip_is_written_to_the_application_log_file(self):
        self._complete_amplifier_run()
        # 先让 CSV 失败，再用一个不声明 CSV 的端口重试：跳过记录必须留下。
        self._archive(
            RecordingGenerator(
                self.layout,
                failures={ArtifactKind.CSV: [ArtifactGenerationError("磁盘已满")]},
            )
        )
        narrow = RecordingGenerator(self.layout, kinds=(ArtifactKind.REPORT,))

        outcome = self._archive(narrow, retry_failed=True)

        self.assertIs(outcome.csv.status, ArtifactStatus.FAILED)
        self.assertEqual(self.captured.names_for("跳过产物重试"), [ARCHIVAL_LOGGER])
        self.assertIn("跳过产物重试", self._log_text())

    def test_generation_adapter_logger_is_on_the_application_log_tree(self):
        # 适配器的告警（例如报告省略 CSV 链接）同样要进日志文件。
        self.assertTrue(
            artifact_generator_module.logger.name.startswith(
                APPLICATION_LOGGER_ROOT + "."
            ),
            "产物生成适配器的 logger 必须挂在 pa_auto_test 树下",
        )


if __name__ == "__main__":
    unittest.main()
