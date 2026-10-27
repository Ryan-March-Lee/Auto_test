"""GUI 运行协调器：持有配置状态、运行契约、仪器连接和测量 controller。

主窗口只负责窗口生命周期、页面注册和信号连接；配置读写、运行创建/加载、仪器
连接以及端口释放都在这里完成。协调器不接触具体页面控件，只通过注入的日志、
告警、错误提示和接线确认回调与窗口交互。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from app.gui_runtime import load_prepared_run, prepare_configuration
from application.gui_data_service import GuiDataService
from application.run_service import RunNotResumable
from infrastructure.filesystem.paths import DEFAULT_PATHS, PathService

from .composition import build_gui_data_service
from .config_form_state import ConfigFormState
from .config_page import ConfigBackfillError
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


def _ignore(_message: str) -> None:
    """默认的日志/提示回调：协调器不要求调用方一定提供 UI。"""


def close_instrument_port(port: Any) -> None:
    """安全关闭仪器端口；端口不存在时不做事，关闭失败由调用方记录。"""
    if port is None:
        return
    close_all = getattr(port, "close_all", None)
    if close_all is not None:
        close_all(close_rf=True)
        return
    safe_shutdown = getattr(port, "safe_shutdown", None)
    if safe_shutdown is not None:
        safe_shutdown()


class ApplicationCoordinator:
    """呈现层运行协调器：单一持有配置、运行契约和仪器端口。

    ``log`` / ``warn`` / ``error`` 是窗口提供的用户可见反馈通道；协调器本身不弹
    对话框、不读写控件，因此可以用纯 Python 替身单独测试。
    """

    def __init__(
        self,
        *,
        log: Callable[[str], Any] = _ignore,
        warn: Callable[[str, str], Any] = _ignore,
        error: Callable[[str], Any] = _ignore,
        paths: PathService | None = None,
        config_path: Any = None,
        gui_data_service: GuiDataService | None = None,
        controller: MeasurementController | None = None,
        widget_host: Any = None,
        confirm_run_wiring: Callable[[dict[str, Any]], bool] | None = None,
        instrument_worker_factory: Callable[[str], Any] = InstrumentWorker,
    ) -> None:
        self.paths = paths or DEFAULT_PATHS
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
        self._widget_host = widget_host
        self._confirm_run_wiring = confirm_run_wiring
        self._instrument_worker_factory = instrument_worker_factory
        # 配置页由窗口在页面注册时绑定；未绑定前一律回退到窗口作为控件宿主。
        self.config_page: Any = None
        self.config: dict[str, Any] = {}
        self.prepared_run: Any = None
        self.instrument_ctrl: Any = None
        self.instrument_worker: Any = None
        self.last_save_error: Exception | None = None

    # ---- 配置状态 ----

    def bind_config_page(self, page: Any) -> None:
        """登记配置页：页面是配置控件与回填的唯一所有者。"""
        self.config_page = page
        self.run_status_changed(self.run_status_text())

    def load_configuration(self) -> dict[str, Any]:
        """通过 GUI 数据服务读取正式配置；读取失败时给出提示并保持空配置。"""
        try:
            self.config = dict(self.gui_data_service.configuration.load())
        except Exception as failure:  # noqa: BLE001 - 读取失败必须让用户看到，而不是带着旧配置运行
            self._warn("配置加载", f"无法加载配置文件: {failure}")
            self.config = {}
        return self.config

    def build_configuration_from_ui(self) -> dict[str, Any]:
        """基于当前配置与控件状态组装完整配置字典，不修改自身状态。"""
        widgets = self.config_page if self.config_page is not None else self._widget_host
        if widgets is None:
            raise RuntimeError("未绑定配置页，无法读取界面配置")
        return ConfigFormState(self.config, widgets).build_config()

    def update_configuration_from_ui(self) -> dict[str, Any]:
        """用当前控件值替换窗口持有的配置副本。"""
        self.config = self.build_configuration_from_ui()
        if self.config_page is not None:
            self.config_page.config = self.config
        return self.config

    def save_configuration(self) -> bool:
        """把当前配置写为正式配置；失败时保留 :attr:`last_save_error`。"""
        try:
            self.config = author_official_configuration(self.config)
            if self.config_page is not None:
                self.config_page.config = self.config
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
        """配置阶段：校验配置、确认接线并冻结为不可变的运行目录快照。"""
        page = self.config_page
        if page is None or not page.validate():
            return None
        self.update_configuration_from_ui()
        if not self._confirm_wiring():
            self._log("已取消创建运行：现场接线未确认")
            return None
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
        self.release_instrument_for_new_run()
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
        self.release_instrument_for_new_run()
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
        """运行状态展示回调；窗口覆盖它把文本写进配置页。"""
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
            return page.driver_mode_check.isChecked()
        return False

    def _confirm_wiring(self) -> bool:
        config = self.config
        confirmer = self._confirm_run_wiring
        if confirmer is None:
            self._log("未配置创建运行接线确认入口")
            return False
        confirmed = bool(confirmer(config))
        self.config = config
        return confirmed

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

    def accept_instrument_controller(self, controller: Any) -> None:
        """接管已连接的仪器端口，并把它注入 controller 供测量使用。"""
        if self.instrument_ctrl is not None and self.instrument_ctrl is not controller:
            self.release_instrument_port()
        self.instrument_ctrl = controller
        try:
            self.controller.set_measurement_port(controller)
        except RuntimeError as failure:
            self._log(f"注入测量端口失败: {failure}")

    def release_instrument_port(self) -> bool:
        """关闭并清除当前端口；失败只记录，返回是否没有遗留端口。"""
        port = self.instrument_ctrl
        if port is None:
            return True
        try:
            close_instrument_port(port)
        except Exception as failure:  # noqa: BLE001 - 端口关闭失败要让用户看到，但窗口仍可继续
            self._log(f"仪器清理失败: {failure}")
            self.instrument_ctrl = None
            return False
        self.instrument_ctrl = None
        return True

    def release_instrument_for_new_run(self) -> None:
        """切换运行前释放已连接端口，避免新运行复用旧配置的仪器资源。"""
        if self.instrument_ctrl is None:
            return
        try:
            close_instrument_port(self.instrument_ctrl)
        except Exception as failure:  # noqa: BLE001 - 释放失败必须提示现场重新连接
            self._log(f"切换运行前仪器清理失败: {failure}")
        self.instrument_ctrl = None
        self._log("已释放上一次连接的仪器端口；请重新连接仪器")

    def release_port_after_measurement(self) -> None:
        """测量线程结束后释放端口；非测量终态（如仅连接仪器）不释放。"""
        state = getattr(self.controller, "state", None)
        if getattr(state, "kind", None) not in _MEASUREMENT_KINDS:
            return
        self.release_instrument_port()

    def instrument_worker_running(self) -> bool:
        worker = self.instrument_worker
        return bool(worker is not None and worker.isRunning())

    def stop_instrument_worker(self, timeout_ms: int = 5000) -> bool:
        """请求停止连接 worker 并在期限内等待；超时返回 ``False``。"""
        worker = self.instrument_worker
        if worker is None:
            return True
        worker.stop()
        if worker.wait(timeout_ms):
            return True
        self._log("仪器连接线程未能在关闭期限内停止")
        return False

    # ---- 实时数据上下文 ----

    def store_realtime_data(self, data: Any) -> bool:
        """把测量实时数据写入聊天上下文缓存；页面绘图由页面自己负责。"""
        if not isinstance(data, dict) or "frequency" not in data:
            return False
        return self.realtime_buffer.store(data)

    def clear_realtime_data(self) -> None:
        self.realtime_buffer.clear()


__all__ = ["ApplicationCoordinator", "close_instrument_port"]
