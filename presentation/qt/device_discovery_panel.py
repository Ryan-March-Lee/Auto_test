"""兼容模块：仪器发现面板的旧模块名。

计划步骤 13 把面板迁移到 :mod:`presentation.qt.instrument_discovery_panel`，面板类
改名为 :class:`~presentation.qt.instrument_discovery_panel.InstrumentDiscoveryPanel`，
发牌字段由 ``deal_layer`` 改名为 ``deal_animation``。兼容期内旧模块名、旧类名与旧字段
名仍然可用，但本模块只做再导出：新代码应直接导入新模块。迁移完成后整个模块删除，
删除条件见 ``docs/配置领域词汇与模块归属.md`` 的命名迁移登记。

注意：测试或调用方若需要替换模块级协作对象（例如 ``CardDealAnimation``），必须对
``presentation.qt.instrument_discovery_panel`` 打补丁；对这里打补丁只会改到兼容模块
自己的名字，不会影响面板实现。
"""

from . import instrument_discovery_panel as _implementation
from .instrument_discovery_panel import *  # noqa: F401,F403

__all__ = list(_implementation.__all__)
