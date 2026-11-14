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
        data = data.with_channel_mode(name, "driver" if enabled else "off")
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
        self.assertAlmostEqual(
            second.x() - (first.x() + first.width()),
            22.0,
            msg="多张卡片之间留 22 的逻辑间距",
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




    def test_clicking_a_card_protection_check_emits_with_the_supply_id(self):
        wall = self.make_wall()
        wall.set_cards([("A", _card("A"))], {"A": 0})
        events: list[tuple[str, str, str, bool]] = []
        wall.protection_toggled.connect(lambda *args: events.append(args))
        card = wall.supply_card("A")
        check = card.protection_check("CH1", "voltage")

        QTest.mouseClick(check, Qt.LeftButton, pos=check.rect().center())

        self.assertEqual(events, [("A", "CH1", "voltage", False)])




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



    def test_rebuilding_preserves_the_uncommitted_delay(self):
        wall = self.make_wall()
        wall.set_cards([("A", _card("A"))], {"A": 0})
        wall.supply_card("A").delay_field("CH1").setValue(4.0)

        wall.set_cards(
            [("A", _card("A"))], {"A": 0}, preserve_edit_state=True
        )

        self.assertAlmostEqual(
            wall.supply_card("A").channel_power_delay("CH1"), 4.0
        )






if __name__ == "__main__":
    unittest.main()
