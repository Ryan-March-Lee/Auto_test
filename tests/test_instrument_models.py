"""仪器领域模型的角色类别与缩写设备码字段。

四条不变量：

1. ``role`` 只表示**设备类别**：多台电源共用 ``power_supply``，靠各自缩写设备码区分，
   而不是靠“第几台”；
2. 旧槽位 ``PS1``..``PS4`` 只在读取时被识别（``normalize_role`` 原样返回），写回时由
   ``canonical_role`` 折叠成 ``power_supply``，不再出现在可写回的角色集合里；
3. ``Instrument.label`` 是可空的稳定缩写设备码（契约名 ``short_code``）：序列化带着它，
   现场显示优先用它；身份只由 ``厂商 + 完整设备码`` 派生，型号可重复；
4. 已退役的 ``name`` / ``model_index`` 不再存在于模型或新写出的设备表里；旧设备表的
   ``serial`` 只在读取边界被投影为 ``full_code``。
"""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from domain.instruments.models import (
    DEVICE_ROLES,
    INSTRUMENT_ROLES,
    LEGACY_ROLES,
    POWER_SUPPLY_ROLE,
    POWER_SUPPLY_SLOTS,
    TEST_ROLES,
    DeviceStatus,
    Instrument,
    InstrumentEndpoint,
    InstrumentIdentity,
    canonical_role,
    is_legacy_power_supply_slot,
    make_device_key,
    normalize_role,
)
from infrastructure.persistence.instrument_registry import SCHEMA_VERSION, InstrumentRegistry

RIGOL_VENDOR = "RIGOL TECHNOLOGIES"
FIRST_CODE = "DP8B214901519"
SECOND_CODE = "DP8B174700950"
THIRD_CODE = "DP8B174400885"


def _instrument(**overrides):
    fields = {
        "id": 0,
        "vendor": RIGOL_VENDOR,
        "model": "DP832A",
        "full_code": FIRST_CODE,
        "device_key": make_device_key(RIGOL_VENDOR, FIRST_CODE),
    }
    fields.update(overrides)
    return Instrument(**fields)


def _document(instruments):
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": "2026-10-29T15:00:00+08:00",
        "instruments": instruments,
    }


def _entry(**overrides):
    entry = {
        "vendor": RIGOL_VENDOR,
        "model": "DP832A",
        "full_code": FIRST_CODE,
        "status": "online",
        "current_ip": "",
        "role": None,
        "known_ips": [],
    }
    entry.update(overrides)
    return entry


class RoleTaxonomyTests(unittest.TestCase):
    """``role`` 是类别而不是编号。"""

    def test_power_supply_is_a_role_value_not_a_slot(self):
        self.assertEqual(POWER_SUPPLY_ROLE, "power_supply")
        self.assertIn(POWER_SUPPLY_ROLE, DEVICE_ROLES)
        self.assertNotIn(POWER_SUPPLY_ROLE, POWER_SUPPLY_SLOTS)
        self.assertEqual(INSTRUMENT_ROLES, ("signal_generator", "spectrum_analyzer"))

    def test_legacy_slots_are_only_recognised_on_read(self):
        self.assertEqual(LEGACY_ROLES, POWER_SUPPLY_SLOTS)
        self.assertEqual(TEST_ROLES, DEVICE_ROLES + LEGACY_ROLES)
        for slot in POWER_SUPPLY_SLOTS:
            with self.subTest(slot=slot):
                self.assertNotIn(slot, DEVICE_ROLES)
                self.assertIn(slot, TEST_ROLES)

    def test_normalize_role_accepts_new_roles_and_legacy_slots(self):
        for role in DEVICE_ROLES:
            with self.subTest(role=role):
                self.assertEqual(normalize_role(role), role)
        for slot in POWER_SUPPLY_SLOTS:
            with self.subTest(slot=slot):
                self.assertEqual(normalize_role(slot), slot)
        self.assertEqual(normalize_role("  power_supply  "), POWER_SUPPLY_ROLE)

    def test_normalize_role_rejects_unknown_values(self):
        for value in (None, "", "   ", "amplifier", "PS5", 7):
            with self.subTest(value=value):
                self.assertIsNone(normalize_role(value))

    def test_canonical_role_folds_legacy_slots_into_power_supply(self):
        for slot in POWER_SUPPLY_SLOTS:
            with self.subTest(slot=slot):
                self.assertEqual(canonical_role(slot), POWER_SUPPLY_ROLE)
        self.assertEqual(canonical_role("signal_generator"), "signal_generator")
        self.assertEqual(canonical_role("spectrum_analyzer"), "spectrum_analyzer")
        self.assertEqual(canonical_role("power_supply"), POWER_SUPPLY_ROLE)

    def test_canonical_role_never_guesses_a_power_supply(self):
        for value in (None, "", "amplifier", "PS5"):
            with self.subTest(value=value):
                self.assertIsNone(canonical_role(value))

    def test_legacy_slot_predicate_only_recognises_old_slot_names(self):
        for slot in POWER_SUPPLY_SLOTS:
            with self.subTest(slot=slot):
                self.assertTrue(is_legacy_power_supply_slot(slot))
                self.assertTrue(is_legacy_power_supply_slot(f"  {slot}  "))
        for value in (None, 7, "", "   ", "PS", "PS5", "DP8B1519", POWER_SUPPLY_ROLE):
            with self.subTest(value=value):
                self.assertFalse(
                    is_legacy_power_supply_slot(value),
                    "缩写设备码、类别与未登记编号都不是旧槽位名，不能拿来当电源键",
                )

    def test_multiple_power_supplies_share_one_role_and_differ_by_label(self):
        first = replace(_instrument(), role=POWER_SUPPLY_ROLE, label="DP8B1519")
        second = replace(
            _instrument(
                id=1,
                full_code=SECOND_CODE,
                device_key=make_device_key(RIGOL_VENDOR, SECOND_CODE),
            ),
            role=POWER_SUPPLY_ROLE,
            label="DP8B0950",
        )

        self.assertEqual({item.role for item in (first, second)}, {POWER_SUPPLY_ROLE})
        self.assertEqual(first.model, second.model)
        self.assertEqual(len({item.device_key for item in (first, second)}), 2)
        self.assertEqual(
            sorted(item.display_name for item in (first, second)), ["DP8B0950", "DP8B1519"]
        )


class InstrumentLabelTests(unittest.TestCase):
    """``label`` 是可空稳定字段，显示优先用它，身份不受它影响。"""

    def test_label_defaults_to_none_and_display_falls_back_to_model(self):
        instrument = _instrument()
        self.assertIsNone(instrument.label)
        self.assertEqual(instrument.display_name, "DP832A")

    def test_display_name_prefers_the_label(self):
        instrument = replace(_instrument(), label="DP8B1519")
        self.assertEqual(instrument.display_name, "DP8B1519")

    def test_to_dict_includes_the_label_and_not_retired_fields(self):
        payload = _instrument().to_dict()
        self.assertIsNone(payload["label"])
        self.assertNotIn("name", payload)
        self.assertNotIn("model_index", payload)
        self.assertNotIn("serial", payload)
        self.assertEqual(
            replace(_instrument(), label="DP8B1519").to_dict()["label"], "DP8B1519"
        )

    def test_label_does_not_change_identity(self):
        labelled = replace(_instrument(), label="DP8B1519", role=POWER_SUPPLY_ROLE)
        self.assertEqual(labelled.device_key, make_device_key(RIGOL_VENDOR, FIRST_CODE))
        self.assertEqual(labelled.to_dict()["device_key"], labelled.device_key)

    def test_label_is_appended_so_positional_arguments_keep_their_meaning(self):
        """``label`` 必须追加在既有字段之后：位置参数顺序一旦改变会静默改写别的字段。"""
        endpoints = (InstrumentEndpoint(ip="192.168.1.102"),)
        instrument = Instrument(
            1,
            RIGOL_VENDOR,
            "DP832A",
            FIRST_CODE,
            make_device_key(RIGOL_VENDOR, FIRST_CODE),
            None,
            None,
            (),
            DeviceStatus.ONLINE,
            None,
            None,
            None,
            POWER_SUPPLY_ROLE,
            endpoints,
        )

        self.assertEqual(instrument.role, POWER_SUPPLY_ROLE)
        self.assertEqual(instrument.endpoints, endpoints)
        self.assertEqual(instrument.current_ip, "192.168.1.102")
        self.assertIsNone(instrument.label)
        self.assertEqual(instrument.display_name, "DP832A")


class LegacyRegistryReadTests(unittest.TestCase):
    """旧注册表记录仍可读取；读入后立即投影为 ``full_code`` 并丢弃资产名。"""

    def test_legacy_slot_roles_are_still_readable(self):
        registry = InstrumentRegistry(document=_document([
            _entry(role="PS3"),
            _entry(full_code=SECOND_CODE, role=None),
            _entry(full_code=THIRD_CODE, role="PS4"),
        ]))

        instruments = {item.full_code: item for item in registry.list()}

        self.assertEqual(instruments[FIRST_CODE].role, "PS3")
        self.assertIsNone(instruments[SECOND_CODE].role)
        self.assertEqual(instruments[THIRD_CODE].role, "PS4")
        self.assertEqual(
            {canonical_role(item.role) for item in instruments.values()},
            {POWER_SUPPLY_ROLE, None},
        )
        self.assertEqual({item.label for item in instruments.values()}, {None})
        self.assertEqual(
            {item.display_name for item in instruments.values()}, {"DP832A"}
        )

    def test_legacy_serial_and_asset_name_are_projected_on_read(self):
        registry = InstrumentRegistry(document={
            "schema_version": 3,
            "updated_at": "2026-10-29T15:00:00+08:00",
            "instruments": [
                {
                    "name": "DP832A-1",
                    "vendor": RIGOL_VENDOR,
                    "model": "DP832A",
                    "model_index": 1,
                    "serial": FIRST_CODE,
                    "label": "DP8B1519",
                    "role": "power_supply",
                    "status": "online",
                    "known_ips": [],
                }
            ],
        })

        instrument = registry.get_by_device_key(make_device_key(RIGOL_VENDOR, FIRST_CODE))

        self.assertIsNotNone(instrument)
        self.assertEqual(instrument.full_code, FIRST_CODE)
        self.assertEqual(instrument.label, "DP8B1519")
        self.assertNotIn("name", registry.raw["instruments"][0])
        self.assertNotIn("model_index", registry.raw["instruments"][0])
        self.assertNotIn("serial", registry.raw["instruments"][0])
        self.assertEqual(registry.raw["instruments"][0]["full_code"], FIRST_CODE)

    def test_multiple_power_supplies_are_read_with_their_labels(self):
        registry = InstrumentRegistry(document=_document([
            _entry(role=POWER_SUPPLY_ROLE, label="DP8B1519"),
            _entry(
                full_code=THIRD_CODE,
                role=POWER_SUPPLY_ROLE,
                label="DP8B0885",
            ),
        ]))

        power_supplies = [item for item in registry.list() if item.role == POWER_SUPPLY_ROLE]

        self.assertEqual({item.label for item in power_supplies}, {"DP8B1519", "DP8B0885"})
        self.assertEqual(
            {item.display_name for item in power_supplies}, {"DP8B1519", "DP8B0885"}
        )
        self.assertEqual(
            sorted(item.device_key for item in power_supplies),
            sorted([
                make_device_key(RIGOL_VENDOR, FIRST_CODE),
                make_device_key(RIGOL_VENDOR, THIRD_CODE),
            ]),
        )

    def test_label_survives_a_scan_and_save_round_trip(self):
        """标签不属于扫描字段：一次 upsert + save 不得把它清掉。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instruments.json"
            path.write_text(
                json.dumps(
                    _document([_entry(role=POWER_SUPPLY_ROLE, label="DP8B1519")]),
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            registry = InstrumentRegistry.load(path)
            registry.upsert(InstrumentIdentity.from_idn(
                f"{RIGOL_VENDOR},DP832A,{FIRST_CODE},00.01.16"
            ))
            registry.save()

            instrument = InstrumentRegistry.load(path).get_by_device_key(
                make_device_key(RIGOL_VENDOR, FIRST_CODE)
            )

        self.assertEqual(instrument.label, "DP8B1519")
        self.assertEqual(instrument.role, POWER_SUPPLY_ROLE)

    def test_saved_document_has_no_retired_asset_name_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instruments.json"
            path.write_text(
                json.dumps(_document([_entry(role=POWER_SUPPLY_ROLE)]), ensure_ascii=False),
                encoding="utf-8",
            )

            registry = InstrumentRegistry.load(path)
            registry.save()
            saved = json.loads(path.read_text(encoding="utf-8"))

        entry = saved["instruments"][0]
        self.assertEqual(saved["schema_version"], SCHEMA_VERSION)
        self.assertIn("full_code", entry)
        self.assertNotIn("name", entry)
        self.assertNotIn("model_index", entry)
        self.assertNotIn("serial", entry)


class WriteBackBoundaryTests(unittest.TestCase):
    """写回边界：注册表里存下来、再读出来的角色必须折叠成 ``power_supply``。"""

    def test_saved_role_is_canonicalisable_to_power_supply(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instruments.json"
            path.write_text(
                json.dumps(_document([_entry()]), ensure_ascii=False), encoding="utf-8"
            )

            registry = InstrumentRegistry.load(path)
            device_key = make_device_key(RIGOL_VENDOR, FIRST_CODE)
            registry.set_role(device_key, "PS3")
            registry.save()

            reloaded = InstrumentRegistry.load(path).get_by_device_key(device_key)

        self.assertEqual(reloaded.role, POWER_SUPPLY_ROLE)
        self.assertEqual(canonical_role(reloaded.role), POWER_SUPPLY_ROLE)


if __name__ == "__main__":
    unittest.main()
