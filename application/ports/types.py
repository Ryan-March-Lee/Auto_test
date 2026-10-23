"""应用端口共用的基础类型。

单独成模块，使“历史结果浏览”与“正式结果仓储/输入”端口可以共享路径类型，
而不需要互相导入彼此的端口模块。
"""

from __future__ import annotations

from pathlib import Path
from typing import Union


PathLike = Union[str, Path]


__all__ = ["PathLike"]
