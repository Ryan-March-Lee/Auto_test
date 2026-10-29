"""项目测试入口：按改动面选择范围，并先跑编译检查与依赖核对。

本入口取代原先的 ``run_tests.ps1`` / ``run_tests.bat``。分层、门禁顺序和并行排除清单与
脚本一致，但规则写在 Python 里：可以被 ``tests/test_test_runner_contract.py`` 断言，
不依赖 PowerShell，也不必先手动 activate conda 环境。

```powershell
# 全量离线测试（默认）
& $env:AUTO_TEST_PYTHON tools/run_tests.py

# 改动只涉及表现层：表现层测试 + 全部门禁
& $env:AUTO_TEST_PYTHON tools/run_tests.py --layer ui

# 表现层 + 真实后端接线
& $env:AUTO_TEST_PYTHON tools/run_tests.py --layer integration

# 全量并行（需要 requirements-test.txt 的 pytest-xdist）
& $env:AUTO_TEST_PYTHON tools/run_tests.py --parallel

# 真实设备冒烟（另需 HARDWARE_SMOKE_ENABLED=1，见 hardware/smoke_gate.py）
& $env:AUTO_TEST_PYTHON tools/run_tests.py --layer hardware `
    --config hardware/smoke_config.local.json --confirm-hardware-smoke
```

步骤按顺序执行，第一个非零退出码即终止本次验证。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    # 直接以 ``python tools/run_tests.py`` 运行时，仓库根尚未在 sys.path 上。
    sys.path.insert(0, str(PROJECT_ROOT))

from infrastructure.runtime.runtime_contract import (  # noqa: E402  (需先补齐 sys.path)
    configured_python,
    python_version_supported,
    runtime_id,
)

EXIT_ENVIRONMENT_ERROR = 2
REEXEC_MARKER = "AUTO_TEST_TEST_RUNNER_REEXEC"
# 每个改动面都必须先通过的仓库门禁。
GATE_MODULES = (
    "tests.test_architecture_dependencies",
    "tests.test_presentation_boundaries",
    "tests.test_public_api_inventory",
    "tests.test_exception_taxonomy",
    "tests.test_static_quality_gate",
    "tests.test_runtime_dependency_contract",
)
# Unit / Simulation 不发现门禁模块，因此测试前单独执行一次。
STATIC_PRE_CHECK_MODULES = (
    "tests.test_static_quality_gate",
    "tests.test_exception_taxonomy",
)
LAYERS = ("offline", "unit", "simulation", "ui", "integration", "hardware")
# pytest 会收集没有 __init__.py 的目录，因此硬件测试必须显式排除。
PARALLEL_EXCLUSIONS = (
    "tests/hardware",
    "tests/integration/test_gui_two_phase_flow.py",
    "tests/test_static_quality_gate.py",
)
# 并行完成后串行补跑：Qt 可能终止 worker 进程，静态扫描是全局基线检查。
PARALLEL_SERIAL_MODULES = (
    "tests.integration.test_gui_two_phase_flow",
    "tests.test_static_quality_gate",
    "tests.test_exception_taxonomy",
)
MAX_PARALLEL_WORKERS = 4
HARDWARE_DIRECTORY = "tests/hardware"


@dataclass(frozen=True)
class Step:
    """一个用项目解释器运行的子步骤。"""

    label: str
    arguments: tuple[str, ...]


def unit_arguments(*modules: str) -> tuple[str, ...]:
    """构造一次显式的 unittest 调用。"""
    return ("-m", "unittest", *modules, "-v")


def discover_arguments(directory: str, top_level: bool = False) -> tuple[str, ...]:
    """构造一次 unittest 发现。

    ``top_level`` 为真时把仓库根设为顶层目录，使模块名保持 ``tests.ui.test_*``；
    没有 ``__init__.py`` 的目录（``tests/unit``、``tests/simulation``、``tests/hardware``）
    只能是发现起点，指定顶层目录会让 unittest 拒绝导入。
    """
    arguments = ["-m", "unittest", "discover", "-s", directory]
    if top_level:
        arguments += ["-t", "."]
    return (*arguments, "-p", "test_*.py", "-v")


def parallel_arguments(workers: int = 0) -> tuple[str, ...]:
    """构造 pytest-xdist 的全量并行参数。"""
    worker_count = workers if workers > 0 else min(MAX_PARALLEL_WORKERS, os.cpu_count() or 1)
    arguments = ["-m", "pytest", "-n", str(worker_count), "--dist", "loadfile", "tests"]
    for exclusion in PARALLEL_EXCLUSIONS:
        arguments += ["--ignore", exclusion]
    return tuple(arguments)


def build_plan(
    layer: str = "offline",
    unittest_arguments: Sequence[str] = (),
    parallel: bool = False,
    workers: int = 0,
) -> tuple[Step, ...]:
    """返回该改动面要顺序执行的步骤。"""
    steps = [
        Step("编译检查", ("tools/compile_check.py", ".")),
        Step("依赖核对", ("tools/export_dependency_lock.py", "--check")),
    ]
    if unittest_arguments:
        # 保留原脚本的透传形式：追加参数替代默认发现，仍先跑编译与依赖核对。
        return (*steps, Step("自定义测试参数", tuple(unittest_arguments)))
    if layer in ("unit", "simulation"):
        steps.append(Step("静态质量门禁", unit_arguments(*STATIC_PRE_CHECK_MODULES)))
    if layer in ("ui", "integration"):
        steps.append(Step("仓库门禁", unit_arguments(*GATE_MODULES)))
    if layer == "unit":
        steps.append(Step("单元测试", discover_arguments("tests/unit")))
    elif layer == "simulation":
        steps.append(Step("仿真测试", discover_arguments("tests/simulation")))
    elif layer == "ui":
        steps.append(Step("表现层测试", discover_arguments("tests/ui", top_level=True)))
    elif layer == "integration":
        steps.append(Step("表现层测试", discover_arguments("tests/ui", top_level=True)))
        steps.append(Step("接线测试", discover_arguments("tests/integration", top_level=True)))
    elif layer == "hardware":
        steps.append(Step("硬件冒烟", discover_arguments(HARDWARE_DIRECTORY)))
    elif parallel:
        steps.append(Step("并行离线测试", parallel_arguments(workers)))
        steps.append(Step("串行补跑", unit_arguments(*PARALLEL_SERIAL_MODULES)))
    else:
        steps.append(Step("全量离线测试", discover_arguments("tests")))
    return tuple(steps)


def conda_runtime_directories(prefix: Path) -> tuple[Path, ...]:
    """返回 conda 环境中存放原生依赖的目录。"""
    candidates = (
        prefix,
        prefix / "Library" / "mingw-w64" / "bin",
        prefix / "Library" / "usr" / "bin",
        prefix / "Library" / "bin",
        prefix / "Scripts",
        prefix / "bin",
    )
    return tuple(candidate for candidate in candidates if candidate.is_dir())


def prepend_runtime_path(environment: dict[str, str], prefix: Path) -> int:
    """把原生依赖目录前置到 ``PATH``，返回实际新增的目录数。

    conda 环境的 freetype、libpng、openssl 等 DLL 位于 ``<env>\\Library\\bin`` 等目录。
    只按绝对路径调用 python.exe 而不激活环境时这些目录不在 ``PATH`` 上：纯 Python 逻辑
    正常，但 matplotlib 渲染与 Qt 插件会在延迟加载阶段直接崩溃（退出码 0xC06D007F）。
    """
    existing = [entry for entry in (environment.get("PATH") or "").split(os.pathsep) if entry]
    known = {entry.rstrip("\\/").casefold() for entry in existing}
    added = [
        directory
        for directory in conda_runtime_directories(prefix)
        if str(directory).rstrip("\\/").casefold() not in known
    ]
    if added:
        environment["PATH"] = os.pathsep.join([str(directory) for directory in added] + existing)
    return len(added)


def reexec_with_configured_python(argv: Sequence[str]) -> int | None:
    """当前进程不是 ``.env`` 指定的解释器时，用它重新启动本入口。"""
    configured = configured_python()
    if configured is None or configured == Path(sys.executable).resolve():
        return None
    if not configured.is_file():
        print(f"AUTO_TEST_PYTHON 指向的解释器不存在: {configured}", file=sys.stderr)
        return EXIT_ENVIRONMENT_ERROR
    if os.environ.get(REEXEC_MARKER) == "1":
        print(
            f"解释器不一致: AUTO_TEST_PYTHON 为 {configured}，当前为 {sys.executable}",
            file=sys.stderr,
        )
        return EXIT_ENVIRONMENT_ERROR
    environment = dict(os.environ, **{REEXEC_MARKER: "1"})
    completed = subprocess.run(
        [str(configured), str(Path(__file__).resolve()), *argv],
        cwd=PROJECT_ROOT,
        env=environment,
    )
    return completed.returncode


def hardware_environment(
    config_path: Path | None,
    confirmed: bool,
    environment: Mapping[str, str],
) -> dict[str, str]:
    """把命令行开关翻译成测试自己会复核的环境变量，并提前跑一次门禁。"""
    from hardware.smoke_gate import HardwareSmokeGateError, load_gated_smoke_config

    if config_path is None:
        raise HardwareSmokeGateError(
            "硬件冒烟需要 --config 指定 hardware/ 下的本地配置。"
        )
    resolved = config_path if config_path.is_absolute() else PROJECT_ROOT / config_path
    prepared = dict(environment)
    prepared["HARDWARE_SMOKE_CONFIG"] = str(resolved.resolve())
    if confirmed:
        prepared["HARDWARE_SMOKE_CONFIRM"] = "1"
    # 失败立即停止，不进入测试发现；测试模块仍会各自复核。
    load_gated_smoke_config(prepared, PROJECT_ROOT)
    return prepared


def main(argv: Sequence[str] | None = None) -> int:
    """解析参数、准备运行时环境并顺序执行计划。"""
    raw = list(sys.argv[1:] if argv is None else argv)
    reexec_code = reexec_with_configured_python(raw)
    if reexec_code is not None:
        return reexec_code

    parser = argparse.ArgumentParser(
        description="按改动面运行测试（编译检查 + 依赖核对 + 分层测试）",
    )
    parser.add_argument("--layer", choices=LAYERS, default="offline", help="改动面（默认 offline）")
    parser.add_argument("--parallel", action="store_true", help="用 pytest-xdist 并行跑全量离线测试")
    parser.add_argument("--workers", type=int, default=0, help="并行 worker 数（默认按 CPU，上限 4）")
    parser.add_argument("--config", type=Path, default=None, help="硬件冒烟配置（hardware/ 下的本地文件）")
    parser.add_argument(
        "--confirm-hardware-smoke",
        action="store_true",
        help="确认设备空载且已获授权后才可传入",
    )
    parser.add_argument(
        "unittest_arguments",
        nargs=argparse.REMAINDER,
        help="直接传给 unittest 的参数，例如 discover -s tests -v",
    )
    arguments = parser.parse_args(raw)

    supported, detail = python_version_supported()
    if not supported:
        print(detail, file=sys.stderr)
        return EXIT_ENVIRONMENT_ERROR

    environment = dict(os.environ)
    if arguments.layer == "hardware" and not arguments.unittest_arguments:
        from hardware.smoke_gate import HardwareSmokeGateError

        try:
            environment = hardware_environment(
                arguments.config,
                arguments.confirm_hardware_smoke,
                environment,
            )
        except HardwareSmokeGateError as error:
            print(error, file=sys.stderr)
            return EXIT_ENVIRONMENT_ERROR

    environment.setdefault("AUTO_TEST_RUN_ID", runtime_id())
    added_directories = prepend_runtime_path(environment, Path(sys.executable).resolve().parent)
    print(f"Python: {Path(sys.executable).resolve()}")
    print(f"Run ID: {environment['AUTO_TEST_RUN_ID']}")
    if added_directories:
        print(f"Runtime PATH: 已前置 {added_directories} 个 conda 目录（无需预先 activate）")

    plan = build_plan(
        layer=arguments.layer,
        unittest_arguments=arguments.unittest_arguments,
        parallel=arguments.parallel,
        workers=arguments.workers,
    )
    for step in plan:
        command = " ".join(step.arguments)
        print(f"==> {step.label}: {command}", flush=True)
        completed = subprocess.run(
            [sys.executable, *step.arguments],
            cwd=PROJECT_ROOT,
            env=environment,
        )
        if completed.returncode != 0:
            return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
