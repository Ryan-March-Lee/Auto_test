"""配置页状态视图模型测试（GUI 重构步骤 6，不需要 Qt）。

覆盖：载入草稿与绑定、测试参数更新、整批设备绑定、正式校验、保存/创建运行 Effect、
运行状态切换与 dirty 基线，以及“视图模型不碰 Qt / 文件 / 控件”的静态边界。
页面侧只验证它把状态与动作委托给视图模型。
"""

import ast
import unittest
from dataclasses import replace
from pathlib import Path

from application.configuration_view_model import (
    CREATE_RUN,
    DEFAULT_RUN_STATUS,
    SAVE_CONFIGURATION,
    ConfigurationAction,
    ConfigurationBindingOutcome,
    ConfigurationPageState,
    ConfigurationTestParameters,
    ConfigurationViewState,
    ConfigurationViewModel,
)
from application.device_binding import ApplyDeviceSelectionRequest
from domain.measurement.values import Decibels, Frequency, PowerDbm

ROOT = Path(__file__).resolve().parents[1]
VIEW_MODEL_SOURCE = ROOT / "application" / "configuration_view_model.py"


def _binding(address, label, model, full_code, device_key):
    """一条当前设备绑定记录（页面读取边界已经规范化后的形状）。"""
    return {
        "vendor": "Rigol Technologies",
        "model": model,
        "label": label,
        "full_code": full_code,
        "device_key": device_key,
        "visa_address": address,
        "last_ip": None,
        "last_port": None,
        "known": True,
        "source": "scan",
    }


def _bindings():
    return {
        "signal_generator": _binding(
            "sim::sg", "SG1", "SMW200A", "105462", "rohde&schwarz:105462"
        ),
        "spectrum_analyzer": _binding(
            "sim::sa", "SA1", "FSW", "101001", "rohde&schwarz:101001"
        ),
        "power_supplies": {
            "DP8B1519": _binding(
                "sim::ps1",
                "DP8B1519",
                "DP832A",
                "DP8B214901519",
                "rigol technologies:dp8b214901519",
            )
        },
    }


def _config():
    """一份完整、可写出正式配置的编辑文档。"""
    return {
        "schema_version": "1.1",
        "test_frequencies": [2.4],
        "signal_source": {"start_power": -10.0, "stop_power": 0.0, "step": 1.0},
        "compression_point": {"type": "5dB"},
        "driver_mode": {"enabled": False},
        "attenuator": {"type": "30dB"},
        "dut_config": {
            "max_input_power": 30.0,
            "power_supply_count": 1,
            "drain_channel": {"supply_id": "DP8B1519", "channel": "CH2"},
        },
        "instruments": {
            "signal_generator": {"address": "sim::sg", "enabled": True},
            "spectrum_analyzer": {"address": "sim::sa", "enabled": True},
            "power_supplies": {
                "DP8B1519": {
                    "label": "DP8B1519",
                    "model": "DP832A",
                    "full_code": "DP8B214901519",
                    "device_key": "rigol technologies:dp8b214901519",
                    "address": "sim::ps1",
                    "enabled": True,
                    "channels": {
                        "CH1": {
                            "voltage": {"value": 2.8},
                            "current": {"value": 0.1},
                        },
                        "CH2": {
                            "voltage": {"value": 28.0},
                            "current": {"value": 2.0},
                        },
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
                        {"supply_id": "DP8B1519", "channel": "CH1"},
                        {"supply_id": "DP8B1519", "channel": "CH2"},
                    ],
                    "inter_channel_delay_s": 2.0,
                },
            },
        },
        "device_bindings": _bindings(),
    }


def _loaded(document=None):
    view = ConfigurationViewModel()
    view.load(_config() if document is None else document, bindings=_bindings())
    return view


def _discovered_power_supply(supply_id="DP8B0885", index=8):
    return {
        "role": "power_supply",
        "supply_id": supply_id,
        "label": supply_id,
        "address": f"TCPIP0::192.168.1.{index}::inst0::INSTR",
        "vendor": "RIGOL TECHNOLOGIES",
        "model": "DP832A",
        "full_code": f"DP8B214900{index}",
        "device_key": f"rigol technologies:dp8b214900{index}",
        "ip": f"192.168.1.{index}",
        "port": 5555,
        "known": True,
    }


def _selection_request(*devices):
    """一次设备勾选的命令 DTO：发现面板的字典列表在交付边界收敛后交给视图模型。"""
    return ApplyDeviceSelectionRequest.from_assignments(devices or ())


class ConfigurationViewModelLoadTests(unittest.TestCase):
    """载入：草稿、绑定、运行状态与干净基线。"""

    def test_load_builds_the_typed_draft_and_the_exact_bindings(self):
        state = _loaded().state

        self.assertEqual(state.draft.frequencies, (Frequency(2.4),))
        self.assertEqual(state.draft.sweep.start, PowerDbm(-10.0))
        self.assertEqual(state.draft.sweep.stop, PowerDbm(0.0))
        self.assertEqual(state.draft.sweep.step, Decibels(1.0))
        self.assertEqual(state.draft.compression_target, Decibels(5.0))
        self.assertEqual(state.draft.attenuator, Decibels(30.0))
        self.assertEqual(state.draft.max_dut_input_power, PowerDbm(30.0))
        self.assertFalse(state.draft.driver_enabled)
        self.assertEqual(state.draft.dut_supply_count, 1)
        self.assertEqual(len(state.draft.dut_channels), 2)
        self.assertEqual(state.bindings_snapshot(), _bindings())
        self.assertEqual(state.run_status, DEFAULT_RUN_STATUS)
        self.assertFalse(state.dirty)
        self.assertTrue(state.validation.valid)

    def test_load_reports_unreadable_fields_without_guessing_defaults(self):
        document = _config()
        document["test_frequencies"] = ["bad"]
        document["driver_mode"] = {"enabled": "yes"}

        state = _loaded(document).state

        self.assertEqual(state.draft.frequencies, ())
        self.assertIsNone(state.draft.driver_enabled)
        self.assertIn(
            "configuration.invalid_frequency",
            {issue.code for issue in state.validation.errors},
        )
        self.assertIn(
            "configuration.invalid_type",
            {issue.code for issue in state.validation.errors},
        )
        # 载入后的基线就是这份规范化结果：坏字段不会让页面一打开就“已修改”。
        self.assertFalse(state.dirty)

    def test_load_rejects_non_object_documents_as_caller_contract_errors(self):
        with self.assertRaises(TypeError):
            ConfigurationViewModel().load([])

    def test_state_is_an_immutable_snapshot(self):
        view = _loaded()
        state = view.state

        view.update_test_parameters(
            ConfigurationTestParameters(frequencies=(Frequency(3.5),))
        )

        self.assertEqual(state.draft.frequencies, (Frequency(2.4),))
        with self.assertRaises(TypeError):
            state.bindings["signal_generator"] = {}  # type: ignore[index]


class ConfigurationViewModelFormSyncTests(unittest.TestCase):
    """步骤 7：页面读数经 ``update_form`` 收进整份草稿，但不重置干净基线。"""

    def test_update_form_syncs_power_channels_and_plans(self):
        view = _loaded()
        document = _config()
        document["instruments"]["power_supplies"]["DP8B1519"]["channels"]["CH1"][
            "voltage"
        ]["value"] = 5.0
        document["power_supply_assignment"]["dut_amplifier"]["power_plan"][
            "power_sequence"
        ].reverse()

        state = view.update_form(document)

        supply = state.draft.power_supplies[0]
        self.assertEqual(supply.channel("CH1").voltage, 5.0)
        self.assertEqual(
            [(route.supply_id, route.channel) for route in state.draft.dut_channels],
            [("DP8B1519", "CH2"), ("DP8B1519", "CH1")],
        )
        self.assertTrue(state.dirty)

    def test_update_form_does_not_reset_the_clean_baseline(self):
        view = _loaded()
        document = _config()
        document["test_frequencies"] = [5.8]

        view.update_form(document)
        self.assertTrue(view.dirty)

        view.update_form(document)
        self.assertTrue(view.dirty)

    def test_update_form_keeps_the_run_status(self):
        view = _loaded()
        view.set_run_status("当前运行: run-20261102")

        state = view.update_form(_config())

        self.assertEqual(state.run_status, "当前运行: run-20261102")

    def test_update_form_rejects_non_object_documents(self):
        with self.assertRaises(TypeError):
            _loaded().update_form([])


class ConfigurationTestParametersTests(unittest.TestCase):
    """测试参数快照：类型化、可由文档转换、只替换本组字段。"""

    def test_from_document_reads_values_and_collects_issues(self):
        parameters, issues = ConfigurationTestParameters.from_document(
            {
                "test_frequencies": [1.0, 2.0],
                "signal_source": {"start_power": -5, "stop_power": 5, "step": 1},
                "compression_point": {"type": "3dB"},
                "attenuator": {"type": "40dB"},
                "dut_config": {"max_input_power": 20, "power_supply_count": 2},
                "driver_mode": {"enabled": True, "estimated_gain_db": 20.5},
            }
        )

        self.assertTrue(issues.valid, issues.messages())
        self.assertEqual(
            parameters.frequencies, (Frequency(1.0), Frequency(2.0))
        )
        self.assertEqual(parameters.compression_target, Decibels(3.0))
        self.assertEqual(parameters.attenuator, Decibels(40.0))
        self.assertEqual(parameters.max_dut_input_power, PowerDbm(20.0))
        self.assertEqual(parameters.dut_supply_count, 2)
        self.assertTrue(parameters.driver_enabled)
        self.assertEqual(parameters.driver_gain_estimate, Decibels(20.5))

    def test_from_document_keeps_invalid_values_missing(self):
        parameters, issues = ConfigurationTestParameters.from_document(
            {"test_frequencies": ["bad"], "dut_config": {"power_supply_count": 2.5}}
        )

        self.assertEqual(parameters.frequencies, ())
        self.assertIsNone(parameters.dut_supply_count)
        self.assertFalse(issues.valid)

    def test_constructor_reuses_the_draft_type_rules(self):
        with self.assertRaises(TypeError):
            ConfigurationTestParameters(frequencies=(object(),))
        with self.assertRaises(TypeError):
            ConfigurationTestParameters(dut_supply_count=True)
        with self.assertRaises(TypeError):
            ConfigurationTestParameters(driver_enabled="yes")  # type: ignore[arg-type]

    def test_update_replaces_only_the_test_parameters_section(self):
        view = _loaded()
        before = view.state

        state = view.update_test_parameters(
            ConfigurationTestParameters(
                frequencies=(Frequency(3.5),),
                compression_target=Decibels(1.0),
                dut_supply_count=2,
                driver_enabled=False,
            )
        )

        self.assertEqual(state.draft.frequencies, (Frequency(3.5),))
        self.assertEqual(state.draft.compression_target, Decibels(1.0))
        self.assertEqual(state.draft.dut_supply_count, 2)
        # 未参与更新的电源参数与绑定保持原样。
        self.assertEqual(
            state.draft.power_supplies, before.draft.power_supplies
        )
        self.assertEqual(state.bindings_snapshot(), before.bindings_snapshot())
        self.assertTrue(state.dirty)
        self.assertFalse(before.dirty)

    def test_update_rejects_plain_mappings_as_caller_contract_errors(self):
        with self.assertRaises(TypeError):
            _loaded().update_test_parameters({"test_frequencies": [1.0]})

    def test_update_does_not_invent_a_reading_the_caller_could_not_parse(self):
        view = _loaded()
        parameters, issues = ConfigurationTestParameters.from_document(
            {"test_frequencies": ["not-a-frequency"]}
        )
        self.assertFalse(issues.valid)

        state = view.update_test_parameters(parameters)

        # 快照保持“没读到”，不替用户猜值；正式校验仍会在这里拦下空频率。
        self.assertEqual(state.draft.frequencies, ())
        self.assertTrue(state.dirty)
        self.assertFalse(view.validate().valid)

    def test_update_clears_stale_issues_of_the_replaced_fields(self):
        document = _config()
        document["compression_point"] = {"type": "not-a-compression"}
        view = _loaded(document)
        self.assertFalse(view.validation.valid)

        state = view.update_test_parameters(
            ConfigurationTestParameters(
                frequencies=(Frequency(2.4),), compression_target=Decibels(3.0)
            )
        )

        self.assertTrue(state.validation.valid, state.validation.messages())


class ConfigurationViewModelBindingTests(unittest.TestCase):
    """设备选择：成功整批替换、失败保持不变。"""

    def test_successful_selection_replaces_every_binding(self):
        view = _loaded()

        outcome = view.apply_device_selection(
            _selection_request(_discovered_power_supply())
        )

        self.assertIsInstance(outcome, ConfigurationBindingOutcome)
        self.assertTrue(outcome.ok, outcome.messages())
        self.assertEqual(
            set(outcome.state.bindings), {"power_supplies"}
        )
        self.assertEqual(
            list(outcome.state.bindings["power_supplies"]), ["DP8B0885"]
        )
        # 页面拿到的是可以自由读写的副本。
        snapshot = outcome.state.bindings_snapshot()
        snapshot["power_supplies"].clear()
        self.assertEqual(
            list(view.bindings["power_supplies"]), ["DP8B0885"]
        )
        self.assertTrue(outcome.state.dirty)

    def test_rejected_selection_keeps_the_previous_bindings_and_state(self):
        view = _loaded()
        before = view.state

        outcome = view.apply_device_selection(ApplyDeviceSelectionRequest())

        self.assertFalse(outcome.ok)
        self.assertIn("请先在发现结果中勾选本次测试所需设备", outcome.messages()[0])
        self.assertEqual(outcome.state.bindings_snapshot(), before.bindings_snapshot())
        self.assertEqual(outcome.state.draft, before.draft)
        self.assertFalse(outcome.state.dirty)

    def test_selection_accepts_the_command_dto(self):
        outcome = _loaded().apply_device_selection(
            _selection_request(_discovered_power_supply())
        )

        self.assertTrue(outcome.ok, outcome.messages())

    def test_selection_rejects_a_dictionary_list(self):
        """字典列表只在发现面板交付边界收敛；视图模型只接受命令 DTO（步骤 13）。"""
        with self.assertRaises(TypeError):
            _loaded().apply_device_selection([_discovered_power_supply()])

    def test_nested_binding_records_are_read_only_through_the_state(self):
        view = _loaded()
        record = view.state.bindings["power_supplies"]["DP8B1519"]

        with self.assertRaises(TypeError):
            record["label"] = "changed"  # type: ignore[index]
        with self.assertRaises(TypeError):
            view.state.bindings["power_supplies"]["DP8B1519"] = {}  # type: ignore[index]
        self.assertEqual(record["label"], "DP8B1519")
        self.assertFalse(view.dirty)

    def test_load_copies_nested_bindings_away_from_the_caller(self):
        bindings = _bindings()
        view = ConfigurationViewModel()
        view.load(_config(), bindings=bindings)

        bindings["power_supplies"]["DP8B1519"]["label"] = "changed"

        self.assertEqual(
            view.state.bindings["power_supplies"]["DP8B1519"]["label"], "DP8B1519"
        )
        self.assertFalse(view.dirty)


class ConfigurationViewModelValidationTests(unittest.TestCase):
    """正式校验与页面动作：结构化问题、Effect 文档。"""

    def test_validate_returns_no_errors_for_a_complete_configuration(self):
        view = _loaded()

        result = view.validate()

        self.assertTrue(result.valid, result.messages())
        self.assertIs(view.state.validation, result)

    def test_validate_reports_run_and_connection_issues_with_source_codes(self):
        document = _config()
        document["test_frequencies"] = []
        view = _loaded(document)

        result = view.validate()

        self.assertFalse(result.valid)
        self.assertEqual(
            {issue.code for issue in result.errors},
            {"configuration.run_validation"},
        )
        self.assertEqual(
            {issue.path for issue in result.errors}, {"frequencies.values"}
        )

    def test_save_returns_the_authored_document_effect(self):
        view = _loaded()

        action = view.save()

        self.assertIsInstance(action, ConfigurationAction)
        self.assertEqual(action.kind, SAVE_CONFIGURATION)
        self.assertTrue(action.ok, action.validation.messages())
        document = action.to_document()
        self.assertEqual(document["schema_version"], "1.1")
        self.assertIn("test_plan", document)
        self.assertIn("run_mapping", document)
        self.assertEqual(document["test_frequencies"], [2.4])
        # Effect 是快照：改写返回值不影响动作本身。
        document["test_frequencies"].append(9.9)
        self.assertEqual(action.to_document()["test_frequencies"], [2.4])

    def test_create_run_uses_the_same_document_but_a_different_kind(self):
        view = _loaded()

        saved = view.save()
        run = view.create_run()

        self.assertEqual(run.kind, CREATE_RUN)
        self.assertTrue(run.ok, run.validation.messages())
        self.assertEqual(run.to_document(), saved.to_document())

    def test_invalid_configuration_blocks_both_actions(self):
        document = _config()
        document["test_frequencies"] = []
        view = _loaded(document)

        for action in (view.save(), view.create_run()):
            with self.subTest(kind=action.kind):
                self.assertFalse(action.ok)
                self.assertEqual(action.to_document(), {})
                self.assertFalse(action.validation.valid)

    def test_unmodelled_top_level_fields_survive_the_action(self):
        document = _config()
        document["custom_block"] = {"keep": True}
        view = _loaded(document)

        action = view.save()

        self.assertTrue(action.ok, action.validation.messages())
        self.assertEqual(action.to_document()["custom_block"], {"keep": True})

    def test_actions_do_not_write_back_legacy_channel_roles(self):
        """旧配置的通道 ``role`` 不进入保存文档，也不影响校验。"""
        document = _config()
        document["instruments"]["power_supplies"]["DP8B1519"]["channels"]["CH1"][
            "role"
        ] = "bias"
        view = _loaded(document)

        action = view.save()

        self.assertTrue(action.ok, action.validation.messages())
        channels = action.to_document()["instruments"]["power_supplies"]["DP8B1519"][
            "channels"
        ]
        self.assertNotIn("role", channels["CH1"])
        self.assertNotIn("role", channels["CH2"])
        self.assertEqual(channels["CH1"]["voltage"]["value"], 2.8)
        self.assertEqual(channels["CH2"]["current"]["value"], 2.0)

    def test_form_readings_cannot_restore_a_removed_channel_role(self):
        """页面读数里的 role 只属于读取兼容，不能在保存时被重新写回。"""
        view = _loaded()
        document = _config()
        document["instruments"]["power_supplies"]["DP8B1519"]["channels"]["CH2"][
            "role"
        ] = "gate"

        view.update_form(document)
        action = view.save()

        self.assertTrue(action.ok, action.validation.messages())
        channel = action.to_document()["instruments"]["power_supplies"]["DP8B1519"][
            "channels"
        ]["CH2"]
        self.assertEqual(set(channel), {"voltage", "current"})

    def test_driver_gain_estimate_survives_form_parameter_updates(self):
        document = _config()
        document["driver_mode"] = {"enabled": True, "estimated_gain_db": 20.5}
        view = _loaded(document)

        # 表单读数还没有专门的增益控件：更新测试参数不能顺手清掉已保存的增益。
        view.update_test_parameters(
            ConfigurationTestParameters.from_draft(view.draft)
        )
        action = view.save()

        self.assertTrue(action.ok, action.validation.messages())
        self.assertEqual(
            action.to_document()["test_plan"]["driver_mode"]["estimated_gain_db"],
            20.5,
        )

    def test_action_rejects_unknown_kinds(self):
        with self.assertRaises(ValueError):
            ConfigurationAction(kind="print")

    def test_action_document_is_read_only_down_to_nested_containers(self):
        document = _config()
        document["custom_block"] = {"keep": True, "items": [1, 2]}
        action = _loaded(document).save()
        self.assertTrue(action.ok, action.validation.messages())

        saved = action.document
        with self.assertRaises(TypeError):
            saved["custom_block"]["keep"] = False  # type: ignore[index]
        with self.assertRaises(AttributeError):
            saved["custom_block"]["items"].append(3)  # type: ignore[union-attr]
        self.assertTrue(saved["custom_block"]["keep"])
        self.assertEqual(saved["custom_block"]["items"], (1, 2))
        # 可写副本是独立的 JSON 形状。
        writable = action.to_document()
        writable["custom_block"]["items"].append(3)
        self.assertEqual(action.to_document()["custom_block"]["items"], [1, 2])

    def test_editing_after_validation_drops_the_stale_formal_conclusion(self):
        document = _config()
        document["test_frequencies"] = []
        view = _loaded(document)
        self.assertFalse(view.validate().valid)
        self.assertFalse(view.state.validation.valid)

        parameters, _issues = ConfigurationTestParameters.from_document(document)
        state = view.update_test_parameters(
            replace(parameters, frequencies=(Frequency(2.4),))
        )

        self.assertTrue(state.validation.valid, state.validation.messages())
        self.assertTrue(view.validate().valid, view.state.validation.messages())


class ConfigurationViewModelDirtyAndStatusTests(unittest.TestCase):
    """dirty 基线与运行状态：状态切换不写文件、不产生业务副作用。"""

    def test_mark_clean_resets_dirty_after_an_edit(self):
        view = _loaded()
        view.update_test_parameters(
            ConfigurationTestParameters(frequencies=(Frequency(5.8),))
        )
        self.assertTrue(view.dirty)

        state = view.mark_clean()

        self.assertFalse(state.dirty)
        self.assertFalse(view.dirty)

    def test_load_starts_a_new_clean_baseline(self):
        view = _loaded()
        view.update_test_parameters(
            ConfigurationTestParameters(frequencies=(Frequency(5.8),))
        )

        state = view.load(_config(), bindings=_bindings())

        self.assertFalse(state.dirty)

    def test_run_status_is_part_of_the_state_and_does_not_touch_dirty(self):
        view = _loaded()

        state = view.set_run_status("当前运行: run-20261102")

        self.assertEqual(state.run_status, "当前运行: run-20261102")
        self.assertEqual(view.state.run_status, "当前运行: run-20261102")
        self.assertFalse(state.dirty)

    def test_run_status_rejects_empty_or_non_text_values(self):
        view = _loaded()
        for value in ("", "   ", None, 42):
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError)):
                    view.set_run_status(value)

    def test_state_validation_rejects_wrong_types(self):
        from application.configuration_draft import ConfigurationDraft

        with self.assertRaises(TypeError):
            ConfigurationPageState(draft={})  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            ConfigurationPageState(
                draft=ConfigurationDraft(), run_status=1  # type: ignore[arg-type]
            )
        with self.assertRaises(TypeError):
            ConfigurationPageState(
                draft=ConfigurationDraft(), dirty="yes"  # type: ignore[arg-type]
            )


class ConfigurationViewStateTests(unittest.TestCase):
    """步骤 8 的只读视图：连接页只读它，不再读配置页的绑定/配置内部状态。"""

    def test_view_state_exposes_bindings_and_saved_instrument_addresses(self):
        view = _loaded()

        state = view.view_state()

        self.assertEqual(
            set(state.bindings), {"signal_generator", "spectrum_analyzer", "power_supplies"}
        )
        # 顺序与文档一致：信号源、频谱仪，然后是电源集合的声明顺序。
        self.assertEqual(
            state.instrument_addresses, ("sim::sg", "sim::sa", "sim::ps1")
        )
        self.assertEqual(state.legacy_binding_count, 0)

    def test_view_state_tracks_the_current_document_and_bindings(self):
        view = _loaded()
        document = _config()
        document["instruments"]["signal_generator"]["address"] = "sim::new-sg"
        view.update_form(document, bindings=_bindings())

        self.assertEqual(view.view_state().instrument_addresses[0], "sim::new-sg")

    def test_view_state_counts_legacy_bindings_without_changing_them(self):
        legacy = _bindings()
        legacy["signal_generator"]["source"] = "legacy_config"
        view = ConfigurationViewModel()
        view.load(_config(), bindings=legacy)

        state = view.view_state()

        self.assertEqual(state.legacy_binding_count, 1)
        self.assertEqual(state.bindings["signal_generator"]["source"], "legacy_config")

    def test_view_state_bindings_are_read_only(self):
        state = _loaded().view_state()

        with self.assertRaises(TypeError):
            state.bindings["signal_generator"] = {}  # type: ignore[index]

    def test_view_state_rejects_invalid_addresses_and_counts(self):
        with self.assertRaises(TypeError):
            ConfigurationViewState(instrument_addresses=("ok", None))  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            ConfigurationViewState(legacy_binding_count="1")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            ConfigurationViewState(legacy_binding_count=-1)


class ConfigurationViewModelBoundaryTests(unittest.TestCase):
    """静态边界：视图模型不导入 Qt、不读写文件，页面只委托它。"""

    def test_module_does_not_import_qt_or_filesystem_modules(self):
        tree = ast.parse(VIEW_MODEL_SOURCE.read_text(encoding="utf-8-sig"))
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        forbidden = {
            "PySide6",
            "PyQt5",
            "pathlib",
            "os",
            "json",
            "infrastructure",
        }
        self.assertEqual(
            {
                module
                for module in modules
                if module.split(".")[0] in forbidden
            },
            set(),
        )

    def test_configuration_page_delegates_state_and_actions_to_the_view_model(self):
        page_source = (
            ROOT / "presentation" / "qt" / "configuration_page.py"
        ).read_text(encoding="utf-8-sig")

        self.assertIn("from application.configuration_view_model import", page_source)
        self.assertIn("self.view_model.apply_device_selection(", page_source)
        self.assertIn("self.view_model.update_form(", page_source)
        self.assertIn("self.view_model.update_test_parameters(", page_source)
        self.assertIn("self.view_model.set_run_status(", page_source)
        self.assertIn("self.view_model.mark_clean()", page_source)
        self.assertNotIn("DeviceBindingService()", page_source)

    def test_page_no_longer_copies_the_binding_validation_loop(self):
        """步骤 5 的边界保持不变：页面不出现三轮校验的服务调用与规则副本。"""
        page_source = (
            ROOT / "presentation" / "qt" / "configuration_page.py"
        ).read_text(encoding="utf-8-sig")
        for rule_copy in ("_checked_devices", "supply_id_problem(", "binding_identity_problem("):
            self.assertNotIn(rule_copy, page_source)


if __name__ == "__main__":
    unittest.main()
