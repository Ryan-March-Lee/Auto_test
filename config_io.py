"""兼容入口：配置 JSON 读写已迁移到 infrastructure.config。"""

from infrastructure.config.json_io import (
    CONFIG_FILE, PathLike, load_config_file, load_json_object, resolve_path,
)

__all__ = ["CONFIG_FILE", "PathLike", "load_config_file", "load_json_object", "resolve_path"]
