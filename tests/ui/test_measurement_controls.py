"""测量页面公共按钮状态绑定测试（GUI 重构步骤 10，不需要 QApplication）。

三个测量页面共享的“测量状态 -> 开始/停止/取消/紧急停止/继续按钮可用性”映射在这里
验证；页面自己的接线图、日志、归档与结果展示仍由各自的页面测试覆盖。本模块不启动
Qt，也不连接 controller signal。
"""

import ast
import unittest
from pathlib import Path

from presentation.qt.measurement_controller_contract import ControllerState
from presentation.qt.measurement_controls import (
    MeasurementAction,
    MeasurementControlAvailability,
    MeasurementControlPresenter,
)
from presentation.qt.measurement_state import (
    MeasurementKind,
    MeasurementStatus,
    MeasurementViewState,
)

ROOT = Path(__file__).resolve().parents[2]
QT_ROOT = ROOT / "presentation" / "qt"
CONTROLS_SOURCE = QT_ROOT / "measurement_controls.py"
PAGE_NAMES = ("cable_loss_page.py", "driver_mapping_page.py", "amplifier_page.py")


class _Sink:
    """记录最后一次可用性的按钮替身。"""

    def __init__(self):
        self.enabled = None
        self.writes = []

    def setEnabled(self, enabled):
        self.enabled = bool(enabled)
        self.writes.append(self.enabled)


def _controller_state(kind, status=MeasurementStatus.RUNNING):
    view = MeasurementViewState(kind).prepare()
    if status is MeasurementStatus.RUNNING:
        view = view.run()
    elif status is MeasurementStatus.WAITING_FOR_CONTINUE:
        view = view.run().wait_for_continue("请重新接线")
    elif status is MeasurementStatus.CANCELLED:
        view = view.run().stop("用户停止")
    return ControllerState(kind, view.status, view)


def _presenter(kind, actions):
    return MeasurementControlPresenter(kind, actions)


class MeasurementControlAvailabilityTests(unittest.TestCase):
    def test_idle_enables_only_start_among_declared_actions(self):
        availability = MeasurementControlAvailability(
            frozenset({MeasurementAction.START})
        )
        self.assertTrue(availability.is_enabled(MeasurementAction.START))
        self.assertFalse(availability.is_enabled(MeasurementAction.STOP))
        self.assertFalse(availability.is_enabled(MeasurementAction.CANCEL))

    def test_availability_normalizes_and_rejects_bad_actions(self):
        availability = MeasurementControlAvailability([MeasurementAction.START])
        self.assertIsInstance(availability.enabled, frozenset)
        with self.assertRaises(TypeError):
            availability.is_enabled("start")
        with self.assertRaises(TypeError):
            MeasurementControlAvailability({"start"})


class MeasurementControlPresenterTests(unittest.TestCase):
    def setUp(self):
        self.start = _Sink()
        self.stop = _Sink()
        self.cancel = _Sink()
        self.emergency = _Sink()
        self.continue_ = _Sink()
        self.presenter = _presenter(
            MeasurementKind.CABLE_LOSS,
            {
                MeasurementAction.START: self.start,
                MeasurementAction.STOP: self.stop,
                MeasurementAction.CANCEL: self.cancel,
                MeasurementAction.EMERGENCY_STOP: self.emergency,
                MeasurementAction.CONTINUE: self.continue_,
            },
        )

    def test_show_idle_enables_start_and_disables_other_actions(self):
        self.presenter.show_idle()

        self.assertTrue(self.start.enabled)
        self.assertFalse(self.stop.enabled)
        self.assertFalse(self.cancel.enabled)
        self.assertFalse(self.emergency.enabled)
        self.assertFalse(self.continue_.enabled)

    def test_active_measurement_of_this_page_disables_start(self):
        availability = self.presenter.apply(
            _controller_state(MeasurementKind.CABLE_LOSS)
        )

        self.assertFalse(self.start.enabled)
        self.assertTrue(self.stop.enabled)
        self.assertTrue(self.cancel.enabled)
        self.assertTrue(self.emergency.enabled)
        self.assertFalse(self.continue_.enabled)
        self.assertEqual(
            availability.enabled,
            frozenset(
                {
                    MeasurementAction.STOP,
                    MeasurementAction.CANCEL,
                    MeasurementAction.EMERGENCY_STOP,
                }
            ),
        )

    def test_continue_is_only_enabled_while_waiting_for_the_second_step(self):
        waiting = _controller_state(
            MeasurementKind.CABLE_LOSS, MeasurementStatus.WAITING_FOR_CONTINUE
        )

        self.presenter.apply(waiting)

        self.assertTrue(self.continue_.enabled)
        self.assertTrue(self.stop.enabled)
        self.assertFalse(self.start.enabled)

    def test_measurement_of_another_page_keeps_this_page_idle(self):
        self.presenter.apply(_controller_state(MeasurementKind.AMPLIFIER))

        self.assertTrue(self.start.enabled)
        self.assertFalse(self.stop.enabled)
        self.assertFalse(self.cancel.enabled)
        self.assertFalse(self.emergency.enabled)
        self.assertFalse(self.continue_.enabled)

    def test_terminal_state_restores_the_idle_buttons(self):
        self.presenter.apply(_controller_state(MeasurementKind.CABLE_LOSS))
        self.presenter.apply(
            _controller_state(MeasurementKind.CABLE_LOSS, MeasurementStatus.CANCELLED)
        )

        self.assertTrue(self.start.enabled)
        self.assertFalse(self.stop.enabled)
        self.assertFalse(self.cancel.enabled)
        self.assertFalse(self.continue_.enabled)

    def test_view_state_without_is_active_is_accepted(self):
        running = MeasurementViewState(MeasurementKind.CABLE_LOSS).prepare().run()

        availability = self.presenter.availability(running)

        self.assertNotIn(MeasurementAction.START, availability.enabled)
        self.assertIn(MeasurementAction.STOP, availability.enabled)

    def test_explicit_activity_flag_is_honoured_over_the_status(self):
        state = type(
            "State",
            (),
            {
                "kind": MeasurementKind.CABLE_LOSS,
                "status": MeasurementStatus.RUNNING,
                "is_active": False,
            },
        )()

        availability = self.presenter.availability(state)

        self.assertIn(MeasurementAction.START, availability.enabled)
        self.assertNotIn(MeasurementAction.STOP, availability.enabled)

    def test_non_boolean_activity_flag_is_a_contract_error(self):
        for value in ("false", 0, 1, None, []):
            with self.subTest(value=value):
                state = type(
                    "State",
                    (),
                    {
                        "kind": MeasurementKind.CABLE_LOSS,
                        "status": MeasurementStatus.RUNNING,
                        "is_active": value,
                    },
                )()
                with self.assertRaises(TypeError):
                    self.presenter.availability(state)

    def test_actions_the_page_does_not_declare_are_not_written(self):
        stop_only = _Sink()
        start_only = _Sink()
        presenter = _presenter(
            MeasurementKind.AMPLIFIER,
            {
                MeasurementAction.START: start_only,
                MeasurementAction.CANCEL: stop_only,
            },
        )

        presenter.apply(_controller_state(MeasurementKind.AMPLIFIER))

        self.assertFalse(start_only.enabled)
        self.assertTrue(stop_only.enabled)
        self.assertEqual(presenter.supported_actions, {
            MeasurementAction.START,
            MeasurementAction.CANCEL,
        })

    def test_unsupported_action_is_absent_from_availability(self):
        cable = _presenter(
            MeasurementKind.CABLE_LOSS,
            {
                MeasurementAction.START: self.start,
                MeasurementAction.CONTINUE: self.continue_,
            },
        )

        running = cable.apply(_controller_state(MeasurementKind.CABLE_LOSS))

        self.assertEqual(running.enabled, frozenset())
        self.assertFalse(self.continue_.enabled)

        waiting = cable.apply(
            _controller_state(
                MeasurementKind.CABLE_LOSS, MeasurementStatus.WAITING_FOR_CONTINUE
            )
        )
        self.assertEqual(waiting.enabled, frozenset({MeasurementAction.CONTINUE}))

    def test_constructor_and_state_contract_errors_raise_type_error(self):
        with self.assertRaises(TypeError):
            MeasurementControlPresenter("cable_loss", {})
        with self.assertRaises(TypeError):
            MeasurementControlPresenter(MeasurementKind.CABLE_LOSS, [])
        with self.assertRaises(TypeError):
            MeasurementControlPresenter(
                MeasurementKind.CABLE_LOSS, {"start": self.start}
            )
        with self.assertRaises(TypeError):
            MeasurementControlPresenter(
                MeasurementKind.CABLE_LOSS,
                {MeasurementAction.START: object()},
            )
        with self.assertRaises(TypeError):
            self.presenter.availability(
                type("State", (), {"kind": "cable_loss", "status": MeasurementStatus.IDLE})()
            )
        with self.assertRaises(TypeError):
            self.presenter.availability(
                type("State", (), {"kind": None, "status": "running"})()
            )

    def test_presenter_keeps_a_snapshot_of_the_button_mapping(self):
        buttons = {
            MeasurementAction.START: self.start,
            MeasurementAction.STOP: self.stop,
        }
        presenter = MeasurementControlPresenter(MeasurementKind.CABLE_LOSS, buttons)
        buttons[MeasurementAction.STOP] = self.cancel

        presenter.show_idle()

        self.assertFalse(self.stop.enabled)
        self.assertIsNone(self.cancel.enabled)


class MeasurementPagesUseThePresenterTests(unittest.TestCase):
    """三个页面不再各自写按钮可用性判断，也不再直接调用 setEnabled。"""

    def test_pages_declare_actions_and_delegate_both_state_and_terminal_events(self):
        expected_actions = {
            "cable_loss_page.py": {
                "START", "STOP", "CANCEL", "CONTINUE",
            },
            "driver_mapping_page.py": {
                "START", "STOP", "CANCEL", "EMERGENCY_STOP",
            },
            "amplifier_page.py": {
                "START", "CANCEL", "EMERGENCY_STOP",
            },
        }
        for name in PAGE_NAMES:
            with self.subTest(page=name):
                source = (QT_ROOT / name).read_text(encoding="utf-8")
                self.assertIn("MeasurementControlPresenter", source)
                self.assertNotIn("setEnabled", source)
                tree = ast.parse(source, name)
                classes = [
                    node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
                ]
                self.assertTrue(classes)
                methods = {
                    node.name: node
                    for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef)
                }
                state_attributes = {
                    node.attr
                    for node in ast.walk(methods["_on_state_changed"])
                    if isinstance(node, ast.Attribute)
                }
                self.assertIn("apply", state_attributes)
                for terminal in ("_on_error", "_on_stopped", "_on_finished"):
                    terminal_attributes = {
                        node.attr
                        for node in ast.walk(methods[terminal])
                        if isinstance(node, ast.Attribute)
                    }
                    self.assertIn("show_idle", terminal_attributes)

                declared = {
                    node.attr
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "MeasurementAction"
                }
                self.assertEqual(declared, expected_actions[name])

    def test_controls_module_is_pure_python(self):
        tree = ast.parse(
            CONTROLS_SOURCE.read_text(encoding="utf-8"), str(CONTROLS_SOURCE)
        )
        imported_roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.level == 0
            ):
                imported_roots.add(node.module.split(".")[0])
        self.assertNotIn("PySide6", imported_roots)
        source = CONTROLS_SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("Signal(", source)


if __name__ == "__main__":
    unittest.main()
