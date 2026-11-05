"""步骤 10 收尾：运行协调器的安全清理契约与配置页窄接口。

覆盖三件事：

1. 端口清理结果必须结构化：``close_all()`` 返回的错误列表不能被当成成功，
   失败端口进入未决清理清单（引用不丢）并可重试。
2. 运行切换只有在旧端口确实释放后才提交；否则保持当前运行不变并明确告警。
3. 协调器只通过 ``ConfigurationPagePort`` 与配置页交互，不读取控件；
   缺少用户可见反馈回调时在构造阶段失败，不允许失败路径静默。
"""

import atexit
import itertools
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from domain.run.models import RunStatus, default_manifest
from presentation.qt.application_coordinator import (
    ApplicationCoordinator,
    ConfigurationPagePort,
    RunFlowState,
    close_instrument_port,
    describe_cleanup_errors,
)
from presentation.qt.config_projection import ConfigBackfillError
from presentation.qt.measurement_state import MeasurementKind, MeasurementStatus

RUN_ID = "20261027T101500Z_c0ffee01"

# 替身需要的真实运行目录统一放在进程级临时根下，退出时显式清理，避免垃圾回收时
# 隐式删除临时目录而触发 ResourceWarning。
_RUN_REPOSITORY_ROOT = tempfile.TemporaryDirectory(prefix="coordinator-runs-")
atexit.register(_RUN_REPOSITORY_ROOT.cleanup)
_RUN_REPOSITORY_IDS = itertools.count(1)


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


class _SettlingPort:
    """带阶段结算入口的端口替身：成功时可复用，失败时仍能完整关闭。"""

    def __init__(self, *, settle_error=None, close_errors=()):
        self.end_stage_calls = 0
        self.close_calls = 0
        self._settle_error = settle_error
        self._close_errors = list(close_errors)

    def end_stage(self, *, timeout_s=30.0):
        self.end_stage_calls += 1
        if self._settle_error is not None:
            raise self._settle_error

    def close_all(self, *, close_rf=False):
        self.close_calls += 1
        return list(self._close_errors)


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


class _RunRepository:
    """运行仓储替身：提供真实存在的运行目录与 manifest，只记录底层删除。

    撤销用例只在运行目录真实存在时读取 manifest，因此替身在 ``layout()`` 里准备好
    目录并生成对应 ``status`` 的 manifest；“是否允许撤销”仍由
    ``application.run_service.discard_unstarted_run`` 判断，替身不复制这条规则。
    """

    def __init__(self, *, status=RunStatus.CREATED):
        self.root = Path(_RUN_REPOSITORY_ROOT.name) / f"runs-{next(_RUN_REPOSITORY_IDS)}"
        self.status = status
        self.discarded = []
        self._manifests = {}

    def layout(self, run_id):
        directory = self.root / run_id
        directory.mkdir(parents=True, exist_ok=True)
        self._manifests.setdefault(
            run_id, default_manifest(run_id, status=self.status)
        )
        return SimpleNamespace(run_id=run_id, run_directory=directory)

    def load_manifest(self, run_id):
        manifest = self._manifests.get(run_id)
        if manifest is None:
            raise FileNotFoundError(f"运行不存在: {run_id}")
        return manifest

    def discard_run(self, run_id):
        self.discarded.append(run_id)
        directory = self.root / run_id
        if directory.is_dir():
            shutil.rmtree(directory)
        return run_id


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
        worker.unreleased_port = None
        worker.wait.return_value = False
        coordinator.instrument_worker = worker

        self.assertFalse(coordinator.stop_instrument_worker(5000))
        self.assertIs(coordinator.instrument_worker, worker)

        worker.wait.return_value = True
        self.assertTrue(coordinator.stop_instrument_worker(5000))
        self.assertIsNone(coordinator.instrument_worker)

    def test_stop_instrument_worker_adopts_an_unconfirmed_port_before_forgetting_it(self):
        coordinator = _coordinator()
        worker = MagicMock()
        port = _FailingPort("RF 未确认关闭")
        worker.unreleased_port = port
        worker.wait.return_value = True
        coordinator.instrument_worker = worker

        self.assertTrue(coordinator.stop_instrument_worker(5000))

        # 线程引用可以清掉，但它未能确认关闭的端口必须先进入未决清理清单。
        self.assertIsNone(coordinator.instrument_worker)
        self.assertIsNone(worker.unreleased_port)
        self.assertTrue(coordinator.has_unreleased_ports())
        self.assertEqual(coordinator.unreleased_port_count(), 1)


class PortHandoffTests(unittest.TestCase):
    def test_accept_controller_injects_port_and_reports_clean_handoff(self):
        controller = MagicMock()
        coordinator = _coordinator(controller=controller)
        port = _OkPort()

        self.assertTrue(coordinator.accept_instrument_controller(port))

        self.assertIs(coordinator.instrument_ctrl, port)
        controller.set_measurement_port.assert_called_once_with(port)

    def test_accept_controller_refuses_to_overwrite_an_unreleased_old_port(self):
        coordinator = _coordinator()
        old_port = _FailingPort()
        coordinator.instrument_ctrl = old_port
        new_port = _OkPort()

        self.assertFalse(coordinator.accept_instrument_controller(new_port))

        # 交接只负责新端口：旧端口必须先由最终化释放步骤处理，不能在这里被静默覆盖；
        # 被拒绝的新端口已经打开，必须当场关闭，避免泄漏。
        self.assertIs(coordinator.instrument_ctrl, old_port)
        self.assertEqual(old_port.close_calls, 0)
        self.assertEqual(new_port.close_calls, 1)
        self.assertTrue(
            any("拒绝覆盖" in message for message in coordinator.messages)
        )


class RunSwitchRequiresCleanReleaseTests(unittest.TestCase):
    def _page(self):
        page = _Page()
        page.config = {}
        return page

    def test_pending_run_is_not_created_when_the_old_port_cannot_be_released(self):
        page = self._page()
        coordinator = _coordinator(page=page)
        previous_run = object()
        coordinator.prepared_run = previous_run
        coordinator.instrument_ctrl = _FailingPort()
        coordinator.update_configuration_from_ui = MagicMock()

        prepared = SimpleNamespace(run_id=RUN_ID, run_directory=Path("runs") / RUN_ID)
        with patch(
            "presentation.qt.application_coordinator.prepare_configuration",
            return_value=prepared,
        ) as create:
            result = coordinator.prepare_pending_run()

        self.assertIsNone(result)
        create.assert_not_called()
        self.assertIs(coordinator.prepared_run, previous_run)
        self.assertIsNone(coordinator._pending_run)
        self.assertIs(coordinator.state, RunFlowState.MANUAL_CLEANUP_REQUIRED)
        self.assertTrue(coordinator.has_unreleased_ports())
        self.assertTrue(
            any("未能安全释放" in message for message in coordinator.messages)
        )
        self.assertTrue(
            any(title == "最终化未生效" for title, _message in coordinator.warnings)
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


class _RecordingWorker:
    """只记录启动的连接 worker 替身（协调器测试不启动真实 QThread）。"""

    def __init__(self, run):
        self.run = run
        self.started = False

    def start(self):
        self.started = True

    def isRunning(self):
        return False


def _prepared_run(repository=None):
    repository = repository or _RunRepository()
    layout = repository.layout(RUN_ID)
    return SimpleNamespace(
        run_id=RUN_ID,
        run_directory=layout.run_directory,
        run_repository=repository,
        hardware_connection_path=lambda: Path("frozen-hardware.json"),
    )


class FinalizationTransactionTests(unittest.TestCase):
    """最终化事务：保存、释放、冻结、提交与失败补偿是同一套可验证步骤。"""

    def _finalization_coordinator(self, *, page=None, controller=None):
        coordinator = _coordinator(
            page=page or _Page(),
            controller=controller or MagicMock(),
            instrument_worker_factory=_RecordingWorker,
        )
        coordinator.update_configuration_from_ui = MagicMock()
        return coordinator

    def test_finalize_freezes_pending_run_without_committing_it(self):
        coordinator = self._finalization_coordinator()
        coordinator.save_configuration = MagicMock(return_value=True)
        prepared = _prepared_run()

        with patch(
            "presentation.qt.application_coordinator.prepare_configuration",
            return_value=prepared,
        ) as create:
            self.assertTrue(coordinator.finalize_configuration())

        self.assertIs(coordinator._pending_run, prepared)
        self.assertIsNone(coordinator.prepared_run)
        self.assertIs(coordinator.state, RunFlowState.FINALIZING)
        self.assertTrue(coordinator.instrument_worker.started)
        self.assertIs(coordinator.instrument_worker.run, prepared)
        coordinator.save_configuration.assert_called_once_with()
        self.assertEqual(create.call_args.kwargs["hardware_config"], coordinator.config)

    def test_validation_failure_changes_nothing(self):
        page = _Page(valid=False)
        coordinator = self._finalization_coordinator(page=page)
        coordinator.save_configuration = MagicMock(return_value=True)

        with patch(
            "presentation.qt.application_coordinator.prepare_configuration"
        ) as create:
            self.assertFalse(coordinator.finalize_configuration())

        coordinator.save_configuration.assert_not_called()
        create.assert_not_called()
        self.assertIsNone(coordinator._pending_run)
        self.assertIsNone(coordinator.instrument_worker)

    def test_save_failure_keeps_the_old_run_and_port(self):
        coordinator = self._finalization_coordinator()
        previous_run = _prepared_run()
        port = _OkPort()
        coordinator.prepared_run = previous_run
        coordinator.instrument_ctrl = port
        coordinator.save_configuration = MagicMock(return_value=False)
        coordinator.last_save_error = RuntimeError("磁盘已满")

        with patch(
            "presentation.qt.application_coordinator.prepare_configuration"
        ) as create:
            self.assertFalse(coordinator.finalize_configuration())

        create.assert_not_called()
        self.assertIs(coordinator.prepared_run, previous_run)
        self.assertIs(coordinator.instrument_ctrl, port)
        self.assertEqual(port.close_calls, 0)
        self.assertIsNone(coordinator._pending_run)

    def test_release_failure_enters_manual_cleanup(self):
        coordinator = self._finalization_coordinator()
        coordinator.prepared_run = _prepared_run()
        coordinator.instrument_ctrl = _FailingPort("RF 未确认关闭")
        coordinator.save_configuration = MagicMock(return_value=True)

        with patch(
            "presentation.qt.application_coordinator.prepare_configuration"
        ) as create:
            self.assertFalse(coordinator.finalize_configuration())

        create.assert_not_called()
        self.assertIs(coordinator.state, RunFlowState.MANUAL_CLEANUP_REQUIRED)
        self.assertTrue(coordinator.has_unreleased_ports())
        self.assertIsNone(coordinator._pending_run)

    def test_freeze_failure_after_release_requires_reconnect(self):
        coordinator = self._finalization_coordinator()
        previous_run = _prepared_run()
        port = _OkPort()
        coordinator.prepared_run = previous_run
        coordinator.instrument_ctrl = port
        coordinator.save_configuration = MagicMock(return_value=True)

        with patch(
            "presentation.qt.application_coordinator.prepare_configuration",
            side_effect=RuntimeError("运行目录写入失败"),
        ):
            self.assertFalse(coordinator.finalize_configuration())

        self.assertEqual(port.close_calls, 1)
        self.assertIs(coordinator.prepared_run, previous_run)
        self.assertIsNone(coordinator._pending_run)
        self.assertIs(coordinator.state, RunFlowState.RECONNECT_REQUIRED)

    def test_unreleased_ports_block_a_new_finalization(self):
        coordinator = self._finalization_coordinator()
        coordinator.instrument_ctrl = _FailingPort()
        coordinator.release_instrument_port()
        self.assertTrue(coordinator.has_unreleased_ports())

        with patch(
            "presentation.qt.application_coordinator.prepare_configuration"
        ) as create:
            self.assertFalse(coordinator.finalize_configuration())

        create.assert_not_called()
        self.assertIs(coordinator.state, RunFlowState.MANUAL_CLEANUP_REQUIRED)

    def test_commit_adopts_pending_run_and_port_atomically(self):
        controller = MagicMock()
        coordinator = self._finalization_coordinator(controller=controller)
        coordinator.save_configuration = MagicMock(return_value=True)
        prepared = _prepared_run()
        with patch(
            "presentation.qt.application_coordinator.prepare_configuration",
            return_value=prepared,
        ):
            coordinator.prepare_pending_run()
        port = _OkPort()

        self.assertTrue(coordinator.commit_finalization(port))

        self.assertIs(coordinator.prepared_run, prepared)
        self.assertIsNone(coordinator._pending_run)
        self.assertIs(coordinator.instrument_ctrl, port)
        self.assertIs(coordinator.state, RunFlowState.CONNECTED)
        controller.set_measurement_port.assert_called_once_with(port)

    def test_failed_connection_discards_the_unstarted_pending_run(self):
        coordinator = self._finalization_coordinator()
        repository = _RunRepository()
        coordinator.save_configuration = MagicMock(return_value=True)
        with patch(
            "presentation.qt.application_coordinator.prepare_configuration",
            return_value=_prepared_run(repository),
        ):
            coordinator.prepare_pending_run()

        coordinator.fail_finalization("电源型号不符")

        # 连接失败不能留下可被误认为有效运行的目录。
        self.assertEqual(repository.discarded, [RUN_ID])
        self.assertIsNone(coordinator._pending_run)
        self.assertIsNone(coordinator.prepared_run)
        self.assertIs(coordinator.state, RunFlowState.CONFIGURING)

    def test_pending_run_discard_delegates_to_the_unstarted_run_use_case(self):
        """补偿只走 ``discard_unstarted_run``；“未开始”判断不在表现层重写。"""
        coordinator = self._finalization_coordinator()
        repository = MagicMock()
        pending = SimpleNamespace(run_id=RUN_ID, run_repository=repository)

        with patch(
            "presentation.qt.application_coordinator.discard_unstarted_run"
        ) as discard:
            coordinator._discard_pending_run(pending)

        discard.assert_called_once_with(repository, RUN_ID)

    def test_pending_run_without_a_run_contract_is_never_deleted_blindly(self):
        coordinator = self._finalization_coordinator()
        coordinator._set_state(RunFlowState.FINALIZING)
        coordinator._pending_run = SimpleNamespace(
            run_id=RUN_ID, run_directory=Path("runs") / RUN_ID
        )

        self.assertFalse(coordinator.fail_finalization("仪器连接失败"))

        # 无法证明运行未开始就不能删除目录：保留引用并阻断后续事务。
        self.assertIs(coordinator.state, RunFlowState.MANUAL_CLEANUP_REQUIRED)
        self.assertIsNotNone(coordinator._pending_run)
        self.assertTrue(coordinator.has_pending_run())
        with patch(
            "presentation.qt.application_coordinator.prepare_configuration"
        ) as create:
            self.assertFalse(coordinator.finalize_configuration())
        create.assert_not_called()

    def test_retry_abandon_discards_the_pending_run_after_manual_cleanup(self):
        coordinator = self._finalization_coordinator()
        repository = _RunRepository()
        coordinator._set_state(RunFlowState.MANUAL_CLEANUP_REQUIRED)
        coordinator._pending_run = _prepared_run(repository)

        self.assertTrue(coordinator.abandon_pending_run())

        self.assertEqual(repository.discarded, [RUN_ID])
        self.assertIsNone(coordinator._pending_run)
        self.assertFalse(coordinator.has_pending_run())

    def test_abandon_failure_keeps_the_pending_run_and_manual_state(self):
        coordinator = self._finalization_coordinator()
        coordinator._set_state(RunFlowState.MANUAL_CLEANUP_REQUIRED)
        coordinator._pending_run = _prepared_run()
        coordinator._discard_pending_run = MagicMock(
            side_effect=RuntimeError("运行目录无法撤销")
        )

        self.assertFalse(coordinator.abandon_pending_run())

        self.assertIsNotNone(coordinator._pending_run)
        self.assertIs(coordinator.state, RunFlowState.MANUAL_CLEANUP_REQUIRED)

    def test_abandon_never_deletes_a_run_that_already_started(self):
        """阶段出口：已进入测量的运行永远不会被放弃流程删除。"""
        coordinator = self._finalization_coordinator()
        repository = _RunRepository(status=RunStatus.RUNNING)
        coordinator._set_state(RunFlowState.MANUAL_CLEANUP_REQUIRED)
        coordinator._pending_run = _prepared_run(repository)

        self.assertFalse(coordinator.abandon_pending_run())

        self.assertEqual(repository.discarded, [])
        self.assertTrue(Path(coordinator._pending_run.run_directory).is_dir())
        self.assertTrue(coordinator.has_pending_run())
        self.assertIs(coordinator.state, RunFlowState.MANUAL_CLEANUP_REQUIRED)

    def test_abandon_refuses_while_the_connection_worker_is_running(self):
        """撤销运行目录只能在连接线程停止之后发生。"""
        coordinator = self._finalization_coordinator()
        repository = _RunRepository()
        coordinator._set_state(RunFlowState.MANUAL_CLEANUP_REQUIRED)
        coordinator._pending_run = _prepared_run(repository)
        worker = MagicMock()
        worker.isRunning.return_value = True
        coordinator.instrument_worker = worker

        self.assertFalse(coordinator.abandon_pending_run())

        self.assertEqual(repository.discarded, [])
        self.assertTrue(coordinator.has_pending_run())

    def test_commit_failure_compensates_pending_run_and_requires_reconnect(self):
        controller = MagicMock()
        controller.set_measurement_port.side_effect = RuntimeError("测量已启动")
        coordinator = self._finalization_coordinator(controller=controller)
        previous_run = _prepared_run()
        coordinator.prepared_run = previous_run
        coordinator.save_configuration = MagicMock(return_value=True)
        with patch(
            "presentation.qt.application_coordinator.prepare_configuration",
            return_value=_prepared_run(),
        ):
            coordinator.prepare_pending_run()
        port = _OkPort()

        self.assertFalse(coordinator.commit_finalization(port))

        self.assertIs(coordinator.prepared_run, previous_run)
        self.assertIsNone(coordinator._pending_run)
        self.assertIsNone(coordinator.instrument_ctrl)
        self.assertIs(coordinator.state, RunFlowState.RECONNECT_REQUIRED)
        self.assertEqual(port.close_calls, 1)

    def test_compensation_failure_blocks_further_transactions(self):
        coordinator = self._finalization_coordinator()
        coordinator.prepared_run = _prepared_run()
        coordinator.save_configuration = MagicMock(return_value=True)
        with patch(
            "presentation.qt.application_coordinator.prepare_configuration",
            return_value=_prepared_run(),
        ):
            coordinator.prepare_pending_run()
        pending = coordinator._pending_run
        coordinator._discard_pending_run = MagicMock(
            side_effect=RuntimeError("运行目录无法撤销")
        )

        coordinator.fail_finalization("仪器连接失败")

        self.assertIs(coordinator.state, RunFlowState.MANUAL_CLEANUP_REQUIRED)
        self.assertIs(coordinator._pending_run, pending)
        with patch(
            "presentation.qt.application_coordinator.prepare_configuration"
        ) as create:
            self.assertFalse(coordinator.finalize_configuration())
        create.assert_not_called()

    def test_worker_error_never_commits_the_pending_run(self):
        coordinator = self._finalization_coordinator()
        previous_run = _prepared_run()
        coordinator.prepared_run = previous_run
        coordinator.save_configuration = MagicMock(return_value=True)
        with patch(
            "presentation.qt.application_coordinator.prepare_configuration",
            return_value=_prepared_run(),
        ):
            coordinator.prepare_pending_run()

        coordinator.fail_finalization("电源型号不符")

        self.assertIs(coordinator.prepared_run, previous_run)
        self.assertIsNone(coordinator._pending_run)
        self.assertIsNone(coordinator.instrument_ctrl)
        self.assertIs(coordinator.state, RunFlowState.RECONNECT_REQUIRED)

    def test_pending_run_is_invisible_to_measurement_pages(self):
        coordinator = self._finalization_coordinator()
        coordinator.config_page = _Page()
        coordinator._pending_run = _prepared_run()
        coordinator.instrument_ctrl = _OkPort()

        self.assertIsNone(coordinator.resolve_measurement_run())
        self.assertTrue(
            any("尚未创建运行" in message for message in coordinator.errors)
        )

    def test_reconnect_reuses_the_committed_run_without_saving_or_freezing(self):
        coordinator = self._finalization_coordinator()
        previous_run = _prepared_run()
        coordinator.prepared_run = previous_run
        coordinator.save_configuration = MagicMock(return_value=True)
        coordinator._set_state(RunFlowState.RECONNECT_REQUIRED)

        with patch(
            "presentation.qt.application_coordinator.prepare_configuration"
        ) as create:
            self.assertTrue(coordinator.finalize_configuration())

        create.assert_not_called()
        coordinator.save_configuration.assert_not_called()
        self.assertIs(coordinator.instrument_worker.run, previous_run)
        self.assertIs(coordinator.state, RunFlowState.FINALIZING)

    def test_measurement_in_progress_blocks_finalization_before_any_action(self):
        controller = MagicMock()
        controller.state = SimpleNamespace(is_active=True)
        coordinator = self._finalization_coordinator(controller=controller)
        port = _OkPort()
        coordinator.instrument_ctrl = port
        coordinator.save_configuration = MagicMock(return_value=True)

        with patch(
            "presentation.qt.application_coordinator.prepare_configuration"
        ) as create:
            self.assertFalse(coordinator.finalize_configuration())

        # 测量正在使用端口：不能保存、不能释放、不能冻结，也不能启动 worker。
        create.assert_not_called()
        coordinator.save_configuration.assert_not_called()
        self.assertEqual(port.close_calls, 0)
        self.assertIs(coordinator.instrument_ctrl, port)
        self.assertIsNone(coordinator._pending_run)
        self.assertIsNone(coordinator.instrument_worker)
        self.assertTrue(
            any("测量正在运行" in message for message in coordinator.messages)
        )

    def test_measurement_in_progress_blocks_reconnect(self):
        controller = MagicMock()
        controller.state = SimpleNamespace(is_active=True)
        coordinator = self._finalization_coordinator(controller=controller)
        previous_run = _prepared_run()
        coordinator.prepared_run = previous_run
        coordinator._set_state(RunFlowState.RECONNECT_REQUIRED)

        self.assertFalse(coordinator.finalize_configuration())

        self.assertIs(coordinator.prepared_run, previous_run)
        self.assertIsNone(coordinator.instrument_worker)
        self.assertIs(coordinator.state, RunFlowState.RECONNECT_REQUIRED)

    def test_worker_cleanup_failure_blocks_the_next_transaction(self):
        coordinator = self._finalization_coordinator()
        coordinator.prepared_run = _prepared_run()
        coordinator.save_configuration = MagicMock(return_value=True)
        with patch(
            "presentation.qt.application_coordinator.prepare_configuration",
            return_value=_prepared_run(),
        ):
            coordinator.prepare_pending_run()
        port = _FailingPort("RF 未确认关闭")
        # 连接 worker 在停止/异常路径自行关闭端口失败时保留的引用。
        coordinator.instrument_worker = SimpleNamespace(unreleased_port=port)

        coordinator.fail_finalization("仪器连接失败")

        self.assertTrue(coordinator.has_unreleased_ports())
        self.assertEqual(coordinator.unreleased_port_count(), 1)
        self.assertIsNone(coordinator.instrument_worker.unreleased_port)
        self.assertIs(coordinator.state, RunFlowState.MANUAL_CLEANUP_REQUIRED)
        with patch(
            "presentation.qt.application_coordinator.prepare_configuration"
        ) as create:
            self.assertFalse(coordinator.finalize_configuration())
        create.assert_not_called()


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

        from presentation.qt.configuration_page import ConfigurationPage

        self.assertIsNotNone(QApplication.instance() or QApplication([]))
        page = ConfigurationPage({})
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

    def test_building_configuration_merges_the_power_fragment_after_the_parameter_page(self):
        page = _Page()
        power = _Page()

        def build_power_fragment(base=None):
            power.bases.append(base)
            return {**dict(base or {}), "power_fragment": True}

        power.build_config = build_power_fragment
        coordinator = _coordinator(page=page)
        coordinator.bind_power_page(power)
        coordinator.config = {"test_frequencies": [2.4]}

        built = coordinator.build_configuration_from_ui()

        self.assertEqual(
            built, {"test_frequencies": [2.4], "power_fragment": True}
        )
        self.assertIs(page.bases[-1], coordinator.config)
        self.assertEqual(power.bases[-1], {"test_frequencies": [2.4]})

    def test_validate_configuration_requires_both_pages(self):
        page = _Page(valid=True)
        power = _Page(valid=False)
        coordinator = _coordinator(page=page)
        coordinator.bind_power_page(power)
        coordinator.update_configuration_from_ui = MagicMock()

        self.assertFalse(coordinator.validate_configuration())
        coordinator.update_configuration_from_ui.assert_called_once_with()

    def test_run_configuration_backfill_rolls_back_both_pages_on_failure(self):
        page = _Page()
        power = _Page(apply_error=ConfigBackfillError("电源页回填失败"))
        coordinator = _coordinator(page=page)
        coordinator.bind_power_page(power)
        prepared = SimpleNamespace(hardware_config={"test_frequencies": [1.0]})

        self.assertFalse(coordinator.apply_run_configuration(prepared))

        # 参数页先回填了新配置，随后电源页失败：参数页必须回到调用前的读数。
        self.assertEqual(
            page.applied, [{"test_frequencies": [1.0]}, {}]
        )
        self.assertEqual(coordinator.config, {})

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


class ConfigurationLoadTests(unittest.TestCase):
    """加载已保存配置：单一读取入口；失败时告警并清空，不留旧配置。"""

    def test_load_configuration_reads_through_the_gui_data_service(self):
        service = MagicMock()
        document = {"test_frequencies": [2.4]}
        service.configuration.load.return_value = document
        coordinator = _coordinator(service=service)

        loaded = coordinator.load_configuration()

        self.assertEqual(loaded, {"test_frequencies": [2.4]})
        self.assertIs(loaded, coordinator.config)
        # 协调器持有自己的顶层副本：后续组装/保存不得改写服务返回的对象。
        self.assertIsNot(loaded, document)
        self.assertEqual(coordinator.warnings, [])
        service.configuration.load.assert_called_once_with()

    def test_load_failure_warns_and_clears_the_previous_configuration(self):
        service = MagicMock()
        service.configuration.load.side_effect = OSError("文件损坏")
        coordinator = _coordinator(service=service)
        coordinator.config = {"stale": True}

        loaded = coordinator.load_configuration()

        self.assertEqual(loaded, {})
        self.assertEqual(coordinator.config, {})
        self.assertEqual(
            coordinator.warnings, [("配置加载", "无法加载配置文件: 文件损坏")]
        )


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


class StageReuseTests(unittest.TestCase):
    """阶段四：成功阶段保留端口复用，异常/结算失败必须关闭并标记需重连。"""

    def _coordinator_for(self, status, port):
        controller = MagicMock()
        controller.state.kind = MeasurementKind.AMPLIFIER
        controller.state.status = status
        coordinator = _coordinator(controller=controller)
        coordinator.instrument_ctrl = port
        coordinator.prepared_run = SimpleNamespace(run_id=RUN_ID)
        coordinator._set_state(RunFlowState.CONNECTED)
        return coordinator

    def test_completed_stage_keeps_the_port_for_reuse(self):
        port = _SettlingPort()
        coordinator = self._coordinator_for(MeasurementStatus.COMPLETED, port)

        self.assertTrue(coordinator.release_port_after_measurement())

        self.assertIs(coordinator.instrument_ctrl, port)
        self.assertEqual(port.end_stage_calls, 1)
        self.assertEqual(port.close_calls, 0)
        self.assertIs(coordinator.state, RunFlowState.CONNECTED)
        self.assertFalse(coordinator.has_unreleased_ports())

    def test_completed_stage_without_a_settle_entry_is_closed(self):
        port = _OkPort()
        coordinator = self._coordinator_for(MeasurementStatus.COMPLETED, port)

        self.assertTrue(coordinator.release_port_after_measurement())

        self.assertIsNone(coordinator.instrument_ctrl)
        self.assertEqual(port.close_calls, 1)
        self.assertIs(coordinator.state, RunFlowState.RECONNECT_REQUIRED)
        self.assertTrue(
            any("没有阶段结算入口" in message for message in coordinator.messages)
        )

    def test_settle_failure_closes_the_port_and_requires_reconnect(self):
        port = _SettlingPort(settle_error=RuntimeError("RF 未确认关闭"))
        coordinator = self._coordinator_for(MeasurementStatus.COMPLETED, port)

        self.assertTrue(coordinator.release_port_after_measurement())

        self.assertEqual(port.end_stage_calls, 1)
        self.assertEqual(port.close_calls, 1)
        self.assertIsNone(coordinator.instrument_ctrl)
        self.assertIs(coordinator.state, RunFlowState.RECONNECT_REQUIRED)
        self.assertTrue(
            any("阶段结束安全清理失败" in message for message in coordinator.messages)
        )

    def test_settle_and_close_failure_requires_manual_cleanup(self):
        port = _SettlingPort(
            settle_error=RuntimeError("RF 未确认关闭"),
            close_errors=[RuntimeError("电源仍在输出")],
        )
        coordinator = self._coordinator_for(MeasurementStatus.COMPLETED, port)

        self.assertFalse(coordinator.release_port_after_measurement())

        self.assertIsNone(coordinator.instrument_ctrl)
        self.assertTrue(coordinator.has_unreleased_ports())
        self.assertIs(coordinator.state, RunFlowState.MANUAL_CLEANUP_REQUIRED)

    def test_cancelled_failed_or_emergency_stopped_stage_closes_the_port(self):
        for status in (
            MeasurementStatus.FAILED,
            MeasurementStatus.CANCELLED,
            MeasurementStatus.EMERGENCY_STOPPED,
        ):
            with self.subTest(status=status):
                port = _SettlingPort()
                coordinator = self._coordinator_for(status, port)

                self.assertTrue(coordinator.release_port_after_measurement())

                self.assertEqual(port.end_stage_calls, 0)
                self.assertEqual(port.close_calls, 1)
                self.assertIsNone(coordinator.instrument_ctrl)
                self.assertIs(coordinator.state, RunFlowState.RECONNECT_REQUIRED)

    def test_closed_stage_detaches_the_port_from_the_controller(self):
        """关闭端口后 controller 不能再把已关闭的会话交给下一阶段。"""
        port = _SettlingPort()
        coordinator = self._coordinator_for(MeasurementStatus.FAILED, port)
        coordinator.controller.set_measurement_port.reset_mock()

        self.assertTrue(coordinator.release_port_after_measurement())

        coordinator.controller.set_measurement_port.assert_called_once_with(None)

    def test_reused_stage_keeps_the_controller_port_reference(self):
        port = _SettlingPort()
        coordinator = self._coordinator_for(MeasurementStatus.COMPLETED, port)
        coordinator.controller.set_measurement_port.reset_mock()

        self.assertTrue(coordinator.release_port_after_measurement())

        coordinator.controller.set_measurement_port.assert_not_called()

    def test_an_active_measurement_does_not_block_port_release(self):
        """运行中的测量无法替换引用；释放端口本身仍然必须完成。"""
        port = _SettlingPort()
        coordinator = self._coordinator_for(MeasurementStatus.FAILED, port)
        coordinator.controller.set_measurement_port.side_effect = RuntimeError(
            "测量运行期间不能替换测量端口"
        )

        self.assertTrue(coordinator.release_port_after_measurement())

        self.assertIsNone(coordinator.instrument_ctrl)
        self.assertEqual(port.close_calls, 1)

    def test_non_measurement_state_leaves_the_port_alone(self):
        controller = MagicMock()
        controller.state.kind = None
        coordinator = _coordinator(controller=controller)
        port = _SettlingPort()
        coordinator.instrument_ctrl = port

        self.assertTrue(coordinator.release_port_after_measurement())

        self.assertIs(coordinator.instrument_ctrl, port)
        self.assertEqual(port.end_stage_calls, 0)
        self.assertEqual(port.close_calls, 0)


class StageStartFailureTests(unittest.TestCase):
    """阶段在 worker 创建前失败：没有线程结束事件，端口仍必须由协调器关闭。"""

    def test_begin_stage_failure_closes_the_port_and_requires_reconnect(self):
        from presentation.qt.measurement_controller import MeasurementController
        from presentation.qt.measurement_controller_contract import MeasurementCommand
        from presentation.qt.measurement_state import MeasurementKind

        from tests.contract_doubles import contract_run

        class _NotReusablePort:
            def __init__(self):
                self.close_calls = 0

            def begin_stage(self):
                raise RuntimeError("RF 输出尚未关闭")

            def close_all(self, *, close_rf=False):
                self.close_calls += 1
                return []

        port = _NotReusablePort()
        controller = MeasurementController(
            {MeasurementKind.AMPLIFIER: MagicMock()},
            lambda: "config.json",
            measurement_port=port,
        )
        coordinator = _coordinator(controller=controller)
        coordinator.instrument_ctrl = port
        coordinator.prepared_run = SimpleNamespace(run_id=RUN_ID)
        coordinator._set_state(RunFlowState.CONNECTED)

        self.assertFalse(
            controller.start_amplifier(
                MeasurementCommand("config.json", contract_run())
            )
        )

        self.assertIsNone(coordinator.instrument_ctrl)
        self.assertEqual(port.close_calls, 1)
        self.assertIs(coordinator.state, RunFlowState.RECONNECT_REQUIRED)
        self.assertTrue(any("阶段启动失败" in message for message in coordinator.messages))


if __name__ == "__main__":
    unittest.main()
