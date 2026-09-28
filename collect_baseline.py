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


def collect_batch_baseline(
    *,
    results_dir: Path = TEST_RESULTS_DIR,
    output_dir: Path = PROJECT_ROOT / "baseline",
    cable_loss_run_id: str,
    driver_mapping_run_id: str,
    amplifier_run_id: str,
    report_dir: Optional[Path] = None,
) -> Tuple[Path, Dict[str, object]]:
    """收集允许跨 ``run_id`` 复用结果的一个生产测试批次。"""
    results_dir = Path(results_dir)
    output_dir = Path(output_dir)
    source_runs = {
        "cable_loss": cable_loss_run_id,
        "driver_mapping": driver_mapping_run_id,
        "amplifier_measurement": amplifier_run_id,
    }
    for run_id in source_runs.values():
        validate_run_id(run_id)
        if not (results_dir / run_id).is_dir():
            raise FileNotFoundError(f"运行目录不存在: {results_dir / run_id}")

    baseline_id = f"{datetime.now():%Y%m%d-%H%M%S}-batch-{uuid.uuid4().hex[:6]}"
    baseline_dir = output_dir / "collected" / baseline_id
    baseline_dir.mkdir(parents=True, exist_ok=False)

    artifacts: Dict[str, List[Dict[str, str]]] = {}
    measurement_files = {
        "cable_loss": ("cable_loss_results*.json", "cable_loss_model.json"),
        "driver_mapping": ("driver_power_mapping_*.json", "driver_power_mapping_model.json"),
        "amplifier_measurement": (
            "amplifier_measurement_*.json",
            "amplifier_measurement_model.json",
        ),
    }
    expected_result_types = {
        "cable_loss": {"cable_loss"},
        "driver_mapping": {"driver_mapping", "driver_power_mapping"},
        "amplifier_measurement": {"amplifier_measurement"},
    }
    integrity_issues: List[str] = []

    for kind, (result_pattern, model_name) in measurement_files.items():
        run_id = source_runs[kind]
        run_directory = results_dir / run_id
        result = _latest(
            path for path in run_directory.glob(result_pattern) if path.name != model_name
        )
        if result is None:
            artifacts[kind] = []
            integrity_issues.append(f"missing:{kind}")
        else:
            destination = baseline_dir / "results" / result.name
            artifacts[kind] = [_copy_artifact(result, destination, PROJECT_ROOT, baseline_dir)]
            try:
                content = json.loads(result.read_text(encoding="utf-8"))
                if not isinstance(content, dict):
                    integrity_issues.append(f"invalid_root:{kind}")
                elif content.get("run_id") != run_id:
                    integrity_issues.append(f"run_id_mismatch:{kind}")
                elif content.get("result_type") not in expected_result_types[kind]:
                    integrity_issues.append(f"result_type_mismatch:{kind}")
            except (OSError, json.JSONDecodeError):
                integrity_issues.append(f"invalid_json:{kind}")

        model = run_directory / model_name
        model_kind = f"{kind}_model"
        artifacts[model_kind] = (
            [_copy_artifact(model, baseline_dir / "results" / model_name, PROJECT_ROOT, baseline_dir)]
            if model.is_file()
            else []
        )

    # 以主功放运行作为批次的规范方案和现场资源快照，同时保留三类来源的快照。
    snapshot_names = ("test_plan_snapshot.json", "run_mapping_snapshot.json", "run_metadata.json")
    artifacts["snapshots"] = []
    for role, run_id in source_runs.items():
        run_directory = results_dir / run_id
        for filename in snapshot_names:
            source = run_directory / filename
            if not source.is_file():
                integrity_issues.append(f"missing:{role}/{filename}")
                continue
            destination = baseline_dir / "snapshots" / f"{role}_{filename}"
            artifacts["snapshots"].append(
                _copy_artifact(source, destination, PROJECT_ROOT, baseline_dir)
            )

    artifacts["reports"] = []
    if report_dir is not None:
        report_dir = Path(report_dir)
        if not report_dir.is_dir():
            raise FileNotFoundError(f"报告目录不存在: {report_dir}")
        for pattern in REPORT_PATTERNS:
            for report in sorted(report_dir.glob(pattern)):
                artifacts["reports"].append(
                    _copy_artifact(report, baseline_dir / "reports" / report.name, PROJECT_ROOT, baseline_dir)
                )
    else:
        integrity_issues.append("missing:reports")

    manifest: Dict[str, object] = {
        "schema_version": "1.1",
        "baseline_id": baseline_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "purpose": "阶段 0 生产基线测试批次",
        "source": {
            "results_directory": _relative_or_name(results_dir, PROJECT_ROOT),
            "batch_run_ids": source_runs,
            "report_directory": _relative_or_name(report_dir, PROJECT_ROOT) if report_dir else None,
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
            kind: "available" if artifacts.get(kind) else "not_found"
            for kind in measurement_files
        },
        "reuse": {
            "allowed": True,
            "description": "三类结果可来自不同 run_id；批次清单保留各自来源，不修改原始结果。",
        },
        "notes": [
            "本清单由 collect_baseline.py 批次模式自动生成。",
            "原始测量结果、快照和报告均从指定来源复制。",
            "批次是否满足现场复用条件仍需人工确认配置、接线、时间和清理状态。",
        ],
    }
    manifest_path = baseline_dir / "baseline_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return baseline_dir, manifest


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="自动整理阶段 0.1 基线样例")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--run-id", help="只收集指定运行目录中的结果")
    source.add_argument("--batch", action="store_true", help="收集允许跨 run_id 复用结果的测试批次")
    parser.add_argument("--cable-loss-run-id", help="批次模式的线损来源运行 ID")
    parser.add_argument("--driver-mapping-run-id", help="批次模式的驱动映射来源运行 ID")
    parser.add_argument("--amplifier-run-id", help="批次模式的主功放来源运行 ID")
    parser.add_argument("--report-dir", type=Path, help="批次模式的报告目录")
    parser.add_argument("--results-dir", type=Path, default=TEST_RESULTS_DIR)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "baseline")
    args = parser.parse_args(argv)
    try:
        if args.batch:
            required = (args.cable_loss_run_id, args.driver_mapping_run_id, args.amplifier_run_id)
            if not all(required) or args.report_dir is None:
                parser.error("--batch 必须同时提供三个来源 run_id 和 --report-dir")
            baseline_dir, manifest = collect_batch_baseline(
                results_dir=args.results_dir,
                output_dir=args.output_dir,
                cable_loss_run_id=args.cable_loss_run_id,
                driver_mapping_run_id=args.driver_mapping_run_id,
                amplifier_run_id=args.amplifier_run_id,
                report_dir=args.report_dir,
            )
        else:
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
