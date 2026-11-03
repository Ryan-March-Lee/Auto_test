"""兼容模块：配置页面的旧模块名。

计划步骤 13 把配置页面迁移到 :mod:`presentation.qt.configuration_page`，页面类
改名为 :class:`~presentation.qt.configuration_page.ConfigurationPage`。兼容期内
``config_page`` 与 ``ConfigPage`` 仍然可用，但只做再导出：新代码应直接导入新模块，
本模块不再承载实现。迁移完成后整个模块删除，删除条件见
``docs/配置领域词汇与模块归属.md`` 的命名迁移登记。
"""

from .configuration_page import (
    ConfigBackfillError,
    ConfigPage,
    ConfigurationPage,
)

__all__ = [
    "ConfigBackfillError",
    "ConfigPage",
    "ConfigurationPage",
]
