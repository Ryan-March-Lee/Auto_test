"""阶段结果仓库端口：按阶段独立保存、显式路径加载与列举正式测量结果。

三类正式结果分别落在 ``test_results/<stage>/`` 阶段目录，阶段文件是结果本身的
权威来源；运行目录只登记引用与审计信息，不再决定结果之间的复用关系。本模块把
这一边界收敛成应用层契约：

- 保存时调用方必须同时给出**阶段**与**正式结果 DTO**；结果类型必须与阶段相符，
  且结果必须带自身身份（``measurement_id``）和保存时间。端口不提供“按最新文件
  保存/查找”，文件名只由 :func:`application.dto.measurement_results.
  measurement_result_filename` 从身份构造。
- 加载只接受**显式路径**：调用方选中的阶段结果文件必须直接位于对应阶段目录内，
  由仓库实现完成目录边界检查。端口没有 ``latest_path()`` 一类入口，避免正式测量
  隐式消费历史最新结果。
- 列举返回阶段目录内可用的正式结果元数据，跳过损坏、未完成或类型不匹配的文件；
  每条记录携带路径、测量身份、SHA-256 摘要和文件大小，应用层无需自行触碰 JSON。

目录边界、原子写入、拒绝覆盖和完整性校验由基础设施实现负责；应用层只依赖本
模块的协议与纯数据事实。步骤 5、6 分别实现原子保存与显式加载适配器。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Protocol, runtime_checkable

from application.dto.measurement_results import (
    MeasurementResult,
    MeasurementStatus,
    MeasurementType,
    measurement_result_filename,
    require_formal_result,
    require_measurement_identity,
    validate_measurement_id,
    validate_measurement_result_filename,
)
from application.errors import ConfigurationError
from domain.run.models import SHA256_PATTERN

from .types import PathLike


__all__ = [
    "LoadedStageResult",
    "MeasurementStage",
    "StageResultEntry",
    "StageResultError",
    "StageResultReference",
    "StageResultRepository",
    "coerce_measurement_stage",
    "measurement_stage_for_type",
    "measurement_type_for_stage",
    "require_stage_result",
    "require_stage_result_path",
]


class StageResultError(ConfigurationError):
    """阶段结果契约失败：阶段缺失、类型错配或结果缺少正式身份。

    读取/保存阶段结果时，任何“不能作为该阶段正式结果”的情况都以本异常结束，
    调用方不需要靠字符串匹配判断失败原因。它同时是 ``ValueError``（与配置类
    错误一致），既有 ``except ValueError`` 处理器继续生效。
    """


class MeasurementStage(str, Enum):
    """拥有独立结果目录的测量阶段。

    取值就是阶段目录名；与正式测量类型的对应关系见
    :func:`measurement_type_for_stage`。``driver_mapping`` 目录对应的正式类型是
    ``driver_power_mapping``，两者不是同一个字符串。
    """

    CABLE_LOSS = "cable_loss"
    DRIVER_MAPPING = "driver_mapping"
    AMPLIFIER = "amplifier"

    @property
    def measurement_type(self) -> MeasurementType:
        """该阶段允许的正式测量类型。"""
        return _STAGE_MEASUREMENT_TYPES[self]

    @property
    def directory_name(self) -> str:
        """阶段结果目录名；与枚举取值一致，单独暴露以强调它只是路径片段。"""
        return self.value


_STAGE_MEASUREMENT_TYPES: Mapping[MeasurementStage, MeasurementType] = MappingProxyType(
    {
        MeasurementStage.CABLE_LOSS: MeasurementType.CABLE_LOSS,
        MeasurementStage.DRIVER_MAPPING: MeasurementType.DRIVER_POWER_MAPPING,
        MeasurementStage.AMPLIFIER: MeasurementType.AMPLIFIER,
    }
)
_MEASUREMENT_TYPE_STAGES: Mapping[MeasurementType, MeasurementStage] = MappingProxyType(
    {kind: stage for stage, kind in _STAGE_MEASUREMENT_TYPES.items()}
)


def coerce_measurement_stage(value: MeasurementStage | str) -> MeasurementStage:
    """把阶段名收敛为 :class:`MeasurementStage`；未知阶段是明确错误。"""
    if isinstance(value, MeasurementStage):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return MeasurementStage(text)
        except ValueError as error:
            stages = ", ".join(stage.value for stage in MeasurementStage)
            raise StageResultError(
                f"未知的测量阶段: {value!r}；可用阶段: {stages}"
            ) from error
    raise StageResultError(
        "测量阶段必须是 MeasurementStage 或字符串，"
        f"收到 {type(value).__name__}"
    )


def measurement_type_for_stage(
    stage: MeasurementStage | str,
) -> MeasurementType:
    """返回阶段允许的正式测量类型。"""
    return coerce_measurement_stage(stage).measurement_type


def measurement_stage_for_type(
    measurement_type: MeasurementType | str,
) -> MeasurementStage:
    """返回正式测量类型所属的阶段；非正式类型是明确错误。"""
    if isinstance(measurement_type, MeasurementType):
        kind = measurement_type
    elif isinstance(measurement_type, str):
        try:
            kind = MeasurementType(measurement_type.strip())
        except ValueError as error:
            raise StageResultError(
                f"未知的测量结果类型: {measurement_type!r}"
            ) from error
    else:
        raise StageResultError(
            "测量结果类型必须是 MeasurementType 或字符串，"
            f"收到 {type(measurement_type).__name__}"
        )
    stage = _MEASUREMENT_TYPE_STAGES.get(kind)
    if stage is None:  # pragma: no cover - MeasurementType 当前三类都有阶段
        raise StageResultError(f"测量类型没有对应阶段: {kind.value}")
    return stage


def require_stage_result(
    stage: MeasurementStage | str, result: MeasurementResult
) -> MeasurementResult:
    """断言结果可以保存到指定阶段，返回该正式结果。

    保存边界必须同时满足：

    - 阶段存在；
    - 结果是三个具体正式结果之一（共享基类、映射或原始文档都拒绝）；
    - 结果类型与阶段一一对应（例如 ``amplifier`` 结果不能写进 ``cable_loss``）；
    - 新结果带 ``measurement_id`` 和非空 ``created_at``。

    任一条件不满足都抛出 :class:`StageResultError`，端口实现不得把错误类型的结果
    写入阶段目录。
    """
    target = coerce_measurement_stage(stage)
    try:
        formal = require_formal_result(result)
    except ValueError as error:
        raise StageResultError(
            f"阶段 {target.value} 只能保存正式测量结果: {error}"
        ) from error
    expected = target.measurement_type
    if formal.measurement_type is not expected:
        raise StageResultError(
            "结果类型与阶段不符："
            f"阶段 {target.value} 需要 {expected.value} 结果，"
            f"收到 {formal.measurement_type.value}"
        )
    try:
        require_measurement_identity(formal)
    except ValueError as error:
        raise StageResultError(
            f"阶段 {target.value} 的新结果缺少身份或保存时间: {error}"
        ) from error
    return formal


def require_stage_result_path(
    path: PathLike,
    *,
    stage: MeasurementStage | str,
    stage_directory: PathLike,
) -> Path:
    """校验路径是阶段目录下的直接结果文件，返回解析后的绝对路径。

    仓库实现用它落实“目录边界由仓库负责”的契约：路径不能为空、必须直接位于给定
    阶段目录内、文件名必须是 ``<measurement_id>.json``。解析会展开符号链接，因此
    指向阶段目录之外的文件同样被拒绝。校验通过后返回解析后的绝对路径，调用方随后
    才能读取、计算摘要或核对文件大小。
    """
    target = coerce_measurement_stage(stage)
    if path is None or (isinstance(path, str) and not path.strip()):
        raise StageResultError("阶段结果路径不能为空")
    try:
        candidate = Path(path).expanduser()
    except (TypeError, ValueError) as error:
        raise StageResultError(f"阶段结果路径非法: {path!r}") from error
    try:
        directory = Path(stage_directory).expanduser()
    except (TypeError, ValueError) as error:
        raise StageResultError(f"阶段目录非法: {stage_directory!r}") from error
    try:
        resolved = candidate.resolve()
        root = directory.resolve()
    except OSError as error:
        raise StageResultError(f"阶段结果路径无法解析: {path!r}") from error
    try:
        filename = validate_measurement_result_filename(resolved.name)
    except ValueError as error:
        raise StageResultError(
            f"阶段结果文件名非法: {resolved.name!r}: {error}"
        ) from error
    if resolved.parent != root:
        raise StageResultError(
            f"阶段结果路径必须直接位于 {target.value} 阶段目录: {path!r}"
        )
    if filename != resolved.name:  # pragma: no cover - 校验器已保证
        raise StageResultError(f"阶段结果文件名非法: {resolved.name!r}")
    return resolved


@dataclass(frozen=True)
class StageResultReference:
    """一次阶段结果保存/列举得到的结构化事实。

    ``path`` 是阶段目录内的实际文件位置，``measurement_id`` 是结果自身身份，
    ``sha256`` 与 ``size_bytes`` 供测量开始前复核文件未变化。字段都是端口返回的
    事实，应用层不需要再读 JSON 或重新计算摘要。
    """

    stage: MeasurementStage | str
    measurement_id: str
    path: Path
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage", coerce_measurement_stage(self.stage))
        try:
            identity = validate_measurement_id(self.measurement_id)
        except ValueError as error:
            raise StageResultError(f"阶段结果的测量身份非法: {error}") from error
        object.__setattr__(self, "measurement_id", identity)
        try:
            resolved = Path(self.path)
        except (TypeError, ValueError) as error:
            raise StageResultError(f"阶段结果路径非法: {self.path!r}") from error
        if not str(resolved).strip() or str(resolved) == ".":
            raise StageResultError("阶段结果路径不能为空")
        try:
            expected_name = measurement_result_filename(self.measurement_id)
        except ValueError as error:
            raise StageResultError(
                f"阶段结果文件名必须由 measurement_id 构造: {error}"
            ) from error
        if resolved.name != expected_name:
            raise StageResultError(
                "阶段结果文件名必须由 measurement_id 构造："
                f"{resolved.name!r} != {expected_name!r}"
            )
        object.__setattr__(self, "path", resolved)
        digest = self.sha256 if isinstance(self.sha256, str) else ""
        if not SHA256_PATTERN.fullmatch(digest):
            raise StageResultError("阶段结果摘要必须是 64 位小写十六进制 sha256")
        object.__setattr__(self, "sha256", digest)
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes < 0
        ):
            raise StageResultError("阶段结果大小必须是非负整数")


@dataclass(frozen=True)
class StageResultEntry:
    """列举阶段结果时的一条只读元数据。

    ``reference`` 携带路径、身份、摘要和大小；其余字段是列表展示所需的正式事实。
    实现只返回可读取的 ``completed`` 正式结果，损坏、未完成或类型不匹配的文件
    直接跳过，不伪造占位记录。构造本结构本身也会拒绝非 ``completed`` 状态，
    避免不同实现在“是否列举未完成结果”上产生分歧。
    """

    reference: StageResultReference
    measurement_type: MeasurementType | str
    status: MeasurementStatus | str
    created_at: str
    run_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.reference, StageResultReference):
            raise StageResultError("阶段结果条目必须包含 StageResultReference")
        try:
            kind = (
                self.measurement_type
                if isinstance(self.measurement_type, MeasurementType)
                else MeasurementType(str(self.measurement_type).strip())
            )
            status = (
                self.status
                if isinstance(self.status, MeasurementStatus)
                else MeasurementStatus(str(self.status).strip())
            )
        except ValueError as error:
            raise StageResultError(f"阶段结果条目包含未知类型或状态: {error}") from error
        if status is not MeasurementStatus.COMPLETED:
            raise StageResultError(
                f"阶段结果条目只接受 completed 结果，收到 {status.value}"
            )
        if kind is not self.reference.stage.measurement_type:
            raise StageResultError(
                "结果类型与阶段不符："
                f"阶段 {self.reference.stage.value} 记录为 {kind.value}"
            )
        if not isinstance(self.created_at, str) or not self.created_at.strip():
            raise StageResultError("阶段结果条目必须包含保存时间 created_at")
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise StageResultError("阶段结果条目必须包含非空 run_id")
        object.__setattr__(self, "measurement_type", kind)
        object.__setattr__(self, "status", status)

    @property
    def path(self) -> Path:
        return self.reference.path

    @property
    def measurement_id(self) -> str:
        return self.reference.measurement_id

    @property
    def sha256(self) -> str:
        return self.reference.sha256

    @property
    def size_bytes(self) -> int:
        return self.reference.size_bytes


@dataclass(frozen=True)
class LoadedStageResult:
    """按显式路径加载得到的正式结果及其文件事实。

    ``reference`` 中的摘要和大小用于测量开始前的再次核对；``result`` 是已经过
    schema、类型、状态和身份校验的正式 DTO：必须是 ``completed`` 状态，且带
    非空 ``measurement_id`` 与 ``created_at``。字段不匹配（类型、阶段、
    测量身份或状态）说明端口实现违反了契约，构造时直接拒绝。
    """

    reference: StageResultReference
    result: MeasurementResult

    def __post_init__(self) -> None:
        if not isinstance(self.reference, StageResultReference):
            raise StageResultError("加载结果必须包含 StageResultReference")
        try:
            formal = require_formal_result(self.result)
        except ValueError as error:
            raise StageResultError(f"加载结果不是正式测量结果: {error}") from error
        try:
            require_measurement_identity(formal)
        except ValueError as error:
            raise StageResultError(
                f"加载结果缺少测量身份或保存时间: {error}"
            ) from error
        if formal.status is not MeasurementStatus.COMPLETED:
            raise StageResultError(
                f"加载结果必须是 completed 状态: 收到 {formal.status.value}"
            )
        if formal.measurement_type is not self.reference.stage.measurement_type:
            raise StageResultError(
                "加载结果类型与阶段不符："
                f"阶段 {self.reference.stage.value} 需要 "
                f"{self.reference.stage.measurement_type.value}，"
                f"收到 {formal.measurement_type.value}"
            )
        if formal.measurement_id != self.reference.measurement_id:
            raise StageResultError(
                "加载结果的 measurement_id 与文件引用不一致："
                f"{formal.measurement_id!r} != {self.reference.measurement_id!r}"
            )
        object.__setattr__(self, "result", formal)

    @property
    def path(self) -> Path:
        return self.reference.path

    @property
    def measurement_id(self) -> str:
        return self.reference.measurement_id

    @property
    def sha256(self) -> str:
        return self.reference.sha256

    @property
    def size_bytes(self) -> int:
        return self.reference.size_bytes


@runtime_checkable
class StageResultRepository(Protocol):
    """阶段结果的保存、显式路径加载与列举端口。

    实现方负责阶段目录的创建、原子写入、拒绝覆盖、目录边界检查、JSON 解析与
    正式 DTO 校验；调用方只给出阶段、正式结果或显式路径，不接触运行目录，也不
    依赖“最新结果”。任何“不能作为该阶段正式结果”的失败都必须抛出
    :class:`StageResultError`（或其子类），不得返回半成品路径。
    """

    def save(
        self,
        stage: MeasurementStage | str,
        result: MeasurementResult,
    ) -> StageResultReference:
        """把正式结果写入其阶段目录并返回实际文件事实。

        实现必须自动创建阶段目录（若不存在）、以
        :func:`application.dto.measurement_results.measurement_result_filename`
        构造文件名、在目标已存在时拒绝覆盖，并用同目录临时文件加原子替换写入。
        结果类型与阶段不符或缺少测量身份时抛出 :class:`StageResultError`。
        """
        ...

    def load(
        self,
        stage: MeasurementStage | str,
        path: PathLike,
    ) -> LoadedStageResult:
        """从显式路径读取一个阶段结果，返回正式 DTO 与文件事实。

        路径必须直接位于该阶段的目录内；实现按“阶段目录边界 → 文件存在 →
        JSON 解析 → 正式 DTO → 类型/状态/schema”顺序校验，缺失、越界、损坏、
        错误类型或未完成状态都抛出 :class:`StageResultError`。端口不扫描最新文件。
        """
        ...

    def list_results(
        self,
        stage: MeasurementStage | str,
    ) -> tuple[StageResultEntry, ...]:
        """列举阶段目录内可用的正式结果，按保存时间从新到旧排序。

        只返回可读取的 ``completed`` 正式结果；损坏、未完成或类型不匹配的文件
        被跳过。目录不存在时返回空元组，不创建目录。
        """
        ...
