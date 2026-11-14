"""测量启动 Facade 测试（GUI 重构步骤 9，不需要 QApplication）。

三个测量页面共享的“检查 controller / 已有测量 / 运行契约 / 接线确认 / 构造
MeasurementCommand”流程集中在这里验证；页面自己的接线图、日志、归档与结果展示仍由
各自的页面测试覆盖。本模块不启动 Qt，也不接触 worker。
"""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

from presentation.qt.measurement_controller_contract import MeasurementCommand
from application.dto.measurement_requests import StageResultDigest
from presentation.qt.measurement_start_facade import (
    BUSY_MESSAGE,
    MeasurementStartFacade,
    StartMeasurementDecision,
    StartMeasurementRequest,
    StartMeasurementStatus,
    is_busy,
)
from presentation.qt.measurement_state import MeasurementKind
from presentation.qt.run_contract import is_valid_prepared_run

ROOT = Path(__file__).resolve().parents[2]
QT_ROOT = ROOT / "presentation" / "qt"
FACADE_SOURCE = QT_ROOT / "measurement_start_facade.py"
PAGE_NAMES = ("cable_loss_page.py", "driver_mapping_page.py", "amplifier_page.py")


def _prepared_run(run_id="run-1"):
    return SimpleNamespace(
        run_id=run_id,
        run_directory=f"runs/{run_id}",
        run_repository=object(),
        hardware_connection_path=lambda: None,
    )


_NO_RUN = object()


class _Controller:
    """记录启动方法与命令的最小 controller 替身。"""

    def __init__(self, *, is_active=False, accepted=True):
        self.state = SimpleNamespace(is_active=is_active)
        self.accepted = accepted
        self.calls = []

    def _record(self, kind, command):
        self.calls.append((kind, command))
        return self.accepted

    def start_cable_loss(self, command):
        return self._record(MeasurementKind.CABLE_LOSS, command)

    def start_driver_mapping(self, command):
        return self._record(MeasurementKind.DRIVER_MAPPING, command)

    def start_amplifier(self, command):
        return self._record(MeasurementKind.AMPLIFIER, command)


class MeasurementStartFacadeTests(unittest.TestCase):
    def setUp(self):
        self.confirmed = []
        self.controller = _Controller()
        self.facade = MeasurementStartFacade(config_path_provider=lambda: "config.json")

    def _prepare(self, kind=MeasurementKind.AMPLIFIER, run=_NO_RUN, *, confirmed=True):
        prepared = _prepared_run() if run is _NO_RUN else run
        return self.facade.prepare(
            lambda: StartMeasurementRequest(kind, prepared),
            controller=self.controller,
            confirm_connection=lambda: self.confirmed.append(True) or confirmed,
        )

    def test_ready_decision_builds_one_command_and_dispatches_by_kind(self):
        for kind, method in (
            (MeasurementKind.CABLE_LOSS, "start_cable_loss"),
            (MeasurementKind.DRIVER_MAPPING, "start_driver_mapping"),
            (MeasurementKind.AMPLIFIER, "start_amplifier"),
        ):
            with self.subTest(kind=kind):
                self.controller = _Controller()
                self.confirmed = []
                run = _prepared_run()
                decision = self._prepare(kind, run)
                self.assertTrue(decision.ready)
                self.assertIsInstance(decision.command, MeasurementCommand)
                self.assertEqual(decision.command.config_path, "config.json")
                self.assertIs(decision.command.prepared_run, run)
                self.assertEqual(self.confirmed, [True])
                self.assertIs(decision.controller, self.controller)

                self.assertTrue(decision.launch())
                self.assertEqual(len(self.controller.calls), 1)
                called_kind, command = self.controller.calls[0]
                self.assertIs(called_kind, kind)
                self.assertIs(command, decision.command)
                self.assertTrue(hasattr(self.controller, method))

    def test_ready_command_carries_the_selected_stage_inputs(self):
        cable = Path("test_results") / "cable_loss" / "20260101T000000Z_abcd.json"
        mapping = Path("test_results") / "driver_mapping" / "20260102T000000Z_ef01.json"
        cable_digest = StageResultDigest(sha256="a" * 64, size_bytes=100)
        mapping_digest = StageResultDigest(sha256="b" * 64, size_bytes=200)
        decision = self.facade.prepare(
            lambda: StartMeasurementRequest(
                MeasurementKind.AMPLIFIER,
                _prepared_run(),
                cable_loss_path=str(cable),
                driver_mapping_path=mapping,
                cable_loss_digest=cable_digest,
                driver_mapping_digest=mapping_digest,
            ),
            controller=self.controller,
            confirm_connection=lambda: True,
        )

        self.assertTrue(decision.ready)
        self.assertEqual(decision.command.cable_loss_path, cable)
        self.assertEqual(decision.command.driver_mapping_path, mapping)
        self.assertIsInstance(decision.command.cable_loss_path, Path)
        self.assertIsInstance(decision.command.driver_mapping_path, Path)
        self.assertEqual(decision.command.cable_loss_digest, cable_digest)
        self.assertEqual(decision.command.driver_mapping_digest, mapping_digest)

    def test_digest_without_its_path_is_rejected_before_the_command(self):
        digest = StageResultDigest(sha256="a" * 64, size_bytes=100)
        with self.assertRaisesRegex(ValueError, "只能在同时给出"):
            StartMeasurementRequest(
                MeasurementKind.DRIVER_MAPPING,
                _prepared_run(),
                cable_loss_digest=digest,
            )
        with self.assertRaisesRegex(ValueError, "只能在同时给出"):
            MeasurementCommand(
                "config.json", _prepared_run(), driver_mapping_digest=digest
            )

    def test_blank_selected_paths_are_rejected_before_the_command(self):
        with self.assertRaisesRegex(ValueError, "cable_loss_path"):
            StartMeasurementRequest(
                MeasurementKind.DRIVER_MAPPING,
                _prepared_run(),
                cable_loss_path="   ",
            )
        with self.assertRaisesRegex(ValueError, "driver_mapping_path"):
            MeasurementCommand(
                "config.json", _prepared_run(), driver_mapping_path=""
            )

    def test_no_controller_is_silent_and_never_confirms(self):
        decision = self.facade.prepare(
            lambda: StartMeasurementRequest(
                MeasurementKind.AMPLIFIER, _prepared_run()
            ),
            controller=None,
            confirm_connection=lambda: self.confirmed.append(True) or True,
        )
        self.assertEqual(decision.status, StartMeasurementStatus.NO_CONTROLLER)
        self.assertFalse(decision.ready)
        self.assertEqual(self.confirmed, [])
        self.assertIsNone(decision.command)
        self.assertIsNone(decision.controller)

        logs, errors = [], []
        decision.report(logs.append, errors.append)
        self.assertEqual((logs, errors), ([], []))

    def test_active_measurement_reports_busy_without_confirmation(self):
        self.controller = _Controller(is_active=True)
        decision = self._prepare()
        self.assertEqual(decision.status, StartMeasurementStatus.BUSY)
        self.assertEqual(decision.message, BUSY_MESSAGE)
        self.assertEqual(self.confirmed, [])
        self.assertIsNone(decision.command)

        logs, errors = [], []
        decision.report(logs.append, errors.append)
        self.assertEqual(logs, [BUSY_MESSAGE])
        self.assertEqual(errors, [])

    def test_busy_does_not_read_the_current_run(self):
        self.controller = _Controller(is_active=True)
        reads = []
        decision = self.facade.prepare(
            lambda: reads.append(True),
            controller=self.controller,
            confirm_connection=lambda: self.confirmed.append(True) or True,
        )
        self.assertEqual(decision.status, StartMeasurementStatus.BUSY)
        self.assertEqual(reads, [])
        self.assertEqual(self.confirmed, [])

    def test_is_busy_is_the_shared_active_measurement_predicate(self):
        # 页面在报告自己的输入前置条件之前复用它，确保“已有测量在运行”不被
        # 页面级提示掩盖；判据只有这一份实现。
        self.assertTrue(is_busy(_Controller(is_active=True)))
        self.assertFalse(is_busy(_Controller(is_active=False)))
        self.assertFalse(is_busy(None))
        self.assertFalse(is_busy(SimpleNamespace(state=None)))

    def test_invalid_run_reports_the_page_specific_message(self):
        expected = {
            MeasurementKind.CABLE_LOSS: "正式线损测量必须先连接仪器",
            MeasurementKind.DRIVER_MAPPING: "正式驱动映射测量必须先连接仪器",
            MeasurementKind.AMPLIFIER: "正式主功放测量必须先连接仪器",
        }
        legacy = SimpleNamespace(run_id="legacy", run_repository=object())
        for kind, message in expected.items():
            for run in (None, legacy):
                with self.subTest(kind=kind, run=run):
                    self.confirmed = []
                    decision = self._prepare(kind, run)
                    self.assertEqual(
                        decision.status, StartMeasurementStatus.INVALID_RUN
                    )
                    self.assertEqual(decision.message, message)
                    self.assertEqual(self.confirmed, [])
                    self.assertIsNone(decision.command)

                    logs, errors = [], []
                    decision.report(logs.append, errors.append)
                    self.assertEqual(logs, [])
                    self.assertEqual(errors, [message])

    def test_rejected_connection_confirmation_does_not_start(self):
        decision = self._prepare(confirmed=False)
        self.assertEqual(
            decision.status, StartMeasurementStatus.CONNECTION_CANCELLED
        )
        self.assertEqual(self.confirmed, [True])
        self.assertIsNone(decision.command)
        with self.assertRaises(ValueError):
            decision.launch()
        self.assertEqual(self.controller.calls, [])

    def test_run_contract_check_reuses_the_single_predicate(self):
        run = _prepared_run()
        self.assertTrue(is_valid_prepared_run(run))
        broken = SimpleNamespace(run_id="run-1")
        self.assertFalse(is_valid_prepared_run(broken))
        self.assertEqual(self._prepare(run=broken).status, StartMeasurementStatus.INVALID_RUN)

    def test_caller_contract_errors_raise_type_error(self):
        with self.assertRaises(TypeError):
            StartMeasurementRequest("amplifier", _prepared_run())
        with self.assertRaises(TypeError):
            self.facade.prepare(object(), controller=self.controller, confirm_connection=lambda: True)
        with self.assertRaises(TypeError):
            self.facade.prepare(
                lambda: StartMeasurementRequest(
                    MeasurementKind.AMPLIFIER, _prepared_run()
                ),
                controller=self.controller,
                confirm_connection=None,
            )
        with self.assertRaises(TypeError):
            self.facade.prepare(
                lambda: object(),
                controller=self.controller,
                confirm_connection=lambda: True,
            )
        with self.assertRaises(TypeError):
            MeasurementStartFacade(config_path_provider=None)

    def test_decision_accepts_one_payload_per_status(self):
        request = StartMeasurementRequest(MeasurementKind.AMPLIFIER, _prepared_run())
        command = MeasurementCommand("config.json", _prepared_run())
        controller = _Controller()

        ready = StartMeasurementDecision(
            status=StartMeasurementStatus.READY,
            request=request,
            command=command,
            controller=controller,
        )
        self.assertTrue(ready.ready)
        for decision in (
            StartMeasurementDecision(
                status=StartMeasurementStatus.BUSY,
                controller=controller,
                message=BUSY_MESSAGE,
            ),
            StartMeasurementDecision(
                status=StartMeasurementStatus.INVALID_RUN,
                request=request,
                controller=controller,
                message="缺少运行",
            ),
            StartMeasurementDecision(
                status=StartMeasurementStatus.CONNECTION_CANCELLED,
                request=request,
                controller=controller,
            ),
            StartMeasurementDecision(status=StartMeasurementStatus.NO_CONTROLLER),
        ):
            self.assertFalse(decision.ready)

    def test_decision_rejects_inconsistent_payload(self):
        request = StartMeasurementRequest(MeasurementKind.AMPLIFIER, _prepared_run())
        command = MeasurementCommand("config.json", _prepared_run())
        controller = _Controller()
        status = StartMeasurementStatus
        invalid = {
            "ready 缺命令": dict(
                status=status.READY, request=request, controller=controller
            ),
            "ready 命令类型不对": dict(
                status=status.READY,
                request=request,
                command=object(),
                controller=controller,
            ),
            "ready 缺 controller": dict(
                status=status.READY, request=request, command=command
            ),
            "busy 带启动意图": dict(
                status=status.BUSY, request=request, controller=controller
            ),
            "busy 带命令": dict(
                status=status.BUSY, command=command, controller=controller
            ),
            "busy 缺 controller": dict(status=status.BUSY),
            "invalid_run 缺启动意图": dict(
                status=status.INVALID_RUN, controller=controller
            ),
            "invalid_run 缺 controller": dict(
                status=status.INVALID_RUN, request=request
            ),
            "invalid_run 带命令": dict(
                status=status.INVALID_RUN,
                request=request,
                command=command,
                controller=controller,
            ),
            "cancelled 缺启动意图": dict(
                status=status.CONNECTION_CANCELLED, controller=controller
            ),
            "cancelled 缺 controller": dict(
                status=status.CONNECTION_CANCELLED, request=request
            ),
            "cancelled 带命令": dict(
                status=status.CONNECTION_CANCELLED,
                request=request,
                command=command,
                controller=controller,
            ),
            "busy 缺文案": dict(status=status.BUSY, controller=controller),
            "invalid_run 缺文案": dict(
                status=status.INVALID_RUN, request=request, controller=controller
            ),
            "ready 带文案": dict(
                status=status.READY,
                request=request,
                command=command,
                controller=controller,
                message="不应出现",
            ),
            "cancelled 带文案": dict(
                status=status.CONNECTION_CANCELLED,
                request=request,
                controller=controller,
                message="不应出现",
            ),
            "no_controller 带启动意图": dict(
                status=status.NO_CONTROLLER, request=request
            ),
            "no_controller 带 controller": dict(
                status=status.NO_CONTROLLER, controller=controller
            ),
            "no_controller 带命令": dict(
                status=status.NO_CONTROLLER, command=command
            ),
        }
        for case, payload in invalid.items():
            with self.subTest(case=case), self.assertRaises(ValueError):
                StartMeasurementDecision(**payload)

    def test_controller_can_reject_the_start(self):
        self.controller = _Controller(accepted=False)
        decision = self._prepare()
        self.assertTrue(decision.ready)
        self.assertFalse(decision.launch())
        self.assertEqual(len(self.controller.calls), 1)

    def test_decision_keeps_the_controller_it_checked(self):
        checked = self.controller
        decision = self._prepare()
        self.assertIs(decision.controller, checked)

        # 页面在接线确认后重新绑定 controller：决策仍提交给检查过的那个。
        self.controller = _Controller()
        self.assertTrue(decision.launch())

        self.assertEqual(len(checked.calls), 1)
        self.assertEqual(self.controller.calls, [])


class MeasurementPagesUseTheFacadeTests(unittest.TestCase):
    """三个页面不再各自构造命令或复制运行契约判据。"""

    def test_pages_delegate_startup_to_the_facade(self):
        start_methods = {
            "cable_loss_page.py": "start_measurement",
            "driver_mapping_page.py": "start_measurement",
            "amplifier_page.py": "start_measurement",
        }
        for name in PAGE_NAMES:
            with self.subTest(page=name):
                source = (QT_ROOT / name).read_text(encoding="utf-8")
                self.assertIn("MeasurementStartFacade", source)
                self.assertIn("StartMeasurementRequest", source)
                self.assertNotIn("MeasurementCommand", source)
                self.assertNotIn("is_valid_prepared_run", source)
                tree = ast.parse(source, name)
                start = next(
                    node
                    for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef)
                    and node.name == start_methods[name]
                )
                attributes = {
                    node.attr
                    for node in ast.walk(start)
                    if isinstance(node, ast.Attribute)
                }
                self.assertIn("prepare", attributes)
                self.assertIn("launch", attributes)

    def test_cable_loss_continues_path2_from_the_step_pause_checkpoint(self):
        source = (QT_ROOT / "cable_loss_page.py").read_text(encoding="utf-8")
        self.assertIn("def _on_step_pause", source)
        self.assertIn("continue_cable_loss_stage2", source)
        self.assertNotIn("stage2_measurement_btn", source)

    def test_facade_is_pure_python(self):
        tree = ast.parse(FACADE_SOURCE.read_text(encoding="utf-8"), str(FACADE_SOURCE))
        imported_roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported_roots.add(node.module.split(".")[0])
        self.assertNotIn("PySide6", imported_roots)
        source = FACADE_SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("QDialog", source)
        self.assertNotIn("QMessageBox", source)


if __name__ == "__main__":
    unittest.main()
