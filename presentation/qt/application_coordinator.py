"""GUI 运行协调器：持有配置状态、运行契约、仪器连接和测量 controller。

主窗口只负责窗口生命周期、页面注册和信号连接；配置读写、运行创建/加载、仪器
连接以及端口清理都在这里完成。

协调器是 **Qt 表现层** 组件，但不接触任何具体控件：它只通过
:class:`ConfigurationPagePort` 这个窄接口（校验、组装、回填、运行状态、驱动模式）
与配置页交互，并通过注入的日志/告警/错误回调与窗口交互。这样它既不需要
知道控件名字，也可以用纯 Python 替身单独测试。

归属（见 `docs/配置领域词汇与模块归属.md`）：presentation controller。它持有呈现层配置状态并
编排 application 用例（配置保存、运行创建/加载、仪器端口生命周期），不读控件、不定义业务规则；
规则来源仍是 :mod:`application.run_service` 与 :mod:`domain.configuration.rules`；
正式配置文档由 :class:`application.configuration_authoring.ConfigurationAuthoringService`
生成（GUI 重构步骤 4），协调器只负责调用与保存。
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol, runtime_checkable

from app.gui_runtime import load_prepared_run, prepare_configuration
from application.configuration_authoring import (
    ConfigurationAuthoringService,
    ConfigurationEditingDocument,
)
from application.gui_data_service import GuiDataService
from application.run_service import (
    RunNotDiscardable,
    RunNotResumable,
    discard_unstarted_run,
)
from domain.run.models import normalize_artifact_path

from .composition import build_gui_data_service, default_paths
from .config_projection import ConfigBackfillError
from .measurement_controller import MeasurementController
from .measurement_state import MeasurementKind, MeasurementStatus
from .measurement_worker_factories import build_measurement_worker_factories
from .realtime_buffer import RealtimeMeasurementBuffer
from .workers import InstrumentWorker

_MEASUREMENT_KINDS = frozenset(
    {
        MeasurementKind.CABLE_LOSS,
        MeasurementKind.DRIVER_MAPPING,
        MeasurementKind.AMPLIFIER,
    }
)

_CONFIG_PAGE_MEMBERS = (
    "validate",
    "build_config",
    "apply_config",
    "set_run_status",
    "driver_mode_requested",
)


class RunFlowState(str, Enum):
    """运行流程状态；只由协调器写入，页面只渲染状态文案。

    ``FINALIZING`` 覆盖“待提交运行已冻结、正在做只读连接检查”的完整窗口。连接
    失败时不会退化成“运行已提交但没有端口”：协调器要么把 ``_pending_run`` 与
    新端口一起提交为 ``CONNECTED``，要么补偿 ``_pending_run`` 并回到
    ``RECONNECT_REQUIRED``/``CONFIGURING``；补偿或清理失败则停在
    ``MANUAL_CLEANUP_REQUIRED``，阻止新的最终动作与测量。
    """

    DEVICE_SELECTION = "device_selection"
    CONFIGURING = "configuring"
    FINALIZING = "finalizing"
    CONNECTED = "connected"
    RECONNECT_REQUIRED = "reconnect_required"
    MANUAL_CLEANUP_REQUIRED = "manual_cleanup_required"


@runtime_checkable
class ConfigurationPagePort(Protocol):
    """配置页必须向协调器提供的窄接口。

    协调器只依赖这五个能力，不读取任何控件；页面内部如何布局、控件叫什么名字与
    协调器无关。
    """

    def validate(self) -> bool:
        """校验当前表单；返回是否可以通过。"""

    def build_config(self, base: Any = None) -> dict[str, Any]:
        """按当前控件值组装配置，``base`` 为组装基线。"""

    def apply_config(self, config: dict[str, Any]) -> None:
        """把冻结配置回填到控件；失败时抛出 ``ConfigBackfillError``。"""

    def set_run_status(self, text: str) -> None:
        """显示当前运行状态文本。"""

    def driver_mode_requested(self) -> bool:
        """返回用户当前选择的驱动功放模式。"""


def close_instrument_port(port: Any) -> tuple[BaseException, ...]:
    """关闭仪器端口并返回**未能完成的清理错误**。

    空元组表示已确认释放。设备适配器的 ``close_all()`` 会返回逐项错误而不是保证
    抛出，因此只判断异常会把“RF 未确认关闭 / 电源仍在输出”当成成功，这里必须
    把返回值一并收敛成错误元组；调用方据此决定是否保留端口引用以便重试。
    """
    if port is None:
        return ()
    close_all = getattr(port, "close_all", None)
    if close_all is not None:
        try:
            return _cleanup_errors(close_all(close_rf=True))
        except Exception as failure:  # noqa: BLE001 - 清理异常必须进入记录，不能让界面崩溃
            return (failure,)
    safe_shutdown = getattr(port, "safe_shutdown", None)
    if safe_shutdown is not None:
        try:
            return _cleanup_errors(safe_shutdown())
        except Exception as failure:  # noqa: BLE001 - 同上
            return (failure,)
    # 没有关闭入口：没有已知资源需要释放，但也如实返回“无需释放”。
    return ()


def settle_instrument_stage(port: Any) -> tuple[BaseException, ...] | None:
    """把成功结束的阶段端口带回可复用状态。

    返回空元组表示已确认可以复用；非空元组表示阶段结算失败；``None`` 表示端口
    没有阶段结算入口——无法确认输出已经关闭，调用方必须按完整关闭处理，绝不能把
    “没有报错”当成可以复用。
    """
    if port is None:
        return ()
    end_stage = getattr(port, "end_stage", None)
    if not callable(end_stage):
        return None
    try:
        end_stage()
    except Exception as failure:  # noqa: BLE001 - 结算失败必须进入关闭流程，不能从 Qt 槽抛出
        return (failure,)
    return ()


def _cleanup_errors(result: Any) -> tuple[BaseException, ...]:
    """把 ``close_all()`` 的返回值收敛成错误元组（``None`` 视为无错误）。"""
    if result is None:
        return ()
    if isinstance(result, BaseException):
        return (result,)
    try:
        items = tuple(result)
    except TypeError:
        return (RuntimeError(f"端口关闭返回了无法判定的结果: {result!r}"),)
    errors: list[BaseException] = []
    for item in items:
        if item is None:
            continue
        errors.append(item if isinstance(item, BaseException) else RuntimeError(str(item)))
    return tuple(errors)


def describe_cleanup_errors(errors: Iterable[BaseException]) -> str:
    """把清理错误列表写成一行可读文本，供日志与界面提示使用。"""
    parts = []
    for error in errors:
        detail = str(error)
        parts.append(
            f"{type(error).__name__}: {detail}" if detail else type(error).__name__
        )
    return "；".join(parts)


class ApplicationCoordinator:
    """呈现层运行协调器：单一持有配置、运行契约和仪器端口。

    ``log`` / ``warn`` / ``error`` 是窗口提供的用户可见反馈通道，必须显式注入：
    协调器的失败路径不允许静默，缺少反馈通道时在构造阶段就失败，而不是在运行
    失败时丢消息。
    """

    def __init__(
        self,
        *,
        log: Callable[[str], Any],
        warn: Callable[[str, str], Any],
        error: Callable[[str], Any],
        paths: Any | None = None,
        config_path: Any = None,
        gui_data_service: GuiDataService | None = None,
        controller: MeasurementController | None = None,
        instrument_worker_factory: Callable[[Any], Any] = InstrumentWorker,
    ) -> None:
        for name, callback in (("log", log), ("warn", warn), ("error", error)):
            if not callable(callback):
                raise TypeError(f"{name} 必须是可调用对象")
        self.paths = paths or default_paths()
        self.config_path = Path(config_path or self.paths.config_file)
        self.gui_data_service: GuiDataService = gui_data_service or build_gui_data_service(
            self.paths, config_path=self.config_path
        )
        self.controller: MeasurementController = controller or MeasurementController(
            build_measurement_worker_factories(), self.measurement_config_path
        )
        # 阶段在 worker 创建前失败时没有线程结束事件；端口所有者必须自己收尾。
        self.controller.signals.stage_start_failed.connect(
            self._on_stage_start_failed
        )
        self.realtime_buffer = RealtimeMeasurementBuffer()
        self._log = log
        self._warn = warn
        self._error = error
        self._instrument_worker_factory = instrument_worker_factory
        self.config_page: ConfigurationPagePort | None = None
        self.config: dict[str, Any] = {}
        self.prepared_run: Any = None
        self.instrument_ctrl: Any = None
        self.instrument_worker: Any = None
        self.last_save_error: Exception | None = None
        # 最终化事务：待提交运行在连接成功前不写进 prepared_run，也不交给测量页。
        self._pending_run: Any = None
        self._reconnecting = False
        self._state = RunFlowState.DEVICE_SELECTION
        # 清理失败的端口进入未决清单：绝不因为一次失败就丢掉引用，现场可重试。
        self._unreleased_ports: list[Any] = []

    # ---- 运行流程状态 ----

    @property
    def state(self) -> RunFlowState:
        """当前运行流程状态；协调器是唯一写入方。"""
        return self._state

    def _set_state(self, state: RunFlowState) -> None:
        if state is self._state:
            return
        previous = self._state
        self._state = state
        self._log(f"运行流程状态: {previous.value} → {state.value}")
        self.run_status_changed(self.run_status_text())

    def begin_device_selection(self) -> None:
        """进入仪表配置页：仍在查找/选择设备。"""
        self._set_state(RunFlowState.DEVICE_SELECTION)

    def begin_configuration(self) -> None:
        """设备绑定已应用：进入参数配置；已提交运行不受影响。"""
        if self._state is RunFlowState.DEVICE_SELECTION:
            self._set_state(RunFlowState.CONFIGURING)

    def _measurement_is_active(self) -> bool:
        """测量线程是否正在使用当前端口；只读测量 controller 的状态。

        测量期间替换端口会让正在运行的用例失去设备，因此所有会保存配置、
        释放旧端口或重新打开仪器的入口都必须在动作前拒绝。
        """
        state = getattr(self.controller, "state", None)
        return getattr(state, "is_active", False) is True

    def _refuse_while_measuring(self, title: str) -> bool:
        """测量进行中时给出统一提示并返回 ``True``（调用方应立即失败）。"""
        if not self._measurement_is_active():
            return False
        message = "测量正在运行；请先结束或停止测量，再重新配置或连接仪器"
        self._log(message)
        self._warn(title, message)
        return True

    # ---- 配置页适配 ----

    def bind_config_page(self, page: Any) -> None:
        """登记配置页；页面只需实现 :class:`ConfigurationPagePort` 的五个能力。"""
        missing = [
            name for name in _CONFIG_PAGE_MEMBERS if not callable(getattr(page, name, None))
        ]
        if missing:
            raise TypeError("配置页缺少协调器所需能力: " + ", ".join(missing))
        self.config_page = page
        self.run_status_changed(self.run_status_text())

    def _require_config_page(self) -> ConfigurationPagePort:
        page = self.config_page
        if page is None:
            raise RuntimeError("未登记配置页，无法读写界面配置")
        return page

    # ---- 配置状态 ----

    def load_configuration(self) -> dict[str, Any]:
        """通过 GUI 数据服务读取正式配置；读取失败时给出提示并保持空配置。"""
        try:
            self.config = dict(self.gui_data_service.configuration.load())
        except Exception as failure:  # noqa: BLE001 - 读取失败必须让用户看到，而不是带着旧配置运行
            self._warn("配置加载", f"无法加载配置文件: {failure}")
            self.config = {}
        return self.config

    def build_configuration_from_ui(self) -> dict[str, Any]:
        """按配置页当前控件值组装完整配置；自身配置是唯一组装基线。"""
        page = self._require_config_page()
        return page.build_config(self.config)

    def update_configuration_from_ui(self) -> dict[str, Any]:
        """用当前控件值替换协调器持有的配置副本。"""
        self.config = self.build_configuration_from_ui()
        self._push_config_to_page()
        return self.config

    def _push_config_to_page(self) -> None:
        """让配置页持有与协调器相同的配置副本。

        ``config`` 不在 :class:`ConfigurationPagePort` 的能力清单里（它会让
        ``runtime_checkable`` 协议退化为数据协议），但页面组装与保存都以它作为基线，
        因此协调器写完配置后仍要同步这一份副本。
        """
        page = self.config_page
        if page is not None:
            page.config = self.config

    def save_configuration(self) -> bool:
        """把当前配置写为正式配置；失败时保留 :attr:`last_save_error`。

        这是一次完整的“编写 + 保存”：先在应用边界用
        :class:`application.configuration_authoring.ConfigurationAuthoringService`
        生成正式文档（校验失败就不会写盘），再经 ``ConfigurationPort.save`` 交给配置
        仓储的原子写入。界面不接触文件、序列化格式或临时文件，运行快照也不走这里。
        """
        try:
            self.config = ConfigurationAuthoringService().author_document(
                ConfigurationEditingDocument.from_mapping(self.config)
            )
            self._push_config_to_page()
            self.gui_data_service.configuration.save(self.config)
        except Exception as failure:  # noqa: BLE001 - 界面需要原始原因，写入失败不得静默
            self.last_save_error = failure
            return False
        self.last_save_error = None
        return True

    # ---- 运行契约 ----

    def measurement_config_path(self) -> str:
        """返回硬件连接与测量应使用的配置路径。

        运行契约运行固定返回运行目录内冻结的 ``inputs/hardware_config.json``，并在
        返回前校验该快照仍然可用（存在、未被删除或篡改）。**尚未创建运行时明确失败**：
        没有运行就没有本次运行的仪器地址，回退到全局可变配置会让界面用与当前运行无关
        的地址连接硬件。

        运行契约不可用（未创建运行、快照缺失、被删除、校验失败，或对象没有校验入口）
        时直接抛出 :class:`RunNotResumable`。
        """
        run = self.prepared_run
        if run is None:
            raise RunNotResumable(
                "尚未创建运行，不能连接仪器；请先在“仪器配置”页点击“完成配置并连接仪器”"
            )
        resolver = getattr(run, "hardware_connection_path", None)
        if resolver is None:
            raise RunNotResumable(
                "当前运行契约不可用，不能连接仪器；请重新创建或重新加载运行"
            )
        return str(resolver())

    def resolve_measurement_run(self):
        """返回当前运行契约，供测量页面消费；不可用时提示并返回 ``None``。

        ``_pending_run`` 永远不在这里出现：待提交运行只有在最终化事务成功、端口
        同时提交后才会变成 ``prepared_run``。
        """
        if self.has_unreleased_ports() or self._state is RunFlowState.MANUAL_CLEANUP_REQUIRED:
            self._error(
                "存在未完成的仪器清理：请在现场确认仪器已断开后重试清理，"
                "清理完成前不能开始测量"
            )
            return None
        if self.prepared_run is None:
            self._error("尚未创建运行：请先在“仪器配置”页点击“完成配置并连接仪器”")
            return None
        if self.instrument_ctrl is None:
            self._error("请先重新连接仪器；上一次连接或测量结束后端口已安全释放")
            return None
        try:
            self.measurement_config_path()
        except RunNotResumable as failure:
            self._error(str(failure))
            return None
        return self.prepared_run

    # ---- 最终化事务 ----

    def finalize_configuration(
        self, bind_signals: Callable[[Any], None] | None = None
    ) -> bool:
        """最终化事务入口：保存 → 释放旧端口 → 冻结待提交运行 → 启动连接检查。

        返回是否已经启动事务。连接结果是异步的：成功后由
        :meth:`commit_finalization` 原子提交 ``_pending_run`` 与新端口；失败由
        :meth:`fail_finalization` / :meth:`cancel_finalization` 补偿。任何同步失败
        都不会切换 ``prepared_run``，也不会留下“已提交运行 + 未连接端口”。

        已有运行但端口丢失（``RECONNECT_REQUIRED``）时，同一入口只重连已有运行：
        不保存配置、不创建新运行。
        """
        if self._refuse_while_measuring("无法完成配置"):
            return False
        if self._state is RunFlowState.FINALIZING:
            self._log("最终动作正在进行，忽略重复提交")
            return False
        if self.has_unreleased_ports() or self._state is RunFlowState.MANUAL_CLEANUP_REQUIRED:
            self._set_state(RunFlowState.MANUAL_CLEANUP_REQUIRED)
            self._warn(
                "无法完成配置",
                "存在未完成的仪器清理；请在现场确认仪器已断开并重试清理后再提交",
            )
            return False
        if self._should_reconnect():
            return self._start_reconnect(bind_signals)
        pending = self.prepare_pending_run()
        if pending is None:
            return False
        if not self._start_connection_worker(pending, bind_signals):
            if self._state is RunFlowState.FINALIZING:
                self._abort_finalization("连接检查未能启动")
            return False
        return True

    def prepare_pending_run(self):
        """事务步骤：校验、保存、释放旧端口并冻结待提交运行（不提交）。

        成功时 ``_pending_run`` 就绪、状态为 ``FINALIZING``，但 ``prepared_run``
        保持不变；连接检查成功前，测量页无法消费这份运行。
        """
        if self._refuse_while_measuring("无法完成配置"):
            return None
        if self._pending_run is not None:
            self._log("已存在未提交的待提交运行；请先完成或取消当前最终动作")
            return None
        if self.has_unreleased_ports() or self._state is RunFlowState.MANUAL_CLEANUP_REQUIRED:
            self._set_state(RunFlowState.MANUAL_CLEANUP_REQUIRED)
            self._warn(
                "无法完成配置",
                "存在未完成的仪器清理；请在现场确认仪器已断开并重试清理后再提交",
            )
            return None
        page = self.config_page
        if page is None or not page.validate():
            self._log("配置校验失败：未保存配置，也未创建运行")
            return None
        if not self._save_configuration_for_finalize():
            return None
        if not self._release_previous_port_for_finalize():
            return None
        pending = self._freeze_pending_run()
        if pending is None:
            return None
        self._pending_run = pending
        self._set_state(RunFlowState.FINALIZING)
        self._log(
            f"已冻结待提交运行 {pending.run_id}，运行目录: {pending.run_directory}"
        )
        return pending

    def _save_configuration_for_finalize(self) -> bool:
        """步骤：从页面读取完整配置并写入正式配置。失败不释放旧端口。"""
        self.update_configuration_from_ui()
        if not self.save_configuration():
            self._log(f"配置保存失败: {self.last_save_error}")
            self._warn("创建运行失败", f"配置保存失败: {self.last_save_error}")
            return False
        mark_clean = getattr(self.config_page, "mark_clean", None)
        if callable(mark_clean):
            mark_clean()
        return True

    def _release_previous_port_for_finalize(self) -> bool:
        """步骤：释放上一次连接的端口；失败进入未决清理并阻断本次事务。"""
        if self.release_instrument_for_new_run():
            return True
        self._set_state(RunFlowState.MANUAL_CLEANUP_REQUIRED)
        self._log(
            "上一次连接的仪器未能安全释放；当前运行保持不变，"
            "端口已进入未决清理清单"
        )
        self._warn(
            "创建运行未生效",
            "上一次连接的仪器未能完成安全清理；请在现场确认仪器已断开后重试",
        )
        return False

    def _freeze_pending_run(self):
        """步骤：把已保存配置冻结为运行目录快照；失败不切换当前运行。"""
        try:
            return prepare_configuration(
                str(self.config_path),
                paths=self.paths,
                hardware_config=self.config,
            )
        except Exception as failure:  # noqa: BLE001 - 建运行失败必须回到界面，且不得切换运行
            self._log(f"创建运行失败: {failure}")
            self._warn("创建运行失败", str(failure))
            # 旧端口已在冻结前释放；旧运行若还在，只能通过重新连接继续。
            if self.prepared_run is not None:
                self._set_state(RunFlowState.RECONNECT_REQUIRED)
            return None

    def _should_reconnect(self) -> bool:
        return (
            self._state is RunFlowState.RECONNECT_REQUIRED
            and self.prepared_run is not None
            and self.instrument_ctrl is None
        )

    def _start_reconnect(self, bind_signals: Callable[[Any], None] | None) -> bool:
        """重连已有运行：不保存配置、不创建新运行，只提交新端口。"""
        run = self.prepared_run
        if run is None:
            self._warn("重新连接失败", "没有可重新连接的运行")
            return False
        self._reconnecting = True
        self._set_state(RunFlowState.FINALIZING)
        self._log(f"重新连接运行 {getattr(run, 'run_id', run)} 的仪器")
        if not self._start_connection_worker(run, bind_signals):
            self._reconnecting = False
            if self._state is RunFlowState.FINALIZING:
                self._set_state(RunFlowState.RECONNECT_REQUIRED)
            return False
        return True

    def commit_finalization(self, controller: Any) -> bool:
        """连接成功：把新端口与 ``_pending_run`` 一次性提交为当前运行。

        新端口注入失败时关闭刚拿到的端口，并走失败补偿；调用方据此不要导航到
        测量区。
        """
        if self._state is not RunFlowState.FINALIZING:
            self._log("没有进行中的最终动作；忽略迟到的端口提交")
            errors = close_instrument_port(controller)
            if errors:
                self._log("迟到端口清理失败: " + describe_cleanup_errors(errors))
                self._remember_unreleased_port(controller)
                self._set_state(RunFlowState.MANUAL_CLEANUP_REQUIRED)
            return False
        target = self.prepared_run if self._reconnecting else self._pending_run
        if target is None:
            self._abort_finalization("待提交运行缺失，不能提交端口")
            return False
        if not self.accept_instrument_controller(controller):
            self._abort_finalization("新端口交接失败")
            return False
        if not self._reconnecting:
            # 原子提交：端口已经就绪，此时才把待提交运行交给测量页。
            self.prepared_run = target
            self._pending_run = None
        self._reconnecting = False
        self._set_state(RunFlowState.CONNECTED)
        self._log(
            f"已提交运行 {getattr(target, 'run_id', target)} 与仪器端口"
        )
        return True

    def fail_finalization(self, reason: str = "") -> bool:
        """连接检查失败：关闭新端口、补偿待提交运行并标记后续状态。"""
        return self._abort_finalization(reason or "仪器连接失败")

    def cancel_finalization(self, reason: str = "") -> bool:
        """用户取消最终动作：与失败补偿共用同一套清理，但不弹错误提示。"""
        return self._abort_finalization(reason or "用户取消最终动作")

    def _abort_finalization(self, reason: str) -> bool:
        # worker 在停止/异常路径自行关闭端口；未确认关闭的引用必须先收管，
        # 否则协调器会误以为没有未决清理而允许下一次事务。
        adopted = self._adopt_worker_unreleased_port()
        if self._state is not RunFlowState.FINALIZING:
            if adopted:
                self._require_manual_cleanup(reason)
            self._log(f"忽略迟到的连接结束事件: {reason}")
            return False
        if self.instrument_ctrl is not None:
            self.release_instrument_port()
        compensated = True
        if not self._reconnecting and self._pending_run is not None:
            compensated = self._compensate_pending_run()
        self._reconnecting = False
        if not compensated or self.has_unreleased_ports():
            self._require_manual_cleanup(reason)
            self._log(f"最终动作未完成且需要人工清理: {reason}")
            return False
        if self.prepared_run is not None:
            self._set_state(RunFlowState.RECONNECT_REQUIRED)
        else:
            self._set_state(RunFlowState.CONFIGURING)
        self._log(f"最终动作未提交: {reason}")
        return True

    def _require_manual_cleanup(self, reason: str) -> None:
        """进入人工清理状态：阻断后续事务并把原因交给用户。"""
        self._set_state(RunFlowState.MANUAL_CLEANUP_REQUIRED)
        self._log(f"需要人工清理仪器端口: {reason}")
        self._warn(
            "仪器清理未完成",
            "未能确认仪器已安全断开；请在现场确认后重试清理，"
            "清理完成前不能开始测量或重新连接",
        )

    def _adopt_worker_unreleased_port(self) -> bool:
        """收管连接 worker 自行关闭未确认的端口；返回是否收管了新端口。"""
        worker = self.instrument_worker
        port = getattr(worker, "unreleased_port", None)
        if port is None:
            return False
        self._remember_unreleased_port(port)
        worker.unreleased_port = None
        self._log("连接线程未能确认关闭仪器端口；已登记为未决清理")
        return True

    def has_pending_run(self) -> bool:
        """是否存在尚未提交的待提交运行。"""
        return self._pending_run is not None

    def abandon_pending_run(self) -> bool:
        """放弃尚未提交的待提交运行（与连接失败补偿共用同一套步骤）。

        只在最终动作之外重试补偿时使用，调用方必须先收尾连接 worker：线程还在
        运行时撤销运行目录会留下“目录已删、连接仍在进行”的半提交状态，因此这里
        直接拒绝。撤销失败保持 ``_pending_run`` 引用并进入人工清理，返回 ``False``；
        成功返回 ``True``。运行已开始或已结束时会由
        :func:`application.run_service.discard_unstarted_run` 拒绝，绝不删除已有的
        测量历史。
        """
        if self._pending_run is None:
            return True
        if self.instrument_worker_running():
            self._log("仪器连接线程仍在运行；停止后才能撤销待提交运行")
            return False
        if not self._compensate_pending_run():
            self._require_manual_cleanup("待提交运行撤销失败")
            return False
        return True

    def _compensate_pending_run(self) -> bool:
        """守卫式放弃待提交运行；返回是否为干净补偿。"""
        pending = self._pending_run
        if pending is None:
            return True
        try:
            self._discard_pending_run(pending)
        except Exception as failure:  # noqa: BLE001 - 补偿失败必须阻断后续事务
            self._log(f"撤销待提交运行失败: {failure}")
            return False
        self._pending_run = None
        self._log(
            f"待提交运行 {getattr(pending, 'run_id', pending)} 未提交，已放弃"
        )
        return True

    def _discard_pending_run(self, pending: Any) -> None:
        """撤销尚未开始的待提交运行目录。

        只删除“已创建且从未开始”的运行；业务判断在
        :func:`application.run_service.discard_unstarted_run`，仓储的
        ``discard_run()`` 仍只做底层删除。运行契约不完整时拒绝执行：无法证明它
        未开始就不能删除，补偿失败会把流程留在人工清理状态。
        """
        run_id = getattr(pending, "run_id", None)
        run_repository = getattr(pending, "run_repository", None)
        if not isinstance(run_id, str) or not run_id.strip() or run_repository is None:
            raise RunNotDiscardable(
                "待提交运行缺少运行契约（运行标识或运行仓储），无法安全撤销；"
                "请人工检查运行目录后重试"
            )
        discard_unstarted_run(run_repository, run_id)

    def load_prepared_run(self, run_id: str):
        """配置阶段：按 run_id 重新打开已有运行，不依赖 GUI 内存中的配置。"""
        if self._refuse_while_measuring("加载运行失败"):
            return None
        try:
            prepared = load_prepared_run(
                run_id, config_path=Path(self.config_path), paths=self.paths
            )
        except Exception as failure:  # noqa: BLE001 - 加载失败必须留在界面，当前运行不变
            self._log(f"加载运行失败: {failure}")
            self._warn("加载运行失败", str(failure))
            return None
        # 两阶段提交：配置页回填成功前不切换当前运行、不释放旧端口、不更新状态。
        if not self.apply_run_configuration(prepared):
            self._log(
                f"加载运行 {prepared.run_id} 失败: 配置页回填失败；当前运行保持不变"
            )
            self._warn(
                "加载运行失败",
                "配置页回填失败，运行未切换；请检查运行快照后重试",
            )
            return None
        if not self.release_instrument_for_new_run():
            self._log(
                f"加载运行 {prepared.run_id} 未生效: 上一次连接的仪器未能安全释放；"
                "当前运行保持不变"
            )
            self._warn(
                "加载运行未生效",
                "上一次连接的仪器未能完成安全清理；请在现场确认仪器已断开后重试",
            )
            return None
        self.prepared_run = prepared
        # 加载的运行还没有端口：用户需要走“重新连接仪器”，而不是再冻结一份新运行。
        self._set_state(RunFlowState.RECONNECT_REQUIRED)
        self._log(
            f"已加载运行 {prepared.run_id}，运行目录: {prepared.run_directory}"
        )
        return prepared

    def apply_run_configuration(self, prepared: Any) -> bool:
        """把已冻结运行配置同步到配置页；返回是否回填成功。"""
        page = self.config_page
        config = getattr(prepared, "hardware_config", None)
        if page is None:
            return False
        if not isinstance(config, dict):
            self._log("运行缺少可展示的硬件配置快照；配置页未回填")
            return False
        applier = getattr(page, "apply_config", None)
        if applier is None:
            self._log("配置页不支持回填冻结配置；配置页未回填")
            return False
        try:
            applier(config)
        except ConfigBackfillError as failure:
            self._log(f"冻结配置同步到配置页失败: {failure}")
            return False
        except Exception as failure:  # noqa: BLE001 - 展示失败不阻断运行契约，但必须可见
            self._log(f"冻结配置同步到配置页出现未预期错误: {failure}")
            return False
        self.config = dict(config)
        return True

    def run_status_text(self) -> str:
        run = self.prepared_run
        run_id = getattr(run, "run_id", None) if run is not None else None
        return "当前运行: 未创建" if run_id is None else f"当前运行: {run_id}"

    def run_status_changed(self, text: str) -> None:
        """把运行状态写进配置页；窗口可覆盖此方法改变展示位置。"""
        page = self.config_page
        if page is not None:
            page.set_run_status(text)

    def amplifier_driver_mode(self) -> bool:
        """返回当前驱动模式；优先使用已冻结运行，创建运行前回退到配置页。"""
        run = self.prepared_run
        if run is not None:
            configuration = getattr(run, "configuration", None)
            plan = getattr(configuration, "test_plan", None)
            if plan is not None:
                return bool(getattr(plan, "driver_enabled", False))
        page = self.config_page
        if page is not None:
            return bool(page.driver_mode_requested())
        return False

    # ---- 自动归档结果的定位（供页面“打开结果目录/报告”） ----

    def current_run_id(self) -> str | None:
        """当前运行 ID；纯查询，不检查仪器连接、不弹窗。

        归档展示只做“属于哪个运行”的文案与按钮判断，因此不能复用
        :meth:`resolve_measurement_run`——它会因为未连接仪器而弹错误对话框。
        """
        run = self.prepared_run
        run_id = getattr(run, "run_id", None)
        return run_id if isinstance(run_id, str) and run_id.strip() else None

    def archival_location(
        self, run_id: str, relative_path: str | None = None
    ) -> str | None:
        """返回归档结果的本地位置：默认运行目录，给定产物相对路径时返回该文件。

        只服务“打开结果目录/测试报告”这类展示动作：运行必须与当前运行一致，
        相对路径必须是归档契约里的运行目录内产物路径。本方法不读取文件内容、
        不判断产物是否可用；产物状态由归档服务与 manifest 决定。
        """
        run = self.prepared_run
        if run is None or getattr(run, "run_id", None) != run_id:
            self._log(
                f"归档结果属于运行 {run_id}，当前运行不是它；请在“历史运行”页查看"
            )
            return None
        directory = getattr(run, "run_directory", None)
        if directory is None or not str(directory).strip():
            self._log(f"运行 {run_id} 没有可定位的运行目录")
            return None
        base = Path(str(directory))
        if relative_path is None:
            return str(base)
        try:
            normalized = normalize_artifact_path(relative_path)
        except ValueError as error:
            self._log(f"归档产物路径不合法: {error}")
            return None
        return str(base.joinpath(*normalized.split("/")))

    # ---- 仪器连接与端口生命周期 ----

    def _configuration_page_is_dirty(self) -> bool:
        """配置页是否有尚未冻结为运行的修改；只用于给用户一条日志提示。

        连接仪器一律使用当前运行的冻结快照（步骤 5），因此界面上的改动不会生效。
        这里不做阻断：是否需要先重建运行由用户按现场情况决定，但“改动被忽略”必须
        在日志里说出来，不能静默。``is_dirty()`` 只是控件值与基线的比较，不吞异常：
        它抛错说明配置页状态已坏，应当在连接前暴露，而不是被这条提示掩盖。
        """
        checker = getattr(self.config_page, "is_dirty", None)
        if not callable(checker):
            return False
        return checker() is True

    def connect_instruments(
        self, bind_signals: Callable[[Any], None] | None = None
    ) -> bool:
        """重新连接当前已提交运行的快捷入口；返回是否已启动。

        这是“运行保留但端口丢失”后的重连动作：不保存配置、不创建新运行，只使用
        现有运行快照打开仪器。没有运行（或运行快照不可用）时不打开任何设备，而是
        明确失败。``bind_signals`` 由窗口提供，在 worker 启动前连接窗口自身的 Qt 槽。
        """
        if self._refuse_while_measuring("仪器连接失败"):
            return False
        run = self.prepared_run
        if run is None:
            self._log(
                "无法连接仪器: 尚未创建运行；请先在“仪器配置”页点击“完成配置并连接仪器”"
            )
            self._warn(
                "仪器连接失败", "尚未创建运行：请先在“仪器配置”页完成配置并连接仪器"
            )
            return False
        if self.instrument_ctrl is not None:
            self._log("当前已有连接的仪器端口；请先完成或取消当前连接流程再重新连接")
            return False
        if self._state is RunFlowState.FINALIZING:
            self._log("最终动作正在进行，忽略重复连接")
            return False
        self._reconnecting = True
        self._set_state(RunFlowState.FINALIZING)
        if not self._start_connection_worker(run, bind_signals):
            self._reconnecting = False
            if self._state is RunFlowState.FINALIZING:
                self._set_state(RunFlowState.RECONNECT_REQUIRED)
            return False
        return True

    def _start_connection_worker(
        self,
        run: Any,
        bind_signals: Callable[[Any], None] | None = None,
    ) -> bool:
        """事务步骤：按指定运行启动连接 worker；不提交运行或端口。

        运行必须是已经冻结的运行契约（最终化时是 ``_pending_run``，重连时是
        ``prepared_run``）。这里只做前置校验和 worker 启动；成功提交、失败补偿由
        :meth:`commit_finalization` / :meth:`_abort_finalization` 负责。
        """
        if self.has_unreleased_ports() or self._state is RunFlowState.MANUAL_CLEANUP_REQUIRED:
            self._set_state(RunFlowState.MANUAL_CLEANUP_REQUIRED)
            self._warn(
                "仪器连接失败",
                "存在未完成的仪器清理；请在现场确认仪器已断开并重试清理后再连接",
            )
            return False
        if run is None:
            self._log(
                "无法连接仪器: 尚未创建运行；请先在“仪器配置”页点击“完成配置并连接仪器”"
            )
            self._warn(
                "仪器连接失败", "尚未创建运行：请先在“仪器配置”页完成配置并连接仪器"
            )
            return False
        self._log(
            f"使用已冻结的运行配置连接仪器: {getattr(run, 'run_id', run)}"
        )
        if self._configuration_page_is_dirty():
            self._log(
                "注意：仪器配置页存在尚未创建运行的修改；本次连接仍使用运行 "
                f"{getattr(run, 'run_id', run)} 的冻结快照，"
                "若要应用修改请重新“完成配置并连接仪器”"
            )
        try:
            # 只做前置校验：拿不到可用快照就在启动 worker 前失败；端口本身由
            # connect_instruments() 装配，并按同一份快照再校验一次（幂等）。
            resolver = getattr(run, "hardware_connection_path", None)
            if resolver is None:
                raise RunNotResumable(
                    "当前运行契约不可用，不能连接仪器；请重新创建或重新加载运行"
                )
            str(resolver())
        except RunNotResumable as failure:
            self._log(f"无法连接仪器: {failure}")
            self._warn("仪器连接失败", str(failure))
            return False
        try:
            worker = self._instrument_worker_factory(run)
            if bind_signals is not None:
                bind_signals(worker)
            self.instrument_worker = worker
            worker.start()
        except Exception as failure:  # noqa: BLE001 - worker 无法启动必须回到界面并补偿事务
            self.instrument_worker = None
            self._log(f"无法启动仪器连接: {failure}")
            self._warn("仪器连接失败", f"无法启动仪器连接: {failure}")
            return False
        return True

    def accept_instrument_controller(self, controller: Any) -> bool:
        """验证并提交一个新端口；不负责释放旧端口或提交运行。

        旧端口必须先由最终化事务的释放步骤处理。这里只有在测量 controller 接受
        端口后才把引用交给协调器；注入失败时关闭刚拿到的端口，避免泄漏。
        """
        if controller is None:
            self._log("仪器连接没有返回端口")
            return False
        if self.instrument_ctrl is not None:
            self._log("端口交接前仍持有旧端口；拒绝覆盖，请先释放旧端口")
            # 新端口已由连接线程打开：不接管就必须关闭，不能让它泄漏。
            self._close_and_record(controller)
            return False
        try:
            self.controller.set_measurement_port(controller)
        except RuntimeError as failure:
            self._log(f"注入测量端口失败: {failure}")
            self._close_and_record(controller)
            return False
        self.instrument_ctrl = controller
        return True

    def release_instrument_port(self) -> bool:
        """释放当前端口；失败时端口进入未决清理清单，返回 ``False``。"""
        port = self.instrument_ctrl
        if port is None:
            return True
        self.instrument_ctrl = None
        self._detach_measurement_port()
        return self._close_and_record(port)

    def release_instrument_for_new_run(self) -> bool:
        """切换运行前释放已连接端口；失败时保留未决引用并返回 ``False``。"""
        port = self.instrument_ctrl
        if port is None:
            return True
        self.instrument_ctrl = None
        self._detach_measurement_port()
        if not self._close_and_record(port):
            return False
        self._log("已释放上一次连接的仪器端口；请重新连接仪器")
        return True

    def _detach_measurement_port(self) -> None:
        """让 controller 不再持有已释放的端口。

        端口关闭后会话不能再用；如果 controller 继续保存这个引用，下一阶段会把已
        关闭的会话注入 worker。测量仍在运行时无法替换引用（worker 正在使用），此时
        保留引用由线程结束后的收尾负责。
        """
        try:
            self.controller.set_measurement_port(None)
        except RuntimeError:
            self._log("测量运行中，暂不解除已释放端口的引用")

    def release_port_after_measurement(self) -> bool:
        """测量线程结束后按阶段结果决定复用还是关闭端口。

        成功完成（``COMPLETED``）的阶段在确认 RF 与供电关闭后保留同一端口供下一
        阶段复用；取消、异常、急停或阶段结算失败都关闭端口，运行保留但标记需重新
        连接。清理失败进入 ``MANUAL_CLEANUP_REQUIRED``，不会静默复用未确认安全的
        会话。
        """
        state = getattr(self.controller, "state", None)
        if getattr(state, "kind", None) not in _MEASUREMENT_KINDS:
            return True
        if getattr(state, "status", None) is MeasurementStatus.COMPLETED:
            return self._settle_port_after_stage()
        return self._close_port_after_stage()

    def _on_stage_start_failed(self, reason: str) -> None:
        """阶段在 worker 创建前失败：按异常阶段关闭端口。

        这条路径没有 worker 线程，因此不会有 ``thread_finished``；只把 controller 标成
        失败会让协调器继续持有未确认安全的端口，下一阶段还会把它当成可复用端口。
        协调器是端口所有者，必须自己完成关闭并进入需重连/人工处理。
        """
        self._log(f"阶段启动失败，按异常阶段收尾仪器端口: {reason}")
        self.release_port_after_measurement()

    def _settle_port_after_stage(self) -> bool:
        """成功阶段：结算端口并保留引用；无法确认安全时退化为完整关闭。"""
        port = self.instrument_ctrl
        if port is None:
            return True
        errors = settle_instrument_stage(port)
        if errors is None:
            self._log("仪器端口没有阶段结算入口；按完整关闭处理，下一阶段需要重新连接")
            return self._close_port_after_stage()
        if errors:
            self._log("阶段结束安全清理失败: " + describe_cleanup_errors(errors))
            return self._close_port_after_stage()
        self._log("阶段完成，仪器端口已确认安全并保留供下一阶段复用")
        return True

    def _close_port_after_stage(self) -> bool:
        """异常阶段：关闭端口；运行保留时进入需重连，清理失败进入人工处理。"""
        released = self.release_instrument_port()
        if not released:
            self._set_state(RunFlowState.MANUAL_CLEANUP_REQUIRED)
        elif self.prepared_run is not None:
            self._set_state(RunFlowState.RECONNECT_REQUIRED)
        return released

    def _close_and_record(self, port: Any) -> bool:
        """关闭一个端口；失败时记录逐项错误并把它放进未决清理清单。"""
        errors = close_instrument_port(port)
        if not errors:
            self._forget_unreleased_port(port)
            return True
        self._log(f"仪器清理失败: {describe_cleanup_errors(errors)}")
        self._remember_unreleased_port(port)
        return False

    def _remember_unreleased_port(self, port: Any) -> None:
        if not any(existing is port for existing in self._unreleased_ports):
            self._unreleased_ports.append(port)

    def _forget_unreleased_port(self, port: Any) -> None:
        self._unreleased_ports = [
            existing for existing in self._unreleased_ports if existing is not port
        ]

    def has_unreleased_ports(self) -> bool:
        """是否存在清理未完成的端口（现场必须人工确认）。"""
        return bool(self._unreleased_ports)

    def unreleased_port_count(self) -> int:
        return len(self._unreleased_ports)

    def retry_unreleased_ports(self) -> bool:
        """重试未决端口清理；返回是否已全部清理完成。"""
        if not self._unreleased_ports:
            return True
        remaining = []
        for port in list(self._unreleased_ports):
            errors = close_instrument_port(port)
            if errors:
                remaining.append(port)
                self._log(
                    "未决仪器清理仍失败: " + describe_cleanup_errors(errors)
                )
        self._unreleased_ports = remaining
        if not remaining and self._state is RunFlowState.MANUAL_CLEANUP_REQUIRED:
            if self.prepared_run is not None:
                self._set_state(RunFlowState.RECONNECT_REQUIRED)
            else:
                self._set_state(RunFlowState.CONFIGURING)
        return not remaining

    def instrument_worker_running(self) -> bool:
        worker = self.instrument_worker
        return bool(worker is not None and worker.isRunning())

    def has_instrument_worker(self) -> bool:
        """是否仍持有连接 worker 引用（运行中，或已跑完但尚未收尾）。

        只判断“是否在运行”会漏掉一种状态：线程已经结束、引用还没清除，它可能带着
        未确认关闭的端口。窗口关闭必须先经停并收管这类 worker，不能因为
        :meth:`instrument_worker_running` 为 ``False`` 就跳过收尾。
        """
        return self.instrument_worker is not None

    def stop_instrument_worker(self, timeout_ms: int = 5000) -> bool:
        """请求停止连接 worker 并在期限内等待；成功停止后清除引用。

        线程停止后、清除引用前先收管它未能确认关闭的端口：连接 worker 在停止/异常
        路径自行关闭端口失败时会保留 ``unreleased_port``，窗口关闭流程必须先把它
        登记为未决清理，不能因为引用被清掉而误判为“已清理”。
        """
        worker = self.instrument_worker
        if worker is None:
            return True
        worker.stop()
        if not worker.wait(timeout_ms):
            self._log("仪器连接线程未能在关闭期限内停止")
            return False
        self._adopt_worker_unreleased_port()
        self.instrument_worker = None
        return True

    # ---- 实时数据上下文 ----

    def store_realtime_data(self, data: Any) -> bool:
        """把测量实时数据写入聊天上下文缓存；页面绘图由页面自己负责。"""
        if not isinstance(data, dict) or "frequency" not in data:
            return False
        return self.realtime_buffer.store(data)

    def clear_realtime_data(self) -> None:
        self.realtime_buffer.clear()


__all__ = [
    "ApplicationCoordinator",
    "ConfigurationPagePort",
    "RunFlowState",
    "close_instrument_port",
    "describe_cleanup_errors",
    "settle_instrument_stage",
]
