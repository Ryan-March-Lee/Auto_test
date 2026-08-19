import unittest

from tests.fakes import FakeResourceManager, FakeVisaResource


class FakeVisaResourceTests(unittest.TestCase):
    def test_write_records_original_command(self):
        resource = FakeVisaResource("FAKE::PS")
        resource.write(":OUTPut CH1,ON")
        self.assertEqual(resource.writes, [":OUTPut CH1,ON"])
        self.assertEqual(resource.operations, [("write", ":OUTPut CH1,ON")])

    def test_query_returns_configured_response_and_records_command(self):
        resource = FakeVisaResource("FAKE::PS", {":MEASure:VOLTage? CH1": "2.800"})
        self.assertEqual(resource.query(":MEASure:VOLTage? CH1"), "2.800")
        self.assertEqual(resource.queries, [":MEASure:VOLTage? CH1"])

    def test_operations_preserve_write_query_order(self):
        resource = FakeVisaResource("FAKE::PS", {"MEAS?": "1.0"})
        resource.write("SET")
        resource.query("MEAS?")
        resource.write("RESET")
        self.assertEqual(
            resource.operations,
            [("write", "SET"), ("query", "MEAS?"), ("write", "RESET")],
        )

    def test_unknown_query_is_explicit_error(self):
        resource = FakeVisaResource("FAKE::PS")
        with self.assertRaisesRegex(KeyError, "未配置仿真查询响应"):
            resource.query("*IDN?")

    def test_write_and_query_failure_rules_are_injected(self):
        resource = FakeVisaResource("FAKE::PS", {"*IDN?": "fake"})
        resource.fail_on_write = lambda command: command.endswith("ON")
        resource.fail_on_query = "*IDN?"

        with self.assertRaisesRegex(RuntimeError, "仿真写入失败"):
            resource.write(":OUTPut CH1,ON")
        with self.assertRaisesRegex(RuntimeError, "仿真查询失败"):
            resource.query("*IDN?")
        self.assertEqual(resource.writes, [":OUTPut CH1,ON"])
        self.assertEqual(resource.queries, ["*IDN?"])

    def test_close_is_idempotent_when_no_failure_is_configured(self):
        resource = FakeVisaResource("FAKE::PS")
        resource.close()
        resource.close()
        self.assertTrue(resource.closed)
        self.assertEqual(resource.close_count, 1)

    def test_close_failure_is_injected(self):
        resource = FakeVisaResource("FAKE::PS")
        resource.fail_on_close = True
        with self.assertRaisesRegex(RuntimeError, "仿真关闭失败"):
            resource.close()
        self.assertEqual(resource.close_count, 1)
        self.assertFalse(resource.closed)


class FakeResourceManagerTests(unittest.TestCase):
    def setUp(self):
        self.resource = FakeVisaResource("FAKE::PS")
        self.manager = FakeResourceManager({"FAKE::PS": self.resource})

    def test_open_returns_registered_resource_and_records_address(self):
        self.assertIs(self.manager.open_resource("FAKE::PS"), self.resource)
        self.assertEqual(self.manager.opened_addresses, ["FAKE::PS"])

    def test_unknown_address_is_explicit_error(self):
        with self.assertRaisesRegex(KeyError, "未注册仿真 VISA 地址"):
            self.manager.open_resource("FAKE::UNKNOWN")
        self.assertEqual(self.manager.opened_addresses, ["FAKE::UNKNOWN"])

    def test_open_and_close_failure_rules_are_injected(self):
        self.manager.fail_on_open = "FAKE::PS"
        with self.assertRaisesRegex(RuntimeError, "仿真打开资源失败"):
            self.manager.open_resource("FAKE::PS")

        self.manager.fail_on_close = True
        with self.assertRaisesRegex(RuntimeError, "仿真 ResourceManager 关闭失败"):
            self.manager.close()
        self.assertFalse(self.manager.closed)

    def test_close_is_idempotent_when_no_failure_is_configured(self):
        self.manager.close()
        self.manager.close()
        self.assertTrue(self.manager.closed)
        self.assertEqual(self.manager.close_count, 1)

    def test_operations_record_open_order(self):
        self.manager.open_resource("FAKE::PS")
        self.manager.close()
        self.assertEqual(
            self.manager.operations,
            [("open_resource", "FAKE::PS")],
        )


if __name__ == "__main__":
    unittest.main()
