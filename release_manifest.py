"""Generate a deterministic release and rollback manifest."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from project_paths import PROJECT_ROOT


RELEASE_FILES = (
    "launcher.py",
    "config_models.py",
    "measurement_services.py",
    "measurement_calculations.py",
    "measurement_lifecycle.py",
    "result_storage.py",
    "result_reading.py",
    "domain",
    "app",
    "instrument",
    "persistence",
    "analysis",
    "presentation",
)
ROLLBACK_FILES = (
    "enhanced_main_gui.py",
    "enhanced_workers.py",
    "cable_loss_measurement.py",
    "driver_power_mapping.py",
    "amplifier_measurement.py",
    "instrument_control.py",
)


def _git_commit(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def _existing(paths: Iterable[str], root: Path) -> list[str]:
    return [item for item in paths if (root / item).exists()]


def build_manifest(root: Path = PROJECT_ROOT) -> dict:
    root = Path(root)
    return {
        "manifest_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": _git_commit(root),
        "python": sys.version,
        "platform": platform.platform(),
        "release_files": _existing(RELEASE_FILES, root),
        "rollback_files": _existing(ROLLBACK_FILES, root),
        "verification": {
            "unit_tests": "python -m unittest discover -s tests -v",
            "compile": "python -m compileall -q .",
            "launcher_check": "python launcher.py --check",
            "config_validation": "python launcher.py --validate-config",
        },
        "real_device_acceptance": "pending",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 PA 自动测试发布/回滚清单")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "release_manifest.json")
    args = parser.parse_args(argv)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(build_manifest(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
