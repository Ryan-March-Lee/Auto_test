"""阶段目录交付产物的文件名契约（重构计划步骤 20）。

新的 CSV 与测试报告不再写进运行目录，而是与正式阶段结果放在同一个阶段目录，并用
结果的 ``measurement_id`` 关联：

```text
test_results/amplifier/
├── <measurement_id>.json          # 正式结果（权威来源）
├── <measurement_id>.csv           # CSV 交付产物
└── <measurement_id>_report.html   # 测试报告
```

本模块只定义“测量身份 + 产物类型 -> 纯文件名”的规则，不读文件、不拼接目录，也不
判断文件是否存在。目录解析、原子写入与失败清理由基础设施适配器负责；应用层拿到的
永远是纯文件名或阶段结果引用中的事实。
"""

from __future__ import annotations

from domain.run.models import ArtifactKind, coerce_status

from .measurement_results import validate_measurement_id


__all__ = [
    "STAGE_ARTIFACT_FILENAME_SUFFIXES",
    "stage_artifact_filename",
    "validate_stage_artifact_filename",
]


# 产物文件名只由身份与固定后缀构成，用户文本不参与路径构造。
STAGE_ARTIFACT_FILENAME_SUFFIXES = {
    ArtifactKind.CSV: ".csv",
    ArtifactKind.REPORT: "_report.html",
}


def stage_artifact_filename(measurement_id: object, kind: ArtifactKind | str) -> str:
    """返回阶段目录内的产物文件名（纯文件名）。

    ``measurement_id`` 先经过正式身份校验，因此调用方无法把路径片段、盘符或用户
    文本拼进文件名；产物类型未知时直接拒绝，避免产生没有约定后缀的文件。
    """
    identity = validate_measurement_id(measurement_id)
    artifact_kind = coerce_status(kind, ArtifactKind, field_name="产物类型")
    suffix = STAGE_ARTIFACT_FILENAME_SUFFIXES.get(artifact_kind)
    if suffix is None:  # pragma: no cover - ArtifactKind 当前两类都有后缀
        raise ValueError(f"未知的阶段产物类型: {artifact_kind.value}")
    return f"{identity}{suffix}"


def validate_stage_artifact_filename(
    filename: object, *, kind: ArtifactKind | str
) -> str:
    """校验阶段产物文件名是 ``<measurement_id><固定后缀>`` 形式的纯文件名。

    只接受本模块构造出的形状：拒绝空值、首尾空白、路径分隔符、盘符、``.``/``..``
    片段和错误后缀。返回值是规范化后的文件名，调用方不应使用原始输入。
    """
    if not isinstance(filename, str) or not filename:
        raise ValueError("阶段产物文件名不能为空")
    if filename != filename.strip():
        raise ValueError("阶段产物文件名不能包含首尾空白")
    if "\x00" in filename:
        raise ValueError("阶段产物文件名不能包含空字符")
    if "/" in filename or "\\" in filename or ":" in filename:
        raise ValueError("阶段产物文件名必须是纯文件名，不能包含路径或盘符")
    if filename in {".", ".."}:
        raise ValueError(f"阶段产物文件名非法: {filename!r}")
    artifact_kind = coerce_status(kind, ArtifactKind, field_name="产物类型")
    suffix = STAGE_ARTIFACT_FILENAME_SUFFIXES.get(artifact_kind)
    if suffix is None:  # pragma: no cover - ArtifactKind 当前两类都有后缀
        raise ValueError(f"未知的阶段产物类型: {artifact_kind.value}")
    if not filename.endswith(suffix):
        raise ValueError(
            f"阶段产物文件名必须使用 {suffix} 后缀: {filename!r}"
        )
    identity = filename[: -len(suffix)]
    if stage_artifact_filename(identity, artifact_kind) != filename:
        raise ValueError(f"阶段产物文件名非法: {filename!r}")
    return filename
