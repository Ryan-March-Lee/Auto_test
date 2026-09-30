import unittest

from hardware.minimal_action_smoke import run_minimal_action_smoke
from hardware.read_only_smoke import SmokeExecutionError


class Resource:
    def __init__(self, responses):
        self.responses = responses
        self.commands = []
        self.closed = False
        self.timeout = None

    def write(self, command):
        self.commands.append(("write", command))
        if command.startswith("FREQ:CENT "):
            self.responses["FREQ:CENT?"] = command.split(" ", 1)[1]
        if command == "OUTP ON":
            self.responses["OUTP?"] = "1"
        if command == "OUTP OFF":
            self.responses["OUTP?"] = "0"
        if command == "OUTP CH2,OFF":
            self.responses["OUTP? CH2"] = "0"
        if command == "OUTP CH1,OFF":
            self.responses["OUTP? CH1"] = "0"

    def query(self, command):
        self.commands.append(("query", command))
        return self.responses[command]

    def close(self):
        self.closed = True


class Manager:
    def __init__(self, resources, discovered=None):
        self.resources = resources
        self.discovered = discovered or []
        self.closed = False

    def open_resource(self, address, **kwargs):
        return self.resources[address]

    def close(self):
        self.closed = True

    def list_resources(self):
        return tuple(self.discovered)


def config():
    common = {"timeout_ms": 1000}
    return {
        "environment": "hardware_smoke", "smoke_mode": "minimal_action",
        "require_empty_setup": True, "require_user_confirmation": True,
        "max_action_count": 1, "max_duration_s": 30, "action_timeout_s": 1, "cleanup_timeout_s": 2,
        "devices": {
            "signal_generator": {**common, "address": "sg", "model": "SG-1", "setup_commands": [],
                "rf_parameters": {"frequency_hz": 1000000, "min_frequency_hz": 900000, "max_frequency_hz": 1100000,
                                  "power_dbm": -30, "max_power_dbm": -20},
                "measurement_queries": [], "state_queries": [{"command": "OUTP?", "expected": "0"}], "cleanup_commands": []},
            "spectrum_analyzer": {**common, "address": "sa", "model": "SA-1", "setup_commands": [],
                "measurement_queries": [{"command": "POW?", "expected": "-30"}],
                "state_queries": [{"command": "FREQ:CENT?", "expected": "1000000"}], "cleanup_commands": ["*CLS"]},
            "power_supply": {**common, "address": "ps", "model": "PS-1", "setup_commands": [],
                "measurement_queries": [], "power_off_sequence": ["OUTP CH2,OFF", "OUTP CH1,OFF"],
                "state_queries": [{"command": "OUTP? CH2", "expected": "0"}, {"command": "OUTP? CH1", "expected": "0"}],
                "cleanup_commands": ["OUTP CH2,OFF", "OUTP CH1,OFF"]},
        },
    }


class MinimalActionSmokeTests(unittest.TestCase):
    def resources(self):
        return {
            "sg": Resource({"*IDN?": "ACME,SG-1", "OUTP?": "0"}),
            "sa": Resource({"*IDN?": "ACME,SA-1", "FREQ:CENT?": "1000000", "POW?": "-30"}),
            "ps": Resource({"*IDN?": "ACME,PS-1", "OUTP? CH2": "0", "OUTP? CH1": "0"}),
        }

    def test_action_measurement_and_cleanup(self):
        resources = self.resources()
        report = run_minimal_action_smoke(config(), Manager(resources))
        self.assertEqual(report["action_count"], 1)
        self.assertTrue(report["resources_closed"])
        self.assertTrue(all(item["safe_after_cleanup"] for item in report["devices"].values()))
        self.assertEqual(sum(command == ("write", "OUTP ON") for command in resources["sg"].commands), 1)
        enabled = resources["sg"].commands.index(("write", "OUTP ON"))
        disabled = resources["sg"].commands.index(("write", "OUTP OFF"))
        self.assertEqual(resources["sa"].commands[0], ("query", "*IDN?"))
        self.assertLess(enabled, disabled)
        self.assertLess(resources["ps"].commands.index(("write", "OUTP CH2,OFF")),
                        resources["ps"].commands.index(("write", "OUTP CH1,OFF")))

    def test_analyzer_previous_frequency_is_prepared_before_rf_action(self):
        resources = self.resources()
        resources["sa"].responses["FREQ:CENT?"] = "3000000000"

        report = run_minimal_action_smoke(config(), Manager(resources))

        self.assertEqual(report["action_count"], 1)
        analyzer_commands = resources["sa"].commands
        prepare = ("write", "FREQ:CENT 1000000")
        enable = ("write", "OUTP ON")
        self.assertIn(prepare, analyzer_commands)
        self.assertLess(analyzer_commands.index(prepare), resources["sg"].commands.index(enable))
        self.assertEqual(report["devices"]["spectrum_analyzer"]["final_state"][0]["response"], "1000000")

    def test_power_supply_can_be_discovered_by_identity(self):
        setup = config()
        setup["devices"]["power_supply"].pop("address")
        setup["devices"]["power_supply"]["discover"] = True
        setup["devices"]["power_supply"].pop("model")
        resources = self.resources()
        manager = Manager(resources, discovered=("unused", "ps"))
        resources["unused"] = Resource({"*IDN?": "ACME,OTHER", "OUTP? CH2": "1", "OUTP? CH1": "0"})

        report = run_minimal_action_smoke(setup, manager)

        self.assertEqual(report["action_count"], 1)
        self.assertEqual(report["devices"]["power_supply"]["identity"], "ACME,PS-1")
        self.assertEqual(report["power_supply_discovery"][0]["address"], "unused")
        self.assertEqual(report["power_supply_discovery"][1]["address"], "ps")
        self.assertTrue(all(item["closed"] for item in report["power_supply_discovery"]))

    def test_all_discovered_power_supplies_are_cleaned_and_verified(self):
        setup = config()
        setup["devices"]["power_supply"].pop("address")
        setup["devices"]["power_supply"]["discover"] = True
        resources = self.resources()
        resources["ps2"] = Resource({"*IDN?": "ACME,PS-1", "OUTP? CH2": "0", "OUTP? CH1": "0"})
        manager = Manager(resources, discovered=("ps", "ps2"))

        report = run_minimal_action_smoke(setup, manager)

        self.assertEqual(report["action_count"], 1)
        self.assertEqual(report["power_supply_discovery_count"], 2)
        power_results = report["devices"]["power_supply"]
        self.assertEqual({result["address"] for result in power_results}, {"ps", "ps2"})
        self.assertTrue(all(result["safe_after_cleanup"] and result["closed"] for result in power_results))
        for resource in (resources["ps"], resources["ps2"]):
            self.assertEqual(
                [command for kind, command in resource.commands if kind == "write"],
                ["OUTP CH2,OFF", "OUTP CH1,OFF"],
            )

    def test_marker_measurement_selects_peak_before_reading_value(self):
        setup = config()
        setup["devices"]["spectrum_analyzer"]["measurement_queries"] = [
            {"command": "CALC:MARK1:Y?", "expected": "-30", "min_dbm": -40, "max_dbm": -20}
        ]
        resources = self.resources()
        resources["sa"].responses["CALC:MARK1:Y?"] = "-30"

        report = run_minimal_action_smoke(setup, Manager(resources))

        self.assertEqual(report["action_count"], 1)
        analyzer_commands = resources["sa"].commands
        marker_setup = analyzer_commands.index(("write", "CALC:MARK1:MAX"))
        marker_read = analyzer_commands.index(("query", "CALC:MARK1:Y?"))
        self.assertLess(marker_setup, marker_read)
        self.assertEqual(report["devices"]["spectrum_analyzer"]["measurements"][0]["response"], "-30")

    def test_measurement_range_failure_still_turns_rf_off(self):
        setup = config()
        setup["devices"]["spectrum_analyzer"]["measurement_queries"][0].update(
            {"min_dbm": -25, "max_dbm": -20}
        )
        resources = self.resources()

        with self.assertRaises(SmokeExecutionError):
            run_minimal_action_smoke(setup, Manager(resources))

        self.assertIn(("write", "OUTP OFF"), resources["sg"].commands)
        self.assertTrue(resources["sg"].closed)

    def test_action_timeout_is_enforced_and_cleanup_runs(self):
        setup = config()
        setup["max_duration_s"] = 0.005
        setup["action_timeout_s"] = 0.001
        resources = self.resources()
        manager = Manager(resources)
        clock_values = iter(index * 0.01 for index in range(100))

        with self.assertRaises(SmokeExecutionError):
            run_minimal_action_smoke(setup, manager, clock=lambda: next(clock_values))

        self.assertTrue(manager.closed)

    def test_action_budget_and_output_enable_are_restricted(self):
        setup = config()
        setup["devices"]["signal_generator"]["rf_parameters"]["power_dbm"] = 0
        with self.assertRaises(ValueError):
            run_minimal_action_smoke(setup, Manager(self.resources()))

    def test_failure_still_runs_cleanup(self):
        setup = config()
        resources = self.resources()
        resources["sa"].responses["POW?"] = "wrong"
        with self.assertRaises(SmokeExecutionError):
            run_minimal_action_smoke(setup, Manager(resources))
        self.assertIn(("write", "OUTP OFF"), resources["sg"].commands)
        self.assertTrue(resources["sg"].closed)

    def test_requires_explicit_safe_expectations_and_power_limits(self):
        setup = config()
        setup["devices"]["signal_generator"]["state_queries"][0].pop("expected")
        with self.assertRaises(ValueError):
            run_minimal_action_smoke(setup, Manager(self.resources()))

        setup = config()
        setup["devices"]["signal_generator"]["rf_parameters"]["power_dbm"] = -5
        with self.assertRaises(ValueError):
            run_minimal_action_smoke(setup, Manager(self.resources()))

    def test_rf_is_off_before_cleanup_verification_and_cleanup_ignores_action_deadline(self):
        setup = config()
        setup["max_duration_s"] = 1
        resources = self.resources()
        report = run_minimal_action_smoke(setup, Manager(resources))
        self.assertTrue(report["devices"]["signal_generator"]["safe_after_cleanup"])
        ops = resources["sg"].commands
        self.assertLess(ops.index(("write", "OUTP OFF")), len(ops) - 1)
        self.assertEqual(ops[-1], ("query", "OUTP?"))


if __name__ == "__main__":
    unittest.main()
