"""独立电源配置页（``PowerConfigurationPage``），是配置流程里的整屏步骤，不是标签页。

页面以通道卡片为主视图：顶部是居中加粗的大标题“电源配置”（与“仪器排布”同一套
``pageTitle``），中间是卡片墙——每台电源一张带蓝黄通道的卡片，按仪器排布页九宫格的
相对位置摆放（:mod:`presentation.qt.power_card_wall`），上电计划与漏极表单接在卡片
下方。进入本页时，排布页的电源卡片从屏幕原位展开成完整卡片
（:mod:`presentation.qt.power_card_morph`），卡片落位后标题、表单与底部导航再淡入；
卡片墙与过渡层共用同一份卡片位置。设备绑定信息不在本页显示：绑定只在上游
“仪器配置”页确认，本页只消费它生成的电源集合。

底部“上一步”回仪器排布页（格位与卡片位置原样保留），“下一步”进入工作区的
“仪器配置”标签页；页面只发信号，切页由主窗口完成。

卡片是通道参数的唯一编辑面：每个通道上角的竖直滚筒写 ``OFF`` / ``Driver`` / ``DUT``，
表示该通道关闭、给驱放供电、给功放供电。换档会同步勾选/取消下方
``driver_amplifier`` / ``dut_amplifier`` 的 ``power_plan`` 路由，因此档位不是临时
显示状态，而是由现有计划结构持久化；进入页面时按已保存计划回显档位。``OFF`` 时该
通道的 VS/IS 输入框与保护勾选不可操作。工作值与保护勾选直接进入配置片段，保护阈值
仍由工作值按 110% 自动推导。两个通道始终保留在配置里，卡片下方的「通道上电计划」与
「效率测量」不属于通道参数。

电源参数、通道上电计划与效率测量漏极都归本页；测试参数、运行入口与
设备发现仍属于 :mod:`presentation.qt.configuration_page` 与
:mod:`presentation.qt.instrument_setup_page`。页面只把控件组合成显式的电源片段，
不写文件、不连接仪器、不导入基础设施或硬件模块；统一保存、版本门禁与运行冻结由
协调器/配置会话在后续接线中完成。

页面与完整配置页共享同一份编辑草稿：本页的 :meth:`build_fragment` 只返回
``instruments.power_supplies``、``power_supply_assignment`` 与
``dut_config.drain_channel`` 这些电源字段，调用方把它们合并进草稿即可，不会覆盖
另一页尚未保存的测试参数。:meth:`apply_config` 回填时也只触碰这些字段，绑定变化
重建卡片时保留操作者尚未提交的工作值、保护勾选、上电顺序、间隔与漏极。

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

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QRect,
    QRectF,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
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
)
from .config_projection import ConfigBackfillError
from .pages import PageControllerBindingMixin
from .power_card import CHANNEL_NAMES
from .power_card_morph import PowerCardMorphItem, PowerCardMorphOverlay
from .power_card_wall import PowerCardWall, power_card_data
from .theme import PAGE_CONTENT_MAX_WIDTH, set_button_variant

__all__ = ["PowerConfigurationPage"]

#: 卡片落位后标题、表单与底部导航的淡入时长；比卡片展开短，收尾不拖沓。
_INTRO_FADE_MS = 180


class PowerConfigurationPage(PageControllerBindingMixin, QWidget):
    """独立电源配置页：通道参数、上电计划与漏极。

    页面通过 :meth:`build_fragment` 提交自己负责的片段，通过 :meth:`apply_config`
    接收同一份编辑草稿中的电源字段；两种方向都不读写其它页面的控件。设备绑定只作为
    电源集合的来源，由 :meth:`apply_device_bindings` 刷新，页面不再显示绑定状态。
    驱动模式变化经 :meth:`set_test_mode` 转发给表单派生状态，并发出
    ``driver_mode_changed`` 供窗口刷新相关页面。驱放自带供电由“驱动功放上电计划为空”
    表达，页面没有单独的供电方式控件。卡片滚筒与两份计划双向同步：换档写计划，
    计划勾选变化回写滚筒；同一物理通道在一组里勾选时会自动取消另一组。
    """

    validation_failed = Signal(str)
    driver_mode_changed = Signal(bool)
    #: 任一电源字段变化时发出；统一会话用它汇总 dirty（步骤 13 接线）。
    fragment_changed = Signal()
    #: 底部“上一步”：回仪器排布页；切页由主窗口完成。
    back_requested = Signal()
    #: 底部“下一步”：进入工作区的“仪器配置”标签页；切页由主窗口完成。
    next_requested = Signal()

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
        # 卡片角色与下方上电计划互相同步时置真，避免 itemChanged 递归。
        self._syncing_modes = False
        self._finalizing = False
        self._fragment_signal_connections: list[tuple[Any, Any]] = []
        #: 仪器排布页的九宫格结果（``supply_id -> 格位下标``），卡片视图按它排相对位置。
        self._rack_slots: dict[str, int] = {}
        #: 卡片 → 排布页的过渡覆盖层；不在过渡中时为 ``None``。
        self._card_morph: PowerCardMorphOverlay | None = None
        #: 过渡结束时直接显示标题/表单/导航（测试与“跳过动画”路径用）。
        self._skip_intro_fade = False
        self._intro_opacity = 1.0
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
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        content = QWidget()
        content.setMaximumWidth(PAGE_CONTENT_MAX_WIDTH)
        column = QVBoxLayout(content)
        column.setContentsMargins(24, 12, 24, 16)
        column.setSpacing(10)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        row.addWidget(content, 100)
        row.addStretch(1)
        root.addLayout(row, 1)

        # 顶部标题：和“仪器排布”同一套大字号居中标题；占着布局位置，只做淡入淡出，
        # 过渡期间版面不会因为标题出现而跳动。
        self._header_row = QWidget()
        header = QHBoxLayout(self._header_row)
        header.setContentsMargins(0, 0, 0, 0)
        self.title_label = QLabel("电源配置")
        self.title_label.setProperty("role", "pageTitle")
        header.addStretch(1)
        header.addWidget(self.title_label)
        header.addStretch(1)
        self._header_effect = QGraphicsOpacityEffect(self._header_row)
        self._header_effect.setOpacity(1.0)
        self._header_row.setGraphicsEffect(self._header_effect)
        column.addWidget(self._header_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self._scroll = scroll
        body = QWidget()
        inner = QVBoxLayout(body)
        # 顶部不留额外呼吸：标题行已经在卡片上方。
        inner.setContentsMargins(0, 0, 4, 0)
        inner.setSpacing(12)
        self.read_only_notice = QLabel()
        self.read_only_notice.setObjectName("powerReadOnlyNotice")
        self.read_only_notice.setProperty("tone", "warning")
        self.read_only_notice.setWordWrap(True)
        self.read_only_notice.setVisible(False)
        inner.addWidget(self.read_only_notice)
        # 卡片视图是页面的主角：放在内容列最上方、水平居中，参数表单接在下面。
        self.card_wall = PowerCardWall(body)
        inner.addWidget(self.card_wall)
        self._build_power_widgets(inner)
        inner.addStretch(1)
        scroll.setWidget(body)
        column.addWidget(scroll, 1)

        # 底部导航与排布页同形：左侧次要“上一步”，右侧主操作“下一步”。
        self._footer_row = QWidget()
        footer = QHBoxLayout(self._footer_row)
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(10)
        self.back_btn = QPushButton("上一步")
        self.back_btn.setToolTip("回到仪器排布页，保留本次电源架位")
        set_button_variant(self.back_btn, "secondary")
        self.next_btn = QPushButton("下一步")
        self.next_btn.setToolTip("进入仪器配置，设置测试参数并连接仪器")
        set_button_variant(self.next_btn, "primary")
        footer.addWidget(self.back_btn)
        footer.addStretch(1)
        footer.addWidget(self.next_btn)
        self._footer_effect = QGraphicsOpacityEffect(self._footer_row)
        self._footer_effect.setOpacity(1.0)
        self._footer_row.setGraphicsEffect(self._footer_effect)
        column.addWidget(self._footer_row)

        self._intro_animation = QVariantAnimation(self)
        self._intro_animation.setDuration(_INTRO_FADE_MS)
        self._intro_animation.setEasingCurve(QEasingCurve.OutCubic)
        self._intro_animation.setStartValue(0.0)
        self._intro_animation.setEndValue(1.0)
        self._intro_animation.valueChanged.connect(self._set_intro_opacity)
        self._intro_animation.finished.connect(self._on_intro_finished)

        self.back_btn.clicked.connect(self.back_requested.emit)
        self.next_btn.clicked.connect(self.next_requested.emit)
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
        # 卡片落位后表单再淡入；效果只作用于表单，不挡卡片墙的绘制。
        self._section_effect = QGraphicsOpacityEffect(self.power_section)
        self._section_effect.setOpacity(1.0)
        self.power_section.setGraphicsEffect(self._section_effect)
        layout.addWidget(self.power_section)

    def _connect_signals(self) -> None:
        self._wire_fragment_signals()
        if self.card_wall is not None:
            self.card_wall.channel_mode_changed.connect(
                self._on_card_mode_changed
            )
            self.card_wall.protection_toggled.connect(
                self._on_card_protection_toggled
            )
            self.card_wall.value_changed.connect(self._on_card_value_changed)

    def _wire_fragment_signals(self) -> None:
        """把电源字段的编辑汇总成 ``fragment_changed``。

        卡片上的 VS/IS 与保护勾选由 :class:`PowerCardWall` 的专用信号直接汇总（见
        :meth:`_connect_signals`）；这里只接卡片下方的计划与漏极控件。计划列表不会
        因绑定重建而换对象，因此不需要像旧标签页那样每次重连。
        """
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

        for group, editor in (
            ("dut", section.dut_power_plan_editor),
            ("driver", section.driver_power_plan_editor),
        ):
            hook(
                editor.list.itemChanged,
                lambda item, name=group: self._on_plan_item_changed(name, item),
            )
            hook(editor.delay_spin.valueChanged, notify)
        hook(section.drain_channel_combo.currentIndexChanged, notify)

    def _emit_fragment_changed(self) -> None:
        """只在用户编辑时发出变更通知；回填与重建不算编辑。"""
        if not self._silent:
            self.fragment_changed.emit()

    def _on_card_protection_toggled(
        self,
        _supply_id: str,
        _channel: str,
        _kind: str,
        _enabled: bool,
    ) -> None:
        """卡面保护勾选变化：卡片已保存新状态，这里只计入 dirty。"""
        self._emit_fragment_changed()

    def _on_card_value_changed(
        self,
        _supply_id: str,
        _channel: str,
        _kind: str,
        _value: float,
    ) -> None:
        """卡面 VS/IS 变化：卡片已保存新值，这里只计入 dirty。"""
        self._emit_fragment_changed()

    # ---- 供电角色 <-> 上电计划 ----------------------------------------------

    @staticmethod
    def _plan_route_set(plan: Any) -> set[tuple[str, str]]:
        """把一份 ``power_plan`` 读成 ``(supply_id, channel)`` 集合。"""
        if not isinstance(plan, Mapping):
            return set()
        sequence = plan.get("power_sequence")
        if not isinstance(sequence, (list, tuple)):
            return set()
        routes: set[tuple[str, str]] = set()
        for item in sequence:
            if not isinstance(item, Mapping):
                continue
            supply_id = item.get("supply_id")
            channel = item.get("channel")
            if isinstance(supply_id, str) and isinstance(channel, str):
                supply_id, channel = supply_id.strip(), channel.strip()
                if supply_id and channel:
                    routes.add((supply_id, channel))
        return routes

    def _planned_modes(self) -> dict[tuple[str, str], str]:
        """按两份上电计划给出每个通道的档位；同一路由同时出现时 DUT 优先。"""
        section = self.power_section
        if section is not None:
            driver = (
                self._plan_route_set(section.driver_power_plan_editor.read_plan())
                if self._driver_plan_active()
                else set()
            )
            dut = self._plan_route_set(section.dut_power_plan_editor.read_plan())
        else:
            driver = (
                self._plan_route_set(self._power_plan_entry("driver_amplifier"))
                if self._driver_plan_active()
                else set()
            )
            dut = self._plan_route_set(self._power_plan_entry("dut_amplifier"))
        modes: dict[tuple[str, str], str] = {}
        for supply_id in self._power_supply_sources():
            for name in CHANNEL_NAMES:
                route = (supply_id, name)
                if route in dut:
                    modes[route] = "dut"
                elif route in driver:
                    modes[route] = "driver"
                else:
                    modes[route] = "off"
        return modes

    def _sync_cards_from_plans(self) -> None:
        """把两份计划的路由状态回写到卡片滚筒；不发信号。"""
        if self.card_wall is not None:
            self.card_wall.set_channel_modes(self._planned_modes())

    def _on_card_mode_changed(
        self, supply_id: str, channel: str, mode: str
    ) -> None:
        """卡面滚筒换档：同步勾选下方对应计划，再计入 dirty。"""
        if self._syncing_modes:
            return
        section = self.power_section
        if section is None:
            self._emit_fragment_changed()
            return
        route = (supply_id, channel)
        was_silent = self._silent
        self._silent = True
        self._syncing_modes = True
        try:
            driver = section.driver_power_plan_editor
            dut = section.dut_power_plan_editor
            if mode == "driver" and self._driver_plan_active():
                dut.set_route_checked(route, False)
                driver.set_route_checked(route, True)
            elif mode == "dut":
                driver.set_route_checked(route, False)
                dut.set_route_checked(route, True)
            else:
                driver.set_route_checked(route, False)
                dut.set_route_checked(route, False)
            # 计划里可能没有这条路由候选（例如电源刚解绑）；以计划实际状态为准
            # 回写卡片，避免滚筒停留在一个没有落库的档位。
            self._sync_cards_from_plans()
        finally:
            self._syncing_modes = False
            self._silent = was_silent
        self._emit_fragment_changed()

    def _on_plan_item_changed(self, group: str, item: Any) -> None:
        """计划编辑器勾选变化：互斥地清掉另一组，并同步卡片滚筒。"""
        if self._syncing_modes:
            return
        route = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(route, (tuple, list)) or len(route) != 2:
            return
        checked = item.checkState() == Qt.CheckState.Checked
        section = self.power_section
        if section is None:
            return
        was_silent = self._silent
        self._silent = True
        self._syncing_modes = True
        try:
            if checked:
                other = (
                    section.driver_power_plan_editor
                    if group == "dut"
                    else section.dut_power_plan_editor
                )
                other.set_route_checked(route, False)
            self._sync_cards_from_plans()
        finally:
            self._syncing_modes = False
            self._silent = was_silent
        self._emit_fragment_changed()

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
        if self.card_wall is not None:
            was_silent = self._silent
            self._silent = True
            try:
                self.card_wall.set_driver_available(self.test_mode_enabled)
                self._sync_cards_from_plans()
            finally:
                self._silent = was_silent

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
        self._refresh_card_wall(preserve_edit_state=True)

    def _card_cards(self) -> list[tuple[str, Any]]:
        """当前配置里每台电源的卡片数据；两个通道都显示，档位按上电计划回填。

        卡片不再在每次进入页面时统一从 OFF 开始：已经写进 ``driver_amplifier`` /
        ``dut_amplifier`` 计划的路由会显示对应档位，其余通道为 OFF。
        """
        modes = self._planned_modes()
        cards: list[tuple[str, Any]] = []
        for supply_id, entry in self._power_supply_sources().items():
            data = power_card_data(entry, supply_id)
            for name in CHANNEL_NAMES:
                data = data.with_channel_mode(
                    name, modes.get((supply_id, name), "off")
                )
            cards.append((supply_id, data))
        return cards

    def _refresh_card_wall(self, *, preserve_edit_state: bool = False) -> None:
        """按当前配置与排布结果重画卡片墙；没有电源时整块高度收为 0。"""
        wall = self.card_wall
        if wall is None:
            return
        wall.set_cards(
            self._card_cards(),
            self._rack_slots,
            preserve_edit_state=preserve_edit_state,
        )

    def play_card_morph(self, start_rects: Any) -> bool:
        """从排布页卡片的屏幕位置展开成卡片；返回是否真的播放了动画。

        ``start_rects`` 是 ``supply_id -> 全局 QRect``。页面必须已经是当前页并完成
        布局；拿不到卡片或起点时直接显示静止卡片墙与标题/表单（返回 ``False``）。
        真正播放时先把标题、表单与底部导航藏起来，覆盖层只画卡片；落位后再淡入，
        因此画面先在卡片墙上收住，再出现“电源配置”标题和下方表单。
        """
        wall = self.card_wall
        if wall is None or wall.is_empty() or not isinstance(start_rects, Mapping):
            self._set_intro_visible(True)
            return False
        starts = {
            str(supply_id): rect
            for supply_id, rect in start_rects.items()
            if isinstance(rect, QRect) and not rect.isEmpty()
        }
        if not starts:
            self._set_intro_visible(True)
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
            target = wall.card_widget_rect(cell.supply_id)
            if target is None:
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
            self._set_intro_visible(True)
            return False
        self.finish_card_morph()
        self._set_intro_visible(False)
        wall.set_paint_enabled(False)
        # 过渡覆盖层鼠标穿透，底层卡片墙若仍可点就会在动画中改配置。
        wall.setEnabled(False)
        overlay = PowerCardMorphOverlay(self, items)
        overlay.setGeometry(self.rect())
        overlay.finished.connect(lambda: self._finish_card_morph_overlay(overlay))
        self._card_morph = overlay
        overlay.start()
        return True

    # ---- 标题 / 表单 / 导航的入场 ------------------------------------------

    def _set_intro_opacity(self, value: Any) -> None:
        opacity = max(0.0, min(1.0, float(value)))
        self._intro_opacity = opacity
        for effect in (
            self._header_effect,
            self._section_effect,
            self._footer_effect,
        ):
            effect.setOpacity(opacity)

    def _set_intro_visible(self, visible: bool) -> None:
        """直接显示或藏起标题/表单/导航；藏起时仍占布局，卡片位置不会跳。"""
        self._intro_animation.stop()
        self._set_intro_opacity(1.0 if visible else 0.0)
        self._set_navigation_enabled(bool(visible))

    def _set_navigation_enabled(self, enabled: bool) -> None:
        self.back_btn.setEnabled(enabled)
        self.next_btn.setEnabled(enabled)

    def _play_intro(self) -> None:
        self._intro_animation.stop()
        self._set_navigation_enabled(False)
        self._set_intro_opacity(0.0)
        self._intro_animation.start()

    def _on_intro_finished(self) -> None:
        self._set_intro_opacity(1.0)
        self._set_navigation_enabled(True)

    def intro_visible(self) -> bool:
        """标题、表单与底部导航是否已经完全显示（过渡结束）。"""
        return self._intro_opacity >= 1.0 - 1e-9

    def card_morph_running(self) -> bool:
        return self._card_morph is not None

    def finish_card_morph(self) -> None:
        """立即结束过渡（含标题/表单淡入）；没有过渡时只把入场收尾。"""
        overlay = self._card_morph
        if overlay is not None:
            self._skip_intro_fade = True
            overlay.finish_now()
            return
        self._set_intro_visible(True)

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
        if self._skip_intro_fade:
            self._skip_intro_fade = False
            self._set_intro_visible(True)
        else:
            self._play_intro()

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

    def showEvent(self, event: Any) -> None:
        """每次进入电源页按当前计划回显通道档位。"""
        super().showEvent(event)
        if self.card_wall is not None:
            self._sync_cards_from_plans()

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
        """用新的设备绑定刷新电源选项与卡片，保留尚未提交的编辑。

        绑定写入仍由仪器设置页经配置会话完成；本页只接收结果，不自己扫描设备、
        不挑默认电源，也不改写配置引用。
        """
        self.device_bindings = bindings if isinstance(bindings, Mapping) else {}
        self._silent = True
        try:
            self._refresh_power_plans(preserve=True)
        finally:
            self._silent = False
        self._refresh_card_wall(preserve_edit_state=True)
        self._sync_cards_from_plans()
        self._emit_fragment_changed()

    # ---- 配置输入（只取本页负责的字段） ------------------------------------

    def _power_supply_sources(self, supplies: Any = None) -> PowerSupplySources:
        """电源卡片条目：配置里已有的键 + 本次已绑定的 ``supply_id``。"""
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

    def _refresh_power_plans(self, *, sources: Any = None, preserve: bool = True) -> None:
        """按当前电源集合刷新计划候选；计划与漏极本身由组件按需保留。"""
        if self.power_section is None:
            return
        current_sources = self._power_supply_sources() if sources is None else sources
        self.power_section.set_power_choices(
            current_sources, self._power_supply_choices()
        )

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
        self._sync_cards_from_plans()

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
                self._refresh_power_plans(
                    sources=self._power_supply_sources(supplies),
                    preserve=False,
                )
                self._refresh_card_wall()
                projected = self.form_adapter.apply_power_configuration(
                    config,
                    driver_plan_active=driver_plan_active,
                )
            except ConfigBackfillError:
                self.device_bindings = previous_bindings
                self.test_mode_enabled = previous_test_mode
                self._refresh_power_plans(
                    sources=self._power_supply_sources(), preserve=False
                )
                self._refresh_card_wall()
                raise
            self.config = projected
            self.update_driver_power_ui()
            self.update_power_supply_options()
            self._refresh_card_wall()
            self._sync_cards_from_plans()
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
    def power_config_widgets(self):
        """按 ``ConfigFormState`` 的读取形状暴露卡片上的通道控件。

        卡片是通道参数的唯一编辑面；这里只把每台电源的控件映射交给表单 Adapter，
        页面不再另外保存一份表单控件。
        """
        if self.card_wall is None:
            return {}
        return self.card_wall.control_records()

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
