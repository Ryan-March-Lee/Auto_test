"""只读运行历史仓储：按 manifest 浏览运行目录内已登记的阶段结果。

该适配器把运行目录（``runs/<run_id>/``）作为历史浏览的数据源：

- ``list_runs``：枚举运行根目录下的运行，读取 manifest 元数据并给出可用状态
  （可用 / 结构不完整 / 损坏 / 符号链接），不扫描全局结果目录。
- ``list_run_summaries``：同一批运行的完整只读摘要（运行状态、阶段状态与交付产物
  状态），历史管理页只需这一份结构化事实，不再各自读 manifest。
- ``list_results``：按 manifest 登记返回**阶段结果**元数据，只暴露结果标识和展示
  元数据，不返回文件系统路径；硬件配置等非阶段结果输入不会出现在历史结果里。
- ``load`` / ``read_bytes``：按 ``run_id`` 与结果标识读取结果，先要求该标识是当前
  运行 manifest 登记的阶段结果，再校验 SHA-256 与大小，最后确认文件仍在运行目录内；
  未登记、非阶段结果、被替换或越界的读取一律拒绝。

运行目录边界：拒绝作为符号链接的运行目录（避免 ``run_directory.resolve()`` 被链接
指向运行根目录之外后仍通过“位于运行目录内”的校验），并对结果文件再次确认其解析后
仍在运行目录内。

本适配器只暴露读取能力：不写入、不删除、不移动运行目录或结果文件，也不把结果
登记进任何 manifest。正式测量输入仍由
:class:`infrastructure.persistence.result_repository.FileMeasurementResultRepository`
配合当前运行 manifest 提供。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from application.dto.run_history import (
    RunArchiveState,
    RunArtifactSummary,
    RunHistorySummary,
    RunStageSummary,
)
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


def _is_within(root: Path, resolved: Path) -> bool:
    """按平台大小写规则判断 ``resolved`` 是否严格位于 ``root`` 内。"""
    root_text = os.path.normcase(str(root))
    target_text = os.path.normcase(str(resolved))
    return target_text.startswith(root_text.rstrip(os.sep) + os.sep)


def _result_entries(manifest: RunManifest) -> tuple[tuple[str, str | None], ...]:
    """运行**阶段结果**的 (相对路径, 阶段名) 列表，保持稳定顺序。

    只纳入真正属于阶段结果的路径：

    - 每个 ``completed`` 阶段登记的 ``record.result``；
    - 登记到某个已知阶段名下的 ``outputs``（主功放拆分结果文件）。

    ``inputs/hardware_config.json`` 等非阶段结果输入不在其中，因此不能被当作
    历史结果读取或导出。
    """
    entries: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for stage, record in manifest.stages.items():
        if record.result is not None and record.result not in seen:
            seen.add(record.result)
            entries.append((record.result, stage))
    for reference in manifest.outputs:
        if reference.path in seen:
            continue
        if reference.stage is not None and reference.stage in manifest.stages:
            seen.add(reference.path)
            entries.append((reference.path, reference.stage))
    return tuple(entries)


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

    def list_run_summaries(self) -> tuple[RunHistorySummary, ...]:
        """返回全部运行的只读摘要（运行/阶段/产物状态），按创建时间从新到旧。"""
        summaries = [
            self._summary(run_id)
            for run_id in self.runs.list_run_ids(include_invalid=True)
        ]
        return tuple(
            sorted(
                summaries,
                key=lambda item: (item.created_at, item.run_id),
                reverse=True,
            )
        )

    def list_results(self, run_id: str) -> tuple[RunStageResultEntry, ...]:
        manifest = self._manifest(run_id)
        references = _reference_index(manifest)
        return tuple(
            self._entry(manifest, references, path, stage)
            for path, stage in _result_entries(manifest)
        )

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

    def _run_directory(self, run_id: str) -> Path:
        """返回运行目录，并拒绝符号链接或越出运行根目录的目录。

        ``layout.relative_path`` 以 ``run_directory.resolve()`` 为边界；若运行目录
        本身是符号链接/联接，``resolve()`` 会跳到运行根目录之外，使“位于运行目录内”
        的校验失效。因此这里以“解析后的运行目录必须严格位于解析后的运行根目录内”
        为准，可同时覆盖符号链接与 Windows 目录联接（junction），绝不跟随越界的
        重解析点。
        """
        layout = self.runs.layout(run_id)
        directory = layout.run_directory
        root = layout.root.resolve()
        resolved = directory.resolve()
        if directory.is_symlink() or resolved == root or not _is_within(root, resolved):
            raise ValueError(
                f"运行目录是符号链接或越出运行根目录，拒绝作为历史结果: {run_id}"
            )
        return directory

    def _manifest(self, run_id: str) -> RunManifest:
        """直接读取运行目录内的 manifest，而不要求运行结构完整。

        历史浏览需要能看到配置快照或目录缺失的“结构不完整运行”，因此不能走
        ``RunRepository.load_manifest``（它会因结构不完整直接失败）。符号链接
        运行目录在这里同样被拒绝。
        """
        directory = self._run_directory(run_id)
        return load_run_manifest(directory / "manifest.json")

    def _describe(self, run_id: str) -> RunHistoryEntry:
        inspection = self._inspect(run_id)
        manifest = inspection.manifest
        if inspection.state is RunArchiveState.INVALID or manifest is None:
            return RunHistoryEntry(
                run_id=run_id,
                created_at=inspection.created_at,
                status=inspection.status,
                state=RunHistoryState.INVALID,
                problem=inspection.problem,
            )
        return RunHistoryEntry(
            run_id=run_id,
            created_at=inspection.created_at,
            status=inspection.status,
            state=RunHistoryState(inspection.state.value),
            total_stages=len(manifest.stages),
            finished_stages=sum(
                1
                for record in manifest.stages.values()
                if record.status in _FINISHED_STAGE_STATUSES
            ),
            result_count=len(_result_entries(manifest)),
            problem=inspection.problem,
        )

    def _summary(self, run_id: str) -> RunHistorySummary:
        """把 manifest 的事实映射成历史管理页需要的结构化摘要。

        ``invalid`` 运行（目录越界、manifest 缺失/损坏、run_id 与目录名不一致）只给出
        可用性与阻塞原因，不携带阶段或产物记录：损坏的 manifest 不能当作状态来源。
        """
        inspection = self._inspect(run_id)
        manifest = inspection.manifest
        if inspection.state is RunArchiveState.INVALID or manifest is None:
            return RunHistorySummary(
                run_id=run_id,
                created_at=inspection.created_at,
                state=RunArchiveState.INVALID,
                problem=inspection.problem,
            )
        return RunHistorySummary(
            run_id=run_id,
            created_at=inspection.created_at,
            state=inspection.state,
            run_status=manifest.status,
            stages=tuple(
                RunStageSummary(
                    name=record.name,
                    status=record.status,
                    result_id=record.result,
                )
                for record in manifest.stages.values()
            ),
            artifacts=tuple(
                RunArtifactSummary(
                    kind=record.kind,
                    status=record.status,
                    source_result=record.source_result,
                    stage=_stage_for_result(manifest, record.source_result),
                    error=record.error,
                )
                for record in manifest.artifacts
            ),
            problem=inspection.problem,
        )

    def _inspect(self, run_id: str) -> "_RunInspection":
        """统一判定一次运行的可用性：目录边界、manifest 可读性与结构完整度。

        ``list_runs`` 与 ``list_run_summaries`` 共用这一份判定，避免两种历史视图对
        “这个运行能不能用”给出不同答案。
        """
        directory = self.runs.layout(run_id).run_directory
        fallback = _directory_modified_at(directory)
        try:
            self._run_directory(run_id)
        except ValueError as error:
            return _RunInspection(
                created_at=fallback,
                state=RunArchiveState.INVALID,
                status="invalid",
                problem=str(error),
            )
        try:
            manifest = self._manifest(run_id)
        except (OSError, ValueError) as error:
            return _RunInspection(
                created_at=fallback,
                state=RunArchiveState.INVALID,
                status="invalid",
                problem=f"manifest 缺失或无法解析: {error}",
            )
        created_at = _parse_moment(manifest.created_at, fallback=fallback)
        if manifest.run_id != run_id:
            return _RunInspection(
                created_at=created_at,
                state=RunArchiveState.INVALID,
                status=manifest.status.value,
                manifest=manifest,
                problem="manifest.run_id 与运行目录名不一致",
            )
        if not self.runs.run_exists(run_id):
            return _RunInspection(
                created_at=created_at,
                state=RunArchiveState.INCOMPLETE,
                status=manifest.status.value,
                manifest=manifest,
                problem="运行目录结构不完整（缺少配置快照或 inputs/results 目录）",
            )
        return _RunInspection(
            created_at=created_at,
            state=RunArchiveState.USABLE,
            status=manifest.status.value,
            manifest=manifest,
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
        if target.is_file() and not target.is_symlink():
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

        拒绝绝对路径、``..`` 片段和空标识；要求结果**是本运行登记的阶段结果**；
        再校验文件存在（且不是符号链接）和 SHA-256 与大小一致。任一条件不满足都
        直接失败，绝不跨运行或越界读取。
        """
        normalized = normalize_relative_path(result_id, field_name="历史结果标识")
        manifest = self._manifest(run_id)
        allowed = {path for path, _stage in _result_entries(manifest)}
        if normalized not in allowed:
            raise ValueError(f"结果不是当前运行登记的阶段结果: {normalized}")
        reference = _reference_index(manifest).get(normalized)
        if reference is None:
            raise ValueError(f"结果未登记到当前运行 manifest: {normalized}")
        target = self._join(run_id, normalized)
        # 边界再确认：解析后的文件必须仍在当前运行目录内。
        self.runs.layout(run_id).relative_path(target)
        if target.is_symlink() or not target.is_file():
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


@dataclass(frozen=True)
class _RunInspection:
    """一次运行的可用性判定结果：两种历史视图共用的中间事实。

    ``manifest`` 只在 manifest 可解析时存在；``invalid`` 运行即使读到了 manifest
    （``run_id`` 与目录名不一致）也不对外暴露阶段与产物。
    """

    created_at: datetime
    state: RunArchiveState
    status: str
    manifest: RunManifest | None = None
    problem: str | None = None


def _stage_for_result(manifest: RunManifest, source_result: str | None) -> str | None:
    """把产物来源正式结果对应到 manifest 中登记它的阶段。

    交付产物只由归档服务按某个阶段的正式结果生成；这里只做事实对应，不做路径推断：
    先看各阶段登记的 ``result``，再看登记了阶段名的 ``outputs``，都对不上就返回
    ``None``（该产物不能作为“重新生成”的输入）。
    """
    if source_result is None:
        return None
    for name, record in manifest.stages.items():
        if record.result == source_result:
            return name
    for reference in manifest.outputs:
        if reference.path == source_result and reference.stage in manifest.stages:
            return reference.stage
    return None


__all__ = ["FileRunHistoryRepository"]
