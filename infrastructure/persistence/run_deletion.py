"""历史运行的删除适配器：整目录删除与边界校验。

只读历史浏览由 :class:`infrastructure.persistence.run_history_repository.
FileRunHistoryRepository` 承担；本模块单独提供删除能力，避免查询/导出对象获得写
权限。删除只作用于 ``runs/<run_id>/`` 一个目录：

- ``run_id`` 先经过运行布局校验，不允许路径穿越；
- 符号链接或目录联接（解析后与目录本身不一致）一律拒绝，防止顺着重解析点删到
  运行根目录之外；
- 目录存在且 manifest 可解析时再次确认运行不是 ``running``，缩小“页面选中后运行
  才启动”的竞态窗口；
- 实际删除委托 :meth:`RunRepositoryPort.discard_run`，不触碰其它运行、全局配置或
  兼容结果；目录已不存在时按幂等无操作处理。
"""

from __future__ import annotations

import os
from pathlib import Path

from application.ports.run_repository import RunRepositoryPort
from domain.run.models import RunManifest, RunStatus


def _is_within(root: Path, resolved: Path) -> bool:
    """按平台大小写规则判断 ``resolved`` 是否严格位于 ``root`` 内。"""
    root_text = os.path.normcase(str(root))
    target_text = os.path.normcase(str(resolved))
    return target_text.startswith(root_text.rstrip(os.sep) + os.sep)


class FileRunDeletion:
    """删除单个已登记运行的目录（用户明确发起的维护动作）。"""

    def __init__(self, *, runs: RunRepositoryPort):
        self.runs = runs

    def delete_run(self, run_id: str) -> None:
        layout = self.runs.layout(run_id)
        directory = layout.run_directory
        if not directory.exists():
            return
        if not directory.is_dir():
            raise NotADirectoryError(f"运行路径不是目录，拒绝删除: {directory}")
        root = layout.root.resolve()
        expected = root / run_id
        resolved = directory.resolve()
        if (
            directory.is_symlink()
            or os.path.normcase(str(resolved))
            != os.path.normcase(str(expected))
            or resolved == root
            or not _is_within(root, resolved)
        ):
            raise ValueError(f"运行目录是符号链接或越出运行根目录，拒绝删除: {run_id}")
        manifest = self._manifest_or_none(run_id)
        if manifest is not None and manifest.status is RunStatus.RUNNING:
            raise ValueError(f"运行 {run_id} 正在进行中，不能删除")
        self.runs.discard_run(run_id)

    def _manifest_or_none(self, run_id: str) -> RunManifest | None:
        """读取 manifest 做删除前复核；缺失或损坏的运行目录仍允许删除。"""
        try:
            return self.runs.load_manifest(run_id)
        except (OSError, ValueError):
            return None


__all__ = ["FileRunDeletion"]
