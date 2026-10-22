"""统一开发、测试和现场入口使用的运行时契约。"""

from __future__ import annotations

import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_RUN_ID_PREFIX = "pa-auto-test"


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
        "conda_environment": os.environ.get("CONDA_DEFAULT_ENV") or Path(sys.prefix).name,
        "run_id": runtime_id(),
    }
