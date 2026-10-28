"""运行目录（Run）布局与 manifest 持久化适配器。

本模块只负责文件系统边界：

- 定义运行目录布局（``manifest.json``、``config.json``、``inputs/``、``results/``）。
- 在指定根目录下创建空运行，并写入初始 manifest。
- 原子读写 manifest。

它不启动测量、不读取仪器，也不改变既有测量流程。
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Union

from domain.run.models import (
    ARTIFACTS_DIRNAME,
    CONFIG_FILENAME,
    INPUTS_DIRNAME,
    MANIFEST_FILENAME,
    RESULTS_DIRNAME,
    RUNS_DIRNAME,
    FileReference,
    RunManifest,
    RunStatus,
    default_manifest,
    generate_run_id,
    normalize_relative_path,
    validate_run_id,
)
from infrastructure.filesystem.atomic_json import read_json, write_json_atomic
from infrastructure.filesystem.paths import DEFAULT_PATHS, PathService
from infrastructure.runtime.runtime_contract import software_version_tag


PathLike = Union[str, Path]


def default_runs_root(*, paths: PathService | None = None) -> Path:
    """返回默认运行根目录 ``<结果根目录>/runs``。"""
    service = paths if paths is not None else DEFAULT_PATHS
    return service.test_results_dir / RUNS_DIRNAME


@dataclass(frozen=True)
class RunDirectoryLayout:
    """某个 ``run_id`` 在运行根目录下的全部相对位置。"""

    root: Path
    run_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root).expanduser())
        validate_run_id(self.run_id)

    @property
    def run_directory(self) -> Path:
        return self.root / self.run_id

    @property
    def manifest_path(self) -> Path:
        return self.run_directory / MANIFEST_FILENAME

    @property
    def config_path(self) -> Path:
        return self.run_directory / CONFIG_FILENAME

    @property
    def inputs_dir(self) -> Path:
        return self.run_directory / INPUTS_DIRNAME

    @property
    def results_dir(self) -> Path:
        return self.run_directory / RESULTS_DIRNAME

    @property
    def artifacts_dir(self) -> Path:
        """自动归档交付产物的受控目录；旧运行可能没有该目录。"""
        return self.run_directory / ARTIFACTS_DIRNAME

    def create_directories(self) -> "RunDirectoryLayout":
        """创建运行目录骨架；已存在的运行目录不会被覆盖。"""
        self.run_directory.mkdir(parents=True, exist_ok=False)
        self.inputs_dir.mkdir(exist_ok=True)
        self.results_dir.mkdir(exist_ok=True)
        self.artifacts_dir.mkdir(exist_ok=True)
        return self

    def relative_path(self, path: PathLike) -> str:
        """把运行目录内的文件路径转换成 manifest 使用的相对路径。

        相对路径按运行目录解析，绝对路径直接解析；两种形式都会在解析后
        校验目标仍位于运行目录内，因此符号链接越界也会被拒绝。
        """
        candidate = Path(path)
        absolute = candidate if candidate.is_absolute() else self.run_directory / candidate
        run_directory = self.run_directory.resolve()
        resolved = absolute.resolve()
        if resolved == run_directory or not _is_within(run_directory, resolved):
            raise ValueError(f"路径不在运行目录内: {path}")
        relative = Path(*resolved.parts[len(run_directory.parts):])
        return normalize_relative_path(relative.as_posix(), field_name="运行文件路径")


def _is_within(run_directory: Path, resolved: Path) -> bool:
    """按平台大小写规则判断 ``resolved`` 是否位于 ``run_directory`` 内。"""
    run_text = os.path.normcase(str(run_directory))
    target_text = os.path.normcase(str(resolved))
    return target_text.startswith(run_text.rstrip(os.sep) + os.sep)


def create_empty_run(
    root: PathLike,
    *,
    run_id: str | None = None,
    software_version: str | None = None,
    now: datetime | None = None,
) -> tuple[RunDirectoryLayout, RunManifest]:
    """在 ``root`` 下创建一个空运行目录和初始 manifest。

    空运行只包含 ``inputs/``、``results/`` 和 ``manifest.json``，不写任何配置快照或结果。
    初始状态固定为 ``created``，所有阶段为 ``pending``；调用方不能创建看起来已完成
    或已失败的运行。若同名运行目录已存在则抛出 ``FileExistsError``，绝不覆盖。
    """
    layout = RunDirectoryLayout(Path(root), run_id if run_id is not None else generate_run_id(now=now))
    if layout.run_directory.exists():
        raise FileExistsError(f"运行目录已存在，不允许覆盖: {layout.run_directory}")
    manifest = default_manifest(
        layout.run_id,
        now=now,
        software_version=software_version or software_version_tag(),
        status=RunStatus.CREATED,
    )
    try:
        layout.create_directories()
        save_run_manifest(layout.manifest_path, manifest)
    except Exception:
        shutil.rmtree(layout.run_directory, ignore_errors=True)
        raise
    return layout, manifest


def save_run_manifest(path: PathLike, manifest: RunManifest) -> Path:
    """原子写入 manifest。"""
    if not isinstance(manifest, RunManifest):
        raise TypeError("manifest 必须是 RunManifest")
    return write_json_atomic(path, manifest.to_dict())


def load_run_manifest(path: PathLike) -> RunManifest:
    """读取并校验 manifest。"""
    return RunManifest.from_dict(read_json(path))


def load_run_manifest_from_directory(run_directory: PathLike) -> RunManifest:
    """按运行目录读取其中的 ``manifest.json``。"""
    return load_run_manifest(Path(run_directory) / MANIFEST_FILENAME)


def file_sha256_and_size(path: PathLike) -> tuple[str, int]:
    """单次读取文件，同时得到 SHA-256 和字节数，避免两次访问之间文件变化。"""
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def file_sha256(path: PathLike) -> str:
    """计算文件 SHA-256，用于 manifest 登记校验值。"""
    return file_sha256_and_size(path)[0]


def file_reference(path: PathLike, *, layout: RunDirectoryLayout, **kwargs: Any) -> FileReference:
    """在运行目录内登记文件引用，自动填充相对路径、大小和 SHA-256。

    登记前要求目标存在且是普通文件；调用方提供的 ``sha256``/``size_bytes``
    必须与实测值一致，避免 manifest 记录与实际文件不符。
    """
    absolute = Path(path)
    if not absolute.is_file():
        raise FileNotFoundError(f"登记文件不存在或不是普通文件: {absolute}")
    reference_path = layout.relative_path(absolute)
    digest, size = file_sha256_and_size(absolute)
    for key, value in (("sha256", digest), ("size_bytes", size)):
        provided = kwargs.get(key)
        if provided is not None and provided != value:
            raise ValueError(f"{key} 与文件实际值不一致: {provided!r} != {value!r}")
        kwargs[key] = value
    return FileReference(path=reference_path, **kwargs)


__all__ = [
    "RunDirectoryLayout",
    "create_empty_run",
    "default_runs_root",
    "file_reference",
    "file_sha256",
    "file_sha256_and_size",
    "load_run_manifest",
    "load_run_manifest_from_directory",
    "save_run_manifest",
]
