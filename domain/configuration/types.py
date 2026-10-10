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


__all__ = ["ConfigIssue", "ConfigValidationResult"]
