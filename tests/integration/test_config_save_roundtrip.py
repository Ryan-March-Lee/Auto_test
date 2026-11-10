"""配置页面保存出的 JSON 必须无需转换即可被正式配置仓储读取。

这是浏览器级别的跨层契约：配置由表现层组装、由应用服务保存、由基础设施仓储读回。
把它放在 ``tests/integration`` 而不是 ``tests/ui``，UI 测试才能只依赖表现层。
"""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from infrastructure.config.json_config_repository import ConfigurationRepository
from tests.ui.test_gui_config_readers import _coordinator


def _editing_config(*, signal_source=None) -> dict:
    """一份可保存的 GUI 编辑文档；``signal_source`` 用来替换功率扫描形状。"""
    config = {
        "schema_version": "1.1",
        "test_frequencies": [2.4],
        "signal_source": {
            "start_power": -10.0,
            "stop_power": 0.0,
            "step": 1.0,
        },
        "compression_point": {"type": "5dB"},
        "driver_mode": {"enabled": False},
        "attenuator": {"type": "30dB"},
        "dut_config": {
            "max_input_power": 30.0,
            "power_supply_count": 1,
            "drain_channel": {"supply_id": "PS1", "channel": "CH2"},
        },
        "instruments": {
            "signal_generator": {"address": "sim::sg", "model": "SG", "enabled": True},
            "spectrum_analyzer": {"address": "sim::sa", "model": "SA", "enabled": True},
            "power_supplies": {
                "PS1": {
                    "address": "sim::ps1",
                    "label": "PS1",
                    "model": "DP832A",
                    "full_code": "SIM1519",
                    "device_key": "rigol technologies:ps1",
                    "enabled": True,
                    "channels": {
                        "CH1": {"voltage": {"value": 2.8}, "current": {"value": 0.1}},
                        "CH2": {"voltage": {"value": 28.0}, "current": {"value": 2.0}},
                    },
                }
            },
        },
        "power_supply_assignment": {
            "driver_amplifier": {
                "power_plan": {"power_sequence": [], "inter_channel_delay_s": 0.0},
            },
            "dut_amplifier": {
                "power_plan": {
                    "power_sequence": [
                        {"supply_id": "PS1", "channel": "CH1"},
                        {"supply_id": "PS1", "channel": "CH2"},
                    ],
                    "inter_channel_delay_s": 2.0,
                },
            },
        },
    }
    if signal_source is not None:
        config["signal_source"] = signal_source
    return config


class ConfigurationSaveRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.service = MagicMock()
        self.service.configuration.save.return_value = None
        self.messages = []
        self.coordinator = _coordinator(
            config={
                "test_frequencies": [1.0],
                "signal_source": {"start_power": -10, "stop_power": 10, "step": 1},
            },
            service=self.service,
            log=self.messages.append,
        )

    def test_save_configuration_writes_official_config_readable_by_repository(self):
        """配置页面保存出的文件无需转换即可被正式配置仓储读取。"""
        flat = _editing_config()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            self.coordinator.config = flat

            class _Configuration:
                def save(self, configuration):
                    path.write_text(json.dumps(configuration), encoding="utf-8")

            self.coordinator.gui_data_service = SimpleNamespace(
                configuration=_Configuration()
            )
            self.assertTrue(self.coordinator.save_configuration())

            loaded = ConfigurationRepository().load(path)

        self.assertTrue(loaded.valid, loaded.validation.errors)
        self.assertEqual(
            loaded.configuration.test_plan.drain_channel,
            {"supply_id": "PS1", "channel": "CH2"},
        )
        self.assertEqual(
            loaded.configuration.run_mapping.dut_power_plan["power_sequence"],
            [
                {"supply_id": "PS1", "channel": "CH1"},
                {"supply_id": "PS1", "channel": "CH2"},
            ],
        )

    def test_multi_plan_power_scan_is_saved_and_read_back_in_order(self):
        """步骤 5.3：GUI 保存的多段计划经正式仓储读回后顺序和数值不变。"""

        plans = [
            {"start_power": -20.0, "step": 2.0, "stop_power": -14.0},
            {"start_power": -14.0, "step": 1.0, "stop_power": -11.0},
        ]
        flat = _editing_config(signal_source={"power_scan_plans": plans})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            self.coordinator.config = flat

            class _Configuration:
                def save(self, configuration):
                    path.write_text(json.dumps(configuration), encoding="utf-8")

            self.coordinator.gui_data_service = SimpleNamespace(
                configuration=_Configuration()
            )
            self.assertTrue(self.coordinator.save_configuration())

            saved = json.loads(path.read_text(encoding="utf-8"))
            loaded = ConfigurationRepository().load(path)

        self.assertTrue(loaded.valid, loaded.validation.errors)
        self.assertEqual(loaded.configuration.test_plan.schema_version, "1.2")
        self.assertEqual(
            [
                plan.to_dict()
                for plan in loaded.configuration.test_plan.power_scan_plans
            ],
            plans,
        )
        self.assertIsNone(loaded.configuration.test_plan.start_power)
        self.assertIsNone(loaded.configuration.test_plan.stop_power)
        self.assertIsNone(loaded.configuration.test_plan.power_step)
        self.assertEqual(
            saved["test_plan"]["signal_source"]["power_scan_plans"], plans
        )


if __name__ == "__main__":
    unittest.main()
