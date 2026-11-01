"""兼容转发壳：正式配置编写已迁到 :mod:`application.configuration_authoring`。

GUI 重构步骤 4 把 ``author_official_configuration()`` 的全部规则搬到 application
边界，正式配置文档只允许由
:class:`application.configuration_authoring.ConfigurationAuthoringService` 生成。
本模块只为迁移期的旧调用方保留同样的导入路径，**不含任何编写规则**，也不得新增
逻辑；新代码请直接使用 application 服务或 :class:`ConfigurationDraft`。

签名与行为保持不变（见 `docs/配置编辑行为基线.md` 4.2、4.5）：

- 输入不完整或字段非法时只生成缺少正式段的文档，由正式校验报告问题；
- 输入对象不被修改，返回新的文档副本；
- ``schema_version``、``test_plan``、``run_mapping`` 由服务补齐。
"""

from __future__ import annotations

from application.configuration_authoring import (
    ConfigurationAuthoringError,
    ConfigurationEditingDocument,
    ConfigurationAuthoringService,
    DUT_CHANNEL_ROLES,
    OFFICIAL_SCHEMA_VERSION,
    author_official_configuration,
    parse_db_value,
)

__all__ = [
    "ConfigurationAuthoringError",
    "ConfigurationAuthoringService",
    "ConfigurationEditingDocument",
    "DUT_CHANNEL_ROLES",
    "OFFICIAL_SCHEMA_VERSION",
    "author_official_configuration",
    "parse_db_value",
]
