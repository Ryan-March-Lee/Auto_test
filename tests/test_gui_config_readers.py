"""配置读取、组装与保存：控件读取归 ConfigFormState，保存归运行协调器。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from presentation.qt.application_coordinator import ApplicationCoordinator
from presentation.qt.config_form_state import ConfigFormState


class FakeTextWidget:
    def __init__(self, value):
        self.value = value

    def text(self):
        return self.value


class FakeCheckWidget:
    def __init__(self, value):
        self.value = value

    def isChecked(self):
        return self.value


class FakeValueWidget:
    def __init__(self, value):
        self._value = value

    def value(self):
        return self._value


class FakeComboWidget:
    def __init__(self, value):
        self.value = value

    def currentText(self):
        return self.value


class _WidgetHost:
    """控件宿主替身：ConfigFormState 只按属性名读取控件。"""


def channel_widgets(voltage, voltage_protection, voltage_enabled, current, current_protection, current_enabled, role=None):
    controls = {
        'voltage': FakeValueWidget(voltage),
        'voltage_protection': FakeValueWidget(voltage_protection),
        'voltage_protection_enabled': FakeCheckWidget(voltage_enabled),
        'current': FakeValueWidget(current),
        'current_protection': FakeValueWidget(current_protection),
        'current_protection_enabled': FakeCheckWidget(current_enabled),
    }
    if role is not None:
        controls['role'] = FakeComboWidget(role)
    return controls


def _coordinator(*, config=None, service=None, page=None, log=None):
    kwargs = {"log": log} if log is not None else {}
    coordinator = ApplicationCoordinator(
        gui_data_service=service or MagicMock(),
        controller=MagicMock(),
        config_path=Path("config.json"),
        widget_host=page,
        **kwargs,
    )
    if config is not None:
        coordinator.config = config
    coordinator.config_page = page
    return coordinator


class ConfigFormStateReaderTests(unittest.TestCase):
    """读取片段只属于 ConfigFormState；窗口和协调器都不再持有这些逻辑。"""

    def setUp(self):
        self.config = {
            'instruments': {
                'existing_instrument_field': 'preserved',
                'power_supplies': {
                    'PS1': {
                        'address': 'old-ps1',
                        'enabled': False,
                        'channels': {'CH1': {'legacy': True}},
                        'unknown': 'preserved',
                    }
                }
            },
            'test_frequencies': [1.0],
            'dut_config': {'max_input_power': 30.0, 'power_supply_count': 1, 'extra': 'keep'},
        }
        self.host = _WidgetHost()
        self.host.sg_address = FakeTextWidget('sg-new')
        self.host.sg_enabled = FakeCheckWidget(True)
        self.host.sa_address = FakeTextWidget('sa-new')
        self.host.sa_enabled = FakeCheckWidget(False)
        for name in ('ps1', 'ps2', 'ps3', 'ps4'):
            setattr(self.host, f'{name}_address', FakeTextWidget(f'{name}-new'))
            setattr(self.host, f'{name}_enabled', FakeCheckWidget(name != 'ps2'))
        self.reader = ConfigFormState(self.config, self.host)

    def _setup_all_widgets(self, pa_unit_count='2', driver_enabled=True):
        """设置全部控件，供 build/save 测试使用。"""
        self.host.freq_edit = FakeTextWidget('[1.0, 2.0]')
        self.host.start_power = FakeValueWidget(-10)
        self.host.stop_power = FakeValueWidget(10)
        self.host.power_step = FakeValueWidget(1)
        self.host.compression_combo = FakeComboWidget('5dB')
        self.host.attenuator_combo = FakeComboWidget('30dB')
        self.host.driver_mode_check = FakeCheckWidget(False)
        self.host.max_input_power = FakeValueWidget(29.5)
        self.host.pa_unit_count_combo = FakeComboWidget(pa_unit_count)
        self.host.power_config_widgets = {
            'PS1': {
                'channels': {
                    'CH1': channel_widgets(2.8, 3.0, True, 0.1, 0.2, False),
                    'CH2': channel_widgets(28.0, 30.0, True, 1.0, 2.0, True),
                }
            }
        }
        self.host.power_assignment_widgets = {
            'driver_enabled': FakeCheckWidget(driver_enabled),
            'driver_power': FakeComboWidget('PS4'),
            'pa_unit1_power': FakeComboWidget('PS1'),
            'pa_unit2_power': FakeComboWidget('PS2'),
            'pa_unit3_power': FakeComboWidget('PS3'),
        }

    def test_instrument_reader_wraps_instruments_and_preserves_existing_fields(self):
        result = self.reader.read_instrument_config()

        self.assertIn('instruments', result)
        instruments = result['instruments']
        self.assertEqual(instruments['existing_instrument_field'], 'preserved')
        self.assertEqual(instruments['power_supplies']['PS1']['address'], 'ps1-new')
        self.assertTrue(instruments['power_supplies']['PS1']['enabled'])
        self.assertEqual(instruments['power_supplies']['PS1']['channels'], {'CH1': {'legacy': True}})
        self.assertEqual(instruments['power_supplies']['PS1']['unknown'], 'preserved')
        self.assertEqual(set(instruments['power_supplies']), {'PS1', 'PS2', 'PS3', 'PS4'})

    def test_test_parameter_reader_keeps_invalid_frequency_out_of_fragment(self):
        self.host.freq_edit = FakeTextWidget('not-a-list')
        self.host.start_power = FakeValueWidget(-10)
        self.host.stop_power = FakeValueWidget(10)
        self.host.power_step = FakeValueWidget(1)
        self.host.compression_combo = FakeComboWidget('5dB')
        self.host.attenuator_combo = FakeComboWidget('30dB')
        self.host.driver_mode_check = FakeCheckWidget(False)

        result = self.reader.read_test_parameters()

        self.assertNotIn('test_frequencies', result)
        self.assertEqual(result['signal_source']['step'], 1)
        self.assertEqual(result['compression_point']['type'], '5dB')
        self.assertFalse(result['driver_mode']['enabled'])

    def test_power_supply_reader_returns_nested_fragment(self):
        self.host.power_config_widgets = {
            'PS1': {
                'channels': {
                    'CH1': channel_widgets(2.8, 3.0, True, 0.1, 0.2, False),
                }
            }
        }

        result = self.reader.read_power_supply_config()

        self.assertEqual(
            result['instruments']['power_supplies']['PS1']['channels']['CH1']['voltage']['value'],
            2.8,
        )
        self.assertTrue(
            result['instruments']['power_supplies']['PS1']['channels']['CH1']['voltage']['protection_enabled']
        )

    def test_power_supply_reader_writes_the_role_selected_in_the_ui(self):
        self.host.power_config_widgets = {
            'PS1': {
                'channels': {
                    'CH1': channel_widgets(2.8, 3.0, True, 0.1, 0.2, False, role='drain'),
                    'CH2': channel_widgets(28.0, 30.0, True, 1.0, 2.0, True, role='gate'),
                }
            }
        }

        result = self.reader.read_power_supply_config()

        channels = result['instruments']['power_supplies']['PS1']['channels']
        self.assertEqual(channels['CH1']['role'], 'drain')
        self.assertEqual(channels['CH2']['role'], 'gate')

    def test_power_supply_reader_keeps_existing_role_without_a_role_widget(self):
        self.config['instruments']['power_supplies']['PS1']['channels'] = {
            'CH1': {'role': 'drain'},
            'CH2': {'role': 'gate'},
        }
        self.host.power_config_widgets = {
            'PS1': {
                'channels': {
                    'CH1': channel_widgets(2.8, 3.0, True, 0.1, 0.2, False),
                    'CH2': channel_widgets(28.0, 30.0, True, 1.0, 2.0, True),
                }
            }
        }

        result = self.reader.read_power_supply_config()

        channels = result['instruments']['power_supplies']['PS1']['channels']
        self.assertEqual(channels['CH1']['role'], 'drain')
        self.assertEqual(channels['CH2']['role'], 'gate')

    def test_power_supply_reader_does_not_invent_a_missing_role(self):
        """没有控件、配置里也没有角色时不得按 CH1/CH2 推断。"""
        self.host.power_config_widgets = {
            'PS1': {
                'channels': {
                    'CH1': channel_widgets(2.8, 3.0, True, 0.1, 0.2, False),
                }
            }
        }

        result = self.reader.read_power_supply_config()

        channels = result['instruments']['power_supplies']['PS1']['channels']
        self.assertIsNone(channels['CH1']['role'])

    def test_power_assignment_reader_preserves_legacy_roles_and_channels(self):
        self.host.power_assignment_widgets = {
            'driver_enabled': FakeCheckWidget(True),
            'driver_power': FakeComboWidget('PS4'),
            'pa_unit1_power': FakeComboWidget('PS1'),
            'pa_unit2_power': FakeComboWidget('PS2'),
            'pa_unit3_power': FakeComboWidget('PS3'),
        }
        self.host.pa_unit_count_combo = FakeComboWidget('3')

        result = self.reader.read_power_assignment()['power_supply_assignment']

        self.assertEqual(result['driver_amplifier']['supplies']['main']['name'], 'PS4')
        self.assertEqual(
            list(result['driver_amplifier']['supplies']['main']['channel']),
            ['CH1', 'CH2'],
        )
        self.assertEqual(set(result['dut_amplifier']['supplies']), {'carrier', 'peaking', 'peaking2'})
        self.assertEqual(result['dut_amplifier']['power_supply_count'], 3)


class CoordinatorConfigBuildTests(unittest.TestCase):
    """步骤5-7: 协调器按当前控件状态组装完整配置，并替换自己的配置副本。"""

    def setUp(self):
        self.config = {
            'instruments': {
                'power_supplies': {
                    'PS1': {
                        'address': 'old-ps1',
                        'enabled': False,
                        'channels': {'CH1': {'legacy': True}},
                        'unknown': 'preserved',
                    },
                    'PS2': {
                        'address': 'old-ps2',
                        'enabled': False,
                        'channels': {'CH1': {'legacy': True}},
                    }
                },
                'extra_instrument': 'keep',
            },
            'test_frequencies': [1.0],
            'dut_config': {'max_input_power': 30.0, 'power_supply_count': 1, 'extra': 'keep'},
            'top_level_extra': 'preserved',
        }
        self.host = _WidgetHost()
        self.host.config = self.config
        self.host.sg_address = FakeTextWidget('sg-new')
        self.host.sg_enabled = FakeCheckWidget(True)
        self.host.sa_address = FakeTextWidget('sa-new')
        self.host.sa_enabled = FakeCheckWidget(False)
        for name in ('ps1', 'ps2', 'ps3', 'ps4'):
            setattr(self.host, f'{name}_address', FakeTextWidget(f'{name}-new'))
            setattr(self.host, f'{name}_enabled', FakeCheckWidget(name != 'ps2'))
        self.host.freq_edit = FakeTextWidget('[1.0, 2.0]')
        self.host.start_power = FakeValueWidget(-10)
        self.host.stop_power = FakeValueWidget(10)
        self.host.power_step = FakeValueWidget(1)
        self.host.compression_combo = FakeComboWidget('5dB')
        self.host.attenuator_combo = FakeComboWidget('30dB')
        self.host.driver_mode_check = FakeCheckWidget(False)
        self.host.max_input_power = FakeValueWidget(29.5)
        self.host.pa_unit_count_combo = FakeComboWidget('2')
        self.host.power_config_widgets = {
            'PS1': {
                'channels': {
                    'CH1': channel_widgets(2.8, 3.0, True, 0.1, 0.2, False),
                    'CH2': channel_widgets(28.0, 30.0, True, 1.0, 2.0, True),
                }
            }
        }
        self.host.power_assignment_widgets = {
            'driver_enabled': FakeCheckWidget(False),
            'driver_power': FakeComboWidget('PS4'),
            'pa_unit1_power': FakeComboWidget('PS1'),
            'pa_unit2_power': FakeComboWidget('PS2'),
            'pa_unit3_power': FakeComboWidget('PS3'),
        }
        self.coordinator = _coordinator(config=self.config, page=self.host)

    def test_build_preserves_top_level_and_instrument_extra_fields(self):
        result = self.coordinator.build_configuration_from_ui()

        self.assertEqual(result['top_level_extra'], 'preserved')
        self.assertEqual(result['instruments']['extra_instrument'], 'keep')
        self.assertEqual(result['instruments']['power_supplies']['PS1']['unknown'], 'preserved')
        self.assertEqual(result['dut_config']['extra'], 'keep')

    def test_build_writes_instrument_addresses_and_enabled(self):
        result = self.coordinator.build_configuration_from_ui()

        instruments = result['instruments']
        self.assertEqual(instruments['signal_generator']['address'], 'sg-new')
        self.assertTrue(instruments['signal_generator']['enabled'])
        self.assertEqual(instruments['spectrum_analyzer']['address'], 'sa-new')
        self.assertFalse(instruments['spectrum_analyzer']['enabled'])
        self.assertEqual(instruments['power_supplies']['PS1']['address'], 'ps1-new')
        self.assertTrue(instruments['power_supplies']['PS1']['enabled'])
        self.assertEqual(instruments['power_supplies']['PS4']['address'], 'ps4-new')

    def test_build_replaces_channels_but_preserves_address_and_enabled(self):
        result = self.coordinator.build_configuration_from_ui()

        ps1 = result['instruments']['power_supplies']['PS1']
        self.assertEqual(ps1['address'], 'ps1-new')
        self.assertTrue(ps1['enabled'])
        self.assertEqual(ps1['unknown'], 'preserved')
        # channels 被替换为 UI 值
        self.assertEqual(ps1['channels']['CH1']['voltage']['value'], 2.8)
        self.assertEqual(ps1['channels']['CH2']['current']['value'], 1.0)
        # legacy 字段应被替换掉（不再存在）
        self.assertNotIn('legacy', ps1['channels'].get('CH1', {}))

    def test_build_writes_test_parameters(self):
        result = self.coordinator.build_configuration_from_ui()

        self.assertEqual(result['test_frequencies'], [1.0, 2.0])
        self.assertEqual(result['signal_source']['start_power'], -10)
        self.assertEqual(result['signal_source']['step'], 1)
        self.assertEqual(result['compression_point']['type'], '5dB')
        self.assertEqual(result['attenuator']['type'], '30dB')
        self.assertFalse(result['driver_mode']['enabled'])

    def test_build_invalid_frequency_preserves_old_value(self):
        self.host.freq_edit = FakeTextWidget('not-a-list')
        result = self.coordinator.build_configuration_from_ui()

        self.assertEqual(result['test_frequencies'], [1.0])

    def test_build_dut_config(self):
        result = self.coordinator.build_configuration_from_ui()

        self.assertEqual(result['dut_config']['max_input_power'], 29.5)
        self.assertEqual(result['dut_config']['power_supply_count'], 2)
        self.assertEqual(result['dut_config']['extra'], 'keep')

    def test_build_pa_unit_count_1_only_carrier(self):
        self.host.pa_unit_count_combo = FakeComboWidget('1')
        result = self.coordinator.build_configuration_from_ui()

        supplies = result['power_supply_assignment']['dut_amplifier']['supplies']
        self.assertEqual(set(supplies), {'carrier'})
        self.assertEqual(result['power_supply_assignment']['dut_amplifier']['power_supply_count'], 1)

    def test_build_pa_unit_count_2_carrier_and_peaking(self):
        result = self.coordinator.build_configuration_from_ui()

        supplies = result['power_supply_assignment']['dut_amplifier']['supplies']
        self.assertEqual(set(supplies), {'carrier', 'peaking'})
        self.assertEqual(result['power_supply_assignment']['dut_amplifier']['power_supply_count'], 2)

    def test_build_pa_unit_count_3_all_roles(self):
        self.host.pa_unit_count_combo = FakeComboWidget('3')
        result = self.coordinator.build_configuration_from_ui()

        supplies = result['power_supply_assignment']['dut_amplifier']['supplies']
        self.assertEqual(set(supplies), {'carrier', 'peaking', 'peaking2'})
        self.assertEqual(result['power_supply_assignment']['dut_amplifier']['power_supply_count'], 3)

    def test_build_driver_disabled_empty_supplies(self):
        result = self.coordinator.build_configuration_from_ui()

        driver = result['power_supply_assignment']['driver_amplifier']
        self.assertEqual(driver['power_supply_count'], 0)
        self.assertEqual(driver['supplies'], {})

    def test_build_driver_enabled_with_main(self):
        self.host.power_assignment_widgets['driver_enabled'] = FakeCheckWidget(True)
        result = self.coordinator.build_configuration_from_ui()

        driver = result['power_supply_assignment']['driver_amplifier']
        self.assertEqual(driver['power_supply_count'], 1)
        self.assertEqual(driver['supplies']['main']['name'], 'PS4')
        self.assertEqual(list(driver['supplies']['main']['channel']), ['CH1', 'CH2'])

    def test_build_channel_order_is_ch1_then_ch2(self):
        result = self.coordinator.build_configuration_from_ui()

        for supply in result['power_supply_assignment']['dut_amplifier']['supplies'].values():
            self.assertEqual(list(supply['channel']), ['CH1', 'CH2'])

    def test_build_does_not_mutate_the_stored_config(self):
        original = json.dumps(self.coordinator.config, sort_keys=True)
        result = self.coordinator.build_configuration_from_ui()
        self.assertEqual(json.dumps(self.coordinator.config, sort_keys=True), original)

        # 返回结果也必须与原始配置完全隔离，不能通过未覆盖的嵌套字段反向修改配置。
        result['instruments']['power_supplies']['PS2']['channels']['CH1']['custom'] = 'changed'
        self.assertNotIn(
            'custom',
            self.coordinator.config['instruments']['power_supplies']['PS2']['channels']['CH1'],
        )

    def test_build_preserves_channels_not_present_in_ui_widgets(self):
        self.coordinator.config['instruments']['power_supplies']['PS1']['channels']['CH3'] = {
            'legacy_channel': True,
        }

        result = self.coordinator.build_configuration_from_ui()

        channels = result['instruments']['power_supplies']['PS1']['channels']
        self.assertIn('CH3', channels)
        self.assertEqual(channels['CH3'], {'legacy_channel': True})

    def test_update_configuration_from_ui_sets_config(self):
        self.coordinator.update_configuration_from_ui()

        self.assertEqual(
            self.coordinator.config['instruments']['signal_generator']['address'], 'sg-new'
        )
        self.assertEqual(self.coordinator.config['test_frequencies'], [1.0, 2.0])
        self.assertEqual(self.coordinator.config['dut_config']['power_supply_count'], 2)
        # 配置页持有的副本必须与协调器一致。
        self.assertIs(self.host.config, self.coordinator.config)

    def test_build_top_level_keys_match_legacy(self):
        result = self.coordinator.build_configuration_from_ui()
        expected_keys = {
            'instruments', 'test_frequencies', 'signal_source',
            'compression_point', 'attenuator', 'driver_mode',
            'dut_config', 'power_supply_assignment', 'top_level_extra', 'wiring',
        }
        self.assertEqual(set(result.keys()), expected_keys)

    def test_build_invalidates_previous_wiring_confirmation(self):
        self.coordinator.config['wiring'] = {
            'confirmed': True,
            'connection_note': 'previous',
            'confirmed_at': 'old',
            'confirmation_source': 'old',
        }
        result = self.coordinator.build_configuration_from_ui()
        self.assertFalse(result['wiring']['confirmed'])
        self.assertIsNone(result['wiring']['connection_note'])
        self.assertIsNone(result['wiring']['confirmed_at'])


class CoordinatorConfigSaveTests(unittest.TestCase):
    """步骤6-7: 配置保存只由协调器写入正式配置文件。"""

    def setUp(self):
        self.service = MagicMock()
        self.service.configuration.save.return_value = None
        self.messages = []
        self.coordinator = _coordinator(
            config={
                'test_frequencies': [1.0],
                'signal_source': {'start_power': -10, 'stop_power': 10, 'step': 1},
            },
            service=self.service,
            log=self.messages.append,
        )

    def test_save_configuration_writes_json(self):
        result = self.coordinator.save_configuration()

        self.assertTrue(result)
        self.service.configuration.save.assert_called_once_with(self.coordinator.config)
        self.assertIsNone(self.coordinator.last_save_error)

    def test_save_configuration_writes_official_config_readable_by_repository(self):
        """配置页面保存出的文件无需转换即可被正式配置仓储读取。"""
        from types import SimpleNamespace

        from infrastructure.config.json_config_repository import ConfigurationRepository

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
            "wiring": {"confirmed": True, "connection_note": "现场接线已确认"},
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

    def test_save_configuration_returns_false_on_error(self):
        self.service.configuration.save.side_effect = OSError("test write failure")
        result = self.coordinator.save_configuration()

        self.assertFalse(result)
        self.assertIsNotNone(self.coordinator.last_save_error)

    def test_save_configuration_reports_unrepresentable_multi_supply_assignment(self):
        """多本机电源指派无法写成正式 run_mapping：保存必须显式失败，不写半成品文件。"""
        self.coordinator.config = {
            "test_frequencies": [2.4],
            "signal_source": {"start_power": -10.0, "stop_power": 0.0, "step": 1.0},
            "compression_point": {"type": "5dB"},
            "driver_mode": {"enabled": True},
            "attenuator": {"type": "30dB"},
            "dut_config": {"max_input_power": 30.0},
            "instruments": {
                "signal_generator": {"address": "sim::sg", "model": "SG", "enabled": True},
                "spectrum_analyzer": {"address": "sim::sa", "model": "SA", "enabled": True},
                "power_supplies": {
                    "PS1": {"address": "sim::ps1", "enabled": True, "channels": {"CH1": {}}},
                    "PS2": {"address": "sim::ps2", "enabled": True, "channels": {"CH1": {}}},
                },
            },
            "power_supply_assignment": {
                "dut_amplifier": {"supplies": {"carrier": {"name": "PS1", "channel": ["CH1"]}}},
                "driver_amplifier": {"supplies": {"driver": {"name": "PS2", "channel": ["CH1"]}}},
            },
            "wiring": {"confirmed": True, "connection_note": "现场接线已确认"},
        }

        result = self.coordinator.save_configuration()

        self.assertFalse(result)
        self.assertIn("PS1", str(self.coordinator.last_save_error))
        self.service.configuration.save.assert_not_called()

    def test_update_and_save_configuration_success(self):
        self.coordinator.update_configuration_from_ui = MagicMock()

        result = self.coordinator.update_and_save_configuration()

        self.assertTrue(result)
        self.coordinator.update_configuration_from_ui.assert_called_once_with()
        self.assertIn('配置已更新并保存', self.messages)

    def test_update_and_save_configuration_failure(self):
        self.coordinator.update_configuration_from_ui = MagicMock()
        self.service.configuration.save.side_effect = OSError("test write failure")

        result = self.coordinator.update_and_save_configuration()

        self.assertFalse(result)
        self.assertTrue(any('配置保存失败' in message for message in self.messages))


class MainWindowSaveConfigDialogTests(unittest.TestCase):
    """窗口只负责保存配置的用户可见反馈。"""

    def setUp(self):
        from presentation.qt.main_window import MainWindow

        self.window = MainWindow.__new__(MainWindow)
        self.window.coordinator = MagicMock()
        self.window.coordinator.prepared_run = None
        self.window.coordinator.last_save_error = None
        self.messages = []
        self.window.add_log_message = self.messages.append
        self.calls = []

    def _record(self, icon, title, text):
        self.calls.append((title, text))

    def test_save_config_success(self):
        self.window.coordinator.save_configuration.return_value = True

        with patch(
            'presentation.qt.main_window.QMessageBox.information', side_effect=self._record
        ):
            self.window.save_config()

        self.assertEqual(self.calls, [('保存成功', '配置文件已保存')])
        self.assertIn('配置已保存', self.messages)

    def test_save_config_failure(self):
        self.window.coordinator.save_configuration.return_value = False
        self.window.coordinator.last_save_error = OSError("test write failure")

        with patch(
            'presentation.qt.main_window.QMessageBox.warning', side_effect=self._record
        ):
            self.window.save_config()

        self.assertTrue(any('保存失败' in title for title, _ in self.calls))
        self.assertTrue(any('配置保存失败' in message for message in self.messages))


if __name__ == '__main__':
    unittest.main()
