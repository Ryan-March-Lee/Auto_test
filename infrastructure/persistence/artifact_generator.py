"""自动归档交付产物的基础设施适配器：把正式结果渲染成运行目录内的 CSV 与测试报告。

本模块实现 :class:`application.ports.result_artifacts.ResultArtifactGeneratorPort`，
是应用层 ``analysis`` 能力的唯一出口（``application`` 不得导入 ``analysis``）：

- :class:`CsvArtifactGenerator` 生成 CSV 交付产物（重构计划步骤 4）；
- :class:`ReportArtifactGenerator` 生成测试报告交付产物（重构计划步骤 5）；
- :class:`ArchiveArtifactGenerator` 把两者组合成自动归档使用的唯一生成入口。

三个实现共享同一套输入/输出边界：

- 输入只来自运行 manifest **已登记并通过校验**的正式结果：读取走
  ``RunRepositoryPort.read_run_json()``（缺登记、被替换、大小不一致都被拒绝），
  再用唯一解析入口 ``measurement_result_from_document()`` 转成正式结果 DTO；
- 输出只能落在 ``runs/<run_id>/artifacts/`` 下的受控相对路径：默认路径由
  :func:`domain.run.models.default_artifact_path` 集中给出，适配器不自己拼文件名，
  也拒绝任何不在 ``artifacts/`` 下、或经真实路径解析后越出该目录的目标（含符号链接）；
- CSV 复用 :func:`analysis.reporting.export_csv`，报告复用
  :func:`analysis.reporting.render_html_report`；两者都先写同目录临时文件（文件名带
  随机后缀，避免并发归档互相覆盖）再原子替换，因此失败不会留下被 manifest 引用的
  半截文件；
- 可预期的失败（空数据、结果不可读、目标不可写、产物类型/阶段不支持）统一抛出
  :class:`ArtifactGenerationError`，由归档服务把该产物登记为 ``failed`` 并保留正式结果；
  其它异常不兜底，避免把编程缺陷伪装成“产物生成失败”。

报告只写一个 HTML 文件（``artifacts/amplifier_report.html``），不调用 matplotlib
渲染图表：自动归档发生在测量成功之后，必须在任何环境下都不拖垮测量进程，图表渲染
留给用户在可视化/历史页显式触发。报告里引用的图表按契约与报告同目录，适配器将来
若补图表，也应写在 ``artifacts/`` 内。

报告与 CSV 是两个彼此独立的产物：CSV 失败不阻止报告生成，但报告**只能**链接真实
存在的同目录 CSV ——“CSV 已登记为 ``completed`` 且文件仍与 manifest 登记值一致”才写
链接，否则省略链接而不是留下指向不存在文件的死链。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from uuid import uuid4

from analysis.reporting import export_csv, render_html_report
from application.dto.measurement_results import (
    AmplifierMeasurementResult,
    MeasurementType,
    measurement_result_from_document,
)
from application.ports.result_artifacts import (
    ArtifactGenerationError,
    ArtifactGenerationRequest,
    GeneratedArtifact,
)
from application.ports.run_repository import RunLayout, RunRepositoryPort
from domain.run.models import (
    ARTIFACTS_DIRNAME,
    ARTIFACT_PATH_PREFIX,
    STAGE_AMPLIFIER,
    ArtifactKind,
    ArtifactStatus,
)
from infrastructure.persistence.run_directory import file_sha256_and_size


logger = logging.getLogger(__name__)

# 自动报告的标题只在这里定义一次，页面与报告不各写一套文案。
_REPORT_TITLE = "PA 测量报告"


class _RegisteredResultGenerator:
    """CSV 与报告共享的输入/目标边界；子类只提供具体的写入方式。

    构造时要求运行仓储提供 :attr:`required_run_methods` 声明的能力：产物只能来自
    manifest 已登记并校验过的正式结果，不能从任意文件或“最新结果”生成。报告还要读
    manifest 判断同目录 CSV 是否真实存在，因此比 CSV 多要求 ``load_manifest()`` 与
    ``verify_artifact()``。
    """

    kind_label = "产物"
    supported_kinds: frozenset[ArtifactKind] = frozenset()
    #: 本适配器用到的运行仓储能力；缺失时在装配阶段就拒绝，而不是运行期才失败。
    required_run_methods: tuple[str, ...] = ("read_run_json",)

    def __init__(self, runs: RunRepositoryPort) -> None:
        for name in self.required_run_methods:
            if not callable(getattr(runs, name, None)):
                raise TypeError(
                    f"{self.kind_label}产物生成需要运行仓储提供 {name}()"
                )
        self.runs = runs

    def generate(self, request: ArtifactGenerationRequest) -> GeneratedArtifact:
        """生成一份受控产物；子类实现具体写入方式。"""
        raise NotImplementedError  # pragma: no cover - 抽象基类

    # -- 请求边界 -----------------------------------------------------------

    def _require_request(
        self, request: ArtifactGenerationRequest, kind: ArtifactKind
    ) -> ArtifactGenerationRequest:
        """确认请求类型、产物类型与本适配器声明一致，且阶段有该类交付产物。"""
        if not isinstance(request, ArtifactGenerationRequest):
            raise TypeError("产物生成请求必须是 ArtifactGenerationRequest")
        if request.kind is not kind:
            raise ArtifactGenerationError(
                f"本适配器只生成 {self.kind_label} 产物，收到: {request.kind.value}"
            )
        if request.stage != STAGE_AMPLIFIER:
            # 契约 2.2：线损与驱动映射没有交付产物，它们应登记 not_applicable。
            raise ArtifactGenerationError(
                f"阶段 {request.stage} 没有 {self.kind_label} 交付产物，"
                f"只有主功放结果可以生成 {self.kind_label}"
            )
        return request

    # -- 输入边界 -----------------------------------------------------------

    def _layout(self, run_id: str) -> RunLayout:
        """取得运行目录布局；仓储不提供 ``layout()`` 时按产物生成失败上报。"""
        layout_factory = getattr(self.runs, "layout", None)
        if not callable(layout_factory):
            raise ArtifactGenerationError(
                "运行仓储不提供 layout()，无法定位运行目录内的产物目标"
            )
        try:
            layout = layout_factory(run_id)
        except (OSError, ValueError) as error:
            raise ArtifactGenerationError(
                f"运行 {run_id} 的目录布局不可用: {error}"
            ) from error
        if not Path(layout.run_directory).is_dir():
            raise ArtifactGenerationError(f"运行目录不存在: {layout.run_directory}")
        return layout

    def _target_path(self, layout: RunLayout, relative_path: str) -> Path:
        """把受控相对路径解析到 ``artifacts/`` 之下，越界目标直接拒绝。"""
        if not relative_path.startswith(ARTIFACT_PATH_PREFIX):
            raise ArtifactGenerationError(
                f"产物目标必须位于 {ARTIFACT_PATH_PREFIX} 下: {relative_path}"
            )
        name = relative_path[len(ARTIFACT_PATH_PREFIX):]
        run_directory = Path(layout.run_directory)
        target = (run_directory / ARTIFACTS_DIRNAME).joinpath(*name.split("/"))
        _require_inside_artifacts(run_directory, target, relative_path)
        return target

    def _amplifier_result(
        self, request: ArtifactGenerationRequest
    ) -> AmplifierMeasurementResult:
        """读取已登记的正式结果并确认它是可导出交付产物的主功放结果。"""
        try:
            document = self.runs.read_run_json(
                request.run_id, request.source_result
            )
        except (OSError, ValueError) as error:
            # FileNotFoundError 是 OSError：缺登记、缺文件、校验值不一致都在这里。
            raise ArtifactGenerationError(
                f"正式结果不可读或未登记，拒绝生成 {self.kind_label}: "
                f"{request.source_result}（{error}）"
            ) from error
        if not isinstance(document, Mapping):
            raise ArtifactGenerationError(
                f"正式结果不是 JSON 对象: {request.source_result}"
            )
        try:
            result = measurement_result_from_document(document)
        except ValueError as error:
            raise ArtifactGenerationError(
                f"正式结果无法解析为正式结果 DTO: {error}"
            ) from error
        if result.run_id != request.run_id:
            # 文件落在本运行目录、登记也齐全，但内容自称属于另一个运行：它可能是
            # 被复制/替换过的结果，不能当作本次运行的正式结果生成交付产物。
            raise ArtifactGenerationError(
                f"正式结果声明的运行标识与本次归档运行不一致: "
                f"{request.source_result} 声明 {result.run_id!r}，"
                f"归档运行 {request.run_id!r}"
            )
        if result.measurement_type is not MeasurementType.AMPLIFIER:
            raise ArtifactGenerationError(
                f"{self.kind_label}交付产物只来自主功放结果，实际为 "
                f"{result.measurement_type.value}: {request.source_result}"
            )
        if not result.report_rows():
            # 空数据是显式失败：不写空文件，也不靠 print 或 FileNotFoundError 表达。
            raise ArtifactGenerationError(
                f"正式结果没有可生成 {self.kind_label} 的数据行: "
                f"{request.source_result}"
            )
        return result

    # -- 写入边界 -----------------------------------------------------------

    @staticmethod
    def _publish(
        target: Path, write: Callable[[Path], None], *, error_label: str
    ) -> None:
        """渲染到同目录临时文件后原子替换目标，失败不留下半截产物。

        临时文件名带随机后缀：同一运行的并发或重复归档不会互相覆盖对方的临时文件，
        也不会把别人正在写的半成品替换成正式产物。
        """
        temporary = target.with_name(f"{target.name}.{uuid4().hex}.tmp")
        try:
            write(temporary)
            os.replace(temporary, target)
        except (OSError, ValueError) as error:
            _remove_quietly(temporary)
            raise ArtifactGenerationError(f"{error_label}: {error}") from error

    def _generated(
        self, request: ArtifactGenerationRequest, target: Path
    ) -> GeneratedArtifact:
        """读回已写入的产物，返回受控路径与校验值。"""
        digest, size = file_sha256_and_size(target)
        return GeneratedArtifact(
            path=request.target_path, sha256=digest, size_bytes=size
        )


class CsvArtifactGenerator(_RegisteredResultGenerator):
    """主功放 CSV 交付产物的生成端口实现（重构计划步骤 4）。"""

    kind_label = "CSV"
    supported_kinds = frozenset({ArtifactKind.CSV})

    def generate(self, request: ArtifactGenerationRequest) -> GeneratedArtifact:
        """把已登记的正式结果写成 ``artifacts/<stage>.csv``，返回路径与校验值。"""
        request = self._require_request(request, ArtifactKind.CSV)
        layout = self._layout(request.run_id)
        target = self._target_path(layout, request.target_path)
        result = self._amplifier_result(request)
        self._publish(
            target,
            lambda destination: export_csv(result, destination),
            error_label="CSV 写入失败",
        )
        return self._generated(request, target)


class ReportArtifactGenerator(_RegisteredResultGenerator):
    """主功放测试报告产物的生成端口实现（重构计划步骤 5）。"""

    kind_label = "报告"
    supported_kinds = frozenset({ArtifactKind.REPORT})
    required_run_methods = ("read_run_json", "load_manifest", "verify_artifact")

    def generate(self, request: ArtifactGenerationRequest) -> GeneratedArtifact:
        """把已登记的正式结果写成 ``artifacts/<stage>_report.html``。

        报告内容只来自正式结果 DTO（运行标识、状态、指标表、配置快照与计算摘要），
        并只链接**真实存在**的同目录 CSV 交付产物。图表文件属于报告产物的一部分，
        本实现不生成，因此报告不会引用任何不存在的文件。
        """
        request = self._require_request(request, ArtifactKind.REPORT)
        layout = self._layout(request.run_id)
        target = self._target_path(layout, request.target_path)
        result = self._amplifier_result(request)
        data_file = self._csv_sibling_name(request)
        self._publish(
            target,
            lambda destination: render_html_report(
                result,
                destination,
                title=_REPORT_TITLE,
                data_file=data_file,
            ),
            error_label="报告写入失败",
        )
        return self._generated(request, target)

    def _csv_sibling_name(self, request: ArtifactGenerationRequest) -> str | None:
        """返回可安全链接的 CSV 兄弟文件名；CSV 不可用时返回 ``None``。

        CSV 与报告是两个独立产物：CSV 生成失败不阻止报告生成，但报告不能引用一个
        不存在、或被替换过的文件。因此这里以 manifest 的最新记录为准，要求该 CSV
        对同一正式结果已登记为 ``completed`` 且文件仍与登记值一致（
        ``RunRepositoryPort.verify_artifact()``）；不满足就省略链接。

        这一步的失败**不是**报告失败：报告本身仍然有效，只是少一条可点击的数据链接，
        所以只写日志，不抛 :class:`ArtifactGenerationError`。
        """
        try:
            manifest = self.runs.load_manifest(request.run_id)
            record = manifest.find_artifact(
                ArtifactKind.CSV, source_result=request.source_result
            )
            if (
                record is None
                or record.status is not ArtifactStatus.COMPLETED
                or not record.path
            ):
                return None
            self.runs.verify_artifact(request.run_id, record)
        except (OSError, ValueError) as error:
            logger.warning(
                "报告省略 CSV 链接：CSV 产物不可用 run=%s source=%s error=%s",
                request.run_id,
                request.source_result,
                error,
            )
            return None
        return Path(record.path).name


class ArchiveArtifactGenerator:
    """把 CSV 与报告的生成实现组合成自动归档的唯一生成入口。

    归档服务只持有**一个**生成端口，``supported_kinds`` 决定它真正会生成哪些产物；
    组合器把每条 ``generate()`` 按产物类型派发给对应实现，任何未组合的类型仍然是
    显式失败，而不是悄悄跳过。
    """

    def __init__(
        self,
        runs: RunRepositoryPort,
        *,
        generators: Iterable[object] | None = None,
    ) -> None:
        built = (
            tuple(generators)
            if generators is not None
            else (CsvArtifactGenerator(runs), ReportArtifactGenerator(runs))
        )
        mapping: dict[ArtifactKind, object] = {}
        for generator in built:
            if not callable(getattr(generator, "generate", None)):
                raise TypeError("产物生成实现必须提供 generate()")
            for kind in _declared_kinds(generator):
                if kind in mapping:
                    raise TypeError(f"产物 {kind.value} 注册了多个生成实现")
                mapping[kind] = generator
        self._generators = mapping
        self.supported_kinds = frozenset(mapping)

    def generate(self, request: ArtifactGenerationRequest) -> GeneratedArtifact:
        """把请求派发给声明了该产物类型的实现。"""
        if not isinstance(request, ArtifactGenerationRequest):
            raise TypeError("产物生成请求必须是 ArtifactGenerationRequest")
        generator = self._generators.get(request.kind)
        if generator is None:
            raise ArtifactGenerationError(
                f"没有可用的 {request.kind.value} 产物生成实现"
            )
        return generator.generate(request)


def _declared_kinds(generator: object) -> frozenset[ArtifactKind]:
    """读取生成实现声明的产物类型；未声明或声明了未知类型都拒绝。

    组合器自己决定派发方式，因此每个实现必须显式说明它能生成什么：未声明的实现
    可能在某些类型上抛错，组合器无法据此判断，只能在装配阶段直接失败。
    """
    declared = getattr(generator, "supported_kinds", None)
    if declared is None:
        raise TypeError("组合器要求每个产物生成实现声明 supported_kinds")
    if isinstance(declared, (str, bytes)) or not isinstance(
        declared, (list, tuple, set, frozenset)
    ):
        raise TypeError("产物生成实现的 supported_kinds 必须是产物类型集合")
    kinds = set()
    for item in declared:
        try:
            kinds.add(ArtifactKind(item))
        except ValueError as error:
            raise TypeError(f"产物生成实现声明了未知产物类型: {item!r}") from error
    if not kinds:
        raise TypeError("产物生成实现的 supported_kinds 不能为空")
    return frozenset(kinds)


def _require_inside_artifacts(
    run_directory: Path, target: Path, relative_path: str
) -> None:
    """按真实路径确认产物目标仍在本次运行的 ``artifacts/`` 目录之内。

    字符串前缀只能挡住 ``..`` 这类字面越界。目录被重定向（POSIX 符号链接、Windows
    junction）后，同一个相对路径会写到运行目录别处甚至目录之外，因此这里比较解析后的
    真实路径，并分两层校验：

    1. ``artifacts/`` 的真实位置必须仍在运行目录之内；
    2. 目标文件的真实位置必须在 ``artifacts/`` 的真实位置之内。
    """
    artifacts_dir = run_directory / ARTIFACTS_DIRNAME
    if artifacts_dir.is_symlink():
        raise ArtifactGenerationError(f"产物目录不能是符号链接: {artifacts_dir}")
    try:
        run_root = run_directory.resolve(strict=False)
        artifacts_root = artifacts_dir.resolve(strict=False)
        resolved = target.resolve(strict=False)
    except (OSError, RuntimeError) as error:
        # RuntimeError：目录链接成环；OSError：路径不可解析。
        raise ArtifactGenerationError(
            f"产物目标路径不可解析: {relative_path}（{error}）"
        ) from error
    if artifacts_root == run_root or not _is_within(run_root, artifacts_root):
        raise ArtifactGenerationError(
            f"产物目录解析后不在运行目录内: {artifacts_dir}"
        )
    if resolved == artifacts_root or not _is_within(artifacts_root, resolved):
        raise ArtifactGenerationError(
            f"产物目标解析后不在 {ARTIFACTS_DIRNAME}/ 目录内: {relative_path}"
        )


def _is_within(root: Path, resolved: Path) -> bool:
    """按平台大小写规则判断 ``resolved`` 是否位于 ``root`` 之内（不含 root 自身）。"""
    root_text = os.path.normcase(str(root))
    target_text = os.path.normcase(str(resolved))
    return target_text.startswith(root_text.rstrip(os.sep) + os.sep)


def _remove_quietly(path: Path) -> None:
    """清理临时文件；清理失败不影响已经上报的产物失败原因。"""
    try:
        path.unlink()
    except OSError:
        pass


__all__ = [
    "ArchiveArtifactGenerator",
    "CsvArtifactGenerator",
    "ReportArtifactGenerator",
]
