"""三类正式测量结果的唯一应用层契约。

结果模型只描述正式结果：

- 线损按频点保存四条线缆分摊损耗；
- 驱动映射保存两层：原始“源功率 -> 补偿后输出”实测点用于审计，目标网格
  “目标 DUT 输入 -> 反查源功率 + 插值输出”用于正式测试；
- 主功放按频点保存压缩点、小信号增益和扫描数据。

结果不继承 ``Mapping``，也不保存服务层的原始载荷。``to_dict()`` 是唯一的结果
文档形状，``measurement_result_from_document()`` 是唯一读取入口：文档必须同时带
``measurement_type``、``status`` 和非空 ``run_id``，且只允许正式字段名。新结果还带
``measurement_id`` 作为结果自身身份（``run_id`` 只表示来源运行）；旧结果没有该字段
时仍可读取，``measurement_id`` 为 ``None``，再次序列化时同样省略该字段。字段缺失是
“旧结果”的唯一表示：显式写 ``null`` 视为写坏的文档并直接报错，避免兼容路径被当成
新结果的默认值。旧字段别名
（``legacy_payload``、``config``、``derived_metrics``、``resource_snapshot`` 等）、
行式 ``sweep_data``、测量类型与数据字段不匹配的文档一律 ``ValueError``，不存在按
字段猜测历史格式的兼容路径。

阶段结果文件名只由 :func:`measurement_result_filename` 从 ``measurement_id`` 构造，
并可用 :func:`validate_measurement_result_filename` 校验；新身份由
:func:`generate_measurement_id` 按 UTC 时间戳加随机短串生成，用户文本不参与路径构造。

展示、报告与导出只使用本模块的显式属性（``report_rows()``、``sweep_rows()``、
``saturation_points()``），因此不需要把结果当成 ``Mapping[str, Any]`` 读取。

本模块是**序列化边界**：字段是 JSON/Qt 可直接消费的基本类型，单位由字段名表达。
dBm↔W、%↔比值等换算和 dB 运算只允许发生在
:mod:`domain.measurement.values` / :mod:`domain.measurement.calculations`，
DTO 与展示层只搬运数值，不重新计算；唯一的例外是
:class:`CableLossRow` 的线损拓扑，它保存值对象并在 ``to_mapping()`` 时展开。
读取结果文档时频率键的文本原样保留（``"2.40"`` 不会被改写），但解析走
:meth:`domain.measurement.values.Frequency.from_document_key`。
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from domain.measurement.values import CableLossProfile, Frequency


# 驱动映射结果的双层契约版本：原始实测点与目标网格分别序列化。
# 旧 ``power_mapping``（输入 -> 输出）文档没有这个版本，不能作为目标网格读取。
DRIVER_MAPPING_SCHEMA_VERSION = "2.0"

# 测量身份用于阶段结果文件名和结果自身引用：只允许 ASCII 字母、数字、点、下划线
# 和短横线，不能以符号开头，也不能包含 ``..``、路径分隔符或盘符。读取旧结果时
# 字段可以缺失，但一旦出现就必须满足该约束，避免身份被当作路径片段使用。
MEASUREMENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
# 新流程生成的测量身份采用 ``<UTC 时间戳>_<随机短串>``；时间格式与运行 ID 保持一致。
# 随机短串默认取完整 ``uuid4`` 的 128 位十六进制，写入端仍须独占创建并拒绝覆盖，
# 不能把随机性当作唯一性约束。
MEASUREMENT_ID_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"
GENERATED_MEASUREMENT_ID_PATTERN = re.compile(r"^[0-9]{8}T[0-9]{6}Z_[0-9a-f]{4,32}$")
_MEASUREMENT_ID_TOKEN_PATTERN = re.compile(r"^[0-9a-f]{4,32}$")
# 正式结果在阶段目录中的唯一文件后缀；文件名只能由测量身份加该后缀构成。
MEASUREMENT_RESULT_SUFFIX = ".json"
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)


class MeasurementType(str, Enum):
    CABLE_LOSS = "cable_loss"
    DRIVER_POWER_MAPPING = "driver_power_mapping"
    AMPLIFIER = "amplifier"


class MeasurementStatus(str, Enum):
    """统一的测量生命周期状态。"""

    IDLE = "idle"
    PREPARING = "preparing"
    RUNNING = "running"
    WAITING_FOR_CONTINUE = "waiting_for_continue"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EMERGENCY_STOPPED = "emergency_stopped"

    @property
    def is_terminal(self) -> bool:
        return self in {
            MeasurementStatus.COMPLETED,
            MeasurementStatus.FAILED,
            MeasurementStatus.CANCELLED,
            MeasurementStatus.EMERGENCY_STOPPED,
        }


def freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(freeze(item) for item in value)
    return value


def thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw(item) for item in value]
    if isinstance(value, frozenset):
        return [thaw(item) for item in value]
    return value


def normalize_frequency_key(value: Any) -> str | None:
    """把频率键规范化为 ``float`` 的字符串形式；无效值返回 ``None``。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return str(float(value))
    except (TypeError, ValueError):
        return None


# 结果文件/阶段使用的结果类型标签与正式测量类型的一一对应关系。
# ``amplifier_measurement`` 是运行目录沿用至今的存储标签，语义等同 ``amplifier``。
_RESULT_TYPE_LABELS: Mapping[str, MeasurementType] = MappingProxyType(
    {
        "cable_loss": MeasurementType.CABLE_LOSS,
        "driver_power_mapping": MeasurementType.DRIVER_POWER_MAPPING,
        "amplifier": MeasurementType.AMPLIFIER,
        "amplifier_measurement": MeasurementType.AMPLIFIER,
    }
)


def measurement_type_for_result_type(value: Any) -> MeasurementType | None:
    """把结果类型标签收敛为正式测量类型；无法识别时返回 ``None``。"""
    if isinstance(value, MeasurementType):
        return value
    if not isinstance(value, str):
        return None
    return _RESULT_TYPE_LABELS.get(value.strip())


def validate_measurement_id(value: Any) -> str:
    """校验结果自身身份的格式；只做通用安全约束，不生成身份。

    ``measurement_id`` 会进入阶段结果文件名，因此拒绝空值、首尾空白、路径片段和
    盘符等可能改变路径含义的文本。新身份的生成规则见 :func:`generate_measurement_id`，
    文件名构造只允许经 :func:`measurement_result_filename`。
    """
    if not isinstance(value, str):
        raise ValueError("measurement_id 必须是字符串")
    if not value or value != value.strip():
        raise ValueError("measurement_id 不能为空或包含首尾空白")
    if ".." in value or not MEASUREMENT_ID_PATTERN.fullmatch(value):
        raise ValueError(
            "measurement_id 只能包含字母、数字、点、下划线或短横线，"
            "不能以符号开头，也不能包含路径片段"
        )
    return value


def generate_measurement_id(
    now: datetime | None = None, *, token: str | None = None
) -> str:
    """按 ``<UTC 时间戳>_<随机短串>`` 生成结果自身身份。

    身份只描述结果本身，与来源运行无关。``token`` 仅供测试注入；正式调用不传它，
    由完整的 ``uuid4`` 提供 128 位随机后缀，极大降低并发碰撞概率；持久化端仍须
    使用独占创建并在目标已存在时拒绝覆盖，不能把随机性当作唯一性约束。
    时间必须带时区；传入的本地时间会先换算为 UTC。
    """
    moment = now if now is not None else datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise ValueError("时间必须带时区信息")
    suffix = uuid4().hex if token is None else token
    if not isinstance(suffix, str) or not _MEASUREMENT_ID_TOKEN_PATTERN.fullmatch(suffix):
        raise ValueError("measurement_id 随机短串必须是 4-32 位小写十六进制")
    stamp = moment.astimezone(timezone.utc).strftime(MEASUREMENT_ID_TIMESTAMP_FORMAT)
    return validate_generated_measurement_id(f"{stamp}_{suffix}")


def validate_generated_measurement_id(value: Any) -> str:
    """校验生成型身份符合 ``<UTC 时间戳>_<随机短串>`` 且时间戳真实存在。"""
    if not isinstance(value, str) or not GENERATED_MEASUREMENT_ID_PATTERN.fullmatch(value):
        raise ValueError(
            "生成的 measurement_id 必须形如 <UTC 时间戳>_<4-32 位小写十六进制>，"
            "例如 20261111T010203Z_a1b2c3d4"
        )
    try:
        datetime.strptime(value[:16], MEASUREMENT_ID_TIMESTAMP_FORMAT)
    except ValueError as exc:
        raise ValueError("生成的 measurement_id 包含无效 UTC 时间戳") from exc
    return value


def measurement_result_filename(measurement_id: Any) -> str:
    """由测量身份构造正式结果文件名；文件名不接受身份之外的任何文本。

    身份会先经过 :func:`validate_measurement_id`，因此调用方无法把用户输入、路径
    片段或盘符拼进文件名。该函数只生成名字，是否已存在、写到哪里由仓储决定。
    """
    identity = validate_measurement_id(measurement_id)
    if identity.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        raise ValueError("measurement_id 不能是 Windows 保留设备名")
    return f"{identity}{MEASUREMENT_RESULT_SUFFIX}"


def validate_measurement_result_filename(filename: Any) -> str:
    """校验阶段结果文件名是 ``<measurement_id>.json`` 形式的纯文件名。

    拒绝空值、首尾空白、空字符、路径分隔符、盘符、Windows 保留设备名、
    ``.``/``..`` 片段和非 ``.json`` 后缀；通过校验后返回规范化文件名。只接受
    小写 ``.json``，避免大小写不敏感文件系统上出现两个名字指向同一文件。
    """
    if not isinstance(filename, str) or not filename:
        raise ValueError("测量结果文件名不能为空")
    if filename != filename.strip():
        raise ValueError("测量结果文件名不能包含首尾空白")
    if "\x00" in filename:
        raise ValueError("测量结果文件名不能包含空字符")
    if "/" in filename or "\\" in filename or ":" in filename:
        raise ValueError("测量结果文件名必须是纯文件名，不能包含路径或盘符")
    if filename in {".", ".."}:
        raise ValueError(f"测量结果文件名非法: {filename!r}")
    if not filename.endswith(MEASUREMENT_RESULT_SUFFIX):
        raise ValueError(
            f"测量结果文件名必须使用 {MEASUREMENT_RESULT_SUFFIX} 后缀: {filename!r}"
        )
    identity = filename[: -len(MEASUREMENT_RESULT_SUFFIX)]
    return measurement_result_filename(identity)


# -- 结果数据 ---------------------------------------------------------------


@dataclass(frozen=True)
class CableLossRow:
    """单个频点的线缆分摊损耗。

    线损拓扑由 :class:`domain.measurement.values.CableLossProfile` 表达；``cable1``
    等字符串字段名只存在于值对象里。构造时接受正式结果文档映射，并立即收敛为
    值对象，未知字段、非数值或非有限数都会被拒绝。
    """

    frequency_ghz: str
    cable_losses: CableLossProfile = field(default_factory=CableLossProfile)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "frequency_ghz",
            _frequency_key(self.frequency_ghz, "线损结果"),
        )
        losses = self.cable_losses
        if not isinstance(losses, CableLossProfile):
            losses = CableLossProfile.from_mapping(losses, source="cable_losses")
        object.__setattr__(self, "cable_losses", losses)


@dataclass(frozen=True)
class DriverMappingRawPoint:
    """驱动映射原始实测点：信号源输出功率 -> 驱放补偿后输出功率。

    原始点只描述实际测量结果，是映射审计依据；正式测试不使用它直接当作目标输入。
    ``gain_db`` 是补偿后输出与源功率之差。构造时可以省略（按两端功率计算），但正式
    结果文档必须显式保存该字段，读取时也会校验它与两端功率一致。
    """

    frequency_ghz: str
    source_power_dbm: float
    driver_output_power_dbm: float
    gain_db: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "frequency_ghz", _frequency_key(self.frequency_ghz, "驱动映射原始点")
        )
        source = _float(self.source_power_dbm, "source_power_dbm")
        output = _float(self.driver_output_power_dbm, "driver_output_power_dbm")
        expected_gain = output - source
        if self.gain_db is None:
            gain = expected_gain
        else:
            gain = _float(self.gain_db, "gain_db")
            if not math.isclose(gain, expected_gain, rel_tol=0.0, abs_tol=1e-6):
                raise ValueError(
                    "gain_db 必须等于补偿后输出功率减源功率："
                    f"{gain}dB != {output}dBm - {source}dBm"
                )
        object.__setattr__(self, "source_power_dbm", source)
        object.__setattr__(self, "driver_output_power_dbm", output)
        object.__setattr__(self, "gain_db", gain)


@dataclass(frozen=True)
class DriverMappingTargetPoint:
    """目标网格上的驱动映射点：目标 DUT 输入功率与正式测试采用的源/输出功率。

    目标点是正式测试消费的数据：``target_input_power_dbm`` 是横向目标，
    ``source_power_dbm`` 是反查得到的信号源设定，``driver_output_power_dbm`` 是
    插值曲线在该源功率处的驱放输出（即正式测试采用的 ``input_power_dut``）。
    """

    frequency_ghz: str
    target_input_power_dbm: float
    source_power_dbm: float
    driver_output_power_dbm: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "frequency_ghz", _frequency_key(self.frequency_ghz, "驱动映射目标点")
        )
        object.__setattr__(
            self,
            "target_input_power_dbm",
            _float(self.target_input_power_dbm, "target_input_power_dbm"),
        )
        object.__setattr__(
            self, "source_power_dbm", _float(self.source_power_dbm, "source_power_dbm")
        )
        object.__setattr__(
            self,
            "driver_output_power_dbm",
            _float(self.driver_output_power_dbm, "driver_output_power_dbm"),
        )


@dataclass(frozen=True)
class AmplifierCompressionPoint:
    """主功放的压缩点指标，字段缺失表示该指标未计算。

    所有字段都是 dB / dBm / % 的有限数值或 ``None``；构造时统一转换并拒绝非有限值。
    """

    input_power_dbm: float | None = None
    output_power_dbm: float | None = None
    gain_db: float | None = None
    efficiency_percent: float | None = None
    compression_db: float | None = None
    signal_generator_power_dbm: float | None = None

    def __post_init__(self) -> None:
        for name in (
            "input_power_dbm",
            "output_power_dbm",
            "gain_db",
            "efficiency_percent",
            "compression_db",
            "signal_generator_power_dbm",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _float(value, name))


@dataclass(frozen=True)
class AmplifierFrequencyResult:
    """单个频点的主功放结果：压缩点、小信号增益与列式扫描数据。"""

    frequency_ghz: str
    compression_type: str | None = None
    compression_point: AmplifierCompressionPoint | None = None
    small_signal_gain_db: float | None = None
    compression_achieved: bool | None = None
    sweep_data: Mapping[str, tuple[Any, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "frequency_ghz", _frequency_key(self.frequency_ghz, "主功放结果")
        )
        if self.compression_point is not None and not isinstance(
            self.compression_point, AmplifierCompressionPoint
        ):
            raise ValueError("compression_point 必须是 AmplifierCompressionPoint")
        if self.small_signal_gain_db is not None:
            object.__setattr__(
                self,
                "small_signal_gain_db",
                _float(self.small_signal_gain_db, "small_signal_gain_db"),
            )
        if self.compression_achieved is not None and not isinstance(
            self.compression_achieved, bool
        ):
            raise ValueError("compression_achieved 必须是布尔值")
        object.__setattr__(self, "sweep_data", _columns(self.sweep_data, "sweep_data"))

    def sweep_rows(self) -> tuple[dict[str, Any], ...]:
        """把列式扫描数据展开为逐点行，供绘图和报告使用。"""
        columns = self.sweep_data
        count = len(next(iter(columns.values()))) if columns else 0
        rows = []
        for index in range(count):
            row: dict[str, Any] = {}
            for name, values in columns.items():
                value = values[index]
                if name == "voltages" and isinstance(value, Mapping):
                    row.update({f"V_{key}": item for key, item in value.items()})
                elif name == "currents" and isinstance(value, Mapping):
                    row.update({f"I_{key}": item for key, item in value.items()})
                else:
                    row[name] = value
            rows.append(row)
        return tuple(rows)


# -- 结果契约 ---------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class MeasurementResult:
    """跨三类测量共享的正式结果契约；子类补充测量类型特有字段。"""

    measurement_type: MeasurementType | str
    status: MeasurementStatus | str = MeasurementStatus.COMPLETED
    run_id: str
    measurement_id: str | None = None
    error: str | None = None
    errors: tuple[str, ...] = ()
    cleanup_records: tuple[Mapping[str, Any], ...] = ()
    termination: Mapping[str, Any] | None = None
    configuration_snapshot: Mapping[str, Any] = field(default_factory=dict)
    device_summary: Mapping[str, Any] = field(default_factory=dict)
    raw_data_reference: Mapping[str, Any] = field(default_factory=dict)
    calculation_summary: Mapping[str, Any] = field(default_factory=dict)
    result_reference: Mapping[str, Any] = field(default_factory=dict)
    created_at: str | None = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    measurement_time: str | None = None
    source_filename: str | None = None

    def __post_init__(self) -> None:
        measurement_type = _enum_value(self.measurement_type, MeasurementType, "measurement_type")
        status = _enum_value(self.status, MeasurementStatus, "status")
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ValueError("run_id 不能为空")
        object.__setattr__(self, "measurement_type", measurement_type)
        object.__setattr__(self, "status", status)
        if self.measurement_id is not None:
            object.__setattr__(
                self, "measurement_id", validate_measurement_id(self.measurement_id)
            )
        for name in (
            "configuration_snapshot",
            "device_summary",
            "raw_data_reference",
            "calculation_summary",
            "result_reference",
        ):
            object.__setattr__(self, name, freeze(getattr(self, name)))
        object.__setattr__(self, "errors", _errors(getattr(self, "errors")))
        object.__setattr__(self, "cleanup_records", _records(getattr(self, "cleanup_records")))
        object.__setattr__(self, "termination", self._frozen_termination())
        if status is MeasurementStatus.COMPLETED and self.termination is not None:
            raise ValueError("completed 结果不能包含 termination")
        if status is MeasurementStatus.EMERGENCY_STOPPED:
            _validate_termination(self.termination, "emergency_stop")
        if status is MeasurementStatus.CANCELLED:
            _validate_termination(self.termination, None)
        if status is MeasurementStatus.FAILED and not self.error:
            raise ValueError("failed 结果必须包含 error")

    def _frozen_termination(self) -> Mapping[str, Any] | None:
        if self.termination is None:
            return None
        return freeze(self.termination)

    def result_document(self) -> dict[str, Any]:
        """返回测量类型特有的正式结果字段；基类结果没有特有字段。"""
        return {}

    def to_dict(self) -> dict[str, Any]:
        """返回运行目录中唯一的结果文档形状。

        旧结果没有自身身份时**省略** ``measurement_id``，而不是写 ``null``：读取端
        只把“字段缺失”当作旧结果，写出的 ``null`` 必须被拒绝，否则往返一次就会产生
        无法再读取的文档。
        """
        document = dict(self.result_document())
        document.update(
            {
                "measurement_type": self.measurement_type.value,
                "status": self.status.value,
                "run_id": self.run_id,
            }
        )
        if self.measurement_id is not None:
            document["measurement_id"] = self.measurement_id
        document.update(
            {
                "error": self.error,
                "termination": thaw(self.termination) if self.termination is not None else None,
                "configuration_snapshot": thaw(self.configuration_snapshot),
                "device_summary": thaw(self.device_summary),
                "raw_data_reference": thaw(self.raw_data_reference),
                "calculation_summary": thaw(self.calculation_summary),
                "created_at": self.created_at,
                "errors": list(self.errors),
                "cleanup_records": thaw(self.cleanup_records),
                "result_reference": thaw(self.result_reference),
                "measurement_time": self.measurement_time,
                "source_filename": self.source_filename,
            }
        )
        return document

    def report_rows(self) -> tuple[dict[str, Any], ...]:
        """报告与 CSV 使用的逐行数据；基类结果没有结果行。"""
        return ()

    def sweep_rows(self) -> Mapping[str, tuple[dict[str, Any], ...]]:
        """按频点分组的扫描行；没有扫描数据时为空。"""
        return {}

    def saturation_points(self) -> tuple[dict[str, Any], ...]:
        """压缩点/饱和点摘要；没有压缩点结果时为空。"""
        return ()

    @property
    def point_count(self) -> int:
        """结果行数；报告用它判断结果是否可绘制。"""
        return len(self.report_rows())

    @property
    def measurement_type_value(self) -> str:
        """测量类型的正式字符串值，供报告与导出使用。"""
        return self.measurement_type.value

    @property
    def status_value(self) -> str:
        """结果状态的正式字符串值，供报告与导出使用。"""
        return self.status.value

    @property
    def is_amplifier(self) -> bool:
        """是否为可生成功放报告的主功放结果。"""
        return self.measurement_type is MeasurementType.AMPLIFIER

    def with_saved_result(self, *, archive_path: str | None = None) -> "MeasurementResult":
        """登记本次结果的保存路径，供上层展示与定位。

        步骤 7 起线损、步骤 8 起驱动映射、步骤 20 起装配阶段产物仓库的主功放结果，
        权威文件都位于阶段结果目录（``test_results/<stage>/<measurement_id>.json``）；
        未装配阶段产物仓库的旧兼容流程仍可能把主功放结果保存在运行目录。字段名
        ``archive_path`` 为兼容既有调用方与已保存文档而保留，值始终是“这份结果实际
        落盘的位置”，不暗示它必须在运行目录内。
        """
        references = dict(self.result_reference)
        if archive_path is not None:
            references["archive_path"] = archive_path
        return replace(self, result_reference=references)

    @property
    def is_formal_result(self) -> bool:
        """是否为三个具体正式结果之一；共享基类本身不参与持久化与展示。"""
        return False

    @property
    def has_measurement_id(self) -> bool:
        """是否带有结果自身身份；旧运行结果没有该字段，仅作只读兼容。"""
        return self.measurement_id is not None


@dataclass(frozen=True, kw_only=True)
class CableLossMeasurementResult(MeasurementResult):
    """线损测量结果：每个频点的四段线缆损耗与衰减器取值。"""

    measurement_type: MeasurementType | str = MeasurementType.CABLE_LOSS
    attenuator_value: float | None = None
    points: tuple[CableLossRow, ...] = ()

    @property
    def is_formal_result(self) -> bool:
        return True

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.measurement_type is not MeasurementType.CABLE_LOSS:
            raise ValueError("CableLossMeasurementResult 的 measurement_type 必须为 cable_loss")
        if self.attenuator_value is not None:
            object.__setattr__(self, "attenuator_value", _float(self.attenuator_value, "attenuator_value"))
        object.__setattr__(self, "points", _rows(self.points, CableLossRow, "points"))

    @classmethod
    def from_service_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        run_id: str,
        status: MeasurementStatus | str | None = None,
        termination: Mapping[str, Any] | None = None,
        measurement_id: str | None = None,
    ) -> "CableLossMeasurementResult":
        """把线损服务载荷转换为正式结果（服务载荷仍是本层唯一入口）。"""
        if not isinstance(payload, Mapping):
            raise ValueError("线损测量结果载荷必须是映射")
        return cls(
            run_id=run_id,
            measurement_id=measurement_id,
            status=_service_status(payload.get("status")) if status is None else status,
            termination=termination,
            attenuator_value=_float_or_none(payload.get("attenuator_value")),
            points=_cable_loss_rows(payload.get("cable_losses")),
        )

    @classmethod
    def from_document(
        cls, document: Mapping[str, Any], *, run_id: str
    ) -> "CableLossMeasurementResult":
        """读取正式线损结果文档；缺少 ``cable_losses`` 时直接报错。"""
        if "cable_losses" not in document:
            raise ValueError("线损结果文档缺少 cable_losses 字段")
        return cls(
            run_id=run_id,
            status=_document_status(document),
            attenuator_value=_float_or_none(document.get("attenuator_value")),
            points=_cable_loss_rows(document.get("cable_losses")),
            created_at=_created_at(document),
            **_contract_fields(document),
        )

    def cable_losses(self) -> dict[str, Mapping[str, float]]:
        """按频点分组的线缆损耗表（结果文档形状），供报告与既有调用方读取。"""
        return {
            row.frequency_ghz: row.cable_losses.to_mapping() for row in self.points
        }

    def result_document(self) -> dict[str, Any]:
        return {
            "attenuator_value": self.attenuator_value,
            "cable_losses": {
                row.frequency_ghz: row.cable_losses.to_mapping() for row in self.points
            },
        }

    def report_rows(self) -> tuple[dict[str, Any], ...]:
        rows = []
        for row in self.points:
            entry: dict[str, Any] = {"frequency_ghz": row.frequency_ghz}
            entry.update(row.cable_losses.to_mapping())
            rows.append(entry)
        return tuple(rows)


@dataclass(frozen=True, kw_only=True)
class DriverPowerMappingMeasurementResult(MeasurementResult):
    """驱动映射结果：原始实测点与目标网格映射的双层契约。

    - ``raw_points`` 保存实际扫描到的“源功率 -> 补偿后驱放输出”，是审计依据；
    - ``target_points`` 保存正式测试目标输入功率、反查源功率和插值输出功率；
      映射阶段尚未生成目标表时允许为空，但字段本身必须在结果文档中存在。

    两类点不能互相替代：旧 ``power_mapping``（输入 -> 输出）字典不是目标网格，
    读取正式文档时会明确拒绝。
    """

    measurement_type: MeasurementType | str = MeasurementType.DRIVER_POWER_MAPPING
    raw_points: tuple[DriverMappingRawPoint, ...] = ()
    target_points: tuple[DriverMappingTargetPoint, ...] = ()

    @property
    def is_formal_result(self) -> bool:
        return True

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.measurement_type is not MeasurementType.DRIVER_POWER_MAPPING:
            raise ValueError(
                "DriverPowerMappingMeasurementResult 的 measurement_type 必须为 driver_power_mapping"
            )
        raw_points = _rows(self.raw_points, DriverMappingRawPoint, "raw_points")
        target_points = _rows(
            self.target_points, DriverMappingTargetPoint, "target_points"
        )
        _validate_unique_driver_values(
            raw_points, "source_power_dbm", "驱动映射原始点的源功率"
        )
        _validate_unique_driver_values(
            target_points, "target_input_power_dbm", "驱动映射目标点"
        )
        object.__setattr__(self, "raw_points", raw_points)
        object.__setattr__(self, "target_points", target_points)

    def to_dict(self) -> dict[str, Any]:
        """返回正式文档，并显式标明双层映射契约的 schema 版本。"""
        document = super().to_dict()
        document["schema_version"] = DRIVER_MAPPING_SCHEMA_VERSION
        return document

    @classmethod
    def from_service_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        run_id: str,
        status: MeasurementStatus | str | None = None,
        termination: Mapping[str, Any] | None = None,
        measurement_id: str | None = None,
    ) -> "DriverPowerMappingMeasurementResult":
        if not isinstance(payload, Mapping):
            raise ValueError("驱动映射结果载荷必须是映射")
        raw_value = payload.get("raw_points")
        if raw_value is not None and "power_mapping" in payload:
            raise ValueError(
                "驱动映射服务载荷不能同时包含 raw_points 与旧 power_mapping"
            )
        if raw_value is not None:
            raw_points = _raw_mapping_points(raw_value, require_gain=False)
        elif "power_mapping" in payload:
            # 服务层的 ``power_mapping`` 是原始实测表（源功率 -> 补偿后输出）。
            # 这个适配入口只服务尚未迁移的驱动扫描服务，转换结果仍是审计点，
            # 不会成为目标输入表；正式文档读取路径不接受该字段。
            raw_points = _raw_points_from_power_mapping(payload["power_mapping"])
        else:
            raise ValueError(
                "驱动映射服务载荷缺少原始映射点（raw_points 或 power_mapping）"
            )
        return cls(
            run_id=run_id,
            measurement_id=measurement_id,
            status=_service_status(payload.get("status")) if status is None else status,
            termination=termination,
            raw_points=raw_points,
            target_points=_target_mapping_points(payload.get("target_mapping")),
        )

    @classmethod
    def from_document(
        cls, document: Mapping[str, Any], *, run_id: str
    ) -> "DriverPowerMappingMeasurementResult":
        _reject_legacy_power_mapping(document)
        _require_driver_mapping_schema(document)
        if "raw_points" not in document:
            raise ValueError("驱动映射结果文档缺少 raw_points 字段")
        if "target_mapping" not in document:
            raise ValueError("驱动映射结果文档缺少 target_mapping 字段")
        return cls(
            run_id=run_id,
            status=_document_status(document),
            raw_points=_raw_mapping_points(document.get("raw_points"), require_gain=True),
            target_points=_target_mapping_points(document.get("target_mapping")),
            created_at=_created_at(document),
            **_contract_fields(document),
        )

    def raw_mapping(self) -> dict[str, dict[str, float]]:
        """按频点分组的“源功率 -> 补偿后输出”审计视图。

        这不是目标输入功率表；正式测试必须使用 :attr:`target_points`。
        """
        mapping: dict[str, dict[str, float]] = {}
        for point in self.raw_points:
            mapping.setdefault(point.frequency_ghz, {})[
                str(point.source_power_dbm)
            ] = point.driver_output_power_dbm
        return mapping

    def result_document(self) -> dict[str, Any]:
        raw_points: dict[str, list[dict[str, float]]] = {}
        for point in self.raw_points:
            raw_points.setdefault(point.frequency_ghz, []).append(
                {
                    "source_power_dbm": point.source_power_dbm,
                    "driver_output_power_dbm": point.driver_output_power_dbm,
                    "gain_db": point.gain_db,
                }
            )
        target_mapping: dict[str, list[dict[str, float]]] = {}
        for point in self.target_points:
            target_mapping.setdefault(point.frequency_ghz, []).append(
                {
                    "target_input_power_dbm": point.target_input_power_dbm,
                    "source_power_dbm": point.source_power_dbm,
                    "driver_output_power_dbm": point.driver_output_power_dbm,
                }
            )
        return {
            "raw_points": raw_points,
            "target_mapping": target_mapping,
        }

    def report_rows(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                "frequency_ghz": point.frequency_ghz,
                "source_power_dbm": point.source_power_dbm,
                "driver_output_power_dbm": point.driver_output_power_dbm,
                "gain_db": point.gain_db,
            }
            for point in self.raw_points
        )

    def sweep_rows(self) -> Mapping[str, tuple[dict[str, Any], ...]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for point in self.raw_points:
            grouped.setdefault(point.frequency_ghz, []).append(
                {
                    "input_power_sg": point.source_power_dbm,
                    "output_power_driver": point.driver_output_power_dbm,
                    "gain": point.gain_db,
                }
            )
        return {frequency: tuple(rows) for frequency, rows in grouped.items()}


@dataclass(frozen=True)
class DriverMappingInput:
    """驱动映射阶段交给正式测试的**已校验**输入。

    - :attr:`target_points` 是正式测试唯一消费的数据：目标 DUT 输入功率、反查源功率
      和插值驱放输出；它不能为空，否则映射还没有生成可执行的目标网格。
    - :attr:`raw_points` 是原始实测审计数据。``raw_mapping()`` 只为仍在迁移中的
      主功放扫描实现保留，不得把它的键当作目标输入功率；目标网格的正式消费切换
      属于重构步骤 9。

    本对象只在**输入读取边界**构造：正式文档已经通过
    :func:`measurement_result_from_document` 的 schema、版本和字段校验，这里再补上
    “目标表可用”与“原始曲线覆盖目标源功率”的交叉校验。
    """

    raw_points: tuple[DriverMappingRawPoint, ...]
    target_points: tuple[DriverMappingTargetPoint, ...]

    def __post_init__(self) -> None:
        raw_points = _rows(self.raw_points, DriverMappingRawPoint, "raw_points")
        target_points = _rows(
            self.target_points, DriverMappingTargetPoint, "target_points"
        )
        if not target_points:
            raise ValueError(
                "驱动映射输入缺少目标网格映射（target_mapping 为空），"
                "不能用于正式测试；请先完成驱动映射并生成目标表"
            )
        _validate_unique_driver_values(
            raw_points, "source_power_dbm", "驱动映射原始点的源功率"
        )
        _validate_unique_driver_values(
            target_points, "target_input_power_dbm", "驱动映射目标点"
        )
        _validate_target_grid_coverage(raw_points, target_points)
        object.__setattr__(self, "raw_points", raw_points)
        object.__setattr__(self, "target_points", target_points)

    @classmethod
    def from_result(
        cls, result: DriverPowerMappingMeasurementResult
    ) -> "DriverMappingInput":
        """从已校验的双层结果构造正式输入；其它测量类型直接拒绝。"""
        if not isinstance(result, DriverPowerMappingMeasurementResult):
            raise ValueError("驱动映射输入必须来自 driver_power_mapping 结果")
        return cls(
            raw_points=result.raw_points,
            target_points=result.target_points,
        )

    def target_mapping(self) -> dict[str, dict[float, tuple[float, float]]]:
        """按频点组织的目标网格视图：目标输入 -> (源功率, 插值驱放输出)。"""
        mapping: dict[str, dict[float, tuple[float, float]]] = {}
        for point in self.target_points:
            mapping.setdefault(point.frequency_ghz, {})[
                point.target_input_power_dbm
            ] = (point.source_power_dbm, point.driver_output_power_dbm)
        return mapping

    def raw_mapping(self) -> dict[str, dict[str, float]]:
        """按频点组织的原始实测审计视图：源功率 -> 补偿后驱放输出。

        只用于兼容尚未切换的主功放扫描实现（重构步骤 9 会改为消费
        :meth:`target_mapping`）；它不是目标输入功率表。
        """
        mapping: dict[str, dict[str, float]] = {}
        for point in self.raw_points:
            mapping.setdefault(point.frequency_ghz, {})[
                str(point.source_power_dbm)
            ] = point.driver_output_power_dbm
        return mapping


@dataclass(frozen=True, kw_only=True)
class AmplifierMeasurementResult(MeasurementResult):
    """主功放结果：每个频点的压缩点、小信号增益与扫描数据。"""

    measurement_type: MeasurementType | str = MeasurementType.AMPLIFIER
    frequencies: tuple[AmplifierFrequencyResult, ...] = ()

    @property
    def is_formal_result(self) -> bool:
        return True

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.measurement_type is not MeasurementType.AMPLIFIER:
            raise ValueError("AmplifierMeasurementResult 的 measurement_type 必须为 amplifier")
        object.__setattr__(
            self, "frequencies", _rows(self.frequencies, AmplifierFrequencyResult, "frequencies")
        )

    @classmethod
    def from_service_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        run_id: str,
        status: MeasurementStatus | str | None = None,
        termination: Mapping[str, Any] | None = None,
        measurement_id: str | None = None,
    ) -> "AmplifierMeasurementResult":
        if not isinstance(payload, Mapping):
            raise ValueError("主功放结果载荷必须是映射")
        return cls(
            run_id=run_id,
            measurement_id=measurement_id,
            status=_service_status(payload.get("status")) if status is None else status,
            termination=termination,
            frequencies=_amplifier_frequencies_from_service(payload.get("results")),
            configuration_snapshot=payload.get("configuration_snapshot", {}),
        )

    @classmethod
    def from_document(
        cls, document: Mapping[str, Any], *, run_id: str
    ) -> "AmplifierMeasurementResult":
        if "results" not in document:
            raise ValueError("主功放结果文档缺少 results 字段")
        return cls(
            run_id=run_id,
            status=_document_status(document),
            frequencies=_amplifier_frequencies_from_document(document.get("results")),
            created_at=_created_at(document),
            **_contract_fields(document),
        )

    def result_document(self) -> dict[str, Any]:
        return {
            "results": {
                item.frequency_ghz: _amplifier_document(item) for item in self.frequencies
            }
        }

    def report_rows(self) -> tuple[dict[str, Any], ...]:
        rows = []
        for item in self.frequencies:
            for sweep_row in item.sweep_rows():
                entry: dict[str, Any] = {"frequency_ghz": item.frequency_ghz}
                entry.update(sweep_row)
                rows.append(entry)
        return tuple(rows)

    def sweep_rows(self) -> Mapping[str, tuple[dict[str, Any], ...]]:
        return {
            item.frequency_ghz: item.sweep_rows()
            for item in self.frequencies
            if item.sweep_data
        }

    def saturation_points(self) -> tuple[dict[str, Any], ...]:
        points = []
        for item in self.frequencies:
            point = item.compression_point
            if point is None:
                continue
            points.append(
                {
                    "frequency": item.frequency_ghz,
                    "output_power": point.output_power_dbm,
                    "input_power": point.input_power_dbm,
                    "gain": point.gain_db,
                    "efficiency": point.efficiency_percent,
                    "sg_power": point.signal_generator_power_dbm,
                    "compression": point.compression_db,
                    "compression_achieved": item.compression_achieved,
                }
            )
        return tuple(points)


def measurement_result_from_document(document: Mapping[str, Any]) -> MeasurementResult:
    """把运行目录中的正式结果文档转换为显式结果 DTO。

    只接受 ``to_dict()`` 产生的正式 schema：

    - ``run_id``、``measurement_type``、``status`` 必须存在且有效；
    - ``measurement_id`` 是结果自身身份；旧结果只在字段缺失时按只读兼容读为
      ``None``，显式 ``null`` 或非法文本一律报错。新结果必须有身份，由写入边界
      调用 :func:`require_measurement_identity` 强制；
    - 测量类型必须与结果数据字段一一对应（``cable_losses`` / ``raw_points`` +
      ``target_mapping`` / ``results``），类型与数据不一致视为非正式文档；
    - 驱动映射只接受双层契约：旧 ``power_mapping`` 字典不能静默解释为目标网格；
    - 旧字段别名、``legacy_payload``、未知字段一律 ``ValueError``；
    - 写入层补充的 ``result_type`` / ``schema_version`` / ``saved_at`` 必须自洽，
      与 ``measurement_type`` 冲突时同样报错。

    不做按字段猜测历史格式的兼容推断。
    """
    if not isinstance(document, Mapping):
        raise ValueError("测量结果文档必须是映射对象")
    canonical = document.get("canonical_model")
    if canonical is not None:
        artifact_extras = set(document) - _CANONICAL_ARTIFACT_KEYS
        if artifact_extras:
            raise ValueError(
                "版本化模型工件包含未知字段: " + ", ".join(sorted(artifact_extras))
            )
        result = measurement_result_from_document(canonical)
        artifact_run_id = document.get("run_id")
        if artifact_run_id is not None and artifact_run_id != result.run_id:
            raise ValueError("版本化模型工件的 run_id 与 canonical_model 不一致")
        artifact_type = document.get("result_type")
        if artifact_type is not None and artifact_type != _CANONICAL_ARTIFACT_TYPE:
            raise ValueError(
                "版本化模型工件的 result_type 必须为 "
                f"{_CANONICAL_ARTIFACT_TYPE}: {artifact_type!r}"
            )
        return result
    run_id = document.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("正式测量结果必须包含非空 run_id")
    declared = document.get("measurement_type")
    if not isinstance(declared, str) or not declared.strip():
        raise ValueError("正式测量结果必须包含 measurement_type")
    try:
        kind = MeasurementType(declared)
    except ValueError as error:
        raise ValueError(f"无法识别的正式测量结果 measurement_type: {declared!r}") from error
    legacy = sorted(_LEGACY_DOCUMENT_KEYS & set(document))
    if legacy:
        raise ValueError(f"不是正式结果 schema：包含旧字段 {', '.join(legacy)}")
    if kind is MeasurementType.DRIVER_POWER_MAPPING:
        _reject_legacy_power_mapping(document)
    data_fields = _TYPE_ALLOWED_FIELDS[kind]
    others = sorted(
        {
            field
            for other, fields in _TYPE_ALLOWED_FIELDS.items()
            if other is not kind
            for field in fields
        }
        & set(document)
    )
    if others:
        raise ValueError(
            f"{kind.value} 结果不能包含其它测量类型字段: {', '.join(others)}"
        )
    missing = sorted(field for field in _TYPE_REQUIRED_FIELDS[kind] if field not in document)
    if missing:
        raise ValueError(f"{kind.value} 结果缺少正式字段: {', '.join(missing)}")
    allowed = _COMMON_DOCUMENT_KEYS | data_fields | _ARTIFACT_METADATA_KEYS
    unknown = sorted(set(document) - allowed)
    if unknown:
        raise ValueError(f"不是正式结果 schema：包含未知字段 {', '.join(unknown)}")
    _validate_artifact_metadata(document, kind)
    if kind is MeasurementType.CABLE_LOSS:
        return CableLossMeasurementResult.from_document(document, run_id=run_id)
    if kind is MeasurementType.DRIVER_POWER_MAPPING:
        return DriverPowerMappingMeasurementResult.from_document(document, run_id=run_id)
    return AmplifierMeasurementResult.from_document(document, run_id=run_id)


def require_formal_result(value: Any) -> MeasurementResult:
    """断言值是三个具体正式结果之一。

    共享基类 ``MeasurementResult`` 只是契约载体：它不描述任何真实测量，也不允许
    被持久化或展示。映射、字符串等原始文档必须先经过
    :func:`measurement_result_from_document`。
    """
    if isinstance(value, MeasurementResult) and value.is_formal_result:
        return value
    raise ValueError(
        "必须是正式测量结果 DTO（CableLoss/DriverPowerMapping/Amplifier）；"
        "原始文档请先调用 measurement_result_from_document()"
    )


def require_measurement_identity(value: Any) -> str:
    """断言正式结果带有自身身份和创建时间，返回其 ``measurement_id``。

    读取旧结果时必须允许 ``measurement_id`` 缺失，但写入阶段目录的**新**结果不能
    复用这条兼容路径，否则“旧文档”和“写坏的新文档”无法区分。因此“新结果必须有
    身份和保存时间”由写入边界调用本函数显式校验，而不是塞进 ``to_dict()``：后者
    仍需要能把旧结果原样往返，且不给现有测量流程增加构造约束。

    校验失败一律 ``ValueError``，与其它正式结果契约错误保持一致。
    """
    result = require_formal_result(value)
    if result.measurement_id is None:
        raise ValueError(
            "新测量结果必须包含 measurement_id；旧结果只在读取时缺省，"
            "不能作为新结果保存"
        )
    if not isinstance(result.created_at, str) or not result.created_at.strip():
        raise ValueError("新测量结果必须包含保存时间 created_at")
    return result.measurement_id


# -- 内部工具 ---------------------------------------------------------------


_COMMON_DOCUMENT_KEYS = frozenset(
    {
        "measurement_type",
        "status",
        "run_id",
        "measurement_id",
        "error",
        "errors",
        "cleanup_records",
        "termination",
        "configuration_snapshot",
        "device_summary",
        "raw_data_reference",
        "calculation_summary",
        "result_reference",
        "created_at",
        "measurement_time",
        "source_filename",
    }
)
_TYPE_REQUIRED_FIELDS: Mapping[MeasurementType, frozenset[str]] = MappingProxyType(
    {
        MeasurementType.CABLE_LOSS: frozenset({"cable_losses"}),
        MeasurementType.DRIVER_POWER_MAPPING: frozenset(
            {"raw_points", "target_mapping"}
        ),
        MeasurementType.AMPLIFIER: frozenset({"results"}),
    }
)
_TYPE_ALLOWED_FIELDS: Mapping[MeasurementType, frozenset[str]] = MappingProxyType(
    {
        MeasurementType.CABLE_LOSS: frozenset({"attenuator_value", "cable_losses"}),
        MeasurementType.DRIVER_POWER_MAPPING: frozenset(
            {"raw_points", "target_mapping"}
        ),
        MeasurementType.AMPLIFIER: frozenset({"results"}),
    }
)
# 结果文件由写入层补充的工件元数据；它们是正式字段，不是历史别名。
_ARTIFACT_METADATA_KEYS = frozenset({"schema_version", "result_type", "saved_at"})
# 正式结果文档支持的 schema 版本：驱动映射的双层契约单独使用 2.0，
# 其它结果仍为 1.0，避免把旧 power_mapping 当目标网格读取。
_SUPPORTED_RESULT_SCHEMA_VERSIONS: Mapping[MeasurementType, frozenset[str]] = (
    MappingProxyType(
        {
            MeasurementType.CABLE_LOSS: frozenset({"1.0"}),
            MeasurementType.DRIVER_POWER_MAPPING: frozenset(
                {DRIVER_MAPPING_SCHEMA_VERSION}
            ),
            MeasurementType.AMPLIFIER: frozenset({"1.0"}),
        }
    )
)
# canonical model 工件（``*_model.json``）的外层结构。
_CANONICAL_ARTIFACT_TYPE = "versioned_measurement_result"
_CANONICAL_ARTIFACT_KEYS = frozenset(
    {"canonical_model", "run_id", "result_type", "schema_version", "saved_at"}
)
# 步骤 6 已退役的旧文档字段；出现即视为非正式 schema。
_LEGACY_DOCUMENT_KEYS = frozenset(
    {
        "legacy_payload",
        "payload",
        "summary",
        "data_reference",
        "config",
        "derived_metrics",
        "resource_snapshot",
        "plan_snapshot",
        "raw_readings",
        "metadata",
        "original_filename",
    }
)


def _frequency_key(value: Any, label: str) -> str:
    """校验频点标识：必须是能解析为正频率的字符串；保留原始文本形式。

    读取已有结果时保留文件里的键文本（``"2.40"`` 不会在读取时被悄悄改写成
    ``"2.4"``），解析本身走唯一的频率边界 ``Frequency.from_document_key``：
    无单位按 schema 约定取 GHz，带单位按声明单位，非字符串或非法数值一律拒绝。
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}的频点标识不能为空")
    frequency = Frequency.from_document_key(value)
    if frequency.ghz <= 0:
        raise ValueError(f"{label}的频点标识非法: {value!r}")
    return value


def _document_status(document: Mapping[str, Any]) -> MeasurementStatus:
    value = document.get("status")
    if value is None:
        raise ValueError("正式测量结果文档缺少 status 字段")
    return MeasurementStatus(str(value))


def _measurement_id(document: Mapping[str, Any]) -> str | None:
    """读取可选的测量身份：只有字段缺失才表示旧结果。

    ``document.get()`` 会把“缺字段”和“显式 ``null``”混为一谈；后者只可能来自写坏的
    新文档，必须明确失败，否则兼容路径会成为新结果跳过身份的漏洞。
    """
    if "measurement_id" not in document:
        return None
    value = document["measurement_id"]
    if value is None:
        raise ValueError(
            "measurement_id 不能为 null：旧结果应省略该字段，新结果必须提供身份"
        )
    return validate_measurement_id(value)


def _validate_artifact_metadata(document: Mapping[str, Any], kind: MeasurementType) -> None:
    """校验写入层补充的工件元数据与测量类型自洽。

    这些键由 ``save_json_result`` 追加，属于正式字段，因此必须与 ``measurement_type``
    一致；冲突的标签说明文件被错误分类，不能当作正式结果读取。
    """
    label = document.get("result_type")
    if label is not None:
        resolved = measurement_type_for_result_type(label)
        if resolved is None:
            raise ValueError(
                f"不是正式结果 schema：无法识别的 result_type {label!r}"
            )
        if resolved is not kind:
            raise ValueError(
                f"result_type 与 measurement_type 不一致: {label!r} != {kind.value!r}"
            )
    if kind is MeasurementType.DRIVER_POWER_MAPPING:
        # 双层映射契约必须显式带版本；缺失版本就是旧的 power_mapping 文档。
        _require_driver_mapping_schema(document)
    elif "schema_version" in document:
        version = document.get("schema_version")
        text = str(version).strip() if version is not None else ""
        if text not in _SUPPORTED_RESULT_SCHEMA_VERSIONS[kind]:
            raise ValueError(f"不支持的测量结果 schema_version: {version!r}")
    if "saved_at" in document:
        saved_at = document.get("saved_at")
        if not isinstance(saved_at, str) or not saved_at.strip():
            raise ValueError("saved_at 必须是非空字符串")


def _reject_legacy_power_mapping(document: Mapping[str, Any]) -> None:
    """拒绝旧的“输入 -> 输出”字典，避免被误读成目标输入 -> 源功率。"""
    if "power_mapping" in document:
        raise ValueError(
            "旧驱动映射格式 power_mapping 不包含目标输入功率网格，"
            "不能作为新的 target_mapping（目标输入 -> 源功率）读取；"
            "请重新执行驱动映射并保存双层映射契约（schema_version "
            f"{DRIVER_MAPPING_SCHEMA_VERSION}）"
        )


def _require_driver_mapping_schema(document: Mapping[str, Any]) -> None:
    """校验驱动映射文档的 schema 版本；缺失版本视为旧格式。"""
    version = document.get("schema_version")
    if version is None:
        raise ValueError(
            "驱动映射结果缺少 schema_version；旧 power_mapping 格式不能作为"
            f"目标网格读取，请重新执行驱动映射（当前版本 "
            f"{DRIVER_MAPPING_SCHEMA_VERSION}）"
        )
    text = str(version).strip()
    if text != DRIVER_MAPPING_SCHEMA_VERSION:
        raise ValueError(
            f"不支持的驱动映射结果 schema_version: {version!r}；"
            f"当前只接受 {DRIVER_MAPPING_SCHEMA_VERSION}，"
            "旧映射请重新执行驱动映射"
        )


def _validate_unique_driver_values(
    points: tuple[Any, ...], attribute: str, label: str
) -> None:
    """同一频点内的锚点功率必须唯一，避免目标表/反查出现二义性。"""
    seen: set[tuple[Frequency, float]] = set()
    for point in points:
        frequency = Frequency.from_document_key(point.frequency_ghz)
        value = getattr(point, attribute)
        key = (frequency, value)
        if key in seen:
            raise ValueError(f"{label}在 {point.frequency_ghz} GHz 重复: {value} dBm")
        seen.add(key)


def _validate_target_grid_coverage(
    raw_points: tuple[DriverMappingRawPoint, ...],
    target_points: tuple[DriverMappingTargetPoint, ...],
) -> None:
    """目标表的源功率必须落在同频点原始实测曲线的覆盖范围内。

    这是输入可用性的结构检查，不做插值一致性计算（步骤 5 的曲线模块负责单调性、
    插值和输出一致性）。这里只保证正式测试不会拿目标表去反查一条不存在的曲线。
    """
    raw_sources: dict[Frequency, list[float]] = {}
    for point in raw_points:
        frequency = Frequency.from_document_key(point.frequency_ghz)
        raw_sources.setdefault(frequency, []).append(point.source_power_dbm)
    for point in target_points:
        frequency = Frequency.from_document_key(point.frequency_ghz)
        sources = raw_sources.get(frequency)
        if not sources:
            raise ValueError(
                f"驱动映射目标点 {point.frequency_ghz} GHz 缺少原始实测点，"
                "无法反查源功率"
            )
        low, high = min(sources), max(sources)
        tolerance = 1e-9 * max(
            1.0, abs(low), abs(high), abs(point.source_power_dbm)
        )
        if point.source_power_dbm < low - tolerance or (
            point.source_power_dbm > high + tolerance
        ):
            raise ValueError(
                f"驱动映射目标点 {point.frequency_ghz} GHz 的源功率 "
                f"{point.source_power_dbm} dBm 超出原始映射覆盖范围 "
                f"[{low}, {high}] dBm，禁止外推"
            )


def _service_status(value: Any) -> MeasurementStatus:
    """把服务层载荷状态收敛为正式状态；服务只使用 waiting/finished 两个别名。"""
    if isinstance(value, MeasurementStatus):
        return value
    if value is None:
        return MeasurementStatus.COMPLETED
    text = str(value)
    translated = {
        "waiting": MeasurementStatus.WAITING_FOR_CONTINUE.value,
        "finished": MeasurementStatus.COMPLETED.value,
    }.get(text, text)
    return MeasurementStatus(translated)


def _cable_loss_rows(value: Any) -> tuple[CableLossRow, ...]:
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise ValueError("cable_losses 必须是映射")
    rows = []
    for frequency, losses in value.items():
        if not isinstance(losses, Mapping):
            raise ValueError(f"线损结果 {frequency!r} 必须是映射")
        rows.append(CableLossRow(frequency_ghz=str(frequency), cable_losses=losses))
    return tuple(rows)


_RAW_MAPPING_POINT_KEYS = frozenset(
    {"source_power_dbm", "driver_output_power_dbm", "gain_db"}
)
_TARGET_MAPPING_POINT_KEYS = frozenset(
    {"target_input_power_dbm", "source_power_dbm", "driver_output_power_dbm"}
)


def _raw_points_from_power_mapping(value: Any) -> tuple[DriverMappingRawPoint, ...]:
    """把服务层的原始 ``power_mapping`` 载荷转换为审计点。

    这个入口只用于**服务载荷**；正式结果文档没有这个字段。服务载荷的含义就是
    “源功率 -> 补偿后驱放输出”，转换后仍是原始点，不会成为目标输入表。
    ``None`` 不是“没有原始点”，而是缺值，直接拒绝。
    """
    if value is None:
        raise ValueError("power_mapping 不能为空值")
    if not isinstance(value, Mapping):
        raise ValueError("power_mapping 必须是映射")
    points = []
    for frequency, bucket in value.items():
        if not isinstance(bucket, Mapping):
            raise ValueError(f"驱动映射结果 {frequency!r} 必须是映射")
        for source_power, output_power in bucket.items():
            points.append(
                DriverMappingRawPoint(
                    frequency_ghz=str(frequency),
                    source_power_dbm=source_power,
                    driver_output_power_dbm=output_power,
                )
            )
    return tuple(points)


def _raw_mapping_points(
    value: Any, *, require_gain: bool
) -> tuple[DriverMappingRawPoint, ...]:
    """读取按频点分组的原始映射点序列。"""
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise ValueError("raw_points 必须是频率到原始映射点的映射")
    points = []
    required = (
        _RAW_MAPPING_POINT_KEYS
        if require_gain
        else _RAW_MAPPING_POINT_KEYS - {"gain_db"}
    )
    for frequency, bucket in value.items():
        if isinstance(bucket, (str, bytes)) or not isinstance(bucket, (list, tuple)):
            raise ValueError(f"驱动映射原始点 {frequency!r} 必须是序列")
        for index, item in enumerate(bucket):
            label = f"驱动映射原始点 {frequency!r}[{index}]"
            if not isinstance(item, Mapping):
                raise ValueError(f"{label} 必须是映射")
            unknown = sorted(set(item) - _RAW_MAPPING_POINT_KEYS)
            if unknown:
                raise ValueError(f"{label} 包含未知字段: {', '.join(unknown)}")
            missing = sorted(required - set(item))
            if missing:
                raise ValueError(f"{label} 缺少字段: {', '.join(missing)}")
            points.append(
                DriverMappingRawPoint(
                    frequency_ghz=str(frequency),
                    source_power_dbm=item.get("source_power_dbm"),
                    driver_output_power_dbm=item.get("driver_output_power_dbm"),
                    gain_db=item.get("gain_db"),
                )
            )
    return tuple(points)


def _target_mapping_points(value: Any) -> tuple[DriverMappingTargetPoint, ...]:
    """读取按频点分组的目标网格映射点序列。"""
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise ValueError("target_mapping 必须是频率到目标映射点的映射")
    points = []
    for frequency, bucket in value.items():
        if isinstance(bucket, (str, bytes)) or not isinstance(bucket, (list, tuple)):
            raise ValueError(f"驱动映射目标点 {frequency!r} 必须是序列")
        for index, item in enumerate(bucket):
            label = f"驱动映射目标点 {frequency!r}[{index}]"
            if not isinstance(item, Mapping):
                raise ValueError(f"{label} 必须是映射")
            unknown = sorted(set(item) - _TARGET_MAPPING_POINT_KEYS)
            if unknown:
                raise ValueError(f"{label} 包含未知字段: {', '.join(unknown)}")
            missing = sorted(_TARGET_MAPPING_POINT_KEYS - set(item))
            if missing:
                raise ValueError(f"{label} 缺少字段: {', '.join(missing)}")
            points.append(
                DriverMappingTargetPoint(
                    frequency_ghz=str(frequency),
                    target_input_power_dbm=item.get("target_input_power_dbm"),
                    source_power_dbm=item.get("source_power_dbm"),
                    driver_output_power_dbm=item.get("driver_output_power_dbm"),
                )
            )
    return tuple(points)


_AMPLIFIER_DOCUMENT_KEYS = frozenset(
    {
        "compression_type",
        "compression_point",
        "small_signal_gain_db",
        "compression_achieved",
        "sweep_data",
    }
)
_AMPLIFIER_COMPRESSION_DOCUMENT_KEYS = frozenset(
    {
        "input_power_dbm",
        "output_power_dbm",
        "gain_db",
        "efficiency_percent",
        "compression_db",
        "signal_generator_power_dbm",
    }
)


def _amplifier_frequencies_from_service(
    value: Any,
) -> tuple[AmplifierFrequencyResult, ...]:
    """读取测量服务载荷（``results``）；服务字段名只在本函数内翻译一次。"""
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise ValueError("results 必须是映射")
    results = []
    for frequency, item in value.items():
        if not isinstance(item, Mapping):
            raise ValueError(f"主功放结果 {frequency!r} 必须是映射")
        point = item.get("compression_point")
        results.append(
            AmplifierFrequencyResult(
                frequency_ghz=str(frequency),
                compression_type=_optional_text(item.get("compression_type")),
                compression_point=_compression_point(
                    point,
                    reader=_service_compression_point,
                    name="compression_point",
                ),
                small_signal_gain_db=_float_or_none(item.get("small_signal_gain")),
                compression_achieved=_optional_bool(item.get("compression_achieved")),
                sweep_data=item.get("sweep_data", {}),
            )
        )
    return tuple(results)


def _amplifier_frequencies_from_document(
    value: Any,
) -> tuple[AmplifierFrequencyResult, ...]:
    """读取正式结果文档（``results``）；只接受正式字段名。"""
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise ValueError("results 必须是映射")
    results = []
    for frequency, item in value.items():
        if not isinstance(item, Mapping):
            raise ValueError(f"主功放结果 {frequency!r} 必须是映射")
        unknown = sorted(set(item) - _AMPLIFIER_DOCUMENT_KEYS)
        if unknown:
            raise ValueError(
                f"主功放结果 {frequency!r} 包含未知字段: {', '.join(unknown)}"
            )
        results.append(
            AmplifierFrequencyResult(
                frequency_ghz=str(frequency),
                compression_type=_optional_text(item.get("compression_type")),
                compression_point=_compression_point(
                    item.get("compression_point"),
                    reader=_document_compression_point,
                    name=f"主功放结果 {frequency!r} 的 compression_point",
                ),
                small_signal_gain_db=_float_or_none(item.get("small_signal_gain_db")),
                compression_achieved=_optional_bool(item.get("compression_achieved")),
                sweep_data=item.get("sweep_data", {}),
            )
        )
    return tuple(results)


def _compression_point(
    value: Any,
    *,
    reader: Callable[[Mapping[str, Any], str], AmplifierCompressionPoint],
    name: str,
) -> AmplifierCompressionPoint | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} 必须是映射")
    if not value:
        return None
    return reader(value, name)


def _service_compression_point(
    value: Mapping[str, Any], name: str
) -> AmplifierCompressionPoint:
    return AmplifierCompressionPoint(
        input_power_dbm=_float_or_none(value.get("input_power")),
        output_power_dbm=_float_or_none(value.get("output_power")),
        gain_db=_float_or_none(value.get("gain")),
        efficiency_percent=_float_or_none(value.get("efficiency")),
        compression_db=_float_or_none(value.get("compression_dB")),
        signal_generator_power_dbm=_float_or_none(
            value.get("sg_power_at_compression")
        ),
    )


def _document_compression_point(
    value: Mapping[str, Any], name: str
) -> AmplifierCompressionPoint:
    unknown = sorted(set(value) - _AMPLIFIER_COMPRESSION_DOCUMENT_KEYS)
    if unknown:
        raise ValueError(f"{name} 包含未知字段: {', '.join(unknown)}")
    return AmplifierCompressionPoint(
        input_power_dbm=_float_or_none(value.get("input_power_dbm")),
        output_power_dbm=_float_or_none(value.get("output_power_dbm")),
        gain_db=_float_or_none(value.get("gain_db")),
        efficiency_percent=_float_or_none(value.get("efficiency_percent")),
        compression_db=_float_or_none(value.get("compression_db")),
        signal_generator_power_dbm=_float_or_none(
            value.get("signal_generator_power_dbm")
        ),
    )


def _amplifier_document(item: AmplifierFrequencyResult) -> dict[str, Any]:
    point = item.compression_point
    return {
        "compression_type": item.compression_type,
        "compression_point": {} if point is None else {
            "input_power_dbm": point.input_power_dbm,
            "output_power_dbm": point.output_power_dbm,
            "gain_db": point.gain_db,
            "efficiency_percent": point.efficiency_percent,
            "compression_db": point.compression_db,
            "signal_generator_power_dbm": point.signal_generator_power_dbm,
        },
        "small_signal_gain_db": item.small_signal_gain_db,
        "sweep_data": {
            name: [thaw(item) for item in values] for name, values in item.sweep_data.items()
        },
        "compression_achieved": item.compression_achieved,
    }


def _contract_fields(document: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "measurement_id": _measurement_id(document),
        "error": document.get("error"),
        "errors": document.get("errors", ()),
        "cleanup_records": document.get("cleanup_records", ()),
        "termination": document.get("termination"),
        "configuration_snapshot": document.get("configuration_snapshot", {}),
        "device_summary": document.get("device_summary", {}),
        "raw_data_reference": document.get("raw_data_reference", {}),
        "calculation_summary": document.get("calculation_summary", {}),
        "result_reference": document.get("result_reference", {}),
        "measurement_time": _optional_text(document.get("measurement_time")),
        "source_filename": _optional_text(document.get("source_filename")),
    }


def _created_at(document: Mapping[str, Any]) -> str | None:
    value = document.get("created_at")
    return value if isinstance(value, str) and value.strip() else None


def _enum_value(value: Any, enum_type: type[Enum], name: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))
    except ValueError as error:
        allowed = ", ".join(item.value for item in enum_type)
        raise ValueError(f"{name} 无效: {value!r}，可选值: {allowed}") from error


def _float(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} 必须是数值: {value!r}")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} 必须是数值: {value!r}") from error
    if not math.isfinite(number):
        raise ValueError(f"{name} 必须是有限数值: {value!r}")
    return number


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    return _float(value, "数值字段")


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_bool(value: Any) -> bool | None:
    """严格读取布尔值：只接受 bool、0/1 与 ``"true"``/``"false"``。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text == "true":
            return True
        if text == "false":
            return False
    raise ValueError(f"布尔字段取值非法: {value!r}")


def _float_mapping(value: Any, name: str) -> Mapping[str, float]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} 必须是映射")
    return freeze({str(key): _float(item, name) for key, item in value.items()})


def _columns(value: Any, name: str) -> Mapping[str, tuple[Any, ...]]:
    """校验列式扫描数据：各列等长，数值列必须是有限浮点。

    ``voltages`` / ``currents`` 是按供电通道分组的映射列，其叶值同样必须是有限浮点；
    其余列一律视为标量序列。结构化对象、字符串数字混排和 ``NaN`` / ``inf`` 都视为
    非正式结果，避免把无法绘图的载荷写进结果模型。
    """
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} 必须是列式映射")
    columns: dict[str, tuple[Any, ...]] = {}
    length: int | None = None
    for key, items in value.items():
        if not isinstance(items, (list, tuple)):
            raise ValueError(f"{name}.{key} 必须是扫描点序列")
        if length is None:
            length = len(items)
        elif len(items) != length:
            raise ValueError(f"{name} 的各列长度必须一致")
        column = str(key)
        if column in _SWEEP_MAPPING_COLUMNS:
            columns[column] = tuple(
                _float_mapping(item, f"{name}.{column}") for item in items
            )
        else:
            columns[column] = tuple(_float(item, f"{name}.{column}") for item in items)
    return MappingProxyType(columns)


_SWEEP_MAPPING_COLUMNS = frozenset({"voltages", "currents"})


def _rows(value: Any, expected: type, name: str) -> tuple[Any, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} 必须是结果序列")
    if not all(isinstance(item, expected) for item in value):
        raise ValueError(f"{name} 中的每项必须是 {expected.__name__}")
    return tuple(value)


def _errors(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, (list, tuple)):
        raise ValueError("errors 必须是字符串或字符串序列")
    return tuple(str(item) for item in value)


def _records(value: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, Mapping):
        value = (value,)
    if not isinstance(value, (list, tuple)):
        raise ValueError("cleanup_records 必须是映射序列")
    records = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("cleanup_records 中的每项必须是映射")
        # 逐项深冻结：否则冻结 dataclass 的内部记录仍可被外部原始对象改写。
        records.append(freeze(item))
    return tuple(records)


def _validate_termination(value: Mapping[str, Any] | None, expected: str | None) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("终止结果必须包含 termination")
    intent = value.get("intent")
    if intent not in {"stop", "cancel", "emergency_stop"}:
        raise ValueError("termination.intent 无效")
    if expected is not None and intent != expected:
        raise ValueError(f"termination.intent 必须为 {expected}")
    reason = value.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("termination.reason 不能为空")


__all__ = [
    "DRIVER_MAPPING_SCHEMA_VERSION",
    "GENERATED_MEASUREMENT_ID_PATTERN",
    "MEASUREMENT_ID_PATTERN",
    "MEASUREMENT_ID_TIMESTAMP_FORMAT",
    "MEASUREMENT_RESULT_SUFFIX",
    "MeasurementType",
    "MeasurementStatus",
    "MeasurementResult",
    "CableLossMeasurementResult",
    "DriverPowerMappingMeasurementResult",
    "AmplifierMeasurementResult",
    "CableLossRow",
    "DriverMappingInput",
    "DriverMappingRawPoint",
    "DriverMappingTargetPoint",
    "AmplifierCompressionPoint",
    "AmplifierFrequencyResult",
    "generate_measurement_id",
    "measurement_result_from_document",
    "measurement_result_filename",
    "measurement_type_for_result_type",
    "require_formal_result",
    "require_measurement_identity",
    "validate_generated_measurement_id",
    "validate_measurement_id",
    "validate_measurement_result_filename",
    "normalize_frequency_key",
    "freeze",
    "thaw",
]
