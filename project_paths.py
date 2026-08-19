"""项目文件路径集中定义。"""

from pathlib import Path
from typing import Optional, Union


PathLike = Union[str, Path]
PROJECT_ROOT = Path(__file__).resolve().parent

CONFIG_FILE = PROJECT_ROOT / "config.json"
CABLE_LOSS_FILE = PROJECT_ROOT / "cable_loss_results.json"
CHAT_HISTORY_FILE = PROJECT_ROOT / "chat_history.json"
CHAT_SETTINGS_FILE = PROJECT_ROOT / "chat_settings.json"
SEARCH_API_CONFIG_FILE = PROJECT_ROOT / "search_api_config.json"
ICONS_DIR = PROJECT_ROOT / "icons"
TEST_RESULTS_DIR = PROJECT_ROOT / "test_results"
TEMP_DIR = PROJECT_ROOT / "temp"


def resolve_path(path_value: Optional[PathLike], default_path: Path) -> Path:
    """省略路径时使用项目默认路径，显式相对路径保持旧语义。"""
    if path_value is None:
        return default_path
    path = Path(path_value).expanduser()
    return path if path.is_absolute() else path.resolve()


def ensure_directory(path: PathLike) -> Path:
    """确保目录存在并返回 Path 对象。"""
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory
