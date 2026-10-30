"""配置页面及其控件联动。

页面拥有配置控件，窗口只通过信号协调保存和连接。该模块只负责控件组合、用户意图
转发和派生界面状态（单元可见性、驱动模式、电源选项）；电源分区的控件构建与标签页
重建在 :mod:`presentation.qt.config_power_section`，把冻结配置投影到控件的规则在
:mod:`presentation.qt.config_projection`，读取控件值组装配置的规则在
:mod:`presentation.qt.config_form_state`。页面把电源分区要显示的条目、可选项与
「槽位 -> 配置路径」的映射（``driver_amplifier.supplies.main`` 等，见
:meth:`ConfigPage._assignment_entries`）读出来交给构建模块，构建模块不判断这些路径。
页面不持有逐字段回填逻辑，也不依赖窗口、测量 controller 或硬件实现。

仪表配置（扫描、勾选设备、设备身份绑定、连接仪器）已独立成
:mod:`presentation.qt.connection_page`：本页不再创建发现面板，也不再提供连接入口，
只在 :meth:`ConfigPage.attach_discovery_panel` 里登记那一页交出的事件面板，并保留写
绑定的边界（整批校验 + 写地址/身份）与派生界面更新。连接请求仍由
:meth:`ConfigPage.request_connect` 校验后经 ``connect_requested`` 交给窗口。
"""

import copy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
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
    DEFAULT_MAX_INPUT_POWER,
    DEFAULT_PA_UNIT_COUNT,
    DEFAULT_SIGNAL_SOURCE,
    DEFAULT_TEST_FREQUENCIES,
    POWER_ASSIGNMENT_SLOTS,
    ConfigBackfillError,
    apply_power_choice,
    driver_power_source_label,
    project_authoring_config,
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
from .theme import set_button_variant

__all__ = [
    "ConfigBackfillError",
    "ConfigPage",
]

# 电源分配下拉框的控件键。界面上的接线位置，与电源设备数量无关：这里只决定刷新时
# 遍历哪些下拉框，选哪台电源完全来自配置引用与当前绑定集合。
POWER_ASSIGNMENT_COMBO_KEYS = POWER_ASSIGNMENT_SLOTS


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

    def __init__(self, config=None, parent=None):
        PageControllerBindingMixin.__init__(self)
        QWidget.__init__(self, parent)
        self.config = config if isinstance(config, dict) else {}
        # 设备身份绑定：信号源/频谱仪各一条（角色键），电源按 ``supply_id`` 多条
        # （``power_supplies``）。只记录用户明确应用过的设备；IP 变化时按序列号重新识别，
        # 不靠地址记忆。绑定是仪器连接的唯一来源；构建界面之前就要就位，电源标签页的
        # 条目与电源分配的可选项都由它决定。
        self.device_bindings = effective_device_bindings(self.config)
        # 配置里原本就不可用的电源绑定：读入时会从可用集合里剔除，但必须在这里留下记录，
        # 由界面提示并在保存/运行校验时明确报错——否则它们会表现为“电源莫名消失”。
        self.binding_problems = self._binding_problems_of(self.config)
        # 「仪表配置」页交出的事件面板：写绑定之后由本页回写它的状态文本。
        # 未登记面板时（纯配置页场景）所有回写都跳过。
        self.discovery_panel = None
        self.build_ui()
        self._connect_signals()
        self.update_pa_unit_ui()
        self.update_driver_power_ui()
        self.update_power_supply_options()
        # 基线用组装结果而不是原始文档：旧配置迁移、绑定写入的启用状态都属于页面自身的
        # 规范化结果，刚打开的页面不应显示为“已修改”。
        self._dirty_baseline = copy.deepcopy(self.build_config(self.config))

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

    def attach_discovery_panel(self, panel):
        """登记「仪表配置」页上的设备发现面板，并接上应用/清除意图。

        面板归独立页面（:mod:`presentation.qt.connection_page`）所有：它只管扫描与
        勾选，不改配置、不连接仪器。用户点“应用所选设备到配置”时，面板把所选设备交给
        本页，本页整批校验后把当前地址与设备身份（序列号）记进 :attr:`device_bindings`，
        保存时随配置一起落盘，作为下次按身份重新识别设备的依据；面板只负责显示结果。

        “应用/清除”写的是配置页状态，因此信号连在本页；面板的日志经
        :attr:`discovery_message` 转给窗口统一呈现。
        """
        self.discovery_panel = panel
        panel.apply_requested.connect(self.apply_discovered_devices)
        panel.clear_requested.connect(self.clear_device_bindings)
        panel.log_message.connect(self.discovery_message.emit)
        panel.set_device_bindings(self.device_bindings)
        if self.binding_problems:
            panel.show_message(
                "；".join(self.binding_problems)
                + "；请重新扫描并应用这些电源，或修正配置后再保存"
            )

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
        """把重建标签页要用的页面状态读出来，交给构建模块。

        条目来源（配置键 + 本次绑定）与控件里的通道参数都是页面状态，所以在这里取；
        合并规则、按电源键恢复选中页与逐页控件构建都在
        :mod:`presentation.qt.config_power_section`。``preserve=False`` 是回填路径，以
        新配置为准，不沿用重建前的残留值。本方法是同名保留的委托壳：
        ``tests/ui/test_config_page.py`` 直接调用它。
        """
        if getattr(self, "power_tabs", None) is None:
            return
        rebuild_power_tabs(
            self,
            sources=self._power_supply_sources() if sources is None else sources,
            preserved=self._power_channel_values() if preserve else {},
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
        """显示当前运行状态；由窗口在创建/加载运行后更新。"""
        self.run_status_label.setText(text)

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

    def _power_supply_choices(self) -> PowerSupplyChoices:
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
