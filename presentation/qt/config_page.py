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

    def _connect_signals(self):
        self.connect_btn.clicked.connect(self.request_connect)
        self.save_config_btn.clicked.connect(self.request_save)
        self.pa_unit_count_combo.currentTextChanged.connect(self.on_pa_unit_count_changed)
        self.driver_mode_check.toggled.connect(self.on_driver_mode_toggled)
        self.driver_power_enabled.toggled.connect(self.on_driver_power_toggled)
        for name in ("ps1", "ps2", "ps3", "ps4"):
            getattr(self, f"{name}_enabled").toggled.connect(self.on_instrument_enabled_changed)

    def request_save(self):
        if self.validate(): self.save_requested.emit()

    def request_connect(self):
        if self.validate(): self.connect_requested.emit()

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
