"""JSON 文件原子写入与读取的基础设施原语。

写入顺序为“写临时文件 -> fsync 临时文件 -> 原子替换”。在单进程内，
读取方只会看到替换前的旧文件或替换后的完整新文件，不会看到半截 JSON。

明确不提供的保证：

- 不 fsync 父目录，因此不承诺断电后的目录项持久性。
- 不提供并发互斥；同一路径的并发写由调用方（运行仓储）负责串行化。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping, Union

from infrastructure.filesystem.paths import ensure_directory


PathLike = Union[str, Path]


def write_json_atomic(
    path: PathLike,
    payload: Mapping[str, Any],
    *,
    indent: int = 4,
    ensure_ascii: bool = False,
    encoder: type[json.JSONEncoder] = json.JSONEncoder,
) -> Path:
    """以临时文件加原子替换的方式写入 JSON 对象。

    目标已存在时会被原子覆盖；并发写同一路径的结果取决于替换顺序，调用方
    需要自行保证同一文件不被并发更新。
    """
    if not isinstance(payload, Mapping):
        raise ValueError("JSON 根节点必须是映射")
    destination = Path(path)
    ensure_directory(destination.parent)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(destination.parent),
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(payload, temporary_file, indent=indent, ensure_ascii=ensure_ascii, cls=encoder)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, destination)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    return destination


def read_json(path: PathLike) -> Any:
    """读取 JSON 文件内容。"""
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


__all__ = ["PathLike", "read_json", "write_json_atomic"]
