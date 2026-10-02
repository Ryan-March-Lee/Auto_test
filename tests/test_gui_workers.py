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
from enhanced_workers import InstrumentWorker as LegacyInstrumentWorker


class _CableService:
    def __init__(self, path1_done):
        self.path1_done = path1_done
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
        self.stop_called = threading.Event()
        self.measure_called = threading.Event()

    def stop_measurement(self):
        self.stop_called.set()

    def measure_all_frequencies(self):
        self.measure_called.set()


class _AmplifierService:
    def __init__(self):
        self.stop_called = threading.Event()
        self.measure_called = threading.Event()

    def stop_measurement(self):
        self.stop_called.set()

    def measure_all_frequencies(self):
        self.measure_called.set()


class GuiWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QCoreApplication.instance() or QCoreApplication([])

    def test_cable_loss_second_step_runs_in_worker_thread(self):
        path1_done = threading.Event()
        service = _CableService(path1_done)
        worker = CableLossWorker("config.json", sleep_fn=lambda _: None)

        prepared = object()
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
        worker = CableLossWorker("config.json", sleep_fn=lambda _: None)

        prepared = object()
        with patch("app.gui_runtime.prepare_configuration", return_value=prepared), \
                patch("app.gui_runtime.create_cable_loss_measurement", return_value=service):
            worker.start()
            self.assertTrue(path1_done.wait(1))
            worker.stop()
            worker.wait(1000)

        self.assertTrue(service.stop_called.is_set())
        self.assertFalse(worker.isRunning())

    def test_preflight_failure_prevents_measurement_service_creation(self):
        worker = CableLossWorker("config.json", sleep_fn=lambda _: None)
        errors = []
        worker.signals.error.connect(errors.append)

        with patch(
            "app.gui_runtime.prepare_configuration",
            side_effect=OSError("snapshot failed"),
        ), patch("app.gui_runtime.create_cable_loss_measurement") as factory:
            worker.run()

        factory.assert_not_called()
        self.assertIsNone(worker.service)
        self.assertEqual(errors, ["应用用例构造失败: snapshot failed"])

    def test_cable_loss_stop_after_assembly_emits_stopped(self):
        service = _CableService(threading.Event())
        stopped = []
        worker = CableLossWorker(
            "config.json",
            sleep_fn=lambda _: None,
            prepare_factory=lambda *_args, **_kwargs: object(),
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
        prepared = object()
        service = _DriverMappingService()
        calls = []

        def prepare(*args, **kwargs):
            calls.append((args, kwargs))
            return prepared

        def factory(*args, **kwargs):
            calls.append((args, kwargs))
            return service

        worker = DriverMappingWorker(
            "config.json",
            sleep_fn=lambda _: None,
            measurement_factory=factory,
            prepare_factory=prepare,
        )
        worker.run()

        self.assertIs(worker.service, service)
        self.assertTrue(service.measure_called.is_set())
        self.assertEqual(calls[0][1], {"operation": "driver_mapping"})
        self.assertIs(calls[1][1]["prepared_run"], prepared)

    def test_measurement_construction_failure_uses_application_error_signal(self):
        errors = []
        worker = AmplifierWorker(
            "config.json",
            prepare_factory=lambda *_args, **_kwargs: object(),
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
            prepare_factory=lambda *_args, **_kwargs: object(),
            measurement_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("assembly failed")
            ),
        )
        worker.signals.error.connect(errors.append)
        worker.run()

        self.assertEqual(errors, ["应用用例构造失败: assembly failed"])
        self.assertEqual(port.close_calls, [])
        self.assertIsNone(worker.measurement_port)

    def test_driver_mapping_stop_before_run_skips_preflight_and_measurement(self):
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

    def test_driver_mapping_stop_during_preflight_skips_measurement_creation(self):
        worker = DriverMappingWorker("config.json", sleep_fn=lambda _: None)
        stopped = []
        worker.signals.stopped.connect(stopped.append)

        def prepare_and_stop(*_args, **_kwargs):
            worker.stop()
            return object()

        with patch("app.gui_runtime.prepare_configuration", side_effect=prepare_and_stop), patch(
            "app.gui_runtime.create_driver_mapping_measurement"
        ) as factory:
            worker.run()

        factory.assert_not_called()
        self.assertEqual(stopped, ["用户停止"])
        self.assertIsNone(worker.service)

    def test_driver_mapping_stop_after_assembly_cancels_service_without_scan(self):
        worker = DriverMappingWorker("config.json", sleep_fn=lambda _: None)
        service = _DriverMappingService()
        stopped = []
        worker.signals.stopped.connect(stopped.append)

        def create_and_stop(*_args, **_kwargs):
            worker.stop()
            return service

        with patch("app.gui_runtime.prepare_configuration", return_value=object()), patch(
            "app.gui_runtime.create_driver_mapping_measurement", side_effect=create_and_stop
        ):
            worker.run()

        self.assertTrue(service.stop_called.is_set())
        self.assertFalse(service.measure_called.is_set())
        self.assertEqual(stopped, ["用户停止"])

    def test_amplifier_stop_before_preflight_skips_measurement(self):
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

    def test_amplifier_stop_during_preflight_skips_assembly(self):
        worker = AmplifierWorker("config.json", sleep_fn=lambda _: None)
        stopped = []
        worker.signals.stopped.connect(stopped.append)

        def prepare_and_stop(*_args, **_kwargs):
            worker.stop()
            return object()

        with patch("app.gui_runtime.prepare_configuration", side_effect=prepare_and_stop), patch(
            "app.gui_runtime.create_amplifier_measurement"
        ) as factory:
            worker.run()

        factory.assert_not_called()
        self.assertEqual(stopped, ["用户停止"])

    def test_amplifier_stop_after_assembly_cancels_service_without_scan(self):
        worker = AmplifierWorker("config.json", sleep_fn=lambda _: None)
        service = _AmplifierService()
        stopped = []
        worker.signals.stopped.connect(stopped.append)

        def create_and_stop(*_args, **_kwargs):
            worker.stop()
            return service

        with patch("app.gui_runtime.prepare_configuration", return_value=object()), patch(
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

    def test_legacy_instrument_worker_uses_composition_root_and_returns_port(self):
        class _Port:
            def __init__(self):
                self.close_calls = []

            def close_all(self, *, close_rf=False):
                self.close_calls.append(close_rf)

        port = _Port()
        worker = LegacyInstrumentWorker("config.json", sleep_fn=lambda _seconds: None)
        results = []
        with patch("app.gui_runtime.connect_instruments", return_value=port):
            worker.signals.result.connect(results.append)
            worker.run()
        self.assertEqual(results, [port])
        self.assertIs(worker.measurement_port, port)
        self.assertEqual(port.close_calls, [])

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
