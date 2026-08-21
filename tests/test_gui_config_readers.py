import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from enhanced_main_gui import MainWindow


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


def channel_widgets(voltage, voltage_protection, voltage_enabled, current, current_protection, current_enabled):
    return {
        'voltage': FakeValueWidget(voltage),
        'voltage_protection': FakeValueWidget(voltage_protection),
        'voltage_protection_enabled': FakeCheckWidget(voltage_enabled),
        'current': FakeValueWidget(current),
        'current_protection': FakeValueWidget(current_protection),
        'current_protection_enabled': FakeCheckWidget(current_enabled),
    }


class GuiConfigReaderTests(unittest.TestCase):
    def setUp(self):
        self.window = MainWindow.__new__(MainWindow)
        self.window.config = {
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
        self.window.sg_address = FakeTextWidget('sg-new')
        self.window.sg_enabled = FakeCheckWidget(True)
        self.window.sa_address = FakeTextWidget('sa-new')
        self.window.sa_enabled = FakeCheckWidget(False)
        for name in ('ps1', 'ps2', 'ps3', 'ps4'):
            setattr(self.window, f'{name}_address', FakeTextWidget(f'{name}-new'))
            setattr(self.window, f'{name}_enabled', FakeCheckWidget(name != 'ps2'))

    def _setup_all_widgets(self, pa_unit_count='2', driver_enabled=True):
        """设置全部控件，供 build/save 测试使用。"""
        self.window.freq_edit = FakeTextWidget('[1.0, 2.0]')
        self.window.start_power = FakeValueWidget(-10)
        self.window.stop_power = FakeValueWidget(10)
        self.window.power_step = FakeValueWidget(1)
        self.window.compression_combo = FakeComboWidget('5dB')
        self.window.attenuator_combo = FakeComboWidget('30dB')
        self.window.driver_mode_check = FakeCheckWidget(False)
        self.window.max_input_power = FakeValueWidget(29.5)
        self.window.pa_unit_count_combo = FakeComboWidget(pa_unit_count)
        self.window.power_config_widgets = {
            'PS1': {
                'channels': {
                    'CH1': channel_widgets(2.8, 3.0, True, 0.1, 0.2, False),
                    'CH2': channel_widgets(28.0, 30.0, True, 1.0, 2.0, True),
                }
            }
        }
        self.window.power_assignment_widgets = {
            'driver_enabled': FakeCheckWidget(driver_enabled),
            'driver_power': FakeComboWidget('PS4'),
            'pa_unit1_power': FakeComboWidget('PS1'),
            'pa_unit2_power': FakeComboWidget('PS2'),
            'pa_unit3_power': FakeComboWidget('PS3'),
        }
        self.window._last_save_error = None

    def test_instrument_reader_wraps_instruments_and_preserves_existing_fields(self):
        result = self.window._read_instrument_config_from_ui()

        self.assertIn('instruments', result)
        instruments = result['instruments']
        self.assertEqual(instruments['existing_instrument_field'], 'preserved')
        self.assertEqual(instruments['power_supplies']['PS1']['address'], 'ps1-new')
        self.assertTrue(instruments['power_supplies']['PS1']['enabled'])
        self.assertEqual(instruments['power_supplies']['PS1']['channels'], {'CH1': {'legacy': True}})
        self.assertEqual(instruments['power_supplies']['PS1']['unknown'], 'preserved')
        self.assertEqual(set(instruments['power_supplies']), {'PS1', 'PS2', 'PS3', 'PS4'})

    def test_test_parameter_reader_keeps_invalid_frequency_out_of_fragment(self):
        self.window.freq_edit = FakeTextWidget('not-a-list')
        self.window.start_power = FakeValueWidget(-10)
        self.window.stop_power = FakeValueWidget(10)
        self.window.power_step = FakeValueWidget(1)
        self.window.compression_combo = FakeComboWidget('5dB')
        self.window.attenuator_combo = FakeComboWidget('30dB')
        self.window.driver_mode_check = FakeCheckWidget(False)

        result = self.window._read_test_parameters_from_ui()

        self.assertNotIn('test_frequencies', result)
        self.assertEqual(result['signal_source']['step'], 1)
        self.assertEqual(result['compression_point']['type'], '5dB')
        self.assertFalse(result['driver_mode']['enabled'])

    def test_power_supply_reader_returns_nested_fragment(self):
        self.window.power_config_widgets = {
            'PS1': {
                'channels': {
                    'CH1': channel_widgets(2.8, 3.0, True, 0.1, 0.2, False),
                }
            }
        }

        result = self.window._read_power_supply_config_from_ui()

        self.assertEqual(
            result['instruments']['power_supplies']['PS1']['channels']['CH1']['voltage']['value'],
            2.8,
        )
        self.assertTrue(
            result['instruments']['power_supplies']['PS1']['channels']['CH1']['voltage']['protection_enabled']
        )

    def test_power_assignment_reader_preserves_legacy_roles_and_channels(self):
        self.window.power_assignment_widgets = {
            'driver_enabled': FakeCheckWidget(True),
            'driver_power': FakeComboWidget('PS4'),
            'pa_unit1_power': FakeComboWidget('PS1'),
            'pa_unit2_power': FakeComboWidget('PS2'),
            'pa_unit3_power': FakeComboWidget('PS3'),
        }
        self.window.pa_unit_count_combo = FakeComboWidget('3')

        result = self.window._read_power_assignment_from_ui()['power_supply_assignment']

        self.assertEqual(result['driver_amplifier']['supplies']['main']['name'], 'PS4')
        self.assertEqual(
            list(result['driver_amplifier']['supplies']['main']['channel']),
            ['CH1', 'CH2'],
        )
        self.assertEqual(set(result['dut_amplifier']['supplies']), {'carrier', 'peaking', 'peaking2'})
        self.assertEqual(result['dut_amplifier']['power_supply_count'], 3)


class GuiConfigBuildTests(unittest.TestCase):
    """步骤5-7: 测试 _build_config_from_ui 组装结果的完整性和兼容性。"""

    def setUp(self):
        self.window = MainWindow.__new__(MainWindow)
        self.window.config = {
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
        self.window.sg_address = FakeTextWidget('sg-new')
        self.window.sg_enabled = FakeCheckWidget(True)
        self.window.sa_address = FakeTextWidget('sa-new')
        self.window.sa_enabled = FakeCheckWidget(False)
        for name in ('ps1', 'ps2', 'ps3', 'ps4'):
            setattr(self.window, f'{name}_address', FakeTextWidget(f'{name}-new'))
            setattr(self.window, f'{name}_enabled', FakeCheckWidget(name != 'ps2'))
        self.window.freq_edit = FakeTextWidget('[1.0, 2.0]')
        self.window.start_power = FakeValueWidget(-10)
        self.window.stop_power = FakeValueWidget(10)
        self.window.power_step = FakeValueWidget(1)
        self.window.compression_combo = FakeComboWidget('5dB')
        self.window.attenuator_combo = FakeComboWidget('30dB')
        self.window.driver_mode_check = FakeCheckWidget(False)
        self.window.max_input_power = FakeValueWidget(29.5)
        self.window.pa_unit_count_combo = FakeComboWidget('2')
        self.window.power_config_widgets = {
            'PS1': {
                'channels': {
                    'CH1': channel_widgets(2.8, 3.0, True, 0.1, 0.2, False),
                    'CH2': channel_widgets(28.0, 30.0, True, 1.0, 2.0, True),
                }
            }
        }
        self.window.power_assignment_widgets = {
            'driver_enabled': FakeCheckWidget(False),
            'driver_power': FakeComboWidget('PS4'),
            'pa_unit1_power': FakeComboWidget('PS1'),
            'pa_unit2_power': FakeComboWidget('PS2'),
            'pa_unit3_power': FakeComboWidget('PS3'),
        }
        self.window._last_save_error = None

    def test_build_preserves_top_level_and_instrument_extra_fields(self):
        result = self.window._build_config_from_ui()

        self.assertEqual(result['top_level_extra'], 'preserved')
        self.assertEqual(result['instruments']['extra_instrument'], 'keep')
        self.assertEqual(result['instruments']['power_supplies']['PS1']['unknown'], 'preserved')
        self.assertEqual(result['dut_config']['extra'], 'keep')

    def test_build_writes_instrument_addresses_and_enabled(self):
        result = self.window._build_config_from_ui()

        instruments = result['instruments']
        self.assertEqual(instruments['signal_generator']['address'], 'sg-new')
        self.assertTrue(instruments['signal_generator']['enabled'])
        self.assertEqual(instruments['spectrum_analyzer']['address'], 'sa-new')
        self.assertFalse(instruments['spectrum_analyzer']['enabled'])
        self.assertEqual(instruments['power_supplies']['PS1']['address'], 'ps1-new')
        self.assertTrue(instruments['power_supplies']['PS1']['enabled'])
        self.assertEqual(instruments['power_supplies']['PS4']['address'], 'ps4-new')

    def test_build_replaces_channels_but_preserves_address_and_enabled(self):
        result = self.window._build_config_from_ui()

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
        result = self.window._build_config_from_ui()

        self.assertEqual(result['test_frequencies'], [1.0, 2.0])
        self.assertEqual(result['signal_source']['start_power'], -10)
        self.assertEqual(result['signal_source']['step'], 1)
        self.assertEqual(result['compression_point']['type'], '5dB')
        self.assertEqual(result['attenuator']['type'], '30dB')
        self.assertFalse(result['driver_mode']['enabled'])

    def test_build_invalid_frequency_preserves_old_value(self):
        self.window.freq_edit = FakeTextWidget('not-a-list')
        result = self.window._build_config_from_ui()

        self.assertEqual(result['test_frequencies'], [1.0])

    def test_build_dut_config(self):
        result = self.window._build_config_from_ui()

        self.assertEqual(result['dut_config']['max_input_power'], 29.5)
        self.assertEqual(result['dut_config']['power_supply_count'], 2)
        self.assertEqual(result['dut_config']['extra'], 'keep')

    def test_build_pa_unit_count_1_only_carrier(self):
        self.window.pa_unit_count_combo = FakeComboWidget('1')
        result = self.window._build_config_from_ui()

        supplies = result['power_supply_assignment']['dut_amplifier']['supplies']
        self.assertEqual(set(supplies), {'carrier'})
        self.assertEqual(result['power_supply_assignment']['dut_amplifier']['power_supply_count'], 1)

    def test_build_pa_unit_count_2_carrier_and_peaking(self):
        result = self.window._build_config_from_ui()

        supplies = result['power_supply_assignment']['dut_amplifier']['supplies']
        self.assertEqual(set(supplies), {'carrier', 'peaking'})
        self.assertEqual(result['power_supply_assignment']['dut_amplifier']['power_supply_count'], 2)

    def test_build_pa_unit_count_3_all_roles(self):
        self.window.pa_unit_count_combo = FakeComboWidget('3')
        result = self.window._build_config_from_ui()

        supplies = result['power_supply_assignment']['dut_amplifier']['supplies']
        self.assertEqual(set(supplies), {'carrier', 'peaking', 'peaking2'})
        self.assertEqual(result['power_supply_assignment']['dut_amplifier']['power_supply_count'], 3)

    def test_build_driver_disabled_empty_supplies(self):
        result = self.window._build_config_from_ui()

        driver = result['power_supply_assignment']['driver_amplifier']
        self.assertEqual(driver['power_supply_count'], 0)
        self.assertEqual(driver['supplies'], {})

    def test_build_driver_enabled_with_main(self):
        self.window.power_assignment_widgets['driver_enabled'] = FakeCheckWidget(True)
        result = self.window._build_config_from_ui()

        driver = result['power_supply_assignment']['driver_amplifier']
        self.assertEqual(driver['power_supply_count'], 1)
        self.assertEqual(driver['supplies']['main']['name'], 'PS4')
        self.assertEqual(list(driver['supplies']['main']['channel']), ['CH1', 'CH2'])

    def test_build_channel_order_is_ch1_then_ch2(self):
        result = self.window._build_config_from_ui()

        for supply in result['power_supply_assignment']['dut_amplifier']['supplies'].values():
            self.assertEqual(list(supply['channel']), ['CH1', 'CH2'])

    def test_build_does_not_mutate_self_config(self):
        original = json.dumps(self.window.config, sort_keys=True)
        result = self.window._build_config_from_ui()
        self.assertEqual(json.dumps(self.window.config, sort_keys=True), original)

        # 返回结果也必须与原始配置完全隔离，不能通过未覆盖的嵌套字段反向修改 self.config。
        result['instruments']['power_supplies']['PS2']['channels']['CH1']['custom'] = 'changed'
        self.assertNotIn(
            'custom',
            self.window.config['instruments']['power_supplies']['PS2']['channels']['CH1'],
        )

    def test_build_preserves_channels_not_present_in_ui_widgets(self):
        self.window.config['instruments']['power_supplies']['PS1']['channels']['CH3'] = {
            'legacy_channel': True,
        }

        result = self.window._build_config_from_ui()

        channels = result['instruments']['power_supplies']['PS1']['channels']
        self.assertIn('CH3', channels)
        self.assertEqual(channels['CH3'], {'legacy_channel': True})

    def test_update_config_from_ui_sets_config(self):
        self.window.update_config_from_ui()

        self.assertEqual(self.window.config['instruments']['signal_generator']['address'], 'sg-new')
        self.assertEqual(self.window.config['test_frequencies'], [1.0, 2.0])
        self.assertEqual(self.window.config['dut_config']['power_supply_count'], 2)

    def test_build_top_level_keys_match_legacy(self):
        result = self.window._build_config_from_ui()
        expected_keys = {
            'instruments', 'test_frequencies', 'signal_source',
            'compression_point', 'attenuator', 'driver_mode',
            'dut_config', 'power_supply_assignment', 'top_level_extra',
        }
        self.assertEqual(set(result.keys()), expected_keys)


class GuiConfigSaveTests(unittest.TestCase):
    """步骤6-7: 测试 _save_config_file 和两个保存入口。"""

    def setUp(self):
        self.window = MainWindow.__new__(MainWindow)
        self.window.config = {
            'test_frequencies': [1.0],
            'signal_source': {'start_power': -10, 'stop_power': 10, 'step': 1},
        }
        self.window._last_save_error = None
        self.window.log_messages = []
        self.window._msg_box_calls = []

    def test_save_config_file_writes_json(self):
        with tempfile.TemporaryDirectory() as d:
            config_path = Path(d) / 'config.json'
            with patch('enhanced_main_gui.CONFIG_FILE', config_path):
                result = self.window._save_config_file()

            self.assertTrue(result)
            with config_path.open('r', encoding='utf-8') as f:
                saved = json.load(f)
            self.assertEqual(saved['test_frequencies'], [1.0])
            self.assertEqual(saved['signal_source']['step'], 1)

    def test_save_config_file_returns_false_on_error(self):
        with patch('enhanced_main_gui.CONFIG_FILE', '/nonexistent/path/config.json'):
            result = self.window._save_config_file()

        self.assertFalse(result)
        self.assertIsNotNone(self.window._last_save_error)

    def test_update_and_save_config_success(self):
        self.window.update_config_from_ui = MagicMock()
        self.window.add_log_message = self.window.log_messages.append

        with tempfile.TemporaryDirectory() as d:
            config_path = Path(d) / 'config.json'
            with patch('enhanced_main_gui.CONFIG_FILE', config_path):
                result = self.window.update_and_save_config()

        self.assertTrue(result)
        self.window.update_config_from_ui.assert_called_once()
        self.assertIn('配置已更新并保存', self.window.log_messages)

    def test_update_and_save_config_failure(self):
        self.window.update_config_from_ui = MagicMock()
        self.window.add_log_message = self.window.log_messages.append

        with patch('enhanced_main_gui.CONFIG_FILE', '/nonexistent/path/config.json'):
            result = self.window.update_and_save_config()

        self.assertFalse(result)
        self.assertTrue(any('配置保存失败' in msg for msg in self.window.log_messages))

    def test_save_config_success(self):
        self.window.update_config_from_ui = MagicMock()
        self.window.add_log_message = self.window.log_messages.append

        def fake_msg_box(icon, title, text):
            self.window._msg_box_calls.append((title, text))

        with tempfile.TemporaryDirectory() as d:
            config_path = Path(d) / 'config.json'
            with patch('enhanced_main_gui.CONFIG_FILE', config_path):
                with patch.object(__import__('enhanced_main_gui').QMessageBox, 'information', side_effect=fake_msg_box):
                    self.window.save_config()

        self.assertIn(('保存成功', '配置文件已保存'), self.window._msg_box_calls)
        self.assertIn('配置已保存', self.window.log_messages)

    def test_save_config_failure(self):
        self.window.update_config_from_ui = MagicMock()
        self.window.add_log_message = self.window.log_messages.append

        def fake_warning(icon, title, text):
            self.window._msg_box_calls.append((title, text))

        with patch('enhanced_main_gui.CONFIG_FILE', '/nonexistent/path/config.json'):
            with patch.object(__import__('enhanced_main_gui').QMessageBox, 'warning', side_effect=fake_warning):
                self.window.save_config()

        self.assertTrue(any('保存失败' in title for title, _ in self.window._msg_box_calls))
        self.assertTrue(any('配置保存失败' in msg for msg in self.window.log_messages))


if __name__ == '__main__':
    unittest.main()
