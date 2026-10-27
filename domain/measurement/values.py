"""测量计算的强类型值对象：频率、功率、损耗和线损拓扑。

技术债步骤 13 的唯一单位边界：GHz/Hz、dBm/W、dB/% 的换算只在这里发生。
``domain.measurement.calculations`` 的纯函数只接受这里的值对象，不接受裸浮点数，
因此“把 Hz 当成 GHz”或“把 W 当成 dBm”这类错误无法静默进入测量计算。

单位约定（每个类型只有一个规范内部单位）：

- :class:`Frequency` 内部是 GHz；
- :class:`Decibels` 是 dB 标量（损耗、增益、压缩量）；
- :class:`PowerDbm` 内部是 dBm，:class:`PowerWatt` 内部是 W；
- :class:`Percent` 内部是百分数（``10`` 表示 10 %），比值通过 ``ratio`` 访问。

字符串频率键和 ``cable1`` 等字符串字段只在本模块出现：线损拓扑由
:class:`CableLossProfile` / :class:`CableLossTable` 表达，JSON 文档形状通过
``to_mapping()`` / ``to_document()`` 显式转换，不再散落在调用方。

频率文本有两个不同的入口，不能互相顶替：

- :meth:`Frequency.parse` 解析**测量文本**，必须带单位（``"2.4GHz"``），
  不猜测裸数值的单位；
- :meth:`Frequency.from_document_key` 解析**结果文档的频率键**：schema 约定
  无单位键表示 GHz（``"2.4"``），也接受显式单位文本，但只接受字符串键。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

import numpy as np

__all__ = [
    "MeasurementValueError",
    "MeasurementUnitError",
    "Frequency",
    "Decibels",
    "PowerDbm",
    "PowerWatt",
    "Percent",
    "CableLossProfile",
    "CableLossTable",
    "DriverOutputTable",
    "CompressionPoint",
    "CompressionResult",
]

# 线损字段是结果文档里的固定键，只在这里出现一次。
_CABLE_FIELDS = (
    "cable1",
    "cable2",
    "cable3",
    "cable4",
    "total_path1",
    "total_path2",
)

# ``parse()`` 接受的频率单位后缀及其到 GHz 的除数；GHz 是缺省单位，与 JSON 频率键一致。
_FREQUENCY_UNITS = (("ghz", 1.0), ("mhz", 1e3), ("hz", 1e9))


class MeasurementValueError(ValueError):
    """测量数值不合法（非有限数值、缺少必需字段、频率不存在等）。"""


class MeasurementUnitError(ValueError):
    """单位文本无法解析或与期望单位不符；不猜测调用方的单位。"""


def _number(value: Any, label: str) -> float:
    """把显式数值收敛为有限 ``float``；拒绝字符串、布尔和值对象。"""
    if isinstance(value, bool) or isinstance(value, (str, bytes)):
        raise TypeError(f"{label} 必须是数值，收到 {type(value).__name__}")
    if isinstance(value, (Frequency, Decibels, PowerDbm, PowerWatt, Percent)):
        raise TypeError(f"{label} 不接受带单位的测量值对象，请显式取数值字段")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise TypeError(f"{label} 必须是数值，收到 {type(value).__name__}") from None
    if not math.isfinite(number):
        raise MeasurementValueError(f"{label} 必须是有限数值，收到 {number!r}")
    return number


def _require(value: Any, expected: type, label: str) -> Any:
    """校验参数就是期望的值对象类型，不接受裸数值或其他单位。"""
    if not isinstance(value, expected):
        raise TypeError(
            f"{label} 必须是 {expected.__name__}，收到 {type(value).__name__}"
        )
    return value


def _text_number(text: str, label: str, original: Any) -> float:
    """把文本数值解析为有限 ``float``；语法错误抛单位错误，非有限值抛数值错误。"""
    try:
        number = float(text)
    except ValueError:
        raise MeasurementUnitError(f"无法解析 {label} 的数值部分: {original!r}") from None
    if not math.isfinite(number):
        raise MeasurementValueError(f"{label} 必须是有限数值，收到 {number!r}")
    return number


def _unit_number(text: Any, unit: str, label: str) -> float:
    """解析“数值 + 单位后缀”的文本；后缀缺失或不同一律拒绝。"""
    if not isinstance(text, str):
        raise MeasurementUnitError(
            f"{label} 必须写成带单位的字符串（如 '30{unit}'），收到 {type(text).__name__}"
        )
    raw = text.strip()
    if not raw:
        raise MeasurementUnitError(f"{label} 不能为空")
    lowered = raw.lower()
    suffix = unit.lower()
    if not lowered.endswith(suffix):
        raise MeasurementUnitError(
            f"{label} 单位必须是 {unit}，收到 {text!r}；不猜测其他单位"
        )
    return _text_number(lowered[: -len(suffix)].strip(), label, text)


def _power_key(key: Any, label: str) -> float:
    """解析驱动映射的输入功率键（dBm）；字符串键与数值键都按 dBm 读取。"""
    if isinstance(key, bool):
        raise MeasurementUnitError(f"{label} 不能是布尔值: {key!r}")
    if isinstance(key, str):
        return _text_number(key.strip(), label, key)
    return _number(key, label)


@dataclass(frozen=True, order=True)
class Frequency:
    """测量频率；规范内部单位是 GHz。"""

    ghz: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "ghz", _number(self.ghz, "频率"))

    @classmethod
    def from_ghz(cls, value: Any) -> "Frequency":
        return cls(_number(value, "频率(GHz)"))

    @classmethod
    def from_mhz(cls, value: Any) -> "Frequency":
        return cls(_number(value, "频率(MHz)") / 1e3)

    @classmethod
    def from_hz(cls, value: Any) -> "Frequency":
        return cls(_number(value, "频率(Hz)") / 1e9)

    @classmethod
    def parse(cls, text: Any) -> "Frequency":
        """解析**带单位**的频率文本：``"2.4GHz"`` / ``"2400MHz"`` / ``"2.4e9Hz"``。

        裸数值文本（如 ``"2.4"``、``"2400"``）不是完整测量值：它既可能是 GHz 也可能是
        MHz，解析时必须由调用方写出单位，不猜测。结果文档里 schema 约定的无单位 GHz
        键用 :meth:`from_document_key` 读取。
        """
        if isinstance(text, Frequency):
            return text
        if isinstance(text, bool) or text is None or not isinstance(text, str):
            raise MeasurementUnitError(
                f"频率必须写成带单位的字符串（如 '2.4GHz'），收到 {type(text).__name__}"
            )
        raw = text.strip()
        if not raw:
            raise MeasurementUnitError("频率不能为空")
        lowered = raw.lower()
        for unit, factor in _FREQUENCY_UNITS:
            if lowered.endswith(unit):
                number = _text_number(lowered[: -len(unit)].strip(), "频率", text)
                return cls(number / factor)
        raise MeasurementUnitError(
            f"频率必须带单位（GHz/MHz/Hz），收到 {text!r}；"
            "无单位的结果文档频率键请用 Frequency.from_document_key 读取"
        )

    @classmethod
    def from_document_key(cls, key: Any) -> "Frequency":
        """解析结果文档的频率键：无单位时按 schema 约定取 GHz。

        只接受字符串键。JSON 对象键本来就是字符串，把浮点数、布尔值或值对象
        悄悄转成文本，会掩盖写入方用错类型或用错单位的问题。
        """
        if isinstance(key, bool) or not isinstance(key, str):
            raise TypeError(
                f"频率键必须是字符串（GHz 或带单位文本），收到 {type(key).__name__}"
            )
        raw = key.strip()
        if not raw:
            raise MeasurementUnitError("频率键不能为空")
        lowered = raw.lower()
        for unit, _factor in _FREQUENCY_UNITS:
            if lowered.endswith(unit):
                return cls.parse(raw)
        return cls(_text_number(raw, "频率键", key))

    @property
    def mhz(self) -> float:
        return self.ghz * 1e3

    @property
    def hz(self) -> float:
        return self.ghz * 1e9

    @property
    def key(self) -> str:
        """正式结果文档中使用的频率键（``float`` 的字符串形式）。"""
        return str(self.ghz)

    def __str__(self) -> str:
        return f"{self.ghz}GHz"


@dataclass(frozen=True, order=True)
class Decibels:
    """dB 标量：线缆损耗、增益、压缩量等相对量。"""

    db: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "db", _number(self.db, "dB 值"))

    @classmethod
    def parse(cls, text: Any) -> "Decibels":
        """解析 ``"30dB"`` 形式的文本；单位必须显式写 dB。"""
        if isinstance(text, Decibels):
            return text
        return cls(_unit_number(text, "dB", "dB 值"))

    def __add__(self, other: "Decibels") -> "Decibels":
        return Decibels(self.db + _require(other, Decibels, "被加的 dB 值").db)

    def __sub__(self, other: "Decibels") -> "Decibels":
        return Decibels(self.db - _require(other, Decibels, "被减的 dB 值").db)

    def __neg__(self) -> "Decibels":
        return Decibels(-self.db)

    def __truediv__(self, divisor: Any) -> "Decibels":
        return Decibels(self.db / _number(divisor, "dB 除数"))

    def __str__(self) -> str:
        return f"{self.db}dB"


@dataclass(frozen=True, order=True)
class PowerWatt:
    """绝对功率；规范内部单位是 W。"""

    watts: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "watts", _number(self.watts, "功率(W)"))

    @classmethod
    def from_watts(cls, value: Any) -> "PowerWatt":
        return cls(value)

    @classmethod
    def from_dbm(cls, value: Any) -> "PowerWatt":
        return cls(10 ** ((_number(value, "功率(dBm)") - 30) / 10))

    def to_dbm(self) -> "PowerDbm":
        if self.watts <= 0:
            raise MeasurementValueError("非正功率无法换算为 dBm")
        return PowerDbm(10 * math.log10(self.watts) + 30)

    def __str__(self) -> str:
        return f"{self.watts}W"


@dataclass(frozen=True, order=True)
class PowerDbm:
    """绝对功率；规范内部单位是 dBm。"""

    dbm: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "dbm", _number(self.dbm, "功率(dBm)"))

    @classmethod
    def from_dbm(cls, value: Any) -> "PowerDbm":
        return cls(value)

    @classmethod
    def from_watt(cls, value: "PowerWatt") -> "PowerDbm":
        return _require(value, PowerWatt, "功率").to_dbm()

    def to_watt(self) -> PowerWatt:
        return PowerWatt(10 ** ((self.dbm - 30) / 10))

    def __add__(self, other: Decibels) -> "PowerDbm":
        return PowerDbm(self.dbm + _require(other, Decibels, "被加的 dB 值").db)

    def __sub__(self, other: Any) -> Any:
        if isinstance(other, Decibels):
            return PowerDbm(self.dbm - other.db)
        return Decibels(self.dbm - _require(other, PowerDbm, "被减的功率").dbm)

    def __str__(self) -> str:
        return f"{self.dbm}dBm"


@dataclass(frozen=True, order=True)
class Percent:
    """百分数；``10`` 表示 10 %，比值通过 :attr:`ratio` 访问。"""

    percent: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "percent", _number(self.percent, "百分比(%)"))

    @classmethod
    def from_ratio(cls, value: Any) -> "Percent":
        return cls(_number(value, "比值") * 100)

    @property
    def ratio(self) -> float:
        return self.percent / 100

    def __str__(self) -> str:
        return f"{self.percent}%"


@dataclass(frozen=True)
class CableLossProfile:
    """单个频点的线损拓扑。

    线①/线②相等、线③/线④相等，由 :func:`domain.measurement.calculations.calculate_cable_losses`
    分摊；这里只表达拓扑本身。字段缺省表示输入文档没有给出该段，访问路径需要它时
    抛出 :class:`MeasurementValueError`，而不是静默按 0 处理。
    """

    cable1: Decibels | None = None
    cable2: Decibels | None = None
    cable3: Decibels | None = None
    cable4: Decibels | None = None
    total_path1: Decibels | None = None
    total_path2: Decibels | None = None

    def __post_init__(self) -> None:
        for name in _CABLE_FIELDS:
            value = getattr(self, name)
            if value is not None:
                _require(value, Decibels, f"cable_losses.{name}")

    @classmethod
    def from_mapping(cls, mapping: Any, *, source: str = "cable_losses") -> "CableLossProfile":
        """读取结果文档中的单频点线损映射；未知字段直接拒绝。"""
        if not isinstance(mapping, Mapping):
            raise TypeError(f"{source} 必须是映射，收到 {type(mapping).__name__}")
        unknown = [key for key in mapping if key not in _CABLE_FIELDS]
        if unknown:
            raise MeasurementValueError(
                f"{source} 含未知线缆字段: {', '.join(map(str, unknown))}；"
                f"允许字段: {', '.join(_CABLE_FIELDS)}"
            )
        values = {
            key: Decibels(_number(value, f"{source}.{key}"))
            for key, value in mapping.items()
        }
        return cls(**values)

    def to_mapping(self) -> dict[str, float]:
        """输出正式结果文档形状（只包含已知且已给出的字段）。"""
        return {
            name: getattr(self, name).db
            for name in _CABLE_FIELDS
            if getattr(self, name) is not None
        }

    def dut_input_path_loss(self) -> Decibels:
        """DUT 输入路径（信号源 -> 线① -> DUT）的损耗。"""
        return self.require_dut_input_path()

    def driver_output_path_loss(self, attenuator_value: Decibels) -> Decibels:
        """驱动功放输出到频谱仪的损耗：衰减器 + 线②（不经过线④）。"""
        attenuator = _require(attenuator_value, Decibels, "衰减器值")
        return attenuator + self.require_driver_output_path()

    def dut_output_path_loss(self, attenuator_value: Decibels) -> Decibels:
        """DUT 输出到频谱仪的损耗：线④ + 衰减器 + 线②（加法顺序与历史公式一致）。"""
        attenuator = _require(attenuator_value, Decibels, "衰减器值")
        cable4, cable2 = self.require_dut_output_path()
        return cable4 + attenuator + cable2

    # 下面三个方法把“某条计算路径还需要哪些线缆，缺了就报错”表达成值对象自己的能力：
    # 调用方不再枚举 ``cable1`` 等字符串字段名，错误信息自带路径语义。

    def require_dut_input_path(self) -> Decibels:
        """校验 DUT 输入路径并返回线①损耗（无驱动时的 DUT 输入功率需要它）。"""
        return self._require_field("cable1", "DUT 输入功率 = sg_power - cable1")

    def require_driver_output_path(self) -> Decibels:
        """校验驱动输出补偿路径并返回线②损耗。"""
        return self._require_field("cable2", "驱动输出补偿需要线②")

    def require_dut_output_path(self) -> tuple[Decibels, Decibels]:
        """校验 DUT 输出补偿路径并返回 ``(线④, 线②)`` 损耗。"""
        cable4 = self._require_field("cable4", "DUT 输出补偿需要线④")
        cable2 = self._require_field("cable2", "DUT 输出补偿需要线②")
        return cable4, cable2

    def _require_field(self, name: str, purpose: str) -> Decibels:
        value = getattr(self, name)
        if value is None:
            raise MeasurementValueError(f"线损数据缺少 {name}（{purpose}）")
        return value


@dataclass(frozen=True)
class CableLossTable:
    """按频率索引的线损表；频率键在边界处就规范化为 :class:`Frequency`。"""

    profiles: Mapping[Frequency, CableLossProfile] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.profiles, Mapping):
            raise TypeError("线损表必须是频率到 CableLossProfile 的映射")
        validated: dict[Frequency, CableLossProfile] = {}
        for frequency, profile in self.profiles.items():
            _require(frequency, Frequency, "线损表频率键")
            _require(profile, CableLossProfile, f"线损表 {frequency.ghz} GHz 的剖面")
            validated[frequency] = profile
        object.__setattr__(self, "profiles", MappingProxyType(validated))

    @classmethod
    def from_mapping(cls, mapping: Any, *, source: str = "cable_losses") -> "CableLossTable":
        """读取 ``{频率键: {cable1..total_path2}}`` 结果文档。

        频率键只能是字符串（schema 约定无单位 GHz，也接受显式单位文本）；同一个
        频率写成 ``"2.4"`` 和 ``"2.40"`` 会在这里被识别为重复记录并拒绝。
        """
        if not isinstance(mapping, Mapping):
            raise TypeError(f"{source} 必须是映射，收到 {type(mapping).__name__}")
        profiles = {}
        for key, profile in mapping.items():
            frequency = Frequency.from_document_key(key)
            if frequency in profiles:
                raise MeasurementValueError(f"{source} 含重复频率 {frequency.key}")
            profiles[frequency] = CableLossProfile.from_mapping(
                profile, source=f"{source}[{key}]"
            )
        return cls(profiles)

    def profile_at(self, frequency: Frequency) -> CableLossProfile:
        _require(frequency, Frequency, "频率")
        try:
            return self.profiles[frequency]
        except KeyError:
            raise MeasurementValueError(
                f"线损数据缺少测试频率 {frequency.key} GHz 的记录"
            ) from None

    def to_document(self) -> dict[str, dict[str, float]]:
        """输出正式结果文档形状：``{频率键: {cable1..total_path2}}``。"""
        return {
            frequency.key: profile.to_mapping()
            for frequency, profile in self.profiles.items()
        }

    def __len__(self) -> int:
        return len(self.profiles)


@dataclass(frozen=True)
class DriverOutputTable:
    """按频率索引的驱动映射表：信号源输入功率 -> 补偿后输出功率。"""

    points: Mapping[Frequency, tuple[tuple[PowerDbm, PowerDbm], ...]] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        if not isinstance(self.points, Mapping):
            raise TypeError("驱动映射表必须是频率到映射点的映射")
        validated: dict[Frequency, tuple[tuple[PowerDbm, PowerDbm], ...]] = {}
        for frequency, pairs in self.points.items():
            _require(frequency, Frequency, "驱动映射频率键")
            validated[frequency] = self._require_pairs(frequency, pairs)
        object.__setattr__(self, "points", MappingProxyType(validated))

    @staticmethod
    def _require_pairs(
        frequency: Frequency, pairs: Any
    ) -> tuple[tuple[PowerDbm, PowerDbm], ...]:
        if isinstance(pairs, (str, bytes, Mapping)):
            raise TypeError(f"驱动映射 {frequency.key} GHz 的映射点必须是序列")
        try:
            items = tuple(pairs)
        except TypeError:
            raise TypeError(
                f"驱动映射 {frequency.key} GHz 的映射点必须是序列"
            ) from None
        validated = []
        for index, pair in enumerate(items):
            if isinstance(pair, (str, bytes, Mapping)):
                raise TypeError(
                    f"驱动映射 {frequency.key} GHz 第 {index} 个点必须是 (输入功率, 输出功率)"
                )
            try:
                input_power, output_power = pair
            except (TypeError, ValueError):
                raise TypeError(
                    f"驱动映射 {frequency.key} GHz 第 {index} 个点必须是 (输入功率, 输出功率)"
                ) from None
            validated.append(
                (
                    _require(input_power, PowerDbm, "驱动映射输入功率"),
                    _require(output_power, PowerDbm, "驱动映射输出功率"),
                )
            )
        return tuple(validated)

    @classmethod
    def from_mapping(
        cls, mapping: Any, *, source: str = "driver_mapping"
    ) -> "DriverOutputTable":
        """读取 ``{频率键: {输入功率键: 输出功率}}`` 驱动映射文档。

        频率键与线损表一样只接受字符串；输入功率键是 dBm 数值文本，字符串键与
        数值键都按 dBm 读取，布尔值和无法解析的文本一律拒绝。
        """
        if not isinstance(mapping, Mapping):
            raise TypeError(f"{source} 必须是映射，收到 {type(mapping).__name__}")
        points = {}
        for frequency_key, bucket in mapping.items():
            frequency = Frequency.from_document_key(frequency_key)
            if frequency in points:
                raise MeasurementValueError(f"{source} 含重复频率 {frequency.key}")
            if not isinstance(bucket, Mapping):
                raise TypeError(
                    f"{source}[{frequency_key}] 必须是输入功率到输出功率的映射"
                )
            pairs = []
            for input_key, output in bucket.items():
                label = f"{source}[{frequency_key}] 的输入功率键"
                try:
                    input_dbm = _power_key(input_key, label)
                except MeasurementUnitError as error:
                    raise MeasurementUnitError(
                        f"{source}[{frequency_key}] 含无法解析的输入功率键 {input_key!r}"
                    ) from error
                pairs.append(
                    (
                        PowerDbm(input_dbm),
                        PowerDbm(
                            _number(output, f"{source}[{frequency_key}][{input_key}]")
                        ),
                    )
                )
            points[frequency] = tuple(pairs)
        return cls(points)

    def is_empty(self) -> bool:
        return not self.points

    def has_points(self, frequency: Frequency) -> bool:
        """指定频率是否有可用映射点；缺频率与空映射点都返回 ``False``。"""
        _require(frequency, Frequency, "频率")
        return bool(self.points.get(frequency))

    def interpolate(self, frequency: Frequency, sg_power: PowerDbm) -> PowerDbm:
        """按输入功率升序线性插值；端点与重复点沿用 ``np.interp`` 行为。"""
        _require(frequency, Frequency, "频率")
        _require(sg_power, PowerDbm, "信号源功率")
        pairs = self.points.get(frequency)
        if pairs is None:
            raise MeasurementValueError(
                f"驱动映射缺少测试频率 {frequency.key} GHz 的记录"
            )
        if not pairs:
            raise MeasurementValueError(
                f"驱动映射在 {frequency.key} GHz 没有有效映射点"
            )
        input_powers = np.array([pair[0].dbm for pair in pairs])
        output_powers = np.array([pair[1].dbm for pair in pairs])
        sorted_indices = np.argsort(input_powers)
        return PowerDbm(
            float(
                np.interp(
                    sg_power.dbm,
                    input_powers[sorted_indices],
                    output_powers[sorted_indices],
                )
            )
        )

    def __len__(self) -> int:
        return len(self.points)


@dataclass(frozen=True)
class CompressionPoint:
    """压缩点指标；单位由字段类型表达，``to_mapping()`` 输出结果文档键。"""

    input_power: PowerDbm
    output_power: PowerDbm
    gain: Decibels
    efficiency: Percent
    compression: Decibels
    sg_power: PowerDbm

    def __post_init__(self) -> None:
        _require(self.input_power, PowerDbm, "compression_point.input_power")
        _require(self.output_power, PowerDbm, "compression_point.output_power")
        _require(self.gain, Decibels, "compression_point.gain")
        _require(self.efficiency, Percent, "compression_point.efficiency")
        _require(self.compression, Decibels, "compression_point.compression")
        _require(self.sg_power, PowerDbm, "compression_point.sg_power")

    def to_mapping(self) -> dict[str, float]:
        return {
            "input_power": self.input_power.dbm,
            "output_power": self.output_power.dbm,
            "gain": self.gain.db,
            "efficiency": self.efficiency.percent,
            "compression_dB": self.compression.db,
            "sg_power_at_compression": self.sg_power.dbm,
        }


@dataclass(frozen=True)
class CompressionResult:
    """压缩点计算结果：小信号增益、是否达到目标压缩量与最大压缩量。"""

    compression_point: CompressionPoint
    small_signal_gain: Decibels
    compression_achieved: bool
    max_compression: Decibels

    def __post_init__(self) -> None:
        _require(self.compression_point, CompressionPoint, "compression_point")
        _require(self.small_signal_gain, Decibels, "small_signal_gain")
        _require(self.max_compression, Decibels, "max_compression")
        if not isinstance(self.compression_achieved, bool):
            raise TypeError("compression_achieved 必须是布尔值")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "compression_point": self.compression_point.to_mapping(),
            "small_signal_gain": self.small_signal_gain.db,
            "compression_achieved": self.compression_achieved,
            "max_compression": self.max_compression.db,
        }
