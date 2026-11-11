"""显式选择阶段结果的加载与“选择时摘要”复核。

页面选择只保存路径时，路径无法表达“选的是哪一份内容”：同目录下的合法结果可以被
另一份同样合法的结果替换，而阶段边界、文件名、JSON 与正式 DTO 都会继续通过校验。
本模块把这两件事分开：

- :func:`load_selected_stage_result` 按显式路径经阶段结果仓库完成阶段契约校验；
- :func:`require_selected_result_unchanged` 再核对用户选择时观察到的 SHA-256 与
  文件大小，不一致时以 :class:`application.run_stages.StageInputUnavailable` 拒绝
  启动，绝不消费用户没有选择的内容。

两者都发生在连接仪器之前，且不扫描目录、不查找“最新结果”、不回退到运行引用。
"""

from __future__ import annotations

from application.dto.measurement_requests import StageResultDigest
from application.ports.stage_result_repository import (
    LoadedStageResult,
    MeasurementStage,
    StageResultRepository,
)
from application.ports.types import PathLike
from application.run_stages import StageInputUnavailable

__all__ = ["load_selected_stage_result", "require_selected_result_unchanged"]


def require_selected_result_unchanged(
    loaded: LoadedStageResult,
    digest: StageResultDigest | None,
    *,
    label: str,
) -> LoadedStageResult:
    """核对已加载结果与用户选择时的文件事实，返回同一个对象。

    省略摘要表示旧调用方（或直接调用用例的代码）只锁定路径：阶段契约仍会被完整
    校验，但不比较选择时的内容。给出摘要时，摘要与大小必须同时一致，否则说明文件
    在选择之后被改动或替换；这时既不重新选择，也不回退到当前运行引用。
    """
    if digest is None:
        return loaded
    if (loaded.sha256, loaded.size_bytes) != (digest.sha256, digest.size_bytes):
        raise StageInputUnavailable(
            f"{label}结果文件在选择后已被修改或替换，请重新选择: {loaded.path}"
        )
    return loaded


def load_selected_stage_result(
    repository: StageResultRepository,
    stage: MeasurementStage | str,
    path: PathLike,
    *,
    digest: StageResultDigest | None = None,
    label: str,
) -> LoadedStageResult:
    """按显式路径加载阶段结果，并复核选择时的摘要与大小。

    ``repository.load()`` 已完成阶段目录边界、文件名身份、UTF-8/JSON、正式 DTO、
    类型、状态与 schema 校验；这里只追加“内容仍是用户选择的那一份”的判断。返回的
    是**同一次读取**得到的结果对象，调用方应直接用它构造测量输入，不再按路径二次
    读取。
    """
    return require_selected_result_unchanged(
        repository.load(stage, path), digest, label=label
    )
