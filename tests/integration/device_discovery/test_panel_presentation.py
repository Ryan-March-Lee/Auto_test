"""面板外观：卡片的排版与结果区的状态（不画外框、顶部按钮、返回上一步）。

这些用例走真实的模拟扫描器把结果填进面板，因此放在接线测试里；只依赖表现层端口与
DTO 的纯控件行为仍在 ``tests/ui``。
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest

from infrastructure.discovery.instrument_discovery import SCAN_MODE_SIMULATED, build_scanner
from presentation.qt.device_cards import InstrumentCard, instrument_picture_path
from presentation.qt.device_discovery_panel import (
    BOX_STATE_BUSY,
    BOX_STATE_IDLE,
    BOX_STATE_RESULTS,
)
from presentation.qt.theme import TOKENS, apply_theme, build_stylesheet

from .support import DiscoveryPanelTestCase, registry


class CardPresentationTests(DiscoveryPanelTestCase):
    """卡片外观：圆角、名字在上、图片其次、加粗设备码在下，并随面板宽度换行。"""

    def setUp(self):
        self._previous_stylesheet = self.app.styleSheet()
        apply_theme(self.app)

    def tearDown(self):
        self.app.setStyleSheet(self._previous_stylesheet)

    def test_cards_are_rounded_and_ordered_name_picture_code(self):
        panel = self.make_panel(size=(760, 560))
        self.run_scan(panel)

        card = panel.card_for("SMW200A-1")
        self.assertEqual(card.objectName(), "instrumentCard")
        self.assertIn("border-radius: 12px;", self.app.styleSheet())
        # 自上而下：仪器名第一行、图片第二行、设备码在最下面。
        layout = card.layout()
        rows = [layout.itemAt(index).widget() for index in range(layout.count())]
        self.assertIs(rows[0], card.name_label)
        self.assertLess(rows.index(card.name_label), rows.index(card.image))
        self.assertGreater(rows.index(card.code_label), rows.index(card.image))
        self.assertEqual(rows[-1], card.code_label, "设备码缩写放在卡片最下面")
        # 仪器名居中放大。
        self.assertTrue(card.name_label.alignment() & Qt.AlignHCenter)
        for element in panel.cards():
            self.assertEqual(element.width(), card.width(), "卡片同宽同列")
            self.assertEqual(element.height(), card.height(), "同一行卡片高度一致")

    def test_cards_wrap_into_columns_instead_of_a_long_strip(self):
        panel = self.make_panel(size=(520, 700))
        self.run_scan(panel)

        narrow = len({card.geometry().left() for card in panel.cards()})
        panel.resize(1200, 700)
        self.app.processEvents()
        self.app.processEvents()
        wide = len({card.geometry().left() for card in panel.cards()})

        # 卡片按可用宽度换行：窄面板换行排两列，宽面板排满四列，不排成一条长带子。
        self.assertGreaterEqual(narrow, 2, "窄面板要换行，不能挤成一列长条")
        self.assertEqual(wide, min(4, panel.card_count()))

    def test_shipped_pictures_draw_the_instruments_at_a_similar_size(self):
        """自带的三张仪器图留白各不相同，仪器在卡片上的视觉大小仍要接近。"""
        areas = {}
        for role, model in (
            ("power_supply", "DP832A"),
            ("signal_generator", "SMW200A"),
            ("spectrum_analyzer", "FPL1000"),
        ):
            card = InstrumentCard(
                "型号", "CODE", picture=instrument_picture_path(role, model)
            )
            card.show()
            self.addCleanup(card.close)
            self.app.processEvents()
            drawn = card.image.picture_rect()
            self.assertFalse(drawn.isEmpty(), f"{role} 的图片没有画出来")
            areas[role] = drawn.width() * drawn.height()

        largest = max(areas.values())
        smallest = min(areas.values())
        self.assertLess(
            largest / smallest,
            1.25,
            f"仪器主体在卡片上的视觉面积相差太大（{areas}）",
        )

    def test_selected_card_has_the_checked_property(self):
        panel = self.make_panel()
        self.run_scan(panel)
        card = panel.card_for("4082F-1")

        self.assertFalse(card.property("checked"))
        QTest.mouseClick(card, Qt.LeftButton)
        self.app.processEvents()
        self.assertTrue(card.property("checked"))

    def test_cards_leave_room_while_there_are_no_results(self):
        panel = self.make_panel()

        self.assertEqual(panel.card_count(), 0)
        self.assertGreater(panel.cards_area.height(), 0)

    def test_results_stay_hidden_until_a_scan_starts(self):
        panel = self.make_panel()

        self.assertFalse(panel.cards_area.isVisible())
        # 结果区里不写常驻提示：没有扫描就没有话要说。
        self.assertFalse(panel.status_label.isVisible())
        self.assertFalse(panel.apply_btn.isVisible())
        self.assertFalse(panel.back_btn.isVisible())

    def test_results_stay_in_place_without_a_collapse_control(self):
        """结果区不是抽屉：结果回来就留在那里，没有“收起结果 / 查看结果”那一套。"""
        panel = self.make_panel(size=(1200, 760))
        self.run_scan(panel)

        self.assertTrue(panel.cards_area.isVisible())
        self.assertEqual(panel.back_btn.text(), "返回")
        self.assertFalse(hasattr(panel, "collapse_btn"))
        self.assertFalse(hasattr(panel, "reopen_btn"))


class ScanRowPresentationTests(DiscoveryPanelTestCase):
    """扫描行：标签与输入框比正文大一号，提示行随字号放大但仍留在次要层级。"""

    def setUp(self):
        # 与结果区用例相同：主题只装一次，这里直接取整份样式表，避免别的用例先把样式表
        # 复原成空串（``apply_theme`` 幂等，不会替这里重新装一次）。
        self._previous_stylesheet = self.app.styleSheet()
        self.app.setStyleSheet(build_stylesheet())

    def tearDown(self):
        self.app.setStyleSheet(self._previous_stylesheet)

    def test_labels_and_inputs_are_larger_than_the_note(self):
        panel = self.make_panel(size=(760, 560))

        self.assertEqual(panel.subnet_label.property("role"), "fieldLabel")
        self.assertEqual(panel.mode_label.property("role"), "fieldLabel")
        self.assertEqual(panel.mode_hint.property("role"), "note")
        for widget in (
            panel.subnet_label,
            panel.mode_label,
            panel.subnet_edit,
            panel.mode_combo,
        ):
            self.assertGreaterEqual(
                widget.font().pointSizeF(),
                12.0,
                f"{type(widget).__name__} 要放大",
            )
        self.assertGreater(panel.mode_hint.font().pointSizeF(), 8.5, "提示行也要比原来的小字大")
        self.assertLess(
            panel.mode_hint.font().pointSizeF(),
            panel.subnet_label.font().pointSizeF(),
            "提示仍是次要文字，不能盖过扫描行的标签",
        )


class ResultsBoxStateTests(DiscoveryPanelTestCase):
    """结果区的状态：它不画外框（无边框、无底色），顶部的“查找设备”随状态收放。"""

    panel_size = (760, 560)

    def setUp(self):
        # 主题只装一次（``apply_theme`` 幂等），这里直接取整份样式表：无论哪个用例先跑，
        # 结果区的“不画外框”规则都在。
        self._previous_stylesheet = self.app.styleSheet()
        self.app.setStyleSheet(build_stylesheet())

    def tearDown(self):
        self.app.setStyleSheet(self._previous_stylesheet)

    def _painted_background(self, box) -> str:
        """结果区自己画出来的底色：把它画在一块画布底色的图上，取内边距处的那一点。

        结果区没有边框也没有底色，因此这一点应当仍是画布色——无论框里是空白还是卡片。
        """
        image = QImage(box.size(), QImage.Format_ARGB32)
        image.fill(QColor(TOKENS["canvas"]))
        box.render(image)
        return image.pixelColor(box.width() // 2, box.height() - 6).name()

    def test_the_scan_button_sits_under_the_scan_row_in_a_frameless_results_area(self):
        panel = self.make_panel()
        box = panel.results_box

        # “查找设备”不在扫描行里，而是结果区顶部的落点，紧跟在扫描行下面。
        self.assertTrue(box.isAncestorOf(panel.scan_btn), "查找设备要落在结果区里")
        self.assertEqual(panel.box_state(), BOX_STATE_IDLE)
        # 结果区不画外框：没有边框、没有底色，“查找设备”直接浮在页面背景上。
        self.assertNotIn(
            'QFrame#discoveryResultsBox[filled="true"]',
            self.app.styleSheet(),
            "结果区不应再有浅灰底的落点状态",
        )
        self.assertEqual(self._painted_background(box), TOKENS["canvas"].lower())
        self.assertTrue(panel.scan_btn.isVisible())
        self.assertFalse(panel.cards_area.isVisible())

        # 水平在结果区正中；竖直贴着结果区顶部，和上面的扫描行之间不留一大段空白。
        centre = panel.scan_btn.mapTo(box, panel.scan_btn.rect().center())
        self.assertAlmostEqual(centre.x(), box.rect().center().x(), delta=2)
        button_top = panel.scan_btn.mapTo(panel, panel.scan_btn.rect().topLeft()).y()
        hint_bottom = panel.mode_hint.mapTo(panel, panel.mode_hint.rect().bottomLeft()).y()
        self.assertGreaterEqual(button_top, hint_bottom)
        self.assertLessEqual(
            button_top - hint_bottom, 40, "查找设备要紧跟在扫描行下面，不能隔着一大段空白"
        )

    def test_scanning_takes_the_button_away(self):
        panel = self.make_panel()

        panel.request_scan()
        self.app.processEvents()

        # 点下去就开始加载设备：按钮收走，结果区顶部换成进度说明。
        self.assertEqual(panel.box_state(), BOX_STATE_BUSY)
        self.assertFalse(panel.scan_btn.isVisible())
        self.assertTrue(panel.loading_label.isVisible())
        self.assertIn("正在扫描", panel.status_label.text())

        panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()

        self.assertEqual(panel.box_state(), BOX_STATE_RESULTS)
        self.assertFalse(panel.scan_btn.isVisible())
        self.assertFalse(panel.loading_label.isVisible())
        self.assertTrue(panel.cards_area.isVisible())
        self.assertGreater(panel.card_count(), 0)
        # 卡片装进结果区之后，它依然没有边框与底色。
        self.assertEqual(self._painted_background(panel.results_box), TOKENS["canvas"].lower())

    def test_a_scan_that_finds_nothing_leaves_the_button_in_the_box_again(self):
        def factory(_mode):
            return build_scanner(
                registry=registry(), mode=SCAN_MODE_SIMULATED, responses={}, timeout_s=0.02
            )

        panel = self.make_panel(factory)
        panel.request_scan()
        panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()

        # 一台都没读到：结果区里放不出卡片，顶部的“查找设备”回来好让用户再试一次。
        self.assertEqual(panel.card_count(), 0)
        self.assertEqual(panel.box_state(), BOX_STATE_IDLE)
        self.assertTrue(panel.scan_btn.isVisible())

    def test_the_results_take_the_box_over_from_the_centre(self):
        panel = self.make_panel()
        panel.request_scan()
        panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()

        self.assertEqual(panel.box_state(), BOX_STATE_RESULTS)
        self.assertTrue(panel.cards_area.isVisible())
        self.assertFalse(panel.centre_area.isVisible(), "卡片看得见时顶部按钮让位给卡片")
        self.assertFalse(panel.scan_btn.isVisible())

        # 结果区里的动作只属于有结果的这一次查找：应用 / 清除 / 返回。
        for button in (panel.apply_btn, panel.clear_btn, panel.back_btn):
            self.assertTrue(panel.results_box.isAncestorOf(button))
            self.assertTrue(button.isVisible())
        self.assertEqual(panel.back_btn.text(), "返回")

    def test_back_clears_this_search_and_returns_to_the_scan_button(self):
        panel = self.make_panel()
        panel.request_scan()
        panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()
        panel.set_card_checked(panel.card_headings()[0], True)
        self.assertTrue(panel.devices)

        QTest.mouseClick(panel.back_btn, Qt.LeftButton)
        self.app.processEvents()
        self.app.processEvents()

        # 回到上一步：本次结果与勾选一起清掉，顶部重新是“查找设备”。
        self.assertEqual(panel.box_state(), BOX_STATE_IDLE)
        self.assertEqual(panel.card_count(), 0)
        self.assertEqual(panel.devices, [])
        self.assertFalse(panel.cards_area.isVisible())
        self.assertTrue(panel.centre_area.isVisible())
        self.assertTrue(panel.scan_btn.isVisible())
        self.assertEqual(panel.status_label.text(), "")
        self.assertFalse(panel.apply_btn.isVisible())
        self.assertFalse(panel.apply_btn.isEnabled())

    def test_the_box_keeps_its_size_through_the_whole_scan(self):
        """点“查找设备”前后框一样大：状态与结果数量都不改变框的大小。"""
        panel = self.make_panel()
        panel.resize(900, 720)
        self.app.processEvents()
        box = panel.results_box
        idle = (box.width(), box.height())

        panel.request_scan()
        self.app.processEvents()
        busy = (box.width(), box.height())

        panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()
        results = (box.width(), box.height())

        panel.request_back()
        self.app.processEvents()
        returned = (box.width(), box.height())

        self.assertGreater(idle[1], 0)
        self.assertEqual(busy, idle, "扫描中结果区不能变")
        self.assertEqual(results, idle, "结果回来后结果区不能变")
        self.assertEqual(returned, idle, "返回后结果区不能变")

    def test_the_box_has_room_for_two_rows_of_cards(self):
        """结果区的固定高度按“两行卡片 + 里面的按钮与状态行”算：场地上 5 台设备不用滚动。"""
        panel = self.make_panel()
        panel.request_scan()
        panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()

        card = panel.cards()[0]
        self.assertGreaterEqual(
            panel.cards_area.height(), 2 * card.height(), "结果区里至少放得下两行卡片"
        )

    def test_clearing_the_results_puts_the_scan_button_back(self):
        panel = self.make_panel()
        panel.request_scan()
        panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()

        panel.clear_results()
        self.app.processEvents()

        # 重新进入页面时回到“还没查找过”的样子：顶部的按钮回来了，外框依旧不画。
        self.assertEqual(panel.box_state(), BOX_STATE_IDLE)
        self.assertTrue(panel.scan_btn.isVisible())
        self.assertFalse(panel.cards_area.isVisible())
        self.assertEqual(
            self._painted_background(panel.results_box), TOKENS["canvas"].lower()
        )
