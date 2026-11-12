"""阶段目录交付产物端口：把 CSV/报告与正式阶段结果放在同一目录。

重构计划步骤 20 起，新的 CSV 与测试报告不再跟随运行目录，而是与正式结果同处
``test_results/<stage>/``：

- 文件名由结果的 ``measurement_id`` 决定（例如 ``<id>.csv``、``<id>_report.html``），
  因此一份结果与它自己的交付产物始终能相互对应；
- 应用层只依赖本端口的纯数据事实（路径、摘要、大小），不导入 ``analysis``，也不
  拼接阶段目录；
- 产物生成失败不会影响正式 JSON：调用方把可预期的失败当作“该产物不可用”，正式
  结果与阶段状态保持原样；
- 端口不提供“最新产物”入口，定位必须显式给出阶段与测量身份。

实现负责阶段目录边界、原子写入和失败清理。旧运行目录中的报告仍按运行内相对路径
定位，继续由运行历史端口兼容打开。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from application.dto.measurement_artifacts import (
    stage_artifact_filename,
    validate_stage_artifact_filename,
)
from application.dto.measurement_results import MeasurementResult
from application.ports.stage_result_repository import (
    MeasurementStage,
    StageResultError,
    coerce_measurement_stage,
)
from domain.run.models import SHA256_PATTERN, ArtifactKind, coerce_status


__all__ = [
    "StageArtifactReference",
    "StageArtifactRepository",
    "stage_artifact_filename",
    "validate_stage_artifact_filename",
]


@dataclass(frozen=True)
class StageArtifactReference:
    """一份已写入阶段目录的交付产物及其文件事实。

    ``path`` 是阶段目录内的实际文件位置，``measurement_id`` 是它对应的正式结果
    身份，``sha256`` 与 ``size_bytes`` 供调用方展示或审计。字段都是端口返回的事实，
    应用层不需要重新读取文件。
    """

    stage: MeasurementStage | str
    measurement_id: str
    kind: ArtifactKind | str
    path: Path
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage", coerce_measurement_stage(self.stage))
        kind = coerce_status(self.kind, ArtifactKind, field_name="产物类型")
        object.__setattr__(self, "kind", kind)
        try:
            expected_name = stage_artifact_filename(self.measurement_id, kind)
        except ValueError as error:
            raise StageResultError(
                f"阶段产物文件名必须由测量身份构造: {error}"
            ) from error
        try:
            path = Path(self.path)
        except (TypeError, ValueError) as error:
            raise StageResultError(f"阶段产物路径非法: {self.path!r}") from error
        if path.name != expected_name:
            raise StageResultError(
                "阶段产物文件名必须由 measurement_id 构造："
                f"{path.name!r} != {expected_name!r}"
            )
        object.__setattr__(self, "path", path)
        digest = self.sha256 if isinstance(self.sha256, str) else ""
        if not SHA256_PATTERN.fullmatch(digest):
            raise StageResultError("阶段产物摘要必须是 64 位小写十六进制 sha256")
        object.__setattr__(self, "sha256", digest)
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes < 0
        ):
            raise StageResultError("阶段产物大小必须是非负整数")


class StageArtifactRepository(Protocol):
    """阶段目录交付产物的写入与定位端口。"""

    def save(
        self,
        stage: MeasurementStage | str,
        measurement_id: str,
        kind: ArtifactKind | str,
        result: MeasurementResult,
    ) -> StageArtifactReference:
        """把一份交付产物原子写入阶段目录，返回实际文件事实。

        结果必须是该阶段的正式 ``completed`` 结果，且自身的 ``measurement_id`` 与
        参数一致；产物只能落在阶段目录内、文件名只能由身份与产物类型构造。可预期的
        生成/写入失败必须抛出 ``application.ports.result_artifacts.
        ArtifactGenerationError``，不得留下半截文件，也不得覆盖正式结果 JSON。
        """
        ...

    def locate(
        self,
        stage: MeasurementStage | str,
        measurement_id: str,
        kind: ArtifactKind | str,
    ) -> Path | None:
        """返回阶段目录内已存在的产物位置；没有可用文件时返回 ``None``。

        定位不扫描目录、不按时间选择，也不得把其它测量身份的产物当成目标。参数
        非法或目标在展开符号链接后不再直接位于阶段目录内时抛出
        :class:`application.ports.result_artifacts.ArtifactGenerationError`，调用方
        据此区分“无法定位”和“尚未生成”。
        """
        ...
