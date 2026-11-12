"""本次正式测量实际消费的校准输入来源记录。

显式选择的线损/驱动映射结果可以来自其它运行，阶段目录文件本身只记录“谁生成了
它”，不记录“谁消费了它”。运行 manifest 若只登记本运行产出的阶段结果引用，就会
丢失“主功放测量用的是哪一份线损、哪一份驱动映射”的审计事实。本模块把这条事实
收敛成纯数据记录：

- ``measurement_type`` 是**消费方**测量类型（驱动映射 / 主功放）；
- ``stage`` 是被消费的阶段（线损 / 驱动映射）；
- ``measurement_id``、``path``、``sha256`` 与 ``size_bytes`` 是阶段结果仓库在同一次
  读取中给出的文件事实，不重新计算、也不从文件名推断来源运行；
- ``source_run_id`` 是生成这份结果的运行，因此跨运行复用可以被完整还原。

记录只描述已经校验并通过结构检查的输入；调用方不得为“补一条来源”重新读取文件。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from domain.run.models import (
    SHA256_PATTERN,
    STAGE_CABLE_LOSS,
    STAGE_DRIVER_MAPPING,
    validate_run_id,
)

from .measurement_results import (
    MeasurementType,
    measurement_result_filename,
    validate_measurement_id,
)

__all__ = ["CALIBRATION_INPUT_STAGES", "CalibrationInputSource"]


# 只有独立阶段目录中的正式结果可以作为校准输入来源。
CALIBRATION_INPUT_STAGES = frozenset({STAGE_CABLE_LOSS, STAGE_DRIVER_MAPPING})


@dataclass(frozen=True)
class CalibrationInputSource:
    """一次测量消费的一份校准输入的来源事实。"""

    measurement_type: MeasurementType | str
    stage: str
    measurement_id: str
    path: str
    sha256: str
    size_bytes: int
    source_run_id: str

    def __post_init__(self) -> None:
        if isinstance(self.measurement_type, MeasurementType):
            kind = self.measurement_type
        else:
            try:
                kind = MeasurementType(str(self.measurement_type).strip())
            except ValueError as error:
                raise ValueError(
                    f"校准输入来源的消费方测量类型非法: {self.measurement_type!r}"
                ) from error
        object.__setattr__(self, "measurement_type", kind)
        if not isinstance(self.stage, str) or self.stage.strip() not in (
            CALIBRATION_INPUT_STAGES
        ):
            allowed = ", ".join(sorted(CALIBRATION_INPUT_STAGES))
            raise ValueError(
                f"校准输入来源的阶段必须是 {allowed}，收到 {self.stage!r}"
            )
        object.__setattr__(self, "stage", self.stage.strip())
        try:
            identity = validate_measurement_id(self.measurement_id)
        except ValueError as error:
            raise ValueError(f"校准输入来源的测量身份非法: {error}") from error
        object.__setattr__(self, "measurement_id", identity)
        if not isinstance(self.path, str) or not self.path.strip():
            raise ValueError("校准输入来源必须包含阶段结果路径")
        path = Path(self.path)
        if not path.is_absolute():
            raise ValueError(f"校准输入来源的结果路径必须是绝对路径: {self.path!r}")
        expected_name = measurement_result_filename(identity)
        if path.name != expected_name:
            raise ValueError(
                "校准输入来源的文件名必须由 measurement_id 构造："
                f"{path.name!r} != {expected_name!r}"
            )
        object.__setattr__(self, "path", str(path))
        if not isinstance(self.sha256, str) or not SHA256_PATTERN.fullmatch(
            self.sha256
        ):
            raise ValueError("校准输入来源的摘要必须是 64 位小写十六进制 sha256")
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes < 0
        ):
            raise ValueError("校准输入来源的文件大小必须是非负整数")
        try:
            run_id = validate_run_id(self.source_run_id)
        except (TypeError, ValueError) as error:
            raise ValueError(f"校准输入来源的 source_run_id 非法: {error}") from error
        object.__setattr__(self, "source_run_id", run_id)

    def to_dict(self) -> dict[str, Any]:
        """返回可直接写入运行目录的来源记录（字段全部是 JSON 原生类型）。"""
        return {
            "measurement_type": self.measurement_type.value,
            "stage": self.stage,
            "measurement_id": self.measurement_id,
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "source_run_id": self.source_run_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CalibrationInputSource":
        """从运行目录的来源记录重建对象；缺失或非法字段直接拒绝。"""
        if not isinstance(value, Mapping):
            raise ValueError("校准输入来源记录必须是 JSON 对象")
        for required in ("measurement_type", "stage", "measurement_id", "path"):
            if value.get(required) in (None, ""):
                raise ValueError(f"校准输入来源记录缺少必需字段: {required}")
        return cls(
            measurement_type=value["measurement_type"],
            stage=value["stage"],
            measurement_id=value["measurement_id"],
            path=value["path"],
            sha256=value.get("sha256"),
            size_bytes=value.get("size_bytes"),
            source_run_id=value.get("source_run_id"),
        )
