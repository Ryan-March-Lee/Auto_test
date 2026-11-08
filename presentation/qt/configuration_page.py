"""配置页面及其控件联动（``ConfigurationPage``）。

模块与页面类都使用领域名称“configuration”。

页面拥有配置控件，窗口只通过信号协调保存和连接。该模块只负责控件组合、用户意图
转发和派生界面状态（驱动模式）；电源参数、通道上电计划与漏极已移入
:mod:`presentation.qt.power_configuration_page`，本页不再创建或重建电源控件，也不再
读取计划候选。把冻结配置投影到控件的规则在
:mod:`presentation.qt.config_projection`，读取控件值组装配置的规则在
:mod:`presentation.qt.config_form_state`。控件读写统一经
:mod:`presentation.qt.config_form_adapter`：页面不再直接调用 ``ConfigFormState``，
``build_config`` / ``apply_config`` 只是 adapter + 视图模型的短委托。
页面不持有逐字段回填逻辑，也不依赖窗口、测量 controller 或硬件实现。

仪表配置（扫描、勾选设备、设备身份绑定）已独立成
:mod:`presentation.qt.instrument_setup_page`：本页不再创建发现面板、不持有它，也不再提供
设备发现入口，只保留写绑定的边界（整批校验 + 写地址/身份）与派生界面更新。写入结果经
``device_selection_finished`` 交给 :class:`presentation.qt.configuration_session`，
由会话把状态行与绑定回写给仪表配置页的面板。连接仪器不再有独立入口：页面底部的最终
按钮一次完成“校验 → 保存配置 → 释放旧端口 → 冻结待提交运行 → 完整只读连接检查”，
与窗口的信号接线是 ``finalize_run_requested``。连接成功前运行只是内部待提交对象，
不会成为测量页可消费的正式运行。

归属：Qt 视图。页面只负责控件、布局、信号与派生界面
状态；设备选择的角色唯一性、引用/身份校验与整批替换规则在
:class:`application.device_binding.DeviceBindingService`（步骤 5），
:meth:`ConfigurationPage.apply_device_selection` 只提交本次勾选结果并投影返回的绑定集合。

页面业务状态由 :class:`application.configuration_view_model.ConfigurationViewModel` 持有
（步骤 6）：测试参数、设备绑定、运行状态与 dirty。本页只把测试参数与连接信息的读数
写进视图模型、再按它返回的状态渲染（运行状态文案、绑定集合）；电源片段由独立电源页
提交，窗口用 :class:`presentation.qt.configuration_editor.ConfigurationEditor` 统一
回填/切换模式/汇总 dirty，协调器负责合并整份草稿与最终校验（步骤 13）。

测试频率不再逐点输入：页面提供“起始/结束/步长/增补”四个控件，由
:mod:`presentation.qt.frequency_sweep` 生成配置文档仍使用的 ``test_frequencies``
列表。结束频率必须落在步长上；旧配置里非等步长的列表不做兼容拆分，页面保留原列表
并禁止保存，直到用户重新输入合法扫描参数。
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
    effective_device_bindings,
    power_supply_binding_problems,
)
from .config_form_adapter import ConfigurationFormAdapter
from .config_projection import (
    DEFAULT_ATTENUATOR_TYPE,
    DEFAULT_COMPRESSION_TYPE,
    DEFAULT_DRIVER_MODE_ENABLED,
    DEFAULT_FREQUENCY_EXTRA_TEXT,
    DEFAULT_FREQUENCY_START,
    DEFAULT_FREQUENCY_STEP,
    DEFAULT_FREQUENCY_STOP,
    DEFAULT_MAX_INPUT_POWER,
    DEFAULT_PA_UNIT_COUNT,
    DEFAULT_SIGNAL_SOURCE,
    ConfigBackfillError,
    apply_frequency_fields,
)
from .config_page_widgets import build_action_bar, combo_box, spin_box
from .frequency_sweep import (
    FrequencySweepError,
    format_frequency,
    generate_frequencies,
    parse_extra_frequencies,
)
from .instrument_discovery_panel import role_label
from .pages import PageControllerBindingMixin
from .theme import PAGE_CONTENT_MAX_WIDTH, set_button_variant, set_tone

__all__ = [
    "ConfigBackfillError",
    "ConfigurationPage",
]


class ConfigurationPage(PageControllerBindingMixin, QWidget):
    save_requested = Signal()
    finalize_run_requested = Signal()
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
        # 1.0 编辑文档（或其他不可写版本）只读：禁用参数与保存/最终入口，保留回退入口。
        self.read_only_reason: str | None = None
        self._read_only_enabled: dict[QWidget, bool] = {}
        # 最终动作进行中：页面不接受重复提交，参数与回退入口都禁用，直到窗口把结果交回。
        self._finalizing = False
        # 端口丢失时最终按钮改为“重新连接仪器”：不保存配置、不创建新运行。
        self._reconnect_required = False
        # 进入最终动作前这些控件各自的可用状态；退出时按原样恢复，不能一律启用。
        self._finalizing_enabled: dict[QWidget, bool] = {}
        # 设备身份绑定：信号源/频谱仪各一条（角色键），电源按 ``supply_id`` 多条
        # （``power_supplies``）。只记录本次勾选并写入的设备；IP 变化时按完整设备码重新识别，
        # 不靠地址记忆。绑定是仪器连接的唯一来源；构建界面之前就要就位，电源卡片的
        # 条目与上电计划候选都由它决定。
        self.device_bindings = effective_device_bindings(self.config)
        # 配置里原本就不可用的电源绑定：读入时会从可用集合里剔除，但必须在这里留下记录，
        # 由界面提示并在保存/运行校验时明确报错——否则它们会表现为“电源莫名消失”。
        self.binding_problems = self._binding_problems_of(self.config)
        # 频率扫描控件与旧配置的只读展示状态：非等步长列表会在这里留下错误，页面禁止
        # 保存，直到用户重新输入合法的起始/结束/步长/增补。
        self.frequency_import_error: str | None = None
        # 页面业务状态由 application 的视图模型持有（步骤 6）：测试参数、设备绑定、运行
        # 状态与 dirty 都在那里；设备选择的业务规则仍只在 DeviceBindingService（步骤 5）。
        self.view_model = ConfigurationViewModel()
        self.view_model.load(self.config, bindings=self.device_bindings)
        self.build_ui()
        if "test_frequencies" in self.config:
            apply_frequency_fields(self, self.config.get("test_frequencies"))
        self._connect_signals()
        self._refresh_frequency_preview()
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
        # 单列布局：测试参数与运行入口都在这里；电源参数、分配与上电计划已移入
        # :mod:`presentation.qt.power_configuration_page`，本页不再持有电源控件。
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 4, 0)
        column.setSpacing(12)
        root.addWidget(scroll, 1)
        scroll.setWidget(content)
        page_column = column
        # 1.0 编辑文档只能查看：提示固定显示在参数区上方，不靠一次模态对话框。
        self.read_only_notice = QLabel()
        self.read_only_notice.setObjectName("configurationReadOnlyNotice")
        self.read_only_notice.setProperty("tone", "warning")
        self.read_only_notice.setWordWrap(True)
        self.read_only_notice.setVisible(False)
        page_column.addWidget(self.read_only_notice)

        # 设备发现与设备勾选都在“仪表配置”页（欢迎页之后的第一步）：本页只配置测试
        # 参数与运行，设备地址与身份来自那一页写入的设备绑定，页面不再提供手工填写地址
        # 的控件，也不再有设备发现入口。
        params = QGroupBox("测试参数配置")
        self.test_parameters_group = params
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
        self.freq_start = spin_box(
            DEFAULT_FREQUENCY_START, 0.0, 1000.0, decimals=6, single_step=0.1
        )
        self.freq_start.setToolTip("频率扫描起始值，单位 GHz")
        self.freq_stop = spin_box(
            DEFAULT_FREQUENCY_STOP, 0.0, 1000.0, decimals=6, single_step=0.1
        )
        self.freq_stop.setToolTip("结束频率是扫描上限，必须等于 起始频率 + 整数 × 步长")
        self.freq_step = spin_box(
            DEFAULT_FREQUENCY_STEP, 0.000001, 1000.0, decimals=6, single_step=0.1
        )
        self.freq_step.setToolTip("相邻频点的间隔，单位 GHz")
        self.freq_extra = QLineEdit(DEFAULT_FREQUENCY_EXTRA_TEXT)
        self.freq_extra.setPlaceholderText("例如 5.8, 6.2（可留空）")
        self.freq_extra.setToolTip(
            "不在扫描网格上的频点，用逗号或空格分隔；可以落在起始/结束范围之外"
        )
        self.freq_extra.setMaximumWidth(200)
        self.freq_preview = QLabel()
        self.freq_preview.setProperty("role", "note")
        self.freq_preview.setWordWrap(True)
        self.freq_preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
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
        dut = self.config.get("dut_config", {})
        if not isinstance(dut, dict): dut = {}
        self.max_input_power = spin_box(dut.get("max_input_power", DEFAULT_MAX_INPUT_POWER), 0, 50)
        self.pa_unit_count_combo = combo_box(("1", "2", "3"), str(dut.get("power_supply_count", DEFAULT_PA_UNIT_COUNT)))
        frequency_rows = (
            ("起始频率 (GHz)", self.freq_start, "结束频率 (GHz)", self.freq_stop),
            ("频率步长 (GHz)", self.freq_step, "增补频点 (GHz)", self.freq_extra),
        )
        for row, (left_label, left_widget, right_label, right_widget) in enumerate(
            frequency_rows
        ):
            for column, (label, widget) in enumerate(
                ((left_label, left_widget), (right_label, right_widget))
            ):
                field_label = QLabel(label)
                field_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                form.addWidget(field_label, row, column * 2)
                form.addWidget(widget, row, column * 2 + 1)
        form.addWidget(self.freq_preview, 2, 0, 1, 4)
        parameter_rows = (("起始功率 (dBm)", self.start_power),
                          ("结束功率 (dBm)", self.stop_power),
                          ("功率步长 (dB)", self.power_step),
                          ("压缩点", self.compression_combo), ("衰减器", self.attenuator_combo),
                          ("DUT最大输入功率 (dBm)", self.max_input_power), ("PA单元数量", self.pa_unit_count_combo))
        for offset, (label, widget) in enumerate(parameter_rows):
            row, column = divmod(offset, 2)
            field_label = QLabel(label)
            field_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            form.addWidget(field_label, row + 3, column * 2)
            form.addWidget(widget, row + 3, column * 2 + 1)
        page_column.addWidget(params)
        # 运行入口紧随测试参数，保存与返回放在页面底部固定操作栏。
        self._build_run_widgets(page_column)
        page_column.addStretch(1)
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
        到页面状态（绑定、电源卡片、分配选项）。

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

    def _build_run_widgets(self, layout):
        """最终动作入口：一次完成校验、保存配置、创建运行与完整连接检查。

        保存配置与返回欢迎页放在页面底部固定操作栏（``_build_action_bar``），
        最终动作留在参数区下方，便于“改参数 → 完成配置并连接仪器”就近操作。
        """
        # 配置阶段入口：页面最终按钮。文案固定为“完成配置并连接仪器”；连接成功后
        # 由窗口切到测量区，页面不会自动开始任何测量。
        run_group = QGroupBox("运行")
        run_layout = QVBoxLayout(run_group)
        run_layout.setSpacing(8)
        self.finalize_run_btn = QPushButton("完成配置并连接仪器")
        self.finalize_run_btn.setToolTip(
            "校验并保存配置，释放旧端口并冻结待提交运行，然后检查全部仪器；"
            "连接成功后才提交运行，失败会留在本页"
        )
        set_button_variant(self.finalize_run_btn, "primary")
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
        run_layout.addWidget(self.finalize_run_btn)
        run_layout.addWidget(self.run_status_label)
        run_layout.addLayout(load_row)
        layout.addWidget(run_group)

    def _connect_signals(self):
        self.save_config_btn.clicked.connect(self.request_save)
        self.finalize_run_btn.clicked.connect(self.request_finalize_run)
        self.load_run_btn.clicked.connect(self.request_load_run)
        self.return_welcome_btn.clicked.connect(self.return_welcome_requested.emit)
        self.previous_step_btn.clicked.connect(self.connection_step_requested.emit)
        for widget in (self.freq_start, self.freq_stop, self.freq_step):
            widget.valueChanged.connect(self._on_frequency_changed)
        self.freq_extra.textChanged.connect(self._on_frequency_changed)

    def _on_frequency_changed(self, *_args) -> None:
        """用户改动了任一频率字段：清除加载错误并刷新预览。"""
        self.frequency_import_error = None
        self._refresh_frequency_preview()

    @staticmethod
    def _frequency_list_text(values, limit: int = 24) -> str:
        """把频点列表压成一行预览；过长的列表只显示首尾并注明总数。"""
        if len(values) <= limit:
            return "，".join(format_frequency(value) for value in values)
        head = "，".join(format_frequency(value) for value in values[: limit - 4])
        tail = "，".join(format_frequency(value) for value in values[-3:])
        return f"{head}，…，{tail}"

    def _refresh_frequency_preview(self) -> None:
        """刷新频率预览：合法时显示生成结果，非法时显示具体原因。"""
        error = self.frequency_import_error
        extras: tuple[float, ...] = ()
        if not error:
            try:
                extras = parse_extra_frequencies(self.freq_extra.text())
                values = generate_frequencies(
                    self.freq_start.value(),
                    self.freq_stop.value(),
                    self.freq_step.value(),
                    extras,
                )
            except FrequencySweepError as failure:
                error = str(failure)
        if error:
            prefix = "" if self.frequency_import_error else "无法生成频点："
            self.freq_preview.setText(f"{prefix}{error}")
            if self.freq_preview.property("tone") != "warning":
                set_tone(self.freq_preview, "warning")
            return
        text = (
            f"共 {len(values)} 个频点："
            f"{self._frequency_list_text(values)}"
        )
        if extras:
            text += "（含增补 " + "，".join(format_frequency(value) for value in extras) + "）"
        self.freq_preview.setText(text)
        if self.freq_preview.property("tone") != "muted":
            set_tone(self.freq_preview, "muted")

    def request_save(self):
        if self.read_only_reason:
            return
        if self.validate(): self.save_requested.emit()

    def request_finalize_run(self):
        """最终动作入口：表单校验通过后才把意图交给窗口。

        真正的保存、运行冻结、连接检查与提交在协调器里完成；本页只负责“当前表单是
        否可以提交”，并在动作进行中拒绝重复点击。
        """
        if self._finalizing or self.read_only_reason:
            return
        if self.validate():
            self.finalize_run_requested.emit()

    def set_read_only(self, reason: str | None) -> None:
        """按统一会话给出的版本结论切换只读态。

        1.0 编辑文档仍可查看参数与绑定，但不能改动测试参数、保存或进入最终动作；
        “返回欢迎页/上一步”保持可用，操作者可以离开而不是被锁在页面里。原因只由
        authoring 的版本门禁给出，页面不识别 ``schema_version``。
        """
        reason = (reason or "").strip() or None
        was_read_only = bool(self.read_only_reason)
        self.read_only_reason = reason
        self.read_only_notice.setText(
            f"当前配置只读：{reason}" if reason else ""
        )
        self.read_only_notice.setVisible(bool(reason))
        widgets = (
            self.test_parameters_group,
            self.save_config_btn,
            self.finalize_run_btn,
        )
        if reason:
            if not was_read_only:
                # 记住进入只读前的可用状态：退出只读时不能顺手打开本该禁用的控件。
                self._read_only_enabled = {
                    widget: widget.isEnabled() for widget in widgets
                }
            for widget in widgets:
                widget.setEnabled(False)
        elif was_read_only:
            previous = self._read_only_enabled
            self._read_only_enabled = {}
            for widget in widgets:
                widget.setEnabled(previous.get(widget, True))

    def set_finalizing(self, active: bool) -> None:
        """进入/退出最终动作状态：禁用提交、回退与参数编辑。

        真正的连接由协调器在后台完成，页面在这段时间里不应允许改参数、回上一步或
        再提交一次。窗口在动作结束（成功或失败）后调用 ``False`` 恢复控件。恢复的是
        进入前各自的可用状态，而不是一律启用：参数区里本来就被业务状态禁用的控件
        必须保持原样，最终动作不能顺手把它们打开。
        """
        active = bool(active)
        if active == self._finalizing:
            return
        widgets = (
            self.finalize_run_btn,
            self.save_config_btn,
            self.previous_step_btn,
            self.return_welcome_btn,
            self.load_run_btn,
            self.load_run_id_edit,
            self.test_parameters_group,
        )
        self._finalizing = active
        if active:
            self._finalizing_enabled = {widget: widget.isEnabled() for widget in widgets}
            for widget in widgets:
                widget.setEnabled(False)
            return
        previous = self._finalizing_enabled
        self._finalizing_enabled = {}
        for widget in widgets:
            widget.setEnabled(previous.get(widget, True))
        # 只读文档在进入最终动作前就被拒绝；恢复时仍以只读结论为准，不能把保存或
        # 最终入口顺手打开。非只读时保留上面恢复的原状态，不改写其它业务禁用。
        if self.read_only_reason:
            for widget in (
                self.test_parameters_group,
                self.save_config_btn,
                self.finalize_run_btn,
            ):
                widget.setEnabled(False)

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

    def set_reconnect_required(self, required: bool) -> None:
        """切换最终按钮的语义：已有运行但端口丢失时只重连，不重建运行。"""
        self._reconnect_required = bool(required)
        if self._reconnect_required:
            self.finalize_run_btn.setText("重新连接仪器")
            self.finalize_run_btn.setToolTip(
                "不保存配置、不创建新运行，只使用当前运行的冻结快照重新连接仪器"
            )
            return
        self.finalize_run_btn.setText("完成配置并连接仪器")
        self.finalize_run_btn.setToolTip(
            "校验并保存配置，释放旧端口并冻结待提交运行，然后检查全部仪器；"
            "连接成功后才提交运行，失败会留在本页"
        )

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
        """设置欢迎页选择的测试模式；上电计划与漏极归独立电源页。"""
        self.test_mode_enabled = bool(enabled)
        self._dirty_baseline = copy.deepcopy(self.build_config(self.config))
        # 切换测试模式本身不算“已修改”：页面与视图模型同时重置干净基线。
        self.view_model.mark_clean()
        self.driver_mode_changed.emit(self.test_mode_enabled)

    def is_dirty(self) -> bool:
        """当前控件读数是否与上次干净基线不同。

        视图模型的 dirty 覆盖步骤 6 已接入的测试参数与设备绑定；整份组装结果的比较仍
        负责尚未接入视图模型的电源通道参数与上电计划（步骤 7 收口）。先组装再比较，
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
        # 先算绑定；本页不再重建电源卡片或分配选项——电源字段由独立电源页回填，
        # 投影失败时只需恢复本页持有的绑定与问题清单。
        bindings = effective_device_bindings(config)
        previous_bindings = self.device_bindings
        previous_problems = self.binding_problems
        self.device_bindings = bindings
        try:
            projected = self.form_adapter.apply_configuration(config)
        except ConfigBackfillError:
            self.device_bindings = previous_bindings
            self.binding_problems = previous_problems
            raise
        self.config = projected
        self._refresh_frequency_preview()
        self.binding_problems = self._binding_problems_of(config)
        # 面板归连接页所有：窗口在回填后调用 ``InstrumentSetupPage.enter()``，由它按新的
        # 只读视图重置面板；本页只在日志里报告读入时就不可用的绑定。
        if self.binding_problems:
            self.discovery_message.emit("；".join(self.binding_problems))
        # 基线与视图模型都取“页面对这份文档的组装结果”：控件缺省值属于页面自身的
        # 规范化结果，刚回填完的页面不算已修改。
        built = self.build_config(self.config)
        self._dirty_baseline = copy.deepcopy(built)
        self.view_model.load(built, bindings=self.device_bindings)
