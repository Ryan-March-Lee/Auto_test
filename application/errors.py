"""跨层异常分类：连接、协议、设备能力、测量、清理与配置。

本模块是[技术债分阶段重构计划](../docs/技术债分阶段重构计划.md)步骤 15 的可执行边界：
正式代码用同一套基类表达“失败属于哪一类”，调用方据此决定重试、降级还是终止，不再靠
字符串匹配或者继续执行硬件动作。

六类语义：

| 类别 | 基类 | 含义 |
| --- | --- | --- |
| 配置 | :class:`ConfigurationError` | 配置、校准输入或运行契约不满足正式结构 |
| 连接 | :class:`DeviceConnectionError` | 无法建立设备连接（VISA 资源打不开等） |
| 协议 | :class:`ProtocolError` | 已连接的设备在命令/响应层失败（含超时） |
| 设备能力 | :class:`DeviceCapabilityError` | 注入的设备对象缺少测量所需能力 |
| 测量 | :class:`MeasurementError` | 测量动作本身失败或结果不可解释 |
| 清理 | :class:`CleanupError` | 安全清理失败；按动作聚合，不吞掉任何一项 |

清理是唯一允许“继续尝试后续动作”的类别：每个失败动作都记录成
:class:`FailureRecord`，最终由聚合异常一次性上报，而不是停在第一个失败处。
其他类别一旦抛出就表示当前动作链必须停止，捕获后继续执行硬件动作是禁止的。

领域层保持自己的值对象错误（``domain.measurement.values.MeasurementValueError`` /
``MeasurementUnitError``），因为它不允许依赖本模块所在的层；应用层在边界处把它翻译成
:class:`MeasurementError` 或 :class:`ConfigurationError`。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable


__all__ = [
    "AutoTestError",
    "CleanupError",
    "ConfigurationError",
    "DeviceCapabilityError",
    "DeviceConnectionError",
    "ErrorCategory",
    "FailureRecord",
    "MeasurementError",
    "ProtocolError",
    "cause_of",
    "normalize_failures",
]


class ErrorCategory(str, Enum):
    """异常分类；与计划步骤 15 的六类一一对应。"""

    CONFIGURATION = "configuration"
    CONNECTION = "connection"
    PROTOCOL = "protocol"
    CAPABILITY = "capability"
    MEASUREMENT = "measurement"
    CLEANUP = "cleanup"


@dataclass(frozen=True)
class FailureRecord:
    """单个失败动作的结构化记录。

    ``action`` 是安全动作名（例如 ``rf_output`` / ``power_supplies`` /
    ``resources``）或设备步骤名；``error`` 是原始异常，或已经无法保留异常对象时
    的文本说明。清理聚合异常必须逐项记录，不允许只保留最后一条消息。
    """

    action: str
    error: BaseException | str

    def message(self) -> str:
        """返回失败说明文本；没有消息时退回异常类名。"""
        if isinstance(self.error, str):
            return self.error
        return str(self.error) or type(self.error).__name__

    def describe(self) -> str:
        """返回 ``动作: 说明`` 形式的一行描述。"""
        return f"{self.action}: {self.message()}" if self.action else self.message()


def _format_failure_message(message: str, failures: Iterable[FailureRecord]) -> str:
    details = "; ".join(record.describe() for record in failures)
    return f"{message}: {details}" if details else message


def cause_of(failures: Iterable[FailureRecord]) -> BaseException | None:
    """返回第一个可用的原始异常，供聚合异常 ``raise ... from`` 使用。

    记录里可能只剩文本说明（例如从运行记录反序列化的失败），此时返回 ``None``，
    调用方仍然抛出聚合异常，但不会伪造异常链。
    """
    for record in failures:
        if isinstance(record.error, BaseException):
            return record.error
    return None


# 失败项的三种来源：结构化记录、原始异常（历史 API 的 ``list[BaseException]``）与文本说明。
FailureKind = FailureRecord | BaseException | str


def normalize_failures(failures: Iterable[FailureKind]) -> tuple[FailureRecord, ...]:
    """把失败项统一成 :class:`FailureRecord` 元组。

    分类之前的聚合异常（``InstrumentSafetyError``、``PowerOffFlowError`` 等）以
    ``(message, errors)`` 接收 ``list[BaseException]``，运行记录里也可能只剩文本。
    这些形态都必须在同一套聚合结构里保留下来，所以 ``AutoTestError`` 在构造时统一
    转换：没有动作名的项留空 ``action``，而不是丢弃或猜测动作。
    """
    normalized: list[FailureRecord] = []
    for failure in failures:
        if isinstance(failure, FailureRecord):
            normalized.append(failure)
        elif isinstance(failure, BaseException):
            normalized.append(FailureRecord("", failure))
        else:
            normalized.append(FailureRecord("", str(failure)))
    return tuple(normalized)


class AutoTestError(Exception):
    """本项目正式代码的异常根。

    继承者声明的 ``category`` 决定调用方的处理策略；``failures`` 让聚合异常保留
    每个失败动作，``errors`` 保留原始异常对象以兼容既有调用方。

    分类基类同时保留历史上的内建基类（设备/测量/清理失败是 ``RuntimeError``，
    配置失败是 ``ValueError``）：既有 ``except RuntimeError`` 处理器继续生效，
    新增的分类能力只是叠加，不改写调用方的既有语义。

    ``failures`` 接受 :class:`FailureRecord`、原始异常或文本说明（见
    :func:`normalize_failures`）：历史聚合异常以 ``(message, errors)`` 形式接收
    ``list[BaseException]``，这个构造形式必须继续可用。
    """

    category: ErrorCategory | None = None

    def __init__(
        self,
        message: str = "",
        failures: Iterable[FailureKind] = (),
    ) -> None:
        self.failures = normalize_failures(failures)
        self.errors = tuple(record.error for record in self.failures)
        super().__init__(_format_failure_message(message, self.failures))

    @property
    def actions(self) -> tuple[str, ...]:
        """返回失败动作名，顺序与 :attr:`failures` 一致。"""
        return tuple(record.action for record in self.failures)

    def to_dict(self) -> dict[str, Any]:
        """返回可写入运行记录/现场报告的纯文档。"""
        return {
            "category": None if self.category is None else self.category.value,
            "message": str(self),
            "failures": [
                {"action": record.action, "message": record.message()}
                for record in self.failures
            ],
        }


class ConfigurationError(AutoTestError, ValueError):
    """配置、校准输入或运行契约不满足正式结构。"""

    category = ErrorCategory.CONFIGURATION


class DeviceConnectionError(AutoTestError, RuntimeError):
    """无法建立设备连接。"""

    category = ErrorCategory.CONNECTION


class ProtocolError(AutoTestError, RuntimeError):
    """设备命令/响应层失败（含超时）。"""

    category = ErrorCategory.PROTOCOL


class DeviceCapabilityError(AutoTestError, RuntimeError):
    """设备对象缺少测量服务所需的能力。"""

    category = ErrorCategory.CAPABILITY


class MeasurementError(AutoTestError, RuntimeError):
    """测量动作失败或结果不可解释。

    允许携带 ``failures``：上电失败时既记录失败的上电步骤，也记录随后安全关闭
    动作的失败，调用方一次就能看到全部问题。
    """

    category = ErrorCategory.MEASUREMENT


class CleanupError(AutoTestError, RuntimeError):
    """安全清理失败；``failures`` 逐项记录失败动作。"""

    category = ErrorCategory.CLEANUP
