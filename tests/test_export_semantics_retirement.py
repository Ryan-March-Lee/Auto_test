"""步骤 10：旧导出页面语义已退役，历史管理语义与门禁保持一致。

覆盖：

- 页面模块、类与装配入口统一为历史管理语义：``run_history_page.py`` /
  ``RunHistoryPage`` / ``build_run_history_page()``，旧 ``export_page`` 模块已删除且不再被
  生产包导入；
- 页面路由 key 为 ``run_history``，用户可见标题仍是“历史运行”，不再保留 ``export`` key；
- 主窗口只持有页面对象，不再把页面的表格与导出按钮复制成窗口状态；
- 页面只保留 ``export(format)`` 一个导出入口，不再为每种格式额外写一份转发方法；
- 界面文案不把“导出”描述成正常测试流程的必经页面；
- 应用层导出用例保留：历史重试与外部调用仍需要 ``ResultExportUseCase``。
"""

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
QT_ROOT = ROOT / "presentation" / "qt"
RUN_HISTORY_PAGE = QT_ROOT / "run_history_page.py"
MAIN_WINDOW = QT_ROOT / "main_window.py"
PAGES = QT_ROOT / "pages.py"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _method_names(path: Path) -> set[str]:
    tree = ast.parse(_source(path))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


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

    def test_page_registry_uses_the_run_history_route_only(self):
        from presentation.qt.pages import PAGE_DEFINITIONS

        keys = {definition.key for definition in PAGE_DEFINITIONS}
        self.assertIn("run_history", keys)
        self.assertNotIn("export", keys, "旧导出页路由 key 已删除")
        titles = {definition.key: definition.title for definition in PAGE_DEFINITIONS}
        self.assertEqual(titles["run_history"], "历史运行")

    def test_main_window_does_not_re_expose_page_state(self):
        source = _source(MAIN_WINDOW)
        self.assertIn("self.run_history_page", source)
        self.assertIn("def create_run_history_tab(self):", source)
        self.assertIn("def refresh_history(self):", source)
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
        methods = _method_names(RUN_HISTORY_PAGE)
        self.assertIn("export", methods)
        for duplicate in ("export_json", "export_csv", "export_report"):
            self.assertNotIn(
                duplicate,
                methods,
                "页面只保留 export(format)，不再为每种格式写转发方法",
            )

    def test_ui_text_does_not_require_a_separate_export_page(self):
        banned = (
            "请先导出",
            "必须先导出",
            "导出后再",
            "打开导出页",
            "数据导出",
        )
        offenders = []
        for path in QT_ROOT.rglob("*.py"):
            text = _source(path)
            for phrase in banned:
                if phrase in text:
                    offenders.append(f"{path.relative_to(ROOT)}: {phrase}")
        self.assertEqual(offenders, [], "界面仍把导出描述成必经流程: " + "; ".join(offenders))
        # 正常流程的提示必须明确“已经自动归档，无需再手动导出”。
        self.assertIn("无需再手动导出", _source(QT_ROOT / "archival_status.py"))

    def test_application_export_use_case_is_retained(self):
        service = ROOT / "application" / "export_service.py"
        self.assertTrue(service.is_file(), "应用层导出用例不能被页面删除连带移除")
        source = _source(service)
        self.assertIn("class ResultExportUseCase", source)
        port_source = _source(ROOT / "application" / "ports" / "gui_data_service.py")
        self.assertIn("def execute(self, request: ExportRequest)", port_source)


if __name__ == "__main__":
    unittest.main()
