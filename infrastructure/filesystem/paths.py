"""项目文件系统路径服务及兼容路径导出。"""

from pathlib import Path
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Union
from uuid import uuid4


PathLike = Union[str, Path]


@dataclass(frozen=True)
class PathService:
    """从一个数据根目录派生应用所需的全部文件系统路径。

    服务不创建目录，也不读取环境变量，因此可以安全地在测试中注入临时根目录。
    """

    root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root).expanduser().resolve())

    @property
    def project_root(self) -> Path:
        return self.root

    @property
    def test_results_dir(self) -> Path:
        return self.root / "test_results"

    @property
    def config_file(self) -> Path:
        return self.root / "config.json"

    @property
    def instruments_file(self) -> Path:
        """设备表（仪器注册表）文件。"""
        return self.root / "instruments.json"

    @property
    def cable_loss_file(self) -> Path:
        return self.test_results_dir / "cable_loss_results.json"

    @property
    def assets_dir(self) -> Path:
        return self.root / "assets"

    @property
    def icons_dir(self) -> Path:
        return self.assets_dir / "icons"

    @property
    def images_dir(self) -> Path:
        return self.assets_dir / "images"

    @property
    def temp_dir(self) -> Path:
        return self.root / "temp"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"


DEFAULT_PATHS = PathService(Path(__file__).resolve().parents[2])

# 历史模块导出的常量保留为默认服务的只读快照，新的代码应注入 PathService。
PROJECT_ROOT = DEFAULT_PATHS.project_root
TEST_RESULTS_DIR = DEFAULT_PATHS.test_results_dir
CONFIG_FILE = DEFAULT_PATHS.config_file
CABLE_LOSS_FILE = DEFAULT_PATHS.cable_loss_file
ASSETS_DIR = DEFAULT_PATHS.assets_dir
ICONS_DIR = DEFAULT_PATHS.icons_dir
IMAGES_DIR = DEFAULT_PATHS.images_dir
TEMP_DIR = DEFAULT_PATHS.temp_dir
LOGS_DIR = DEFAULT_PATHS.logs_dir


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


def unique_directory_name(prefix: str) -> str:
    """生成带时间戳与随机后缀的目录名，避免同一秒内的输出互相覆盖。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{stamp}_{uuid4().hex[:8]}"


__all__ = [
    "ASSETS_DIR", "CABLE_LOSS_FILE",
    "CONFIG_FILE", "ICONS_DIR", "IMAGES_DIR", "LOGS_DIR", "PathLike",
    "DEFAULT_PATHS", "PathService", "PROJECT_ROOT",
    "TEMP_DIR", "TEST_RESULTS_DIR",
    "ensure_directory", "resolve_path", "unique_directory_name",
]
