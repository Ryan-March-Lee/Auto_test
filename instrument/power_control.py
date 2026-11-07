"""按显式通道顺序执行电源上电、关断与失败清理。

本模块只接收 ``PowerSupplyPort`` 和**已经排好序**的物理通道 token，不读取配置、
不持有 VISA 资源，也不认识 gate/drain 角色。顺序就是调用方给出的列表顺序：上电
逐通道开启，关断按本次尝试顺序逆序执行。上电命令一旦发出就把通道视为可能已有
输出（响应丢失不等于设备没有动作），只有关闭命令成功返回后才解除这个标记。

两组计划（DUT / 驱动功放）之间仍由适配器按阶段调用；本模块只负责一组内部的
顺序、组间隔、失败停止与 best-effort 清理。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from application.errors import (
    CleanupError,
    FailureRecord,
    MeasurementError,
    cause_of,
)
from domain.configuration.models import MAX_INTER_CHANNEL_DELAY_S
from infrastructure.logging.app_logging import get_logger

logger = get_logger(__name__)

# 取消后清理失败的结构化附加属性。取消异常的类型与原因保持不变，调用方仍可按
# 自己的取消类型处理；清理失败另取一份记录，不依赖日志或 ``__notes__`` 文本。
POWER_CLEANUP_FAILURES_ATTR = "power_cleanup_failures"


class PowerControlError(CleanupError):
    """安全关断存在失败；``failures`` 逐项记录失败通道。"""


class PowerOnControlError(MeasurementError):
    """上电失败；``failures`` 同时保留启动失败与随后回滚失败。

    上电是测量动作，不是清理动作：把它归入测量类别，调用方才能区分“没上去”与
    “上去了但关不回来”。回滚失败仍在同一个聚合里，不会被丢掉。
    """


def cleanup_failures_of(error: BaseException) -> tuple[FailureRecord, ...]:
    """返回取消异常附带的电源清理失败记录；没有时返回空元组。"""
    return tuple(getattr(error, POWER_CLEANUP_FAILURES_ATTR, ()))


def _channel_text(value: object) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError("供电通道不能为空")
    return text


def _delay_seconds(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"通道间隔必须是有限数值（秒），收到 {type(value).__name__}")
    seconds = float(value)
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError(f"通道间隔必须是有限非负数（秒），收到 {value!r}")
    if seconds > MAX_INTER_CHANNEL_DELAY_S:
        raise ValueError(
            f"通道间隔不能超过 {MAX_INTER_CHANNEL_DELAY_S:g}s，收到 {value!r}"
        )
    return seconds


@dataclass(frozen=True)
class PowerChannelStep:
    """一条物理通道及其（可选）工作值；不承载顺序，顺序由所在计划决定。"""

    channel: str
    voltage_v: float | None = None
    current_a: float | None = None
    #: 本通道开启后到下一条上电命令的等待秒数；``None`` 表示沿用组间隔。
    power_on_delay_s: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "channel", _channel_text(self.channel))
        for field_name in ("voltage_v", "current_a"):
            value = getattr(self, field_name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"{field_name} 必须是有限数值，收到 {type(value).__name__}"
                )
            number = float(value)
            if not math.isfinite(number):
                raise ValueError(f"{field_name} 必须是有限数值，收到 {value!r}")
            object.__setattr__(self, field_name, number)
        if self.power_on_delay_s is not None:
            object.__setattr__(
                self,
                "power_on_delay_s",
                _delay_seconds(self.power_on_delay_s),
            )


@dataclass(frozen=True)
class PowerGroupPlan:
    """一组电源通道的有序执行计划：列表顺序就是上电顺序。"""

    channels: tuple[PowerChannelStep, ...] = ()
    inter_channel_delay_s: float = 0.0

    def __post_init__(self) -> None:
        steps = tuple(self.channels)
        for index, step in enumerate(steps):
            if not isinstance(step, PowerChannelStep):
                raise ValueError(
                    f"计划通道[{index}] 必须是 PowerChannelStep，"
                    f"收到 {type(step).__name__}"
                )
        tokens = tuple(step.channel for step in steps)
        if len(set(tokens)) != len(tokens):
            duplicated = sorted({token for token in tokens if tokens.count(token) > 1})
            raise ValueError("同一组电源通道不能重复: " + ", ".join(duplicated))
        object.__setattr__(self, "channels", steps)
        object.__setattr__(
            self, "inter_channel_delay_s", _delay_seconds(self.inter_channel_delay_s)
        )

    @classmethod
    def from_channels(
        cls,
        channels: Iterable[object],
        *,
        settings: Mapping[str, object] | None = None,
        inter_channel_delay_s: float = 0.0,
    ) -> "PowerGroupPlan":
        """从有序通道集合构造计划；``settings`` 按 token 索引工作值。"""
        values = dict(settings or {})
        steps = []
        for item in channels:
            if isinstance(item, PowerChannelStep):
                steps.append(item)
                continue
            token = _channel_text(item)
            voltage, current = _setting_values(values.get(token))
            steps.append(
                PowerChannelStep(
                    channel=token,
                    voltage_v=voltage,
                    current_a=current,
                )
            )
        return cls(channels=tuple(steps), inter_channel_delay_s=inter_channel_delay_s)

    @property
    def tokens(self) -> tuple[str, ...]:
        return tuple(step.channel for step in self.channels)

    @property
    def is_empty(self) -> bool:
        return not self.channels


def _setting_values(value: object) -> tuple[float | None, float | None]:
    if value is None:
        return None, None
    if isinstance(value, Mapping):
        return value.get("voltage_v"), value.get("current_a")
    return getattr(value, "voltage_v", None), getattr(value, "current_a", None)


class PowerController:
    """按调用方给出的物理通道顺序执行一组电源的上电与关断。

    控制器把“已经发出开启命令”的通道都视为**可能已上电**，而不是只认返回成功的
    通道：设备可能已经执行了上电、但响应超时或在回程中断开。于是即使上电命令以
    异常结束，该通道也会进入关断集合；只有关闭命令成功返回后才会从集合中移除。
    """

    def __init__(
        self,
        power_supply,
        channels: Iterable[object],
        *,
        inter_channel_delay_s: float = 0.0,
        channel_delays_s: Mapping[str, float] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        channel_labels: Mapping[str, str] | None = None,
    ):
        tokens = tuple(_channel_text(channel) for channel in channels)
        if len(set(tokens)) != len(tokens):
            duplicated = sorted({token for token in tokens if tokens.count(token) > 1})
            raise ValueError("供电通道不能重复: " + ", ".join(duplicated))
        self.power_supply = power_supply
        self.channels = tokens
        self.inter_channel_delay_s = _delay_seconds(inter_channel_delay_s)
        #: 逐通道间隔：有值就覆盖组间隔；只保留本控制器实际会开启的通道。
        self.channel_delays_s = {
            str(name): _delay_seconds(value)
            for name, value in (channel_delays_s or {}).items()
            if str(name) in tokens
        }
        self.sleep_fn = sleep_fn or time.sleep
        # token -> 日志写法（``短标签/CHx``）：只影响日志与失败记录，不参与路由。
        self.channel_labels = {
            str(token): str(text) for token, text in (channel_labels or {}).items()
        }
        # 本次运行中可能已有输出的通道，按上电顺序保存。它是关断与重试的唯一依据。
        self._on_channels: list[str] = []
        # 本次运行真正发出过开启/关闭命令的通道；用于区分“没轮到”和“关掉了”。
        self._attempted_channels: list[str] = []
        self._power_attempted = False

    def channel_display(self, channel) -> str:
        """日志与失败记录里的通道名：``短标签/CHx``；没有登记时退回路由 token。"""
        token = str(channel)
        return self.channel_labels.get(token) or token

    @property
    def remaining_channels(self) -> tuple[str, ...]:
        """本次已尝试、但尚未确认关闭的通道；供调用方登记重试。"""
        return tuple(self._on_channels)

    @property
    def attempted_channels(self) -> tuple[str, ...]:
        """本次已经发出过动作命令的通道，按发出顺序；用于保留未轮到的旧输出。"""
        return tuple(self._attempted_channels)

    def _track_on(self, channel: str) -> None:
        if channel not in self._on_channels:
            self._on_channels.append(channel)
        if channel not in self._attempted_channels:
            self._attempted_channels.append(channel)

    def power_on(
        self,
        *,
        timeout_s: float = 10.0,
        check_cancelled: Callable[[], None] | None = None,
    ) -> tuple[str, ...]:
        """按计划顺序逐个开启；成功后等待组间隔，失败则停止并逆序清理。

        每次开启命令发出前先把通道登记为“可能已上电”。命令异常时，该通道与之前
        已尝试的通道一起逆序关断；关断失败的通道保留在 :attr:`remaining_channels`。
        """
        self._power_attempted = True
        powered: list[str] = []
        for index, channel in enumerate(self.channels):
            self._cancellation_guard(check_cancelled, timeout_s)
            self._track_on(channel)
            try:
                self.power_supply.set_output_enabled(channel, True, timeout_s=timeout_s)
            except Exception as error:
                failures = [FailureRecord(self.channel_display(channel), error)]
                failures.extend(
                    self._shutdown(tuple(self._on_channels), timeout_s=timeout_s)
                )
                raise PowerOnControlError(
                    "电源通道上电失败，已逆序尝试关闭本次可能已开启的通道", failures
                ) from error
            logger.info("打开电源通道 %s", self.channel_display(channel))
            powered.append(channel)
            self._cancellation_guard(check_cancelled, timeout_s)
            if index < len(self.channels) - 1:
                # 逐通道间隔优先；没有单独值时用组间隔。零间隔是显式的
                # “不等待”，不制造一个无意义的 sleep 调用。
                delay = self.channel_delays_s.get(channel)
                if delay is None:
                    delay = self.inter_channel_delay_s
                if delay > 0:
                    self.sleep_fn(delay)
                    self._cancellation_guard(check_cancelled, timeout_s)
        return tuple(powered)

    def power_off(
        self,
        *,
        timeout_s: float = 10.0,
        channels: Iterable[object] | None = None,
    ) -> tuple[str, ...]:
        """按实际尝试顺序逆序关断；单项失败不阻止其余通道。"""
        if channels is None:
            targets = tuple(self._on_channels) if self._power_attempted else self.channels
        else:
            targets = tuple(_channel_text(channel) for channel in channels)
            if len(set(targets)) != len(targets):
                duplicated = sorted(
                    {token for token in targets if targets.count(token) > 1}
                )
                raise ValueError("供电通道不能重复: " + ", ".join(duplicated))
        for channel in targets:
            self._track_on(channel)
        failures = self._shutdown(targets, timeout_s=timeout_s)
        if failures:
            raise PowerControlError("供电安全掉电存在失败", failures) from cause_of(failures)
        return tuple(reversed(targets))

    def _cancellation_guard(
        self,
        check_cancelled: Callable[[], None] | None,
        timeout_s: float,
    ) -> None:
        """取消检查；取消后先逆序关闭本次已尝试通道，再保留原取消异常。"""
        if check_cancelled is None:
            return
        try:
            check_cancelled()
        except Exception as cancellation:
            failures = self._shutdown(
                tuple(self._on_channels), timeout_s=timeout_s
            )
            if failures:
                detail = "; ".join(record.describe() for record in failures)
                logger.error("取消后的电源通道关闭失败: %s", detail)
                # 结构化保留：调用方按自己的取消类型处理后仍能拿到逐项失败记录。
                try:
                    setattr(cancellation, POWER_CLEANUP_FAILURES_ATTR, tuple(failures))
                except (AttributeError, TypeError):
                    # 极少数内建异常不接受动态属性；下面的 note 仍保留文本细节。
                    logger.warning(
                        "取消异常无法附加结构化电源清理记录: %s",
                        type(cancellation).__name__,
                    )
                add_note = getattr(cancellation, "add_note", None)
                if callable(add_note):
                    add_note(f"取消后的电源通道关闭失败: {detail}")
            raise

    def _shutdown(
        self,
        channels: Iterable[str],
        *,
        timeout_s: float,
    ) -> list[FailureRecord]:
        """逆序逐项尝试关闭；一项失败不阻止其余项，返回全部失败。

        只有关闭命令成功返回的通道才会从“可能已上电”集合中移除；失败通道保留，
        供调用方在后续清理中重试。
        """
        failures: list[FailureRecord] = []
        for channel in reversed(tuple(channels)):
            try:
                self.power_supply.set_output_enabled(channel, False, timeout_s=timeout_s)
            except Exception as error:
                logger.error(
                    "关闭电源通道失败 %s: %s", self.channel_display(channel), error
                )
                failures.append(FailureRecord(self.channel_display(channel), error))
            else:
                if channel in self._on_channels:
                    self._on_channels.remove(channel)
                logger.info("关闭电源通道 %s", self.channel_display(channel))
        return failures


__all__ = [
    "POWER_CLEANUP_FAILURES_ATTR",
    "PowerChannelStep",
    "PowerControlError",
    "PowerController",
    "PowerGroupPlan",
    "PowerOnControlError",
    "cleanup_failures_of",
]
