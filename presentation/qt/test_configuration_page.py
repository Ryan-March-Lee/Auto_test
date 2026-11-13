"""测试配置页面及其控件联动（``TestConfigurationPage``）。

模块与页面类都使用领域名称“test configuration”，与
:mod:`presentation.qt.power_configuration_page` 的“power configuration”成对命名。

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
:meth:`TestConfigurationPage.apply_device_selection` 只提交本次勾选结果并投影返回的绑定集合。

页面业务状态由 :class:`application.configuration_view_model.ConfigurationViewModel` 持有
（步骤 6）：测试参数、设备绑定、运行状态与 dirty。本页只把测试参数与连接信息的读数
写进视图模型、再按它返回的状态渲染（运行状态文案、绑定集合）；电源片段由独立电源页
提交，窗口用 :class:`presentation.qt.configuration_editor.ConfigurationEditor` 统一
回填/切换模式/汇总 dirty，协调器负责合并整份草稿与最终校验（步骤 13）。

参数区按被测对象分成两张卡片：**“DUT 参数”**承载 DUT 频率扫描计划、DUT 功率扫描
计划、压缩点、衰减器与 DUT 最大输入功率；**“驱放参数”**只承载有驱放测试才生效的
驱动功放预估增益与驱放最大输入功率，无驱放测试时整组不显示。控件对象仍保留在页面
上，因此配置里已有的驱放值会原样回写，不会因为切换模式被删除。运行入口和底部操作栏
不属于参数分组，仍独立成块。

测试频率不再逐点输入：页面提供“起始/步长/终止/增补”四个控件，由
:mod:`presentation.qt.frequency_sweep` 生成配置文档仍使用的 ``test_frequencies``
列表。结束频率必须落在步长上；旧配置里非等步长的列表不做兼容拆分，页面保留原列表
并禁止保存，直到用户重新输入合法扫描参数。频率块与下面的功率计划块同构：加粗放大的
“DUT频率扫描计划”标题、带编号与单位的输入行、行内频点预览，再加一行汇总；两层灰字
的排版规则在 :mod:`presentation.qt.frequency_scan_preview`。

起止功率表示**主功放输入功率**，功率步长是目标输入功率网格的间隔；“驱动功放预估
增益 (dB)”是测试配置里的单一全局值，只在有驱放测试时出现。页面不做功率反算或
增益推算，数值合法性由配置 DTO 校验；驱放关闭时不收集界面输入，但已保存的增益
仍随配置保留。

DUT 功率扫描计划按行编辑：每行由 :mod:`presentation.qt.power_sweep_plan_row` 提供
“起始/步长/终止”三个输入框，页面持有行列表、编号与增删规则。行内灰色点位预览和
汇总标签只负责显示：点位生成、相邻计划衔接判断与 DUT 上限都经
:mod:`presentation.qt.power_scan_preview` 调用 ``application.dto`` 的共享入口，页面
不另写一套步长或连续性算法，也不为修复空档/重叠而改写用户输入。
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
    DEFAULT_DRIVER_GAIN_ESTIMATE,
    DEFAULT_DRIVER_MAX_INPUT_POWER,
    DEFAULT_DRIVER_MODE_ENABLED,
    DEFAULT_FREQUENCY_EXTRA_TEXT,
    DEFAULT_FREQUENCY_START,
    DEFAULT_FREQUENCY_STEP,
    DEFAULT_FREQUENCY_STOP,
    DEFAULT_MAX_INPUT_POWER,
    DEFAULT_POWER_SCAN_PLAN,
    ConfigBackfillError,
    apply_frequency_fields,
    bool_or_default,
    driver_gain_text,
    driver_max_input_power_text,
    number_or_none,
)
from .config_page_widgets import (
    build_action_bar,
    combo_box,
    number_field,
)
from .frequency_scan_preview import preview_frequency_scan
from .instrument_discovery_panel import role_label
from .pages import PageControllerBindingMixin
from .power_scan_preview import preview_power_scan_plans
from .power_sweep_plan_row import PLAN_NUMBER_LABEL_SAMPLE, PowerSweepPlanRow
from .theme import PAGE_CONTENT_MAX_WIDTH, set_button_variant, set_tone

__all__ = [
    "ConfigBackfillError",
    "TestConfigurationPage",
]


class TestConfigurationPage(PageControllerBindingMixin, QWidget):
    save_requested = Signal()
    finalize_run_requested = Signal()
    load_run_requested = Signal(str)
    #: 底部“上一步”：回上一级电源配置页；切页由主窗口完成，参数原地保留。
    power_step_requested = Signal()
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
        # 功率扫描计划的动态编辑器状态：列表顺序就是执行顺序，页面负责新增、删除、
        # 编号与布局；计划是否连续、点位如何生成仍由 application/domain 校验。
        self.power_scan_plan_rows: list[PowerSweepPlanRow] = []
        # 计划结构本身无法读出时（空列表、非对象元素等）留下的用户错误。它不由行编辑
        # 清除：只有“重建计划”或整份配置回填才解除，避免改一行就吞掉读不出来的其它段。
        # 多段计划的连续性错误由正式校验按索引报告。
        self.power_scan_import_error: str | None = None
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
        #
        # 参数区按被测对象分成两张卡片：DUT 组承载频率/功率扫描计划与压缩点、衰减器、
        # 最大输入功率；驱放组只承载有驱放测试才生效的全局预估增益与最大输入功率，
        # 无驱放测试时整组隐藏。两条扫描计划与三个 DUT 电气参数同属被测件，混排会让
        # “这一项到底约束谁”难以判断。
        params = QGroupBox("DUT 参数")
        self.dut_parameters_group = params
        # 旧名称保留：只读/最终动作与既有用例都通过它判断“参数区”是否可用。
        self.test_parameters_group = params
        form = QGridLayout(params)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        # 标签列定宽、输入列吃满剩余宽度：两列表单的标签才会上下对齐。
        form.setColumnMinimumWidth(0, 118)
        form.setColumnMinimumWidth(2, 118)
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)
        # 保留“键缺失”与“显式空对象”的区别：前者是新建页面的空基线，后者说明配置
        # 声明了 signal_source 却没有计划，必须作为导入错误报告，不能伪装成默认计划。
        test = self.config.get("signal_source")
        self.freq_start = number_field(
            DEFAULT_FREQUENCY_START, 0.0, 1000.0, decimals=6
        )
        self.freq_start.setToolTip("频率扫描起始值，单位 GHz")
        self.freq_stop = number_field(
            DEFAULT_FREQUENCY_STOP, 0.0, 1000.0, decimals=6
        )
        self.freq_stop.setToolTip("结束频率是扫描上限，必须等于 起始频率 + 整数 × 步长")
        self.freq_step = number_field(
            DEFAULT_FREQUENCY_STEP, 0.000001, 1000.0, decimals=6
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
        compression = self.config.get("compression_point", {})
        attenuator = self.config.get("attenuator", {})
        if not isinstance(compression, dict): compression = {}
        if not isinstance(attenuator, dict): attenuator = {}
        self.compression_combo = combo_box(("1dB", "3dB", "5dB"), compression.get("type", DEFAULT_COMPRESSION_TYPE))
        self.attenuator_combo = combo_box(("30dB", "40dB"), attenuator.get("type", DEFAULT_ATTENUATOR_TYPE))
        dut = self.config.get("dut_config", {})
        if not isinstance(dut, dict): dut = {}
        self.max_input_power = number_field(dut.get("max_input_power", DEFAULT_MAX_INPUT_POWER), 0, 50)
        # 频率扫描与下面的 DUT 功率扫描计划同构：首行是加粗放大的区块标题，输入行带
        # “计划 1”编号与带单位的字段标签，输入行下面先给该段的频点预览，再给一行汇总。
        # 频率固定只有一段，因此没有“添加计划/删除”；编号保留是为了与功率计划对齐。
        frequency_header = QHBoxLayout()
        frequency_header.setSpacing(8)
        self.frequency_sweep_title = QLabel("DUT频率扫描计划")
        self.frequency_sweep_title.setProperty("role", "section")
        frequency_header.addWidget(self.frequency_sweep_title)
        frequency_header.addStretch(1)
        form.addLayout(frequency_header, 0, 0, 1, 4)

        frequency_row = QHBoxLayout()
        frequency_row.setSpacing(6)
        self.frequency_plan_number = QLabel("计划 1")
        self.frequency_plan_number.setProperty("role", "fieldLabel")
        # 与功率计划行预留同一份编号宽度：两块计划的编号列起点一致，字段才能上下对齐。
        self.frequency_plan_number.setFixedWidth(
            self.fontMetrics().horizontalAdvance(PLAN_NUMBER_LABEL_SAMPLE) + 8
        )
        frequency_row.addWidget(self.frequency_plan_number)
        for label, widget in (
            ("起始频率 (GHz)", self.freq_start),
            ("步长 (GHz)", self.freq_step),
            ("终止频率 (GHz)", self.freq_stop),
            ("增补 (GHz)", self.freq_extra),
        ):
            field_label = QLabel(label)
            field_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            frequency_row.addWidget(field_label)
            frequency_row.addWidget(widget)
            frequency_row.addSpacing(6)
        frequency_row.addStretch(1)
        form.addLayout(frequency_row, 1, 0, 1, 4)
        form.addWidget(self.freq_preview, 2, 0, 1, 4)
        # 汇总与功率计划的汇总同一位置、同一样式：行内给出这一段会生成什么，汇总给出
        # 段数、点数与首尾范围。
        self.freq_summary = QLabel()
        self.freq_summary.setObjectName("frequencySweepSummary")
        self.freq_summary.setProperty("role", "note")
        self.freq_summary.setWordWrap(True)
        self.freq_summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addWidget(self.freq_summary, 3, 0, 1, 4)
        first_parameter_row = self._build_power_scan_plan_editor(form, 4, test)
        parameter_rows = (
            ("压缩点", self.compression_combo),
            ("衰减器", self.attenuator_combo),
            ("DUT最大输入功率 (dBm)", self.max_input_power),
        )
        for offset, (label, widget) in enumerate(parameter_rows):
            row, column = divmod(offset, 2)
            field_label = QLabel(label)
            field_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            form.addWidget(field_label, row + first_parameter_row, column * 2)
            form.addWidget(widget, row + first_parameter_row, column * 2 + 1)
        page_column.addWidget(params)
        self._build_driver_parameters(page_column)
        self._apply_driver_gain_state()
        # 运行入口紧随测试参数，保存与返回放在页面底部固定操作栏。
        self._build_run_widgets(page_column)
        page_column.addStretch(1)
        root.addWidget(self._build_action_bar())

    # -- 驱放参数组 ---------------------------------------------------------
    def _build_driver_parameters(self, layout) -> None:
        """建立驱放参数组：有驱放测试才生效的预估增益与最大输入功率。

        本页与驱放直接相关的配置是 ``driver_mode.estimated_gain_db`` 与
        ``driver_mode.max_input_power``：驱放上电计划归电源配置页，有/无驱放由欢迎页
        选择。组内两行分别是预估增益与最大输入功率；后者是有驱放链路中信号源输出
        设定值的硬上限。无驱放测试时整组不显示（见 :meth:`_apply_driver_gain_state`）。
        """
        # 驱动功放预估增益是测试配置里的单一全局值（dB）。空文本表示尚未填写；
        # 增益的业务校验（有限数值、驱动模式下必填）由配置 DTO 承担，页面不推算。
        self.driver_gain = QLineEdit(DEFAULT_DRIVER_GAIN_ESTIMATE)
        self.driver_gain.setPlaceholderText("例如 30.0（启用驱动时填写）")
        self.driver_gain.setMaximumWidth(140)
        self.driver_gain.setToolTip(
            "驱动功放预估增益（dB），用于推算驱动映射的起点"
        )
        # 驱放最大输入功率是信号源输出设定的硬上限；空文本表示尚未填写，不猜 0 dBm。
        self.driver_max_input_power = QLineEdit(DEFAULT_DRIVER_MAX_INPUT_POWER)
        self.driver_max_input_power.setPlaceholderText(
            "例如 0.0（启用驱动时必填）"
        )
        self.driver_max_input_power.setMaximumWidth(140)
        self.driver_max_input_power.setToolTip(
            "驱放最大输入功率（dBm）：有驱放链路中信号源输出设定值不得超过它"
        )
        mode = self.config.get("driver_mode", {})
        mode = mode if isinstance(mode, dict) else {}
        # 与配置回填使用同一套解析：只接受真正的布尔值（``"false"``/``0``/``None``
        # 都按缺失处理），增益与最大输入渲染也复用投影规则，构造与 ``apply_config``
        # 不会各判一次。
        self.test_mode_enabled = bool_or_default(
            mode.get("enabled"), DEFAULT_DRIVER_MODE_ENABLED
        )
        self.driver_gain.setText(driver_gain_text(mode.get("estimated_gain_db")))
        self.driver_max_input_power.setText(
            driver_max_input_power_text(mode.get("max_input_power"))
        )

        group = QGroupBox("驱放参数")
        self.driver_parameters_group = group
        form = QGridLayout(group)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        # 与 DUT 组同一套标签列宽：两张卡片的标签起点一致，上下扫读时不会左右跳。
        form.setColumnMinimumWidth(0, 118)
        form.setColumnStretch(1, 1)

        self.driver_gain_label = QLabel("驱动功放预估增益 (dB)")
        self.driver_gain_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.addWidget(self.driver_gain_label, 0, 0)
        form.addWidget(self.driver_gain, 0, 1, Qt.AlignLeft)

        self.driver_max_input_power_label = QLabel("驱放最大输入功率 (dBm)")
        self.driver_max_input_power_label.setAlignment(
            Qt.AlignLeft | Qt.AlignVCenter
        )
        form.addWidget(self.driver_max_input_power_label, 1, 0)
        form.addWidget(self.driver_max_input_power, 1, 1, Qt.AlignLeft)

        layout.addWidget(group)

    # -- DUT 功率扫描计划编辑器 ---------------------------------------------
    @staticmethod
    def _default_power_scan_values() -> tuple[float, float, float]:
        """空配置的一行计划：``(起始, 步长, 终止)``，与投影的空配置基线同源。"""
        return (
            float(DEFAULT_POWER_SCAN_PLAN[0]),
            float(DEFAULT_POWER_SCAN_PLAN[1]),
            float(DEFAULT_POWER_SCAN_PLAN[2]),
        )

    #: 旧 1.1 单计划的标量字段；只在三个字段整体存在时才能包装成一段。
    _POWER_SCAN_SCALAR_FIELDS = ("start_power", "step", "stop_power")

    @classmethod
    def _power_scan_plan_values(
        cls, signal_source
    ) -> tuple[list[tuple[float, float, float]], str | None]:
        """把配置里的功率扫描段读成有序行值，并返回结构错误（若有）。

        读取边界与 ``ConfigurationDraft._read_sweeps`` 保持一致：计划字段按键判断，
        ``power_scan_plans`` 是 1.2 的唯一来源，旧标量只在三字段整体存在时包装成一行。
        缺字段、两种形状并存或元素不可读都不伪造可用计划：能读出的段照原顺序展示，
        其余问题作为导入错误阻止保存，直到用户明确重建。

        ``signal_source`` 为 ``None`` 表示键缺失（新建页面的空基线），默认创建一行；
        显式的空对象 ``{}`` 说明配置声明了扫描区却没有计划，按缺失计划报错。
        """
        default = cls._default_power_scan_values()
        if signal_source is None:
            return [default], None
        if not isinstance(signal_source, dict):
            return (
                [default],
                "配置的 DUT 功率扫描计划格式不正确：signal_source 必须是对象",
            )
        scalar_fields = [
            key for key in cls._POWER_SCAN_SCALAR_FIELDS if key in signal_source
        ]
        if "power_scan_plans" in signal_source:
            if scalar_fields:
                return (
                    [default],
                    "配置同时包含 power_scan_plans 与 start_power/stop_power/step，"
                    "DUT 功率扫描只能有一个来源",
                )
            plans = signal_source.get("power_scan_plans")
            if not isinstance(plans, (list, tuple)) or not plans:
                return [default], "配置的 DUT 功率扫描计划为空或格式不正确"
            values: list[tuple[float, float, float]] = []
            problems: list[str] = []
            for index, item in enumerate(plans, 1):
                if not isinstance(item, dict):
                    problems.append(f"第 {index} 段不是计划对象")
                    continue
                start = number_or_none(item.get("start_power"))
                step = number_or_none(item.get("step"))
                stop = number_or_none(item.get("stop_power"))
                if start is None or step is None or stop is None:
                    problems.append(f"第 {index} 段缺少有效的起始/步长/终止功率")
                    continue
                values.append((start, step, stop))
            if problems:
                # 能读出的段仍然展示；剩余问题保留为导入错误，行编辑不能清除它。
                return (
                    values or [default],
                    "配置的 DUT 功率扫描计划无法完整读取：" + "；".join(problems),
                )
            return values, None
        if not scalar_fields:
            return [default], "配置缺少 DUT 功率扫描计划"
        start = number_or_none(signal_source.get("start_power"))
        step = number_or_none(signal_source.get("step"))
        stop = number_or_none(signal_source.get("stop_power"))
        if start is None or step is None or stop is None:
            return (
                [default],
                "配置的旧版 DUT 功率扫描参数不完整：需要同时提供 "
                "start_power、step 和 stop_power",
            )
        return [(start, step, stop)], None

    def _build_power_scan_plan_editor(self, form, row: int, signal_source) -> int:
        """在测试参数组里建立计划标题、加号按钮和行容器，返回下一参数行号。"""
        header = QHBoxLayout()
        header.setSpacing(8)
        self.power_scan_plan_title = QLabel("DUT功率扫描计划")
        self.power_scan_plan_title.setProperty("role", "section")
        header.addWidget(self.power_scan_plan_title)
        header.addStretch(1)
        self.add_power_scan_plan_btn = QPushButton("+ 添加计划")
        self.add_power_scan_plan_btn.setObjectName("addPowerScanPlanButton")
        self.add_power_scan_plan_btn.setToolTip(
            "在当前计划后追加一段；起点默认接续上一段终点，步长沿用上一段"
        )
        set_button_variant(self.add_power_scan_plan_btn, "secondary")
        header.addWidget(self.add_power_scan_plan_btn)
        # 结构错误只在用户明确确认“以当前页面上的计划为准”后解除：只编辑其中一行
        # 不能证明旧配置里读不出来的其它段已经被处理。
        self.rebuild_power_scan_plan_btn = QPushButton("重建计划")
        self.rebuild_power_scan_plan_btn.setObjectName("rebuildPowerScanPlanButton")
        self.rebuild_power_scan_plan_btn.setToolTip(
            "旧配置的计划无法完整读取时，确认以当前页面上的计划为准"
        )
        set_button_variant(self.rebuild_power_scan_plan_btn, "ghost")
        self.rebuild_power_scan_plan_btn.setVisible(False)
        header.addWidget(self.rebuild_power_scan_plan_btn)
        form.addLayout(header, row, 0, 1, 4)

        self.power_scan_plan_container = QWidget()
        self.power_scan_plan_container.setObjectName("powerScanPlanContainer")
        self.power_scan_plan_layout = QVBoxLayout(self.power_scan_plan_container)
        self.power_scan_plan_layout.setContentsMargins(0, 0, 0, 0)
        self.power_scan_plan_layout.setSpacing(6)
        form.addWidget(self.power_scan_plan_container, row + 1, 0, 1, 4)

        # 汇总把“多段是否真的首尾相接”变成可见数字：合并后的目标点总数与首尾范围；
        # 有点位问题时就地显示问题数，完整原因同时放在 tooltip 里。
        self.power_scan_summary = QLabel()
        self.power_scan_summary.setObjectName("powerScanPlanSummary")
        self.power_scan_summary.setProperty("role", "note")
        self.power_scan_summary.setWordWrap(True)
        self.power_scan_summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addWidget(self.power_scan_summary, row + 2, 0, 1, 4)

        self.apply_power_scan_plans(signal_source)
        return row + 3

    def apply_power_scan_plans(self, signal_source) -> str | None:
        """把配置里的扫描段重建为行；返回结构错误，供回填投影写入页面状态。

        结构错误先写进页面状态再重建行：预览汇总要用它判断“当前可见行是否就是配置里
        的全部计划”，否则回填后的第一次刷新会按上一份配置的错误结论排版。
        """
        plans, error = self._power_scan_plan_values(signal_source)
        self.power_scan_import_error = error
        self._replace_power_scan_plan_rows(plans)
        self._sync_power_scan_plan_actions()
        return error

    def reset_power_scan_plans(self) -> None:
        """把动态计划编辑器恢复为空配置的一行默认计划（投影重置入口）。

        回填投影在应用文档前先整组重置控件；行对象归页面所有，因此由页面负责重建。
        这里复用缺省计划的同一入口：编号、删除按钮、汇总与预览都按一行默认计划刷新。
        """
        self.apply_power_scan_plans(None)

    def rebuild_power_scan_plans(self) -> None:
        """用户确认以当前可见行重建计划，解除“旧计划读不出来”的导入错误。

        行编辑本身不清除该错误：结构错误意味着配置里有页面读不出来的计划，只改其中
        一行并不能证明其它段已经处理，保存会静默丢掉它们。这里保留当前可见行，让
        用户先确认它们确实是想要的完整计划。
        """
        self.power_scan_import_error = None
        self._sync_power_scan_plan_actions()
        self._refresh_power_scan_previews()

    def _sync_power_scan_plan_actions(self) -> None:
        """结构错误存在时显示“重建计划”，正常编辑时不打扰用户。"""
        button = getattr(self, "rebuild_power_scan_plan_btn", None)
        if button is not None:
            button.setVisible(bool(self.power_scan_import_error))

    def add_power_scan_plan(self) -> PowerSweepPlanRow:
        """追加一段计划：起点取上一行终点，步长沿用，终点先等于起点。"""
        rows = self.power_scan_plan_rows
        if rows:
            _, step, stop = rows[-1].values()
            values = (stop, step, stop)
        else:
            values = self._default_power_scan_values()
        row = self._append_power_scan_plan_row(*values)
        self._refresh_power_scan_plan_rows()
        return row

    def remove_power_scan_plan(self, row: PowerSweepPlanRow) -> bool:
        """删除一行；只剩一行时拒绝删除并返回 ``False``。"""
        if len(self.power_scan_plan_rows) <= 1 or row not in self.power_scan_plan_rows:
            return False
        self.power_scan_plan_rows.remove(row)
        self.power_scan_plan_layout.removeWidget(row)
        row.setParent(None)
        row.deleteLater()
        self._refresh_power_scan_plan_rows()
        return True

    def _append_power_scan_plan_row(
        self, start_power: float, step: float, stop_power: float
    ) -> PowerSweepPlanRow:
        row = PowerSweepPlanRow(
            start_power,
            step,
            stop_power,
            number=len(self.power_scan_plan_rows) + 1,
            parent=self.power_scan_plan_container,
        )
        row.remove_requested.connect(self.remove_power_scan_plan)
        row.values_changed.connect(self._refresh_power_scan_previews)
        self.power_scan_plan_layout.addWidget(row)
        self.power_scan_plan_rows.append(row)
        return row

    def _replace_power_scan_plan_rows(self, plans) -> None:
        for row in list(self.power_scan_plan_rows):
            self.power_scan_plan_layout.removeWidget(row)
            row.setParent(None)
            row.deleteLater()
        self.power_scan_plan_rows.clear()
        for values in plans or [self._default_power_scan_values()]:
            self._append_power_scan_plan_row(*values)
        self._refresh_power_scan_plan_rows()

    def _refresh_power_scan_plan_rows(self) -> None:
        """刷新编号与预览；只剩一行时禁用删除，保证列表至少保留一段。"""
        removable = len(self.power_scan_plan_rows) > 1
        for number, row in enumerate(self.power_scan_plan_rows, 1):
            row.set_number(number)
            row.delete_button.setEnabled(removable)
        self._refresh_power_scan_previews()

    def _refresh_power_scan_previews(self) -> None:
        """按当前行读数刷新每段点位文本与汇总；只改显示，不改用户输入。

        点位生成与相邻计划校验都走 :mod:`presentation.qt.power_scan_preview` 背后的
        ``application.dto`` 共享入口，页面不再实现第二套步长/连续性算法。输入暂时
        非法时行内显示具体错误（不保留旧预览），保存是否被阻止仍由 :meth:`validate`
        汇总的正式校验决定。

        结构导入错误存在时，可见行不一定是配置里的全部计划，汇总必须说明点数只是
        按可见行估算；否则用户会以为一份读不全的计划已经有确定的目标序列。
        """
        preview = preview_power_scan_plans(
            (row.values() for row in self.power_scan_plan_rows),
            self.max_input_power.value(),
        )
        for row, row_preview in zip(self.power_scan_plan_rows, preview.rows):
            row.set_preview(
                row_preview.text,
                tone=row_preview.tone,
                tooltip=row_preview.tooltip,
            )
        if self.power_scan_import_error:
            summary_text = (
                f"{preview.summary}；计划未完整读取，预览只按当前可见行估算"
            )
            summary_tooltip = (
                f"{self.power_scan_import_error}\n\n{preview.summary_tooltip}"
            )
            summary_tone = "warning"
        else:
            summary_text = preview.summary
            summary_tooltip = preview.summary_tooltip
            summary_tone = preview.summary_tone
        self.power_scan_summary.setText(summary_text)
        self.power_scan_summary.setToolTip(summary_tooltip)
        if self.power_scan_summary.property("tone") != summary_tone:
            set_tone(self.power_scan_summary, summary_tone)

    #: 单行兼容读数：仍指向第一段计划，供尚未迁移到行列表的调用方与测试使用。
    @property
    def start_power(self):
        return self.power_scan_plan_rows[0].start_power

    @property
    def stop_power(self):
        return self.power_scan_plan_rows[0].stop_power

    @property
    def power_step(self):
        return self.power_scan_plan_rows[0].step

    def _build_action_bar(self):
        """页面底部固定操作栏：回到仪表配置或保存当前配置。"""
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

        保存配置放在页面底部固定操作栏（``_build_action_bar``），
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
        self.previous_step_btn.clicked.connect(self.power_step_requested.emit)
        self.add_power_scan_plan_btn.clicked.connect(self.add_power_scan_plan)
        self.rebuild_power_scan_plan_btn.clicked.connect(self.rebuild_power_scan_plans)
        self.max_input_power.valueChanged.connect(self._refresh_power_scan_previews)
        for widget in (self.freq_start, self.freq_stop, self.freq_step):
            widget.valueChanged.connect(self._on_frequency_changed)
        self.freq_extra.textChanged.connect(self._on_frequency_changed)

    def _on_frequency_changed(self, *_args) -> None:
        """用户改动了任一频率字段：清除加载错误并刷新预览。"""
        self.frequency_import_error = None
        self._refresh_frequency_preview()

    def _refresh_frequency_preview(self) -> None:
        """刷新频率的行内预览与汇总：合法时显示生成结果，非法时显示具体原因。

        文案与语义色的排版规则在 :mod:`presentation.qt.frequency_scan_preview`，与功率
        计划的两层灰字同构；这里只把结果写进控件。
        """
        preview = preview_frequency_scan(
            self.freq_start.value(),
            self.freq_stop.value(),
            self.freq_step.value(),
            self.freq_extra.text(),
            import_error=self.frequency_import_error,
        )
        self.freq_preview.setText(preview.row_text)
        self.freq_preview.setToolTip(preview.row_tooltip)
        if self.freq_preview.property("tone") != preview.row_tone:
            set_tone(self.freq_preview, preview.row_tone)
        self.freq_summary.setText(preview.summary)
        self.freq_summary.setToolTip(preview.summary_tooltip)
        if self.freq_summary.property("tone") != preview.summary_tone:
            set_tone(self.freq_summary, preview.summary_tone)

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
        “上一步”保持可用，操作者可以离开而不是被锁在页面里。原因只由
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
            self.driver_parameters_group,
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
            self.load_run_btn,
            self.load_run_id_edit,
            self.test_parameters_group,
            self.driver_parameters_group,
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
                self.driver_parameters_group,
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

    def _apply_driver_gain_state(self) -> None:
        """按驱动模式显示或隐藏整张驱放参数卡。

        无驱放测试时预估增益与最大输入功率都不参与运行，整组从界面移除，避免留下
        一组灰显控件让人反复确认它们是否需要填写。控件对象本身仍保留，因此配置里
        已有的驱放值会原样回写，不会因为切换模式被删除；有驱放测试时整组出现。
        """
        self.driver_parameters_group.setVisible(bool(self.test_mode_enabled))

    def set_test_mode(self, enabled: bool) -> None:
        """设置欢迎页选择的测试模式；上电计划与漏极归独立电源页。"""
        self.test_mode_enabled = bool(enabled)
        self._apply_driver_gain_state()
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
        self._apply_driver_gain_state()
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
