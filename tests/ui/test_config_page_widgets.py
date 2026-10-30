"""配置页控件构建辅助的独立测试。

覆盖底部操作栏把按钮登记到页面上，以及页面共用的数值框/下拉框工厂建出来的初始状态：
工厂只决定控件怎么起步，控件仍由页面持有并连接信号。
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from presentation.qt.config_page_widgets import build_action_bar, combo_box, spin_box


class BuildActionBarTests(unittest.TestCase):
    """操作栏把页面要连接的按钮登记到页面对象上。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_registers_the_buttons_the_page_connects(self):
        page = QWidget()

        bar = build_action_bar(page)

        for name in ("previous_step_btn", "save_config_btn", "return_welcome_btn"):
            self.assertTrue(hasattr(page, name), f"操作栏应把 {name} 登记到页面上")
        self.assertEqual(bar.objectName(), "pageActionBar")
        self.assertEqual(page.save_config_btn.text(), "保存配置")


class InputFactoryTests(unittest.TestCase):
    """数值框与下拉框工厂：范围、初值与“还没有值”的语义。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_spin_box_sets_the_range_value_and_page_width(self):
        widget = spin_box(3.5, 0, 50)

        self.assertEqual((widget.minimum(), widget.maximum()), (0, 50))
        self.assertEqual(widget.value(), 3.5)
        self.assertEqual(widget.maximumWidth(), 140)

    def test_combo_box_selects_a_matching_current_text(self):
        widget = combo_box(("1dB", "3dB", "5dB"), "3dB")

        self.assertEqual(widget.currentText(), "3dB")

    def test_combo_box_without_a_current_value_keeps_the_first_item(self):
        """``current=None`` 表示“还没有值”：不把 ``None`` 渲染成一项文本。"""
        widget = combo_box(("a", "b"), None)

        self.assertEqual(widget.count(), 2)
        self.assertEqual(widget.currentIndex(), 0)

    def test_combo_box_can_start_empty_for_choices_filled_later(self):
        """电源分配下拉框先建空框，选项由 ``apply_power_choice`` 填入。"""
        widget = combo_box()

        self.assertEqual(widget.count(), 0)
        self.assertEqual(widget.currentIndex(), -1)


if __name__ == "__main__":
    unittest.main()
