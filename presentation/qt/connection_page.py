"""仪表配置页：欢迎页选择测试模式之后的第一个整屏页面。

本页只做一件事：扫描网段、勾选本次测试使用的仪器，并在点“下一步”时把所选设备的
当前地址与设备身份（完整设备码）写进配置里的设备绑定。写入边界与派生界面（电源标签页、
电源分配可选项、读入时的坏绑定提示）都归
:class:`presentation.qt.config_page.ConfigPage`：本页只通过窄接口
:class:`application.ports.device_binding.DeviceBindingPort` 读取只读配置视图并提交
:class:`application.device_binding.ApplyDeviceSelectionRequest`，由配置会话协调器交给
配置页的视图模型。本页不持有配置页实例、不写文件、不解析配置文档，也不自己连仪器；
它只从只读视图读出当前绑定与已保存地址，用来填扫描框初值；点“连接仪器”只发出用户意图
（``connect_requested``），由窗口转给配置页的校验入口。

版式分两层：静态骨架（页面标题、居中滚动区、页面动作的声明位置）由 Qt Designer 文件
``presentation/qt/ui/connection_page.ui`` 描述，改排列或文案只动那个文件；动态部分
（设备发现面板、扫描结果卡片，以及“连接仪器／返回欢迎页／下一步”这一行动作）仍在
Python 里构建并插进骨架。两个页面动作虽然声明在 ``.ui`` 的页面动作栏里，但最终和“连接仪器”
一起排进设备发现面板的结果区动作行：那一行才是卡片、“返回”与页面动作共同所在的版面。
页面不写颜色、字号与间距常量：颜色与字号来自 :mod:`presentation.qt.theme`，静态间距在
``.ui`` 里，版心宽度用主题令牌 ``theme.PAGE_CONTENT_MAX_WIDTH`` 收口。

点“查找设备”后，大标题与面板里的扫描网段/方式提示**隐去文字**（扫描行同时禁用），但保留
这一块固定的高度。它是页面主体的锚点：`查找设备` 按钮与点下去后换成的等待动画落在同一个
格子里，若这时把页头从布局里拿掉，主体会整体上移一截，按钮就会跳。**扫描出结果后页头连同
高度一起拿掉**：按钮已经收走，仪器卡片因此可以升到按钮上方、靠近页面顶部，不再和按钮
同高（按钮本身在结果区里被引导间距压在卡片落点之下，两者矩形不重叠）；按“返回”重新查找、
扫描失败或没有结果时页头原样回来。页面主体本身固定在上部
（`connection_page.ui` 的顶部留白只留一小段，富余高度全部落在底部）。

“下一步”先把本次勾选的设备写入配置（整批替换），再切到仪器配置页；配置文件仍随
“保存配置”或“校验并创建运行”一起落盘，因此从本页离开不会产生半保存的配置。
"""

from __future__ import annotations

from typing import Any, Callable, TypeVar

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from application.configuration_view_model import ConfigurationViewState
from application.device_binding import ApplyDeviceSelectionRequest
from application.ports.device_binding import DeviceBindingPort
from application.ports.device_discovery import DEMO_SUBNET

from .config_form_bindings import binding_address, binding_records
from .device_discovery_panel import (
    BOX_STATE_IDLE,
    BOX_STATE_RESULTS,
    DeviceDiscoveryPanel,
    subnet_from_address,
)
from .theme import PAGE_CONTENT_MAX_WIDTH, set_button_variant
from .ui_loader import load_ui

#: 控件查询的返回类型；``.ui`` 里声明的控件必须存在，缺失属于装配错误而不是运行状态。
_Widget = TypeVar("_Widget", bound=QWidget)


class ConnectionPage(QWidget):
    """仪表配置页：整屏页面，不登记进标签栏。

    页面持有大字号标题、设备发现面板，以及结果区动作行里的三个动作（返回欢迎页、
    下一步、连接仪器——它们和面板自己的“返回”排在同一条水平线上）；设备绑定的写入与
    派生界面状态由配置页负责，本页通过
    :class:`application.ports.device_binding.DeviceBindingPort` 读取与提交，不持有
    配置页实例。

    静态骨架来自 ``.ui``，页面只做控件绑定、动态面板插入与信号接线。因此调整按钮顺序、
    文案或静态间距不需要改这个模块，业务边界也不会被视觉改动带着走。
    """

    next_requested = Signal()
    return_welcome_requested = Signal()
    connect_requested = Signal()
    log_message = Signal(str)

    def __init__(
        self,
        binding_port: DeviceBindingPort,
        parent: Any = None,
        scanner_factory: Callable[[str], Any] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("connectionPage")
        self.binding_port = binding_port
        self._scanner_factory = scanner_factory
        self._build_ui()
        self._connect_signals()

    # ---- 界面 -------------------------------------------------------------

    def _build_ui(self) -> None:
        """加载 ``.ui`` 静态骨架，再把设备发现面板与三个动作接到动态位置。"""
        self._ui = load_ui("connection_page")
        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        shell.addWidget(self._ui)

        # 版心宽度是跨页面共用的设计令牌，不写进 ``.ui`` 也不在本页另存一份。
        self.scroll_area = self._require(QScrollArea, "connectionScroll")
        self.content_widget = self._require(QWidget, "connectionContent")
        self.content_widget.setMaximumWidth(PAGE_CONTENT_MAX_WIDTH)
        self.title_label = self._require(QLabel, "connectionPageTitle")

        # 扫描行与结果区属于动态面板（卡片数量、扫描状态都由运行时决定），
        # 所以面板本身仍由 Python 创建，只把它的外层位置交给 ``.ui``。
        host = self._require(QWidget, "discoveryPanelHost")
        host_layout = host.layout()
        if host_layout is None:
            raise RuntimeError("connection_page.ui 的 discoveryPanelHost 缺少布局")
        self.discovery_panel = DeviceDiscoveryPanel(
            scanner_factory=self._scanner_factory or self._default_scanner_factory(),
            default_subnet=self._default_scan_subnet(self.binding_port.view_state()),
            parent=host,
        )
        host_layout.addWidget(self.discovery_panel)
        # 大标题与扫描行在扫描中隐去文字，但保留高度：它们是页面主体的锚点，
        # 按钮、等待动画与结果卡片因此不再上下跳。
        self._setup_header_visibility()
        # 页面动作栏：两个页面动作移进结果区动作行后，这一栏不再有可见内容；保留引用
        # 供视觉契约量取页面上下留白。
        self.page_action_bar = self._require(QWidget, "pageActionBar")
        # 三个动作排在同一行：连接仪器、返回欢迎页、下一步。两个页面级按钮虽然声明在
        # ``.ui`` 的页面动作栏里，但按钮之间的“哪一行”属于动态面板的版面（结果区动作行
        # 才是卡片与“返回”所在的那一行），因此在这里把它们移进同一行。
        self.return_welcome_btn = self._require(QPushButton, "returnWelcomeButton")
        self.next_btn = self._require(QPushButton, "nextButton")
        self.discovery_panel.add_action_widget(self._build_connect_button())
        self.discovery_panel.add_action_widget(self.return_welcome_btn)
        self.discovery_panel.add_action_widget(self.next_btn)
        # 窗口尺寸变化时把结果区高度收进当前窗口。视口第一次拿到真实高度是在显示之后，
        # 所以这里只装监听：每次视口尺寸变化都会按新的可用高度重算一次。
        self.scroll_area.viewport().installEventFilter(self)

    def eventFilter(self, watched: Any, event: Any) -> bool:
        """视口尺寸变化时重新安排结果区；其余事件照常交给基类。"""
        if (
            watched is self.scroll_area.viewport()
            and event.type() == QEvent.Type.Resize
        ):
            self._fit_discovery_panel()
        return super().eventFilter(watched, event)

    def _fit_discovery_panel(self) -> None:
        """按当前视口高度给结果区留出合适的高度。

        结果区原先固定按两行卡片占位：窗口一矮（例如 1366x768 的笔记本），标题、扫描行
        加这块固定高度就超过了视口，页面右侧于是出现滚动条，而屏幕上的组件其实全都看得
        见。这里把“结果区以外的高度”减掉，剩下的才是结果区真正能用的高度：够高时仍按
        两行卡片占位（结果区大小不随扫描状态变化），不够高时收起到一行卡片，卡片多出来
        的行在卡片区内部滚动，页面本身保持不滚动。
        """
        panel = getattr(self, "discovery_panel", None)
        scroll = getattr(self, "scroll_area", None)
        if panel is None or scroll is None:
            return
        viewport_height = scroll.viewport().height()
        content = scroll.widget()
        if viewport_height <= 0 or content is None:
            return
        # 结果区以外的高度（标题、上下留白、扫描行）与视口无关：内容的最小高度减去结果区
        # 当前占用的高度就是这部分固定开销。
        fixed_height = content.minimumSizeHint().height() - panel.results_box.height()
        panel.set_available_height(viewport_height - fixed_height)

    def _require(self, widget_type: type[_Widget], name: str) -> _Widget:
        """取 ``.ui`` 里声明的控件；缺失或类型不符时尽早报装配错误。"""
        widget = self._ui.findChild(widget_type, name)
        if widget is None:
            raise RuntimeError(f"connection_page.ui 缺少控件: {name}")
        return widget

    def _setup_header_visibility(self) -> None:
        """给大标题与设备发现面板的扫描行各挂一层透明度效果。

        页头在三种结果区状态下有不同的版面职责：

        - 未查找：文字可见，按钮停在页头下面；
        - 正在查找：文字隐去但**保留高度**。结果区里的“查找设备”按钮与点下去后换成的
          等待动画落在同一格；若这时把页头从布局里拿掉，主体会上移一截，按钮就会跳一下。
        - 有结果：页头连同高度一起拿掉（见 :meth:`_set_header_hidden`）。按钮已经收走，
          仪器卡片因此能升到按钮上方、靠近页面顶部，不必再和按钮同高。
        """
        self._header_hidden = False
        self._title_opacity = QGraphicsOpacityEffect(self.title_label)
        self._title_opacity.setOpacity(1.0)
        self.title_label.setGraphicsEffect(self._title_opacity)
        self._scan_header_opacity = QGraphicsOpacityEffect(
            self.discovery_panel.scan_header
        )
        self._scan_header_opacity.setOpacity(1.0)
        self.discovery_panel.scan_header.setGraphicsEffect(self._scan_header_opacity)

    def _set_header_text_visible(self, visible: bool) -> None:
        """显示或隐去页头文字：只改透明度与可用性，不改版面。"""
        opacity = 1.0 if visible else 0.0
        self._title_opacity.setOpacity(opacity)
        self._scan_header_opacity.setOpacity(opacity)
        # 隐去后不能再留下一个看不见却可操作的输入区。
        self.discovery_panel.scan_header.setEnabled(visible)

    def _set_header_hidden(self, hidden: bool) -> None:
        """把页头整块拿掉（有结果时）或放回版面（回到查找/扫描时）。

        只有这一处会改变页面的固定高度：有结果时按钮已经收走，页头不必再占着位置，
        仪器卡片因此可以升到页面顶部、高于按钮原来的位置。未查找与正在查找时页头都
        留在版面里，点“查找设备”前后按钮与等待动画不跳。
        """
        hidden = bool(hidden)
        if hidden == self._header_hidden:
            return
        self._header_hidden = hidden
        self.title_label.setVisible(not hidden)
        self.discovery_panel.scan_header.setVisible(not hidden)
        if hidden:
            self._set_header_text_visible(False)
        layout = self.content_widget.layout()
        if layout is not None:
            layout.activate()
        panel_layout = self.discovery_panel.layout()
        if panel_layout is not None:
            panel_layout.activate()
        # 页头让出/收回的高度会改变结果区可用的高度：当场重排，避免卡片压到别处。
        self._fit_discovery_panel()

    def _on_box_state_changed(self, state: str) -> None:
        """有结果时把页头整块拿掉（卡片上移）；其余状态按需隐去或复原文字。"""
        if state == BOX_STATE_RESULTS:
            self._set_header_hidden(True)
            return
        self._set_header_hidden(False)
        self._set_header_text_visible(state == BOX_STATE_IDLE)

    def _build_connect_button(self) -> QPushButton:
        """结果区动作行里的“连接仪器”：与“返回”、返回欢迎页、下一步同一行。"""
        self.connect_btn = QPushButton("连接仪器")
        self.connect_btn.setObjectName("connectButton")
        self.connect_btn.setToolTip(
            "连接本次运行冻结的仪器（地址与身份来自创建运行时的设备绑定）"
        )
        set_button_variant(self.connect_btn, "primary")
        return self.connect_btn

    def _connect_signals(self) -> None:
        self.discovery_panel.box_state_changed.connect(self._on_box_state_changed)
        self.next_btn.clicked.connect(self.request_next)
        self.return_welcome_btn.clicked.connect(self.return_welcome_requested.emit)
        # “连接仪器”只发出用户意图：本页不校验配置、不拼地址、也不持有连接端口。
        # 窗口把 ``connect_requested`` 转给配置页的 ``request_connect``，校验通过后
        # 再由配置页发 ``connect_requested`` 交给协调器真正连仪器。
        self.connect_btn.clicked.connect(self.connect_requested.emit)

    # ---- 语义查询 ---------------------------------------------------------
    #
    # 行为测试和窗口装配只读这些入口，不按 ``.ui`` 的 objectName 找控件。控件层级与
    # 静态文案的契约仍由 ``tests/ui/test_ui_loader.py`` 对 ``.ui`` 单独锁定；改控件名或
    # 调整静态排列时，这里只改实现，业务行为测试不需要跟着改。版心宽度、上下留白这类
    # 视觉几何由视觉契约测试直接量取呈现结果，不在这里新增测试专用查询方法。

    def page_title(self) -> str:
        """页面大标题文案。"""
        return self.title_label.text()

    def action_labels(self) -> tuple[str, str]:
        """两个页面级动作的文案：返回欢迎页、下一步。"""
        return self.return_welcome_btn.text(), self.next_btn.text()

    def page_actions_visible(self) -> tuple[bool, bool]:
        """两个页面级动作当前是否可见（仍按返回、下一步的顺序）。"""
        return self.return_welcome_btn.isVisible(), self.next_btn.isVisible()

    def request_next(self) -> None:
        """写入本次勾选的设备，然后进入“仪器配置”页。

        勾选就是本次测试的设备清单，写入按整批替换：勾了几台，配置里就只留这几台，
        没勾的旧角色与旧电源随地址一起失效。因此不再需要单独的“应用”按钮，也没有
        “清除绑定”这一步。

        - 扫描出了结果：把勾选结果包成
          :class:`application.device_binding.ApplyDeviceSelectionRequest` 交给设备绑定
          端口；拒绝时留在本页，面板状态行说明原因，不跳页。
        - 扫描出了结果却一台都没勾：明确阻止，避免用户以为“没勾选=没改”而带着上一批
          设备继续。
        - 还没扫描：视为本次不更换设备，沿用已保存的绑定，直接进入下一步。
        """
        panel = self.discovery_panel
        if panel.box_state() == BOX_STATE_RESULTS:
            assignments, problems = panel.selected_assignments()
            if problems:
                panel.show_message("；".join(problems))
                return
            if not assignments:
                panel.show_message(
                    "请先勾选本次测试所需的设备（测试角色来自设备表登记，不需要选择）"
                )
                return
            request = ApplyDeviceSelectionRequest.from_assignments(assignments)
            if not self.binding_port.apply_selection(request).ok:
                return
        self.next_requested.emit()

    # ---- 进入页面 ---------------------------------------------------------

    def enter(self) -> None:
        """每次从欢迎页进入本页时按当前配置重置面板。

        上次会话的扫描结果不能留下来冒充本次的发现结果：清空卡片与状态行；扫描框初值
        按当前配置（含刚选定的测试模式）重新取，落在这批设备所在的网段上。只有地址、
        没被本次扫描确认过的旧绑定不再画在结果区里，改为写一条日志，说明它们“未扫描确认”。
        """
        state = self.binding_port.view_state()
        panel = self.discovery_panel
        panel.set_device_bindings(state.bindings_snapshot())
        panel.clear_results()
        panel.subnet_edit.setText(self._default_scan_subnet(state) or DEMO_SUBNET)
        if state.legacy_binding_count:
            self.log_message.emit(
                f"有 {state.legacy_binding_count} 台设备的连接沿用已保存的配置，未扫描确认"
            )

    # ---- 扫描框初值 -------------------------------------------------------

    def _default_scan_subnet(self, state: ConfigurationViewState) -> str | None:
        """扫描框初值：优先已绑定设备的地址，其次配置里已有的仪器地址。"""
        for _key, record in binding_records(state.bindings):
            subnet = subnet_from_address(binding_address(record) or record.get("last_ip"))
            if subnet:
                return subnet
        for address in state.instrument_addresses:
            subnet = subnet_from_address(address)
            if subnet:
                return subnet
        return None

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


__all__ = ["ConnectionPage"]
