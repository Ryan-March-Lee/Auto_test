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

from domain.instruments.models import (
    POWER_SUPPLY_ROLE,
    make_device_key,
    normalize_serial,
)

from .config_form_state import (
    POWER_SUPPLY_BINDINGS,
    ConfigFormState,
    binding_address,
    binding_records,
    bound_power_supply_entries,
    bound_power_supplies,
    effective_device_bindings,
    power_supply_binding_problems,
    power_supply_bindings,
    supply_id_problem,
)
from .config_projection import (
    DEFAULT_ATTENUATOR_TYPE,
    DEFAULT_COMPRESSION_TYPE,
    DEFAULT_DRIVER_MODE_ENABLED,
    DRIVER_POWER_SOURCE_VALUES,
    DEFAULT_MAX_INPUT_POWER,
    DEFAULT_PA_UNIT_COUNT,
    DEFAULT_SIGNAL_SOURCE,
    DEFAULT_TEST_FREQUENCIES,
    POWER_ASSIGNMENT_SLOTS,
    POWER_SUPPLY_ROLES,
    ConfigBackfillError,
    apply_power_choice,
    driver_power_source_label,
    project_authoring_config,
    supply_option_label,
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

# 电源分配下拉框的控件键。界面上的接线位置，与电源设备数量无关：这里只决定刷新时
# 遍历哪些下拉框，选哪台电源完全来自配置引用与当前绑定集合。
POWER_ASSIGNMENT_COMBO_KEYS = POWER_ASSIGNMENT_SLOTS


def _spin_value(value) -> float:
    """通道数值只接受非 bool 的有限数字；其它类型（字符串/布尔/NaN）按 0 处理。

    配置页也用于回填冻结配置：类型不正确的字段不应让重建标签页崩溃，而应落到与投影
    一致的默认值，交给正式校验报告。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    number = float(value)
    return number if math.isfinite(number) else 0.0


def _entry_channels(entry) -> dict:
    """取出一个电源条目的 ``通道 -> 参数`` 映射；不是对象或没有映射时返回空字典。

    条目本身是一份完整电源配置（``{model, channels: {...}}``）。
    """
    channels = entry.get("channels") if isinstance(entry, dict) else None
    return channels if isinstance(channels, dict) else {}


def _channel_map(source, preserved):
    """取出一个电源条目的 ``通道 -> 参数`` 映射，供重建标签页时建控件。

    ``source`` 是这次要显示的配置条目，``preserved`` 是重建前从控件读到的同类条目
    （``preserve=False`` 时为空）。两者合并的优先级是“**控件里刚填的 > 配置里的**”：

    - 重建发生在保存之前（应用绑定、清除绑定、再次回填），操作者刚在控件里改过的参数
      必须保留，否则刷新会把它悄悄改回旧配置值；``preserve=True`` 的整个目的就是这个。
    - 配置里已有、但这次没有对应控件的通道仍从配置取（例如新绑定电源的缺省参数）。

    因此 ``preserve=False`` 时 ``preserved`` 为空，结果就是配置本身；``preserve=True``
    时控件快照优先。判定按 ``supply_id`` + 通道键，与标签页位置无关。
    """
    channels = dict(_entry_channels(source))
    for channel, value in _entry_channels(preserved).items():
        if isinstance(value, dict):
            merged = dict(channels.get(channel) or {})
            merged.update(value)
            channels[channel] = merged
    return channels


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
        # 配置里原本就不可用的电源绑定：读入时会从可用集合里剔除，但必须在这里留下记录，
        # 由界面提示并在保存/运行校验时明确报错——否则它们会表现为“电源莫名消失”。
        self.binding_problems = self._binding_problems_of(self.config)
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
            if self.binding_problems:
                panel.show_message(
                    "；".join(self.binding_problems)
                    + "；请重新扫描并应用这些电源，或修正配置后再保存"
                )

    @staticmethod
    def _binding_problems_of(config) -> list[str]:
        """列出一份配置里不可用的电源绑定（读入边界与加载路径共用）。"""
        devices = config.get("device_bindings") if isinstance(config, dict) else None
        return [
            f"设备绑定不可用：{problem}"
            for problem in power_supply_binding_problems(devices)
        ]

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
        assignment = self.config.get("power_supply_assignment", {})
        driver = assignment.get("driver_amplifier") if isinstance(assignment, dict) else {}
        driver = driver if isinstance(driver, dict) else {}
        self.driver_power_source = driver_power_source_label(
            mode.get("power_source"), driver.get("power_supply_count")
        )
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
        重复、地址为空、缺少设备身份、身份与序列号不一致、电源缺少 ``supply_id`` 或
        ``supply_id`` 不能作为配置引用、同一台设备被重复绑定，任何一项都整批拒绝，
        不留下“一半已应用”的配置。

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
                reason = supply_id_problem(supply_id)
                if reason:
                    # 引用会拼成 ``supply_id/CHx``：含空白或 ``/`` 的标签写进配置后
                    # 路由会截断，宁可整批拒绝并指出要修的标签，也不悄悄改名。
                    reject(
                        f"电源短标签 {supply_id!r} 不能作为配置引用（{reason}）；"
                        "请先在设备表里修正该标签并保存，再重新扫描"
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
            if device_key and not vendor:
                # 只有 ``device_key`` 时至少要求它携带的序列号与本次读到的序列号一致，
                # 否则地址会被写到一个身份自相矛盾的名字下。
                tail = device_key.rsplit(":", 1)[-1].strip().casefold()
                if tail != normalize_serial(serial).casefold():
                    reject(
                        f"{role} 的设备身份与序列号不一致（{device_key} ≠ {serial}），未应用"
                    )
                    return
            if not identity and vendor:
                try:
                    identity = make_device_key(vendor, serial)
                except ValueError:
                    identity = ""
            if role == POWER_SUPPLY_ROLE and not identity:
                # 电源绑定的身份是唯一匹配依据：没有它，下一次扫描找不回这台设备，
                # 配置读取也会把这条记录判为不可用，等于写了一条坏绑定。
                reject(
                    f"电源 {self._supply_id_of(item)} 缺少可用于绑定的设备身份"
                    "（device_key，或厂商 + 序列号），未应用"
                )
                return
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
        # 这一批记录刚刚通过全部校验：之前读入时的坏绑定要么被替换、要么已被移除。
        self.binding_problems = []
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
        self.binding_problems = []
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

    @staticmethod
    def _power_choice_combo(choices, entry):
        """电源分配下拉框：只把配置里的真实引用回填到当前可选项。

        引用按 ``supply_id`` 读取（旧配置只有 ``name`` 时读一次，见
        :func:`config_form_state.assignment_supply_id`），并且必须**出现在本次绑定的
        可选项里**才会被选中。配置写了引用、但那台电源本次没有绑定（含还没迁移的旧槽位
        ``PS1``）时保持空选择，交给校验报告——静默停在第一项会把“这台电源已经不在”
        显示成“换成了另一台电源”。

        显示文本是 ``DP8B1519（DP832A）`` 这样的短标签加型号，业务值放在 ``itemData``
        （``supply_id``）；选中与读取都只看数据，不从显示字符串反解析身份。

        两种状态严格分开（见 :func:`config_form_state.assignment_declared`）：配置里
        **没有**这条分配时落到第一台可用电源；配置里**有**这条分配但引用为空
        （``supply_id: null``）时保持空选择，不能按缺省规则补一台。判定与整份配置回填
        共用 :func:`presentation.qt.config_projection.apply_power_choice`，所有槽位
        （含驱动功放）用同一条缺省规则；驱动功放是否启用由 :meth:`update_driver_power_ui`
        按驱放模式与供电方式决定。
        """
        combo = WheelSafeComboBox()
        apply_power_choice(combo, entry, choices)
        return combo

    def _build_power_widgets(self, layout, supplies):
        """电源详细配置：按配置里的电源键与本次绑定动态创建标签页。

        电源数量与身份都由设备绑定决定，不再有 ``PS1``..``PS4`` 四个固定页：配置里原有
        的条目（含尚未迁移的旧槽位）各占一页，本次绑定的每台电源各占一页，标签页标题用
        短标签加型号（``DP8B1519（DP832A）``）。
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
        self.driver_power_source_combo = self._combo(
            DRIVER_POWER_SOURCE_VALUES, str(self.driver_power_source)
        )
        self.driver_power_source_label = QLabel("驱放供电方式:")
        self.driver_power_hint = QLabel("自带供电的驱放由外部供电设备供电，本系统不会控制其电源。")
        self.driver_power_hint.setWordWrap(True)
        driver_supply = driver.get("supplies", {}); dut_supply = dut.get("supplies", {})
        if not isinstance(driver_supply, dict): driver_supply = {}
        if not isinstance(dut_supply, dict): dut_supply = {}
        # 条目缺失（键不存在/不是对象）与“声明了但未绑定”在回填时是两回事：
        # 前者按缺省规则选第一台可用电源，后者保持空选择，因此这里保留 ``None``。
        main = driver_supply.get("main")
        carrier = dut_supply.get("carrier")
        peaking = dut_supply.get("peaking")
        peaking2 = dut_supply.get("peaking2")
        supplies_choices = self._power_supply_choices()
        # 所有槽位（含驱动功放）用同一条缺省规则：已有引用优先，否则第一台可用电源。
        # 驱动功放是否真的启用由 update_driver_power_ui() 按驱放模式与供电方式处理。
        self.driver_power_combo = self._power_choice_combo(supplies_choices, main)
        self.pa_unit1_power_combo = self._power_choice_combo(supplies_choices, carrier)
        self.pa_unit2_power_combo = self._power_choice_combo(supplies_choices, peaking)
        self.pa_unit3_power_combo = self._power_choice_combo(supplies_choices, peaking2)
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
            for field in ("label", "model", "serial", "device_key"):
                value = record.get(field)
                if isinstance(value, str) and value.strip():
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
        参数写到另一台上。通道参数按**配置键**取回（不是按标签页位置），当前选中的标签页
        也按**电源键**恢复：某台电源被移除或新增时，操作者仍然停在原来那台设备上。
        ``preserve=True`` 时控件里刚填的通道值优先于配置里的旧值（见 :func:`_channel_map`）；
        ``preserve=False`` 是回填路径，以新配置为准，不沿用重建前的残留值。
        """
        tabs = getattr(self, "power_tabs", None)
        if tabs is None:
            return
        if sources is None:
            sources = self._power_supply_sources()
        existing = self._power_channel_values() if preserve else {}
        current_key = self._current_power_tab_key()
        tabs.clear()
        self.power_config_widgets = {}
        for key, source in sources.items():
            source = source if isinstance(source, dict) else {}
            channels = _channel_map(source, existing.get(key))
            self._add_power_tab(key, source, channels)
        if current_key in self.power_config_widgets:
            tabs.setCurrentIndex(tabs.indexOf(self.power_config_widgets[current_key]["page"]))

    def _current_power_tab_key(self) -> str | None:
        """当前选中电源标签页的电源键；没有标签页时返回 ``None``。"""
        tabs = getattr(self, "power_tabs", None)
        if tabs is None or not tabs.count():
            return None
        index = tabs.currentIndex()
        for key, record in getattr(self, "power_config_widgets", {}).items():
            if record.get("page") is tabs.widget(index):
                return key
        return None

    def _add_power_tab(self, key, source, channels) -> None:
        """为一条电源配置建一个标签页；标题是短标签加型号（``DP8B1519（DP832A）``）。

        标签页按配置键（``supply_id``）建，标题只用于显示：设备数量与身份都来自配置与
        当前绑定，没有固定的四个槽位页。
        """
        page = QWidget(); page_layout = QVBoxLayout(page)
        page_layout.setSpacing(10)
        record = {"channels": {}, "page": page}
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
        title = supply_option_label(source.get("label") or key, source.get("model"))
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
        # 读入时判定为不可用的设备绑定先报出来：它们已经从可用电源里剔除，若不在这里
        # 明确失败，保存就会把对应条目的地址清空，看起来像“电源无故消失”。
        errors = list(getattr(self, "binding_problems", []))
        errors.extend(ConfigFormState(self.config, self).validation_errors())
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
        return list(self._power_supply_choices())
    def update_power_supply_options(self):
        """刷新电源分配下拉框：只保留当前绑定的电源，失效引用保持空选择。

        选项按 ``supply_id`` 重建，显示文本是短标签加型号（``DP8B1519（DP832A）``），
        业务值放在 ``itemData``。刷新按**数据值**恢复上一次的选择，不按位置、不按可见
        文本：``QComboBox.clear()`` 会把当前项重置到第一项，按位置保存就会把“这台电源
        已经不在”变成一次看不见的分配改动。

        配置引用的电源不再绑定时不能被静默换成列表里的第一台——空选择交给校验明确报告，
        由操作员重新选择。
        """
        options = self._power_supply_choices()
        for key in POWER_ASSIGNMENT_COMBO_KEYS:
            combo = self.power_assignment_widgets[key]
            current = self._combo_supply_id(combo)
            apply_power_choice(combo, {"supply_id": current}, options)

    @staticmethod
    def _combo_supply_id(combo) -> str:
        """下拉框当前选中项的 ``supply_id``（``itemData``）；空选择时返回空串。"""
        if combo is None:
            return ""
        find_data = getattr(combo, "findData", None)
        if not callable(find_data):
            return ""
        index = combo.currentIndex()
        data = combo.itemData(index) if isinstance(index, int) and index >= 0 else None
        return data.strip() if isinstance(data, str) and data.strip() else ""

    def _power_supply_choices(self) -> dict[str, dict]:
        """电源分配下拉框的可选项：当前绑定的电源，带真实设备的型号用于显示。"""
        return bound_power_supply_entries(self.device_bindings)

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
            projected = project_authoring_config(
                self, config, supplies=self._power_supply_choices()
            )
        except ConfigBackfillError:
            self.device_bindings = previous_bindings
            self.binding_problems = previous_problems
            self.driver_power_source = previous_driver_source
            self._rebuild_power_tabs(sources=self._power_supply_sources(), preserve=False)
            raise
        self.config = projected
        self.binding_problems = self._binding_problems_of(config)
        panel = getattr(self, "discovery_panel", None)
        if panel is not None:
            panel.set_device_bindings(self.device_bindings)
            panel.clear_results()
        if self.binding_problems:
            self.discovery_message.emit("；".join(self.binding_problems))
        self.update_pa_unit_ui()
        self.update_driver_power_ui()
        self.update_power_supply_options()
        self._dirty_baseline = copy.deepcopy(self.build_config(self.config))
