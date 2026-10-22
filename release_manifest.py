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

from infrastructure.filesystem.paths import DEFAULT_PATHS
from infrastructure.runtime import current_runtime, runtime_id


RELEASE_FILES = (
    "launcher.py",
    "domain/measurement",
    "domain/configuration",
    "infrastructure/config",
    "infrastructure/persistence",
    "domain",
    "app",
    "application",
    "instrument",
    "persistence",
    "analysis",
    "presentation",
)
ROLLBACK_FILES = (
    "enhanced_main_gui.py",
    "cable_loss_measurement.py",
    "driver_power_mapping.py",
    "amplifier_measurement.py",
)
COMPATIBILITY_FILES = (
    "instrument_control.py",
    "measurement_services.py",
    "measurement_lifecycle.py",
    "data_visualization.py",
    "connection_diagrams.py",
    "config_models.py",
    "measurement_calculations.py",
    "result_storage.py",
    "result_reading.py",
    "legacy_config_conversion.py",
    "app_logging.py",
    "project_paths.py",
    "config_io.py",
    "config_validation.py",
    "llm.py",
    "web_search_tools.py",
)
REAL_DEVICE_ACCEPTANCE_STATES = frozenset({"pending", "passed"})


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


def build_manifest(
    root: Path | None = None,
    *,
    real_device_acceptance: str = "pending",
) -> dict:
    root = Path(root) if root is not None else DEFAULT_PATHS.project_root
    if real_device_acceptance not in REAL_DEVICE_ACCEPTANCE_STATES:
        raise ValueError(
            "real_device_acceptance 必须是 pending 或 passed"
        )
    return {
        "manifest_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": _git_commit(root),
        "python": sys.version,
        "platform": platform.platform(),
        "runtime": current_runtime(),
        "run_id": runtime_id(),
        "release_files": _existing(RELEASE_FILES, root),
        "compatibility_files": _existing(COMPATIBILITY_FILES, root),
        "rollback_files": _existing(ROLLBACK_FILES, root),
        "verification": {
            "unit_tests": "python -m unittest discover -s tests -v",
            "compile": "python -m compileall -q .",
            "launcher_check": "python launcher.py --check",
            "config_validation": "python launcher.py --validate-config",
        },
        "real_device_acceptance": real_device_acceptance,
        "evidence": {
            "required": ["config_snapshot", "result_snapshot", "cleanup_record", "现场报告"],
            "sensitive_data_policy": "现场证据仅保存于受控归档，不提交版本库",
            "rollback_decision": "pending",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 PA 自动测试发布/回滚清单")
    parser.add_argument("--output", type=Path, default=DEFAULT_PATHS.project_root / "release_manifest.json")
    parser.add_argument(
        "--real-device-acceptance",
        choices=sorted(REAL_DEVICE_ACCEPTANCE_STATES),
        default="pending",
        help="真实设备验收状态；只有现场证据完整后才能使用 passed",
    )
    args = parser.parse_args(argv)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            build_manifest(real_device_acceptance=args.real_device_acceptance),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
