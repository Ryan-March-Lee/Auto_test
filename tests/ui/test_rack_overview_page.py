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
        self.assertEqual(emitted, [], "淡出动画结束前还不能切页")
        page.finish_transition()
        self.assertEqual(emitted, [True])

    def test_next_fades_out_everything_but_the_power_tiles(self):
        """点“下一步”先淡出其余内容：卡片位置不变，淡出结束才切页。"""
        page = self._page()
        page.finish_transition()
        supplies = page.power_supply_ids()
        for index, supply in enumerate(supplies):
            page.place_supply(supply, index)

        rects = page.power_tile_rects_global()
        self.assertEqual(set(rects), set(supplies))
        for rect in rects.values():
            self.assertFalse(rect.isEmpty())

        page.next_btn.click()
        self.assertTrue(page.next_transition_running())

        page._set_next_progress(0.5)
        self.assertAlmostEqual(page.board._finish_progress, 0.5)
        self.assertAlmostEqual(page.header_opacity(), 0.5)
        self.assertEqual(page.power_tile_rects_global(), rects, "卡片不能挪位")

        page.finish_transition()
        self.assertFalse(page.next_transition_running())

    def test_back_button_label_is_previous_step(self):
        page = self._page()
        self.assertEqual(page.back_btn.text(), "上一步")

    def test_header_shows_only_the_centred_title(self):
        """标题行只剩居中的“仪器排布”，不再显示拖放提示的灰字。"""
        page = self._page()
        header = page.title_label.parentWidget()

        self.assertEqual(
            header.findChildren(QLabel),
            [page.title_label],
            "标题行里除标题外不能再有提示文字",
        )
        self.app.processEvents()
        self.assertAlmostEqual(
            page.title_label.geometry().center().x(),
            header.rect().center().x(),
            delta=1,
            msg="“仪器排布”四个字要水平居中",
        )

    def test_the_power_block_sits_close_above_the_chain(self):
        """托盘、九宫格与箭头整体靠下，箭头贴近底部链路但不压上去。"""
        arrow_bottom = rack._ARROW_TOP + rack._ARROW_HEIGHT
        gap = rack._DUT_RECT.top() - arrow_bottom

        self.assertGreater(gap, 0.0, "箭头不能压到下面的链路")
        self.assertLessEqual(gap, 24.0, "箭头要靠近底部链路")
        self.assertGreater(rack._TRAY_TOP, 0.0, "整块已经离开画布顶边")

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
        # 进入排布页的卡片保持选中态（白底、彩色原图），动画第一帧不再换一次外观。
        # 电源卡的设备码交给页面单独绘制，位图里不再画一遍。
        expected = render_card_front(
            replace(card.data, checked=True),
            width=card.width(),
            height=card.height(),
            include_code=False,
        )
        image = entries[0].front.toImage()
        self.assertEqual(image, expected.toImage())
        self.assertEqual(
            entries[0].code_row,
            card_row_plan(
                replace(card.data, checked=True), card.width(), card.height()
            ).code,
            "设备码的位置要按卡片实际尺寸算，动画起点才能与真实卡片重合",
        )
        self.assertEqual(
            image.pixelColor(12, CARD_HEIGHT // 2).name(),
            TOKENS["surface"].lower(),
            "进入排布页的卡片应是选中态的白底",
        )

    def test_entries_from_cards_render_at_the_card_width(self):
        """结果区把电源卡收窄后，动画第一帧仍按真实尺寸出图，字号不会先缩一次。"""
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
        card.setFixedWidth(CARD_MIN_WIDTH)
        card.setFixedHeight(CARD_HEIGHT)

        entries = RackOverviewPage.entries_from_cards([card])

        front = entries[0].front
        ratio = float(front.devicePixelRatio()) or 1.0
        self.assertAlmostEqual(front.width() / ratio, CARD_MIN_WIDTH, places=3)
        self.assertAlmostEqual(front.height() / ratio, CARD_HEIGHT, places=3)
        expected = render_card_front(
            replace(card.data, checked=True),
            width=CARD_MIN_WIDTH,
            height=CARD_HEIGHT,
            include_code=False,
        )
        self.assertEqual(front.toImage(), expected.toImage())

    def test_offscreen_start_rect_is_moved_back_inside_the_canvas(self):
        """卡片真的超出画布控件时整张移回可见区域，否则卡片顶部会被裁平。"""
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

    def test_the_board_covers_the_content_column(self):
        """画布铺满整列内容，设计画布只占其中一块：卡片才画得进上方那块留白。"""
        page = self._page()

        self.assertEqual(page.board.geometry(), page._content.rect())
        self.assertGreater(
            page.board._viewport.top(),
            0,
            "设计画布上方必须留出可以画卡片的区域",
        )

    def test_a_card_above_the_design_canvas_keeps_its_screen_position(self):
        """上一页卡片在标题行下方、设计画布上方时也不能被挪动。

        这是入场第一帧与返回最后一帧的位置：只要这里被“移回可见区域”，动画开头和
        结尾就会各跳一下（选择页的卡片比排布页的设计画布更靠上）。
        """
        page = self._page()
        board = page.board
        scale, offset = board._transform()
        start = QRect(
            board.mapToGlobal(QPoint(100, 10)), QSize(CARD_WIDTH, CARD_HEIGHT)
        )

        design = board._design_rect_from_global(start)

        self.assertAlmostEqual(design.left(), (100 - offset.x()) / scale, places=3)
        self.assertAlmostEqual(design.top(), (10 - offset.y()) / scale, places=3)
        self.assertLess(
            design.top(),
            0,
            "这张卡在起点上就位于设计画布上方的留白里",
        )

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

    def test_power_morph_uses_the_card_border_instead_of_an_extra_outline(self):
        """缩小动画只贴卡片自己的正面位图，不再沿轮廓补一圈深色粗边。"""
        page = self._page()
        front = QPixmap(CARD_WIDTH, CARD_HEIGHT)
        front.fill(QColor("#FFFFFF"))
        painter = QPainter(front)
        try:
            painter.setPen(QColor("#123456"))
            painter.drawRect(1, 1, CARD_WIDTH - 3, CARD_HEIGHT - 3)
        finally:
            painter.end()
        entry = RackCardEntry(
            "power_supply",
            "DP8B1519",
            front,
            QRect(0, 0, CARD_WIDTH, CARD_HEIGHT),
        )
        rect = QRectF(0.0, 0.0, float(CARD_WIDTH), float(CARD_HEIGHT))
        image = QImage(CARD_WIDTH, CARD_HEIGHT, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        try:
            page.board._paint_power_morph(painter, entry, rect, rect, 0.0)
        finally:
            painter.end()

        middle = CARD_HEIGHT // 2
        self.assertEqual(
            image.pixelColor(1, middle).name(),
            "#123456",
            "卡片左边应是位图自带的边框，而不是覆盖上去的描边",
        )
        self.assertNotEqual(
            image.pixelColor(0, middle).name(),
            QColor(TOKENS["text"]).name(),
            "卡片轮廓外不应再有一圈深色粗描边",
        )

    def test_morph_code_font_matches_the_card_at_the_start_and_the_tile_at_the_end(self):
        """设备码只在卡片字号与卡片字号之间过渡：两端都没有突然变大变小。"""
        tile = card_code_font()
        scale = 0.8

        start = rack._morph_code_font(tile, card_code_font(), scale, 0.0)
        self.assertAlmostEqual(
            start.pointSizeF() * scale,
            tile.pointSizeF(),
            places=6,
            msg="起点要等于真实卡片上的字号",
        )
        end = rack._morph_code_font(tile, card_code_font(), scale, 1.0)
        self.assertAlmostEqual(
            end.pointSizeF(), tile.pointSizeF(), places=6, msg="终点要等于静止卡片的字号"
        )

    def test_reverse_morph_code_font_goes_back_to_the_card_size(self):
        """返回动画从卡片字号出发，结束时又是结果区卡片上的字号。"""
        tile = card_code_font()
        scale = 0.8

        start = rack._morph_code_font(
            tile, card_code_font(), scale, 0.0, reverse=True
        )
        self.assertAlmostEqual(start.pointSizeF(), tile.pointSizeF(), places=6)
        end = rack._morph_code_font(
            tile, card_code_font(), scale, 1.0, reverse=True
        )
        self.assertAlmostEqual(
            end.pointSizeF() * scale,
            card_code_font().pointSizeF(),
            places=6,
            msg="返回动画结束时必须回到卡片字号",
        )

    def test_morph_code_rect_ends_at_the_static_tile_text_area(self):
        tile = QRectF(10.0, 20.0, 170.0, 80.0)
        row = QRectF(9.0, 9.0, 172.0, 44.0)

        text = rack._morph_code_rect(tile, row, 1.0, 1.0)
        tile_text = tile.adjusted(
            rack._TILE_TEXT_INSET, 0.0, -rack._TILE_TEXT_INSET, 0.0
        )

        self.assertEqual(
            text,
            tile_text,
            "动画终点必须落在静止卡片的文字区域上",
        )
        self.assertEqual(
            rack._morph_code_rect(tile, row, 1.0, 0.0, reverse=True),
            tile_text,
            "返回动画的起点同样落在静止卡片的文字区域上",
        )

    def test_morph_end_frame_matches_the_static_tile(self):
        """动画最后一帧与静止卡片逐像素一致，交回静止画面时不会再跳一下。"""
        page = self._page()
        board = page.board
        rect = QRectF(10.0, 20.0, 170.0, 80.0)
        entry = RackCardEntry(
            "power_supply",
            "DP8B1519",
            _front(),
            QRect(0, 0, CARD_WIDTH, CARD_HEIGHT),
            code_row=QRectF(9.0, 9.0, 172.0, 44.0),
        )
        size = QSize(200, 120)

        def paint(callback) -> QImage:
            image = QImage(size, QImage.Format.Format_ARGB32)
            image.fill(QColor(0, 0, 0, 0))
            painter = QPainter(image)
            try:
                callback(painter)
            finally:
                painter.end()
            return image

        morph = paint(
            lambda painter: board._paint_power_morph(
                painter, entry, rect, rect, 1.0
            )
        )
        static = paint(
            lambda painter: board._paint_tile(
                painter, rect, "DP8B1519", card_code_font()
            )
        )

        self.assertEqual(morph, static, "缩小动画的终点必须与托盘卡片完全一致")

    def test_tile_border_is_the_card_border_not_a_dark_outline(self):
        """动画结束后电源卡片也只留卡片原来的 1px 浅边，不把深色描边加回来。"""
        page = self._page()
        rect = QRectF(0.0, 0.0, 170.0, 80.0)
        image = QImage(170, 80, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        try:
            page.board._paint_tile(painter, rect, "DP8B1519", card_code_font())
        finally:
            painter.end()

        self.assertEqual(
            image.pixelColor(85, 0).name(),
            QColor(TOKENS["border"]).name(),
            "卡片上边应是与卡片一致的 1px 浅色边",
        )

    # ---- 链路几何与功放标签 -----------------------------------------------

    def test_empty_slots_are_the_size_of_the_shrunk_power_tile(self):
        """九宫格的虚线框和托盘里的电源卡片一样大，电源放进去不会换尺寸。"""
        page = self._page()
        page.finish_transition()
        supply = page.power_supply_ids()[0]
        slot = page.board._slot_rects()[0]
        tile = page.board._tray_layout()[supply]

        self.assertEqual(slot.size(), tile.size())
        self.assertAlmostEqual(slot.height(), rack._TILE_HEIGHT, places=6)

    def test_the_three_rf_cables_are_equally_long(self):
        """信号源—Driver、Driver—DUT、DUT—频谱仪三段线等长，四件仪器大致等距。"""
        left = rack._DRIVER_RECT.left() - rack._CHAIN_LEFT
        middle = rack._DUT_RECT.left() - rack._DRIVER_RECT.right()
        right = rack._CHAIN_RIGHT - rack._DUT_RECT.right()

        self.assertAlmostEqual(left, middle, places=6)
        self.assertAlmostEqual(middle, right, places=6)
        self.assertGreater(
            middle, 120.0, "中间要给射频线留出完整一段，不能只剩两个接头"
        )

    def test_amplifier_triangles_are_not_stretched_sideways(self):
        self.assertLessEqual(
            rack._AMP_WIDTH,
            rack._AMP_HEIGHT + 4.0,
            "三角宽高应接近，不能横向拉长",
        )

    def test_amplifier_label_is_centred_in_the_triangle_in_times_new_roman(self):
        rect = QRectF(100.0, 200.0, rack._AMP_WIDTH, rack._AMP_HEIGHT)
        text = rack._amplifier_text_rect(rect)
        font = rack._amplifier_font()

        self.assertEqual(font.families()[0], "Times New Roman")
        self.assertTrue(font.bold())
        self.assertAlmostEqual(
            text.center().x(),
            rect.left() + rect.width() / 3.0,
            places=6,
            msg="文字中心应落在三角形重心的横坐标上",
        )
        self.assertAlmostEqual(text.center().y(), rect.center().y(), places=6)
        self.assertGreaterEqual(text.left(), rect.left())
        self.assertLessEqual(text.right(), rect.left() + rect.width() * 2.0 / 3.0)

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

    def test_drag_preview_is_smaller_than_the_resting_tile(self):
        """拿起电源时跟手位图缩小一点，而不是比静止卡片更大。"""
        page = self._page()
        page.finish_transition()
        board = page.board
        supply = page.power_supply_ids()[0]
        rect = board._tile_rect(supply)
        scale, _offset = board._transform()

        preview = board._render_tile(supply, rect)
        ratio = float(preview.devicePixelRatio()) or 1.0
        logical_width = preview.width() / ratio
        logical_height = preview.height() / ratio

        self.assertAlmostEqual(
            logical_width,
            rect.width() * scale * rack._DRAG_PREVIEW_SCALE,
            delta=1.0,
        )
        self.assertAlmostEqual(
            logical_height,
            rect.height() * scale * rack._DRAG_PREVIEW_SCALE,
            delta=1.0,
        )
        self.assertLess(logical_width, rect.width() * scale)
        self.assertLess(rack._DRAG_PREVIEW_SCALE, 1.0)

    def test_a_placed_supply_keeps_the_tray_code_size(self):
        """电源放进格位后设备码仍用托盘的字号，不会明显变小。"""
        page = self._page()
        page.finish_transition()
        board = page.board
        supply = page.power_supply_ids()[0]
        page.place_supply(supply, 0)

        sizes = []
        original = board._paint_tile

        def record(painter, rect, text, font):
            sizes.append(font.pointSizeF())
            original(painter, rect, text, font)

        board._paint_tile = record
        try:
            page.grab()
        finally:
            del board._paint_tile

        self.assertTrue(sizes, "摆好电源后至少要画出格位与托盘的卡片")
        self.assertEqual(set(sizes), {card_code_font().pointSizeF()})


if __name__ == "__main__":
    unittest.main()
