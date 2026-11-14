"""仪器排布页：入场动画、九宫格拖放与“下一步”门禁。

本组用例是纯表现层测试：卡片正面用一份纯色位图代替真实照片，页面不读配置、不连接
仪器。绘制用例只验证页面能完成静态与动画帧的绘制，不比较像素内容——外观由
``theme`` 的令牌决定，行为契约在这里锁定。
"""

import os
import unittest
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import (
    QMimeData,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QVariantAnimation,
)
from PySide6.QtGui import QColor, QDropEvent, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QLabel

from presentation.qt import rack_overview_page as rack
from presentation.qt.card_assets import render_card_front
from presentation.qt.device_cards import (
    CARD_HEIGHT,
    CARD_MIN_WIDTH,
    CARD_WIDTH,
    InstrumentCardData,
    card_row_plan,
)
from presentation.qt.device_cards import InstrumentCard
from presentation.qt.rack_overview_page import RackCardEntry, RackOverviewPage
from presentation.qt.theme import TOKENS, card_code_font


def _front() -> QPixmap:
    pixmap = QPixmap(CARD_WIDTH, CARD_HEIGHT)
    pixmap.fill(QColor("#FFFFFF"))
    return pixmap


def _entries(power_codes=("DP8B1519", "DP8B0885"), *, sides=True, offset=0):
    entries = [
        RackCardEntry(
            "power_supply",
            code,
            _front(),
            QRect(120 + index * 40 + offset, 120 + offset, CARD_WIDTH, CARD_HEIGHT),
        )
        for index, code in enumerate(power_codes)
    ]
    if sides:
        entries.append(
            RackCardEntry(
                "signal_generator",
                "5462",
                _front(),
                QRect(120 + offset, 420 + offset, CARD_WIDTH, CARD_HEIGHT),
            )
        )
        entries.append(
            RackCardEntry(
                "spectrum_analyzer",
                "QZNI0866",
                _front(),
                QRect(900 + offset, 420 + offset, CARD_WIDTH, CARD_HEIGHT),
            )
        )
    return entries


def _image_difference(first: QImage, second: QImage) -> float:
    """两张位图不一致的字节占比；只用来放过抗锯齿级差异。"""

    def pixels(image: QImage) -> np.ndarray:
        image = image.convertToFormat(QImage.Format.Format_RGBA8888)
        raw = np.frombuffer(image.constBits(), dtype=np.uint8)
        return raw.reshape(image.height(), image.bytesPerLine())[
            :, : image.width() * 4
        ].copy()

    a = pixels(first)
    b = pixels(second)
    return float(np.count_nonzero(a != b)) / max(1, a.size)


class RackOverviewPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _page(self, entries=None):
        page = RackOverviewPage()
        page.resize(1180, 760)
        page.show()
        self.app.processEvents()
        page.enter(_entries() if entries is None else entries)
        self.addCleanup(page.close)
        self.addCleanup(page.deleteLater)
        return page

    # ---- 动画与门禁 -------------------------------------------------------

    def test_enter_starts_the_transition_and_disables_next(self):
        page = self._page()
        self.assertTrue(page.transition_running())
        self.assertFalse(page.next_btn.isEnabled())

        page.finish_transition()

        self.assertFalse(page.transition_running())
        self.assertFalse(page.next_btn.isEnabled(), "电源还没摆好，不能进入测试配置页")

    def test_no_power_supplies_allows_next_after_the_transition(self):
        page = self._page(_entries(power_codes=()))
        page.finish_transition()

        self.assertTrue(page.all_supplies_placed())
        self.assertTrue(page.next_btn.isEnabled())

    def test_every_power_supply_must_be_placed_before_next(self):
        page = self._page()
        page.finish_transition()
        supplies = page.power_supply_ids()
        self.assertEqual(len(supplies), 2)

        page.place_supply(supplies[0], 0)
        self.assertFalse(page.next_btn.isEnabled(), "只放一台还不够")

        page.place_supply(supplies[1], 4)
        self.assertTrue(page.all_supplies_placed())
        self.assertTrue(page.next_btn.isEnabled())

        page.unplace_supply(supplies[0])
        self.assertFalse(page.next_btn.isEnabled(), "取回一台后要重新变灰")

    def test_entering_again_resets_the_rack(self):
        page = self._page()
        page.finish_transition()
        supply = page.power_supply_ids()[0]
        page.place_supply(supply, 3)

        page.enter(_entries(offset=20))
        page.finish_transition()

        self.assertEqual(page.slot_assignments(), {})
        self.assertFalse(page.next_btn.isEnabled())

    def test_animated_enter_can_be_skipped_for_a_loaded_configuration(self):
        page = RackOverviewPage()
        page.resize(1180, 760)
        page.show()
        self.addCleanup(page.close)
        self.addCleanup(page.deleteLater)

        page.enter(_entries(), animate=False)

        self.assertFalse(page.transition_running())
        self.assertFalse(page.next_btn.isEnabled(), "电源还没恢复格位")
        page.set_slot_assignments({"DP8B1519": 4, "DP8B0885": 7})
        self.assertTrue(page.next_btn.isEnabled())

    def test_set_slot_assignments_filters_unknown_and_invalid_entries(self):
        page = self._page()
        page.finish_transition()

        page.set_slot_assignments(
            {
                "UNKNOWN": 0,
                "DP8B1519": 99,
                "DP8B0885": 3,
            }
        )

        self.assertEqual(page.slot_assignments(), {"DP8B0885": 3})
        self.assertFalse(page.next_btn.isEnabled())

        page.set_slot_assignments({"DP8B1519": 2, "DP8B0885": 2})
        self.assertEqual(
            page.slot_assignments(),
            {"DP8B1519": 2},
            "同一格位只能留给第一台电源",
        )

    def test_configuration_entries_rebuild_the_same_instrument_roles(self):
        config = {
            "instruments": {
                "signal_generator": {"model": "SMW200A", "address": "sim::sg"},
                "spectrum_analyzer": {"model": "4082F", "address": "sim::sa"},
                "power_supplies": {
                    "PS1": {"model": "DP832A", "address": "sim::ps1"},
                    "PS2": {"model": "DP832A", "address": "sim::ps2"},
                },
            },
            "device_bindings": {
                "signal_generator": {"visa_address": "sim::sg", "source": "scan"},
                "spectrum_analyzer": {"visa_address": "sim::sa", "source": "scan"},
                "power_supplies": {
                    "PS1": {"visa_address": "sim::ps1", "source": "scan"},
                    "PS2": {"visa_address": "sim::ps2", "source": "scan"},
                },
            },
        }

        entries = RackOverviewPage.entries_from_configuration(config)

        self.assertEqual(
            [entry.role_key for entry in entries],
            ["signal_generator", "spectrum_analyzer", "power_supply", "power_supply"],
        )
        self.assertEqual(
            [entry.code for entry in entries[2:]],
            ["PS1", "PS2"],
            "电源条目的 code 必须是配置里的 supply_id，九宫格映射才不丢",
        )
        self.assertTrue(all(not entry.front.isNull() for entry in entries))

    def test_next_emits_only_when_the_button_is_ready(self):
        page = self._page()
        emitted = []
        page.next_requested.connect(lambda: emitted.append(True))

        page.finish_transition()
        page.next_btn.click()
        self.assertEqual(emitted, [], "未摆好时点击不能推进")

        for index, supply in enumerate(page.power_supply_ids()):
            page.place_supply(supply, index)
        page.next_btn.click()
        self.assertEqual(emitted, [], "淡出动画结束前还不能切页")
        page.finish_transition()
        self.assertEqual(emitted, [True])














    # ---- 摆位规则 ---------------------------------------------------------

    def test_placing_on_an_occupied_slot_swaps_the_two_supplies(self):
        page = self._page()
        first, second = page.power_supply_ids()
        page.place_supply(first, 0)
        page.place_supply(second, 1)

        page.place_supply(first, 1)

        self.assertEqual(page.slot_of(first), 1)
        self.assertEqual(page.slot_of(second), 0)

    def test_dropping_a_placed_supply_on_an_occupied_slot_swaps(self):
        page = self._page()
        first, second = page.power_supply_ids()
        page.place_supply(first, 2)
        page.place_supply(second, 5)
        # 第二台还在格子里，第一台拖到第二台的格子：两台互换，不能把第二台挤掉。
        page.place_supply(first, 5)
        self.assertEqual(page.slot_of(first), 5)
        self.assertEqual(page.slot_of(second), 2)

    def test_placing_on_an_occupied_slot_from_the_tray_returns_the_occupant(self):
        page = self._page()
        first, second = page.power_supply_ids()
        page.place_supply(first, 4)

        page.place_supply(second, 4)

        self.assertEqual(page.slot_of(second), 4)
        self.assertIsNone(page.slot_of(first), "被占用的电源应退回托盘")

    def test_drop_supply_accepts_slot_and_tray_targets(self):
        page = self._page()
        supply = page.power_supply_ids()[0]

        self.assertTrue(page.drop_supply(supply, 7))
        self.assertEqual(page.slot_of(supply), 7)

        self.assertTrue(page.drop_supply(supply, -1))
        self.assertIsNone(page.slot_of(supply))

        self.assertFalse(page.drop_supply(supply, None), "落点不在本页时不改变状态")

    def test_place_rejects_unknown_supply_and_out_of_range_slot(self):
        page = self._page()
        supply = page.power_supply_ids()[0]

        with self.assertRaises(ValueError):
            page.place_supply("NOT-A-SUPPLY", 0)
        with self.assertRaises(ValueError):
            page.place_supply(supply, rack._SLOT_COUNT)
        with self.assertRaises(ValueError):
            page.place_supply(supply, -1)

    # ---- 拖放事件 ---------------------------------------------------------


    # ---- 卡片入口与绘制 ---------------------------------------------------














    # ---- 链路几何与功放标签 -----------------------------------------------





    def test_no_driver_mode_drops_the_driver_from_the_chain(self):
        """无驱放测试没有驱动功放：链路里只留居中的 DUT，两侧射频线等长。"""
        page = self._page()
        page.finish_transition()
        self.assertEqual(
            [label for label, _ in page.board._amplifiers()], ["Driver", "DUT"]
        )

        page.enter(_entries(), driver_enabled=False)
        page.finish_transition()

        amplifiers = page.board._amplifiers()
        self.assertEqual([label for label, _ in amplifiers], ["DUT"])
        dut = amplifiers[0][1]
        self.assertAlmostEqual(dut.center().x(), rack._CENTER_X, places=6)
        self.assertAlmostEqual(
            dut.left() - rack._CHAIN_LEFT,
            rack._CHAIN_RIGHT - dut.right(),
            places=6,
            msg="没有 Driver 时两侧射频线仍要等长",
        )




if __name__ == "__main__":
    unittest.main()
