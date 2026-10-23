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
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QCoreApplication

from app.run_context import PreparedRun, load_prepared_run
from app.gui_runtime import prepare_configuration
from application.run_service import RunNotResumable
from domain.run.models import HARDWARE_CONFIG_INPUT_PATH, RunStatus
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


def _broken_contract_run(hardware_config_path=None):
    """运行契约存在但冻结硬件快照不可用的替身（缺失、空路径、被删除或指向目录）。

    绑定 :class:`PreparedRun` 的真实方法，保证替身与生产对象的判定规则一致。
    """
    from app.run_context import PreparedRun

    return type(
        "BrokenContractRun",
        (),
        {
            "run_id": RUN_ID,
            "run_repository": object(),
            "config_path": Path("global-config.json"),
            "has_run_contract": PreparedRun.has_run_contract,
            "hardware_config_path": (
                None if hardware_config_path is None else Path(hardware_config_path)
            ),
            "hardware_connection_path": PreparedRun.hardware_connection_path,
            "_verified_hardware_snapshot": PreparedRun._verified_hardware_snapshot,
        },
    )()


def _resolvable_contract_run(path="frozen-hardware.json"):
    """可正常解析硬件快照的运行替身，供只关心门控顺序的测试使用。"""
    return type(
        "ResolvableContractRun",
        (),
        {
            "run_id": RUN_ID,
            "hardware_connection_path": lambda self, _path=path: Path(_path),
        },
    )()


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

    def test_reopened_run_hardware_config_is_frozen_inside_the_run(self):
        created, config_path = self._create_run()

        # 修改全局兼容配置后重新打开：硬件连接仍必须使用运行目录内的冻结快照。
        changed = _fully_valid_gui_config()
        changed["test_frequencies"] = [9.9]
        config_path.write_text(json.dumps(changed), encoding="utf-8")

        reopened = load_prepared_run(
            RUN_ID, config_path=config_path, run_repository=self.repository
        )

        frozen = created.run_directory / HARDWARE_CONFIG_INPUT_PATH
        self.assertEqual(reopened.hardware_config_path, frozen)
        self.assertEqual(reopened.hardware_connection_path(), frozen)
        self.assertTrue(frozen.is_file())
        self.assertEqual(reopened.hardware_config["test_frequencies"], [2.4])
        self.assertNotEqual(reopened.hardware_config["test_frequencies"], [9.9])

    def test_terminal_run_cannot_be_reopened(self):
        self._create_run()
        manifest = self.repository.load_manifest(RUN_ID)
        self.repository.update_manifest(
            RUN_ID, manifest.with_status(RunStatus.FAILED, error="硬件异常")
        )

        with self.assertRaises(RunNotResumable):
            load_prepared_run(RUN_ID, run_repository=self.repository)

    def test_run_without_hardware_config_snapshot_is_not_resumable(self):
        # 直接创建一个只有领域快照的运行，模拟创建中断或旧版本运行目录。
        self.repository.create_run(
            {"schema_version": 1, "test_plan": {}, "run_mapping": {}},
            run_id=RUN_ID,
        )
        with self.assertRaises(RunNotResumable):
            load_prepared_run(RUN_ID, run_repository=self.repository)

    def test_tampered_hardware_config_is_rejected_on_reopen(self):
        created, _config_path = self._create_run()
        tampered = created.run_directory / HARDWARE_CONFIG_INPUT_PATH
        payload = json.loads(tampered.read_text(encoding="utf-8"))
        payload["test_frequencies"] = [1.0]
        tampered.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaises(RunNotResumable):
            load_prepared_run(RUN_ID, run_repository=self.repository)

    def test_deleted_hardware_snapshot_cannot_be_used_for_connection(self):
        created, _config_path = self._create_run()
        frozen = created.run_directory / HARDWARE_CONFIG_INPUT_PATH
        self.assertTrue(frozen.is_file())
        frozen.unlink()

        with self.assertRaises(RunNotResumable) as raised:
            created.hardware_connection_path()
        self.assertIn("硬件配置快照", str(raised.exception))

    def test_snapshot_tampered_after_reopen_cannot_be_used_for_connection(self):
        created, _config_path = self._create_run()
        reopened = load_prepared_run(RUN_ID, run_repository=self.repository)
        frozen = created.run_directory / HARDWARE_CONFIG_INPUT_PATH
        self.assertEqual(reopened.hardware_connection_path(), frozen)

        # 加载后快照被替换：SHA-256 不再匹配，必须拒绝连接而不是继续使用。
        frozen.write_text(json.dumps({"test_frequencies": [1.0]}), encoding="utf-8")

        with self.assertRaises(RunNotResumable) as raised:
            reopened.hardware_connection_path()
        self.assertIn("硬件配置快照", str(raised.exception))

    def test_deregistered_hardware_snapshot_cannot_be_used_for_connection(self):
        created, _config_path = self._create_run()
        manifest = self.repository.load_manifest(RUN_ID)
        self.repository.update_manifest(RUN_ID, replace(manifest, inputs=()))

        # 文件仍在磁盘上，但 manifest 不再登记它：仍然必须拒绝连接。
        self.assertTrue(
            (created.run_directory / HARDWARE_CONFIG_INPUT_PATH).is_file()
        )
        with self.assertRaises(RunNotResumable) as raised:
            created.hardware_connection_path()
        self.assertIn("硬件配置快照", str(raised.exception))


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

        run = _resolvable_contract_run()
        window = MainWindow.__new__(MainWindow)
        window.config_path = Path("global-config.json")
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

    def test_invalid_contract_blocks_measurement_run_resolution(self):
        from presentation.qt.main_window import MainWindow

        cases = {
            "缺少快照字段": None,
            "快照文件不存在": Path("missing-hardware.json"),
        }
        for label, value in cases.items():
            with self.subTest(case=label):
                window = MainWindow.__new__(MainWindow)
                window.config_path = Path("global-config.json")
                window.prepared_run = _broken_contract_run(value)
                window.instrument_ctrl = object()
                window.on_measurement_error = MagicMock()

                self.assertIsNone(MainWindow._resolve_measurement_run(window))
                window.on_measurement_error.assert_called_once()
                self.assertIn(
                    "硬件配置快照",
                    str(window.on_measurement_error.call_args.args[0]),
                )

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
        create.assert_called_once()
        args, kwargs = create.call_args
        self.assertEqual(args, (str(window.config_path),))
        self.assertTrue(kwargs["run_contract"])
        self.assertIsNone(kwargs["paths"])
        self.assertEqual(kwargs["hardware_config"], window.config)

    def test_create_prepared_run_aborts_when_validation_fails(self):
        from presentation.qt.main_window import MainWindow

        window = MainWindow.__new__(MainWindow)
        window.config_page = MagicMock()
        window.config_page.validate.return_value = False
        window.prepared_run = None

        with patch("presentation.qt.main_window.prepare_configuration") as create:
            self.assertIsNone(MainWindow._create_prepared_run(window))

        create.assert_not_called()

    def test_measurement_config_path_prefers_frozen_run_config(self):
        from presentation.qt.main_window import MainWindow

        window = MainWindow.__new__(MainWindow)
        window.config_path = Path("global-config.json")
        frozen = Path("runs") / RUN_ID / HARDWARE_CONFIG_INPUT_PATH
        window.prepared_run = type(
            "PreparedRun",
            (),
            {"hardware_connection_path": lambda self: frozen},
        )()

        self.assertEqual(MainWindow._measurement_config_path(window), str(frozen))

        # 未创建运行时才回退到全局兼容配置。
        window.prepared_run = None
        self.assertEqual(
            MainWindow._measurement_config_path(window), str(Path("global-config.json"))
        )

    def test_measurement_config_path_does_not_fallback_for_contract_run(self):
        from presentation.qt.main_window import MainWindow

        cases = {
            "缺少快照字段": None,
            "空路径": "",
            "快照文件不存在": Path("missing-hardware.json"),
            "快照路径指向目录": Path(__file__).parent,
        }
        for label, value in cases.items():
            with self.subTest(case=label):
                window = MainWindow.__new__(MainWindow)
                window.config_path = Path("global-config.json")
                window.prepared_run = _broken_contract_run(value)

                # 运行契约存在但冻结硬件快照不可用：必须明确失败，绝不回退全局配置。
                with self.assertRaises(RunNotResumable) as raised:
                    MainWindow._measurement_config_path(window)

                self.assertIn("硬件配置快照", str(raised.exception))
                self.assertNotIn("global-config.json", str(raised.exception))
                self.assertIsInstance(raised.exception, ValueError)

    def test_missing_contract_resolver_is_rejected(self):
        from presentation.qt.main_window import MainWindow

        window = MainWindow.__new__(MainWindow)
        window.config_path = Path("global-config.json")
        # 对象没有契约校验入口（例如被替换成错误类型）也必须明确失败。
        window.prepared_run = object()

        with self.assertRaisesRegex(RunNotResumable, "运行契约不可用"):
            MainWindow._measurement_config_path(window)

    def test_invalid_contract_blocks_instrument_connection(self):
        from presentation.qt.main_window import MainWindow

        cases = {
            "缺少快照字段": None,
            "快照文件不存在": Path("missing-hardware.json"),
        }
        for label, value in cases.items():
            with self.subTest(case=label):
                window = MainWindow.__new__(MainWindow)
                window.config_path = Path("global-config.json")
                window.prepared_run = _broken_contract_run(value)
                window.connect_btn = MagicMock()
                window.instrument_worker = None
                window.add_log_message = MagicMock()
                window.update_and_save_config = MagicMock(return_value=True)

                with patch(
                    "presentation.qt.main_window.InstrumentWorker"
                ) as worker, patch(
                    "presentation.qt.main_window.QMessageBox.warning"
                ) as warning:
                    MainWindow.connect_instruments(window)

                # 不得构造 InstrumentWorker，也不得留下工作线程引用。
                worker.assert_not_called()
                self.assertIsNone(window.instrument_worker)
                # 用户可以看到明确的失败提示，并重新启用连接按钮。
                warning.assert_called_once()
                self.assertIn("硬件配置快照", str(warning.call_args.args[2]))
                window.connect_btn.setEnabled.assert_called_with(True)
                self.assertTrue(
                    any(
                        "无法连接仪器" in str(call.args[0])
                        for call in window.add_log_message.call_args_list
                        if call.args
                    )
                )

    def test_legacy_flow_still_uses_global_config(self):
        from app.run_context import PreparedRun
        from presentation.qt.main_window import MainWindow

        window = MainWindow.__new__(MainWindow)
        window.config_path = Path("global-config.json")
        window.prepared_run = None
        self.assertEqual(
            MainWindow._measurement_config_path(window), str(Path("global-config.json"))
        )

        # 旧兼容快照不带运行契约，仍使用创建时的源配置路径。
        window.prepared_run = PreparedRun(
            configuration=None,
            context=None,
            run_directory=Path("legacy-run"),
            config_path=Path("legacy-config.json"),
        )
        self.assertEqual(
            MainWindow._measurement_config_path(window), str(Path("legacy-config.json"))
        )

    def test_load_run_releases_previous_instrument_port(self):
        from presentation.qt.main_window import MainWindow

        port = object()
        window = MainWindow.__new__(MainWindow)
        window.instrument_ctrl = port
        window.instrument_worker = None
        window.config_path = Path("global-config.json")
        window.paths = None
        window.config_page = MagicMock()
        window.add_log_message = MagicMock()
        window._update_run_status = MagicMock()
        window._close_instrument_port = MagicMock(return_value=None)
        window.on_measurement_error = MagicMock()

        prepared = type(
            "PreparedRun",
            (),
            {"run_id": RUN_ID, "run_directory": Path("runs") / RUN_ID,
             "hardware_config": {"test_frequencies": [2.4]}},
        )()
        with patch(
            "presentation.qt.main_window.load_prepared_run", return_value=prepared
        ):
            MainWindow._load_prepared_run(window, RUN_ID)

        window._close_instrument_port.assert_called_once_with(port)
        self.assertIsNone(window.instrument_ctrl)

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
        # 端口注入已从接线确认回调移到连接成功后的交接点。
        window.measurement_controller.set_measurement_port.assert_not_called()


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

    def test_apply_config_reflects_a_frozen_run_on_the_widgets(self):
        from presentation.qt.config_page import ConfigPage

        page = ConfigPage({})
        frozen = _fully_valid_gui_config()
        frozen["test_frequencies"] = [2.4, 2.6]
        frozen["driver_mode"] = {"enabled": False}

        page.apply_config(frozen)

        self.assertEqual(page.freq_edit.text(), "[2.4, 2.6]")
        self.assertFalse(page.driver_mode_check.isChecked())
        self.assertEqual(page.sg_address.text(), "sim::sg")
        self.assertEqual(page.ps1_address.text(), "sim::ps1")
        self.assertEqual(page.pa_unit1_power_combo.currentText(), "PS1")
        self.assertEqual(page.power_config_widgets["PS1"]["channels"]["CH2"]["voltage"].value(), 28.0)


if __name__ == "__main__":
    unittest.main()
