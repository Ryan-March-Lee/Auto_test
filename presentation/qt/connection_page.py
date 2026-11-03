"""兼容模块：仪器设置页的旧模块名。

计划步骤 13 把页面迁移到 :mod:`presentation.qt.instrument_setup_page`，页面类与
``.ui`` 骨架改名为 ``InstrumentSetupPage`` / ``instrument_setup_page.ui``。兼容期内
``connection_page`` 与 ``ConnectionPage`` 仍然可用，但只做再导出：新代码应直接导入
新模块。迁移完成后整个模块删除，删除条件见
``docs/配置领域词汇与模块归属.md`` 的命名迁移登记。
"""

from .instrument_setup_page import ConnectionPage, InstrumentSetupPage

__all__ = ["ConnectionPage", "InstrumentSetupPage"]
