"""旧 ``amplifier_measurement`` 入口的停用守卫测试。

正式实现位于 :class:`application.measurements.services.AmplifierMeasurementService`
与 :class:`application.measurements.amplifier_test.AmplifierMeasurementUseCase`。
旧装配适配器在步骤 4 删除前必须立即失败，不得读取配置、连接仪器或写结果。
"""

import sys
import unittest
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import amplifier_measurement
from app.legacy_amplifier_measurement import AmplifierMeasurement


class LegacyAmplifierEntryGuardTests(unittest.TestCase):
    def test_forwarder_exposes_the_same_guard_class(self):
        self.assertIs(amplifier_measurement.AmplifierMeasurement, AmplifierMeasurement)

    def test_constructor_fails_before_loading_or_storage(self):
        with self.assertRaisesRegex(RuntimeError, "旧入口已停用"):
            AmplifierMeasurement(measurement_port=object())


if __name__ == "__main__":
    unittest.main()
