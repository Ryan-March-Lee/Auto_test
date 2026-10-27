"""测量会话与安全清理端口。

application 只依赖这里的协议：测量服务通过注入的测量会话执行
RF -> 电源 -> 连接 的统一安全清理，不再导入 ``instrument.flow`` 的具体实现。
清理结果是结构化记录，失败时以 ``SafetyCleanupError`` 携带记录抛出，调用方
必须把该错误当作明确的运行终态，而不是只写一条日志。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Protocol, runtime_checkable

if TYPE_CHECKING:  # 避免 application.ports -> instrument 的运行时导入环
    from instrument.ports import MeasurementInstrumentPort


__all__ = [
    "CleanupRecord",
    "CleanupStepStatus",
    "MeasurementSessionBinding",
    "MeasurementSessionPort",
    "SafetyCleanupError",
    "SafetyCleanupPort",
]


class CleanupStepStatus(str, Enum):
    """单个安全清理动作的结果。"""

    PERFORMED = "performed"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class CleanupRecord:
    """一次安全清理的结构化结果。

    ``rf_output``、``power_supplies`` 和 ``resources`` 分别记录 RF 关闭、
    电源关闭和连接释放的执行状态；``errors`` 保存全部失败的文本描述。
    """

    rf_output: CleanupStepStatus
    power_supplies: CleanupStepStatus
    resources: CleanupStepStatus
    errors: tuple[str, ...] = ()

    @property
    def succeeded(self) -> bool:
        """所有动作都完成或按边界跳过时返回 True。"""
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        """返回可写入运行记录的结果文档。"""
        return {
            "rf_output": self.rf_output.value,
            "power_supplies": self.power_supplies.value,
            "resources": self.resources.value,
            "errors": list(self.errors),
            "succeeded": self.succeeded,
        }


class SafetyCleanupError(RuntimeError):
    """安全清理失败；``record`` 是本次清理的完整结构化结果。"""

    def __init__(self, record: CleanupRecord) -> None:
        self.record = record
        details = "; ".join(record.errors)
        message = "测量安全清理存在失败"
        super().__init__(f"{message}: {details}" if details else message)


@runtime_checkable
class SafetyCleanupPort(Protocol):
    """测量会话的安全清理端口。"""

    def cleanup(
        self,
        *,
        power_cleanup: Callable[[], None] | None = None,
        release_resources: bool = True,
    ) -> CleanupRecord:
        """按 RF -> 电源 -> 连接 顺序清理并返回结构化记录。

        失败时必须抛出 :class:`SafetyCleanupError`，异常携带的记录描述每个
        动作的状态和错误；成功时返回所有动作都已完成或按边界跳过的记录。
        """


@runtime_checkable
class MeasurementSessionPort(Protocol):
    """一次测量使用的设备会话。

    会话把设备控制端口和它的安全清理端口绑定在同一生命周期边界内；
    application 只依赖本协议，不导入任何具体 flow 实现。
    """

    @property
    def instrument(self) -> MeasurementInstrumentPort:
        """返回本次测量使用的设备控制端口。"""

    def cleanup(
        self,
        *,
        power_cleanup: Callable[[], None] | None = None,
        release_resources: bool = True,
    ) -> CleanupRecord:
        """执行安全清理并返回结构化记录。"""


@dataclass(frozen=True)
class MeasurementSessionBinding:
    """把测量端口和安全清理端口绑定为一个测量会话。

    绑定在构造阶段校验两个端口：清理端口必须提供可调用的 ``cleanup()``，避免
    测量动作执行后才在 ``finally`` 清理时才发现注入对象无效。
    """

    instrument: MeasurementInstrumentPort
    safety_cleanup: SafetyCleanupPort

    def __post_init__(self) -> None:
        if self.instrument is None:
            raise ValueError("测量会话必须包含 instrument")
        if self.safety_cleanup is None:
            raise ValueError("测量会话必须包含 safety_cleanup")
        if not callable(getattr(self.safety_cleanup, "cleanup", None)):
            raise ValueError("测量会话的 safety_cleanup 必须提供 cleanup() 方法")

    def cleanup(
        self,
        *,
        power_cleanup: Callable[[], None] | None = None,
        release_resources: bool = True,
    ) -> CleanupRecord:
        return self.safety_cleanup.cleanup(
            power_cleanup=power_cleanup,
            release_resources=release_resources,
        )
