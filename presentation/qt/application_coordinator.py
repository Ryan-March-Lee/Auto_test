"""GUI 运行协调器：持有配置状态、运行契约、仪器连接和测量 controller。

主窗口只负责窗口生命周期、页面注册和信号连接；配置读写、运行创建/加载、仪器
连接以及端口清理都在这里完成。

协调器是 **Qt 表现层** 组件，但不接触任何具体控件：它只通过
:class:`ConfigurationPagePort` 这个窄接口（校验、组装、回填、运行状态、驱动模式）
与配置页交互，并通过注入的日志/告警/错误回调与窗口交互。这样它既不需要
知道控件名字，也可以用纯 Python 替身单独测试。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable, Protocol, runtime_checkable

from app.gui_runtime import load_prepared_run, prepare_configuration
from application.gui_data_service import GuiDataService
from application.run_service import RunNotResumable
from domain.run.models import normalize_artifact_path

from .composition import build_gui_data_service, default_paths
from .config_projection import ConfigBackfillError
from .configuration_authoring import author_official_configuration
from .measurement_controller import MeasurementController
from .measurement_state import MeasurementKind
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
        instrument_worker_factory: Callable[[str], Any] = InstrumentWorker,
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
        # 清理失败的端口进入未决清单：绝不因为一次失败就丢掉引用，现场可重试。
        self._unreleased_ports: list[Any] = []

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
        """把当前配置写为正式配置；失败时保留 :attr:`last_save_error`。"""
        try:
            self.config = author_official_configuration(self.config)
            self._push_config_to_page()
            self.gui_data_service.configuration.save(self.config)
        except Exception as failure:  # noqa: BLE001 - 界面需要原始原因，写入失败不得静默
            self.last_save_error = failure
            return False
        self.last_save_error = None
        return True

    def update_and_save_configuration(self) -> bool:
        """连接仪器前的“读取界面并保存”；结果写进日志，由调用方决定是否继续。"""
        self.update_configuration_from_ui()
        if self.save_configuration():
            self._log("配置已更新并保存")
            return True
        self._log(f"配置保存失败: {self.last_save_error}")
        return False

    # ---- 运行契约 ----

    def measurement_config_path(self) -> str:
        """返回硬件连接与测量应使用的配置路径。

        运行契约运行固定返回运行目录内冻结的 ``inputs/hardware_config.json``，并在
        返回前校验该快照仍然可用（存在、未被删除或篡改）；只有尚未创建运行
        （``prepared_run is None``）时才回退到全局配置。

        运行契约不可用（快照缺失、被删除、校验失败，或对象没有校验入口）时直接抛出
        :class:`RunNotResumable`：已创建运行必须使用自己的快照，否则会用与该运行无关的
        仪器地址连接硬件。
        """
        run = self.prepared_run
        if run is None:
            return str(self.config_path)
        resolver = getattr(run, "hardware_connection_path", None)
        if resolver is None:
            raise RunNotResumable(
                "当前运行契约不可用，不能连接仪器；请重新创建或重新加载运行"
            )
        return str(resolver())

    def resolve_measurement_run(self):
        """返回当前运行契约，供测量页面消费；不可用时提示并返回 ``None``。"""
        if self.prepared_run is None:
            self._error("尚未创建运行：请先在“仪器配置”页点击“校验并创建运行”")
            return None
        if self.instrument_ctrl is None:
            self._error("请先连接仪器；上一次测量结束后端口已安全释放")
            return None
        try:
            self.measurement_config_path()
        except RunNotResumable as failure:
            self._error(str(failure))
            return None
        return self.prepared_run

    def create_prepared_run(self):
        """配置阶段：校验配置并冻结为不可变的运行目录快照。

        创建运行只做参数校验：配置页校验 + 完整运行配置校验，通过后冻结快照。
        现场接线由各测量页开始前的连接图对话框确认，不在创建运行时预置。
        """
        page = self.config_page
        if page is None or not page.validate():
            return None
        self.update_configuration_from_ui()
        if not self.save_configuration():
            self._log(f"配置保存失败: {self.last_save_error}")
            self._warn("创建运行失败", f"配置保存失败: {self.last_save_error}")
            return None
        try:
            prepared = prepare_configuration(
                str(self.config_path),
                paths=self.paths,
                hardware_config=self.config,
            )
        except Exception as failure:  # noqa: BLE001 - 创建失败必须回到界面，且不得切换运行
            self._log(f"创建运行失败: {failure}")
            self._warn("创建运行失败", str(failure))
            return None
        if not self.release_instrument_for_new_run():
            # 旧端口未能安全释放：不得把界面切到新运行，否则现场会以为连接已收尾。
            self._log(
                f"运行 {prepared.run_id} 已冻结在 {prepared.run_directory}，"
                "但上一次连接的仪器未能安全释放；当前运行保持不变"
            )
            self._warn(
                "创建运行未生效",
                "上一次连接的仪器未能完成安全清理；请在现场确认仪器已断开后重试",
            )
            return None
        self.prepared_run = prepared
        self.run_status_changed(self.run_status_text())
        self._log(
            f"已创建运行 {prepared.run_id}，运行目录: {prepared.run_directory}"
        )
        return prepared

    def load_prepared_run(self, run_id: str):
        """配置阶段：按 run_id 重新打开已有运行，不依赖 GUI 内存中的配置。"""
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
        self.run_status_changed(self.run_status_text())
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
        return "当前运行: 未创建" if run is None else f"当前运行: {run.run_id}"

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

    def connect_instruments(
        self, bind_signals: Callable[[Any], None] | None = None
    ) -> bool:
        """启动仪器连接 worker；返回是否已启动。

        ``bind_signals`` 由窗口提供，在 worker 启动前连接窗口自身的 Qt 槽，
        保证第一个事件不会在连接建立前发出。
        """
        self._log("开始连接仪器...")
        if self.prepared_run is None:
            if not self.update_and_save_configuration():
                return False
        else:
            self._log(f"使用已冻结的运行配置连接仪器: {self.prepared_run.run_id}")
        try:
            config_path = self.measurement_config_path()
        except RunNotResumable as failure:
            self._log(f"无法连接仪器: {failure}")
            self._warn("仪器连接失败", str(failure))
            return False
        worker = self._instrument_worker_factory(config_path)
        if bind_signals is not None:
            bind_signals(worker)
        self.instrument_worker = worker
        worker.start()
        return True

    def accept_instrument_controller(self, controller: Any) -> bool:
        """接管已连接的仪器端口并注入 controller。

        返回是否完全干净地完成交接：旧端口清理失败时它进入未决清理清单（引用不丢），
        新端口仍被接管，避免把一次可用的连接判成不可用。
        """
        released = self.release_instrument_port()
        self.instrument_ctrl = controller
        try:
            self.controller.set_measurement_port(controller)
        except RuntimeError as failure:
            self._log(f"注入测量端口失败: {failure}")
            return False
        return released

    def release_instrument_port(self) -> bool:
        """释放当前端口；失败时端口进入未决清理清单，返回 ``False``。"""
        port = self.instrument_ctrl
        if port is None:
            return True
        self.instrument_ctrl = None
        return self._close_and_record(port)

    def release_instrument_for_new_run(self) -> bool:
        """切换运行前释放已连接端口；失败时保留未决引用并返回 ``False``。"""
        port = self.instrument_ctrl
        if port is None:
            return True
        self.instrument_ctrl = None
        if not self._close_and_record(port):
            return False
        self._log("已释放上一次连接的仪器端口；请重新连接仪器")
        return True

    def release_port_after_measurement(self) -> bool:
        """测量线程结束后释放端口；非测量终态（如仅连接仪器）不释放。"""
        state = getattr(self.controller, "state", None)
        if getattr(state, "kind", None) not in _MEASUREMENT_KINDS:
            return True
        return self.release_instrument_port()

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
        return not remaining

    def instrument_worker_running(self) -> bool:
        worker = self.instrument_worker
        return bool(worker is not None and worker.isRunning())

    def stop_instrument_worker(self, timeout_ms: int = 5000) -> bool:
        """请求停止连接 worker 并在期限内等待；成功停止后清除引用。"""
        worker = self.instrument_worker
        if worker is None:
            return True
        worker.stop()
        if not worker.wait(timeout_ms):
            self._log("仪器连接线程未能在关闭期限内停止")
            return False
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
    "close_instrument_port",
    "describe_cleanup_errors",
]
