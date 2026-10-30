"""配置校验结果的领域值对象。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class ConfigIssue:
    """单个配置问题。"""

    level: str
    path: str
    message: str


@dataclass(frozen=True)
class ConfigValidationResult:
    """配置校验结果。"""

    errors: List[ConfigIssue]
    warnings: List[ConfigIssue]

    @property
    def valid(self) -> bool:
        return not self.errors

    def messages(self) -> List[str]:
        """按 ``“<path>: <message>”`` 列出全部问题，供界面与日志逐条显示。"""
        return [f"{issue.path}: {issue.message}" for issue in (*self.errors, *self.warnings)]


__all__ = ["ConfigIssue", "ConfigValidationResult"]
