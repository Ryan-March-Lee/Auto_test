"""电源配置页卡片视图：相对格位、真实卡片控件、缩放与编辑信号。"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from presentation.qt.power_card import (
    POWER_CARD_HEIGHT,
    POWER_CARD_WIDTH,
    PowerSupplyCardData,
    power_card_layout,
)
from presentation.qt.power_card_wall import PowerCardWall, power_card_data


def _card(code: str, *, enabled: bool = True) -> PowerSupplyCardData:
    data = power_card_data(
        {
            "label": code,
            "model": "DP832A",
            "channels": {
                "CH1": {
                    "voltage": {"value": 2.5, "protection_enabled": True},
                    "current": {"value": 0.4},
                }
            },
        },
        code,
    )
    for name in ("CH1", "CH2"):
        data = data.with_channel_enabled(name, enabled)
    return data


class PowerCardWallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_wall(self) -> PowerCardWall:
        wall = PowerCardWall()
        wall.resize(1000, 232)
        wall.show()
        self.app.processEvents()
        self.addCleanup(self._destroy_widget, wall)
        return wall

    def _destroy_widget(self, widget) -> None:
        widget.close()
        widget.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_cards_on_the_same_row_keep_their_left_to_right_order(self):
        wall = self.make_wall()
        wall.set_cards(
            [("DP8B1519", _card("DP8B1519")), ("DP8B0885", _card("DP8B0885"))],
            {"DP8B1519": 0, "DP8B0885": 1},
        )

        first = wall.card_rect("DP8B1519")
        second = wall.card_rect("DP8B0885")
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(first.y(), second.y())
        self.assertLess(first.x(), second.x())
        self.assertEqual(first.size(), second.size())
        self.assertAlmostEqual(first.width(), POWER_CARD_WIDTH)
        self.assertAlmostEqual(first.height(), POWER_CARD_HEIGHT)

    def test_diagonal_slots_keep_the_grid_relationship_and_shrink_to_fit(self):
        wall = self.make_wall()
        wall.set_cards(
            [("DP8B1519", _card("DP8B1519")), ("DP8B0885", _card("DP8B0885"))],
            {"DP8B1519": 0, "DP8B0885": 8},
        )

        top_left = wall.card_rect("DP8B1519")
        bottom_right = wall.card_rect("DP8B0885")
        self.assertLess(top_left.x(), bottom_right.x(), "右边的电源仍要排在右边")
        self.assertLess(top_left.y(), bottom_right.y(), "下方的电源仍要排在下方")
        self.assertLess(
            bottom_right.width(), POWER_CARD_WIDTH, "三列放不下时整块等比缩小"
        )
        self.assertAlmostEqual(
            bottom_right.width() / bottom_right.height(),
            POWER_CARD_WIDTH / POWER_CARD_HEIGHT,
            places=4,
        )

    def test_missing_slots_are_filled_in_order(self):
        wall = self.make_wall()
        wall.set_cards(
            [("A", _card("A")), ("B", _card("B")), ("C", _card("C"))],
            {"B": 0},
        )

        self.assertEqual(sorted(cell.slot for cell in wall.cards()), [0, 1, 2])
        self.assertEqual(
            {cell.supply_id: cell.slot for cell in wall.cards()}["B"], 0
        )

    def test_an_empty_wall_takes_no_height_and_removes_cards(self):
        wall = self.make_wall()
        wall.set_cards([("A", _card("A"))], {"A": 0})
        self.assertIsNotNone(wall.supply_card("A"))

        wall.set_cards([])

        self.assertTrue(wall.is_empty())
        self.assertEqual(wall.maximumHeight(), 0)
        self.assertIsNone(wall.supply_card("A"))

    def test_card_data_always_keeps_both_channels(self):
        data = power_card_data(
            {
                "label": "DP8B1519",
                "channels": {
                    "CH1": {
                        "voltage": {"value": 2.8, "protection_enabled": True},
                        "current": {"value": 0.1, "protection_enabled": False},
                    }
                },
            },
            "DP8B1519",
        )

        self.assertEqual(data.code, "DP8B1519")
        self.assertTrue(data.channel("CH1").enabled)
        self.assertAlmostEqual(data.channel("CH1").voltage_set, 2.8)
        self.assertAlmostEqual(data.channel("CH1").current_set, 0.1)
        self.assertTrue(data.channel_protection("CH1", "voltage"))
        self.assertFalse(data.channel_protection("CH1", "current"))
        self.assertTrue(data.channel("CH2").enabled, "两个通道始终保留在卡面上")
        self.assertAlmostEqual(data.channel("CH2").voltage_set, 0.0)

    def test_missing_protection_flags_are_unchecked(self):
        data = power_card_data(
            {
                "label": "DP8B1519",
                "channels": {
                    "CH1": {
                        "voltage": {"value": 2.8, "protection_enabled": None},
                        "current": {"value": 0.1},
                    }
                },
            },
            "DP8B1519",
        )

        self.assertFalse(data.channel_protection("CH1", "voltage"))
        self.assertFalse(data.channel_protection("CH1", "current"))

    def test_set_protection_updates_only_that_card(self):
        wall = self.make_wall()
        wall.set_cards(
            [("A", _card("A")), ("B", _card("B"))], {"A": 0, "B": 1}
        )

        self.assertTrue(wall.set_protection("A", "CH1", "current", True))
        self.assertFalse(wall.set_protection("A", "CH1", "current", True))
        cards = {cell.supply_id: cell.data for cell in wall.cards()}
        self.assertTrue(cards["A"].channel_protection("CH1", "current"))
        self.assertFalse(cards["B"].channel_protection("CH1", "current"))
        self.assertFalse(wall.set_protection("C", "CH1", "voltage", True))
        self.assertTrue(
            wall.supply_card("A").channel_protection("CH1", "current"),
            "卡片控件与墙面快照同步",
        )

    def test_clicking_a_card_protection_check_emits_with_the_supply_id(self):
        wall = self.make_wall()
        wall.set_cards([("A", _card("A"))], {"A": 0})
        events: list[tuple[str, str, str, bool]] = []
        wall.protection_toggled.connect(lambda *args: events.append(args))
        card = wall.supply_card("A")
        check = card.protection_check("CH1", "voltage")

        QTest.mouseClick(check, Qt.LeftButton, pos=check.rect().center())

        self.assertEqual(events, [("A", "CH1", "voltage", False)])

    def test_clicking_an_off_channel_check_does_nothing(self):
        wall = self.make_wall()
        data = _card("A").with_channel_enabled("CH2", False)
        wall.set_cards([("A", data)], {"A": 0})
        events: list[tuple[str, str, str, bool]] = []
        wall.protection_toggled.connect(lambda *args: events.append(args))
        check = wall.supply_card("A").protection_check("CH2", "voltage")

        QTest.mouseClick(check, Qt.LeftButton, pos=check.rect().center())

        self.assertEqual(events, [])

    def test_editing_a_card_value_emits_and_updates_the_wall_snapshot(self):
        wall = self.make_wall()
        wall.set_cards([("A", _card("A"))], {"A": 0})
        events: list[tuple[str, str, str, float]] = []
        wall.value_changed.connect(lambda *args: events.append(args))
        card = wall.supply_card("A")

        card.value_field("CH1", "voltage").setValue(7.5)

        self.assertEqual(events, [("A", "CH1", "voltage", 7.5)])
        self.assertAlmostEqual(
            wall.cards()[0].data.channel("CH1").voltage_set, 7.5
        )

    def test_toggling_a_card_switch_emits_only_the_edit_state(self):
        wall = self.make_wall()
        wall.set_cards([("A", _card("A", enabled=False))], {"A": 0})
        channel_events: list[tuple[str, str, bool]] = []
        value_events: list[tuple[str, str, str, float]] = []
        wall.channel_toggled.connect(lambda *args: channel_events.append(args))
        wall.value_changed.connect(lambda *args: value_events.append(args))
        card = wall.supply_card("A")
        switch = card.channel_switch("CH1")

        QTest.mouseClick(switch, Qt.LeftButton, pos=switch.rect().center())

        self.assertEqual(channel_events, [("A", "CH1", True)])
        self.assertEqual(value_events, [], "启用通道不是数值编辑")

    def test_card_pixmap_is_a_snapshot_of_the_real_card(self):
        wall = self.make_wall()
        wall.set_cards([("A", _card("A"))], {"A": 0})
        card = wall.supply_card("A")

        pixmap = wall.card_pixmap("A")

        self.assertFalse(pixmap.isNull())
        ratio = float(pixmap.devicePixelRatio()) or 1.0
        self.assertEqual(
            (round(pixmap.width() / ratio), round(pixmap.height() / ratio)),
            (card.width(), card.height()),
            "展开动画从真实控件取图",
        )

    def test_cards_and_their_inputs_shrink_with_the_wall(self):
        wall = self.make_wall()
        wall.set_cards([("A", _card("A"))], {"A": 0})
        wall.resize(300, 232)
        self.app.processEvents()

        card = wall.supply_card("A")
        rect = wall.card_rect("A")
        self.assertLess(card.width(), POWER_CARD_WIDTH)
        self.assertAlmostEqual(card.width(), rect.width())
        field = card.value_field("CH1", "voltage")
        layout = power_card_layout()
        expected_width = round(layout.channels[0].value_boxes[0].width() * card.scale)
        self.assertEqual(field.width(), expected_width)

    def test_control_records_match_the_configuration_form_protocol(self):
        wall = self.make_wall()
        wall.set_cards([("A", _card("A"))], {"A": 0})

        controls = wall.control_records()["A"]["channels"]["CH1"]

        self.assertEqual(
            set(controls),
            {
                "voltage",
                "current",
                "voltage_protection_enabled",
                "current_protection_enabled",
            },
        )
        self.assertAlmostEqual(controls["voltage"].value(), 2.5)
        self.assertTrue(controls["voltage_protection_enabled"].isChecked())

    def test_rebuilding_can_preserve_or_discard_uncommitted_edits(self):
        wall = self.make_wall()
        wall.set_cards([("A", _card("A", enabled=False))], {"A": 0})
        card = wall.supply_card("A")
        card.set_channel_enabled("CH1", True)
        card.set_value("CH1", "voltage", 9.0)

        wall.set_cards(
            [("A", _card("A", enabled=False))], {"A": 0}, preserve_edit_state=True
        )
        preserved = wall.supply_card("A")
        self.assertTrue(preserved.channel_enabled("CH1"))
        self.assertAlmostEqual(preserved.channel_value("CH1", "voltage"), 9.0)

        wall.set_cards(
            [("A", _card("A", enabled=False))], {"A": 0}, preserve_edit_state=False
        )
        discarded = wall.supply_card("A")
        self.assertFalse(discarded.channel_enabled("CH1"))
        self.assertAlmostEqual(discarded.channel_value("CH1", "voltage"), 2.5)


if __name__ == "__main__":
    unittest.main()
