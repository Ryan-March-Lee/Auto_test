"""仿真运行标记：唯一判据，供运行装配、连接守卫与加载守卫共用。

仿真运行与正式运行共用 :class:`app.run_context.PreparedRun`、阶段记录与归档，
唯一区别是端口读数来自冻结的 ``readings.json``。因此“这次运行是仿真运行”不能靠
进程内布尔值或目录名猜测，必须能从运行目录本身证明：

- 运行装配把标记写入 ``inputs/simulation.json`` 并通过运行仓储登记 manifest；
- :func:`read_simulation_marker` 只把**已登记且校验通过**的标记当作仿真事实；
- :func:`is_simulated_run` 是唯一判据：位于生产默认仿真根下的运行目录同样按仿真
  运行拒绝，覆盖“创建后、登记前崩溃”的窗口；其它自定义根以标记为准（只用于测试）。

标记缺失、损坏、摘要不一致或未登记导致无法判断时，守卫必须**失败关闭**
（:class:`application.run_service.RunNotResumable`），绝不把仿真运行当成真机运行
连接，也不把它交给“加载运行”入口恢复。本模块不读写运行目录之外的路径，不连接
仪器，也不修改 manifest schema：标记只是运行仓储登记的另一条 input 记录。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from application.dto.measurement_results import MeasurementType
from application.ports.run_repository import RunRepositoryPort
from application.run_service import RunNotResumable
from domain.run.models import SHA256_PATTERN


#: 仿真运行标记在运行目录内的固定相对路径；只写在 ``inputs/`` 下。
SIMULATION_MARKER_PATH = "inputs/simulation.json"
#: 标记文档类型与版本；升级版本必须同时升级解析与守卫，不允许静默降级。
SIMULATION_MARKER_KIND = "simulation_run"
SIMULATION_MARKER_SCHEMA_VERSION = 1
SIMULATION_MARKER_SCHEMA_VERSIONS = frozenset({SIMULATION_MARKER_SCHEMA_VERSION})

#: 标记里的路径是相对项目根的数据包引用，只用于审计；真实读取仍由 Mock 路径服务解析。
_MOCK_REFERENCE_PREFIX = "mock_data/"

_MARKER_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "run_id",
        "created_at",
        "time_scale",
        "snapshot_name",
        "readings",
    }
)
_READING_FIELDS = frozenset(
    {
        "measurement_type",
        "readings_path",
        "readings_sha256",
        "readings_size_bytes",
        "scenario_path",
        "scenario_sha256",
    }
)


@dataclass(frozen=True)
class SimulationReadingsDigest:
    """标记里记录的一份读数包及其上游场景摘要（审计事实）。

    ``readings_path`` / ``scenario_path`` 是相对项目根的 ``mock_data/...`` 引用；
    运行期不会按它重新读文件，只用于回答“本次实际使用了哪一份数据”。
    """

    measurement_type: MeasurementType
    readings_path: str
    readings_sha256: str
    readings_size_bytes: int
    scenario_path: str
    scenario_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "measurement_type",
            _coerce_measurement_type(self.measurement_type),
        )
        object.__setattr__(
            self,
            "readings_path",
            _require_mock_reference(self.readings_path, "readings_path"),
        )
        object.__setattr__(
            self,
            "readings_sha256",
            _require_sha256(self.readings_sha256, "readings_sha256"),
        )
        size = self.readings_size_bytes
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError(
                "readings_size_bytes 必须是非负整数，"
                f"收到 {size!r}"
            )
        object.__setattr__(self, "readings_size_bytes", size)
        object.__setattr__(
            self,
            "scenario_path",
            _require_mock_reference(self.scenario_path, "scenario_path"),
        )
        object.__setattr__(
            self,
            "scenario_sha256",
            _require_sha256(self.scenario_sha256, "scenario_sha256"),
        )

    def to_document(self) -> dict[str, Any]:
        return {
            "measurement_type": self.measurement_type.value,
            "readings_path": self.readings_path,
            "readings_sha256": self.readings_sha256,
            "readings_size_bytes": self.readings_size_bytes,
            "scenario_path": self.scenario_path,
            "scenario_sha256": self.scenario_sha256,
        }


@dataclass(frozen=True)
class SimulationMarker:
    """一次仿真运行的不可变标记；只保存事实，不解释测量结果。"""

    schema_version: int
    run_id: str
    created_at: str
    time_scale: float
    snapshot_name: str
    readings: tuple[SimulationReadingsDigest, ...]

    def __post_init__(self) -> None:
        version = self.schema_version
        if isinstance(version, bool) or not isinstance(version, int):
            raise ValueError(
                "schema_version 必须是整数，收到 " + type(version).__name__
            )
        if version not in SIMULATION_MARKER_SCHEMA_VERSIONS:
            supported = "、".join(
                str(item) for item in sorted(SIMULATION_MARKER_SCHEMA_VERSIONS)
            )
            raise ValueError(
                f"不支持的仿真运行标记 schema_version: {version!r}；"
                f"当前支持: {supported}"
            )
        object.__setattr__(self, "schema_version", version)
        object.__setattr__(self, "run_id", _require_text(self.run_id, "run_id"))
        object.__setattr__(
            self, "created_at", _require_text(self.created_at, "created_at")
        )
        object.__setattr__(
            self, "time_scale", _require_time_scale(self.time_scale)
        )
        object.__setattr__(
            self,
            "snapshot_name",
            _require_text(self.snapshot_name, "snapshot_name"),
        )
        items = self.readings
        if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
            raise ValueError(
                "readings 必须是仿真读数摘要数组，"
                f"收到 {type(items).__name__}"
            )
        digests = tuple(items)
        if not digests:
            raise ValueError("readings 至少需要一个阶段的读数摘要")
        seen: set[MeasurementType] = set()
        for index, item in enumerate(digests):
            if not isinstance(item, SimulationReadingsDigest):
                raise ValueError(
                    f"readings[{index}] 必须是 SimulationReadingsDigest，"
                    f"收到 {type(item).__name__}"
                )
            if item.measurement_type in seen:
                raise ValueError(
                    "readings 重复登记了测量类型: "
                    f"{item.measurement_type.value}"
                )
            seen.add(item.measurement_type)
        object.__setattr__(self, "readings", digests)

    @classmethod
    def from_document(cls, document: Any) -> "SimulationMarker":
        """严格解析标记文档；未知字段、缺失字段与非法值都失败。"""
        root = _require_mapping(document, "仿真运行标记")
        missing = _MARKER_FIELDS - set(root)
        unknown = set(root) - _MARKER_FIELDS
        if missing:
            raise ValueError(
                "仿真运行标记缺少字段: " + "、".join(sorted(missing))
            )
        if unknown:
            raise ValueError(
                "仿真运行标记包含未知字段: " + "、".join(sorted(unknown))
            )
        kind = _require_text(root["kind"], "kind")
        if kind != SIMULATION_MARKER_KIND:
            raise ValueError(
                f"仿真运行标记的 kind 必须是 {SIMULATION_MARKER_KIND!r}，"
                f"收到 {kind!r}"
            )
        readings = root["readings"]
        if isinstance(readings, (str, bytes)) or not isinstance(readings, Sequence):
            raise ValueError(
                "仿真运行标记的 readings 必须是数组，"
                f"收到 {type(readings).__name__}"
            )
        digests = tuple(
            _parse_reading_digest(item, index)
            for index, item in enumerate(readings)
        )
        return cls(
            schema_version=root["schema_version"],
            run_id=root["run_id"],
            created_at=root["created_at"],
            time_scale=root["time_scale"],
            snapshot_name=root["snapshot_name"],
            readings=digests,
        )

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": SIMULATION_MARKER_KIND,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "time_scale": self.time_scale,
            "snapshot_name": self.snapshot_name,
            "readings": [item.to_document() for item in self.readings],
        }


def read_simulation_marker(
    run_repository: RunRepositoryPort, run_id: str
) -> SimulationMarker | None:
    """读取并校验运行 manifest 登记的仿真标记；没有登记时返回 ``None``。

    未登记的 ``inputs/simulation.json`` 不构成仿真标记：真实运行目录里出现同名
    未登记文件时不能被误判为仿真运行。登记了却缺失、被替换或内容损坏时按失败关闭
    处理：无法证明“这是不是仿真运行”时不能连接真实仪器。
    """
    manifest = run_repository.load_manifest(run_id)
    registered = any(
        reference.path == SIMULATION_MARKER_PATH for reference in manifest.inputs
    )
    if not registered:
        return None
    if not callable(getattr(run_repository, "read_run_json", None)):
        raise RunNotResumable(
            f"运行 {run_id} 的仿真运行标记无法校验：运行仓储缺少 read_run_json"
        )
    try:
        document = run_repository.read_run_json(run_id, SIMULATION_MARKER_PATH)
    except RunNotResumable:
        raise
    except (OSError, ValueError, TypeError) as error:
        raise RunNotResumable(
            f"运行 {run_id} 的仿真运行标记校验失败: {error}"
        ) from error
    try:
        marker = SimulationMarker.from_document(document)
    except (ValueError, TypeError) as error:
        raise RunNotResumable(
            f"运行 {run_id} 的仿真运行标记不可解释: {error}"
        ) from error
    if marker.run_id != run_id:
        raise RunNotResumable(
            f"运行 {run_id} 的仿真运行标记属于另一个运行 "
            f"{marker.run_id!r}；拒绝把它当作本次运行"
        )
    return marker


def is_simulated_run(
    run_repository: RunRepositoryPort,
    run_id: str,
    *,
    run_directory: str | Path | None = None,
    default_simulation_root: str | Path | None = None,
) -> bool:
    """判断一个运行是否是仿真运行；这是连接/加载守卫的唯一判据。

    - 运行目录位于 ``default_simulation_root`` 之下时直接判定为仿真运行：运行创建、
      标记写入与 manifest 登记不是一次原子操作，崩溃窗口里可能只有目录没有标记；
    - 其它根（测试或自定义注入）以 manifest 登记的标记为准；
    - 无法读取/解释已登记标记时抛 :class:`RunNotResumable`（失败关闭）。
    """
    if run_directory is not None and default_simulation_root is not None:
        if _is_within(Path(run_directory), Path(default_simulation_root)):
            return True
    if not callable(getattr(run_repository, "load_manifest", None)):
        # 没有运行契约仓储的对象连“是不是仿真运行”都无法表达；后续运行契约校验会
        # 明确拒绝，这里不替它猜一个布尔值。
        return False
    return read_simulation_marker(run_repository, run_id) is not None


def reject_simulated_run(
    run_repository: RunRepositoryPort,
    run_id: str,
    *,
    run_directory: str | Path | None = None,
    default_simulation_root: str | Path | None = None,
) -> None:
    """已被判定为仿真运行时抛出 :class:`RunNotResumable`。"""
    if not is_simulated_run(
        run_repository,
        run_id,
        run_directory=run_directory,
        default_simulation_root=default_simulation_root,
    ):
        return
    raise RunNotResumable(
        f"运行 {run_id} 是仿真运行：仿真运行不能连接真实仪器，"
        "也不提供加载运行入口；请重新进入仿真运行"
    )


def reject_simulated_prepared_run(
    prepared_run: Any, *, default_simulation_root: str | Path | None = None
) -> None:
    """对 ``PreparedRun`` 形态的运行对象执行同一条守卫。

    缺少运行标识或运行仓储的对象不属于运行契约，交由各自入口的既有校验报告，
    这里不替它们判断。
    """
    run_id = getattr(prepared_run, "run_id", None)
    repository = getattr(prepared_run, "run_repository", None)
    if not isinstance(run_id, str) or not run_id.strip() or repository is None:
        return
    reject_simulated_run(
        repository,
        run_id,
        run_directory=getattr(prepared_run, "run_directory", None),
        default_simulation_root=default_simulation_root,
    )


# -- 解析辅助 ---------------------------------------------------------------


def _parse_reading_digest(value: Any, index: int) -> SimulationReadingsDigest:
    description = f"readings[{index}]"
    root = _require_mapping(value, description)
    missing = _READING_FIELDS - set(root)
    unknown = set(root) - _READING_FIELDS
    if missing:
        raise ValueError(f"{description} 缺少字段: " + "、".join(sorted(missing)))
    if unknown:
        raise ValueError(f"{description} 包含未知字段: " + "、".join(sorted(unknown)))
    return SimulationReadingsDigest(
        measurement_type=root["measurement_type"],
        readings_path=root["readings_path"],
        readings_sha256=root["readings_sha256"],
        readings_size_bytes=root["readings_size_bytes"],
        scenario_path=root["scenario_path"],
        scenario_sha256=root["scenario_sha256"],
    )


def _require_mapping(value: Any, description: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{description} 必须是 JSON 对象，收到 {type(value).__name__}")
    return value


def _require_text(value: Any, description: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{description} 必须是非空字符串，收到 {value!r}")
    return value.strip()


def _require_sha256(value: Any, description: str) -> str:
    text = _require_text(value, description)
    if not SHA256_PATTERN.fullmatch(text):
        raise ValueError(
            f"{description} 必须是 64 位小写十六进制 SHA-256，收到 {text!r}"
        )
    return text


def _require_mock_reference(value: Any, description: str) -> str:
    text = _require_text(value, description)
    if not text.startswith(_MOCK_REFERENCE_PREFIX):
        raise ValueError(
            f"{description} 必须是 mock_data/<阶段>/<文件名> 形式的相对路径，"
            f"收到 {text!r}"
        )
    parts = text.split("/")
    if len(parts) != 3 or any(part in ("", ".", "..") for part in parts):
        raise ValueError(
            f"{description} 必须是 mock_data/<阶段>/<文件名> 形式的相对路径，"
            f"收到 {text!r}"
        )
    if "\\" in text or re.search(r"[A-Za-z]:", text):
        raise ValueError(f"{description} 不能包含盘符或反斜线: {text!r}")
    return text


def _require_time_scale(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"time_scale 必须是数字，收到 {type(value).__name__}"
        )
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"time_scale 必须是有限正数，收到 {value!r}")
    return number


def _coerce_measurement_type(value: Any) -> MeasurementType:
    if isinstance(value, MeasurementType):
        return value
    try:
        return MeasurementType(value)
    except (TypeError, ValueError) as error:
        allowed = "、".join(kind.value for kind in MeasurementType)
        raise ValueError(
            f"未知测量类型: {value!r}；可用类型: {allowed}"
        ) from error


def _is_within(path: Path, directory: Path) -> bool:
    try:
        resolved_path = path.expanduser().resolve()
        resolved_directory = directory.expanduser().resolve()
        resolved_path.relative_to(resolved_directory)
    except (OSError, RuntimeError, ValueError):
        return False
    return True


__all__ = [
    "SIMULATION_MARKER_KIND",
    "SIMULATION_MARKER_PATH",
    "SIMULATION_MARKER_SCHEMA_VERSION",
    "SIMULATION_MARKER_SCHEMA_VERSIONS",
    "SimulationMarker",
    "SimulationReadingsDigest",
    "is_simulated_run",
    "read_simulation_marker",
    "reject_simulated_prepared_run",
    "reject_simulated_run",
]
