"""阶段结果文件仓储：把正式测量结果原子写入各自阶段目录。

本模块实现 :class:`application.ports.stage_result_repository.StageResultRepository`
的保存侧契约（重构计划步骤 5）：

- 正式结果按阶段写入 ``test_results/<stage>/<measurement_id>.json``，阶段目录按需创建；
- 文件名只由结果自身身份构造，用户文本不参与路径；
- 目标文件已存在时拒绝覆盖；创建目标使用独占打开，不依赖进程内先检查后写入；
- 内容先写入同目录临时文件，再由 :func:`~infrastructure.filesystem.atomic_json.write_json_atomic`
  完成序列化、``fsync`` 与临时文件替换，最后原子发布到目标；
- 返回的 SHA-256 与大小在**发布前**从已 ``fsync`` 的暂存文件读取，发布成功后不再
  触碰文件系统，因此引用事实不会出现“结果已提交、调用却报错”的中间状态；
- 任何一步失败都清理临时文件和尚未发布的占位目标，不留下半份结果。

加载与列举在本步骤范围之外，由后续步骤接入。
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from application.dto.measurement_results import (
    MeasurementResult,
    measurement_result_filename,
)
from application.ports.stage_result_repository import (
    MeasurementStage,
    StageResultError,
    StageResultReference,
    coerce_measurement_stage,
    require_stage_result,
)
from infrastructure.filesystem.atomic_json import write_json_atomic
from infrastructure.filesystem.paths import DEFAULT_PATHS, PathService
from infrastructure.persistence.json_encoder import NumpyJSONEncoder

__all__ = ["FileStageResultRepository"]

logger = logging.getLogger(__name__)

_READ_CHUNK_BYTES = 1024 * 1024


class FileStageResultRepository:
    """基于阶段目录的正式测量结果写入适配器。

    保存前必须同时给出阶段和正式结果 DTO；结果类型与阶段不符、缺少测量身份或
    文件名无法由身份构造时，在创建目录/写盘之前以 :class:`StageResultError` 结束。
    文件系统错误（目录创建、序列化、读取暂存文件、独占创建、替换）原样抛出，调用
    方仍能看到真实失败原因；临时文件清理失败只记录日志，不顶替原始异常。
    发布成功后不再读写文件，返回的引用因此不会与已提交文件脱节。
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
