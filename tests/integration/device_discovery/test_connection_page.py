"""连接页与发现面板的版面接线：整屏步骤、扫描行居中和结果区不跳动。"""

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QScrollArea

from presentation.qt.instrument_discovery_panel import (
    BOX_STATE_BUSY,
    BOX_STATE_IDLE,
    BOX_STATE_RESULTS,
    InstrumentDiscoveryPanel,
    DiscoveryPhase,
)

from .support import InstrumentSetupPageTestCase


class InstrumentSetupPageLayoutTests(InstrumentSetupPageTestCase):
    """连接页是“下一步”之前的整屏步骤：版面、居中、结果区位置与页面切换。

    绑定写入与派生界面（电源标签页、电源分配可选项）的契约在
    ``test_binding_writes.py`` / ``test_binding_rejections.py``；这里只检查两块页面接起来
    之后的样子。
    """

    def test_page_builds_the_discovery_panel(self):
        page = self.make_page()
        self.assertIsInstance(self.connection.discovery_panel, InstrumentDiscoveryPanel)
        # 面板只归连接页所有：配置页不再登记或持有它，回写出配置会话完成。
        self.assertFalse(hasattr(page, "discovery_panel"))
        self.assertFalse(hasattr(page, "attach_discovery_panel"))
        self.assertTrue(hasattr(page, "apply_device_selection"))
        self.assertFalse(hasattr(page, "apply_discovered_devices"))

    def test_the_connection_page_is_a_full_screen_step_before_the_configuration(self):
        """仪表配置页是“下一步”之前的一整屏页面：有自己的大标题、返回与下一步。"""
        self.make_page()

        self.assertEqual(self.connection.page_title(), "仪表配置")
        self.assertEqual(self.connection.action_labels(), ("返回欢迎页", "下一步"))

        requested = []
        self.connection.next_requested.connect(lambda: requested.append(True))
        self.connection.next_btn.click()
        self.assertEqual(requested, [True])

    def test_entering_the_page_resets_stale_scan_results(self):
        """再次进入本页时，上一次的结果不能冒充本次的发现结果。"""
        self.make_page()
        panel = self.connection.discovery_panel
        self.run_scan(panel)
        self.assertTrue(panel.card_headings())

        self.connection.enter()

        self.assertEqual(panel.card_headings(), [])
        # 状态行回到“没有说话”的状态：不再常驻“尚未扫描”。
        self.assertEqual(panel.status_label.text(), "")
        self.assertFalse(panel.status_label.isVisible())

    def test_the_panel_is_never_squeezed_below_its_card_grid(self):
        """卡片出现在框里后面板必须拿到足够高度：否则卡片区被裁掉并与按钮重叠。"""
        self.make_page()
        panel = self.connection.discovery_panel

        self.run_scan(panel)

        self.assertTrue(panel.cards_area.isVisible())
        self.assertGreaterEqual(panel.height(), panel.minimumSizeHint().height())
        self.assertGreaterEqual(
            panel.cards_area.height(),
            panel.cards_area.minimumHeight(),
        )

    def test_scan_subnet_defaults_to_the_bound_device_address(self):
        self.make_page(
            {
                "test_frequencies": [1.0],
                "instruments": {
                    "power_supplies": {},
                    "signal_generator": {
                        "address": "TCPIP0::192.168.1.201::inst0::INSTR",
                        "enabled": True,
                    },
                },
                "device_bindings": {
                    "signal_generator": {
                        "visa_address": "TCPIP0::10.2.3.201::inst0::INSTR",
                        "device_key": "rohde&schwarz:105462",
                    }
                },
            }
        )

        self.assertEqual(self.connection.discovery_panel.subnet_edit.text(), "10.2.3.0/24")

    def test_connection_box_has_no_manual_address_widgets(self):
        """仪器连接只有“扫描后勾选”一套逻辑：不再逐台填写地址或勾选启用。"""
        page = self.make_page()

        # 发现结果落在结果区里（不画外框），框外的扫描行才是输入入口。
        self.assertEqual(
            self.connection.discovery_panel.results_box.objectName(),
            "discoveryResultsBox",
        )
        for widget in (
            "sg_address", "sg_enabled", "sa_address", "sa_enabled",
            "ps1_address", "ps1_enabled", "ps4_address", "ps4_enabled",
        ):
            self.assertFalse(hasattr(page, widget), f"不应再存在手工地址控件 {widget}")

    @staticmethod
    def _scan_row(panel):
        """扫描行：整块扫描提示容器的第一行。"""
        return panel.scan_header.layout().itemAt(0).layout()

    def _scan_row_centre(self, panel):
        """扫描行里所有可见控件的整体中线（面板坐标）。"""
        row = self._scan_row(panel)
        spans = []
        for index in range(row.count()):
            widget = row.itemAt(index).widget()
            if widget is None or not widget.isVisible():
                continue
            left = widget.mapTo(panel, widget.rect().topLeft()).x()
            right = widget.mapTo(panel, widget.rect().topRight()).x()
            spans.append((min(left, right), max(left, right)))
        left = min(item[0] for item in spans)
        right = max(item[1] for item in spans)
        return (left + right) / 2

    def test_scan_row_sits_centred_under_the_title(self):
        """扫描网段与扫描方式居中排在“仪表配置”标题下面，与标题同一中线。"""
        self.make_page()
        panel = self.connection.discovery_panel
        title = self.connection.title_label

        title_centre = title.mapTo(
            self.connection, title.rect().center()
        ).x()
        panel_centre = panel.mapTo(
            self.connection, panel.rect().center()
        ).x()
        self.assertAlmostEqual(title_centre, panel_centre, delta=2, msg="标题与扫描行同一条中线")

        # 扫描行的两端由两个等大的弹性留白顶住：中间那组控件整体落在面板中线上。
        row = self._scan_row(panel)
        self.assertIsNotNone(row.itemAt(0).spacerItem(), "行首的留白把控件推到中线")
        self.assertIsNotNone(row.itemAt(row.count() - 1).spacerItem(), "行尾的留白同样大")
        self.assertAlmostEqual(
            self._scan_row_centre(panel), panel.width() / 2, delta=3, msg="整行居中"
        )

        # 有结果、按“返回”回到查找按钮之后也一样：动作按钮都在结果区里，不挤这一行。
        self.run_scan(panel)
        panel.request_back()
        self.app.processEvents()
        self.assertAlmostEqual(
            self._scan_row_centre(panel), panel.width() / 2, delta=3, msg="返回后仍然居中"
        )

    def test_scanning_hides_the_header_text_and_results_take_its_space(self):
        """扫描中只隐去页头文字（高度保留，按钮不跳）；有结果时页头整块让位。"""
        self.make_page()
        page = self.connection
        panel = page.discovery_panel
        title_effect = page.title_label.graphicsEffect()
        header_effect = panel.scan_header.graphicsEffect()
        self.assertIsNotNone(title_effect)
        self.assertIsNotNone(header_effect)
        self.assertAlmostEqual(title_effect.opacity(), 1.0, places=2)

        QTest.mouseClick(panel.scan_btn, Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(panel.box_state(), "busy")
        self.assertAlmostEqual(title_effect.opacity(), 0.0, places=2)
        self.assertAlmostEqual(header_effect.opacity(), 0.0, places=2)
        self.assertTrue(page.title_label.isVisible(), "扫描中页头高度仍保留，按钮才不跳")
        self.assertTrue(panel.scan_header.isVisible())
        self.assertFalse(panel.scan_header.isEnabled(), "隐去的扫描行不能继续接收输入")

        if panel.worker is not None:
            panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()
        self.finish_scan(panel)
        self.assertEqual(panel.box_state(), "results")
        self.assertFalse(page.title_label.isVisible(), "有结果时页头整块让位给卡片")
        self.assertFalse(panel.scan_header.isVisible())

        panel.request_back()
        self.app.processEvents()
        self.assertTrue(page.title_label.isVisible())
        self.assertTrue(panel.scan_header.isVisible())
        self.assertAlmostEqual(title_effect.opacity(), 1.0, places=2)
        self.assertAlmostEqual(header_effect.opacity(), 1.0, places=2)
        self.assertTrue(panel.scan_header.isEnabled())
        self.assertEqual(panel.box_state(), "idle")

    def test_the_scan_button_sits_in_the_results_area(self):
        """“查找设备”是结果区顶部的落点，不在上面的扫描行里。"""
        self.make_page()
        panel = self.connection.discovery_panel

        self.assertTrue(panel.results_box.isAncestorOf(panel.scan_btn))
        self.assertEqual(panel.box_state(), BOX_STATE_IDLE)

    def test_the_button_does_not_jump_and_results_sit_above_it(self):
        """点“查找设备”按钮不跳；结果卡片与按钮解耦，出现位置高于按钮。

        - 未查找 → 正在查找：页头只隐去文字、高度保留，按钮与等待动画同格，结果区几何不变；
        - 正在查找 → 有结果：页头整块让位，结果卡片升到按钮原来的位置之上；
        - 返回：页头复原，按钮回到原来的位置。
        """
        self.make_page()
        panel = self.connection.discovery_panel
        box = panel.results_box
        page = self.connection

        def snapshot():
            top_left = box.mapTo(page, box.rect().topLeft())
            return (top_left.x(), top_left.y(), box.width(), box.height())

        before = snapshot()
        button_before = panel.scan_btn.mapTo(
            page, panel.scan_btn.rect().topLeft()
        )

        panel.request_scan()
        self.app.processEvents()
        scanning = snapshot()
        self.assertEqual(scanning, before, "扫描中结果区不能移动或变形")

        # 等待动画的牌堆正好压住按钮原来的位置：点下去卡片原地摊开。
        stage = panel.wait_stack.mapTo(page, panel.wait_stack.rect().topLeft())
        deck_x = stage.x() + (panel.wait_stack.width() - panel.scan_btn.width()) // 2
        deck_y = stage.y() + (panel.wait_stack.height() - panel.scan_btn.height()) // 2
        self.assertEqual(
            (deck_x, deck_y), (button_before.x(), button_before.y()),
            "等待动画的牌堆必须与按钮同一位置，按钮不能跳变",
        )

        panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()
        self.assertEqual(panel.box_state(), BOX_STATE_BUSY)
        self.assertEqual(snapshot(), scanning, "等动画时结果区不能移动或变形")
        self.finish_scan(panel)
        results = snapshot()

        # 有结果后页头让位，卡片整体上移，高于按钮原来的位置。
        self.assertLess(results[1], scanning[1], "结果卡片应升到按钮上方")
        first_card = panel.cards()[0]
        card_top = first_card.mapTo(page, first_card.rect().topLeft()).y()
        card_bottom = first_card.mapTo(page, first_card.rect().bottomLeft()).y()
        self.assertLess(card_top, button_before.y(), "卡片出现的位置应高于按钮")
        self.assertLess(
            card_bottom, button_before.y(), "卡片与按钮不能重叠：整块卡片都在按钮上方"
        )
        self.assertLess(
            card_top, page.height() * 0.3, "卡片应出现在页面顶部附近"
        )

        panel.request_back()
        self.app.processEvents()
        self.assertEqual(snapshot(), before, "返回后按钮与结果区回到原位")

    def test_clicking_the_scan_button_does_not_move_the_scan_row_or_scroll_the_page(self):
        """点“查找设备”不能把扫描行推走或滚动页面。

        Qt 会在焦点控件被禁用或隐藏时把焦点交给下一个可聚焦控件，外层滚动区再滚到
        它那里：卡片一收走，焦点落到结果区动作行里的“连接仪器”，整页向上滚，扫描行
        （扫描网段 / 扫描方式）就离开了点之前的位置。扫描中页头文字隐去、高度保留，
        扫描行本身留在原处；以点击之前的位置为准。
        """
        self.make_page()
        page = self.connection
        # 用矮窗口模拟“页面可能滚动”的情形；滚动条是否出现不影响这条断言。
        page.resize(1024, 520)
        self.app.processEvents()
        panel = page.discovery_panel
        bar = page.findChild(QScrollArea).verticalScrollBar()
        before_value = bar.value()

        def row_position():
            point = panel.mode_combo.mapTo(page, panel.mode_combo.rect().topLeft())
            return (point.x(), point.y())

        before = row_position()
        QTest.mouseClick(panel.scan_btn, Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(panel.box_state(), "busy")
        self.assertAlmostEqual(
            panel.scan_header.graphicsEffect().opacity(), 0.0, places=2
        )
        self.assertEqual(row_position(), before, "扫描中扫描行不能移动")
        self.assertEqual(bar.value(), before_value, "点“查找设备”不能滚动页面")

        if panel.worker is not None:
            panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()
        self.finish_scan(panel)
        self.assertFalse(panel.scan_header.isVisible(), "有结果时页头让位给卡片")
        self.assertEqual(bar.value(), before_value, "结果回来不能滚动页面")

        panel.request_back()
        self.app.processEvents()
        self.assertTrue(panel.scan_header.isVisible())
        self.assertAlmostEqual(
            panel.scan_header.graphicsEffect().opacity(), 1.0, places=2
        )
        self.assertEqual(row_position(), before, "返回后扫描行仍在原处")
        self.assertEqual(bar.value(), before_value, "返回也不能滚动页面")

    def test_connect_button_shares_the_back_row(self):
        """“连接仪器”和“返回”同一行、落在右端：点它先走配置页的连接入口。"""
        page = self.make_page()
        failures = []
        requested = []
        page.validation_failed.connect(failures.append)
        page.connect_requested.connect(lambda: requested.append(True))

        button = self.connection.connect_btn
        panel = self.connection.discovery_panel
        self.assertEqual(button.text(), "连接仪器")
        self.assertIs(
            button.parentWidget(),
            panel.back_btn.parentWidget(),
            "连接仪器要与返回放在同一行",
        )
        # 两个页面级动作也在这一行：它们与“连接仪器”共用同一个父控件、同一条水平线。
        for action in (self.connection.return_welcome_btn, self.connection.next_btn):
            self.assertIs(
                action.parentWidget(),
                button.parentWidget(),
                "返回欢迎页与下一步要和连接仪器同一行",
            )
        # 有结果时“返回”才可见，两个按钮的几何位置也才有意义。
        self.run_scan(panel)
        self.assertTrue(panel.back_btn.isVisible())
        connect_centre = button.mapTo(
            self.connection, button.rect().center()
        )
        back_centre = panel.back_btn.mapTo(
            self.connection, panel.back_btn.rect().center()
        )
        self.assertAlmostEqual(
            connect_centre.y(), back_centre.y(), delta=2, msg="两个按钮同一条水平线"
        )
        for action in (self.connection.return_welcome_btn, self.connection.next_btn):
            centre = action.mapTo(self.connection, action.rect().center())
            self.assertAlmostEqual(
                centre.y(), connect_centre.y(), delta=2, msg="页面动作与连接仪器同一行"
            )
            self.assertGreater(
                centre.x(), connect_centre.x(), "页面动作排在连接仪器右侧"
            )

        button.click()

        # 当前配置没有可用电源：校验挡住连接，说明点击确实到了配置页的连接入口。
        self.assertEqual(requested, [])
        self.assertTrue(failures)


class InstrumentSetupPageNextWriteTests(InstrumentSetupPageTestCase):
    """“下一步”就是写入点：勾选结果整批替换配置里的设备绑定。"""

    def _scan(self):
        panel = self.connection.discovery_panel
        return self.run_scan(panel)

    def test_the_panel_has_no_apply_or_clear_buttons(self):
        self.make_page()
        panel = self.connection.discovery_panel

        self.assertFalse(hasattr(panel, "apply_btn"))
        self.assertFalse(hasattr(panel, "clear_btn"))
        self.assertFalse(hasattr(panel, "request_clear"))

    def test_next_writes_the_checked_devices_before_leaving(self):
        page = self.make_page()
        panel = self._scan()
        panel.set_card_checked("SMW200A（105462）", True)
        requested = []
        self.connection.next_requested.connect(lambda: requested.append(True))

        self.connection.next_btn.click()

        self.assertEqual(requested, [True], "写入成功后要进入下一页")
        self.assertEqual(set(page.device_bindings), {"signal_generator"})
        self.assertEqual(
            page.build_config()["instruments"]["signal_generator"]["address"],
            "TCPIP0::192.168.1.201::inst0::INSTR",
        )

    def test_next_with_results_and_nothing_checked_is_blocked(self):
        """扫描出结果却一台没勾：阻止跳页，不把“没勾选”当成“清空绑定”。"""
        page = self.make_page()
        panel = self._scan()
        requested = []
        self.connection.next_requested.connect(lambda: requested.append(True))

        self.connection.next_btn.click()

        self.assertEqual(requested, [])
        self.assertEqual(page.device_bindings, {})
        self.assertIn("请先勾选", panel.status_label.text())

    def test_next_is_blocked_when_the_selection_has_a_problem(self):
        """勾选结果本身有歧义（同一角色两台设备）时留在本页，不写半份绑定。"""
        from domain.instruments.models import InstrumentIdentity
        from infrastructure.discovery.instrument_discovery import (
            SCAN_MODE_SIMULATED,
            build_scanner,
        )
        from infrastructure.persistence.instrument_registry import InstrumentRegistry

        from .support import SMW_IDN

        second_smw = "Rohde&Schwarz,SMW200A,1412.0000K02/200001,5.20.043.47"

        def factory(_mode):
            registry = InstrumentRegistry()
            registry.upsert(InstrumentIdentity.from_idn(SMW_IDN), role="signal_generator")
            registry.upsert(
                InstrumentIdentity.from_idn(second_smw), role="signal_generator"
            )
            return build_scanner(
                registry=registry,
                mode=SCAN_MODE_SIMULATED,
                responses={
                    ("192.168.1.201", 5025): SMW_IDN,
                    ("192.168.1.203", 5025): second_smw,
                },
                timeout_s=0.02,
            )

        page = self.make_page()
        panel = self.connection.discovery_panel
        panel.scanner_factory = factory
        panel = self._scan()
        for heading in panel.card_headings():
            panel.set_card_checked(heading, True)
        requested = []
        self.connection.next_requested.connect(lambda: requested.append(True))

        self.connection.next_btn.click()

        self.assertEqual(requested, [])
        self.assertEqual(page.device_bindings, {})
        self.assertIn("重复选择", panel.status_label.text())

    def test_next_without_a_scan_keeps_the_saved_bindings(self):
        """返回本页只改参数、没重新扫描时，沿用已保存的绑定，不要求再选一次。"""
        page = self.make_page(
            {
                "test_frequencies": [1.0],
                "instruments": {"power_supplies": {}},
                "device_bindings": {
                    "signal_generator": {
                        "full_code": "105462",
                        "device_key": "rohde&schwarz:105462",
                    }
                },
            }
        )
        requested = []
        self.connection.next_requested.connect(lambda: requested.append(True))

        self.connection.next_btn.click()

        self.assertEqual(requested, [True])
        self.assertIn("signal_generator", page.device_bindings)


class DiscoveryAnimationOverlayTests(InstrumentSetupPageTestCase):
    """页面级动画覆盖层（技术债重构步骤 6）：不放进卡片滚动区，落点在开始时冻结。"""

    def _dealing(self, size=(1200, 900), position=None):
        """扫完一轮、进入发牌但先不替它收尾：返回 ``(page, panel, layer)``。

        ``position`` 把页面窗口挪到桌面上的另一处：覆盖层坐标应只取决于控件之间的
        相对位置，窗口在哪里都不该改变落点。
        """
        self.make_page()
        page = self.connection
        page.resize(*size)
        if position is not None:
            page.move(*position)
        self.app.processEvents()
        panel = page.discovery_panel
        panel.request_scan()
        panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()
        panel.wait_stack.finish_cycle()
        self.app.processEvents()
        self.app.processEvents()
        self.assertTrue(panel.deal_animation.is_running(), "扫描结果应由发牌动画带出来")
        return page, panel, panel.deal_animation

    def test_the_deal_overlay_sits_on_the_page_root_not_in_the_scroll_area(self):
        """覆盖层挂在页面根容器上：滚动区与结果区都不再是它的坐标系。

        计划步骤 6：动画不依赖 ``results_box`` 的内部布局，也不随 ``QScrollArea`` 滚动。
        """
        self.make_page()
        page = self.connection
        panel = page.discovery_panel
        overlay = page.animation_overlay

        self.assertIs(panel.animation_overlay(), overlay)
        self.assertIs(
            panel.deal_animation.parentWidget(), overlay, "发牌层铺在页面级覆盖层里"
        )
        self.assertIs(overlay.parentWidget(), page.overlay_host)
        self.assertFalse(page.scroll_area.isAncestorOf(overlay), "覆盖层不能在滚动区里")
        self.assertFalse(panel.results_box.isAncestorOf(overlay), "覆盖层不能挂结果区")
        self.assertTrue(overlay.testAttribute(Qt.WA_TransparentForMouseEvents))
        self.assertEqual(overlay.focusPolicy(), Qt.NoFocus)
        self.assertEqual(
            overlay.geometry(), page.overlay_host.rect(), "覆盖层要铺满页面根容器"
        )

    def test_the_targets_are_captured_in_overlay_coordinates(self):
        """每张牌的落点在动画开始时换算到覆盖层坐标，之后不再追踪卡片控件。"""
        page, panel, layer = self._dealing()
        cards = panel.cards()
        self.assertGreater(len(cards), 1)
        diagnostics = layer.diagnostics()
        self.assertGreater(diagnostics["paint_frames"], 0, "页面级覆盖层要真的绘制出来")
        self.assertGreater(diagnostics["drawn_cards"], 0, "首帧要画出可见的牌面")
        self.assertIn(
            "AnimationOverlay(pageAnimationOverlay)",
            diagnostics["overlay_parent"],
            "诊断里的父控件应指向页面级覆盖层",
        )

        for index, card in enumerate(cards):
            target = layer._target_rect(index)
            # 期望值用页面根容器（卡片与覆盖层的共同祖先）换算；覆盖层与它同大，
            # 因此这既是卡片在覆盖层里的位置，也不经过任何“映射到兄弟控件”的捷径。
            origin = card.mapTo(page.overlay_host, card.rect().topLeft())
            self.assertAlmostEqual(target.x(), origin.x(), delta=1, msg="落点在覆盖层坐标里")
            self.assertAlmostEqual(target.y(), origin.y(), delta=1, msg="落点在覆盖层坐标里")
            self.assertAlmostEqual(target.width(), card.width(), delta=1)
            self.assertAlmostEqual(target.height(), card.height(), delta=1)

        # 页头文字淡出只改透明度、不改版面：已经开始的动画坐标保持不变。
        frozen = [layer._target_rect(index) for index in range(len(cards))]
        page._set_header_text_visible(False)
        self.app.processEvents()
        self.assertEqual(
            [layer._target_rect(index) for index in range(len(cards))],
            frozen,
            "开始之后淡出页头文字不能移动已经算好的落点",
        )

        layer.finish()
        self.app.processEvents()

    def test_the_targets_do_not_depend_on_where_the_window_sits(self):
        """覆盖层坐标只看控件相对位置：窗口挪到桌面别处，落点不能跟着偏。

        旧实现用 ``mapTo`` 把卡片映射到兄弟分支的覆盖层，窗口不在 (0, 0) 时会拿到
        屏幕坐标；这条用例把窗口挪开再扫描，专门盯住那个偏移。
        """
        page, panel, layer = self._dealing(position=(140, 90))
        host = page.overlay_host

        for index, card in enumerate(panel.cards()):
            target = layer._target_rect(index)
            expected = card.mapTo(host, card.rect().topLeft())
            self.assertAlmostEqual(target.x(), expected.x(), delta=1, msg="落点不能被窗口位置带偏")
            self.assertAlmostEqual(target.y(), expected.y(), delta=1, msg="落点不能被窗口位置带偏")

        layer.finish()
        self.app.processEvents()

    def test_a_card_area_layout_change_while_dealing_cancels(self):
        """卡片区自己的重排（不改变页面根容器尺寸）也要取消这次发牌。

        结果区在页面里可能单独变高/变矮（滚动条出现、页头让出高度），覆盖层收不到
        resize；面板必须自己按 ``surface-changed`` 收尾，不能把牌画到旧坐标上。
        """
        _page, panel, layer = self._dealing()
        panel.resize(panel.width() - 60, panel.height())
        self.app.processEvents()

        self.assertFalse(layer.is_running(), "卡片区布局变化应结束这一次发牌")
        self.assertEqual(layer.diagnostics()["cancel_reason"], "surface-changed")
        self.assertTrue(panel.cards_area.isEnabled(), "取消后卡片恢复可交互")

    def test_resizing_the_page_while_dealing_cancels_instead_of_tracking(self):
        """窗口缩放时取消这一次发牌并恢复卡片，而不是在动画中途追踪布局。"""
        page, panel, layer = self._dealing()
        page.resize(page.width() - 80, page.height())
        self.app.processEvents()

        self.assertFalse(layer.is_running(), "缩放应结束这一次发牌")
        self.assertFalse(layer.isVisible())
        diagnostics = layer.diagnostics()
        self.assertEqual(diagnostics["cancel_reason"], "surface-changed")
        self.assertEqual(diagnostics["restore_result"], "revealed")
        self.assertTrue(panel.cards_area.isEnabled(), "取消后卡片恢复可交互")
        self.assertTrue(
            panel.status_label.isVisible(), "取消后结果统计也要放出来，不能一直藏着"
        )
        for card in panel.cards():
            self.assertIsNone(card.graphicsEffect(), "取消后卡片不再被透明度效果藏起来")

    def test_leaving_the_page_while_dealing_stops_the_animation(self):
        """页面被藏起来（切页、标签页切走、窗口最小化）：按 ``page-hidden`` 收尾。

        通用过渡约定（计划步骤 12）：看不见的动画不该继续走，也不该把计时器留在隐藏的
        页面上。收尾之后卡片恢复可交互，回到页面时不会重播已经结束的一次发牌。
        """
        page, panel, layer = self._dealing()

        page.hide()
        self.app.processEvents()

        self.assertFalse(layer.is_running(), "页面隐藏后发牌不能还在走")
        self.assertFalse(layer.isVisible())
        diagnostics = layer.diagnostics()
        self.assertEqual(diagnostics["cancel_reason"], "page-hidden")
        self.assertEqual(diagnostics["restore_result"], "revealed")
        self.assertIs(panel.phase(), DiscoveryPhase.INTERACTIVE)
        self.assertTrue(panel.cards_area.isEnabled())
        self.assertFalse(panel.status_label.isHidden(), "收尾后统计行要放出来")
        for card in panel.cards():
            self.assertIsNone(card.graphicsEffect(), "收尾后卡片不再被藏起来")

        page.show()
        self.app.processEvents()
        self.assertFalse(layer.is_running(), "回到页面不该重播已经结束的发牌")
        self.assertIs(panel.phase(), DiscoveryPhase.INTERACTIVE)

    def test_minimizing_the_window_while_dealing_stops_the_animation(self):
        """窗口最小化：顶层窗口状态变化也按 ``page-hidden`` 收尾。

        最小化不一定给子控件发 ``Hide``（各平台实现不同），所以覆盖层同时盯住宿主所在
        顶层窗口的 ``WindowStateChange``；无论哪条路径先到，收尾只发生一次。
        """
        page, panel, layer = self._dealing()

        page.showMinimized()
        self.app.processEvents()

        self.assertTrue(page.isMinimized())
        self.assertFalse(layer.is_running(), "最小化后发牌不能还在走")
        diagnostics = layer.diagnostics()
        self.assertEqual(diagnostics["cancel_reason"], "page-hidden")
        self.assertEqual(diagnostics["restore_result"], "revealed")
        self.assertIs(panel.phase(), DiscoveryPhase.INTERACTIVE)
        self.assertTrue(panel.cards_area.isEnabled())
        for card in panel.cards():
            self.assertIsNone(card.graphicsEffect(), "收尾后卡片不再被藏起来")

        page.showNormal()
        self.app.processEvents()
        self.assertFalse(layer.is_running(), "恢复正常窗口不该重播已经结束的发牌")
        self.assertIs(panel.phase(), DiscoveryPhase.INTERACTIVE)

    def test_hiding_the_page_closes_the_transition_exactly_once(self):
        """页面级覆盖层与面板隐藏都会触发收尾：重复入口必须幂等。

        子控件与页面宿主的隐藏事件没有约定先后顺序；两条路径都调用同一个公开入口，
        没有在播时返回 ``False``，因此流程只推进一次、只写一份诊断。
        """
        page, panel, layer = self._dealing()
        transitions = []
        panel.phase_changed.connect(transitions.append)

        page.hide()
        self.app.processEvents()

        self.assertEqual(
            transitions,
            [DiscoveryPhase.INTERACTIVE.value],
            "收尾入口重复触发不能重复推进流程",
        )
        self.assertEqual(layer.diagnostics()["cancel_reason"], "page-hidden")
        self.assertFalse(layer.is_running())

    def test_closing_the_page_while_dealing_stops_without_restoring(self):
        """页面关闭：收尾但不恢复即将销毁的控件，流程进入 ``CLOSED``。"""
        page, panel, layer = self._dealing()

        page.close()
        self.app.processEvents()

        self.assertFalse(layer.is_running(), "关闭页面后不能还有发牌计时器")
        diagnostics = layer.diagnostics()
        self.assertEqual(diagnostics["cancel_reason"], "page-shutdown")
        self.assertEqual(diagnostics["restore_result"], "hidden")
        self.assertIs(panel.phase(), DiscoveryPhase.CLOSED)
