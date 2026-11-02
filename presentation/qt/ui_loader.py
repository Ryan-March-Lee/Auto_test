"""加载 Qt Designer ``.ui`` 文件里的静态布局。

``.ui`` 只负责控件层级、静态文案和页面本地的排列（边距、间距、弹性留白）；颜色、
字号、语义属性（``role`` / ``variant`` / ``tone``）与跨页面共用的尺寸仍由
:mod:`presentation.qt.theme` 决定。业务规则、控件接线和动态内容（设备卡片、电源
标签页、实时图等）留在 Python 页面里。

``.ui`` 文件是运行时资源，不生成 Python 代码：Qt Designer 保存什么，页面就加载什么，
不需要额外的编译步骤；文件位置固定在 ``presentation/qt/ui/``，随 ``presentation``
包一起发布。创建控件需要一个已经存在的 ``QApplication``：页面由主窗口在应用创建之后
构造，因此正常调用方不需要额外处理。
"""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QFile, QIODevice
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QWidget

__all__ = ["UI_DIRECTORY", "UiLoadError", "load_ui", "ui_file"]

#: ``.ui`` 资源的唯一目录：文件名不带扩展名，避免调用方各自拼接路径。
UI_DIRECTORY = Path(__file__).resolve().with_name("ui")

#: 页面名只允许小写蛇形标识。拼路径前先校验：``../`` 之类的名字不能读到目录外面，
#: 拼错的名字也会在加载前就得到明确报错，而不是变成“文件不存在”。
_PAGE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class UiLoadError(RuntimeError):
    """``.ui`` 文件缺失、无法读取或没有生成根控件。"""


def ui_file(name: str) -> Path:
    """返回 ``name`` 对应的 ``.ui`` 文件路径（``name`` 不带扩展名）。

    名字不是小写蛇形标识时抛 :class:`ValueError`：这是调用方写错了页面名，属于
    调用契约错误，不是运行时可以恢复的加载失败。
    """
    if not _PAGE_NAME.match(name):
        raise ValueError(f"非法的界面文件名: {name!r}")
    return UI_DIRECTORY / f"{name}.ui"


def load_ui(name: str, parent: QWidget | None = None) -> QWidget:
    """加载 ``name`` 对应的 ``.ui`` 文件并返回根控件。

    每次调用都新建一个 :class:`QUiLoader`：Qt 的加载器不是线程安全的，页面也应当
    各自拥有一份控件树。文件缺失、无法读取或解析失败时抛出 :class:`UiLoadError`，
    而不是返回 ``None`` 让页面在后面的属性访问里才失败；解析失败会带上 Qt 报告的
    原因，便于区分“文件没发布”和“文件内容损坏”。
    """
    path = ui_file(name)
    if not path.is_file():
        raise UiLoadError(f"界面文件不存在: {path}")
    loader = QUiLoader()
    file = QFile(str(path))
    if not file.open(QIODevice.ReadOnly | QIODevice.Text):
        raise UiLoadError(f"无法读取界面文件: {path}")
    try:
        widget = loader.load(file, parent)
    finally:
        file.close()
    if widget is None:
        raise UiLoadError(
            f"界面文件没有生成根控件: {path}: {loader.errorString() or '未知原因'}"
        )
    return widget
