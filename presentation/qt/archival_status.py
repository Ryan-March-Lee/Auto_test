"""把结构化归档结果转成页面文案与动作状态。

本模块是纯 Python：不依赖 Qt、不读取文件、不判断路径是否存在，也不拼接导出规则。
页面只把 :class:`ArchivalSummary` 写成文案，并据此启用“打开结果目录/报告”按钮；
产物是否可用由应用层归档服务与 manifest 决定。

对照重构计划步骤 6，四类状态各有独立提示：

- 成功：结果已保存且适用产物已生成；
- 部分产物失败：正式结果保留，提示可定位的运行 ID 与重新生成入口；
- 测试异常：本次没有归档结果，给出失败原因；
- 取消/紧急停止：本次没有归档结果，给出停止原因。

归档进行中同样有明确状态（:func:`summarize_archival_running` 与
:func:`summarize_archival_missing`），避免用户误以为测试结束后一切都已完成。
"""

from __future__ import annotations

from dataclasses import dataclass

from application.dto.archival import (
    ArchivalOutcome,
    ArchivalStatus,
    ArchivalTestStatus,
    ArchivedArtifact,
)
from domain.run.models import ArtifactKind, ArtifactStatus

from .measurement_state import MeasurementKind


TONE_INFO = "info"
TONE_SUCCESS = "success"
TONE_WARNING = "warning"
TONE_ERROR = "error"

_KIND_LABELS = {
    MeasurementKind.CABLE_LOSS.value: "线损",
    MeasurementKind.DRIVER_MAPPING.value: "驱动映射",
    MeasurementKind.AMPLIFIER.value: "主功放",
}

_ARTIFACT_LABELS = {
    ArtifactKind.CSV.value: "CSV 数据",
    ArtifactKind.REPORT.value: "测试报告",
}

# 归档结果里只出现这两类交付产物；未知类型直接拒绝，避免页面显示半截状态。
_DELIVERABLE_KINDS = frozenset({ArtifactKind.CSV.value, ArtifactKind.REPORT.value})


@dataclass(frozen=True)
class ArchivalSummary:
    """页面显示一次归档结果所需的全部信息。"""

    headline: str
    detail: str
    tone: str = TONE_INFO
    run_id: str | None = None
    report_path: str | None = None

    def __post_init__(self) -> None:
        for name in ("headline", "detail"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"归档提示的 {name} 必须是非空字符串")
        if self.tone not in {TONE_INFO, TONE_SUCCESS, TONE_WARNING, TONE_ERROR}:
            raise ValueError(f"归档提示的语气必须是已知取值，收到: {self.tone!r}")
        if self.run_id is not None and (
            not isinstance(self.run_id, str) or not self.run_id.strip()
        ):
            raise ValueError("归档提示的运行 ID 必须是非空字符串")
        if self.report_path is not None:
            if self.run_id is None:
                raise ValueError("只有定位到运行时才允许提供报告位置")
            if not isinstance(self.report_path, str) or not self.report_path.strip():
                raise ValueError("报告位置必须是非空字符串")

    @property
    def can_open_location(self) -> bool:
        """是否可以提供“打开结果目录”：只有一个已定位的运行才可以。"""
        return self.run_id is not None

    @property
    def can_open_report(self) -> bool:
        """是否可以提供“打开测试报告”：只有已生成的报告才可以。"""
        return self.run_id is not None and self.report_path is not None


def summarize_archival(outcome: ArchivalOutcome) -> ArchivalSummary:
    """把一次归档结果转成页面文案。

    ``outcome`` 必须是正式归档 DTO；页面据此生成文案，不再自行判断产物是否可用。
    """
    if not isinstance(outcome, ArchivalOutcome):
        raise TypeError(
            "归档展示只接受 ArchivalOutcome，实际: " + type(outcome).__name__
        )
    label = _kind_label(outcome.stage)
    run_id = outcome.run_id
    test_status = outcome.test_status
    if test_status is ArchivalTestStatus.CANCELLED:
        return ArchivalSummary(
            headline=f"{label}测试已取消：本次未归档结果",
            detail=f"运行 {run_id} 的阶段结果已清理，未生成交付产物。",
            tone=TONE_WARNING,
            run_id=run_id,
        )
    if test_status is ArchivalTestStatus.FAILED:
        if outcome.formal_result_path is None:
            detail = f"运行 {run_id} 在失败前没有保存正式结果，未生成交付产物。"
        else:
            detail = (
                f"运行 {run_id} 保留了失败前的正式结果 "
                f"{outcome.formal_result_path}；交付产物不适用。"
            )
        return ArchivalSummary(
            headline=f"{label}测试失败：未生成交付产物",
            detail=detail,
            tone=TONE_ERROR,
            run_id=run_id,
        )
    if test_status is ArchivalTestStatus.PARTIAL:
        return ArchivalSummary(
            headline=f"{label}阶段结果已自动归档",
            detail=(
                f"正式结果 {outcome.formal_result_path} 已写入运行 {run_id}；"
                "后续阶段尚未完成，CSV 与测试报告不适用于本阶段。"
            ),
            tone=TONE_SUCCESS,
            run_id=run_id,
        )
    return _summarize_completed(outcome, label=label, run_id=run_id)


def _summarize_completed(
    outcome: ArchivalOutcome, *, label: str, run_id: str
) -> ArchivalSummary:
    """完整成功运行的归档文案：全部完成、仍在生成、部分产物失败三种。"""
    lines = "；".join(_artifact_line(item) for item in outcome.artifacts)
    status = outcome.archive_status
    if status is ArchivalStatus.COMPLETED:
        return ArchivalSummary(
            headline=f"{label}结果已自动归档",
            detail=f"运行 {run_id}：{lines}。",
            tone=TONE_SUCCESS,
            run_id=run_id,
            report_path=_completed_report_path(outcome),
        )
    if status is ArchivalStatus.PENDING:
        return ArchivalSummary(
            headline=f"{label}结果已保存，交付产物仍在生成",
            detail=f"运行 {run_id}：{lines}。",
            tone=TONE_INFO,
            run_id=run_id,
        )
    return ArchivalSummary(
        headline=f"{label}结果已保存，但部分交付产物生成失败",
        detail=(
            f"运行 {run_id}：{lines}。正式结果已保留；"
            f"可在“历史运行”页按运行 {run_id} 重新生成失败的产物。"
        ),
        tone=TONE_WARNING,
        run_id=run_id,
        report_path=_completed_report_path(outcome),
    )


def summarize_archival_running(kind: MeasurementKind | str) -> ArchivalSummary:
    """测量刚启动时的归档说明：告知结束后会自动归档，不需要第二次保存动作。"""
    label = _kind_label(kind)
    return ArchivalSummary(
        headline=f"{label}测量结束后将自动归档结果",
        detail="完成后正式结果与交付产物会自动写入当前运行，无需再手动导出。",
        tone=TONE_INFO,
    )


def summarize_archival_saving(kind: MeasurementKind | str) -> ArchivalSummary:
    """扫描结束、正在写正式结果并生成产物：避免用户误以为测试已经全部结束。"""
    label = _kind_label(kind)
    return ArchivalSummary(
        headline=f"{label}扫描结束，正在保存结果并生成交付产物…",
        detail="正式结果写入当前运行、交付产物生成完成后，这里会自动更新。",
        tone=TONE_INFO,
    )


def summarize_archival_missing(
    kind: MeasurementKind | str, run_id: str | None
) -> ArchivalSummary:
    """测试已完成但没收到归档结果：如实说明状态未知，并给出可定位的运行 ID。"""
    label = _kind_label(kind)
    if run_id is None:
        detail = "交付产物状态未知；请查看日志确认运行是否已完成。"
    else:
        detail = (
            f"运行 {run_id} 的交付产物状态未知；"
            "请查看日志或“历史运行”页确认，正式结果仍以运行目录为准。"
        )
    return ArchivalSummary(
        headline=f"{label}结果已保存，但未收到归档结果",
        detail=detail,
        tone=TONE_WARNING,
        run_id=run_id,
    )


def summarize_test_failure(kind: MeasurementKind | str, error: str) -> ArchivalSummary:
    """测试异常：本次没有自动归档，失败原因照原样呈现。"""
    label = _kind_label(kind)
    reason = (error or "").strip() or "未提供失败原因"
    return ArchivalSummary(
        headline=f"{label}测试异常：本次未归档结果",
        detail=f"失败原因：{reason}。请处理异常后重新测量。",
        tone=TONE_ERROR,
    )


def summarize_test_cancelled(
    kind: MeasurementKind | str, reason: str
) -> ArchivalSummary:
    """取消或紧急停止：本次没有归档结果，原因是用户操作或安全停止。"""
    label = _kind_label(kind)
    stop_reason = (reason or "").strip() or "用户停止"
    return ArchivalSummary(
        headline=f"{label}测试已停止：本次未归档结果",
        detail=f"停止原因：{stop_reason}；阶段结果已清理，未生成交付产物。",
        tone=TONE_WARNING,
    )


def _kind_label(kind: MeasurementKind | str) -> str:
    value = kind.value if isinstance(kind, MeasurementKind) else str(kind)
    label = _KIND_LABELS.get(value)
    if label is None:
        raise ValueError(f"未知的测量类型: {value!r}")
    return label


def _artifact_line(artifact: ArchivedArtifact) -> str:
    kind = _artifact_label(artifact.kind)
    if artifact.status is ArtifactStatus.COMPLETED:
        return f"{kind}已生成（{artifact.path}）"
    if artifact.status is ArtifactStatus.FAILED:
        return f"{kind}生成失败：{artifact.reason}"
    if artifact.status is ArtifactStatus.PENDING:
        return f"{kind}正在生成"
    return f"{kind}不适用"


def _artifact_label(kind: ArtifactKind | str) -> str:
    value = kind.value if isinstance(kind, ArtifactKind) else str(kind)
    if value not in _DELIVERABLE_KINDS:
        raise ValueError(f"归档展示只支持 CSV 与测试报告，收到: {value!r}")
    return _ARTIFACT_LABELS[value]


def _completed_report_path(outcome: ArchivalOutcome) -> str | None:
    report = outcome.report
    if report.status is ArtifactStatus.COMPLETED:
        return report.path
    return None


__all__ = [
    "ArchivalSummary",
    "TONE_ERROR",
    "TONE_INFO",
    "TONE_SUCCESS",
    "TONE_WARNING",
    "summarize_archival",
    "summarize_archival_missing",
    "summarize_archival_running",
    "summarize_archival_saving",
    "summarize_test_cancelled",
    "summarize_test_failure",
]
