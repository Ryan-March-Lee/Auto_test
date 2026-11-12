"""本次运行消费的外部校准输入来源：登记、合并与兼容读取。

步骤 17 之后，线损/驱动映射可以由页面显式选择，因此可以来自其它运行。运行目录
必须能回答“这次测量消费了哪个运行的哪份结果”，但又不能把输入来源与本运行产出的
阶段结果引用混为一谈。本模块覆盖该契约：

- 来源文档只记录阶段、身份、路径、摘要、大小与来源运行，不复制结果正文；
- 同一键（消费方测量类型 + 输入阶段）重复登记相同事实是幂等的；
- 驱动映射与主功放消费的来源按运行累积，互不覆盖；
- 没有来源文档的旧运行继续可读，不影响阶段结果按显式路径加载。
"""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from application.dto import (
    CalibrationInputSource,
    CableLossMeasurementResult,
    CableLossRow,
    DriverMappingRawPoint,
    DriverMappingTargetPoint,
    DriverPowerMappingMeasurementResult,
    generate_measurement_id,
)
from application.ports.stage_result_repository import MeasurementStage
from application.run_stages import (
    CALIBRATION_INPUTS_INPUT_PATH,
    CALIBRATION_INPUTS_SCHEMA,
    RunStageRecorder,
)
from infrastructure.filesystem.paths import PathService
from infrastructure.persistence.run_repository import RunRepository
from infrastructure.persistence.stage_result_repository import FileStageResultRepository


RUN_ID = "20260101T000000Z_sources"
OTHER_RUN_ID = "20260101T010000Z_source"
CREATED_AT = datetime(2026, 10, 22, 15, 30, tzinfo=timezone.utc)
CABLE_LOSSES = {"1.0": {"cable1": 0.1, "cable2": 0.2, "cable3": 0.3, "cable4": 0.4}}


class CalibrationInputSourceTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.base = Path(self._temporary.name)
        self.paths = PathService(self.base)
        self.repository = RunRepository(self.base / "runs")
        self.stage_repository = FileStageResultRepository(paths=self.paths)
        self.repository.create_run({"run_id": RUN_ID}, run_id=RUN_ID, now=CREATED_AT)

    def _recorder(self):
        return RunStageRecorder(
            self.repository, RUN_ID, stage_results=self.stage_repository
        )

    def _source(self, stage=MeasurementStage.CABLE_LOSS, *, run_id=OTHER_RUN_ID):
        if stage is MeasurementStage.CABLE_LOSS:
            result = CableLossMeasurementResult(
                run_id=run_id,
                measurement_id=generate_measurement_id(),
                points=tuple(
                    CableLossRow(frequency_ghz=frequency, cable_losses=values)
                    for frequency, values in CABLE_LOSSES.items()
                ),
            )
        else:
            result = DriverPowerMappingMeasurementResult(
                run_id=run_id,
                measurement_id=generate_measurement_id(),
                raw_points=(
                    DriverMappingRawPoint(
                        frequency_ghz="1.0",
                        source_power_dbm=-20.0,
                        driver_output_power_dbm=-20.0,
                    ),
                ),
                target_points=(
                    DriverMappingTargetPoint(
                        frequency_ghz="1.0",
                        target_input_power_dbm=-20.0,
                        source_power_dbm=-20.0,
                        driver_output_power_dbm=-20.0,
                    ),
                ),
            )
        reference = self.stage_repository.save(stage, result)
        return CalibrationInputSource(
            measurement_type="amplifier",
            stage=reference.stage.value,
            measurement_id=reference.measurement_id,
            path=str(reference.path),
            sha256=reference.sha256,
            size_bytes=reference.size_bytes,
            source_run_id=run_id,
        )

    def _document(self):
        return self.repository.read_run_json(RUN_ID, CALIBRATION_INPUTS_INPUT_PATH)

    def test_source_document_records_the_consumed_input_and_its_run(self):
        source = self._source()

        reference = self._recorder().record_calibration_inputs((source,), now=CREATED_AT)

        self.assertEqual(reference.path, CALIBRATION_INPUTS_INPUT_PATH)
        document = self._document()
        self.assertEqual(document["schema"], CALIBRATION_INPUTS_SCHEMA)
        self.assertEqual(document["run_id"], RUN_ID)
        [entry] = document["inputs"]
        self.assertEqual(entry["measurement_type"], "amplifier")
        self.assertEqual(entry["stage"], MeasurementStage.CABLE_LOSS.value)
        self.assertEqual(entry["measurement_id"], source.measurement_id)
        # 来源路径指向阶段目录文件本身，摘要与大小与实际文件一致。
        self.assertEqual(Path(entry["path"]), Path(source.path))
        self.assertEqual(entry["sha256"], source.sha256)
        self.assertEqual(entry["size_bytes"], Path(source.path).stat().st_size)
        self.assertEqual(entry["source_run_id"], OTHER_RUN_ID)
        # manifest 只登记来源文档这个输入文件，不登记阶段目录结果正文。
        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(
            [item.path for item in manifest.inputs], [CALIBRATION_INPUTS_INPUT_PATH]
        )

    def test_recording_the_same_source_twice_is_idempotent(self):
        source = self._source()
        recorder = self._recorder()

        recorder.record_calibration_inputs((source,), now=CREATED_AT)
        before = (self.base / "runs" / RUN_ID / CALIBRATION_INPUTS_INPUT_PATH).read_bytes()
        registered_at = self._document()["registered_at"]

        recorder.record_calibration_inputs((source,), now=CREATED_AT)

        after = (self.base / "runs" / RUN_ID / CALIBRATION_INPUTS_INPUT_PATH).read_bytes()
        self.assertEqual(after, before)
        self.assertEqual(self._document()["registered_at"], registered_at)

    def test_distinct_consumers_accumulate_without_overwriting(self):
        loss = self._source()
        mapping = self._source(MeasurementStage.DRIVER_MAPPING)
        recorder = self._recorder()

        recorder.record_calibration_inputs((loss,), now=CREATED_AT)
        recorder.record_calibration_inputs((mapping,), now=CREATED_AT)

        entries = self._document()["inputs"]
        self.assertEqual(
            [(entry["measurement_type"], entry["stage"]) for entry in entries],
            [
                ("amplifier", MeasurementStage.CABLE_LOSS.value),
                ("amplifier", MeasurementStage.DRIVER_MAPPING.value),
            ],
        )

    def test_non_source_values_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "CalibrationInputSource"):
            self._recorder().record_calibration_inputs((object(),))

    def test_source_rejects_a_path_outside_the_stage_identity(self):
        source = self._source()
        with self.assertRaisesRegex(ValueError, "文件名必须由 measurement_id 构造"):
            CalibrationInputSource(
                measurement_type="amplifier",
                stage=source.stage,
                measurement_id=source.measurement_id,
                path=str(Path(source.path).with_name("other.json")),
                sha256=source.sha256,
                size_bytes=source.size_bytes,
                source_run_id=source.source_run_id,
            )

    def test_old_run_without_the_source_document_still_registers(self):
        """旧运行没有来源文档时按“无记录”处理，不阻止登记。"""
        source = self._source()
        document_path = (
            self.base / "runs" / RUN_ID / CALIBRATION_INPUTS_INPUT_PATH
        )
        document_path.write_text(
            json.dumps({"schema": "legacy", "inputs": []}), encoding="utf-8"
        )

        self._recorder().record_calibration_inputs((source,), now=CREATED_AT)

        self.assertEqual(self._document()["schema"], CALIBRATION_INPUTS_SCHEMA)
        self.assertEqual(len(self._document()["inputs"]), 1)


if __name__ == "__main__":
    unittest.main()
