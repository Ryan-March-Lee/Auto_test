"""步骤 10：旧导出页面语义已退役，历史管理语义与门禁保持一致。

覆盖：

- 页面模块、类与装配入口统一为历史管理语义：``run_history_page.py`` /
  ``RunHistoryPage`` / ``build_run_history_page()``，旧 ``export_page`` 模块已删除且不再被
  生产包导入；步骤 18 起该模块不再登记为标签页，生产装配也不再构造它，只作为未注册的
  遗留页面模块保留，运行历史服务/仓储继续承担后台兼容与迁移；
- 页面注册表既没有 ``export`` 也没有 ``run_history`` 路由，主界面不展示历史运行标签；
- 主窗口不再构造历史运行页，也没有它的刷新入口，更不把页面的表格与按钮复制成窗口状态；
- 数据可视化页只保留 ``export(format)`` 一个导出入口，不再为每种格式额外写一份转发
  方法；历史运行页只负责运行与产物状态，不再导出（但页面未装配，界面不可达）；
- 运行时文案（页面标题、窗口标题、提示、日志与生成的报告文本）不把“导出”描述成正常
  测试流程的必经页面：扫描范围是全部运行时生产包，不是只有 ``presentation/qt``；
- 应用层导出用例保留：历史重试与外部调用仍需要 ``ResultExportUseCase``。
"""

import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
QT_ROOT = ROOT / "presentation" / "qt"
RUN_HISTORY_PAGE = QT_ROOT / "run_history_page.py"
VISUALIZATION_PAGE = QT_ROOT / "visualization_page.py"
MAIN_WINDOW = QT_ROOT / "main_window.py"
PAGES = QT_ROOT / "pages.py"
# 运行时生产包：菜单/页面标题、窗口标题、提示、日志与生成的产物文案都只在这些包里出现。
# 历史步骤文档（``docs/``）保留旧名称用于记录迁移过程，不属于运行时文案。
PRODUCTION_PACKAGES = (
    "analysis",
    "app",
    "application",
    "domain",
    "infrastructure",
    "instrument",
    "presentation",
    "launcher.py",
)
# 把“导出”说成正常流程必经步骤的措辞；用户可见文案只能把导出描述为副本/重试。
BANNED_EXPORT_FLOW_PHRASES = (
    "请先导出",
    "必须先导出",
    "导出后再",
    "打开导出页",
    "数据导出",
)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _production_sources() -> list[Path]:
    """运行时生产包里的全部 Python 源文件。"""
    sources: list[Path] = []
    for package in PRODUCTION_PACKAGES:
        path = ROOT / package
        if path.is_dir():
            sources.extend(sorted(path.rglob("*.py")))
        elif path.is_file():
            sources.append(path)
    return sources


def _method_names(path: Path) -> set[str]:
    tree = ast.parse(_source(path))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _string_literals(path: Path) -> list[str]:
    """文件里的字符串字面量，跳过模块/类/函数的文档字符串。"""
    tree = ast.parse(_source(path))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", [])
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


class ExportPageSemanticsRetirementTests(unittest.TestCase):
    """旧的“独立导出页”模块、路由与页面级适配代码都不得回流。"""

    def test_page_module_and_builder_are_named_after_history_management(self):
        self.assertFalse(
            (QT_ROOT / "export_page.py").exists(),
            "旧 export_page 模块已退役，不能重新出现",
        )
        self.assertTrue(RUN_HISTORY_PAGE.is_file())
        source = _source(RUN_HISTORY_PAGE)
        self.assertIn("class RunHistoryPage", source)
        self.assertIn("def build_run_history_page(", source)
        self.assertIn('__all__ = ["RunHistoryPage", "build_run_history_page"]', source)

    def test_production_packages_do_not_import_the_retired_module(self):
        producers = ("presentation", "application", "infrastructure", "app", "instrument")
        offenders = []
        for package in producers:
            for path in (ROOT / package).rglob("*.py"):
                text = _source(path)
                if "presentation.qt.export_page" in text or "from .export_page" in text:
                    offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [], "生产包仍在导入旧导出页: " + "; ".join(offenders))

    def test_runtime_copy_does_not_reference_the_removed_history_page(self):
        """步骤 18：用户可见文案不得再引用已移除的“历史运行页”。

        自动补生成的唯一界面触发器随历史页移除；归档摘要继续承诺“页面会自动补生成”
        就是误导。这里只看字符串字面量（跳过文档字符串），因此解释性注释可以说明移除
        事实，用户可见文案不能引用一个不存在的页面。
        """
        offenders = []
        for path in _production_sources():
            if path == RUN_HISTORY_PAGE:
                continue
            if any("历史运行页" in text for text in _string_literals(path)):
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(
            offenders,
            [],
            "历史运行页面已从界面移除，运行时文案仍在引用它: " + "; ".join(offenders),
        )

    def test_page_registry_has_no_export_or_history_route(self):
        from presentation.qt.pages import PAGE_DEFINITIONS

        keys = {definition.key for definition in PAGE_DEFINITIONS}
        self.assertNotIn("export", keys, "旧导出页路由 key 已删除")
        self.assertNotIn(
            "run_history", keys, "步骤 18 已把历史运行页从主界面移除，路由 key 不得回流"
        )
        titles = [definition.title for definition in PAGE_DEFINITIONS]
        self.assertEqual(
            [title for title in titles if "历史运行" in title or "导出" in title],
            [],
            "标签页不得再出现历史运行或导出入口",
        )

    def test_main_window_does_not_re_expose_page_state(self):
        source = _source(MAIN_WINDOW)
        self.assertNotIn("self.run_history_page", source)
        self.assertNotIn("def create_run_history_tab(self):", source)
        self.assertNotIn("def refresh_history(self):", source)
        # 页面内部表格/按钮归页面所有，窗口不再复制一份可被误用的状态。
        for attribute in (
            "self.export_page",
            "self.file_table =",
            "self.export_json_btn =",
            "self.export_csv_btn =",
            "self.export_report_btn =",
        ):
            self.assertNotIn(attribute, source)

    def test_page_only_exposes_one_export_entry(self):
        methods = _method_names(VISUALIZATION_PAGE)
        self.assertIn("export", methods)
        for duplicate in ("export_json", "export_csv", "export_report"):
            self.assertNotIn(
                duplicate,
                methods,
                "页面只保留 export(format)，不再为每种格式写转发方法",
            )
        self.assertNotIn(
            "export",
            _method_names(RUN_HISTORY_PAGE),
            "结果另存为已移到数据可视化页，历史运行页不再导出",
        )

    def test_ui_text_does_not_require_a_separate_export_page(self):
        """运行时文案门禁覆盖全部生产包：提示、日志与生成的报告文本一起查。"""
        sources = _production_sources()
        scanned = {path.relative_to(ROOT).as_posix() for path in sources}
        # 扫描范围自身要被验证：路径写错时门禁不能静默通过。
        self.assertGreater(len(sources), 100, f"文案扫描范围异常：只有 {len(sources)} 个文件")
        self.assertIn(
            "presentation/qt/run_history_page.py", scanned, "历史页必须落在扫描范围内"
        )
        self.assertIn(
            "analysis/visualization.py",
            scanned,
            "生成的报告文本也属于用户可见文案，必须被扫描",
        )
        offenders = []
        for path in sources:
            text = _source(path)
            for phrase in BANNED_EXPORT_FLOW_PHRASES:
                if phrase in text:
                    offenders.append(f"{path.relative_to(ROOT)}: {phrase}")
        self.assertEqual(offenders, [], "运行时文案仍把导出描述成必经流程: " + "; ".join(offenders))
        # 正常流程的提示必须明确“已经自动归档，无需再手动导出”。
        self.assertIn("无需再手动导出", _source(QT_ROOT / "archival_status.py"))

    def test_page_titles_and_window_title_do_not_name_an_export_page(self):
        """菜单/标签页与窗口标题是用户最先看到的入口，不得出现“导出”这一功能页。"""
        from presentation.qt.pages import PAGE_DEFINITIONS

        titles = [definition.title for definition in PAGE_DEFINITIONS]
        self.assertEqual(
            [title for title in titles if "导出" in title], [], f"页面标题: {titles}"
        )
        window_source = _source(MAIN_WINDOW)
        match = re.search(r'self\.setWindowTitle\("([^"]*)"\)', window_source)
        self.assertIsNotNone(match, "窗口必须自己设置标题，标题文案由本门禁检查")
        self.assertNotIn("导出", match.group(1))

    def test_application_export_use_case_is_retained(self):
        service = ROOT / "application" / "export_service.py"
        self.assertTrue(service.is_file(), "应用层导出用例不能被页面删除连带移除")
        source = _source(service)
        self.assertIn("class ResultExportUseCase", source)
        port_source = _source(ROOT / "application" / "ports" / "gui_data_service.py")
        self.assertIn("def execute(self, request: ExportRequest)", port_source)


if __name__ == "__main__":
    unittest.main()
