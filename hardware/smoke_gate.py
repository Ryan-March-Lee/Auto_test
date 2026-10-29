"""真实设备冒烟的安全门禁。

原先由根目录的 ``run_hardware_smoke.ps1`` 在启动测试之前执行这一组检查。脚本由固定命令
驱动，只有走那一个入口才有效；现在检查放在 Python 里，并由 ``tests/hardware`` 的每个用例
在打开任何 VISA 资源之前调用，因此无论从 unittest、pytest 还是 ``tools/run_tests.py``
进入，都绕不过去。

检查项（缺一不可）：

1. ``HARDWARE_SMOKE_CONFIG`` 指向 ``hardware/`` 下的本地配置，且文件名不是示例或模板；
2. 配置声明 ``environment`` 为 ``hardware_smoke``，``require_user_confirmation`` 与
   ``require_empty_setup`` 均为 ``true``，且不含 ``REPLACE_WITH_`` 占位符；
3. 环境变量 ``HARDWARE_SMOKE_ENABLED=1``，且当前进程不在 CI 中；
4. 操作者显式确认设备空载并已获授权（``HARDWARE_SMOKE_CONFIRM=1``，由
   ``tools/run_tests.py --confirm-hardware-smoke`` 设置）。

任一项不满足都抛 :class:`HardwareSmokeGateError`，测试在连接仪器之前终止。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Mapping


CONFIG_VARIABLE = "HARDWARE_SMOKE_CONFIG"
ENABLED_VARIABLE = "HARDWARE_SMOKE_ENABLED"
CONFIRM_VARIABLE = "HARDWARE_SMOKE_CONFIRM"
PLACEHOLDER_MARKER = "REPLACE_WITH_"
EXAMPLE_NAME_MARKERS = ("example", "sample", "template")
# CI 变量取这些值时视为“不在 CI 中”；其余非空值一律按 CI 处理。
FALSE_VALUES = frozenset({"0", "false", "no"})
ENTRY_POINT_HINT = (
    "真实设备冒烟只能用 tools/run_tests.py --layer hardware --config "
    "hardware/smoke_config.local.json --confirm-hardware-smoke 启动，"
    "并先设置 HARDWARE_SMOKE_ENABLED=1。"
)


class HardwareSmokeGateError(RuntimeError):
    """硬件冒烟的安全前提不满足；此时不得打开任何仪器资源。"""


def project_root() -> Path:
    """返回仓库根目录。"""
    return Path(__file__).resolve().parents[1]


def resolve_config_path(config_value: str | Path | None, root: Path | None = None) -> Path:
    """把配置值解析成 ``hardware/`` 下的真实文件路径。"""
    if not config_value:
        raise HardwareSmokeGateError(f"{CONFIG_VARIABLE} 未设置。{ENTRY_POINT_HINT}")
    base = (root or project_root()).resolve()
    candidate = Path(config_value)
    if not candidate.is_absolute():
        candidate = base / candidate
    resolved = candidate.resolve()
    if not resolved.is_file():
        raise HardwareSmokeGateError(f"硬件冒烟配置不存在: {config_value}")
    hardware_root = (base / "hardware").resolve()
    if hardware_root not in resolved.parents:
        raise HardwareSmokeGateError(f"硬件冒烟配置必须位于 hardware/ 下: {config_value}")
    lowered = resolved.name.lower()
    if any(marker in lowered for marker in EXAMPLE_NAME_MARKERS):
        raise HardwareSmokeGateError(f"示例或模板配置不能直接执行: {config_value}")
    return resolved


def _read_config(config_path: Path) -> tuple[dict, str]:
    try:
        text = config_path.read_text(encoding="utf-8-sig")
        config = json.loads(text)
    except (OSError, ValueError) as error:
        raise HardwareSmokeGateError(f"硬件冒烟配置无法读取或不是合法 JSON: {config_path}") from error
    if not isinstance(config, dict):
        raise HardwareSmokeGateError(f"硬件冒烟配置必须是 JSON 对象: {config_path}")
    return config, text


def _require_true(config: dict, key: str) -> None:
    if config.get(key) is not True:
        raise HardwareSmokeGateError(f"硬件冒烟配置必须把 {key} 设为 true。")


def load_gated_smoke_config(
    environ: Mapping[str, str] | None = None,
    root: Path | None = None,
) -> tuple[Path, dict]:
    """校验全部安全前提，返回 ``(配置路径, 配置内容)``。"""
    environment = os.environ if environ is None else environ
    config_path = resolve_config_path(environment.get(CONFIG_VARIABLE), root)
    config, text = _read_config(config_path)
    if config.get("environment") != "hardware_smoke":
        raise HardwareSmokeGateError("硬件冒烟配置必须把 environment 设为 hardware_smoke。")
    _require_true(config, "require_user_confirmation")
    _require_true(config, "require_empty_setup")
    if PLACEHOLDER_MARKER in text:
        raise HardwareSmokeGateError("硬件冒烟配置仍是占位符，未填写真实的设备地址。")
    if environment.get(ENABLED_VARIABLE) != "1":
        raise HardwareSmokeGateError(
            f"硬件冒烟未启用：请显式设置 {ENABLED_VARIABLE}=1。{ENTRY_POINT_HINT}"
        )
    ci_value = (environment.get("CI") or "").strip()
    if ci_value and ci_value.casefold() not in FALSE_VALUES:
        raise HardwareSmokeGateError("禁止在 CI 中执行硬件冒烟。")
    if environment.get(CONFIRM_VARIABLE) != "1":
        raise HardwareSmokeGateError(
            "缺少空载确认与授权：确认设备空载后加 --confirm-hardware-smoke 再运行。"
        )
    return config_path, config
