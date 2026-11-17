"""项目文件系统路径服务及兼容路径导出。"""

from pathlib import Path
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Optional, Union
from uuid import uuid4

from application.errors import ConfigurationError


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
    def configuration_snapshots_dir(self) -> Path:
        """用户可复用配置快照的独立目录。"""
        return self.test_results_dir / "configurations"

    @property
    def cable_loss_results_dir(self) -> Path:
        """线损正式结果的阶段目录。"""
        return self.test_results_dir / "cable_loss"

    @property
    def driver_mapping_results_dir(self) -> Path:
        """驱动映射正式结果的阶段目录。"""
        return self.test_results_dir / "driver_mapping"

    @property
    def amplifier_results_dir(self) -> Path:
        """主功放正式结果的阶段目录。"""
        return self.test_results_dir / "amplifier"

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


MOCK_DATA_DIRNAME = "mock_data"
MOCK_CONFIG_FILENAME = "config.mock.json"
MOCK_SCENARIO_FILENAME = "scenario.json"


class MockDataPathError(ConfigurationError):
    """Mock 数据路径契约失败：未知阶段、非法文件名或文件缺失。

    归入配置类异常（同时保留 :class:`ValueError` 基类），使步骤 8 的回放 worker
    与 :class:`application.dto.mock_scenario.MockScenarioError` 使用同一错误类别。
    """


class MockStage(str, Enum):
    """``mock_data`` 下拥有独立场景目录的测量阶段。

    ``value`` 就是场景目录名；与正式测量类型的对应关系记录在
    :data:`MOCK_STAGE_INDEX`。例如 ``driver_mapping`` 目录对应正式类型
    ``driver_power_mapping``，两者不能混用。
    """

    CABLE_LOSS = "cable_loss"
    DRIVER_MAPPING = "driver_mapping"
    AMPLIFIER = "amplifier"


@dataclass(frozen=True)
class MockStageEntry:
    """Mock 阶段索引中的一条只读事实。

    ``calibration_inputs`` 是启动该阶段前必须已存在的 Mock 阶段结果，按依赖顺序
    排列；线损没有校准输入，驱动映射依赖线损，主功放依赖线损和驱动映射。传入的
    任何序列都在构造时规范化为不可变 ``tuple``，索引建立后不能再被调用方改写。
    """

    stage: MockStage
    measurement_type: str
    calibration_inputs: tuple[MockStage, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.stage, MockStage):
            raise MockDataPathError(
                f"Mock 阶段必须是 MockStage，收到 {type(self.stage).__name__}"
            )
        if not isinstance(self.measurement_type, str):
            raise MockDataPathError("Mock 阶段的测量类型必须是字符串")
        if not self.measurement_type.strip():
            raise MockDataPathError("Mock 阶段的测量类型不能为空")
        if self.measurement_type != self.measurement_type.strip():
            raise MockDataPathError("Mock 阶段的测量类型不能包含首尾空白")
        inputs = self.calibration_inputs
        if isinstance(inputs, (str, bytes)):
            raise MockDataPathError(
                "Mock 校准输入阶段不能是字符串，必须是 MockStage 序列"
            )
        try:
            normalized_inputs = tuple(inputs)
        except TypeError as error:
            raise MockDataPathError(
                "Mock 校准输入阶段必须是 MockStage 序列，"
                f"收到 {type(inputs).__name__}"
            ) from error
        seen: set[MockStage] = set()
        for dependency in normalized_inputs:
            if not isinstance(dependency, MockStage):
                raise MockDataPathError(
                    "Mock 校准输入阶段必须是 MockStage，"
                    f"收到 {type(dependency).__name__}"
                )
            if dependency is self.stage:
                raise MockDataPathError(
                    f"Mock 阶段 {self.stage.value} 不能依赖自身的校准结果"
                )
            if dependency in seen:
                raise MockDataPathError(
                    f"Mock 阶段 {self.stage.value} 的校准输入阶段重复: "
                    f"{dependency.value}"
                )
            seen.add(dependency)
        object.__setattr__(self, "calibration_inputs", normalized_inputs)


MOCK_STAGE_INDEX: Mapping[MockStage, MockStageEntry] = MappingProxyType(
    {
        MockStage.CABLE_LOSS: MockStageEntry(
            stage=MockStage.CABLE_LOSS,
            measurement_type="cable_loss",
        ),
        MockStage.DRIVER_MAPPING: MockStageEntry(
            stage=MockStage.DRIVER_MAPPING,
            measurement_type="driver_power_mapping",
            calibration_inputs=(MockStage.CABLE_LOSS,),
        ),
        MockStage.AMPLIFIER: MockStageEntry(
            stage=MockStage.AMPLIFIER,
            measurement_type="amplifier",
            calibration_inputs=(MockStage.CABLE_LOSS, MockStage.DRIVER_MAPPING),
        ),
    }
)


def _available_mock_stages() -> str:
    return "、".join(stage.value for stage in MockStage)


def coerce_mock_stage(value: Union[MockStage, str]) -> MockStage:
    """把阶段目录名或枚举值统一为 :class:`MockStage`，未知值给出可读错误。"""
    if isinstance(value, MockStage):
        return value
    if not isinstance(value, str):
        raise MockDataPathError(
            "Mock 阶段必须是字符串或 MockStage，"
            f"收到 {type(value).__name__}；可用阶段: {_available_mock_stages()}"
        )
    normalized = value.strip()
    if not normalized:
        raise MockDataPathError(
            f"Mock 阶段不能为空；可用阶段: {_available_mock_stages()}"
        )
    try:
        return MockStage(normalized)
    except ValueError as error:
        raise MockDataPathError(
            f"未知的 Mock 阶段: {value!r}；可用阶段: {_available_mock_stages()}"
        ) from error


def mock_stage_entry(stage: Union[MockStage, str]) -> MockStageEntry:
    """返回阶段索引条目；未知阶段抛 :class:`MockDataPathError`。"""
    return MOCK_STAGE_INDEX[coerce_mock_stage(stage)]


def mock_measurement_type_for_stage(stage: Union[MockStage, str]) -> str:
    """返回 Mock 阶段对应的正式测量类型。

    返回值与 :class:`application.dto.measurement_results.MeasurementType` 的取值
    一一对应。路径模块只保存字符串，以免基础设施反向依赖应用层；转换成正式枚举
    由步骤 2 的场景契约负责，取值漂移由跨层一致性用例发现。
    """
    return mock_stage_entry(stage).measurement_type


def mock_stage_for_measurement_type(measurement_type: str) -> MockStage:
    """按正式测量类型查找 Mock 阶段，未知类型抛 :class:`MockDataPathError`。"""
    if not isinstance(measurement_type, str):
        raise MockDataPathError(
            "测量类型必须是字符串，"
            f"收到 {type(measurement_type).__name__}"
        )
    normalized = measurement_type.strip()
    for stage, entry in MOCK_STAGE_INDEX.items():
        if entry.measurement_type == normalized:
            return stage
    available = "、".join(
        entry.measurement_type for entry in MOCK_STAGE_INDEX.values()
    )
    raise MockDataPathError(
        f"未知的 Mock 测量类型: {measurement_type!r}；可用类型: {available}"
    )


# Windows 保留设备名：即使带扩展名也会被系统解析成设备，不能作为 Mock 结果文件名。
_RESERVED_WINDOWS_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)


def _require_plain_file_name(filename: str, *, stage: MockStage) -> str:
    """校验结果文件名是阶段目录内的纯文件名，不允许路径分隔或盘符。"""
    if not isinstance(filename, str):
        raise MockDataPathError(
            f"Mock {stage.value} 结果文件名必须是字符串，"
            f"收到 {type(filename).__name__}"
        )
    if not filename:
        raise MockDataPathError(f"Mock {stage.value} 结果文件名不能为空")
    if filename != filename.strip():
        raise MockDataPathError(
            f"Mock {stage.value} 结果文件名不能包含首尾空白: {filename!r}"
        )
    if filename in (".", ".."):
        raise MockDataPathError(
            f"Mock {stage.value} 结果文件名不能是相对目录标记: {filename!r}"
        )
    for separator in ("/", "\\", ":", "\0"):
        if separator in filename:
            raise MockDataPathError(
                f"Mock {stage.value} 结果文件名必须是阶段目录内的纯文件名: "
                f"{filename!r}"
            )
    if filename.endswith("."):
        raise MockDataPathError(
            f"Mock {stage.value} 结果文件名不能以点结尾: {filename!r}"
        )
    if filename.split(".", 1)[0].upper() in _RESERVED_WINDOWS_NAMES:
        raise MockDataPathError(
            f"Mock {stage.value} 结果文件名不能是 Windows 保留设备名: {filename!r}"
        )
    return filename


def _is_within(path: Path, directory: Path) -> bool:
    """``path`` 是否位于 ``directory`` 之内；两者都应已解析为绝对路径。"""
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


def _resolve_mock_path(path: Path, *, description: str) -> Path:
    """解析绝对路径；链接成环或目标不可达时以 :class:`MockDataPathError` 失败。"""
    try:
        return path.resolve()
    except (OSError, RuntimeError) as error:
        raise MockDataPathError(f"{description}无法解析: {path}") from error


def _require_file_in_stage(
    candidate: Path,
    *,
    stage: MockStage,
    stage_directory: Path,
    mock_data_directory: Path,
    project_root: Path,
    description: str,
) -> Path:
    """确认候选路径解析后仍是阶段目录内的普通文件，返回解析后的绝对路径。

    校验顺序固定为“数据目录在项目根目录内 → 阶段目录在数据目录内 → 文件是阶段
    目录的直接子项”，因此符号链接、Windows 目录联接（junction）或指向上级目录的
    路径都会在读取前失败，不会把 Mock 数据解析到 ``mock_data`` 或项目之外。返回
    解析后的路径，调用方拿到的是随后真正会被读取的那个文件。
    """
    mock_root = _resolve_mock_path(mock_data_directory, description="Mock 数据目录")
    if not _is_within(mock_root, project_root):
        raise MockDataPathError(
            f"Mock 数据目录不能指向项目根目录之外: {mock_data_directory}"
        )
    stage_root = _resolve_mock_path(stage_directory, description="Mock 阶段目录")
    if not _is_within(stage_root, mock_root):
        raise MockDataPathError(
            f"Mock {stage.value} 阶段目录不能指向 mock_data 之外: {stage_directory}"
        )
    resolved = _resolve_mock_path(candidate, description=description)
    if resolved.parent != stage_root:
        raise MockDataPathError(
            f"{description}必须直接位于 {stage.value} 阶段目录: {candidate}"
        )
    try:
        is_file = resolved.is_file()
    except OSError as error:
        raise MockDataPathError(f"{description}无法访问: {resolved}") from error
    if not is_file:
        raise MockDataPathError(f"{description}不存在或不是普通文件: {resolved}")
    return resolved


@dataclass(frozen=True)
class MockDataPathService:
    """从项目根目录派生只读的 Mock 场景与正式结果路径。

    服务不创建目录、不读取场景内容，也不改变正式 :class:`PathService` 的结果目录。
    场景或结果缺失由 :meth:`require_scenario_path` / :meth:`require_result_path`
    以 :class:`MockDataPathError` 报告，方便后续回放服务在播放前失败。

    两个 ``require_*`` 入口都在读取前解析链接，并确认 ``mock_data`` 仍在项目根目录
    内、阶段目录仍在 ``mock_data`` 内、目标仍是阶段目录的直接子文件；返回的是解析
    后的绝对路径。
    """

    root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root).expanduser().resolve())

    @property
    def project_root(self) -> Path:
        return self.root

    @property
    def mock_data_dir(self) -> Path:
        """Mock 数据的只读根目录。"""
        return self.root / MOCK_DATA_DIRNAME

    @property
    def config_file(self) -> Path:
        """Mock 配置文档；固定对应仓库根目录的 ``config.mock.json``。"""
        return self.root / MOCK_CONFIG_FILENAME

    def stage_dir(self, stage: Union[MockStage, str]) -> Path:
        """返回某阶段的 Mock 数据目录，不检查目录是否存在。"""
        return self.mock_data_dir / coerce_mock_stage(stage).value

    def scenario_path(self, stage: Union[MockStage, str]) -> Path:
        """返回 ``mock_data/<stage>/scenario.json``，不检查文件是否存在。"""
        return self.stage_dir(stage) / MOCK_SCENARIO_FILENAME

    def result_path(
        self, stage: Union[MockStage, str], filename: str
    ) -> Path:
        """返回阶段目录下的正式结果路径，拒绝任何越界文件名。

        文件名来自场景的 ``completion[].file``，只能是纯文件名；传入路径、盘符、
        相对目录标记、Windows 保留设备名或以点结尾的名字都会在拼接前失败，不会把
        结果解析到 ``mock_data/<stage>`` 之外。
        """
        target_stage = coerce_mock_stage(stage)
        directory = self.stage_dir(target_stage)
        return directory / _require_plain_file_name(filename, stage=target_stage)

    def calibration_input_stages(
        self, stage: Union[MockStage, str]
    ) -> tuple[MockStage, ...]:
        """返回启动该阶段前必须已存在的 Mock 校准结果阶段（按依赖顺序）。"""
        return mock_stage_entry(stage).calibration_inputs

    def require_scenario_path(self, stage: Union[MockStage, str]) -> Path:
        """返回已存在的场景文件；越界或缺失时以 :class:`MockDataPathError` 失败。"""
        target_stage = coerce_mock_stage(stage)
        return self._require_stage_file(
            target_stage,
            MOCK_SCENARIO_FILENAME,
            description=f"Mock {target_stage.value} 场景文件",
        )

    def require_result_path(
        self, stage: Union[MockStage, str], filename: str
    ) -> Path:
        """返回已存在的正式结果文件；越界或缺失时以可读错误失败。"""
        target_stage = coerce_mock_stage(stage)
        return self._require_stage_file(
            target_stage,
            _require_plain_file_name(filename, stage=target_stage),
            description=f"Mock {target_stage.value} 正式结果文件",
        )

    def _require_stage_file(
        self, stage: MockStage, filename: str, *, description: str
    ) -> Path:
        stage_directory = self.stage_dir(stage)
        return _require_file_in_stage(
            stage_directory / filename,
            stage=stage,
            stage_directory=stage_directory,
            mock_data_directory=self.mock_data_dir,
            project_root=self.root,
            description=description,
        )


DEFAULT_PATHS = PathService(Path(__file__).resolve().parents[2])
DEFAULT_MOCK_DATA_PATHS = MockDataPathService(DEFAULT_PATHS.project_root)

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
    "CONFIG_FILE", "DEFAULT_MOCK_DATA_PATHS", "ICONS_DIR", "IMAGES_DIR", "LOGS_DIR",
    "MOCK_CONFIG_FILENAME", "MOCK_DATA_DIRNAME", "MOCK_SCENARIO_FILENAME",
    "MOCK_STAGE_INDEX", "MockDataPathError", "MockDataPathService",
    "MockStage", "MockStageEntry", "PathLike",
    "DEFAULT_PATHS", "PathService", "PROJECT_ROOT",
    "TEMP_DIR", "TEST_RESULTS_DIR",
    "coerce_mock_stage", "ensure_directory", "mock_measurement_type_for_stage",
    "mock_stage_entry", "mock_stage_for_measurement_type", "resolve_path",
    "unique_directory_name",
]
