"""Adapter from instrument drivers/session to the measurement service port."""

from __future__ import annotations

from typing import Callable, Iterable, Mapping

from application.errors import FailureRecord
from infrastructure.logging.app_logging import get_logger

from .power_control import PowerController, PowerGroupPlan
from .safety import InstrumentSafetyError

logger = get_logger(__name__)


class PortMeasurementAdapter:
    """Expose the measurement service port over explicit instrument ports.

    ``dut_power_plan`` / ``driver_power_plan`` are the **only** source of
    channel selection and ordering: each :class:`PowerGroupPlan` list is the
    power-on order, and each step carries its working voltage/current.  The
    adapter never interprets a gate/drain role and never infers order from a
    channel name.

    ``qualified_power_channels`` marks channel names that must be routed as
    ``<supply>/<channel>`` tokens (multi-supply hardware), and
    ``emergency_channels`` fixes the exact channels a shutdown must switch off.
    Both are explicit construction inputs; nothing is inferred from attributes
    of the injected power supply.  When ``emergency_channels`` is omitted the
    adapter switches off the declared plan channels in reverse global order.

    ``power_channel_labels`` maps each routing token to its log text
    (``短标签/CHx``).  Routing and logging stay separate: the token is what the
    device receives, the label is what the operator reads.

    ``sleep_fn`` and ``cancellation_check`` are injected for deterministic
    tests and for the application cancellation token: the check is evaluated
    before each channel action, after it, and around the group delay.
    """

    def __init__(
        self,
        session,
        *,
        dut_power_plan: PowerGroupPlan | None = None,
        driver_power_plan: PowerGroupPlan | None = None,
        qualified_power_channels: bool = False,
        emergency_channels: Iterable[tuple[int, str]] | None = None,
        power_channel_labels: Mapping[str, str] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        cancellation_check: Callable[[], None] | None = None,
    ):
        self.session = session
        self.signal_gen = session.signal_generator
        self.signal_generator = session.signal_generator
        self.spectrum_analyzer = session.spectrum_analyzer
        self.power_supply = session.power_supply
        self.qualified_power_channels = bool(qualified_power_channels)
        self.sleep_fn = sleep_fn
        self.cancellation_check = cancellation_check
        # token -> 日志写法：只用于日志，``emergency_channels`` / ``_last_emergency_power_off``
        # 仍保存路由 token，安全记录必须能被程序再对一次。
        self.power_channel_labels = {
            str(token): str(text) for token, text in (power_channel_labels or {}).items()
        }
        self.dut_power_plan = _checked_plan("DUT", dut_power_plan)
        self.driver_power_plan = _checked_plan("driver", driver_power_plan)
        self._validate_power_plans()
        self.emergency_channels: tuple[tuple[int, str], ...] = (
            tuple(emergency_channels)
            if emergency_channels is not None
            else tuple(
                (0, token)
                for token in reversed(
                    self.driver_power_plan.tokens + self.dut_power_plan.tokens
                )
            )
        )
        self._session_cleaned = False
        self._active_power_groups: dict[str, tuple[str, ...]] = {}
        # 组本次尝试上电的先后顺序：阶段结算按它逆序关闭，先 DUT 后驱动功放。
        self._powered_group_order: list[str] = []
        # 急停成功关闭的通道；与 ``emergency_power_off_all`` 的返回值（失败项）区分。
        self._last_emergency_power_off_succeeded: list[tuple[int, str]] = []

    def _validate_power_plans(self) -> None:
        overlap = set(self.dut_power_plan.tokens) & set(self.driver_power_plan.tokens)
        if overlap:
            raise ValueError(
                f"DUT and driver power channels overlap: {', '.join(sorted(overlap))}"
            )

    def _controller(
        self, plan: PowerGroupPlan, channels: Iterable[str] | None = None
    ) -> PowerController:
        return PowerController(
            self.power_supply,
            tuple(channels) if channels is not None else plan.tokens,
            inter_channel_delay_s=plan.inter_channel_delay_s,
            sleep_fn=self.sleep_fn,
            channel_labels=self.power_channel_labels,
        )

    def set_frequency(self, frequency_ghz: float, *, timeout_s: float = 5.0) -> None:
        frequency_hz = float(frequency_ghz) * 1e9
        self.signal_generator.set_frequency_hz(frequency_hz, timeout_s=timeout_s)
        self.spectrum_analyzer.configure_center_frequency_hz(frequency_hz, timeout_s=timeout_s)

    def set_power(self, power_dbm: float, *, timeout_s: float = 5.0) -> None:
        was_enabled = bool(self.signal_generator.rf_enabled)
        if was_enabled:
            self.signal_generator.set_rf_enabled(False, timeout_s=timeout_s)
        # 功率写入成功后才恢复原来的 RF 状态。失败时保持 RF 关闭：在未确认的
        # 输出电平上重新打开 RF 会把功放置于未知驱动状态。
        self.signal_generator.set_power_dbm(power_dbm, timeout_s=timeout_s)
        if was_enabled:
            self.signal_generator.set_rf_enabled(True, timeout_s=timeout_s)

    def set_center_frequency(self, frequency_ghz: float, *, timeout_s: float = 5.0) -> None:
        self.spectrum_analyzer.configure_center_frequency_hz(float(frequency_ghz) * 1e9, timeout_s=timeout_s)

    def set_span(self, span_mhz: float, *, timeout_s: float = 5.0) -> None:
        self.spectrum_analyzer.configure_bandwidth_hz(float(span_mhz) * 1e6, timeout_s=timeout_s)

    def measure_power_with_average(self, *, timeout_s: float = 10.0) -> float:
        return self.spectrum_analyzer.measure_power_dbm(timeout_s=timeout_s)

    def rf_output_on(self, *, timeout_s: float = 5.0) -> None:
        self.session.set_rf_enabled(True, timeout_s=timeout_s)

    def rf_output_off(self, *, timeout_s: float = 5.0) -> None:
        self.session.set_rf_enabled(False, timeout_s=timeout_s)

    def start_measurement(self) -> None:
        """Enter the measurement state without enabling DUT power outputs."""
        self.session.start_measurement()

    def begin_stage(self) -> None:
        """下一阶段开始前确认同一端口可以复用。

        复用条件由会话自己裁定（已结算到 ``PREPARED`` 且 RF 关闭）；适配器只负责
        把失败翻译成阶段启动失败，不在这里重新打开资源。
        """
        if self._session_cleaned:
            raise RuntimeError("instrument session has already been cleaned")
        self.session.require_stage_ready()

    def end_stage(self, *, timeout_s: float = 30.0) -> None:
        """成功阶段结束后结算会话；失败时调用方必须关闭端口。

        适配器按本次尝试上电顺序的逆序关闭活动电源组；关断动作经会话执行，
        因此会话保留“可能已上电”的通道集合。任一组关闭失败都不结算会话，
        让协调器按异常阶段关闭端口，并可在后续 ``close_all()`` 里重试。
        """
        if self._session_cleaned:
            raise RuntimeError("instrument session has already been cleaned")
        failures = []
        for group in reversed(tuple(self._powered_group_order)):
            try:
                self._power_off_group(group, timeout_s=timeout_s)
            except Exception as error:  # 清理聚合：一组失败不阻止尝试其余组
                failures.append(FailureRecord("power_supplies", error))
        if failures:
            raise InstrumentSafetyError("阶段结束前的电源关闭失败", failures)
        self.session.end_stage(timeout_s=timeout_s)
        self._active_power_groups.clear()
        self._powered_group_order.clear()

    def emergency_power_off_all(self, *, timeout_s: float = 5.0) -> list[BaseException]:
        """Switch off the explicitly configured channels before closing.

        Returns the failures of the channels that could not be switched off.
        ``_last_emergency_power_off_succeeded`` records the ``(supply index,
        channel)`` pairs that did switch off, so a caller never has to read the
        return value backwards: a non-empty return means "these channels still
        need attention", an empty return means every configured channel was
        switched off successfully.

        ``emergency_channels`` already carry the explicit ``supply_id/CHx`` token
        of each declared plan channel; the log shows the supply short label and
        the channel the operator has to confirm on the front panel.
        """
        errors = []
        self._last_emergency_power_off_succeeded = []
        for index, channel in self.emergency_channels:
            try:
                self.power_supply.set_output_enabled(channel, False, timeout_s=timeout_s)
            except Exception as error:
                logger.error(
                    "紧急关闭电源通道失败 %s: %s", self._channel_display(channel), error
                )
                errors.append(error)
            else:
                logger.info("紧急关闭电源通道 %s", self._channel_display(channel))
                self._last_emergency_power_off_succeeded.append((index, channel))
        return errors

    def _channel_display(self, channel) -> str:
        """日志里的通道名：``短标签/CHx``；没有登记时退回路由 token。"""
        token = str(channel)
        return self.power_channel_labels.get(token) or token

    def set_voltage(self, supply_name: str, channel: str, voltage: float) -> None:
        self.power_supply.set_voltage_v(self._power_channel(supply_name, channel), voltage)

    def set_current(self, supply_name: str, channel: str, current: float) -> None:
        self.power_supply.set_current_limit_a(
            self._power_channel(supply_name, channel), current
        )

    def read_voltage(self, supply_name: str, channel: str) -> float:
        return self.power_supply.read_voltage_v(self._power_channel(supply_name, channel))

    def read_current(self, supply_name: str, channel: str) -> float:
        return self.power_supply.read_current_a(self._power_channel(supply_name, channel))

    def _power_channel(self, supply_name: str, channel: str) -> str:
        """Return the explicit physical channel token for the power supply."""
        if not self.qualified_power_channels:
            return channel
        if not isinstance(supply_name, str) or not supply_name.strip():
            raise ValueError("限定电源通道必须提供电源名称")
        return f"{supply_name}/{channel}"

    def setup_driver_amplifier_power(self) -> None:
        self._configure_plan(self.driver_power_plan)

    def setup_dut_power(self) -> None:
        self._configure_plan(self.dut_power_plan)

    def _configure_plan(self, plan: PowerGroupPlan) -> None:
        for step in plan.channels:
            if step.voltage_v is not None:
                self.power_supply.set_voltage_v(step.channel, step.voltage_v)
            if step.current_a is not None:
                self.power_supply.set_current_limit_a(step.channel, step.current_a)

    def power_on_driver(self) -> None:
        self._power_group_on("driver", self.driver_power_plan)

    def power_on_sequence(self) -> None:
        self._power_group_on("dut", self.dut_power_plan)

    def _power_group_on(self, group: str, plan: PowerGroupPlan) -> None:
        if plan.is_empty:
            return
        # 先登记整组计划：上电中途失败或取消时，后续清理仍会重试所有可能已有输出的
        # 通道；控制器内部的 best-effort 回滚只是第一道清理，不是最后一道。会话
        # 同步登记同样的通道，这样 close()/end_stage() 不依赖适配器内部状态。
        self._active_power_groups[group] = plan.tokens
        if group not in self._powered_group_order:
            self._powered_group_order.append(group)
        self.session.mark_powered(channels=plan.tokens)
        powered = self._controller(plan).power_on(check_cancelled=self.cancellation_check)
        # 成功返回时只保留实际开启的通道（正常路径与计划相同）。
        self._active_power_groups[group] = powered

    def power_off_driver(self) -> None:
        self._power_off_group("driver")

    def power_off_sequence(self) -> None:
        self._power_off_group("dut")

    def _power_off_group(self, group: str, *, timeout_s: float = 10.0) -> None:
        channels = self._active_power_groups.get(group)
        if not channels:
            return
        # 关断交给会话执行：会话同时维护“可能已上电”的全局集合，失败的通道会
        # 留在集合里，供下一次清理重试，而不是只存在于这个适配器实例中。
        self.session.power_off(channels=channels, timeout_s=timeout_s)
        self._active_power_groups.pop(group, None)
        if group in self._powered_group_order:
            self._powered_group_order.remove(group)

    def close_all(self, *, close_rf: bool = False, timeout_s: float = 30.0):
        if self._session_cleaned:
            return []
        errors = []
        if close_rf:
            try:
                self.session.set_rf_enabled(False, timeout_s=timeout_s)
            except Exception as error:
                errors.append(error)
        # 会话按全局上电顺序的逆序关闭所有“可能已上电”的通道（先 DUT、后驱放，
        # 组内再逆序）；不在这里重复遍历电源组，避免一次 close_all 里对同一通道
        # 发起两轮关断。会话关闭失败时保留通道记录，下一次 close_all 会重试。
        try:
            self.session.close(emergency=True, timeout_s=timeout_s)
        except Exception as error:
            errors.append(error)
        else:
            self._session_cleaned = True
            self._active_power_groups.clear()
            self._powered_group_order.clear()
        return errors

    def safe_shutdown(self):
        """Compatibility name for GUI callers during the migration."""
        return self.close_all(close_rf=True)


def _checked_plan(label: str, plan: PowerGroupPlan | None) -> PowerGroupPlan:
    if plan is None:
        return PowerGroupPlan()
    if not isinstance(plan, PowerGroupPlan):
        raise ValueError(
            f"{label} 上电计划必须是 PowerGroupPlan，收到 {type(plan).__name__}"
        )
    return plan
