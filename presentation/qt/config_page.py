"""配置页面及其控件联动。

页面拥有配置控件，窗口只通过信号协调保存和连接。该模块不依赖窗口、测量
controller 或硬件实现。
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QTabWidget,
    QVBoxLayout, QWidget,
)

from .config_form_state import ConfigFormState
from .pages import PageControllerBindingMixin


class ConfigPage(PageControllerBindingMixin, QWidget):
    save_requested = Signal()
    connect_requested = Signal()
    create_run_requested = Signal()
    load_run_requested = Signal(str)
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

    def build_ui(self):
        """按页面协议构建并返回配置页根 widget。"""
        self._build_ui()
        return self

    def _build_ui(self):
        root = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
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
        grid.setColumnStretch(1, 3)
        entries = (("sg", "信号发生器", "signal_generator", True),
                   ("sa", "频谱分析仪", "spectrum_analyzer", True))
        for row, (key, label, source, default) in enumerate(entries):
            item = instruments.get(source, {})
            if not isinstance(item, dict): item = {}
            address = QLineEdit(str(item.get("address", "")))
            enabled = QCheckBox("启用")
            enabled.setChecked(item.get("enabled", default))
            setattr(self, f"{key}_address", address)
            setattr(self, f"{key}_enabled", enabled)
            grid.addWidget(QLabel(f"{label}地址:"), row, 0)
            grid.addWidget(address, row, 1)
            grid.addWidget(enabled, row, 2)
        for row, name in enumerate(("PS1", "PS2", "PS3", "PS4"), 2):
            item = supplies.get(name, {})
            if not isinstance(item, dict): item = {}
            address = QLineEdit(str(item.get("address", "")))
            enabled = QCheckBox("启用")
            enabled.setChecked(item.get("enabled", name in ("PS1", "PS2")))
            setattr(self, f"{name.lower()}_address", address)
            setattr(self, f"{name.lower()}_enabled", enabled)
            grid.addWidget(QLabel(f"{name}地址:"), row, 0)
            grid.addWidget(address, row, 1)
            grid.addWidget(enabled, row, 2)
        self.connect_btn = QPushButton("连接仪器")
        grid.addWidget(self.connect_btn, 6, 0, 1, 2)
        layout.addWidget(connection)

        params = QGroupBox("测试参数配置")
        form = QFormLayout(params)
        test = self.config.get("signal_source", {})
        if not isinstance(test, dict): test = {}
        self.freq_edit = QLineEdit(str(self.config.get("test_frequencies", [4.0, 4.4, 4.8, 5.2, 5.6, 5.8])))
        self.start_power = self._spin(test.get("start_power", -38), -50, 20)
        self.stop_power = self._spin(test.get("stop_power", -16), -50, 20)
        self.power_step = self._spin(test.get("step", 1), 0.1, 5)
        compression = self.config.get("compression_point", {})
        attenuator = self.config.get("attenuator", {})
        if not isinstance(compression, dict): compression = {}
        if not isinstance(attenuator, dict): attenuator = {}
        self.compression_combo = self._combo(("1dB", "3dB", "5dB"), compression.get("type", "5dB"))
        self.attenuator_combo = self._combo(("30dB", "40dB"), attenuator.get("type", "40dB"))
        self.driver_mode_check = QCheckBox("启用驱动功放模式")
        mode = self.config.get("driver_mode", {})
        self.driver_mode_check.setChecked(mode.get("enabled", True) if isinstance(mode, dict) else True)
        dut = self.config.get("dut_config", {})
        if not isinstance(dut, dict): dut = {}
        self.max_input_power = self._spin(dut.get("max_input_power", 33.5), 0, 50)
        self.pa_unit_count_combo = self._combo(("1", "2", "3"), str(dut.get("power_supply_count", 2)))
        for label, widget in (("测试频率 (GHz)", self.freq_edit), ("起始功率 (dBm)", self.start_power),
                              ("结束功率 (dBm)", self.stop_power), ("功率步长 (dB)", self.power_step),
                              ("压缩点", self.compression_combo), ("衰减器", self.attenuator_combo),
                              ("DUT最大输入功率 (dBm)", self.max_input_power), ("PA单元数量", self.pa_unit_count_combo)):
            form.addRow(label, widget)
        form.addRow(self.driver_mode_check)
        layout.addWidget(params)
        self._build_power_widgets(layout, supplies)

    @staticmethod
    def _spin(value, minimum, maximum):
        widget = QDoubleSpinBox()
        widget.setRange(minimum, maximum)
        widget.setValue(float(value))
        return widget

    @staticmethod
    def _combo(items, current):
        widget = QComboBox(); widget.addItems(list(items)); widget.setCurrentText(str(current)); return widget

    def _build_power_widgets(self, layout, supplies):
        detail = QGroupBox("电源详细配置")
        detail_layout = QVBoxLayout(detail)
        tabs = QTabWidget(); self.power_config_widgets = {}
        for name in ("PS1", "PS2", "PS3", "PS4"):
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
                controls = {
                    "voltage": self._spin(voltage.get("value", 0), 0, 50),
                    "voltage_protection": self._spin(voltage.get("protection", 0), 0, 50),
                    "voltage_protection_enabled": QCheckBox("启用"),
                    "current": self._spin(current.get("value", 0), 0, 10),
                    "current_protection": self._spin(current.get("protection", 0), 0, 10),
                    "current_protection_enabled": QCheckBox("启用"),
                }
                controls["voltage_protection_enabled"].setChecked(voltage.get("protection_enabled", False))
                controls["current_protection_enabled"].setChecked(current.get("protection_enabled", False))
                form.addRow("电压 (V)", controls["voltage"]); form.addRow("保护电压 (V)", controls["voltage_protection"])
                form.addRow("电压保护", controls["voltage_protection_enabled"]); form.addRow("电流 (A)", controls["current"])
                form.addRow("保护电流 (A)", controls["current_protection"]); form.addRow("电流保护", controls["current_protection_enabled"])
                page_layout.addWidget(box); record["channels"][channel] = controls
            tabs.addTab(page, name); self.power_config_widgets[name] = record
        detail_layout.addWidget(tabs); layout.addWidget(detail)

        assignment = QGroupBox("电源分配设置"); form = QFormLayout(assignment)
        existing = self.config.get("power_supply_assignment", {})
        if not isinstance(existing, dict): existing = {}
        driver = existing.get("driver_amplifier", {}); dut = existing.get("dut_amplifier", {})
        if not isinstance(driver, dict): driver = {}
        if not isinstance(dut, dict): dut = {}
        self.driver_power_enabled = QCheckBox("启用驱动功放电源")
        self.driver_power_enabled.setChecked(driver.get("power_supply_count", 0) > 0)
        driver_supply = driver.get("supplies", {}); dut_supply = dut.get("supplies", {})
        if not isinstance(driver_supply, dict): driver_supply = {}
        if not isinstance(dut_supply, dict): dut_supply = {}
        main = driver_supply.get("main", {}) if isinstance(driver_supply.get("main", {}), dict) else {}
        carrier = dut_supply.get("carrier", {}) if isinstance(dut_supply.get("carrier", {}), dict) else {}
        peaking = dut_supply.get("peaking", {}) if isinstance(dut_supply.get("peaking", {}), dict) else {}
        peaking2 = dut_supply.get("peaking2", {}) if isinstance(dut_supply.get("peaking2", {}), dict) else {}
        self.driver_power_combo = self._combo(("PS1", "PS2", "PS3", "PS4"), main.get("name", "PS1"))
        self.pa_unit1_power_combo = self._combo(("PS1", "PS2", "PS3", "PS4"), carrier.get("name", "PS2"))
        self.pa_unit2_power_combo = self._combo(("PS1", "PS2", "PS3", "PS4"), peaking.get("name", "PS3"))
        self.pa_unit3_power_combo = self._combo(("PS1", "PS2", "PS3", "PS4"), peaking2.get("name", "PS4"))
        self.driver_power_label = QLabel("驱动功放电源:"); self.pa_unit2_label = QLabel("PA Unit2电源:"); self.pa_unit3_label = QLabel("PA Unit3电源:")
        form.addRow(self.driver_power_enabled); form.addRow(self.driver_power_label, self.driver_power_combo)
        form.addRow("PA Unit1电源:", self.pa_unit1_power_combo); form.addRow(self.pa_unit2_label, self.pa_unit2_power_combo); form.addRow(self.pa_unit3_label, self.pa_unit3_power_combo)
        layout.addWidget(assignment)
        self.power_assignment_widgets = {"driver_enabled": self.driver_power_enabled, "driver_power": self.driver_power_combo,
            "pa_unit1_power": self.pa_unit1_power_combo, "pa_unit2_power": self.pa_unit2_power_combo,
            "pa_unit2_label": self.pa_unit2_label, "pa_unit3_power": self.pa_unit3_power_combo, "pa_unit3_label": self.pa_unit3_label}
        self.save_config_btn = QPushButton("保存配置"); layout.addWidget(self.save_config_btn)

        # 配置阶段入口：校验并创建一次运行。运行创建后测量页面只消费该不可变快照。
        run_group = QGroupBox("运行")
        run_form = QFormLayout(run_group)
        self.create_run_btn = QPushButton("校验并创建运行")
        self.run_status_label = QLabel("当前运行: 未创建")
        self.run_status_label.setWordWrap(True)
        self.load_run_id_edit = QLineEdit()
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
        self.pa_unit_count_combo.currentTextChanged.connect(self.on_pa_unit_count_changed)
        self.driver_mode_check.toggled.connect(self.on_driver_mode_toggled)
        self.driver_power_enabled.toggled.connect(self.on_driver_power_toggled)
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
    def on_driver_mode_toggled(self, checked):
        self.update_driver_power_ui()
        self.driver_mode_changed.emit(bool(checked))
    def on_driver_power_toggled(self, _checked): self.update_driver_power_ui()
    def update_driver_power_ui(self):
        enabled = self.driver_mode_check.isChecked() and self.driver_power_enabled.isChecked()
        self.driver_power_enabled.setEnabled(self.driver_mode_check.isChecked())
        self.driver_power_combo.setEnabled(enabled); self.driver_power_label.setEnabled(enabled)
        if not enabled: self.driver_power_combo.setCurrentIndex(-1)
    def on_instrument_enabled_changed(self, _checked=False): self.update_power_supply_options()
    def get_enabled_power_supplies(self):
        return [name for name in ("PS1", "PS2", "PS3", "PS4") if getattr(self, f"{name.lower()}_enabled").isChecked()]
    def update_power_supply_options(self):
        enabled = self.get_enabled_power_supplies()
        for key in ("driver_power", "pa_unit1_power", "pa_unit2_power", "pa_unit3_power"):
            combo = self.power_assignment_widgets[key]; current = combo.currentText(); combo.clear(); combo.addItems(enabled)
            if current in enabled: combo.setCurrentText(current)

    def build_config(self): return ConfigFormState(self.config, self).build_config()

    # -- 冻结运行配置回填 ---------------------------------------------------
    def apply_config(self, config) -> None:
        """把一份已冻结配置回填到控件，使界面与当前运行保持一致。

        只更新控件显示，不写文件、不保存、不连接仪器；运行期间的测量输入仍来自
        运行目录内的冻结快照。
        """
        if not isinstance(config, dict):
            return
        self.config = config
        self._apply_instrument_widgets(config.get("instruments"))
        self._apply_test_parameter_widgets(config)
        self._apply_power_supply_widgets(config.get("instruments"))
        self._apply_power_assignment_widgets(config.get("power_supply_assignment"))
        self.update_pa_unit_ui()
        self.update_driver_power_ui()
        self.update_power_supply_options()

    def _apply_instrument_widgets(self, instruments) -> None:
        instruments = instruments if isinstance(instruments, dict) else {}
        supplies = instruments.get("power_supplies")
        supplies = supplies if isinstance(supplies, dict) else {}
        for name, source in (("sg", "signal_generator"), ("sa", "spectrum_analyzer")):
            item = instruments.get(source)
            item = item if isinstance(item, dict) else {}
            address = getattr(self, f"{name}_address", None)
            if address is not None:
                address.setText(str(item.get("address", "")))
            enabled = getattr(self, f"{name}_enabled", None)
            if enabled is not None:
                enabled.setChecked(bool(item.get("enabled", True)))
        for name in ("PS1", "PS2", "PS3", "PS4"):
            item = supplies.get(name)
            item = item if isinstance(item, dict) else {}
            address = getattr(self, f"{name.lower()}_address", None)
            if address is not None:
                address.setText(str(item.get("address", "")))
            enabled = getattr(self, f"{name.lower()}_enabled", None)
            if enabled is not None:
                enabled.setChecked(bool(item.get("enabled", name in ("PS1", "PS2"))))

    def _apply_test_parameter_widgets(self, config) -> None:
        frequencies = config.get("test_frequencies")
        if isinstance(frequencies, list):
            self.freq_edit.setText(str(frequencies))
        signal_source = config.get("signal_source")
        signal_source = signal_source if isinstance(signal_source, dict) else {}
        for widget, key, default in (
            (self.start_power, "start_power", -38),
            (self.stop_power, "stop_power", -16),
            (self.power_step, "step", 1),
        ):
            value = signal_source.get(key, default)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                widget.setValue(float(value))
        compression = config.get("compression_point")
        compression = compression if isinstance(compression, dict) else {}
        self._set_combo(self.compression_combo, compression.get("type"))
        attenuator = config.get("attenuator")
        attenuator = attenuator if isinstance(attenuator, dict) else {}
        self._set_combo(self.attenuator_combo, attenuator.get("type"))
        mode = config.get("driver_mode")
        mode = mode if isinstance(mode, dict) else {}
        self.driver_mode_check.setChecked(bool(mode.get("enabled", True)))
        dut = config.get("dut_config")
        dut = dut if isinstance(dut, dict) else {}
        value = dut.get("max_input_power")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            self.max_input_power.setValue(float(value))
        self._set_combo(self.pa_unit_count_combo, dut.get("power_supply_count"))

    def _apply_power_supply_widgets(self, instruments) -> None:
        instruments = instruments if isinstance(instruments, dict) else {}
        supplies = instruments.get("power_supplies")
        supplies = supplies if isinstance(supplies, dict) else {}
        for name, record in getattr(self, "power_config_widgets", {}).items():
            source = supplies.get(name)
            source = source if isinstance(source, dict) else {}
            channels = source.get("channels")
            channels = channels if isinstance(channels, dict) else {}
            for channel, controls in record.get("channels", {}).items():
                values = channels.get(channel)
                values = values if isinstance(values, dict) else {}
                self._apply_channel_widgets(controls, values)

    @staticmethod
    def _apply_channel_widgets(controls, values) -> None:
        voltage = values.get("voltage")
        voltage = voltage if isinstance(voltage, dict) else {}
        current = values.get("current")
        current = current if isinstance(current, dict) else {}
        for key, value in (("voltage", voltage.get("value")), ("voltage_protection", voltage.get("protection")),
                           ("current", current.get("value")), ("current_protection", current.get("protection"))):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                controls[key].setValue(float(value))
        for key, value in (("voltage_protection_enabled", voltage.get("protection_enabled")),
                           ("current_protection_enabled", current.get("protection_enabled"))):
            controls[key].setChecked(bool(value))

    def _apply_power_assignment_widgets(self, assignment) -> None:
        assignment = assignment if isinstance(assignment, dict) else {}
        driver = assignment.get("driver_amplifier")
        driver = driver if isinstance(driver, dict) else {}
        dut = assignment.get("dut_amplifier")
        dut = dut if isinstance(dut, dict) else {}
        widgets = getattr(self, "power_assignment_widgets", {})
        if not widgets:
            return
        driver_supplies = driver.get("supplies")
        driver_supplies = driver_supplies if isinstance(driver_supplies, dict) else {}
        main = driver_supplies.get("main")
        main = main if isinstance(main, dict) else {}
        enabled = driver.get("power_supply_count", 0)
        widgets["driver_enabled"].setChecked(isinstance(enabled, (int, float)) and enabled > 0)
        self._set_combo(widgets["driver_power"], main.get("name"))
        dut_supplies = dut.get("supplies")
        dut_supplies = dut_supplies if isinstance(dut_supplies, dict) else {}
        for role, key in (("carrier", "pa_unit1_power"), ("peaking", "pa_unit2_power"), ("peaking2", "pa_unit3_power")):
            item = dut_supplies.get(role)
            item = item if isinstance(item, dict) else {}
            if item.get("name"):
                self._set_combo(widgets[key], item.get("name"))

    @staticmethod
    def _set_combo(combo, value) -> None:
        if value is None:
            return
        text = str(value)
        if combo.findText(text) >= 0:
            combo.setCurrentText(text)
