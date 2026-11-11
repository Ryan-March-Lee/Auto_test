"""三个测量页面共享的测量启动 Facade（GUI 重构步骤 9）。

页面只负责收集用户点击、显示接线确认对话框并提交启动意图；本模块集中处理启动前的
共同步骤：

1. 是否绑定了 controller；
2. 是否已有测量在运行；
3. 当前运行是否满足正式运行契约（判据仍只来自
   :func:`presentation.qt.run_contract.is_valid_prepared_run`）；
4. 接线确认（由页面传入的回调显示对话框）；
5. 构造唯一的 ``MeasurementCommand``，并在页面决定提交时调用 controller。

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

from application.dto.measurement_requests import coerce_input_path

from .measurement_controller_contract import START_METHODS, MeasurementCommand
from .measurement_state import MeasurementKind
from .run_contract import is_valid_prepared_run


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
    阶段结果路径一起冻结进请求；这里不缓存运行对象，也不回读控件。路径与 ``run_id``
    无关，允许来自其它运行，空路径/非法类型在构造期就按字段名拒绝。

    ``prepared_run`` 是否满足契约由 :meth:`MeasurementStartFacade.prepare`
    判定，因为“没有运行”是用户可修正的问题，不是构造错误。
    """

    kind: MeasurementKind
    prepared_run: Any
    cable_loss_path: Path | str | None = None
    driver_mapping_path: Path | str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, MeasurementKind):
            raise TypeError("kind 必须是 MeasurementKind")
        object.__setattr__(
            self,
            "cable_loss_path",
            coerce_input_path(self.cable_loss_path, "cable_loss_path"),
        )
        object.__setattr__(
            self,
            "driver_mapping_path",
            coerce_input_path(self.driver_mapping_path, "driver_mapping_path"),
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

    def __init__(self, *, config_path_provider: Callable[[], str]) -> None:
        if not callable(config_path_provider):
            raise TypeError("config_path_provider 必须可调用")
        self._config_path_provider = config_path_provider

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
        if not is_valid_prepared_run(request.prepared_run):
            return self._decision(
                StartMeasurementStatus.INVALID_RUN,
                request=request,
                controller=controller,
                message=(
                    f"正式{START_LABELS[request.kind]}必须先创建或按 run_id 重新加载运行"
                ),
            )
        if not confirm_connection():
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
