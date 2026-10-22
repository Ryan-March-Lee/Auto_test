"""运行目录仓储在应用层的端口契约。

应用层只通过本端口创建运行、读取配置快照并查询运行状态，具体的目录布局、
原子写入和校验值计算由基础设施适配器
:class:`infrastructure.persistence.run_repository.RunRepository` 实现。

这样应用层无需感知 ``runs/<run_id>/`` 的物理布局，也不会反向依赖基础设施。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Protocol

from domain.run.models import RunManifest


class RunLayout(Protocol):
    """运行目录布局在应用层的最小视图。"""

    @property
    def run_id(self) -> str: ...

    @property
    def run_directory(self) -> Path: ...

    @property
    def config_path(self) -> Path: ...


class RunRepositoryPort(Protocol):
    """应用层创建和读取一次运行所需的最小仓储接口。"""

    def create_run(
        self,
        config: Mapping[str, Any],
        *,
        run_id: str | None = None,
        software_version: str | None = None,
        notes: str | None = None,
        product_name: str | None = None,
        now: datetime | None = None,
    ) -> tuple[RunLayout, RunManifest]:
        """创建唯一运行目录并写入不可变的 ``config.json`` 和初始 manifest。"""

    def load_manifest(self, run_id: str) -> RunManifest:
        """读取并校验指定运行的 manifest。"""

    def read_config(self, run_id: str, *, verify: bool = True) -> Any:
        """按 manifest 登记的校验值读取运行配置快照。"""

    def run_exists(self, run_id: str) -> bool:
        """判断指定运行目录是否完整存在并可用。"""

    def list_run_ids(self, *, include_invalid: bool = False) -> tuple[str, ...]:
        """列出运行根目录下的运行标识。"""


__all__ = ["RunLayout", "RunRepositoryPort"]
