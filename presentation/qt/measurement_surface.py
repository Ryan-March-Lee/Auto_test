"""共享测量面：控制面、结果面与面内的三种排版小件。

测量页的目标形态是“每页最多两块面”：左栏控制面（当前频点、主操作、校准输入）与
右侧结果面（表格、曲线或空态）。两块面都保留卡片的白底、细边与圆角，但内部不再
嵌套 ``QGroupBox``；分区只靠小标题、留白和 1px 发丝线表达。本模块把这块共用的
外观与排版收在一处，三个测量页在步骤 10–12 里只负责往面里放自己的控件。

- :func:`build_surface`：返回一个带 ``QVBoxLayout`` 的面；``kind`` 决定 objectName，
  从而由 :mod:`presentation.qt.theme` 的同一条 QSS 决定外观。
- :func:`section_caption`：面内小标题（``role="sectionCaption"``）。
- :func:`hairline`：一条 1px 分隔线（``QFrame#measurementHairline``）。
- :func:`metric_label`：结果面里的关键数字（``QLabel#measurementMetric``）。
- :class:`StageMetricsRow`：结果面的 KPI 行；页面把已经取好的
  :class:`~presentation.qt.stage_summary.StageMetric` 交给它，它只排版，不计算。

颜色、字号与线宽全部由主题样式表给出，本模块不写颜色值，也不设置内联样式；页面
不重复声明这些几何与语义，只在返回的面里追加自己的控件。模块不导入页面注册表，
也不读取测量业务数据。
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .stage_summary import StageMetric

#: 控制面：左栏的窄面，放当前频点、主操作与校准输入。
CONTROL_SURFACE = "control"

#: 结果面：右侧的宽面，放表格、图表或空态说明。
RESULT_SURFACE = "result"

#: 允许的面类型；未知取值直接报错，避免页面在运行时才发现 objectName 写错。
SURFACE_KINDS = (CONTROL_SURFACE, RESULT_SURFACE)

_SURFACE_OBJECT_NAMES = {
    CONTROL_SURFACE: "measurementSurface",
    RESULT_SURFACE: "measurementResultSurface",
}

#: 面内边距与控件间距：三个测量页共用同一组几何，切页时内容不会横向跳动。
SURFACE_MARGINS = (16, 14, 16, 14)
SURFACE_SPACING = 10


def _validated_kind(kind: str) -> str:
    """校验面类型；非字符串抛 ``TypeError``，未知取值抛 ``ValueError``。"""
    if not isinstance(kind, str):
        raise TypeError("测量面类型必须是字符串")
    if kind not in _SURFACE_OBJECT_NAMES:
        raise ValueError(f"未知的测量面类型: {kind!r}")
    return kind


def build_surface(
    kind: str = CONTROL_SURFACE,
    *,
    title: str | None = None,
    parent: QWidget | None = None,
) -> QFrame:
    """构造一块测量面，并返回已经装好垂直布局的 ``QFrame``。

    ``kind`` 取 :data:`CONTROL_SURFACE` 或 :data:`RESULT_SURFACE`；``title`` 非空时
    在面内最上方放一条 :func:`section_caption`，调用方随后往 ``surface.layout()`` 里
    追加自己的控件。面本身不拥有页面数据，也不连接任何信号。
    """
    resolved = _validated_kind(kind)
    cleaned_title = None
    if title is not None:
        if not isinstance(title, str):
            raise TypeError("测量面标题必须是字符串")
        cleaned_title = title.strip()
        if not cleaned_title:
            raise ValueError("测量面标题不能为空")

    surface = QFrame(parent)
    surface.setObjectName(_SURFACE_OBJECT_NAMES[resolved])
    layout = QVBoxLayout(surface)
    layout.setContentsMargins(*SURFACE_MARGINS)
    layout.setSpacing(SURFACE_SPACING)
    if cleaned_title is not None:
        layout.addWidget(section_caption(cleaned_title, parent=surface))
    return surface


def section_caption(text: str, *, parent: QWidget | None = None) -> QLabel:
    """面内小标题：只声明语义角色，字号与颜色由主题样式表决定。"""
    if not isinstance(text, str):
        raise TypeError("小标题必须是字符串")
    label = QLabel(text, parent)
    label.setProperty("role", "sectionCaption")
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


def hairline(*, parent: QWidget | None = None) -> QFrame:
    """1px 发丝线：分隔同一块面里的两组内容，不引入新的卡片边界。"""
    line = QFrame(parent)
    line.setObjectName("measurementHairline")
    line.setFrameShape(QFrame.Shape.NoFrame)
    line.setFixedHeight(1)
    line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return line


def metric_label(text: str = "", *, parent: QWidget | None = None) -> QLabel:
    """结果面里的关键数字／摘要值；文本由调用方从已显示的数据里取。"""
    if not isinstance(text, str):
        raise TypeError("度量文本必须是字符串")
    label = QLabel(text, parent)
    label.setObjectName("measurementMetric")
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


class StageMetricsRow(QWidget):
    """结果面的 KPI 行：只排版页面已经取好的值和标签，不做任何统计。

    三个测量页在数据变化时重新调用 :meth:`set_metrics`；行内单元在构造后复用，只在
    数量增加时补建控件，避免每个频点都销毁重建标签。空元组隐藏整行，页面自己负责
    空态说明；摘要的“未开始”由页面按 :mod:`presentation.qt.stage_summary` 的约定给出。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("stageMetricsRow")
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        # 芯片之间只留 10px：KPI 是一组读数，不是三段被拉满整行的标题。
        self._layout.setSpacing(10)
        self._cells: list[tuple[QWidget, QLabel, QLabel]] = []
        self._metrics: tuple[StageMetric, ...] = ()
        # 尾部留白吸收多余宽度：芯片保持自身宽度，不再均分结果面。
        self._layout.addStretch(1)
        self.setVisible(False)

    def metrics(self) -> tuple[StageMetric, ...]:
        """当前展示的 KPI；空元组表示整行隐藏。"""
        return self._metrics

    def set_metrics(self, metrics: Sequence[StageMetric]) -> None:
        """写入一排 KPI；重复写入同一组值不重建控件。"""
        if isinstance(metrics, (str, bytes)) or not isinstance(
            metrics, Sequence
        ):
            raise TypeError("KPI 必须是 StageMetric 序列")
        validated = tuple(metrics)
        for metric in validated:
            if not isinstance(metric, StageMetric):
                raise TypeError("KPI 必须是 StageMetric 序列")
        if validated == self._metrics:
            return
        while len(self._cells) < len(validated):
            self._add_cell()
        for index, (container, label, value) in enumerate(self._cells):
            if index < len(validated):
                metric = validated[index]
                label.setText(metric.label)
                value.setText(metric.value)
                container.show()
            else:
                container.hide()
        self._metrics = validated
        self.setVisible(bool(validated))

    def _add_cell(self) -> None:
        """补建一个 KPI 单元：小标签在上，关键数字在下。"""
        container = QFrame(self)
        container.setObjectName("measurementKpiChip")
        container.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred
        )
        cell_layout = QVBoxLayout(container)
        cell_layout.setContentsMargins(10, 6, 10, 6)
        cell_layout.setSpacing(1)
        label = QLabel(container)
        label.setProperty("role", "hint")
        label.setTextFormat(Qt.TextFormat.PlainText)
        value = metric_label(parent=container)
        cell_layout.addWidget(label)
        cell_layout.addWidget(value)
        # 最后一个布局项永远是尾部留白：新芯片插在它前面，保持左对齐。
        self._layout.insertWidget(self._layout.count() - 1, container)
        self._cells.append((container, label, value))


__all__ = [
    "CONTROL_SURFACE",
    "RESULT_SURFACE",
    "SURFACE_KINDS",
    "SURFACE_MARGINS",
    "SURFACE_SPACING",
    "StageMetricsRow",
    "build_surface",
    "hairline",
    "metric_label",
    "section_caption",
]
