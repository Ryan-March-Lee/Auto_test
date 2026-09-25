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

from project_paths import CONFIG_FILE, PROJECT_ROOT, TEST_RESULTS_DIR


MEASUREMENT_PATTERNS = {
    "cable_loss": "cable_loss_results*.json",
    "driver_mapping": "driver_power_mapping_*.json",
    "amplifier_measurement": "amplifier_measurement_*.json",
}
REPORT_PATTERNS = ("*.html", "*.pdf", "*.csv")
PACKAGE_NAMES = ("PySide6", "matplotlib", "numpy", "pandas", "seaborn", "pyvisa", "markdown", "requests")


def _json_files(directory: Path, pattern: str) -> Iterable[Path]:
    """返回结果目录及其运行子目录中的 JSON 文件。"""
    yield from directory.glob(pattern)
    yield from directory.glob(f"*/{pattern}")


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
    """收集最新结果，返回新建的基线目录和清单。"""
    results_dir = Path(results_dir)
    output_dir = Path(output_dir)
    if not results_dir.exists():
        raise FileNotFoundError(f"结果目录不存在: {results_dir}")

    baseline_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    baseline_dir = output_dir / "collected" / baseline_id
    baseline_dir.mkdir(parents=True, exist_ok=False)

    artifacts: Dict[str, List[Dict[str, str]]] = {}
    selected: Dict[str, Optional[Path]] = {}
    for kind, pattern in MEASUREMENT_PATTERNS.items():
        candidate = Path(run_id) if run_id else None
        if candidate:
            selected[kind] = _latest((results_dir / candidate).glob(pattern))
        else:
            selected[kind] = _latest(_json_files(results_dir, pattern))
        source = selected[kind]
        if source:
            destination = baseline_dir / "results" / source.name
            artifacts[kind] = [_copy_artifact(source, destination, PROJECT_ROOT, baseline_dir)]
        else:
            artifacts[kind] = []

    # 快照通常位于各测量自己的运行目录中，收集最新的一组可用快照。
    for kind, filename in (
        ("test_plan_snapshot", "test_plan_snapshot.json"),
        ("run_mapping_snapshot", "run_mapping_snapshot.json"),
        ("run_metadata", "run_metadata.json"),
        ("conversion_review", "conversion_review.json"),
    ):
        source = _latest(results_dir.glob(f"*/{filename}"))
        if source:
            artifacts[kind] = [_copy_artifact(source, baseline_dir / "snapshots" / filename, PROJECT_ROOT, baseline_dir)]
        else:
            artifacts[kind] = []

    if CONFIG_FILE.exists():
        artifacts["config"] = [_copy_artifact(CONFIG_FILE, baseline_dir / "config.json", PROJECT_ROOT, baseline_dir)]
    else:
        artifacts["config"] = []

    reports = []
    for pattern in REPORT_PATTERNS:
        report = _latest(list(results_dir.glob(pattern)) + list(results_dir.glob(f"*/{pattern}")))
        if report:
            reports.append(_copy_artifact(report, baseline_dir / "reports" / report.name, PROJECT_ROOT, baseline_dir))
    artifacts["reports"] = reports

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
        "measurement_status": {
            kind: "available" if files else "not_found"
            for kind, files in artifacts.items()
            if kind in MEASUREMENT_PATTERNS
        },
        "notes": [
            "本清单由 collect_baseline.py 自动生成。",
            "not_found 表示该测量类型未在结果目录中找到，不代表测试失败。",
            "请在真实设备项目中另行确认接线和安全清理状态。",
        ],
    }
    manifest_path = baseline_dir / "baseline_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return baseline_dir, manifest


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="自动整理阶段 0.1 基线样例")
    parser.add_argument("--run-id", help="只收集指定运行目录中的结果")
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
