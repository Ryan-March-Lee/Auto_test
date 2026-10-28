"""自动归档交付产物的生成端口（受控路径 + 校验值契约）。

自动归档需要把已经登记的正式结果渲染成 CSV 或测试报告，但这个动作必须复用
``analysis`` 的只读能力，而应用层不允许导入 ``analysis`` 或任何基础设施实现。
本端口把“生成一份产物”收敛成应用层契约：

- 输入是已登记的正式结果与**运行目录内**的受控目标路径（``artifacts/`` 之下），
  默认路径由 :func:`domain.run.models.default_artifact_path` 集中给出；
- 输出是实际写入的相对路径与 SHA-256/大小，应用服务据此登记 manifest；返回的
  ``path`` 必须等于 ``request.target_path``，写到别的路径会被应用服务判为该产物
  失败，避免 manifest 里出现与生成请求不对应的“另一份”产物；
- 失败必须显式抛出 :class:`ArtifactGenerationError`，不得返回 ``None``、空路径，
  也不得用 ``print`` 或“制造 ``FileNotFoundError``”掩盖空数据/不适用；应用服务只把
  :class:`ArtifactGenerationError`、``OSError`` 与 ``ValueError`` 登记为 ``failed``，
  其它异常会向上抛出并让该产物停在 ``pending``，因此可预期的生成失败必须包成
  :class:`ArtifactGenerationError`；
- 端口只读写 ``artifacts/`` 下的产物文件，不写正式结果，因此正式 JSON 永远不会被
  产物生成覆盖；同一目标可能被并发或重复归档触达，实现必须用带随机后缀的同目录临时
  文件加原子替换，不得用固定临时文件名。

当前实现状态：CSV 由
:class:`infrastructure.persistence.artifact_generator.CsvArtifactGenerator` 在重构计划
步骤 4 实现，报告由同模块的
:class:`infrastructure.persistence.artifact_generator.ReportArtifactGenerator` 在步骤 5
接入，组合根用 ``ArchiveArtifactGenerator`` 把两者合成**一个**生成端口，声明
``supported_kinds = {CSV, REPORT}``；线损与驱动映射在归档服务里登记
``not_applicable``，不会触达本端口。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from domain.run.models import (
    DEFAULT_STAGE_NAMES,
    INPUTS_DIRNAME,
    RESULTS_DIRNAME,
    SHA256_PATTERN,
    ArtifactKind,
    coerce_status,
    normalize_artifact_path,
    normalize_relative_path,
    validate_run_id,
)


class ArtifactGenerationError(RuntimeError):
    """产物生成失败：没有可用产物，调用方只能把该产物登记为 ``failed``。

    端口实现必须用它表达“空数据 / 模板不可用 / 写入失败 / 无实际产物”等失败，
    不要返回空路径或其它类型，也不要抛出让测量流程整体失败的异常类型。
    """


@dataclass(frozen=True)
class ArtifactGenerationRequest:
    """一次产物生成请求：正式结果 + 运行目录内的受控目标路径。"""

    run_id: str
    stage: str
    kind: ArtifactKind
    source_result: str
    target_path: str

    def __post_init__(self) -> None:
        validate_run_id(self.run_id)
        if self.stage not in DEFAULT_STAGE_NAMES:
            raise ValueError(
                "产物所属阶段必须是标准阶段: " + ", ".join(DEFAULT_STAGE_NAMES)
            )
        kind = coerce_status(self.kind, ArtifactKind, field_name="产物类型")
        if kind not in (ArtifactKind.CSV, ArtifactKind.REPORT):
            raise ValueError(f"自动归档只生成 CSV 与报告产物，收到: {kind.value}")
        object.__setattr__(self, "kind", kind)
        source = normalize_relative_path(self.source_result, field_name="产物来源结果")
        if not (
            source.startswith(INPUTS_DIRNAME + "/")
            or source.startswith(RESULTS_DIRNAME + "/")
        ):
            raise ValueError(
                "产物来源结果必须是运行目录 inputs/ 或 results/ 下的文件: "
                f"{self.source_result!r}"
            )
        object.__setattr__(self, "source_result", source)
        object.__setattr__(
            self, "target_path", normalize_artifact_path(self.target_path)
        )


@dataclass(frozen=True)
class GeneratedArtifact:
    """端口返回的产物事实：实际相对路径与校验值。

    路径只在校验阶段使用，应用服务登记到 manifest 后才是唯一事实来源；返回任何
    非法路径、错误摘要或负数大小都会在登记阶段被拒绝，并记录为该产物失败。
    """

    path: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", normalize_artifact_path(self.path))
        digest = self.sha256 if isinstance(self.sha256, str) else ""
        if not SHA256_PATTERN.fullmatch(digest):
            raise ValueError("产物摘要必须是 64 位小写十六进制 sha256")
        object.__setattr__(self, "sha256", digest)
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes < 0
        ):
            raise ValueError("产物大小必须是非负整数")


class ResultArtifactGeneratorPort(Protocol):
    """应用层调用交付产物生成的唯一端口。"""

    def generate(self, request: ArtifactGenerationRequest) -> GeneratedArtifact:
        """生成一份受控产物并返回实际路径与校验值。

        实现必须写入 ``request.target_path``，并且只在返回值里报告该路径；写到运行
        目录之外、写到别的位置、覆盖正式结果或改写 manifest 都违反契约。空数据、
        不适用或写入失败必须抛出 :class:`ArtifactGenerationError`，由应用服务登记为
        失败并允许显式重试；其它异常类型会被视为端口实现缺陷向上抛出，产物状态停在
        ``pending``。
        """
        ...


__all__ = [
    "ArtifactGenerationError",
    "ArtifactGenerationRequest",
    "GeneratedArtifact",
    "ResultArtifactGeneratorPort",
]
