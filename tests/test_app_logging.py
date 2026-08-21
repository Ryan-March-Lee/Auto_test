"""app_logging 最小日志基础设施测试。

只操作专用 ``pa_auto_test`` logger；root logger 的处理器和级别
必须保持不变。Windows 下先关闭日志文件句柄再清理临时目录。
"""

import logging
import os
import tempfile
import unittest
from io import StringIO
from pathlib import Path

import app_logging
from app_logging import ROOT_LOGGER_NAME


class AppLoggingTests(unittest.TestCase):
    def setUp(self):
        app_logging.reset_logging()
        self._temporary = tempfile.TemporaryDirectory()
        self.directory = self._temporary.name

    def tearDown(self):
        # 先复位日志（关闭文件句柄），再删除临时目录（Windows 句柄占用）。
        app_logging.reset_logging()
        self._temporary.cleanup()

    def test_setup_logging_creates_log_file_and_accepts_records(self):
        log_path = app_logging.setup_logging(self.directory)
        self.assertTrue(log_path.exists())
        self.assertIn("pa_auto_test_", log_path.name)
        app_logging.get_logger("test.module").info("hello-logging")
        self._flush()
        content = log_path.read_text(encoding="utf-8")
        self.assertIn("hello-logging", content)
        self.assertIn("pa_auto_test.module", content)

    def test_setup_logging_is_idempotent(self):
        first = app_logging.setup_logging(self.directory)
        second = app_logging.setup_logging(self.directory)
        self.assertEqual(first, second)
        self.assertEqual(app_logging.current_log_path(), first)
        handler_count = len(logging.getLogger(ROOT_LOGGER_NAME).handlers)
        app_logging.setup_logging(self.directory)
        self.assertEqual(len(logging.getLogger(ROOT_LOGGER_NAME).handlers), handler_count)

    def test_current_log_path_is_none_before_setup(self):
        self.assertIsNone(app_logging.current_log_path())

    def test_log_file_name_contains_pid(self):
        log_path = app_logging.setup_logging(self.directory)
        self.assertIn(f"_{os.getpid()}.log", log_path.name)

    def test_setup_logging_does_not_touch_root_logger(self):
        root = logging.getLogger()
        original_handlers = list(root.handlers)
        original_level = root.level
        app_logging.setup_logging(self.directory)
        self.assertEqual(list(root.handlers), original_handlers)
        self.assertEqual(root.level, original_level)
        self.assertFalse(logging.getLogger(ROOT_LOGGER_NAME).propagate)

    def test_visa_addresses_are_redacted(self):
        log_path = app_logging.setup_logging(self.directory)
        app_logging.get_logger("instrument_control").exception(
            "仪器初始化失败: %s",
            RuntimeError("无法打开资源 TCPIP0::192.168.1.201::inst0::INSTR"))
        self._flush()
        content = log_path.read_text(encoding="utf-8")
        self.assertNotIn("192.168.1.201", content)
        self.assertIn("[REDACTED:visa]", content)
        self.assertIn("仪器初始化失败", content)

    def test_secrets_are_redacted(self):
        log_path = app_logging.setup_logging(self.directory)
        app_logging.get_logger("assistant").error(
            "请求失败 api_key=abc123deadbeef token: xyz789")
        self._flush()
        content = log_path.read_text(encoding="utf-8")
        self.assertNotIn("abc123deadbeef", content)
        self.assertNotIn("xyz789", content)
        self.assertIn("[REDACTED:secret]", content)

    def test_url_credentials_are_redacted(self):
        log_path = app_logging.setup_logging(self.directory)
        app_logging.get_logger("assistant").warning(
            "连接 http://user:secret@192.168.1.5:11434 失败")
        self._flush()
        content = log_path.read_text(encoding="utf-8")
        self.assertNotIn("user:secret", content)
        self.assertIn("[REDACTED:credentials]@", content)

    def test_power_channel_names_are_not_redacted(self):
        log_path = app_logging.setup_logging(self.directory)
        app_logging.get_logger("instrument_control").info(
            "上电序列完成: PS4/CH1, PS4/CH2")
        self._flush()
        content = log_path.read_text(encoding="utf-8")
        self.assertIn("PS4/CH1", content)
        self.assertIn("PS4/CH2", content)

    def test_records_before_setup_are_not_propagated_to_root(self):
        root = logging.getLogger()
        captured = StringIO()
        root_handler = logging.StreamHandler(captured)
        root.addHandler(root_handler)
        try:
            app_logging.get_logger("some.module").warning("不应泄漏到 root")
            self.assertEqual(captured.getvalue(), "")
        finally:
            root.removeHandler(root_handler)

    def test_log_directory_is_created_when_missing(self):
        nested = Path(self.directory) / "logs" / "nested"
        log_path = app_logging.setup_logging(nested)
        self.assertTrue(nested.exists())
        self.assertTrue(log_path.exists())

    @staticmethod
    def _flush():
        for handler in logging.getLogger(ROOT_LOGGER_NAME).handlers:
            handler.flush()


if __name__ == "__main__":
    unittest.main()
