"""运行目录（Run）的数据契约：manifest、阶段状态和文件引用。

本模块只定义纯数据模型和校验规则：

- 不读写文件、不创建目录、不连接仪器，也不导入 Qt 或基础设施。
- manifest 内的所有文件路径统一使用相对运行目录的 POSIX 路径，便于整体搬迁。
- 阶段状态和运行状态的合法转换在此集中定义，供后续运行仓储与阶段服务复用。

本次只建立契约，不改变既有测量流程。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4


MANIFEST_SCHEMA_VERSION = 1
SUPPORTED_MANIFEST_SCHEMA_VERSIONS = frozenset({MANIFEST_SCHEMA_VERSION})

MANIFEST_FILENAME = "manifest.json"
CONFIG_FILENAME = "config.json"
INPUTS_DIRNAME = "inputs"
RESULTS_DIRNAME = "results"
RUNS_DIRNAME = "runs"

RUN_ID_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"
# 兼容既有历史运行目录名（例如 ``20260821-120000-test``），同时禁止路径穿越字符。
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
# 新流程生成的 run_id 采用更严格的 ``<UTC 时间戳>_<随机短串>`` 格式。
GENERATED_RUN_ID_PATTERN = re.compile(r"^\d{8}T\d{6}Z_[0-9a-f]{4,32}$")
RUN_ID_TOKEN_PATTERN = re.compile(r"^[0-9a-f]{4,32}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")

STAGE_CABLE_LOSS = "cable_loss"
STAGE_DRIVER_MAPPING = "driver_mapping"
STAGE_AMPLIFIER = "amplifier"
DEFAULT_STAGE_NAMES = (STAGE_CABLE_LOSS, STAGE_DRIVER_MAPPING, STAGE_AMPLIFIER)

CABLE_LOSS_INPUT_PATH = "inputs/cable_loss.json"
DRIVER_MAPPING_INPUT_PATH = "inputs/driver_mapping.json"
# 主功放阶段的权威结果文件；主功放允许存在多个拆分结果文件，因此它不是
# ``default_stage_result_path`` 的固定路径，而由调用方显式指定为主结果。
AMPLIFIER_RESULT_PATH = "results/amplifier_measurement.json"

# 阶段结果在运行目录内的固定相对路径；``amplifier`` 允许存在多个结果文件，故为空。
STAGE_RESULT_PATHS: Mapping[str, str] = MappingProxyType(
    {
        STAGE_CABLE_LOSS: CABLE_LOSS_INPUT_PATH,
        STAGE_DRIVER_MAPPING: DRIVER_MAPPING_INPUT_PATH,
    }
)

_UNCHANGED: Any = object()


def utc_now_iso(now: datetime | None = None) -> str:
    """返回 UTC ISO-8601 秒级时间戳，格式与 manifest 示例一致。"""
    moment = now if now is not None else datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise ValueError("时间必须带时区信息")
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def generate_run_id(now: datetime | None = None, *, token: str | None = None) -> str:
    """按 ``<UTC 时间戳>_<随机短串>`` 规则生成稳定、唯一的 ``run_id``。"""
    moment = now if now is not None else datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise ValueError("时间必须带时区信息")
    suffix = uuid4().hex[:8] if token is None else token
    if not isinstance(suffix, str) or not RUN_ID_TOKEN_PATTERN.fullmatch(suffix):
        raise ValueError("run_id 随机短串必须是 4-32 位小写十六进制")
    stamp = moment.astimezone(timezone.utc).strftime(RUN_ID_TIMESTAMP_FORMAT)
    return validate_generated_run_id(f"{stamp}_{suffix}")


def validate_generated_run_id(run_id: str) -> str:
    """校验新流程生成的 ``run_id`` 严格符合目录命名契约。"""
    if not isinstance(run_id, str) or not GENERATED_RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError(
            "生成型 run_id 必须形如 <UTC 时间戳>_<4-32 位小写十六进制>，"
            "例如 20261022T153000Z_a1b2c3d4"
        )
    return run_id


def validate_run_id(run_id: str) -> str:
    """校验 ``run_id`` 可安全用作目录名，并拒绝路径穿越片段。

    这里只保证文件名安全并兼容历史运行目录名，不保证新流程格式；
    新生成的运行请使用 :func:`validate_generated_run_id` 校验。
    """
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run_id 必须是 1-100 个字母、数字、点、下划线或短横线")
    if ".." in run_id:
        raise ValueError("run_id 不能包含 '..' 路径片段")
    return run_id


def normalize_relative_path(value: Any, *, field_name: str = "path") -> str:
    """校验并规范化运行目录内的相对路径（POSIX 分隔符）。

    拒绝空值、绝对路径、Windows 盘符、``.``/``..`` 片段和空路径段，
    从而避免 manifest 内出现可穿越运行目录的引用。
    """
    if not isinstance(value, str):
        raise ValueError(f"{field_name} 必须是字符串")
    candidate = value.strip()
    if not candidate:
        raise ValueError(f"{field_name} 不能为空")
    if "\x00" in candidate:
        raise ValueError(f"{field_name} 不能包含空字符")
    candidate = candidate.replace("\\", "/")
    if candidate.startswith("/"):
        raise ValueError(f"{field_name} 必须是相对路径，不能是绝对路径: {value!r}")
    if _WINDOWS_DRIVE_PATTERN.match(candidate):
        raise ValueError(f"{field_name} 不能包含盘符: {value!r}")
    segments = candidate.split("/")
    if any(segment == "" for segment in segments):
        raise ValueError(f"{field_name} 不能包含空路径段: {value!r}")
    if any(segment in (".", "..") for segment in segments):
        raise ValueError(f"{field_name} 不能包含 '.' 或 '..' 路径段: {value!r}")
    return "/".join(segments)


def is_relative_path(value: Any) -> bool:
    """判断值是否可作为 manifest 内的相对路径。"""
    try:
        normalize_relative_path(value)
    except ValueError:
        return False
    return True


def default_stage_result_path(stage_name: str) -> str | None:
    """返回阶段结果的标准相对路径；没有固定路径的阶段返回 ``None``。"""
    return STAGE_RESULT_PATHS.get(stage_name)


class RunStatus(str, Enum):
    """运行级状态。"""

    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class StageStatus(str, Enum):
    """阶段状态。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STAGE_STATUSES = frozenset(
    {
        StageStatus.COMPLETED,
        StageStatus.SKIPPED,
        StageStatus.FAILED,
        StageStatus.CANCELLED,
    }
)
TERMINAL_RUN_STATUSES = frozenset(
    {
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }
)

# 已完成的阶段不允许静默回到 running；重跑必须创建新运行或显式覆盖。
STAGE_STATUS_TRANSITIONS: Mapping[StageStatus, frozenset[StageStatus]] = MappingProxyType(
    {
        StageStatus.PENDING: frozenset(
            {StageStatus.RUNNING, StageStatus.SKIPPED, StageStatus.CANCELLED}
        ),
        StageStatus.RUNNING: frozenset(
            {StageStatus.COMPLETED, StageStatus.FAILED, StageStatus.CANCELLED}
        ),
        StageStatus.COMPLETED: frozenset(),
        StageStatus.SKIPPED: frozenset(),
        StageStatus.FAILED: frozenset(),
        StageStatus.CANCELLED: frozenset(),
    }
)

RUN_STATUS_TRANSITIONS: Mapping[RunStatus, frozenset[RunStatus]] = MappingProxyType(
    {
        RunStatus.CREATED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.FAILED}),
        RunStatus.RUNNING: frozenset(
            {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
                RunStatus.INTERRUPTED,
            }
        ),
        RunStatus.INTERRUPTED: frozenset(
            {RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.FAILED}
        ),
        RunStatus.COMPLETED: frozenset(),
        RunStatus.FAILED: frozenset(),
        RunStatus.CANCELLED: frozenset(),
    }
)


def coerce_status(value: Any, enum_type: type[Enum], *, field_name: str):
    """把字符串或枚举值转成目标状态枚举，非法值直接拒绝。"""
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        try:
            return enum_type(value)
        except ValueError as error:
            allowed = ", ".join(item.value for item in enum_type)
            raise ValueError(f"{field_name} 非法: {value!r}，允许值: {allowed}") from error
    raise ValueError(f"{field_name} 必须是 {enum_type.__name__} 或字符串")


def validate_transition(current, target, *, transitions, label: str = "状态", enum_type=None):
    """校验状态转换是否被允许，并返回规范化后的目标状态。

    字符串输入会按转换表的枚举类型统一转换，非法状态和非法转换都抛 ``ValueError``，
    不会向调用方泄漏 ``AttributeError``。
    """
    status_enum = enum_type or _transition_enum_type(transitions)
    if status_enum is not None:
        current = coerce_status(current, status_enum, field_name=f"{label}当前值")
        target = coerce_status(target, status_enum, field_name=f"{label}目标值")
    allowed = transitions.get(current, frozenset())
    if target not in allowed:
        allowed_text = ", ".join(sorted(_status_text(item) for item in allowed)) or "无"
        raise ValueError(
            f"{label} 不允许从 {_status_text(current)} 转换到 {_status_text(target)}，"
            f"允许: {allowed_text}"
        )
    return target


def _transition_enum_type(transitions) -> type[Enum] | None:
    try:
        first = next(iter(transitions))
    except (StopIteration, TypeError):
        return None
    return type(first) if isinstance(first, Enum) else None


def _status_text(value: Any) -> str:
    return str(getattr(value, "value", value))


@dataclass(frozen=True)
class FileReference:
    """运行目录内的一个文件引用。

    ``path`` 是相对运行目录的 POSIX 路径，例如 ``inputs/cable_loss.json``。
    """

    path: str
    sha256: str | None = None
    size_bytes: int | None = None
    created_at: str | None = None
    stage: str | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "path", normalize_relative_path(self.path, field_name="文件引用路径")
        )
        if self.sha256 is not None:
            digest = str(self.sha256)
            if not SHA256_PATTERN.fullmatch(digest):
                raise ValueError(f"sha256 必须是 64 位小写十六进制: {self.sha256!r}")
            object.__setattr__(self, "sha256", digest)
        if self.size_bytes is not None:
            if (
                isinstance(self.size_bytes, bool)
                or not isinstance(self.size_bytes, int)
                or self.size_bytes < 0
            ):
                raise ValueError("size_bytes 必须是非负整数")
        if self.stage is not None and not str(self.stage).strip():
            raise ValueError("stage 不能为空字符串")
        for name in ("created_at", "description"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{name} 必须是字符串")

    def to_dict(self) -> dict[str, Any]:
        value = {
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at,
            "stage": self.stage,
            "description": self.description,
        }
        return {key: item for key, item in value.items() if item is not None}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FileReference":
        if not isinstance(value, Mapping):
            raise ValueError("文件引用必须是 JSON 对象")
        if "path" not in value:
            raise ValueError("文件引用缺少 path")
        return cls(
            path=value["path"],
            sha256=value.get("sha256"),
            size_bytes=value.get("size_bytes"),
            created_at=value.get("created_at"),
            stage=value.get("stage"),
            description=value.get("description"),
        )


@dataclass(frozen=True)
class StageRecord:
    """运行目录内一个阶段的状态、结果引用和错误信息。"""

    name: str
    status: StageStatus = StageStatus.PENDING
    result: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    error: str | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("阶段名不能为空")
        object.__setattr__(
            self,
            "status",
            coerce_status(self.status, StageStatus, field_name=f"阶段 {self.name} 的状态"),
        )
        if self.result is not None:
            object.__setattr__(
                self,
                "result",
                normalize_relative_path(self.result, field_name=f"阶段 {self.name} 的结果路径"),
            )
        if self.status is StageStatus.COMPLETED:
            if self.result is None:
                raise ValueError(f"阶段 {self.name} 完成时必须登记结果路径")
        elif self.result is not None:
            raise ValueError(
                f"阶段 {self.name} 只有在 completed 状态才能登记结果，"
                f"当前为 {self.status.value}"
            )
        for field_name in ("started_at", "ended_at", "error", "note"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{field_name} 必须是字符串")

    def transition_to(
        self,
        target,
        *,
        now: datetime | None = None,
        result: Any = _UNCHANGED,
        error: Any = _UNCHANGED,
        note: Any = _UNCHANGED,
    ) -> "StageRecord":
        """返回状态转换后的新记录；非法转换直接拒绝。"""
        target_status = coerce_status(target, StageStatus, field_name=f"阶段 {self.name} 的状态")
        validate_transition(
            self.status,
            target_status,
            transitions=STAGE_STATUS_TRANSITIONS,
            label=f"阶段 {self.name}",
            enum_type=StageStatus,
        )
        if target_status is StageStatus.COMPLETED:
            if (result is _UNCHANGED or result is None) and self.result is None:
                raise ValueError(f"阶段 {self.name} 完成时必须提供结果路径")
        changes: dict[str, Any] = {"status": target_status}
        if target_status is StageStatus.RUNNING and self.started_at is None:
            changes["started_at"] = utc_now_iso(now)
        if target_status in TERMINAL_STAGE_STATUSES:
            changes["ended_at"] = utc_now_iso(now)
        for key, value in (("result", result), ("error", error), ("note", note)):
            if value is not _UNCHANGED:
                changes[key] = value
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        value = {
            "name": self.name,
            "status": self.status.value,
            "result": self.result,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "error": self.error,
            "note": self.note,
        }
        return {key: item for key, item in value.items() if item is not None}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StageRecord":
        if not isinstance(value, Mapping):
            raise ValueError("阶段记录必须是 JSON 对象")
        return cls(
            name=value.get("name", ""),
            status=value.get("status", StageStatus.PENDING),
            result=value.get("result"),
            started_at=value.get("started_at"),
            ended_at=value.get("ended_at"),
            error=value.get("error"),
            note=value.get("note"),
        )


@dataclass(frozen=True)
class RunManifest:
    """运行目录的索引与状态来源。

    ``stages``、``inputs``、``outputs`` 中的路径都是相对运行目录的 POSIX 路径。
    """

    run_id: str
    created_at: str
    schema_version: int = MANIFEST_SCHEMA_VERSION
    updated_at: str | None = None
    status: RunStatus = RunStatus.CREATED
    software_version: str = "unknown"
    config: FileReference | None = None
    stages: Mapping[str, StageRecord] = field(default_factory=dict)
    inputs: tuple[FileReference, ...] = ()
    outputs: tuple[FileReference, ...] = ()
    error: str | None = None
    notes: str | None = None
    product_name: str | None = None

    def __post_init__(self) -> None:
        validate_run_id(self.run_id)
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version not in SUPPORTED_MANIFEST_SCHEMA_VERSIONS
        ):
            supported = ", ".join(str(item) for item in sorted(SUPPORTED_MANIFEST_SCHEMA_VERSIONS))
            raise ValueError(
                f"不支持的 manifest schema_version: {self.schema_version!r}，支持: {supported}"
            )
        if not isinstance(self.created_at, str) or not self.created_at.strip():
            raise ValueError("created_at 不能为空")
        if self.updated_at is not None and not isinstance(self.updated_at, str):
            raise ValueError("updated_at 必须是字符串")
        object.__setattr__(
            self, "status", coerce_status(self.status, RunStatus, field_name="运行状态")
        )
        object.__setattr__(self, "config", _coerce_file_reference(self.config, "config"))
        stages = _coerce_stages(self.stages)
        _validate_stage_contract(stages)
        object.__setattr__(self, "stages", stages)
        object.__setattr__(self, "inputs", _coerce_references(self.inputs, "inputs"))
        object.__setattr__(self, "outputs", _coerce_references(self.outputs, "outputs"))

    def stage(self, name: str) -> StageRecord:
        try:
            return self.stages[name]
        except KeyError as error:
            raise KeyError(f"manifest 缺少阶段: {name}") from error

    def with_stage(self, record: StageRecord, *, now: datetime | None = None) -> "RunManifest":
        """用新的阶段记录替换同名阶段，并推进更新时间。"""
        if not isinstance(record, StageRecord):
            raise ValueError("record 必须是 StageRecord")
        stages = dict(self.stages)
        stages[record.name] = record
        return replace(self, stages=stages, updated_at=utc_now_iso(now))

    def with_status(
        self, target, *, error: Any = _UNCHANGED, now: datetime | None = None
    ) -> "RunManifest":
        """按运行状态转换规则切换状态。"""
        target_status = coerce_status(target, RunStatus, field_name="运行状态")
        validate_transition(
            self.status,
            target_status,
            transitions=RUN_STATUS_TRANSITIONS,
            label="运行状态",
            enum_type=RunStatus,
        )
        changes: dict[str, Any] = {"status": target_status, "updated_at": utc_now_iso(now)}
        if error is not _UNCHANGED:
            changes["error"] = error
        return replace(self, **changes)

    def transition_stage(
        self,
        name: str,
        target,
        *,
        now: datetime | None = None,
        result: Any = _UNCHANGED,
        error: Any = _UNCHANGED,
        note: Any = _UNCHANGED,
    ) -> "RunManifest":
        """切换指定阶段的状态，并同步 manifest 更新时间。"""
        record = self.stage(name).transition_to(
            target, now=now, result=result, error=error, note=note
        )
        return self.with_stage(record, now=now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status.value,
            "software_version": self.software_version,
            "config": None if self.config is None else self.config.to_dict(),
            "stages": {name: record.to_dict() for name, record in self.stages.items()},
            "inputs": [item.to_dict() for item in self.inputs],
            "outputs": [item.to_dict() for item in self.outputs],
            "error": self.error,
            "notes": self.notes,
            "product_name": self.product_name,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunManifest":
        """从 JSON 结构还原 manifest，并拒绝非法状态、路径和 schema 版本。"""
        if not isinstance(value, Mapping):
            raise ValueError("manifest 必须是 JSON 对象")
        for required in ("run_id", "created_at"):
            if value.get(required) in (None, ""):
                raise ValueError(f"manifest 缺少必需字段: {required}")
        return cls(
            run_id=value["run_id"],
            created_at=value["created_at"],
            schema_version=value.get("schema_version", MANIFEST_SCHEMA_VERSION),
            updated_at=value.get("updated_at"),
            status=value.get("status", RunStatus.CREATED),
            software_version=value.get("software_version", "unknown"),
            config=value.get("config"),
            stages=value.get("stages", {}),
            inputs=value.get("inputs", ()),
            outputs=value.get("outputs", ()),
            error=value.get("error"),
            notes=value.get("notes"),
            product_name=value.get("product_name"),
        )


def default_manifest(
    run_id: str,
    *,
    created_at: str | None = None,
    now: datetime | None = None,
    software_version: str = "unknown",
    status: RunStatus = RunStatus.CREATED,
    config: FileReference | None = None,
) -> RunManifest:
    """创建一个包含全部阶段（均为 pending）的初始 manifest。"""
    return RunManifest(
        run_id=run_id,
        created_at=created_at if created_at is not None else utc_now_iso(now),
        status=status,
        software_version=software_version,
        config=config,
        stages={name: StageRecord(name=name) for name in DEFAULT_STAGE_NAMES},
    )


def _coerce_file_reference(value: Any, field_name: str) -> FileReference | None:
    if value is None:
        return None
    if isinstance(value, FileReference):
        return value
    if isinstance(value, Mapping):
        return FileReference.from_dict(value)
    raise ValueError(f"{field_name} 必须是文件引用或 null")


def _coerce_stages(value: Any) -> Mapping[str, StageRecord]:
    if not isinstance(value, Mapping):
        raise ValueError("stages 必须是映射")
    stages: dict[str, StageRecord] = {}
    for name, item in value.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("阶段名不能为空")
        if isinstance(item, StageRecord):
            record = item
        elif isinstance(item, Mapping):
            payload = dict(item)
            payload.setdefault("name", name)
            record = StageRecord.from_dict(payload)
        else:
            raise ValueError(f"阶段 {name} 必须是阶段记录")
        if record.name != name:
            raise ValueError(f"阶段键 {name} 与记录名 {record.name} 不一致")
        stages[name] = record
    return MappingProxyType(stages)


def _validate_stage_contract(stages: Mapping[str, StageRecord]) -> None:
    """要求标准阶段完整，并校验已完成阶段的固定结果路径。"""
    missing = [name for name in DEFAULT_STAGE_NAMES if name not in stages]
    if missing:
        raise ValueError(
            "manifest 必须包含标准阶段: "
            + ", ".join(DEFAULT_STAGE_NAMES)
            + "，缺少: "
            + ", ".join(missing)
        )
    for name, record in stages.items():
        expected = STAGE_RESULT_PATHS.get(name)
        if expected is None or record.status is not StageStatus.COMPLETED:
            continue
        if record.result != expected:
            raise ValueError(
                f"阶段 {name} 完成结果必须登记在 {expected}，实际: {record.result!r}"
            )


def _coerce_references(value: Any, field_name: str) -> tuple[FileReference, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} 必须是文件引用序列")
    references = []
    for item in value:
        reference = _coerce_file_reference(item, field_name)
        if reference is None:
            raise ValueError(f"{field_name} 不能包含 null")
        references.append(reference)
    return tuple(references)


__all__ = [
    "AMPLIFIER_RESULT_PATH",
    "CABLE_LOSS_INPUT_PATH",
    "CONFIG_FILENAME",
    "DEFAULT_STAGE_NAMES",
    "DRIVER_MAPPING_INPUT_PATH",
    "FileReference",
    "GENERATED_RUN_ID_PATTERN",
    "INPUTS_DIRNAME",
    "MANIFEST_FILENAME",
    "MANIFEST_SCHEMA_VERSION",
    "RESULTS_DIRNAME",
    "RUNS_DIRNAME",
    "RUN_ID_PATTERN",
    "RUN_ID_TIMESTAMP_FORMAT",
    "RUN_STATUS_TRANSITIONS",
    "RunManifest",
    "RunStatus",
    "SHA256_PATTERN",
    "STAGE_AMPLIFIER",
    "STAGE_CABLE_LOSS",
    "STAGE_DRIVER_MAPPING",
    "STAGE_RESULT_PATHS",
    "STAGE_STATUS_TRANSITIONS",
    "SUPPORTED_MANIFEST_SCHEMA_VERSIONS",
    "StageRecord",
    "StageStatus",
    "TERMINAL_RUN_STATUSES",
    "TERMINAL_STAGE_STATUSES",
    "coerce_status",
    "default_manifest",
    "default_stage_result_path",
    "generate_run_id",
    "is_relative_path",
    "normalize_relative_path",
    "utc_now_iso",
    "validate_generated_run_id",
    "validate_run_id",
    "validate_transition",
]
