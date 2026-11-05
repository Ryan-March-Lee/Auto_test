"""正式配置编写的应用层测试（GUI 重构步骤 4，不需要 Qt）。

覆盖：正式 ``test_plan`` / ``run_mapping`` 的生成规则、快照身份冻结、空引用与重复通道的
处理，以及“旧文档入口与 ``ConfigurationDraft`` 入口生成同一份正式文档”。旧转发壳
``presentation.qt.configuration_authoring`` 已在步骤 13 删除，这里同时验证它不会重新出现。
"""

import ast
import copy
import json
import unittest
from pathlib import Path

from application.configuration_authoring import (
    ConfigurationAuthoringError,
    ConfigurationAuthoringService,
    ConfigurationEditingDocument,
    OFFICIAL_SCHEMA_VERSION,
    author_official_configuration,
    parse_db_value,
)
from application.configuration_draft import (
    ConfigurationDraft,
    DeviceBindingDraft,
    DeviceBindingsDraft,
)
from domain.configuration.models import validate_run_configuration

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_FIXTURE = (
    ROOT / "tests" / "fixtures" / "configuration_authoring"
    / "official_configuration_golden.json"
)


def _flat_config() -> dict:
    """一份最小的表单配置：一台电源、一条 DUT 分配。

    电源条目带身份字段（``label`` / ``full_code`` / ``device_key``）：正式运行映射的快照
    条目必须自带身份。只有地址的条目仍会被写进映射，但正式校验会报缺少身份。
    """
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
                        "CH1": {"voltage": {"value": 2.8}, "current": {"value": 0.1}},
                        "CH2": {"voltage": {"value": 28.0}, "current": {"value": 2.0}},
                    },
                }
            },
        },
        "power_supply_assignment": {
            "driver_amplifier": {
                "power_supply_count": 0,
                "supplies": {},
                "power_plan": {"power_sequence": [], "inter_channel_delay_s": 0.0},
            },
            "dut_amplifier": {
                "power_supply_count": 1,
                "supplies": {
                    "carrier": {
                        "supply_id": "DP8B1519",
                        "channel": ["CH1", "CH2"],
                    }
                },
                "power_plan": {
                    "power_sequence": [
                        {"supply_id": "DP8B1519", "channel": "CH1"},
                        {"supply_id": "DP8B1519", "channel": "CH2"},
                    ],
                    "inter_channel_delay_s": 2.0,
                },
            },
        },
    }


def _second_supply() -> dict:
    return {
        "label": "DP8B0885",
        "model": "DP832A",
        "full_code": "DP8B174400885",
        "device_key": "rigol technologies:dp8b174400885",
        "address": "sim::ps2",
        "enabled": True,
        "channels": {
            "CH1": {"voltage": {"value": 5.0}, "current": {"value": 0.5}},
            "CH2": {"voltage": {"value": 28.0}, "current": {"value": 1.0}},
        },
    }


def _bindings_for(document) -> DeviceBindingsDraft:
    """按配置里的仪器条目构造一份等价绑定，供草稿入口使用。

    草稿的 ``to_document()`` 从绑定重建 ``instruments``；这里模拟已经应用过设备的页面
    （绑定与配置条目同源），这样草稿入口与文档入口才是在比较同一份编辑数据。
    """
    instruments = document["instruments"]

    def binding(entry):
        return DeviceBindingDraft(
            vendor=None,
            model=entry.get("model"),
            label=entry.get("label"),
            full_code=entry.get("full_code"),
            device_key=entry.get("device_key"),
            visa_address=entry.get("address"),
            known=True,
        )

    return DeviceBindingsDraft(
        signal_generator=binding(instruments["signal_generator"]),
        spectrum_analyzer=binding(instruments["spectrum_analyzer"]),
        power_supply_bindings=tuple(
            (supply_id, binding(entry))
            for supply_id, entry in instruments["power_supplies"].items()
        ),
    )


class ConfigurationAuthoringTests(unittest.TestCase):
    def _configuration(self, document):
        from domain.configuration.models import RunConfiguration, RunResourceMapping, TestPlan

        return RunConfiguration(
            TestPlan.from_dict(document["test_plan"]),
            RunResourceMapping.from_dict(document["run_mapping"]),
        )

    def test_authored_configuration_is_valid_official_schema(self):
        document = author_official_configuration(_flat_config())
        configuration = self._configuration(document)

        self.assertTrue(validate_run_configuration(configuration).valid)
        self.assertEqual(
            configuration.test_plan.drain_channel,
            {"supply_id": "DP8B1519", "channel": "CH2"},
        )
        self.assertEqual(
            [
                (item["supply_id"], item["channel"])
                for item in document["test_plan"]["dut"]["channel_settings"]
            ],
            [("DP8B1519", "CH1"), ("DP8B1519", "CH2")],
        )
        self.assertEqual(
            [
                (item["supply_id"], item["channel"])
                for item in document["run_mapping"]["dut_power_plan"]["power_sequence"]
            ],
            [("DP8B1519", "CH1"), ("DP8B1519", "CH2")],
        )

    def test_run_mapping_freezes_instrument_identity(self):
        """信号源与频谱仪的快照条目冻结缩写码、完整设备码、设备键与地址。"""
        flat = _flat_config()
        flat["instruments"]["signal_generator"].update(
            {"label": "5462", "full_code": "105462", "device_key": "rohde&schwarz:105462"}
        )
        flat["instruments"]["spectrum_analyzer"].update(
            {
                "label": "QZNI0866",
                "full_code": "QZNI000866",
                "device_key": "ceyear technologies:qzni000866",
            }
        )

        document = author_official_configuration(flat)

        self.assertEqual(
            document["run_mapping"]["instruments"]["signal_generator"],
            {
                "label": "5462",
                "model": "SG",
                "full_code": "105462",
                "device_key": "rohde&schwarz:105462",
                "visa_address": "sim::sg",
            },
        )
        spectrum = document["run_mapping"]["instruments"]["spectrum_analyzer"]
        self.assertEqual(spectrum["label"], "QZNI0866")
        self.assertEqual(spectrum["full_code"], "QZNI000866")
        for entry in (document["run_mapping"]["instruments"]["signal_generator"], spectrum):
            self.assertNotIn("serial", entry)

    def test_unknown_channel_is_kept_as_a_physical_route(self):
        """authoring 不按通道名猜角色；未知通道按用户写的物理路由原样进入正式段。"""
        flat = _flat_config()
        flat["power_supply_assignment"]["dut_amplifier"]["power_plan"] = {
            "power_sequence": [{"supply_id": "DP8B1519", "channel": "CH99"}],
            "inter_channel_delay_s": 0.0,
        }
        flat["dut_config"]["drain_channel"] = {"supply_id": "DP8B1519", "channel": "CH99"}
        document = author_official_configuration(flat)

        self.assertEqual(
            document["test_plan"]["dut"]["channel_settings"][0]["channel"], "CH99"
        )
        self.assertEqual(
            document["run_mapping"]["dut_power_plan"]["power_sequence"],
            [{"supply_id": "DP8B1519", "channel": "CH99"}],
        )
        self.assertNotIn("role", document["test_plan"]["dut"])

    def test_1_1_output_never_rewrites_channel_roles(self):
        """1.1 正式段与编辑段都不再出现 role / power_roles / power_channels / connection。"""
        document = author_official_configuration(_flat_config())
        self.assert_no_legacy_power_fields(document)

    def assert_no_legacy_power_fields(self, document):
        def walk(value, path=""):
            if isinstance(value, dict):
                for key, item in value.items():
                    self.assertNotIn(
                        key,
                        {
                            "power_roles",
                            "power_channels",
                            "dut_power_channels",
                            "connection",
                        },
                        f"{path}.{key}",
                    )
                    if key == "role":
                        self.assertFalse(
                            path.startswith(("test_plan", "run_mapping"))
                            or ".power_supplies" in path,
                            f"电源段不得写 role: {path}.{key}",
                        )
                    walk(item, f"{path}.{key}")
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    walk(item, f"{path}[{index}]")

        walk(document)

    def test_externally_powered_driver_mode_keeps_the_driver_mapping(self):
        """驱放自带供电：不占本机电源通道，但驱动映射照常生成并执行。"""
        flat = _flat_config()
        flat["driver_mode"] = {"enabled": True, "power_source": "external_ac"}
        flat["power_supply_assignment"]["driver_amplifier"] = {
            "power_supply_count": 0,
            "supplies": {},
            "power_plan": {"power_sequence": [], "inter_channel_delay_s": 0.0},
        }
        document = author_official_configuration(flat)

        self.assertTrue(document["test_plan"]["driver_mode"]["enabled"])
        self.assertEqual(
            document["test_plan"]["driver_mode"]["power_source"], "external_ac"
        )
        self.assertTrue(document["run_mapping"]["driver_mode"]["enabled"])
        self.assertEqual(
            document["run_mapping"]["driver_mode"]["power_source"], "external_ac"
        )
        self.assertEqual(
            document["run_mapping"]["driver_mode"]["power_plan"],
            {"power_sequence": [], "inter_channel_delay_s": 0.0},
        )
        self.assertTrue(validate_run_configuration(self._configuration(document)).valid)

    def test_driver_mode_without_a_power_source_is_inferred_from_the_channels(self):
        flat = _flat_config()
        flat["driver_mode"] = {"enabled": True}
        flat["power_supply_assignment"]["driver_amplifier"] = {
            "power_supply_count": 1,
            "supplies": {
                "main": {"supply_id": "DP8B1519", "channel": ["CH1", "CH2"]}
            },
            "power_plan": {
                "power_sequence": [
                    {"supply_id": "DP8B1519", "channel": "CH2"},
                    {"supply_id": "DP8B1519", "channel": "CH1"},
                ],
                "inter_channel_delay_s": 1.5,
            },
        }
        document = author_official_configuration(flat)

        self.assertEqual(
            document["test_plan"]["driver_mode"]["power_source"], "dc_supply"
        )
        self.assertEqual(
            document["run_mapping"]["driver_mode"]["power_plan"]["power_sequence"],
            [
                {"supply_id": "DP8B1519", "channel": "CH2"},
                {"supply_id": "DP8B1519", "channel": "CH1"},
            ],
        )

    def test_authoring_does_not_mutate_the_input(self):
        flat = _flat_config()
        original = copy.deepcopy(flat)
        author_official_configuration(flat)
        self.assertEqual(flat, original)

    def test_power_supply_follows_the_assignment_not_the_enabled_count(self):
        """正式 run_mapping 记录被分配使用的电源，而不是“唯一启用的电源”。"""
        flat = _flat_config()
        flat["instruments"]["power_supplies"]["PS2"] = {
            "address": "sim::ps2",
            "enabled": True,
            "channels": {"CH1": {"voltage": {"value": 5.0}, "current": {"value": 0.5}}},
        }
        document = author_official_configuration(flat)
        power_supplies = document["run_mapping"]["instruments"]["power_supplies"]
        self.assertEqual(sorted(power_supplies), ["DP8B1519"])
        self.assertEqual(power_supplies["DP8B1519"]["visa_address"], "sim::ps1")

    def test_run_mapping_carries_the_supply_identity_not_just_its_name(self):
        """快照条目必须自带 label / 型号 / 序列号 / 设备键，历史重现不回查注册表。"""
        document = author_official_configuration(_flat_config())
        entry = document["run_mapping"]["instruments"]["power_supplies"]["DP8B1519"]
        self.assertEqual(
            entry,
            {
                "label": "DP8B1519",
                "model": "DP832A",
                "full_code": "DP8B214901519",
                "device_key": "rigol technologies:dp8b214901519",
                "visa_address": "sim::ps1",
            },
        )
        self.assertNotIn("power_supply", document["run_mapping"]["instruments"])

    def test_channels_are_written_as_physical_routes_across_supplies(self):
        """DUT 的漏极与其它通道可以来自不同电源，顺序由用户计划决定。"""
        flat = _flat_config()
        flat["instruments"]["power_supplies"]["DP8B0885"] = _second_supply()
        flat["power_supply_assignment"]["dut_amplifier"]["power_plan"] = {
            "power_sequence": [
                {"supply_id": "DP8B0885", "channel": "CH2"},
                {"supply_id": "DP8B1519", "channel": "CH1"},
            ],
            "inter_channel_delay_s": 3.0,
        }
        flat["dut_config"]["drain_channel"] = {
            "supply_id": "DP8B0885",
            "channel": "CH2",
        }
        document = author_official_configuration(flat)

        self.assertEqual(
            [
                (item["supply_id"], item["channel"])
                for item in document["test_plan"]["dut"]["channel_settings"]
            ],
            [("DP8B0885", "CH2"), ("DP8B1519", "CH1")],
        )
        self.assertEqual(
            document["run_mapping"]["dut_power_plan"]["power_sequence"],
            [
                {"supply_id": "DP8B0885", "channel": "CH2"},
                {"supply_id": "DP8B1519", "channel": "CH1"},
            ],
        )
        self.assertEqual(
            sorted(document["run_mapping"]["instruments"]["power_supplies"]),
            ["DP8B0885", "DP8B1519"],
        )
        self.assertTrue(validate_run_configuration(self._configuration(document)).valid)

    def test_one_supply_may_power_driver_and_dut_on_different_channels(self):
        """同一台电源 CH1→驱放、CH2→DUT：计划路由唯一来源，不再需要固定分配。"""
        flat = _flat_config()
        flat["driver_mode"] = {"enabled": True, "power_source": "dc_supply"}
        flat["power_supply_assignment"]["driver_amplifier"]["power_plan"] = {
            "power_sequence": [{"supply_id": "DP8B1519", "channel": "CH1"}],
            "inter_channel_delay_s": 0.0,
        }
        flat["power_supply_assignment"]["dut_amplifier"]["power_plan"] = {
            "power_sequence": [{"supply_id": "DP8B1519", "channel": "CH2"}],
            "inter_channel_delay_s": 1.0,
        }
        flat["dut_config"]["drain_channel"] = {
            "supply_id": "DP8B1519",
            "channel": "CH2",
        }

        document = author_official_configuration(flat)

        self.assertEqual(
            document["run_mapping"]["driver_mode"]["power_plan"]["power_sequence"],
            [{"supply_id": "DP8B1519", "channel": "CH1"}],
        )
        self.assertEqual(
            document["run_mapping"]["dut_power_plan"]["power_sequence"],
            [{"supply_id": "DP8B1519", "channel": "CH2"}],
        )
        self.assertEqual(
            [
                (item["supply_id"], item["channel"])
                for item in document["test_plan"]["driver_mode"]["channel_settings"]
            ],
            [("DP8B1519", "CH1")],
        )
        self.assertEqual(
            [
                (item["supply_id"], item["channel"])
                for item in document["test_plan"]["dut"]["channel_settings"]
            ],
            [("DP8B1519", "CH2")],
        )
        self.assertEqual(
            sorted(document["run_mapping"]["instruments"]["power_supplies"]),
            ["DP8B1519"],
            "同一台电源只登记一次",
        )
        self.assertNotIn(
            "supplies", document["power_supply_assignment"]["dut_amplifier"]
        )
        self.assertTrue(validate_run_configuration(self._configuration(document)).valid)

    def test_two_supplies_assigned_to_two_groups_are_both_registered(self):
        """步骤 10：多电源指派不再被拒绝，DUT 与驱动功放各登记一台独立电源。"""
        flat = _flat_config()
        flat["driver_mode"] = {"enabled": True, "power_source": "dc_supply"}
        flat["instruments"]["power_supplies"]["DP8B0885"] = _second_supply()
        flat["power_supply_assignment"]["driver_amplifier"] = {
            "power_supply_count": 1,
            "supplies": {
                "driver": {"supply_id": "DP8B0885", "channel": ["CH1"]}
            },
            "power_plan": {
                "power_sequence": [{"supply_id": "DP8B0885", "channel": "CH1"}],
                "inter_channel_delay_s": 0.0,
            },
        }
        document = author_official_configuration(flat)

        power_supplies = document["run_mapping"]["instruments"]["power_supplies"]
        self.assertEqual(sorted(power_supplies), ["DP8B0885", "DP8B1519"])
        self.assertEqual(power_supplies["DP8B0885"]["visa_address"], "sim::ps2")
        self.assertEqual(
            document["run_mapping"]["driver_mode"]["power_plan"]["power_sequence"],
            [{"supply_id": "DP8B0885", "channel": "CH1"}],
        )
        self.assertTrue(validate_run_configuration(self._configuration(document)).valid)

    def test_unassigned_supplies_are_not_registered_as_the_run_supply(self):
        flat = _flat_config()
        flat["power_supply_assignment"]["dut_amplifier"]["power_plan"] = {
            "power_sequence": [],
            "inter_channel_delay_s": 0.0,
        }
        flat["dut_config"].pop("drain_channel", None)
        document = author_official_configuration(flat)
        self.assertEqual(document["run_mapping"]["instruments"]["power_supplies"], {})

        from domain.configuration.models import RunConfiguration, RunResourceMapping, TestPlan

        configuration = RunConfiguration(
            TestPlan.from_dict(document["test_plan"]),
            RunResourceMapping.from_dict(document["run_mapping"]),
        )
        paths = {issue.path for issue in validate_run_configuration(configuration).errors}
        self.assertIn("run_mapping.instruments.power_supplies", paths)

    def test_route_without_a_supply_never_becomes_a_power_supply(self):
        """计划里不可解析的路由不会凭空写进快照。"""
        flat = _flat_config()
        flat["power_supply_assignment"]["dut_amplifier"]["power_plan"] = {
            "power_sequence": [{"supply_id": None, "channel": "CH1"}],
            "inter_channel_delay_s": 0.0,
        }
        flat["dut_config"].pop("drain_channel", None)
        document = author_official_configuration(flat)

        self.assertEqual(document["run_mapping"]["instruments"]["power_supplies"], {})
        self.assertEqual(document["test_plan"]["dut"]["channel_settings"], [])
        paths = {
            issue.path
            for issue in validate_run_configuration(
                self._configuration(document)
            ).errors
        }
        self.assertIn("run_mapping.instruments.power_supplies", paths)

    def test_a_channel_used_twice_has_one_settings_entry(self):
        """``channel_settings`` 是集合：同一物理路由只写一条，重复路由由编辑校验报告。"""
        flat = _flat_config()
        flat["power_supply_assignment"]["dut_amplifier"]["power_plan"][
            "power_sequence"
        ] = [
            {"supply_id": "DP8B1519", "channel": "CH1"},
            {"supply_id": "DP8B1519", "channel": "CH1"},
            {"supply_id": "DP8B1519", "channel": "CH2"},
        ]
        document = author_official_configuration(flat)

        routes = [
            (item["supply_id"], item["channel"])
            for item in document["test_plan"]["dut"]["channel_settings"]
        ]
        self.assertEqual(routes, [("DP8B1519", "CH1"), ("DP8B1519", "CH2")])

    def test_parse_db_value(self):
        self.assertEqual(parse_db_value("30dB"), 30.0)
        self.assertEqual(parse_db_value(" 5 dB "), 5.0)
        self.assertIsNone(parse_db_value("attenuator"))
        self.assertIsNone(parse_db_value(None))


class ConfigurationAuthoringServiceTests(unittest.TestCase):
    """服务边界：草稿入口、显式文档 DTO 与旧转发入口生成同一份正式文档。"""

    def test_source_module_owns_the_official_schema(self):
        source_path = (
            Path(__file__).resolve().parents[1]
            / "application"
            / "configuration_authoring.py"
        )
        source = source_path.read_text(encoding="utf-8")
        self.assertIn('values["test_plan"]', source)
        self.assertIn('values["run_mapping"]', source)
        self.assertEqual(OFFICIAL_SCHEMA_VERSION, "1.1")

    def test_editing_document_accepts_only_objects(self):
        document = ConfigurationEditingDocument({"test_frequencies": [1.0]})
        self.assertEqual(document.to_mapping(), {"test_frequencies": [1.0]})
        self.assertEqual(
            ConfigurationEditingDocument.from_mapping(None).to_mapping(), {}
        )
        self.assertEqual(
            ConfigurationEditingDocument.from_mapping(["not", "an", "object"]).to_mapping(),
            {},
        )
        with self.assertRaises(TypeError):
            ConfigurationEditingDocument(values=["not", "an", "object"])
        with self.assertRaises(TypeError):
            ConfigurationAuthoringService().author_document(_flat_config())

    def test_editing_document_is_copied_on_output(self):
        document = ConfigurationEditingDocument({"test_frequencies": [1.0]})
        output = document.to_mapping()
        output["test_frequencies"] = [9.9]
        self.assertEqual(document.to_mapping(), {"test_frequencies": [1.0]})

    def test_editing_document_snapshots_the_input(self):
        """构造之后改写原字典或其中的嵌套集合，都不能改变 DTO 的内容。"""
        source = {
            "test_frequencies": [1.0],
            "instruments": {"signal_generator": {"address": "sim::sg"}},
        }
        document = ConfigurationEditingDocument.from_mapping(source)
        source["test_frequencies"].append(9.9)
        source["instruments"]["signal_generator"]["address"] = "sim::other"

        self.assertEqual(document.to_mapping()["test_frequencies"], [1.0])
        self.assertEqual(
            document.to_mapping()["instruments"]["signal_generator"]["address"],
            "sim::sg",
        )

    def test_editing_document_constructor_snapshots_the_input(self):
        source = {"test_frequencies": [1.0]}
        document = ConfigurationEditingDocument(values=source)
        source["test_frequencies"].append(2.0)

        self.assertEqual(document.to_mapping(), {"test_frequencies": [1.0]})

    def test_draft_entry_matches_the_document_entry(self):
        """同一份编辑数据经草稿或文档入口，正式段必须完全一致。

        草稿入口的 ``to_document()`` 会按绑定重建 ``instruments``（键序与缺省字段都会
        规范化），因此这里比较的是正式 ``test_plan`` / ``run_mapping`` 与其版本号，而不是
        整份编辑文档；整份文档的逐字节回归由 :class:`GoldenConfigurationTests` 负责。
        """
        flat = _flat_config()
        draft = ConfigurationDraft.from_document(
            flat, bindings=_bindings_for(flat)
        ).draft

        by_draft = ConfigurationAuthoringService().author(draft)
        by_document = author_official_configuration(flat)

        self.assertEqual(by_draft["schema_version"], OFFICIAL_SCHEMA_VERSION)
        self.assertEqual(by_draft["schema_version"], by_document["schema_version"])
        self.assertEqual(by_draft["test_plan"], by_document["test_plan"])
        self.assertEqual(by_draft["run_mapping"], by_document["run_mapping"])

    def test_draft_entry_does_not_mutate_the_draft(self):
        flat = _flat_config()
        draft = ConfigurationDraft.from_document(
            flat, bindings=_bindings_for(flat)
        ).draft
        original = copy.deepcopy(draft)
        ConfigurationAuthoringService().author(draft)
        self.assertEqual(draft, original)

    def test_draft_entry_rejects_non_drafts(self):
        with self.assertRaises(TypeError):
            ConfigurationAuthoringService().author(_flat_config())

    def test_validate_document_returns_no_errors_for_a_complete_configuration(self):
        result = ConfigurationAuthoringService().validate_document(
            ConfigurationEditingDocument.from_mapping(_flat_config())
        )

        self.assertTrue(result.valid, result.messages())

    def test_validate_document_reports_run_and_connection_issues(self):
        """正式运行校验与硬件连接校验都在应用边界执行，问题带来源错误码。"""
        flat = _flat_config()
        flat["test_frequencies"] = []
        flat["instruments"]["power_supplies"]["DP8B0885"] = _second_supply()
        flat["instruments"]["power_supplies"]["DP8B0885"]["address"] = "sim::ps1"

        result = ConfigurationAuthoringService().validate_document(
            ConfigurationEditingDocument.from_mapping(flat)
        )

        paths = {issue.path for issue in result.errors}
        self.assertIn("frequencies.values", paths)
        self.assertIn("instruments.power_supplies.DP8B0885.address", paths)
        self.assertEqual(
            {issue.code for issue in result.errors},
            {"configuration.run_validation", "configuration.hardware_connections"},
        )

    def test_validate_document_does_not_touch_the_callers_document(self):
        flat = _flat_config()
        original = copy.deepcopy(flat)
        document = ConfigurationEditingDocument.from_mapping(flat)

        ConfigurationAuthoringService().validate_document(document)

        self.assertEqual(flat, original)
        self.assertEqual(document.to_mapping(), original)

    def test_validate_document_rejects_non_documents(self):
        with self.assertRaises(TypeError):
            ConfigurationAuthoringService().validate_document(_flat_config())

    def test_legacy_1_0_document_is_refused_before_save(self):
        flat = _flat_config()
        flat["schema_version"] = "1.0"
        with self.assertRaises(ConfigurationAuthoringError) as caught:
            author_official_configuration(flat)
        self.assertEqual(caught.exception.path, "schema_version")

    def test_save_block_reason_is_the_same_gate_as_saving(self):
        """只读判定与保存门禁同源：1.0 给出原因，1.1 允许保存。"""
        service = ConfigurationAuthoringService()
        legacy = _flat_config()
        legacy["schema_version"] = "1.0"
        current = _flat_config()

        self.assertIn(
            "迁移器", service.save_block_reason(
                ConfigurationEditingDocument.from_mapping(legacy)
            )
        )
        self.assertIsNone(
            service.save_block_reason(
                ConfigurationEditingDocument.from_mapping(current)
            )
        )

    def test_newer_schema_version_is_refused(self):
        flat = _flat_config()
        flat["schema_version"] = "2.0"
        with self.assertRaises(ConfigurationAuthoringError) as caught:
            author_official_configuration(flat)
        self.assertEqual(caught.exception.path, "schema_version")

    def test_versionless_document_with_legacy_power_fields_is_refused(self):
        flat = _flat_config()
        flat.pop("schema_version")
        flat["instruments"]["power_supplies"]["DP8B1519"]["channels"]["CH1"]["role"] = (
            "gate"
        )
        with self.assertRaises(ConfigurationAuthoringError) as caught:
            author_official_configuration(flat)
        self.assertEqual(
            caught.exception.path,
            "instruments.power_supplies.DP8B1519.channels.CH1.role",
        )

    def test_1_1_legacy_editing_fields_are_not_written_back(self):
        flat = _flat_config()
        flat["instruments"]["power_supplies"]["DP8B1519"]["channels"]["CH1"]["role"] = (
            "gate"
        )
        flat["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"][
            "name"
        ] = "DP8B1519"
        document = author_official_configuration(flat)

        channels = document["instruments"]["power_supplies"]["DP8B1519"]["channels"]
        self.assertNotIn("role", channels["CH1"])
        dut_block = document["power_supply_assignment"]["dut_amplifier"]
        self.assertNotIn("supplies", dut_block)
        self.assertNotIn("power_supply_count", dut_block)

    def test_legacy_assignment_supplies_are_dropped(self):
        """旧编辑段的固定电源分配表只用于读取，保存后只保留上电计划。"""
        flat = _flat_config()
        entry = flat["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"]
        entry["supply_id"] = None
        entry["name"] = "DP8B1519"

        document = author_official_configuration(flat)

        dut_block = document["power_supply_assignment"]["dut_amplifier"]
        self.assertNotIn("supplies", dut_block)
        self.assertEqual(
            document["run_mapping"]["dut_power_plan"]["power_sequence"],
            [
                {"supply_id": "DP8B1519", "channel": "CH1"},
                {"supply_id": "DP8B1519", "channel": "CH2"},
            ],
        )

    def test_versionless_clean_draft_is_still_saveable(self):
        flat = _flat_config()
        flat.pop("schema_version")
        document = author_official_configuration(flat)
        self.assertEqual(document["schema_version"], OFFICIAL_SCHEMA_VERSION)

    def test_stale_formal_sections_are_reported(self):
        flat = _flat_config()
        flat["test_plan"] = {"schema_version": "1.1", "template": False}
        issues = ConfigurationAuthoringService().stale_formal_sections(
            ConfigurationEditingDocument.from_mapping(flat)
        )
        self.assertIn("configuration.stale_formal_sections", {issue.code for issue in issues})
        self.assertIn("test_plan", {issue.path for issue in issues})

    def test_current_formal_sections_are_not_stale(self):
        flat = _flat_config()
        authored = author_official_configuration(flat)
        issues = ConfigurationAuthoringService().stale_formal_sections(
            ConfigurationEditingDocument.from_mapping(authored)
        )
        self.assertEqual(issues, ())

    def test_runtime_metadata_is_ignored_in_stale_comparison(self):
        flat = _flat_config()
        authored = author_official_configuration(flat)
        authored["run_mapping"]["run_id"] = "run-1"
        authored["run_mapping"]["run_datetime"] = "2026-08-19T10:00:00"
        authored["run_mapping"]["operator"] = "tester"
        issues = ConfigurationAuthoringService().stale_formal_sections(
            ConfigurationEditingDocument.from_mapping(authored)
        )
        self.assertEqual(issues, ())

    def test_stale_sections_do_not_block_saving_current_edits(self):
        """页面编辑后派生段必然过期；保存时整段重写，不把 stale 当成本次编辑错误。"""
        flat = _flat_config()
        flat["test_plan"] = {"schema_version": "1.1", "template": False}
        result = ConfigurationAuthoringService().validate_document(
            ConfigurationEditingDocument.from_mapping(flat)
        )
        self.assertNotIn(
            "configuration.stale_formal_sections",
            {issue.code for issue in result.errors},
        )


class GoldenConfigurationTests(unittest.TestCase):
    """v1.1 正式文档基线：整个输出逐字节比较。

    输入是规范 1.1 编辑段（用户顺序、间隔、漏极与通道参数），期望值经步骤 6 评审后
    一次性生成，见 ``tests/fixtures/configuration_authoring/README.md``。它冻结正式段的
    字段名、键序与透传行为；1.0 输入的拒绝由本模块的版本门禁测试单独覆盖。
    """

    def test_authoring_reproduces_the_v1_1_document(self):
        fixture = json.loads(GOLDEN_FIXTURE.read_text(encoding="utf-8"))

        self.assertTrue(fixture["cases"], "编写基线不能为空")
        for case in fixture["cases"]:
            with self.subTest(case=case["name"]):
                self.assertEqual(
                    author_official_configuration(case["input"]), case["expected"]
                )

    def test_fixture_records_the_contract_it_came_from(self):
        fixture = json.loads(GOLDEN_FIXTURE.read_text(encoding="utf-8"))

        self.assertIn("v1.1", fixture["source"])
        for case in fixture["cases"]:
            with self.subTest(case=case["name"]):
                output = json.dumps(case["expected"], ensure_ascii=False)
                self.assertNotIn('"power_roles"', output)
                self.assertNotIn('"power_channels"', output)
                self.assertNotIn('"dut_power_channels"', output)


class PresentationFormalValidationBoundaryTests(unittest.TestCase):
    """页面只展示应用边界给出的正式校验结果，不自己导入 domain 正式模型。"""

    def test_form_state_delegates_formal_validation_to_the_application_service(self):
        source_path = ROOT / "presentation" / "qt" / "config_form_state.py"
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_names = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }

        self.assertFalse(
            imported_names
            & {
                "RunConfiguration",
                "RunResourceMapping",
                "TestPlan",
                "validate_run_configuration",
                "validate_hardware_connections",
            },
            "页面不得绕过应用边界做正式运行校验",
        )
        self.assertIn("validate_document", source)


class RemovedCompatibilityShellTests(unittest.TestCase):
    """旧转发壳（``presentation.qt.configuration_authoring``）已随步骤 13 删除。"""

    def test_forwarding_shell_file_is_gone(self):
        shell = ROOT / "presentation" / "qt" / "configuration_authoring.py"

        self.assertFalse(shell.exists(), "兼容转发壳不得重新出现")
        self.assertFalse(
            hasattr(__import__("presentation.qt", fromlist=["qt"]), "configuration_authoring"),
            "旧模块不得通过包属性重新暴露",
        )


class AuthoringDocumentValidationTests(unittest.TestCase):
    """上电计划、漏极与分配引用由 domain 判定，应用边界只转成结构化问题。"""

    def _document_with(self, mutate) -> dict:
        flat = _flat_config()
        mutate(flat)
        return flat

    @staticmethod
    def _plan_document() -> dict:
        flat = _flat_config()
        flat["dut_config"]["drain_channel"] = {
            "supply_id": "DP8B1519",
            "channel": "CH2",
        }
        flat["power_supply_assignment"]["dut_amplifier"]["power_plan"] = {
            "power_sequence": [
                {"supply_id": "DP8B1519", "channel": "CH1"},
                {"supply_id": "DP8B1519", "channel": "CH2"},
            ],
            "inter_channel_delay_s": 2.0,
        }
        flat["power_supply_assignment"]["driver_amplifier"]["power_plan"] = {
            "power_sequence": [],
            "inter_channel_delay_s": 0.0,
        }
        return flat

    def test_role_free_channels_are_accepted_by_the_editing_check(self):
        """步骤 5 起通道角色不再是草稿字段：编辑文档没有 role 时不再报角色缺失。"""

        def clear_roles(flat):
            channels = flat["instruments"]["power_supplies"]["DP8B1519"]["channels"]
            for channel in channels.values():
                channel.pop("role", None)

        result = ConfigurationAuthoringService().validate_document(
            ConfigurationEditingDocument.from_mapping(self._document_with(clear_roles))
        )

        self.assertTrue(result.valid, result.messages())
        self.assertNotIn(
            "configuration.power_plan",
            {issue.code for issue in result.errors},
        )

    def test_power_plan_and_drain_are_accepted(self):
        result = ConfigurationAuthoringService().validate_document(
            ConfigurationEditingDocument.from_mapping(self._plan_document())
        )
        self.assertTrue(result.valid, result.messages())

    def test_plan_is_independent_of_legacy_supplies(self):
        document = self._plan_document()
        document["dut_config"]["drain_channel"] = {
            "supply_id": "DP8B1519",
            "channel": "CH1",
        }
        document["power_supply_assignment"]["dut_amplifier"]["power_plan"][
            "power_sequence"
        ] = [{"supply_id": "DP8B1519", "channel": "CH1"}]
        # 旧编辑段的固定分配表与计划不再需要相等。
        document["power_supply_assignment"]["dut_amplifier"]["supplies"] = {
            "carrier": {"supply_id": "DP8B1519", "channel": ["CH1", "CH2"]}
        }

        result = ConfigurationAuthoringService().validate_document(
            ConfigurationEditingDocument.from_mapping(document)
        )

        self.assertTrue(result.valid, result.messages())

    def test_plan_route_must_reference_a_configured_channel(self):
        document = self._plan_document()
        document["power_supply_assignment"]["dut_amplifier"]["power_plan"][
            "power_sequence"
        ] = [
            {"supply_id": "DP8B1519", "channel": "CH1"},
            {"supply_id": "DP8B1519", "channel": "CH99"},
        ]

        result = ConfigurationAuthoringService().validate_document(
            ConfigurationEditingDocument.from_mapping(document)
        )

        self.assertTrue(
            any("CH99" in issue.message for issue in result.errors),
            [issue.message for issue in result.errors],
        )

    def test_plan_must_declare_both_keys(self):
        for key in ("power_sequence", "inter_channel_delay_s"):
            with self.subTest(key=key):
                document = self._plan_document()
                document["power_supply_assignment"]["dut_amplifier"]["power_plan"].pop(key)

                result = ConfigurationAuthoringService().validate_document(
                    ConfigurationEditingDocument.from_mapping(document)
                )

                self.assertIn(
                    f"power_supply_assignment.dut_amplifier.power_plan.{key}",
                    {issue.path for issue in result.errors},
                )
                self.assertIn(
                    "configuration.power_plan", {issue.code for issue in result.errors}
                )

    def test_plan_rejects_duplicate_physical_routes(self):
        document = self._plan_document()
        duplicate = {"supply_id": "DP8B1519", "channel": "CH1"}
        document["power_supply_assignment"]["dut_amplifier"]["power_plan"][
            "power_sequence"
        ] = [duplicate, duplicate]

        result = ConfigurationAuthoringService().validate_document(
            ConfigurationEditingDocument.from_mapping(document)
        )

        self.assertTrue(
            any("不能重复" in issue.message for issue in result.errors),
            [issue.message for issue in result.errors],
        )
        self.assertIn(
            "power_supply_assignment.dut_amplifier.power_plan.power_sequence",
            {issue.path for issue in result.errors},
        )

    def test_plan_rejects_out_of_range_delay(self):
        document = self._plan_document()
        document["power_supply_assignment"]["dut_amplifier"]["power_plan"][
            "inter_channel_delay_s"
        ] = 61

        result = ConfigurationAuthoringService().validate_document(
            ConfigurationEditingDocument.from_mapping(document)
        )

        self.assertIn(
            "power_supply_assignment.dut_amplifier.power_plan.inter_channel_delay_s",
            {issue.path for issue in result.errors},
        )

    def test_drain_must_be_a_dut_channel_in_the_power_sequence(self):
        document = self._plan_document()
        document["dut_config"]["drain_channel"] = {
            "supply_id": "DP8B1519",
            "channel": "CH99",
        }
        result = ConfigurationAuthoringService().validate_document(
            ConfigurationEditingDocument.from_mapping(document)
        )
        self.assertTrue(
            any(
                issue.path == "dut_config.drain_channel" and "上电顺序" in issue.message
                for issue in result.errors
            ),
            [f"{issue.path}: {issue.message}" for issue in result.errors],
        )

        document = self._plan_document()
        document["power_supply_assignment"]["dut_amplifier"]["power_plan"][
            "power_sequence"
        ] = [{"supply_id": "DP8B1519", "channel": "CH1"}]
        result = ConfigurationAuthoringService().validate_document(
            ConfigurationEditingDocument.from_mapping(document)
        )
        self.assertTrue(
            any(
                issue.path == "dut_config.drain_channel"
                and "上电顺序" in issue.message
                for issue in result.errors
            ),
            [f"{issue.path}: {issue.message}" for issue in result.errors],
        )

    def test_cross_group_plan_routes_are_rejected(self):
        document = self._plan_document()
        document["driver_mode"] = {"enabled": True, "power_source": "dc_supply"}
        document["instruments"]["power_supplies"]["DP8B0885"] = _second_supply()
        document["power_supply_assignment"]["driver_amplifier"] = {
            "power_supply_count": 1,
            "supplies": {"main": {"supply_id": "DP8B0885", "channel": ["CH1"]}},
            "power_plan": {
                "power_sequence": [{"supply_id": "DP8B1519", "channel": "CH1"}],
                "inter_channel_delay_s": 0.0,
            },
        }

        result = ConfigurationAuthoringService().validate_document(
            ConfigurationEditingDocument.from_mapping(document)
        )

        self.assertTrue(
            any("不能同时用于 DUT 与驱动功放" in issue.message for issue in result.errors),
            [issue.message for issue in result.errors],
        )

    def test_empty_dut_plan_is_reported_by_run_validation(self):
        document = self._plan_document()
        document["dut_config"].pop("drain_channel", None)
        document["power_supply_assignment"]["dut_amplifier"]["power_plan"] = {
            "power_sequence": [],
            "inter_channel_delay_s": 0.0,
        }

        result = ConfigurationAuthoringService().validate_document(
            ConfigurationEditingDocument.from_mapping(document)
        )

        self.assertIn(
            "run_mapping.dut_power_plan.power_sequence",
            {issue.path for issue in result.errors},
        )
        self.assertIn(
            "configuration.run_validation",
            {issue.code for issue in result.errors},
        )

    def test_plan_route_to_an_unapplied_supply_is_reported(self):
        def drop_supply(flat):
            flat["instruments"]["power_supplies"]["DP8B1519"].pop("address", None)

        result = ConfigurationAuthoringService().validate_document(
            ConfigurationEditingDocument.from_mapping(self._document_with(drop_supply))
        )

        self.assertTrue(
            any(
                "上电计划引用的电源 'DP8B1519' 没有已应用的设备" in issue.message
                for issue in result.errors
            ),
            [issue.message for issue in result.errors],
        )


if __name__ == "__main__":
    unittest.main()
