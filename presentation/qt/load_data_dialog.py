"""测量页“加载数据”入口的选择对话框。

主功放页同时使用线损与驱动映射两类校准输入：左栏只保留一个“加载数据”按钮，
点击后在这里让用户选择要加载哪一类。对话框只负责“列出候选项 + 已选来源 →
返回选中的键”，不打开文件选择器、不读文件、不碰应用层；文件选择与阶段结果
校验仍由窗口侧的 :mod:`presentation.qt.stage_result_selection` 承担。

只有一种数据来源的页面（线损页、驱动映射页、无驱放模式下的主功放页）不弹这个
对话框，直接打开文件选择器；因此这里也提供把选择转成展示文案的纯函数，让页面
的按钮 tooltip 与对话框共用同一份说法。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .theme import set_button_variant

__all__ = [
    "LoadDataDialog",
    "LoadTarget",
    "choose_load_target",
    "load_tooltip",
    "selection_source_text",
]

#: 对话框宽度：两个整宽条目在窄栏之外仍保持一行说明可读。
DIALOG_WIDTH = 420


@dataclass(frozen=True)
class LoadTarget:
    """一个可加载的数据来源：键、按钮文案与当前已选来源（未选择为 ``None``）。"""

    key: str
    label: str
    source: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not self.key.strip():
            raise ValueError("加载数据键不能为空")
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("加载数据按钮文案不能为空")
        if self.source is not None and not isinstance(self.source, str):
            raise TypeError("加载数据来源说明必须是字符串或 None")


def selection_source_text(selection: Any) -> str | None:
    """把页面保存的选择转成“文件名 + 测量时间”；没有选择时返回 ``None``。

    只读页面已经保存的事实，不读取文件、不解析 JSON；页面与对话框都用这一段
    文案，避免两处各写一套格式。
    """
    filename = getattr(selection, "filename", None)
    if not isinstance(filename, str) or not filename.strip():
        return None
    created_at = getattr(selection, "created_at", None)
    if isinstance(created_at, str) and created_at.strip():
        return f"{filename.strip()}\n测量时间：{created_at.strip()}"
    return filename.strip()


def load_tooltip(targets: Sequence[LoadTarget]) -> str:
    """按钮 tooltip：一个入口下每一类数据当前用的是哪份文件。"""
    lines = ["加载数据"]
    for target in targets:
        source = target.source.strip() if isinstance(target.source, str) else ""
        lines.append(f"{target.label}：{source or '未选择'}")
    return "\n".join(lines)


class LoadDataDialog(QDialog):
    """列出可加载的数据来源，用户选中哪一项就以该项的键关闭。"""

    def __init__(
        self,
        targets: Sequence[LoadTarget],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if isinstance(targets, (str, bytes)) or not isinstance(targets, Sequence):
            raise TypeError("加载数据候选项必须是 LoadTarget 序列")
        if not targets:
            raise ValueError("加载数据候选项不能为空")
        self._targets = tuple(targets)
        for target in self._targets:
            if not isinstance(target, LoadTarget):
                raise TypeError("加载数据候选项必须是 LoadTarget 序列")
        self._selected: str | None = None
        self.setWindowTitle("加载数据")
        self.setMinimumWidth(DIALOG_WIDTH)
        self.setModal(True)
        self._build_ui()

    # ---- 构建 ---------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        hint = QLabel("选择要加载的数据来源")
        hint.setObjectName("emptyHint")
        hint.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(hint)
        for target in self._targets:
            group = QVBoxLayout()
            group.setContentsMargins(0, 0, 0, 0)
            group.setSpacing(4)
            button = QPushButton(target.label, self)
            button.setObjectName("loadDataTarget")
            set_button_variant(button, "secondary")
            button.clicked.connect(
                lambda _checked=False, key=target.key: self._choose(key)
            )
            group.addWidget(button)
            source = QLabel(
                f"当前：{target.source}" if target.source else "未选择", self
            )
            source.setObjectName("emptyHint")
            source.setTextFormat(Qt.TextFormat.PlainText)
            source.setWordWrap(True)
            group.addWidget(source)
            layout.addLayout(group)
        cancel_row = QHBoxLayout()
        cancel_row.setContentsMargins(0, 0, 0, 0)
        cancel_row.addStretch(1)
        cancel = QPushButton("取消", self)
        cancel.setObjectName("loadDataCancel")
        set_button_variant(cancel, "secondary")
        cancel.clicked.connect(self.reject)
        cancel_row.addWidget(cancel)
        layout.addLayout(cancel_row)

    # ---- 结果 ---------------------------------------------------------------

    def _choose(self, key: str) -> None:
        self._selected = key
        self.accept()

    def selected_key(self) -> str | None:
        """用户选中的数据键；取消或尚未选择时为 ``None``。"""
        return self._selected

    def target_buttons(self) -> tuple[QPushButton, ...]:
        """按顺序返回每一类数据的整宽按钮（供界面测试直接驱动）。"""
        buttons = self.findChildren(QPushButton, "loadDataTarget")
        return tuple(buttons)


def choose_load_target(
    parent: QWidget | None, targets: Sequence[LoadTarget]
) -> str | None:
    """模态选择一项数据来源；用户取消时返回 ``None``。"""
    dialog = LoadDataDialog(targets, parent)
    try:
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.selected_key()
        return None
    finally:
        dialog.deleteLater()
