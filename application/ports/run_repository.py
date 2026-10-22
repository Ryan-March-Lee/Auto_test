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

from domain.run.models import FileReference, RunManifest


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

    def layout(self, run_id: str) -> RunLayout:
        """返回指定运行的目录布局（目录、配置快照和 manifest 的物理位置）。"""

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

    def update_manifest(
        self, run_id: str, manifest: RunManifest, *, now: datetime | None = None
    ) -> RunManifest:
        """原子写回 manifest；不隐式创建运行目录。"""

    def read_config(self, run_id: str, *, verify: bool = True) -> Any:
        """按 manifest 登记的校验值读取运行配置快照。"""

    def write_run_json(
        self, run_id: str, relative_path: str, payload: Any, *, overwrite: bool = False
    ) -> str:
        """原子写入运行目录 ``inputs/`` 或 ``results/`` 下的 JSON 文件。

        返回规范化后的相对路径；只允许在运行目录内写入，不允许覆盖运行目录外文件。
        默认 ``overwrite=False``，目标文件已存在时抛 ``FileExistsError``，避免阶段
        提交或拆分文件写入静默覆盖已有运行结果。
        """

    def register_file(
        self,
        run_id: str,
        path: str,
        *,
        stage: str | None = None,
        description: str | None = None,
        now: datetime | None = None,
    ) -> FileReference:
        """登记运行目录内已完成的文件并同步更新 manifest。"""

    def discard_file(self, run_id: str, relative_path: str) -> str:
        """删除运行目录内的文件并撤销 manifest 登记。

        用于阶段失败/取消或提交失败时清理未提交的结果文件。文件或登记不存在时
        按无操作处理并返回规范化后的相对路径。
        """

    def verify_file(self, run_id: str, reference: FileReference) -> Path:
        """校验登记文件仍位于运行目录内，且 SHA-256 和大小与引用一致。

        返回已校验文件的绝对路径；文件缺失或校验值不一致时直接失败，
        供后续阶段在消费前确认前置输入没有被替换。
        """

    def run_exists(self, run_id: str) -> bool:
        """判断指定运行目录是否完整存在并可用。"""

    def list_run_ids(self, *, include_invalid: bool = False) -> tuple[str, ...]:
        """列出运行根目录下的运行标识。"""


__all__ = ["RunLayout", "RunRepositoryPort"]
