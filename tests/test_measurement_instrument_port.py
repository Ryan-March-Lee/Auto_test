"""步骤 8：测量服务只接受显式设备协议，能力不足在构造阶段失败。

本文件把两件事固定为回归契约：

1. :class:`instrument.ports.MeasurementInstrumentPort` 覆盖三个正式测量服务实际
   调用的全部设备方法，能力集合按服务显式声明；
2. 服务构造阶段校验能力、运行 ID 和可选注入依赖，不再用 ``getattr`` 在测量
   途中猜测缺失属性，也不接受未声明的动态设备对象。
"""

import ast
import time
import unittest
from pathlib import Path

from application.dto.measurement_requests import MeasurementRequest
from application.measurements.services import (
    AmplifierMeasurementService,
    CableLossService,
    DriverPowerMappingService,
)
from instrument.ports import (
    DRIVER_POWER_CAPABILITIES,
    DUT_POWER_CAPABILITIES,
    SIGNAL_MEASUREMENT_CAPABILITIES,
    InstrumentCapabilityError,
    MeasurementInstrumentPort,
)
from tests.fakes import RecordingSafetyCleanup

SERVICES_SOURCE = (
    Path(__file__).resolve().parents[1] / "application" / "measurements" / "services.py"
)

CABLE_LOSS_CONFIG = {"test_frequencies": [1.0], "attenuator": {"type": "0dB"}}
DRIVER_MAPPING_CONFIG = {
    "test_frequencies": [1.0],
    "attenuator": {"type": "0dB"},
    "signal_source": {"start_power": -20, "stop_power": -20, "step": 1},
}
AMPLIFIER_CONFIG = {
    "test_frequencies": [1.0],
    "attenuator": {"type": "0dB"},
    "signal_source": {"start_power": -20, "stop_power": -20, "step": 1},
    "compression_point": {"type": "1dB"},
    "power_supply_assignment": {"dut_amplifier": {"supplies": {}}},
    "driver_mode": {"enabled": False},
}


class _FullInstrument:
    """实现三类测量全部能力的设备替身，并记录硬件动作。"""

    signal_gen = object()

    def __init__(self):
        self.operations = []

    def set_power(self, power):
        self.operations.append(("set_power", power))

    def set_frequency(self, frequency):
        self.operations.append(("set_frequency", frequency))

    def set_center_frequency(self, frequency):
        self.operations.append(("set_center_frequency", frequency))

    def set_span(self, span):
        self.operations.append(("set_span", span))

    def rf_output_on(self):
        self.operations.append(("rf_output_on",))

    def rf_output_off(self):
        self.operations.append(("rf_output_off",))

    def measure_power_with_average(self):
        self.operations.append(("measure_power",))
        return -10.0

    def setup_driver_amplifier_power(self):
        self.operations.append(("setup_driver_amplifier_power",))

    def power_on_driver(self):
        self.operations.append(("power_on_driver",))

    def power_off_driver(self):
        self.operations.append(("power_off_driver",))

    def setup_dut_power(self):
        self.operations.append(("setup_dut_power",))

    def power_on_sequence(self):
        self.operations.append(("power_on_sequence",))

    def power_off_sequence(self):
        self.operations.append(("power_off_sequence",))

    def read_voltage(self, _name, _channel):
        return 5.0

    def read_current(self, _name, _channel):
        return 1.0

    def close_all(self, close_rf=False):
        return []


class _MissingCapabilities:
    """隐藏指定能力，用于验证构造阶段的能力校验。"""

    def __init__(self, instrument, *hidden):
        self._instrument = instrument
        self._hidden = set(hidden)

    def __getattr__(self, name):
        if name in self._hidden:
            raise AttributeError(name)
        return getattr(self._instrument, name)


def _build(service_type, instrument, config, **kwargs):
    kwargs.setdefault("run_id", "run-step-8")
    kwargs.setdefault("safety_cleanup", RecordingSafetyCleanup())
    if service_type is DriverPowerMappingService:
        return service_type(config, instrument, {"cable_losses": {}}, **kwargs)
    if service_type is AmplifierMeasurementService:
        return service_type(config, instrument, {"cable_losses": {}}, None, **kwargs)
    return service_type(config, instrument, **kwargs)


SERVICE_CASES = (
    (CableLossService, CABLE_LOSS_CONFIG, SIGNAL_MEASUREMENT_CAPABILITIES),
    (
        DriverPowerMappingService,
        DRIVER_MAPPING_CONFIG,
        SIGNAL_MEASUREMENT_CAPABILITIES + DRIVER_POWER_CAPABILITIES,
    ),
    (
        AmplifierMeasurementService,
        AMPLIFIER_CONFIG,
        SIGNAL_MEASUREMENT_CAPABILITIES + DUT_POWER_CAPABILITIES,
    ),
)


class MeasurementInstrumentPortTests(unittest.TestCase):
    def test_protocol_covers_every_capability_the_services_call(self):
        tree = ast.parse(SERVICES_SOURCE.read_text(encoding="utf-8-sig"))
        used = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "inst_ctrl"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self"
        }
        declared = (
            set(SIGNAL_MEASUREMENT_CAPABILITIES)
            | set(DRIVER_POWER_CAPABILITIES)
            | set(DUT_POWER_CAPABILITIES)
        )
        protocol = {
            name for name in dir(MeasurementInstrumentPort) if not name.startswith("_")
        }

        self.assertEqual(used, declared, "服务调用的设备方法与声明的能力集合不一致")
        self.assertEqual(declared - protocol, set(), "MeasurementInstrumentPort 缺少设备方法")

    def test_full_instrument_satisfies_the_runtime_checkable_protocol(self):
        self.assertIsInstance(_FullInstrument(), MeasurementInstrumentPort)
        self.assertNotIsInstance(object(), MeasurementInstrumentPort)

    def test_port_adapter_satisfies_the_measurement_protocol(self):
        from instrument.measurement_adapter import PortMeasurementAdapter
        from instrument.simulation import (
            CommandRecorder,
            SimulatedPowerSupply,
            SimulatedSignalGenerator,
            SimulatedSpectrumAnalyzer,
        )
        from instrument.session import ManagedInstrumentSession

        recorder = CommandRecorder()
        session = ManagedInstrumentSession(
            SimulatedSignalGenerator(recorder),
            SimulatedSpectrumAnalyzer(recorder=recorder),
            SimulatedPowerSupply(recorder),
            {"gate": "A", "drain": "B"},
        )
        self.assertIsInstance(PortMeasurementAdapter(session), MeasurementInstrumentPort)

    def test_measurement_request_annotates_the_instrument_protocol(self):
        self.assertEqual(
            MeasurementRequest.__annotations__["measurement_port"],
            "MeasurementInstrumentPort",
        )


class ServiceCapabilityValidationTests(unittest.TestCase):
    def test_services_require_an_explicit_run_id(self):
        for service_type, config, _capabilities in SERVICE_CASES:
            for run_id in ("", "   ", None):
                with self.subTest(service=service_type.__name__, run_id=run_id):
                    with self.assertRaisesRegex(ValueError, "运行 ID"):
                        _build(service_type, _FullInstrument(), config, run_id=run_id)

    def test_service_rejects_dynamic_device_object(self):
        for service_type, config, capabilities in SERVICE_CASES:
            with self.subTest(service=service_type.__name__):
                with self.assertRaises(InstrumentCapabilityError) as raised:
                    _build(service_type, object(), config)

                self.assertEqual(raised.exception.requirement, service_type.__name__)
                self.assertEqual(raised.exception.missing, capabilities)

    def test_service_rejects_instrument_missing_one_capability(self):
        for service_type, config, capabilities in SERVICE_CASES:
            for capability in capabilities:
                with self.subTest(service=service_type.__name__, missing=capability):
                    instrument = _FullInstrument()
                    with self.assertRaises(InstrumentCapabilityError) as raised:
                        _build(
                            service_type,
                            _MissingCapabilities(instrument, capability),
                            config,
                        )

                    self.assertEqual(raised.exception.missing, (capability,))
                    self.assertEqual(instrument.operations, [], "能力校验不得触达硬件")

    def test_amplifier_requires_driver_power_only_in_driver_mode(self):
        hidden = _MissingCapabilities(_FullInstrument(), *DRIVER_POWER_CAPABILITIES)

        _build(AmplifierMeasurementService, hidden, AMPLIFIER_CONFIG)

        enabled_config = dict(AMPLIFIER_CONFIG, driver_mode={"enabled": True})
        with self.assertRaises(InstrumentCapabilityError) as raised:
            _build(AmplifierMeasurementService, hidden, enabled_config)
        self.assertEqual(raised.exception.missing, DRIVER_POWER_CAPABILITIES)

    def test_service_rejects_invalid_optional_injections_at_construction(self):
        for keyword in ("event_sink", "sleep_fn", "result_repository", "cancellation_token"):
            with self.subTest(keyword=keyword):
                with self.assertRaises(TypeError):
                    _build(
                        CableLossService,
                        _FullInstrument(),
                        CABLE_LOSS_CONFIG,
                        **{keyword: object()},
                    )


class ExplicitDependencyInjectionTests(unittest.TestCase):
    def test_service_keeps_injected_sleep_and_result_saver(self):
        deltas = []
        saved = []
        service = _build(
            CableLossService,
            _FullInstrument(),
            CABLE_LOSS_CONFIG,
            sleep_fn=deltas.append,
            result_repository=saved.append,
        )

        service.sleep_fn(1.5)
        service._save({"ok": True})

        self.assertEqual(deltas, [1.5])
        self.assertEqual(saved, [{"ok": True}])

    def test_service_uses_module_sleep_when_none_is_injected(self):
        service = _build(CableLossService, _FullInstrument(), CABLE_LOSS_CONFIG)

        self.assertIs(service.sleep_fn, time.sleep)

    def test_service_source_has_no_getattr_self_fallback(self):
        tree = ast.parse(SERVICES_SOURCE.read_text(encoding="utf-8-sig"))
        offenders = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "self"
        ]

        self.assertEqual(offenders, [], f"服务仍存在 getattr(self, ...) 后备: {offenders}")


if __name__ == "__main__":
    unittest.main()
