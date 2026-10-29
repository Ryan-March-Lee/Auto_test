"""配置页面及其控件联动。

页面拥有配置控件，窗口只通过信号协调保存和连接。该模块只负责控件构建、用户意图
转发和派生界面状态（单元可见性、驱动模式、电源选项）；把冻结配置投影到控件的规则
在 :mod:`presentation.qt.config_projection`，读取控件值组装配置的规则在
:mod:`presentation.qt.config_form_state`。页面不持有逐字段回填逻辑，也不依赖窗口、
测量 controller 或硬件实现。
"""

import copy
import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFormLayout, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QTabWidget,
    QVBoxLayout, QWidget,
)

from domain.instruments.models import POWER_SUPPLY_ROLE, make_device_key

from .config_form_state import (
    POWER_SUPPLY_BINDINGS,
    ConfigFormState,
    binding_address,
    binding_records,
    bound_power_supplies,
    effective_device_bindings,
    power_supply_bindings,
)
from .config_projection import (
    DEFAULT_ATTENUATOR_TYPE,
    DEFAULT_COMPRESSION_TYPE,
    DEFAULT_DRIVER_MODE_ENABLED,
    DRIVER_POWER_SOURCE_VALUES,
    DEFAULT_MAX_INPUT_POWER,
    DEFAULT_PA_UNIT_COUNT,
    DEFAULT_POWER_ASSIGNMENT,
    DEFAULT_SIGNAL_SOURCE,
    DEFAULT_TEST_FREQUENCIES,
    POWER_SUPPLY_ROLES,
    ConfigBackfillError,
    project_authoring_config,
)
from .configuration_authoring import DUT_CHANNEL_ROLES
from .device_discovery_panel import DeviceDiscoveryPanel, role_label, subnet_from_address
from .input_controls import KeyboardOnlyDoubleSpinBox, WheelSafeComboBox
from .pages import PageControllerBindingMixin
from .theme import set_button_variant

__all__ = [
    "ConfigBackfillError",
    "ConfigPage",
]


def _spin_value(value) -> float:
    """通道数值只接受非 bool 的有限数字；其它类型（字符串/布尔/NaN）按 0 处理。

    配置页也用于回填冻结配置：类型不正确的字段不应让重建标签页崩溃，而应落到与投影
    一致的默认值，交给正式校验报告。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    number = float(value)
    return number if math.isfinite(number) else 0.0


class ConfigPage(PageControllerBindingMixin, QWidget):
    save_requested = Signal()
    connect_requested = Signal()
    create_run_requested = Signal()
    load_run_requested = Signal(str)
    return_welcome_requested = Signal()
    validation_failed = Signal(str)
    driver_mode_changed = Signal(bool)
    discovery_message = Signal(str)

    def __init__(self, config=None, parent=None, scanner_factory=None):
        PageControllerBindingMixin.__init__(self)
        QWidget.__init__(self, parent)
        self.config = config if isinstance(config, dict) else {}
        # 设备身份绑定：信号源/频谱仪各一条（角色键），电源按 ``supply_id`` 多条
        # （``power_supplies``）。只记录用户明确应用过的设备；IP 变化时按序列号重新识别，
        # 不靠地址记忆。绑定是仪器连接的唯一来源；构建界面之前就要就位，扫描框初值才能
        # 取到已绑定设备的网段，电源分配的可选项也由它决定。
        self.device_bindings = effective_device_bindings(self.config)
        self._scanner_factory = scanner_factory
        self.build_ui()
        self._connect_signals()
        self.update_pa_unit_ui()
        self.update_driver_power_ui()
        self.update_power_supply_options()
        # 基线用组装结果而不是原始文档：旧配置迁移、绑定写入的启用状态都属于页面自身的
        # 规范化结果，刚打开的页面不应显示为“已修改”。
        self._dirty_baseline = copy.deepcopy(self.build_config(self.config))
        panel = getattr(self, "discovery_panel", None)
        if panel is not None:
            panel.set_device_bindings(self.device_bindings)

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
        # 限制内容最大宽度：窗口过宽时不再把卡片横向拉伸成很长的边框。
        content.setMaximumWidth(1180)
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

        # 仪器连接配置只有一个入口：扫描设备后勾选。页面不再提供手工填写地址的控件，
        # 连接地址只来自用户明确应用过的扫描结果。
        self._build_discovery_widget(left_column)
        connect_row = QHBoxLayout()
        connect_row.setSpacing(10)
        connect_hint = QLabel("应用设备后在此连接本次测试使用的仪器。")
        connect_hint.setProperty("role", "hint")
        connect_row.addWidget(connect_hint, 1)
        self.connect_btn = QPushButton("连接仪器")
        set_button_variant(self.connect_btn, "primary")
        connect_row.addWidget(self.connect_btn)
        left_column.addLayout(connect_row)

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
        """页面底部固定操作栏：保存当前配置或返回欢迎页。"""
        bar = QWidget()
        bar.setObjectName("pageActionBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 4, 0)
        row.setSpacing(10)
        dirty_hint = QLabel("修改参数后请保存配置，运行时使用已保存并冻结的配置。")
        dirty_hint.setProperty("role", "hint")
        row.addWidget(dirty_hint, 1)
        self.save_config_btn = QPushButton("保存配置")
        set_button_variant(self.save_config_btn, "primary")
        self.return_welcome_btn = QPushButton("返回欢迎页")
        set_button_variant(self.return_welcome_btn, "secondary")
        row.addWidget(self.return_welcome_btn)
        row.addWidget(self.save_config_btn)
        return bar

    def _build_discovery_widget(self, layout):
        """仪器连接配置：扫描网段，勾选本次测试所需设备并写入连接配置。

        这是配置页上唯一的仪器连接入口，页面不再提供逐台填写地址或选择角色的控件：
        测试角色由设备表 ``instruments.json`` 登记的 ``role`` 决定，界面只显示
        “设备名（角色）”。面板不改配置、不连接仪器；只有用户点击“应用所选设备到配置”
        时，本页才把发现到的当前地址与设备身份（序列号）记进 :attr:`device_bindings`，
        保存时随配置一起落盘，作为下次按身份重新识别设备的依据。
        """
        factory = self._scanner_factory
        if factory is None:
            from .composition import build_device_scanner_factory

            factory = build_device_scanner_factory()
        self.discovery_panel = DeviceDiscoveryPanel(
            scanner_factory=factory,
            default_subnet=self._default_scan_subnet(),
            title="仪器连接配置",
            parent=self,
        )
        self.discovery_panel.apply_requested.connect(self.apply_discovered_devices)
        self.discovery_panel.clear_requested.connect(self.clear_device_bindings)
        self.discovery_panel.log_message.connect(self.discovery_message.emit)
        layout.addWidget(self.discovery_panel)

    def _default_scan_subnet(self):
        """扫描框初值：优先已绑定设备的地址，其次配置里已有的仪器地址。"""
        for _key, record in binding_records(self.device_bindings):
            subnet = subnet_from_address(binding_address(record) or record.get("last_ip"))
            if subnet:
                return subnet
        instruments = self.config.get("instruments")
        instruments = instruments if isinstance(instruments, dict) else {}
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

    def apply_discovered_devices(self, assignments):
        """把面板勾选的设备写成配置里的连接信息与身份记录。

        角色不是用户选的：它来自设备表 ``instruments.json`` 登记的 ``role``（旧槽位
        ``PS1``..``PS4`` 由面板折叠成 ``power_supply``），面板按它交付。这是写入的最终
        边界，因此它自己的校验必须完整，不能只依赖面板的勾选校验：角色未知、唯一角色
        重复、地址为空、缺少设备身份、身份与序列号不一致、电源缺少 ``supply_id``、同一
        台设备被重复绑定，任何一项都整批拒绝，不留下“一半已应用”的配置。

        绑定结构：信号源与频谱仪用角色键（暂时保持单设备），**电源用
        ``device_bindings.power_supplies[<supply_id>]``**——电源是同一类别下的多台设备，
        一个角色键放不下第二台，每条记录必须带 ``device_key``，同一个 ``device_key``
        不能出现在两条电源绑定里。

        应用是**整批替换**：本次勾选结果就是完整的设备绑定，没勾选的旧角色与旧电源会
        被移除并随地址一起失效。这样“更换设备”不会残留上一台设备的连接，电源分配也
        不会再指向一台本次没有选中的电源。
        """
        valid_roles = {"signal_generator", "spectrum_analyzer", POWER_SUPPLY_ROLE}
        entries = list(assignments or ())
        panel = getattr(self, "discovery_panel", None)

        def reject(message: str) -> None:
            """拒绝整批：日志与面板状态都要看到原因，不能只留在日志里。"""
            if panel is not None:
                panel.show_message(message)
            self.validation_failed.emit(message)

        if not entries:
            reject("请先在发现结果中勾选本次测试所需设备")
            return

        # 第一遍只看角色与电源引用：未知角色、唯一角色重复、电源没有可用的引用 ID 是
        # “这一批要做什么”的问题，先报出来，避免用户看到的是地址/序列号之类更细节的错。
        used_roles: dict[str, str] = {}
        supply_ids: dict[str, str] = {}
        for item in entries:
            role = item.get("role")
            if role not in valid_roles:
                reject(f"未知的测试角色: {role}")
                return
            if role == POWER_SUPPLY_ROLE:
                # 电源是类别不是编号：多台各自独立，这里只判定引用 ID 可用且唯一。
                supply_id = self._supply_id_of(item)
                if not supply_id:
                    reject(
                        f"电源 {item.get('name') or item.get('ip')} 还没有短标签"
                        "（supply_id），没有可写的配置引用；请先在设备表里保存一次为它"
                        "补上标签，再重新扫描"
                    )
                    return
                if supply_id in supply_ids:
                    reject(
                        f"本次应用有两行发现结果都指向电源 {supply_id}"
                        f"（{supply_ids[supply_id]} 与 {item.get('ip')}）；请只保留一台"
                    )
                    return
                supply_ids[supply_id] = str(item.get("ip") or "")
                continue
            if role in used_roles:
                reject(
                    f"角色 {role} 在本次应用中被重复指定（{used_roles[role]} 与 "
                    f"{item.get('ip')}）；请只保留一台设备"
                )
                return
            used_roles[role] = str(item.get("ip") or "")
        # 第二遍校验每条记录的地址与身份，全部通过后才写绑定记录。
        checked: list[tuple[dict, str]] = []
        for item in entries:
            role = item["role"]
            address = item.get("address")
            if not isinstance(address, str) or not address.strip():
                reject(f"{role} 的发现地址为空，未应用")
                return
            serial = str(item.get("serial") or "").strip()
            vendor = str(item.get("vendor") or "").strip()
            if not serial:
                reject(f"{role} 缺少设备身份（序列号），未应用")
                return
            device_key = str(item.get("device_key") or "").strip()
            if device_key and vendor:
                try:
                    expected = make_device_key(vendor, serial)
                except ValueError as error:
                    reject(f"{role} 的设备身份无法解析: {error}")
                    return
                if device_key.casefold() != expected:
                    reject(
                        f"{role} 的设备身份与序列号不一致（{device_key} ≠ {expected}），未应用"
                    )
                    return
            identity = device_key.casefold()
            if not identity and vendor:
                try:
                    identity = make_device_key(vendor, serial)
                except ValueError:
                    identity = ""
            checked.append((item, identity))

        # 第三遍：同一个 device_key 不能出现在两条电源绑定里（同一台设备不能接两次）。
        bound_keys: dict[str, str] = {}
        for item, identity in checked:
            if item["role"] != POWER_SUPPLY_ROLE or not identity:
                continue
            supply_id = self._supply_id_of(item)
            if identity in bound_keys:
                reject(
                    f"电源 {supply_id} 与 {bound_keys[identity]} 是同一台设备"
                    f"（{identity}），同一台设备不能重复绑定；请只保留一台"
                )
                return
            bound_keys[identity] = supply_id

        bindings: dict[str, object] = {}
        supplies: dict[str, dict] = {}
        applied = []
        for item, identity in checked:
            role = item["role"]
            record = {
                "name": item.get("name"),
                "vendor": item.get("vendor"),
                "model": item.get("model"),
                "serial": item.get("serial"),
                # 身份统一写成 ``厂商:序列号`` 的小写形式；记录必须带身份，才能在下一次
                # 扫描时按身份找回同一台设备。
                "device_key": identity or item.get("device_key"),
                "visa_address": item["address"].strip(),
                "last_ip": item.get("ip"),
                # 探测端口只是“这台设备在本网段怎么被读出身份”的记录，不参与拼接连接地址。
                "last_port": item.get("port"),
                "known": bool(item.get("known")),
                # 模拟扫描写入的绑定带来源标记，避免演示数据被当成现场设备记录。
                "source": "simulated" if item.get("simulated") else "scan",
            }
            if role == POWER_SUPPLY_ROLE:
                # 集合的键就是引用 ID：记录里不再重复写 role/label/supply_id。
                supplies[self._supply_id_of(item)] = record
                applied.append(f"电源 {self._supply_id_of(item)}")
            else:
                bindings[role] = record
                applied.append(
                    f"{role_label(role) or role} "
                    f"{item.get('name') or item.get('model') or item.get('ip')}"
                )
        if supplies:
            bindings[POWER_SUPPLY_BINDINGS] = supplies
        previous = self._binding_labels(self.device_bindings)
        current = self._binding_labels(bindings)
        removed = [label for label in previous if label not in set(current)]
        self.device_bindings = bindings
        self._rebuild_power_tabs()
        self.update_power_supply_options()
        if panel is not None:
            panel.set_device_bindings(self.device_bindings)
        message = "已应用发现设备: " + "，".join(applied)
        if removed:
            message += "；已移除本次未勾选的旧绑定: " + "、".join(removed)
        if any(item.get("simulated") for item in entries):
            message += "（模拟数据，仅用于离线演示，不能用于真实测量）"
        if panel is not None:
            panel.show_message(message)
        self.discovery_message.emit(message)

    @staticmethod
    def _supply_id_of(item) -> str:
        """一条电源发现结果对应的配置引用 ID（= 设备表里的短标签）。"""
        if not isinstance(item, dict):
            return ""
        for field in ("supply_id", "label"):
            value = item.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _binding_labels(bindings) -> list[str]:
        """把绑定结构显示成“信号发生器 SMW200A-1 / 电源 DP8B1519”这类可读标签。"""
        labels: list[str] = []
        for key, record in binding_records(bindings):
            name = record.get("name") or record.get("model") or record.get("serial") or ""
            if key in ("signal_generator", "spectrum_analyzer"):
                labels.append(f"{role_label(key) or key} {name}".strip())
            else:
                labels.append(f"电源 {key}")
        return labels

    def clear_device_bindings(self):
        """清除全部设备绑定，并让这些角色的连接信息同时失效。

        绑定是唯一的连接来源，所以清除后对应仪器在保存的配置里变成“未启用、无地址”。
        若只删绑定记录、把旧地址留在配置里，界面看起来已经断开，连接却仍会打到旧设备。
        """
        self.device_bindings = {}
        panel = getattr(self, "discovery_panel", None)
        if panel is not None:
            panel.set_device_bindings({})
            panel.show_message("已清除设备绑定；相关仪器地址与启用状态一并失效，请重新扫描应用")
        self._rebuild_power_tabs()
        self.update_power_supply_options()
        self.discovery_message.emit("已清除设备绑定记录")

    @staticmethod
    def _spin(value, minimum, maximum):
        widget = KeyboardOnlyDoubleSpinBox()
        widget.setRange(minimum, maximum)
        widget.setValue(float(value))
        widget.setMaximumWidth(140)
        return widget

    @staticmethod
    def _combo(items, current):
        widget = WheelSafeComboBox(); widget.addItems(list(items)); widget.setCurrentText(str(current)); return widget

    def _build_power_widgets(self, layout, supplies):
        """电源详细配置：按配置里的电源键与本次绑定动态创建标签页。

        电源数量与身份都由设备绑定决定，不再有 ``PS1``..``PS4`` 四个固定页：配置里原有
        的条目（含尚未迁移的旧槽位）各占一页，本次绑定的每台电源各占一页，标签页标题用
        设备表的短标签。
        """
        detail = QGroupBox("电源详细配置")
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        self.power_tabs = QTabWidget(); self.power_tabs.setObjectName("subTabs")
        self.power_config_widgets = {}
        self._rebuild_power_tabs(
            sources=self._power_supply_sources(supplies), preserve=False
        )
        detail_layout.addWidget(self.power_tabs)
        layout.addWidget(detail)

        assignment = QGroupBox("电源分配设置"); form = QGridLayout(assignment)
        form.setHorizontalSpacing(10); form.setVerticalSpacing(8)
        form.setColumnMinimumWidth(0, 118)
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
        supplies_choices = bound_power_supplies(self.device_bindings)
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

    def _power_supply_sources(self, supplies=None) -> dict[str, dict]:
        """电源标签页要显示的条目：配置里已有的键 + 本次已绑定的 ``supply_id``。

        配置条目保留各自未知字段与通道参数；绑定记录只补身份字段（短标签、型号、序列
        号、设备键），不覆盖配置里已有的值。这样“更换电源”不会沿用上一台的身份，而
        “重装页面”也不会因为配置里已有同名字段而丢掉绑定来源。
        """
        if supplies is None:
            instruments = self.config.get("instruments")
            instruments = instruments if isinstance(instruments, dict) else {}
            supplies = instruments.get("power_supplies")
        sources: dict[str, dict] = {}
        if isinstance(supplies, dict):
            for key, item in supplies.items():
                if isinstance(item, dict):
                    sources[str(key)] = item
        for supply_id, record in power_supply_bindings(self.device_bindings).items():
            entry = dict(sources.get(supply_id) or {})
            for field in ("label", "model", "serial", "device_key"):
                value = record.get(field)
                if value and not entry.get(field):
                    entry[field] = value
            entry.setdefault("label", supply_id)
            sources[supply_id] = entry
        return sources

    def _power_channel_values(self) -> dict[str, dict]:
        """读取当前电源标签页里的通道值，重建标签页时用它保留用户已填的参数。"""
        if not getattr(self, "power_config_widgets", None):
            return {}
        fragment = ConfigFormState(self.config, self).read_power_supply_config()
        return fragment.get("instruments", {}).get("power_supplies", {}) or {}

    def _rebuild_power_tabs(self, *, sources=None, preserve=True) -> None:
        """按电源键重建标签页；``preserve`` 时保留当前控件里的通道参数。

        电源数量变化（应用/清除绑定、加载另一份配置）后重建，避免按位置把某一台设备的
        参数写到另一台上。
        """
        tabs = getattr(self, "power_tabs", None)
        if tabs is None:
            return
        if sources is None:
            sources = self._power_supply_sources()
        existing = self._power_channel_values() if preserve else {}
        current = tabs.currentIndex()
        tabs.clear()
        self.power_config_widgets = {}
        for key, source in sources.items():
            source = source if isinstance(source, dict) else {}
            channels = existing.get(key)
            if not isinstance(channels, dict):
                channels = source.get("channels")
            if not isinstance(channels, dict):
                channels = {}
            self._add_power_tab(key, source, channels)
        if tabs.count() and current >= 0:
            tabs.setCurrentIndex(min(current, tabs.count() - 1))

    def _add_power_tab(self, key, source, channels) -> None:
        """为一条电源配置建一个标签页；标题优先用短标签与型号。"""
        page = QWidget(); page_layout = QVBoxLayout(page)
        page_layout.setSpacing(10)
        record = {"channels": {}}
        for channel in ("CH1", "CH2"):
            value = channels.get(channel, {})
            if not isinstance(value, dict): value = {}
            voltage = value.get("voltage", {}); current = value.get("current", {})
            if not isinstance(voltage, dict): voltage = {}
            if not isinstance(current, dict): current = {}
            box = QGroupBox(f"通道 {channel}"); form = QFormLayout(box)
            form.setHorizontalSpacing(10); form.setVerticalSpacing(6)
            form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            role = value.get("role")
            if role not in POWER_SUPPLY_ROLES:
                role = DUT_CHANNEL_ROLES.get(channel, POWER_SUPPLY_ROLES[0])
            controls = {
                "role": self._combo(POWER_SUPPLY_ROLES, role),
                "voltage": self._spin(_spin_value(voltage.get("value")), 0, 50),
                "voltage_protection": self._spin(_spin_value(voltage.get("protection")), 0, 50),
                "voltage_protection_enabled": QCheckBox("启用"),
                "current": self._spin(_spin_value(current.get("value")), 0, 10),
                "current_protection": self._spin(_spin_value(current.get("protection")), 0, 10),
                "current_protection_enabled": QCheckBox("启用"),
            }
            controls["voltage_protection_enabled"].setChecked(voltage.get("protection_enabled") is True)
            controls["current_protection_enabled"].setChecked(current.get("protection_enabled") is True)
            form.addRow("供电角色", controls["role"])
            form.addRow("电压 (V)", controls["voltage"]); form.addRow("保护电压 (V)", controls["voltage_protection"])
            form.addRow("电压保护", controls["voltage_protection_enabled"]); form.addRow("电流 (A)", controls["current"])
            form.addRow("保护电流 (A)", controls["current_protection"]); form.addRow("电流保护", controls["current_protection_enabled"])
            page_layout.addWidget(box); record["channels"][channel] = controls
        title = str(source.get("label") or key)
        model = source.get("model")
        if model and str(model) != title:
            title = f"{title}（{model}）"
        self.power_tabs.addTab(page, title)
        self.power_config_widgets[key] = record

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
        self.run_status_label = QLabel("当前运行: 未创建")
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
        self.connect_btn.clicked.connect(self.request_connect)
        self.save_config_btn.clicked.connect(self.request_save)
        self.create_run_btn.clicked.connect(self.request_create_run)
        self.load_run_btn.clicked.connect(self.request_load_run)
        self.return_welcome_btn.clicked.connect(self.return_welcome_requested.emit)
        self.pa_unit_count_combo.currentTextChanged.connect(self.on_pa_unit_count_changed)
        self.driver_power_source_combo.currentTextChanged.connect(self.on_driver_power_toggled)

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

    def close(self) -> None:
        """关闭页面时先协作停止设备扫描，再解除 controller 连接。"""
        panel = getattr(self, "discovery_panel", None)
        if panel is not None and not panel.shutdown():
            self.discovery_message.emit("设备扫描线程未能在关闭期限内停止")
        super().close()

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
    def get_enabled_power_supplies(self):
        """电源分配里可选的电源：只来自当前已应用的设备绑定。

        “启用”不再由界面上的勾选框决定：``instruments.power_supplies.<name>.enabled``
        由绑定写入，因此可选项与保存出的配置使用同一个来源，不存在“界面能选、配置里
        却未启用”的错位。
        """
        return bound_power_supplies(self.device_bindings)
    def update_power_supply_options(self):
        """刷新电源分配下拉框：只保留当前绑定的电源，失效引用保持空选择。

        配置引用的电源不再绑定时不能被静默换成列表里的第一台——那会把一次设备更换变成
        一次看不见的分配改动。空选择交给校验明确报告，由操作员重新选择。
        """
        enabled = self.get_enabled_power_supplies()
        for key in ("driver_power", "pa_unit1_power", "pa_unit2_power", "pa_unit3_power"):
            combo = self.power_assignment_widgets[key]
            current = combo.currentText()
            combo.clear()
            combo.addItems(enabled)
            if current in enabled:
                combo.setCurrentText(current)
            else:
                combo.setCurrentIndex(-1)

    def build_config(self, base=None):
        """按当前控件值组装完整配置。

        ``base`` 是组装基线（缺省为页面自己的配置副本）。运行协调器传入它持有的
        权威配置，保证“页面只是视图、配置只有一个数据源”。
        """
        base_config = self.config if base is None else base
        config = ConfigFormState(base_config, self).build_config()
        # 设备身份绑定随配置一起保存：角色 -> 序列号等稳定标识。各仪器的 address 由
        # 绑定里的当前地址写入（当前测量链路按地址连接），身份用于下次扫描时按序列号
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
        self.device_bindings = bindings
        try:
            self._rebuild_power_tabs(
                sources=self._power_supply_sources(supplies),
                preserve=False,
            )
            projected = project_authoring_config(
                self, config, supplies=bound_power_supplies(bindings)
            )
        except ConfigBackfillError:
            self.device_bindings = previous_bindings
            self._rebuild_power_tabs(sources=self._power_supply_sources(), preserve=False)
            raise
        self.config = projected
        panel = getattr(self, "discovery_panel", None)
        if panel is not None:
            panel.set_device_bindings(self.device_bindings)
            panel.clear_results()
        self.update_pa_unit_ui()
        self.update_driver_power_ui()
        self.update_power_supply_options()
        self._dirty_baseline = copy.deepcopy(self.build_config(self.config))
