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
        flat = {
            "test_frequencies": [2.4],
            "signal_source": {"start_power": -10.0, "stop_power": 0.0, "step": 1.0},
            "compression_point": {"type": "5dB"},
            "driver_mode": {"enabled": False},
            "attenuator": {"type": "30dB"},
            "dut_config": {"max_input_power": 30.0, "power_supply_count": 1},
            "instruments": {
                "signal_generator": {"address": "sim::sg", "model": "SG", "enabled": True},
                "spectrum_analyzer": {"address": "sim::sa", "model": "SA", "enabled": True},
                "power_supplies": {
                    "PS1": {
                        "address": "sim::ps1",
                        "enabled": True,
                        "channels": {
                            "CH1": {"voltage": {"value": 2.8}, "current": {"value": 0.1}},
                            "CH2": {"voltage": {"value": 28.0}, "current": {"value": 2.0}},
                        },
                    }
                },
            },
            "power_supply_assignment": {
                "driver_amplifier": {"power_supply_count": 0, "supplies": {}},
                "dut_amplifier": {
                    "power_supply_count": 1,
                    "supplies": {"carrier": {"name": "PS1", "channel": ["CH1", "CH2"]}},
                },
            },
        }
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
            set(loaded.configuration.test_plan.dut_power_channels), {"gate", "drain"}
        )


if __name__ == "__main__":
    unittest.main()
