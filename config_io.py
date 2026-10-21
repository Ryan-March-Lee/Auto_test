"""已弃用兼容入口：请迁移到 ``infrastructure.config.json_io``。"""

from infrastructure.config.json_io import (
    CONFIG_FILE, PathLike, load_config_file, load_json_object, resolve_path,
)

__all__ = ["CONFIG_FILE", "PathLike", "load_config_file", "load_json_object", "resolve_path"]
