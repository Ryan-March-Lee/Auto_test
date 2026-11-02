"""连接页与发现面板的版面接线：整屏步骤、扫描行居中和结果区不跳动。"""

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QScrollArea

from presentation.qt.device_discovery_panel import (
    BOX_STATE_BUSY,
    BOX_STATE_IDLE,
    DeviceDiscoveryPanel,
)

from .support import ConnectionPageTestCase


class ConnectionPageLayoutTests(ConnectionPageTestCase):
    """连接页是“下一步”之前的整屏步骤：版面、居中、结果区位置与页面切换。

    绑定写入与派生界面（电源标签页、电源分配可选项）的契约在
    ``test_binding_writes.py`` / ``test_binding_rejections.py``；这里只检查两块页面接起来
    之后的样子。
    """

    def test_page_builds_the_discovery_panel(self):
        page = self.make_page()
        self.assertIsInstance(self.connection.discovery_panel, DeviceDiscoveryPanel)
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
        """面板布局里的扫描行（第一层是控制区，控制区的第一行才是扫描行）。"""
        return panel.layout().itemAt(0).layout().itemAt(0).layout()

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

    def test_the_scan_button_sits_in_the_results_area(self):
        """“查找设备”是结果区顶部的落点，不在上面的扫描行里。"""
        self.make_page()
        panel = self.connection.discovery_panel

        self.assertTrue(panel.results_box.isAncestorOf(panel.scan_btn))
        self.assertEqual(panel.box_state(), BOX_STATE_IDLE)

    def test_scanning_does_not_move_or_resize_the_results_area(self):
        """点“查找设备”前后，结果区的位置与大小都不变：页面不会跳一下。"""
        self.make_page()
        panel = self.connection.discovery_panel
        box = panel.results_box

        def snapshot():
            top_left = box.mapTo(self.connection, box.rect().topLeft())
            return (top_left.x(), top_left.y(), box.width(), box.height())

        before = snapshot()

        panel.request_scan()
        self.app.processEvents()
        scanning = snapshot()

        panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()
        # 结果先挂起，等这一轮动画走完才换进结果区；框的大小在这段等待里同样不能变。
        self.assertEqual(panel.box_state(), BOX_STATE_BUSY)
        animating = snapshot()
        self.finish_scan(panel)
        results = snapshot()

        panel.request_back()
        self.app.processEvents()
        returned = snapshot()

        self.assertEqual(scanning, before, "扫描中结果区不能移动或变形")
        self.assertEqual(animating, before, "结果挂起等动画时结果区不能移动或变形")
        self.assertEqual(results, before, "结果回来后结果区不能移动或变形")
        self.assertEqual(returned, before, "返回后结果区不能移动或变形")

    def test_clicking_the_scan_button_keeps_the_scan_row_where_it_was(self):
        """窗口矮到页面可以滚动时，点“查找设备”也不能把扫描行推走。

        Qt 会在焦点控件被禁用或隐藏时把焦点交给下一个可聚焦控件，外层滚动区再滚到
        它那里：卡片一收走，焦点落到结果区动作行里的“连接仪器”，整页向上滚，扫描行
        （扫描网段 / 扫描方式）就离开了点之前的位置。以点击之前的位置为准。
        """
        self.make_page()
        page = self.connection
        # 这个尺寸下页面高于窗口，滚动条一定在：否则测不到焦点引起的滚动。
        page.resize(1024, 600)
        self.app.processEvents()
        panel = page.discovery_panel
        bar = page.findChild(QScrollArea).verticalScrollBar()
        self.assertTrue(bar.isVisible(), "页面应当可以滚动，否则测不到焦点滚动")

        def row_position():
            point = panel.mode_combo.mapTo(page, panel.mode_combo.rect().topLeft())
            return (point.x(), point.y())

        before = row_position()
        QTest.mouseClick(panel.scan_btn, Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(row_position(), before, "扫描中扫描行不能移动")
        self.assertEqual(bar.value(), 0, "点“查找设备”不能滚动页面")

        if panel.worker is not None:
            panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()
        self.finish_scan(panel)
        self.assertEqual(row_position(), before, "结果回来后扫描行仍在原处")
        self.assertEqual(bar.value(), 0, "结果回来不能滚动页面")

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


class ConnectionPageNextWriteTests(ConnectionPageTestCase):
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
