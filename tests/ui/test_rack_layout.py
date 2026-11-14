"""九宫格排布的清洗与补位：纯数据，不需要 Qt 窗口。"""

import unittest

from presentation.qt.rack_layout import (
    SLOT_COUNT,
    configured_power_supply_ids,
    normalize_rack_slots,
)


class NormalizeRackSlotsTests(unittest.TestCase):
    def test_keeps_valid_slots_and_fills_missing_supplies_in_order(self):
        slots = normalize_rack_slots(
            {"PS2": 4},
            ["PS1", "PS2", "PS3"],
        )

        self.assertEqual(slots, {"PS1": 0, "PS2": 4, "PS3": 1})

    def test_drops_unknown_out_of_range_duplicate_and_non_integer_entries(self):
        slots = normalize_rack_slots(
            {
                "UNKNOWN": 0,
                "PS1": 9,
                "PS2": "not-an-int",
                "PS3": 2,
                "PS4": 2,
                "PS5": True,
            },
            ["PS1", "PS2", "PS3", "PS4", "PS5"],
        )

        self.assertEqual(slots, {"PS1": 0, "PS2": 1, "PS3": 2, "PS4": 3, "PS5": 4})
        self.assertEqual(len(slots), 5)
        self.assertNotIn(9, slots.values())

    def test_never_exceeds_the_nine_slots(self):
        supply_ids = [f"PS{index}" for index in range(SLOT_COUNT + 2)]

        slots = normalize_rack_slots({}, supply_ids)

        self.assertEqual(len(slots), SLOT_COUNT)
        self.assertEqual(set(slots.values()), set(range(SLOT_COUNT)))

    def test_accepts_numeric_strings_from_hand_edited_metadata(self):
        slots = normalize_rack_slots({"PS1": "7"}, ["PS1"])

        self.assertEqual(slots, {"PS1": 7})

    def test_missing_or_malformed_mapping_falls_back_to_empty_layout(self):
        self.assertEqual(normalize_rack_slots(None, ["PS1"]), {"PS1": 0})
        self.assertEqual(normalize_rack_slots("bad", ["PS1"]), {"PS1": 0})


class ConfiguredPowerSupplyIdsTests(unittest.TestCase):
    def test_uses_bindings_then_legacy_instrument_keys_without_duplicates(self):
        def binding(prefix: str) -> dict:
            return {
                "vendor": "RIGOL TECHNOLOGIES",
                "model": "DP832A",
                "label": prefix,
                "full_code": f"CODE{prefix}",
                "device_key": f"rigol technologies:code{prefix.lower()}",
                "visa_address": f"sim::{prefix.lower()}",
                "last_ip": None,
                "last_port": None,
                "known": True,
                "source": "scan",
            }

        config = {
            "device_bindings": {
                "power_supplies": {
                    "PS-B": binding("PS-B"),
                    "PS-A": binding("PS-A"),
                }
            },
            "instruments": {
                "power_supplies": {
                    "PS-A": {"model": "DP832A"},
                    "PS-LEGACY": {"model": "DP832A"},
                }
            },
        }

        self.assertEqual(
            configured_power_supply_ids(config),
            ["PS-B", "PS-A", "PS-LEGACY"],
        )


if __name__ == "__main__":
    unittest.main()
