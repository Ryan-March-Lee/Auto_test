import unittest

from hardware.safe_prepare_smoke import _expected_matches, run_safe_prepare_smoke
from hardware.read_only_smoke import SmokeExecutionError


class Resource:
    def __init__(self, responses, *, fail_writes=(), timeout_error=False):
        self.responses = responses
        self.commands = []
        self.closed = False
        self.fail_writes = set(fail_writes)
        self.timeout_error = timeout_error

    @property
    def timeout(self):
        return None

    @timeout.setter
    def timeout(self, value):
        if self.timeout_error:
            raise OSError("timeout setup failed")

    def query(self, command):
        self.commands.append(("query", command))
        return self.responses[command]

    def write(self, command):
        self.commands.append(("write", command))
        if command in self.fail_writes:
            raise OSError(f"write failed: {command}")

    def close(self):
        self.closed = True


class Manager:
    def __init__(self, resource):
        self.resource = resource
        self.closed = False

    def open_resource(self, address, **kwargs):
        if isinstance(self.resource, dict):
            return self.resource[address]
        return self.resource

    def close(self):
        self.closed = True


def config():
    return {
        "environment": "hardware_smoke", "smoke_mode": "safe_prepare", "max_action_count": 0,
        "devices": {
            "signal_generator": {"address": "sim::sg", "model": "SG-1", "setup_commands": ["FREQ 1000000", "POW -30"],
                "state_queries": [{"command": "OUTP?", "expected": "0"}], "cleanup_commands": ["OUTP OFF"]},
            "spectrum_analyzer": {"address": "sim::sa", "model": "SA-1", "setup_commands": ["FREQ:CENT 1000000"],
                "state_queries": [{"command": "FREQ:CENT?", "expected": "1000000"}], "cleanup_commands": ["*CLS"]},
            "power_supply": {"address": "sim::ps", "model": "PS-1", "setup_commands": ["VOLT 1"],
                "state_queries": [{"command": "OUTP?", "expected": "0"}], "cleanup_commands": ["OUTP OFF"]},
        }
    }


class SafePrepareSmokeTests(unittest.TestCase):
    def test_off_and_zero_are_equivalent_safe_output_values(self):
        self.assertTrue(_expected_matches("OFF", "0"))
        self.assertTrue(_expected_matches("0", "OFF"))
        self.assertFalse(_expected_matches("ON", "0"))

    def test_prepares_and_always_cleans_up(self):
        setup = config()
        resources = {
            "sim::sg": Resource({"*IDN?": "ACME,SG-1", "OUTP?": "0"}),
            "sim::sa": Resource({"*IDN?": "ACME,SA-1", "FREQ:CENT?": "1000000"}),
            "sim::ps": Resource({"*IDN?": "ACME,PS-1", "OUTP?": "0"}),
        }
        manager = Manager(resources)
        report = run_safe_prepare_smoke(setup, manager)
        self.assertTrue(report["resources_closed"])
        self.assertTrue(all(resource.closed for resource in resources.values()))
        self.assertTrue(all(result["safe_after_cleanup"] for result in report["devices"].values()))
        signal_commands = resources["sim::sg"].commands
        self.assertLess(
            signal_commands.index(("query", "OUTP?")),
            signal_commands.index(("write", "FREQ 1000000")),
        )

    def test_rejects_output_enable_and_cleans_up(self):
        setup = config()
        setup["devices"]["signal_generator"]["setup_commands"] = ["OUTP:STAT ON"]
        resource = Resource({"*IDN?": "ACME,SG-1", "OUTP?": "0"})
        with self.assertRaises(ValueError):
            run_safe_prepare_smoke(setup, Manager(resource))
        self.assertEqual(resource.commands, [])

    def test_requires_zero_action_budget(self):
        setup = config()
        setup["max_action_count"] = 1
        with self.assertRaises(ValueError):
            run_safe_prepare_smoke(setup, Manager(Resource({})))

    def test_rejects_missing_safety_query_before_opening_resources(self):
        setup = config()
        setup["devices"]["spectrum_analyzer"]["state_queries"] = []
        manager = Manager(Resource({}))
        with self.assertRaises(ValueError):
            run_safe_prepare_smoke(setup, manager)
        self.assertFalse(manager.closed)

    def test_timeout_failure_still_closes_opened_resource(self):
        resource = Resource({"*IDN?": "ACME,SG-1", "OUTP?": "0"}, timeout_error=True)
        manager = Manager(resource)
        with self.assertRaises(SmokeExecutionError):
            run_safe_prepare_smoke(config(), manager)
        self.assertTrue(resource.closed)
        self.assertIn(("write", "OUTP OFF"), resource.commands)

    def test_cleanup_attempts_later_commands_after_failure(self):
        setup = config()
        setup["devices"]["signal_generator"]["cleanup_commands"] = ["*CLS", "OUTP OFF"]
        resources = {
            "sim::sg": Resource({"*IDN?": "ACME,SG-1", "OUTP?": "0"}, fail_writes={"*CLS"}),
            "sim::sa": Resource({"*IDN?": "ACME,SA-1", "FREQ:CENT?": "1000000"}),
            "sim::ps": Resource({"*IDN?": "ACME,PS-1", "OUTP?": "0"}),
        }
        with self.assertRaises(SmokeExecutionError):
            run_safe_prepare_smoke(setup, Manager(resources))
        self.assertIn(("write", "OUTP OFF"), resources["sim::sg"].commands)
        self.assertTrue(resources["sim::sg"].closed)

    def test_final_unsafe_state_fails_smoke(self):
        setup = config()
        resource = Resource({"*IDN?": "ACME,SG-1", "OUTP?": "0"})
        original_write = resource.write

        def turn_on_after_cleanup(command):
            original_write(command)
            if command == "OUTP OFF":
                resource.responses["OUTP?"] = "1"

        resource.write = turn_on_after_cleanup
        with self.assertRaises(SmokeExecutionError) as caught:
            run_safe_prepare_smoke(setup, Manager(resource))
        self.assertFalse(caught.exception.report["devices"]["signal_generator"]["safe_after_cleanup"])


if __name__ == "__main__":
    unittest.main()
