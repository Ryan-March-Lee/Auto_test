"""阶段元数据的契约：页面顺序、文案、测量类型映射与查找严格性。"""

import unittest
from dataclasses import FrozenInstanceError, replace

from presentation.qt.measurement_stage import (
    MEASUREMENT_STAGES,
    StageDescriptor,
    StageState,
    stage_for_key,
    stage_for_kind,
)
from presentation.qt.measurement_state import MeasurementKind
from presentation.qt.pages import PAGE_DEFINITIONS


MEASUREMENT_PAGES = tuple(
    definition
    for definition in PAGE_DEFINITIONS
    if definition.key != "test_configuration"
)


def _descriptor(**overrides):
    return replace(MEASUREMENT_STAGES[0], **overrides)


class MeasurementStageMetadataTests(unittest.TestCase):
    def test_stages_match_measurement_pages_in_order_and_titles(self):
        self.assertTrue(
            all(isinstance(stage, StageDescriptor) for stage in MEASUREMENT_STAGES)
        )
        self.assertEqual(
            tuple(stage.key for stage in MEASUREMENT_STAGES),
            tuple(definition.key for definition in MEASUREMENT_PAGES),
            "阶段 key 必须按顺序覆盖 PAGE_DEFINITIONS 的全部测量页",
        )
        self.assertEqual(
            tuple(stage.order for stage in MEASUREMENT_STAGES),
            tuple(range(1, len(MEASUREMENT_STAGES) + 1)),
        )
        self.assertEqual(
            {stage.key: stage.title for stage in MEASUREMENT_STAGES},
            {definition.key: definition.title for definition in MEASUREMENT_PAGES},
            "阶段标题必须与 PAGE_DEFINITIONS 的测量页标题一致，避免两处文案漂移",
        )
        self.assertNotIn(
            "test_configuration",
            {stage.key for stage in MEASUREMENT_STAGES},
            "测试配置是右侧工具入口，不属于阶段条",
        )

    def test_subtitles_are_static_purpose_text(self):
        self.assertEqual(
            {stage.key: stage.subtitle for stage in MEASUREMENT_STAGES},
            {
                "cable_loss": "测量线缆与测试链路损耗",
                "driver_mapping": "建立驱动功率与频点映射",
                "amplifier": "测量主功放输出功率",
                "visualization": "只读查看与导出结果",
            },
            "副标题描述阶段目的；未启用/完成/失败属于 StageState，不能写进静态元数据",
        )

    def test_stage_kinds_match_measurement_types(self):
        self.assertEqual(
            {stage.key: stage.kind for stage in MEASUREMENT_STAGES},
            {
                "cable_loss": MeasurementKind.CABLE_LOSS,
                "driver_mapping": MeasurementKind.DRIVER_MAPPING,
                "amplifier": MeasurementKind.AMPLIFIER,
                "visualization": None,
            },
        )

    def test_measurement_stage_keys_come_from_measurement_kinds(self):
        for stage in MEASUREMENT_STAGES:
            if stage.kind is None:
                continue
            with self.subTest(key=stage.key):
                self.assertEqual(stage.key, stage.kind.value)
                self.assertIs(stage_for_key(stage.kind.value), stage)
                self.assertIs(stage_for_kind(stage.kind), stage)

    def test_stage_state_exposes_every_visual_state(self):
        self.assertEqual(
            {state.value for state in StageState},
            {"pending", "active", "completed", "stopped", "failed", "disabled"},
        )
        # 继承 str，便于直接与 signal 载荷或持久化字符串比较；显示取 .value。
        self.assertIsInstance(StageState.ACTIVE, str)
        self.assertEqual(StageState.ACTIVE, "active")
        self.assertEqual(StageState.ACTIVE.value, "active")

    def test_descriptor_is_frozen_and_validated(self):
        stage = MEASUREMENT_STAGES[0]
        with self.assertRaises(FrozenInstanceError):
            stage.title = "另一个标题"

        with self.assertRaises(TypeError):
            _descriptor(key=1)
        with self.assertRaises(ValueError):
            _descriptor(key="   ")
        with self.assertRaises(ValueError):
            _descriptor(key=" cable_loss")
        with self.assertRaises(ValueError):
            _descriptor(key="amplifier")
        with self.assertRaises(TypeError):
            _descriptor(order=True)
        with self.assertRaises(TypeError):
            _descriptor(order=1.0)
        with self.assertRaises(ValueError):
            _descriptor(order=0)
        with self.assertRaises(TypeError):
            _descriptor(title=None)
        with self.assertRaises(ValueError):
            _descriptor(title="  ")
        with self.assertRaises(TypeError):
            _descriptor(subtitle=None)
        with self.assertRaises(ValueError):
            _descriptor(subtitle="  ")
        with self.assertRaises(TypeError):
            _descriptor(kind="cable_loss")

    def test_stage_for_key_returns_the_registered_descriptor(self):
        for stage in MEASUREMENT_STAGES:
            with self.subTest(key=stage.key):
                self.assertIs(stage_for_key(stage.key), stage)

        for key in ("test_configuration", "unknown", ""):
            with self.subTest(key=key), self.assertRaises(KeyError):
                stage_for_key(key)

        for value in (None, 1, []):
            with self.subTest(value=value), self.assertRaises(TypeError):
                stage_for_key(value)

    def test_stage_for_kind_covers_measurements_but_not_visualization(self):
        self.assertEqual(
            {stage_for_kind(kind).key for kind in MeasurementKind},
            {"cable_loss", "driver_mapping", "amplifier"},
        )
        for kind in MeasurementKind:
            with self.subTest(kind=kind):
                stage = stage_for_kind(kind)
                self.assertIs(stage.kind, kind)
                self.assertIs(stage_for_key(kind.value), stage)

        # None 与字符串都不是测量类型，不能落到只读的可视化阶段。
        for value in (None, "cable_loss"):
            with self.subTest(value=value), self.assertRaises(TypeError):
                stage_for_kind(value)


if __name__ == "__main__":
    unittest.main()
