"""可复用配置快照的应用端口。

配置快照是用户保存/加载的配置模板，和一次实际测量的执行上下文分开。
端口只描述持久化事实，不负责配置字段校验；正式配置文档仍由
``ConfigurationAuthoringService`` 在进入端口前生成。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Protocol

from .types import PathLike


@dataclass(frozen=True)
class ConfigurationSnapshot:
    """一份可复用配置快照的持久化位置和展示信息。

    ``rack_slots`` 是仪器排布页的界面恢复信息（``supply_id -> 九宫格格位``），
    与正式配置文档分开保存：正式配置仍只由 ``configuration_path`` 指向的文件
    表达，排布不会进入运行校验、执行上下文或配置迁移。旧快照没有这段信息时为
    ``None``，界面按配置顺序补位。
    """

    display_name: str
    directory: Path
    configuration_path: Path
    metadata_path: Path
    saved_at: str
    rack_slots: Mapping[str, int] | None = None


@dataclass(frozen=True)
class ConfigurationSaveOutcome:
    """命名配置保存的结构化结果。

    ``configuration_saved`` 与 ``snapshot`` 必须分开表达：当前工作配置
    （``config.json``）与命名快照是两次持久化，前者成功、后者失败时用户需要
    看到“当前配置已保存，但快照未保存”，不能把它报告成完全失败。

    - ``configuration_saved=True, snapshot=None`` 表示部分成功；
    - ``snapshot is not None`` 表示命名快照已经提交，此时 ``error`` 必须为 ``None``；
    - 两者都为假表示本次没有写入任何配置。
    """

    configuration_saved: bool
    snapshot: ConfigurationSnapshot | None
    error: Exception | None = None

    def __post_init__(self) -> None:
        if self.snapshot is not None and self.error is not None:
            raise ValueError("命名快照成功时不能再携带失败原因")
        if self.snapshot is not None and not self.configuration_saved:
            raise ValueError("命名快照成功时当前工作配置必须已经保存")


class ConfigurationSnapshotRepository(Protocol):
    """命名配置快照的保存、加载和列举契约。"""

    def save(
        self,
        display_name: str,
        configuration: Mapping[str, Any],
        *,
        rack_slots: Mapping[str, int] | None = None,
        now: datetime | None = None,
    ) -> ConfigurationSnapshot:
        """按用户名称保存；同名快照直接覆盖其配置文件。

        ``rack_slots`` 只作为界面恢复信息随快照保存，不写进正式配置文档。
        """
        ...

    def load(self, path: PathLike) -> Mapping[str, Any]:
        """按用户明确选择的配置文件加载，不扫描最新配置。"""
        ...

    def list(self) -> tuple[ConfigurationSnapshot, ...]:
        """列出可加载的快照，最新修改的排在前面。"""
        ...


__all__ = [
    "ConfigurationSaveOutcome",
    "ConfigurationSnapshot",
    "ConfigurationSnapshotRepository",
]
