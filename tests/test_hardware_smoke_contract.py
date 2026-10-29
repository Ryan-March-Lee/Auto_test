import json
import unittest
import tempfile
from pathlib import Path

from hardware import smoke_gate as gate
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


class HardwareSmokeGateTests(unittest.TestCase):
    """安全门禁在打开仪器之前拦下缺项；它随测试一起运行，不依赖某个入口脚本。"""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "hardware").mkdir()

    def write_config(self, name="smoke_config.local.json", text=None, **overrides):
        config = {
            "environment": "hardware_smoke",
            "require_user_confirmation": True,
            "require_empty_setup": True,
            "devices": {},
        }
        config.update(overrides)
        path = self.root / "hardware" / name
        path.write_text(text if text is not None else json.dumps(config), encoding="utf-8")
        return path

    def environment(self, config_path, **overrides):
        values = {
            gate.CONFIG_VARIABLE: str(config_path),
            gate.ENABLED_VARIABLE: "1",
            gate.CONFIRM_VARIABLE: "1",
        }
        values.update(overrides)
        return values

    def assert_gate_rejects(self, environment, message=None):
        with self.assertRaises(gate.HardwareSmokeGateError) as caught:
            gate.load_gated_smoke_config(environment, self.root)
        if message:
            self.assertIn(message, str(caught.exception))

    def test_a_complete_configuration_passes_and_returns_the_config(self):
        config_path = self.write_config(devices={"signal_generator": {"address": "TCPIP::SG::INSTR"}})
        loaded_path, config = gate.load_gated_smoke_config(
            self.environment(config_path), self.root
        )
        self.assertEqual(loaded_path, config_path.resolve())
        self.assertEqual(config["environment"], "hardware_smoke")

    def test_the_configuration_variable_is_required(self):
        self.assert_gate_rejects({}, gate.ENTRY_POINT_HINT)

    def test_configuration_outside_the_hardware_directory_is_rejected(self):
        outside = self.root / "smoke_config.local.json"
        outside.write_text("{}", encoding="utf-8")
        self.assert_gate_rejects(self.environment(outside), "hardware/")

    def test_example_and_template_configurations_are_rejected(self):
        for name in ("smoke_config.example.json", "smoke_config.sample.json", "smoke_config.template.json"):
            with self.subTest(name=name):
                self.assert_gate_rejects(self.environment(self.write_config(name)))

    def test_missing_configuration_flags_are_rejected(self):
        cases = (
            ({"environment": "offline"}, "environment"),
            ({"require_user_confirmation": False}, "require_user_confirmation"),
            ({"require_empty_setup": False}, "require_empty_setup"),
        )
        for overrides, message in cases:
            with self.subTest(overrides=overrides):
                self.assert_gate_rejects(self.environment(self.write_config(**overrides)), message)

    def test_placeholder_addresses_are_rejected(self):
        config_path = self.write_config(
            devices={"signal_generator": {"address": "REPLACE_WITH_SIGNAL_GENERATOR"}}
        )
        self.assert_gate_rejects(self.environment(config_path), "占位符")

    def test_invalid_json_is_rejected(self):
        self.assert_gate_rejects(self.environment(self.write_config(text="{not json")))

    def test_enabled_confirm_and_ci_flags_are_enforced(self):
        config_path = self.write_config()
        cases = (
            ({gate.ENABLED_VARIABLE: "0"}, gate.ENABLED_VARIABLE),
            ({gate.CONFIRM_VARIABLE: "0"}, "--confirm-hardware-smoke"),
            ({"CI": "true"}, "CI"),
        )
        for overrides, message in cases:
            with self.subTest(overrides=overrides):
                self.assert_gate_rejects(self.environment(config_path, **overrides), message)

    def test_an_explicit_non_ci_marker_does_not_block_the_run(self):
        config_path = self.write_config()
        loaded_path, _ = gate.load_gated_smoke_config(
            self.environment(config_path, CI="false"), self.root
        )
        self.assertEqual(loaded_path, config_path.resolve())


if __name__ == "__main__":
    unittest.main()
