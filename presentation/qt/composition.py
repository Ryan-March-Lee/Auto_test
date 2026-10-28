"""Composition root for the Qt presentation process."""

from pathlib import Path
from typing import Any

from application.archival_service import ResultArchivalService
from application.gui_data_service import GuiDataService
from application.run_history_service import RunHistoryService
from infrastructure.discovery.instrument_discovery import (
    SCAN_MODE_SIMULATED,
    InstrumentDiscoveryError,
    build_scanner,
)
from infrastructure.discovery.simulated_network import demo_idn_responses
from infrastructure.filesystem.paths import DEFAULT_PATHS, PathService
from infrastructure.gui_data_service import (
    JsonConfigurationAdapter,
    ResultExportAdapter,
    ResultQueryAdapter,
)
from infrastructure.persistence.artifact_generator import ArchiveArtifactGenerator
from infrastructure.persistence.instrument_registry import InstrumentRegistry
from infrastructure.persistence.run_directory import default_runs_root
from infrastructure.persistence.run_history_repository import FileRunHistoryRepository
from infrastructure.persistence.run_repository import RunRepository


def build_gui_data_service(
    paths: PathService | None = None, *, config_path: Path | None = None
) -> GuiDataService:
    paths = paths or DEFAULT_PATHS
    # 历史查看/导出只注入运行目录历史：旧全局兼容副本已退役，正式测量结果仓储
    # 不进入 GUI 数据服务，从依赖关系上保证历史数据不会成为新运行的输入。
    runs = RunRepository(paths=paths)
    run_history = FileRunHistoryRepository(runs=runs)
    query = ResultQueryAdapter(
        paths,
        run_history=run_history,
        runs_root=default_runs_root(paths=paths),
    )
    # 历史管理的“重新生成失败产物”复用同一个归档服务与产物生成端口：产物写回原运行
    # 目录的受控路径，页面既不需要拼接路径，也不需要另写一份导出规则。
    archival = ResultArchivalService(
        runs=runs,
        generator=ArchiveArtifactGenerator(runs),
    )
    return GuiDataService(
        JsonConfigurationAdapter(config_path or paths),
        query,
        ResultExportAdapter(query, paths),
        run_history=RunHistoryService(statuses=run_history, archival=archival),
    )


def build_device_scanner_factory(
    paths: PathService | None = None,
) -> Any:
    """返回 ``mode -> InstrumentScanner`` 工厂，供配置页的设备发现面板使用。

    每次扫描都重新读取设备表：手工新增或退役的设备在下一轮扫描即可被识别，不需要
    重启程序。设备表损坏时抛出 :class:`InstrumentDiscoveryError`，由面板把失败原
    因显示给用户，而不是退回空设备表把全部设备显示成“未知设备”。
    """

    def factory(mode: str):
        registry_path = (paths or DEFAULT_PATHS).instruments_file
        try:
            registry = InstrumentRegistry.load(registry_path)
        except (OSError, ValueError) as error:
            raise InstrumentDiscoveryError(
                f"无法读取设备表 {registry_path}: {error}"
            ) from error
        responses = demo_idn_responses() if mode == SCAN_MODE_SIMULATED else None
        return build_scanner(registry=registry, mode=mode, responses=responses)

    return factory
