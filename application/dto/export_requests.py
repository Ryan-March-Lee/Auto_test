"""历史结果导出的正式请求与结果 DTO。

导出是一次**应用层**动作：页面只描述“导出哪一条历史结果、导出成什么格式、目标文件
是什么”，由 :class:`application.export_service.ResultExportUseCase` 交给导出端口执行。

约定：

- ``ExportRequest.result_id`` 只能是历史查询端口返回的结果标识；页面不得用任意文件
  路径构造一次导出，也不持有原始文档。
- ``JSON`` / ``CSV`` 必须带目标文件路径（用户在文件对话框中选择的位置）；
  ``REPORT``（测试汇总报告）的输出位置由导出端口决定，请求不得携带目标路径，避免
  两处各自解释“报告放哪”。
- 请求自身只校验结构，能否真正导出（结果是否存在、格式是否适用于该结果类型）由导出
  用例在读取历史结果后判定。
- :class:`ExportOutcome` 只承载**结构化结果**（格式、来源文件名、实际产物位置），
  用户可见文案由界面层按自己的措辞生成；应用层不拼接本地化提示。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ExportFormat(str, Enum):
    """历史结果支持的导出格式。

    ``REPORT`` 是测试汇总报告：导出端口生成报告文件并返回**实际**产物位置，格式名称
    与产物保持一致（不再把 HTML 汇总报告称作 PDF）。
    """

    JSON = "json"
    CSV = "csv"
    REPORT = "report"

    @property
    def label(self) -> str:
        """用户可见的格式名称。"""
        return {
            ExportFormat.JSON: "JSON",
            ExportFormat.CSV: "CSV",
            ExportFormat.REPORT: "测试报告",
        }[self]

    @property
    def suffix(self) -> str | None:
        """目标文件默认后缀；``REPORT`` 由导出端口决定输出位置。"""
        return {
            ExportFormat.JSON: "json",
            ExportFormat.CSV: "csv",
            ExportFormat.REPORT: None,
        }[self]

    @property
    def needs_target(self) -> bool:
        """是否需要调用方指定目标文件。"""
        return self is not ExportFormat.REPORT


@dataclass(frozen=True)
class ExportRequest:
    """一次导出意图；只能指向历史仓储返回的结果标识。"""

    result_id: str
    format: ExportFormat
    target: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.result_id, str) or not self.result_id.strip():
            raise ValueError("导出请求必须携带历史结果标识")
        if not isinstance(self.format, ExportFormat):
            raise ValueError(
                f"未知导出格式: {self.format!r}；只接受 ExportFormat 成员"
            )
        if self.format.needs_target:
            if not isinstance(self.target, str) or not self.target.strip():
                raise ValueError(f"{self.format.label} 导出必须指定目标文件")
        elif self.target is not None:
            raise ValueError(
                f"{self.format.label} 的输出位置由导出端口决定，请求不得携带目标路径"
            )

    @property
    def target_path(self) -> str:
        """JSON/CSV 的目标文件；报告请求不携带目标。"""
        if self.target is None:
            raise ValueError(f"{self.format.label} 不使用目标路径")
        return self.target


@dataclass(frozen=True)
class ExportOutcome:
    """一次导出的结构化结果：格式、来源文件名与实际产物位置。"""

    result_id: str
    format: ExportFormat
    filename: str
    artifact_path: str

    def __post_init__(self) -> None:
        if not isinstance(self.filename, str) or not self.filename.strip():
            raise ValueError("导出结果必须记录来源文件名")
        if not isinstance(self.artifact_path, str) or not self.artifact_path.strip():
            raise ValueError("导出结果必须记录实际产物位置")


__all__ = ["ExportFormat", "ExportOutcome", "ExportRequest"]
