"""仪表配置页：欢迎页选择测试模式之后的第一个整屏页面。

本页只做一件事：扫描网段、勾选本次测试使用的仪器，并把所选设备的当前地址与设备
身份（序列号）写进配置里的设备绑定。写入边界与派生界面（电源标签页、电源分配可选
项、读入时的坏绑定提示）都归 :class:`presentation.qt.config_page.ConfigPage`：本页
把设备发现面板交给配置页登记，用户点“应用所选设备到配置”时由配置页整批校验后写入，
配置页再把结果回写到面板的状态文本上。本页不写文件、不解析配置文档，也不自己连仪器：
它只从配置页读当前绑定与已保存地址，用来填“当前配置”与扫描框初值；点“连接仪器”
只是把请求交给配置页去校验。

版式只有三块：页面偏上的大字号标题、居中放在标题下面的扫描行（扫描网段 / 扫描方式），
以及下面一块显示发现结果的圆角矩形框；“查找设备”是框正中的落点，还没点它时框内铺一层
浅灰底，点下去之后按钮与灰底一起收走、开始加载设备。“连接仪器”放在框外右下角，点它仍然走
配置页的连接入口（:meth:`presentation.qt.config_page.ConfigPage.request_connect`
校验后发 ``connect_requested``，由窗口连仪器）。

“下一步”只做页面切换：配置随“保存配置”或“校验并创建运行”一起落盘，因此从本页
离开不会产生半保存的配置。
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from application.ports.device_discovery import DEMO_SUBNET

from .config_form_bindings import LEGACY_BINDING_SOURCE, binding_address, binding_records
from .device_discovery_panel import DeviceDiscoveryPanel, subnet_from_address
from .theme import set_button_variant

#: 页面标题：整屏步骤页只有这一行大字号标题，居中放在页面偏上的位置。
PAGE_TITLE = "仪表配置"
#: 操作栏提示：说明“下一步”不会丢弃已应用的设备绑定。
ACTION_HINT = "已应用的设备会随配置一起保存；点“下一步”继续配置测试参数与运行。"


class ConnectionPage(QWidget):
    """仪表配置页：整屏页面，不登记进标签栏。

    页面持有大字号标题、设备发现面板、框外右下的“连接仪器”，以及两个页面级动作
    （返回欢迎页、下一步）；设备绑定的写入与派生界面状态由配置页负责，本页把它当成
    窄接口使用。
    """

    next_requested = Signal()
    return_welcome_requested = Signal()
    log_message = Signal(str)

    def __init__(
        self,
        config_page: Any,
        parent: Any = None,
        scanner_factory: Callable[[str], Any] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("connectionPage")
        self.config_page = config_page
        self._scanner_factory = scanner_factory
        self._build_ui()
        self._connect_signals()

    # ---- 界面 -------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 16, 0, 0)
        root.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        content = QWidget()
        # 与仪器配置页同一套版心宽度：窗口再宽，卡片网格也不会被拉成很长的边框。
        content.setMaximumWidth(1180)
        column = QVBoxLayout(content)
        column.setContentsMargins(16, 0, 16, 0)
        column.setSpacing(12)

        # 整页只有标题、扫描行、结果框与“连接仪器”四块：上下留白对半分，
        # 让这一组控件落在页面中间（竖直方向大致居中）。
        column.addStretch(1)
        self.title_label = QLabel(PAGE_TITLE)
        self.title_label.setObjectName("connectionPageTitle")
        self.title_label.setProperty("role", "pageTitle")
        self.title_label.setAlignment(Qt.AlignHCenter)
        column.addWidget(self.title_label)
        column.addSpacing(12)

        # 扫描行（扫描网段 / 扫描方式）就排在这个标题下面、和标题一起居中；
        # 面板的结果框里才放“查找设备”，点下去就开始加载设备。
        self.discovery_panel = DeviceDiscoveryPanel(
            scanner_factory=self._scanner_factory or self._default_scanner_factory(),
            default_subnet=self._default_scan_subnet(),
            parent=self,
        )
        column.addWidget(self.discovery_panel)
        # 结果框外面只剩一个动作：连接仪器放在框的右下角。
        column.addWidget(self._build_connect_row())
        column.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        root.addWidget(self._build_action_bar())

    def _build_connect_row(self) -> QWidget:
        """结果框外的一行：只放“连接仪器”，右对齐在框下面。"""
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addStretch(1)
        self.connect_btn = QPushButton("连接仪器")
        self.connect_btn.setToolTip("连接本次已应用的仪器（地址与身份来自设备绑定）")
        set_button_variant(self.connect_btn, "primary")
        layout.addWidget(self.connect_btn)
        return row

    def _build_action_bar(self) -> QWidget:
        """页面底部固定操作栏：返回欢迎页，或进入测试参数配置。"""
        bar = QWidget()
        bar.setObjectName("pageActionBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(16, 0, 16, 0)
        row.setSpacing(10)
        hint = QLabel(ACTION_HINT)
        hint.setProperty("role", "hint")
        row.addWidget(hint, 1)
        self.return_welcome_btn = QPushButton("返回欢迎页")
        set_button_variant(self.return_welcome_btn, "secondary")
        self.next_btn = QPushButton("下一步")
        self.next_btn.setToolTip("进入仪器配置页，配置测试参数并创建运行")
        set_button_variant(self.next_btn, "primary")
        row.addWidget(self.return_welcome_btn)
        row.addWidget(self.next_btn)
        return bar

    def _connect_signals(self) -> None:
        self.next_btn.clicked.connect(self.next_requested.emit)
        self.return_welcome_btn.clicked.connect(self.return_welcome_requested.emit)
        # “连接仪器”仍然走配置页的连接入口：先按当前配置校验，通过后由配置页发
        # ``connect_requested``，窗口再交给协调器真正连仪器。连接只来自已应用的设备
        # 绑定，本页不自己拼地址，也不持有连接端口。
        self.connect_btn.clicked.connect(self.config_page.request_connect)
        # 发现面板的日志（扫描结果、失败原因）经配置页转出，窗口统一写进状态面板。
        self.config_page.discovery_message.connect(self.log_message.emit)
        # 面板的“应用/清除”由配置页处理：写绑定、重建电源标签页、回写面板状态。
        self.config_page.attach_discovery_panel(self.discovery_panel)

    # ---- 进入页面 ---------------------------------------------------------

    def enter(self) -> None:
        """每次从欢迎页进入本页时按当前配置重置面板。

        上次会话的扫描结果不能留下来冒充本次的发现结果：清空卡片与状态行；扫描框初值
        按当前配置（含刚选定的测试模式）重新取，落在这批设备所在的网段上。只有地址、
        没被本次扫描确认过的旧绑定不再画在框里，改为写一条日志，说明它们“未扫描确认”。
        """
        panel = self.discovery_panel
        panel.set_device_bindings(self.config_page.device_bindings)
        panel.clear_results()
        panel.subnet_edit.setText(self._default_scan_subnet() or DEMO_SUBNET)
        legacy = [
            record
            for _key, record in binding_records(self.config_page.device_bindings)
            if record.get("source") == LEGACY_BINDING_SOURCE
        ]
        if legacy:
            self.log_message.emit(
                f"有 {len(legacy)} 台设备的连接沿用已保存的配置，未扫描确认"
            )

    # ---- 扫描框初值 -------------------------------------------------------

    def _default_scan_subnet(self) -> str | None:
        """扫描框初值：优先已绑定设备的地址，其次配置里已有的仪器地址。"""
        for _key, record in binding_records(self.config_page.device_bindings):
            subnet = subnet_from_address(binding_address(record) or record.get("last_ip"))
            if subnet:
                return subnet
        instruments = self._config_instruments()
        supplies = instruments.get("power_supplies")
        supplies = supplies if isinstance(supplies, dict) else {}
        entries = [instruments.get("signal_generator"), instruments.get("spectrum_analyzer")]
        entries.extend(supplies.values())
        for item in entries:
            if isinstance(item, dict):
                subnet = subnet_from_address(item.get("address"))
                if subnet:
                    return subnet
        return None

    def _config_instruments(self) -> dict:
        """配置页当前持有的 ``instruments`` 段；缺失或类型不对时返回空字典。"""
        config = getattr(self.config_page, "config", None)
        instruments = config.get("instruments") if isinstance(config, dict) else None
        return instruments if isinstance(instruments, dict) else {}

    @staticmethod
    def _default_scanner_factory() -> Callable[[str], Any]:
        """未注入扫描器时退回装配根的默认实现（离线演示模式为模拟数据）。"""
        from .composition import build_device_scanner_factory

        return build_device_scanner_factory()

    # ---- 生命周期 ---------------------------------------------------------

    def close(self) -> None:
        """关闭页面：协作停止设备扫描线程（幂等，可重复调用）。"""
        panel = getattr(self, "discovery_panel", None)
        if panel is not None and not panel.shutdown():
            self.log_message.emit("设备扫描线程未能在关闭期限内停止")


__all__ = ["ConnectionPage", "PAGE_TITLE", "ACTION_HINT"]
