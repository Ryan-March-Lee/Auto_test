import copy
import json
import tempfile
import unittest
from pathlib import Path

from domain.configuration.rules import validate_config, validate_hardware_connections
from infrastructure.config.validation import load_config, validate_config_file


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "config_driver_enabled_no_assignment.json"


class ConfigValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 固定夹具：驱动模式启用但未配置驱动供电分配。
        # 不读取现场 config.json，避免测试结果随每日现场配置变化。
        with FIXTURE_PATH.open("r", encoding="utf-8") as config_file:
            cls.base_config = json.load(config_file)

    def test_current_config_is_readable_and_only_has_known_warning(self):
        result = validate_config(self.base_config)
        self.assertTrue(result.valid, result.errors)
        self.assertTrue(any("按自带供电处理" in issue.message for issue in result.warnings))

    def test_confirmed_external_driver_power_has_no_assignment_warning(self):
        config = copy.deepcopy(self.base_config)
        config["driver_mode"] = {"enabled": True, "power_source": "external_ac"}
        result = validate_config(config)
        self.assertFalse(any(issue.path == "driver_mode.power_source" for issue in result.warnings))

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
        config["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"]["name"] = "PS_UNKNOWN"
        result = validate_config(config)
        self.assertTrue(any("不存在的电源" in issue.message for issue in result.errors))

    def test_disabled_assigned_supply_is_error(self):
        config = copy.deepcopy(self.base_config)
        config["instruments"]["power_supplies"]["PS4"]["enabled"] = False
        result = validate_config(config)
        self.assertTrue(any("未启用" in issue.message for issue in result.errors))

    def test_missing_assigned_channel_is_error(self):
        config = copy.deepcopy(self.base_config)
        config["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"]["channel"] = ["CH99"]
        result = validate_config(config)
        self.assertTrue(any("CH99" in issue.message for issue in result.errors))

    def test_assigned_channel_requires_explicit_role(self):
        config = copy.deepcopy(self.base_config)
        del config["instruments"]["power_supplies"]["PS4"]["channels"]["CH1"]["role"]
        result = validate_config(config)
        self.assertFalse(result.valid)
        self.assertTrue(
            any("显式声明 role" in issue.message for issue in result.errors),
            [issue.message for issue in result.errors],
        )

    def test_duplicate_assigned_role_is_error(self):
        config = copy.deepcopy(self.base_config)
        config["instruments"]["power_supplies"]["PS4"]["channels"]["CH2"]["role"] = "gate"
        result = validate_config(config)
        self.assertFalse(result.valid)
        self.assertTrue(
            any("供电角色重复配置" in issue.message for issue in result.errors),
            [issue.message for issue in result.errors],
        )

    def test_duplicate_assigned_role_across_supplies_is_error(self):
        """gate 由两个不同电源同时提供同样是重复配置。"""
        config = copy.deepcopy(self.base_config)
        config["instruments"]["power_supplies"]["PS1"]["enabled"] = True
        config["power_supply_assignment"]["dut_amplifier"]["supplies"]["peaking"] = {
            "name": "PS4",
            "channel": ["CH1"],
        }
        result = validate_config(config)
        self.assertFalse(result.valid)
        self.assertTrue(
            any("供电角色重复配置" in issue.message for issue in result.errors),
            [issue.message for issue in result.errors],
        )

    def test_assignment_must_provide_both_gate_and_drain(self):
        config = copy.deepcopy(self.base_config)
        config["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"]["channel"] = ["CH1"]
        result = validate_config(config)
        self.assertFalse(result.valid)
        self.assertTrue(
            any("gate 和 drain" in issue.message for issue in result.errors),
            [issue.message for issue in result.errors],
        )

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

    def test_same_physical_channel_for_dut_and_driver_is_error(self):
        """同一个 ``supply_id/CHx`` 不能同时接 DUT 与驱动功放（步骤 11）。"""
        config = copy.deepcopy(self.base_config)
        config["driver_mode"] = {"enabled": True, "power_source": "dc_supply"}
        config["power_supply_assignment"]["driver_amplifier"] = {
            "power_supply_count": 1,
            "supplies": {
                "main": {"supply_id": "PS4", "name": "PS4", "channel": ["CH1", "CH2"]}
            },
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

    def test_reference_alias_must_match_the_supply_id(self):
        """过渡别名 ``name`` 与 ``supply_id`` 不同值就是第二套编号，必须报错。"""
        config = copy.deepcopy(self.base_config)
        carrier = config["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"]
        carrier["supply_id"] = "PS1"
        result = validate_config(config)
        self.assertFalse(result.valid)
        self.assertTrue(
            any(
                issue.path == "power_supply_assignment.dut_amplifier.supplies.carrier.name"
                and "必须与 supply_id 同值" in issue.message
                for issue in result.errors
            ),
            [f"{issue.path}: {issue.message}" for issue in result.errors],
        )

    def test_unbound_reference_is_reported_on_the_supply_id_field(self):
        """``supply_id: null`` 是“声明了但未绑定”，必须报错，不能按缺省补一台电源。"""
        config = copy.deepcopy(self.base_config)
        config["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"] = {
            "supply_id": None,
            "channel": ["CH1", "CH2"],
        }
        result = validate_config(config)
        self.assertFalse(result.valid)
        self.assertTrue(
            any(
                issue.path == "power_supply_assignment.dut_amplifier.supplies.carrier.supply_id"
                and "必须填写已绑定的电源引用" in issue.message
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

        这是步骤 5 的关键契约：启动器不得用只认扁平字段的旧校验放行无法创建运行的配置。
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
        config["run_mapping"]["wiring"]["confirmed"] = False
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

        现场 ``config.json`` 就是这种形态，字段级校验必须继续通过，由迁移器改写。
        """
        shipped = json.loads((PROJECT_ROOT / "config.json").read_text(encoding="utf-8-sig"))
        config = copy.deepcopy(self.base_config)
        config["run_mapping"] = copy.deepcopy(shipped["run_mapping"])
        config["run_mapping"]["instruments"].pop("power_supplies", None)
        with tempfile.TemporaryDirectory() as directory:
            result = validate_config_file(self._write(directory, config))
        self.assertTrue(
            result.valid, [f"{issue.path}: {issue.message}" for issue in result.errors]
        )

    def test_single_supply_entry_cannot_mix_with_the_collection(self):
        """电源只有一个来源：新旧两种写法同时出现必须报错，而不是二选一。"""
        config = copy.deepcopy(self.base_config)
        config["run_mapping"]["instruments"]["power_supplies"] = {
            "DP8B1519": {
                "label": "DP8B1519",
                "model": "DP832A",
                "serial": "DP8B214901519",
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
    """模拟扫描写入的设备绑定可以保存，但绝不能通过真实运行校验。"""

    @classmethod
    def setUpClass(cls):
        with FIXTURE_PATH.open("r", encoding="utf-8") as config_file:
            cls.base_config = json.load(config_file)

    def test_simulated_binding_is_rejected_with_a_pointed_message(self):
        config = copy.deepcopy(self.base_config)
        config["device_bindings"] = {
            "signal_generator": {
                "serial": "105462",
                "device_key": "rohde&schwarz:105462",
                "source": "simulated",
            }
        }

        result = validate_config(config)

        paths = {issue.path for issue in result.errors}
        self.assertIn("device_bindings.signal_generator.source", paths)
        self.assertIn("模拟扫描", result.errors[-1].message)

    def test_simulated_power_supply_binding_is_rejected_too(self):
        """电源在 ``power_supplies`` 集合里逐台检查，不能因为嵌套结构绕过门禁。"""
        config = copy.deepcopy(self.base_config)
        config["device_bindings"] = {
            "power_supplies": {
                "DP8B1519": {
                    "device_key": "rigol technologies:dp8b214901519",
                    "source": "simulated",
                }
            }
        }

        result = validate_config(config)

        paths = {issue.path for issue in result.errors}
        self.assertIn("device_bindings.power_supplies.DP8B1519.source", paths)
        self.assertIn("模拟扫描", result.errors[-1].message)

    def test_real_scan_binding_is_accepted(self):
        config = copy.deepcopy(self.base_config)
        config["device_bindings"] = {
            "signal_generator": {
                "serial": "105462",
                "device_key": "rohde&schwarz:105462",
                "source": "scan",
            }
        }

        result = validate_config(config)

        self.assertFalse(
            any(issue.path.startswith("device_bindings") for issue in result.errors)
        )

    def test_missing_or_malformed_bindings_are_ignored_by_this_rule(self):
        for bindings in (None, [], "text", {"signal_generator": "not-a-record"}):
            with self.subTest(bindings=bindings):
                config = copy.deepcopy(self.base_config)
                if bindings is not None:
                    config["device_bindings"] = bindings
                result = validate_config(config)
                self.assertFalse(
                    any(issue.path.startswith("device_bindings") for issue in result.errors)
                )


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

    def test_missing_role_is_field_completeness_not_a_connection_conflict(self):
        """通道没声明供电角色是配置作者要补的内容，不是“会连错设备”。

        运行创建只拦连接冲突；若这里也把它当冲突，一份结构缺失的配置会在写运行目录前
        以“供电连接存在冲突”被拒，把人引向排查接线。
        """
        config = copy.deepcopy(self.config)
        supply = config["instruments"]["power_supplies"]["PS4"]
        for channel in supply["channels"].values():
            channel.pop("role", None)

        self.assertTrue(validate_hardware_connections(config).valid)
        paths = {issue.path for issue in validate_config(config).errors}
        self.assertIn("power_supply_assignment.dut_amplifier.supplies.carrier.channel", paths)

    def test_missing_gate_or_drain_and_channel_list_are_not_connection_conflicts(self):
        """gate/drain 不齐、通道列表为空同样只由完整校验报。"""
        config = copy.deepcopy(self.config)
        config["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"]["channel"] = [
            "CH1"
        ]
        self.assertTrue(validate_hardware_connections(config).valid)

        config["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"]["channel"] = []
        self.assertTrue(validate_hardware_connections(config).valid)
        self.assertFalse(validate_config(config).valid)

    def test_channel_list_that_is_not_a_list_is_left_to_the_full_check(self):
        """``channel`` 不是列表是结构问题：完整校验报，连接校验不跟着失败。"""
        config = copy.deepcopy(self.config)
        config["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"]["channel"] = "CH1"
        self.assertTrue(validate_hardware_connections(config).valid)
        self.assertFalse(validate_config(config).valid)

    def test_channel_missing_from_the_supply_is_still_a_connection_conflict(self):
        """引用了这台电源上不存在的通道：装配时按 ``supply_id/CHx`` 路由会失败。"""
        config = copy.deepcopy(self.config)
        config["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"]["channel"] = [
            "CH1",
            "CH9",
        ]
        messages = [issue.message for issue in validate_hardware_connections(config).errors]
        self.assertTrue(any("CH9" in message and "不存在" in message for message in messages), messages)

    def test_unbound_reference_and_supply_id_alias_conflict_are_reported(self):
        config = copy.deepcopy(self.config)
        carrier = config["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"]
        carrier["supply_id"] = "PS1"
        paths = {issue.path for issue in validate_hardware_connections(config).errors}
        self.assertIn("power_supply_assignment.dut_amplifier.supplies.carrier.name", paths)
        self.assertIn("power_supply_assignment.dut_amplifier.supplies.carrier.supply_id", paths)

    def test_disabled_supply_is_not_part_of_the_connection(self):
        config = copy.deepcopy(self.config)
        config["instruments"]["power_supplies"]["PS1"]["address"] = config["instruments"][
            "power_supplies"
        ]["PS4"]["address"]
        self.assertTrue(validate_hardware_connections(config).valid)

    def test_same_physical_channel_for_dut_and_driver_is_a_conflict(self):
        config = copy.deepcopy(self.config)
        config["power_supply_assignment"]["driver_amplifier"] = {
            "power_supply_count": 1,
            "supplies": {
                "main": {"supply_id": "PS4", "name": "PS4", "channel": ["CH1"]}
            },
        }
        messages = [issue.message for issue in validate_hardware_connections(config).errors]
        self.assertTrue(
            any("同一物理通道不能同时用于 DUT 与驱动功放" in message for message in messages),
            messages,
        )


class ShippedConfigValidationTests(unittest.TestCase):
    """仓库默认生产配置必须通过启动器使用的同一套文件级校验。"""

    def test_repository_config_passes_the_launcher_file_check(self):
        result = validate_config_file(PROJECT_ROOT / "config.json")
        self.assertTrue(
            result.valid,
            [f"{issue.path}: {issue.message}" for issue in result.errors],
        )


if __name__ == "__main__":
    unittest.main()
