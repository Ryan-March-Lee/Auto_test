"""步骤 7 测试：GUI 收敛为“配置阶段 + 测量阶段”两阶段流程。

覆盖验收标准：

- 配置页面通过“校验并创建运行”创建 ``PreparedRun``，并提供按 ``run_id`` 重新打开
  已有运行的入口。
- 三个测量页面只接受当前 ``PreparedRun``，不再在开始前重新保存 GUI 配置。
- controller / worker 直接消费运行契约，不再重复执行配置预检。
- 重新打开 GUI 后可以仅凭 ``run_id`` 重建运行上下文，不依赖 GUI 内存配置。
"""

import json
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QCoreApplication

from app.run_context import PreparedRun, load_prepared_run
from app.gui_runtime import prepare_configuration
from infrastructure.persistence.run_repository import RunRepository
from presentation.qt.measurement_controller import MeasurementController
from presentation.qt.measurement_controller_contract import MeasurementCommand
from presentation.qt.measurement_state import MeasurementKind
from presentation.qt.workers import AmplifierWorker, CableLossWorker, DriverMappingWorker


RUN_ID = "20261022T153000Z_a1b2c3d4"


def _fully_valid_gui_config() -> dict:
    """完整有效的旧版 GUI 组合配置（驱动模式关闭，DUT 通道已确认接线）。"""
    return {
        "test_frequencies": [2.4],
        "signal_source": {"start_power": -10.0, "stop_power": 0.0, "step": 1.0},
        "compression_point": {"type": "5dB"},
        "driver_mode": {"enabled": False},
        "attenuator": {"type": "30dB"},
        "dut_config": {"max_input_power": 30.0, "power_supply_count": 1},
        "instruments": {
            "signal_generator": {"address": "sim::sg", "model": "SG", "enabled": True},
            "spectrum_analyzer": {"address": "sim::sa", "model": "SA", "enabled": True},
            "power_supplies": {
                "PS1": {
                    "address": "sim::ps1",
                    "enabled": True,
                    "channels": {
                        "CH1": {"voltage": {"value": 2.8}, "current": {"value": 0.1}},
                        "CH2": {"voltage": {"value": 28.0}, "current": {"value": 2.0}},
                    },
                }
            },
        },
        "power_supply_assignment": {
            "driver_amplifier": {"power_supply_count": 0, "supplies": {}},
            "dut_amplifier": {
                "power_supply_count": 1,
                "supplies": {"carrier": {"name": "PS1", "channel": ["CH1", "CH2"]}},
            },
        },
        "wiring": {"confirmed": True, "connection_note": "现场接线已确认"},
    }


class _CableService:
    def __init__(self):
        self.last_result = {"cable_losses": {"2.4": {"cable1": 1.0}}}
        self.measure_called = threading.Event()
        self.continue_called = threading.Event()

    def set_step_pause_callback(self, _callback):
        pass

    def measure_all_frequencies(self):
        self.measure_called.set()

    def continue_to_step2(self):
        self.continue_called.set()

    def stop_measurement(self):
        pass


class _MappingService:
    def __init__(self):
        self.last_result = {"power_mapping": {"2.4": {"-10": 1.0}}}
        self.measure_called = threading.Event()

    def measure_all_frequencies(self):
        self.measure_called.set()

    def stop_measurement(self):
        pass


class _Signal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def disconnect(self, slot):
        self.slots.remove(slot)

    def emit(self, *args):
        for slot in tuple(self.slots):
            slot(*args)


class _ControllerSignals:
    def __init__(self):
        for name in (
            "progress", "message", "data_update", "result", "finished",
            "stopped", "error", "step_pause", "rejected", "state_changed",
            "thread_finished",
        ):
            setattr(self, name, _Signal())


class _Worker:
    def __init__(self):
        self.signals = _ControllerSignals()
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        pass

    def emergency_stop(self):
        pass


class RunContractReopenTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.base = Path(self._temporary.name)
        self.repository = RunRepository(self.base / "runs")

    def _create_run(self, run_id=RUN_ID):
        config_path = self.base / "config.json"
        config_path.write_text(json.dumps(_fully_valid_gui_config()), encoding="utf-8")
        prepared = prepare_configuration(
            str(config_path),
            run_contract=True,
            run_id=run_id,
            run_repository=self.repository,
        )
        return prepared, config_path

    def test_reopen_by_run_id_uses_the_frozen_snapshot(self):
        created, config_path = self._create_run()

        # 修改 GUI 配置文件后，重新打开仍必须使用运行目录中的冻结快照。
        changed = _fully_valid_gui_config()
        changed["test_frequencies"] = [9.9]
        config_path.write_text(json.dumps(changed), encoding="utf-8")

        reopened = load_prepared_run(
            RUN_ID, config_path=config_path, run_repository=self.repository
        )

        self.assertIsInstance(reopened, PreparedRun)
        self.assertTrue(reopened.has_run_contract)
        self.assertEqual(reopened.run_id, RUN_ID)
        self.assertEqual(reopened.run_directory, created.run_directory)
        self.assertEqual(reopened.configuration.test_plan.frequencies, [2.4])

    def test_reopen_unknown_run_fails(self):
        with self.assertRaises(FileNotFoundError):
            load_prepared_run("20260101T000000Z_deadbeef", run_repository=self.repository)

    def test_reopened_run_carries_a_stage_recorder_capable_repository(self):
        self._create_run()
        reopened = load_prepared_run(RUN_ID, run_repository=self.repository)
        # 运行上下文可以被阶段记录器直接消费，说明重开后的运行与创建时等价。
        self.assertIs(reopened.run_repository, self.repository)
        self.assertEqual(reopened.run_repository.load_manifest(RUN_ID).run_id, RUN_ID)


class MeasurementCommandRunContractTests(unittest.TestCase):
    def test_command_carries_an_opaque_run_contract(self):
        run = object()
        command = MeasurementCommand("config.json", prepared_run=run)
        self.assertIs(command.prepared_run, run)
        self.assertEqual(command.config_path, "config.json")

    def test_command_defaults_to_legacy_without_run_contract(self):
        self.assertIsNone(MeasurementCommand("config.json").prepared_run)


class ControllerRunContractTests(unittest.TestCase):
    def test_controller_forwards_prepared_run_to_worker_factory(self):
        captured = {}
        run = object()

        def factory(command, *, measurement_port=None):
            captured["command"] = command
            captured["port"] = measurement_port
            return _Worker()

        controller = MeasurementController(
            {MeasurementKind.AMPLIFIER: factory}, lambda: "runtime-config.json"
        )
        self.assertTrue(
            controller.start_amplifier(
                MeasurementCommand("stale.json", prepared_run=run)
            )
        )

        self.assertIs(captured["command"].prepared_run, run)
        # 运行时配置路径仍由 provider 提供，页面路径只作为输入元数据。
        self.assertEqual(captured["command"].config_path, "runtime-config.json")


class WorkerReusesPreparedRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QCoreApplication.instance() or QCoreApplication([])

    def test_cable_loss_worker_does_not_reprepare_when_run_is_given(self):
        service = _CableService()
        run = object()
        worker = CableLossWorker(
            "config.json",
            sleep_fn=lambda _: None,
            prepared_run=run,
            measurement_factory=lambda *args, **kwargs: service,
        )
        results = []
        worker.signals.result.connect(results.append)
        with patch("app.gui_runtime.prepare_configuration") as prepare:
            worker.start()
            self.assertTrue(service.measure_called.wait(1))
            worker.continue_measurement()
            self.assertTrue(service.continue_called.wait(1))
            worker.wait(1000)

        prepare.assert_not_called()
        # 运行契约运行直接消费注入的运行，不经过任何预检/快照重建。
        self.assertIs(worker.service, service)

    def test_driver_mapping_worker_uses_injected_run_without_preflight(self):
        service = _MappingService()
        run = object()
        calls = []

        def factory(*args, **kwargs):
            calls.append(kwargs.get("prepared_run"))
            return service

        worker = DriverMappingWorker(
            "config.json",
            sleep_fn=lambda _: None,
            prepared_run=run,
            measurement_factory=factory,
        )
        with patch("app.gui_runtime.prepare_configuration") as prepare:
            worker.run()

        prepare.assert_not_called()
        self.assertEqual(calls, [run])
        self.assertTrue(service.measure_called.is_set())

    def test_amplifier_worker_uses_injected_run_without_preflight(self):
        service = _MappingService()
        run = object()
        calls = []

        def factory(*args, **kwargs):
            calls.append(kwargs.get("prepared_run"))
            return service

        worker = AmplifierWorker(
            "config.json",
            sleep_fn=lambda _: None,
            prepared_run=run,
            measurement_factory=factory,
        )
        with patch("app.gui_runtime.prepare_configuration") as prepare:
            worker.run()

        prepare.assert_not_called()
        self.assertEqual(calls, [run])
        self.assertTrue(service.measure_called.is_set())


class MainWindowTwoPhaseTests(unittest.TestCase):
    def test_resolve_measurement_run_requires_contract_and_instrument(self):
        from presentation.qt.main_window import MainWindow

        run = object()
        window = MainWindow.__new__(MainWindow)
        window.prepared_run = run
        window.instrument_ctrl = object()
        window.add_log_message = MagicMock()
        window.on_measurement_error = MagicMock()

        self.assertIs(MainWindow._resolve_measurement_run(window), run)
        window.on_measurement_error.assert_not_called()

        window.prepared_run = None
        self.assertIsNone(MainWindow._resolve_measurement_run(window))
        window.on_measurement_error.assert_called_once()

        window.prepared_run = run
        window.instrument_ctrl = None
        self.assertIsNone(MainWindow._resolve_measurement_run(window))
        self.assertEqual(window.on_measurement_error.call_count, 2)

    def test_create_prepared_run_freezes_the_run_contract(self):
        from presentation.qt.main_window import MainWindow

        window = MainWindow.__new__(MainWindow)
        window.config_page = MagicMock()
        window.config_page.validate.return_value = True
        window.config = {}
        window.config_path = Path("config.json")
        window.paths = None
        window.prepared_run = None
        window.add_log_message = MagicMock()
        window.update_config_from_ui = MagicMock()
        window._confirm_create_run_wiring = MagicMock(return_value=True)
        window._save_config_file = MagicMock(return_value=True)
        window._update_run_status = MagicMock()

        from types import SimpleNamespace

        prepared = SimpleNamespace(run_id=RUN_ID, run_directory=Path("runs") / RUN_ID)
        with patch(
            "presentation.qt.main_window.prepare_configuration", return_value=prepared
        ) as create:
            result = MainWindow._create_prepared_run(window)

        self.assertIs(result, prepared)
        self.assertIs(window.prepared_run, prepared)
        create.assert_called_once_with(
            str(window.config_path), run_contract=True, paths=None
        )

    def test_create_prepared_run_aborts_when_validation_fails(self):
        from presentation.qt.main_window import MainWindow

        window = MainWindow.__new__(MainWindow)
        window.config_page = MagicMock()
        window.config_page.validate.return_value = False
        window.prepared_run = None

        with patch("presentation.qt.main_window.prepare_configuration") as create:
            self.assertIsNone(MainWindow._create_prepared_run(window))

        create.assert_not_called()

    def test_confirm_wiring_does_not_rewrite_frozen_config(self):
        from presentation.qt.main_window import MainWindow

        window = MainWindow.__new__(MainWindow)
        window.config = {"wiring": {"confirmed": True, "connection_note": "frozen"}}
        window.instrument_ctrl = object()
        window.measurement_controller = MagicMock()
        window.add_log_message = MagicMock()
        window._save_config_file = MagicMock()

        self.assertTrue(MainWindow._confirm_cable_loss_wiring(window))
        self.assertTrue(MainWindow._confirm_driver_mapping_wiring(window))
        self.assertTrue(MainWindow._confirm_amplifier_wiring(window))

        window._save_config_file.assert_not_called()
        self.assertEqual(
            window.config["wiring"],
            {"confirmed": True, "connection_note": "frozen"},
        )
        window.measurement_controller.set_measurement_port.assert_called()


class ConfigPageRunEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_create_run_signal_is_emitted_after_validation(self):
        from presentation.qt.config_page import ConfigPage

        page = ConfigPage({})
        requested = []
        page.create_run_requested.connect(lambda: requested.append(True))
        page.create_run_btn.click()
        self.assertEqual(requested, [True])

    def test_load_run_signal_carries_the_run_id(self):
        from presentation.qt.config_page import ConfigPage

        page = ConfigPage({})
        requested = []
        page.load_run_requested.connect(requested.append)
        page.load_run_id_edit.setText(RUN_ID)
        page.load_run_btn.click()
        self.assertEqual(requested, [RUN_ID])

    def test_load_run_requires_a_run_id(self):
        from presentation.qt.config_page import ConfigPage

        page = ConfigPage({})
        failures = []
        requested = []
        page.validation_failed.connect(failures.append)
        page.load_run_requested.connect(requested.append)
        page.load_run_btn.click()
        self.assertEqual(requested, [])
        self.assertEqual(len(failures), 1)

    def test_run_status_label_is_updated_by_the_window(self):
        from presentation.qt.config_page import ConfigPage

        page = ConfigPage({})
        page.set_run_status(f"当前运行: {RUN_ID}")
        self.assertEqual(page.run_status_label.text(), f"当前运行: {RUN_ID}")


if __name__ == "__main__":
    unittest.main()
