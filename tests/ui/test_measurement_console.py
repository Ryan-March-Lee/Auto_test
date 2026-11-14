"""运行控制台：运行事实、前置检查与主题选择器。"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QLabel

from presentation.qt.measurement_console import (
    CheckState,
    ConsoleCheck,
    MeasurementRunConsole,
    archive_check,
    input_check,
    run_session_check,
    wiring_check,
)
from presentation.qt.theme import build_stylesheet


def _destroy(widget):
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _led_tones(console: MeasurementRunConsole) -> list[str]:
    return [
        led.property("tone")
        for led in console.findChildren(QLabel, "consoleLed")
        if not led.isHidden()
    ]


class ConsoleCheckTests(unittest.TestCase):
    def test_check_carries_label_state_and_detail(self):
        check = ConsoleCheck("执行会话", CheckState.READY, "已连接运行")

        self.assertEqual(check.label, "执行会话")
        self.assertIs(check.state, CheckState.READY)
        self.assertEqual(check.detail, "已连接运行")

    def test_check_rejects_invalid_fields(self):
        with self.assertRaises(ValueError):
            ConsoleCheck("  ", CheckState.READY)
        with self.assertRaises(TypeError):
            ConsoleCheck("执行会话", "ready")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            ConsoleCheck("执行会话", CheckState.READY, None)  # type: ignore[arg-type]


class ConsoleCheckHelperTests(unittest.TestCase):
    def test_run_session_check_reads_only_a_run_id(self):
        self.assertIs(run_session_check("run-1").state, CheckState.READY)
        self.assertIs(run_session_check(None).state, CheckState.WAITING)
        self.assertIs(run_session_check("  ").state, CheckState.WAITING)
        self.assertEqual(run_session_check(None).label, "执行会话")

    def test_input_check_marks_the_missing_calibration_input(self):
        self.assertIs(input_check("线损结果", True).state, CheckState.READY)
        self.assertEqual(input_check("线损结果", False).detail, "待选择")
        self.assertIs(input_check("线损结果", False).state, CheckState.WAITING)
        with self.assertRaises(TypeError):
            input_check("线损结果", "yes")  # type: ignore[arg-type]

    def test_wiring_is_optional_because_it_is_confirmed_at_start(self):
        check = wiring_check()

        self.assertIs(check.state, CheckState.OPTIONAL)
        self.assertEqual(check.detail, "开始时确认")

    def test_archive_check_distinguishes_unconfigured_missing_and_ready(self):
        self.assertEqual(
            archive_check(None, configured=False).detail, "未配置归档位置"
        )
        self.assertIs(
            archive_check(None, configured=False).state, CheckState.WAITING
        )
        self.assertEqual(archive_check(None, configured=True).detail, "等待运行")
        self.assertIs(
            archive_check("runs/run-1", configured=True).state, CheckState.READY
        )
        with self.assertRaises(TypeError):
            archive_check("runs/run-1", configured=1)  # type: ignore[arg-type]


class MeasurementRunConsoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _console(self) -> MeasurementRunConsole:
        console = MeasurementRunConsole()
        self.addCleanup(_destroy, console)
        return console

    def test_starts_with_no_run_and_no_archive(self):
        console = self._console()

        self.assertEqual(console.run_id_text(), "未连接")
        self.assertIsNone(console.mode_text())
        self.assertEqual(console.archive_text(), "未配置")
        self.assertEqual(console.checks(), ())

    def test_run_facts_show_id_and_keep_the_full_value_in_the_tooltip(self):
        console = self._console()
        long_run = "run-20261114T010203Z-0123456789abcdef"

        console.set_run_facts(
            run_id=long_run,
            mode="有驱放测试",
            archive=r"C:\runs\2026\run-1\artifacts",
            archive_configured=True,
        )

        self.assertEqual(len(console.run_id_text()), 36)
        self.assertIn("…", console.run_id_text())
        self.assertEqual(console.run_id_tooltip(), long_run)
        self.assertEqual(console.mode_text(), "有驱放测试")
        self.assertEqual(console.archive_text(), "…/run-1/artifacts")
        self.assertIn("C:\\runs\\2026\\run-1\\artifacts", console._archive_value.toolTip())

    def test_mode_row_is_hidden_when_no_mode_is_known(self):
        console = self._console()
        console.set_run_facts(
            run_id="run-1", mode="有驱放测试", archive=None
        )
        self.assertEqual(console.mode_text(), "有驱放测试")

        console.set_run_facts(run_id="run-1", mode=None, archive=None)

        self.assertIsNone(console.mode_text())
        self.assertTrue(console._mode_value.isHidden())
        self.assertTrue(console._mode_caption.isHidden())

    def test_archive_text_distinguishes_unready_from_unconfigured(self):
        console = self._console()
        console.set_run_facts(
            run_id=None, mode=None, archive=None, archive_configured=True
        )
        self.assertEqual(console.archive_text(), "未就绪")

        console.set_run_facts(
            run_id=None, mode=None, archive=None, archive_configured=False
        )
        self.assertEqual(console.archive_text(), "未配置")

    def test_archive_text_shows_a_long_run_directory_only_once_elided(self):
        console = self._console()
        path = (
            r"D:\runs\20261114T201158Z_922f77f9"
        )

        console.set_run_facts(run_id=None, mode=None, archive=path)

        self.assertEqual(console.archive_text(), "…/20261114T201158Z_922f77f9")
        self.assertEqual(console.archive_text().count("…"), 1)
        self.assertEqual(console._archive_value.toolTip(), path)

    def test_run_facts_reject_non_string_values(self):
        console = self._console()

        with self.assertRaises(TypeError):
            console.set_run_facts(run_id=1, mode=None, archive=None)
        with self.assertRaises(TypeError):
            console.set_run_facts(run_id=None, mode=1, archive=None)
        with self.assertRaises(TypeError):
            console.set_run_facts(run_id=None, mode=None, archive=1)
        with self.assertRaises(TypeError):
            console.set_run_facts(
                run_id=None, mode=None, archive=None, archive_configured=1
            )

    def test_checks_update_led_tones_and_reuse_rows(self):
        console = self._console()

        console.set_checks(
            (
                ConsoleCheck("执行会话", CheckState.READY, "已连接运行"),
                ConsoleCheck("线损结果", CheckState.WAITING, "待选择"),
                wiring_check(),
            )
        )

        self.assertEqual(len(console.checks()), 3)
        self.assertIs(console.check_state("执行会话"), CheckState.READY)
        self.assertIs(console.check_state("线损结果"), CheckState.WAITING)
        self.assertIs(console.check_state("接线确认"), CheckState.OPTIONAL)
        self.assertIsNone(console.check_state("结果归档"))
        self.assertEqual(_led_tones(console), ["ready", "waiting", "optional"])

        console.set_checks((archive_check("runs/run-1", configured=True),))

        self.assertEqual(_led_tones(console), ["ready"])
        self.assertEqual(len(console._check_rows), 3)
        for led, label, detail in console._check_rows[1:]:
            self.assertTrue(led.isHidden())
            self.assertTrue(label.isHidden())
            self.assertTrue(detail.isHidden())

    def test_check_rows_show_label_and_detail(self):
        console = self._console()
        console.set_checks((ConsoleCheck("线损结果", CheckState.WAITING, "待选择"),))

        led, label, detail = console._check_rows[0]
        self.assertEqual(label.text(), "线损结果")
        self.assertEqual(detail.text(), "待选择")
        self.assertFalse(label.isHidden())
        self.assertFalse(detail.isHidden())
        self.assertEqual(led.accessibleName(), "前置检查状态")

    def test_check_detail_keeps_its_width_after_layout(self):
        """右侧短说明不能被布局压成 0 宽：它是检查行唯一的文字结论。"""
        console = self._console()
        console.set_checks((ConsoleCheck("线损结果", CheckState.WAITING, "待选择"),))
        console.resize(320, 220)
        console.show()
        self.app.processEvents()

        _, _, detail = console._check_rows[0]
        self.assertEqual(detail.text(), "待选择")
        self.assertGreater(detail.width(), 0)
        self.assertGreaterEqual(detail.width(), detail.sizeHint().width())

    def test_more_than_the_maximum_rows_is_an_error(self):
        console = self._console()
        checks = tuple(
            ConsoleCheck(f"检查 {index}", CheckState.READY)
            for index in range(console.MAX_CHECKS + 1)
        )

        with self.assertRaises(ValueError):
            console.set_checks(checks)

    def test_checks_reject_non_console_check_values(self):
        console = self._console()

        with self.assertRaises(TypeError):
            console.set_checks("执行会话")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            console.set_checks((object(),))  # type: ignore[arg-type]


class MeasurementRunConsoleThemeTests(unittest.TestCase):
    def test_stylesheet_contains_console_selectors(self):
        stylesheet = build_stylesheet()

        for selector in (
            "QLabel#consoleFactLabel",
            "QLabel#consoleFactValue",
            "QLabel#consoleCheckLabel",
            "QLabel#consoleCheckDetail",
            "QLabel#consoleLed",
            'QLabel#consoleLed[tone="ready"]',
            'QLabel#consoleLed[tone="waiting"]',
            'QLabel#consoleLed[tone="optional"]',
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, stylesheet)


if __name__ == "__main__":
    unittest.main()
