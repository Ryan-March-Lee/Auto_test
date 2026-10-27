"""配置页面及其控件联动。

页面拥有配置控件，窗口只通过信号协调保存和连接。该模块只负责控件构建、用户意图
转发和派生界面状态（单元可见性、驱动模式、电源选项）；把冻结配置投影到控件的规则
在 :mod:`presentation.qt.config_projection`，读取控件值组装配置的规则在
:mod:`presentation.qt.config_form_state`。页面不持有逐字段回填逻辑，也不依赖窗口、
测量 controller 或硬件实现。
"""

import copy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QTabWidget,
    QVBoxLayout, QWidget,
)

from .config_form_state import ConfigFormState
from .config_projection import (
    DEFAULT_ATTENUATOR_TYPE,
    DEFAULT_COMPRESSION_TYPE,
    DEFAULT_DRIVER_MODE_ENABLED,
    DRIVER_POWER_SOURCE_VALUES,
    DEFAULT_ENABLED_POWER_SUPPLIES,
    DEFAULT_MAX_INPUT_POWER,
    DEFAULT_PA_UNIT_COUNT,
    DEFAULT_POWER_ASSIGNMENT,
    DEFAULT_POWER_SUPPLY_NAMES,
    DEFAULT_SIGNAL_SOURCE,
    DEFAULT_TEST_FREQUENCIES,
    INSTRUMENT_ENTRIES,
    POWER_SUPPLY_ROLES,
    ConfigBackfillError,
    project_authoring_config,
)
from .configuration_authoring import DUT_CHANNEL_ROLES
from .input_controls import KeyboardOnlyDoubleSpinBox, WheelSafeComboBox
from .pages import PageControllerBindingMixin

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
    validation_failed = Signal(str)
    driver_mode_changed = Signal(bool)

    def __init__(self, config=None, parent=None):
        PageControllerBindingMixin.__init__(self)
        QWidget.__init__(self, parent)
        self.config = config if isinstance(config, dict) else {}
        self.build_ui()
        self._connect_signals()
        self.update_pa_unit_ui()
        self.update_driver_power_ui()
        self.update_power_supply_options()
        self._dirty_baseline = copy.deepcopy(self.config)

    def build_ui(self):
        """按页面协议构建并返回配置页根 widget。"""
        self._build_ui()
        return self

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        content = QWidget()
        # 限制内容最大宽度：窗口过宽时不再把分组框横向拉伸成很长的灰色边框。
        content.setMaximumWidth(1200)
        # 两栏布局：左侧仪器连接/测试参数/运行，右侧电源配置。避免单个分组框
        # 随视口无限横向拉伸，同时降低页面总高度。
        columns = QHBoxLayout(content)
        columns.setContentsMargins(6, 6, 6, 6)
        columns.setSpacing(8)
        left_column = QVBoxLayout()
        left_column.setSpacing(6)
        right_column = QVBoxLayout()
        right_column.setSpacing(6)
        columns.addLayout(left_column, 3)
        columns.addLayout(right_column, 2)
        root.addWidget(scroll)
        scroll.setWidget(content)

        instruments = self.config.get("instruments", {})
        if not isinstance(instruments, dict):
            instruments = {}
        supplies = instruments.get("power_supplies", {})
        if not isinstance(supplies, dict):
            supplies = {}

        connection = QGroupBox("仪器连接配置")
        grid = QGridLayout(connection)
        grid.setColumnStretch(1, 1)
        for row, (key, label, source, default) in enumerate(INSTRUMENT_ENTRIES):
            item = instruments.get(source, {})
            if not isinstance(item, dict): item = {}
            address = QLineEdit(str(item.get("address", "")))
            address.setMaximumWidth(260)
            enabled = QCheckBox("启用")
            enabled.setChecked(item.get("enabled", default))
            setattr(self, f"{key}_address", address)
            setattr(self, f"{key}_enabled", enabled)
            grid.addWidget(QLabel(f"{label}地址:"), row, 0)
            grid.addWidget(address, row, 1)
            grid.addWidget(enabled, row, 2)
        for row, name in enumerate(DEFAULT_POWER_SUPPLY_NAMES, 2):
            item = supplies.get(name, {})
            if not isinstance(item, dict): item = {}
            address = QLineEdit(str(item.get("address", "")))
            address.setMaximumWidth(260)
            enabled = QCheckBox("启用")
            enabled.setChecked(item.get("enabled", name in DEFAULT_ENABLED_POWER_SUPPLIES))
            setattr(self, f"{name.lower()}_address", address)
            setattr(self, f"{name.lower()}_enabled", enabled)
            grid.addWidget(QLabel(f"{name}地址:"), row, 0)
            grid.addWidget(address, row, 1)
            grid.addWidget(enabled, row, 2)
        self.connect_btn = QPushButton("连接仪器")
        grid.addWidget(self.connect_btn, 6, 0, 1, 2)
        left_column.addWidget(connection)

        params = QGroupBox("测试参数配置")
        form = QGridLayout(params)
        test = self.config.get("signal_source", {})
        if not isinstance(test, dict): test = {}
        self.freq_edit = QLineEdit(str(self.config.get("test_frequencies", DEFAULT_TEST_FREQUENCIES)))
        self.freq_edit.setMaximumWidth(320)
        self.start_power = self._spin(test.get("start_power", DEFAULT_SIGNAL_SOURCE["start_power"]), -50, 20)
        self.stop_power = self._spin(test.get("stop_power", DEFAULT_SIGNAL_SOURCE["stop_power"]), -50, 20)
        self.power_step = self._spin(test.get("step", DEFAULT_SIGNAL_SOURCE["step"]), 0.1, 5)
        compression = self.config.get("compression_point", {})
        attenuator = self.config.get("attenuator", {})
        if not isinstance(compression, dict): compression = {}
        if not isinstance(attenuator, dict): attenuator = {}
        self.compression_combo = self._combo(("1dB", "3dB", "5dB"), compression.get("type", DEFAULT_COMPRESSION_TYPE))
        self.attenuator_combo = self._combo(("30dB", "40dB"), attenuator.get("type", DEFAULT_ATTENUATOR_TYPE))
        mode = self.config.get("driver_mode", {})
        mode = mode if isinstance(mode, dict) else {}
        self.test_mode_enabled = bool(mode.get("enabled", DEFAULT_DRIVER_MODE_ENABLED))
        source = mode.get("power_source")
        self.driver_power_source = {"dc_supply": "直流电源", "external_ac": "自带供电"}.get(source)
        dut = self.config.get("dut_config", {})
        if not isinstance(dut, dict): dut = {}
        self.max_input_power = self._spin(dut.get("max_input_power", DEFAULT_MAX_INPUT_POWER), 0, 50)
        self.pa_unit_count_combo = self._combo(("1", "2", "3"), str(dut.get("power_supply_count", DEFAULT_PA_UNIT_COUNT)))
        parameter_rows = (("测试频率 (GHz)", self.freq_edit), ("起始功率 (dBm)", self.start_power),
                          ("结束功率 (dBm)", self.stop_power), ("功率步长 (dB)", self.power_step),
                          ("压缩点", self.compression_combo), ("衰减器", self.attenuator_combo),
                          ("DUT最大输入功率 (dBm)", self.max_input_power), ("PA单元数量", self.pa_unit_count_combo))
        for index, (label, widget) in enumerate(parameter_rows):
            row, column = divmod(index, 2)
            form.addWidget(QLabel(label), row, column * 2)
            form.addWidget(widget, row, column * 2 + 1)
        left_column.addWidget(params)
        self._build_power_widgets(right_column, supplies)
        # 运行入口紧随测试参数，保存按钮放在左栏底部。
        self._build_run_widgets(left_column)
        left_column.addStretch(1)
        right_column.addStretch(1)

    @staticmethod
    def _spin(value, minimum, maximum):
        widget = KeyboardOnlyDoubleSpinBox()
        widget.setRange(minimum, maximum)
        widget.setValue(float(value))
        widget.setMaximumWidth(120)
        return widget

    @staticmethod
    def _combo(items, current):
        widget = WheelSafeComboBox(); widget.addItems(list(items)); widget.setCurrentText(str(current)); return widget

    def _build_power_widgets(self, layout, supplies):
        detail = QGroupBox("电源详细配置")
        detail_layout = QVBoxLayout(detail)
        tabs = QTabWidget(); self.power_config_widgets = {}
        for name in DEFAULT_POWER_SUPPLY_NAMES:
            page = QWidget(); page_layout = QVBoxLayout(page)
            source = supplies.get(name, {})
            if not isinstance(source, dict): source = {}
            channels = source.get("channels", {})
            if not isinstance(channels, dict): channels = {}
            record = {"channels": {}}
            for channel in ("CH1", "CH2"):
                value = channels.get(channel, {})
                if not isinstance(value, dict): value = {}
                voltage = value.get("voltage", {}); current = value.get("current", {})
                if not isinstance(voltage, dict): voltage = {}
                if not isinstance(current, dict): current = {}
                box = QGroupBox(f"通道 {channel}"); form = QFormLayout(box)
                role = value.get("role")
                if role not in POWER_SUPPLY_ROLES:
                    role = DUT_CHANNEL_ROLES.get(channel, POWER_SUPPLY_ROLES[0])
                controls = {
                    "role": self._combo(POWER_SUPPLY_ROLES, role),
                    "voltage": self._spin(voltage.get("value", 0), 0, 50),
                    "voltage_protection": self._spin(voltage.get("protection", 0), 0, 50),
                    "voltage_protection_enabled": QCheckBox("启用"),
                    "current": self._spin(current.get("value", 0), 0, 10),
                    "current_protection": self._spin(current.get("protection", 0), 0, 10),
                    "current_protection_enabled": QCheckBox("启用"),
                }
                controls["voltage_protection_enabled"].setChecked(voltage.get("protection_enabled", False))
                controls["current_protection_enabled"].setChecked(current.get("protection_enabled", False))
                form.addRow("供电角色", controls["role"])
                form.addRow("电压 (V)", controls["voltage"]); form.addRow("保护电压 (V)", controls["voltage_protection"])
                form.addRow("电压保护", controls["voltage_protection_enabled"]); form.addRow("电流 (A)", controls["current"])
                form.addRow("保护电流 (A)", controls["current_protection"]); form.addRow("电流保护", controls["current_protection_enabled"])
                page_layout.addWidget(box); record["channels"][channel] = controls
            tabs.addTab(page, name); self.power_config_widgets[name] = record
        detail_layout.addWidget(tabs); layout.addWidget(detail)

        assignment = QGroupBox("电源分配设置"); form = QGridLayout(assignment)
        self.power_assignment_group = assignment
        existing = self.config.get("power_supply_assignment", {})
        if not isinstance(existing, dict): existing = {}
        driver = existing.get("driver_amplifier", {}); dut = existing.get("dut_amplifier", {})
        if not isinstance(driver, dict): driver = {}
        if not isinstance(dut, dict): dut = {}
        inferred_source = "直流电源" if driver.get("power_supply_count", 0) > 0 else "自带供电"
        self.driver_power_source_combo = self._combo(DRIVER_POWER_SOURCE_VALUES, self.driver_power_source or inferred_source)
        self.driver_power_source_label = QLabel("驱放供电方式:")
        self.driver_power_hint = QLabel("自带供电的驱放由外部供电设备供电，本系统不会控制其电源。")
        self.driver_power_hint.setWordWrap(True)
        driver_supply = driver.get("supplies", {}); dut_supply = dut.get("supplies", {})
        if not isinstance(driver_supply, dict): driver_supply = {}
        if not isinstance(dut_supply, dict): dut_supply = {}
        main = driver_supply.get("main", {}) if isinstance(driver_supply.get("main", {}), dict) else {}
        carrier = dut_supply.get("carrier", {}) if isinstance(dut_supply.get("carrier", {}), dict) else {}
        peaking = dut_supply.get("peaking", {}) if isinstance(dut_supply.get("peaking", {}), dict) else {}
        peaking2 = dut_supply.get("peaking2", {}) if isinstance(dut_supply.get("peaking2", {}), dict) else {}
        supplies_choices = DEFAULT_POWER_SUPPLY_NAMES
        self.driver_power_combo = self._combo(supplies_choices, main.get("name", DEFAULT_POWER_ASSIGNMENT["driver_power"]))
        self.pa_unit1_power_combo = self._combo(supplies_choices, carrier.get("name", DEFAULT_POWER_ASSIGNMENT["pa_unit1_power"]))
        self.pa_unit2_power_combo = self._combo(supplies_choices, peaking.get("name", DEFAULT_POWER_ASSIGNMENT["pa_unit2_power"]))
        self.pa_unit3_power_combo = self._combo(supplies_choices, peaking2.get("name", DEFAULT_POWER_ASSIGNMENT["pa_unit3_power"]))
        self.driver_power_label = QLabel("驱动功放电源:"); self.pa_unit2_label = QLabel("PA Unit2电源:"); self.pa_unit3_label = QLabel("PA Unit3电源:")
        # 两列布局（标签 + 下拉框），避免右栏过窄时撑出横向滚动条。
        form.addWidget(self.driver_power_source_label, 0, 0)
        form.addWidget(self.driver_power_source_combo, 0, 1)
        form.addWidget(self.driver_power_hint, 1, 0, 1, 2)
        form.addWidget(self.driver_power_label, 2, 0); form.addWidget(self.driver_power_combo, 2, 1)
        form.addWidget(QLabel("PA Unit1电源:"), 3, 0); form.addWidget(self.pa_unit1_power_combo, 3, 1)
        form.addWidget(self.pa_unit2_label, 4, 0); form.addWidget(self.pa_unit2_power_combo, 4, 1)
        form.addWidget(self.pa_unit3_label, 5, 0); form.addWidget(self.pa_unit3_power_combo, 5, 1)
        form.setColumnStretch(1, 1)
        layout.addWidget(assignment)
        self.power_assignment_widgets = {"driver_source": self.driver_power_source_combo, "driver_power": self.driver_power_combo,
            "pa_unit1_power": self.pa_unit1_power_combo, "pa_unit2_power": self.pa_unit2_power_combo,
            "pa_unit2_label": self.pa_unit2_label, "pa_unit3_power": self.pa_unit3_power_combo, "pa_unit3_label": self.pa_unit3_label}

    def _build_run_widgets(self, layout):
        """保存按钮与运行入口；与电源配置解耦，便于放在左侧栏底部。"""
        self.save_config_btn = QPushButton("保存配置"); layout.addWidget(self.save_config_btn)
        self.return_welcome_btn = QPushButton("返回欢迎页")
        layout.addWidget(self.return_welcome_btn)

        # 配置阶段入口：校验并创建一次运行。运行创建后测量页面只消费该不可变快照。
        run_group = QGroupBox("运行")
        run_form = QFormLayout(run_group)
        self.create_run_btn = QPushButton("校验并创建运行")
        self.run_status_label = QLabel("当前运行: 未创建")
        self.run_status_label.setWordWrap(True)
        self.load_run_id_edit = QLineEdit()
        self.load_run_id_edit.setMaximumWidth(320)
        self.load_run_id_edit.setPlaceholderText("输入 run_id 以重新打开已有运行")
        self.load_run_btn = QPushButton("加载运行")
        run_form.addRow(self.create_run_btn)
        run_form.addRow(self.run_status_label)
        run_form.addRow("run_id:", self.load_run_id_edit)
        run_form.addRow(self.load_run_btn)
        layout.addWidget(run_group)

    def _connect_signals(self):
        self.connect_btn.clicked.connect(self.request_connect)
        self.save_config_btn.clicked.connect(self.request_save)
        self.create_run_btn.clicked.connect(self.request_create_run)
        self.load_run_btn.clicked.connect(self.request_load_run)
        self.return_welcome_btn.clicked.connect(self.return_welcome_requested.emit)
        self.pa_unit_count_combo.currentTextChanged.connect(self.on_pa_unit_count_changed)
        self.driver_power_source_combo.currentTextChanged.connect(self.on_driver_power_toggled)
        for name in ("ps1", "ps2", "ps3", "ps4"):
            getattr(self, f"{name}_enabled").toggled.connect(self.on_instrument_enabled_changed)

    def request_save(self):
        if self.validate(): self.save_requested.emit()

    def request_connect(self):
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
        """显示当前运行状态；由窗口在创建/加载运行后更新。"""
        self.run_status_label.setText(text)

    def validate(self):
        errors = ConfigFormState(self.config, self).validation_errors()
        if errors:
            self.validation_failed.emit("；".join(errors)); return False
        return True

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
    def on_instrument_enabled_changed(self, _checked=False): self.update_power_supply_options()
    def get_enabled_power_supplies(self):
        return [name for name in DEFAULT_POWER_SUPPLY_NAMES if getattr(self, f"{name.lower()}_enabled").isChecked()]
    def update_power_supply_options(self):
        enabled = self.get_enabled_power_supplies()
        for key in ("driver_power", "pa_unit1_power", "pa_unit2_power", "pa_unit3_power"):
            combo = self.power_assignment_widgets[key]; current = combo.currentText(); combo.clear(); combo.addItems(enabled)
            if current in enabled: combo.setCurrentText(current)

    def build_config(self, base=None):
        """按当前控件值组装完整配置。

        ``base`` 是组装基线（缺省为页面自己的配置副本）。运行协调器传入它持有的
        权威配置，保证“页面只是视图、配置只有一个数据源”。
        """
        base_config = self.config if base is None else base
        return ConfigFormState(base_config, self).build_config()

    def set_test_mode(self, enabled: bool) -> None:
        """设置欢迎页选择的测试模式并同步供电选项。"""
        self.test_mode_enabled = bool(enabled)
        mode = self.config.get("driver_mode", {})
        mode = dict(mode) if isinstance(mode, dict) else {}
        source = mode.get("power_source")
        if source not in ("dc_supply", "external_ac"):
            assignment = self.config.get("power_supply_assignment", {})
            driver = assignment.get("driver_amplifier", {}) if isinstance(assignment, dict) else {}
            source = "dc_supply" if isinstance(driver, dict) and driver.get("power_supply_count", 0) else "external_ac"
        self.driver_power_source_combo.setCurrentText(
            {"dc_supply": "直流电源", "external_ac": "自带供电"}[source]
        )
        self.power_assignment_group.setVisible(self.test_mode_enabled)
        self.update_driver_power_ui()
        self._dirty_baseline = copy.deepcopy(self.build_config(self.config))
        self.driver_mode_changed.emit(self.test_mode_enabled)

    def is_dirty(self) -> bool:
        return self.build_config(self.config) != self._dirty_baseline

    def mark_clean(self) -> None:
        self._dirty_baseline = copy.deepcopy(self.build_config(self.config))

    def driver_mode_requested(self) -> bool:
        """返回欢迎页选择的驱放测试模式。"""
        return bool(self.test_mode_enabled)

    # -- 冻结运行配置回填 ---------------------------------------------------
    def apply_config(self, config) -> None:
        """把一份已冻结的配置编写文档确定性地投影到控件。

        投影规则（重置到空配置基线、逐字段读取编写文档字段、非法类型回落默认值）都在
        :func:`presentation.qt.config_projection.project_authoring_config` 里，页面只
        负责刷新派生界面状态。参数不是 JSON 对象（``dict``）时抛出
        :class:`ConfigBackfillError`，不会被静默当作回填成功；全部投影成功后才更新
        ``self.config``，避免部分失败时页面与文档副本互相矛盾。

        只更新控件显示，不写文件、不保存、不连接仪器；运行期间的测量输入仍来自
        运行目录内的冻结快照。正式 ``test_plan`` / ``run_mapping`` 由
        :func:`presentation.qt.configuration_authoring.author_official_configuration`
        生成，本页面不读取也不解释它们。
        """
        projected = project_authoring_config(self, config)
        self.update_pa_unit_ui()
        self.update_driver_power_ui()
        self.update_power_supply_options()
        # 全部投影成功后再提交文档副本（project_authoring_config 返回深拷贝）。
        self.config = projected
        self._dirty_baseline = copy.deepcopy(self.build_config(self.config))
