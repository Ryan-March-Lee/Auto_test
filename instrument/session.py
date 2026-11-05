"""设备会话的唯一正式实现。

会话只协调 driver、供电控制和资源所有权；配置读取与 VISA 资源创建由
``measurement_factory`` 完成。外部注入资源默认不由会话关闭。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from application.errors import CleanupError, cause_of

from .ports import InstrumentState
from .power_control import PowerController
from .safety import InstrumentSafetyError, shutdown_instruments


def _power_tokens(channels: Iterable[object]) -> tuple[str, ...]:
    """把显式通道序列规范化成物理 token；拒绝空值与重复项。"""
    tokens = tuple(str(channel) for channel in channels)
    if any(not token.strip() for token in tokens):
        raise ValueError("会话的供电通道不能为空")
    if len(set(tokens)) != len(tokens):
        duplicated = sorted({token for token in tokens if tokens.count(token) > 1})
        raise ValueError("会话的供电通道不能重复: " + ", ".join(duplicated))
    return tokens


class InstrumentSessionError(CleanupError):
    """设备会话清理失败；``failures`` 逐项记录失败动作。"""


class ManagedInstrumentSession:
    """统一管理设备生命周期、安全动作和资源所有权。"""

    def __init__(self, signal_generator, spectrum_analyzer, power_supply,
                 power_channels: Iterable[str] | None = None, *,
                 power_channel_labels: Mapping[str, str] | None = None,
                 owns_signal_generator: bool = True,
                 owns_spectrum_analyzer: bool = True,
                 owns_power_supply: bool = True,
                 can_disable_rf: bool = True,
                 can_disable_power: bool = True,
                 sleep_fn=None):
        self.signal_generator = signal_generator
        self.spectrum_analyzer = spectrum_analyzer
        self.power_supply = power_supply
        # 会话管理的是**物理通道序列**，不是角色映射。顺序只在显式传入计划时由
        # 计划决定；这里只保留一个可独立测试的生命周期级通道列表。
        self.power_channels = _power_tokens(power_channels or ())
        # 路由 token -> 日志写法（``短标签/CHx``）：只影响日志与失败记录，不参与路由。
        self.power_channel_labels = dict(power_channel_labels or {})
        self.owns_signal_generator = owns_signal_generator
        self.owns_spectrum_analyzer = owns_spectrum_analyzer
        self.owns_power_supply = owns_power_supply
        self.can_disable_rf = can_disable_rf
        self.can_disable_power = can_disable_power
        self.state = InstrumentState.CREATED
        self._attempted = []
        self._connected = []
        self._sleep_fn = sleep_fn
        # 本次已尝试、可能已有输出的物理通道；关断与生命周期确认都以它为准，
        # 不用角色反查。只有关闭命令成功返回后才会从这里移除。
        self._powered_channels: tuple[str, ...] = ()

    def validate(self, *, timeout_s: float = 10.0) -> None:
        if self.state is not InstrumentState.CREATED:
            raise ValueError(f"cannot validate from {self.state.value}")
        self.state = InstrumentState.VALIDATED

    def connect(self, *, timeout_s: float = 30.0) -> None:
        if self.state is not InstrumentState.VALIDATED:
            raise ValueError(f"cannot connect from {self.state.value}")
        try:
            for device in (self.signal_generator, self.spectrum_analyzer, self.power_supply):
                if device not in self._attempted:
                    self._attempted.append(device)
                device.connect(timeout_s=timeout_s)
                if device not in self._connected:
                    self._connected.append(device)
        except Exception as error:
            try:
                self.close(timeout_s=timeout_s)
            except Exception as cleanup_error:
                raise error from cleanup_error
            raise
        self.state = InstrumentState.CONNECTED

    def prepare(self, *, frequency_hz=None, bandwidth_hz=None,
                timeout_s: float = 30.0) -> None:
        if self.state is not InstrumentState.CONNECTED:
            raise ValueError(f"cannot prepare from {self.state.value}")
        try:
            if frequency_hz is not None:
                self.signal_generator.set_frequency_hz(frequency_hz, timeout_s=timeout_s)
                self.spectrum_analyzer.configure_center_frequency_hz(frequency_hz, timeout_s=timeout_s)
            if bandwidth_hz is not None:
                self.spectrum_analyzer.configure_bandwidth_hz(bandwidth_hz, timeout_s=timeout_s)
            # Drivers use this flag to guard RF enable.  Preparation is a
            # lifecycle state even when the caller leaves optional frequency
            # and bandwidth configuration to a later measurement step.
            if hasattr(self.signal_generator, "prepared"):
                self.signal_generator.prepared = True
            self.state = InstrumentState.PREPARED
        except Exception as error:
            try:
                self.close(timeout_s=timeout_s)
            except Exception as cleanup_error:
                raise error from cleanup_error
            raise

    def _register_powered(self, channels: Iterable[object]) -> None:
        """把**可能已有输出**的通道并入本次运行记录，保持首次开启顺序。"""
        tokens = _power_tokens(channels)
        merged = list(self._powered_channels)
        for token in tokens:
            if token not in merged:
                merged.append(token)
        self._powered_channels = tuple(merged)

    def _sync_after_off(
        self, attempted: Iterable[str], remaining: Iterable[str]
    ) -> None:
        """按关断结果更新记录：成功关闭的移除，未确认关闭的保留。

        ``attempted`` 是本次真正发出过动作命令的通道，``remaining`` 是其中关闭
        失败的通道。不在 ``attempted`` 里的旧通道代表“这一轮还没轮到”，必须原样
        保留，不能因为不在本次计划里就忘记它们可能仍有输出。
        """
        attempted_set = set(attempted)
        remaining_set = set(remaining)
        kept = [
            channel
            for channel in self._powered_channels
            if channel not in attempted_set or channel in remaining_set
        ]
        for channel in remaining:
            if channel not in kept:
                kept.append(channel)
        self._powered_channels = tuple(kept)

    def power_on(self, *, channels: Iterable[str] | None = None,
                 inter_channel_delay_s: float = 0.0,
                 timeout_s: float = 10.0) -> None:
        """按物理通道序列逐个上电；失败会停止后续通道并逆序清理。

        会话只保留生命周期状态；通道顺序由调用方给出的序列决定，不接受
        role/gate/drain 参数，也不从通道名推断顺序。上电命令发出后即视为
        “可能已上电”：失败路径把未能确认关闭的通道保留在会话里，``close()``
        或后续 ``power_off()`` 会重试关断。
        """
        if self.state not in (InstrumentState.PREPARED, InstrumentState.POWERED):
            raise ValueError(f"cannot power on from {self.state.value}")
        targets = tuple(channels) if channels is not None else self.power_channels
        controller = PowerController(
            self.power_supply,
            targets,
            inter_channel_delay_s=inter_channel_delay_s,
            sleep_fn=self._sleep_fn,
            channel_labels=self.power_channel_labels,
        )
        try:
            powered = controller.power_on(timeout_s=timeout_s)
        except Exception as error:
            self._sync_after_off(
                controller.attempted_channels, controller.remaining_channels
            )
            try:
                self.close(timeout_s=timeout_s)
            except Exception as cleanup_error:
                raise error from cleanup_error
            raise
        self._register_powered(powered)
        self.state = InstrumentState.POWERED

    def power_off(self, *, channels: Iterable[str] | None = None,
                  timeout_s: float = 10.0) -> None:
        if self.power_supply not in self._connected:
            return
        targets = (
            tuple(channels)
            if channels is not None
            else tuple(self._powered_channels)
        )
        if not targets:
            return
        controller = PowerController(
            self.power_supply,
            targets,
            sleep_fn=self._sleep_fn,
            channel_labels=self.power_channel_labels,
        )
        try:
            controller.power_off(timeout_s=timeout_s)
        except Exception:
            self._sync_after_off(targets, controller.remaining_channels)
            raise
        self._sync_after_off(targets, ())

    def mark_powered(self, *, channels: Iterable[str] | None = None) -> None:
        """登记调用方已按显式通道计划打开（或可能打开）的输出。

        适配器按计划执行通道顺序与失败回滚；会话只保存生命周期状态和本次运行
        可能已有输出的物理通道，因此这里不接受 roles/gate/drain 参数，也不重新
        执行任何输出动作。``channels`` 省略时退回会话构造时的通道序列；没有受管
        通道就是“没有需要会话确认的输出”。
        """
        if self.state not in (InstrumentState.PREPARED, InstrumentState.POWERED):
            raise ValueError(f"cannot mark powered from {self.state.value}")
        self._register_powered(self.power_channels if channels is None else channels)
        self.state = InstrumentState.POWERED

    def start_measurement(self) -> None:
        if self.state not in (InstrumentState.PREPARED, InstrumentState.POWERED):
            raise ValueError(f"cannot measure from {self.state.value}")
        self.state = InstrumentState.MEASURING

    def require_stage_ready(self) -> None:
        """校验下一阶段开始前的复用前置条件。

        只有上一阶段已经结算（``PREPARED``）且 RF 已关闭时才允许继续复用同一
        端口；失败时抛 ``ValueError``，由调用方保留本阶段错误而不是重新打开资源。
        """
        if self.state is not InstrumentState.PREPARED:
            raise ValueError(
                f"仪器会话不在可复用阶段（当前 {self.state.value}）；"
                "请先结束上一阶段或重新连接仪器"
            )
        if self.signal_generator.rf_enabled:
            raise ValueError("RF 输出尚未关闭，禁止开始下一阶段")

    def _stage_rf_shutdown(self, timeout_s: float):
        """返回阶段结算用的 RF 关闭动作。

        ``can_disable_rf=False`` 表示会话不能发出 RF 关闭命令；此时无法确认 RF 已
        关闭，结算必须失败。把“没有动作”当成清理成功会让未确认安全的端口进入可
        复用状态。
        """
        if not self.can_disable_rf:
            def _cannot_confirm() -> None:
                raise RuntimeError("会话不具备关闭 RF 的能力，无法确认 RF 已关闭")

            return _cannot_confirm
        return lambda: self.set_rf_enabled(False, timeout_s=timeout_s)

    def _stage_power_shutdown(self, timeout_s: float):
        """返回阶段结算用的电源关闭动作。

        ``can_disable_power=False`` 且会话确实管理着已连接的电源输出时同样无法确认
        关闭；只有“没有受管输出”才不需要这个动作。
        """
        if not self.can_disable_power:
            if not self.power_channels and not self._powered_channels:
                # 没有受管通道就没有需要确认的输出。
                return None

            def _cannot_confirm() -> None:
                raise RuntimeError("会话不具备关闭电源输出的能力，无法确认输出已关闭")

            return _cannot_confirm
        return lambda: self.power_off(timeout_s=timeout_s)

    def end_stage(self, *, timeout_s: float = 30.0) -> None:
        """结束一个测量阶段并把会话带回 ``PREPARED``。

        成功阶段结束后端口保留给下一阶段复用：只有 RF 和已连接电源的输出都确认
        关闭时才进入 ``PREPARED``。任一动作失败（包括会话本身没有关闭能力、无法
        确认输出已关闭）都把会话留在 ``STOPPING``（不可复用），并以
        :class:`InstrumentSessionError` 聚合上报；调用方必须关闭端口并标记需重新
        连接，不能靠放宽状态前置条件继续下一阶段。

        确认口径是“关闭命令成功返回”，不额外回读 ``OUTP?``/RF 状态：生产测量服务
        在阶段结束时已经执行过同样的安全清理，这里复核的是同一组动作没有失败。
        """
        if self.state not in (
            InstrumentState.PREPARED,
            InstrumentState.POWERED,
            InstrumentState.MEASURING,
        ):
            raise ValueError(f"cannot end stage from {self.state.value}")
        try:
            shutdown_instruments(
                rf_off=self._stage_rf_shutdown(timeout_s),
                power_off=self._stage_power_shutdown(timeout_s),
            )
        except InstrumentSafetyError as error:
            # 未确认关闭的会话不能复用：STOPPING 会让 power_on /
            # start_measurement / end_stage 的前置条件显式失败，关闭端口是唯一
            # 允许的收尾动作。
            self.state = InstrumentState.STOPPING
            raise InstrumentSessionError(
                "instrument stage cleanup failed", error.failures
            ) from cause_of(error.failures)
        self.state = InstrumentState.PREPARED

    def set_rf_enabled(self, enabled: bool, *, timeout_s: float = 5.0) -> None:
        # RF 只要求已完成准备：线损阶段不打开 DUT 供电，但仍要启用 RF 测量通路。
        # 电源输出关闭由阶段开始（require_stage_ready）和结算（end_stage）分别保证。
        if enabled and self.state not in (
            InstrumentState.PREPARED,
            InstrumentState.POWERED,
            InstrumentState.MEASURING,
        ):
            raise ValueError("RF may only be enabled after preparation")
        self.signal_generator.set_rf_enabled(enabled, timeout_s=timeout_s)

    def stop(self, *, emergency=False, timeout_s: float = 30.0) -> None:
        if not emergency and self.state not in (InstrumentState.POWERED, InstrumentState.MEASURING):
            raise ValueError(f"ordinary stop is invalid from {self.state.value}")
        self.close(emergency=emergency, timeout_s=timeout_s)

    def close(self, *, emergency=False, timeout_s: float = 30.0) -> None:
        if self.state is InstrumentState.CLEANED:
            return
        self.state = InstrumentState.STOPPING
        def close_owned(*, rf_safe=True, power_safe=True):
            errors = []
            owned = []
            if self.owns_spectrum_analyzer and self.spectrum_analyzer in self._attempted:
                owned.append(self.spectrum_analyzer)
            # 电源输出失败后仍关闭连接；下一次安全操作不能依赖一个
            # 已知处于异常状态的 VISA 连接。RF 关闭失败则保留信号源，
            # 以便现场重试关闭 RF。
            if self.owns_power_supply and self.power_supply in self._attempted:
                owned.append(self.power_supply)
            if rf_safe and self.owns_signal_generator and self.signal_generator in self._attempted:
                owned.append(self.signal_generator)
            for device in owned:
                try:
                    device.close(timeout_s=timeout_s)
                except Exception as error:  # 单个设备关闭失败不阻止释放其余设备
                    errors.append(error)
                else:
                    if device in self._connected:
                        self._connected.remove(device)
                    if device in self._attempted:
                        self._attempted.remove(device)
            return errors

        try:
            shutdown_instruments(
                rf_off=(lambda: self.set_rf_enabled(False, timeout_s=timeout_s))
                if self.can_disable_rf else None,
                power_off=lambda: self.power_off(timeout_s=timeout_s)
                if self.can_disable_power else None,
                close=close_owned,
                emergency=emergency,
            )
        except InstrumentSafetyError as error:
            # 安全清理只抛出这一种聚合异常；动作名与原始异常已经逐项记录。
            raise InstrumentSessionError(
                "instrument cleanup failed", error.failures
            ) from cause_of(error.failures)
        self.state = InstrumentState.CLEANED
