import copy
import json
import tempfile
import unittest
from pathlib import Path

from domain.configuration.models import RunResourceMapping, validate_run_mapping
from domain.configuration.rules import validate_config, validate_hardware_connections
from infrastructure.config.validation import load_config, validate_config_file


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "config_driver_enabled_no_assignment.json"
# 旧运行映射（单数 ``instruments.power_supply``、裸通道名）的固定样例。历史运行目录
# 仍按 1.0 读取；仓库当前生产配置已经是 1.1，不能再用它当旧格式样例。
LEGACY_RUN_MAPPING_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "power_supply_identity"
    / "run_mapping_legacy_single.json"
)


class ConfigValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 固定夹具：驱动模式启用但未配置驱动供电分配。
        # 不读取现场 config.json，避免测试结果随每日现场配置变化。
        with FIXTURE_PATH.open("r", encoding="utf-8") as config_file:
            cls.base_config = json.load(config_file)

    def test_current_config_is_readable_without_a_driver_power_warning(self):
        result = validate_config(self.base_config)
        self.assertTrue(result.valid, result.errors)
        self.assertFalse(
            any("供电方式" in issue.message for issue in result.warnings)
        )

    def test_missing_top_level_section_is_error(self):
        config = copy.deepcopy(self.base_config)
        del config["signal_source"]
        result = validate_config(config)
        self.assertFalse(result.valid)
        self.assertTrue(any(issue.path == "signal_source" for issue in result.errors))

    def test_invalid_power_range_and_step_are_errors(self):
        config = copy.deepcopy(self.base_config)
        config["signal_source"].update({"start_power": 0, "stop_power": -1, "step": 0})
        result = validate_config(config)
        paths = {issue.path for issue in result.errors}
        self.assertIn("signal_source", paths)
        self.assertIn("signal_source.step", paths)

    def test_invalid_db_value_is_error(self):
        config = copy.deepcopy(self.base_config)
        config["attenuator"]["type"] = "attenuator"
        result = validate_config(config)
        self.assertIn("attenuator.type", {issue.path for issue in result.errors})

    def test_non_finite_numbers_are_errors(self):
        config = copy.deepcopy(self.base_config)
        config["test_frequencies"][0] = float("nan")
        config["signal_source"]["start_power"] = float("inf")
        config["instruments"]["power_supplies"]["PS4"]["channels"]["CH1"]["voltage"]["value"] = float("-inf")
        result = validate_config(config)
        paths = {issue.path for issue in result.errors}
        self.assertIn("test_frequencies[0]", paths)
        self.assertIn("signal_source.start_power", paths)
        self.assertIn("instruments.power_supplies.PS4.channels.CH1.voltage.value", paths)

    def test_channel_power_on_delay_must_be_in_range(self):
        for bad in (-0.1, 61):
            with self.subTest(delay=bad):
                config = copy.deepcopy(self.base_config)
                config["instruments"]["power_supplies"]["PS4"]["channels"]["CH1"][
                    "power_on_delay_s"
                ] = bad
                result = validate_config(config)
                self.assertIn(
                    "instruments.power_supplies.PS4.channels.CH1.power_on_delay_s",
                    {issue.path for issue in result.errors},
                )
        config = copy.deepcopy(self.base_config)
        config["instruments"]["power_supplies"]["PS4"]["channels"]["CH1"][
            "power_on_delay_s"
        ] = 2.0
        self.assertTrue(validate_config(config).valid)

    def test_missing_section_does_not_duplicate_nested_errors(self):
        config = copy.deepcopy(self.base_config)
        del config["signal_source"]
        result = validate_config(config)
        self.assertEqual([issue.path for issue in result.errors], ["signal_source"])

    def test_compression_point_must_be_positive_and_attenuator_non_negative(self):
        config = copy.deepcopy(self.base_config)
        config["compression_point"]["type"] = "0dB"
        config["attenuator"]["type"] = "-1dB"
        result = validate_config(config)
        paths = {issue.path for issue in result.errors}
        self.assertIn("compression_point.type", paths)
        self.assertIn("attenuator.type", paths)

    def test_enabled_protection_above_working_value_is_warning(self):
        config = copy.deepcopy(self.base_config)
        config["instruments"]["power_supplies"]["PS4"]["enabled"] = True
        config["instruments"]["power_supplies"]["PS4"]["channels"]["CH2"]["current"].update(
            {"value": 3.0, "protection": 2.0, "protection_enabled": True}
        )
        result = validate_config(config)
        self.assertTrue(result.valid)
        self.assertTrue(any("保护已启用" in issue.message for issue in result.warnings))

    def test_disabled_supply_does_not_report_runtime_protection_warning(self):
        config = copy.deepcopy(self.base_config)
        config["instruments"]["power_supplies"]["PS4"]["enabled"] = False
        config["instruments"]["power_supplies"]["PS4"]["channels"]["CH2"]["current"].update(
            {"value": 3.0, "protection": 2.0, "protection_enabled": True}
        )
        result = validate_config(config)
        self.assertFalse(any("保护已启用" in issue.message for issue in result.warnings))

    def test_missing_assigned_supply_is_error(self):
        config = copy.deepcopy(self.base_config)
        config["power_supply_assignment"]["dut_amplifier"]["power_plan"][
            "power_sequence"
        ][0]["supply_id"] = "PS_UNKNOWN"
        result = validate_config(config)
        self.assertTrue(any("不存在的电源" in issue.message for issue in result.errors))

    def test_disabled_assigned_supply_is_error(self):
        config = copy.deepcopy(self.base_config)
        config["instruments"]["power_supplies"]["PS4"]["enabled"] = False
        result = validate_config(config)
        self.assertTrue(any("未启用" in issue.message for issue in result.errors))

    def test_missing_assigned_channel_is_error(self):
        config = copy.deepcopy(self.base_config)
        config["power_supply_assignment"]["dut_amplifier"]["power_plan"][
            "power_sequence"
        ][0]["channel"] = "CH99"
        result = validate_config(config)
        self.assertTrue(any("CH99" in issue.message for issue in result.errors))

    def test_assigned_channel_does_not_require_a_role(self):
        config = copy.deepcopy(self.base_config)
        for channel in config["instruments"]["power_supplies"]["PS4"]["channels"].values():
            channel.pop("role", None)
        result = validate_config(config)
        self.assertTrue(result.valid, result.errors)

    def test_duplicate_assigned_role_is_no_longer_an_error(self):
        """1.0 残留的 ``role`` 只是读取兼容字段，不影响校验或上电。"""
        config = copy.deepcopy(self.base_config)
        config["instruments"]["power_supplies"]["PS4"]["channels"]["CH2"]["role"] = "gate"
        result = validate_config(config)
        self.assertTrue(result.valid, result.errors)

    def test_duplicate_physical_route_in_one_group_is_error(self):
        """同一物理通道在计划里出现两次是计划自身的矛盾。"""
        config = copy.deepcopy(self.base_config)
        sequence = config["power_supply_assignment"]["dut_amplifier"]["power_plan"][
            "power_sequence"
        ]
        sequence.append(dict(sequence[0]))
        result = validate_config(config)
        self.assertFalse(result.valid)
        self.assertTrue(
            any("不能重复" in issue.message for issue in result.errors),
            [issue.message for issue in result.errors],
        )

    def test_plan_may_provide_only_one_channel(self):
        config = copy.deepcopy(self.base_config)
        config["power_supply_assignment"]["dut_amplifier"]["power_plan"][
            "power_sequence"
        ] = [{"supply_id": "PS4", "channel": "CH1"}]
        config["dut_config"]["drain_channel"] = {"supply_id": "PS4", "channel": "CH1"}
        result = validate_config(config)
        self.assertTrue(result.valid, result.errors)

    def test_two_enabled_supplies_with_the_same_address_are_error(self):
        """两台会被连接的电源不能抢同一个 VISA 地址（步骤 11）。"""
        config = copy.deepcopy(self.base_config)
        config["instruments"]["power_supplies"]["PS1"]["enabled"] = True
        config["instruments"]["power_supplies"]["PS1"]["address"] = config["instruments"][
            "power_supplies"
        ]["PS4"]["address"]
        result = validate_config(config)
        self.assertFalse(result.valid)
        duplicates = [
            issue for issue in result.errors if "共用同一 VISA 地址" in issue.message
        ]
        self.assertEqual(len(duplicates), 1, [issue.message for issue in result.errors])
        issue = duplicates[0]
        self.assertTrue(issue.path.startswith("instruments.power_supplies."), issue.path)
        # 两个字段都要被点名：只报“地址重复”等于让操作员自己去找是哪两台。
        self.assertIn("PS1", issue.path + issue.message)
        self.assertIn("PS4", issue.path + issue.message)

    def test_supply_sharing_the_signal_generator_address_is_error(self):
        config = copy.deepcopy(self.base_config)
        config["instruments"]["power_supplies"]["PS4"]["address"] = config["instruments"][
            "signal_generator"
        ]["address"]
        result = validate_config(config)
        self.assertFalse(result.valid)
        self.assertTrue(
            any(
                issue.path == "instruments.power_supplies.PS4.address"
                and "instruments.signal_generator.address" in issue.message
                for issue in result.errors
            ),
            [f"{issue.path}: {issue.message}" for issue in result.errors],
        )

    def test_disabled_supply_address_is_not_a_connection_conflict(self):
        """未启用的电源不会被连接，它的历史地址不参与地址冲突判定。"""
        config = copy.deepcopy(self.base_config)
        config["instruments"]["power_supplies"]["PS1"]["address"] = config["instruments"][
            "power_supplies"
        ]["PS4"]["address"]
        result = validate_config(config)
        self.assertFalse(
            any("共用同一 VISA 地址" in issue.message for issue in result.errors),
            [issue.message for issue in result.errors],
        )

    def test_one_supply_may_feed_driver_and_dut_on_different_channels(self):
        """同一台电源 CH1 供驱放、CH2 供 DUT：计划是唯一来源，连接校验不要求两台。"""
        config = copy.deepcopy(self.base_config)
        config["driver_mode"] = {"enabled": True}
        config["power_supply_assignment"]["dut_amplifier"]["power_plan"][
            "power_sequence"
        ] = [{"supply_id": "PS4", "channel": "CH2"}]
        config["dut_config"]["drain_channel"] = {
            "supply_id": "PS4",
            "channel": "CH2",
        }
        config["power_supply_assignment"]["driver_amplifier"]["power_plan"] = {
            "power_sequence": [{"supply_id": "PS4", "channel": "CH1"}],
            "inter_channel_delay_s": 0.0,
        }

        result = validate_config(config)

        self.assertTrue(
            result.valid,
            [f"{issue.path}: {issue.message}" for issue in result.errors],
        )

    def test_same_physical_channel_for_dut_and_driver_is_error(self):
        """同一个 ``supply_id/CHx`` 不能同时接 DUT 与驱动功放（步骤 11）。"""
        config = copy.deepcopy(self.base_config)
        config["driver_mode"] = {"enabled": True}
        config["power_supply_assignment"]["driver_amplifier"] = {
            "power_plan": {
                "power_sequence": [
                    {"supply_id": "PS4", "channel": "CH1"},
                    {"supply_id": "PS4", "channel": "CH2"},
                ],
                "inter_channel_delay_s": 0.0,
            }
        }
        result = validate_config(config)
        self.assertFalse(result.valid)
        messages = [issue.message for issue in result.errors]
        self.assertTrue(
            any("同一物理通道不能同时用于 DUT 与驱动功放" in message for message in messages),
            messages,
        )
        self.assertTrue(any("PS4/CH1" in message for message in messages), messages)

    def test_supply_id_is_the_reference_field(self):
        """新配置只写 ``supply_id``：没有过渡别名 ``name`` 也能通过校验。"""
        config = copy.deepcopy(self.base_config)
        config["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"] = {
            "supply_id": "PS4",
            "channel": ["CH1", "CH2"],
        }
        result = validate_config(config)
        self.assertTrue(result.valid, [f"{issue.path}: {issue.message}" for issue in result.errors])

    def test_legacy_assignment_supplies_do_not_affect_validation(self):
        """旧编辑段的 ``supplies`` 只用于读取历史文档，校验只看上电计划。"""
        config = copy.deepcopy(self.base_config)
        carrier = config["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"]
        carrier["name"] = "PS4"
        carrier["supply_id"] = "PS1"
        result = validate_config(config)
        self.assertTrue(result.valid, result.errors)

    def test_unbound_plan_route_is_reported(self):
        """计划引用了没有连接地址的电源，必须报错，不能按缺省补一台电源。"""
        config = copy.deepcopy(self.base_config)
        config["instruments"]["power_supplies"]["PS4"].pop("address", None)
        result = validate_config(config)
        self.assertFalse(result.valid)
        self.assertTrue(
            any(
                "power_supply_assignment.dut_amplifier.power_plan.power_sequence" in issue.path
                and "没有已应用的设备" in issue.message
                for issue in result.errors
            ),
            [f"{issue.path}: {issue.message}" for issue in result.errors],
        )

    def test_file_loader_uses_explicit_path_without_hardware_access(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path = Path(temporary_directory) / "config.json"
            config_path.write_text(json.dumps(self.base_config), encoding="utf-8")
            self.assertEqual(load_config(config_path)["test_frequencies"], self.base_config["test_frequencies"])
            self.assertTrue(validate_config_file(config_path).valid)


class OfficialConfigFileValidationTests(unittest.TestCase):
    """文件级校验必须同时覆盖正式运行 schema 与硬件装配段。"""

    @classmethod
    def setUpClass(cls):
        with FIXTURE_PATH.open("r", encoding="utf-8") as config_file:
            cls.base_config = json.load(config_file)

    def _flat_only_config(self):
        config = copy.deepcopy(self.base_config)
        for key in ("schema_version", "test_plan", "run_mapping"):
            config.pop(key, None)
        return config

    def _write(self, directory, document):
        path = Path(directory) / "config.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def test_fixture_is_a_valid_official_configuration(self):
        """测试夹具本身就是能被正式仓储接受的正式配置。"""
        with tempfile.TemporaryDirectory() as directory:
            result = validate_config_file(self._write(directory, self.base_config))
        self.assertTrue(result.valid, result.errors)

    def test_flat_fields_alone_are_not_a_valid_configuration(self):
        """扁平硬件字段全部合法、但缺正式段时，文件校验必须失败。

        这是步骤 5 的关键契约：``--validate-config`` 不得用只认扁平字段的旧校验放行无法创建运行的配置。
        """
        with tempfile.TemporaryDirectory() as directory:
            result = validate_config_file(self._write(directory, self._flat_only_config()))
        self.assertFalse(result.valid)
        paths = {issue.path for issue in result.errors}
        self.assertIn("schema_version", paths)
        self.assertIn("test_plan", paths)
        self.assertIn("run_mapping", paths)

    def test_unsupported_root_schema_version_is_rejected(self):
        config = copy.deepcopy(self.base_config)
        config["schema_version"] = "9.0"
        with tempfile.TemporaryDirectory() as directory:
            result = validate_config_file(self._write(directory, config))
        self.assertFalse(result.valid)
        self.assertTrue(any(issue.path == "schema_version" for issue in result.errors))

    def test_legacy_wiring_block_no_longer_blocks_the_file_level_check(self):
        """接线不再是配置字段：旧文件残留的 wiring 块既不校验也不阻塞。"""
        config = copy.deepcopy(self.base_config)
        config["run_mapping"]["wiring"] = {"confirmed": False, "connection_note": None}
        with tempfile.TemporaryDirectory() as directory:
            result = validate_config_file(self._write(directory, config))
        self.assertFalse(any(issue.path.startswith("wiring") for issue in result.errors))

    def test_hardware_section_errors_are_reported_alongside_formal_schema(self):
        config = copy.deepcopy(self.base_config)
        config["test_plan"]["frequencies"]["values"] = []
        config["instruments"]["signal_generator"]["address"] = ""
        with tempfile.TemporaryDirectory() as directory:
            result = validate_config_file(self._write(directory, config))
        paths = {issue.path for issue in result.errors}
        self.assertIn("frequencies.values", paths)
        self.assertIn("instruments.signal_generator.address", paths)

    def test_identical_issues_are_reported_once(self):
        """正式段与硬件段对同一字段的错误不重复堆叠。"""
        config = copy.deepcopy(self.base_config)
        config["test_plan"]["frequencies"]["values"] = []
        with tempfile.TemporaryDirectory() as directory:
            result = validate_config_file(self._write(directory, config))
        keys = [(issue.level, issue.path, issue.message) for issue in result.errors]
        self.assertEqual(len(keys), len(set(keys)))

    def test_legacy_single_supply_run_mapping_is_readable(self):
        """步骤 10：旧运行映射（单数 ``instruments.power_supply``、裸通道名）仍可读取。

        历史运行目录里的 1.0 映射必须继续通过字段级校验；升级现场配置不会改写它们。
        """
        legacy = json.loads(
            LEGACY_RUN_MAPPING_PATH.read_text(encoding="utf-8-sig")
        )
        mapping = RunResourceMapping.from_dict(legacy)
        self.assertEqual(mapping.schema_version, "1.0")
        self.assertIn("power_supply", mapping.instruments)
        self.assertNotIn("power_supplies", mapping.instruments)
        self.assertTrue(validate_run_mapping(mapping).valid)

    def test_single_supply_entry_cannot_mix_with_the_collection(self):
        """电源只有一个来源：新旧两种写法同时出现必须报错，而不是二选一。"""
        config = copy.deepcopy(self.base_config)
        config["run_mapping"]["instruments"]["power_supplies"] = {
            "DP8B1519": {
                "label": "DP8B1519",
                "model": "DP832A",
                "full_code": "DP8B214901519",
                "device_key": "rigol technologies:dp8b214901519",
                "visa_address": "TCPIP0::192.168.1.102::inst0::INSTR",
            }
        }
        config["run_mapping"]["dut_power_channels"] = [
            {"channel": "DP8B1519/CH1", "role": "gate", "connection": "DP8B1519/CH1 -> DUT 栅极"},
            {"channel": "DP8B1519/CH2", "role": "drain", "connection": "DP8B1519/CH2 -> DUT 漏极"},
        ]
        config["run_mapping"]["instruments"]["power_supply"] = {
            "model": "PS1",
            "visa_address": "TCPIP0::192.168.1.101::inst0::INSTR",
        }
        with tempfile.TemporaryDirectory() as directory:
            result = validate_config_file(self._write(directory, config))
        paths = {issue.path for issue in result.errors}
        self.assertIn("instruments.power_supply", paths)


class SimulatedDeviceBindingValidationTests(unittest.TestCase):
    """模拟扫描的绑定只警告：可以保存、可以启动界面，但不能创建真实运行。"""

    @classmethod
    def setUpClass(cls):
        with FIXTURE_PATH.open("r", encoding="utf-8") as config_file:
            cls.base_config = json.load(config_file)

    def _assert_simulated_warning(self, result, expected_path):
        self.assertTrue(result.valid, result.errors)
        paths = {issue.path for issue in result.warnings}
        self.assertIn(expected_path, paths)
        self.assertIn("模拟扫描", result.warnings[-1].message)
        self.assertIn("不能创建真实运行", result.warnings[-1].message)

    def test_simulated_binding_is_a_warning_with_a_pointed_message(self):
        config = copy.deepcopy(self.base_config)
        config["device_bindings"] = {
            "signal_generator": {
                "full_code": "105462",
                "device_key": "rohde&schwarz:105462",
                "source": "simulated",
            }
        }

        self._assert_simulated_warning(
            validate_config(config), "device_bindings.signal_generator.source"
        )

    def test_simulated_power_supply_binding_is_reported_too(self):
        """电源在 ``power_supplies`` 集合里逐台检查，不能因为嵌套结构漏报。"""
        config = copy.deepcopy(self.base_config)
        config["device_bindings"] = {
            "power_supplies": {
                "DP8B1519": {
                    "device_key": "rigol technologies:dp8b214901519",
                    "source": "simulated",
                }
            }
        }

        self._assert_simulated_warning(
            validate_config(config),
            "device_bindings.power_supplies.DP8B1519.source",
        )

    def test_simulated_binding_does_not_block_the_file_check(self):
        """``--validate-config`` 的文件校验只提示：模拟绑定不阻止读取，也不阻止启动界面。"""
        config = copy.deepcopy(self.base_config)
        config["device_bindings"] = {
            "signal_generator": {
                "full_code": "105462",
                "device_key": "rohde&schwarz:105462",
                "source": "simulated",
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            result = validate_config_file(self._write(directory, config))
        self._assert_simulated_warning(
            result, "device_bindings.signal_generator.source"
        )

    def test_real_scan_binding_is_accepted(self):
        config = copy.deepcopy(self.base_config)
        config["device_bindings"] = {
            "signal_generator": {
                "full_code": "105462",
                "device_key": "rohde&schwarz:105462",
                "source": "scan",
            }
        }

        result = validate_config(config)

        for issues in (result.errors, result.warnings):
            self.assertFalse(
                any(issue.path.startswith("device_bindings") for issue in issues)
            )

    def test_missing_or_malformed_bindings_are_ignored_by_this_rule(self):
        for bindings in (None, [], "text", {"signal_generator": "not-a-record"}):
            with self.subTest(bindings=bindings):
                config = copy.deepcopy(self.base_config)
                if bindings is not None:
                    config["device_bindings"] = bindings
                result = validate_config(config)
                for issues in (result.errors, result.warnings):
                    self.assertFalse(
                        any(
                            issue.path.startswith("device_bindings")
                            for issue in issues
                        )
                    )

    @staticmethod
    def _write(directory, document):
        path = Path(directory) / "config.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path


class HardwareConnectionValidationTests(unittest.TestCase):
    """``validate_hardware_connections`` 只拦连接冲突，不重复字段完整性校验。"""

    def setUp(self):
        self.config = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_clean_configuration_has_no_conflict(self):
        self.assertTrue(validate_hardware_connections(self.config).valid)

    def test_missing_protection_values_are_not_connection_conflicts(self):
        """字段完整性归完整校验：运行创建只保证“不会连错设备”。"""
        config = copy.deepcopy(self.config)
        config["instruments"]["power_supplies"]["PS4"]["channels"]["CH1"]["voltage"].pop(
            "protection"
        )
        self.assertTrue(validate_hardware_connections(config).valid)
        self.assertFalse(validate_config(config).valid)

    def test_missing_role_is_not_a_connection_conflict_or_a_field_error(self):
        """步骤 5 起通道角色不再是字段：没有角色不影响连接校验，也不影响完整校验。"""
        config = copy.deepcopy(self.config)
        supply = config["instruments"]["power_supplies"]["PS4"]
        for channel in supply["channels"].values():
            channel.pop("role", None)

        self.assertTrue(validate_hardware_connections(config).valid)
        self.assertTrue(validate_config(config).valid, validate_config(config).errors)

    def test_single_channel_plan_is_valid_and_malformed_plan_is_a_field_check(self):
        """单通道计划合法；计划形状错误归完整校验，连接校验不跟着失败。"""
        config = copy.deepcopy(self.config)
        config["power_supply_assignment"]["dut_amplifier"]["power_plan"][
            "power_sequence"
        ] = [{"supply_id": "PS4", "channel": "CH1"}]
        config["dut_config"]["drain_channel"] = {"supply_id": "PS4", "channel": "CH1"}
        self.assertTrue(validate_hardware_connections(config).valid)
        self.assertTrue(validate_config(config).valid, validate_config(config).errors)

        config["power_supply_assignment"]["dut_amplifier"]["power_plan"][
            "power_sequence"
        ] = "CH1"
        self.assertTrue(validate_hardware_connections(config).valid)
        self.assertFalse(validate_config(config).valid)

    def test_channel_list_that_is_not_a_list_is_left_to_the_full_check(self):
        """计划里的路由形状错误归完整校验：连接校验不跟着失败。"""
        config = copy.deepcopy(self.config)
        config["power_supply_assignment"]["dut_amplifier"]["power_plan"][
            "power_sequence"
        ][0]["channel"] = ["CH1"]
        self.assertTrue(validate_hardware_connections(config).valid)
        self.assertFalse(validate_config(config).valid)

    def test_channel_missing_from_the_supply_is_still_a_connection_conflict(self):
        """引用了这台电源上不存在的通道：装配时按 ``supply_id/CHx`` 路由会失败。"""
        config = copy.deepcopy(self.config)
        config["power_supply_assignment"]["dut_amplifier"]["power_plan"][
            "power_sequence"
        ].append({"supply_id": "PS4", "channel": "CH9"})
        messages = [issue.message for issue in validate_hardware_connections(config).errors]
        self.assertTrue(any("CH9" in message and "不存在" in message for message in messages), messages)

    def test_plan_route_to_an_unapplied_supply_is_reported(self):
        config = copy.deepcopy(self.config)
        config["instruments"]["power_supplies"]["PS4"].pop("address", None)
        paths = {issue.path for issue in validate_hardware_connections(config).errors}
        self.assertIn("power_supply_assignment.dut_amplifier.power_plan.power_sequence[0]", paths)

    def test_disabled_supply_is_not_part_of_the_connection(self):
        config = copy.deepcopy(self.config)
        config["instruments"]["power_supplies"]["PS1"]["address"] = config["instruments"][
            "power_supplies"
        ]["PS4"]["address"]
        self.assertTrue(validate_hardware_connections(config).valid)

    def test_same_physical_channel_for_dut_and_driver_is_a_conflict(self):
        config = copy.deepcopy(self.config)
        config["power_supply_assignment"]["driver_amplifier"] = {
            "power_plan": {
                "power_sequence": [{"supply_id": "PS4", "channel": "CH1"}],
                "inter_channel_delay_s": 0.0,
            }
        }
        messages = [issue.message for issue in validate_hardware_connections(config).errors]
        self.assertTrue(
            any("同一物理通道不能同时用于 DUT 与驱动功放" in message for message in messages),
            messages,
        )


class PowerScanPlanValidationTests(unittest.TestCase):
    """步骤 2.4：编辑文档的计划列表与正式 TestPlan 使用同一套连续性规则。"""

    def setUp(self):
        with FIXTURE_PATH.open("r", encoding="utf-8") as config_file:
            self.base_config = json.load(config_file)

    def _with_plans(self, *plans):
        config = copy.deepcopy(self.base_config)
        config["signal_source"] = {
            "power_scan_plans": [
                {"start_power": start, "step": step, "stop_power": stop}
                for start, step, stop in plans
            ]
        }
        return config

    def test_continuous_plans_are_valid(self):
        result = validate_config(self._with_plans((-10.0, 1.0, 0.0), (0.0, 0.5, 5.0)))

        self.assertTrue(result.valid, result.errors)

    def test_gap_and_overlap_point_at_the_following_segment(self):
        for plans, keyword in (
            (((-10.0, 1.0, 0.0), (1.0, 0.5, 5.0)), "空档"),
            (((-10.0, 1.0, 0.0), (-1.0, 0.5, 5.0)), "重叠"),
        ):
            with self.subTest(keyword=keyword):
                result = validate_config(self._with_plans(*plans))
                issues = [
                    issue
                    for issue in result.errors
                    if issue.path == "signal_source.power_scan_plans[1]"
                ]
                self.assertTrue(
                    any(keyword in issue.message for issue in issues),
                    [f"{issue.path}: {issue.message}" for issue in result.errors],
                )

    def test_scalar_and_plan_fields_together_are_an_error(self):
        config = self._with_plans((-10.0, 1.0, 0.0))
        config["signal_source"].update(
            {"start_power": -10.0, "stop_power": 0.0, "step": 1.0}
        )

        paths = {issue.path for issue in validate_config(config).errors}

        self.assertIn("signal_source", paths)

    def test_invalid_element_reports_its_index(self):
        config = self._with_plans((-10.0, 1.0, 0.0))
        config["signal_source"]["power_scan_plans"].append("not-a-plan")

        paths = {issue.path for issue in validate_config(config).errors}

        self.assertIn("signal_source.power_scan_plans[1]", paths)


class ShippedConfigValidationTests(unittest.TestCase):
    """仓库默认生产配置必须通过 ``--validate-config`` 使用的同一套文件级校验。"""

    def test_repository_config_passes_the_launcher_file_check(self):
        result = validate_config_file(PROJECT_ROOT / "config.json")
        self.assertTrue(
            result.valid,
            [f"{issue.path}: {issue.message}" for issue in result.errors],
        )


if __name__ == "__main__":
    unittest.main()
