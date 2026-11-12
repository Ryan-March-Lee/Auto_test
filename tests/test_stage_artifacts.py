"""步骤 20：阶段目录交付产物（CSV/报告）的命名、写入与定位。"""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from application.dto.measurement_artifacts import (
    stage_artifact_filename,
    validate_stage_artifact_filename,
)
from application.dto.measurement_results import (
    AmplifierFrequencyResult,
    AmplifierMeasurementResult,
    CableLossMeasurementResult,
    CableLossRow,
    MeasurementStatus,
    generate_measurement_id,
)
from application.ports.result_artifacts import ArtifactGenerationError
from application.ports.stage_artifacts import StageArtifactReference
from application.ports.stage_result_repository import MeasurementStage
from domain.run.models import ArtifactKind
from infrastructure.filesystem.paths import PathService
from infrastructure.gui_data_service import ResultQueryAdapter
from infrastructure.persistence import stage_artifact_repository as stage_artifact_module
from infrastructure.persistence.stage_artifact_repository import (
    FileStageArtifactRepository,
)


MEASUREMENT_ID = "20261112T010203Z_abcdef12"
RUN_ID = "20261112T010203Z_a1b2c3d4"


def _amplifier_result(*, measurement_id=MEASUREMENT_ID, **overrides):
    values = {
        "run_id": RUN_ID,
        "measurement_id": measurement_id,
        "frequencies": (
            AmplifierFrequencyResult(
                frequency_ghz="1.0",
                sweep_data={
                    "sg_power": (-20.0, -15.0),
                    "input_power_dut": (-10.0, -5.0),
                    "output_power_dut": (10.0, 15.0),
                    "gain": (20.0, 20.0),
                },
            ),
        ),
    }
    values.update(overrides)
    return AmplifierMeasurementResult(**values)


def _fake_plots(result, output_dir, measurement_id):
    output_dir = Path(output_dir)
    path = output_dir / f"{measurement_id}_p_in_vs_p_out.png"
    path.write_bytes(b"fake-plot")
    return {"p_in_vs_p_out": path}, ()


class StageArtifactFilenameTests(unittest.TestCase):
    def test_filename_is_derived_from_identity_and_kind(self):
        self.assertEqual(
            stage_artifact_filename(MEASUREMENT_ID, ArtifactKind.CSV),
            f"{MEASUREMENT_ID}.csv",
        )
        self.assertEqual(
            stage_artifact_filename(MEASUREMENT_ID, ArtifactKind.REPORT),
            f"{MEASUREMENT_ID}_report.html",
        )

    def test_filename_rejects_path_fragments_and_wrong_suffix(self):
        for value in ("../evil", "C:/evil", "", "  "):
            with self.assertRaises(ValueError):
                stage_artifact_filename(value, ArtifactKind.CSV)
        with self.assertRaises(ValueError):
            validate_stage_artifact_filename(
                f"{MEASUREMENT_ID}_report.html", kind=ArtifactKind.CSV
            )
        with self.assertRaises(ValueError):
            validate_stage_artifact_filename(
                "other.txt", kind=ArtifactKind.CSV
            )


class FileStageArtifactRepositoryTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.paths = PathService(self.root)
        self.repository = FileStageArtifactRepository(
            paths=self.paths, report_plot_renderer=_fake_plots
        )

    def test_csv_is_written_next_to_the_formal_result(self):
        result = _amplifier_result()

        reference = self.repository.save(
            MeasurementStage.AMPLIFIER, MEASUREMENT_ID, ArtifactKind.CSV, result
        )

        self.assertIsInstance(reference, StageArtifactReference)
        self.assertEqual(reference.path.parent, self.paths.amplifier_results_dir)
        self.assertEqual(reference.path.name, f"{MEASUREMENT_ID}.csv")
        self.assertTrue(reference.path.is_file())
        text = reference.path.read_text(encoding="utf-8-sig")
        self.assertIn(RUN_ID, text)
        self.assertIn("amplifier", text)
        self.assertGreater(reference.size_bytes, 0)
        self.assertEqual(len(reference.sha256), 64)

    def test_report_links_the_same_measurements_csv_and_plots(self):
        result = _amplifier_result()
        self.repository.save(
            MeasurementStage.AMPLIFIER, MEASUREMENT_ID, ArtifactKind.CSV, result
        )

        reference = self.repository.save(
            MeasurementStage.AMPLIFIER, MEASUREMENT_ID, ArtifactKind.REPORT, result
        )

        self.assertEqual(reference.path.name, f"{MEASUREMENT_ID}_report.html")
        report = reference.path.read_text(encoding="utf-8")
        self.assertIn(f"{MEASUREMENT_ID}.csv", report)
        self.assertIn(f"{MEASUREMENT_ID}_p_in_vs_p_out.png", report)
        self.assertTrue(
            (
                self.paths.amplifier_results_dir
                / f"{MEASUREMENT_ID}_p_in_vs_p_out.png"
            ).is_file()
        )

    def test_report_without_csv_does_not_link_a_missing_file(self):
        reference = self.repository.save(
            MeasurementStage.AMPLIFIER,
            MEASUREMENT_ID,
            ArtifactKind.REPORT,
            _amplifier_result(),
        )

        self.assertNotIn(f"{MEASUREMENT_ID}.csv", reference.path.read_text(encoding="utf-8"))

    def test_identity_mismatch_is_rejected_before_writing(self):
        with self.assertRaises(ArtifactGenerationError):
            self.repository.save(
                MeasurementStage.AMPLIFIER,
                MEASUREMENT_ID,
                ArtifactKind.CSV,
                _amplifier_result(measurement_id=generate_measurement_id()),
            )
        self.assertFalse(self.paths.amplifier_results_dir.exists())

    def test_non_amplifier_result_is_rejected(self):
        result = CableLossMeasurementResult(
            run_id=RUN_ID,
            measurement_id=MEASUREMENT_ID,
            points=(
                CableLossRow(frequency_ghz="1.0", cable_losses={"cable1": 1.0}),
            ),
        )

        with self.assertRaises(ArtifactGenerationError):
            self.repository.save(
                MeasurementStage.AMPLIFIER, MEASUREMENT_ID, ArtifactKind.CSV, result
            )

    def test_non_completed_result_is_rejected(self):
        result = _amplifier_result(
            status=MeasurementStatus.FAILED, error="故意失败"
        )

        with self.assertRaises(ArtifactGenerationError):
            self.repository.save(
                MeasurementStage.AMPLIFIER, MEASUREMENT_ID, ArtifactKind.CSV, result
            )

    def test_locate_only_returns_the_matching_stage_file(self):
        self.assertIsNone(
            self.repository.locate(
                MeasurementStage.AMPLIFIER, MEASUREMENT_ID, ArtifactKind.CSV
            )
        )
        reference = self.repository.save(
            MeasurementStage.AMPLIFIER,
            MEASUREMENT_ID,
            ArtifactKind.CSV,
            _amplifier_result(),
        )

        self.assertEqual(
            self.repository.locate(
                MeasurementStage.AMPLIFIER, MEASUREMENT_ID, ArtifactKind.CSV
            ),
            reference.path,
        )
        self.assertIsNone(
            self.repository.locate(
                MeasurementStage.AMPLIFIER,
                generate_measurement_id(),
                ArtifactKind.CSV,
            )
        )

    def test_stage_directory_boundary_rejects_an_outside_target(self):
        """位置与写入共用的目录边界：目标必须直接位于阶段目录内。"""
        directory = self.paths.amplifier_results_dir
        directory.mkdir(parents=True)

        with self.assertRaises(ArtifactGenerationError):
            FileStageArtifactRepository._require_inside_stage_directory(
                directory, self.root / "outside.csv"
            )

    def test_write_failure_leaves_no_partial_or_temporary_file(self):
        target = self.paths.amplifier_results_dir / f"{MEASUREMENT_ID}.csv"
        target.mkdir(parents=True)

        with self.assertRaises(ArtifactGenerationError):
            self.repository.save(
                MeasurementStage.AMPLIFIER,
                MEASUREMENT_ID,
                ArtifactKind.CSV,
                _amplifier_result(),
            )

        self.assertTrue(target.is_dir())
        self.assertEqual(
            [path.name for path in self.paths.amplifier_results_dir.iterdir()],
            [f"{MEASUREMENT_ID}.csv"],
        )


class StageArtifactReferenceContractTests(unittest.TestCase):
    def test_reference_requires_the_identity_derived_filename(self):
        with self.assertRaises(ValueError):
            StageArtifactReference(
                stage=MeasurementStage.AMPLIFIER,
                measurement_id=MEASUREMENT_ID,
                kind=ArtifactKind.CSV,
                path=Path("other.csv"),
                sha256="a" * 64,
                size_bytes=1,
            )


class StageArtifactWriteFailureCleanupTests(unittest.TestCase):
    """渲染器抛出的非预期异常也不能留下临时文件或半份报告。"""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.paths = PathService(self.root)
        self.repository = FileStageArtifactRepository(
            paths=self.paths, report_plot_renderer=_fake_plots
        )

    def test_unexpected_render_failure_leaves_no_temporary_file(self):
        from unittest.mock import patch

        def _broken(result, destination, **kwargs):
            Path(destination).write_text("半份报告", encoding="utf-8")
            raise TypeError("渲染器缺陷")

        with patch.object(
            stage_artifact_module, "render_html_report", _broken
        ):
            with self.assertRaises(TypeError):
                self.repository.save(
                    MeasurementStage.AMPLIFIER,
                    MEASUREMENT_ID,
                    ArtifactKind.REPORT,
                    _amplifier_result(),
                )

        directory = self.paths.amplifier_results_dir
        self.assertFalse(
            (directory / f"{MEASUREMENT_ID}_report.html").exists()
        )
        self.assertEqual(
            [path.name for path in directory.iterdir() if path.name.endswith(".tmp")],
            [],
        )


class ResultQueryStageArtifactTests(unittest.TestCase):
    """查询适配器的产物定位复用阶段产物仓库的边界校验。"""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.paths = PathService(self.root)
        self.artifacts = FileStageArtifactRepository(
            paths=self.paths, report_plot_renderer=_fake_plots
        )
        self.adapter = ResultQueryAdapter(
            self.paths,
            run_history=SimpleNamespace(),
            stage_artifacts=self.artifacts,
        )

    def test_missing_artifact_returns_none(self):
        self.assertIsNone(
            self.adapter.stage_artifact_path(
                MeasurementStage.AMPLIFIER, MEASUREMENT_ID, ArtifactKind.CSV
            )
        )

    def test_invalid_stage_is_reported_as_a_value_error(self):
        with self.assertRaises(ValueError):
            self.adapter.stage_artifact_path(
                "unknown_stage", MEASUREMENT_ID, ArtifactKind.CSV
            )

    def test_existing_artifact_is_found_next_to_the_formal_result(self):
        self.artifacts.save(
            MeasurementStage.AMPLIFIER,
            MEASUREMENT_ID,
            ArtifactKind.CSV,
            _amplifier_result(),
        )

        self.assertEqual(
            self.adapter.stage_artifact_path(
                MeasurementStage.AMPLIFIER, MEASUREMENT_ID, ArtifactKind.CSV
            ),
            str(self.paths.amplifier_results_dir / f"{MEASUREMENT_ID}.csv"),
        )

    def test_artifact_escaping_the_stage_directory_is_rejected(self):
        directory = self.paths.amplifier_results_dir
        directory.mkdir(parents=True)
        outside = self.root / "outside_report.html"
        outside.write_text("不属于该阶段目录", encoding="utf-8")
        link = directory / f"{MEASUREMENT_ID}_report.html"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            self.skipTest("当前环境不允许创建符号链接")

        with self.assertRaises(ValueError):
            self.adapter.stage_artifact_path(
                MeasurementStage.AMPLIFIER,
                MEASUREMENT_ID,
                ArtifactKind.REPORT,
            )


if __name__ == "__main__":
    unittest.main()
