"""三个测量页面共享的测量启动 Facade（GUI 重构步骤 9）。

页面只负责收集用户点击、显示接线确认对话框并提交启动意图；本模块集中处理启动前的
共同步骤：

1. 是否绑定了 controller；
2. 是否已有测量在运行；
3. 当前运行是否满足正式运行契约（判据仍只来自
   :func:`presentation.qt.run_contract.is_valid_prepared_run`）；
4. 接线确认（由页面传入的回调显示对话框）；
5. 构造唯一的 ``MeasurementCommand``，并在页面决定提交时调用 controller。

模拟会话（``StartMeasurementRequest.from_session`` 传入
:class:`~presentation.qt.simulation_session.SimulationSession`）跳过第 3、4 步：
模拟链路没有正式运行、端口或真实接线；第 2 步的忙碌检查对两种模式一致生效。

Facade 不导入 PySide6、不创建对话框或 worker，也不决定连接图内容；线损的“第二步
继续”和三个页面各自的结果展示仍是页面专属行为。

接线确认会打开模态对话框并进入嵌套事件循环，页面不能在对话框返回后重新读取
``self.controller``。为此 :meth:`MeasurementStartFacade.prepare` 把检查过的 controller
收进决策，:meth:`StartMeasurementDecision.launch` 只提交给这一个 controller，调用方
无法把命令交给另一个 controller。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping

from application.dto.measurement_requests import (
    StageResultDigest,
    coerce_input_path,
    coerce_stage_digest,
)

from .measurement_controller_contract import (
    START_METHODS,
    MeasurementCommand,
    MeasurementExecutionMode,
)
from .measurement_state import MeasurementKind
from .run_contract import is_valid_prepared_run
from .simulation_session import SimulationSession


BUSY_MESSAGE = "已有测量正在运行"

START_LABELS: Mapping[MeasurementKind, str] = MappingProxyType(
    {
        MeasurementKind.CABLE_LOSS: "线损测量",
        MeasurementKind.DRIVER_MAPPING: "驱动映射测量",
        MeasurementKind.AMPLIFIER: "主功放测量",
    }
)


def is_busy(controller: Any) -> bool:
    """“已有测量正在运行”的唯一判据。

    页面在报告自己的输入前置条件（例如驱动映射缺少线损）之前也需要判断顺序：
    忙碌时让 :meth:`MeasurementStartFacade.prepare` 统一报告 ``BUSY_MESSAGE``，
    避免页面把“请先加载线损”当成真正原因。页面只调用本函数，不复制判据。
    """
    return bool(getattr(getattr(controller, "state", None), "is_active", False))


class StartMeasurementStatus(str, Enum):
    """一次启动意图在前置条件检查中的结果。"""

    READY = "ready"
    NO_CONTROLLER = "no_controller"
    BUSY = "busy"
    INVALID_RUN = "invalid_run"
    CONNECTION_CANCELLED = "connection_cancelled"


@dataclass(frozen=True)
class StartMeasurementRequest:
    """页面提交的启动意图：测量类型 + 当前运行契约 + 显式校准输入来源。

    页面在用户点击“开始”时读取 ``run_provider`` 得到当前运行，并把页面上已选择的
    阶段结果路径与选择时的文件事实一起冻结进请求；这里不缓存运行对象，也不回读控件。
    路径与 ``run_id`` 无关，允许来自其它运行，空路径/非法类型在构造期就按字段名拒绝。
    摘要只能与对应路径一起出现：启动时应用层据此复核文件没有被替换。

    ``prepared_run`` 是否满足契约由 :meth:`MeasurementStartFacade.prepare`
    判定，因为“没有运行”是用户可修正的问题，不是构造错误。

    模拟启动（``simulation_session`` 非空）不携带正式运行契约，也不携带页面选择的
    校准来源：模拟链路的校准输入由 :class:`SimulationSession` 固定，命令里再放一份
    从旧正式会话遗留下来的文件只会制造两套来源。页面统一用
    :meth:`from_session` 构造请求，由它按会话类型分流。
    """

    kind: MeasurementKind
    prepared_run: Any = None
    cable_loss_path: Path | str | None = None
    driver_mapping_path: Path | str | None = None
    cable_loss_digest: StageResultDigest | None = None
    driver_mapping_digest: StageResultDigest | None = None
    simulation_session: SimulationSession | None = None

    @classmethod
    def from_session(
        cls,
        kind: MeasurementKind,
        session: Any,
        *,
        cable_loss_path: Path | str | None = None,
        driver_mapping_path: Path | str | None = None,
        cable_loss_digest: StageResultDigest | None = None,
        driver_mapping_digest: StageResultDigest | None = None,
    ) -> "StartMeasurementRequest":
        """按统一会话入口构造启动意图。

        ``session`` 来自 :meth:`ApplicationCoordinator.resolve_measurement_session`：
        模拟会话进入 ``simulation_session``，正式运行契约（或不可用时的 ``None``）
        进入 ``prepared_run``。模拟会话自带固定校准输入，调用方传入的显式来源不会
        进入模拟命令。
        """
        if isinstance(session, SimulationSession):
            return cls(kind, simulation_session=session)
        return cls(
            kind,
            session,
            cable_loss_path=cable_loss_path,
            driver_mapping_path=driver_mapping_path,
            cable_loss_digest=cable_loss_digest,
            driver_mapping_digest=driver_mapping_digest,
        )

    def __post_init__(self) -> None:
        if not isinstance(self.kind, MeasurementKind):
            raise TypeError("kind 必须是 MeasurementKind")
        if self.simulation_session is not None:
            if not isinstance(self.simulation_session, SimulationSession):
                raise TypeError(
                    "simulation_session 必须是 SimulationSession，"
                    f"收到 {type(self.simulation_session).__name__}"
                )
            self._reject_simulation_payload()
        cable_loss_path = coerce_input_path(self.cable_loss_path, "cable_loss_path")
        driver_mapping_path = coerce_input_path(
            self.driver_mapping_path, "driver_mapping_path"
        )
        object.__setattr__(self, "cable_loss_path", cable_loss_path)
        object.__setattr__(self, "driver_mapping_path", driver_mapping_path)
        object.__setattr__(
            self,
            "cable_loss_digest",
            coerce_stage_digest(
                self.cable_loss_digest,
                cable_loss_path,
                field_name="cable_loss_digest",
                path_name="cable_loss_path",
            ),
        )
        object.__setattr__(
            self,
            "driver_mapping_digest",
            coerce_stage_digest(
                self.driver_mapping_digest,
                driver_mapping_path,
                field_name="driver_mapping_digest",
                path_name="driver_mapping_path",
            ),
        )

    @property
    def simulation(self) -> bool:
        """本次启动是否走模拟回放；正式运行与模拟会话互斥。"""
        return self.simulation_session is not None

    def _reject_simulation_payload(self) -> None:
        """模拟启动不能混入正式运行契约或页面选择的校准来源。"""
        if self.prepared_run is not None:
            raise ValueError(
                "模拟启动请求不能携带正式运行契约 prepared_run；"
                "模拟链路不创建运行目录，也不连接仪器"
            )
        for name in ("cable_loss_path", "driver_mapping_path"):
            if getattr(self, name) is not None:
                raise ValueError(
                    f"模拟启动请求不能携带 {name}；模拟会话已固定校准输入，"
                    "页面选择的正式结果不会进入模拟回放"
                )
        for name in ("cable_loss_digest", "driver_mapping_digest"):
            if getattr(self, name) is not None:
                raise ValueError(
                    f"模拟启动请求不能携带 {name}；模拟回放不校验正式结果摘要"
                )


@dataclass(frozen=True)
class StartMeasurementDecision:
    """启动意图的检查结论；就绪时携带唯一构造出的测量命令。

    ``controller`` 是 :meth:`MeasurementStartFacade.prepare` 检查过的那个对象，只用于
    :meth:`launch`：接线确认期间页面可能重新绑定 controller，决策不能反过来读取页面
    当前状态，否则前置检查与提交会落在两个不同的 controller 上。

    每种状态携带的字段是固定的（构造期校验，不允许调用方拼出半成品）：

    - ``READY``：启动意图 + 测量命令 + controller，无文案；
    - ``INVALID_RUN``：启动意图 + controller + 用户文案，无命令；
    - ``CONNECTION_CANCELLED``：启动意图 + controller，无命令、无文案；
    - ``BUSY``：controller + 用户文案，无启动意图、无命令；
    - ``NO_CONTROLLER``：什么都没有。
    """

    status: StartMeasurementStatus
    request: StartMeasurementRequest | None = None
    command: MeasurementCommand | None = None
    controller: Any = None
    message: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.status, StartMeasurementStatus):
            raise TypeError("status 必须是 StartMeasurementStatus")
        if self.request is not None and not isinstance(
            self.request, StartMeasurementRequest
        ):
            raise TypeError("request 必须是 StartMeasurementRequest")
        needs_request = self.status in {
            StartMeasurementStatus.READY,
            StartMeasurementStatus.INVALID_RUN,
            StartMeasurementStatus.CONNECTION_CANCELLED,
        }
        if needs_request and self.request is None:
            raise ValueError("该启动结论必须携带启动意图")
        if not needs_request and self.request is not None:
            raise ValueError("该启动结论不能携带启动意图")

        # 除“未绑定 controller”外，每种结论都记录了当时检查过的 controller。
        if self.status is StartMeasurementStatus.NO_CONTROLLER:
            if self.controller is not None:
                raise ValueError("未绑定 controller 的启动决策不能携带 controller")
        elif self.controller is None:
            raise ValueError("该启动结论必须携带 controller")

        if self.status is StartMeasurementStatus.READY:
            if not isinstance(self.command, MeasurementCommand):
                raise ValueError("就绪的启动决策必须携带测量命令")
        elif self.command is not None:
            raise ValueError("未就绪的启动决策不能携带测量命令")

        # 只有需要 report() 的两种结论带用户文案；其余结论不能借文案另开通道。
        if self.status in {
            StartMeasurementStatus.BUSY,
            StartMeasurementStatus.INVALID_RUN,
        }:
            if not self.message:
                raise ValueError("该启动结论必须携带用户文案")
        elif self.message:
            raise ValueError("该启动结论不能携带用户文案")

    @property
    def ready(self) -> bool:
        return self.status is StartMeasurementStatus.READY

    def report(
        self,
        log_callback: Callable[[str], Any],
        error_callback: Callable[[str], Any],
    ) -> None:
        """把未就绪的原因交给页面通道：忙碌记日志，运行契约问题是错误。"""
        if self.status is StartMeasurementStatus.BUSY:
            log_callback(self.message)
        elif self.status is StartMeasurementStatus.INVALID_RUN:
            error_callback(self.message)

    def launch(self) -> bool:
        """把就绪命令提交给 ``prepare`` 时检查过的同一个 controller。"""
        if not self.ready:
            raise ValueError("只有就绪的启动决策才能提交 controller")
        request = self.request
        if request is None:
            raise ValueError("就绪的启动决策必须携带启动意图")
        start = getattr(self.controller, START_METHODS[request.kind])
        return start(self.command)


class MeasurementStartFacade:
    """测量启动前置条件与 ``MeasurementCommand`` 构造的唯一入口。"""

    def __init__(
        self,
        *,
        config_path_provider: Callable[[], str],
        simulation_session_provider: Callable[[], Any] | None = None,
    ) -> None:
        if not callable(config_path_provider):
            raise TypeError("config_path_provider 必须可调用")
        if simulation_session_provider is not None and not callable(
            simulation_session_provider
        ):
            raise TypeError("simulation_session_provider 必须可调用")
        self._config_path_provider = config_path_provider
        self._simulation_session_provider = simulation_session_provider

    def simulation_session(self) -> SimulationSession | None:
        """静默读取当前模拟会话；普通会话或未接线时返回 ``None``。

        页面在“是否要求手动校准输入 / 是否显示 Mock 来源”之前调用这里。它必须是
        无副作用查询：正式入口仍由 :meth:`prepare` 在需要运行契约时读取，不能为了
        判断模式提前弹窗。返回非 :class:`SimulationSession` 的值一律视为普通模式。
        """
        provider = self._simulation_session_provider
        if provider is None:
            return None
        session = provider()
        return session if isinstance(session, SimulationSession) else None

    def prepare(
        self,
        request_factory: Callable[[], StartMeasurementRequest],
        *,
        controller: Any,
        confirm_connection: Callable[[], bool],
    ) -> StartMeasurementDecision:
        """检查启动前置条件并构造测量命令；确认接线由页面回调完成。

        ``request_factory`` 只在便宜的前置检查（controller、已有测量）通过后调用，
        因此“已有测量正在运行”时页面不会去读取当前运行，保持用户点击时的读取顺序。

        模拟启动跳过正式运行契约检查与物理接线确认：模拟链路没有运行目录、端口和
        真实接线；忙碌检查与 controller 检查仍对两种模式一致生效。
        """
        if not callable(request_factory):
            raise TypeError("request_factory 必须可调用")
        if not callable(confirm_connection):
            raise TypeError("confirm_connection 必须可调用")
        if controller is None:
            return self._decision(StartMeasurementStatus.NO_CONTROLLER)
        if is_busy(controller):
            return self._decision(
                StartMeasurementStatus.BUSY,
                controller=controller,
                message=BUSY_MESSAGE,
            )
        request = request_factory()
        if not isinstance(request, StartMeasurementRequest):
            raise TypeError("request_factory 必须返回 StartMeasurementRequest")
        simulation = request.simulation
        if not simulation and not is_valid_prepared_run(request.prepared_run):
            return self._decision(
                StartMeasurementStatus.INVALID_RUN,
                request=request,
                controller=controller,
                message=(
                    f"正式{START_LABELS[request.kind]}必须先连接仪器"
                ),
            )
        if not simulation and not confirm_connection():
            return self._decision(
                StartMeasurementStatus.CONNECTION_CANCELLED,
                request=request,
                controller=controller,
            )
        command = MeasurementCommand(
            self._config_path_provider(),
            prepared_run=request.prepared_run,
            cable_loss_path=request.cable_loss_path,
            driver_mapping_path=request.driver_mapping_path,
            cable_loss_digest=request.cable_loss_digest,
            driver_mapping_digest=request.driver_mapping_digest,
            mode=(
                MeasurementExecutionMode.SIMULATION
                if simulation
                else MeasurementExecutionMode.FORMAL
            ),
            simulation_session=request.simulation_session,
        )
        return self._decision(
            StartMeasurementStatus.READY,
            request=request,
            command=command,
            controller=controller,
        )

    @staticmethod
    def _decision(
        status: StartMeasurementStatus,
        *,
        request: StartMeasurementRequest | None = None,
        command: MeasurementCommand | None = None,
        controller: Any = None,
        message: str = "",
    ) -> StartMeasurementDecision:
        return StartMeasurementDecision(
            status=status,
            request=request,
            command=command,
            controller=controller,
            message=message,
        )


__all__ = [
    "BUSY_MESSAGE",
    "START_LABELS",
    "MeasurementStartFacade",
    "StartMeasurementDecision",
    "StartMeasurementRequest",
    "StartMeasurementStatus",
    "is_busy",
]
