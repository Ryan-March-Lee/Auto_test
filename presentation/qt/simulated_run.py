"""协调器持有的仿真运行上下文。

上下文只保存一次进入仿真运行后**不可变**的装配事实：真实 ``PreparedRun``、冻结
读数、运行级端口、仿真作用域路径与结果查询、标记与快放倍数。它不读文件、不创建
运行、不解释读数；这些都已由 :func:`app.simulation_runtime.prepare_simulated_run`
完成。

端口所有权与正式运行一致：协调器是唯一所有者，worker 只使用端口；阶段成功由协调器
调用 ``end_stage()`` 结算并保留端口，失败/取消由协调器关闭。重连只按同一份冻结读数
重建端口（:meth:`SimulatedRunContext.with_new_port`），不重新读文件、不创建第二个
运行。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from app.simulation_runtime import SimulatedRunAssembly, build_simulation_port
from application.dto.measurement_results import MeasurementType
from application.dto.simulation_readings import SimulationReadings
from application.errors import ConfigurationError
from application.simulation_run import SimulationMarker


@dataclass(frozen=True)
class SimulatedRunContext:
    """一次已提交仿真运行的只读上下文；端口由协调器持有。"""

    prepared_run: Any
    readings: Mapping[MeasurementType, SimulationReadings]
    port: Any
    paths: Any
    results: Any
    marker: SimulationMarker
    time_scale: float
    #: 端口重建入口，默认由 app 装配层提供；测试可注入替身，不进入相等比较。
    port_factory: Callable[[Mapping[MeasurementType, SimulationReadings]], Any] = field(
        default=build_simulation_port, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if not isinstance(self.readings, Mapping) or not self.readings:
            raise ConfigurationError("仿真运行上下文必须携带至少一个阶段的冻结读数")
        if self.port is None:
            raise ConfigurationError("仿真运行上下文缺少运行级端口")
        if self.paths is None:
            raise ConfigurationError("仿真运行上下文缺少仿真作用域路径服务")
        if self.results is None:
            raise ConfigurationError("仿真运行上下文缺少仿真作用域结果查询")
        if not isinstance(self.marker, SimulationMarker):
            raise ConfigurationError(
                "仿真运行上下文必须携带 SimulationMarker，"
                f"收到 {type(self.marker).__name__}"
            )
        scale = self.time_scale
        if isinstance(scale, bool) or not isinstance(scale, (int, float)) or scale <= 0:
            raise ConfigurationError(f"仿真运行上下文的时间倍率非法: {scale!r}")
        object.__setattr__(self, "time_scale", float(scale))

    @classmethod
    def from_assembly(cls, assembly: SimulatedRunAssembly) -> "SimulatedRunContext":
        """把应用层装配结果收敛为协调器上下文，并构建本次运行端口。"""
        if not isinstance(assembly, SimulatedRunAssembly):
            raise TypeError(
                "SimulatedRunContext.from_assembly 需要 SimulatedRunAssembly，"
                f"收到 {type(assembly).__name__}"
            )
        return cls(
            prepared_run=assembly.prepared_run,
            readings=assembly.readings,
            port=build_simulation_port(assembly.readings),
            paths=assembly.paths,
            results=assembly.results,
            marker=assembly.marker,
            time_scale=assembly.time_scale,
        )

    @property
    def run_id(self) -> str:
        return self.marker.run_id

    def with_new_port(self) -> "SimulatedRunContext":
        """用同一份冻结读数重建端口；不重新加载数据、不创建运行。"""
        return replace(self, port=self.port_factory(self.readings))


__all__ = ["SimulatedRunContext"]
