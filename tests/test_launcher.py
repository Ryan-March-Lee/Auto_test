import io
import sys
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch

import launcher


class LauncherTests(unittest.TestCase):
    def test_help_returns_success(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(launcher.main(["--help"]), launcher.EXIT_SUCCESS)

    def test_validate_config_returns_success_for_current_config(self):
        with patch.object(launcher, "validate_config_file", return_value=launcher.ConfigValidationResult([], [])):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(launcher.main(["--validate-config"]), launcher.EXIT_SUCCESS)

    def test_validate_config_returns_config_error_without_importing_gui(self):
        from config_validation import ConfigIssue

        invalid_result = launcher.ConfigValidationResult(
            errors=[ConfigIssue("error", "test", "invalid")],
            warnings=[],
        )
        with patch.object(launcher, "validate_config_file", return_value=invalid_result):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(launcher.main(["--validate-config"]), launcher.EXIT_CONFIG_ERROR)

    def test_validate_missing_file_returns_file_error(self):
        with patch.object(launcher, "validate_config_file", side_effect=FileNotFoundError):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(launcher.validate_default_config(), launcher.EXIT_CONFIG_FILE_ERROR)

    def test_unknown_argument_returns_argument_error(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(launcher.main(["--unknown-option"]), 2)

    def test_check_and_validate_config_are_mutually_exclusive(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(launcher.main(["--check", "--validate-config"]), 2)

    def test_validate_config_does_not_check_packages(self):
        with patch.object(launcher, "validate_config_file", return_value=launcher.ConfigValidationResult([], [])), patch.object(
            launcher, "check_packages", side_effect=AssertionError("不应检查依赖")
        ):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(launcher.main(["--validate-config"]), launcher.EXIT_SUCCESS)

    def test_normal_start_with_missing_dependency_does_not_validate_or_launch(self):
        packages = {"required": {"required": True, "installed": False, "version": "", "error": "未找到模块"}}
        with patch.object(launcher, "check_environment", return_value=True), patch.object(
            launcher, "check_packages", return_value=packages
        ), patch.object(launcher, "validate_default_config", side_effect=AssertionError("不应校验配置")), patch.object(
            launcher, "launch_gui_version", side_effect=AssertionError("不应启动 GUI")
        ):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(launcher.main(["--silent"]), launcher.EXIT_DEPENDENCY_ERROR)

    def test_legacy_gui_launcher_signature_remains_available(self):
        fake_gui = types.ModuleType("enhanced_main_gui")
        fake_gui.main = Mock()
        with patch.dict(sys.modules, {"enhanced_main_gui": fake_gui}):
            self.assertTrue(launcher.launch_gui_version("enhanced", {"PySide6": {"installed": True}}, False))
        fake_gui.main.assert_called_once()

    def test_silent_config_error_is_written_to_standard_error(self):
        from config_validation import ConfigIssue

        invalid_result = launcher.ConfigValidationResult(
            errors=[ConfigIssue("error", "test", "invalid")],
            warnings=[],
        )
        standard_error = io.StringIO()
        with patch.object(launcher, "validate_config_file", return_value=invalid_result), redirect_stdout(
            io.StringIO()
        ), redirect_stderr(standard_error):
            self.assertEqual(launcher.main(["--silent", "--validate-config"]), launcher.EXIT_CONFIG_ERROR)
        self.assertIn("错误 [test]: invalid", standard_error.getvalue())

    def test_missing_required_package_returns_dependency_error_for_check(self):
        packages = {"required": {"required": True, "installed": False, "version": ""}}
        with patch.object(launcher, "check_environment", return_value=True), patch.object(
            launcher, "check_packages", return_value=packages
        ):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(launcher.main(["--check"]), launcher.EXIT_DEPENDENCY_ERROR)

    def test_check_packages_reports_standard_library_module_as_available(self):
        original_packages = launcher.check_packages
        with patch.object(launcher, "check_packages") as check_packages:
            check_packages.side_effect = original_packages
            packages = launcher.check_packages(silent=True)
        self.assertTrue(packages["requests"]["installed"])

    def test_environment_name_uses_python_prefix_when_conda_variable_is_missing(self):
        with patch.dict("os.environ", {}, clear=True), patch.object(sys, "prefix", r"C:\envs\Auto_test"):
            self.assertEqual(launcher.get_conda_environment_name(), "Auto_test")

    def test_environment_variable_takes_precedence_over_python_prefix(self):
        with patch.dict("os.environ", {"CONDA_DEFAULT_ENV": "Auto_test"}), patch.object(
            sys, "prefix", r"C:\envs\other"
        ):
            self.assertEqual(launcher.get_conda_environment_name(), "Auto_test")

    def test_validate_config_does_not_initialize_logging(self):
        # --validate-config 是只读命令：不得创建日志文件。
        with patch.object(launcher, "validate_config_file", return_value=launcher.ConfigValidationResult([], [])), patch.object(
            launcher, "setup_logging", side_effect=AssertionError("不应初始化日志")
        ):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(launcher.main(["--validate-config"]), launcher.EXIT_SUCCESS)

    def test_check_does_not_initialize_logging(self):
        # --check 是只读命令：不得创建日志文件。
        with patch.object(launcher, "check_environment", return_value=True), patch.object(
            launcher, "check_packages"
        ), patch.object(launcher, "setup_logging", side_effect=AssertionError("不应初始化日志")):
            with redirect_stdout(io.StringIO()):
                launcher.main(["--check"])

    def test_gui_start_initializes_logging_before_launch(self):
        # 正常 GUI 启动路径应在启动 GUI 前完成日志初始化。
        calls = []
        with patch.object(launcher, "check_environment", return_value=True), patch.object(
            launcher, "check_packages", return_value={}
        ), patch.object(
            launcher, "validate_default_config", return_value=launcher.EXIT_SUCCESS
        ), patch.object(
            launcher, "setup_logging", side_effect=lambda: calls.append("log")
        ), patch.object(
            launcher, "launch_gui_version", side_effect=lambda **kw: calls.append("gui") or True
        ):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(launcher.main([]), launcher.EXIT_SUCCESS)
        self.assertEqual(calls, ["log", "gui"])

    def test_logging_failure_does_not_block_gui_start(self):
        with patch.object(launcher, "check_environment", return_value=True), patch.object(
            launcher, "check_packages", return_value={}
        ), patch.object(
            launcher, "validate_default_config", return_value=launcher.EXIT_SUCCESS
        ), patch.object(
            launcher, "setup_logging", side_effect=OSError("磁盘不可写")
        ), patch.object(
            launcher, "launch_gui_version", return_value=True
        ):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(launcher.main([]), launcher.EXIT_SUCCESS)

    def test_main_does_not_reference_silent_mode_before_initialization(self):
        with patch.object(launcher, "check_environment", side_effect=RuntimeError("boom")):
            with patch.object(launcher, "check_packages"):
                self.assertEqual(launcher.main(["--silent"]), launcher.EXIT_GUI_ERROR)


if __name__ == "__main__":
    unittest.main()
