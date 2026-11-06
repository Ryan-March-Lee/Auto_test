"""仪器排布页：入场动画、九宫格拖放与“下一步”门禁。

本组用例是纯表现层测试：卡片正面用一份纯色位图代替真实照片，页面不读配置、不连接
仪器。绘制用例只验证页面能完成静态与动画帧的绘制，不比较像素内容——外观由
``theme`` 的令牌决定，行为契约在这里锁定。
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import (
    QMimeData,
    QPoint,
    QPointF,
    QRect,
    QSize,
    Qt,
    QVariantAnimation,
)
from PySide6.QtGui import QColor, QDropEvent, QImage, QPixmap
from PySide6.QtWidgets import QApplication

from presentation.qt import rack_overview_page as rack
from presentation.qt.device_cards import CARD_HEIGHT, CARD_WIDTH, InstrumentCardData
from presentation.qt.device_cards import InstrumentCard
from presentation.qt.rack_overview_page import RackCardEntry, RackOverviewPage


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
        self.assertFalse(page.next_btn.isEnabled(), "电源还没摆好，不能进入参数页")

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
        self.assertEqual(emitted, [True])

    def test_back_button_label_is_previous_step(self):
        page = self._page()
        self.assertEqual(page.back_btn.text(), "上一步")

    def test_header_stays_hidden_while_the_entry_animation_runs(self):
        page = self._page()
        self.assertEqual(page.header_opacity(), 0.0, "入场开始时不显示标题行")

        page.board._animation.stop()
        page.board._animation.setCurrentTime(650)
        self.app.processEvents()
        self.assertEqual(
            page.header_opacity(), 0.0, "卡片还在飞入时标题行不能出现"
        )

    def test_header_fades_in_only_after_the_entry_animation_finishes(self):
        page = self._page()
        page.board._animation.stop()
        page.board._animation.setCurrentTime(page.board._animation.duration())
        self.app.processEvents()

        self.assertEqual(
            page._header_animation.state(),
            QVariantAnimation.Running,
            "卡片落位后标题行才开始淡入",
        )
        page._header_animation.setCurrentTime(rack._HEADER_FADE_IN_MS)
        self.assertEqual(page.header_opacity(), 1.0, "淡入结束标题行完全显示")

    def test_skipping_the_entry_animation_shows_the_header_immediately(self):
        page = self._page()
        page.finish_transition()
        self.assertEqual(page.header_opacity(), 1.0)

    def test_header_disappears_first_during_the_back_transition(self):
        page = self._page()
        page.finish_transition()
        self.assertEqual(page.header_opacity(), 1.0)

        page.request_back()

        self.assertEqual(
            page.header_opacity(), 0.0, "返回动画一开始标题行就已经消失"
        )

        page.finish_transition()
        self.assertEqual(page.header_opacity(), 0.0, "返回完成后标题行不能闪回来")

    def test_back_before_the_entry_finishes_does_not_flash_the_header(self):
        page = self._page()
        page.board._animation.stop()
        page.board._animation.setCurrentTime(300)
        self.app.processEvents()
        self.assertEqual(page.header_opacity(), 0.0, "入场中途标题行还没出现")

        page.request_back()

        self.assertEqual(
            page.header_opacity(), 0.0, "入场没播完就返回时标题行不能先闪出来"
        )

    def test_back_button_plays_the_reverse_transition_before_emitting(self):
        page = self._page()
        emitted = []
        page.back_requested.connect(lambda: emitted.append(True))
        page.finish_transition()
        page.place_supply(page.power_supply_ids()[0], 0)

        page.back_btn.click()

        self.assertTrue(page.transition_running(), "返回要先播反向动画")
        self.assertEqual(emitted, [], "动画播完前不能切页")
        self.assertFalse(page.next_btn.isEnabled(), "返回过程中不能再进入下一步")

        page.finish_transition()

        self.assertFalse(page.transition_running())
        self.assertEqual(emitted, [True])

    def test_back_button_ignores_repeated_clicks_while_returning(self):
        page = self._page()
        emitted = []
        page.back_requested.connect(lambda: emitted.append(True))
        page.finish_transition()

        page.back_btn.click()
        page.back_btn.click()
        page.finish_transition()

        self.assertEqual(emitted, [True], "返回动画只播一次、只切一次页")

    def test_back_animation_starts_at_the_rack_and_ends_at_the_selection_layout(self):
        page = self._page()
        page.finish_transition()

        page.request_back()

        board = page.board
        self.assertEqual(board._direction, -1)
        self.assertEqual(
            board._progress,
            1.0,
            "返回动画的起点必须是排布页布局（入场进度 1），不能先跳到选择页布局",
        )
        self.assertEqual(board._animation.startValue(), 1.0)
        self.assertEqual(board._animation.endValue(), 0.0)

    def test_back_first_frame_is_exactly_the_layout_on_screen(self):
        """点下“上一步”的第一帧不能跳：仍要看到点击前的排布画面。"""
        page = self._page()
        page.finish_transition()
        page.place_supply(page.power_supply_ids()[0], 0)
        self.app.processEvents()
        before = page.board.grab().toImage()

        page.request_back()

        after = page.board.grab().toImage()
        self.assertLess(
            _image_difference(before, after),
            0.002,
            "返回动画第一帧必须与点击前的版面一致（只允许抗锯齿级差异）",
        )

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

    def test_drop_event_places_the_supply_in_the_target_slot(self):
        page = self._page()
        page.finish_transition()
        board = page.board
        scale, offset = board._transform()
        slot = board._slot_rects()[0]
        point = QPointF(
            offset.x() + slot.center().x() * scale,
            offset.y() + slot.center().y() * scale,
        )
        mime = QMimeData()
        mime.setData(rack.RACK_SUPPLY_MIME, "DP8B1519".encode("utf-8"))
        event = QDropEvent(point, Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)

        board.dropEvent(event)

        self.assertEqual(page.slot_of("DP8B1519"), 0)

    # ---- 卡片入口与绘制 ---------------------------------------------------

    def test_entries_from_cards_uses_role_code_and_card_rect(self):
        card = InstrumentCard(
            InstrumentCardData(
                "DP832A（DP8B1519）",
                code="DP8B1519",
                model="DP832A",
                role="电源",
                role_key="power_supply",
                checkable=True,
            )
        )
        card.resize(CARD_WIDTH, CARD_HEIGHT)

        entries = RackOverviewPage.entries_from_cards([card])

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].role_key, "power_supply")
        self.assertEqual(entries[0].code, "DP8B1519")
        self.assertEqual(entries[0].start_rect_global.size(), card.size())
        self.assertFalse(entries[0].front.isNull())

    def test_offscreen_start_rect_is_moved_back_inside_the_canvas(self):
        """上一页卡片的位置高于板子时整张移回可见区域，否则卡片顶部会被裁平。"""
        page = self._page()
        board = page.board
        scale, offset = board._transform()
        above = QRect(
            board.mapToGlobal(QPoint(0, -40)), QSize(CARD_WIDTH, CARD_HEIGHT)
        )

        design = board._design_rect_from_global(above)

        self.assertGreaterEqual(
            design.top(),
            -offset.y() / scale,
            "起点要移回板子的可见区域",
        )
        self.assertAlmostEqual(
            design.height(),
            CARD_HEIGHT / scale,
            places=3,
            msg="只移动，不缩小卡片",
        )

    def test_start_rect_inside_the_board_is_left_where_it_is(self):
        page = self._page()
        board = page.board
        scale, offset = board._transform()
        inside = QRect(
            board.mapToGlobal(QPoint(100, 120)), QSize(CARD_WIDTH, CARD_HEIGHT)
        )

        design = board._design_rect_from_global(inside)

        self.assertAlmostEqual(design.left(), (100 - offset.x()) / scale, places=3)
        self.assertAlmostEqual(design.top(), (120 - offset.y()) / scale, places=3)

    def test_board_paints_static_and_transition_frames(self):
        page = self._page()
        page.finish_transition()
        self.assertFalse(page.grab().isNull(), "静态布局必须能画出来")

        page.enter(_entries())
        # 直接取一帧动画中间态，确认变形与滑入的绘制路径可用。
        page.board._animation.stop()
        page.board._running = True
        page.board._progress = 0.4
        page.board.update()
        self.app.processEvents()
        self.assertFalse(page.grab().isNull(), "动画中间帧必须能画出来")

        # 反向动画的中间帧同样要能画出来（卡片从托盘/格位飞回选择页）。
        page.finish_transition()
        page.request_back()
        page.board._animation.stop()
        page.board._running = True
        page.board._progress = 0.5
        page.board.update()
        self.app.processEvents()
        self.assertFalse(page.grab().isNull(), "返回动画中间帧必须能画出来")


if __name__ == "__main__":
    unittest.main()
