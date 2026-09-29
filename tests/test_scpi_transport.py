import unittest
from unittest.mock import patch

from pyvisa import constants, errors

from instrument.transport import (
    MockScpiTransport,
    ScpiTransportError,
    ScpiTransportTimeoutError,
    VisaScpiTransport,
)


class MockScpiTransportTests(unittest.TestCase):
    def test_records_order_and_returns_configured_response(self):
        transport = MockScpiTransport({"MEAS?": "1.25"})
        transport.write("CONF")
        self.assertEqual(transport.query("MEAS?"), "1.25")
        transport.write("OUTP OFF")
        self.assertEqual(
            transport.operations,
            [("write", "CONF"), ("query", "MEAS?"), ("write", "OUTP OFF")],
        )

    def test_missing_response_is_explicit_transport_error(self):
        with self.assertRaisesRegex(ScpiTransportError, "未配置 SCPI 查询响应"):
            MockScpiTransport().query("*IDN?")

    def test_rejects_empty_commands_before_recording_an_operation(self):
        transport = MockScpiTransport()
        with self.assertRaises(ValueError):
            transport.write("  ")
        with self.assertRaises(ValueError):
            transport.query("")
        self.assertEqual(transport.operations, [])

    def test_mock_can_inject_a_specific_write_exception_with_context(self):
        cause = RuntimeError("device rejected command")
        transport = MockScpiTransport(fail_on_write=cause)
        with self.assertRaises(ScpiTransportError) as raised:
            transport.write("CONF")
        self.assertEqual(raised.exception.operation, "write")
        self.assertIs(raised.exception.original_error, cause)

    def test_mock_preserves_injected_timeout_type_with_context(self):
        cause = ScpiTransportTimeoutError("injected timeout")
        transport = MockScpiTransport(fail_on_query=cause)
        with self.assertRaises(ScpiTransportTimeoutError) as raised:
            transport.query("MEAS?")
        self.assertEqual(raised.exception.operation, "query")
        self.assertEqual(raised.exception.command_summary, "MEAS?")
        self.assertIs(raised.exception.original_error, cause)

    def test_query_response_must_be_text(self):
        with self.assertRaises(ScpiTransportError):
            MockScpiTransport({"MEAS?": 1.25}).query("MEAS?")

    def test_timeout_is_configurable_without_sleeping(self):
        transport = MockScpiTransport(timeout_s=1.5, timeout_on_query="MEAS?")
        with self.assertRaisesRegex(ScpiTransportTimeoutError, "1.5s"):
            transport.query("MEAS?")
        self.assertEqual(transport.operations, [("query", "MEAS?")])

    def test_timeout_can_be_changed_for_the_next_operation(self):
        transport = MockScpiTransport()
        transport.set_timeout_s(1.25)
        self.assertEqual(transport.timeout_s, 1.25)
        with self.assertRaises(ValueError):
            transport.set_timeout_s(float("inf"))

    def test_close_is_idempotent(self):
        transport = MockScpiTransport()
        transport.close()
        transport.close()
        self.assertTrue(transport.closed)
        self.assertEqual(transport.close_count, 1)

    def test_failed_close_is_terminal_and_is_not_retried(self):
        transport = MockScpiTransport(fail_on_close=True)
        with self.assertRaises(ScpiTransportError):
            transport.close()
        with self.assertRaises(ScpiTransportError):
            transport.write("AFTER CLOSE")
        transport.close()
        self.assertTrue(transport.closed)
        self.assertEqual(transport.close_count, 1)

    def test_mock_does_not_construct_resource_manager(self):
        with patch("pyvisa.ResourceManager", side_effect=AssertionError("VISA")):
            transport = MockScpiTransport({"*IDN?": "MOCK"})
            self.assertEqual(transport.query("*IDN?"), "MOCK")


class VisaScpiTransportTests(unittest.TestCase):
    class Resource:
        def __init__(self):
            self.timeout = None
            self.calls = []
            self.response = "OK"

        def write(self, command):
            self.calls.append(("write", command))

        def query(self, command):
            self.calls.append(("query", command))
            return self.response

        def close(self):
            self.calls.append(("close",))

    def test_wraps_resource_and_configures_timeout(self):
        resource = self.Resource()
        transport = VisaScpiTransport(resource, timeout_s=2.5)
        transport.write("CONF")
        self.assertEqual(transport.query("MEAS?"), "OK")
        self.assertEqual(resource.timeout, 2500)
        self.assertEqual(resource.calls[:2], [("write", "CONF"), ("query", "MEAS?")])

    def test_rejects_empty_commands_before_touching_resource(self):
        resource = self.Resource()
        transport = VisaScpiTransport(resource)
        with self.assertRaises(ValueError):
            transport.write(" ")
        with self.assertRaises(ValueError):
            transport.query("")
        self.assertEqual(resource.calls, [])

    def test_updates_resource_timeout_for_each_operation_timeout(self):
        resource = self.Resource()
        transport = VisaScpiTransport(resource, timeout_s=2.5)
        transport.set_timeout_s(1.25)
        self.assertEqual(resource.timeout, 1250)
        self.assertEqual(transport.timeout_s, 1.25)

    def test_translates_timeout_and_closes_once(self):
        resource = self.Resource()

        def timeout(_command):
            raise TimeoutError("timed out")

        resource.query = timeout
        transport = VisaScpiTransport(resource)
        with self.assertRaises(ScpiTransportTimeoutError):
            transport.query("MEAS?")
        transport.close()
        transport.close()
        self.assertEqual(transport.close_count, 1)

    def test_translates_pyvisa_timeout_error_code(self):
        resource = self.Resource()
        resource.query = lambda _command: (_ for _ in ()).throw(
            errors.VisaIOError(constants.StatusCode.error_timeout)
        )
        transport = VisaScpiTransport(resource)
        with self.assertRaises(ScpiTransportTimeoutError):
            transport.query("MEAS?")

    def test_translates_non_timeout_visa_error(self):
        resource = self.Resource()
        resource.write = lambda _command: (_ for _ in ()).throw(
            errors.VisaIOError(constants.StatusCode.error_system_error)
        )
        transport = VisaScpiTransport(resource)
        with self.assertRaises(ScpiTransportError) as raised:
            transport.write("CONF")
        self.assertNotIsInstance(raised.exception, ScpiTransportTimeoutError)
        self.assertEqual(raised.exception.operation, "write")
        self.assertEqual(raised.exception.command_summary, "CONF")
        self.assertIsNotNone(raised.exception.original_error)
        self.assertNotIn("-1073807298", str(raised.exception))

    def test_failed_close_is_terminal_and_is_not_retried(self):
        resource = self.Resource()
        close_calls = []

        def close():
            close_calls.append(1)
            raise RuntimeError("close failed")

        resource.close = close
        transport = VisaScpiTransport(resource)
        with self.assertRaises(ScpiTransportError):
            transport.close()
        transport.close()
        self.assertTrue(transport.closed)
        self.assertEqual(transport.close_count, 1)
        self.assertEqual(len(close_calls), 1)

    def test_operations_after_close_are_rejected(self):
        resource = self.Resource()
        transport = VisaScpiTransport(resource)
        transport.close()
        with self.assertRaises(ScpiTransportError):
            transport.write("AFTER CLOSE")
        with self.assertRaises(ScpiTransportError):
            transport.query("AFTER CLOSE")

    def test_timeout_configuration_failure_is_translated(self):
        class ResourceWithReadOnlyTimeout(self.Resource):
            def __init__(self):
                self.calls = []
                self.response = "OK"

            @property
            def timeout(self):
                return None

            @timeout.setter
            def timeout(self, _value):
                raise RuntimeError("timeout cannot be configured")

        with self.assertRaises(ScpiTransportError) as raised:
            VisaScpiTransport(ResourceWithReadOnlyTimeout())
        self.assertEqual(raised.exception.operation, "configure_timeout")
        self.assertIsNotNone(raised.exception.original_error)

    def test_query_rejects_non_text_resource_response(self):
        resource = self.Resource()
        resource.response = 1.25
        transport = VisaScpiTransport(resource)
        with self.assertRaises(ScpiTransportError):
            transport.query("MEAS?")


if __name__ == "__main__":
    unittest.main()
