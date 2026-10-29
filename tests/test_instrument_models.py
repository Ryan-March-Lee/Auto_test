"""仪器领域模型的角色类别与短标签字段（电源重构计划步骤 3）。

三条不变量：

1. ``role`` 只表示**设备类别**：新增 ``power_supply`` 后多台电源共用同一类别，
   靠各自短标签区分，而不是靠“第几台”；
2. 旧槽位 ``PS1``..``PS4`` 只在读取时被识别（``normalize_role`` 原样返回），写回时由
   ``canonical_role`` 折叠成 ``power_supply``，不再出现在可写回的角色集合里；
3. ``Instrument.label`` 是可空的稳定短标签：序列化带着它，现场显示优先用它，
   ``name`` 仍是软件资产名；身份仍然只由 ``厂商 + 序列号`` 派生。它是**追加**字段，
   位置参数顺序保持不变，否则公开模型的既有构造方式会被静默改写。

读取兼容在最后一组用例里固定：旧注册表记录（``role: "PS3"`` / 无 ``label``）仍可读取，
新注册表里的多台 ``power_supply`` 带着各自的 ``label`` 读出。``label`` 的生成与写回属于
注册表迁移（步骤 4），本步骤不生成标签、不改动扫描流程。
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
    make_device_key,
    normalize_role,
)
from infrastructure.persistence.instrument_registry import SCHEMA_VERSION, InstrumentRegistry

RIGOL_VENDOR = "RIGOL TECHNOLOGIES"
FIRST_SERIAL = "DP8B214901519"
SECOND_SERIAL = "DP8B174700950"
THIRD_SERIAL = "DP8B174400885"


def _instrument(**overrides):
    fields = {
        "id": 0,
        "name": "DP832A-1",
        "vendor": RIGOL_VENDOR,
        "model": "DP832A",
        "model_index": 1,
        "serial": FIRST_SERIAL,
        "device_key": make_device_key(RIGOL_VENDOR, FIRST_SERIAL),
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
        "name": "DP832A-1",
        "vendor": RIGOL_VENDOR,
        "model": "DP832A",
        "model_index": 1,
        "serial": FIRST_SERIAL,
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

    def test_multiple_power_supplies_share_one_role(self):
        first = replace(_instrument(), role=POWER_SUPPLY_ROLE, label="DP8B1519")
        second = replace(
            _instrument(
                id=1,
                name="DP832A-2",
                serial=SECOND_SERIAL,
                device_key=make_device_key(RIGOL_VENDOR, SECOND_SERIAL),
                model_index=2,
            ),
            role=POWER_SUPPLY_ROLE,
            label="DP8B0950",
        )

        self.assertEqual({item.role for item in (first, second)}, {POWER_SUPPLY_ROLE})
        self.assertEqual(len({item.device_key for item in (first, second)}), 2)
        self.assertEqual(
            sorted(item.display_name for item in (first, second)), ["DP8B0950", "DP8B1519"]
        )


class InstrumentLabelTests(unittest.TestCase):
    """``label`` 是可空稳定字段，显示优先用它，身份不受它影响。"""

    def test_label_defaults_to_none_and_display_falls_back_to_asset_name(self):
        instrument = _instrument()
        self.assertIsNone(instrument.label)
        self.assertEqual(instrument.display_name, "DP832A-1")

    def test_display_name_prefers_the_label(self):
        instrument = replace(_instrument(), label="DP8B1519")
        self.assertEqual(instrument.display_name, "DP8B1519")
        self.assertEqual(instrument.name, "DP832A-1")

    def test_to_dict_includes_the_label(self):
        self.assertIsNone(_instrument().to_dict()["label"])
        self.assertEqual(
            replace(_instrument(), label="DP8B1519").to_dict()["label"], "DP8B1519"
        )

    def test_label_does_not_change_identity(self):
        labelled = replace(_instrument(), label="DP8B1519", role=POWER_SUPPLY_ROLE)
        self.assertEqual(labelled.device_key, make_device_key(RIGOL_VENDOR, FIRST_SERIAL))
        self.assertEqual(labelled.to_dict()["device_key"], labelled.device_key)

    def test_label_is_appended_so_positional_arguments_keep_their_meaning(self):
        """``label`` 必须追加在既有字段之后：位置参数顺序一旦改变会静默改写别的字段。"""
        endpoints = (InstrumentEndpoint(ip="192.168.1.102"),)
        instrument = Instrument(
            1,
            "DP832A-1",
            RIGOL_VENDOR,
            "DP832A",
            1,
            FIRST_SERIAL,
            make_device_key(RIGOL_VENDOR, FIRST_SERIAL),
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
        self.assertEqual(instrument.display_name, "DP832A-1")


class LegacyRegistryReadTests(unittest.TestCase):
    """旧注册表记录仍可读取；新记录里的多台电源各自带标签。"""

    def test_legacy_slot_roles_are_still_readable(self):
        registry = InstrumentRegistry(document=_document([
            _entry(role="PS3"),
            _entry(name="DP832A-2", model_index=2, serial=SECOND_SERIAL, role=None),
            _entry(name="DP832A-3", model_index=3, serial=THIRD_SERIAL, role="PS4"),
        ]))

        instruments = registry.list()

        self.assertEqual([item.role for item in instruments], ["PS3", None, "PS4"])
        self.assertEqual(
            [canonical_role(item.role) for item in instruments],
            [POWER_SUPPLY_ROLE, None, POWER_SUPPLY_ROLE],
        )
        self.assertEqual([item.label for item in instruments], [None, None, None])
        self.assertEqual(
            [item.display_name for item in instruments],
            ["DP832A-1", "DP832A-2", "DP832A-3"],
        )

    def test_multiple_power_supplies_are_read_with_their_labels(self):
        registry = InstrumentRegistry(document=_document([
            _entry(role=POWER_SUPPLY_ROLE, label="DP8B1519"),
            _entry(
                name="DP832A-3",
                model_index=3,
                serial=THIRD_SERIAL,
                role=POWER_SUPPLY_ROLE,
                label="DP8B0885",
            ),
        ]))

        power_supplies = [item for item in registry.list() if item.role == POWER_SUPPLY_ROLE]

        self.assertEqual([item.label for item in power_supplies], ["DP8B1519", "DP8B0885"])
        self.assertEqual(
            [item.display_name for item in power_supplies], ["DP8B1519", "DP8B0885"]
        )
        self.assertEqual(
            sorted(item.device_key for item in power_supplies),
            sorted([
                make_device_key(RIGOL_VENDOR, FIRST_SERIAL),
                make_device_key(RIGOL_VENDOR, THIRD_SERIAL),
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
                f"{RIGOL_VENDOR},DP832A,{FIRST_SERIAL},00.01.16"
            ))
            registry.save()

            instrument = InstrumentRegistry.load(path).get_by_name("DP832A-1")

        self.assertEqual(instrument.label, "DP8B1519")
        self.assertEqual(instrument.role, POWER_SUPPLY_ROLE)


class WriteBackBoundaryTests(unittest.TestCase):
    """写回边界：注册表里存下来、再读出来的角色必须折叠成 ``power_supply``。

    ``set_role`` 仍接受旧槽位字面量（兼容旧调用），真正折叠发生在 ``save()`` 的迁移里；
    这里固定跨 API 的不变量——存进文件再读出来，经过 :func:`canonical_role` 必须落在
    ``power_supply``，不为折叠发生在哪一层写死实现。
    """

    def test_saved_role_is_canonicalisable_to_power_supply(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instruments.json"
            path.write_text(
                json.dumps(_document([_entry()]), ensure_ascii=False), encoding="utf-8"
            )

            registry = InstrumentRegistry.load(path)
            registry.set_role("DP832A-1", "PS3")
            registry.save()

            reloaded = InstrumentRegistry.load(path).get_by_name("DP832A-1")

        self.assertEqual(reloaded.role, POWER_SUPPLY_ROLE)
        self.assertEqual(canonical_role(reloaded.role), POWER_SUPPLY_ROLE)


if __name__ == "__main__":
    unittest.main()
