"""文件系统配置快照仓储。

布局固定为::

    test_results/configurations/<名称>_<YYYYMMDD_HHMM>/<名称>.json
    test_results/configurations/<名称>_<YYYYMMDD_HHMM>/metadata.json

同一用户名称再次保存时，查找已有快照并**整代替换**其中的配置文件与元数据；新名称
才创建新的带时间后缀目录。``metadata.json`` 只保存展示、审计与界面恢复信息，不承载
正式配置字段，因此配置文件仍可直接交给现有配置加载器。仪器排布页的九宫格格位以
``rack_slots`` 单独记录在这里，不进入正式配置文档。

一致性与失败语义：

- 配置文件和元数据先写进同一根目录下的暂存目录，再用目录替换提交。读取方只会看到
  上一代完整快照或下一代完整快照，不会读到“新配置配旧元数据”；
- 提交前失败会删除暂存目录，目标快照保持原样；
- 目录替换中途失败会恢复备份目录；进程中断留下的备份/暂存目录在下次访问时恢复或
  清理，不会成为可加载的“半代快照”；
- 仓储在进程内串行化保存、列举和加载，避免同名并发写互相覆盖暂存目录。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from application.dto.configuration_snapshots import (
    configuration_folder_name,
    validate_configuration_name,
)
from application.ports.configuration_snapshots import ConfigurationSnapshot
from application.ports.types import PathLike
from infrastructure.config.json_io import load_json_object
from infrastructure.filesystem.atomic_json import write_json_atomic
from infrastructure.filesystem.paths import DEFAULT_PATHS, PathService

logger = logging.getLogger(__name__)

_METADATA_FILENAME = "metadata.json"
_METADATA_SCHEMA = "auto_test.configuration_snapshot/1"
#: 与仪器排布页的 3×3 九宫格一致；元数据只接受这个范围内的格位下标。
_RACK_SLOT_COUNT = 9
#: 暂存/备份目录名：``.<目标目录名>.staging-<uuid>``。点前缀让用户目录列表不把它
#: 当成快照；UUID 后缀用于精确识别进程中断留下的目录。
_TRANSIENT_DIRECTORY = re.compile(
    r"^\.(?P<target>.+)\.(?P<kind>staging|backup)-(?P<token>[0-9a-f]{32})$"
)
#: 进程内串行化配置快照的保存、列举和加载。跨进程并发不由本模块承诺；桌面应用
#: 只有一个快照写入进程，全局锁比按根目录维护锁表更简单且不会漏掉暂存/恢复。
_SAVE_LOCK = threading.RLock()


def _saved_at(now: datetime | None) -> str:
    moment = now or datetime.now().astimezone()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _folder_moment(now: datetime | None) -> datetime:
    """目录名与保存时间共用一个时刻，避免午夜前后落到不同日期。"""
    return now or datetime.now().astimezone()


def _clean_rack_slots(value: Any) -> dict[str, int]:
    """清洗九宫格排布元数据：只保留合法电源键与 0..8 的整数格位。

    排布是界面恢复信息，坏数据不能让整份快照不可加载；清洗后为空就当作没有保存
    过排布，界面会按配置顺序补位。
    """
    if not isinstance(value, Mapping):
        return {}
    cleaned: dict[str, int] = {}
    for raw_key, raw_index in value.items():
        key = str(raw_key or "").strip()
        if not key or key in cleaned or isinstance(raw_index, bool):
            continue
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            continue
        if 0 <= index < _RACK_SLOT_COUNT:
            cleaned[key] = index
    return cleaned


class FileConfigurationSnapshotRepository:
    """把用户命名配置保存到独立目录的适配器。"""

    def __init__(
        self,
        root: PathLike | None = None,
        *,
        paths: PathService | None = None,
    ) -> None:
        if root is not None and paths is not None:
            raise ValueError("root 与 paths 只能提供一个")
        self.root = (
            Path(root).expanduser()
            if root is not None
            else (paths or DEFAULT_PATHS).configuration_snapshots_dir
        ).resolve()

    def save(
        self,
        display_name: str,
        configuration: Mapping[str, Any],
        *,
        rack_slots: Mapping[str, int] | None = None,
        now: datetime | None = None,
    ) -> ConfigurationSnapshot:
        name = validate_configuration_name(display_name)
        if not isinstance(configuration, Mapping):
            raise ValueError("配置快照必须是对象")
        layout = _clean_rack_slots(rack_slots)

        with _SAVE_LOCK:
            self._recover_interrupted_commits()
            moment = _folder_moment(now)
            saved_at = _saved_at(moment)
            existing = self._existing_for_name(name)
            if existing is None:
                directory = self.root / configuration_folder_name(name, now=moment)
            else:
                # 同名配置整代覆盖，保留首次创建的时间后缀；saved_at 记录本次保存时间。
                directory = existing.directory
            configuration_path = directory / f"{name}.json"
            metadata_path = directory / _METADATA_FILENAME
            staging = self._transient_path(directory, "staging")
            staging.mkdir(parents=True, exist_ok=False)
            try:
                write_json_atomic(staging / configuration_path.name, configuration)
                metadata: dict[str, Any] = {
                    "schema": _METADATA_SCHEMA,
                    "display_name": name,
                    "configuration_file": configuration_path.name,
                    "saved_at": saved_at,
                }
                if layout:
                    metadata["rack_slots"] = layout
                write_json_atomic(staging / _METADATA_FILENAME, metadata)
                self._commit_staging(staging, directory)
            finally:
                # 提交成功时暂存目录已被移走；失败或中断时这里保证不留下可被误读的
                # 目录。目录替换提交过程中移走的备份由 _commit_staging 自己负责。
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
            return ConfigurationSnapshot(
                display_name=name,
                directory=directory,
                configuration_path=configuration_path,
                metadata_path=metadata_path,
                saved_at=saved_at,
                rack_slots=layout or None,
            )

    def load(self, path: PathLike) -> Mapping[str, Any]:
        with _SAVE_LOCK:
            self._recover_interrupted_commits()
            candidate = self._require_configuration_path(path)
            document = load_json_object(candidate)
            if not isinstance(document, dict):
                raise ValueError("配置快照根节点必须是对象")
            return document

    def list(self) -> tuple[ConfigurationSnapshot, ...]:
        with _SAVE_LOCK:
            self._recover_interrupted_commits()
            try:
                directories = tuple(
                    path
                    for path in self.root.iterdir()
                    if path.is_dir() and not path.name.startswith(".")
                )
            except FileNotFoundError:
                return ()
            except OSError as error:
                logger.warning("无法列出配置快照目录 %s: %s", self.root, error)
                return ()

            snapshots: list[ConfigurationSnapshot] = []
            for directory in directories:
                snapshot = self._read_snapshot(directory)
                if snapshot is not None:
                    snapshots.append(snapshot)
            snapshots.sort(key=lambda item: item.saved_at, reverse=True)
            return tuple(snapshots)

    # ---- 提交与恢复 ---------------------------------------------------------

    def _transient_path(self, directory: Path, kind: str) -> Path:
        return self.root / f".{directory.name}.{kind}-{uuid.uuid4().hex}"

    def _commit_staging(self, staging: Path, directory: Path) -> None:
        """把完整暂存目录提交为目标快照；失败时恢复上一代目录。"""
        if not directory.exists():
            os.replace(staging, directory)
            return

        backup = self._transient_path(directory, "backup")
        os.replace(directory, backup)
        try:
            os.replace(staging, directory)
        except OSError as failure:
            # 新目录没有换上去：把上一代完整快照放回原位，保证用户仍能加载它。
            try:
                os.replace(backup, directory)
            except OSError as restore_failure:
                raise OSError(
                    f"{failure}；恢复上一代配置快照失败: {restore_failure}"
                ) from failure
            raise
        # 新快照已经可见，旧一代的清理失败只记录，不影响本次保存结果。
        try:
            shutil.rmtree(backup)
        except OSError as error:
            logger.warning("无法清理旧配置快照备份 %s: %s", backup, error)

    def _recover_interrupted_commits(self) -> None:
        """恢复进程中断留下的目录交换，并清理未提交的暂存目录。

        恢复规则只依赖目录名，不读取配置内容：

        - 有备份且目标缺失：把备份换回目标（提交前中断）；
        - 有备份且目标存在：目标已经是新快照，删除备用旧代；
        - 只有暂存目录：本次保存没有提交，直接删除。
        """
        try:
            entries = tuple(self.root.iterdir())
        except FileNotFoundError:
            return
        except OSError as error:
            logger.warning("无法检查配置快照目录 %s: %s", self.root, error)
            return

        for entry in entries:
            if not entry.is_dir():
                continue
            match = _TRANSIENT_DIRECTORY.match(entry.name)
            if match is None:
                continue
            target = self.root / match.group("target")
            kind = match.group("kind")
            if kind == "backup":
                if target.exists():
                    shutil.rmtree(entry, ignore_errors=True)
                    continue
                try:
                    os.replace(entry, target)
                except OSError as error:
                    logger.warning(
                        "无法恢复配置快照备份 %s -> %s: %s", entry, target, error
                    )
                continue
            shutil.rmtree(entry, ignore_errors=True)

    # ---- 读取边界 -----------------------------------------------------------

    def _existing_for_name(self, name: str) -> ConfigurationSnapshot | None:
        matches = [snapshot for snapshot in self.list() if snapshot.display_name == name]
        return matches[0] if matches else None

    def _read_snapshot(self, directory: Path) -> ConfigurationSnapshot | None:
        metadata_path = directory / _METADATA_FILENAME
        try:
            metadata = load_json_object(metadata_path)
        except (OSError, ValueError, TypeError):
            return None
        if not isinstance(metadata, dict):
            return None
        if metadata.get("schema") != _METADATA_SCHEMA:
            return None
        try:
            name = validate_configuration_name(metadata["display_name"])
            filename = metadata["configuration_file"]
            saved_at = metadata["saved_at"]
        except (KeyError, TypeError, ValueError):
            return None
        if not isinstance(filename, str) or filename != f"{name}.json":
            return None
        if not isinstance(saved_at, str) or not saved_at.strip():
            return None
        configuration_path = directory / filename
        try:
            if not configuration_path.is_file():
                return None
        except OSError:
            return None
        layout = _clean_rack_slots(metadata.get("rack_slots"))
        return ConfigurationSnapshot(
            display_name=name,
            directory=directory,
            configuration_path=configuration_path,
            metadata_path=metadata_path,
            saved_at=saved_at,
            rack_slots=layout or None,
        )

    def _require_configuration_path(self, path: PathLike) -> Path:
        candidate = Path(path).expanduser().resolve()
        root = self.root.resolve()
        try:
            relative = candidate.relative_to(root)
        except ValueError as error:
            raise ValueError("配置快照路径必须位于配置快照目录内") from error
        # 只允许 configurations/<snapshot>/<name>.json，不能加载 metadata 或嵌套文件。
        if len(relative.parts) != 2 or candidate.name == _METADATA_FILENAME:
            raise ValueError("配置快照路径必须是快照目录中的直接配置文件")
        if candidate.suffix.lower() != ".json":
            raise ValueError("配置快照必须是 JSON 文件")
        # 目录里的任意 JSON 不是“快照”：必须由 metadata.json 登记，且登记的文件名
        # 与用户选择的路径完全一致，否则手放进快照目录的文件也能被加载。
        snapshot = self._read_snapshot(candidate.parent)
        if snapshot is None:
            raise ValueError("配置快照未登记或已损坏，无法加载")
        if snapshot.configuration_path.resolve() != candidate:
            raise ValueError("配置快照路径与登记信息不一致")
        if not candidate.is_file():
            raise FileNotFoundError(f"配置快照不存在: {candidate}")
        return candidate


__all__ = ["FileConfigurationSnapshotRepository"]
