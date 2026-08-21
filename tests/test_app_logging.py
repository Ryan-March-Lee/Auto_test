"""app_logging 最小日志基础设施测试。"""

import logging
import tempfile
import unittest
from pathlib import Path

import app_logging


def _reset_root_logger():
    root_logger = logging.getLogger()
    path = getattr(root_logger, app_logging._MARKER_ATTRIBUTE, None)
    if path is not None:
        delattr(root_logger, app_logging._MARKER_ATTRIBUTE)
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        handler.close()
    return path


class AppLoggingTests(unittest.TestCase):
    def setUp(self):
        _reset_root_logger()

    def tearDown(self):
        _reset_root_logger()

    def test_setup_logging_creates_log_file_and_accepts_records(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = app_logging.setup_logging(directory)
            self.assertTrue(log_path.exists())
            self.assertIn("pa_auto_test_", log_path.name)
            logging.getLogger("test.module").info("hello-logging")
            _flush_root_handlers()
            content = log_path.read_text(encoding="utf-8")
            self.assertIn("hello-logging", content)
            self.assertIn("test.module", content)
            _reset_root_logger()  # 释放文件句柄，Windows 下临时目录才能删除

    def test_setup_logging_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            first = app_logging.setup_logging(directory)
            second = app_logging.setup_logging(directory)
            self.assertEqual(first, second)
            self.assertEqual(app_logging.current_log_path(), first)
            handler_count = len(logging.getLogger().handlers)
            app_logging.setup_logging(directory)
            self.assertEqual(len(logging.getLogger().handlers), handler_count)
            _reset_root_logger()

    def test_current_log_path_is_none_before_setup(self):
        self.assertIsNone(app_logging.current_log_path())

    def test_instrument_events_are_logged_via_module_logger(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = app_logging.setup_logging(directory)
            logging.getLogger("instrument_control").warning("RF 输出关闭")
            _flush_root_handlers()
            content = log_path.read_text(encoding="utf-8")
            self.assertIn("instrument_control", content)
            self.assertIn("RF 输出关闭", content)
            _reset_root_logger()

    def test_log_directory_is_created_when_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            nested = Path(directory) / "logs" / "nested"
            log_path = app_logging.setup_logging(nested)
            self.assertTrue(nested.exists())
            self.assertTrue(log_path.exists())
            _reset_root_logger()


def _flush_root_handlers():
    for handler in logging.getLogger().handlers:
        handler.flush()


if __name__ == "__main__":
    unittest.main()
