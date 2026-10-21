"""``measurement_services`` 与 ``measurement_lifecycle`` 历史入口的兼容转发回归。

正式实现分别位于 :mod:`application.measurements.services` 与
:mod:`instrument.flow.cleanup`。根模块只允许重新导出历史符号，本测试确认转发
对象与正式对象同一，不再验证第二套实现。
"""

import unittest

import app.cancellation as cancellation
import app.events as events
import application.measurements.services as canonical_services
import domain.measurement.calculations as calculations
import instrument.flow.cleanup as flow_cleanup
import instrument.ports as instrument_ports
import measurement_lifecycle
import measurement_services
from application.measurements.services import (
    AmplifierMeasurementService,
    CableLossService,
    DriverPowerMappingService,
    NullEventSink,
    ResultRepository,
    _Service,
    _publish,
)

# 受支持的兼容 API 必须精确等于这组符号，避免兼容面被无意扩大或缩窄。
EXPECTED_SERVICE_EXPORTS = {
    "ResultRepository",
    "NullEventSink",
    "CableLossService",
    "DriverPowerMappingService",
    "AmplifierMeasurementService",
}


class MeasurementServiceCompatibilityTests(unittest.TestCase):
    def test_root_service_symbols_are_canonical_reexports(self):
        self.assertIs(measurement_services.CableLossService, CableLossService)
        self.assertIs(measurement_services.DriverPowerMappingService, DriverPowerMappingService)
        self.assertIs(measurement_services.AmplifierMeasurementService, AmplifierMeasurementService)
        self.assertIs(measurement_services.NullEventSink, NullEventSink)
        self.assertIs(measurement_services.ResultRepository, ResultRepository)

    def test_root_service_all_is_the_explicit_compatibility_contract(self):
        self.assertEqual(set(measurement_services.__all__), EXPECTED_SERVICE_EXPORTS)
        for name in measurement_services.__all__:
            self.assertTrue(hasattr(measurement_services, name), name)
            self.assertIs(getattr(measurement_services, name), getattr(canonical_services, name), name)

    def test_root_service_keeps_historical_aliases_without_duplicating_implementation(self):
        # 历史实现细节只做别名转发，不得在根模块重新定义实现。
        self.assertIs(measurement_services._Service, _Service)
        self.assertIs(measurement_services._publish, _publish)
        self.assertIs(measurement_services.cleanup_measurement, flow_cleanup.cleanup_measurement)
        self.assertIs(
            measurement_services.MeasurementInstrumentPort,
            instrument_ports.MeasurementInstrumentPort,
        )
        self.assertIs(measurement_services.CancellationToken, cancellation.CancellationToken)
        for name in (
            "CheckpointEvent",
            "EventSink",
            "MessageEvent",
            "ProgressEvent",
            "RealtimeDataEvent",
            "StoppedEvent",
        ):
            self.assertIs(getattr(measurement_services, name), getattr(events, name), name)
        for name in (
            "calculate_cable_losses",
            "calculate_compression_result",
            "calculate_dut_input_power",
            "calculate_efficiency",
            "calculate_gain",
            "compensate_amplifier_output_power",
            "compensate_driver_output_power",
        ):
            self.assertIs(getattr(measurement_services, name), getattr(calculations, name), name)

    def test_root_lifecycle_cleanup_is_canonical_reexport(self):
        self.assertIs(measurement_lifecycle.cleanup_measurement, flow_cleanup.cleanup_measurement)
        self.assertEqual(measurement_lifecycle.__all__, ["cleanup_measurement"])


if __name__ == "__main__":
    unittest.main()
