"""阶段目录交付产物适配器：把 CSV 与测试报告写到正式结果旁边。

重构计划步骤 20 起，主功放等阶段的交付产物与正式结果同处
``test_results/<stage>/``，文件名由结果的 ``measurement_id`` 决定：

```text
test_results/amplifier/<id>.json
test_results/amplifier/<id>.csv
test_results/amplifier/<id>_report.html
```

本模块实现 :class:`application.ports.stage_artifacts.StageArtifactRepository`：

- 输入是已校验的正式结果 DTO 与明确的阶段/测量身份；不接受运行目录或任意路径；
- 目标只能是阶段目录内的直接文件，文件名由身份构造；写入使用同目录临时文件加
  原子替换，不会覆盖正式结果 JSON，也不会留下半截产物；
- CSV 复用 :func:`analysis.reporting.export_csv`，报告复用
  :func:`analysis.reporting.render_html_report`；报告只链接真实存在、且与本次
  测量身份相同的 CSV；
- 可预期失败统一抛出 :class:`ArtifactGenerationError`，由调用方降级为“该产物
  不可用”，不改变正式结果或阶段状态。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from uuid import uuid4

from analysis.reporting import export_csv, render_html_report
from application.dto.measurement_artifacts import stage_artifact_filename
from application.dto.measurement_results import (
    AmplifierMeasurementResult,
    MeasurementResult,
    MeasurementStatus,
)
from application.ports.result_artifacts import ArtifactGenerationError
from application.ports.stage_artifacts import StageArtifactReference
from application.ports.stage_result_repository import (
    MeasurementStage,
    StageResultError,
    coerce_measurement_stage,
    require_stage_result,
)
from domain.run.models import ArtifactKind, coerce_status
from infrastructure.filesystem.paths import DEFAULT_PATHS, PathService
from infrastructure.logging.app_logging import get_logger
from infrastructure.persistence.run_directory import file_sha256_and_size


logger = get_logger(__name__)

_REPORT_TITLE = "PA 测量报告"

# 报告图表渲染入口：返回 ``(图表名称 -> PNG 路径, 失败原因)``。默认实现留在
# analysis 边界；注入的实现同样只返回事实，不改变报告主体。
StageReportPlotSet = tuple[dict[str, Path], tuple[str, ...]]
StageReportPlotRenderer = Callable[
    [AmplifierMeasurementResult, Path, str], StageReportPlotSet
]


def _default_report_plots(
    result: AmplifierMeasurementResult, output_dir: Path, measurement_id: str
) -> StageReportPlotSet:
    """渲染与报告同目录的 PNG 图表；前缀带上测量身份，避免同目录互相覆盖。"""
    from analysis.visualization import DataVisualization

    visualizer = DataVisualization(
        output_dir=output_dir,
        filename_prefix=f"{measurement_id}_",
        include_pdf=False,
        dpi=150,
        quiet=True,
    )
    return visualizer.create_report_plots(result)


class FileStageArtifactRepository:
    """基于阶段目录的 CSV/报告交付产物写入与定位适配器。"""

    def __init__(
        self,
        *,
        paths: PathService | None = None,
        report_plot_renderer: StageReportPlotRenderer | None = None,
    ) -> None:
        if report_plot_renderer is not None and not callable(report_plot_renderer):
            raise TypeError("阶段报告图表渲染入口必须可调用")
        self._paths = paths if paths is not None else DEFAULT_PATHS
        self._plot_renderer = report_plot_renderer or _default_report_plots

    # -- 写入 ---------------------------------------------------------------

    def save(
        self,
        stage: MeasurementStage | str,
        measurement_id: str,
        kind: ArtifactKind | str,
        result: MeasurementResult,
    ) -> StageArtifactReference:
        """把一份交付产物原子写入其阶段目录，返回实际文件事实。"""
        target_stage = coerce_measurement_stage(stage)
        artifact_kind = coerce_status(kind, ArtifactKind, field_name="产物类型")
        try:
            formal = require_stage_result(target_stage, result)
        except StageResultError as error:
            raise ArtifactGenerationError(str(error)) from error
        if formal.status is not MeasurementStatus.COMPLETED:
            raise ArtifactGenerationError(
                f"只有 completed 阶段结果可以生成 {artifact_kind.value} 产物: "
                f"{formal.status.value}"
            )
        if formal.measurement_id != measurement_id:
            raise ArtifactGenerationError(
                "交付产物必须使用结果自身的 measurement_id："
                f"{measurement_id!r} != {formal.measurement_id!r}"
            )
        if not isinstance(formal, AmplifierMeasurementResult):
            raise ArtifactGenerationError(
                "只有主功放结果会生成 CSV 或测试报告"
            )
        if not formal.report_rows():
            raise ArtifactGenerationError(
                f"正式结果没有可生成 {artifact_kind.value} 的数据行: "
                f"{measurement_id}"
            )
        directory = self._directory_for(target_stage)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / stage_artifact_filename(
                measurement_id, artifact_kind
            )
            self._require_inside_stage_directory(directory, target)
            if artifact_kind is ArtifactKind.CSV:
                self._publish(
                    target,
                    lambda destination: export_csv(formal, destination),
                    error_label="CSV 写入失败",
                )
            else:
                self._write_report(target, formal, measurement_id)
        except (OSError, ValueError) as error:
            raise ArtifactGenerationError(
                f"{artifact_kind.value} 产物写入失败: {error}"
            ) from error
        digest, size_bytes = file_sha256_and_size(target)
        return StageArtifactReference(
            stage=target_stage,
            measurement_id=formal.measurement_id,
            kind=artifact_kind,
            path=target,
            sha256=digest,
            size_bytes=size_bytes,
        )

    def _write_report(
        self,
        target: Path,
        result: AmplifierMeasurementResult,
        measurement_id: str,
    ) -> None:
        """生成报告；图表失败只降级为备注，不放弃整份报告。"""
        plot_paths, plot_failures = self._render_plots(
            result, target.parent, measurement_id
        )
        for failure in plot_failures:
            logger.warning(
                "阶段报告图表生成失败 measurement_id=%s detail=%s",
                measurement_id,
                failure,
            )
        notes = tuple(f"图表生成失败：{failure}" for failure in plot_failures)
        csv_name = stage_artifact_filename(measurement_id, ArtifactKind.CSV)
        data_file = csv_name if (target.parent / csv_name).is_file() else None
        self._publish(
            target,
            lambda destination: render_html_report(
                result,
                destination,
                title=_REPORT_TITLE,
                data_file=data_file,
                plot_paths=tuple(plot_paths.values()),
                notes=notes,
            ),
            error_label="报告写入失败",
        )

    def _render_plots(
        self,
        result: AmplifierMeasurementResult,
        output_dir: Path,
        measurement_id: str,
    ) -> StageReportPlotSet:
        """调用图表渲染入口；渲染器不可用时退化为无图报告。"""
        try:
            plots, failures = self._plot_renderer(result, output_dir, measurement_id)
        except (OSError, ValueError, RuntimeError) as error:
            reason = str(error).strip() or type(error).__name__
            logger.warning(
                "阶段报告图表渲染不可用 measurement_id=%s error=%s",
                measurement_id,
                reason,
            )
            return {}, (f"图表渲染不可用: {reason}",)
        if not isinstance(plots, Mapping):
            raise TypeError("阶段报告图表渲染入口必须返回图表映射")
        normalized: dict[str, Path] = {}
        for name, path in plots.items():
            if not isinstance(name, str) or not isinstance(path, (str, Path)):
                raise TypeError("阶段报告图表映射必须是 名称 -> 路径")
            normalized[name] = Path(path)
        if isinstance(failures, (str, bytes)) or not isinstance(
            failures, (list, tuple)
        ):
            raise TypeError("阶段报告图表失败原因必须是字符串序列")
        return normalized, tuple(str(item) for item in failures)

    # -- 定位 ---------------------------------------------------------------

    def locate(
        self,
        stage: MeasurementStage | str,
        measurement_id: str,
        kind: ArtifactKind | str,
    ) -> Path | None:
        """返回阶段目录内已存在的产物文件；没有时返回 ``None``。"""
        target_stage = coerce_measurement_stage(stage)
        artifact_kind = coerce_status(kind, ArtifactKind, field_name="产物类型")
        directory = self._directory_for(target_stage)
        target = directory / stage_artifact_filename(
            measurement_id, artifact_kind
        )
        self._require_inside_stage_directory(directory, target)
        return target if target.is_file() else None

    # -- 内部工具 -----------------------------------------------------------

    def _directory_for(self, stage: MeasurementStage) -> Path:
        directories = {
            MeasurementStage.CABLE_LOSS: self._paths.cable_loss_results_dir,
            MeasurementStage.DRIVER_MAPPING: self._paths.driver_mapping_results_dir,
            MeasurementStage.AMPLIFIER: self._paths.amplifier_results_dir,
        }
        return directories[stage]

    @staticmethod
    def _require_inside_stage_directory(directory: Path, target: Path) -> None:
        """确认目标直接位于阶段目录内，展开符号链接后仍然成立。"""
        try:
            root = directory.resolve(strict=False)
            resolved = target.resolve(strict=False)
        except (OSError, RuntimeError) as error:
            raise ArtifactGenerationError(
                f"阶段产物目标路径不可解析: {target}（{error}）"
            ) from error
        if resolved.parent != root:
            raise ArtifactGenerationError(
                f"阶段产物必须直接位于阶段目录: {target}"
            )

    @staticmethod
    def _publish(
        target: Path, write: Callable[[Path], None], *, error_label: str
    ) -> None:
        """渲染到同目录临时文件后原子替换目标，失败不留下半截产物。"""
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            write(temporary)
            os.replace(temporary, target)
        except (OSError, ValueError) as error:
            raise ArtifactGenerationError(f"{error_label}: {error}") from error
        finally:
            # 无论成功、可预期失败还是渲染器抛出的其它异常，都不留下临时文件；
            # 目标在 ``os.replace`` 之前始终不存在，因此后续读取看不到半截产物。
            _remove_quietly(temporary)


def _remove_quietly(path: Path) -> None:
    """清理临时文件；清理失败不影响已经上报的产物失败原因。"""
    try:
        path.unlink()
    except OSError:
        pass


__all__ = ["FileStageArtifactRepository"]
