"""配置读取、组装与保存：控件读取归 ConfigFormState，保存归运行协调器。"""

import json
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


class FakePlanEditor:
    """上电计划编辑器的替身：只实现表单读取用到的两个入口。"""

    def __init__(self, sequence=(), active=True):
        self.sequence = [dict(route) for route in sequence]
        self.active = active

    def read_plan(self):
        return {
            "power_sequence": [dict(route) for route in self.sequence],
            "inter_channel_delay_s": 0.0,
        }

    def read_active_plan(self):
        if not self.active:
            return {"power_sequence": [], "inter_channel_delay_s": 0.0}
        return self.read_plan()


class _WidgetHost:
    """控件宿主替身：ConfigFormState 只按属性名读取控件。"""


def channel_widgets(voltage, voltage_enabled, current, current_enabled, role=None):
    """一条通道的控件替身；保护阈值由工作值推导，控件里只有启用开关。"""
    controls = {
        'voltage': FakeValueWidget(voltage),
        'voltage_protection_enabled': FakeCheckWidget(voltage_enabled),
        'current': FakeValueWidget(current),
        'current_protection_enabled': FakeCheckWidget(current_enabled),
    }
    if role is not None:
        controls['role'] = FakeComboWidget(role)
    return controls


class TestConfigurationPageStub:
    """配置页替身：只实现协调器需要的窄接口。

    控件读取仍交给 :class:`ConfigFormState`，与真实 ``TestConfigurationPage.build_config()``
    走同一条组装路径，因此协调器测试不复制配置组装逻辑。
    """

    def __init__(self, widgets, config=None):
        self.widgets = widgets
        self.config = config if isinstance(config, dict) else {}
        self.statuses = []
        self.applied = []

    def validate(self):
        return not ConfigFormState(self.config, self.widgets).validation_errors()

    def build_config(self, base=None):
        base_config = self.config if base is None else base
        return ConfigFormState(base_config, self.widgets).build_config()

    def apply_config(self, config):
        self.applied.append(config)
        self.config = dict(config)

    def set_run_status(self, text):
        self.statuses.append(text)

    def driver_mode_requested(self):
        return bool(getattr(self.widgets, 'test_mode_enabled', False))


def _coordinator(*, config=None, service=None, widgets=None, log=None, warn=None, error=None):
    page = TestConfigurationPageStub(widgets, config) if widgets is not None else None
    coordinator = ApplicationCoordinator(
        log=log or (lambda _message: None),
        warn=warn or (lambda _title, _message: None),
        error=error or (lambda _message: None),
        gui_data_service=service or MagicMock(),
        controller=MagicMock(),
        config_path=Path("config.json"),
    )
    if config is not None:
        coordinator.config = config
    if page is not None:
        coordinator.bind_test_config_page(page)
    return coordinator


class ConfigFormStateReaderTests(unittest.TestCase):
    """读取片段只属于 ConfigFormState；窗口和协调器都不再持有这些逻辑。"""

    def setUp(self):
        self.config = {
            'instruments': {
                'existing_instrument_field': 'preserved',
                'power_supplies': {
                    'DP8B1519': {
                        'address': 'old-dp8b1519',
                        'enabled': False,
                        'channels': {'CH1': {'legacy': True}},
                        'unknown': 'preserved',
                    },
                    # 旧槽位条目：还没有迁移到短标签，地址与启用状态随绑定失效。
                    'PS1': {
                        'address': 'old-ps1',
                        'enabled': True,
                        'channels': {'CH1': {'legacy': True}},
                    },
                }
            },
            'test_frequencies': [1.0],
            'dut_config': {'max_input_power': 30.0, 'power_supply_count': 1, 'extra': 'keep'},
        }
        self.host = _WidgetHost()
        # 仪器连接只来自设备发现结果（device_bindings），界面没有地址/启用控件。
        # 电源按 supply_id 集合存放，不再用 PS1..PS4 这类槽位键。
        self.host.device_bindings = {
            'signal_generator': {'visa_address': 'sg-new'},
            'spectrum_analyzer': {'visa_address': 'sa-new'},
            'power_supplies': {
                'DP8B1519': {
                    'device_key': 'rigol technologies:dp8b214901519',
                    'visa_address': 'ps1-new',
                },
                'DP8B0885': {
                    'device_key': 'rigol technologies:dp8b174400885',
                    'visa_address': 'ps4-new',
                },
            },
        }
        self.host.freq_start = FakeValueWidget(1.0)
        self.host.freq_stop = FakeValueWidget(1.0)
        self.host.freq_step = FakeValueWidget(0.4)
        self.host.freq_extra = FakeTextWidget('')
        self.host.frequency_import_error = None
        self.reader = ConfigFormState(self.config, self.host)

    def test_power_assignment_reader_writes_plans_only(self):
        self.host.dut_power_plan_editor = FakePlanEditor(
            [
                {"supply_id": "DP8B1519", "channel": "CH1"},
                {"supply_id": "DP8B1519", "channel": "CH2"},
            ]
        )
        self.host.driver_power_plan_editor = FakePlanEditor(
            [{"supply_id": "DP8B0885", "channel": "CH1"}],
            active=False,
        )

        result = self.reader.read_power_assignment()['power_supply_assignment']

        self.assertEqual(
            result['dut_amplifier']['power_plan']['power_sequence'],
            [
                {"supply_id": "DP8B1519", "channel": "CH1"},
                {"supply_id": "DP8B1519", "channel": "CH2"},
            ],
        )
        # 未激活的驱动计划写成空计划：它不占本机电源，不留下上一次的勾选。
        self.assertEqual(
            result['driver_amplifier']['power_plan']['power_sequence'], []
        )
        for group in result.values():
            self.assertEqual(set(group), {'power_plan'}, '不再写 supplies / power_supply_count')

    def test_power_assignment_reader_is_silent_without_plan_editors(self):
        """测试配置页没有计划控件：不能把已保存的电源段改写成空段。"""
        self.assertEqual(self.reader.read_power_assignment(), {})

    def test_instrument_reader_wraps_instruments_and_preserves_existing_fields(self):
        result = self.reader.read_instrument_config()

        self.assertIn('instruments', result)
        instruments = result['instruments']
        self.assertEqual(instruments['existing_instrument_field'], 'preserved')
        self.assertEqual(instruments['signal_generator']['address'], 'sg-new')
        self.assertTrue(instruments['signal_generator']['enabled'])
        self.assertEqual(instruments['spectrum_analyzer']['address'], 'sa-new')
        # 绑定记录覆盖配置里已有的地址，并把该电源标记为启用；集合键同时写成 label。
        supply = instruments['power_supplies']['DP8B1519']
        self.assertEqual(supply['address'], 'ps1-new')
        self.assertTrue(supply['enabled'])
        self.assertEqual(supply['label'], 'DP8B1519')
        self.assertEqual(supply['device_key'], 'rigol technologies:dp8b214901519')
        self.assertEqual(supply['channels'], {'CH1': {'legacy': True}})
        self.assertEqual(supply['unknown'], 'preserved')
        # 未绑定的电源（含旧槽位条目）都保留，只是没有可连接的地址。
        self.assertEqual(
            set(instruments['power_supplies']),
            {'DP8B1519', 'DP8B0885', 'PS1'},
        )

    def test_unbound_role_is_written_as_disabled_without_address(self):
        """没有本次发现的绑定记录：不臆造地址，也不保留存量的连接状态。

        绑定是唯一连接来源，所以未绑定的电源必须显式写成“未启用、无地址”，否则清除
        绑定后旧地址仍会被当成可连接设备。
        """
        instruments = self.reader.read_instrument_config()['instruments']

        self.assertNotIn('address', instruments['power_supplies']['PS1'])
        self.assertFalse(instruments['power_supplies']['PS1']['enabled'])

    def test_test_parameter_reader_keeps_invalid_frequency_out_of_fragment(self):
        self.host.freq_extra = FakeTextWidget('not-a-frequency')
        self.host.start_power = FakeValueWidget(-10)
        self.host.stop_power = FakeValueWidget(10)
        self.host.power_step = FakeValueWidget(1)
        self.host.compression_combo = FakeComboWidget('5dB')
        self.host.attenuator_combo = FakeComboWidget('30dB')
        self.host.test_mode_enabled = False

        result = self.reader.read_test_parameters()

        self.assertNotIn('test_frequencies', result)
        self.assertEqual(result['signal_source']['power_scan_plans'][0]['step'], 1)
        self.assertEqual(result['compression_point']['type'], '5dB')
        self.assertFalse(result['driver_mode']['enabled'])

    def test_power_supply_reader_returns_nested_fragment(self):
        self.host.power_config_widgets = {
            'PS1': {
                'channels': {
                    'CH1': channel_widgets(2.8, True, 0.1, False),
                }
            }
        }

        result = self.reader.read_power_supply_config()

        self.assertEqual(
            result['instruments']['power_supplies']['PS1']['channels']['CH1']['voltage']['value'],
            2.8,
        )
        self.assertEqual(
            result['instruments']['power_supplies']['PS1']['channels']['CH1']['voltage']['protection'],
            3.08,
        )
        self.assertTrue(
            result['instruments']['power_supplies']['PS1']['channels']['CH1']['voltage']['protection_enabled']
        )

    def test_power_supply_reader_does_not_write_the_role_widget(self):
        """步骤 6：通道 role 属于 1.0 只读兼容，控件选择不再进入保存文档。"""
        self.host.power_config_widgets = {
            'PS1': {
                'channels': {
                    'CH1': channel_widgets(2.8, True, 0.1, False, role='drain'),
                    'CH2': channel_widgets(28.0, True, 1.0, True, role='gate'),
                }
            }
        }

        result = self.reader.read_power_supply_config()

        channels = result['instruments']['power_supplies']['PS1']['channels']
        self.assertNotIn('role', channels['CH1'])
        self.assertNotIn('role', channels['CH2'])

    def test_power_supply_reader_drops_existing_role(self):
        self.config['instruments']['power_supplies']['PS1']['channels'] = {
            'CH1': {'role': 'drain'},
            'CH2': {'role': 'gate'},
        }
        self.host.power_config_widgets = {
            'PS1': {
                'channels': {
                    'CH1': channel_widgets(2.8, True, 0.1, False),
                    'CH2': channel_widgets(28.0, True, 1.0, True),
                }
            }
        }

        result = self.reader.read_power_supply_config()

        channels = result['instruments']['power_supplies']['PS1']['channels']
        self.assertNotIn('role', channels['CH1'])
        self.assertNotIn('role', channels['CH2'])

    def test_power_supply_reader_does_not_invent_a_missing_role(self):
        """没有控件、配置里也没有角色时不得按 CH1/CH2 推断。"""
        self.host.power_config_widgets = {
            'PS1': {
                'channels': {
                    'CH1': channel_widgets(2.8, True, 0.1, False),
                }
            }
        }

        result = self.reader.read_power_supply_config()

        channels = result['instruments']['power_supplies']['PS1']['channels']
        self.assertNotIn('role', channels['CH1'])


class CoordinatorConfigBuildTests(unittest.TestCase):
    """步骤5-7: 协调器按当前控件状态组装完整配置，并替换自己的配置副本。"""

    def setUp(self):
        self.config = {
            'instruments': {
                'power_supplies': {
                    'DP8B1519': {
                        'address': 'old-dp8b1519',
                        'enabled': False,
                        'channels': {'CH1': {'legacy': True}},
                        'unknown': 'preserved',
                    },
                    # 旧槽位条目：参数保留，但不是本次绑定的电源。
                    'PS1': {
                        'address': 'old-ps1',
                        'enabled': False,
                        'channels': {'CH1': {'legacy': True}},
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
        self.host.device_bindings = {
            'signal_generator': {'visa_address': 'sg-new'},
            'spectrum_analyzer': {'visa_address': 'sa-new'},
            'power_supplies': {
                'DP8B1519': {
                    'device_key': 'rigol technologies:dp8b214901519',
                    'visa_address': 'ps1-new',
                },
                'DP8B0885': {
                    'device_key': 'rigol technologies:dp8b174400885',
                    'visa_address': 'ps4-new',
                },
            },
        }
        self.host.freq_start = FakeValueWidget(1.0)
        self.host.freq_stop = FakeValueWidget(2.0)
        self.host.freq_step = FakeValueWidget(1.0)
        self.host.freq_extra = FakeTextWidget('')
        self.host.frequency_import_error = None
        self.host.start_power = FakeValueWidget(-10)
        self.host.stop_power = FakeValueWidget(10)
        self.host.power_step = FakeValueWidget(1)
        self.host.compression_combo = FakeComboWidget('5dB')
        self.host.attenuator_combo = FakeComboWidget('30dB')
        self.host.test_mode_enabled = False
        self.host.max_input_power = FakeValueWidget(29.5)
        self.host.power_config_widgets = {
            'DP8B1519': {
                'channels': {
                    'CH1': channel_widgets(2.8, True, 0.1, False),
                    'CH2': channel_widgets(28.0, True, 1.0, True),
                }
            }
        }
        # 驱放测试模式默认关闭，驱动计划此时不生效。
        self.host.dut_power_plan_editor = FakePlanEditor(
            [
                {'supply_id': 'DP8B1519', 'channel': 'CH1'},
                {'supply_id': 'DP8B0885', 'channel': 'CH2'},
            ]
        )
        self.host.driver_power_plan_editor = FakePlanEditor(
            [{'supply_id': 'DP8B0885', 'channel': 'CH1'}], active=False
        )
        self.coordinator = _coordinator(config=self.config, widgets=self.host)

    def test_build_preserves_top_level_and_instrument_extra_fields(self):
        result = self.coordinator.build_configuration_from_ui()

        self.assertEqual(result['top_level_extra'], 'preserved')
        self.assertEqual(result['instruments']['extra_instrument'], 'keep')
        self.assertEqual(
            result['instruments']['power_supplies']['DP8B1519']['unknown'], 'preserved'
        )
        self.assertEqual(result['dut_config']['extra'], 'keep')

    def test_build_writes_instrument_addresses_and_enabled(self):
        result = self.coordinator.build_configuration_from_ui()

        instruments = result['instruments']
        self.assertEqual(instruments['signal_generator']['address'], 'sg-new')
        self.assertTrue(instruments['signal_generator']['enabled'])
        self.assertEqual(instruments['spectrum_analyzer']['address'], 'sa-new')
        self.assertTrue(instruments['spectrum_analyzer']['enabled'])
        self.assertEqual(instruments['power_supplies']['DP8B1519']['address'], 'ps1-new')
        self.assertTrue(instruments['power_supplies']['DP8B1519']['enabled'])
        self.assertEqual(instruments['power_supplies']['DP8B0885']['address'], 'ps4-new')
        # 未绑定设备的电源（含旧槽位条目）被显式置为未启用、且不再保留旧地址：
        # 连接只来自绑定。
        for key in ('PS1', 'PS2'):
            self.assertNotIn('address', instruments['power_supplies'][key])
            self.assertFalse(instruments['power_supplies'][key]['enabled'])

    def test_build_replaces_channels_but_preserves_address_and_enabled(self):
        result = self.coordinator.build_configuration_from_ui()

        ps1 = result['instruments']['power_supplies']['DP8B1519']
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
        self.assertEqual(
            result['signal_source']['power_scan_plans'][0]['start_power'], -10
        )
        self.assertEqual(
            result['signal_source']['power_scan_plans'][0]['step'], 1
        )
        self.assertEqual(result['compression_point']['type'], '5dB')
        self.assertEqual(result['attenuator']['type'], '30dB')
        self.assertFalse(result['driver_mode']['enabled'])

    def test_build_invalid_frequency_preserves_old_value(self):
        self.host.freq_extra = FakeTextWidget('not-a-frequency')
        result = self.coordinator.build_configuration_from_ui()

        self.assertEqual(result['test_frequencies'], [1.0])

    def test_build_dut_config(self):
        result = self.coordinator.build_configuration_from_ui()

        self.assertEqual(result['dut_config']['max_input_power'], 29.5)
        self.assertNotIn('power_supply_count', result['dut_config'])
        self.assertEqual(result['dut_config']['extra'], 'keep')

    def test_build_writes_the_dut_power_plan_as_read(self):
        """上电计划是唯一的电源选择来源：顺序与路由原样写入，不再由 PA 数量推导。"""
        result = self.coordinator.build_configuration_from_ui()

        dut = result['power_supply_assignment']['dut_amplifier']
        self.assertEqual(
            dut['power_plan']['power_sequence'],
            [
                {'supply_id': 'DP8B1519', 'channel': 'CH1'},
                {'supply_id': 'DP8B0885', 'channel': 'CH2'},
            ],
        )
        self.assertEqual(set(dut), {'power_plan'}, '不再写 supplies / power_supply_count')

    def test_build_driver_disabled_writes_an_empty_plan(self):
        result = self.coordinator.build_configuration_from_ui()

        driver = result['power_supply_assignment']['driver_amplifier']
        self.assertEqual(driver['power_plan']['power_sequence'], [])
        self.assertEqual(set(driver), {'power_plan'})

    def test_build_driver_enabled_with_an_empty_plan_keeps_the_mode(self):
        """驱放自带供电：仍是有驱放测试，驱动计划保持为空。"""
        self.host.test_mode_enabled = True
        self.host.driver_power_plan_editor = FakePlanEditor(())
        result = self.coordinator.build_configuration_from_ui()

        self.assertTrue(result['driver_mode']['enabled'])
        self.assertNotIn('power_source', result['driver_mode'])
        driver = result['power_supply_assignment']['driver_amplifier']
        self.assertEqual(driver['power_plan']['power_sequence'], [])

    def test_build_driver_enabled_with_dc_supply_writes_the_plan(self):
        self.host.test_mode_enabled = True
        self.host.driver_power_plan_editor = FakePlanEditor(
            [{'supply_id': 'DP8B0885', 'channel': 'CH1'}]
        )
        result = self.coordinator.build_configuration_from_ui()

        driver = result['power_supply_assignment']['driver_amplifier']
        self.assertEqual(
            driver['power_plan']['power_sequence'],
            [{'supply_id': 'DP8B0885', 'channel': 'CH1'}],
        )
        self.assertNotIn('power_source', result['driver_mode'])

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
        self.assertNotIn('power_supply_count', self.coordinator.config['dut_config'])
        # 配置页持有的副本必须与协调器一致。
        self.assertIs(self.coordinator.test_config_page.config, self.coordinator.config)

    def test_build_top_level_keys_match_legacy(self):
        result = self.coordinator.build_configuration_from_ui()
        expected_keys = {
            'instruments', 'test_frequencies', 'signal_source',
            'compression_point', 'attenuator', 'driver_mode',
            'dut_config', 'power_supply_assignment', 'top_level_extra',
        }
        self.assertEqual(set(result.keys()), expected_keys)

    def test_build_drops_legacy_wiring_block(self):
        """接线由各阶段测量前的连接图对话框确认，不再写回配置。"""
        self.coordinator.config['wiring'] = {
            'confirmed': True,
            'connection_note': 'previous',
            'confirmed_at': 'old',
            'confirmation_source': 'old',
        }
        result = self.coordinator.build_configuration_from_ui()
        self.assertNotIn('wiring', result)


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

    def test_save_configuration_returns_false_on_error(self):
        self.service.configuration.save.side_effect = OSError("test write failure")
        result = self.coordinator.save_configuration()

        self.assertFalse(result)
        self.assertIsNotNone(self.coordinator.last_save_error)

    def test_save_refuses_a_legacy_document_before_writing(self):
        """1.0 文档在保存边界即被拒绝：不迁移、不改写、不调用仓储。"""
        legacy = {"schema_version": "1.0", "test_frequencies": [1.0]}
        coordinator = _coordinator(config=legacy, service=self.service)

        self.assertFalse(coordinator.save_configuration())

        self.assertIn("迁移器", str(coordinator.last_save_error))
        self.assertEqual(coordinator.config, legacy)
        self.assertEqual(coordinator.config["schema_version"], "1.0")
        self.service.configuration.save.assert_not_called()

    def test_read_only_document_is_not_rewritten_from_page_widgets(self):
        """只读文档也不接受页面读数：保存前不会先把 1.0 就地合并成新草稿。"""

        class _LegacyPage:
            def __init__(self):
                self.config = {}

            def validate(self):
                return True

            def build_config(self, base=None):
                return {"schema_version": "1.1", "test_frequencies": [9.9]}

            def apply_config(self, config):
                self.config = dict(config)

            def set_run_status(self, text):
                pass

            def driver_mode_requested(self):
                return False

        legacy = {"schema_version": "1.0", "test_frequencies": [1.0]}
        coordinator = _coordinator(config=legacy, service=self.service)
        coordinator.bind_test_config_page(_LegacyPage())

        self.assertEqual(coordinator.update_configuration_from_ui(), legacy)
        self.assertFalse(coordinator.save_configuration())
        self.assertEqual(coordinator.config, legacy)
        self.service.configuration.save.assert_not_called()

    def test_save_configuration_accepts_a_two_supply_plan(self):
        """步骤 10：多电源上电计划已成为正式 schema，保存写出的 run_mapping 能通过正式校验。"""
        self.coordinator.config = {
            "schema_version": "1.1",
            "test_frequencies": [2.4],
            "signal_source": {"start_power": -10.0, "stop_power": 0.0, "step": 1.0},
            "compression_point": {"type": "5dB"},
            "driver_mode": {"enabled": True},
            "attenuator": {"type": "30dB"},
            "dut_config": {
                "max_input_power": 30.0,
                "drain_channel": {"supply_id": "DP8B0885", "channel": "CH2"},
            },
            "instruments": {
                "signal_generator": {"address": "sim::sg", "model": "SG", "enabled": True},
                "spectrum_analyzer": {"address": "sim::sa", "model": "SA", "enabled": True},
                "power_supplies": {
                    "DP8B1519": {
                        "label": "DP8B1519",
                        "model": "DP832A",
                        "full_code": "DP8B214901519",
                        "device_key": "rigol technologies:dp8b214901519",
                        "address": "sim::ps1",
                        "enabled": True,
                        "channels": {
                            "CH1": {"voltage": {"value": 5.0}, "current": {"value": 1.0}},
                            "CH2": {"voltage": {"value": 28.0}, "current": {"value": 2.0}},
                        },
                    },
                    "DP8B0885": {
                        "label": "DP8B0885",
                        "model": "DP832A",
                        "full_code": "DP8B174400885",
                        "device_key": "rigol technologies:dp8b174400885",
                        "address": "sim::ps2",
                        "enabled": True,
                        "channels": {
                            "CH1": {"voltage": {"value": 5.0}, "current": {"value": 1.0}},
                            "CH2": {"voltage": {"value": 28.0}, "current": {"value": 2.0}},
                        },
                    },
                },
            },
            "power_supply_assignment": {
                "dut_amplifier": {
                    "power_plan": {
                        "power_sequence": [
                            {"supply_id": "DP8B1519", "channel": "CH1"},
                            {"supply_id": "DP8B0885", "channel": "CH2"},
                        ],
                        "inter_channel_delay_s": 2.0,
                    },
                },
                "driver_amplifier": {
                    "power_plan": {
                        "power_sequence": [
                            {"supply_id": "DP8B0885", "channel": "CH1"}
                        ],
                        "inter_channel_delay_s": 0.0,
                    },
                },
            },
        }

        result = self.coordinator.save_configuration()

        self.assertTrue(result, self.coordinator.last_save_error)
        self.service.configuration.save.assert_called_once()
        written = self.service.configuration.save.call_args[0][0]
        power_supplies = written["run_mapping"]["instruments"]["power_supplies"]
        self.assertEqual(sorted(power_supplies), ["DP8B0885", "DP8B1519"])
        self.assertEqual(
            written["run_mapping"]["dut_power_plan"]["power_sequence"],
            [
                {"supply_id": "DP8B1519", "channel": "CH1"},
                {"supply_id": "DP8B0885", "channel": "CH2"},
            ],
        )
        from domain.configuration.models import (
            RunConfiguration,
            RunResourceMapping,
            TestPlan,
            validate_run_configuration,
        )

        configuration = RunConfiguration(
            TestPlan.from_dict(written["test_plan"]),
            RunResourceMapping.from_dict(written["run_mapping"]),
        )
        self.assertTrue(validate_run_configuration(configuration).valid)


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
