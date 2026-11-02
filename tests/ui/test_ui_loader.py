"""步骤 11：Qt Designer ``.ui`` 只承担静态结构与页面本地几何，主题语义仍由设计令牌约束。

这一组用例不启动主窗口：它检查 ``.ui`` 文件能被加载、控件层级与静态文案就位，并守住
三条边界：

- ``.ui`` 不写颜色、字体、内联样式或固定尺寸；
- ``.ui`` 声明的语义属性取值都在 :mod:`presentation.qt.theme` 的允许集合内，并且
  每个取值在样式表里确有对应规则（默认按钮层级除外，它使用基础 ``QPushButton`` 规则）；
- 页面名不能逃出 ``presentation/qt/ui/``。

视觉调整因此只改 ``.ui`` 或 :mod:`presentation.qt.theme`，不会回落到页面业务模块。
"""

import os
import unittest
import xml.etree.ElementTree as ElementTree

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QPushButton,
    QScrollArea,
    QWidget,
)

from presentation.qt.theme import (
    BUTTON_VARIANTS,
    DEFAULT_BUTTON_VARIANT,
    LABEL_ROLES,
    TONE_NAMES,
    build_stylesheet,
)
from presentation.qt.ui_loader import UI_DIRECTORY, UiLoadError, load_ui, ui_file

#: ``.ui`` 里只允许出现这些属性；颜色、字体、内联样式与固定尺寸必须留给主题。
FORBIDDEN_UI_PROPERTIES = frozenset(
    {
        "styleSheet",
        "font",
        "palette",
        "foregroundBrush",
        "backgroundBrush",
        "maximumSize",
        "minimumSize",
        "fixedSize",
    }
)
#: 颜色、画刷、渐变与字体只能由主题样式表生成，``.ui`` 不得内联这些 XML 节点。
FORBIDDEN_UI_NODES = frozenset({"color", "brush", "gradient", "font"})
#: 语义属性 -> 主题模块允许的取值表。
SEMANTIC_PROPERTIES = {
    "variant": BUTTON_VARIANTS,
    "role": LABEL_ROLES,
    "tone": TONE_NAMES,
}


def _property_value(property_node):
    """Qt Designer 把字符串值放在 ``<string>`` 子节点里，这里取实际文本。"""
    text = (property_node.text or "").strip()
    if text:
        return text
    for child in property_node:
        child_text = (child.text or "").strip()
        if child_text:
            return child_text
    return ""


class UiLoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_connection_page_ui_loads_its_static_skeleton(self):
        ui = load_ui("connection_page")

        self.assertEqual(ui.objectName(), "connectionPageUi")
        title = ui.findChild(QLabel, "connectionPageTitle")
        self.assertIsNotNone(title)
        self.assertEqual(title.text(), "仪表配置")
        self.assertEqual(title.property("role"), "pageTitle")

        hint = ui.findChild(QLabel, "actionHint")
        self.assertIsNotNone(hint)
        self.assertIn("下一步", hint.text())
        self.assertEqual(hint.property("role"), "hint")

        scroll = ui.findChild(QScrollArea, "connectionScroll")
        self.assertIsNotNone(scroll)
        self.assertTrue(scroll.widgetResizable())
        self.assertIsNotNone(ui.findChild(QWidget, "connectionContent"))
        self.assertIsNotNone(ui.findChild(QWidget, "discoveryPanelHost"))

        next_btn = ui.findChild(QPushButton, "nextButton")
        return_btn = ui.findChild(QPushButton, "returnWelcomeButton")
        self.assertEqual(next_btn.text(), "下一步")
        self.assertEqual(return_btn.text(), "返回欢迎页")
        self.assertEqual(next_btn.property("variant"), "primary")
        self.assertEqual(return_btn.property("variant"), "secondary")

    def test_ui_files_declare_no_colors_fonts_styles_or_fixed_sizes(self):
        """结构在 ``.ui``，外观与尺寸令牌在 ``theme.py``：两边不各自维护一套视觉常量。"""
        files = sorted(UI_DIRECTORY.rglob("*.ui"))
        self.assertTrue(files, "至少应有一个 Qt Designer 静态布局文件")

        violations = []
        for path in files:
            relative = path.relative_to(UI_DIRECTORY).as_posix()
            document = ElementTree.parse(path).getroot()
            for property_node in document.iter("property"):
                name = property_node.get("name")
                if name in FORBIDDEN_UI_PROPERTIES:
                    violations.append(f"{relative}: 不得声明 {name}")
                if name in SEMANTIC_PROPERTIES:
                    value = _property_value(property_node)
                    if value not in SEMANTIC_PROPERTIES[name]:
                        violations.append(
                            f"{relative}: {name}={value!r} 不在主题允许的取值内"
                        )
            for tag in sorted(FORBIDDEN_UI_NODES):
                for _node in document.iter(tag):
                    violations.append(f"{relative}: 不得内联 {tag}")
        self.assertEqual(violations, [], "；".join(violations))

    def test_semantic_properties_have_matching_theme_rules(self):
        """语义取值表必须与样式表同步：否则 ``.ui`` 声明了一个不会被渲染的角色。"""
        stylesheet = build_stylesheet()
        for name, values in SEMANTIC_PROPERTIES.items():
            for value in values:
                if name == "variant" and value == DEFAULT_BUTTON_VARIANT:
                    self.assertIn(
                        "QPushButton {",
                        stylesheet,
                        "默认按钮层级必须由基础 QPushButton 规则承接",
                    )
                    continue
                with self.subTest(property=name, value=value):
                    self.assertIn(
                        f'[{name}="{value}"]',
                        stylesheet,
                        f"{name}={value!r} 没有对应的主题规则",
                    )

    def test_missing_ui_file_is_a_load_error(self):
        self.assertFalse(ui_file("not_a_real_page").exists())
        with self.assertRaises(UiLoadError) as raised:
            load_ui("not_a_real_page")
        self.assertIn("not_a_real_page.ui", str(raised.exception))

    def test_page_names_cannot_escape_the_ui_directory(self):
        for name in (
            "../connection_page",
            "connection_page/../../outside",
            "ConnectionPage",
            "connection-page",
            "",
            ".hidden",
        ):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    ui_file(name)


if __name__ == "__main__":
    unittest.main()
