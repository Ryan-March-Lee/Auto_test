"""配置页面及其控件联动。

页面拥有配置控件，窗口只通过信号协调保存和连接。该模块只负责控件组合、用户意图
转发和派生界面状态（单元可见性、驱动模式、电源选项）；电源分区的控件构建与标签页
重建在 :mod:`presentation.qt.config_power_section`，把冻结配置投影到控件的规则在
:mod:`presentation.qt.config_projection`，读取控件值组装配置的规则在
:mod:`presentation.qt.config_form_state`。控件读写统一经
:mod:`presentation.qt.config_form_adapter`：页面不再直接调用 ``ConfigFormState``，
``build_config`` / ``apply_config`` 只是 adapter + 视图模型的短委托。
页面把电源分区要显示的条目、可选项与
「槽位 -> 配置路径」的映射（``driver_amplifier.supplies.main`` 等，见
:meth:`ConfigPage._assignment_entries`）读出来交给构建模块，构建模块不判断这些路径。
页面不持有逐字段回填逻辑，也不依赖窗口、测量 controller 或硬件实现。

仪表配置（扫描、勾选设备、设备身份绑定、连接仪器）已独立成
:mod:`presentation.qt.connection_page`：本页不再创建发现面板、不持有它，也不再提供
连接入口，只保留写绑定的边界（整批校验 + 写地址/身份）与派生界面更新。写入结果经
``device_selection_finished`` 交给 :class:`presentation.qt.configuration_session`，
由会话把状态行与绑定回写给连接页的面板；连接请求仍由
:meth:`ConfigPage.request_connect` 校验后经 ``connect_requested`` 交给窗口。

归属（见 `docs/配置领域词汇与模块归属.md`）：Qt 视图。页面只负责控件、布局、信号与派生界面
状态；设备选择的角色唯一性、引用/身份校验与整批替换规则在
:class:`application.device_binding.DeviceBindingService`（步骤 5），
:meth:`ConfigPage.apply_device_selection` 只提交本次勾选结果并投影返回的绑定集合。

页面业务状态由 :class:`application.configuration_view_model.ConfigurationViewModel` 持有
（步骤 6）：测试参数、设备绑定、运行状态、dirty，以及步骤 7 经表单 Adapter 收口的
整份配置读数（电源通道参数与供电分配）。页面把这些读数写进视图模型、再按它返回的
状态渲染（运行状态文案、绑定集合）；页面不再直接读取或组装配置字典。
"""

import copy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from application.configuration_view_model import (
    DEFAULT_RUN_STATUS,
    ConfigurationViewModel,
)
from application.device_binding import ApplyDeviceSelectionRequest
from domain.instruments.labels import instrument_display_label

from .config_form_bindings import (
    binding_records,
    bound_power_supply_entries,
    effective_device_bindings,
    power_supply_binding_problems,
    power_supply_bindings,
)
from .config_form_adapter import ConfigurationFormAdapter
from .config_projection import (
    DEFAULT_ATTENUATOR_TYPE,
    DEFAULT_COMPRESSION_TYPE,
    DEFAULT_DRIVER_MODE_ENABLED,
    DEFAULT_MAX_INPUT_POWER,
    DEFAULT_PA_UNIT_COUNT,
    DEFAULT_SIGNAL_SOURCE,
    DEFAULT_TEST_FREQUENCIES,
    ConfigBackfillError,
    driver_power_source_label,
)
from .config_power_section import (
    PowerAssignmentEntries,
    PowerSupplyChoices,
    PowerSupplySources,
    build_power_section,
    current_power_tab_key,
    rebuild_power_tabs,
)
from .config_page_widgets import build_action_bar, combo_box, spin_box
from .device_discovery_panel import role_label
from .pages import PageControllerBindingMixin
from .theme import PAGE_CONTENT_MAX_WIDTH, set_button_variant

__all__ = [
    "ConfigBackfillError",
    "ConfigPage",
]


class ConfigPage(PageControllerBindingMixin, QWidget):
    save_requested = Signal()
    connect_requested = Signal()
    create_run_requested = Signal()
    load_run_requested = Signal(str)
    return_welcome_requested = Signal()
    connection_step_requested = Signal()
    validation_failed = Signal(str)
    driver_mode_changed = Signal(bool)
    discovery_message = Signal(str)
    #: 一次设备勾选写入的完整结果：(DeviceBindingResult, 面向操作员的提示文案)。
    #: 面板归连接页所有，本页只交出结果；回写由配置会话完成。
    device_selection_finished = Signal(object, str)

    def __init__(self, config=None, parent=None):
        PageControllerBindingMixin.__init__(self)
        QWidget.__init__(self, parent)
        self.config = config if isinstance(config, dict) else {}
        # 设备身份绑定：信号源/频谱仪各一条（角色键），电源按 ``supply_id`` 多条
        # （``power_supplies``）。只记录本次勾选并写入的设备；IP 变化时按完整设备码重新识别，
        # 不靠地址记忆。绑定是仪器连接的唯一来源；构建界面之前就要就位，电源标签页的
        # 条目与电源分配的可选项都由它决定。
        self.device_bindings = effective_device_bindings(self.config)
        # 配置里原本就不可用的电源绑定：读入时会从可用集合里剔除，但必须在这里留下记录，
        # 由界面提示并在保存/运行校验时明确报错——否则它们会表现为“电源莫名消失”。
        self.binding_problems = self._binding_problems_of(self.config)
        # 页面业务状态由 application 的视图模型持有（步骤 6）：测试参数、设备绑定、运行
        # 状态与 dirty 都在那里；设备选择的业务规则仍只在 DeviceBindingService（步骤 5）。
        self.view_model = ConfigurationViewModel()
        self.view_model.load(self.config, bindings=self.device_bindings)
        self.build_ui()
        self._connect_signals()
        self.update_pa_unit_ui()
        self.update_driver_power_ui()
        self.update_power_supply_options()
        # 基线用组装结果而不是原始文档：旧配置迁移、绑定写入的启用状态都属于页面自身的
        # 规范化结果，刚打开的页面不应显示为“已修改”。
        self._dirty_baseline = copy.deepcopy(self.build_config(self.config))
        # 视图模型以同一份组装结果作为干净基线：建页时写出的默认值不算用户修改。
        self.view_model.mark_clean()

    @staticmethod
    def _binding_problems_of(config) -> list[str]:
        """列出一份配置里不可用的电源绑定（读入边界与加载路径共用）。"""
        devices = config.get("device_bindings") if isinstance(config, dict) else None
        return [
            f"设备绑定不可用：{problem}"
            for problem in power_supply_binding_problems(devices)
        ]

    @property
    def form_adapter(self) -> ConfigurationFormAdapter:
        """配置页表单 Adapter（控件读写、回填投影与错误提示的唯一入口）。

        Adapter 是无状态的，每次按当前页面创建：它只保存页面引用与页面的错误通道，
        不缓存控件值。按需创建也避免了“页面 -> adapter -> 页面”的引用环——Qt 控件
        对象必须在主线程析构，不能留给后台扫描线程触发循环垃圾回收。
        """
        return ConfigurationFormAdapter(
            self, on_error=self.validation_failed.emit
        )

    def build_ui(self):
        """按页面协议构建并返回配置页根 widget。"""
        self._build_ui()
        return self

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        content = QWidget()
        # 限制内容最大宽度：窗口过宽时不再把卡片横向拉伸成很长的边框。宽度是跨页面
        # 共用的主题令牌，连接页的 ``.ui`` 与这里取同一个值。
        content.setMaximumWidth(PAGE_CONTENT_MAX_WIDTH)
        # 两栏布局：左侧仪器连接/测试参数/运行，右侧电源配置。避免单个卡片
        # 随视口无限横向拉伸，同时降低页面总高度。
        columns = QHBoxLayout(content)
        columns.setContentsMargins(0, 0, 4, 0)
        columns.setSpacing(12)
        left_column = QVBoxLayout()
        left_column.setSpacing(12)
        right_column = QVBoxLayout()
        right_column.setSpacing(12)
        columns.addLayout(left_column, 3)
        columns.addLayout(right_column, 2)
        root.addWidget(scroll, 1)
        scroll.setWidget(content)

        instruments = self.config.get("instruments")
        instruments = instruments if isinstance(instruments, dict) else {}
        supplies = instruments.get("power_supplies")
        supplies = supplies if isinstance(supplies, dict) else {}

        # 设备发现与“连接仪器”都在“仪表配置”页（欢迎页之后的第一步）：本页只配置测试
        # 参数与运行，设备地址与身份来自那一页写入的设备绑定，页面不再提供手工填写地址
        # 的控件，也不再有连接入口。
        params = QGroupBox("测试参数配置")
        form = QGridLayout(params)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        # 标签列定宽、输入列吃满剩余宽度：两列表单的标签才会上下对齐。
        form.setColumnMinimumWidth(0, 118)
        form.setColumnMinimumWidth(2, 118)
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)
        test = self.config.get("signal_source", {})
        if not isinstance(test, dict): test = {}
        self.freq_edit = QLineEdit(str(self.config.get("test_frequencies", DEFAULT_TEST_FREQUENCIES)))
        self.start_power = spin_box(test.get("start_power", DEFAULT_SIGNAL_SOURCE["start_power"]), -50, 20)
        self.stop_power = spin_box(test.get("stop_power", DEFAULT_SIGNAL_SOURCE["stop_power"]), -50, 20)
        self.power_step = spin_box(test.get("step", DEFAULT_SIGNAL_SOURCE["step"]), 0.1, 5)
        compression = self.config.get("compression_point", {})
        attenuator = self.config.get("attenuator", {})
        if not isinstance(compression, dict): compression = {}
        if not isinstance(attenuator, dict): attenuator = {}
        self.compression_combo = combo_box(("1dB", "3dB", "5dB"), compression.get("type", DEFAULT_COMPRESSION_TYPE))
        self.attenuator_combo = combo_box(("30dB", "40dB"), attenuator.get("type", DEFAULT_ATTENUATOR_TYPE))
        mode = self.config.get("driver_mode", {})
        mode = mode if isinstance(mode, dict) else {}
        self.test_mode_enabled = bool(mode.get("enabled", DEFAULT_DRIVER_MODE_ENABLED))
        assignment = self.config.get("power_supply_assignment", {})
        driver = assignment.get("driver_amplifier") if isinstance(assignment, dict) else {}
        driver = driver if isinstance(driver, dict) else {}
        self.driver_power_source = driver_power_source_label(
            mode.get("power_source"), driver.get("power_supply_count")
        )
        dut = self.config.get("dut_config", {})
        if not isinstance(dut, dict): dut = {}
        self.max_input_power = spin_box(dut.get("max_input_power", DEFAULT_MAX_INPUT_POWER), 0, 50)
        self.pa_unit_count_combo = combo_box(("1", "2", "3"), str(dut.get("power_supply_count", DEFAULT_PA_UNIT_COUNT)))
        parameter_rows = (("测试频率 (GHz)", self.freq_edit), ("起始功率 (dBm)", self.start_power),
                          ("结束功率 (dBm)", self.stop_power), ("功率步长 (dB)", self.power_step),
                          ("压缩点", self.compression_combo), ("衰减器", self.attenuator_combo),
                          ("DUT最大输入功率 (dBm)", self.max_input_power), ("PA单元数量", self.pa_unit_count_combo))
        for index, (label, widget) in enumerate(parameter_rows):
            row, column = divmod(index, 2)
            field_label = QLabel(label)
            field_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            form.addWidget(field_label, row, column * 2)
            form.addWidget(widget, row, column * 2 + 1)
        freq_hint = QLabel("测试频率以 GHz 为单位，用逗号分隔多个频点。")
        freq_hint.setProperty("role", "hint")
        form.addWidget(freq_hint, 4, 0, 1, 4)
        left_column.addWidget(params)
        self._build_power_widgets(right_column, supplies)
        # 运行入口紧随测试参数，保存与返回放在页面底部固定操作栏。
        self._build_run_widgets(left_column)
        left_column.addStretch(1)
        right_column.addStretch(1)
        root.addWidget(self._build_action_bar())

    def _build_action_bar(self):
        """页面底部固定操作栏：回到仪表配置、保存当前配置或返回欢迎页。"""
        return build_action_bar(self)

    def view_state(self):
        """返回连接页读取的只读配置视图。

        连接页与配置会话只经这里读绑定、已保存的仪器地址和旧绑定条数，不碰页面的
        ``config`` / ``device_bindings`` 内部字典。
        """
        return self.view_model.view_state()

    def apply_device_selection(self, request):
        """把“应用本次勾选结果”的命令写成配置里的连接信息与身份记录。

        角色不是用户选的：它来自设备表 ``instruments.json`` 登记的 ``role``（旧槽位
        ``PS1``..``PS4`` 由面板折叠成 ``power_supply``），面板按它交付。写入规则（角色
        唯一性、电源引用、地址/身份、同一台设备不重复与整批替换）都在
        :class:`application.device_binding.DeviceBindingService`；页面只把请求交给
        :meth:`ConfigurationViewModel.apply_device_selection`，再把返回的新绑定集合投影
        到页面状态（绑定、电源标签页、分配选项）。

        成功与失败都返回 :class:`application.device_binding.DeviceBindingResult`，并发出
        ``device_selection_finished(result, message)``：面板归连接页所有，本页不持有它，
        由配置会话把提示与绑定回写到面板。``ok`` 为假表示整批拒绝（调用方留在本页）；
        为真表示绑定已整批替换，可以继续跳页。
        """
        if not isinstance(request, ApplyDeviceSelectionRequest):
            raise TypeError(
                "request 必须是 ApplyDeviceSelectionRequest，"
                f"收到 {type(request).__name__}"
            )
        outcome = self.view_model.apply_device_selection(request)
        result = outcome.result
        if not result.ok:
            # 面板与日志看到的是业务文案本身（不带结构化问题的路径前缀）。
            message = "；".join(issue.message for issue in result.issues)
            self.validation_failed.emit(message)
            self.device_selection_finished.emit(result, message)
            return result

        previous = self._binding_labels(self.device_bindings)
        self.device_bindings = outcome.state.bindings_snapshot()
        # 这一批记录刚刚通过全部校验：之前读入时的坏绑定要么被替换、要么已被移除。
        self.binding_problems = []
        self._rebuild_power_tabs()
        self.update_power_supply_options()
        message = "已应用发现设备: " + "，".join(
            self._applied_labels(result.applied)
        )
        current = self._binding_labels(self.device_bindings)
        removed = [label for label in previous if label not in set(current)]
        if removed:
            message += "；已移除本次未勾选的旧绑定: " + "、".join(removed)
        if result.simulated:
            message += "（模拟数据，仅用于离线演示，不能用于真实测量）"
        self.discovery_message.emit(message)
        self.device_selection_finished.emit(result, message)
        return result

    @staticmethod
    def _applied_labels(applied) -> list[str]:
        """成功摘要里的设备名：角色显示名 + 型号/完整设备码/地址，电源只写引用 ID。"""
        labels: list[str] = []
        for item in applied:
            if item.supply_id:
                labels.append(f"电源 {item.supply_id}")
            else:
                name = item.model or item.full_code or item.ip
                labels.append(f"{role_label(item.role) or item.role} {name}")
        return labels

    @staticmethod
    def _binding_labels(bindings) -> list[str]:
        """把绑定结构显示成“信号发生器 SMW200A（5462） / 电源 DP8B1519”这类可读标签。"""
        labels: list[str] = []
        for key, record in binding_records(bindings):
            if key in ("signal_generator", "spectrum_analyzer"):
                text = instrument_display_label(record.get("label"), record.get("model"))
                labels.append(f"{role_label(key) or key} {text}".strip())
            else:
                labels.append(f"电源 {key}")
        return labels

    def _build_power_widgets(self, layout, supplies):
        """建电源分区：把页面状态（条目、分配引用、可选项、供电方式）交给构建模块。

        电源数量与身份都由设备绑定决定，不再有 ``PS1``..``PS4`` 固定页；标签页标题、
        通道控件与分配表单的布局见 :mod:`presentation.qt.config_power_section`。
        """
        build_power_section(
            self,
            layout,
            sources=self._power_supply_sources(supplies),
            choices=self._power_supply_choices(),
            assignment_entries=self._assignment_entries(),
            driver_power_source=self.driver_power_source,
        )

    def _assignment_entries(self) -> PowerAssignmentEntries:
        """电源分配各槽位在配置里的引用条目；``None`` 表示配置没有写这条分配。

        “条目缺失”与“声明了但引用为空”在回填时是两回事：前者按缺省规则选第一台可用
        电源，后者保持空选择，因此这里不把 ``None`` 换成空字典。

        「槽位 -> 配置路径」（``driver_amplifier.supplies.main``、
        ``dut_amplifier.supplies.carrier/peaking/peaking2``）只在这里：构建模块拿到的是
        已经取好的引用，不再读这些路径。
        """
        existing = self.config.get("power_supply_assignment", {})
        if not isinstance(existing, dict): existing = {}
        driver = existing.get("driver_amplifier", {}); dut = existing.get("dut_amplifier", {})
        if not isinstance(driver, dict): driver = {}
        if not isinstance(dut, dict): dut = {}
        driver_supply = driver.get("supplies", {}); dut_supply = dut.get("supplies", {})
        if not isinstance(driver_supply, dict): driver_supply = {}
        if not isinstance(dut_supply, dict): dut_supply = {}
        return {
            "driver_power": driver_supply.get("main"),
            "pa_unit1_power": dut_supply.get("carrier"),
            "pa_unit2_power": dut_supply.get("peaking"),
            "pa_unit3_power": dut_supply.get("peaking2"),
        }

    def _power_supply_sources(self, supplies=None) -> PowerSupplySources:
        """电源标签页要显示的条目：配置里已有的键 + 本次已绑定的 ``supply_id``。

        配置条目保留各自未知字段与通道参数；绑定记录只补身份字段（短标签、型号、序列
        号、设备键），不覆盖配置里已有的值。这样“更换电源”不会沿用上一台的身份，而
        “重装页面”也不会因为配置里已有同名字段而丢掉绑定来源。绑定记录的型号比配置里
        的旧值更可信（它是这台设备本次扫描读到的型号），因此标签页标题优先用它。
        """
        if supplies is None:
            instruments = self.config.get("instruments")
            instruments = instruments if isinstance(instruments, dict) else {}
            supplies = instruments.get("power_supplies")
        sources: dict[str, dict] = {}
        if isinstance(supplies, dict):
            for key, item in supplies.items():
                if isinstance(item, dict):
                    sources[str(key)] = dict(item)
        for supply_id, record in power_supply_bindings(self.device_bindings).items():
            entry = dict(sources.get(supply_id) or {})
            for field in ("label", "model", "full_code", "device_key"):
                value = record.get(field)
                if isinstance(value, str) and value.strip():
                    entry[field] = value
            entry.setdefault("label", supply_id)
            sources[supply_id] = entry
        return sources

    def _rebuild_power_tabs(self, *, sources=None, preserve=True) -> None:
        """把重建标签页要用的页面状态读出来，交给构建模块。

        条目来源（配置键 + 本次绑定）与控件里的通道参数都是页面状态，所以在这里取；
        合并规则、按电源键恢复选中页与逐页控件构建都在
        :mod:`presentation.qt.config_power_section`。``preserve=False`` 是回填路径，以
        新配置为准，不沿用重建前的残留值。通道读数经表单 Adapter 读取，页面不再直接
        调用 ``ConfigFormState``。本方法是同名保留的委托壳：
        ``tests/ui/test_config_page.py`` 直接调用它。
        """
        if getattr(self, "power_tabs", None) is None:
            return
        rebuild_power_tabs(
            self,
            sources=self._power_supply_sources() if sources is None else sources,
            preserved=(
                self.form_adapter.read_power_channel_snapshot() if preserve else {}
            ),
        )

    def _current_power_tab_key(self) -> str | None:
        """当前选中电源标签页的电源键；同名委托壳（测试直接调用）。"""
        return current_power_tab_key(self)

    def _build_run_widgets(self, layout):
        """运行入口：校验并创建一次运行，或按 run_id 重新打开已有运行。

        保存配置与返回欢迎页放在页面底部固定操作栏（``_build_action_bar``），
        运行入口留在左栏，便于“改参数 → 创建运行”就近操作。
        """
        # 配置阶段入口：校验并创建一次运行。运行创建后测量页面只消费该不可变快照。
        run_group = QGroupBox("运行")
        run_layout = QVBoxLayout(run_group)
        run_layout.setSpacing(8)
        self.create_run_btn = QPushButton("校验并创建运行")
        set_button_variant(self.create_run_btn, "primary")
        self.run_status_label = QLabel(DEFAULT_RUN_STATUS)
        self.run_status_label.setWordWrap(True)
        self.run_status_label.setProperty("tone", "muted")
        self.load_run_id_edit = QLineEdit()
        self.load_run_id_edit.setPlaceholderText("输入 run_id 以重新打开已有运行")
        self.load_run_btn = QPushButton("加载运行")
        set_button_variant(self.load_run_btn, "secondary")
        load_row = QHBoxLayout()
        load_row.setSpacing(8)
        load_row.addWidget(self.load_run_id_edit, 1)
        load_row.addWidget(self.load_run_btn)
        run_layout.addWidget(self.create_run_btn)
        run_layout.addWidget(self.run_status_label)
        run_layout.addLayout(load_row)
        layout.addWidget(run_group)

    def _connect_signals(self):
        self.save_config_btn.clicked.connect(self.request_save)
        self.create_run_btn.clicked.connect(self.request_create_run)
        self.load_run_btn.clicked.connect(self.request_load_run)
        self.return_welcome_btn.clicked.connect(self.return_welcome_requested.emit)
        self.previous_step_btn.clicked.connect(self.connection_step_requested.emit)
        self.pa_unit_count_combo.currentTextChanged.connect(self.on_pa_unit_count_changed)
        self.driver_power_source_combo.currentTextChanged.connect(self.on_driver_power_toggled)

    def request_save(self):
        if self.validate(): self.save_requested.emit()

    def request_connect(self):
        """连接入口（按钮在“仪表配置”页）：校验通过后由窗口连仪器。"""
        if self.validate(): self.connect_requested.emit()

    def request_create_run(self):
        if self.validate(): self.create_run_requested.emit()

    def request_load_run(self):
        run_id = self.load_run_id_edit.text().strip()
        if not run_id:
            self.validation_failed.emit("请输入要加载的 run_id")
            return
        self.load_run_requested.emit(run_id)

    def set_run_status(self, text: str) -> None:
        """显示当前运行状态；由窗口在创建/加载运行后更新。

        运行状态只写进视图模型（步骤 6），标签文案从返回的状态渲染：页面不再是运行状态
        的第二份来源。
        """
        state = self.view_model.set_run_status(text)
        self.run_status_label.setText(state.run_status)

    def validate(self):
        """校验当前表单；先同步整份控件读数，再汇总页面错误。

        控件读数经表单 Adapter 收进视图模型（步骤 7），错误文案与顺序保持原样；
        “用户刚输入的值、页面校验看到的值、视图模型持有的值”来自同一次读数。
        """
        # 读入时判定为不可用的设备绑定先报出来：它们已经从可用电源里剔除，若不在这里
        # 明确失败，保存就会把对应条目的地址清空，看起来像“电源无故消失”。
        self.build_config()
        errors = list(getattr(self, "binding_problems", []))
        errors.extend(self.form_adapter.validation_errors())
        return self.form_adapter.report_errors(errors)

    def on_pa_unit_count_changed(self, _value): self.update_pa_unit_ui()
    def update_pa_unit_ui(self):
        count = int(self.pa_unit_count_combo.currentText())
        for index in (2, 3):
            visible = count >= index
            self.power_assignment_widgets[f"pa_unit{index}_power"].setVisible(visible)
            self.power_assignment_widgets[f"pa_unit{index}_label"].setVisible(visible)
    def on_driver_power_toggled(self, _value): self.update_driver_power_ui()
    def update_driver_power_ui(self):
        enabled = self.test_mode_enabled and self.driver_power_source_combo.currentText() == "直流电源"
        self.driver_power_combo.setEnabled(enabled); self.driver_power_label.setEnabled(enabled)
        self.driver_power_hint.setVisible(self.test_mode_enabled and not enabled)
        if not enabled: self.driver_power_combo.setCurrentIndex(-1)
    def get_enabled_power_supplies(self):
        """电源分配里可选的电源：只来自当前已应用的设备绑定。

        “启用”不再由界面上的勾选框决定：``instruments.power_supplies.<name>.enabled``
        由绑定写入，因此可选项与保存出的配置使用同一个来源，不存在“界面能选、配置里
        却未启用”的错位。
        """
        return list(self._power_supply_choices())
    def update_power_supply_options(self):
        """刷新电源分配下拉框：只保留当前绑定的电源，失效引用保持空选择。

        选项按 ``supply_id`` 重建，显示文本是型号加缩写设备码（``DP832A（DP8B1519）``），
        业务值放在 ``itemData``。刷新按**数据值**恢复上一次的选择，不按位置、不按可见
        文本：``QComboBox.clear()`` 会把当前项重置到第一项，按位置保存就会把“这台电源
        已经不在”变成一次看不见的分配改动。

        配置引用的电源不再绑定时不能被静默换成列表里的第一台——空选择交给校验明确报告，
        由操作员重新选择。刷新动作本身由表单 Adapter 完成（步骤 7）。
        """
        self.form_adapter.refresh_power_choices(self._power_supply_choices())

    def _power_supply_choices(self) -> PowerSupplyChoices:
        """电源分配下拉框的可选项：当前绑定的电源，带真实设备的型号用于显示。"""
        return bound_power_supply_entries(self.device_bindings)

    def build_config(self, base=None):
        """按当前控件值组装完整配置（控件读数经表单 Adapter）。

        ``base`` 是组装基线（缺省为页面自己的配置副本）。运行协调器传入它持有的
        权威配置，保证“页面只是视图、配置只有一个数据源”。整份文档与测试参数快照
        来自表单 Adapter 的**同一次**读数，随后一起写进视图模型（步骤 7），因此
        页面业务状态、dirty 与保存文档不会出现两套读数；设备身份绑定仍随配置一起保存。
        """
        reading = self.form_adapter.read_form(base)
        config = reading.document
        self.view_model.update_form(config, bindings=self.device_bindings)
        # 测试参数是视图模型的类型化快照：非法频率文本不进入快照，页面校验仍按原文案
        # 报告（步骤 6 的读数边界，步骤 7 保持），但快照与上面的文档来自同一次读数。
        self.view_model.update_test_parameters(reading.test_parameters)
        # 设备身份绑定随配置一起保存：角色 -> 完整设备码等稳定标识。各仪器的 address 由
        # 绑定里的当前地址写入（当前测量链路按地址连接），身份用于下次扫描时按完整设备码
        # 重新识别同一台设备，而不是依赖可能变化的 IP。
        if self.device_bindings:
            config["device_bindings"] = copy.deepcopy(self.device_bindings)
        else:
            # 页面没有绑定时清掉基线里的旧绑定，避免保存出“地址已换、身份还是上一台”的配置。
            config.pop("device_bindings", None)
        return config

    def set_test_mode(self, enabled: bool) -> None:
        """设置欢迎页选择的测试模式并同步供电选项。"""
        self.test_mode_enabled = bool(enabled)
        mode = self.config.get("driver_mode", {})
        mode = dict(mode) if isinstance(mode, dict) else {}
        assignment = self.config.get("power_supply_assignment", {})
        driver = assignment.get("driver_amplifier", {}) if isinstance(assignment, dict) else {}
        driver = driver if isinstance(driver, dict) else {}
        # 与建页、回填同一个推断：``power_source`` 缺失时看驱动功放分配。
        self.driver_power_source = driver_power_source_label(
            mode.get("power_source"), driver.get("power_supply_count")
        )
        self.driver_power_source_combo.setCurrentText(self.driver_power_source)
        self.power_assignment_group.setVisible(self.test_mode_enabled)
        self.update_driver_power_ui()
        self._dirty_baseline = copy.deepcopy(self.build_config(self.config))
        # 切换测试模式本身不算“已修改”：页面与视图模型同时重置干净基线。
        self.view_model.mark_clean()
        self.driver_mode_changed.emit(self.test_mode_enabled)

    def is_dirty(self) -> bool:
        """当前控件读数是否与上次干净基线不同。

        视图模型的 dirty 覆盖步骤 6 已接入的测试参数与设备绑定；整份组装结果的比较仍
        负责尚未接入视图模型的电源通道参数与供电分配（步骤 7 收口）。先组装再比较，
        保证视图模型在判定前已经看到本次控件读数。
        """
        current = self.build_config(self.config)
        return self.view_model.dirty or current != self._dirty_baseline

    def mark_clean(self) -> None:
        self._dirty_baseline = copy.deepcopy(self.build_config(self.config))
        self.view_model.mark_clean()

    def driver_mode_requested(self) -> bool:
        """返回欢迎页选择的驱放测试模式。"""
        return bool(self.test_mode_enabled)

    # -- 冻结运行配置回填 ---------------------------------------------------
    def apply_config(self, config) -> None:
        """把一份已冻结的配置编写文档确定性地投影到控件。

        投影规则（重置到空配置基线、逐字段读取编写文档字段、非法类型回落默认值）都在
        :func:`presentation.qt.config_projection.project_authoring_config` 里，由
        :class:`ConfigurationFormAdapter` 调用；页面只负责刷新派生界面状态并把结果交给
        视图模型。参数不是 JSON 对象（``dict``）时抛出 :class:`ConfigBackfillError`，
        不会被静默当作回填成功；全部投影成功后才更新 ``self.config``，避免部分失败时
        页面与文档副本互相矛盾。

        只更新控件显示，不写文件、不保存、不连接仪器；运行期间的测量输入仍来自
        运行目录内的冻结快照。正式 ``test_plan`` / ``run_mapping`` 由
        :class:`application.configuration_authoring.ConfigurationAuthoringService`
        生成，本页面不读取也不解释它们。
        """
        # 先算绑定，再用它决定电源分配的可选项与电源标签页：投影不允许把配置引用的
        # 电源临时塞进下拉框，否则一份指向“没有绑定设备”的电源配置会被静默保存下来；
        # 标签页也按新配置的电源键与绑定重建，投影才有对应控件可写。
        # 重建属于“回填动作”：投影失败时把绑定与标签页恢复原样，页面不留在半新半旧的
        # 状态（``self.config`` 本来就只在投影成功后才更新）。
        bindings = effective_device_bindings(config)
        instruments = config.get("instruments") if isinstance(config, dict) else {}
        instruments = instruments if isinstance(instruments, dict) else {}
        supplies = instruments.get("power_supplies")
        supplies = supplies if isinstance(supplies, dict) else {}
        previous_bindings = self.device_bindings
        previous_problems = self.binding_problems
        previous_driver_source = self.driver_power_source
        self.device_bindings = bindings
        # 供电方式必须先按新配置算好：投影不另立一套推断，它读的就是这个页面状态。
        mode = config.get("driver_mode") if isinstance(config, dict) else None
        mode = mode if isinstance(mode, dict) else {}
        assignment = config.get("power_supply_assignment") if isinstance(config, dict) else None
        assignment = assignment if isinstance(assignment, dict) else {}
        driver = assignment.get("driver_amplifier")
        driver = driver if isinstance(driver, dict) else {}
        self.driver_power_source = driver_power_source_label(
            mode.get("power_source"), driver.get("power_supply_count")
        )
        try:
            self._rebuild_power_tabs(
                sources=self._power_supply_sources(supplies),
                preserve=False,
            )
            projected = self.form_adapter.apply_configuration(
                config, supplies=self._power_supply_choices()
            )
        except ConfigBackfillError:
            self.device_bindings = previous_bindings
            self.binding_problems = previous_problems
            self.driver_power_source = previous_driver_source
            self._rebuild_power_tabs(sources=self._power_supply_sources(), preserve=False)
            raise
        self.config = projected
        self.binding_problems = self._binding_problems_of(config)
        # 面板归连接页所有：窗口在回填后调用 ``ConnectionPage.enter()``，由它按新的
        # 只读视图重置面板；本页只在日志里报告读入时就不可用的绑定。
        if self.binding_problems:
            self.discovery_message.emit("；".join(self.binding_problems))
        self.update_pa_unit_ui()
        self.update_driver_power_ui()
        self.update_power_supply_options()
        # 基线与视图模型都取“页面对这份文档的组装结果”：控件缺省值（例如驱放供电方式）
        # 属于页面自身的规范化结果，刚回填完的页面不算已修改。
        built = self.build_config(self.config)
        self._dirty_baseline = copy.deepcopy(built)
        self.view_model.load(built, bindings=self.device_bindings)
