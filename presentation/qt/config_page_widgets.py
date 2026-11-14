"""控件构建辅助。

本模块只负责配置页的控件构建：底部固定操作栏的布局，以及页面共用的输入控件工厂
（数值框、下拉框）。工厂把“安全输入控件 + 页面表单的固定宽度/选项”这类构建细节
收在一处；控件仍登记在页面上、信号由页面连接，因此配置解释与业务状态不会进到 Qt
构建代码里。
"""

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from .input_controls import NumericTextField, WheelSafeComboBox
from .theme import PAGE_CONTENT_MAX_WIDTH, set_button_variant

# 配置页表单里数值框的固定宽度：长数字不撑开列宽，两列表单的输入列才能对齐。
_NUMBER_FIELD_MAXIMUM_WIDTH = 140

#: DUT 输入功率扫描的输入范围（dBm）与步长范围（dB）。
#:
#: 功率计划行与配置页原有的单计划控件共用这一份取值，范围只改一处；控件只提供输入
#: 体验（钳制与显示精度），DUT 上限等业务上限仍由上层校验。
POWER_RANGE_DBM = (-50.0, 20.0)
POWER_STEP_RANGE_DB = (0.1, 5.0)


def number_field(value, minimum, maximum, *, decimals=2):
    """建一个纯文本数值输入框；宽度按配置页表单固定，避免长数字撑开列宽。

    ``decimals`` 由调用点声明：频率需要 kHz 级精度，功率沿用两位小数。控件只提供
    文本直输与范围/精度处理，不再有上下步进按钮。
    """
    widget = NumericTextField(value, minimum, maximum, decimals=decimals)
    widget.setMaximumWidth(_NUMBER_FIELD_MAXIMUM_WIDTH)
    return widget


def combo_box(items=(), current=None):
    """建一个下拉框；给了选项就按顺序加入，``current`` 不是 ``None`` 时才选中。

    ``current=None`` 表示“此刻还没有值”，因此不调用 ``setCurrentText``：既不会把
    ``None`` 渲染成一项文本，也让“先建空框、稍后再填选项”的调用点保持原样。
    """
    widget = WheelSafeComboBox()
    if items:
        widget.addItems([str(item) for item in items])
    if current is not None:
        widget.setCurrentText(str(current))
    return widget


def build_action_bar(page, primary_action=None) -> QWidget:
    """构建配置页底部操作栏，并把需要由页面使用的控件登记到 ``page``。

    ``primary_action`` 是页面自己的最终动作（“连接仪器”）：它排在“上一步”“保存配置”
    之后，成为底栏唯一的主操作。页面在调用本函数之前已经创建好该按钮，这里只负责摆放，
    不改文案、信号或可用状态。缺省为 ``None`` 时底栏仍只保留回退与保存，供独立测试使用。

    底栏是一条贴在页面底部的动作条：一条 1px 发丝线把它与上方可滚动内容分开（见
    ``theme.py`` 的 ``QFrame#pageActionBar``），层级由按钮的 ``variant`` 声明。
    """
    bar = QFrame()
    bar.setObjectName("pageActionBar")
    bar.setFrameShape(QFrame.NoFrame)
    # 与上方内容列同宽：动作条由页面放进居中的容器，这里给出跨页面共用的宽度上限。
    bar.setMaximumWidth(PAGE_CONTENT_MAX_WIDTH)
    row = QHBoxLayout(bar)
    row.setContentsMargins(0, 10, 4, 0)
    row.setSpacing(10)

    dirty_hint = QLabel("修改参数后请保存配置，运行时使用已保存并冻结的配置。")
    dirty_hint.setProperty("role", "hint")
    row.addWidget(dirty_hint, 1)

    page.previous_step_btn = QPushButton("上一步")
    page.previous_step_btn.setToolTip("回到上一级“电源配置”页，保留已经填好的电源参数")
    set_button_variant(page.previous_step_btn, "secondary")
    page.save_config_btn = QPushButton("保存配置")
    # 保存不是本页的终点：最终动作是“连接仪器”，保存降为次要操作，避免两个主按钮。
    set_button_variant(page.save_config_btn, "ghost")
    # “加载配置”已移到欢迎页：配置流程内只保留回退与保存。
    row.addWidget(page.previous_step_btn)
    row.addWidget(page.save_config_btn)
    if primary_action is not None:
        row.addWidget(primary_action)
    return bar


__all__ = [
    "POWER_RANGE_DBM",
    "POWER_STEP_RANGE_DB",
    "build_action_bar",
    "combo_box",
    "number_field",
]
