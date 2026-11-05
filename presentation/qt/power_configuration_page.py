"""独立电源配置页（``PowerConfigurationPage``）。

电源参数、供电分配、通道上电计划与效率测量漏极都归本页；测试参数、运行入口与
设备发现仍属于 :mod:`presentation.qt.configuration_page` 与
:mod:`presentation.qt.instrument_setup_page`。页面只把控件组合成显式的电源片段，
不写文件、不连接仪器、不导入基础设施或硬件模块；统一保存、版本门禁与运行冻结由
协调器/配置会话在后续接线中完成。

页面与完整配置页共享同一份编辑草稿：本页的 :meth:`build_fragment` 只返回
``instruments.power_supplies``、``power_supply_assignment``、
``dut_config.drain_channel`` 与 ``driver_mode.power_source`` 这些电源字段，调用方把
它们合并进草稿即可，不会覆盖另一页尚未保存的测试参数。:meth:`apply_config` 回填时
也只触碰这些字段，绑定变化重建控件时保留操作者尚未提交的通道参数、上电顺序、间隔
与漏极。

窗口用 :class:`presentation.qt.configuration_editor.ConfigurationEditor` 把本页与
参数页装配成共享编辑状态，整份草稿的合并/校验/保存仍由
:class:`presentation.qt.application_coordinator.ApplicationCoordinator` 完成；页面
不持有另一页的实例，也不写文件。

漏极候选只来自有效 DUT 物理通道（已有 DUT 计划时限定在计划内，计划尚未生成时退回
分配通道），页面上明确写出“漏极仅用于效率测量，不影响上电顺序”。漏极不是上电
通道角色，也不参与排序、间隔或安全关断。
"""

from __future__ import annotations

import copy
from typing import Any, Mapping

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGroupBox,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .config_form_adapter import ConfigurationFormAdapter
from .config_form_bindings import (
    bound_power_supply_entries,
    effective_device_bindings,
    power_supply_binding_problems,
    power_supply_bindings,
)
from .config_power_section import (
    PowerAssignmentEntries,
    PowerFormSection,
    PowerSupplyChoices,
    PowerSupplySources,
    current_power_tab_key,
)
from .config_projection import (
    DEFAULT_PA_UNIT_COUNT,
    ConfigBackfillError,
    driver_power_source_label,
)
from .pages import PageControllerBindingMixin
from .theme import PAGE_CONTENT_MAX_WIDTH

__all__ = ["PowerConfigurationPage"]


class PowerConfigurationPage(PageControllerBindingMixin, QWidget):
    """独立电源配置页：通道参数、供电分配、组计划和漏极。

    页面通过 :meth:`build_fragment` 提交自己负责的片段，通过 :meth:`apply_config`
    接收同一份编辑草稿中的电源字段；两种方向都不读写其它页面的控件。绑定状态由
    构造时的配置与 :meth:`apply_device_bindings` 更新，驱动模式变化经
    :meth:`set_test_mode` 转发给表单派生状态，并发出 ``driver_mode_changed`` 供
    窗口刷新相关页面。
    """

    validation_failed = Signal(str)
    driver_mode_changed = Signal(bool)
    #: 任一电源字段变化时发出；统一会话用它汇总 dirty（步骤 13 接线）。
    fragment_changed = Signal()

    def __init__(self, config: Any = None, parent: Any = None) -> None:
        PageControllerBindingMixin.__init__(self)
        QWidget.__init__(self, parent)
        self.config = config if isinstance(config, dict) else {}
        self.device_bindings = effective_device_bindings(self.config)
        self.binding_problems = self._binding_problems_of(self.config)
        mode = self.config.get("driver_mode")
        mode = mode if isinstance(mode, dict) else {}
        self.test_mode_enabled = bool(mode.get("enabled", True))
        assignment = self.config.get("power_supply_assignment")
        driver = assignment.get("driver_amplifier") if isinstance(assignment, dict) else {}
        driver = driver if isinstance(driver, dict) else {}
        self.driver_power_source = driver_power_source_label(
            mode.get("power_source"), driver.get("power_supply_count")
        )
        dut = self.config.get("dut_config")
        dut = dut if isinstance(dut, dict) else {}
        self.pa_unit_count = max(0, self._int_or(dut.get("power_supply_count"), 2))
        self.power_section: PowerFormSection | None = None
        # 回填/重建期间控件会连续发出 valueChanged；那是程序写入，不是用户编辑。
        self._silent = False
        self._finalizing = False
        self._fragment_signal_connections: list[tuple[Any, Any]] = []
        self.build_ui()
        self._connect_signals()
        self.update_pa_unit_ui()
        self.update_driver_power_ui()
        self._refresh_binding_status()
        self._dirty_baseline = copy.deepcopy(self.build_config(self.config))
        self.mark_clean()

    # ---- 页面协议 -----------------------------------------------------------

    def build_ui(self) -> Any:
        """按页面协议构建并返回本页根 widget。"""
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        content = QWidget()
        content.setMaximumWidth(PAGE_CONTENT_MAX_WIDTH)
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 4, 0)
        column.setSpacing(12)
        column.addWidget(self._build_binding_widgets())
        self._build_power_widgets(column)
        column.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        return self

    def _build_binding_widgets(self) -> QGroupBox:
        """设备绑定状态：只显示本次已应用电源，不在本页提供发现/扫描入口。"""
        group = QGroupBox("设备绑定")
        layout = QVBoxLayout(group)
        layout.setSpacing(6)
        self.binding_status_label = QLabel()
        self.binding_status_label.setObjectName("powerBindingStatus")
        self.binding_status_label.setProperty("role", "hint")
        self.binding_status_label.setWordWrap(True)
        layout.addWidget(self.binding_status_label)
        self.binding_group = group
        return group

    def _build_power_widgets(self, layout: Any) -> None:
        self.power_section = PowerFormSection(
            sources=self._power_supply_sources(),
            choices=self._power_supply_choices(),
            assignment_entries=self._assignment_entries(),
            driver_power_source=self.driver_power_source,
            dut_supply_count=self.pa_unit_count,
            driver_plan_active=self._driver_plan_active(),
            dut_power_plan=self._power_plan_entry("dut_amplifier"),
            driver_power_plan=self._power_plan_entry("driver_amplifier"),
            drain_channel=self._drain_channel_entry(),
        )
        layout.addWidget(self.power_section)

    def _connect_signals(self) -> None:
        if self.power_section is None:
            return
        source = self.power_section.power_assignment_widgets.get("driver_source")
        if source is not None and hasattr(source, "currentTextChanged"):
            source.currentTextChanged.connect(self.on_driver_power_toggled)
        self._wire_fragment_signals()

    def _wire_fragment_signals(self) -> None:
        """把任一电源字段的编辑都汇总成 ``fragment_changed``。

        通道参数（电压/电流及保护值）在绑定变化重建标签页时会换成新控件，因此这里
        每次都先断开上一批连接、再按当前控件重新连接；否则重建之后修改通道参数不会
        通知共享会话，dirty 会漏掉电源片段。
        """
        for signal, slot in self._fragment_signal_connections:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._fragment_signal_connections = []
        section = self.power_section
        if section is None:
            return

        def hook(signal: Any, slot: Any) -> None:
            connect = getattr(signal, "connect", None)
            if connect is None:
                return
            connect(slot)
            self._fragment_signal_connections.append((signal, slot))

        def notify(*_args: Any) -> None:
            self._emit_fragment_changed()

        for supply in getattr(section, "power_config_widgets", {}).values():
            for controls in supply.get("channels", {}).values():
                for key in ("voltage", "voltage_protection", "current", "current_protection"):
                    widget = controls.get(key)
                    if widget is not None and hasattr(widget, "valueChanged"):
                        hook(widget.valueChanged, notify)
                for key in ("voltage_protection_enabled", "current_protection_enabled"):
                    widget = controls.get(key)
                    if widget is not None and hasattr(widget, "toggled"):
                        hook(widget.toggled, notify)
        for key in (
            "driver_power",
            "pa_unit1_power",
            "pa_unit2_power",
            "pa_unit3_power",
        ):
            combo = section.power_assignment_widgets.get(key)
            if combo is not None and hasattr(combo, "currentIndexChanged"):
                hook(combo.currentIndexChanged, notify)
        for editor in (section.dut_power_plan_editor, section.driver_power_plan_editor):
            hook(editor.list.itemChanged, notify)
            hook(editor.delay_spin.valueChanged, notify)
        hook(section.drain_channel_combo.currentIndexChanged, notify)

    def _emit_fragment_changed(self) -> None:
        """只在用户编辑时发出变更通知；回填与重建不算编辑。"""
        if not self._silent:
            self.fragment_changed.emit()

    # ---- 表单 Adapter -------------------------------------------------------

    @property
    def form_adapter(self) -> ConfigurationFormAdapter:
        """控件读写的唯一入口；Adapter 无状态，按需创建避免引用环。"""
        return ConfigurationFormAdapter(self, on_error=self.validation_failed.emit)

    # ---- 页面级派生状态 -----------------------------------------------------

    def on_driver_power_toggled(self, _value: Any = None) -> None:
        if self._silent:
            # 回填/重置会程序化切换组合框；页面状态由 apply_config 按文档算出，
            # 不能在这里被重置时的中间值（直流电源）覆盖。
            return
        self.driver_power_source = self.driver_power_source_combo.currentText()
        self.update_driver_power_ui()
        self._emit_fragment_changed()

    def update_driver_power_ui(self) -> None:
        """驱放是否占用本机电源：关闭驱放或自带供电时禁用驱动槽位与计划。"""
        enabled = self._driver_plan_active()
        self.driver_power_combo.setEnabled(enabled)
        self.driver_power_label.setEnabled(enabled)
        self.driver_power_hint.setVisible(self.test_mode_enabled and not enabled)
        if not enabled:
            self.driver_power_combo.setCurrentIndex(-1)
        if self.power_section is not None:
            self.power_section.set_driver_plan_active(enabled)

    def update_pa_unit_ui(self) -> None:
        """PA 单元数量只控制 DUT 分配槽位的可见性与候选，不增减电源标签页。"""
        for index in (2, 3):
            visible = self.pa_unit_count >= index
            self.power_assignment_widgets[f"pa_unit{index}_power"].setVisible(visible)
            self.power_assignment_widgets[f"pa_unit{index}_label"].setVisible(visible)
        if self.power_section is not None:
            self.power_section.set_dut_supply_count(self.pa_unit_count)

    def set_pa_unit_count(self, count: int) -> None:
        count = max(0, int(count))
        if count == self.pa_unit_count:
            return
        self.pa_unit_count = count
        self._silent = True
        try:
            self.update_pa_unit_ui()
        finally:
            self._silent = False
        self._emit_fragment_changed()

    def set_test_mode(self, enabled: bool) -> None:
        """设置欢迎页选择的驱放测试模式，并刷新依赖它的电源派生状态。

        只改派生状态与可见性；干净基线由共享会话在加载/保存成功时统一重置。这里
        直接 ``mark_clean()`` 会把操作者尚未保存的电源修改悄悄变成“已保存”。
        """
        enabled = bool(enabled)
        self.test_mode_enabled = enabled
        self._silent = True
        try:
            self.power_assignment_group.setVisible(enabled)
            self.update_driver_power_ui()
        finally:
            self._silent = False
        self._refresh_binding_status()
        self.driver_mode_changed.emit(enabled)

    def apply_device_bindings(self, bindings: Any) -> None:
        """用新的设备绑定刷新电源选项与标签页，保留尚未提交的编辑。

        绑定写入仍由仪器设置页经配置会话完成；本页只接收结果，不自己扫描设备、
        不挑默认电源，也不改写配置引用。
        """
        self.device_bindings = bindings if isinstance(bindings, Mapping) else {}
        self.binding_problems = self._binding_problems_of({"device_bindings": self.device_bindings})
        self._silent = True
        try:
            self._rebuild_power_tabs(preserve=True)
            self.form_adapter.refresh_power_choices(self._power_supply_choices())
            if self.power_section is not None:
                self.power_section.refresh_plan_routes(preserve=True)
        finally:
            self._silent = False
        self._refresh_binding_status()
        self._emit_fragment_changed()

    # ---- 绑定状态 -----------------------------------------------------------

    @staticmethod
    def _binding_problems_of(config: Any) -> list[str]:
        devices = config.get("device_bindings") if isinstance(config, Mapping) else None
        return [
            f"设备绑定不可用：{problem}"
            for problem in power_supply_binding_problems(devices)
        ]

    def _refresh_binding_status(self) -> None:
        supplies = list(power_supply_bindings(self.device_bindings))
        if supplies:
            text = "已绑定电源：" + "、".join(supplies)
        else:
            text = "尚未绑定电源：请先在“仪器配置”页查找并应用设备。"
        if self.binding_problems:
            text += "；" + "；".join(self.binding_problems)
        self.binding_status_label.setText(text)

    # ---- 配置输入（只取本页负责的字段） ------------------------------------

    def _assignment_entries(self) -> PowerAssignmentEntries:
        existing = self.config.get("power_supply_assignment", {})
        if not isinstance(existing, dict):
            existing = {}
        driver = existing.get("driver_amplifier", {})
        dut = existing.get("dut_amplifier", {})
        if not isinstance(driver, dict):
            driver = {}
        if not isinstance(dut, dict):
            dut = {}
        driver_supply = driver.get("supplies", {})
        dut_supply = dut.get("supplies", {})
        if not isinstance(driver_supply, dict):
            driver_supply = {}
        if not isinstance(dut_supply, dict):
            dut_supply = {}
        return {
            "driver_power": driver_supply.get("main"),
            "pa_unit1_power": dut_supply.get("carrier"),
            "pa_unit2_power": dut_supply.get("peaking"),
            "pa_unit3_power": dut_supply.get("peaking2"),
        }

    def _power_supply_sources(self, supplies: Any = None) -> PowerSupplySources:
        """电源标签页条目：配置里已有的键 + 本次已绑定的 ``supply_id``。"""
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

    def _power_supply_choices(self) -> PowerSupplyChoices:
        return bound_power_supply_entries(self.device_bindings)

    def _power_plan_entry(self, group: str) -> Any:
        assignment = self.config.get("power_supply_assignment")
        block = assignment.get(group) if isinstance(assignment, dict) else None
        return block.get("power_plan") if isinstance(block, dict) else None

    def _drain_channel_entry(self) -> Any:
        dut = self.config.get("dut_config")
        return dut.get("drain_channel") if isinstance(dut, dict) else None

    def _driver_plan_active(self) -> bool:
        """驱放计划是否生效：只有本机直流供电的驱放测试模式才有上电顺序。"""
        source = self.driver_power_source
        if self.power_section is not None:
            source = self.power_section.driver_power_source_combo.currentText()
        return bool(self.test_mode_enabled and source == "直流电源")

    # ---- 电源表单组件委托 ---------------------------------------------------

    def _rebuild_power_tabs(self, *, sources: Any = None, preserve: bool = True) -> None:
        if self.power_section is None:
            return
        self.power_section.rebuild(
            sources=self._power_supply_sources() if sources is None else sources,
            preserved_channels=(
                self.form_adapter.read_power_channel_snapshot() if preserve else {}
            ),
            preserve=preserve,
        )
        # 重建会把通道控件换成新对象：旧连接随控件一起失效，必须按新控件重连。
        self._wire_fragment_signals()

    def _current_power_tab_key(self) -> str | None:
        if self.power_section is None:
            return None
        return current_power_tab_key(self.power_section)

    def update_power_supply_options(self) -> None:
        """刷新分配下拉框：只保留当前绑定，失效引用保持空选择。"""
        self.form_adapter.refresh_power_choices(self._power_supply_choices())
        if self.power_section is not None:
            self.power_section.refresh_plan_routes(preserve=True)
        self._emit_fragment_changed()

    def get_enabled_power_supplies(self) -> list[str]:
        return list(self._power_supply_choices())

    def read_drain_channel(self) -> dict[str, str] | None:
        """当前漏极选择的物理路由；未选择时返回 ``None``。"""
        if self.power_section is None:
            return None
        return self.power_section.read_drain_channel()

    def reset_power_plans(self) -> None:
        if self.power_section is not None:
            self.power_section.reset_power_plans()

    def apply_power_plan_config(
        self, config: Any, *, driver_plan_active: bool | None = None
    ) -> None:
        if self.power_section is not None:
            self.power_section.apply_power_plan_config(
                config, driver_plan_active=driver_plan_active
            )

    # ---- 读写 ---------------------------------------------------------------

    def build_fragment(self, base: Any = None) -> dict[str, Any]:
        """返回本页负责的配置片段，供统一会话合并进共享草稿。"""
        return self.form_adapter.read_power_fragment(
            self.config if base is None else base
        )

    def build_config(self, base: Any = None) -> dict[str, Any]:
        """把电源片段合并进 ``base``（缺省为页面自己的配置副本）。"""
        return self.form_adapter.build_power_configuration(
            self.config if base is None else base
        )

    def validate(self) -> bool:
        """校验电源片段；错误经 ``validation_failed`` 交给统一状态行。"""
        errors = list(self.binding_problems)
        errors.extend(self.form_adapter.power_validation_errors(self.config))
        return self.form_adapter.report_errors(errors)

    def apply_config(self, config: Any) -> None:
        """把同一份编辑草稿里的电源字段确定性地回填到控件。"""
        if not isinstance(config, dict):
            raise ConfigBackfillError(
                "冻结配置必须是 JSON 对象（dict），无法确定性地回填电源配置页"
            )
        previous_bindings = self.device_bindings
        previous_problems = self.binding_problems
        previous_source = self.driver_power_source
        previous_test_mode = self.test_mode_enabled
        previous_count = self.pa_unit_count
        self._silent = True
        try:
            bindings = effective_device_bindings(config)
            mode = config.get("driver_mode")
            mode = mode if isinstance(mode, dict) else {}
            assignment = config.get("power_supply_assignment")
            assignment = assignment if isinstance(assignment, dict) else {}
            driver = assignment.get("driver_amplifier")
            driver = driver if isinstance(driver, dict) else {}
            dut = config.get("dut_config")
            dut = dut if isinstance(dut, dict) else {}
            self.device_bindings = bindings
            self.test_mode_enabled = bool(mode.get("enabled", True))
            self.driver_power_source = driver_power_source_label(
                mode.get("power_source"), driver.get("power_supply_count")
            )
            self.pa_unit_count = max(
                0, self._int_or(dut.get("power_supply_count"), int(DEFAULT_PA_UNIT_COUNT))
            )
            driver_plan_active = (
                bool(mode.get("enabled")) and self.driver_power_source == "直流电源"
            )
            instruments = config.get("instruments")
            supplies = (
                instruments.get("power_supplies") if isinstance(instruments, dict) else {}
            )
            if not isinstance(supplies, dict):
                supplies = {}
            try:
                self._rebuild_power_tabs(
                    sources=self._power_supply_sources(supplies),
                    preserve=False,
                )
                projected = self.form_adapter.apply_power_configuration(
                    config,
                    supplies=self._power_supply_choices(),
                    driver_plan_active=driver_plan_active,
                )
            except ConfigBackfillError:
                self.device_bindings = previous_bindings
                self.binding_problems = previous_problems
                self.driver_power_source = previous_source
                self.test_mode_enabled = previous_test_mode
                self.pa_unit_count = previous_count
                self._rebuild_power_tabs(
                    sources=self._power_supply_sources(), preserve=False
                )
                raise
            self.config = projected
            self.binding_problems = self._binding_problems_of(config)
            self.update_pa_unit_ui()
            self.update_driver_power_ui()
            self.update_power_supply_options()
            self._refresh_binding_status()
            self.mark_clean()
        finally:
            self._silent = False

    # ---- dirty --------------------------------------------------------------

    def set_finalizing(self, active: bool) -> None:
        """最终动作进行中禁用电源表单，防止提交后继续改片段。"""
        active = bool(active)
        if active == self._finalizing:
            return
        self._finalizing = active
        if self.power_section is not None:
            self.power_section.setEnabled(not active)

    def mark_clean(self) -> None:
        self._dirty_baseline = copy.deepcopy(self.build_config(self.config))

    def is_dirty(self) -> bool:
        return self.build_config(self.config) != self._dirty_baseline

    # ---- 小工具 -------------------------------------------------------------

    @staticmethod
    def _int_or(value: Any, default: int) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    # ---- 控件只读入口（表单组件持有，页面不重复保存） ----------------------

    @property
    def power_tabs(self):
        return self.power_section.power_tabs

    @property
    def power_config_widgets(self):
        return self.power_section.power_config_widgets

    @property
    def power_assignment_group(self):
        return self.power_section.power_assignment_group

    @property
    def power_assignment_widgets(self):
        return self.power_section.power_assignment_widgets

    @property
    def driver_power_source_combo(self):
        return self.power_section.driver_power_source_combo

    @property
    def driver_power_source_label(self):
        return self.power_section.driver_power_source_label

    @property
    def driver_power_hint(self):
        return self.power_section.driver_power_hint

    @property
    def driver_power_combo(self):
        return self.power_section.driver_power_combo

    @property
    def driver_power_label(self):
        return self.power_section.driver_power_label

    @property
    def pa_unit1_power_combo(self):
        return self.power_section.pa_unit1_power_combo

    @property
    def pa_unit2_power_combo(self):
        return self.power_section.pa_unit2_power_combo

    @property
    def pa_unit3_power_combo(self):
        return self.power_section.pa_unit3_power_combo

    @property
    def pa_unit2_label(self):
        return self.power_section.pa_unit2_label

    @property
    def pa_unit3_label(self):
        return self.power_section.pa_unit3_label

    @property
    def power_plan_group(self):
        return self.power_section.power_plan_group

    @property
    def driver_power_plan_box(self):
        return self.power_section.driver_power_plan_box

    @property
    def dut_power_plan_editor(self):
        return self.power_section.dut_power_plan_editor

    @property
    def driver_power_plan_editor(self):
        return self.power_section.driver_power_plan_editor

    @property
    def drain_channel_group(self):
        return self.power_section.drain_channel_group

    @property
    def drain_channel_combo(self):
        return self.power_section.drain_channel_combo
