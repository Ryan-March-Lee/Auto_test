"""JSON 文件原子写入与读取的基础设施原语。

写入顺序为“写临时文件 -> fsync 临时文件 -> 原子替换”。在单进程内，
读取方只会看到替换前的旧文件或替换后的完整新文件，不会看到半截 JSON。

失败语义（调用方可以依赖）：

- 序列化、写入、fsync 或替换失败时，目标文件保持失败前的内容：既不会被截断，也不会
  被换成半份新文件；目标原本不存在时，失败后仍然不存在。
- **原始异常原样抛出**：清理临时文件失败只写日志，不会顶替真正的失败原因。
- 清理成功时不在目标目录留下临时文件；清理本身也失败时临时文件可能残留，日志会给出
  该临时文件的完整路径。

明确不提供的保证：

- 不 fsync 父目录，因此不承诺断电后的目录项持久性。
- 不提供并发互斥；同一路径的并发写由调用方（运行仓储）负责串行化。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping, Union

from infrastructure.filesystem.paths import ensure_directory


PathLike = Union[str, Path]

logger = logging.getLogger(__name__)


def _discard_temporary_file(path: Path) -> None:
    """尽力删除临时文件，失败只记录日志。

    该函数只能从失败的写入路径调用：调用方正在传播真正的写入/替换异常，清理异常一旦
    冒泡就会顶替原始原因，调用方因此看不到“为什么没写成功”。``unlink`` 的失败类别是
    清理属于失败路径的尽力动作，因此任何普通 ``Exception`` 都只记录而不再抛出；
    ``BaseException``（如用户中断）仍然按原语义传播。
    """
    try:
        path.unlink(missing_ok=True)
    except Exception as error:  # noqa: BLE001 - 清理失败不得覆盖原始写入异常
        logger.warning("无法清理临时文件 %s: %s", path, error)


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

    失败时目标文件保持失败前的内容，且原始异常（序列化、写入、fsync、替换或目录创建
    的异常）原样抛出；临时文件清理失败只记录日志，不改变异常。
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
            # 清理失败不得顶替原始异常：目标文件状态与失败原因都优先如实上报。
            _discard_temporary_file(temporary_path)
        raise
    return destination


def read_json(path: PathLike) -> Any:
    """读取 JSON 文件内容。"""
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


__all__ = ["PathLike", "read_json", "write_json_atomic"]
