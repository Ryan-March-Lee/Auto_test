"""统一开发、测试和现场入口使用的运行时契约。"""

from __future__ import annotations

import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_RUN_ID_PREFIX = "pa-auto-test"
# 技术债步骤 14：项目只支持 3.11 系列解释器，测试、编译、启动检查和发布清单
# 全部以本常量判定，不再各自维护过宽或过窄的版本条件。
REQUIRED_PYTHON_SERIES = "3.11"
# 主系统依赖的锁定清单；DPD 依赖保持在 requirements-dpd.txt，不合并进来。
DEPENDENCY_LOCK_FILE = "requirements.lock.txt"
DEPENDENCY_DIRECT_FILE = "requirements.txt"
DPD_REQUIREMENTS_FILE = "requirements-dpd.txt"


def software_version_tag() -> str:
    """返回写入运行快照的软件版本标识，避免硬编码解释器版本。"""
    return f"python-{platform.python_version()}"


def load_env_value(name: str, env_file: Path | None = None) -> str | None:
    """读取环境变量，未设置时回退到项目根目录 ``.env``。"""
    value = os.environ.get(name)
    if value:
        return value.strip().strip('"').strip("'")
    env_file = env_file or Path(__file__).resolve().parents[2] / ".env"
    if not env_file.is_file():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, candidate = stripped.split("=", 1)
        if key.strip() == name:
            return candidate.strip().strip('"').strip("'") or None
    return None


def configured_python() -> Path | None:
    value = load_env_value("AUTO_TEST_PYTHON")
    return Path(value).expanduser().resolve() if value else None


def validate_python_interpreter() -> tuple[bool, str]:
    """验证当前进程是否由 ``.env`` 指定的解释器运行。"""
    configured = configured_python()
    current = Path(sys.executable).resolve()
    if configured is None:
        return False, "AUTO_TEST_PYTHON 未配置，请在项目根目录 .env 中设置。"
    if not configured.is_file():
        return False, f"AUTO_TEST_PYTHON 指向的解释器不存在: {configured}"
    if configured != current:
        return False, f"解释器不一致: 配置为 {configured}，当前为 {current}"
    return True, str(current)


def python_version_requirement() -> str:
    """返回对用户和发布清单展示的 Python 版本要求。"""
    return f"{REQUIRED_PYTHON_SERIES}.x"


def python_version_supported(version_info: Any = None) -> tuple[bool, str]:
    """判定解释器是否落在项目支持的 3.11 系列内。

    返回 ``(是否支持, 说明)``；说明在失败时可直接用于启动器输出。
    """
    current = sys.version_info if version_info is None else version_info
    detail = f"{current.major}.{current.minor}.{current.micro}"
    series = f"{current.major}.{current.minor}"
    if series != REQUIRED_PYTHON_SERIES:
        return False, f"需要 Python {python_version_requirement()}，当前为 {detail}"
    return True, detail


def interpreter_path() -> Path:
    """返回项目解释器：``.env`` 配置值优先，回退到当前解释器。"""
    configured = configured_python()
    if configured is not None:
        return configured
    return Path(sys.executable).resolve()


def interpreter_command(*arguments: str) -> str:
    """构造单条项目命令，首段永远是显式解释器路径而不是裸 ``python``。"""
    executable = str(interpreter_path())
    if " " in executable:
        executable = f'"{executable}"'
    return " ".join((executable, *arguments))


def verification_commands() -> dict[str, str]:
    """测试、编译、启动检查和配置校验的官方命令集合。

    启动器帮助、README、发布清单和 CI 都引用这一份结果，避免各自维护
    裸 ``python`` 命令而产生解释器漂移。
    """
    return {
        "unit_tests": interpreter_command("-m", "unittest", "discover", "-s", "tests", "-v"),
        "compile": interpreter_command("tools/compile_check.py", "."),
        "dependency_lock": interpreter_command("tools/export_dependency_lock.py", "--check"),
        "launcher_check": interpreter_command("launcher.py", "--check"),
        "config_validation": interpreter_command("launcher.py", "--validate-config"),
        "release_manifest": interpreter_command("release_manifest.py"),
    }


def runtime_id() -> str:
    """返回可关联配置、结果和现场报告的运行标识。"""
    explicit = load_env_value("AUTO_TEST_RUN_ID")
    if explicit:
        return explicit
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{DEFAULT_RUN_ID_PREFIX}-{timestamp}"


def current_runtime() -> dict[str, Any]:
    configured = configured_python()
    valid, detail = validate_python_interpreter()
    return {
        "python_executable": str(Path(sys.executable).resolve()),
        "configured_python": str(configured) if configured else None,
        "interpreter_matches_config": valid,
        "interpreter_check": detail,
        "python_version": platform.python_version(),
        "python_requirement": python_version_requirement(),
        "conda_environment": os.environ.get("CONDA_DEFAULT_ENV") or Path(sys.prefix).name,
        "run_id": runtime_id(),
    }
