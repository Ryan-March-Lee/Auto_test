"""运行目录仓储服务：创建运行、读写 manifest、校验并登记文件。

本模块在 :mod:`infrastructure.persistence.run_directory` 的布局与原子读写原语之上，
提供面向运行生命周期的仓储接口：

- ``create_run``：创建唯一运行目录，写入不可变 ``config.json`` 和初始 manifest。
- ``load_manifest`` / ``update_manifest``：原子读取和更新 manifest。
- ``register_file``：按 ``inputs/`` 与 ``results/`` 目录登记文件及其 SHA-256、字节数。
- ``verify_file`` / ``verify_manifest_files``：校验登记文件是否仍与 manifest 一致。
- ``read_config`` / ``list_run_ids``：校验读取配置快照并枚举有效运行。

职责边界：

- 只管理运行目录内的文件，不触碰全局配置和兼容结果目录。
- 所有读写接口都要求运行已由 ``create_run`` 创建，绝不隐式新建或覆盖运行。
- 不启动测量、不访问仪器，也不在运行创建后改写 ``config.json``。
- 所有 JSON 都使用“临时文件 + 原子替换”，进程中断不会留下半截目标文件。

本实现是 ``application.ports.run_repository`` 协议端口的基础设施适配器：
应用层的 ``application.run_service.create_run_snapshot`` 与
``app.run_context.create_prepared_run`` 通过该端口创建运行快照和读取配置。
测量流程迁移到新运行目录属于后续步骤，当前 ``prepare_run`` 仍写兼容快照。
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Union

from domain.run.models import (
    CONFIG_FILENAME,
    INPUTS_DIRNAME,
    MANIFEST_FILENAME,
    RESULTS_DIRNAME,
    FileReference,
    RunManifest,
    default_manifest,
    generate_run_id,
    normalize_relative_path,
    utc_now_iso,
    validate_run_id,
)
from infrastructure.filesystem.atomic_json import read_json, write_json_atomic
from infrastructure.filesystem.paths import PathService
from infrastructure.persistence.json_encoder import NumpyJSONEncoder
from infrastructure.persistence.run_directory import (
    RunDirectoryLayout,
    default_runs_root,
    file_reference,
    file_sha256_and_size,
    load_run_manifest,
    save_run_manifest,
)
from infrastructure.runtime.runtime_contract import software_version_tag


PathLike = Union[str, Path]


def _upsert(
    references: tuple[FileReference, ...], reference: FileReference
) -> tuple[FileReference, ...]:
    """按相对路径覆盖同名文件引用，保持登记顺序稳定。"""
    for index, item in enumerate(references):
        if item.path == reference.path:
            return references[:index] + (reference,) + references[index + 1 :]
    return references + (reference,)


class RunRepository:
    """在运行根目录下创建、读取和更新运行目录（Run）的仓储服务。

    默认根目录为 ``<结果根目录>/runs``，也可以通过 ``root`` 直接注入临时目录，
    便于测试和离线归档。
    """

    _MAX_ID_ATTEMPTS = 8

    def __init__(self, root: PathLike | None = None, *, paths: PathService | None = None):
        if root is not None:
            self.root = Path(root).expanduser()
        else:
            self.root = default_runs_root(paths=paths)

    def layout(self, run_id: str) -> RunDirectoryLayout:
        """返回指定运行在运行根目录下的布局。"""
        return RunDirectoryLayout(self.root, run_id)

    def run_exists(self, run_id: str) -> bool:
        """判断运行目录是否完整存在（目录、manifest、配置快照和输入输出目录齐全）。"""
        try:
            self._require_existing_run(run_id)
        except (FileNotFoundError, ValueError):
            return False
        return True

    def create_run(
        self,
        config: Mapping[str, Any],
        *,
        run_id: str | None = None,
        software_version: str | None = None,
        notes: str | None = None,
        product_name: str | None = None,
        now: datetime | None = None,
    ) -> tuple[RunDirectoryLayout, RunManifest]:
        """创建新的运行目录，写入 ``config.json`` 和初始 manifest。

        ``config`` 是本次运行实际使用的配置快照，写入后不再被本仓储修改。
        省略 ``run_id`` 时自动生成唯一标识，并在目录名冲突时重新生成；
        显式指定的 ``run_id`` 若已存在则抛出 ``FileExistsError``，绝不覆盖。
        返回 ``(布局, 初始 manifest)``，调用方持有它作为后续阶段的运行上下文。
        """
        if not isinstance(config, Mapping):
            raise ValueError("config 必须是配置映射，用于写入运行目录的 config.json")
        if run_id is not None:
            return self._create_run(
                config,
                run_id=run_id,
                software_version=software_version,
                notes=notes,
                product_name=product_name,
                now=now,
            )
        last_error: FileExistsError | None = None
        for _ in range(self._MAX_ID_ATTEMPTS):
            try:
                return self._create_run(
                    config,
                    run_id=generate_run_id(now=now),
                    software_version=software_version,
                    notes=notes,
                    product_name=product_name,
                    now=now,
                )
            except FileExistsError as error:
                last_error = error
        raise FileExistsError("多次生成的 run_id 都已被占用，请检查运行根目录状态") from last_error

    def load_manifest(self, run_id: str) -> RunManifest:
        """读取并校验指定运行的 manifest；运行不存在或结构不完整时直接失败。"""
        return load_run_manifest(self._require_existing_run(run_id).manifest_path)

    def update_manifest(
        self, run_id: str, manifest: RunManifest, *, now: datetime | None = None
    ) -> RunManifest:
        """原子写回 manifest，并要求 ``run_id`` 与目标运行目录一致。

        manifest 的完整性与阶段状态规则由 :class:`RunManifest` 保证；
        本方法只负责运行存在性、目录一致性和原子落盘，不会隐式创建运行目录。
        """
        if not isinstance(manifest, RunManifest):
            raise TypeError("manifest 必须是 RunManifest")
        layout = self._require_existing_run(run_id)
        if manifest.run_id != layout.run_id:
            raise ValueError(
                f"manifest.run_id ({manifest.run_id}) 与目标运行目录 ({layout.run_id}) 不一致"
            )
        target = manifest if now is None else replace(manifest, updated_at=utc_now_iso(now))
        save_run_manifest(layout.manifest_path, target)
        return target

    def register_file(
        self,
        run_id: str,
        path: PathLike,
        *,
        stage: str | None = None,
        description: str | None = None,
        now: datetime | None = None,
    ) -> FileReference:
        """登记运行目录内已完成的文件，并同步更新 manifest。

        ``path`` 可以是运行目录内的相对路径（例如 ``inputs/cable_loss.json``），
        也可以是绝对路径；文件类别由所在目录决定：``inputs/`` 记为输入，
        ``results/`` 记为输出。登记的引用包含相对路径、SHA-256、字节数和登记时间，
        重复登记同一路径会覆盖旧引用。
        """
        layout = self._require_existing_run(run_id)
        reference = file_reference(
            self._resolve_member_path(layout, path),
            layout=layout,
            stage=stage,
            description=description,
            created_at=utc_now_iso(now),
        )
        manifest = load_run_manifest(layout.manifest_path)
        if self._classify(reference.path) == "input":
            updated = replace(manifest, inputs=_upsert(manifest.inputs, reference))
        else:
            updated = replace(manifest, outputs=_upsert(manifest.outputs, reference))
        self.update_manifest(run_id, updated, now=now)
        return reference

    def write_run_json(self, run_id: str, relative_path: str, payload: Any) -> str:
        """原子写入运行目录 ``inputs/`` 或 ``results/`` 下的 JSON 文件。

        只允许写在运行目录内，返回规范化后的相对路径。本方法只负责落盘，
        不修改 manifest；调用方应在文件写完后通过 :meth:`register_file` 登记校验值。
        采用临时文件加原子替换，进程中断不会留下半截目标文件。
        """
        layout = self._require_existing_run(run_id)
        normalized = normalize_relative_path(relative_path, field_name="运行文件路径")
        self._classify(normalized)
        target = self._resolve_reference_path(layout, normalized)
        target.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(target, payload, encoder=NumpyJSONEncoder)
        return normalized

    def discard_file(self, run_id: str, relative_path: str) -> str:
        """删除运行目录内的文件并撤销 manifest 登记。

        这是 :meth:`write_run_json` / :meth:`register_file` 的补偿操作：阶段提交
        失败或阶段被取消时，用它清除未提交的结果，避免 manifest 或文件系统里留下
        看起来完整的阶段结果。文件不存在、未登记时按无操作处理。
        """
        layout = self._require_existing_run(run_id)
        normalized = normalize_relative_path(relative_path, field_name="运行文件路径")
        category = self._classify(normalized)
        target = self._resolve_reference_path(layout, normalized)
        target.unlink(missing_ok=True)

        manifest = load_run_manifest(layout.manifest_path)
        references = manifest.inputs if category == "input" else manifest.outputs
        remaining = tuple(item for item in references if item.path != normalized)
        if len(remaining) != len(references):
            if category == "input":
                manifest = replace(manifest, inputs=remaining)
            else:
                manifest = replace(manifest, outputs=remaining)
            self.update_manifest(run_id, manifest)
        return normalized

    def read_config(self, run_id: str, *, verify: bool = True) -> Any:
        """读取运行目录中的配置快照 ``config.json``。

        默认按 manifest 登记的 SHA-256 和大小校验文件，发现被替换时拒绝返回；
        仅在确实需要读取历史或损坏快照时才显式传 ``verify=False``。
        """
        layout = self._require_existing_run(run_id)
        if verify:
            manifest = load_run_manifest(layout.manifest_path)
            if manifest.config is None:
                raise ValueError(f"manifest 未登记配置快照: {run_id}")
            if manifest.config.path != CONFIG_FILENAME:
                raise ValueError(f"manifest 配置快照路径异常: {manifest.config.path!r}")
            self.verify_file(run_id, manifest.config)
        return read_json(layout.config_path)

    def verify_file(self, run_id: str, reference: FileReference) -> Path:
        """校验登记文件仍位于运行目录内，且 SHA-256 和大小与引用一致。"""
        if not isinstance(reference, FileReference):
            raise TypeError("reference 必须是 FileReference")
        layout = self._require_existing_run(run_id)
        path = self._resolve_reference_path(layout, reference.path)
        if not path.is_file():
            raise FileNotFoundError(f"登记文件不存在或不是普通文件: {reference.path}")
        digest, size = file_sha256_and_size(path)
        if reference.sha256 is not None and digest != reference.sha256:
            raise ValueError(f"文件 SHA-256 与 manifest 不一致: {reference.path}")
        if reference.size_bytes is not None and size != reference.size_bytes:
            raise ValueError(f"文件大小与 manifest 不一致: {reference.path}")
        return path

    def verify_manifest_files(
        self,
        run_id: str,
        *,
        verify_config: bool = True,
        verify_inputs: bool = True,
        verify_outputs: bool = True,
    ) -> RunManifest:
        """校验 manifest 登记的全部或部分文件引用，返回读取到的 manifest。"""
        manifest = self.load_manifest(run_id)
        references: list[FileReference] = []
        if verify_config and manifest.config is not None:
            references.append(manifest.config)
        if verify_inputs:
            references.extend(manifest.inputs)
        if verify_outputs:
            references.extend(manifest.outputs)
        for reference in references:
            self.verify_file(run_id, reference)
        return manifest

    def list_run_ids(self, *, include_invalid: bool = False) -> tuple[str, ...]:
        """按名称排序列出运行根目录下的运行标识。

        默认只返回名称合法、包含可解析 manifest 且 ``manifest.run_id`` 与目录名一致的运行；
        ``include_invalid=True`` 时额外返回名称合法但 manifest 缺失或损坏的目录，便于诊断。
        """
        if not self.root.is_dir():
            return ()
        run_ids: list[str] = []
        for item in sorted(self.root.iterdir(), key=lambda path: path.name):
            if not item.is_dir():
                continue
            try:
                validate_run_id(item.name)
            except ValueError:
                continue
            if include_invalid or self._is_valid_run(item):
                run_ids.append(item.name)
        return tuple(run_ids)

    def _require_existing_run(self, run_id: str) -> RunDirectoryLayout:
        """确认运行完整存在，返回其布局；缺失时抛出 ``FileNotFoundError``。"""
        layout = self.layout(run_id)
        if not layout.run_directory.is_dir():
            raise FileNotFoundError(f"运行不存在: {run_id}")
        missing = [
            name
            for name, path in (
                (MANIFEST_FILENAME, layout.manifest_path),
                (CONFIG_FILENAME, layout.config_path),
            )
            if not path.is_file()
        ]
        if not layout.inputs_dir.is_dir():
            missing.append(INPUTS_DIRNAME + "/")
        if not layout.results_dir.is_dir():
            missing.append(RESULTS_DIRNAME + "/")
        if missing:
            raise FileNotFoundError(
                f"运行目录结构不完整 {run_id}: 缺少 " + ", ".join(missing)
            )
        return layout

    def _is_valid_run(self, run_directory: Path) -> bool:
        """判断目录是否是一个 manifest 可解析且标识一致的运行。"""
        try:
            manifest = load_run_manifest(run_directory / MANIFEST_FILENAME)
        except (OSError, ValueError):
            return False
        return manifest.run_id == run_directory.name

    @staticmethod
    def _resolve_member_path(layout: RunDirectoryLayout, path: PathLike) -> Path:
        """把运行目录内的相对路径解析为绝对路径；绝对路径保持原样。"""
        candidate = Path(path)
        return candidate if candidate.is_absolute() else layout.run_directory / candidate

    def _resolve_reference_path(self, layout: RunDirectoryLayout, relative_path: str) -> Path:
        """把 manifest 相对路径解析到运行目录内，并拒绝越界引用。"""
        path = layout.run_directory / Path(*relative_path.split("/"))
        layout.relative_path(path)
        return path

    def _create_run(
        self,
        config: Mapping[str, Any],
        *,
        run_id: str,
        software_version: str | None,
        notes: str | None,
        product_name: str | None,
        now: datetime | None,
    ) -> tuple[RunDirectoryLayout, RunManifest]:
        layout = self.layout(run_id)
        try:
            # mkdir 成功才代表本调用拥有该运行目录，避免并发时误删他人目录。
            layout.run_directory.mkdir(parents=True, exist_ok=False)
        except FileExistsError as error:
            raise FileExistsError(f"运行目录已存在，不允许覆盖: {layout.run_directory}") from error
        try:
            layout.inputs_dir.mkdir(exist_ok=True)
            layout.results_dir.mkdir(exist_ok=True)
            write_json_atomic(layout.config_path, config, encoder=NumpyJSONEncoder)
            config_reference = file_reference(
                layout.config_path, layout=layout, description="运行配置快照"
            )
            manifest = default_manifest(
                layout.run_id,
                now=now,
                software_version=software_version or software_version_tag(),
                config=config_reference,
            )
            if notes is not None or product_name is not None:
                manifest = replace(manifest, notes=notes, product_name=product_name)
            save_run_manifest(layout.manifest_path, manifest)
        except Exception:
            # 半途失败时清理本调用创建的目录，避免留下看似可用的运行。
            shutil.rmtree(layout.run_directory, ignore_errors=True)
            raise
        return layout, manifest

    @staticmethod
    def _classify(relative_path: str) -> str:
        if relative_path.startswith(INPUTS_DIRNAME + "/"):
            return "input"
        if relative_path.startswith(RESULTS_DIRNAME + "/"):
            return "output"
        raise ValueError(
            f"只能登记 {INPUTS_DIRNAME}/ 或 {RESULTS_DIRNAME}/ 下的文件: {relative_path}"
        )


__all__ = ["PathLike", "RunRepository"]
