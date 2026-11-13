"""测量工作台外壳：常驻阶段条 + 页头 + 单个内容槽。

测量区的连续感来自“一个始终不动的框架”：阶段条和页头在切页时保持原位置，
只有内容槽里的内容在换。本模块只负责这块框架的组装与展示，不拥有页面栈，也
不导入任何页面类型：

- 内容由窗口通过 :meth:`MeasurementWorkspace.set_content` 注入，第一步继续由
  窗口里的 ``QTabWidget`` 承载页面；外壳不调用页面的 ``on_activated`` /
  ``on_deactivated``，也不决定页面生命周期。
- 页头只展示调用方传入的文本（当前阶段、run 标识、模式文本），不读配置、不读
  运行服务，避免在表现层形成第二个业务真相源。
- 阶段条仍是受控视图：点击阶段只经 :attr:`MeasurementWorkspace.stage_selected`
  发出导航请求，窗口切页完成后调用 :meth:`set_current_stage` 同步。
- ``show_utility`` 用于不属于 01–04 阶段的工具页（例如“测试配置”）：它清空当前
  阶段（阶段条不点亮任何一项），页头显示工具页自己的标题与副标题。
- 类名不带 ``Page`` 后缀，避免被页面注册门禁误登记为页面。

设计约束（见 ``docs/测量工作台重构计划.md`` 步骤 4）：

- 外壳不拥有内容栈、不导入页面类、不调用页面生命周期；
- 页头只做展示，``run_id`` 与模式文本由窗口传入；无运行时显示“未连接”；
- 内容槽只接受一个 widget；替换时从布局移除旧 widget，**不销毁**它，页面所有权
  仍归窗口/标签页；
- 颜色与字号只取 :mod:`presentation.qt.theme` 的既有语义角色，不在本模块写
  颜色值或字号。

阶段集合由构造参数决定：外壳接受 ``stages`` 时，当前阶段的查找也只在这个集合里
进行，不再回落到全局 :data:`~presentation.qt.measurement_stage.MEASUREMENT_STAGES`，
否则自定义阶段会出现“画得出、同步不了”的半成品状态。内容槽与右侧状态区只接受
调用方自己的控件；外壳的结构控件（阶段条、页头、内容宿主及其子控件）不能被搬进
自己里面，否则会拆掉常驻框架。
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .measurement_stage import (
    MEASUREMENT_STAGES,
    StageDescriptor,
    StageState,
)
from .measurement_stage_rail import MeasurementStageRail
from .theme import set_button_variant

__all__ = ["MeasurementWorkspace"]

#: 工具页（测试配置等）的 eyebrow：它不属于 01–04 阶段，不能显示阶段编号。
_UTILITY_EYEBROW = "工作区"

#: 没有 run 标识时页头显示的固定文案；步骤 6 只传入文本，不在外壳判断连接状态。
_UNCONNECTED_RUN_TEXT = "未连接"

_HEADER_MARGINS = (16, 12, 16, 8)
_CONTENT_MARGINS = (16, 0, 12, 12)


def _clean_text(value: str | None) -> str | None:
    """把可选文本规整成“去首尾空白后的非空字符串”或 ``None``。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("文本必须是字符串或 None")
    cleaned = value.strip()
    return cleaned or None


class MeasurementWorkspace(QWidget):
    """测量工作台的常驻外壳：阶段条、页头与内容槽。

    调用方只负责写入“当前在哪、各阶段什么状态、页头显示什么”，外壳不读配置、
    不读控制器、也不切换页面。点击阶段只发出 :attr:`stage_selected`；右侧“配置”
    动作只发出 :attr:`configuration_requested`，由窗口决定导航到哪一页。
    """

    #: 用户请求进入某个测量阶段；载荷是阶段 key，窗口负责真正切页。
    stage_selected = Signal(str)

    #: 用户请求回到测试配置；配置入口是工具动作，不是第五个阶段。
    configuration_requested = Signal()

    def __init__(
        self,
        stages: tuple[StageDescriptor, ...] = MEASUREMENT_STAGES,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._stages = tuple(stages)
        self._total_stages = len(self._stages)
        # 当前阶段的查找表与阶段条同源（同一份 ``stages``），不回落全局默认阶段：
        # 自定义阶段必须既能画出来，也能同步页头。
        self._stages_by_key: Mapping[str, StageDescriptor] = MappingProxyType(
            {stage.key: stage for stage in self._stages}
        )
        self._current_stage_key: str | None = None
        self._utility_view = False
        self._content: QWidget | None = None
        self._right_widgets: list[QWidget] = []
        self._run_id: str | None = None
        self._mode_text: str | None = None
        self._animations_enabled = True

        self._build_ui()
        self.set_run_identity(None, None)

    # ---- 构造 ---------------------------------------------------------------

    def _build_ui(self) -> None:
        """自上而下：阶段条 → 页头 → 内容槽；三段都是常驻框架。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.rail = MeasurementStageRail(self._stages, self)
        self.rail.setObjectName("measurementWorkspaceRail")
        self.rail.stage_selected.connect(self._on_rail_stage_selected)
        layout.addWidget(self.rail)

        layout.addWidget(self._build_header())
        layout.addWidget(self._build_content_host(), 1)

    def _build_header(self) -> QWidget:
        """页头：左侧 eyebrow/标题/副标题，右侧运行标识、状态区与配置入口。"""
        self.header = QFrame(self)
        self.header.setObjectName("measurementWorkspaceHeader")
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(*_HEADER_MARGINS)
        header_layout.setSpacing(16)

        title_column = QVBoxLayout()
        title_column.setContentsMargins(0, 0, 0, 0)
        title_column.setSpacing(2)

        self.eyebrow_label = QLabel(_UTILITY_EYEBROW, self.header)
        self.eyebrow_label.setObjectName("measurementWorkspaceEyebrow")
        self.eyebrow_label.setProperty("role", "hint")
        self.eyebrow_label.setTextFormat(Qt.TextFormat.PlainText)

        self.title_label = QLabel("", self.header)
        self.title_label.setObjectName("measurementWorkspaceTitle")
        self.title_label.setProperty("role", "section")
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)

        self.subtitle_label = QLabel("", self.header)
        self.subtitle_label.setObjectName("measurementWorkspaceSubtitle")
        self.subtitle_label.setProperty("role", "hint")
        self.subtitle_label.setTextFormat(Qt.TextFormat.PlainText)

        title_column.addWidget(self.eyebrow_label)
        title_column.addWidget(self.title_label)
        title_column.addWidget(self.subtitle_label)
        header_layout.addLayout(title_column, 1)

        right_panel = QWidget(self.header)
        right_panel.setObjectName("measurementWorkspaceStatusArea")
        self._right_layout = QHBoxLayout(right_panel)
        self._right_layout.setContentsMargins(0, 0, 0, 0)
        self._right_layout.setSpacing(8)

        self.run_identity_label = QLabel("", right_panel)
        self.run_identity_label.setObjectName("measurementWorkspaceRunIdentity")
        self.run_identity_label.setProperty("role", "hint")
        self.run_identity_label.setTextFormat(Qt.TextFormat.PlainText)
        self.run_identity_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._right_layout.addWidget(self.run_identity_label)

        self.configuration_btn = QPushButton("配置", right_panel)
        self.configuration_btn.setObjectName("measurementWorkspaceConfigButton")
        self.configuration_btn.setAccessibleName("打开测试配置")
        self.configuration_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        set_button_variant(self.configuration_btn, "ghost")
        self.configuration_btn.clicked.connect(
            self._emit_configuration_requested
        )
        # “配置”始终是右侧状态区的最后一个动作；add_right_widget 插在它前面。
        self._right_layout.addWidget(self.configuration_btn)

        header_layout.addWidget(
            right_panel,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        return self.header

    def _build_content_host(self) -> QWidget:
        """内容槽的宿主：只放一个 widget，替换时只做移除与重挂。"""
        self.content_host = QWidget(self)
        self.content_host.setObjectName("measurementWorkspaceContent")
        self._content_layout = QVBoxLayout(self.content_host)
        self._content_layout.setContentsMargins(*_CONTENT_MARGINS)
        self._content_layout.setSpacing(0)
        return self.content_host

    # ---- 读取 ---------------------------------------------------------------

    def stage_rail(self) -> MeasurementStageRail:
        """常驻阶段条；窗口在步骤 5 之后可据此做细粒度检查。"""
        return self.rail

    def current_stage(self) -> str | None:
        """当前阶段 key；工具页（测试配置等）为 ``None``。"""
        return self._current_stage_key

    def is_utility_view(self) -> bool:
        """页头当前是否显示工具页（不点亮任何阶段）。"""
        return self._utility_view

    def content_widget(self) -> QWidget | None:
        """内容槽里的当前 widget；没有内容时为 ``None``。"""
        return self._content

    def right_widgets(self) -> tuple[QWidget, ...]:
        """右侧状态区里由调用方添加的 widget，按添加顺序返回。"""
        return tuple(self._right_widgets)

    def run_identity(self) -> tuple[str | None, str | None]:
        """页头展示的 ``(run_id, mode_text)``；未设置的部分为 ``None``。"""
        return self._run_id, self._mode_text

    def animations_enabled(self) -> bool:
        """外壳的动效开关（当前只作用于阶段条指示块）。"""
        return self._animations_enabled

    # ---- 写入：阶段与页头 ---------------------------------------------------

    def set_current_stage(self, key: str | None) -> None:
        """设置当前阶段并同步页头；``None`` 表示没有当前阶段。

        阶段条是受控视图：这里由窗口在真正切页后调用。key 必须是构造时传入的阶段
        集合里的 key；未知 key 抛 ``KeyError``，且不会改动任何页头文本。
        """
        if key is None:
            self._enter_no_stage_view()
            return
        if not isinstance(key, str):
            raise TypeError("当前阶段 key 必须是字符串或 None")
        try:
            descriptor = self._stages_by_key[key]
        except KeyError:
            raise KeyError(f"未知测量阶段: {key!r}") from None
        self.rail.set_current(descriptor.key)
        self._current_stage_key = descriptor.key
        self._utility_view = False
        self._apply_stage_header(descriptor)

    def show_utility(self, title: str, subtitle: str) -> None:
        """显示一个不属于 01–04 阶段的工具页（例如“测试配置”）。

        阶段条不点亮任何一项；页头标题与副标题由窗口传入。``title`` 必须非空，
        ``subtitle`` 可以为空字符串。
        """
        if not isinstance(title, str):
            raise TypeError("工具页标题必须是字符串")
        if not isinstance(subtitle, str):
            raise TypeError("工具页副标题必须是字符串")
        cleaned_title = title.strip()
        if not cleaned_title:
            raise ValueError("工具页标题不能为空")

        self.rail.set_current(None)
        self._current_stage_key = None
        self._utility_view = True
        self.eyebrow_label.setText(_UTILITY_EYEBROW)
        self.title_label.setText(cleaned_title)
        self.subtitle_label.setText(subtitle.strip())

    def set_run_identity(
        self, run_id: str | None, mode_text: str | None
    ) -> None:
        """写入页头展示的 run 标识与模式文本；只展示，不判断连接状态。

        ``run_id`` 为空时显示“未连接”；模式文本为空时只显示 run 部分。
        """
        self._run_id = _clean_text(run_id)
        self._mode_text = _clean_text(mode_text)
        self.run_identity_label.setText(self._format_run_identity())

    def _format_run_identity(self) -> str:
        run_text = self._run_id or _UNCONNECTED_RUN_TEXT
        if self._mode_text:
            return f"本次运行 {run_text} · {self._mode_text}"
        return f"本次运行 {run_text}"

    def _apply_stage_header(self, descriptor: StageDescriptor) -> None:
        kind_text = "查看" if descriptor.kind is None else "测量"
        self.eyebrow_label.setText(
            f"阶段 {descriptor.order:02d} / {self._total_stages:02d}"
            f" · {kind_text}"
        )
        self.title_label.setText(descriptor.title)
        self.subtitle_label.setText(descriptor.subtitle)

    def _enter_no_stage_view(self) -> None:
        """没有当前阶段：阶段条收起指示块，页头回到空的工作区状态。"""
        self.rail.set_current(None)
        self._current_stage_key = None
        self._utility_view = False
        self.eyebrow_label.setText(_UTILITY_EYEBROW)
        self.title_label.setText("")
        self.subtitle_label.setText("")

    # ---- 写入：阶段状态与动效 -------------------------------------------------

    def set_stage_state(self, key: str, state: StageState | str) -> None:
        """更新一个阶段的运行状态；只接受五个运行状态，``DISABLED`` 由启用状态派生。"""
        self.rail.set_state(key, state)

    def set_stage_enabled(self, key: str, enabled: bool) -> None:
        """启用/停用一个阶段；停用只影响交互与显示，编号与保存的状态都不丢。"""
        self.rail.set_stage_enabled(key, enabled)

    def set_animations_enabled(self, enabled: bool) -> None:
        """打开/关闭阶段条动效；关闭时指示块立即落到当前阶段。"""
        if not isinstance(enabled, bool):
            raise TypeError("enabled 必须是 bool")
        self._animations_enabled = enabled
        self.rail.set_animations_enabled(enabled)

    # ---- 写入：内容与右侧状态区 -----------------------------------------------

    def _structural_widgets(self) -> tuple[QWidget, ...]:
        """常驻框架自己的控件：它们不能被搬进内容槽或右侧状态区。"""
        return (self.rail, self.header, self.content_host)

    def _ensure_external_widget(self, widget: QWidget, usage: str) -> None:
        """拒绝把外壳自己、它的结构控件（含子控件）或它的祖先交给外壳摆放。

        ``set_content`` / ``add_right_widget`` 只负责摆放调用方的控件；把常驻框架
        的一部分重新挂进去会拆掉阶段条或页头，属于调用错误而不是显示选择。
        """
        if widget is self or widget.isAncestorOf(self):
            raise ValueError(f"{usage}不能是工作台本身或它的祖先")
        for structural in self._structural_widgets():
            if widget is structural or structural.isAncestorOf(widget):
                raise ValueError(f"{usage}不能是工作台自己的结构控件")

    def set_content(self, widget: QWidget | None) -> None:
        """设置内容槽里的唯一 widget；``None`` 清空内容。

        替换时先把旧 widget 从布局移除并摘掉父级，**不销毁**它：页面所有权仍归
        窗口/标签页，外壳只负责摆放。重复设置同一个 widget 是幂等的。
        """
        if widget is not None and not isinstance(widget, QWidget):
            raise TypeError("内容必须是 QWidget 或 None")
        if widget is self._content:
            return
        if widget is not None:
            self._ensure_external_widget(widget, "内容")

        previous = self._content
        if previous is not None:
            previous.hide()
            self._content_layout.removeWidget(previous)
            previous.setParent(None)

        self._content = widget
        if widget is not None:
            self._content_layout.addWidget(widget, 1)
            widget.show()

    def add_right_widget(self, widget: QWidget) -> None:
        """把一个状态 widget 追加到页头右侧；“配置”动作保持在最右端。

        重复添加同一个 widget 是幂等的；widget 原来属于别的父控件时由 Qt 重新
        挂接，调用方仍持有它，外壳不销毁。
        """
        if not isinstance(widget, QWidget):
            raise TypeError("右侧状态控件必须是 QWidget")
        if any(existing is widget for existing in self._right_widgets):
            return
        self._ensure_external_widget(widget, "右侧状态控件")
        # 最后一项始终是“配置”动作，状态控件插在它前面。
        self._right_layout.insertWidget(
            self._right_layout.count() - 1, widget
        )
        self._right_widgets.append(widget)

    # ---- 内部：信号转发 -------------------------------------------------------

    def _on_rail_stage_selected(self, key: str) -> None:
        """阶段条点击只转发导航请求，不在这里写 ``current``。"""
        self.stage_selected.emit(key)

    def _emit_configuration_requested(self) -> None:
        self.configuration_requested.emit()
