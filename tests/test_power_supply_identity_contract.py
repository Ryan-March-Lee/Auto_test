"""门禁：电源设备身份与配置数据契约（重构计划步骤 1）。

本文件与[电源设备身份与配置数据契约](../docs/电源设备身份与配置数据契约.md)一一对应。它只校验
契约数据和 `tests/fixtures/power_supply_identity/` 里的样例是否自洽，不实现标签生成器、注册表迁移、
配置页面或运行映射——那些属于后续步骤。

覆盖：

- 每份 fixture 都是可解析的 JSON，仓库里的契约文档确实登记了每一个 fixture；
- 注册表的 `label` 与契约第 3 节给出的期望标签逐步一致，`device_key` 由 `厂商:序列号` 生成；
- 注册表迁移只改 `role` 与 `label`：资产名、序列号、地址都不动，未登记角色的设备不会被猜成电源；
- 配置里的电源键、条目内 `label` 与分配引用的 `supply_id` 是同一个字符串，且分配不引用不存在的电源；
- 1 台 / 2 台 / 5 台三种规模都能表达，旧槽位只出现在迁移输入与旧运行快照里；
- 运行快照只冻结实际使用的电源，通道一律写 `supply_id/CHx`；
- 迁移摘要覆盖每个旧槽位：无法迁移条目的旧通道参数与分配记录不丢失，且不会变成目标配置里的电源条目。
"""

import json
import unittest
from pathlib import Path

from domain.instruments.models import (
    POWER_SUPPLY_SLOTS,
    make_device_key,
    normalize_serial,
)


ROOT = Path(__file__).parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "power_supply_identity"
CONTRACT_DOC = ROOT / "docs" / "电源设备身份与配置数据契约.md"

# 契约第 3 节：现场三台 DP832A 与两台样例设备的期望标签（前缀 + 末四位）。
EXPECTED_LABELS = {
    "DP8B214901519": "DP8B1519",
    "DP8B174700950": "DP8B0950",
    "DP8B174400885": "DP8B0885",
    "DP8B234501191": "DP8B1191",
    "DP8B234701216": "DP8B1216",
}

# 目标格式的配置样例：1 台、2 台、5 台，以及迁移后仍需人工补全的部分迁移结果。
DYNAMIC_CONFIGS = (
    "config_one_supply.json",
    "config_two_supplies.json",
    "config_five_supplies.json",
    "config_slots_migrated_partial.json",
)
EXPECTED_SCALES = {
    "config_one_supply.json": 1,
    "config_two_supplies.json": 2,
    "config_five_supplies.json": 5,
    "config_slots_migrated_partial.json": 2,
}
REGISTRY_FIXTURES = (
    "registry_legacy_v2.json",
    "registry_migrated_v3.json",
    "registry_migrated_v3_all_registered.json",
)
# 已经写完标签的设备表；旧版本设备表由 test_migration_only_changes_role_and_label 单独对照。
MIGRATED_REGISTRIES = (
    "registry_migrated_v3.json",
    "registry_migrated_v3_all_registered.json",
)
LEGACY_CONFIG = "config_slots_legacy.json"
MIGRATED_CONFIG = "config_slots_migrated_partial.json"
SUMMARY_FIXTURE = "migration_summary_legacy.json"
# 契约第 9 节：迁移判定只有这四种结果。
SUMMARY_STATUSES = ("migrated", "needs_rescan", "unidentifiable", "conflict")


def _load(name: str) -> dict:
    path = FIXTURE_DIR / name
    if not path.is_file():
        raise AssertionError(f"缺少 fixture: {name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _instruments(document: dict) -> list:
    return list(document.get("instruments") or [])


def _supplies(config: dict) -> dict:
    return dict((config.get("instruments") or {}).get("power_supplies") or {})


def _assignments(config: dict) -> list:
    """返回所有分配条目 (组名, 角色键, 条目)。"""
    groups = config.get("power_supply_assignment") or {}
    entries = []
    for group_name, group in groups.items():
        for role_key, entry in (group or {}).get("supplies", {}).items():
            entries.append((group_name, role_key, entry))
    return entries


def _used_supply_ids(config: dict) -> set:
    return {
        entry.get("supply_id")
        for _, _, entry in _assignments(config)
        if entry.get("supply_id")
    }


def _stable_serial(value: str) -> str:
    """契约第 3.1 节：归一化到稳定值。``normalize_serial`` 自己已循环到稳定，这里只验证。"""
    stable = normalize_serial(value)
    if normalize_serial(stable) != stable:
        raise AssertionError(f"序列号归一化不稳定: {value!r} -> {stable!r}")
    return stable


def _fixture_serials():
    """(fixture 名, 序列号) —— 注册表、配置电源条目、运行快照与迁移摘要里的全部序列号。"""
    for name in REGISTRY_FIXTURES:
        for record in _instruments(_load(name)):
            yield name, record.get("serial")
    for name in DYNAMIC_CONFIGS + (MIGRATED_CONFIG,):
        config = _load(name)
        for entry in _supplies(config).values():
            yield name, entry.get("serial")
        for entry in (config.get("run_mapping") or {}).get("instruments", {}).get(
            "power_supplies", {}
        ).values():
            yield name, entry.get("serial")
    for entry in _load(SUMMARY_FIXTURE)["entries"]:
        yield SUMMARY_FIXTURE, entry.get("serial")
    for name in (
        "run_mapping_legacy_single.json",
        "run_mapping_snapshot_two_supplies.json",
    ):
        mapping = _load(name)
        for entry in (mapping.get("instruments") or {}).get("power_supplies", {}).values():
            yield name, entry.get("serial")


class FixtureInventoryTests(unittest.TestCase):
    """fixture 目录本身就是契约的一部分：少一个文件或多一个未登记的 JSON 都要被发现。"""

    def test_every_fixture_parses_as_json(self):
        files = sorted(FIXTURE_DIR.glob("*.json"))
        self.assertGreaterEqual(len(files), 10, "fixture 数量少于契约登记的数量")
        for path in files:
            with self.subTest(fixture=path.name):
                document = json.loads(path.read_text(encoding="utf-8"))
                self.assertIsInstance(document, dict)

    def test_documented_fixtures_all_exist(self):
        for name in REGISTRY_FIXTURES + DYNAMIC_CONFIGS:
            with self.subTest(fixture=name):
                self.assertTrue((FIXTURE_DIR / name).is_file(), f"契约登记的 fixture 不存在: {name}")

    def test_contract_document_names_every_fixture(self):
        text = CONTRACT_DOC.read_text(encoding="utf-8")
        for path in sorted(FIXTURE_DIR.glob("*.json")):
            with self.subTest(fixture=path.name):
                self.assertIn(path.name, text, "契约文档没有登记这个 fixture，改名时也没有同步")


class RegistryContractTests(unittest.TestCase):
    """注册表的身份字段与迁移结果。"""

    def test_labels_follow_the_documented_rule(self):
        for name in MIGRATED_REGISTRIES:
            for record in _instruments(_load(name)):
                label = record.get("label")
                serial = record.get("serial")
                with self.subTest(fixture=name, serial=serial):
                    if label is None:
                        self.assertIsNone(
                            record.get("role"),
                            "只有未登记角色的设备才允许没有标签",
                        )
                        continue
                    self.assertIn(
                        serial,
                        EXPECTED_LABELS,
                        "出现未收录期望标签的序列号：先更新契约第 3 节和本表",
                    )
                    self.assertEqual(label, EXPECTED_LABELS[serial])

    def test_labels_are_unique_inside_a_registry(self):
        for name in REGISTRY_FIXTURES:
            labels = [
                record["label"]
                for record in _instruments(_load(name))
                if record.get("label")
            ]
            with self.subTest(fixture=name):
                self.assertEqual(len(labels), len(set(labels)), f"标签冲突: {labels}")

    def test_instrument_identity_is_vendor_plus_serial(self):
        for name in REGISTRY_FIXTURES:
            for record in _instruments(_load(name)):
                with self.subTest(fixture=name, serial=record.get("serial")):
                    key = make_device_key(record.get("vendor"), record.get("serial"))
                    self.assertEqual(key, (record["vendor"] + ":" + record["serial"]).casefold())

    def test_serials_are_stored_in_normalized_form(self):
        checked = 0
        for name, serial in _fixture_serials():
            if serial is None:
                continue
            checked += 1
            with self.subTest(fixture=name, serial=serial):
                self.assertEqual(
                    serial, _stable_serial(serial), "fixture 里的序列号必须是归一化后的稳定形态"
                )
                self.assertEqual(serial, serial.strip(), "序列号不能带首尾空白")
                self.assertEqual(serial, serial.upper(), "序列号必须大写")
                self.assertNotIn(" ", serial, "序列号含内部空白时标签生成会被拒绝，样例不能这样写")
        self.assertGreater(checked, 0, "没有任何序列号被检查")

    def test_migration_only_changes_role_and_label(self):
        before = {record["serial"]: record for record in _instruments(_load("registry_legacy_v2.json"))}
        after = {
            record["serial"]: record
            for record in _instruments(_load("registry_migrated_v3.json"))
        }
        self.assertEqual(sorted(before), sorted(after), "迁移不能增删设备记录")
        for serial, old in before.items():
            new = after[serial]
            with self.subTest(serial=serial):
                for field in ("name", "vendor", "model", "model_index", "current_ip", "known_ips"):
                    self.assertEqual(new.get(field), old.get(field), f"迁移改动了身份/地址字段: {field}")
                if old.get("role") in POWER_SUPPLY_SLOTS:
                    self.assertEqual(new.get("role"), "power_supply")
                    self.assertEqual(new.get("label"), EXPECTED_LABELS[serial])
                else:
                    self.assertIsNone(old.get("role"))
                    self.assertIsNone(new.get("role"), "未登记角色不能被猜成电源")
                    self.assertIsNone(new.get("label"))

    def test_registering_a_device_explicitly_adds_its_label(self):
        records = {
            record["serial"]: record
            for record in _instruments(_load("registry_migrated_v3_all_registered.json"))
        }
        for serial in ("DP8B214901519", "DP8B174700950", "DP8B174400885"):
            with self.subTest(serial=serial):
                self.assertEqual(records[serial]["role"], "power_supply")
                self.assertEqual(records[serial]["label"], EXPECTED_LABELS[serial])


class ConfigContractTests(unittest.TestCase):
    """配置里的电源集合、分配引用与运行映射。"""

    def test_power_supply_keys_are_labels_and_carry_identity(self):
        for name in DYNAMIC_CONFIGS:
            supplies = _supplies(_load(name))
            with self.subTest(fixture=name):
                self.assertTrue(supplies, "目标格式的配置必须至少有一台电源")
            for supply_id, entry in supplies.items():
                with self.subTest(fixture=name, supply_id=supply_id):
                    self.assertEqual(entry.get("label"), supply_id, "集合键必须等于条目内的 label")
                    self.assertNotIn("supply_id", entry, "条目内部不再重复写 supply_id")
                    self.assertEqual(
                        entry.get("device_key"),
                        make_device_key("RIGOL TECHNOLOGIES", entry.get("serial")),
                        "device_key 必须由厂商与序列号生成",
                    )
                    self.assertIn(
                        entry.get("serial"),
                        EXPECTED_LABELS,
                        "出现未收录期望标签的序列号：先更新契约第 3 节",
                    )
                    self.assertEqual(supply_id, EXPECTED_LABELS[entry["serial"]])
                    self.assertEqual(entry.get("model"), "DP832A")
                    self.assertIn("channels", entry)

    def test_assignments_only_reference_existing_power_supplies(self):
        for name in DYNAMIC_CONFIGS:
            config = _load(name)
            supplies = _supplies(config)
            for group_name, role_key, entry in _assignments(config):
                with self.subTest(fixture=name, group=group_name, role=role_key):
                    self.assertIn("supply_id", entry, "分配引用必须写 supply_id")
                    supply_id = entry.get("supply_id")
                    if supply_id is not None:
                        self.assertIn(supply_id, supplies, "分配引用了不存在的电源")
                        if "name" in entry:
                            self.assertEqual(entry["name"], supply_id, "过渡期的 name 必须与 supply_id 相同")
                    else:
                        self.assertNotIn("name", entry, "未绑定的引用不保留槽位名")

    def test_one_two_and_five_power_supplies_are_expressible(self):
        for name, expected in EXPECTED_SCALES.items():
            with self.subTest(fixture=name):
                self.assertEqual(len(_supplies(_load(name))), expected)

    def test_partial_migration_leaves_no_slot_keys_and_no_fake_reference(self):
        config = _load("config_slots_migrated_partial.json")
        supplies = _supplies(config)
        self.assertEqual(sorted(supplies), ["DP8B0885", "DP8B1519"])
        for slot in POWER_SUPPLY_SLOTS:
            self.assertNotIn(slot, supplies, "迁移结果不能保留旧槽位键")
        carrier = config["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"]
        self.assertIsNone(carrier["supply_id"], "只有地址的旧电源必须留成未绑定引用")
        self.assertEqual(carrier["channel"], ["CH1", "CH2"], "通道列表要保留，参数由迁移摘要给出")
        self.assertEqual(config["run_mapping"]["instruments"]["power_supplies"], {})
        self.assertEqual(config["run_mapping"]["dut_power_channels"], [])
        self.assertEqual(
            config["device_bindings"]["power_supplies"], {}, "无法识别的旧绑定不能变成电源绑定"
        )

    def test_run_mapping_freezes_only_the_supplies_in_use(self):
        for name in DYNAMIC_CONFIGS:
            config = _load(name)
            mapping = config["run_mapping"]
            frozen = mapping["instruments"]["power_supplies"]
            with self.subTest(fixture=name):
                self.assertEqual(
                    sorted(frozen), sorted(_used_supply_ids(config)), "快照只登记实际使用的电源"
                )
            for supply_id, entry in frozen.items():
                with self.subTest(fixture=name, supply_id=supply_id):
                    for field in ("label", "model", "serial", "device_key", "visa_address"):
                        self.assertTrue(entry.get(field), f"快照条目缺少 {field}")
                    self.assertEqual(entry["label"], supply_id)
                    self.assertEqual(
                        entry["device_key"],
                        _supplies(config)[supply_id]["device_key"],
                        "快照身份必须与配置里的电源条目一致",
                    )
            for channel in mapping["dut_power_channels"]:
                with self.subTest(fixture=name, channel=channel.get("channel")):
                    token = channel.get("channel") or ""
                    self.assertIn("/", token, "通道必须写成 supply_id/CHx")
                    self.assertIn(token.split("/", 1)[0], frozen, "通道路由必须指到快照里的电源")

    def test_legacy_config_is_kept_verbatim_as_slot_input(self):
        config = _load("config_slots_legacy.json")
        self.assertEqual(sorted(_supplies(config)), sorted(POWER_SUPPLY_SLOTS))
        instruments = config["run_mapping"]["instruments"]
        self.assertIn("power_supply", instruments, "旧运行映射是单数 power_supply")
        self.assertNotIn("power_supplies", instruments)
        self.assertIn("PS1", config["device_bindings"], "旧绑定以槽位名为键")
        for slot, entry in _supplies(config).items():
            self.assertNotIn("label", entry, "旧配置条目没有标签")
            self.assertNotIn("device_key", entry, "旧配置条目没有身份")


class MigrationSummaryContractTests(unittest.TestCase):
    """迁移摘要（契约第 9 节）：判定齐全，无法迁移的参数只留在摘要里。"""

    def test_summary_covers_every_legacy_slot_exactly_once(self):
        keys = [entry["old_key"] for entry in _load(SUMMARY_FIXTURE)["entries"]]
        legacy_keys = sorted(_supplies(_load(LEGACY_CONFIG)))
        self.assertEqual(sorted(keys), legacy_keys, "摘要必须与旧配置的槽位一一对应")
        self.assertEqual(len(keys), len(set(keys)), "同一槽位只能有一条摘要")

    def test_every_entry_states_status_and_reason(self):
        for entry in _load(SUMMARY_FIXTURE)["entries"]:
            with self.subTest(old_key=entry.get("old_key")):
                self.assertIn(entry.get("status"), SUMMARY_STATUSES)
                reason = entry.get("reason")
                self.assertIsInstance(reason, str)
                self.assertTrue(reason.strip(), "迁移摘要必须给出面向操作员的原因")

    def test_legacy_channel_parameters_survive_the_migration(self):
        legacy = _supplies(_load(LEGACY_CONFIG))
        for entry in _load(SUMMARY_FIXTURE)["entries"]:
            old = legacy[entry["old_key"]]
            with self.subTest(old_key=entry["old_key"]):
                self.assertEqual(
                    entry["channels"], old["channels"], "旧通道参数必须逐字段保留在摘要里"
                )
                self.assertEqual(entry["legacy_enabled"], old.get("enabled", False))
                self.assertEqual(entry["legacy_address"], old.get("address"))

    def test_migrated_entries_match_the_migrated_config(self):
        migrated = _supplies(_load(MIGRATED_CONFIG))
        entries = [
            entry
            for entry in _load(SUMMARY_FIXTURE)["entries"]
            if entry["status"] == "migrated"
        ]
        self.assertTrue(entries, "现场数据里至少有两台能自动迁移的电源")
        for entry in entries:
            supply = migrated[entry["new_key"]]
            with self.subTest(old_key=entry["old_key"]):
                self.assertEqual(entry["label"], supply["label"])
                self.assertEqual(entry["serial"], supply["serial"])
                self.assertEqual(entry["device_key"], supply["device_key"])
                self.assertEqual(entry["channels"], supply["channels"], "能自动迁移的电源也不能丢参数")

    def test_unmigrated_entries_never_claim_a_device(self):
        migrated = _supplies(_load(MIGRATED_CONFIG))
        addresses = {
            entry.get("address")
            for entry in migrated.values()
            if entry.get("address")
        }
        for entry in _load(SUMMARY_FIXTURE)["entries"]:
            if entry["status"] == "migrated":
                continue
            with self.subTest(old_key=entry["old_key"]):
                for field in ("new_key", "device_key", "serial", "label"):
                    self.assertIsNone(entry[field], "无法识别的旧条目不能凭空得到身份")
                self.assertNotIn(entry["old_key"], migrated, "无法识别的旧条目不能变成电源条目")
                if entry["legacy_address"]:
                    self.assertNotIn(
                        entry["legacy_address"],
                        addresses,
                        "只有地址的旧条目不能被当成已绑定的电源",
                    )

    def test_address_hint_follows_the_registry_known_addresses(self):
        known = {
            endpoint.get("ip"): make_device_key(record["vendor"], record["serial"])
            for record in _instruments(_load("registry_legacy_v2.json"))
            for endpoint in (record.get("known_ips") or [])
        }
        for entry in _load(SUMMARY_FIXTURE)["entries"]:
            address = entry.get("legacy_address")
            if not address:
                with self.subTest(old_key=entry["old_key"]):
                    self.assertIsNone(entry.get("address_hint"), "没有地址就没有地址提示")
                continue
            ip = address.split("::")[1]
            with self.subTest(old_key=entry["old_key"]):
                self.assertEqual(
                    entry.get("address_hint"),
                    known.get(ip),
                    "地址提示必须与注册表的已知地址一致，且始终只是提示",
                )
                self.assertIsNone(entry["new_key"], "地址命中也不能据此自动迁移")

    def test_every_unbound_reference_is_reported_in_the_summary(self):
        config = _load(MIGRATED_CONFIG)
        reported = {
            (assignment["group"], assignment["role_key"])
            for entry in _load(SUMMARY_FIXTURE)["entries"]
            for assignment in entry["assignments"]
        }
        actual = {
            (group_name, role_key)
            for group_name, group in (config["power_supply_assignment"] or {}).items()
            for role_key, reference in (group or {}).get("supplies", {}).items()
            if reference.get("supply_id") is None
        }
        self.assertEqual(actual, reported, "未绑定的引用必须在摘要里交代清楚，不能静默消失")


class RunSnapshotContractTests(unittest.TestCase):
    """运行目录里的快照：旧结构只读，新结构自带身份与显式路由。"""

    def test_legacy_snapshot_uses_slot_names_and_bare_channels(self):
        mapping = _load("run_mapping_legacy_single.json")
        self.assertIn("power_supply", mapping["instruments"])
        self.assertNotIn("power_supplies", mapping["instruments"])
        for channel in mapping["dut_power_channels"]:
            with self.subTest(channel=channel.get("channel")):
                self.assertNotIn("/", channel["channel"], "旧快照的通道是裸通道名")
                self.assertIn("PS1", channel["connection"])

    def test_new_snapshot_carries_identity_for_every_supply(self):
        mapping = _load("run_mapping_snapshot_two_supplies.json")
        frozen = mapping["instruments"]["power_supplies"]
        self.assertEqual(sorted(frozen), ["DP8B0885", "DP8B1519"])
        for supply_id, entry in frozen.items():
            with self.subTest(supply_id=supply_id):
                for field in ("label", "model", "serial", "device_key", "visa_address"):
                    self.assertTrue(entry.get(field), f"快照条目缺少 {field}")
                self.assertEqual(entry["device_key"], make_device_key("RIGOL TECHNOLOGIES", entry["serial"]))
        roles = {}
        for channel in mapping["dut_power_channels"]:
            roles[channel["role"]] = channel["channel"].split("/", 1)[0]
        self.assertEqual(
            roles,
            {"gate": "DP8B1519", "drain": "DP8B0885"},
            "gate 与 drain 必须能来自不同电源",
        )
        self.assertTrue(mapping["run_id"], "正式快照要有 run_id 才能被历史页找到")


if __name__ == "__main__":
    unittest.main()
