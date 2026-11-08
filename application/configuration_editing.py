"""配置编辑阶段的结构化问题与结果对象（GUI 重构步骤 3）。

配置校验、设备选择与电源分配里“用户可修正”的失败用这里的结果对象返回，不再靠异常
控制正常流程：每个问题至少带**路径**（配置文档里的字段位置）、**稳定错误码**和**面向
用户的文案**，页面只负责展示，不重新解释业务规则。

边界：

- 本模块只表达“编辑阶段的结果”，不依赖 Qt、文件路径、仪器对象或运行标识；
- 业务规则仍归 `domain`，这里不判断频率是否重复、压缩点是否受支持等规则；
- 文件、硬件和程序错误继续使用异常；只有用户输入问题走 :class:`ValidationResult`。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = ["ValidationIssue", "ValidationResult"]

_SEVERITIES = ("error", "warning")


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} 必须是字符串，收到 {type(value).__name__}")
    if not value.strip():
        raise ValueError(f"{label} 不能为空")
    return value


@dataclass(frozen=True)
class ValidationIssue:
    """一条面向用户的配置问题。

    ``path`` 是配置文档里的字段路径（如 ``test_frequencies[0]``），``code`` 是供程序
    分支判断的稳定错误码（不随文案变化），``message`` 是直接展示给用户的句子。
    ``severity`` 只区分 ``error`` 与 ``warning``：错误让 :attr:`ValidationResult.valid`
    为假，警告只提示。
    """

    path: str
    code: str
    message: str
    severity: str = "error"

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _text(self.path, "问题路径"))
        object.__setattr__(self, "code", _text(self.code, "问题错误码"))
        object.__setattr__(self, "message", _text(self.message, "问题文案"))
        if not isinstance(self.severity, str):
            raise TypeError(
                f"问题级别必须是字符串，收到 {type(self.severity).__name__}"
            )
        if self.severity not in _SEVERITIES:
            raise ValueError(
                f"问题级别必须是 {'、'.join(_SEVERITIES)} 之一，收到 {self.severity!r}"
            )

    @property
    def is_error(self) -> bool:
        """是否为错误（警告不影响配置是否有效）。"""
        return self.severity == "error"

    def describe(self) -> str:
        """返回 ``路径: 文案`` 形式的一行描述，供日志与页面列表使用。"""
        return f"{self.path}: {self.message}"


def _issue_tuple(values: Any) -> tuple[ValidationIssue, ...]:
    if isinstance(values, (str, bytes, Mapping)) or not isinstance(values, Iterable):
        raise TypeError(
            f"问题集合必须是 ValidationIssue 序列，收到 {type(values).__name__}"
        )
    issues = tuple(values)
    for index, issue in enumerate(issues):
        if not isinstance(issue, ValidationIssue):
            raise TypeError(
                f"问题集合[{index}] 必须是 ValidationIssue，"
                f"收到 {type(issue).__name__}"
            )
    return issues


@dataclass(frozen=True)
class ValidationResult:
    """一次配置编辑操作返回的全部问题。

    ``valid`` 只表示没有 ``error`` 级别的问题；``warnings`` 可以存在而配置仍然有效。
    问题顺序保持调用方给出的顺序，便于页面按字段位置稳定展示。
    """

    issues: tuple[ValidationIssue, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "issues", _issue_tuple(self.issues))

    @classmethod
    def of(cls, *issues: ValidationIssue) -> "ValidationResult":
        """按顺序构造结果；与直接传序列等价。"""
        return cls(issues=tuple(issues))

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.is_error)

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if not issue.is_error)

    @property
    def valid(self) -> bool:
        """没有错误级别的问题时为真；警告不改变有效性。"""
        return not self.errors

    @property
    def has_issues(self) -> bool:
        return bool(self.issues)

    def messages(self) -> tuple[str, ...]:
        """按 ``路径: 文案`` 列出全部问题（错误与警告都包含）。"""
        return tuple(issue.describe() for issue in self.issues)
