"""Validation shared by formal Qt measurement entry points."""

from __future__ import annotations

from typing import Any

from application.run_service import has_run_contract


def is_valid_prepared_run(value: Any) -> bool:
    """Return whether ``value`` exposes the complete formal GUI run contract.

    判据本身只有一份实现：:func:`application.run_service.has_run_contract`
    （运行标识 + 运行目录 + 运行仓储 + 冻结快照解析入口）。本函数只作为 GUI 边界的
    正式名称，不再自己复制一套检查，避免两个入口对“是否有运行契约”给出不同答案。
    """
    return has_run_contract(value)


def require_prepared_run(value: Any, label: str) -> Any:
    """Raise a user-facing validation error for a non-contract run object."""
    if not has_run_contract(value):
        raise ValueError(f"正式{label}必须携带有效 PreparedRun；请先创建或重新加载运行")
    return value


__all__ = ["is_valid_prepared_run", "require_prepared_run"]
