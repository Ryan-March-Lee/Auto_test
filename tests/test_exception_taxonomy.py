r"""门禁：异常分类的语义、归类与核心路径的异常收窄。

本文件与[异常分类与静态质量门禁](../docs/异常分类与静态质量门禁.md)一一对应：

- 六类异常基类存在且各自声明 `category`；
- 生产代码里已有的异常类型都归入正确类别，并保留历史上的内建基类；
- 清理聚合异常逐项记录失败动作（不是只留最后一条消息）；
- 核心路径不再用 `except Exception` 把无关失败改写成设备清理错误。

验证方式：

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" -m unittest tests.test_exception_taxonomy -v
```
"""

import unittest
from unittest.mock import patch

from application.errors import (
    AutoTestError,
    CleanupError,
    ConfigurationError,
    DeviceCapabilityError,
    DeviceConnectionError,
    ErrorCategory,
    FailureRecord,
    MeasurementError,
    ProtocolError,
    cause_of,
)
from application.export_service import ExportRequestError
from application.measurements import AmplifierCalibrationError
from application.ports.measurement_session import (
    CleanupRecord,
    CleanupStepStatus,
    SafetyCleanupError,
)
from application.run_service import RunNotResumable
from application.run_stages import StageInputUnavailable
from instrument.action import PowerSupplyActions
from instrument.drivers.spectrum_analyzer import SpectrumAnalyzerMeasurementError
from instrument.flow import (
    PowerOffFlow,
    PowerOffFlowError,
    PowerOnFlow,
    PowerOnFlowError,
    SafetyShutdownError,
)
from instrument.flow.cleanup import cleanup_measurement
from instrument.measurement_adapter import PortMeasurementAdapter
from instrument.ports import InstrumentCapabilityError
from instrument.power_control import PowerControlError
from instrument.session import InstrumentSessionError, ManagedInstrumentSession
from instrument.simulation import (
    CommandRecorder,
    SimulatedPowerSupply,
    SimulatedSignalGenerator,
    SimulatedSpectrumAnalyzer,
)
from instrument.safety import InstrumentSafetyError, shutdown_instruments
from instrument.transport.scpi_transport import ScpiTransportError, ScpiTransportTimeoutError
from presentation.qt.config_projection import ConfigBackfillError
from presentation.qt.configuration_authoring import ConfigurationAuthoringError


class ErrorCategoryTests(unittest.TestCase):
    """六类异常基类与 `category` 对应关系。"""

    def test_every_category_is_defined_by_one_base_class(self):
        expected = {
            ConfigurationError: ErrorCategory.CONFIGURATION,
            DeviceConnectionError: ErrorCategory.CONNECTION,
            ProtocolError: ErrorCategory.PROTOCOL,
            DeviceCapabilityError: ErrorCategory.CAPABILITY,
            MeasurementError: ErrorCategory.MEASUREMENT,
            CleanupError: ErrorCategory.CLEANUP,
        }
        self.assertEqual(
            {base: base.category for base in expected},
            expected,
            "每个分类基类必须声明自己的 category",
        )
        self.assertIsNone(AutoTestError.category, "根异常不属于任何具体分类")
        self.assertEqual(
            {category.value for category in ErrorCategory},
            {"configuration", "connection", "protocol", "capability", "measurement", "cleanup"},
        )

    def test_domain_value_errors_stay_out_of_the_cross_layer_taxonomy(self):
        """领域层不依赖上层：值对象错误保持自己的基类，由应用层在边界处翻译。"""
        from domain.measurement.values import MeasurementUnitError, MeasurementValueError

        self.assertFalse(issubclass(MeasurementValueError, AutoTestError))
        self.assertFalse(issubclass(MeasurementUnitError, AutoTestError))
        self.assertTrue(issubclass(MeasurementValueError, ValueError))


class ExistingErrorClassificationTests(unittest.TestCase):
    """生产代码已有的异常类型必须归入正确类别。"""

    def test_instrument_errors_are_classified(self):
        self.assertIs(ScpiTransportError.category, ErrorCategory.PROTOCOL)
        self.assertIs(ScpiTransportTimeoutError.category, ErrorCategory.PROTOCOL)
        self.assertIs(InstrumentCapabilityError.category, ErrorCategory.CAPABILITY)
        self.assertIs(SpectrumAnalyzerMeasurementError.category, ErrorCategory.MEASUREMENT)
        self.assertIs(PowerOnFlowError.category, ErrorCategory.MEASUREMENT)
        for error_type in (SafetyShutdownError, InstrumentSafetyError, InstrumentSessionError):
            self.assertIs(error_type.category, ErrorCategory.CLEANUP)
        for error_type in (PowerOffFlowError,):
            self.assertIs(error_type.category, ErrorCategory.CLEANUP)

    def test_application_and_presentation_errors_are_classified(self):
        for error_type in (
            AmplifierCalibrationError,
            StageInputUnavailable,
            RunNotResumable,
            ConfigurationAuthoringError,
            ConfigBackfillError,
        ):
            self.assertIs(error_type.category, ErrorCategory.CONFIGURATION)

    def test_historical_builtin_bases_are_preserved(self):
        """分类是叠加：既有 except RuntimeError / except ValueError 处理器继续生效。"""
        self.assertTrue(issubclass(ScpiTransportError, RuntimeError))
        self.assertTrue(issubclass(ScpiTransportTimeoutError, TimeoutError))
        self.assertTrue(issubclass(InstrumentCapabilityError, RuntimeError))
        self.assertTrue(issubclass(SafetyCleanupError, RuntimeError))
        self.assertTrue(
            issubclass(StageInputUnavailable, RuntimeError),
            "StageInputUnavailable 归类为配置失败，但分类之前就是 RuntimeError",
        )
        self.assertTrue(issubclass(StageInputUnavailable, ConfigurationError))
        for error_type in (
            InstrumentSafetyError,
            InstrumentSessionError,
            PowerOffFlowError,
            PowerOnFlowError,
            PowerControlError,
        ):
            self.assertTrue(issubclass(error_type, RuntimeError), error_type.__name__)
        self.assertTrue(issubclass(SpectrumAnalyzerMeasurementError, ValueError))
        self.assertTrue(issubclass(AmplifierCalibrationError, ValueError))
        self.assertTrue(issubclass(RunNotResumable, ValueError))
        self.assertTrue(issubclass(ConfigBackfillError, ValueError))
        self.assertFalse(issubclass(ExportRequestError, AutoTestError), "导出请求错误不属于六类")


class FailureAggregationTests(unittest.TestCase):
    """清理聚合异常逐项记录失败动作，并可直接落成运行记录。"""

    def test_failure_record_describes_action_and_message(self):
        record = FailureRecord("rf_output", RuntimeError("device busy"))
        self.assertEqual(record.describe(), "rf_output: device busy")
        self.assertEqual(FailureRecord("resources", RuntimeError("")).message(), "RuntimeError")
        self.assertEqual(FailureRecord("", "text only").describe(), "text only")

    def test_cleanup_error_exposes_actions_errors_and_document(self):
        failures = (
            FailureRecord("rf_output", RuntimeError("rf")),
            FailureRecord("resources", RuntimeError("close")),
        )
        error = CleanupError("安全清理存在失败", failures)
        self.assertEqual(error.actions, ("rf_output", "resources"))
        self.assertEqual([str(item) for item in error.errors], ["rf", "close"])
        self.assertEqual(
            error.to_dict(),
            {
                "category": "cleanup",
                "message": "安全清理存在失败: rf_output: rf; resources: close",
                "failures": [
                    {"action": "rf_output", "message": "rf"},
                    {"action": "resources", "message": "close"},
                ],
            },
        )
        self.assertIs(cause_of(failures), failures[0].error)
        self.assertIsNone(cause_of((FailureRecord("resources", "text"),)))

    def test_safety_cleanup_error_covers_text_only_records(self):
        """只有文本消息的记录同样进入消息与 errors，不被静默丢弃。"""
        record = CleanupRecord(
            rf_output=CleanupStepStatus.PERFORMED,
            power_supplies=CleanupStepStatus.FAILED,
            resources=CleanupStepStatus.PERFORMED,
            errors=("power", "close"),
        )
        error = SafetyCleanupError(record)
        self.assertIs(error.record, record)
        self.assertEqual(error.errors, ("power", "close"))
        self.assertTrue(str(error).startswith("测量安全清理存在失败"))
        self.assertIn("power", str(error))
        self.assertIn("close", str(error))

    def test_shutdown_aggregates_every_action(self):
        with self.assertRaises(InstrumentSafetyError) as raised:
            shutdown_instruments(
                rf_off=lambda: (_ for _ in ()).throw(RuntimeError("rf")),
                power_off=lambda: (_ for _ in ()).throw(RuntimeError("power")),
                close=lambda **_flags: [RuntimeError("close"), RuntimeError("close-2")],
            )
        self.assertEqual(
            raised.exception.actions,
            ("rf_output", "power_supplies", "resources", "resources"),
        )
        self.assertEqual(
            [str(item) for item in raised.exception.errors],
            ["rf", "power", "close", "close-2"],
        )

    def test_legacy_cleanup_measurement_raises_the_classified_aggregate(self):
        class _Controller:
            signal_gen = object()

            def rf_output_off(self):
                raise RuntimeError("rf")

            def close_all(self, *, close_rf):  # pragma: no cover - RF 失败后必须保留连接
                raise AssertionError("RF 未确认关闭时不得释放连接")

        with self.assertRaises(InstrumentSafetyError) as raised:
            cleanup_measurement(_Controller())
        self.assertEqual(raised.exception.actions, ("rf_output",))
        self.assertIn("测量安全清理存在失败", str(raised.exception))


class LegacyAggregateCompatibilityTests(unittest.TestCase):
    """分类前的聚合异常以 ``(message, errors)`` 构造，旧调用方必须继续可用。"""

    def test_aggregate_errors_accept_exception_lists(self):
        for error_type in (
            InstrumentSafetyError,
            InstrumentSessionError,
            PowerOffFlowError,
            PowerOnFlowError,
            PowerControlError,
        ):
            with self.subTest(error_type=error_type.__name__):
                error = error_type("安全清理失败", [RuntimeError("rf"), "文字说明"])
                self.assertEqual(
                    [str(item) for item in error.errors], ["rf", "文字说明"]
                )
                self.assertEqual(error.actions, ("", ""), "未提供动作名时留空，不猜测")
                self.assertIn("安全清理失败", str(error))
                self.assertIn("rf", str(error))
                self.assertIn("文字说明", str(error))

    def test_cleanup_record_failures_imply_failure(self):
        """只填 failures 的记录同样算失败，不会被读成“清理成功”。"""
        record = CleanupRecord(
            rf_output=CleanupStepStatus.PERFORMED,
            power_supplies=CleanupStepStatus.FAILED,
            resources=CleanupStepStatus.PERFORMED,
            failures=(FailureRecord("power_supplies", RuntimeError("power")),),
        )
        self.assertEqual(record.errors, ("power",))
        self.assertFalse(record.succeeded)
        error = SafetyCleanupError(record)
        self.assertEqual(error.actions, ("power_supplies",))
        self.assertEqual([str(item) for item in error.errors], ["power"])

    def test_stage_input_unavailable_is_configuration_and_runtime_error(self):
        self.assertTrue(issubclass(StageInputUnavailable, ConfigurationError))
        self.assertTrue(issubclass(StageInputUnavailable, RuntimeError))
        for expected in (RuntimeError, ValueError):
            with self.assertRaises(expected):
                raise StageInputUnavailable("阶段输入不可用")


class CorePathNarrowingTests(unittest.TestCase):
    """核心路径不再用宽泛捕获把无关失败改写成设备清理错误。"""

    def test_session_close_only_translates_safety_errors(self):
        """安全清理之外的异常原样上抛，不再被包装成 InstrumentSessionError。"""
        recorder = CommandRecorder()
        session = ManagedInstrumentSession(
            SimulatedSignalGenerator(recorder),
            SimulatedSpectrumAnalyzer(recorder=recorder),
            SimulatedPowerSupply(recorder),
            {"gate": "A", "drain": "B"},
        )
        session.validate()
        session.connect()
        session.prepare()

        with patch(
            "instrument.session.shutdown_instruments", side_effect=ValueError("配置错误")
        ):
            with self.assertRaises(ValueError):
                session.close()

    def test_adapter_power_failure_keeps_rf_off_and_propagates_the_error(self):
        recorder = CommandRecorder()
        signal = SimulatedSignalGenerator(recorder)
        session = ManagedInstrumentSession(
            signal,
            SimulatedSpectrumAnalyzer(recorder=recorder),
            SimulatedPowerSupply(recorder),
            {"gate": "A", "drain": "B"},
        )
        session.validate()
        session.connect()
        session.prepare()
        session.power_on()
        adapter = PortMeasurementAdapter(session)
        adapter.rf_output_on()
        self.assertTrue(signal.rf_enabled)

        # 驱动原始异常必须原样上抛，不能被适配器改写成新的运行时错误。
        signal.set_power_dbm = lambda *args, **kwargs: (_ for _ in ()).throw(
            ValueError("原始失败")
        )
        with self.assertRaises(ValueError):
            adapter.set_power(-10)

        self.assertFalse(signal.rf_enabled, "功率写入失败后不得重新打开 RF")

    def test_driver_close_narrows_to_transport_errors(self):
        """关闭失败只翻译 SCPI 传输错误；其他异常原样上抛。"""

        class _Transport:
            def __init__(self):
                self.fail = None

            def set_timeout_s(self, timeout_s):
                pass

            def write(self, command):
                if self.fail is not None:
                    raise self.fail
                return None

            def query(self, command):  # pragma: no cover - close 不需要查询
                return "0"

            def close(self):
                if self.fail is not None:
                    raise self.fail

        from instrument.drivers.signal_generator import SignalGeneratorDriver

        transport = _Transport()
        driver = SignalGeneratorDriver(transport)
        driver.connect()
        driver.set_frequency_hz(1e9)
        driver.set_rf_enabled(True)

        transport.fail = ValueError("协议外的失败")
        with self.assertRaises(ValueError):
            driver.close()

        transport = _Transport()
        driver = SignalGeneratorDriver(transport)
        driver.connect()
        driver.set_frequency_hz(1e9)
        driver.set_rf_enabled(True)
        transport.fail = ScpiTransportError("传输失败", operation="write")
        with self.assertRaises(ScpiTransportError) as raised:
            driver.close()
        self.assertIn("信号源 RF 关闭失败", str(raised.exception))
        self.assertIs(
            raised.exception.category,
            ErrorCategory.PROTOCOL,
            "关闭失败仍是命令层失败，不能降级成无法归类的 RuntimeError",
        )
        self.assertIsInstance(raised.exception.__cause__, ScpiTransportError)
        self.assertEqual(raised.exception.operation, "close")


class PowerSupplyFlowAggregationTests(unittest.TestCase):
    """供电流程同样按动作记录失败。"""

    def setUp(self):
        self.recorder = CommandRecorder()
        self.supply = SimulatedPowerSupply(self.recorder)
        self.supply.connect()
        self.gate = PowerSupplyActions(self.supply, channel="A")
        self.drain = PowerSupplyActions(self.supply, channel="B")

    def test_power_on_failure_records_the_failed_step_and_cleanup(self):
        self.supply.inject_failure("output_on", once=True)
        flow = PowerOnFlow({"gate": self.gate, "drain": self.drain})

        with self.assertRaises(PowerOnFlowError) as raised:
            flow.run({
                "gate": {"voltage_v": 2, "current_a": 1},
                "drain": {"voltage_v": 20, "current_a": 1},
            })

        self.assertEqual(raised.exception.actions, ("gate.output_on",))

    def test_power_off_failure_records_every_failed_channel(self):
        self.supply.inject_failure("output_off", once=True)
        flow = PowerOffFlow({"gate": self.gate, "drain": self.drain})

        with self.assertRaises(PowerOffFlowError) as raised:
            flow.run()

        self.assertEqual(raised.exception.actions, ("drain.output_off",))


if __name__ == "__main__":
    unittest.main()
