"""File-oriented report adapters consuming only the formal result contract.

报告适配器只接受正式结果 DTO（或满足 :class:`analysis.result_source.ReportResult`
的对象），不再读取 ``raw_readings`` / ``plan_snapshot`` 等旧结果字段，也不接受
``Mapping[str, Any]`` 形式的结果。
"""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import Any, Iterable

from analysis.result_source import ReportResult, plain_document, require_report_result


def _rows(result: ReportResult) -> list[dict[str, Any]]:
    rows = []
    for row in result.report_rows():
        entry = dict(row)
        entry["measurement_type"] = result.measurement_type_value
        entry["run_id"] = result.run_id
        rows.append(_flatten(entry))
    return rows


def _flatten(value: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for key, item in value.items():
        result[key] = json.dumps(item, ensure_ascii=False) if isinstance(item, (dict, list)) else item
    return result


def export_csv(result: ReportResult, destination: str | Path) -> Path:
    """Export all result rows using a stable union of field names."""
    result = require_report_result(result)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    rows = _rows(result)
    fieldnames = sorted({key for row in rows for key in row})
    with destination.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        if fieldnames:
            writer.writeheader()
            writer.writerows(rows)
    return destination


# HTML 报告中的指标列：(结果字段名, 表头文案)。字段来自
# ``ReportResult.saturation_points()``；缺失值渲染为“—”，不伪造 0 值。
_METRIC_COLUMNS = (
    ("frequency", "频率 (GHz)"),
    ("output_power", "输出功率 (dBm)"),
    ("input_power", "输入功率 (dBm)"),
    ("gain", "增益 (dB)"),
    ("efficiency", "效率 (%)"),
    ("compression", "压缩 (dB)"),
    ("compression_achieved", "压缩达标"),
)


def render_html_report(
    result: ReportResult,
    destination: str | Path,
    *,
    title: str = "PA 测量报告",
    plot_paths: Iterable[str | Path] = (),
    data_file: str | None = None,
) -> Path:
    """Assemble a diagnostic HTML report without touching measurement objects.

    ``plot_paths`` 与 ``data_file`` 都是**与报告同目录**的兄弟文件名：自动归档把报告和
    图表写进运行目录同一个 ``artifacts/``，报告只能引用文件名，不能引用绝对位置，
    否则交付产物换一台机器就断链。报告内容只来自正式结果 DTO：运行标识、状态、指标
    表、配置快照与计算摘要。

    ``data_file`` 按文件名严格校验（拒绝目录分隔符、盘符与 ``..``），因为它是报告唯一的
    外部数据链接；图表路径取其文件名，因此也只能是同目录兄弟文件。
    """
    result = require_report_result(result)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if data_file is not None:
        data_file = _sibling_file_name(data_file, field_name="报告数据文件名")
    images = "".join(
        f'<li><img src="{html.escape(_plot_file_name(path), quote=True)}" '
        'alt="measurement plot"></li>'
        for path in plot_paths
    )
    gallery = f"<ul>{images}</ul>" if images else ""
    data_link = (
        f'<p>数据文件: <a href="{html.escape(data_file, quote=True)}">'
        f"{html.escape(data_file)}</a></p>"
        if data_file
        else ""
    )
    body = {
        "run_id": result.run_id,
        "measurement_type": result.measurement_type_value,
        "status": result.status_value,
        "point_count": result.point_count,
        "configuration_snapshot": plain_document(result.configuration_snapshot),
        "calculation_summary": plain_document(result.calculation_summary),
    }
    content = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title></head><body>"
        f"<h1>{html.escape(title)}</h1><p>run_id: {html.escape(result.run_id)}</p>"
        f"<p>measurement_type: {html.escape(result.measurement_type_value)}</p>"
        f"<p>status: {html.escape(result.status_value)}</p>"
        f"<p>points: {result.point_count}</p>"
        f"<p>measurement_time: {html.escape(str(result.measurement_time or '—'))}</p>"
        f"<p>source_file: {html.escape(str(result.source_filename or '—'))}</p>"
        f"{data_link}{_metrics_table(result)}{gallery}"
        f"<pre>{html.escape(json.dumps(body, ensure_ascii=False, indent=2))}</pre>"
        "</body></html>"
    )
    destination.write_text(content, encoding="utf-8")
    return destination


def _metrics_table(result: ReportResult) -> str:
    """把压缩点/饱和点指标渲染成表格；没有指标时返回空字符串。"""
    points = list(result.saturation_points())
    if not points:
        return ""
    header = "".join(
        f"<th>{html.escape(label)}</th>" for _, label in _METRIC_COLUMNS
    )
    rows = "".join(
        "<tr>"
        + "".join(
            f"<td>{html.escape(_metric_cell(point.get(name)))}</td>"
            for name, _ in _METRIC_COLUMNS
        )
        + "</tr>"
        for point in points
    )
    return (
        "<h2>测量指标</h2><table><thead><tr>"
        f"{header}</tr></thead><tbody>{rows}</tbody></table>"
    )


def _metric_cell(value: Any) -> str:
    """把指标值渲染成文本：缺失值保持“—”，不补零、不猜测。"""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


def _plot_file_name(path: str | Path) -> str:
    """取图表路径的文件名，并按兄弟文件规则校验。"""
    return _sibling_file_name(Path(path).name, field_name="报告图表文件名")


def _sibling_file_name(value: Any, *, field_name: str) -> str:
    """校验报告引用的是与报告同目录的**单一文件名**。

    报告和它的数据/图表文件总在同一个 ``artifacts/`` 里，因此只能写文件名：绝对路径
    会让交付产物换机器后断链，目录分隔符、盘符、``..`` 会把链接指到报告目录之外。
    这里全部拒绝，而不是“尽量规范化”，避免把调用方的路径错误静默成一份看着正常的
    报告。
    """
    if not isinstance(value, str):
        raise ValueError(f"{field_name} 必须是字符串")
    name = value.strip()
    if not name:
        raise ValueError(f"{field_name} 不能为空")
    if "\x00" in name:
        raise ValueError(f"{field_name} 不能包含空字符: {value!r}")
    if "/" in name or "\\" in name:
        raise ValueError(
            f"{field_name} 只能是同目录的文件名，不能包含目录分隔符: {value!r}"
        )
    if name in (".", "..") or Path(name).name != name:
        raise ValueError(f"{field_name} 不是有效的文件名: {value!r}")
    return name
