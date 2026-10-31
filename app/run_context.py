"""Application boundary for preparing a run before hardware access."""

from __future__ import annotations

import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from domain.configuration.models import RunConfiguration
from domain.configuration.rules import validate_hardware_connections
from domain.models import RunContext
from domain.run.models import generate_run_id
from application.ports.config_repository import ConfigurationLoadResult
from application.ports.run_repository import RunRepositoryPort
from application.run_service import (
    RunNotResumable,
    RunSnapshot,
    create_run_snapshot,
    ensure_run_repository_supports_run_contract,
    ensure_run_resumable,
    find_run_hardware_config,
    has_run_contract,
    load_hardware_config,
    load_run_configuration,
)
from infrastructure.filesystem.paths import PathService
from infrastructure.persistence.run_repository import RunRepository


@dataclass(frozen=True)
class PreparedRun:
    """Immutable data passed from configuration loading to an instrument session.

    字段语义必须保持明确：

    - ``config_path``：创建运行时的源 GUI 配置路径，仅用于兼容追踪，**不能**作为
      硬件连接或测量阶段的输入来源。
    - ``hardware_config_path``：运行目录内冻结的硬件装配配置，硬件连接与测量
      阶段必须使用它。
    - ``run_repository``：运行契约仓储；它与运行标识、运行目录和冻结快照解析入口
      一起构成运行契约（:func:`application.run_service.has_run_contract`）。
    """

    configuration: RunConfiguration
    context: RunContext
    run_directory: Path
    config_path: Path | None = None
    # 运行契约运行会携带创建它的仓储；它参与运行契约判据，缺它即不是运行契约对象。
    run_repository: RunRepositoryPort | None = None
    # 运行目录内冻结的硬件装配配置（``inputs/hardware_config.json``）。
    hardware_config_path: Path | None = None
    # 上述硬件装配配置的内容副本，供 GUI 展示运行配置；不连接硬件、不参与测量。
    hardware_config: Mapping[str, Any] | None = None

    @property
    def run_id(self) -> str:
        """Expose the prepared identity without making callers unpack context."""
        return self.context.run_id

    @property
    def has_run_contract(self) -> bool:
        """是否携带 ``runs/<run_id>/`` 运行目录契约（可用于阶段结果记录）。

        判定实现只有一份：:func:`application.run_service.has_run_contract`。它要求
        运行标识、运行目录、运行仓储与冻结快照解析入口齐备，与 GUI 边界判据
        :func:`presentation.qt.run_contract.is_valid_prepared_run` 完全一致。
        """
        return has_run_contract(self)

    @property
    def frozen_config_path(self) -> Path | None:
        """运行目录内的领域配置快照 ``config.json``；没有运行契约时为 ``None``。"""
        if not self.has_run_contract:
            return None
        return Path(self.run_directory) / "config.json"

    def hardware_connection_path(self) -> Path:
        """硬件连接与测量应使用的配置路径。

        运行契约运行固定使用运行目录内冻结的硬件配置，并在返回前确认该快照仍然
        可用：路径缺失、文件被删除或指向目录、manifest 登记项与 SHA-256 不一致时
        都抛出 :class:`RunNotResumable`。**绝不**回退到全局可变配置，避免用与本次
        运行无关的仪器地址连接硬件；不带运行契约的对象同样明确失败，而不是退回
        调用方传入的路径。
        """
        if not self.has_run_contract:
            raise RunNotResumable(
                "运行缺少运行契约（运行标识、运行目录、运行仓储与冻结快照解析入口），"
                "不能连接仪器；请重新创建或重新加载运行"
            )
        if self.hardware_config_path is None:
            raise RunNotResumable(
                f"运行 {self.run_id} 缺少有效的硬件配置快照，不能连接仪器；"
                "请重新创建或重新加载运行"
            )
        return self._verified_hardware_snapshot(Path(self.hardware_config_path))

    def _verified_hardware_snapshot(self, frozen: Path) -> Path:
        """确认契约运行的冻结硬件快照仍可用，返回可用的快照路径。

        先做文件级检查（存在、是普通文件），再要求运行契约仓储具备 manifest 校验
        能力，并以 manifest 登记项为准确认 SHA-256/大小未被篡改。任一失败都转成
        :class:`RunNotResumable`，让调用方明确失败而不是换成其它配置；仓储不完整
        同样明确失败，绝不因为缺少校验方法而放行未校验的快照。
        """
        try:
            usable = frozen.is_file()
        except OSError as error:
            raise RunNotResumable(
                f"运行 {self.run_id} 的硬件配置快照不可读: {error}"
            ) from error
        if not usable:
            raise RunNotResumable(
                f"运行 {self.run_id} 缺少有效的硬件配置快照"
                f"（{frozen} 不存在或不是普通文件）；请重新创建或重新加载运行"
            )
        repository = self.run_repository
        # 能进入本方法说明该对象被视为运行契约运行；仓储能力不完整必须明确失败，
        # 避免未做完整性校验的硬件快照被用于连接仪器。
        ensure_run_repository_supports_run_contract(repository)
        try:
            # 以 manifest 登记项为准校验：硬件快照被单独替换或删除都会在这里失败。
            return Path(find_run_hardware_config(repository, self.run_id))
        except RunNotResumable:
            raise
        except (OSError, ValueError) as error:
            raise RunNotResumable(
                f"运行 {self.run_id} 的硬件配置快照校验失败: {error}"
            ) from error


def create_prepared_run(
    loaded: ConfigurationLoadResult,
    *,
    run_id: str | None = None,
    software_version: str = "unknown",
    git_commit: str | None = None,
    config_path: Path | None = None,
    paths: PathService | None = None,
    run_repository: RunRepositoryPort | None = None,
    hardware_config: Mapping[str, Any],
) -> PreparedRun:
    """Create an immutable run snapshot (``runs/<run_id>/``).

    This is the step-3 "创建运行" entry point: the configuration is validated as
    a complete run, then frozen through the run repository port.  ``hardware_config``
    is **required by the run contract** and freezes the hardware assembly
    configuration inside the run directory (``inputs/hardware_config.json``), so
    the created run is always resumable via :func:`load_prepared_run` and later
    stages never read the global mutable ``config.json``.  Passing ``None`` or a
    non-mapping value fails before any run directory is created.
    """
    if hardware_config is None:
        raise ValueError(
            "运行契约必须包含硬件配置快照：create_prepared_run 的 "
            "hardware_config 不能为 None，缺少硬件配置的运行无法恢复"
        )
    _require_connectable_hardware(hardware_config)
    repository = run_repository if run_repository is not None else RunRepository(paths=paths)
    snapshot: RunSnapshot = create_run_snapshot(
        loaded,
        run_repository=repository,
        run_id=run_id or generate_run_id(),
        software_version=software_version,
        git_commit=git_commit,
        hardware_config=hardware_config,
    )
    context = RunContext.from_resource_mapping(
        loaded.configuration.run_mapping.to_dict(),
        run_id=snapshot.run_id,
        software_version=software_version,
    )
    return PreparedRun(
        loaded.configuration,
        context,
        Path(snapshot.run_directory),
        config_path,
        repository,
        snapshot.hardware_config_path,
        hardware_config,
    )


def _require_connectable_hardware(hardware_config: Mapping[str, Any]) -> None:
    """创建运行目录之前拒绝存在供电连接冲突的硬件配置。

    正式运行映射的校验只看 ``run_mapping``；硬件装配读的是 ``instruments`` /
    ``power_supply_assignment``，其中的连接冲突（两台会被连接的电源共用同一个 VISA
    地址、同一物理通道同时供 DUT 与驱动功放、分配引用了未绑定的电源）只在这一段可见。
    这些冲突在装配阶段（``instrument.measurement_factory``）同样会失败，但那时运行目录
    与冻结快照已经写好，留下一份“建好了却连不上”的运行。这里放在唯一创建运行的入口，
    规则本身仍只有 :func:`domain.configuration.rules.validate_hardware_connections`
    一份，消息里点名具体字段（步骤 11）。

    只拦连接冲突，不重复字段完整性校验：通道保护值缺失之类由配置页保存与
    ``launcher --validate-config`` 负责。
    """
    result = validate_hardware_connections(dict(hardware_config))
    if not result.valid:
        details = "; ".join(f"{issue.path}: {issue.message}" for issue in result.errors)
        raise ValueError(f"硬件配置的供电连接存在冲突，不能创建运行: {details}")


def load_prepared_run(
    run_id: str,
    *,
    config_path: Path | None = None,
    paths: PathService | None = None,
    run_repository: RunRepositoryPort | None = None,
) -> PreparedRun:
    """Reconstruct a :class:`PreparedRun` from an existing ``runs/<run_id>/`` run.

    This is the step-7 "reopen by run_id" entry point.  It refuses to reopen a
    terminal run and requires the frozen hardware configuration snapshot to be
    present and checksum-verified, so a reopened run always assembles hardware
    from its own frozen configuration rather than the global ``config.json``.
    """
    repository = run_repository if run_repository is not None else RunRepository(paths=paths)
    if not repository.run_exists(run_id):
        raise FileNotFoundError(f"运行不存在或结构不完整: {run_id}")
    ensure_run_resumable(repository.load_manifest(run_id))
    hardware_config_path = find_run_hardware_config(repository, run_id)
    hardware_config = load_hardware_config(repository, run_id)
    loaded = load_run_configuration(repository, run_id)
    if not loaded.valid:
        details = "; ".join(f"{issue.path}: {issue.message}" for issue in loaded.validation.errors)
        raise ValueError(f"运行 {run_id} 的配置快照校验失败: {details}")
    configuration = loaded.configuration
    context = RunContext.from_resource_mapping(
        configuration.run_mapping.to_dict(),
        run_id=run_id,
        software_version=environment_version(),
    )
    layout = repository.layout(run_id)
    return PreparedRun(
        configuration,
        context,
        Path(layout.run_directory),
        config_path,
        repository,
        hardware_config_path,
        hardware_config,
    )


def environment_version() -> str:
    """Return a stable, dependency-free software identity for run metadata."""
    return f"python-{platform.python_version()}"
