"""门禁：设备注册表迁移（电源重构计划步骤 4）。

注册表的读写分工在这里固定下来：

1. **读取兼容旧 schema**：``schema_version`` 2、``role: "PS1".."PS4"``、没有 ``label`` 的旧文件
   仍可读取，读出来还是旧字面量——设备发现面板与配置页还按旧槽位工作（步骤 5、6 才改），
   读取侧折叠会让它们找不到电源；
2. **保存统一写新字段**：写盘前把旧槽位折叠成 ``power_supply``，为登记为 ``power_supply``
   却缺标签的设备按序列号补生成 ``label``，``schema_version`` 升到 3；现场注册表迁移后的期望
   结果就是步骤 1 的 ``registry_migrated_v3.json``；
3. **迁移只改 ``role`` 与 ``label``**：资产名、序列号、地址、人工补充的字段逐字节保留；
   已有标签原样冻结（不裁剪空白、不改大小写），只有 ``null`` / 空串 / 纯空白才算“还没有标签”；
4. **未登记角色不猜成电源**，也就不生成标签；要使用它必须显式登记，那时才得到标签
   （``registry_migrated_v3_all_registered.json``）；
5. **冲突阻止保存**：标签重复、标签不能当 ``supply_id``（含空白、``/`` 或非字符串）、序列号
   派不出可用标签、文件版本高于本程序，都明确报错并指出设备，绝不覆盖旧标签、也不留下半截
   文件；
6. **保存是一次事务**：迁移、校验、写盘任何一步失败，实例状态与原文件都不变。
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import infrastructure.persistence.instrument_registry as registry_module
from domain.instruments.models import POWER_SUPPLY_ROLE, make_device_key, normalize_serial
from infrastructure.persistence.instrument_registry import (
    LEGACY_SCHEMA_VERSIONS,
    SCHEMA_VERSION,
    InstrumentRegistry,
    InstrumentRegistryError,
)

ROOT = Path(__file__).parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "power_supply_identity"
LEGACY_REGISTRY = FIXTURE_DIR / "registry_legacy_v2.json"
MIGRATED_REGISTRY = FIXTURE_DIR / "registry_migrated_v3.json"
ALL_REGISTERED_REGISTRY = FIXTURE_DIR / "registry_migrated_v3_all_registered.json"

# 契约第 3 节：现场三台 DP832A 的期望短标签。
EXPECTED_LABELS = {
    "DP8B214901519": "DP8B1519",
    "DP8B174700950": "DP8B0950",
    "DP8B174400885": "DP8B0885",
}
# 每次保存都会刷新，不参与“迁移结果与样例一致”的比对。
VOLATILE_FIELDS = ("updated_at",)


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _stable(document):
    return {key: value for key, value in document.items() if key not in VOLATILE_FIELDS}


def _by_serial(document):
    return {record["serial"]: record for record in document["instruments"]}


def _entry(name, serial, *, model, model_index, role=None, label=None):
    entry = {
        "name": name,
        "vendor": "RIGOL TECHNOLOGIES",
        "model": model,
        "model_index": model_index,
        "serial": serial,
        "order_no": None,
        "firmware": None,
        "options": [],
        "status": "online",
        "current_ip": "",
        "notes": None,
        "role": role,
        "first_seen_at": None,
        "last_seen_at": None,
        "known_ips": [],
    }
    if label is not None:
        entry["label"] = label
    return entry


def _document(instruments, schema_version=SCHEMA_VERSION):
    return {"schema_version": schema_version, "updated_at": "2026-10-29T12:00:00+08:00",
            "instruments": instruments}


class RegistryFileTestCase(unittest.TestCase):
    """每个用例在临时目录里操作一份设备表副本，绝不改写仓库里的现场文件。"""

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.path = Path(self.temporary_directory.name) / "instruments.json"

    def copy_fixture(self, source):
        shutil.copyfile(source, self.path)
        return self.path

    def write(self, document):
        self.path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.path


class LegacyReadTests(RegistryFileTestCase):
    """步骤 4 不改读取：旧槽位字面量照旧返回，面板还能按它们工作。"""

    def test_legacy_file_still_reads_slot_roles_without_labels(self):
        self.copy_fixture(LEGACY_REGISTRY)

        registry = InstrumentRegistry.load(self.path)

        self.assertEqual(registry.schema_version, 2)
        self.assertIn(2, LEGACY_SCHEMA_VERSIONS)
        self.assertEqual(
            [item.role for item in registry.list()],
            ["PS3", None, "PS4"],
        )
        self.assertEqual([item.label for item in registry.list()], [None, None, None])
        # 没有标签时现场显示退回软件资产名，不会因为缺少新字段而报错。
        self.assertEqual(
            [item.display_name for item in registry.list()],
            ["DP832A-1", "DP832A-2", "DP832A-3"],
        )

    def test_legacy_file_is_not_rewritten_by_reading_it(self):
        self.copy_fixture(LEGACY_REGISTRY)
        before = self.path.read_bytes()

        InstrumentRegistry.load(self.path)

        self.assertEqual(self.path.read_bytes(), before, "只读加载不得改写旧文件")


class MigrationTests(RegistryFileTestCase):
    """保存路径：折叠旧角色、补生成标签、升级 schema，结果是步骤 1 的样例。"""

    def test_migration_matches_the_documented_result(self):
        self.copy_fixture(LEGACY_REGISTRY)

        registry = InstrumentRegistry.load(self.path)
        registry.save()

        saved = _load(self.path)
        expected = _load(MIGRATED_REGISTRY)
        self.assertEqual(saved["schema_version"], SCHEMA_VERSION)
        self.assertEqual(_stable(saved), _stable(expected), "迁移结果必须与契约样例逐字段一致")

    def test_registered_power_supplies_get_labels_and_unregistered_stays_alone(self):
        """迁移后已登记的两台电源各有短标签；未登记角色的那台不猜成电源、也不发标签。"""
        self.copy_fixture(LEGACY_REGISTRY)

        InstrumentRegistry.load(self.path).save()

        records = _by_serial(_load(self.path))
        self.assertEqual(
            {serial: record["label"] for serial, record in records.items()},
            {
                "DP8B214901519": "DP8B1519",
                "DP8B174700950": None,
                "DP8B174400885": "DP8B0885",
            },
            "未登记角色的设备由操作员显式登记后才得到标签（第三条标签见登记用例）",
        )
        self.assertEqual(
            {serial: record["role"] for serial, record in records.items()},
            {
                "DP8B214901519": POWER_SUPPLY_ROLE,
                "DP8B174700950": None,
                "DP8B174400885": POWER_SUPPLY_ROLE,
            },
            "未登记角色的设备不能被猜成电源",
        )

    def test_migration_only_changes_role_and_label(self):
        self.copy_fixture(LEGACY_REGISTRY)
        before = _by_serial(_load(self.path))

        InstrumentRegistry.load(self.path).save()

        after = _by_serial(_load(self.path))
        self.assertEqual(sorted(before), sorted(after), "迁移不能增删设备记录")
        for serial, old in before.items():
            with self.subTest(serial=serial):
                new = after[serial]
                for field in (
                    "name",
                    "vendor",
                    "model",
                    "model_index",
                    "current_ip",
                    "known_ips",
                    "first_seen_at",
                    "last_seen_at",
                ):
                    self.assertEqual(new.get(field), old.get(field), f"迁移改动了 {field}")
                self.assertEqual(
                    make_device_key(new["vendor"], new["serial"]),
                    make_device_key(old["vendor"], old["serial"]),
                    "设备键只由厂商与序列号派生，迁移不能改变身份",
                )

    def test_manual_fields_survive_the_migration(self):
        document = _load(LEGACY_REGISTRY)
        document["instruments"][0]["calibration_due"] = "2027-03-01"
        self.write(document)

        registry = InstrumentRegistry.load(self.path)
        registry.save()

        migrated = _load(self.path)
        self.assertEqual(migrated["instruments"][0]["calibration_due"], "2027-03-01")
        self.assertEqual(migrated["instruments"][0]["label"], "DP8B1519")

    def test_reload_keeps_label_serial_and_device_key_together(self):
        self.copy_fixture(LEGACY_REGISTRY)
        InstrumentRegistry.load(self.path).save()

        registry = InstrumentRegistry.load(self.path)
        power_supplies = [item for item in registry.list() if item.role == POWER_SUPPLY_ROLE]

        self.assertEqual(registry.schema_version, SCHEMA_VERSION)
        self.assertEqual(
            [(item.label, item.serial, item.device_key) for item in power_supplies],
            [
                ("DP8B1519", "DP8B214901519", make_device_key("RIGOL TECHNOLOGIES", "DP8B214901519")),
                ("DP8B0885", "DP8B174400885", make_device_key("RIGOL TECHNOLOGIES", "DP8B174400885")),
            ],
        )
        self.assertEqual(
            [item.display_name for item in power_supplies], ["DP8B1519", "DP8B0885"]
        )
        for item in power_supplies:
            self.assertEqual(item.serial, normalize_serial(item.serial))

    def test_saving_twice_does_not_move_a_frozen_label(self):
        self.copy_fixture(LEGACY_REGISTRY)
        InstrumentRegistry.load(self.path).save()
        first = {serial: record["label"] for serial, record in _by_serial(_load(self.path)).items()}

        InstrumentRegistry.load(self.path).save()

        self.assertEqual(
            {serial: record["label"] for serial, record in _by_serial(_load(self.path)).items()},
            first,
            "标签生成一次后冻结，重复保存不得重算",
        )

    def test_hand_written_label_is_kept_as_is(self):
        self.write(_document([
            _entry("DP832A-1", "DP8B214901519", model="DP832A", model_index=1,
                   role=POWER_SUPPLY_ROLE, label="RACK-B-1"),
        ]))

        InstrumentRegistry.load(self.path).save()

        self.assertEqual(_load(self.path)["instruments"][0]["label"], "RACK-B-1")

    def test_existing_label_text_is_frozen_verbatim(self):
        """迁移只填空缺：大小写与字面值都必须原样保留，不做规范化。"""
        self.write(_document([
            _entry("DP832A-1", "DP8B214901519", model="DP832A", model_index=1,
                   role=POWER_SUPPLY_ROLE, label="rack-b-1"),
        ]))

        InstrumentRegistry.load(self.path).save()

        self.assertEqual(
            _load(self.path)["instruments"][0]["label"], "rack-b-1",
            "已有标签不得被裁剪空白、改写大小写或重新生成",
        )

    def test_blank_label_counts_as_missing(self):
        """空串与纯空白不算标签：电源按规则补生成，其余设备写 ``null``。"""
        document = _document([
            _entry("DP832A-1", "DP8B214901519", model="DP832A", model_index=1,
                   role=POWER_SUPPLY_ROLE, label="   "),
            _entry("SMW200A-1", "105462", model="SMW200A", model_index=1,
                   role="signal_generator", label=""),
        ])
        document["instruments"][1]["vendor"] = "Rohde&Schwarz"
        self.write(document)

        InstrumentRegistry.load(self.path).save()

        records = _load(self.path)["instruments"]
        self.assertEqual(records[0]["label"], "DP8B1519", "空白标签视为缺标签，电源照常补生成")
        self.assertIsNone(records[1]["label"], "非电源设备没有标签时写 null")


class RegistrationTests(RegistryFileTestCase):
    """显式登记为电源才得到标签：未登记角色不会被猜成电源。"""

    def test_registering_the_unassigned_device_adds_its_label(self):
        self.copy_fixture(LEGACY_REGISTRY)
        registry = InstrumentRegistry.load(self.path)
        registry.set_role("DP832A-2", POWER_SUPPLY_ROLE)
        registry.save()

        saved = _load(self.path)
        records = _by_serial(saved)
        self.assertEqual(
            {serial: record["label"] for serial, record in records.items()}, EXPECTED_LABELS
        )
        self.assertEqual(
            {record["role"] for record in records.values()}, {POWER_SUPPLY_ROLE}
        )

        # 与步骤 1 的“全部登记”样例对照：除人工补充的 notes 外必须一致。
        expected = _by_serial(_load(ALL_REGISTERED_REGISTRY))
        for serial, record in records.items():
            with self.subTest(serial=serial):
                for field in ("name", "vendor", "model", "model_index", "serial", "label",
                              "role", "current_ip", "known_ips"):
                    self.assertEqual(record.get(field), expected[serial].get(field))

    def test_legacy_slot_role_input_is_written_back_canonical(self):
        self.copy_fixture(LEGACY_REGISTRY)
        registry = InstrumentRegistry.load(self.path)
        registry.set_role("DP832A-2", "PS2")
        registry.save()

        record = _by_serial(_load(self.path))["DP8B174700950"]
        self.assertEqual(record["role"], POWER_SUPPLY_ROLE, "旧槽位写回时折叠成 power_supply")
        self.assertEqual(record["label"], "DP8B0950")


class ConflictGateTests(RegistryFileTestCase):
    """冲突与不可派生的序列号必须阻止保存，并指出是哪台设备。"""

    def test_duplicate_labels_are_reported_with_both_devices(self):
        self.write(_document([
            _entry("FAKE-1", "B0001", model="FAKE", model_index=1,
                   role=POWER_SUPPLY_ROLE, label="DP8B1519"),
            _entry("DP832A-1", "DP8B214901519", model="DP832A", model_index=1,
                   role=POWER_SUPPLY_ROLE, label="DP8B1519"),
        ]))

        with self.assertRaises(InstrumentRegistryError) as raised:
            InstrumentRegistry.load(self.path)

        message = str(raised.exception)
        self.assertIn("标签冲突", message)
        self.assertIn("FAKE-1", message)
        self.assertIn("DP832A-1", message)

    def test_label_must_be_usable_as_a_supply_id(self):
        for label in ("DP8B/1519", "DP8B 1519"):
            with self.subTest(label=label):
                self.write(_document([
                    _entry("DP832A-1", "DP8B214901519", model="DP832A", model_index=1,
                           role=POWER_SUPPLY_ROLE, label=label),
                ]))
                with self.assertRaises(InstrumentRegistryError) as raised:
                    InstrumentRegistry.load(self.path)
                self.assertIn("supply_id", str(raised.exception))

    def test_surrounding_whitespace_is_reported_not_trimmed(self):
        """写错的标签必须报错：迁移不许悄悄裁剪成“看起来合法”的值。"""
        self.write(_document([
            _entry("DP832A-1", "DP8B214901519", model="DP832A", model_index=1,
                   role=POWER_SUPPLY_ROLE, label=" DP8B1519 "),
        ]))

        with self.assertRaises(InstrumentRegistryError) as raised:
            InstrumentRegistry.load(self.path)

        self.assertIn("首尾空白", str(raised.exception))

    def test_non_string_label_is_rejected(self):
        self.write(_document([
            _entry("DP832A-1", "DP8B214901519", model="DP832A", model_index=1,
                   role=POWER_SUPPLY_ROLE, label=1234),
        ]))

        with self.assertRaises(InstrumentRegistryError) as raised:
            InstrumentRegistry.load(self.path)

        self.assertIn("字符串", str(raised.exception))

    def test_exhausted_label_candidates_block_the_save_and_keep_the_file(self):
        self.write(_document([
            _entry("FAKE-1", "B0001", model="FAKE", model_index=1,
                   role="spectrum_analyzer", label="A5462"),
            _entry("DP832A-1", "A5462", model="DP832A", model_index=1,
                   role=POWER_SUPPLY_ROLE),
        ]))
        before = self.path.read_bytes()
        registry = InstrumentRegistry.load(self.path)
        before_document = registry.raw

        with self.assertRaises(InstrumentRegistryError) as raised:
            registry.save()

        message = str(raised.exception)
        self.assertIn("DP832A-1", message)
        self.assertIn("A5462", message)
        self.assertEqual(self.path.read_bytes(), before, "保存失败不得改写原文件")
        self.assertEqual(registry.raw, before_document, "保存失败不得提交半个迁移结果")
        self.assertIsNone(
            registry.get_by_name("DP832A-1").label, "保存失败不得在内存里留下半个迁移结果"
        )

    def test_newer_schema_version_is_rejected_instead_of_downgraded(self):
        self.write(_document([], schema_version=SCHEMA_VERSION + 1))

        with self.assertRaises(InstrumentRegistryError) as raised:
            InstrumentRegistry.load(self.path)

        self.assertIn("schema_version", str(raised.exception))


class SaveTransactionTests(RegistryFileTestCase):
    """保存是一次事务：迁移、校验、写盘任何一步失败都不改动实例状态与文件。

    顺序固定为“副本迁移 → 副本校验 → 补版本与时间戳 → 原子写入 → 提交回对象”，
    这里把“提交回对象”放在最后一步的不变量钉住。
    """

    def test_write_failure_keeps_the_document_and_file_unchanged(self):
        self.copy_fixture(LEGACY_REGISTRY)
        registry = InstrumentRegistry.load(self.path)
        before_document = registry.raw
        before_bytes = self.path.read_bytes()

        with mock.patch.object(
            registry_module, "write_json_atomic", side_effect=OSError("磁盘已满")
        ):
            with self.assertRaises(OSError):
                registry.save()

        self.assertEqual(registry.raw, before_document, "写盘失败不得提交迁移结果")
        self.assertEqual(self.path.read_bytes(), before_bytes, "写盘失败不得改写原文件")
        # 实例仍按迁移前的旧字面量工作，调用方可以改完再重试。
        self.assertEqual(registry.get_by_name("DP832A-1").role, "PS3")

    def test_validation_failure_keeps_the_document_and_file_unchanged(self):
        self.copy_fixture(LEGACY_REGISTRY)
        registry = InstrumentRegistry.load(self.path)
        before_document = registry.raw
        before_bytes = self.path.read_bytes()

        with mock.patch.object(
            registry_module,
            "_validate_document",
            side_effect=InstrumentRegistryError("模拟校验失败"),
        ):
            with self.assertRaises(InstrumentRegistryError):
                registry.save()

        self.assertEqual(registry.raw, before_document, "校验失败不得提交迁移结果")
        self.assertEqual(self.path.read_bytes(), before_bytes, "校验失败不得改写原文件")


if __name__ == "__main__":
    unittest.main()
