"""File-oriented report adapters consuming only domain result models."""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import Any, Iterable

from domain.models import MeasurementResult


def _rows(result: MeasurementResult) -> list[dict[str, Any]]:
    rows = []
    for point in result.points:
        row = point.to_dict()
        row["measurement_type"] = result.measurement_type
        row["run_id"] = result.run_id
        rows.append(_flatten(row))
    return rows


def _flatten(value: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for key, item in value.items():
        result[key] = json.dumps(item, ensure_ascii=False) if isinstance(item, (dict, list)) else item
    return result


def export_csv(result: MeasurementResult, destination: str | Path) -> Path:
    """Export all model points using a stable union of field names."""
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
    result: MeasurementResult,
    destination: str | Path,
    *,
    title: str = "PA 测量报告",
    plot_paths: Iterable[str | Path] = (),
) -> Path:
    """Assemble a diagnostic HTML report without touching measurement objects."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    images = "".join(
        f'<li><img src="{html.escape(str(path))}" alt="measurement plot"></li>'
        for path in plot_paths
    )
    body = {
        "run_id": result.run_id,
        "measurement_type": result.measurement_type,
        "status": result.status.value,
        "schema_version": result.schema_version,
        "method_version": result.method_version,
        "point_count": len(result.points),
        "plan_snapshot": result.plan_snapshot,
        "resource_snapshot": result.resource_snapshot,
        "derived_metrics": result.derived_metrics,
    }
    content = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title></head><body>"
        f"<h1>{html.escape(title)}</h1><p>run_id: {html.escape(result.run_id)}</p>"
        f"<p>measurement_type: {html.escape(result.measurement_type)}</p>"
        f"<p>points: {len(result.points)}</p><ul>{images}</ul>"
        f"<pre>{html.escape(json.dumps(body, ensure_ascii=False, indent=2, default=str))}</pre>"
        "</body></html>"
    )
    destination.write_text(content, encoding="utf-8")
    return destination
