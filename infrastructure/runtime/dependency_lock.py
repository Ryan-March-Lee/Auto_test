"""依赖锁定清单的严格解析与运行环境核对。

技术债步骤 14 把 ``requirements.lock.txt`` 定为主系统依赖版本的唯一来源，
启动器、导出工具和门禁都通过本模块读写同一份定义，避免各自实现一套宽松解析：

- :func:`read_lock_packages`：只接受 ``包名==版本``；缺失文件、未固定版本、
  无法解析的行和重复条目都抛出 :class:`DependencyLockError`，不静默跳过。
- :func:`installed_packages`：按当前解释器读取已安装发行版（含传递依赖）。
- :func:`lock_drift`：以锁文件为准核对整份环境，区分缺失、版本不符和锁外多余包。

锁外多出来的包（包括 ``pip`` / ``setuptools`` / ``wheel``）只是提示，不视为
环境漂移；锁文件声明过的包缺失或版本不符才是阻断项。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

# 打包工具由安装环境自带，不属于应用依赖，不进入锁文件，也不参与锁外多余包提示。
PACKAGING_TOOLS = frozenset({"pip", "setuptools", "wheel"})

# PEP 508 名称与 ``==`` 精确版本的子集；范围、通配符和直接 URL 都不属于锁定清单。
_PIN_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"\s*==\s*"
    r"(?P<version>[A-Za-z0-9][A-Za-z0-9._+!-]*)$"
)


class DependencyLockError(ValueError):
    """锁定清单缺失、格式错误或重复声明。"""


@dataclass(frozen=True)
class LockedPackage:
    """锁文件中的一条 ``包名==版本`` 记录，保留原始写法用于输出。"""

    name: str
    version: str


@dataclass(frozen=True)
class InstalledPackage:
    """当前解释器中的一个已安装发行版。"""

    name: str
    version: str


@dataclass(frozen=True)
class LockDrift:
    """锁文件与已安装环境的差异。

    ``missing`` / ``mismatched`` 是阻断项：锁文件要求的依赖没有按版本安装。
    ``unexpected`` 只是提示：环境里多装了锁文件没有声明的包。
    """

    missing: tuple[str, ...] = ()
    mismatched: tuple[str, ...] = ()
    unexpected: tuple[str, ...] = ()

    @property
    def blocking(self) -> bool:
        """是否存在必须修复的漂移。"""
        return bool(self.missing or self.mismatched)

    def blocking_messages(self) -> list[str]:
        """返回可直接打印的阻断项说明。"""
        messages = [f"{name}: 锁文件要求但未安装" for name in self.missing]
        messages.extend(self.mismatched)
        return messages

    def warning_messages(self) -> list[str]:
        """返回可直接打印的提示项说明。"""
        return [f"{item}（锁文件未声明）" for item in self.unexpected]


def normalize_distribution_name(name: str) -> str:
    """PEP 503 归一化：比较锁文件和已安装发行版时忽略大小写与分隔符。"""
    return name.strip().lower().replace("-", "_").replace(".", "_")


def read_lock_packages(lock_file: Path) -> dict[str, LockedPackage]:
    """严格解析锁定清单，返回 ``归一化名称 -> LockedPackage``。

    任何非注释行都必须是精确版本；文件缺失、空文件、重复声明和无法解析的行
    都抛出 :class:`DependencyLockError`，绝不返回部分结果。
    """
    path = Path(lock_file)
    if not path.is_file():
        raise DependencyLockError(f"缺少依赖锁定清单: {path}")
    packages: dict[str, LockedPackage] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _PIN_PATTERN.match(line)
        if match is None:
            raise DependencyLockError(
                f"{path.name} 第 {number} 行不是精确版本（必须写成 包名==版本）: {line}"
            )
        package = LockedPackage(match.group("name"), match.group("version"))
        key = normalize_distribution_name(package.name)
        if key in packages:
            raise DependencyLockError(
                f"{path.name} 第 {number} 行重复声明 {package.name}，"
                f"已由 {packages[key].name}=={packages[key].version} 锁定"
            )
        packages[key] = package
    if not packages:
        raise DependencyLockError(f"{path.name} 没有任何依赖条目")
    return packages


def installed_packages() -> dict[str, InstalledPackage]:
    """返回当前解释器已安装的发行版，``归一化名称 -> InstalledPackage``。

    同名发行版在多个 ``sys.path`` 条目中出现时保留先出现的那个（导入顺序优先）。
    """
    installed: dict[str, InstalledPackage] = {}
    for distribution in metadata.distributions():
        name = distribution.metadata.get("Name")
        if not name:
            continue
        key = normalize_distribution_name(name)
        if key in installed:
            continue
        installed[key] = InstalledPackage(name, distribution.version or "")
    return installed


def lock_drift(
    locked: dict[str, LockedPackage],
    installed: dict[str, InstalledPackage] | None = None,
) -> LockDrift:
    """以锁文件为准核对整份已安装环境。"""
    current = installed_packages() if installed is None else installed
    missing: list[str] = []
    mismatched: list[str] = []
    for key, package in locked.items():
        actual = current.get(key)
        if actual is None:
            missing.append(package.name)
            continue
        if actual.version != package.version:
            mismatched.append(
                f"{package.name}: 锁定 {package.version}，已安装 {actual.version or '未知版本'}"
            )
    unexpected = [
        f"{package.name} {package.version}".strip()
        for key, package in sorted(current.items(), key=lambda item: item[1].name.lower())
        if key not in locked and key not in PACKAGING_TOOLS
    ]
    return LockDrift(tuple(missing), tuple(mismatched), tuple(unexpected))
