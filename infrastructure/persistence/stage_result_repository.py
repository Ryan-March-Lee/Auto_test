"""阶段结果文件仓储：把正式测量结果原子写入各自阶段目录并显式加载。

本模块实现 :class:`application.ports.stage_result_repository.StageResultRepository`
的保存与加载契约（重构计划步骤 5、6）。

保存侧：

- 正式结果按阶段写入 ``test_results/<stage>/<measurement_id>.json``，阶段目录按需创建；
- 文件名只由结果自身身份构造，用户文本不参与路径；
- 目标文件已存在时拒绝覆盖；创建目标使用独占打开，不依赖进程内先检查后写入；
- 内容先写入同目录临时文件，再由 :func:`~infrastructure.filesystem.atomic_json.write_json_atomic`
  完成序列化、``fsync`` 与临时文件替换，最后原子发布到目标；
- 返回的 SHA-256 与大小在**发布前**从已 ``fsync`` 的暂存文件读取，发布成功后不再
  触碰文件系统，因此引用事实不会出现“结果已提交、调用却报错”的中间状态；
- 任何一步失败都清理临时文件和尚未发布的占位目标，不留下半份结果。

加载侧：

- 只接受调用方给出的显式路径，且路径必须是阶段目录下的 ``<measurement_id>.json``；
  没有 ``latest_path()`` 一类入口，正式测量不会隐式消费“最新结果”；
- 校验顺序固定为“阶段目录边界 → 文件存在 → UTF-8/JSON 解析 → 正式 DTO →
  身份/类型/状态/schema”。摘要与大小取自**同一次读取的字节**，因此返回的文件事实
  与解析结果必然对应，调用方可在测量开始前用摘要复核文件未变化；这份字节同时随
  加载结果返回，逐字节导出无需在校验后重读文件；
- 返回结果必须与文件名中的测量身份一致、类型与阶段相符且状态为 ``completed``；
  缺少身份/保存时间的旧运行结果不会被当作正式阶段结果读取，仍走运行目录的兼容入口；
- 列举只返回可读取的 ``completed`` 正式结果，跳过损坏、未完成或类型不匹配的文件，
  不创建目录，也不把跳过原因包装成半成品记录。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from application.dto.measurement_results import (
    MEASUREMENT_RESULT_SUFFIX,
    MeasurementResult,
    measurement_result_filename,
    measurement_result_from_document,
    validate_measurement_result_filename,
)
from application.ports.stage_result_repository import (
    LoadedStageResult,
    MeasurementStage,
    StageResultEntry,
    StageResultError,
    StageResultReference,
    coerce_measurement_stage,
    require_stage_result,
    require_stage_result_path,
)
from application.ports.types import PathLike
from infrastructure.filesystem.atomic_json import write_json_atomic
from infrastructure.filesystem.paths import DEFAULT_PATHS, PathService
from infrastructure.persistence.json_encoder import NumpyJSONEncoder

__all__ = ["FileStageResultRepository"]

logger = logging.getLogger(__name__)

_READ_CHUNK_BYTES = 1024 * 1024
# 列举排序所用的固定纪元：与日期相减得到秒数，不依赖平台的 timestamp() 支持范围。
_SORT_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class FileStageResultRepository:
    """基于阶段目录的正式测量结果读写适配器。

    保存前必须同时给出阶段和正式结果 DTO；结果类型与阶段不符、缺少测量身份或
    文件名无法由身份构造时，在创建目录/写盘之前以 :class:`StageResultError` 结束。
    文件系统错误（目录创建、序列化、读取暂存文件、独占创建、替换）原样抛出，调用
    方仍能看到真实失败原因；临时文件清理失败只记录日志，不顶替原始异常。
    发布成功后不再读写文件，返回的引用因此不会与已提交文件脱节。

    加载只从调用方给出的显式路径读取，并返回同一次读取得到的摘要与大小；列举只
    返回可读取的 ``completed`` 正式结果，不扫描“最新文件”。
    """

    def __init__(self, *, paths: PathService | None = None) -> None:
        self._paths = paths if paths is not None else DEFAULT_PATHS

    def save(
        self,
        stage: MeasurementStage | str,
        result: MeasurementResult,
    ) -> StageResultReference:
        """把正式结果原子写入其阶段目录，返回实际文件事实。"""
        target_stage = coerce_measurement_stage(stage)
        formal = require_stage_result(target_stage, result)
        directory = self._directory_for(target_stage)
        try:
            filename = measurement_result_filename(formal.measurement_id)
        except ValueError as error:
            raise StageResultError(
                f"阶段 {target_stage.value} 的结果文件名无法由测量身份构造: {error}"
            ) from error
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / filename
        if target.exists():
            raise StageResultError(f"阶段结果已存在，不允许覆盖: {target}")
        digest, size_bytes = self._write_new_json(target, formal.to_dict())
        return StageResultReference(
            stage=target_stage,
            measurement_id=formal.measurement_id,
            path=target,
            sha256=digest,
            size_bytes=size_bytes,
        )

    def _directory_for(self, stage: MeasurementStage) -> Path:
        directories = {
            MeasurementStage.CABLE_LOSS: self._paths.cable_loss_results_dir,
            MeasurementStage.DRIVER_MAPPING: self._paths.driver_mapping_results_dir,
            MeasurementStage.AMPLIFIER: self._paths.amplifier_results_dir,
        }
        return directories[stage]

    def load(
        self,
        stage: MeasurementStage | str,
        path: PathLike,
    ) -> LoadedStageResult:
        """从显式路径读取阶段结果，返回已校验 DTO 与同一次读取的文件事实。

        校验顺序就是端口要求的顺序：先确认路径直接位于阶段目录内（展开符号链接后
        仍越界即拒绝），再确认目标存在且是普通文件，然后按 UTF-8/JSON 解析、正式
        结果 DTO、身份/类型/状态/schema 逐层校验。摘要与大小来自解析用的同一份
        字节，因此不会出现“摘要对应旧内容、DTO 对应新内容”的错位；这份字节也随
        加载结果返回，导出方不必在校验之后重新读取可能已被替换的文件。

        任何“不能作为该阶段正式结果”的情况都以 :class:`StageResultError` 结束：
        空路径、目录越界、缺失文件、损坏 JSON、非正式文档、旧结果缺少身份、类型或
        状态不符都在这里失败，不返回半成品。文件系统取消/中断等 ``BaseException``
        仍按原语义传播。
        """
        target_stage = coerce_measurement_stage(stage)
        directory = self._directory_for(target_stage)
        resolved = require_stage_result_path(
            path, stage=target_stage, stage_directory=directory
        )
        if not resolved.is_file():
            raise StageResultError(f"阶段结果文件不存在或不是普通文件: {resolved}")
        try:
            raw_bytes = resolved.read_bytes()
        except OSError as error:
            raise StageResultError(
                f"无法读取阶段结果文件 {resolved}: {error}"
            ) from error
        document = _decode_result_document(raw_bytes, resolved)
        try:
            result = measurement_result_from_document(document)
        except (TypeError, ValueError) as error:
            raise StageResultError(
                f"阶段结果文件不是正式测量结果 {resolved}: {error}"
            ) from error
        reference = StageResultReference(
            stage=target_stage,
            measurement_id=_identity_from_filename(resolved.name),
            path=resolved,
            sha256=hashlib.sha256(raw_bytes).hexdigest(),
            size_bytes=len(raw_bytes),
        )
        return LoadedStageResult(
            reference=reference, result=result, raw_bytes=raw_bytes
        )

    def list_results(
        self,
        stage: MeasurementStage | str,
    ) -> tuple[StageResultEntry, ...]:
        """列举阶段目录内可读取的 ``completed`` 正式结果，按保存时间从新到旧。

        目录不存在时返回空元组且不创建目录；目录内文件名不合法、指向目录、损坏、
        身份与文件名不一致、类型不匹配或未完成的文件直接跳过。跳过的原因写入
        debug 日志，不会以占位条目的形式出现在结果列表里。
        """
        target_stage = coerce_measurement_stage(stage)
        directory = self._directory_for(target_stage)
        if not directory.exists():
            return ()
        if not directory.is_dir():
            raise StageResultError(f"阶段结果路径不是目录: {directory}")
        try:
            candidates = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError as error:
            raise StageResultError(
                f"无法列举阶段结果目录 {directory}: {error}"
            ) from error

        entries: list[StageResultEntry] = []
        for candidate in candidates:
            try:
                validate_measurement_result_filename(candidate.name)
            except ValueError as error:
                logger.debug("跳过非法阶段结果文件名 %s: %s", candidate, error)
                continue
            if not candidate.is_file():
                continue
            try:
                loaded = self.load(target_stage, candidate)
            except StageResultError as error:
                logger.debug("跳过不可用的阶段结果 %s: %s", candidate, error)
                continue
            entries.append(
                StageResultEntry(
                    reference=loaded.reference,
                    measurement_type=loaded.result.measurement_type,
                    status=loaded.result.status,
                    created_at=loaded.result.created_at,
                    run_id=loaded.result.run_id,
                )
            )
        entries.sort(key=_entry_sort_key)
        return tuple(entries)

    def _write_new_json(
        self, target: Path, payload: Mapping[str, Any]
    ) -> tuple[str, int]:
        """把载荷原子发布到尚不存在的目标，绝不覆盖已有文件。

        步骤 1 写入同目录临时文件，步骤 2 从暂存文件读取 ``(sha256, size_bytes)``，
        步骤 3 用 ``O_CREAT | O_EXCL`` 原子占位，步骤 4 用 ``os.replace`` 把临时文件
        换到占位目标。摘要与大小取自暂存文件，发布成功后不再读取目标，调用方不会
        遇到“文件已提交但计算引用失败”的不确定状态。

        占位与发布之间崩溃只可能留下零字节占位文件；任何受控失败都会在 ``finally``
        中同时删除暂存文件与占位文件。
        """
        staging_path = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        placeholder_created = False
        published = False
        try:
            write_json_atomic(staging_path, payload, encoder=NumpyJSONEncoder)
            digest, size_bytes = _file_facts(staging_path)
            try:
                descriptor = os.open(
                    target, os.O_CREAT | os.O_EXCL | os.O_WRONLY
                )
            except FileExistsError as error:
                raise StageResultError(
                    f"阶段结果已存在，不允许覆盖: {target}"
                ) from error
            placeholder_created = True
            os.close(descriptor)
            os.replace(staging_path, target)
            published = True
            return digest, size_bytes
        finally:
            if not published:
                self._discard(staging_path)
            if placeholder_created and not published:
                self._discard(target)

    @staticmethod
    def _discard(path: Path) -> None:
        """尽力删除失败路径上的临时文件/占位文件，清理失败只记录日志。"""
        try:
            path.unlink(missing_ok=True)
        except Exception as error:  # noqa: BLE001 - 清理失败不得覆盖原始写入异常
            logger.warning("无法清理阶段结果临时文件 %s: %s", path, error)


def _decode_result_document(raw_bytes: bytes, path: Path) -> Mapping[str, Any]:
    """把阶段结果文件的原始字节解析为 JSON 对象。

    读取与解析使用同一份字节：调用方随后计算摘要时，摘要对应的就是这里解析的内容。
    UTF-8 解码失败、JSON 语法错误和根节点不是对象都翻译为 :class:`StageResultError`，
    调用方看到的失败原因与“文件损坏”一致，而不是底层编解码异常。
    """
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise StageResultError(
            f"阶段结果文件不是 UTF-8 文本: {path}: {error}"
        ) from error
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise StageResultError(
            f"阶段结果文件不是有效 JSON: {path}: {error}"
        ) from error
    if not isinstance(document, Mapping):
        raise StageResultError(f"阶段结果文件根节点必须是 JSON 对象: {path}")
    return document


def _identity_from_filename(filename: str) -> str:
    """从已校验的文件名取出测量身份，交给引用构造再次校验。"""
    try:
        validated = validate_measurement_result_filename(filename)
    except ValueError as error:
        raise StageResultError(
            f"阶段结果文件名非法: {filename!r}: {error}"
        ) from error
    return validated[: -len(MEASUREMENT_RESULT_SUFFIX)]


def _entry_sort_key(entry: StageResultEntry) -> tuple[int, float, str]:
    """按保存时间从新到旧排序；无法解析的时间排在最后。

    ``created_at`` 是正式结果必须携带的 ISO 时间；正常文档都能解析。无法解析说明
    文档本身可疑，但列举契约要求跳过的是损坏文件，而条目已通过结构校验，因此这里
    不抛错，只把它稳定地排到末尾。

    排序不使用 ``datetime.timestamp()``：该调用在 Windows 上对 ``0001-01-01`` 这类
    合法但极端的日期会抛 ``OSError``，从而让整个列举失败。改为与固定 UTC 纪元相减，
    对 1..9999 年的日期都稳定可用；无时区的旧文本按 UTC 解释，避免排序结果依赖
    本机时区或夏令时。
    """
    try:
        moment = datetime.fromisoformat(entry.created_at)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        seconds = (moment.astimezone(timezone.utc) - _SORT_EPOCH).total_seconds()
        return (0, -seconds, entry.path.name)
    except (TypeError, ValueError, OverflowError, OSError):
        return (1, 0.0, entry.path.name)


def _file_facts(path: Path) -> tuple[str, int]:
    """从已完成的暂存文件读取 ``(sha256, size_bytes)``。

    调用点必须位于原子发布之前：内容已 ``fsync`` 并关闭，摘要与随后发布的目标文件
    必然一致，同时不会在提交后留下可能失败的第二次读取。
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_READ_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest(), path.stat().st_size
