"""步骤 10 收尾：运行协调器的安全清理契约与配置页窄接口。

覆盖三件事：

1. 端口清理结果必须结构化：``close_all()`` 返回的错误列表不能被当成成功，
   失败端口进入未决清理清单（引用不丢）并可重试。
2. 运行切换只有在旧端口确实释放后才提交；否则保持当前运行不变并明确告警。
3. 协调器只通过 ``ConfigurationPagePort`` 与配置页交互，不读取控件；
   缺少用户可见反馈回调时在构造阶段失败，不允许失败路径静默。
"""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from presentation.qt.application_coordinator import (
    ApplicationCoordinator,
    ConfigurationPagePort,
    close_instrument_port,
    describe_cleanup_errors,
)

RUN_ID = "20261027T101500Z_c0ffee01"


class _OkPort:
    """可正常关闭的端口替身。"""

    def __init__(self):
        self.close_calls = 0

    def close_all(self, *, close_rf=False):
        self.close_calls += 1
        return []


class _FailingPort:
    """关闭返回逐项错误的端口替身（模拟 RF 未确认关闭）。"""

    def __init__(self, *messages):
        self.errors = [RuntimeError(message) for message in messages or ("RF 未确认关闭",)]
        self.close_calls = 0

    def close_all(self, *, close_rf=False):
        self.close_calls += 1
        return list(self.errors)


class _FlakyPort:
    """第一次关闭失败、重试成功的端口替身。"""

    def __init__(self):
        self.attempts = 0

    def close_all(self, *, close_rf=False):
        self.attempts += 1
        return [RuntimeError("电源仍在输出")] if self.attempts == 1 else []


class _Page:
    """配置页端口替身：记录协调器传入的基线与回填内容。"""

    def __init__(self, *, valid=True, driver_mode=False, apply_error=None):
        self._valid = valid
        self._driver_mode = driver_mode
        self._apply_error = apply_error
        self.config = {}
        self.bases = []
        self.applied = []
        self.statuses = []

    def validate(self):
        return self._valid

    def build_config(self, base=None):
        self.bases.append(base)
        return dict(base or {})

    def apply_config(self, config):
        if self._apply_error is not None:
            raise self._apply_error
        self.applied.append(config)
        self.config = dict(config)

    def set_run_status(self, text):
        self.statuses.append(text)

    def driver_mode_requested(self):
        return self._driver_mode


def _coordinator(*, page=None, controller=None, service=None, **overrides):
    messages = overrides.pop("messages", [])
    warnings = overrides.pop("warnings", [])
    errors = overrides.pop("errors", [])
    coordinator = ApplicationCoordinator(
        log=messages.append,
        warn=lambda title, message: warnings.append((title, message)),
        error=errors.append,
        gui_data_service=service or MagicMock(),
        controller=controller or MagicMock(),
        config_path=Path("config.json"),
        **overrides,
    )
    if page is not None:
        coordinator.bind_config_page(page)
    coordinator.messages = messages
    coordinator.warnings = warnings
    coordinator.errors = errors
    return coordinator


class PortCleanupContractTests(unittest.TestCase):
    def test_close_returns_structured_errors_for_unknown_close_result(self):
        class _Port:
            def close_all(self, *, close_rf=False):
                return object()

        errors = close_instrument_port(_Port())
        self.assertEqual(len(errors), 1)
        self.assertIn("无法判定", str(errors[0]))

    def test_close_captures_raised_exceptions_as_errors(self):
        class _Port:
            def close_all(self, *, close_rf=False):
                raise RuntimeError("连接已损坏")

        errors = close_instrument_port(_Port())
        self.assertEqual(len(errors), 1)
        self.assertEqual(str(errors[0]), "连接已损坏")

    def test_describe_cleanup_errors_lists_every_failure(self):
        text = describe_cleanup_errors([RuntimeError("RF 失败"), ValueError("电源失败")])
        self.assertIn("RuntimeError: RF 失败", text)
        self.assertIn("ValueError: 电源失败", text)

    def test_release_success_clears_the_port(self):
        coordinator = _coordinator()
        port = _OkPort()
        coordinator.instrument_ctrl = port

        self.assertTrue(coordinator.release_instrument_port())

        self.assertIsNone(coordinator.instrument_ctrl)
        self.assertEqual(port.close_calls, 1)
        self.assertFalse(coordinator.has_unreleased_ports())

    def test_release_failure_keeps_the_reference_for_retry(self):
        coordinator = _coordinator()
        port = _FailingPort("RF 未确认关闭", "电源仍在输出")
        coordinator.instrument_ctrl = port

        self.assertFalse(coordinator.release_instrument_port())

        # 端口已不可用于测量，但引用必须留在未决清单里，绝不静默丢弃。
        self.assertIsNone(coordinator.instrument_ctrl)
        self.assertTrue(coordinator.has_unreleased_ports())
        self.assertEqual(coordinator.unreleased_port_count(), 1)
        self.assertTrue(
            any(
                "仪器清理失败" in message and "电源仍在输出" in message
                for message in coordinator.messages
            )
        )

    def test_repeated_failures_do_not_duplicate_the_retry_entry(self):
        coordinator = _coordinator()
        port = _FailingPort()
        coordinator.instrument_ctrl = port
        coordinator.release_instrument_port()

        coordinator.instrument_ctrl = port
        coordinator.release_instrument_port()

        self.assertEqual(coordinator.unreleased_port_count(), 1)

    def test_retry_clears_a_recovered_port(self):
        coordinator = _coordinator()
        port = _FlakyPort()
        coordinator.instrument_ctrl = port
        self.assertFalse(coordinator.release_instrument_port())
        self.assertTrue(coordinator.has_unreleased_ports())

        self.assertTrue(coordinator.retry_unreleased_ports())

        self.assertFalse(coordinator.has_unreleased_ports())
        self.assertEqual(port.attempts, 2)

    def test_retry_keeps_still_failing_ports(self):
        coordinator = _coordinator()
        coordinator.instrument_ctrl = _FailingPort()
        coordinator.release_instrument_port()

        self.assertFalse(coordinator.retry_unreleased_ports())

        self.assertTrue(coordinator.has_unreleased_ports())
        self.assertTrue(
            any("未决仪器清理仍失败" in message for message in coordinator.messages)
        )

    def test_stop_instrument_worker_clears_reference_only_after_stop(self):
        coordinator = _coordinator()
        worker = MagicMock()
        worker.wait.return_value = False
        coordinator.instrument_worker = worker

        self.assertFalse(coordinator.stop_instrument_worker(5000))
        self.assertIs(coordinator.instrument_worker, worker)

        worker.wait.return_value = True
        self.assertTrue(coordinator.stop_instrument_worker(5000))
        self.assertIsNone(coordinator.instrument_worker)


class PortHandoffTests(unittest.TestCase):
    def test_accept_controller_injects_port_and_reports_clean_handoff(self):
        controller = MagicMock()
        coordinator = _coordinator(controller=controller)
        port = _OkPort()

        self.assertTrue(coordinator.accept_instrument_controller(port))

        self.assertIs(coordinator.instrument_ctrl, port)
        controller.set_measurement_port.assert_called_once_with(port)

    def test_accept_controller_keeps_both_ports_when_old_cleanup_fails(self):
        coordinator = _coordinator()
        old_port = _FailingPort()
        coordinator.instrument_ctrl = old_port
        new_port = _OkPort()

        self.assertFalse(coordinator.accept_instrument_controller(new_port))

        self.assertIs(coordinator.instrument_ctrl, new_port)
        self.assertEqual(coordinator.unreleased_port_count(), 1)
        self.assertEqual(old_port.close_calls, 1)


class RunSwitchRequiresCleanReleaseTests(unittest.TestCase):
    def _page(self):
        page = _Page()
        page.config = {}
        return page

    def test_create_run_is_not_adopted_when_the_old_port_cannot_be_released(self):
        page = self._page()
        coordinator = _coordinator(page=page, confirm_run_wiring=lambda _config: True)
        previous_run = object()
        coordinator.prepared_run = previous_run
        coordinator.instrument_ctrl = _FailingPort()
        coordinator.update_configuration_from_ui = MagicMock()

        prepared = SimpleNamespace(run_id=RUN_ID, run_directory=Path("runs") / RUN_ID)
        with patch(
            "presentation.qt.application_coordinator.prepare_configuration",
            return_value=prepared,
        ):
            result = coordinator.create_prepared_run()

        self.assertIsNone(result)
        self.assertIs(coordinator.prepared_run, previous_run)
        self.assertTrue(coordinator.has_unreleased_ports())
        self.assertTrue(
            any("未能安全释放" in message for message in coordinator.messages)
        )
        self.assertTrue(
            any(title == "创建运行未生效" for title, _message in coordinator.warnings)
        )

    def test_load_run_is_not_adopted_when_the_old_port_cannot_be_released(self):
        page = self._page()
        coordinator = _coordinator(page=page)
        previous_run = object()
        coordinator.prepared_run = previous_run
        coordinator.instrument_ctrl = _FailingPort()

        prepared = SimpleNamespace(
            run_id=RUN_ID,
            run_directory=Path("runs") / RUN_ID,
            hardware_config={"test_frequencies": [2.4]},
        )
        with patch(
            "presentation.qt.application_coordinator.load_prepared_run",
            return_value=prepared,
        ):
            result = coordinator.load_prepared_run(RUN_ID)

        self.assertIsNone(result)
        self.assertIs(coordinator.prepared_run, previous_run)
        self.assertTrue(coordinator.has_unreleased_ports())
        self.assertTrue(
            any(title == "加载运行未生效" for title, _message in coordinator.warnings)
        )


class ConfigurationPagePortTests(unittest.TestCase):
    def test_bind_rejects_a_page_without_the_required_capabilities(self):
        coordinator = _coordinator()

        with self.assertRaises(TypeError) as raised:
            coordinator.bind_config_page(SimpleNamespace(validate=lambda: True))

        message = str(raised.exception)
        for name in ("build_config", "apply_config", "set_run_status", "driver_mode_requested"):
            self.assertIn(name, message)

    def test_real_config_page_satisfies_the_port(self):
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from presentation.qt.config_page import ConfigPage

        self.assertIsNotNone(QApplication.instance() or QApplication([]))
        page = ConfigPage({})
        self.assertIsInstance(page, ConfigurationPagePort)

    def test_building_configuration_requires_a_bound_page(self):
        coordinator = _coordinator()

        with self.assertRaisesRegex(RuntimeError, "配置页"):
            coordinator.build_configuration_from_ui()

    def test_building_configuration_uses_the_coordinator_config_as_base(self):
        page = _Page()
        coordinator = _coordinator(page=page)
        coordinator.config = {"test_frequencies": [2.4]}

        built = coordinator.build_configuration_from_ui()

        self.assertEqual(built, {"test_frequencies": [2.4]})
        self.assertIs(page.bases[-1], coordinator.config)

    def test_driver_mode_uses_the_page_port_not_widgets(self):
        page = _Page(driver_mode=True)
        coordinator = _coordinator(page=page)

        self.assertTrue(coordinator.amplifier_driver_mode())

    def test_frozen_run_wins_over_the_page_for_driver_mode(self):
        page = _Page(driver_mode=True)
        coordinator = _coordinator(page=page)
        coordinator.prepared_run = SimpleNamespace(
            configuration=SimpleNamespace(test_plan=SimpleNamespace(driver_enabled=False))
        )

        self.assertFalse(coordinator.amplifier_driver_mode())

    def test_bind_reports_the_initial_run_status_to_the_page(self):
        page = _Page()
        _coordinator(page=page)

        self.assertEqual(page.statuses, ["当前运行: 未创建"])


class FeedbackCallbackTests(unittest.TestCase):
    def test_feedback_callbacks_are_required(self):
        for missing in ("log", "warn", "error"):
            with self.subTest(missing=missing):
                kwargs = {
                    "log": lambda _message: None,
                    "warn": lambda _title, _message: None,
                    "error": lambda _message: None,
                    "gui_data_service": MagicMock(),
                    "controller": MagicMock(),
                }
                kwargs.pop(missing)
                with self.assertRaises(TypeError):
                    ApplicationCoordinator(**kwargs)

    def test_non_callable_feedback_is_rejected(self):
        with self.assertRaises(TypeError):
            ApplicationCoordinator(
                log=None,
                warn=lambda _title, _message: None,
                error=lambda _message: None,
                gui_data_service=MagicMock(),
                controller=MagicMock(),
            )


class ArchivalLocationTests(unittest.TestCase):
    """归档位置只服务“打开结果目录/报告”：必须属于当前运行且在运行目录内。"""

    def _coordinator(self, run_directory="D:/runs/current"):
        coordinator = _coordinator()
        coordinator.prepared_run = SimpleNamespace(
            run_id=RUN_ID,
            run_directory=Path(run_directory) if run_directory else None,
        )
        return coordinator

    def test_current_run_directory_is_returned(self):
        coordinator = self._coordinator()

        self.assertEqual(
            coordinator.archival_location(RUN_ID), str(Path("D:/runs/current"))
        )

    def test_artifact_path_is_resolved_inside_the_run_directory(self):
        coordinator = self._coordinator()

        location = coordinator.archival_location(
            RUN_ID, "artifacts/amplifier_report.html"
        )

        self.assertEqual(
            location,
            str(Path("D:/runs/current") / "artifacts" / "amplifier_report.html"),
        )

    def test_other_runs_are_refused(self):
        coordinator = self._coordinator()

        self.assertIsNone(coordinator.archival_location("20261027T101500Z_0ther0id"))
        self.assertTrue(
            any("当前运行不是它" in message for message in coordinator.messages)
        )

    def test_artifact_paths_outside_artifacts_are_refused(self):
        coordinator = self._coordinator()

        self.assertIsNone(
            coordinator.archival_location(RUN_ID, "results/amplifier_measurement.json")
        )
        self.assertTrue(
            any("路径不合法" in message for message in coordinator.messages)
        )

    def test_without_a_current_run_nothing_is_located(self):
        coordinator = _coordinator()

        self.assertIsNone(coordinator.archival_location(RUN_ID))
        self.assertTrue(
            any("当前运行不是它" in message for message in coordinator.messages)
        )

    def test_current_run_id_is_a_silent_query(self):
        coordinator = self._coordinator()

        self.assertEqual(coordinator.current_run_id(), RUN_ID)
        self.assertEqual(coordinator.errors, [])

        coordinator.prepared_run = None
        self.assertIsNone(coordinator.current_run_id())
        self.assertEqual(coordinator.errors, [])
        self.assertEqual(coordinator.messages, [])


if __name__ == "__main__":
    unittest.main()
