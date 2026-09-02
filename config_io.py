"""统一的配置 JSON 读取边界。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from project_paths import CONFIG_FILE, PathLike, resolve_path


def load_json_object(path: PathLike) -> Dict[str, Any]:
    """读取 JSON 文件并返回字典。

    统一处理：
    - Path 转换
    - UTF-8 编码
    - JSON 根节点必须是对象
    - 文件不存在、JSON 格式错误时返回一致异常

    Args:
        path: JSON 文件路径

    Returns:
        JSON 对象字典

    Raises:
        FileNotFoundError: 文件不存在
        json.JSONDecodeError: JSON 格式错误
        ValueError: JSON 根节点不是对象
    """
    file_path = Path(path)
    with file_path.open("r", encoding="utf-8") as json_file:
        data = json.load(json_file)
    if not isinstance(data, dict):
        raise ValueError(f"JSON 根节点必须是对象，实际类型: {type(data).__name__}")
    return data


def load_config_file(path: Optional[PathLike] = None) -> Dict[str, Any]:
    """读取配置文件，保持项目既有的默认路径和相对路径语义。"""
    return load_json_object(resolve_path(path, CONFIG_FILE))
