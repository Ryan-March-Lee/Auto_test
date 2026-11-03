"""面板外观：卡片的排版与结果区的状态（不画外框、顶部按钮、返回上一步）。

这些用例走真实的模拟扫描器把结果填进面板，因此放在接线测试里；只依赖表现层端口与
DTO 的纯控件行为仍在 ``tests/ui``。
"""

from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtTest import QTest

from infrastructure.discovery.instrument_discovery import SCAN_MODE_SIMULATED, build_scanner
from presentation.qt.device_cards import (
    CARD_HEIGHT,
    CARD_WIDTH,
    InstrumentCard,
    InstrumentCardData,
    instrument_picture_path,
)
from presentation.qt.device_discovery_panel import (
    BOX_STATE_BUSY,
    BOX_STATE_IDLE,
    BOX_STATE_RESULTS,
)
from presentation.qt.theme import TOKENS, build_stylesheet

from .support import DiscoveryPanelTestCase, registry, six_device_factory


class CardPresentationTests(DiscoveryPanelTestCase):
    """卡片外观：圆角、设备码在上、图片其次、型号在下，并随面板宽度换行。"""

    def setUp(self):
        # ``apply_theme`` 是幂等的（主题只装一次）；别的用例可能已经把它复原成空串，
        # 这里直接取整份样式表，用例的通过条件就不依赖执行顺序。
        self._previous_stylesheet = self.app.styleSheet()
        self.app.setStyleSheet(build_stylesheet())

    def tearDown(self):
        self.app.setStyleSheet(self._previous_stylesheet)

    def test_cards_are_rounded_and_ordered_code_picture_caption_address(self):
        panel = self.make_panel(size=(760, 560))
        self.run_scan(panel)

        card = panel.card_for("SMW200A（105462）")
        self.assertEqual(card.objectName(), "instrumentCard")
        stylesheet = self.app.styleSheet()
        card_rule = stylesheet.split("QFrame#instrumentCard {", 1)[1].split("}", 1)[0]
        self.assertIn("border-radius: 8px;", card_rule, "卡片四角只做轻微圆角")
        # 自上而下：设备码第一行、图片第二行，型号·角色在图片下面，最下面是 IP 地址。
        layout = card.layout()
        rows = [layout.itemAt(index).widget() for index in range(layout.count())]
        self.assertIs(rows[0], card.code_label, "设备码缩写放在卡片第一行")
        self.assertLess(rows.index(card.code_label), rows.index(card.image))
        self.assertGreater(rows.index(card.name_label), rows.index(card.image))
        self.assertGreater(rows.index(card.address_label), rows.index(card.name_label))
        self.assertEqual(rows[-1], card.address_label, "IP 地址放在卡片最下面")
        # 设备码第一行也要居中，型号·角色与地址同样居中。
        self.assertTrue(card.code_label.alignment() & Qt.AlignHCenter)
        self.assertTrue(card.name_label.alignment() & Qt.AlignHCenter)
        self.assertTrue(card.address_label.alignment() & Qt.AlignHCenter)
        # 型号后面接角色，最下面一行是本次扫描到的 IP。
        self.assertEqual(card.name_label.text(), "SMW200A·信号发生器")
        self.assertEqual(card.address_label.text(), "192.168.1.201")
        for element in panel.cards():
            self.assertEqual(element.width(), card.width(), "卡片同宽同列")
            self.assertEqual(element.height(), card.height(), "同一行卡片高度一致")

    def test_cards_and_the_animation_read_the_same_data_snapshot(self):
        """卡片上写的、和动画要读的，是同一份数据：动画不必问卡片控件要文字。"""
        panel = self.make_panel()
        self.run_scan(panel)

        heading = "SMW200A（105462）"
        data = panel.card_data(heading)
        card = panel.card_for(heading)
        self.assertEqual(data.heading, heading)
        self.assertEqual(data.code, card.code_label.text())
        self.assertEqual(data.caption(), card.name_label.text())
        self.assertEqual(data.address, card.address_label.text())
        self.assertEqual(data.status, card.status_label.text())
        self.assertFalse(data.checked)

        # 用户勾选后快照跟着走：发牌那一刻读到的是卡片上真正的选中状态。
        panel.set_card_checked(heading, True)
        self.assertTrue(panel.card_data(heading).checked)
        self.assertTrue(panel.card_for(heading).is_checked())
        self.assertEqual(panel.card_data(heading).status, "", "用户自己选的卡片不再写预选说明")
        self.assertEqual(
            panel.card_data(heading),
            panel.card_for(heading).data,
            "动画读的快照就是卡片此刻显示的内容",
        )

        with self.assertRaises(KeyError):
            panel.card_data("没有这台设备")

    def test_the_snapshot_carries_the_preselection_status(self):
        """配置预选写下的说明也进同一份快照：动画不必另读控件文字。"""
        panel = self.make_panel(
            bindings={
                "signal_generator": {
                    "full_code": "105462",
                    "device_key": "rohde&schwarz:105462",
                }
            }
        )
        self.run_scan(panel)

        data = panel.card_data("SMW200A（105462）")
        self.assertTrue(data.checked)
        self.assertEqual(data.status, "已按配置预选")
        self.assertFalse(panel.card_data("DP832A（DP8B214901519）").checked)

        # 用户取消后预选说明随之消失，快照仍与卡片一致。
        panel.set_card_checked("SMW200A（105462）", False)
        data = panel.card_data("SMW200A（105462）")
        self.assertFalse(data.checked)
        self.assertEqual(data.status, "")
        self.assertEqual(data, panel.card_for("SMW200A（105462）").data)

    def test_cards_wrap_into_columns_instead_of_a_long_strip(self):
        panel = self.make_panel(six_device_factory, size=(520, 700))
        self.run_scan(panel)

        narrow = len({card.geometry().left() for card in panel.cards()})
        panel.resize(1200, 700)
        self.app.processEvents()
        self.app.processEvents()
        wide = len({card.geometry().left() for card in panel.cards()})

        # 卡片按可用宽度换行：窄面板换行排两列，宽面板一行排满六列，不排成一条长带子。
        self.assertGreaterEqual(narrow, 2, "窄面板要换行，不能挤成一列长条")
        self.assertEqual(panel.card_count(), 6)
        self.assertEqual(wide, 6, "默认窗口宽度下 6 台设备排成一行，不在 4 台处换行")

    def test_cards_are_centred_whatever_the_count(self):
        """卡片整组居中：不论几张，每一行（含不满的最后一行）都落在卡片区中线。"""
        panel = self.make_panel(size=(660, 560))
        self.run_scan(panel)
        host = panel.cards_host
        self.assertGreaterEqual(panel.card_count(), 2)

        rows: dict[int, list] = {}
        for card in panel.cards():
            rows.setdefault(card.geometry().top(), []).append(card)
        # 窄面板上这批设备排成 3 + 1：最后一行只有一张，正好验证“不满一行也居中”。
        self.assertGreaterEqual(len(rows), 2, "窄面板要多行排布，才验证得到不满一行的情况")
        self.assertEqual(min(len(cards) for cards in rows.values()), 1)
        for cards in rows.values():
            left = min(card.geometry().left() for card in cards)
            right = max(card.geometry().right() for card in cards)
            self.assertAlmostEqual(
                (left + right) / 2, host.width() / 2, delta=3, msg="每一行都居中"
            )

    def test_shipped_pictures_draw_the_instruments_at_a_similar_size(self):
        """自带的三张仪器图留白各不相同，仪器在卡片上的视觉大小仍要接近。"""
        areas = {}
        for role, model in (
            ("power_supply", "DP832A"),
            ("signal_generator", "SMW200A"),
            ("spectrum_analyzer", "FPL1000"),
        ):
            card = InstrumentCard(
                InstrumentCardData(
                    "型号", code="CODE", picture_path=instrument_picture_path(role, model)
                )
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
        card = panel.card_for("4082F（QZNI000866）")

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
        # 悬停时不该弹出说明框：这张卡片的用法由扫描行与提示行说明。
        self.assertEqual(panel.scan_btn.toolTip(), "")

        # 水平在结果区正中；竖直方向给结果卡片让位：按钮整体落在“结果卡片”那一格
        # 的下方（页面还会在结果显示时把标题/扫描行让出来，页面级位置由连接页测试锁定）。
        from presentation.qt.scan_card import SCAN_CARD_HEADROOM

        centre = panel.scan_btn.mapTo(box, panel.scan_btn.rect().center())
        self.assertAlmostEqual(centre.x(), box.rect().center().x(), delta=2)
        button_top = panel.scan_btn.mapTo(panel, panel.scan_btn.rect().topLeft()).y()
        hint_bottom = panel.mode_hint.mapTo(panel, panel.mode_hint.rect().bottomLeft()).y()
        self.assertGreaterEqual(button_top, hint_bottom)
        box_top = box.mapTo(panel, box.rect().topLeft()).y()
        self.assertGreater(
            button_top - box_top,
            SCAN_CARD_HEADROOM,
            "查找设备要在结果区里让出引导高度，不能贴着结果区顶边",
        )

    def test_the_scan_card_keeps_room_above_it_for_its_decorations(self):
        """卡片上方要留出一圈：投影伸出卡片的那一段不能被结果区上沿裁成平的。"""
        from presentation.qt.scan_card import SCAN_CARD_HEADROOM

        panel = self.make_panel()
        top = panel.scan_btn.mapTo(panel.centre_area, panel.scan_btn.rect().topLeft()).y()
        self.assertGreaterEqual(
            top,
            SCAN_CARD_HEADROOM,
            "卡片上方要留出投影的位置，否则结果区上沿会把它裁出一条硬边",
        )

    def test_clicking_scan_does_not_make_the_card_drop_down(self):
        """等待动画的牌堆从按钮原来的位置开始转，点下去卡片不能先往下掉一截。"""
        from presentation.qt.device_cards import CARD_HEIGHT, CARD_WIDTH

        panel = self.make_panel()
        button = panel.scan_btn.mapTo(panel, panel.scan_btn.rect().topLeft())
        size = (panel.width(), panel.height())

        panel.request_scan()
        self.app.processEvents()

        self.assertTrue(panel.wait_stack.isVisible())
        self.assertEqual(
            (panel.width(), panel.height()), size, "换成等待动画不能把面板撑大或撑小"
        )
        stage = panel.wait_stack.mapTo(panel, panel.wait_stack.rect().topLeft())
        deck_left = stage.x() + (panel.wait_stack.width() - CARD_WIDTH) // 2
        deck_top = stage.y() + (panel.wait_stack.height() - CARD_HEIGHT) // 2
        self.assertEqual(deck_top, button.y(), "牌堆上沿要与按钮上沿重合")
        self.assertEqual(deck_left, button.x(), "牌堆左沿要与按钮左沿重合")

    def test_scanning_takes_the_button_away(self):
        panel = self.make_panel()

        panel.request_scan()
        self.app.processEvents()

        # 点下去就开始加载设备：按钮收走，同一位置换成那一叠等待动画；进度写进状态行。
        self.assertEqual(panel.box_state(), BOX_STATE_BUSY)
        self.assertFalse(panel.scan_btn.isVisible())
        self.assertTrue(panel.wait_stack.isVisible())
        self.assertIn("正在查找设备", panel.status_label.text())

        panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()

        # 后台扫描已经结束，但这一轮等待动画还没走完：结果先挂起，牌堆继续转。
        self.assertEqual(panel.box_state(), BOX_STATE_BUSY)
        self.assertTrue(panel.wait_stack.isVisible())
        self.assertFalse(panel.cards_area.isVisible())
        self.assertEqual(panel.card_count(), 0)

        # 一整轮播完（所有卡叠回原样）才进入下一步：结果换进结果区。
        self.finish_scan(panel)

        self.assertEqual(panel.box_state(), BOX_STATE_RESULTS)
        self.assertFalse(panel.scan_btn.isVisible())
        self.assertFalse(panel.wait_stack.isVisible())
        self.assertTrue(panel.cards_area.isVisible())
        self.assertGreater(panel.card_count(), 0)
        # 卡片装进结果区之后，它依然没有边框与底色。
        self.assertEqual(self._painted_background(panel.results_box), TOKENS["canvas"].lower())

    def test_the_results_are_dealt_face_down_and_then_flipped_one_by_one(self):
        """结果不是直接出现：先背面朝上发到卡片位，全部发完再逐张翻开。"""
        panel = self.make_panel()
        panel.request_scan()
        panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()
        panel.wait_stack.finish_cycle()
        self.app.processEvents()
        self.app.processEvents()

        self.assertEqual(panel.box_state(), BOX_STATE_RESULTS)
        self.assertTrue(panel.cards_area.isVisible())
        self.assertTrue(panel.deal_layer.is_running(), "结果卡片应由发牌动画带出来")
        self.assertFalse(
            panel.status_label.isVisible(), "牌还背面朝上时先不要写出结果统计"
        )
        diagnostics = panel.deal_layer.diagnostics()
        self.assertIn(diagnostics["event"], ("start", "first_paint"))
        self.assertIn(
            diagnostics["phase"], ("dealing", "settling", "flipping")
        )
        self.assertGreater(
            diagnostics["paint_frames"], 0, "发牌层启动后必须真的绘制出来"
        )
        self.assertGreater(
            diagnostics["drawn_cards"], 0, "首帧要真的画出可见的牌面，而不是空画面"
        )
        for card in panel.cards():
            effect = card.graphicsEffect()
            self.assertIsNotNone(effect, "发牌期间真正的卡片应藏在覆盖层后面")
            self.assertAlmostEqual(effect.opacity(), 0.0)

        # 覆盖层走完：所有正面露出来，状态行这时才出现。
        panel.deal_layer.finish()
        self.app.processEvents()
        self.assertFalse(panel.deal_layer.is_running())
        self.assertFalse(panel.deal_layer.isVisible())
        self.assertTrue(panel.status_label.isVisible())
        for card in panel.cards():
            self.assertIsNone(card.graphicsEffect())

    def test_the_deal_fronts_do_not_come_from_a_card_screenshot(self):
        """发牌正面由卡片资产渲染：卡片即使截不出来，发牌也照常走。

        计划步骤 5：默认路径改用 ``CardAsset.front``，``card.grab()`` 只留在排障回退里。
        """
        panel = self.make_panel()
        with mock.patch.object(
            InstrumentCard, "grab", side_effect=AssertionError("发牌不应再截取卡片")
        ) as grab:
            panel.request_scan()
            panel.worker.wait(5000)
            self.app.processEvents()
            self.app.processEvents()
            panel.wait_stack.finish_cycle()
            self.app.processEvents()
            self.app.processEvents()

            self.assertTrue(panel.deal_layer.is_running())
            diagnostics = panel.deal_layer.diagnostics()
            self.assertEqual(diagnostics["front_source"], "asset")
            self.assertGreater(diagnostics["drawn_cards"], 0, "画面仍要有牌")
            grab.assert_not_called()
            panel.deal_layer.finish()

    def test_the_screenshot_fronts_are_only_a_troubleshooting_fallback(self):
        """回退开关打开时正面换回截图，并在诊断里写成 ``screenshot``。

        回退必须真的截了每一张卡：只看诊断字段的话，字段写对、画面仍是资产也能通过。
        """
        from presentation.qt import device_discovery_panel as panel_module

        if panel_module._USE_SCREENSHOT_FRONTS:
            self.skipTest("默认路径就是资产；回退开关只在排障时人工打开")

        panel = self.make_panel()
        grabbed: list[bool] = []

        def record_grab():
            grabbed.append(True)
            return QPixmap(CARD_WIDTH, CARD_HEIGHT)

        with (
            mock.patch.object(panel_module, "_USE_SCREENSHOT_FRONTS", True),
            mock.patch.object(InstrumentCard, "grab", side_effect=record_grab),
        ):
            panel.request_scan()
            panel.worker.wait(5000)
            self.app.processEvents()
            self.app.processEvents()
            panel.wait_stack.finish_cycle()
            self.app.processEvents()
            self.app.processEvents()

            self.assertTrue(panel.deal_layer.is_running())
            self.assertEqual(
                panel.deal_layer.diagnostics()["front_source"], "screenshot"
            )
            self.assertEqual(
                len(grabbed),
                len(panel.cards()),
                "回退路径要为每张卡片各截一次图",
            )
            panel.deal_layer.finish()

    def test_the_deal_assets_follow_the_cards_not_the_heading(self):
        """资产按卡片对象逐张渲染：标题重复也不会把某一台的画面套到另一台上。

        卡片标题并不保证唯一（同型号、同缩写码或未登记设备都可能重名）。这里给两张
        标题相同的卡片，验证 ``_deal_assets`` 读的是各自的 ``data`` 快照。
        """
        from presentation.qt import device_discovery_panel as panel_module
        from presentation.qt.card_assets import render_card_asset as real_render

        class _StubCard:
            """只需要 ``_deal_assets`` 用到的那几样：内容快照与逻辑尺寸。"""

            def __init__(self, data):
                self.data = data

            def width(self) -> int:
                return CARD_WIDTH

            def height(self) -> int:
                return CARD_HEIGHT

        panel = self.make_panel()
        shared_heading = "DP832A（DP8B1519）"
        first = _StubCard(InstrumentCardData(shared_heading, code="DP8B1519"))
        second = _StubCard(InstrumentCardData(shared_heading, code="DP8B0885"))
        seen = []

        def record_asset(data, **kwargs):
            seen.append(data)
            return real_render(data, **kwargs)

        with mock.patch.object(
            panel_module, "render_card_asset", side_effect=record_asset
        ):
            assets = panel._deal_assets(
                [first, second], panel_module.DEAL_FRONT_SOURCE_ASSET
            )

        self.assertEqual([data.code for data in seen], ["DP8B1519", "DP8B0885"])
        self.assertIs(seen[0], first.data, "第一份资产读的是第一张卡自己的快照")
        self.assertIs(seen[1], second.data, "第二份资产读的是第二张卡自己的快照")
        self.assertEqual(len(assets), 2, "资产必须与卡片一一对应")

    def test_every_card_is_dealt_to_where_it_will_be_flipped(self):
        """发牌落点就是卡片最终所在的位置：一开始就按最终版面取位，翻牌也翻在同一处。

        卡片数在拿到扫描结果时就定了（一台仪器一张卡片），因此发牌前结果区的版面
        已经落定：每张牌各飞各的格子，不会全部飞向同一个点、等下一轮事件才散开。
        """
        panel = self.make_panel(size=(760, 560))
        panel.request_scan()
        panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()

        # 这一轮等待动画走完之后直接进入发牌：中间不再处理事件，验证的是“发牌第一帧
        # 拿到的就是最终位置”，而不是下一轮布局事件补出来的位置。
        panel.wait_stack.finish_cycle()

        layer = panel.deal_layer
        self.assertTrue(layer.is_running())
        cards = panel.cards()
        self.assertGreater(len(cards), 1)
        starts = [card.geometry().topLeft() for card in cards]
        self.assertEqual(
            len(set(starts)), len(starts), "发牌一开始每张卡片就该在自己的格子上"
        )
        slots = []
        for index, card in enumerate(cards):
            slot = layer._target_rect(index)
            origin = card.mapTo(layer, card.rect().topLeft())
            self.assertAlmostEqual(slot.x(), origin.x(), delta=1, msg="发牌落点就是卡片位")
            self.assertAlmostEqual(slot.y(), origin.y(), delta=1, msg="发牌落点就是卡片位")
            self.assertAlmostEqual(slot.width(), card.width(), delta=1)
            self.assertAlmostEqual(slot.height(), card.height(), delta=1)
            slots.append((round(slot.x()), round(slot.y())))

        # 翻牌期间不再挪窝：走完发牌与翻牌，每张卡片仍在它的格子上。
        layer.finish()
        self.app.processEvents()
        self.assertEqual(layer.revealed_count, len(cards), "查到的每台仪器都要发出来")
        for (left, top), card in zip(slots, cards):
            origin = card.mapTo(layer, card.rect().topLeft())
            self.assertEqual((round(origin.x()), round(origin.y())), (left, top))

    def test_back_while_dealing_returns_to_the_scan_button(self):
        """发牌/翻牌期间按“返回”也能干净收尾：覆盖层收起，本次结果清空。"""
        panel = self.make_panel()
        panel.request_scan()
        panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()
        panel.wait_stack.finish_cycle()
        self.app.processEvents()
        self.app.processEvents()
        self.assertTrue(panel.deal_layer.is_running())

        panel.request_back()
        self.app.processEvents()

        self.assertEqual(panel.box_state(), BOX_STATE_IDLE)
        self.assertFalse(panel.deal_layer.isVisible())
        self.assertEqual(panel.card_count(), 0)
        self.assertTrue(panel.scan_btn.isVisible())
        diagnostics = panel.deal_layer.diagnostics()
        self.assertEqual(
            diagnostics["cancel_reason"], "results-cleared", "返回清空结果要写明取消原因"
        )
        self.assertEqual(diagnostics["restore_result"], "hidden")
        self.assertEqual(diagnostics["event"], "cancel")
        self.assertGreater(
            diagnostics["card_count"], 0, "取消后的快照仍要描述被取消的那一次动画"
        )

    def test_shutdown_while_dealing_records_the_cancel_reason(self):
        """关页面时取消发牌，日志里要能看出是页面关闭而不是别的原因。"""
        panel = self.make_panel()
        panel.request_scan()
        panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()
        panel.wait_stack.finish_cycle()
        self.app.processEvents()
        self.app.processEvents()
        self.assertTrue(panel.deal_layer.is_running())

        self.assertTrue(panel.shutdown())

        diagnostics = panel.deal_layer.diagnostics()
        self.assertEqual(diagnostics["cancel_reason"], "page-shutdown")
        self.assertEqual(diagnostics["restore_result"], "hidden")
        self.assertFalse(diagnostics["running"])

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

        # 扫描结束但动画还没走完：牌堆留在原处，不能中途收走。
        self.assertEqual(panel.box_state(), BOX_STATE_BUSY)
        self.assertTrue(panel.wait_stack.isVisible())

        self.finish_scan(panel)

        # 一台都没读到：结果区里放不出卡片，顶部的“查找设备”回来好让用户再试一次。
        self.assertEqual(panel.card_count(), 0)
        self.assertEqual(panel.box_state(), BOX_STATE_IDLE)
        self.assertTrue(panel.scan_btn.isVisible())
        self.assertFalse(panel.wait_stack.isVisible())

    def test_the_wait_animation_keeps_looping_while_the_scan_is_still_running(self):
        """一轮动画走完时结果还没回来：不换画面，牌堆继续下一轮，等扫描结束。"""
        import threading

        release = threading.Event()
        base_factory = self.default_factory

        def factory(mode):
            scanner = base_factory(mode)

            class _DelayedScanner:
                def set_cancel_event(self, event):
                    scanner.set_cancel_event(event)

                def scan(self, subnet, progress=None):
                    release.wait(5)
                    return scanner.scan(subnet, progress=progress)

                def request_cancel(self):
                    scanner.request_cancel()

            return _DelayedScanner()

        panel = self.make_panel(factory)
        panel.request_scan()
        self.app.processEvents()

        # 扫描还没结束，动画已经走完一轮：没有结果可换，画面保持等待动画。
        panel.wait_stack.finish_cycle()
        self.app.processEvents()
        self.assertEqual(panel.box_state(), BOX_STATE_BUSY)
        self.assertTrue(panel.wait_stack.isVisible())
        self.assertFalse(panel.scan_btn.isVisible())
        self.assertFalse(panel.cards_area.isVisible())

        # 结果回来后仍要等这一轮动画走完（牌堆复原）才进入下一步。
        release.set()
        panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()
        self.assertEqual(panel.box_state(), BOX_STATE_BUSY)
        self.assertEqual(panel.card_count(), 0)

        self.finish_scan(panel)

        self.assertEqual(panel.box_state(), BOX_STATE_RESULTS)
        self.assertGreater(panel.card_count(), 0)

    def test_the_results_take_the_box_over_from_the_centre(self):
        panel = self.make_panel()
        self.run_scan(panel)

        self.assertEqual(panel.box_state(), BOX_STATE_RESULTS)
        self.assertTrue(panel.cards_area.isVisible())
        self.assertFalse(panel.centre_area.isVisible(), "卡片看得见时顶部按钮让位给卡片")
        self.assertFalse(panel.scan_btn.isVisible())

        # 结果区里的动作只属于有结果的这一次查找：返回。
        self.assertTrue(panel.results_box.isAncestorOf(panel.back_btn))
        self.assertTrue(panel.back_btn.isVisible())
        self.assertEqual(panel.back_btn.text(), "返回")

    def test_back_clears_this_search_and_returns_to_the_scan_button(self):
        panel = self.make_panel()
        self.run_scan(panel)
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
        self.assertFalse(panel.back_btn.isVisible())

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
        # 结果还没换进结果区（动画未走完），框的大小同样不能变。
        animating = (box.width(), box.height())

        self.finish_scan(panel)
        results = (box.width(), box.height())

        panel.request_back()
        self.app.processEvents()
        returned = (box.width(), box.height())

        self.assertGreater(idle[1], 0)
        self.assertEqual(busy, idle, "扫描中结果区不能变")
        self.assertEqual(animating, idle, "结果挂起等动画时结果区不能变")
        self.assertEqual(results, idle, "结果回来后结果区不能变")
        self.assertEqual(returned, idle, "返回后结果区不能变")

    def test_the_box_has_room_for_two_rows_of_cards(self):
        """结果区的固定高度按“两行卡片 + 里面的按钮与状态行”算：场地上 5 台设备不用滚动。"""
        panel = self.make_panel()
        self.run_scan(panel)

        card = panel.cards()[0]
        self.assertGreaterEqual(
            panel.results_box.height(), 2 * card.height(), "结果区里至少放得下两行卡片"
        )

    def test_available_height_caps_the_box_but_keeps_one_card_row(self):
        """页面给出的矮窗口高度把结果区压下来，但不会再压到一行卡片以下。"""
        from presentation.qt.device_cards import CARD_HEIGHT

        panel = self.make_panel()
        two_rows = panel.results_box.height()

        panel.set_available_height(two_rows - 60)
        self.assertEqual(panel.results_box.height(), two_rows - 60)

        panel.set_available_height(1)
        self.assertGreaterEqual(
            panel.results_box.height(),
            CARD_HEIGHT,
            "结果区至少留一行卡片，扫描卡片与动作行才不会被裁掉",
        )

        panel.set_available_height(two_rows + 500)
        self.assertEqual(
            panel.results_box.height(),
            two_rows,
            "窗口再高也不超过两行卡片：多出来的高度留给页面留白",
        )

    def test_the_summary_line_sits_under_the_cards_and_centred(self):
        """统计/失败说明紧跟卡片下方并横向居中，不再孤零零地贴在结果区左下角。"""
        panel = self.make_panel(size=(760, 560))
        self.run_scan(panel)

        self.assertTrue(panel.status_label.isVisible())
        box = panel.results_box
        cards_bottom = max(
            card.mapTo(box, card.rect().bottomLeft()).y() for card in panel.cards()
        )
        status_top = panel.status_label.mapTo(box, panel.status_label.rect().topLeft()).y()
        self.assertGreaterEqual(
            status_top, cards_bottom, "说明要排在最后一行卡片下面，不能压在卡片上"
        )
        self.assertLess(
            status_top - cards_bottom, 40, "说明要紧跟卡片，不能隔着一整行的空白"
        )
        centre = panel.status_label.mapTo(box, panel.status_label.rect().center()).x()
        self.assertAlmostEqual(centre, box.rect().center().x(), delta=2)

    def test_clearing_the_results_puts_the_scan_button_back(self):
        panel = self.make_panel()
        self.run_scan(panel)

        panel.clear_results()
        self.app.processEvents()

        # 重新进入页面时回到“还没查找过”的样子：顶部的按钮回来了，外框依旧不画。
        self.assertEqual(panel.box_state(), BOX_STATE_IDLE)
        self.assertTrue(panel.scan_btn.isVisible())
        self.assertFalse(panel.cards_area.isVisible())
        self.assertEqual(
            self._painted_background(panel.results_box), TOKENS["canvas"].lower()
        )
