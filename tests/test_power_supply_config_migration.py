"""门禁：旧配置迁移器（电源重构计划步骤 13）。

固定的行为：

1. **现场样例可复现**：``config_slots_legacy.json`` + 现场设备表 ``registry_legacy_v2.json``
   迁移后与 ``config_slots_migrated_partial.json`` 逐字段相等，摘要与
   ``migration_summary_legacy.json`` 逐字段相等（只有 ``generated_at`` 随运行时间变化）；
2. **自动迁移的依据是登记的身份**：旧条目自带 ``device_key`` / ``serial`` 时按身份迁移；
   只有旧键 ``PSn`` 时按设备表里登记的 ``role`` 迁移，该角色对应多台就直接报 ``conflict``，
   绝不按顺序挑一台；
3. **身份字段必须互相印证**：``device_key`` 与 ``serial`` 指向两台不同的设备、注册表里同一
   身份对应多条记录、旧条目声明的 ``device_key`` 不在册，一律 ``conflict``；
4. **旧地址不是身份**：只有地址的条目一律 ``needs_rescan``，即使地址命中设备表的历史地址
   也只给 ``address_hint`` 提示，不给 ``new_key``；
5. **参数不丢**：每个旧条目（含 ``migrated`` 的）都有一条摘要，``channels`` 与
   ``assignments`` 原样取自旧配置；能迁移的电源条目与摘要里的通道参数逐字段相等；
6. **只有完整迁移才写盘**：``save_migrated_configuration`` 默认就跑文件级校验，任何
   ``needs_rescan`` / ``unidentifiable`` / ``conflict``、或仍未落地的分配引用都拒绝写入，
   原文件逐字节不变；认不出的参数靠 ``save_migration_summary`` 保存的摘要留存；
7. **迁移可重复执行**：对已迁移的配置再跑一次，结果与第一次相同（旧槽位不会复活）；
8. **设备表不被悄悄改写**：迁移只读设备表；要让旧槽位的短标签落盘，必须显式调用
   :func:`persist_registry_labels`；
9. **认不出的条目不会变成电源条目**：目标配置里没有它的电源条目，未绑定的引用写成
   ``supply_id: null``，运行映射的电源段与通道行都按迁移后的分配重建，不留旧槽位通道名。

验证方式：

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" -m unittest tests.test_power_supply_config_migration -v
```
"""

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from domain.configuration.rules import validate_config
from infrastructure.config.legacy_power_supply_migration import (
    STATUS_CONFLICT,
    STATUS_MIGRATED,
    STATUS_NEEDS_RESCAN,
    STATUS_UNIDENTIFIABLE,
    LegacyConfigMigrationError,
    LegacyMigrationResult,
    default_summary_path,
    legacy_address_hint,
    legacy_assignments,
    load_and_migrate,
    migrate_legacy_power_supplies,
    persist_registry_labels,
    save_migrated_configuration,
    save_migration_summary,
)
from infrastructure.config.validation import validate_official_config
from infrastructure.persistence.instrument_registry import InstrumentRegistry

ROOT = Path(__file__).parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "power_supply_identity"
LEGACY_CONFIG = FIXTURE_DIR / "config_slots_legacy.json"
MIGRATED_CONFIG = FIXTURE_DIR / "config_slots_migrated_partial.json"
LEGACY_REGISTRY = FIXTURE_DIR / "registry_legacy_v2.json"
MIGRATED_REGISTRY = FIXTURE_DIR / "registry_migrated_v3.json"
SUMMARY = FIXTURE_DIR / "migration_summary_legacy.json"
ONE_SUPPLY_CONFIG = FIXTURE_DIR / "config_one_supply.json"
TWO_SUPPLIES_CONFIG = FIXTURE_DIR / "config_two_supplies.json"

# 现场三台 DP832A：注册表里登记为 PS3 / 未登记 / PS4，配置里用的是 PS1..PS4。
SUPPLY_ONE = "rigol technologies:dp8b214901519"
SUPPLY_THREE = "rigol technologies:dp8b174400885"
LABEL_ONE = "DP8B1519"
LABEL_THREE = "DP8B0885"
UNREGISTERED = "rigol technologies:dp8b174700950"


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _entries(result):
    return {entry.old_key: entry for entry in result.entries}


def _legacy_two_supplies():
    """2 台电源的旧写法：``PS1``/``PS2`` 槽位 + 带身份的绑定记录。

    这是“每条旧条目都能识别、引用都能落地”的对照样例：两台设备都能从身份确认，绑定记录
    本身带 ``device_key``/``serial``（重新扫描确认过），因此迁移后地址与启用状态都有来源，
    整份配置应当通过文件级校验并允许写盘。
    """
    config = _load(TWO_SUPPLIES_CONFIG)
    supplies = config["instruments"]["power_supplies"]
    gate_supply = supplies.pop("DP8B1519")
    drain_supply = supplies.pop("DP8B0885")
    config["instruments"]["power_supplies"] = {
        "PS1": {
            "channels": gate_supply["channels"],
            "enabled": True,
            "serial": "DP8B214901519",
            "address": "TCPIP0::192.168.1.111::inst0::INSTR",
        },
        "PS2": {"channels": drain_supply["channels"], "enabled": False, "device_key": SUPPLY_THREE},
    }
    config["power_supply_assignment"]["dut_amplifier"]["supplies"] = {
        "gate": {"name": "PS1", "channel": ["CH1"]},
        "drain": {"name": "PS2", "channel": ["CH2"]},
    }
    bindings = config["device_bindings"]
    bindings["PS1"] = bindings["power_supplies"].pop("DP8B1519")
    bindings["PS2"] = bindings["power_supplies"].pop("DP8B0885")
    bindings.pop("power_supplies")
    config["run_mapping"]["instruments"].pop("power_supplies")
    config["run_mapping"]["dut_power_channels"] = []
    return config


class MigrationFixtureTests(unittest.TestCase):
    """现场样例：旧配置 + 现场设备表 -> 契约给的迁移结果与摘要。"""

    def setUp(self):
        self.config = _load(LEGACY_CONFIG)
        self.registry = _load(LEGACY_REGISTRY)
        self.result = migrate_legacy_power_supplies(self.config, self.registry)

    def test_current_config_migrates_to_the_contracted_configuration(self):
        self.assertEqual(self.result.config, _load(MIGRATED_CONFIG))
        self.assertEqual(self.result.old_keys, ("PS1", "PS2", "PS3", "PS4"))

    def test_summary_matches_the_contracted_sample(self):
        """摘要与契约样例只差“原因”的措辞与时间戳：判定、身份、参数与分配逐字段一致。

        原因文本是给操作员看的说明，模板措辞会随实现调整（样例里写的是 IP，迁移器给出
        完整 VISA 资源名）；门禁比较的是摘要的判定与参数字段，而不是这句话的措辞。
        """
        summary = self.result.summary(
            registry_source=LEGACY_REGISTRY.name,
            config_source=LEGACY_CONFIG.name,
            migrated_config_source=MIGRATED_CONFIG.name,
        )
        expected = _load(SUMMARY)
        for key in ("schema_version", "source"):
            self.assertEqual(summary[key], expected[key])
        self.assertIsInstance(summary["generated_at"], str)
        self.assertEqual(len(summary["entries"]), len(expected["entries"]))
        for actual, wanted in zip(summary["entries"], expected["entries"]):
            residue = {name: value for name, value in wanted.items() if name != "reason"}
            with self.subTest(old_key=wanted["old_key"]):
                self.assertEqual(
                    {name: actual[name] for name in residue}, residue
                )
                self.assertTrue(actual["reason"].strip())

    def test_statuses_follow_the_identity_evidence(self):
        entries = _entries(self.result)
        self.assertEqual(entries["PS1"].status, STATUS_NEEDS_RESCAN, "只有地址，不能迁移")
        self.assertEqual(entries["PS2"].status, STATUS_UNIDENTIFIABLE, "无地址无序列号")
        self.assertEqual(entries["PS3"].status, STATUS_MIGRATED)
        self.assertEqual(entries["PS4"].status, STATUS_MIGRATED)
        self.assertEqual(entries["PS3"].new_key, LABEL_ONE)
        self.assertEqual(entries["PS3"].device_key, SUPPLY_ONE)
        self.assertEqual(entries["PS4"].new_key, LABEL_THREE)
        self.assertEqual(entries["PS4"].device_key, SUPPLY_THREE)

    def test_migrated_supplies_keep_channels_and_lose_the_legacy_address(self):
        supplies = self.result.config["instruments"]["power_supplies"]
        legacy = self.config["instruments"]["power_supplies"]
        entries = _entries(self.result)
        for old_key, new_key in (("PS3", LABEL_ONE), ("PS4", LABEL_THREE)):
            with self.subTest(old_key=old_key):
                supply = supplies[new_key]
                self.assertEqual(supply["channels"], legacy[old_key]["channels"])
                self.assertEqual(supply["channels"], entries[old_key].channels)
                self.assertIs(supply["enabled"], False, "可用电源只来自设备绑定")
                self.assertEqual(supply["model"], "DP832A")
                self.assertNotIn("address", supply)
        self.assertEqual(list(supplies), [LABEL_ONE, LABEL_THREE], "认不出的旧条目不成条目")

    def test_dut_assignment_becomes_an_explicit_unbound_reference(self):
        carrier = self.result.config["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"]
        self.assertIsNone(carrier["supply_id"])
        self.assertNotIn("name", carrier, "认不出电源时不能留着旧槽位名冒充引用")
        self.assertEqual(carrier["channel"], ["CH1", "CH2"])
        self.assertEqual(
            self.result.config["power_supply_assignment"]["dut_amplifier"]["power_supply_count"], 0
        )

    def test_legacy_slot_bindings_are_dropped_and_the_collection_kept(self):
        bindings = self.result.config["device_bindings"]
        self.assertNotIn("PS1", bindings)
        self.assertEqual(bindings["power_supplies"], {})
        self.assertIn("signal_generator", bindings)

    def test_single_power_supply_run_mapping_becomes_an_empty_collection(self):
        mapping = self.result.config["run_mapping"]
        self.assertNotIn("power_supply", mapping["instruments"])
        self.assertEqual(mapping["instruments"]["power_supplies"], {})
        self.assertEqual(mapping["dut_power_channels"], [], "旧写法的通道没有物理路由")

    def test_every_entry_reports_its_assignments_and_reason(self):
        carrier = _entries(self.result)["PS1"].assignments
        self.assertEqual(len(carrier), 1)
        self.assertEqual(carrier[0], {"group": "dut_amplifier", "role_key": "carrier",
                                      "channels": ["CH1", "CH2"]})
        for entry in self.result.entries:
            with self.subTest(old_key=entry.old_key):
                self.assertTrue(entry.reason.strip(), "每条摘要都要有面向操作员的原因")

    def test_unresolved_entries_are_reported_by_the_result(self):
        unresolved = {old_key for old_key, _reason in self.result.unresolved}
        self.assertEqual(unresolved, {"PS1", "PS2"})

    def test_partial_result_is_still_self_consistent_but_not_runnable(self):
        """迁移结果自身可读（可继续被配置页打开），但运行前校验会要求补全引用。"""
        hardware = validate_config(self.result.config)
        self.assertFalse(hardware.valid)
        paths = {issue.path for issue in hardware.errors}
        self.assertIn("power_supply_assignment.dut_amplifier.supplies.carrier.supply_id", paths)

    def test_source_objects_are_not_modified(self):
        config_before = copy.deepcopy(self.config)
        registry_before = copy.deepcopy(self.registry)
        migrate_legacy_power_supplies(self.config, self.registry)
        self.assertEqual(self.config, config_before)
        self.assertEqual(self.registry, registry_before)

    def test_migrated_registry_table_produces_the_same_answer(self):
        """折叠了旧槽位角色的设备表：旧键失效后不能凭编号猜设备，目标配置不因此产条目。"""
        from_v3 = migrate_legacy_power_supplies(self.config, _load(MIGRATED_REGISTRY))
        statuses = {entry.old_key: entry.status for entry in from_v3.entries}
        self.assertEqual(statuses["PS1"], STATUS_NEEDS_RESCAN, "地址仍是线索，要重扫")
        for old_key in ("PS2", "PS3", "PS4"):
            with self.subTest(old_key=old_key):
                self.assertEqual(statuses[old_key], STATUS_UNIDENTIFIABLE)
        self.assertEqual(from_v3.config["instruments"]["power_supplies"], {})
        self.assertEqual(
            {old_key for old_key, _reason in from_v3.unresolved},
            {"PS1", "PS2", "PS3", "PS4"},
        )

    def test_migration_is_idempotent(self):
        again = migrate_legacy_power_supplies(self.result.config, self.registry)
        self.assertEqual(
            again.config["instruments"]["power_supplies"],
            self.result.config["instruments"]["power_supplies"],
        )
        self.assertEqual(
            again.config["power_supply_assignment"],
            self.result.config["power_supply_assignment"],
        )
        self.assertEqual(again.old_keys, (LABEL_ONE, LABEL_THREE), "已迁移的键原样保留")
        self.assertEqual(
            [entry.status for entry in again.entries], [STATUS_MIGRATED, STATUS_MIGRATED]
        )
        self.assertNotIn("legacy_address", json.dumps(again.config, ensure_ascii=False))

    def test_partially_migrated_document_still_reports_what_is_missing(self):
        """把半成品再迁移一次也不会“看起来完整”：未落地的引用仍然被点出来，且不允许写盘。

        认不出的旧条目的通道参数只存在于摘要里（``config_slots_migrated_partial.json`` 里
        已经没有 ``PS1``/``PS2``）。因此半成品永远不能当配置保存——这正是
        ``save_migrated_configuration`` 拒绝未决结果的原因；参数靠
        ``save_migration_summary`` 写的摘要文件留存。
        """
        again = migrate_legacy_power_supplies(self.result.config, self.registry)
        self.assertEqual(again.pending, ())
        self.assertEqual(again.unresolved_references, ("dut_amplifier.carrier",))
        self.assertFalse(again.writable, "引用没落地就不是完整迁移")
        self.assertIn("dut_amplifier.carrier", again.summary_text())


class AddressIsNotIdentityTests(unittest.TestCase):
    """地址只作重扫线索：命中设备表也只给提示。"""

    def setUp(self):
        self.registry = _load(LEGACY_REGISTRY)

    def test_hint_follows_known_addresses(self):
        hint = legacy_address_hint(self.registry, "TCPIP0::192.168.1.102::inst0::INSTR")
        self.assertEqual(hint, SUPPLY_ONE)
        self.assertIsNone(legacy_address_hint(self.registry, "TCPIP0::192.168.1.101::inst0::INSTR"))
        self.assertIsNone(legacy_address_hint(self.registry, None))

    def test_address_hit_still_needs_a_rescan(self):
        config = _load(LEGACY_CONFIG)
        config["instruments"]["power_supplies"]["PS1"]["address"] = "TCPIP0::192.168.1.102::inst0::INSTR"
        # PS1 同时命中 PS3 那台设备的地址与角色：角色名不是 PS1，所以只按地址处理。
        entry = _entries(migrate_legacy_power_supplies(config, self.registry))["PS1"]
        self.assertEqual(entry.status, STATUS_NEEDS_RESCAN)
        self.assertEqual(entry.address_hint, SUPPLY_ONE)
        self.assertIsNone(entry.new_key, "地址命中不能据此自动改挂")
        self.assertIsNone(entry.device_key)

    def test_ipv4_forms_are_recognised(self):
        self.assertEqual(
            legacy_address_hint(self.registry, "TCPIP0::192.168.1.108::5555::SOCKET"), SUPPLY_THREE
        )
        self.assertEqual(legacy_address_hint(self.registry, "192.168.1.102"), SUPPLY_ONE)
        self.assertIsNone(legacy_address_hint(self.registry, "GPIB0::12::INSTR"))


class IdentityRuleTests(unittest.TestCase):
    """判定顺序与“绝不猜身份”的边界。"""

    def setUp(self):
        self.config = _load(LEGACY_CONFIG)
        self.registry = _load(LEGACY_REGISTRY)

    def _supply(self, **overrides):
        item = {
            "enabled": True,
            "channels": {
                "CH1": {"role": "gate", "voltage": {"value": 2.8}, "current": {"value": 0.1}},
                "CH2": {"role": "drain", "voltage": {"value": 28.0}, "current": {"value": 2.0}},
            },
        }
        item.update(overrides)
        return item

    def _config_with(self, supplies, *, references=None):
        config = copy.deepcopy(self.config)
        config["instruments"]["power_supplies"] = supplies
        if references is not None:
            config["power_supply_assignment"] = references
        return config

    def test_entry_with_serial_is_migrated_by_identity(self):
        config = self._config_with({"PS1": self._supply(serial="DP8B214901519", address="TCPIP0::192.168.9.9::inst0::INSTR")})
        result = migrate_legacy_power_supplies(config, self.registry)
        entry = _entries(result)["PS1"]
        self.assertEqual(entry.status, STATUS_MIGRATED)
        self.assertEqual(entry.new_key, LABEL_ONE)
        self.assertEqual(entry.device_key, SUPPLY_ONE)
        self.assertIn(LABEL_ONE, result.config["instruments"]["power_supplies"])
        self.assertNotIn("PS1", result.config["instruments"]["power_supplies"])

    def test_entry_with_device_key_is_migrated_even_on_a_renamed_slot(self):
        config = self._config_with({"CUSTOM": self._supply(device_key=SUPPLY_THREE)})
        entry = _entries(migrate_legacy_power_supplies(config, self.registry))["CUSTOM"]
        self.assertEqual(entry.status, STATUS_MIGRATED)
        self.assertEqual(entry.new_key, LABEL_THREE)

    def test_slot_role_matching_two_devices_is_a_conflict(self):
        registry = copy.deepcopy(self.registry)
        second = copy.deepcopy(registry["instruments"][0])
        second.update({"name": "DP832A-9", "model_index": 9, "serial": "DP8B999999999",
                       "role": "PS3", "current_ip": "", "known_ips": []})
        registry["instruments"].append(second)
        entry = _entries(migrate_legacy_power_supplies(self.config, registry))["PS3"]
        self.assertEqual(entry.status, STATUS_CONFLICT)
        self.assertIsNone(entry.new_key)
        self.assertIn("2 台", entry.reason)
        self.assertIn("DP832A-9", entry.reason)

    def test_two_old_keys_on_the_same_device_conflict(self):
        supplies = {
            "PS1": self._supply(serial="DP8B214901519"),
            "PS2": self._supply(serial="DP8B214901519"),
        }
        result = migrate_legacy_power_supplies(self._config_with(supplies), self.registry)
        entries = _entries(result)
        self.assertEqual(entries["PS1"].status, STATUS_MIGRATED)
        self.assertEqual(entries["PS2"].status, STATUS_CONFLICT)
        self.assertTrue(result.blocked)
        self.assertEqual(entries["PS2"].new_key, None)
        self.assertEqual(list(result.config["instruments"]["power_supplies"]), [LABEL_ONE])

    def test_entry_claiming_a_serial_the_registry_does_not_know_needs_a_rescan(self):
        config = self._config_with({"PS1": self._supply(serial="DP8B000000000")})
        entry = _entries(migrate_legacy_power_supplies(config, self.registry))["PS1"]
        self.assertEqual(entry.status, STATUS_UNIDENTIFIABLE)
        self.assertIsNone(entry.new_key)

    def test_unregistered_device_is_never_guessed_from_the_model(self):
        registry = copy.deepcopy(self.registry)
        registry["instruments"][1]["role"] = None
        registry["instruments"][1]["label"] = None
        config = self._config_with({"PS2": self._supply(model="DP832A")})
        entry = _entries(migrate_legacy_power_supplies(config, registry))["PS2"]
        self.assertEqual(entry.status, STATUS_UNIDENTIFIABLE)
        self.assertNotEqual(entry.device_key, UNREGISTERED)

    def test_registry_without_labels_asks_for_a_registry_save_first(self):
        config = self._config_with({"PS3": self._supply()})
        entry = _entries(
            migrate_legacy_power_supplies(config, self.registry, migrate_registry_labels=False)
        )["PS3"]
        self.assertEqual(entry.status, STATUS_NEEDS_RESCAN)
        self.assertIsNone(entry.new_key)
        self.assertIn("短标签", entry.reason)

    def test_slot_role_claiming_two_devices_conflicts(self):
        """``PS3`` 在设备表里对应两台设备：不按顺序挑一台，直接报 conflict。"""
        supplies = {"PS3": self._supply(), "PS4": self._supply()}
        registry = copy.deepcopy(self.registry)
        registry["instruments"][2]["role"] = "PS3"
        result = migrate_legacy_power_supplies(self._config_with(supplies), registry)
        statuses = {entry.old_key: entry.status for entry in result.entries}
        self.assertEqual(statuses["PS3"], STATUS_CONFLICT)
        self.assertEqual(statuses["PS4"], STATUS_UNIDENTIFIABLE, "PS4 已无设备可对应")
        self.assertTrue(result.blocked)
        self.assertEqual(result.config["instruments"]["power_supplies"], {})

    def test_identity_source_takes_precedence_over_slot_role(self):
        """带序列号的条目按序列号走，不受同名旧槽位角色的影响。"""
        config = self._config_with({"PS4": self._supply(serial="DP8B214901519")})
        entry = _entries(migrate_legacy_power_supplies(config, self.registry))["PS4"]
        self.assertEqual(entry.status, STATUS_MIGRATED)
        self.assertEqual(entry.new_key, LABEL_ONE)
        self.assertIn("自带设备身份", entry.reason)


class ReferenceRewriteTests(unittest.TestCase):
    """分配引用、绑定与运行映射的改写。"""

    def setUp(self):
        self.registry = _load(LEGACY_REGISTRY)

    def _config(self):
        config = _load(LEGACY_CONFIG)
        supplies = config["instruments"]["power_supplies"]
        supplies["PS3"]["channels"] = copy.deepcopy(supplies["PS1"]["channels"])
        return config

    def test_references_to_migrated_supplies_are_rewritten_with_the_alias(self):
        config = self._config()
        config["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"]["name"] = "PS3"
        result = migrate_legacy_power_supplies(config, self.registry)
        carrier = result.config["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"]
        self.assertEqual(carrier["supply_id"], LABEL_ONE)
        self.assertEqual(carrier["name"], LABEL_ONE, "过渡别名与新引用同值")
        self.assertEqual(
            result.config["power_supply_assignment"]["dut_amplifier"]["power_supply_count"], 1
        )
        self.assertEqual(
            result.config["dut_config"]["power_supply_count"],
            1,
            "dut_config 是测试方案参数，迁移不改测试方案",
        )

    def test_run_mapping_follows_the_references(self):
        config = self._config()
        config["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"]["name"] = "PS3"
        config["device_bindings"]["PS3"] = {
            "name": "DP832A-1",
            "vendor": "RIGOL TECHNOLOGIES",
            "model": "DP832A",
            "serial": "DP8B214901519",
            "device_key": SUPPLY_ONE,
            "visa_address": "TCPIP0::192.168.1.102::inst0::INSTR",
            "last_ip": "192.168.1.102",
            "last_port": 5555,
            "known": True,
            "source": "scan",
        }
        result = migrate_legacy_power_supplies(config, self.registry)
        mapping = result.config["run_mapping"]
        self.assertNotIn("power_supply", mapping["instruments"])
        supplies = mapping["instruments"]["power_supplies"]
        self.assertEqual(list(supplies), [LABEL_ONE])
        entry = supplies[LABEL_ONE]
        self.assertEqual(entry["label"], LABEL_ONE)
        self.assertEqual(entry["serial"], "DP8B214901519")
        self.assertEqual(entry["device_key"], SUPPLY_ONE)
        # 快照里的地址来自设备绑定（重新扫描确认过的连接目标）；旧条目自己那个未经确认的
        # 地址不会出现在这里。
        self.assertEqual(entry["visa_address"], "TCPIP0::192.168.1.102::inst0::INSTR")
        self.assertEqual(
            mapping["dut_power_channels"],
            [
                {
                    "channel": f"{LABEL_ONE}/CH1",
                    "role": "gate",
                    "connection": f"{LABEL_ONE}/CH1 -> DUT 栅极",
                },
                {
                    "channel": f"{LABEL_ONE}/CH2",
                    "role": "drain",
                    "connection": f"{LABEL_ONE}/CH2 -> DUT 漏极",
                },
            ],
            "通道行按迁移后的分配重写：旧快照的 PS1-CH1 是旧槽位名，没有物理路由",
        )

    def test_unconfirmed_supply_keeps_no_address_or_snapshot_entry(self):
        """没有身份绑定的旧地址不写进配置，也不进运行快照。"""
        config = self._config()
        result = migrate_legacy_power_supplies(config, self.registry)
        supply = result.config["instruments"]["power_supplies"][LABEL_ONE]
        self.assertNotIn("address", supply, "未确认的旧地址不能成为连接目标")
        self.assertEqual(result.config["run_mapping"]["instruments"]["power_supplies"], {})
        self.assertEqual(
            result.config["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"][
                "supply_id"
            ],
            None,
        )

    def test_trusted_binding_supplies_the_address_and_others_do_not(self):
        config = self._config()
        config["device_bindings"]["PS3"] = {
            "name": "DP832A-1",
            "vendor": "RIGOL TECHNOLOGIES",
            "model": "DP832A",
            "serial": "DP8B214901519",
            "device_key": SUPPLY_ONE,
            "visa_address": "TCPIP0::192.168.1.102::inst0::INSTR",
            "last_ip": "192.168.1.102",
            "last_port": 5555,
            "known": True,
            "source": "scan",
        }
        result = migrate_legacy_power_supplies(config, self.registry)
        supplies = result.config["instruments"]["power_supplies"]
        self.assertEqual(
            supplies[LABEL_ONE]["address"], "TCPIP0::192.168.1.102::inst0::INSTR"
        )
        self.assertIs(
            supplies[LABEL_ONE]["enabled"], True, "有确认过的地址才启用，与配置页的绑定规则一致"
        )
        self.assertNotIn("address", supplies[LABEL_THREE], "没有确认过的地址不写进配置")
        self.assertIs(supplies[LABEL_THREE]["enabled"], False, "没有绑定的电源保持未启用")
        bindings = result.config["device_bindings"]["power_supplies"]
        self.assertEqual(list(bindings), [LABEL_ONE])
        self.assertEqual(bindings[LABEL_ONE]["device_key"], SUPPLY_ONE)

    def test_existing_new_style_references_are_kept(self):
        config = self._config()
        config["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"] = {
            "supply_id": "SOME-SUPPLY",
            "channel": ["CH1"],
        }
        result = migrate_legacy_power_supplies(config, self.registry)
        carrier = result.config["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"]
        self.assertEqual(carrier, {"supply_id": "SOME-SUPPLY", "channel": ["CH1"]})


class SummaryHelperTests(unittest.TestCase):
    """摘要字段只有一份实现，供迁移器与界面共用。"""

    def test_assignments_are_read_through_the_single_reference_rule(self):
        config = _load(LEGACY_CONFIG)
        self.assertEqual(
            legacy_assignments(config, "PS1"),
            [{"group": "dut_amplifier", "role_key": "carrier", "channels": ["CH1", "CH2"]}],
        )
        self.assertEqual(legacy_assignments(config, "PS2"), [])

    def test_summary_text_lists_every_entry(self):
        result = migrate_legacy_power_supplies(_load(LEGACY_CONFIG), _load(LEGACY_REGISTRY))
        text = result.summary_text()
        for old_key in ("PS1", "PS2", "PS3", "PS4"):
            self.assertIn(old_key, text)
        self.assertIn(LABEL_ONE, text)


class SavingTests(unittest.TestCase):
    """写盘门槛：只有“每条都能迁移 + 校验通过”才写；任何未决项都不覆盖原文件。"""

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.directory = Path(self.temporary_directory.name)
        self.config_path = self.directory / "config.json"
        shutil.copyfile(LEGACY_CONFIG, self.config_path)
        self.original_bytes = self.config_path.read_bytes()

    def test_pending_migration_is_never_written(self):
        """现场配置有两个认不出身份的槽位：拒绝写盘，原文件逐字节不变。

        这条是“参数不丢”的关键：认不出的旧条目的通道参数只在摘要里，一旦写进
        ``config.json``，它们就从配置里彻底消失，而原配置也不再是“还能继续用”的那份。
        """
        result = migrate_legacy_power_supplies(_load(LEGACY_CONFIG), _load(LEGACY_REGISTRY))
        self.assertFalse(result.writable)
        self.assertEqual({entry.old_key for entry in result.pending}, {"PS1", "PS2"})
        self.assertEqual(result.unresolved_references, ("dut_amplifier.carrier",))

        with self.assertRaises(LegacyConfigMigrationError) as caught:
            save_migrated_configuration(self.config_path, result)
        message = str(caught.exception)
        self.assertIn("PS1", message)
        self.assertIn("PS2", message)
        self.assertIn("摘要", message, "要告诉操作员参数在哪、下一步怎么做")
        self.assertEqual(self.config_path.read_bytes(), self.original_bytes)

    def test_summary_file_keeps_the_unresolved_parameters(self):
        """摘要写成独立文件：认不出的旧条目的通道参数能离开进程，配置本身不动。"""
        result = migrate_legacy_power_supplies(_load(LEGACY_CONFIG), _load(LEGACY_REGISTRY))
        summary_path = default_summary_path(self.config_path)

        written = save_migration_summary(
            summary_path,
            result,
            registry_source=LEGACY_REGISTRY.name,
            config_source=LEGACY_CONFIG.name,
        )

        self.assertEqual(written, summary_path)
        self.assertEqual(summary_path.name, "config.json.migration-summary.json")
        self.assertEqual(self.config_path.read_bytes(), self.original_bytes)
        summary = _load(summary_path)
        self.assertEqual(
            [entry["old_key"] for entry in summary["entries"]], ["PS1", "PS2", "PS3", "PS4"]
        )
        ps1 = summary["entries"][0]
        self.assertEqual(ps1["status"], STATUS_NEEDS_RESCAN)
        self.assertEqual(ps1["channels"]["CH2"]["voltage"]["value"], 28.0)
        self.assertEqual(ps1["channels"]["CH2"]["current"]["value"], 2.0)
        self.assertEqual(ps1["legacy_address"], "TCPIP0::192.168.1.101::inst0::INSTR")

    def test_fully_migrated_configuration_is_written_and_valid(self):
        """每条都能识别、引用都能落地时，结果原子写入并通过文件级校验。"""
        result = migrate_legacy_power_supplies(_legacy_two_supplies(), _load(LEGACY_REGISTRY))
        self.assertTrue(result.writable, [entry.reason for entry in result.pending])
        self.assertEqual(result.unresolved_references, ())

        written = save_migrated_configuration(self.config_path, result)

        self.assertEqual(written, self.config_path)
        self.assertEqual(_load(self.config_path), result.config)
        self.assertTrue(
            validate_official_config(_load(self.config_path)).valid,
            "写进 config.json 的迁移结果必须能通过正式校验",
        )

    def test_blocked_migration_leaves_the_file_untouched(self):
        supplies = {
            "PS1": {"enabled": True, "serial": "DP8B214901519", "channels": {}},
            "PS2": {"enabled": True, "serial": "DP8B214901519", "channels": {}},
        }
        config = _load(LEGACY_CONFIG)
        config["instruments"]["power_supplies"] = supplies
        result = migrate_legacy_power_supplies(config, _load(LEGACY_REGISTRY))
        self.assertTrue(result.blocked)

        with self.assertRaises(LegacyConfigMigrationError) as caught:
            save_migrated_configuration(self.config_path, result)
        self.assertIn("PS2", str(caught.exception))
        self.assertEqual(self.config_path.read_bytes(), self.original_bytes)

    def test_validation_failure_keeps_the_file(self):
        """迁移本身没有未决项，但结果过不了文件级校验时同样拒绝覆盖原文件。"""
        path = self.directory / "two_supplies.json"
        shutil.copyfile(TWO_SUPPLIES_CONFIG, path)
        before = path.read_bytes()
        config = _load(TWO_SUPPLIES_CONFIG)
        # 把一组引用指向一台不存在的电源：迁移本身不产生未决项，但校验必然失败。
        config["power_supply_assignment"]["dut_amplifier"]["supplies"]["gate"]["supply_id"] = (
            "MISSING"
        )
        result = migrate_legacy_power_supplies(config, _load(MIGRATED_REGISTRY))
        self.assertTrue(result.writable, "这里要测的是校验器，而不是未决项门槛")

        with self.assertRaises(LegacyConfigMigrationError) as caught:
            save_migrated_configuration(path, result)
        self.assertIn("MISSING", str(caught.exception))
        self.assertEqual(path.read_bytes(), before)

    def test_load_and_migrate_reads_both_files_without_writing(self):
        registry_path = self.directory / "instruments.json"
        shutil.copyfile(LEGACY_REGISTRY, registry_path)
        registry_bytes = registry_path.read_bytes()

        result = load_and_migrate(self.config_path, registry_path)

        self.assertIsInstance(result, LegacyMigrationResult)
        self.assertEqual(self.config_path.read_bytes(), self.original_bytes)
        self.assertEqual(registry_path.read_bytes(), registry_bytes)
        self.assertEqual(result.config, _load(MIGRATED_CONFIG))

    def test_persisting_registry_labels_is_explicit(self):
        registry_path = self.directory / "instruments.json"
        shutil.copyfile(LEGACY_REGISTRY, registry_path)

        persist_registry_labels(registry_path)

        saved = InstrumentRegistry.load(registry_path)
        self.assertEqual(saved.schema_version, 3)
        by_serial = {item.serial: item for item in saved.list()}
        self.assertEqual(by_serial["DP8B214901519"].label, LABEL_ONE)
        self.assertEqual(by_serial["DP8B174400885"].label, LABEL_THREE)
        # 短标签由序列号派生、与角色无关：未登记角色的那台也会拿到标签，只是 role 仍是 null。
        self.assertEqual(by_serial["DP8B174700950"].label, "DP8B0950")
        self.assertIsNone(by_serial["DP8B174700950"].role)


class IdentityContradictionTests(unittest.TestCase):
    """身份字段互相矛盾或注册表身份不唯一：报 conflict，绝不挑一个继续迁移。"""

    def setUp(self):
        self.registry = _load(LEGACY_REGISTRY)

    def _entry(self, **overrides):
        item = {"enabled": True, "channels": {}}
        item.update(overrides)
        return item

    def _migrate_one(self, entry, registry=None, **kwargs):
        config = _load(LEGACY_CONFIG)
        config["instruments"]["power_supplies"] = {"PS1": entry}
        result = migrate_legacy_power_supplies(config, registry or self.registry, **kwargs)
        return _entries(result)["PS1"], result

    def test_device_key_and_serial_pointing_to_different_devices_conflict(self):
        entry, result = self._migrate_one(
            self._entry(device_key=SUPPLY_THREE, serial="DP8B214901519")
        )
        self.assertEqual(entry.status, STATUS_CONFLICT)
        self.assertIsNone(entry.new_key)
        self.assertIn("自相矛盾", entry.reason)
        self.assertTrue(result.blocked)
        self.assertEqual(result.config["instruments"]["power_supplies"], {})

    def test_device_key_missing_from_the_registry_conflicts(self):
        """序列号能对上某台设备，但旧条目声明的 device_key 不在册：数据矛盾，不是身份。"""
        entry, _result = self._migrate_one(
            self._entry(device_key="keysight technologies:my123456", serial="DP8B214901519")
        )
        self.assertEqual(entry.status, STATUS_CONFLICT)
        self.assertIn("不在设备表里", entry.reason)

    def test_duplicate_device_key_in_the_registry_conflicts(self):
        registry = copy.deepcopy(self.registry)
        duplicate = copy.deepcopy(registry["instruments"][0])
        duplicate["name"] = "DP832A-9"
        duplicate["model_index"] = 9
        registry["instruments"].append(duplicate)
        # 走原样设备表：注册表保存路径本身也会拒绝重复序列号，这里测的是迁移器的判定。
        entry, _result = self._migrate_one(
            self._entry(serial="DP8B214901519"), registry, migrate_registry_labels=False
        )
        self.assertEqual(entry.status, STATUS_CONFLICT)
        self.assertIn("共用序列号", entry.reason)

    def test_serial_shared_by_two_vendors_conflicts(self):
        registry = copy.deepcopy(self.registry)
        other = copy.deepcopy(registry["instruments"][0])
        other.update({"name": "N6705C-1", "vendor": "KEYSIGHT", "model": "N6705C", "model_index": 1})
        registry["instruments"].append(other)
        entry, _result = self._migrate_one(
            self._entry(serial="DP8B214901519"), registry, migrate_registry_labels=False
        )
        self.assertEqual(entry.status, STATUS_CONFLICT)
        self.assertIn("序列号", entry.reason)
        self.assertIn("N6705C-1", entry.reason)

    def test_stale_serial_is_reported_but_device_key_wins(self):
        """device_key 唯一命中时按它迁移，但旧条目里的序列号不一致必须写进原因。"""
        entry, _result = self._migrate_one(
            self._entry(device_key=SUPPLY_ONE, serial="DP8B000000000")
        )
        self.assertEqual(entry.status, STATUS_MIGRATED)
        self.assertEqual(entry.new_key, LABEL_ONE)
        self.assertIn("与设备表不一致", entry.reason)


class AlreadyMigratedConfigurationTests(unittest.TestCase):
    """已经写成新格式的配置不被迁移器改写。"""

    def test_new_format_configuration_is_left_alone(self):
        config = _load(ONE_SUPPLY_CONFIG)
        result = migrate_legacy_power_supplies(config, _load(MIGRATED_REGISTRY))
        self.assertEqual(result.old_keys, (LABEL_ONE,))
        self.assertEqual(result.config, config)
        self.assertEqual(result.entries[0].status, STATUS_MIGRATED)

    def test_invalid_documents_are_rejected_before_migration(self):
        with self.assertRaises(LegacyConfigMigrationError):
            migrate_legacy_power_supplies([], {})
        with self.assertRaises(LegacyConfigMigrationError):
            migrate_legacy_power_supplies({"instruments": {"power_supplies": []}}, {})


if __name__ == "__main__":
    unittest.main()
