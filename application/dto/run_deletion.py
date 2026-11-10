"""历史运行删除的正式 DTO：批量删除结果与逐项失败原因。

删除是一次**用户明确发起的维护动作**，不改变历史浏览与导出端口的只读契约：

- 用例只接受历史列表中的运行标识，进行中的运行不允许删除；
- 成功与失败按运行逐项返回，同一批次中的失败不会中断其它运行；
- 失败原因由应用层或基础设施给出纯文本，页面据此生成用户文案。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RunDeletionFailure:
    """一次运行未能删除的原因。"""

    run_id: str
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ValueError("删除失败的运行标识必须是非空字符串")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("删除失败原因必须是非空字符串")


@dataclass(frozen=True)
class RunDeletionOutcome:
    """一次批量删除的结果：已删除的运行与逐项失败。"""

    deleted_run_ids: tuple[str, ...] = ()
    failures: tuple[RunDeletionFailure, ...] = ()

    def __post_init__(self) -> None:
        deleted = _run_ids(self.deleted_run_ids, "deleted_run_ids")
        failures = tuple(self.failures)
        if not all(isinstance(item, RunDeletionFailure) for item in failures):
            raise ValueError("failures 中的每项必须是 RunDeletionFailure")
        failed_ids = [item.run_id for item in failures]
        if len(set(failed_ids)) != len(failed_ids):
            raise ValueError("同一运行的删除失败只能记录一次")
        if set(deleted) & set(failed_ids):
            raise ValueError("同一运行不能同时出现在已删除与失败列表中")
        object.__setattr__(self, "deleted_run_ids", deleted)
        object.__setattr__(self, "failures", failures)

    @property
    def deleted_count(self) -> int:
        return len(self.deleted_run_ids)

    @property
    def failed_count(self) -> int:
        return len(self.failures)

    @property
    def has_failures(self) -> bool:
        return bool(self.failures)


def _run_ids(value: object, field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} 必须是运行标识序列")
    records = tuple(value)
    if not all(
        isinstance(item, str) and item.strip() for item in records
    ):
        raise ValueError(f"{field_name} 中的每个运行标识必须是非空字符串")
    if len(set(records)) != len(records):
        raise ValueError(f"{field_name} 不能包含重复的运行标识")
    return records


__all__ = ["RunDeletionFailure", "RunDeletionOutcome"]
