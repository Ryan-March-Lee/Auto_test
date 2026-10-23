"""配置页面及其控件联动。

页面拥有配置控件，窗口只通过信号协调保存和连接。该模块不依赖窗口、测量
controller 或硬件实现。
"""

import copy
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QTabWidget,
    QVBoxLayout, QWidget,
)

from .config_form_state import ConfigFormState, parse_frequency_text
from .pages import PageControllerBindingMixin


class ConfigBackfillError(ValueError):
    """冻结配置无法确定性地回填到配置页控件时抛出。

    回填失败必须可识别：调用方要么收到该异常，要么据此明确报告“未回填成功”，
    不得把它静默当作加载成功。
    """


# 配置页受控控件的空配置基线。``apply_config()`` 先重置到这些默认值，再投影
# 冻结配置，从而保证 ``apply_config(config)`` 的显示结果与 ``ConfigPage(config)``
# 的构造结果一致，不会残留上一次运行的显示状态。
DEFAULT_TEST_FREQUENCIES = [4.0, 4.4, 4.8, 5.2, 5.6, 5.8]
DEFAULT_SIGNAL_SOURCE = {"start_power": -38.0, "stop_power": -16.0, "step": 1.0}
DEFAULT_COMPRESSION_TYPE = "5dB"
DEFAULT_ATTENUATOR_TYPE = "40dB"
DEFAULT_DRIVER_MODE_ENABLED = True
DEFAULT_MAX_INPUT_POWER = 33.5
DEFAULT_PA_UNIT_COUNT = "2"
DEFAULT_ENABLED_POWER_SUPPLIES = ("PS1", "PS2")
DEFAULT_POWER_SUPPLY_NAMES = ("PS1", "PS2", "PS3", "PS4")
# (控件前缀, 中文标签, 配置键, 默认启用) —— 同时供 build 与 reset 使用。
INSTRUMENT_ENTRIES = (
    ("sg", "信号发生器", "signal_generator", True),
    ("sa", "频谱分析仪", "spectrum_analyzer", True),
)
# 电源分配组合框的默认映射，重置时逐项恢复。
DEFAULT_POWER_ASSIGNMENT = {
    "driver_power": "PS1",
    "pa_unit1_power": "PS2",
    "pa_unit2_power": "PS3",
    "pa_unit3_power": "PS4",
}


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
        for row, (key, label, source, default) in enumerate(INSTRUMENT_ENTRIES):
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
        for row, name in enumerate(DEFAULT_POWER_SUPPLY_NAMES, 2):
            item = supplies.get(name, {})
            if not isinstance(item, dict): item = {}
            address = QLineEdit(str(item.get("address", "")))
            enabled = QCheckBox("启用")
            enabled.setChecked(item.get("enabled", name in DEFAULT_ENABLED_POWER_SUPPLIES))
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
        self.freq_edit = QLineEdit(str(self.config.get("test_frequencies", DEFAULT_TEST_FREQUENCIES)))
        self.start_power = self._spin(test.get("start_power", DEFAULT_SIGNAL_SOURCE["start_power"]), -50, 20)
        self.stop_power = self._spin(test.get("stop_power", DEFAULT_SIGNAL_SOURCE["stop_power"]), -50, 20)
        self.power_step = self._spin(test.get("step", DEFAULT_SIGNAL_SOURCE["step"]), 0.1, 5)
        compression = self.config.get("compression_point", {})
        attenuator = self.config.get("attenuator", {})
        if not isinstance(compression, dict): compression = {}
        if not isinstance(attenuator, dict): attenuator = {}
        self.compression_combo = self._combo(("1dB", "3dB", "5dB"), compression.get("type", DEFAULT_COMPRESSION_TYPE))
        self.attenuator_combo = self._combo(("30dB", "40dB"), attenuator.get("type", DEFAULT_ATTENUATOR_TYPE))
        self.driver_mode_check = QCheckBox("启用驱动功放模式")
        mode = self.config.get("driver_mode", {})
        self.driver_mode_check.setChecked(mode.get("enabled", DEFAULT_DRIVER_MODE_ENABLED) if isinstance(mode, dict) else DEFAULT_DRIVER_MODE_ENABLED)
        dut = self.config.get("dut_config", {})
        if not isinstance(dut, dict): dut = {}
        self.max_input_power = self._spin(dut.get("max_input_power", DEFAULT_MAX_INPUT_POWER), 0, 50)
        self.pa_unit_count_combo = self._combo(("1", "2", "3"), str(dut.get("power_supply_count", DEFAULT_PA_UNIT_COUNT)))
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
        supplies_choices = DEFAULT_POWER_SUPPLY_NAMES
        self.driver_power_combo = self._combo(supplies_choices, main.get("name", DEFAULT_POWER_ASSIGNMENT["driver_power"]))
        self.pa_unit1_power_combo = self._combo(supplies_choices, carrier.get("name", DEFAULT_POWER_ASSIGNMENT["pa_unit1_power"]))
        self.pa_unit2_power_combo = self._combo(supplies_choices, peaking.get("name", DEFAULT_POWER_ASSIGNMENT["pa_unit2_power"]))
        self.pa_unit3_power_combo = self._combo(supplies_choices, peaking2.get("name", DEFAULT_POWER_ASSIGNMENT["pa_unit3_power"]))
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
        return [name for name in DEFAULT_POWER_SUPPLY_NAMES if getattr(self, f"{name.lower()}_enabled").isChecked()]
    def update_power_supply_options(self):
        enabled = self.get_enabled_power_supplies()
        for key in ("driver_power", "pa_unit1_power", "pa_unit2_power", "pa_unit3_power"):
            combo = self.power_assignment_widgets[key]; current = combo.currentText(); combo.clear(); combo.addItems(enabled)
            if current in enabled: combo.setCurrentText(current)

    def build_config(self): return ConfigFormState(self.config, self).build_config()

    # -- 冻结运行配置回填 ---------------------------------------------------
    @staticmethod
    def _text_or_empty(value: Any) -> str:
        """只接受字符串文本（如仪器地址）；其它类型一律视为缺失，返回空串。"""
        return value if isinstance(value, str) else ""

    @staticmethod
    def _bool_or_default(value: Any, default: bool) -> bool:
        """只接受真正的布尔值；其它类型（含 ``"false"``/``1``）视为缺失，用默认值。"""
        return value if isinstance(value, bool) else default

    @staticmethod
    def _number_or_none(value: Any) -> float | None:
        """只接受非 bool 的数字；其它类型（含 ``"12"``/``True``）视为缺失。"""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    def apply_config(self, config) -> None:
        """把一份已冻结配置确定性地回填到控件，使界面与当前运行保持一致。

        执行顺序固定：先把所有受控控件重置到空配置基线，再逐字段投影冻结配置。
        缺失或类型不正确的字段会落到明确默认值（不会被强制转换成字符串或布尔
        值），绝不残留上一次运行的显示状态；``apply_config(config)`` 的显示结果
        与 ``ConfigPage(config)`` 的构造结果一致，便于审计。只有全部投影成功后
        才更新 ``self.config``，避免部分回填失败时页面与配置副本互相矛盾。

        只更新控件显示，不写文件、不保存、不连接仪器；运行期间的测量输入仍来自
        运行目录内的冻结快照。参数不是 JSON 对象（``dict``）时抛出
        :class:`ConfigBackfillError`，避免把回填失败静默当作加载成功。
        """
        if not isinstance(config, dict):
            raise ConfigBackfillError(
                "冻结配置必须是 JSON 对象（dict），无法确定性地回填配置页"
            )
        self._reset_control_widgets()
        self._apply_instrument_widgets(config.get("instruments"))
        self._apply_test_parameter_widgets(config)
        self._apply_power_supply_widgets(config.get("instruments"))
        self._apply_power_assignment_widgets(config.get("power_supply_assignment"))
        self.update_pa_unit_ui()
        self.update_driver_power_ui()
        self.update_power_supply_options()
        # 全部投影成功后再提交配置副本，使用深拷贝避免与调用方共享可变结构。
        self.config = copy.deepcopy(config)

    def _reset_control_widgets(self) -> None:
        """把所有受控控件重置到空配置基线，消除上一次运行的残留值。"""
        for name, _label, _source, default_enabled in INSTRUMENT_ENTRIES:
            address = getattr(self, f"{name}_address", None)
            if address is not None:
                address.setText("")
            enabled = getattr(self, f"{name}_enabled", None)
            if enabled is not None:
                enabled.setChecked(bool(default_enabled))
        for name in DEFAULT_POWER_SUPPLY_NAMES:
            address = getattr(self, f"{name.lower()}_address", None)
            if address is not None:
                address.setText("")
            enabled = getattr(self, f"{name.lower()}_enabled", None)
            if enabled is not None:
                enabled.setChecked(name in DEFAULT_ENABLED_POWER_SUPPLIES)
        self.freq_edit.setText(str(list(DEFAULT_TEST_FREQUENCIES)))
        self.start_power.setValue(DEFAULT_SIGNAL_SOURCE["start_power"])
        self.stop_power.setValue(DEFAULT_SIGNAL_SOURCE["stop_power"])
        self.power_step.setValue(DEFAULT_SIGNAL_SOURCE["step"])
        self._set_combo(self.compression_combo, DEFAULT_COMPRESSION_TYPE)
        self._set_combo(self.attenuator_combo, DEFAULT_ATTENUATOR_TYPE)
        self.driver_mode_check.setChecked(DEFAULT_DRIVER_MODE_ENABLED)
        self.max_input_power.setValue(DEFAULT_MAX_INPUT_POWER)
        self._set_combo(self.pa_unit_count_combo, DEFAULT_PA_UNIT_COUNT)
        self._reset_power_supply_channels()
        self._reset_power_assignment_widgets()

    def _reset_power_supply_channels(self) -> None:
        for record in getattr(self, "power_config_widgets", {}).values():
            for controls in record.get("channels", {}).values():
                for key in ("voltage", "voltage_protection", "current", "current_protection"):
                    controls[key].setValue(0.0)
                for key in ("voltage_protection_enabled", "current_protection_enabled"):
                    controls[key].setChecked(False)

    def _reset_power_assignment_widgets(self) -> None:
        widgets = getattr(self, "power_assignment_widgets", {})
        if not widgets:
            return
        widgets["driver_enabled"].setChecked(False)
        for key, default in DEFAULT_POWER_ASSIGNMENT.items():
            self._set_combo(widgets[key], default)

    def _apply_instrument_widgets(self, instruments) -> None:
        instruments = instruments if isinstance(instruments, dict) else {}
        supplies = instruments.get("power_supplies")
        supplies = supplies if isinstance(supplies, dict) else {}
        for name, _label, source, default_enabled in INSTRUMENT_ENTRIES:
            item = instruments.get(source)
            item = item if isinstance(item, dict) else {}
            address = getattr(self, f"{name}_address", None)
            if address is not None:
                address.setText(self._text_or_empty(item.get("address")))
            enabled = getattr(self, f"{name}_enabled", None)
            if enabled is not None:
                enabled.setChecked(self._bool_or_default(item.get("enabled"), bool(default_enabled)))
        for name in DEFAULT_POWER_SUPPLY_NAMES:
            item = supplies.get(name)
            item = item if isinstance(item, dict) else {}
            address = getattr(self, f"{name.lower()}_address", None)
            if address is not None:
                address.setText(self._text_or_empty(item.get("address")))
            enabled = getattr(self, f"{name.lower()}_enabled", None)
            if enabled is not None:
                enabled.setChecked(
                    self._bool_or_default(
                        item.get("enabled"), name in DEFAULT_ENABLED_POWER_SUPPLIES
                    )
                )

    def _apply_test_parameter_widgets(self, config) -> None:
        frequencies = config.get("test_frequencies")
        # 复用频率解析规则：非列表、空列表或含非法元素时保持基线默认值。
        if isinstance(frequencies, list) and parse_frequency_text(repr(frequencies)) is not None:
            self.freq_edit.setText(str(frequencies))
        signal_source = config.get("signal_source")
        signal_source = signal_source if isinstance(signal_source, dict) else {}
        for widget, key in (
            (self.start_power, "start_power"),
            (self.stop_power, "stop_power"),
            (self.power_step, "step"),
        ):
            value = self._number_or_none(signal_source.get(key))
            if value is not None:
                widget.setValue(value)
        compression = config.get("compression_point")
        compression = compression if isinstance(compression, dict) else {}
        self._set_combo(self.compression_combo, compression.get("type"))
        attenuator = config.get("attenuator")
        attenuator = attenuator if isinstance(attenuator, dict) else {}
        self._set_combo(self.attenuator_combo, attenuator.get("type"))
        mode = config.get("driver_mode")
        mode = mode if isinstance(mode, dict) else {}
        self.driver_mode_check.setChecked(
            self._bool_or_default(mode.get("enabled"), DEFAULT_DRIVER_MODE_ENABLED)
        )
        dut = config.get("dut_config")
        dut = dut if isinstance(dut, dict) else {}
        value = self._number_or_none(dut.get("max_input_power"))
        if value is not None:
            self.max_input_power.setValue(value)
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

    def _apply_channel_widgets(self, controls, values) -> None:
        voltage = values.get("voltage")
        voltage = voltage if isinstance(voltage, dict) else {}
        current = values.get("current")
        current = current if isinstance(current, dict) else {}
        for key, value in (("voltage", voltage.get("value")), ("voltage_protection", voltage.get("protection")),
                           ("current", current.get("value")), ("current_protection", current.get("protection"))):
            number = self._number_or_none(value)
            if number is not None:
                controls[key].setValue(number)
        for key, value in (("voltage_protection_enabled", voltage.get("protection_enabled")),
                           ("current_protection_enabled", current.get("protection_enabled"))):
            controls[key].setChecked(self._bool_or_default(value, False))

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
        count = self._number_or_none(driver.get("power_supply_count"))
        widgets["driver_enabled"].setChecked(count is not None and count > 0)
        self._set_combo(
            widgets["driver_power"],
            self._text_or_empty(main.get("name")) or DEFAULT_POWER_ASSIGNMENT["driver_power"],
        )
        dut_supplies = dut.get("supplies")
        dut_supplies = dut_supplies if isinstance(dut_supplies, dict) else {}
        for role, key in (("carrier", "pa_unit1_power"), ("peaking", "pa_unit2_power"), ("peaking2", "pa_unit3_power")):
            item = dut_supplies.get(role)
            item = item if isinstance(item, dict) else {}
            self._set_combo(
                widgets[key],
                self._text_or_empty(item.get("name")) or DEFAULT_POWER_ASSIGNMENT[key],
            )

    @staticmethod
    def _set_combo(combo, value) -> None:
        # 布尔值不是合法选项（避免 ``True`` 被当成 "True"）：视为缺失，保持当前项。
        if isinstance(value, bool) or value is None:
            return
        text = str(value)
        if combo.findText(text) >= 0:
            combo.setCurrentText(text)
