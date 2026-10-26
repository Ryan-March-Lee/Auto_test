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
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QCoreApplication

from app.run_context import PreparedRun, load_prepared_run
from app.gui_runtime import create_cable_loss_measurement, prepare_configuration
from application.run_service import RunNotResumable
from domain.run.models import HARDWARE_CONFIG_INPUT_PATH, RunStatus
from infrastructure.persistence.run_repository import RunRepository
from presentation.qt.configuration_authoring import author_official_configuration
from presentation.qt.measurement_controller import MeasurementController
from presentation.qt.measurement_controller_contract import MeasurementCommand
from presentation.qt.measurement_state import MeasurementKind
from presentation.qt.workers import AmplifierWorker, CableLossWorker, DriverMappingWorker


RUN_ID = "20261022T153000Z_a1b2c3d4"


def _fully_valid_gui_config() -> dict:
    """完整有效的 GUI 组合配置（驱动模式关闭，DUT 通道已确认接线）。"""
    return author_official_configuration({
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
    })


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

    def test_command_defaults_to_legacy_for_migration_adapters(self):
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
    @staticmethod
    def _contract_run():
        return SimpleNamespace(
            has_run_contract=True,
            run_repository=object(),
            run_id=RUN_ID,
            run_directory=Path("runs") / RUN_ID,
            hardware_connection_path=lambda: None,
        )

    @classmethod
    def setUpClass(cls):
        cls.qt_app = QCoreApplication.instance() or QCoreApplication([])

    def test_cable_loss_worker_does_not_reprepare_when_run_is_given(self):
        service = _CableService()
        run = self._contract_run()
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
        run = self._contract_run()
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
        run = self._contract_run()
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
        # 创建运行入口不再接受 run_contract 开关：GUI 只走唯一的运行契约路径。
        self.assertNotIn("run_contract", kwargs)
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

    def test_create_prepared_run_releases_previous_instrument_port(self):
        from presentation.qt.main_window import MainWindow

        port = object()
        window = MainWindow.__new__(MainWindow)
        window.config_page = MagicMock()
        window.config_page.validate.return_value = True
        window.config = {}
        window.config_path = Path("config.json")
        window.paths = None
        window.instrument_ctrl = port
        window.instrument_worker = None
        window.prepared_run = None
        window.add_log_message = MagicMock()
        window.update_config_from_ui = MagicMock()
        window._confirm_create_run_wiring = MagicMock(return_value=True)
        window._save_config_file = MagicMock(return_value=True)
        window._update_run_status = MagicMock()
        window._close_instrument_port = MagicMock()

        prepared = SimpleNamespace(run_id=RUN_ID, run_directory=Path("runs") / RUN_ID)
        with patch(
            "presentation.qt.main_window.prepare_configuration", return_value=prepared
        ):
            result = MainWindow._create_prepared_run(window)

        # 创建新运行必须释放旧连接的仪器端口，避免新运行复用旧配置的资源。
        self.assertIs(result, prepared)
        window._close_instrument_port.assert_called_once_with(port)
        self.assertIsNone(window.instrument_ctrl)
        self.assertIs(window.prepared_run, prepared)

    def test_create_prepared_run_failure_keeps_previous_run_and_port(self):
        from presentation.qt.main_window import MainWindow

        port = object()
        previous_run = object()
        window = MainWindow.__new__(MainWindow)
        window.config_page = MagicMock()
        window.config_page.validate.return_value = True
        window.config = {}
        window.config_path = Path("config.json")
        window.paths = None
        window.instrument_ctrl = port
        window.instrument_worker = None
        window.prepared_run = previous_run
        window.add_log_message = MagicMock()
        window.update_config_from_ui = MagicMock()
        window._confirm_create_run_wiring = MagicMock(return_value=True)
        window._save_config_file = MagicMock(return_value=True)
        window._update_run_status = MagicMock()
        window._close_instrument_port = MagicMock()

        with patch(
            "presentation.qt.main_window.prepare_configuration",
            side_effect=RuntimeError("创建运行失败：磁盘已满"),
        ), patch("presentation.qt.main_window.QMessageBox.warning") as warning:
            result = MainWindow._create_prepared_run(window)

        # 创建失败不得切换运行、释放端口或报告为新状态。
        self.assertIsNone(result)
        self.assertIs(window.prepared_run, previous_run)
        self.assertIs(window.instrument_ctrl, port)
        window._close_instrument_port.assert_not_called()
        window._update_run_status.assert_not_called()
        warning.assert_called_once()

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

    def test_load_run_reports_backfill_failure_instead_of_success(self):
        from presentation.qt.config_page import ConfigBackfillError
        from presentation.qt.main_window import MainWindow

        current_port = object()
        current_run = object()
        window = MainWindow.__new__(MainWindow)
        window.instrument_ctrl = current_port
        window.instrument_worker = None
        window.config_path = Path("global-config.json")
        window.paths = None
        window.prepared_run = current_run
        window.config = {"sentinel": True}
        window.config_page = MagicMock()
        window.config_page.apply_config.side_effect = ConfigBackfillError("坏配置")
        window.add_log_message = MagicMock()
        window._update_run_status = MagicMock()
        window._close_instrument_port = MagicMock()

        prepared = type(
            "PreparedRun",
            (),
            {"run_id": RUN_ID, "run_directory": Path("runs") / RUN_ID,
             "hardware_config": {"test_frequencies": [2.4]}},
        )()
        with patch(
            "presentation.qt.main_window.load_prepared_run", return_value=prepared
        ), patch("presentation.qt.main_window.QMessageBox.warning") as warning:
            result = MainWindow._load_prepared_run(window, RUN_ID)

        # 两阶段提交：回填失败必须放弃切换，绝不报告为加载成功。
        self.assertIsNone(result)
        self.assertIs(window.prepared_run, current_run)
        self.assertEqual(window.config, {"sentinel": True})
        self.assertIs(window.instrument_ctrl, current_port)
        window._close_instrument_port.assert_not_called()
        window._update_run_status.assert_not_called()
        warning.assert_called_once()
        messages = [
            str(call.args[0])
            for call in window.add_log_message.call_args_list
            if call.args
        ]
        self.assertTrue(any("回填失败" in message for message in messages))
        self.assertFalse(any("已加载运行" in message for message in messages))

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

    def test_apply_config_clears_previous_values(self):
        from presentation.qt.config_page import ConfigPage

        page = ConfigPage({})
        page.apply_config(_fully_valid_gui_config())
        # 先确认配置 A 确实写入了非基线值，避免测试空转。
        self.assertEqual(page.sg_address.text(), "sim::sg")
        self.assertEqual(page.start_power.value(), -10.0)
        self.assertFalse(page.driver_mode_check.isChecked())

        # 配置 B 为空：所有受控控件必须回到空配置基线，不残留 A 的值。
        page.apply_config({})

        self.assertEqual(page.sg_address.text(), "")
        self.assertEqual(page.ps1_address.text(), "")
        self.assertEqual(page.freq_edit.text(), "[4.0, 4.4, 4.8, 5.2, 5.6, 5.8]")
        self.assertEqual(page.start_power.value(), -38.0)
        self.assertEqual(page.stop_power.value(), -16.0)
        self.assertEqual(page.power_step.value(), 1.0)
        self.assertEqual(page.compression_combo.currentText(), "5dB")
        self.assertEqual(page.attenuator_combo.currentText(), "40dB")
        self.assertTrue(page.driver_mode_check.isChecked())
        self.assertEqual(page.max_input_power.value(), 33.5)
        self.assertEqual(page.pa_unit_count_combo.currentText(), "2")
        self.assertTrue(page.ps1_enabled.isChecked())
        self.assertFalse(page.ps3_enabled.isChecked())
        self.assertEqual(
            page.power_config_widgets["PS1"]["channels"]["CH2"]["voltage"].value(), 0.0
        )

    def test_apply_config_projects_all_supported_fields(self):
        from presentation.qt.config_page import ConfigPage

        frozen = {
            "test_frequencies": [2.4, 2.6],
            "signal_source": {"start_power": -30.0, "stop_power": -5.0, "step": 0.5},
            "compression_point": {"type": "3dB"},
            "attenuator": {"type": "30dB"},
            "driver_mode": {"enabled": True},
            "dut_config": {"max_input_power": 25.5, "power_supply_count": 3},
            "instruments": {
                "signal_generator": {"address": "sim::sg2", "enabled": False},
                "spectrum_analyzer": {"address": "sim::sa2", "enabled": True},
                "power_supplies": {
                    "PS1": {
                        "address": "sim::ps1",
                        "enabled": True,
                        "channels": {
                            "CH1": {
                                "voltage": {
                                    "value": 3.3,
                                    "protection": 5.0,
                                    "protection_enabled": True,
                                },
                                "current": {
                                    "value": 0.5,
                                    "protection": 1.0,
                                    "protection_enabled": True,
                                },
                            },
                            "CH2": {
                                "voltage": {"value": 12.0},
                                "current": {"value": 2.5},
                            },
                        },
                    },
                    "PS2": {"address": "sim::ps2", "enabled": True},
                    "PS3": {"address": "sim::ps3", "enabled": True},
                },
            },
            "power_supply_assignment": {
                "driver_amplifier": {
                    "power_supply_count": 1,
                    "supplies": {"main": {"name": "PS1", "channel": ["CH1"]}},
                },
                "dut_amplifier": {
                    "power_supply_count": 3,
                    "supplies": {
                        "carrier": {"name": "PS2"},
                        "peaking": {"name": "PS3"},
                        "peaking2": {"name": "PS1"},
                    },
                },
            },
        }

        page = ConfigPage({})
        page.apply_config(frozen)

        self.assertEqual(page.freq_edit.text(), "[2.4, 2.6]")
        self.assertEqual(page.start_power.value(), -30.0)
        self.assertEqual(page.stop_power.value(), -5.0)
        self.assertEqual(page.power_step.value(), 0.5)
        self.assertEqual(page.compression_combo.currentText(), "3dB")
        self.assertEqual(page.attenuator_combo.currentText(), "30dB")
        self.assertTrue(page.driver_mode_check.isChecked())
        self.assertEqual(page.max_input_power.value(), 25.5)
        self.assertEqual(page.pa_unit_count_combo.currentText(), "3")
        self.assertEqual(page.sg_address.text(), "sim::sg2")
        self.assertFalse(page.sg_enabled.isChecked())
        self.assertEqual(page.sa_address.text(), "sim::sa2")
        self.assertTrue(page.sa_enabled.isChecked())
        self.assertEqual(page.ps1_address.text(), "sim::ps1")
        self.assertTrue(page.ps3_enabled.isChecked())
        self.assertFalse(page.ps4_enabled.isChecked())
        channel = page.power_config_widgets["PS1"]["channels"]["CH1"]
        self.assertEqual(channel["voltage"].value(), 3.3)
        self.assertEqual(channel["voltage_protection"].value(), 5.0)
        self.assertTrue(channel["voltage_protection_enabled"].isChecked())
        self.assertEqual(channel["current"].value(), 0.5)
        self.assertEqual(channel["current_protection"].value(), 1.0)
        self.assertTrue(channel["current_protection_enabled"].isChecked())
        self.assertTrue(page.driver_power_enabled.isChecked())
        self.assertEqual(page.driver_power_combo.currentText(), "PS1")
        self.assertEqual(page.pa_unit1_power_combo.currentText(), "PS2")
        self.assertEqual(page.pa_unit2_power_combo.currentText(), "PS3")
        self.assertEqual(page.pa_unit3_power_combo.currentText(), "PS1")

    def test_apply_config_matches_a_freshly_built_page(self):
        from presentation.qt.config_page import ConfigPage

        partial = {"test_frequencies": [9.0], "driver_mode": {"enabled": False}}
        rebuilt = ConfigPage(partial)
        page = ConfigPage({})
        page.apply_config(_fully_valid_gui_config())
        page.apply_config(partial)

        self.assertEqual(page.freq_edit.text(), rebuilt.freq_edit.text())
        self.assertEqual(page.start_power.value(), rebuilt.start_power.value())
        self.assertEqual(
            page.compression_combo.currentText(), rebuilt.compression_combo.currentText()
        )
        self.assertEqual(
            page.pa_unit_count_combo.currentText(), rebuilt.pa_unit_count_combo.currentText()
        )
        self.assertFalse(page.driver_mode_check.isChecked())
        self.assertEqual(page.sg_address.text(), rebuilt.sg_address.text())
        self.assertEqual(
            page.pa_unit1_power_combo.currentText(),
            rebuilt.pa_unit1_power_combo.currentText(),
        )

    def test_apply_config_reports_invalid_payload(self):
        from presentation.qt.config_page import ConfigBackfillError, ConfigPage

        page = ConfigPage({})
        for payload in (None, ["not", "a", "mapping"], "text"):
            with self.subTest(payload=payload):
                with self.assertRaises(ConfigBackfillError):
                    page.apply_config(payload)

    def test_apply_config_uses_defaults_for_invalid_field_types(self):
        from presentation.qt.config_page import ConfigPage

        page = ConfigPage({})
        # 先写入与默认相反的非默认值，确认非法类型确实被规范化而不是残留。
        initial = _fully_valid_gui_config()
        initial["instruments"]["signal_generator"]["enabled"] = False
        initial["instruments"]["power_supplies"]["PS1"]["enabled"] = False
        page.apply_config(initial)
        self.assertFalse(page.sg_enabled.isChecked())
        self.assertFalse(page.ps1_enabled.isChecked())

        page.apply_config(
            {
                "test_frequencies": ["invalid"],
                "driver_mode": {"enabled": "false"},
                "dut_config": {"power_supply_count": True, "max_input_power": "30"},
                "instruments": {
                    "signal_generator": {"address": None, "enabled": "false"},
                    "power_supplies": {
                        "PS1": {
                            "address": 123,
                            "enabled": "false",
                            "channels": {
                                "CH1": {
                                    "voltage": {"value": "12"},
                                    "current": {
                                        "value": True,
                                        "protection_enabled": "false",
                                    },
                                }
                            },
                        }
                    },
                },
            }
        )

        # 字符串不是合法文本值：地址回落到空串，布尔回落默认，数字回落基线。
        self.assertEqual(page.sg_address.text(), "")
        self.assertTrue(page.sg_enabled.isChecked())
        self.assertEqual(page.ps1_address.text(), "")
        self.assertTrue(page.ps1_enabled.isChecked())
        self.assertTrue(page.driver_mode_check.isChecked())
        self.assertEqual(page.freq_edit.text(), "[4.0, 4.4, 4.8, 5.2, 5.6, 5.8]")
        self.assertEqual(page.pa_unit_count_combo.currentText(), "2")
        self.assertEqual(page.max_input_power.value(), 33.5)
        channel = page.power_config_widgets["PS1"]["channels"]["CH1"]
        self.assertEqual(channel["voltage"].value(), 0.0)
        self.assertEqual(channel["current"].value(), 0.0)
        self.assertFalse(channel["current_protection_enabled"].isChecked())

    def test_apply_config_rejects_invalid_frequency_lists(self):
        from presentation.qt.config_page import ConfigPage

        page = ConfigPage({})
        for bad in (None, "not-a-list", [], ["bad"], [True], [float("nan")], [[]]):
            with self.subTest(bad=bad):
                page.apply_config({"test_frequencies": bad})
                self.assertEqual(
                    page.freq_edit.text(), "[4.0, 4.4, 4.8, 5.2, 5.6, 5.8]"
                )


class FrozenSnapshotValidationBoundaryTests(unittest.TestCase):
    """步骤 E：领域配置快照与硬件装配快照使用各自独立的校验规则。

    硬件快照不得再被当作领域运行配置解析，只做 JSON 对象校验与 manifest
    SHA-256/大小校验；领域快照则继续按领域运行配置校验。

    独立性用例在**创建运行时**就冻结不同 schema 的硬件配置（不事后改写登记项），
    默认可变性用例则只改文件、不更新 manifest，从而验证完整性校验而非受控覆盖。
    """

    def setUp(self):
        import tempfile

        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.base = Path(self._temporary.name)
        self.repository = RunRepository(self.base / "runs")
        self.config_path = self.base / "config.json"
        self.config_path.write_text(
            json.dumps(_fully_valid_gui_config()), encoding="utf-8"
        )

    def _create_run(self, *, hardware_config=None):
        return prepare_configuration(
            str(self.config_path),
            run_id=RUN_ID,
            run_repository=self.repository,
            hardware_config=hardware_config,
        )

    def _assemble_cable_loss(self, prepared):
        with patch(
            "app.gui_runtime.connect_instruments", return_value=object()
        ) as connect, patch("app.gui_runtime.CableLossUseCase", return_value=object()):
            create_cable_loss_measurement(
                str(self.config_path), prepared_run=prepared
            )
        return connect

    def test_independent_schema_hardware_config_is_loadable_and_connectable(self):
        # 创建运行时冻结的硬件装配快照与领域配置字段集合完全不同：
        # 它必须能通过加载校验并被用于连接，说明两种 schema 互不套用。
        independent = {
            "hardware": {
                "signal_generator": {"address": "sim::sg"},
                "power_supplies": {"PS1": {"address": "sim::ps1"}},
            },
            "meta": {"assembler": "lab"},
        }
        prepared = self._create_run(hardware_config=independent)
        frozen = prepared.run_directory / HARDWARE_CONFIG_INPUT_PATH
        self.assertEqual(
            json.loads(frozen.read_text(encoding="utf-8")), independent
        )

        reopened = load_prepared_run(RUN_ID, run_repository=self.repository)
        self.assertEqual(reopened.hardware_config, independent)
        self.assertEqual(reopened.hardware_connection_path(), frozen)

        connect = self._assemble_cable_loss(reopened)
        connect.assert_called_once_with(str(frozen))

    def test_non_object_hardware_snapshot_is_rejected(self):
        # 已登记、SHA-256 一致但根节点不是 JSON 对象的硬件快照必须被拒绝。
        prepared = self._create_run()
        frozen = prepared.run_directory / HARDWARE_CONFIG_INPUT_PATH
        frozen.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
        self.repository.register_file(
            RUN_ID, HARDWARE_CONFIG_INPUT_PATH, description="硬件组装配置快照"
        )

        with patch("app.gui_runtime.connect_instruments") as connect:
            with self.assertRaises(RunNotResumable) as raised:
                create_cable_loss_measurement(
                    str(self.config_path), prepared_run=prepared
                )

        connect.assert_not_called()
        self.assertIn("硬件配置快照", str(raised.exception))
        self.assertTrue(frozen.is_file())

    def test_hardware_snapshot_tampering_without_manifest_update_is_rejected(self):
        # 创建后只改硬件快照文件、不更新 manifest：SHA-256 不一致必须被拒绝。
        prepared = self._create_run()
        frozen = prepared.run_directory / HARDWARE_CONFIG_INPUT_PATH
        payload = json.loads(frozen.read_text(encoding="utf-8"))
        payload["test_frequencies"] = [9.9]
        frozen.write_text(json.dumps(payload), encoding="utf-8")

        with patch("app.gui_runtime.connect_instruments") as connect:
            with self.assertRaises(RunNotResumable) as raised:
                create_cable_loss_measurement(
                    str(self.config_path), prepared_run=prepared
                )

        connect.assert_not_called()
        self.assertIn("硬件配置快照", str(raised.exception))

    def test_domain_snapshot_tampering_without_manifest_update_is_rejected(self):
        # 创建后只改领域快照文件、不更新 manifest：同样必须被拒绝。
        prepared = self._create_run()
        domain_snapshot = prepared.run_directory / "config.json"
        payload = json.loads(domain_snapshot.read_text(encoding="utf-8"))
        payload["test_plan"] = {"frequencies": [9.9]}
        domain_snapshot.write_text(json.dumps(payload), encoding="utf-8")

        with patch("app.gui_runtime.connect_instruments") as connect:
            with self.assertRaises(ValueError) as raised:
                create_cable_loss_measurement(
                    str(self.config_path), prepared_run=prepared
                )

        connect.assert_not_called()
        self.assertIn("配置快照", str(raised.exception))

    def test_global_config_change_is_ignored(self):
        prepared = self._create_run()
        changed = _fully_valid_gui_config()
        changed["test_frequencies"] = [9.9]
        self.config_path.write_text(json.dumps(changed), encoding="utf-8")

        connect = self._assemble_cable_loss(prepared)
        connect.assert_called_once_with(str(prepared.hardware_config_path))


class ContractRepositoryCapabilityTests(unittest.TestCase):
    """运行契约仓储能力不完整时必须明确失败，绝不静默跳过快照校验。"""

    def setUp(self):
        import tempfile

        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.base = Path(self._temporary.name)
        self.repository = RunRepository(self.base / "runs")
        self.config_path = self.base / "config.json"
        self.config_path.write_text(
            json.dumps(_fully_valid_gui_config()), encoding="utf-8"
        )

    def test_contract_run_with_incomplete_repository_is_rejected(self):
        # 契约对象指向一个存在的普通文件，但仓储缺少全部校验能力。
        prepared = SimpleNamespace(
            configuration=object(),
            context=SimpleNamespace(run_id=RUN_ID),
            run_directory=self.base / "runs" / RUN_ID,
            run_repository=object(),
            hardware_config_path=Path(__file__),
        )

        with patch("app.gui_runtime.connect_instruments") as connect:
            with self.assertRaisesRegex(RunNotResumable, "仓储不完整"):
                create_cable_loss_measurement(
                    str(self.config_path), prepared_run=prepared
                )

        connect.assert_not_called()

    def test_contract_run_missing_hardware_reader_is_rejected(self):
        class _PartialRepository:
            """具备领域快照读取与 manifest 校验，但缺少已登记 JSON 读取能力。"""

            def read_config(self, run_id, *, verify=True):
                return {}

            def load_manifest(self, run_id):
                raise AssertionError("不应读取 manifest")

            def verify_file(self, run_id, reference):
                raise AssertionError("不应校验登记文件")

        prepared = SimpleNamespace(
            configuration=object(),
            context=SimpleNamespace(run_id=RUN_ID),
            run_directory=self.base / "runs" / RUN_ID,
            run_repository=_PartialRepository(),
            hardware_config_path=Path(__file__),
        )

        with patch("app.gui_runtime.connect_instruments") as connect:
            with self.assertRaisesRegex(RunNotResumable, "read_run_json"):
                create_cable_loss_measurement(
                    str(self.config_path), prepared_run=prepared
                )

        connect.assert_not_called()

    def test_prepared_run_rejects_incomplete_contract_repository(self):
        # 运行上下文层使用同一能力契约：仓储不完整时不得放行未校验的快照。
        run = type(
            "IncompleteContractRun",
            (),
            {
                "run_id": RUN_ID,
                "run_repository": object(),
                "has_run_contract": PreparedRun.has_run_contract,
                "hardware_config_path": Path(__file__),
                "hardware_connection_path": PreparedRun.hardware_connection_path,
                "_verified_hardware_snapshot": PreparedRun._verified_hardware_snapshot,
            },
        )()

        with self.assertRaisesRegex(RunNotResumable, "仓储不完整"):
            run.hardware_connection_path()


if __name__ == "__main__":
    unittest.main()
