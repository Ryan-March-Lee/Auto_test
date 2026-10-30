"""统一主题：设计令牌、调色板与全局样式表。

界面只有一处配色与排版来源：本模块给出颜色/字号/间距令牌（:data:`TOKENS`）并生成
整份 QSS（:func:`build_stylesheet`）。页面不再各自 ``setStyleSheet`` 写死颜色，只用
控件属性声明语义（``variant`` 表示按钮层级、``tone`` 表示文字语义），外观由样式表
决定；因此“主操作/次要操作/危险操作/状态文字”在任何页面都是同一套视觉。

只做字符串拼装与 Qt 调色板构造，导入本模块不需要 ``QApplication``。少数几个字形
（下拉箭头、步进箭头、勾选标记）以 SVG 放在 ``assets/``：样式表只能引用图片文件，
无法用 QSS 画出来，因此这些图标随本模块一起维护，描边色与 :data:`TOKENS` 的文字色
一致（``text_secondary`` / ``text_muted`` / 白色）。
"""

from __future__ import annotations

from pathlib import Path
from string import Template
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette

__all__ = [
    "TOKENS",
    "FONT_FAMILIES",
    "WELCOME_BACKGROUND_FILE",
    "BUTTON_VARIANTS",
    "TONE_NAMES",
    "button_variant",
    "build_palette",
    "build_stylesheet",
    "base_font",
    "apply_theme",
    "set_button_variant",
    "set_tone",
]

# ---- 设计令牌 ---------------------------------------------------------------

TOKENS = {
    # 底色与容器
    "canvas": "#F5F7FA",
    "surface": "#FFFFFF",
    "surface_sunken": "#F8FAFC",
    "surface_alt": "#FAFBFC",
    # 空落点的浅灰底：“仪表配置”页里还没点“查找设备”时的结果框填充色。
    "surface_dropzone": "#F1F5F9",
    "border": "#E2E8F0",
    "border_strong": "#CBD5E1",
    "table_grid": "#F1F5F9",
    "header_text": "#475569",
    # 文字
    "text": "#1F2937",
    "text_secondary": "#64748B",
    "text_muted": "#94A3B8",
    # 主色
    "primary": "#2563EB",
    "primary_hover": "#1D4ED8",
    "primary_pressed": "#1E40AF",
    "primary_soft": "#EFF6FF",
    "primary_soft_border": "#DBEAFE",
    # 强调填充色：欢迎页模式按钮悬停时从左侧涌入的圆形色块（emerald-500）。
    "accent": "#10B981",
    "accent_pressed": "#047857",
    # 语义色
    "success": "#16A34A",
    "success_hover": "#15803D",
    "success_pressed": "#166534",
    "success_soft": "#F0FDF4",
    "warning": "#D97706",
    "warning_soft": "#FFFBEB",
    "danger": "#DC2626",
    "danger_hover": "#B91C1C",
    "danger_pressed": "#991B1B",
    "danger_soft": "#FEF2F2",
    # 图表曲线（实时图与历史图共用同一组序列色）
    "series_1": "#2563EB",
    "series_2": "#16A34A",
    "series_3": "#D97706",
    "series_4": "#7C3AED",
    # 控件表面：输入区“凹”、按钮“凸”。
    # 输入区用浅灰底 + 细边，按钮用白面 + 稍深的下边；两者因此不靠文字也能分辨。
    "button_face_top": "#FFFFFF",
    "button_face_bottom": "#F2F5FA",
    "button_shadow": "#BFCBDA",
    # 禁用与悬浮提示（工具提示保留深色，与浅色界面区分开）
    "disabled_bg": "#F1F5F9",
    "disabled_text": "#94A3B8",
    "scroll": "#CBD5E1",
    "tooltip_bg": "#0F172A",
    "tooltip_text": "#CBD5E1",
    "tooltip_border": "#1E293B",
    # 字号
    "font_title": "15pt",
    "font_page_title": "28pt",
    "font_section": "10.5pt",
    "font_body": "9.5pt",
    "font_base": "9.5pt",
    "font_small": "8.5pt",
    # 设备卡片第一行的仪器名：居中放大，“仪表配置”页一眼看到型号。
    "font_card_name": "14pt",
    # 设备码：加粗放大的等宽字，现场对机时一眼认得出。
    "font_code": "12.5pt",
    "font_mono": "Consolas",
}

FONT_FAMILIES = ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI")

#: 允许的按钮层级：主操作、次要操作、危险操作、低强调动作、正向切换。
BUTTON_VARIANTS = ("primary", "secondary", "danger", "ghost", "success")

#: 允许的语义文字色，用于状态标签（归档结果、扫描提示等）。
TONE_NAMES = ("info", "success", "warning", "danger", "muted")


#: 欢迎页背景图，放在项目根目录的 ``assets/``：它是一张场景图而不是界面字形，
#: 因此与图标分开存放；样式表按绝对路径引用，打包位置变化时只需改这一处。
WELCOME_BACKGROUND_FILE = (
    Path(__file__).resolve().parents[2] / "assets" / "init_background.png"
)


# ---- 调色板与字体 -----------------------------------------------------------


def build_palette() -> QPalette:
    """与样式表一致的浅色调色板。

    样式表只覆盖被点名的控件；未被覆盖的容器、布局间隙、原生对话框仍取调色板，
    因此调色板必须与 :data:`TOKENS` 同源，否则深色系统主题会重新露出黑底。
    """
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(TOKENS["canvas"]))
    palette.setColor(QPalette.WindowText, QColor(TOKENS["text"]))
    palette.setColor(QPalette.Base, QColor(TOKENS["surface"]))
    palette.setColor(QPalette.AlternateBase, QColor(TOKENS["surface_alt"]))
    palette.setColor(QPalette.Text, QColor(TOKENS["text"]))
    palette.setColor(QPalette.Button, QColor(TOKENS["surface"]))
    palette.setColor(QPalette.ButtonText, QColor(TOKENS["text"]))
    palette.setColor(QPalette.BrightText, QColor("#FFFFFF"))
    palette.setColor(QPalette.Light, QColor(TOKENS["surface"]))
    palette.setColor(QPalette.Midlight, QColor(TOKENS["border"]))
    palette.setColor(QPalette.Mid, QColor(TOKENS["border_strong"]))
    palette.setColor(QPalette.Dark, QColor(TOKENS["text_muted"]))
    palette.setColor(QPalette.Highlight, QColor(TOKENS["primary"]))
    palette.setColor(QPalette.HighlightedText, QColor("#FFFFFF"))
    palette.setColor(QPalette.ToolTipBase, QColor(TOKENS["tooltip_bg"]))
    palette.setColor(QPalette.ToolTipText, QColor(TOKENS["tooltip_text"]))
    palette.setColor(QPalette.PlaceholderText, QColor(TOKENS["text_muted"]))
    palette.setColor(QPalette.Link, QColor(TOKENS["primary"]))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor(TOKENS["disabled_text"]))
    return palette


def base_font() -> QFont:
    """界面基准字体：带中文字体回退链，行高按 9.5pt 计算。"""
    font = QFont()
    font.setFamilies(list(FONT_FAMILIES))
    font.setPointSizeF(9.5)
    return font


# ---- 控件语义属性 -----------------------------------------------------------


def _repolish(widget: Any) -> None:
    """属性变化后重新应用样式表；控件尚未创建样式时静默跳过。"""
    style = widget.style()
    if style is None:
        return
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_button_variant(button: Any, variant: str = "secondary") -> Any:
    """声明按钮层级（``primary``/``secondary``/``danger``/``ghost``/``success``）。

    只设置样式属性，不改变按钮的可用状态、文本或信号连接。
    """
    if variant not in BUTTON_VARIANTS:
        raise ValueError(f"未知的按钮层级: {variant}")
    button.setProperty("variant", variant)
    _repolish(button)
    return button


def set_tone(label: Any, tone: str) -> Any:
    """声明标签文字的语义色（``info``/``success``/``warning``/``danger``/``muted``）。"""
    if tone not in TONE_NAMES:
        raise ValueError(f"未知的语义色: {tone}")
    label.setProperty("tone", tone)
    _repolish(label)
    return label


# ---- 全局样式表 -------------------------------------------------------------

_VALUES = dict(TOKENS)
_VALUES["font_stack"] = FONT_FAMILIES[0]

#: 图标目录：QSS 的 ``image`` 只能引用图片文件，箭头与勾选标记因此以 SVG 随本模块发布。
ASSETS_DIR = Path(__file__).resolve().with_name("assets")


def _asset(name: str) -> str:
    """图标在样式表里的路径：必须是绝对路径，且用正斜杠。"""
    return (ASSETS_DIR / name).as_posix()


_VALUES["icon_chevron_down"] = _asset("chevron-down.svg")
_VALUES["icon_chevron_down_muted"] = _asset("chevron-down-muted.svg")
_VALUES["icon_chevron_up"] = _asset("chevron-up.svg")
_VALUES["icon_chevron_up_muted"] = _asset("chevron-up-muted.svg")
_VALUES["icon_check"] = _asset("check.svg")

_QSS = Template(
    """
/* ===== 基础 ===== */
QWidget {
    color: $text;
    font-family: "$font_stack";
    font-size: $font_base;
}
QMainWindow, QDialog, QMessageBox, QInputDialog, QFileDialog {
    background-color: $canvas;
}
QScrollArea, QScrollArea > QWidget > QWidget {
    background: transparent;
    border: none;
}
QStackedWidget {
    background: transparent;
}
QToolTip {
    background-color: $tooltip_bg;
    color: $tooltip_text;
    border: 1px solid $tooltip_border;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: $font_small;
}
QMenu {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 4px;
}
QMenu::item {
    padding: 6px 20px 6px 14px;
    border-radius: 4px;
    color: $text;
}
QMenu::item:selected {
    background-color: $primary_soft;
    color: $primary_pressed;
}
QMenu::separator {
    height: 1px;
    background: $border;
    margin: 4px 8px;
}

/* ===== 测试模式徽标（标签栏右侧） ===== */
QLabel#appModeBadge {
    background-color: $primary_soft;
    border: 1px solid $primary_soft_border;
    border-radius: 10px;
    color: $primary_pressed;
    font-size: $font_small;
    font-weight: 600;
    padding: 3px 10px;
}

/* ===== 欢迎页 =====
   整页是一张场景图（蓝色实验室），标题与按钮直接压在图上，不再套白色卡片；
   标题用白色粗体，落在图中偏亮的天空区域；按钮白底深字，悬停整块转主色蓝、文字转白，
   未悬停与悬停的差别一眼可见。
   背景图不写在这里：页面里的按钮与拉伸空白会让 Qt 只在局部画出这层背景，图片改由
   ``welcome_page.WelcomePage.paintEvent`` 铺满整页，这里只负责文字与按钮。
   字号也不写在这里：它随窗口尺寸缩放（``welcome_page.TITLE_POINT_SIZE`` 等），由
   ``WelcomePage`` 在尺寸变化时写进控件自己的样式表；应用样式表里写死一个字号会被
   窗口化/全屏切换后的版面比例顶开。这里只留与尺寸无关的颜色、字重与内边距。 */
QLabel#welcomeTitle {
    background: transparent;
    color: #FFFFFF;
    font-weight: 700;
    padding: 4px 18px;
}
QLabel#welcomeSubtitle {
    background: transparent;
    color: #E8F1FF;
    padding: 0 18px;
}
/* 模式按钮（#welcomeChoice）在这里没有规则：它由 ``welcome_page.ModeChoiceButton``
   自绘，悬停时一块圆形色块从左侧涌入并放大、箭头同时旋转。伪元素、``transition`` 与
   ``transform`` 都不在 QSS 的能力里，整块画下来才做得出来；用到的颜色仍取自 TOKENS。 */

/* ===== 主标签栏（分段选择器） ===== */
QTabWidget#mainTabs::pane {
    border: none;
    background: transparent;
}
QTabWidget#mainTabs QTabBar {
    background: transparent;
}
QTabWidget#mainTabs QTabBar::tab {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    color: $text_secondary;
    font-size: $font_body;
    margin: 0 2px;
    padding: 6px 14px;
}
QTabWidget#mainTabs QTabBar::tab:hover {
    background-color: $surface_sunken;
    color: $text;
}
QTabWidget#mainTabs QTabBar::tab:selected {
    background-color: $primary_soft;
    border-color: $primary_soft_border;
    color: $primary_pressed;
    font-weight: 600;
}

/* ===== 次级标签（电源 PS1..PS4） ===== */
QTabWidget#subTabs::pane {
    background-color: transparent;
    border: none;
    border-top: 1px solid $border;
    top: -1px;
}
QTabWidget#subTabs > QTabBar::tab {
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    color: $text_secondary;
    padding: 6px 12px;
}
QTabWidget#subTabs > QTabBar::tab:hover {
    color: $text;
}
QTabWidget#subTabs > QTabBar::tab:selected {
    border-bottom: 2px solid $primary;
    color: $primary;
    font-weight: 600;
}
/* ===== 卡片（QGroupBox） ===== */
QGroupBox {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 8px;
    margin-top: 0;
    padding: 32px 12px 12px 12px;
    font-size: $font_body;
}
QGroupBox::title {
    background: transparent;
    color: $text;
    font-weight: 600;
    left: 12px;
    padding: 0;
    subcontrol-origin: padding;
    subcontrol-position: top left;
    top: 10px;
}
QGroupBox QGroupBox {
    background-color: $surface_sunken;
    border-color: $border;
    padding: 28px 10px 10px 10px;
}
QGroupBox QGroupBox::title {
    color: $header_text;
    font-size: $font_small;
    left: 10px;
    top: 8px;
}

/* ===== 整屏页面标题 =====
   整屏步骤页（如“仪表配置”）只有一个大字号标题，居中放在页面偏上位置；
   它不参与卡片标题的层级，因此单独一条属性。 */
QLabel[role="pageTitle"] {
    color: $text;
    font-size: $font_page_title;
    font-weight: 700;
}

/* ===== 设备发现结果框（“仪表配置”页） =====
   扫描行留在框外，框只包住发现结果：一块圆角矩形，空着时是一块明确的落点，
   有卡片时卡片在框内换行。还没点过“查找设备”时框内铺浅灰底（按钮在正中）：
   点下去之后灰底与按钮一起收走，框回到白的、只装进度与结果。 */
QFrame#discoveryResultsBox {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 12px;
}
QFrame#discoveryResultsBox[filled="true"] {
    background-color: $surface_dropzone;
}
/* 框中央的“查找设备”在这里没有规则：它是一张自绘的深色卡片按钮
   （``scan_card.ScanPromptCard``），正中的渐变标题、柔光、光带与粒子都画在它自己的
   画布上，悬停时从卡片背后旋出去的底卡也一样——伪元素与 ``transition`` 不在 QSS
   的能力里，整块画下来才做得出来。 */

/* ===== 设备卡片（“仪表配置”页的发现结果） =====
   一台仪器一张卡片，自上而下读：居中放大的仪器名、仪器图、最下面的加粗设备码缩写。
   图片直接贴在卡片本色上，不垫底板也不描边。卡片可点击选中：选中时整块描主色并
   铺一层极浅的主色底，与“只悬停”区分得开；不可应用的卡片整块降低对比度，并在
   卡片里写明原因。 */
QFrame#instrumentCard {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 12px;
}
QFrame#instrumentCard:hover {
    border-color: $border_strong;
}
QFrame#instrumentCard[checked="true"] {
    background-color: $primary_soft;
    border: 2px solid $primary;
}
QFrame#instrumentCard[enabled-state="false"] {
    background-color: $surface_alt;
    border-color: $border;
}
QWidget#instrumentCardImage {
    background: transparent;
    border: none;
}
QLabel#instrumentCardName {
    color: $text;
    font-size: $font_card_name;
    font-weight: 700;
}
QLabel#instrumentCardCode {
    background: transparent;
    border: none;
    color: $text;
    font-family: "$font_mono";
    font-size: $font_code;
    font-weight: 700;
}
QLabel#instrumentCardStatus {
    color: $warning;
    font-size: $font_small;
}
QFrame#instrumentCard[enabled-state="false"] QLabel#instrumentCardName {
    color: $text_secondary;
}
QScrollArea#instrumentCardArea {
    background: transparent;
    border: none;
}
QWidget#instrumentCardHost {
    background: transparent;
}

/* ===== 文字 ===== */
QLabel {
    background: transparent;
}
QLabel[tone="info"] {
    color: $primary_pressed;
}
QLabel[tone="success"] {
    color: $success;
}
QLabel[tone="warning"] {
    color: $warning;
}
QLabel[tone="danger"] {
    color: $danger;
}
QLabel[tone="muted"] {
    color: $text_secondary;
}
QLabel[role="section"] {
    color: $text;
    font-size: $font_section;
    font-weight: 600;
}
QLabel[role="hint"] {
    color: $text_secondary;
    font-size: $font_small;
}

/* ===== 输入框 / 数值框：凹陷的“填写区” =====
   语义是“往里面写字”：浅灰底、1px 细边、没有箭头、文字左对齐。
   聚焦时底色转白、描边加粗到 2px 主色；内外边距互补，外框尺寸不变，文字不跳。 */
QLineEdit, QSpinBox, QDoubleSpinBox {
    background-color: $surface_sunken;
    border: 1px solid $border;
    border-radius: 6px;
    color: $text;
    min-height: 20px;
    padding: 5px 9px;
    selection-background-color: $primary;
    selection-color: #FFFFFF;
}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover {
    border-color: $border_strong;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    background-color: $surface;
    border: 2px solid $primary;
    padding: 4px 8px;
}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
    background-color: $disabled_bg;
    border-color: $border;
    color: $disabled_text;
}
QLineEdit[readOnly="true"] {
    background-color: $disabled_bg;
    color: $text_secondary;
}

/* 数值框：高度与输入框一致，步进按钮贴在内边，静止时只有箭头，悬停才浮出一块浅色。
   不填充常驻底色：那样会压住描边，聚焦加粗时边框看起来是断的。 */
QSpinBox, QDoubleSpinBox {
    padding: 4px 9px;
    min-height: 18px;
}
QSpinBox:focus, QDoubleSpinBox:focus {
    padding: 3px 8px;
}
QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 24px;
    height: 14px;
    border: none;
    border-radius: 4px;
    background: transparent;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: padding;
    subcontrol-position: bottom right;
    width: 24px;
    height: 14px;
    border: none;
    border-radius: 4px;
    background: transparent;
}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
    background-color: $border;
}
QSpinBox::up-button:pressed, QDoubleSpinBox::up-button:pressed,
QSpinBox::down-button:pressed, QDoubleSpinBox::down-button:pressed {
    background-color: $border_strong;
}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
    image: url($icon_chevron_up);
    height: 11px;
    width: 11px;
}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
    image: url($icon_chevron_down);
    height: 11px;
    width: 11px;
}
QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled {
    image: url($icon_chevron_up_muted);
}
QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {
    image: url($icon_chevron_down_muted);
}

/* ===== 下拉框：白底 + 右侧可见的展开箭头 =====
   下拉框是“从固定选项里挑一个”，因此保留白底与箭头（输入框是浅灰底、无箭头）。
   箭头区不填充颜色，描边在聚焦加粗时依然完整。 */
QComboBox {
    background-color: $surface;
    border: 1px solid $border_strong;
    border-radius: 6px;
    color: $text;
    min-height: 20px;
    padding: 5px 30px 5px 9px;
}
QComboBox:hover {
    border-color: $text_muted;
}
QComboBox:focus {
    border: 2px solid $primary;
    padding: 4px 29px 4px 8px;
}
QComboBox:disabled {
    background-color: $disabled_bg;
    border-color: $border;
    color: $disabled_text;
}
QComboBox::drop-down {
    subcontrol-origin: border;
    subcontrol-position: center right;
    width: 26px;
    border: none;
    background: transparent;
}
QComboBox::down-arrow {
    image: url($icon_chevron_down);
    height: 11px;
    width: 11px;
}
QComboBox::down-arrow:disabled {
    image: url($icon_chevron_down_muted);
}
QComboBox QAbstractItemView {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 6px;
    color: $text;
    outline: none;
    padding: 4px;
    selection-background-color: $primary_soft;
    selection-color: $primary_pressed;
}
QComboBox QAbstractItemView::item {
    min-height: 24px;
    padding: 2px 8px;
    border-radius: 4px;
}
QComboBox QAbstractItemView::item:hover {
    background-color: $surface_sunken;
    color: $text;
}
QComboBox QAbstractItemView::item:selected {
    background-color: $primary_soft;
    color: $primary_pressed;
}

/* ===== 勾选框：与输入框同一套边线，选中后填充主色 ===== */
QCheckBox, QRadioButton {
    background: transparent;
    color: $text;
    spacing: 6px;
}
QCheckBox:disabled, QRadioButton:disabled {
    color: $disabled_text;
}
QCheckBox::indicator {
    background-color: $surface;
    border: 1px solid $border_strong;
    border-radius: 4px;
    height: 15px;
    width: 15px;
}
QCheckBox::indicator:hover {
    border-color: $primary;
}
QCheckBox::indicator:checked {
    background-color: $primary;
    border-color: $primary;
    image: url($icon_check);
}
QCheckBox::indicator:checked:hover {
    background-color: $primary_hover;
    border-color: $primary_hover;
}
QCheckBox::indicator:disabled {
    background-color: $disabled_bg;
    border-color: $border;
}
QCheckBox::indicator:checked:disabled {
    background-color: $border_strong;
    border-color: $border_strong;
}

/* ===== 多行文本 ===== */
QTextEdit, QPlainTextEdit {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 6px;
    color: $text;
    padding: 6px 8px;
    selection-background-color: $primary;
    selection-color: #FFFFFF;
}
QTextEdit:focus, QPlainTextEdit:focus {
    border-color: $primary;
}
/* 实时日志：与卡片同一套浅色表面（浅灰底 + 细边 + 深色字），保留等宽字体让
   时间戳与数值对齐；它是一块“凹进去”的阅读区，不再使用深色终端配色。 */
QTextEdit#logView {
    background-color: $surface_sunken;
    border: 1px solid $border;
    border-radius: 6px;
    color: $text;
    font-family: "$font_mono";
    font-size: $font_small;
    padding: 8px 10px;
    selection-background-color: $primary;
    selection-color: #FFFFFF;
}
QTextEdit#logView:focus {
    border-color: $primary;
}
/* ===== 按钮：凸起的“操作元件”，层级由 variant 声明 =====
   默认（次要）按钮是白色面 + 极轻竖向渐变 + 稍深的下边，看上去被托起一格；
   悬停转主色描边与主色文字，按下填充浅主色并下沉。与输入框的差别因此来自表面
   质感、字重与悬停行为，而不是文字内容。 */
QPushButton {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 $button_face_top, stop:1 $button_face_bottom);
    border: 1px solid $border_strong;
    border-bottom: 2px solid $button_shadow;
    border-radius: 6px;
    color: $text;
    font-weight: 500;
    min-height: 20px;
    padding: 5px 14px 4px 14px;
}
QPushButton:hover {
    border-color: $primary;
    border-bottom-color: $primary_hover;
    color: $primary;
}
QPushButton:pressed {
    background-color: $primary_soft;
    border-color: $primary_hover;
    border-bottom-color: $primary_pressed;
    color: $primary_pressed;
}
QPushButton:focus {
    border-color: $primary;
    outline: none;
}
QPushButton[variant="primary"] {
    background-color: $primary;
    border: 1px solid $primary;
    border-bottom: 2px solid $primary_pressed;
    color: #FFFFFF;
    font-weight: 600;
}
QPushButton[variant="primary"]:hover {
    background-color: $primary_hover;
    border-color: $primary_hover;
    border-bottom-color: $primary_pressed;
    color: #FFFFFF;
}
QPushButton[variant="primary"]:pressed {
    background-color: $primary_pressed;
    border-color: $primary_pressed;
    border-bottom-color: $primary_soft_border;
    color: #FFFFFF;
}
QPushButton[variant="danger"] {
    background-color: $danger;
    border: 1px solid $danger;
    border-bottom: 2px solid $danger_pressed;
    color: #FFFFFF;
    font-weight: 600;
}
QPushButton[variant="danger"]:hover {
    background-color: $danger_hover;
    border-color: $danger_hover;
    border-bottom-color: $danger_pressed;
    color: #FFFFFF;
}
QPushButton[variant="danger"]:pressed {
    background-color: $danger_pressed;
    border-color: $danger_pressed;
    border-bottom-color: $danger_soft;
    color: #FFFFFF;
}
QPushButton[variant="success"] {
    background-color: $success;
    border: 1px solid $success;
    border-bottom: 2px solid $success_pressed;
    color: #FFFFFF;
    font-weight: 600;
}
QPushButton[variant="success"]:hover {
    background-color: $success_hover;
    border-color: $success_hover;
    border-bottom-color: $success_pressed;
    color: #FFFFFF;
}
QPushButton[variant="success"]:pressed,
QPushButton[variant="success"]:checked {
    background-color: $success_pressed;
    border-color: $success_pressed;
    border-bottom-color: $success_soft;
    color: #FFFFFF;
}
QPushButton[variant="ghost"] {
    background: transparent;
    border: 1px solid transparent;
    border-bottom: 2px solid transparent;
    color: $primary;
    font-weight: 500;
}
QPushButton[variant="ghost"]:hover {
    background-color: $primary_soft;
    border-color: $primary_soft_border;
    color: $primary_pressed;
}
QPushButton[variant="ghost"]:pressed {
    background-color: $primary_soft_border;
    color: $primary_pressed;
}
QPushButton:disabled {
    background-color: $disabled_bg;
    border-color: $border;
    border-bottom-color: $border;
    color: $disabled_text;
    font-weight: 500;
}
QPushButton[variant="primary"]:disabled,
QPushButton[variant="danger"]:disabled,
QPushButton[variant="success"]:disabled,
QPushButton[variant="ghost"]:disabled {
    background-color: $disabled_bg;
    border-color: $border;
    border-bottom-color: $border;
    color: $disabled_text;
}

/* ===== 表格 ===== */
QTableWidget, QTableView {
    alternate-background-color: $surface_alt;
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 8px;
    color: $text;
    gridline-color: $table_grid;
    selection-background-color: $primary_soft;
    selection-color: $primary_pressed;
    /* 点击单元格（含勾选框旁边的空白）会把该格设为“当前格”，Qt 随后在这格上再画一个
       圆角焦点框；与选中底色重复，看起来像一个多余的方框。选中状态已由底色表达，
       这里关掉焦点框，不再让它出现在表格里。 */
    outline: none;
}
QTableWidget::item, QTableView::item {
    border: none;
    padding: 5px 8px;
}
QTableWidget::item:selected, QTableView::item:selected {
    background-color: $primary_soft;
    color: $primary_pressed;
}
QHeaderView {
    background: transparent;
}
QHeaderView::section {
    background-color: $surface_sunken;
    border: none;
    border-bottom: 1px solid $border;
    border-right: 1px solid $table_grid;
    color: $header_text;
    font-weight: 600;
    padding: 7px 8px;
}
QTableCornerButton::section {
    background-color: $surface_sunken;
    border: none;
    border-bottom: 1px solid $border;
}

/* ===== 进度条 ===== */
QProgressBar {
    background-color: $disabled_bg;
    border: 1px solid $border;
    border-radius: 8px;
    color: $text_secondary;
    font-size: $font_small;
    max-height: 16px;
    min-height: 14px;
    text-align: center;
}
QProgressBar::chunk {
    background-color: $primary;
    border-radius: 7px;
}

/* ===== 滚动条 ===== */
QScrollBar:vertical {
    background: transparent;
    border: none;
    margin: 0;
    width: 10px;
}
QScrollBar::handle:vertical {
    background-color: $scroll;
    border-radius: 5px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background-color: $text_muted;
}
QScrollBar:horizontal {
    background: transparent;
    border: none;
    height: 10px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background-color: $scroll;
    border-radius: 5px;
    min-width: 30px;
}
QScrollBar::handle:horizontal:hover {
    background-color: $text_muted;
}
QScrollBar::add-line, QScrollBar::sub-line {
    height: 0;
    width: 0;
}
QScrollBar::add-page, QScrollBar::sub-page {
    background: transparent;
}

/* ===== 状态区 ===== */
QFrame#statusPanel {
    background-color: $surface;
    border-left: 1px solid $border;
}
/* 工作区分隔条：本身不可见，边界由状态面板的左边线给出。 */
QSplitter#workspaceSplitter {
    background: transparent;
}
QSplitter#workspaceSplitter::handle {
    background: transparent;
}
QWidget#plotPanel {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 8px;
}
QLabel#statusCaption {
    color: $text_secondary;
    font-weight: 600;
}
QLabel#freqBadge {
    background-color: $primary_soft;
    border: 1px solid $primary_soft_border;
    border-radius: 10px;
    color: $primary_pressed;
    font-weight: 600;
    padding: 3px 12px;
}
QLabel#emptyHint {
    color: $text_muted;
    font-size: $font_small;
    padding: 6px 2px;
}
"""
)


def build_stylesheet() -> str:
    """生成全局样式表；纯字符串拼装，可在没有 QApplication 时调用。"""
    return _QSS.substitute(_VALUES)


# 主题只装一次：``setStyleSheet`` 会让 Qt 给当前存活的每个控件重新 polish，
# 重复调用等于把整段已经建好的界面重算一遍（离屏测试里尤其明显）。
_THEME_APPLIED_PROPERTY = "_paThemeApplied"


def apply_theme(app: Any) -> None:
    """把主题装到应用上：固定浅色方案、基准字体、调色板与全局样式表。"""
    if app.property(_THEME_APPLIED_PROPERTY):
        return
    hints = app.styleHints()
    set_color_scheme = getattr(hints, "setColorScheme", None)
    if set_color_scheme is not None:  # Qt >= 6.8 才能主动指定配色方案
        set_color_scheme(Qt.ColorScheme.Light)
    app.setFont(base_font())
    app.setPalette(build_palette())
    app.setStyleSheet(build_stylesheet())
    app.setProperty(_THEME_APPLIED_PROPERTY, True)
