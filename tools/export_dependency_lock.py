"""按当前解释器导出或校验主系统依赖锁定清单。

技术债步骤 14 要求锁定清单可复现，且不得混入打包工具和 DPD 依赖。裸
``pip freeze`` 会把 ``pip`` / ``setuptools`` / ``wheel`` 写进清单，因此导出统一走本脚本：

```powershell
# 按已安装环境重写 requirements.lock.txt
& $env:AUTO_TEST_PYTHON tools/export_dependency_lock.py

# 校验锁文件与当前环境、requirements.txt 是否一致（CI 与发布前使用）
& $env:AUTO_TEST_PYTHON tools/export_dependency_lock.py --check
```

``--check`` 返回 ``0`` 表示一致，``1`` 表示存在漂移或清单格式错误。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    # 直接以 ``python tools/export_dependency_lock.py`` 运行时，仓库根尚未在 sys.path 上。
    sys.path.insert(0, str(PROJECT_ROOT))

from infrastructure.runtime.dependency_lock import (  # noqa: E402  (需先补齐 sys.path)
    PACKAGING_TOOLS,
    DependencyLockError,
    installed_packages,
    lock_drift,
    read_lock_packages,
)
from infrastructure.runtime.runtime_contract import (  # noqa: E402
    DEPENDENCY_DIRECT_FILE,
    DEPENDENCY_LOCK_FILE,
)

EXIT_OK = 0
EXIT_DRIFT = 1

LOCK_HEADER = """\
# PA 自动测试系统 - 主系统完整依赖锁定（Python 3.11 / Conda 环境 Auto_test）
#
# 本文件是新环境复现项目依赖的唯一依据，由已验证环境导出：
#     & $env:AUTO_TEST_PYTHON tools/export_dependency_lock.py
# 安装：
#     & $env:AUTO_TEST_PYTHON -m pip install -r requirements.lock.txt
#
# 约定：
# - 每个条目必须是 == 精确版本；传递依赖也固定在这里。
# - requirements.txt 中的直接依赖版本必须与本文件一致。
# - 打包工具（pip / setuptools / wheel）由安装环境自带，不属于应用依赖，不锁定。
# - DPD_auto_test 的依赖（requirements-dpd.txt）不得出现在本文件。
#
# 手工编辑无效：请改依赖后在 Auto_test 环境重新运行本脚本生成。
"""


def render_lock(installed=None) -> str:
    """把当前环境的已安装发行版渲染成锁定清单文本（含打包工具过滤与排序）。"""
    current = installed_packages() if installed is None else installed
    lines = [
        f"{package.name}=={package.version}"
        for key, package in current.items()
        if key not in PACKAGING_TOOLS and package.version
    ]
    lines.sort(key=str.lower)
    return LOCK_HEADER + "".join(f"{line}\n" for line in lines)


def direct_requirement_drift(lock_path: Path, direct_path: Path) -> list[str]:
    """检查 ``requirements.txt`` 的每个直接依赖是否与锁定清单完全一致。"""
    locked = read_lock_packages(lock_path)
    drift: list[str] = []
    for key, package in read_lock_packages(direct_path).items():
        locked_package = locked.get(key)
        if locked_package is None:
            drift.append(f"{package.name}: requirements.txt 声明但锁定清单缺少")
        elif locked_package.version != package.version:
            drift.append(
                f"{package.name}: requirements.txt 为 {package.version}，"
                f"锁定清单为 {locked_package.version}"
            )
    return drift


def check(root: Path = PROJECT_ROOT) -> tuple[list[str], list[str]]:
    """返回 ``(阻断项, 提示项)``：阻断项表示锁内依赖缺失或版本不符。"""
    lock_path = root / DEPENDENCY_LOCK_FILE
    direct_path = root / DEPENDENCY_DIRECT_FILE
    blocking = list(direct_requirement_drift(lock_path, direct_path))
    drift = lock_drift(read_lock_packages(lock_path))
    blocking.extend(drift.blocking_messages())
    return blocking, list(drift.warning_messages())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出或校验主系统依赖锁定清单")
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验锁文件与当前环境、requirements.txt 是否一致，不写文件",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=PROJECT_ROOT,
        help="项目根目录（默认：本脚本所在仓库）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=f"锁定清单输出路径（默认：<项目根>/{DEPENDENCY_LOCK_FILE}）",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    lock_path = root / DEPENDENCY_LOCK_FILE

    if args.check:
        try:
            blocking, warnings = check(root)
        except DependencyLockError as error:
            print(f"依赖锁定清单无效: {error}", file=sys.stderr)
            return EXIT_DRIFT
        for item in warnings:
            print(f"锁外多余包: {item}")
        if blocking:
            print(f"{DEPENDENCY_LOCK_FILE} 与当前环境不一致:", file=sys.stderr)
            for problem in blocking:
                print(f"    {problem}", file=sys.stderr)
            return EXIT_DRIFT
        print(f"{DEPENDENCY_LOCK_FILE} 与当前环境和 {DEPENDENCY_DIRECT_FILE} 一致")
        return EXIT_OK

    text = render_lock()
    target = args.output if args.output is not None else lock_path
    # 仓库统一使用 CRLF 且不带 BOM；显式换行避免不同平台生成不同字节。
    target.write_text(text, encoding="utf-8", newline="\r\n")
    print(target)
    # 写完立刻核对直接依赖：requirements.txt 与已安装环境不一致时不能悄悄通过。
    problems = direct_requirement_drift(target, root / DEPENDENCY_DIRECT_FILE)
    if problems:
        print(f"{DEPENDENCY_DIRECT_FILE} 与 {DEPENDENCY_LOCK_FILE} 不一致:", file=sys.stderr)
        for problem in problems:
            print(f"    {problem}", file=sys.stderr)
        return EXIT_DRIFT
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
