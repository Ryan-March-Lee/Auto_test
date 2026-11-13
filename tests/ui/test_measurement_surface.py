"""共享测量面的契约（重构计划步骤 9）。

三个测量页在步骤 10–12 里都会换成“控制面 + 结果面”。这里先锁定共享件本身：

- 两种面由 objectName 区分，外观只由主题样式表决定；
- 面自带垂直布局，标题、发丝线与度量标签都是面内可用的最小件；
- 未知面类型在构造时报错，而不是画出一块没有样式的空框；
- 主题里确实存在对应选择器，``sectionCaption`` 也已登记为合法语义角色。

离屏用例只检查结构、文本与常量；像素外观由 QSS 单源保证。
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QFrame,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
)

from presentation.qt.measurement_surface import (  # noqa: E402
    CONTROL_SURFACE,
    RESULT_SURFACE,
    SURFACE_KINDS,
    SURFACE_MARGINS,
    SURFACE_SPACING,
    build_surface,
    hairline,
    metric_label,
    section_caption,
)
from presentation.qt.theme import LABEL_ROLES, build_stylesheet  # noqa: E402


def _destroy(widget):
    """关闭并销毁控件，再投递排队的删除（离屏用例的统一收尾方式）。"""
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class MeasurementSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _track(self, widget):
        self.addCleanup(_destroy, widget)
        return widget

    def test_control_surface_has_shared_name_and_layout(self):
        surface = self._track(build_surface())

        self.assertIsInstance(surface, QFrame)
        self.assertEqual(surface.objectName(), "measurementSurface")
        layout = surface.layout()
        self.assertIsInstance(layout, QVBoxLayout)
        margins = layout.contentsMargins()
        self.assertEqual(
            (
                margins.left(),
                margins.top(),
                margins.right(),
                margins.bottom(),
            ),
            SURFACE_MARGINS,
        )
        self.assertEqual(layout.spacing(), SURFACE_SPACING)
        self.assertEqual(layout.count(), 0)

    def test_result_surface_has_its_own_name(self):
        surface = self._track(build_surface(RESULT_SURFACE))

        self.assertEqual(surface.objectName(), "measurementResultSurface")
        self.assertNotEqual(surface.objectName(), "measurementSurface")

    def test_surface_can_start_with_a_section_caption(self):
        surface = self._track(build_surface(RESULT_SURFACE, title="测量结果"))

        layout = surface.layout()
        self.assertEqual(layout.count(), 1)
        caption = layout.itemAt(0).widget()
        self.assertIsInstance(caption, QLabel)
        self.assertEqual(caption.text(), "测量结果")
        self.assertEqual(caption.property("role"), "sectionCaption")

    def test_empty_surface_title_is_rejected(self):
        with self.assertRaises(ValueError):
            build_surface(title="   ")

    def test_invalid_title_does_not_create_a_widget(self):
        """标题校验先于控件构造：非法输入不应留下任何 Qt 对象。"""
        before = set(QApplication.allWidgets())
        with self.assertRaises(ValueError):
            build_surface(title="   ")
        self.assertEqual(set(QApplication.allWidgets()), before)

    def test_unknown_surface_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            build_surface("unknown")
        with self.assertRaises(TypeError):
            build_surface(None)
        self.assertEqual(SURFACE_KINDS, (CONTROL_SURFACE, RESULT_SURFACE))

    def test_section_caption_declares_only_its_semantic_role(self):
        caption = self._track(section_caption("校准输入"))

        self.assertIsInstance(caption, QLabel)
        self.assertEqual(caption.text(), "校准输入")
        self.assertEqual(caption.property("role"), "sectionCaption")
        self.assertIn("sectionCaption", LABEL_ROLES)

    def test_hairline_is_a_one_pixel_expanding_line(self):
        line = self._track(hairline())

        self.assertIsInstance(line, QFrame)
        self.assertEqual(line.objectName(), "measurementHairline")
        self.assertEqual(line.frameShape(), QFrame.Shape.NoFrame)
        self.assertEqual(line.minimumHeight(), 1)
        self.assertEqual(line.maximumHeight(), 1)
        policy = line.sizePolicy()
        self.assertEqual(policy.horizontalPolicy(), QSizePolicy.Policy.Expanding)
        self.assertEqual(policy.verticalPolicy(), QSizePolicy.Policy.Fixed)

    def test_metric_label_is_read_only_text_with_shared_object_name(self):
        metric = self._track(metric_label("6 频点"))

        self.assertIsInstance(metric, QLabel)
        self.assertEqual(metric.objectName(), "measurementMetric")
        self.assertEqual(metric.text(), "6 频点")
        self.assertEqual(metric.textFormat(), Qt.TextFormat.PlainText)
        self.assertTrue(
            metric.textInteractionFlags()
            & Qt.TextInteractionFlag.TextSelectableByMouse
        )


class MeasurementSurfaceThemeTests(unittest.TestCase):
    """共享面的外观必须在主题里确有规则，页面不再各自写样式。"""

    def test_stylesheet_contains_every_measurement_surface_selector(self):
        stylesheet = build_stylesheet()

        for selector in (
            "QFrame#measurementSurface",
            "QFrame#measurementResultSurface",
            "QFrame#measurementResultSurface QTableWidget",
            'QLabel[role="sectionCaption"]',
            "QLabel#measurementMetric",
            "QFrame#measurementHairline",
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, stylesheet)


if __name__ == "__main__":
    unittest.main()
