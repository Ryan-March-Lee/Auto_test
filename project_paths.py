"""已弃用兼容入口：请迁移到 ``infrastructure.filesystem.paths``。"""

from infrastructure.filesystem.paths import (
    ASSETS_DIR, CABLE_LOSS_FILE, CHAT_HISTORY_FILE, CHAT_SETTINGS_FILE,
    CONFIG_FILE, DEFAULT_PATHS, ICONS_DIR, IMAGES_DIR, LOGS_DIR, PathLike,
    PROJECT_ROOT, PathService,
    SEARCH_API_CONFIG_FILE, TEMP_DIR, TEST_RESULTS_DIR, ensure_directory,
    resolve_path,
)

__all__ = [
    "ASSETS_DIR", "CABLE_LOSS_FILE", "CHAT_HISTORY_FILE", "CHAT_SETTINGS_FILE",
    "CONFIG_FILE", "DEFAULT_PATHS", "ICONS_DIR", "IMAGES_DIR", "LOGS_DIR",
    "PathLike", "PathService",
    "PROJECT_ROOT", "SEARCH_API_CONFIG_FILE", "TEMP_DIR", "TEST_RESULTS_DIR",
    "ensure_directory", "resolve_path",
]
