"""配置快照名称和目录命名规则（DTO 层）。

这里是用户输入到文件系统名称之间的唯一转换边界。允许中文、空格和常见标点，
拒绝路径片段、控制字符、Windows 保留设备名以及会改变目录边界的字符。

放在 ``application.dto`` 是刻意的：配置页与协调器需要“提交前校验名称”，但它们
不得依赖 ``application.configuration_*`` 的配置业务实现；命名规则是不可变的用户
输入契约，归 DTO 层，仓储与界面共用同一份校验。
"""

from __future__ import annotations

import re
from datetime import datetime


_INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
MAX_CONFIGURATION_NAME_LENGTH = 80


def validate_configuration_name(value: str) -> str:
    """校验并返回可用于快照身份的用户名称。"""

    if not isinstance(value, str):
        raise ValueError("配置名称必须是文本")
    name = value.strip()
    if not name:
        raise ValueError("配置名称不能为空")
    if len(name) > MAX_CONFIGURATION_NAME_LENGTH:
        raise ValueError(
            f"配置名称不能超过 {MAX_CONFIGURATION_NAME_LENGTH} 个字符"
        )
    if name in {".", ".."} or _INVALID_NAME.search(name):
        raise ValueError("配置名称包含非法字符或路径片段")
    if name.endswith((".", " ")):
        raise ValueError("配置名称不能以句点或空格结尾")
    if name.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        raise ValueError("配置名称不能使用 Windows 保留设备名")
    return name


def configuration_folder_name(name: str, *, now: datetime | None = None) -> str:
    """生成 ``名称_YYYYMMDD_HHMM`` 目录名。

    时间使用本地时间，便于用户在文件管理器中按现场操作时间查找；精确时间仍由
    快照元数据保存，执行审计继续使用运行层的 UTC 时间。
    """

    safe_name = validate_configuration_name(name)
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M")
    return f"{safe_name}_{stamp}"


__all__ = [
    "MAX_CONFIGURATION_NAME_LENGTH",
    "configuration_folder_name",
    "validate_configuration_name",
]
