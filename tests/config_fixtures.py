"""配置相关的共享测试夹具。

配置仓储测试（``tests/test_config_repository.py``）与配置写入适配器测试
（``tests/test_config_adapter_save.py``）需要同一份“可保存且可校验”的正式组合配置。
把它放在共享模块里，测试之间就不必互相导入对方的私有辅助函数。

与 ``tests/fakes.py`` 一样，本模块不是测试用例（文件名不匹配 ``test_*.py``，不会被
发现收集）。
"""

from __future__ import annotations

from presentation.qt.configuration_authoring import author_official_configuration


def complete_combined_config() -> dict:
    """组装一份通过正式 schema 校验的组合配置（单文件 ``config.json`` 形态）。"""
    return author_official_configuration(
        {
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
                    "DP8B1519": {
                        "label": "DP8B1519",
                        "model": "DP832A",
                        "serial": "DP8B214901519",
                        "device_key": "rigol technologies:dp8b214901519",
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
                    "supplies": {
                        "carrier": {
                            "supply_id": "DP8B1519",
                            "name": "DP8B1519",
                            "channel": ["CH1", "CH2"],
                        }
                    },
                },
            },
        }
    )


__all__ = ["complete_combined_config"]
