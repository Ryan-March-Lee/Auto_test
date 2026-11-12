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
from infrastructure.persistence.run_deletion import FileRunDeletion
from infrastructure.persistence.run_history_repository import FileRunHistoryRepository
from infrastructure.persistence.run_repository import RunRepository
from infrastructure.persistence.stage_artifact_repository import (
    FileStageArtifactRepository,
)
from infrastructure.persistence.stage_result_repository import FileStageResultRepository


def default_paths() -> PathService:
    """Qt 进程的默认路径服务。

    表现层其它模块只透传这个对象，不 import 基础设施；路径规则变化只影响本模块。
    """
    return DEFAULT_PATHS


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
        # 步骤 7 起阶段结果保存在独立阶段目录，运行目录只登记引用：浏览/导出按引用
        # 读取权威结果。这里注入的是阶段结果仓储的读取能力，不是运行写入路径。
        stage_results=FileStageResultRepository(paths=paths),
        # 步骤 20 起 CSV/报告与正式阶段结果同目录：报告定位复用阶段产物仓库的目录
        # 边界与身份派生规则，适配器不自行拼路径或退回运行目录。
        stage_artifacts=FileStageArtifactRepository(paths=paths),
    )
    # 历史管理的“重新生成失败产物”复用同一个归档服务与产物生成端口：产物写回原运行
    # 目录的受控路径，页面既不需要拼接路径，也不需要另写一份导出规则。
    archival = ResultArchivalService(
        runs=runs,
        generator=ArchiveArtifactGenerator(runs),
    )
    # 删除是独立的写入口：只读历史仓储不获得写权限，页面也不能直接删目录。
    deletion = FileRunDeletion(runs=runs)
    return GuiDataService(
        JsonConfigurationAdapter(config_path or paths),
        query,
        ResultExportAdapter(query, paths),
        run_history=RunHistoryService(
            statuses=run_history,
            archival=archival,
            # 同一运行目录仓储同时提供只读产物位置：数据可视化页的“打开测试报告”
            # 不自行拼路径。
            locations=run_history,
            # 用户明确发起的删除：整目录删除，进行中的运行由用例与适配器双重拒绝。
            deletions=deletion,
            # 按历史结果定位所属运行的归档报告：页面只拿状态与位置，不解析结果标识。
            results=query,
        ),
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
        # 离线演示展示设备表里登记的全部仪器：把设备表交给演示响应生成器，按各自的
        # 当前 IP、探测端口与身份补出 *IDN?，不另存一份会与设备表脱节的设备清单。
        responses = (
            demo_idn_responses(registry) if mode == SCAN_MODE_SIMULATED else None
        )
        return build_scanner(registry=registry, mode=mode, responses=responses)

    return factory
