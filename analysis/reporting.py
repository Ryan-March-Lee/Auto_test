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


def render_html_report(
    result: ReportResult,
    destination: str | Path,
    *,
    title: str = "PA 测量报告",
    plot_paths: Iterable[str | Path] = (),
) -> Path:
    """Assemble a diagnostic HTML report without touching measurement objects."""
    result = require_report_result(result)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    images = "".join(
        f'<li><img src="{html.escape(str(path))}" alt="measurement plot"></li>'
        for path in plot_paths
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
        f"<p>points: {result.point_count}</p><ul>{images}</ul>"
        f"<pre>{html.escape(json.dumps(body, ensure_ascii=False, indent=2))}</pre>"
        "</body></html>"
    )
    destination.write_text(content, encoding="utf-8")
    return destination
