"""硬件冒烟安全门禁：打开仪器之前就拒绝缺项或不安全的本地配置。"""

import json
import tempfile
import unittest
from pathlib import Path

from hardware import smoke_gate as gate


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
