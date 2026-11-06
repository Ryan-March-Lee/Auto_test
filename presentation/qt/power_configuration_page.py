"""独立电源配置页（``PowerConfigurationPage``）。

页面以通道卡片为主视图：每台电源一张带蓝黄通道的卡片，按仪器排布页九宫格的
相对位置摆在内容列顶部（:mod:`presentation.qt.power_card_wall`），通道参数、上电
计划与漏极表单整体接在卡片下方。进入本页时，排布页的电源卡片从屏幕原位展开成完整
卡片（:mod:`presentation.qt.power_card_morph`），卡片墙与过渡层共用同一份卡片位置。
设备绑定信息不在本页显示：绑定只在上游“仪器配置”页确认，本页只消费它生成的电源
集合。每个通道只保留“是否启用保护”的勾选，保护阈值由工作值按 110% 自动写入配置；
卡片上的电压/电流保护勾选框可点击，点击写回下方表单的同一开关，不直接改配置文档。

电源参数、通道上电计划与效率测量漏极都归本页；测试参数、运行入口与
设备发现仍属于 :mod:`presentation.qt.configuration_page` 与
:mod:`presentation.qt.instrument_setup_page`。页面只把控件组合成显式的电源片段，
不写文件、不连接仪器、不导入基础设施或硬件模块；统一保存、版本门禁与运行冻结由
协调器/配置会话在后续接线中完成。

页面与完整配置页共享同一份编辑草稿：本页的 :meth:`build_fragment` 只返回
``instruments.power_supplies``、``power_supply_assignment`` 与
``dut_config.drain_channel`` 这些电源字段，调用方把它们合并进草稿即可，不会覆盖
另一页尚未保存的测试参数。:meth:`apply_config` 回填时也只触碰这些字段，绑定变化
重建控件时保留操作者尚未提交的通道参数、上电顺序、间隔与漏极。

窗口用 :class:`presentation.qt.configuration_editor.ConfigurationEditor` 把本页与
参数页装配成共享编辑状态，整份草稿的合并/校验/保存仍由
:class:`presentation.qt.application_coordinator.ApplicationCoordinator` 完成；页面
不持有另一页的实例，也不写文件。

漏极候选只来自有效 DUT 物理通道（已有 DUT 计划时限定在计划内，计划尚未生成时退回
全部候选通道），页面上明确写出“漏极仅用于效率测量，不影响上电顺序”。漏极不是上电
通道角色，也不参与排序、间隔或安全关断。
"""

from __future__ import annotations

import copy
from typing import Any, Mapping

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .config_form_adapter import ConfigurationFormAdapter
from .config_form_bindings import (
    bound_power_supply_entries,
    effective_device_bindings,
    power_supply_bindings,
)
from .config_power_section import (
    PowerFormSection,
    PowerSupplyChoices,
    PowerSupplySources,
    current_power_tab_key,
)
from .config_projection import ConfigBackfillError
from .pages import PageControllerBindingMixin
from .power_card import CHANNEL_NAMES, PROTECTION_KINDS
from .power_card_morph import PowerCardMorphItem, PowerCardMorphOverlay
from .power_card_wall import PowerCardWall, power_card_data
from .theme import PAGE_CONTENT_MAX_WIDTH

__all__ = ["PowerConfigurationPage"]


class PowerConfigurationPage(PageControllerBindingMixin, QWidget):
    """独立电源配置页：通道参数、上电计划与漏极。

    页面通过 :meth:`build_fragment` 提交自己负责的片段，通过 :meth:`apply_config`
    接收同一份编辑草稿中的电源字段；两种方向都不读写其它页面的控件。设备绑定只作为
    电源集合的来源，由 :meth:`apply_device_bindings` 刷新，页面不再显示绑定状态。
    驱动模式变化经 :meth:`set_test_mode` 转发给表单派生状态，并发出
    ``driver_mode_changed`` 供窗口刷新相关页面。驱放自带供电由“驱动功放上电计划为空”
    表达，页面没有单独的供电方式控件。
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
        mode = self.config.get("driver_mode")
        mode = mode if isinstance(mode, dict) else {}
        self.test_mode_enabled = bool(mode.get("enabled", True))
        self.power_section: PowerFormSection | None = None
        # 1.0 编辑文档只读：整段电源表单禁用，但页面本身与回退入口保持可用。
        self.read_only_reason: str | None = None
        # 回填/重建期间控件会连续发出 valueChanged；那是程序写入，不是用户编辑。
        self._silent = False
        self._finalizing = False
        self._fragment_signal_connections: list[tuple[Any, Any]] = []
        #: 仪器排布页的九宫格结果（``supply_id -> 格位下标``），卡片视图按它排相对位置。
        self._rack_slots: dict[str, int] = {}
        #: 卡片 → 排布页的过渡覆盖层；不在过渡中时为 ``None``。
        self._card_morph: PowerCardMorphOverlay | None = None
        self.card_wall: PowerCardWall | None = None
        self.build_ui()
        self._connect_signals()
        self.update_driver_power_ui()
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
        self._scroll = scroll
        content = QWidget()
        content.setMaximumWidth(PAGE_CONTENT_MAX_WIDTH)
        column = QVBoxLayout(content)
        # 顶部留一点呼吸：卡片不贴住标签栏。
        column.setContentsMargins(0, 8, 4, 0)
        column.setSpacing(12)
        self.read_only_notice = QLabel()
        self.read_only_notice.setObjectName("powerReadOnlyNotice")
        self.read_only_notice.setProperty("tone", "warning")
        self.read_only_notice.setWordWrap(True)
        self.read_only_notice.setVisible(False)
        column.addWidget(self.read_only_notice)
        # 卡片视图是页面的主角：放在内容列最上方、水平居中，参数表单接在下面。
        self.card_wall = PowerCardWall(content)
        column.addWidget(self.card_wall)
        self._build_power_widgets(column)
        column.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        self._refresh_card_wall()
        return self

    def _build_power_widgets(self, layout: Any) -> None:
        self.power_section = PowerFormSection(
            sources=self._power_supply_sources(),
            choices=self._power_supply_choices(),
            driver_plan_active=self._driver_plan_active(),
            dut_power_plan=self._power_plan_entry("dut_amplifier"),
            driver_power_plan=self._power_plan_entry("driver_amplifier"),
            drain_channel=self._drain_channel_entry(),
        )
        layout.addWidget(self.power_section)

    def _connect_signals(self) -> None:
        self._wire_fragment_signals()
        if self.card_wall is not None:
            self.card_wall.protection_toggled.connect(
                self._on_card_protection_toggled
            )

    def _wire_fragment_signals(self) -> None:
        """把任一电源字段的编辑都汇总成 ``fragment_changed``。

        通道参数（电压/电流及保护开关）在绑定变化重建标签页时会换成新控件，因此这里
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

        for supply_id, supply in getattr(
            section, "power_config_widgets", {}
        ).items():
            for channel, controls in supply.get("channels", {}).items():
                for key in ("voltage", "current"):
                    widget = controls.get(key)
                    if widget is not None and hasattr(widget, "valueChanged"):
                        hook(widget.valueChanged, notify)
                for key, kind in (
                    ("voltage_protection_enabled", "voltage"),
                    ("current_protection_enabled", "current"),
                ):
                    widget = controls.get(key)
                    if widget is not None and hasattr(widget, "toggled"):
                        hook(
                            widget.toggled,
                            self._protection_notifier(
                                str(supply_id), str(channel), kind
                            ),
                        )
        for editor in (section.dut_power_plan_editor, section.driver_power_plan_editor):
            hook(editor.list.itemChanged, notify)
            hook(editor.delay_spin.valueChanged, notify)
        hook(section.drain_channel_combo.currentIndexChanged, notify)

    def _emit_fragment_changed(self) -> None:
        """只在用户编辑时发出变更通知；回填与重建不算编辑。"""
        if not self._silent:
            self.fragment_changed.emit()

    def _protection_notifier(self, supply_id: str, channel: str, kind: str):
        """表单保护开关的槽：同步卡面勾选框，再把这次编辑计入 dirty。"""
        def notify(checked: bool) -> None:
            if self.card_wall is not None:
                self.card_wall.set_protection(
                    supply_id, channel, kind, bool(checked)
                )
            self._emit_fragment_changed()

        return notify

    # ---- 表单 Adapter -------------------------------------------------------

    @property
    def form_adapter(self) -> ConfigurationFormAdapter:
        """控件读写的唯一入口；Adapter 无状态，按需创建避免引用环。"""
        return ConfigurationFormAdapter(self, on_error=self.validation_failed.emit)

    # ---- 页面级派生状态 -----------------------------------------------------

    def update_driver_power_ui(self) -> None:
        """按驱放测试模式刷新驱动计划的可见性与生效状态。"""
        if self.power_section is not None:
            self.power_section.driver_power_plan_box.setVisible(self.test_mode_enabled)
            self.power_section.set_driver_plan_active(self.test_mode_enabled)

    # ---- 卡片视图 -----------------------------------------------------------

    def set_rack_slots(self, slots: Any) -> None:
        """接收仪器排布页的九宫格结果，卡片按同一相对位置摆放。"""
        cleaned: dict[str, int] = {}
        if isinstance(slots, Mapping):
            for supply_id, index in slots.items():
                try:
                    value = int(index)
                except (TypeError, ValueError):
                    continue
                if 0 <= value < 9:
                    cleaned[str(supply_id)] = value
        self._rack_slots = cleaned
        self._refresh_card_wall()

    def _card_cards(self) -> list[tuple[str, Any]]:
        """当前配置里每台电源的卡片数据；顺序与表单标签页一致。

        保护勾选框以表单当前状态为准：绑定变化或回填会重建表单，重建前操作者
        尚未保存的保护开关必须继续显示在卡面上。工作值仍按配置快照显示。
        """
        sources = self._power_supply_sources()
        snapshot: Mapping[str, Any] = {}
        if self.power_section is not None:
            snapshot = self.form_adapter.read_power_channel_snapshot()
        cards: list[tuple[str, Any]] = []
        for supply_id, entry in sources.items():
            data = power_card_data(entry, supply_id)
            live = snapshot.get(supply_id)
            channels = live.get("channels") if isinstance(live, Mapping) else None
            if isinstance(channels, Mapping):
                for channel, block in channels.items():
                    if channel not in CHANNEL_NAMES or not isinstance(block, Mapping):
                        continue
                    for kind in PROTECTION_KINDS:
                        values = block.get(kind)
                        if not isinstance(values, Mapping):
                            continue
                        data = data.with_channel_protection(
                            channel,
                            kind,
                            values.get("protection_enabled") is True,
                        )
            cards.append((supply_id, data))
        return cards

    def _refresh_card_wall(self) -> None:
        """按当前配置与排布结果重画卡片墙；没有电源时整块高度收为 0。"""
        wall = self.card_wall
        if wall is None:
            return
        wall.set_cards(self._card_cards(), self._rack_slots)

    def _on_card_protection_toggled(
        self,
        supply_id: str,
        channel: str,
        kind: str,
        enabled: bool,
    ) -> None:
        """卡面勾选框被点击：写回下方表单的同一个开关。

        配置写入仍走表单读数，卡面不直接改配置文档；表单的 ``toggled`` 信号会同步
        卡面并计入 dirty。只读、收尾或卡片过渡期间不接收点击。
        """
        if (
            self._card_morph is not None
            or bool(self.read_only_reason)
            or self._finalizing
        ):
            return
        section = self.power_section
        if section is None:
            return
        supply = getattr(section, "power_config_widgets", {}).get(supply_id)
        controls = supply.get("channels", {}).get(channel) if isinstance(supply, Mapping) else None
        widget = (
            controls.get(f"{kind}_protection_enabled")
            if isinstance(controls, Mapping)
            else None
        )
        if widget is None:
            return
        widget.setChecked(bool(enabled))
        # 状态没变时 setChecked 不发信号，这里仍同步一次卡面，避免卡面停在旧值。
        if self.card_wall is not None:
            self.card_wall.set_protection(
                supply_id, channel, kind, bool(enabled)
            )

    def play_card_morph(self, start_rects: Any) -> bool:
        """从排布页卡片的屏幕位置展开成卡片；返回是否真的播放了动画。

        ``start_rects`` 是 ``supply_id -> 全局 QRect``。页面必须已经是当前页并完成
        布局；拿不到卡片或起点时直接显示静止卡片墙（返回 ``False``）。
        """
        wall = self.card_wall
        if wall is None or wall.is_empty() or not isinstance(start_rects, Mapping):
            return False
        starts = {
            str(supply_id): rect
            for supply_id, rect in start_rects.items()
            if isinstance(rect, QRect) and not rect.isEmpty()
        }
        if not starts:
            return False
        # 卡片墙在内容最上方：先把滚动条收回顶部，动画终点才和屏幕上的卡片对齐。
        self._scroll.verticalScrollBar().setValue(0)
        self._scroll.horizontalScrollBar().setValue(0)
        self._settle_card_layout()
        # 排布页的卡片写的是设备发现卡片上的缩写码/完整码；电源配置的键是
        # supply_id。两边在旧设备表里可能只差一个 full_code，这里按身份别名对上，
        # 不让一张已经摆好的卡片因为编码来源不同而丢掉动画。
        aliases: dict[str, set[str]] = {}
        for supply_id, entry in self._power_supply_sources().items():
            names = {str(supply_id)}
            for field in ("label", "full_code"):
                value = entry.get(field) if isinstance(entry, Mapping) else None
                if isinstance(value, str) and value.strip():
                    names.add(value.strip())
            aliases[str(supply_id)] = names
        items: list[PowerCardMorphItem] = []
        for cell in wall.cards():
            start = starts.get(cell.supply_id)
            if start is None:
                names = aliases.get(cell.supply_id, {cell.supply_id})
                start = next(
                    (starts[name] for name in names if name in starts), None
                )
            target = wall.card_rect(cell.supply_id)
            if start is None or target is None or target.isEmpty():
                continue
            top_left = self.mapFromGlobal(start.topLeft())
            mapped = wall.mapTo(self, QPoint(round(target.x()), round(target.y())))
            items.append(
                PowerCardMorphItem(
                    supply_id=cell.supply_id,
                    code=cell.data.code or cell.supply_id,
                    start=QRectF(
                        float(top_left.x()),
                        float(top_left.y()),
                        float(start.width()),
                        float(start.height()),
                    ),
                    target=QRectF(
                        float(mapped.x()),
                        float(mapped.y()),
                        target.width(),
                        target.height(),
                    ),
                    pixmap=wall.card_pixmap(cell.supply_id),
                )
            )
        if not items:
            return False
        self.finish_card_morph()
        wall.set_paint_enabled(False)
        # 过渡覆盖层鼠标穿透，底层卡片墙若仍可点就会在动画中改配置。
        wall.setEnabled(False)
        overlay = PowerCardMorphOverlay(self, items)
        overlay.setGeometry(self.rect())
        overlay.finished.connect(lambda: self._finish_card_morph_overlay(overlay))
        self._card_morph = overlay
        overlay.start()
        return True

    def card_morph_running(self) -> bool:
        return self._card_morph is not None

    def finish_card_morph(self) -> None:
        """立即结束过渡；没有过渡时什么也不做。"""
        overlay = self._card_morph
        if overlay is not None:
            overlay.finish_now()

    def _finish_card_morph_overlay(self, overlay: PowerCardMorphOverlay) -> None:
        if self._card_morph is not overlay:
            return
        self._card_morph = None
        if self.card_wall is not None:
            self.card_wall.set_paint_enabled(True)
            self.card_wall.setEnabled(
                not self._finalizing and not bool(self.read_only_reason)
            )
        overlay.deleteLater()

    def _settle_card_layout(self) -> None:
        """强制跑一次内容列布局，确保卡片墙已有真实宽度与位置。"""
        wall = self.card_wall
        if wall is None:
            return
        layout = self.layout()
        if layout is not None:
            layout.activate()
        parent = wall.parentWidget()
        if parent is not None and parent.layout() is not None:
            parent.layout().activate()
        wall.refresh_layout()

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        if self._card_morph is not None:
            self._card_morph.setGeometry(self.rect())

    def set_test_mode(self, enabled: bool) -> None:
        """设置欢迎页选择的驱放测试模式，并刷新依赖它的电源派生状态。

        只改派生状态与可见性；干净基线由共享会话在加载/保存成功时统一重置。这里
        直接 ``mark_clean()`` 会把操作者尚未保存的电源修改悄悄变成“已保存”。
        """
        enabled = bool(enabled)
        self.test_mode_enabled = enabled
        self._silent = True
        try:
            self.update_driver_power_ui()
        finally:
            self._silent = False
        self.driver_mode_changed.emit(enabled)

    def apply_device_bindings(self, bindings: Any) -> None:
        """用新的设备绑定刷新电源选项与标签页，保留尚未提交的编辑。

        绑定写入仍由仪器设置页经配置会话完成；本页只接收结果，不自己扫描设备、
        不挑默认电源，也不改写配置引用。
        """
        self.device_bindings = bindings if isinstance(bindings, Mapping) else {}
        self._silent = True
        try:
            self._rebuild_power_tabs(preserve=True)
        finally:
            self._silent = False
        self._refresh_card_wall()
        self._emit_fragment_changed()

    # ---- 配置输入（只取本页负责的字段） ------------------------------------

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
        """驱放计划是否生效：驱放测试模式开启时才有上电顺序。"""
        return bool(self.test_mode_enabled)

    # ---- 电源表单组件委托 ---------------------------------------------------

    def _rebuild_power_tabs(self, *, sources: Any = None, preserve: bool = True) -> None:
        if self.power_section is None:
            return
        current_sources = self._power_supply_sources() if sources is None else sources
        self.power_section.rebuild(
            sources=current_sources,
            preserved_channels=(
                self.form_adapter.read_power_channel_snapshot() if preserve else {}
            ),
            preserve=preserve,
        )
        self.power_section.set_power_choices(
            current_sources, self._power_supply_choices()
        )
        # 重建会把通道控件换成新对象：旧连接随控件一起失效，必须按新控件重连。
        self._wire_fragment_signals()
        self._refresh_card_wall()

    def _current_power_tab_key(self) -> str | None:
        if self.power_section is None:
            return None
        return current_power_tab_key(self.power_section)

    def update_power_supply_options(self) -> None:
        """刷新计划候选：只保留当前绑定，失效路由留在计划里交给校验。"""
        if self.power_section is not None:
            self.power_section.set_power_choices(
                self._power_supply_sources(), self._power_supply_choices()
            )
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
        """校验电源片段；错误经 ``validation_failed`` 交给统一状态行。

        绑定记录在“仪器配置”页应用设备时已经由 application.device_binding 判定；本页
        不再重复展示绑定问题，只校验通道参数、上电计划与漏极。
        """
        errors = list(self.form_adapter.power_validation_errors(self.config))
        return self.form_adapter.report_errors(errors)

    def apply_config(self, config: Any) -> None:
        """把同一份编辑草稿里的电源字段确定性地回填到控件。"""
        if not isinstance(config, dict):
            raise ConfigBackfillError(
                "冻结配置必须是 JSON 对象（dict），无法确定性地回填电源配置页"
            )
        previous_bindings = self.device_bindings
        previous_test_mode = self.test_mode_enabled
        self._silent = True
        try:
            bindings = effective_device_bindings(config)
            mode = config.get("driver_mode")
            mode = mode if isinstance(mode, dict) else {}
            self.device_bindings = bindings
            self.test_mode_enabled = bool(mode.get("enabled", True))
            driver_plan_active = self.test_mode_enabled
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
                    driver_plan_active=driver_plan_active,
                )
            except ConfigBackfillError:
                self.device_bindings = previous_bindings
                self.test_mode_enabled = previous_test_mode
                self._rebuild_power_tabs(
                    sources=self._power_supply_sources(), preserve=False
                )
                raise
            self.config = projected
            self.update_driver_power_ui()
            self.update_power_supply_options()
            self._refresh_card_wall()
            self.mark_clean()
        finally:
            self._silent = False

    # ---- dirty --------------------------------------------------------------

    def set_read_only(self, reason: str | None) -> None:
        """按统一会话给出的版本结论切换只读态。

        电源片段（通道参数、分配、计划与漏极）整体禁用；只读提示仍显示，操作者能看到
        当前文档为什么不能保存。原因只由 authoring 的版本门禁给出，页面不识别
        ``schema_version``，也不自行尝试迁移。
        """
        reason = (reason or "").strip() or None
        self.read_only_reason = reason
        self.read_only_notice.setText(
            f"当前配置只读：{reason}" if reason else ""
        )
        self.read_only_notice.setVisible(bool(reason))
        self._apply_read_only_controls()

    def _apply_read_only_controls(self) -> None:
        if self.power_section is not None:
            self.power_section.setEnabled(
                not self._finalizing and not bool(self.read_only_reason)
            )
        if self.card_wall is not None:
            self.card_wall.setEnabled(
                not self._finalizing
                and not bool(self.read_only_reason)
                and self._card_morph is None
            )

    def set_finalizing(self, active: bool) -> None:
        """最终动作进行中禁用电源表单，防止提交后继续改片段。"""
        active = bool(active)
        if active == self._finalizing:
            return
        self._finalizing = active
        self._apply_read_only_controls()

    def mark_clean(self) -> None:
        self._dirty_baseline = copy.deepcopy(self.build_config(self.config))

    def is_dirty(self) -> bool:
        return self.build_config(self.config) != self._dirty_baseline

    # ---- 控件只读入口（表单组件持有，页面不重复保存） ----------------------

    @property
    def power_tabs(self):
        return self.power_section.power_tabs

    @property
    def power_config_widgets(self):
        return self.power_section.power_config_widgets

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
