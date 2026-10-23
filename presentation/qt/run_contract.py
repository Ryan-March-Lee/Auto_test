"""Validation shared by formal Qt measurement entry points."""

from __future__ import annotations

from typing import Any


def is_valid_prepared_run(value: Any) -> bool:
    """Return whether ``value`` exposes the complete formal GUI run contract."""
    if value is None or getattr(value, "has_run_contract", False) is not True:
        return False
    if getattr(value, "run_repository", None) is None:
        return False
    if not isinstance(getattr(value, "run_id", None), str) or not value.run_id.strip():
        return False
    if getattr(value, "run_directory", None) is None:
        return False
    return callable(getattr(value, "hardware_connection_path", None))


def require_prepared_run(value: Any, label: str) -> Any:
    """Raise a user-facing validation error for a non-contract run object."""
    if not is_valid_prepared_run(value):
        raise ValueError(f"正式{label}测量必须携带有效 PreparedRun；请先创建或重新加载运行")
    return value


__all__ = ["is_valid_prepared_run", "require_prepared_run"]
