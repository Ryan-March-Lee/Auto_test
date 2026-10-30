"""连接页与发现面板的版面接线：整屏步骤、扫描行居中和结果框不跳动。"""

from PySide6.QtWidgets import QLabel

from presentation.qt.device_discovery_panel import BOX_STATE_IDLE, DeviceDiscoveryPanel

from .support import ConnectionPageTestCase


class ConnectionPageLayoutTests(ConnectionPageTestCase):
    """连接页是“下一步”之前的整屏步骤：版面、居中、结果框位置与页面切换。

    绑定写入与派生界面（电源标签页、电源分配可选项）的契约在
    ``test_binding_writes.py`` / ``test_binding_rejections.py``；这里只检查两块页面接起来
    之后的样子。
    """

    def test_page_builds_the_discovery_panel(self):
        page = self.make_page()
        self.assertIsInstance(self.connection.discovery_panel, DeviceDiscoveryPanel)
        # 面板由连接页交出、配置页登记：写绑定归配置页，展示归连接页。
        self.assertIs(page.discovery_panel, self.connection.discovery_panel)
        self.assertTrue(hasattr(page, "apply_discovered_devices"))

    def test_the_connection_page_is_a_full_screen_step_before_the_configuration(self):
        """仪表配置页是“下一步”之前的一整屏页面：有自己的大标题、返回与下一步。"""
        self.make_page()

        titles = [
            label.text()
            for label in self.connection.findChildren(QLabel)
            if label.property("role") == "pageTitle"
        ]
        self.assertEqual(titles, ["仪表配置"])
        self.assertEqual(self.connection.next_btn.text(), "下一步")
        self.assertEqual(self.connection.return_welcome_btn.text(), "返回欢迎页")

        requested = []
        self.connection.next_requested.connect(lambda: requested.append(True))
        self.connection.next_btn.click()
        self.assertEqual(requested, [True])

    def test_entering_the_page_resets_stale_scan_results(self):
        """再次进入本页时，上一次的结果不能冒充本次的发现结果。"""
        self.make_page()
        panel = self.connection.discovery_panel
        panel.request_scan()
        panel.worker.wait(5000)
        self.app.processEvents()
        self.assertTrue(panel.card_headings())

        self.connection.enter()

        self.assertEqual(panel.card_headings(), [])
        # 状态行回到“没有说话”的状态：不再常驻“尚未扫描”。
        self.assertEqual(panel.status_label.text(), "")
        self.assertFalse(panel.status_label.isVisible())

    def test_the_panel_is_never_squeezed_below_its_card_grid(self):
        """卡片展开后面板必须拿到足够高度：否则卡片区被裁掉并与按钮重叠。"""
        self.make_page()
        panel = self.connection.discovery_panel

        panel.request_scan()
        panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()

        self.assertTrue(panel.is_open())
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

        # 发现结果落在一块圆角矩形框里，框外的扫描行才是输入入口。
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

        # 有结果、收起之后也一样：动作按钮都在框里，不挤这一行。
        panel.request_scan()
        panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()
        panel.set_open(False)
        self.app.processEvents()
        self.assertAlmostEqual(
            self._scan_row_centre(panel), panel.width() / 2, delta=3, msg="收起后仍然居中"
        )

    def test_the_scan_button_sits_in_the_centre_of_the_results_box(self):
        """“查找设备”是结果框正中的落点，不在上面的扫描行里。"""
        self.make_page()
        panel = self.connection.discovery_panel

        self.assertTrue(panel.results_box.isAncestorOf(panel.scan_btn))
        self.assertTrue(panel.results_box.property("filled"))
        self.assertEqual(panel.box_state(), BOX_STATE_IDLE)

    def test_scanning_does_not_move_or_resize_the_results_box(self):
        """点“查找设备”前后，结果框的位置与大小都不变：页面不会跳一下。"""
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
        results = snapshot()

        panel.set_open(False)
        self.app.processEvents()
        collapsed = snapshot()

        self.assertEqual(scanning, before, "扫描中框不能移动或变形")
        self.assertEqual(results, before, "结果回来后框不能移动或变形")
        self.assertEqual(collapsed, before, "收起后框不能移动或变形")

    def test_connect_button_sits_outside_the_results_box(self):
        """“连接仪器”在结果框外面（右下角）：点它先走配置页的连接入口。"""
        page = self.make_page()
        failures = []
        requested = []
        page.validation_failed.connect(failures.append)
        page.connect_requested.connect(lambda: requested.append(True))

        button = self.connection.connect_btn
        self.assertEqual(button.text(), "连接仪器")
        self.assertFalse(
            self.connection.discovery_panel.results_box.isAncestorOf(button),
            "连接仪器应放在结果框外面",
        )

        button.click()

        # 当前配置没有可用电源：校验挡住连接，说明点击确实到了配置页的连接入口。
        self.assertEqual(requested, [])
        self.assertTrue(failures)
