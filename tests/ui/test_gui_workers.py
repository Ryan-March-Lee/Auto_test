import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication

from presentation.qt.workers import (
    AmplifierWorker,
    CableLossWorker,
    DriverMappingWorker,
    InstrumentWorker,
)


class _CableService:
    def __init__(self, path1_done):
        self.path1_done = path1_done
        self.last_result = {"cable_losses": {"2.4": {"cable1": 1.0}}}
        self.continue_called = threading.Event()
        self.stop_called = threading.Event()

    def set_step_pause_callback(self, callback):
        self.step_pause_callback = callback

    def measure_all_frequencies(self):
        self.path1_done.set()

    def continue_to_step2(self):
        self.continue_called.set()

    def stop_measurement(self):
        self.stop_called.set()


class _DriverMappingService:
    def __init__(self):
        self.last_result = {"power_mapping": {"2.4": {"-10": 1.0}}}
        self.stop_called = threading.Event()
        self.measure_called = threading.Event()

    def stop_measurement(self):
        self.stop_called.set()

    def measure_all_frequencies(self):
        self.measure_called.set()


class _AmplifierService:
    def __init__(self):
        self.last_result = {"results": {"2.4": {"compression_point": 1.0}}}
        self.stop_called = threading.Event()
        self.measure_called = threading.Event()

    def stop_measurement(self):
        self.stop_called.set()

    def measure_all_frequencies(self):
        self.measure_called.set()


class _StopDuringValidation:
    """在 worker 校验运行契约时触发停止请求的替身运行契约。

    worker 必须在校验通过后、创建测量用例前再次检查停止意图：这里让
    ``has_run_contract`` 的读取触发 ``worker.stop()``，从而精确覆盖该分支，
    证明停止后不再装配测量对象（也就不会有任何硬件动作）。
    """

    def __init__(self, worker):
        self._worker = worker

    @property
    def has_run_contract(self):
        self._worker.stop()
        return True

    run_repository = object()
    run_id = "run-worker"
    run_directory = "runs/run-worker"

    def hardware_connection_path(self):
        return None


class GuiWorkerTests(unittest.TestCase):
    @staticmethod
    def _contract_run():
        return type("PreparedRun", (), {
            "has_run_contract": True,
            "run_repository": object(),
            "run_id": "run-worker",
            "run_directory": "runs/run-worker",
            "hardware_connection_path": lambda self: None,
        })()
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QCoreApplication.instance() or QCoreApplication([])

    def test_cable_loss_second_step_runs_in_worker_thread(self):
        path1_done = threading.Event()
        service = _CableService(path1_done)
        prepared = self._contract_run()
        worker = CableLossWorker("config.json", sleep_fn=lambda _: None, prepared_run=prepared)
        with patch("app.gui_runtime.prepare_configuration", return_value=prepared), \
                patch("app.gui_runtime.create_cable_loss_measurement", return_value=service) as factory:
            worker.start()
            self.assertTrue(path1_done.wait(1))
            deadline = time.monotonic() + 1
            while not worker._waiting_for_continue and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(worker.isRunning())
            worker.continue_measurement()
            self.assertTrue(service.continue_called.wait(1))
            worker.wait(1000)

        self.assertIs(factory.call_args.kwargs["prepared_run"], prepared)
        self.assertTrue(service.continue_called.is_set())
        self.assertFalse(worker.isRunning())

    def test_stopping_at_cable_loss_checkpoint_releases_worker(self):
        path1_done = threading.Event()
        service = _CableService(path1_done)
        prepared = self._contract_run()
        worker = CableLossWorker(
            "config.json",
            sleep_fn=lambda _: None,
            prepared_run=prepared,
        )

        with patch("app.gui_runtime.prepare_configuration", return_value=prepared), \
                patch("app.gui_runtime.create_cable_loss_measurement", return_value=service):
            worker.start()
            self.assertTrue(path1_done.wait(1))
            worker.stop()
            worker.wait(1000)

        self.assertTrue(service.stop_called.is_set())
        self.assertFalse(worker.isRunning())

    def test_missing_prepared_run_prevents_measurement_service_creation(self):
        worker = CableLossWorker("config.json", sleep_fn=lambda _: None)
        errors = []
        worker.signals.error.connect(errors.append)

        with patch("app.gui_runtime.prepare_configuration") as prepare, patch(
            "app.gui_runtime.create_cable_loss_measurement"
        ) as factory:
            worker.run()

        prepare.assert_not_called()
        factory.assert_not_called()
        self.assertIsNone(worker.service)
        self.assertEqual(
            errors,
            ["应用用例构造失败: 正式线损测量必须携带有效 PreparedRun；请先创建或重新加载运行"],
        )

    def test_workers_do_not_implicitly_prepare_a_legacy_run(self):
        for worker_type, factory_name in (
            (DriverMappingWorker, "create_driver_mapping_measurement"),
            (AmplifierWorker, "create_amplifier_measurement"),
        ):
            with self.subTest(worker=worker_type.__name__):
                errors = []
                worker = worker_type("config.json")
                worker.signals.error.connect(errors.append)
                with patch("app.gui_runtime.prepare_configuration") as prepare, patch(
                    f"app.gui_runtime.{factory_name}"
                ) as factory:
                    worker.run()
                prepare.assert_not_called()
                factory.assert_not_called()
                self.assertEqual(len(errors), 1)
                self.assertIn("必须携带有效 PreparedRun", errors[0])

    def test_workers_reject_non_contract_run_objects(self):
        """传入的不是运行契约对象时同样在创建测量前失败。"""
        for worker_type, factory_name, label in (
            (CableLossWorker, "create_cable_loss_measurement", "线损"),
            (DriverMappingWorker, "create_driver_mapping_measurement", "驱动映射"),
            (AmplifierWorker, "create_amplifier_measurement", "主功放"),
        ):
            with self.subTest(worker=worker_type.__name__):
                errors = []
                worker = worker_type("config.json", sleep_fn=lambda _: None, prepared_run=object())
                worker.signals.error.connect(errors.append)
                with patch(f"app.gui_runtime.{factory_name}") as factory:
                    worker.run()
                factory.assert_not_called()
                self.assertIsNone(worker.service)
                self.assertEqual(
                    errors,
                    [f"应用用例构造失败: 正式{label}测量必须携带有效 PreparedRun；请先创建或重新加载运行"],
                )

    def test_cable_loss_stop_during_run_contract_validation_skips_measurement(self):
        """运行契约校验期间收到停止请求时，不得继续创建测量服务（无硬件动作）。"""
        worker = CableLossWorker("config.json", sleep_fn=lambda _: None)
        stopped = []
        worker.signals.stopped.connect(stopped.append)
        worker.prepared_run = _StopDuringValidation(worker)

        with patch("app.gui_runtime.create_cable_loss_measurement") as factory:
            worker.run()

        factory.assert_not_called()
        self.assertEqual(stopped, ["用户停止"])
        self.assertIsNone(worker.service)

    def test_cable_loss_stop_after_assembly_emits_stopped(self):
        service = _CableService(threading.Event())
        stopped = []
        worker = CableLossWorker(
            "config.json",
            sleep_fn=lambda _: None,
            prepared_run=self._contract_run(),
        )
        worker.signals.stopped.connect(stopped.append)

        def create_and_stop(*_args, **_kwargs):
            worker.stop()
            return service

        worker.measurement_factory = create_and_stop
        worker.run()

        self.assertTrue(service.stop_called.is_set())
        self.assertEqual(stopped, ["用户停止"])
        self.assertIsNone(worker.measurement_port)

    def test_measurement_factory_is_explicitly_injected(self):
        prepared = self._contract_run()
        service = _DriverMappingService()
        calls = []

        def factory(*args, **kwargs):
            calls.append((args, kwargs))
            return service

        worker = DriverMappingWorker(
            "config.json",
            sleep_fn=lambda _: None,
            measurement_factory=factory,
            prepared_run=prepared,
        )
        worker.run()

        self.assertIs(worker.service, service)
        self.assertTrue(service.measure_called.is_set())
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], ("config.json",))
        self.assertIs(calls[0][1]["prepared_run"], prepared)

    def test_measurement_workers_publish_structured_results(self):
        cases = (
            (DriverMappingWorker, _DriverMappingService, "driver_mapping"),
            (AmplifierWorker, _AmplifierService, "amplifier"),
        )
        for worker_type, service_type, _label in cases:
            with self.subTest(worker=worker_type.__name__):
                results = []
                worker = worker_type(
                    "config.json",
                    sleep_fn=lambda _: None,
                    prepared_run=self._contract_run(),
                    measurement_factory=lambda *_args, **_kwargs: service_type(),
                )
                worker.signals.result.connect(results.append)
                worker.run()
                self.assertEqual(len(results), 1)
                self.assertIsInstance(results[0], dict)

    def test_measurement_construction_failure_uses_application_error_signal(self):
        errors = []
        worker = AmplifierWorker(
            "config.json",
            prepared_run=self._contract_run(),
            measurement_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("request assembly failed")
            ),
        )
        worker.signals.error.connect(errors.append)
        worker.run()

        self.assertEqual(errors, ["应用用例构造失败: request assembly failed"])
        self.assertIsNone(worker.service)

    def test_measurement_construction_failure_does_not_close_injected_port(self):
        class _Port:
            def __init__(self):
                self.close_calls = []

            def close_all(self, *, close_rf=False):
                self.close_calls.append(close_rf)

        port = _Port()
        errors = []
        worker = DriverMappingWorker(
            "config.json",
            measurement_port=port,
            prepared_run=self._contract_run(),
            measurement_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("assembly failed")
            ),
        )
        worker.signals.error.connect(errors.append)
        worker.run()

        self.assertEqual(errors, ["应用用例构造失败: assembly failed"])
        self.assertEqual(port.close_calls, [])
        self.assertIsNone(worker.measurement_port)

    def test_driver_mapping_stop_before_run_skips_measurement(self):
        worker = DriverMappingWorker("config.json", sleep_fn=lambda _: None)
        stopped = []
        worker.signals.stopped.connect(stopped.append)
        worker.stop()

        with patch("app.gui_runtime.prepare_configuration") as prepare, patch(
            "app.gui_runtime.create_driver_mapping_measurement"
        ) as factory:
            worker.run()

        prepare.assert_not_called()
        factory.assert_not_called()
        self.assertEqual(stopped, ["用户停止"])
        self.assertIsNone(worker.service)

    def test_driver_mapping_stop_during_run_contract_validation_skips_measurement(self):
        """运行契约校验期间收到停止请求时，不得继续创建测量服务。"""
        worker = DriverMappingWorker("config.json", sleep_fn=lambda _: None)
        stopped = []
        worker.signals.stopped.connect(stopped.append)
        worker.prepared_run = _StopDuringValidation(worker)

        with patch("app.gui_runtime.create_driver_mapping_measurement") as factory:
            worker.run()

        factory.assert_not_called()
        self.assertEqual(stopped, ["用户停止"])
        self.assertIsNone(worker.service)

    def test_driver_mapping_stop_after_assembly_cancels_service_without_scan(self):
        worker = DriverMappingWorker(
            "config.json",
            sleep_fn=lambda _: None,
            prepared_run=self._contract_run(),
        )
        service = _DriverMappingService()
        stopped = []
        worker.signals.stopped.connect(stopped.append)

        def create_and_stop(*_args, **_kwargs):
            worker.stop()
            return service

        with patch(
            "app.gui_runtime.create_driver_mapping_measurement", side_effect=create_and_stop
        ):
            worker.run()

        self.assertTrue(service.stop_called.is_set())
        self.assertFalse(service.measure_called.is_set())
        self.assertEqual(stopped, ["用户停止"])

    def test_amplifier_stop_before_run_skips_measurement(self):
        worker = AmplifierWorker("config.json", sleep_fn=lambda _: None)
        stopped = []
        worker.signals.stopped.connect(stopped.append)
        worker.stop()

        with patch("app.gui_runtime.prepare_configuration") as prepare, patch(
            "app.gui_runtime.create_amplifier_measurement"
        ) as factory:
            worker.run()

        prepare.assert_not_called()
        factory.assert_not_called()
        self.assertEqual(stopped, ["用户停止"])

    def test_amplifier_stop_during_run_contract_validation_skips_assembly(self):
        """运行契约校验期间收到停止请求时，不得继续装配测量用例。"""
        worker = AmplifierWorker("config.json", sleep_fn=lambda _: None)
        stopped = []
        worker.signals.stopped.connect(stopped.append)
        worker.prepared_run = _StopDuringValidation(worker)

        with patch("app.gui_runtime.create_amplifier_measurement") as factory:
            worker.run()

        factory.assert_not_called()
        self.assertEqual(stopped, ["用户停止"])

    def test_amplifier_stop_after_assembly_cancels_service_without_scan(self):
        worker = AmplifierWorker(
            "config.json",
            sleep_fn=lambda _: None,
            prepared_run=self._contract_run(),
        )
        service = _AmplifierService()
        stopped = []
        worker.signals.stopped.connect(stopped.append)

        def create_and_stop(*_args, **_kwargs):
            worker.stop()
            return service

        with patch(
            "app.gui_runtime.create_amplifier_measurement", side_effect=create_and_stop
        ):
            worker.run()

        self.assertTrue(service.stop_called.is_set())
        self.assertFalse(service.measure_called.is_set())
        self.assertEqual(stopped, ["用户停止"])


    def test_instrument_worker_closes_port_if_handoff_fails(self):
        class _Port:
            def __init__(self):
                self.close_calls = []

            def close_all(self, *, close_rf=False):
                self.close_calls.append(close_rf)

        port = _Port()
        worker = InstrumentWorker("config.json", sleep_fn=lambda _seconds: (_ for _ in ()).throw(OSError("handoff failed")))
        with patch("app.gui_runtime.connect_instruments", return_value=port):
            worker.run()
        self.assertEqual(port.close_calls, [True])
        self.assertIsNone(worker.measurement_port)

    def test_instrument_worker_accepts_explicit_connect_factory(self):
        class _Port:
            def close_all(self, *, close_rf=False):
                raise AssertionError("成功交接不应清理端口")

        port = _Port()
        results = []
        worker = InstrumentWorker(
            "config.json",
            sleep_fn=lambda _seconds: None,
            connect_factory=lambda path: (self.assertEqual(path, "config.json"), port)[1],
        )
        worker.signals.result.connect(results.append)
        worker.run()

        self.assertEqual(results, [port])
        self.assertIs(worker.measurement_port, port)

    def test_shared_instrument_worker_stops_after_connecting_before_handoff(self):
        class _Port:
            def __init__(self):
                self.close_calls = []

            def close_all(self, *, close_rf=False):
                self.close_calls.append(close_rf)

        port = _Port()
        worker = InstrumentWorker("config.json", sleep_fn=lambda _seconds: None)
        worker.stop()
        stopped = []
        worker.signals.stopped.connect(stopped.append)
        with patch("app.gui_runtime.connect_instruments", return_value=port):
            worker.run()
        self.assertEqual(port.close_calls, [True])
        self.assertIsNone(worker.measurement_port)
        self.assertEqual(stopped, ["用户停止"])


if __name__ == "__main__":
    unittest.main()
