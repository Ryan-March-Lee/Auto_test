"""Page registration boundary for the existing main-window pages.

The legacy window still owns the widgets during the transition.  Keeping the
page list here makes the ownership explicit and gives later page extraction a
single integration point without changing the user workflow in this stage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Any


@dataclass(frozen=True)
class PageDefinition:
    key: str
    title: str
    builder: Callable[[Any], Any]


PAGE_DEFINITIONS = (
    PageDefinition("configuration", "仪器配置", lambda window: window.create_config_tab()),
    PageDefinition("cable_loss", "线损测量", lambda window: window.create_cable_loss_tab()),
    PageDefinition("driver_mapping", "驱动映射", lambda window: window.create_driver_mapping_tab()),
    PageDefinition("amplifier", "功放测试", lambda window: window.create_amplifier_test_tab()),
    PageDefinition("visualization", "数据可视化", lambda window: window.create_visualization_tab()),
    PageDefinition("export", "数据导出", lambda window: window.create_data_export_tab()),
)


def build_pages(window: Any) -> None:
    """Build the pages owned by *window* in the stable user-facing order."""
    for page in PAGE_DEFINITIONS:
        page.builder(window)


__all__ = ["PageDefinition", "PAGE_DEFINITIONS", "build_pages"]
