"""把旧运行目录中的线损/驱动映射结果复制迁移到独立阶段目录。

扫描 ``test_results/runs/*/inputs/cable_loss.json`` 与
``test_results/runs/*/inputs/driver_mapping.json``，只把合法、已完成的正式结果复制到
``test_results/<阶段>/``，并在结果文档中补齐测量身份；旧运行目录保持原样，不移动、
不改写、不删除任何文件。重复执行是幂等的：同一份源文件始终指向同一个阶段结果文件，
已迁移的条目不会被重复写入，目标已存在且内容不同时记为冲突且不覆盖。

用法::

    python tools/migrate_legacy_results.py
    python tools/migrate_legacy_results.py --data-root D:/data
    python tools/migrate_legacy_results.py --runs-root D:/data/test_results/runs
    python tools/migrate_legacy_results.py --report migration.json
    python tools/migrate_legacy_results.py --no-report

默认把 JSON 迁移报告写到 ``<data-root>/test_results/migrations/`` 下，文件名带 UTC
时间戳；``--report`` 可指定位置，``--no-report`` 只打印摘要。存在失败或冲突时退出码为
1，需要人工核对；干净的迁移（含全部已迁移）返回 0。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    # 直接以 ``python tools/migrate_legacy_results.py`` 运行时，仓库根尚未在 sys.path 上。
    sys.path.insert(0, str(PROJECT_ROOT))

from infrastructure.filesystem.atomic_json import write_json_atomic  # noqa: E402
from infrastructure.filesystem.paths import DEFAULT_PATHS, PathService  # noqa: E402
from infrastructure.persistence.legacy_stage_result_migration import (  # noqa: E402
    LegacyStageResultMigration,
)
from infrastructure.persistence.run_repository import RunRepository  # noqa: E402
from infrastructure.persistence.stage_result_repository import (  # noqa: E402
    FileStageResultRepository,
)

REPORT_DIRNAME = "migrations"


def _default_report_path(paths: PathService) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return (
        paths.test_results_dir
        / REPORT_DIRNAME
        / f"legacy_stage_result_migration_{stamp}.json"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "把旧运行目录中的线损/驱动映射正式结果复制到独立阶段目录"
            "（只复制，不移动，不覆盖已有阶段结果）"
        )
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=None,
        help="数据根目录（包含 test_results/），默认使用项目根目录",
    )
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=None,
        help="运行根目录，默认 <data-root>/test_results/runs",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="JSON 迁移报告路径，默认写到 <data-root>/test_results/migrations/",
    )
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="只打印摘要，不写 JSON 迁移报告",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = (
        PathService(args.data_root) if args.data_root is not None else DEFAULT_PATHS
    )
    run_repository = (
        RunRepository(args.runs_root, paths=paths)
        if args.runs_root is not None
        else RunRepository(paths=paths)
    )
    migration = LegacyStageResultMigration(
        stage_results=FileStageResultRepository(paths=paths),
        run_repository=run_repository,
    )
    try:
        report = migration.run()
    except (OSError, ValueError) as error:
        print(f"旧结果迁移无法完成: {error}", file=sys.stderr)
        return 2

    for entry in report.entries:
        line = f"[{entry.outcome.value}] {entry.run_id} {entry.stage.value}: {entry.source_path}"
        if entry.detail:
            line += f"（{entry.detail}）"
        print(line)
    counts = report.counts()
    summary = "，".join(f"{name}: {count}" for name, count in counts.items() if count)
    print(f"迁移完成：{summary or '没有发现旧结果文件'}")
    print(f"运行根目录：{run_repository.root}")

    if not args.no_report:
        report_path = args.report or _default_report_path(paths)
        try:
            write_json_atomic(report_path, report.to_dict())
        except (OSError, ValueError) as error:
            print(f"无法写入迁移报告 {report_path}: {error}", file=sys.stderr)
            return 2
        print(f"迁移报告：{report_path}")

    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
