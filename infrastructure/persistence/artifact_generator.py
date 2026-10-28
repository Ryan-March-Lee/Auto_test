"""自动归档交付产物的基础设施适配器：把正式结果渲染成运行目录内的 CSV。

本适配器实现 :class:`application.ports.result_artifacts.ResultArtifactGeneratorPort`，
是应用层 ``analysis`` 能力的唯一出口（``application`` 不得导入 ``analysis``）：

- 输入只来自运行 manifest **已登记并通过校验**的正式结果：读取走
  ``RunRepositoryPort.read_run_json()``（缺登记、被替换、大小不一致都被拒绝），
  再用唯一解析入口 ``measurement_result_from_document()`` 转成正式结果 DTO；
- 输出只能落在 ``runs/<run_id>/artifacts/`` 下的受控相对路径：默认路径由
  :func:`domain.run.models.default_artifact_path` 集中给出，适配器不自己拼文件名，
  也拒绝任何不在 ``artifacts/`` 下的目标；
- CSV 复用 :func:`analysis.reporting.export_csv`，先写同目录临时文件再原子替换，
  因此失败不会留下被 manifest 引用的半截文件；
- 可预期的失败（空数据、结果不可读、目标不可写、产物类型/阶段不支持）统一抛出
  :class:`ArtifactGenerationError`，由归档服务把该产物登记为 ``failed`` 并保留正式结果；
  其它异常不兜底，避免把编程缺陷伪装成“产物生成失败”。

``supported_kinds`` 声明本适配器当前只生成 CSV；报告生成按重构计划步骤 5 接入，届时
再扩展这里并同步 ``supported_kinds``。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from analysis.reporting import export_csv
from application.dto.measurement_results import (
    MeasurementResult,
    MeasurementType,
    measurement_result_from_document,
)
from application.ports.result_artifacts import (
    ArtifactGenerationError,
    ArtifactGenerationRequest,
    GeneratedArtifact,
)
from application.ports.run_repository import RunLayout, RunRepositoryPort
from domain.run.models import (
    ARTIFACTS_DIRNAME,
    ARTIFACT_PATH_PREFIX,
    STAGE_AMPLIFIER,
    ArtifactKind,
)
from infrastructure.persistence.run_directory import file_sha256_and_size


class CsvArtifactGenerator:
    """主功放 CSV 交付产物的生成端口实现（重构计划步骤 4）。"""

    #: 本适配器当前能生成的产物类型；报告在计划步骤 5 接入。
    supported_kinds = frozenset({ArtifactKind.CSV})

    def __init__(self, runs: RunRepositoryPort) -> None:
        if not callable(getattr(runs, "read_run_json", None)):
            raise TypeError("CSV 产物生成需要运行仓储提供 read_run_json()")
        self.runs = runs

    # -- 端口入口 -----------------------------------------------------------

    def generate(self, request: ArtifactGenerationRequest) -> GeneratedArtifact:
        """把已登记的正式结果写成 ``artifacts/<stage>.csv``，返回路径与校验值。"""
        if not isinstance(request, ArtifactGenerationRequest):
            raise TypeError("产物生成请求必须是 ArtifactGenerationRequest")
        if request.kind is not ArtifactKind.CSV:
            raise ArtifactGenerationError(f"{request.kind.value} 产物生成尚未接入")
        if request.stage != STAGE_AMPLIFIER:
            # 契约 2.2：线损与驱动映射没有 CSV 交付产物，它们应登记 not_applicable。
            raise ArtifactGenerationError(
                f"阶段 {request.stage} 没有 CSV 交付产物，只有主功放结果可以导出 CSV"
            )
        layout = self._layout(request.run_id)
        target = self._target_path(layout, request.target_path)
        result = self._result(request)
        self._write(result, target)
        digest, size = file_sha256_and_size(target)
        return GeneratedArtifact(
            path=request.target_path, sha256=digest, size_bytes=size
        )

    # -- 输入边界 -----------------------------------------------------------

    def _layout(self, run_id: str) -> RunLayout:
        """取得运行目录布局；仓储不提供 ``layout()`` 时按产物生成失败上报。"""
        layout_factory = getattr(self.runs, "layout", None)
        if not callable(layout_factory):
            raise ArtifactGenerationError(
                "运行仓储不提供 layout()，无法定位运行目录内的产物目标"
            )
        try:
            layout = layout_factory(run_id)
        except (OSError, ValueError) as error:
            raise ArtifactGenerationError(
                f"运行 {run_id} 的目录布局不可用: {error}"
            ) from error
        if not Path(layout.run_directory).is_dir():
            raise ArtifactGenerationError(f"运行目录不存在: {layout.run_directory}")
        return layout

    def _target_path(self, layout: RunLayout, relative_path: str) -> Path:
        """把受控相对路径解析到 ``artifacts/`` 之下，越界目标直接拒绝。"""
        if not relative_path.startswith(ARTIFACT_PATH_PREFIX):
            raise ArtifactGenerationError(
                f"产物目标必须位于 {ARTIFACT_PATH_PREFIX} 下: {relative_path}"
            )
        name = relative_path[len(ARTIFACT_PATH_PREFIX):]
        target = Path(layout.run_directory) / ARTIFACTS_DIRNAME
        return target.joinpath(*name.split("/"))

    def _result(self, request: ArtifactGenerationRequest) -> MeasurementResult:
        """读取已登记的正式结果并解析成正式结果 DTO。"""
        try:
            document = self.runs.read_run_json(
                request.run_id, request.source_result
            )
        except (OSError, ValueError) as error:
            # FileNotFoundError 是 OSError：缺登记、缺文件、校验值不一致都在这里。
            raise ArtifactGenerationError(
                f"正式结果不可读或未登记，拒绝生成 CSV: {request.source_result}"
                f"（{error}）"
            ) from error
        if not isinstance(document, Mapping):
            raise ArtifactGenerationError(
                f"正式结果不是 JSON 对象: {request.source_result}"
            )
        try:
            result = measurement_result_from_document(document)
        except ValueError as error:
            raise ArtifactGenerationError(
                f"正式结果无法解析为正式结果 DTO: {error}"
            ) from error
        if result.measurement_type is not MeasurementType.AMPLIFIER:
            raise ArtifactGenerationError(
                "CSV 交付产物只来自主功放结果，实际为 "
                f"{result.measurement_type.value}: {request.source_result}"
            )
        if not result.report_rows():
            # 空数据是显式失败，不用“空文件”或间接的 FileNotFoundError 表达。
            raise ArtifactGenerationError(
                f"正式结果没有可导出的 CSV 数据行: {request.source_result}"
            )
        return result

    # -- 写入边界 -----------------------------------------------------------

    @staticmethod
    def _write(result: MeasurementResult, target: Path) -> None:
        """渲染 CSV 到临时文件后原子替换目标，失败不留下半截产物。"""
        temporary = target.with_name(target.name + ".tmp")
        try:
            export_csv(result, temporary)
            os.replace(temporary, target)
        except (OSError, ValueError) as error:
            _remove_quietly(temporary)
            raise ArtifactGenerationError(f"CSV 写入失败: {error}") from error


def _remove_quietly(path: Path) -> None:
    """清理临时文件；清理失败不影响已经上报的产物失败原因。"""
    try:
        path.unlink()
    except OSError:
        pass


__all__ = ["CsvArtifactGenerator"]
