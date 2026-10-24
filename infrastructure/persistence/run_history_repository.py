"""只读运行历史仓储：按 manifest 浏览运行目录内已登记的阶段结果。

该适配器把运行目录（``runs/<run_id>/``）作为历史浏览的数据源：

- ``list_runs``：枚举运行根目录下的运行，读取 manifest 元数据并给出可用状态
  （可用 / 结构不完整 / 损坏），不扫描全局结果目录。
- ``list_results``：按 manifest 登记返回阶段结果元数据，只暴露结果标识和展示
  元数据，不返回文件系统路径。
- ``load`` / ``read_bytes``：按 ``run_id`` 与结果标识读取结果，先要求该标识已被
  当前运行 manifest 登记，再校验 SHA-256 与大小，最后确认文件仍在运行目录内；
  未登记、被替换或越界的读取一律拒绝。

本适配器只暴露读取能力：不写入、不删除、不移动运行目录或结果文件，也不把结果
登记进任何 manifest。正式测量输入仍由
:class:`infrastructure.persistence.result_repository.FileMeasurementResultRepository`
配合当前运行 manifest 提供。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from application.ports.run_history import (
    RunHistoryEntry,
    RunHistoryRepository,
    RunHistoryState,
    RunStageResultEntry,
    result_type_for_result,
)
from application.ports.run_repository import RunRepositoryPort
from domain.run.models import (
    FileReference,
    RunManifest,
    StageStatus,
    normalize_relative_path,
)
from infrastructure.filesystem.atomic_json import read_json
from infrastructure.persistence.run_directory import (
    file_sha256_and_size,
    load_run_manifest,
)


_FINISHED_STAGE_STATUSES = frozenset({StageStatus.COMPLETED, StageStatus.SKIPPED})
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _parse_moment(value: str | None, *, fallback: datetime) -> datetime:
    """把 manifest 的 UTC 时间戳解析为带时区的 ``datetime``；失败时回退。"""
    if not value:
        return fallback
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return fallback
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def _directory_modified_at(directory: Path) -> datetime:
    """运行目录自身的修改时间，作为 manifest 时间不可用时的回退。"""
    try:
        return datetime.fromtimestamp(directory.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return _EPOCH


class FileRunHistoryRepository(RunHistoryRepository):
    """运行目录内已登记阶段结果的只读历史视图。"""

    def __init__(self, *, runs: RunRepositoryPort):
        self.runs = runs

    # -- 列表 ---------------------------------------------------------------

    def list_runs(self) -> tuple[RunHistoryEntry, ...]:
        entries = [
            self._describe(run_id)
            for run_id in self.runs.list_run_ids(include_invalid=True)
        ]
        return tuple(
            sorted(
                entries,
                key=lambda entry: (entry.created_at, entry.run_id),
                reverse=True,
            )
        )

    def list_results(self, run_id: str) -> tuple[RunStageResultEntry, ...]:
        manifest = self._manifest(run_id)
        references = _reference_index(manifest)
        results: list[RunStageResultEntry] = []
        seen: set[str] = set()
        for stage, record in manifest.stages.items():
            if record.result is None or record.result in seen:
                continue
            seen.add(record.result)
            results.append(self._entry(manifest, references, record.result, stage))
        for reference in manifest.outputs:
            if reference.path in seen:
                continue
            seen.add(reference.path)
            results.append(self._entry(manifest, references, reference.path, None))
        return tuple(results)

    # -- 读取 ---------------------------------------------------------------

    def load(self, run_id: str, result_id: str) -> Mapping[str, Any]:
        target = self._verified_target(run_id, result_id)
        payload = read_json(target)
        if not isinstance(payload, Mapping):
            raise ValueError(f"运行结果根节点必须是 JSON 对象: {result_id}")
        return payload

    def read_bytes(self, run_id: str, result_id: str) -> bytes:
        target = self._verified_target(run_id, result_id)
        return target.read_bytes()

    # -- 内部工具 -----------------------------------------------------------

    def _manifest(self, run_id: str) -> RunManifest:
        """直接读取运行目录内的 manifest，而不要求运行结构完整。

        历史浏览需要能看到配置快照或目录缺失的“结构不完整运行”，因此不能走
        ``RunRepository.load_manifest``（它会因结构不完整直接失败）。
        """
        return load_run_manifest(self.runs.layout(run_id).manifest_path)

    def _describe(self, run_id: str) -> RunHistoryEntry:
        directory = self.runs.layout(run_id).run_directory
        fallback = _directory_modified_at(directory)
        try:
            manifest = self._manifest(run_id)
        except (OSError, ValueError) as error:
            return RunHistoryEntry(
                run_id=run_id,
                created_at=fallback,
                status="invalid",
                state=RunHistoryState.INVALID,
                problem=f"manifest 缺失或无法解析: {error}",
            )
        created_at = _parse_moment(manifest.created_at, fallback=fallback)
        if manifest.run_id != run_id:
            return RunHistoryEntry(
                run_id=run_id,
                created_at=created_at,
                status=manifest.status.value,
                state=RunHistoryState.INVALID,
                problem="manifest.run_id 与运行目录名不一致",
            )
        finished = sum(
            1
            for record in manifest.stages.values()
            if record.status in _FINISHED_STAGE_STATUSES
        )
        result_count = len(_result_paths(manifest))
        if not self.runs.run_exists(run_id):
            return RunHistoryEntry(
                run_id=run_id,
                created_at=created_at,
                status=manifest.status.value,
                state=RunHistoryState.INCOMPLETE,
                total_stages=len(manifest.stages),
                finished_stages=finished,
                result_count=result_count,
                problem="运行目录结构不完整（缺少配置快照或 inputs/results 目录）",
            )
        return RunHistoryEntry(
            run_id=run_id,
            created_at=created_at,
            status=manifest.status.value,
            state=RunHistoryState.USABLE,
            total_stages=len(manifest.stages),
            finished_stages=finished,
            result_count=result_count,
        )

    def _entry(
        self,
        manifest: RunManifest,
        references: Mapping[str, FileReference],
        relative_path: str,
        stage: str | None,
    ) -> RunStageResultEntry:
        reference = references.get(relative_path)
        target = self._join(manifest.run_id, relative_path)
        fallback = _parse_moment(
            manifest.created_at,
            fallback=_directory_modified_at(
                self.runs.layout(manifest.run_id).run_directory
            ),
        )
        if target.is_file():
            stat = target.stat()
            modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            size_bytes: int | None = stat.st_size
        else:
            modified_at = _parse_moment(
                reference.created_at if reference is not None else None,
                fallback=fallback,
            )
            size_bytes = reference.size_bytes if reference is not None else None
        return RunStageResultEntry(
            run_id=manifest.run_id,
            result_id=relative_path,
            stage=stage,
            result_type=result_type_for_result(stage, relative_path),
            modified_at=modified_at,
            size_bytes=size_bytes,
        )

    def _verified_target(self, run_id: str, result_id: str) -> Path:
        """解析并校验结果标识，返回运行目录内的绝对路径。

        拒绝绝对路径、``..`` 片段和空标识；要求结果已被当前运行 manifest 登记；
        再校验文件存在且 SHA-256 与大小一致。任一条件不满足都直接失败，绝不
        跨运行或越界读取。
        """
        normalized = normalize_relative_path(result_id, field_name="历史结果标识")
        manifest = self._manifest(run_id)
        reference = _reference_index(manifest).get(normalized)
        if reference is None:
            raise ValueError(f"结果未登记到当前运行 manifest: {normalized}")
        target = self._join(run_id, normalized)
        # 边界再确认：解析后的文件必须仍在当前运行目录内。
        self.runs.layout(run_id).relative_path(target)
        if not target.is_file():
            raise ValueError(f"运行结果文件不存在或不是普通文件: {normalized}")
        digest, size = file_sha256_and_size(target)
        if reference.sha256 is not None and digest != reference.sha256:
            raise ValueError(f"运行结果 SHA-256 与 manifest 不一致: {normalized}")
        if reference.size_bytes is not None and size != reference.size_bytes:
            raise ValueError(f"运行结果大小与 manifest 不一致: {normalized}")
        return target

    def _join(self, run_id: str, relative_path: str) -> Path:
        layout = self.runs.layout(run_id)
        return layout.run_directory / Path(*relative_path.split("/"))


def _reference_index(manifest: RunManifest) -> dict[str, FileReference]:
    references: dict[str, FileReference] = {}
    for reference in (*manifest.inputs, *manifest.outputs):
        references.setdefault(reference.path, reference)
    return references


def _result_paths(manifest: RunManifest) -> tuple[str, ...]:
    paths: list[str] = []
    seen: set[str] = set()
    for record in manifest.stages.values():
        if record.result is not None and record.result not in seen:
            seen.add(record.result)
            paths.append(record.result)
    for reference in manifest.outputs:
        if reference.path not in seen:
            seen.add(reference.path)
            paths.append(reference.path)
    return tuple(paths)


__all__ = ["FileRunHistoryRepository"]
