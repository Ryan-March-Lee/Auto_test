"""整理一次测试运行的阶段 0.1 基线样例。

用法：测试完成后在项目根目录执行 ``python collect_baseline.py``。
脚本只读取已有结果并创建新的基线目录，不会修改或覆盖测试结果。
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from project_paths import PROJECT_ROOT, TEST_RESULTS_DIR
from result_storage import validate_run_id


MEASUREMENT_PATTERNS = {
    "cable_loss": "cable_loss_results*.json",
    "driver_mapping": "driver_power_mapping_*.json",
    "amplifier_measurement": "amplifier_measurement_*.json",
}
MODEL_PATTERNS = {
    "cable_loss_model": "cable_loss_model.json",
    "driver_mapping_model": "driver_power_mapping_model.json",
    "amplifier_measurement_model": "amplifier_measurement_model.json",
}
REPORT_PATTERNS = ("*.html", "*.pdf", "*.csv")
PACKAGE_NAMES = ("PySide6", "matplotlib", "numpy", "pandas", "seaborn", "pyvisa", "markdown", "requests")


def _latest(paths: Iterable[Path]) -> Optional[Path]:
    candidates = [path for path in paths if path.is_file()]
    return max(candidates, key=lambda path: path.stat().st_mtime, default=None)


def _relative_or_name(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


def _git_commit() -> Optional[str]:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return completed.stdout.strip() or None


def _dependency_versions() -> Dict[str, Optional[str]]:
    versions: Dict[str, Optional[str]] = {}
    for package_name in PACKAGE_NAMES:
        try:
            versions[package_name] = metadata.version(package_name)
        except metadata.PackageNotFoundError:
            versions[package_name] = None
    return versions


def _copy_artifact(source: Path, destination: Path, project_root: Path, artifact_root: Path) -> Dict[str, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return {
        "source": _relative_or_name(source, project_root),
        "path": str(destination.relative_to(artifact_root)),
    }


def collect_baseline(
    *,
    results_dir: Path = TEST_RESULTS_DIR,
    output_dir: Path = PROJECT_ROOT / "baseline",
    run_id: Optional[str] = None,
) -> Tuple[Path, Dict[str, object]]:
    """只从指定运行目录收集结果；缺少关联文件时将基线标记为不完整。"""
    results_dir = Path(results_dir)
    output_dir = Path(output_dir)
    if run_id is None:
        raise ValueError("必须指定 run_id")
    validate_run_id(run_id)
    if not results_dir.exists():
        raise FileNotFoundError(f"结果目录不存在: {results_dir}")

    run_directory = results_dir / run_id
    if not run_directory.is_dir():
        raise FileNotFoundError(f"运行目录不存在: {run_directory}")

    baseline_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    baseline_dir = output_dir / "collected" / baseline_id
    baseline_dir.mkdir(parents=True, exist_ok=False)

    artifacts: Dict[str, List[Dict[str, str]]] = {}
    selected: Dict[str, Optional[Path]] = {}
    for kind, pattern in MEASUREMENT_PATTERNS.items():
        candidates = sorted(run_directory.glob(pattern))
        selected[kind] = candidates[0] if candidates else None
        source = selected[kind]
        if source:
            destination = baseline_dir / "results" / source.name
            artifacts[kind] = [_copy_artifact(source, destination, PROJECT_ROOT, baseline_dir)]
        else:
            artifacts[kind] = []

    for kind, filename in MODEL_PATTERNS.items():
        source = run_directory / filename if (run_directory / filename).is_file() else None
        artifacts[kind] = (
            [_copy_artifact(source, baseline_dir / "results" / filename, PROJECT_ROOT, baseline_dir)]
            if source else []
        )

    # Snapshot and reports must come from the exact same run as measurement data.
    for kind, filename in (
        ("test_plan_snapshot", "test_plan_snapshot.json"),
        ("run_mapping_snapshot", "run_mapping_snapshot.json"),
        ("run_metadata", "run_metadata.json"),
        ("conversion_review", "conversion_review.json"),
    ):
        source = run_directory / filename if (run_directory / filename).is_file() else None
        if source:
            artifacts[kind] = [_copy_artifact(source, baseline_dir / "snapshots" / filename, PROJECT_ROOT, baseline_dir)]
        else:
            artifacts[kind] = []

    artifacts["config"] = []

    reports = []
    for pattern in REPORT_PATTERNS:
        report = _latest(run_directory.glob(pattern))
        if report:
            reports.append(_copy_artifact(report, baseline_dir / "reports" / report.name, PROJECT_ROOT, baseline_dir))
    artifacts["reports"] = reports

    required_kinds = ("test_plan_snapshot", "run_mapping_snapshot", "run_metadata", *MEASUREMENT_PATTERNS)
    integrity_issues = []
    for kind in required_kinds:
        paths = artifacts.get(kind, [])
        if not paths:
            integrity_issues.append(f"missing:{kind}")
            continue
        artifact_path = baseline_dir / paths[0]["path"]
        try:
            content = json.loads(artifact_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            integrity_issues.append(f"invalid_json:{kind}")
            continue
        if not isinstance(content, dict):
            integrity_issues.append(f"invalid_root:{kind}")
            continue
        if content.get("run_id") != run_id:
            integrity_issues.append(f"run_id_mismatch:{kind}")

    manifest: Dict[str, object] = {
        "schema_version": "1.0",
        "baseline_id": baseline_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "purpose": "阶段 0.1 基线样例",
        "source": {
            "results_directory": _relative_or_name(results_dir, PROJECT_ROOT),
            "requested_run_id": run_id,
        },
        "environment": {
            "python_version": platform.python_version(),
            "python_executable": sys.executable,
            "platform": platform.platform(),
            "git_commit": _git_commit(),
            "dependencies": _dependency_versions(),
        },
        "artifacts": artifacts,
        "complete": not integrity_issues,
        "integrity_issues": integrity_issues,
        "measurement_status": {
            kind: "available" if files else "not_found"
            for kind, files in artifacts.items()
            if kind in MEASUREMENT_PATTERNS
        },
        "notes": [
            "本清单由 collect_baseline.py 自动生成。",
            "只有指定运行目录中的文件会被收集；缺少快照或任一测量结果时 complete=false。",
            "请在真实设备项目中另行确认接线和安全清理状态。",
        ],
    }
    manifest_path = baseline_dir / "baseline_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return baseline_dir, manifest


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="自动整理阶段 0.1 基线样例")
    parser.add_argument("--run-id", required=True, help="只收集指定运行目录中的结果")
    parser.add_argument("--results-dir", type=Path, default=TEST_RESULTS_DIR)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "baseline")
    args = parser.parse_args(argv)
    try:
        baseline_dir, manifest = collect_baseline(
            results_dir=args.results_dir,
            output_dir=args.output_dir,
            run_id=args.run_id,
        )
    except (OSError, ValueError) as error:
        print(f"基线整理失败: {error}", file=sys.stderr)
        return 1

    print(f"基线整理完成: {baseline_dir}")
    print(f"清单文件: {baseline_dir / 'baseline_manifest.json'}")
    for kind, status in manifest["measurement_status"].items():
        print(f"{kind}: {status}")
    print(f"complete: {str(manifest['complete']).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
