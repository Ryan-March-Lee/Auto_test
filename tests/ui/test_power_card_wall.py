"""电源配置页卡片视图：相对格位、卡片数据与空布局。"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from presentation.qt.power_card import (
    POWER_CARD_HEIGHT,
    POWER_CARD_WIDTH,
    PowerSupplyCardData,
)
from presentation.qt.power_card_wall import PowerCardWall, power_card_data


def _card(code: str) -> PowerSupplyCardData:
    return power_card_data(
        {"label": code, "channels": {"CH1": {"voltage": {"value": 2.5}}}},
        code,
    )


class PowerCardWallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_wall(self) -> PowerCardWall:
        wall = PowerCardWall()
        wall.resize(1000, 232)
        wall.show()
        self.app.processEvents()
        return wall

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

    def test_an_empty_wall_takes_no_height(self):
        wall = self.make_wall()
        wall.set_cards([])

        self.assertTrue(wall.is_empty())
        self.assertEqual(wall.maximumHeight(), 0)

    def test_card_data_reads_channel_values_and_marks_missing_channels_off(self):
        data = power_card_data(
            {
                "label": "DP8B1519",
                "channels": {
                    "CH1": {
                        "voltage": {"value": 2.8},
                        "current": {"value": 0.1},
                    }
                },
            },
            "DP8B1519",
        )

        self.assertEqual(data.code, "DP8B1519")
        self.assertTrue(data.channel("CH1").enabled)
        self.assertAlmostEqual(data.channel("CH1").voltage_set, 2.8)
        self.assertAlmostEqual(data.channel("CH1").voltage_protection, 3.08)
        self.assertAlmostEqual(data.channel("CH1").current_set, 0.1)
        self.assertAlmostEqual(data.channel("CH1").current_protection, 0.11)
        self.assertFalse(data.channel("CH2").enabled)
        self.assertAlmostEqual(data.channel("CH2").voltage_set, 0.0)
