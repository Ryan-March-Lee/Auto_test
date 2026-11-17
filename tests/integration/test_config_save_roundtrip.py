"""配置页面保存出的 JSON 必须无需转换即可被正式配置仓储读取。

这是浏览器级别的跨层契约：配置由表现层组装、由应用服务保存、由基础设施仓储读回。
把它放在 ``tests/integration`` 而不是 ``tests/ui``，UI 测试才能只依赖表现层。
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from infrastructure.config.json_config_repository import ConfigurationRepository
from infrastructure.filesystem.paths import PathService
from infrastructure.gui_data_service import JsonConfigurationAdapter
from presentation.qt.composition import build_configuration_template_loader
from tests.ui.test_gui_config_readers import _coordinator


class _FileConfiguration:
    """实现配置端口的 ``save``/``load``：读写同一个临时文件，不靠 ``getattr`` 猜能力。"""

    def __init__(self, path):
        self.path = path

    def save(self, configuration):
        self.path.write_text(json.dumps(configuration), encoding="utf-8")

    def load(self):
        return json.loads(self.path.read_text(encoding="utf-8"))


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
            # 保存成功会建立后台运行上下文：让运行目录也落在同一个临时根下。
            self.coordinator.paths = PathService(Path(directory))
            self.coordinator.config_path = path

            self.coordinator.gui_data_service = SimpleNamespace(
                configuration=_FileConfiguration(path)
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
            self.coordinator.paths = PathService(Path(directory))
            self.coordinator.config_path = path

            self.coordinator.gui_data_service = SimpleNamespace(
                configuration=_FileConfiguration(path)
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


class StartupConfigurationSeedTests(unittest.TestCase):
    """当前配置缺失时，启动加载必须从入库模板生成一份可读回的当前配置。"""

    def _coordinator_for(self, root: Path):
        paths = PathService(root)
        service = MagicMock()
        service.configuration = JsonConfigurationAdapter(paths.config_file)
        coordinator = _coordinator(
            service=service,
            configuration_template=build_configuration_template_loader(paths),
        )
        return coordinator, paths

    def test_missing_current_configuration_is_created_from_the_default_template(self):
        template = {
            "schema_version": "1.2",
            "test_frequencies": [1.0],
            "source": "default-template",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.default.json").write_text(
                json.dumps(template), encoding="utf-8"
            )
            with patch(
                "infrastructure.config.config_templates.load_env_value",
                return_value=None,
            ):
                coordinator, paths = self._coordinator_for(root)

                loaded = coordinator.load_configuration()

            self.assertEqual(loaded, template)
            self.assertEqual(
                json.loads(paths.config_file.read_text(encoding="utf-8")), template
            )
            self.assertTrue(coordinator.seeded_configuration_from_template)

    def test_mock_template_is_used_only_when_explicitly_selected(self):
        template = {"schema_version": "1.2", "source": "mock-template"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.default.json").write_text(
                json.dumps({"source": "default-template"}), encoding="utf-8"
            )
            (root / "config.mock.json").write_text(
                json.dumps(template), encoding="utf-8"
            )
            coordinator, paths = self._coordinator_for(root)

            with patch.dict(
                os.environ, {"AUTO_TEST_CONFIG_TEMPLATE": "config.mock.json"}
            ):
                loaded = coordinator.load_configuration()

            self.assertEqual(loaded, template)
            self.assertEqual(
                json.loads(paths.config_file.read_text(encoding="utf-8")), template
            )

    def test_the_composed_coordinator_builds_the_seed_loader_itself(self):
        """真实装配路径不显式传模板读取器：由协调器在组合根上取同一份模板。"""

        from presentation.qt.application_coordinator import ApplicationCoordinator

        template = {"schema_version": "1.2", "source": "composed"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.default.json").write_text(
                json.dumps(template), encoding="utf-8"
            )
            paths = PathService(root)
            with patch(
                "infrastructure.config.config_templates.load_env_value",
                return_value=None,
            ):
                coordinator = ApplicationCoordinator(
                    log=lambda _message: None,
                    warn=lambda _title, _message: None,
                    error=lambda _message: None,
                    paths=paths,
                    controller=MagicMock(),
                )

                loaded = coordinator.load_configuration()

            self.assertEqual(loaded, template)
            self.assertEqual(
                json.loads(paths.config_file.read_text(encoding="utf-8")), template
            )
            self.assertTrue(coordinator.seeded_configuration_from_template)


if __name__ == "__main__":
    unittest.main()
