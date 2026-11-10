"""步骤 12 收尾：连接说明对话框的失败语义。

连接图不可用（可选依赖缺失、图工厂失败、未知图类型）时对话框必须给出原因，
并且**禁用确认按钮**：看不到接线图就不能完成现场接线确认。
"""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from presentation.qt.connection_dialog import ConnectionDialog, build_diagram


def _application():
    return QApplication.instance() or QApplication([])


class BuildDiagramTests(unittest.TestCase):
    def test_unknown_diagram_type_is_reported_instead_of_drawn(self):
        figure, reason = build_diagram("不存在的图")

        self.assertIsNotNone(reason)
        self.assertIn("未知的连接图类型", reason)
        self.assertEqual(list(figure.axes), [])

    def test_render_failure_is_reported_instead_of_raised(self):
        from presentation.qt import connection_diagrams

        with patch.object(
            connection_diagrams.ConnectionDiagram,
            "create_amplifier_test",
            side_effect=RuntimeError("字体初始化失败"),
        ):
            figure, reason = build_diagram("amplifier_test")

        self.assertIsNotNone(reason)
        self.assertIn("连接图渲染失败", reason)
        self.assertIn("字体初始化失败", reason)
        self.assertEqual(list(figure.axes), [])

    def test_missing_optional_dependency_is_reported(self):
        import sys

        with patch.dict(sys.modules, {"presentation.qt.connection_diagrams": None}):
            figure, reason = build_diagram("amplifier_test")

        self.assertIsNotNone(reason)
        self.assertIn("连接图功能未启用", reason)
        self.assertEqual(list(figure.axes), [])

    def test_successful_diagram_has_no_reason(self):
        figure, reason = build_diagram("amplifier_test")

        self.assertIsNone(reason)
        self.assertTrue(figure.axes)

    def test_combined_cable_loss_diagram_stacks_both_paths(self):
        figure, reason = build_diagram("cable_loss_paths")

        self.assertIsNone(reason)
        self.assertEqual(len(figure.axes), 2)
        self.assertIn("路径1", figure.axes[0].get_title())
        self.assertIn("路径2", figure.axes[1].get_title())


class ConnectionDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = _application()

    def test_confirm_is_available_only_when_the_diagram_rendered(self):
        dialog = ConnectionDialog("amplifier_test")
        self.addCleanup(dialog.deleteLater)

        self.assertIsNone(dialog.unavailable_reason)
        self.assertTrue(dialog.ok_btn.isEnabled())
        self.assertTrue(dialog.cancel_btn.isEnabled())

    def test_confirm_is_disabled_when_the_diagram_is_unavailable(self):
        dialog = ConnectionDialog("不存在的图")
        self.addCleanup(dialog.deleteLater)

        self.assertIsNotNone(dialog.unavailable_reason)
        self.assertFalse(dialog.ok_btn.isEnabled())
        self.assertTrue(dialog.cancel_btn.isEnabled())

    def test_render_failure_also_disables_the_confirmation(self):
        from presentation.qt import connection_diagrams

        with patch.object(
            connection_diagrams.ConnectionDiagram,
            "create_amplifier_test",
            side_effect=ValueError("图数据非法"),
        ):
            dialog = ConnectionDialog("amplifier_test")
        self.addCleanup(dialog.deleteLater)

        self.assertFalse(dialog.ok_btn.isEnabled())
        self.assertIn("图数据非法", dialog.unavailable_reason)

    def test_combined_diagram_is_a_view_only_window(self):
        dialog = ConnectionDialog("cable_loss_paths")
        self.addCleanup(dialog.deleteLater)

        self.assertTrue(dialog.view_only)
        self.assertEqual(dialog.windowTitle(), "线损测量连接图")
        self.assertEqual(dialog.close_btn.text(), "关闭")
        self.assertFalse(hasattr(dialog, "ok_btn"))
        self.assertFalse(hasattr(dialog, "cancel_btn"))


if __name__ == "__main__":
    unittest.main()
