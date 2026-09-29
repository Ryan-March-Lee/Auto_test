import unittest
import tempfile
from pathlib import Path

from hardware.read_only_smoke import (
    SmokeExecutionError,
    resolve_report_path,
    run_read_only_smoke,
    write_report,
)


class _Resource:
    def __init__(self, responses):
        self.responses = responses
        self.commands = []
        self.timeout = None
        self.closed = False

    def query(self, command):
        self.commands.append(command)
        return self.responses[command]

    def close(self):
        self.closed = True


class _Manager:
    def __init__(self, resource):
        self.resource = resource
        self.addresses = []
        self.closed = False

    def open_resource(self, address, **kwargs):
        self.addresses.append((address, kwargs))
        return self.resource

    def close(self):
        self.closed = True


class HardwareSmokeContractTests(unittest.TestCase):
    def test_only_configured_read_queries_are_sent_and_resources_close(self):
        resource = _Resource({"*IDN?": "ACME,SG-1", "SYST:ERR?": "0,OK", "OUTP?": "0"})
        manager = _Manager(resource)
        report = run_read_only_smoke(
            {
                "devices": {
                    "signal_generator": {
                        "address": "TCPIP::SG::INSTR",
                        "model": "SG-1",
                        "queries": ["*IDN?", "SYST:ERR?", "OUTP?"],
                        "expected_responses": {"SYST:ERR?": "0", "OUTP?": "0"},
                    }
                }
            },
            manager,
            clock=iter([1.0, 1.01, 2.0, 2.01, 3.0, 3.01]).__next__,
        )
        self.assertEqual(resource.commands, ["*IDN?", "SYST:ERR?", "OUTP?"])
        self.assertTrue(resource.closed)
        self.assertTrue(manager.closed)
        self.assertTrue(report["resources_closed"])
        self.assertEqual(report["devices"]["signal_generator"]["queries"][1]["response"], "0,OK")

    def test_state_changing_commands_are_rejected_before_query(self):
        resource = _Resource({"*IDN?": "ACME,SG-1"})
        manager = _Manager(resource)
        with self.assertRaises(SmokeExecutionError):
            run_read_only_smoke(
                {"devices": {"sg": {"address": "sim::sg", "queries": ["OUTP ON"]}}},
                manager,
            )
        self.assertEqual(resource.commands, [])
        self.assertTrue(resource.closed)
        self.assertTrue(manager.closed)

    def test_expected_error_code_is_compared_exactly(self):
        resource = _Resource({"SYST:ERR?": "10,Error"})
        manager = _Manager(resource)
        with self.assertRaises(SmokeExecutionError):
            run_read_only_smoke(
                {"devices": {"sa": {
                    "address": "sim::sa",
                    "queries": ["SYST:ERR?"],
                    "expected_responses": {"SYST:ERR?": "0"},
                }}},
                manager,
            )
        self.assertTrue(resource.closed)
        self.assertTrue(manager.closed)

    def test_close_failure_fails_smoke_and_is_reported(self):
        resource = _Resource({"*IDN?": "ACME,SG-1"})
        resource.close = lambda: (_ for _ in ()).throw(OSError("resource close failed"))
        manager = _Manager(resource)
        with self.assertRaises(SmokeExecutionError) as caught:
            run_read_only_smoke(
                {"devices": {"sg": {"address": "sim::sg", "queries": ["*IDN?"]}}},
                manager,
            )
        self.assertFalse(caught.exception.report["resources_closed"])
        self.assertIn("close_errors", caught.exception.report)
        self.assertTrue(manager.closed)

    def test_failure_report_is_available_for_persistence(self):
        resource = _Resource({"SYST:ERR?": "1,Error"})
        manager = _Manager(resource)
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "failed.json"
            with self.assertRaises(SmokeExecutionError) as caught:
                run_read_only_smoke(
                    {"devices": {"sa": {
                        "address": "sim::sa", "queries": ["SYST:ERR?"],
                        "expected_responses": {"SYST:ERR?": "0"},
                    }}},
                    manager,
                )
            write_report(caught.exception.report, report_path)
            self.assertIn('"error":', report_path.read_text(encoding="utf-8"))

    def test_report_path_must_stay_under_hardware_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "hardware"
            root.mkdir()
            config_path = root / "smoke_config.local.json"
            self.assertEqual(
                resolve_report_path(config_path, "reports/result.json", root),
                (root / "reports/result.json").resolve(),
            )
            for invalid in ("../outside.json", str(Path(directory) / "absolute.json")):
                with self.assertRaises(ValueError):
                    resolve_report_path(config_path, invalid, root)

    def test_report_can_be_written_as_local_json(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "smoke-result.json"
            write_report({"devices": {}, "resources_closed": True}, path)
            self.assertIn('"resources_closed": true', path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
