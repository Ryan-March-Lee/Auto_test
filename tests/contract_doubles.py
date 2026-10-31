"""正式测量链路测试共用的运行契约替身。

判据只有一份：:func:`application.run_service.has_run_contract`，它要求运行标识、
运行目录、运行仓储与可调用的 ``hardware_connection_path()`` 四项齐备
（见《无运行契约调用方清单》第 3.0 节第 3 条与第 8 节 D3/D10）：

- **正式链路测试**必须用真实 ``PreparedRun`` 或本模块的契约替身；
- **禁止**用 ``object()``、``SimpleNamespace(run_id=...)`` 这类“非空但不带契约”的
  空替身把流程跑通——那会让“构造即满足契约”变成一句空话；
- 需要证明“没有运行契约就必须失败”时，用 ``contract_run(...)`` 显式把某一项置
  ``None`` 造出对应的不满足替身，并断言明确失败。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

DEFAULT_RUN_ID = "20260101T000000Z_contract"


class _Unset:
    """占位符：区分“调用方没传”与“调用方显式传 None”。"""

    def __repr__(self) -> str:  # pragma: no cover - 仅用于调试输出
        return "<unset>"


_UNSET = _Unset()


def _default_hardware_connection_path(run_id: str):
    def hardware_connection_path(self=None) -> Path:
        return Path("runs") / run_id / "inputs" / "hardware_config.json"

    return hardware_connection_path


def contract_run(
    run_id: str = DEFAULT_RUN_ID,
    *,
    run_directory: Any = _UNSET,
    run_repository: Any = _UNSET,
    hardware_connection_path: Any = _UNSET,
    **overrides: Any,
):
    """返回满足运行契约判据的最小替身：只读属性，不连接任何设备。

    显式把某项传 ``None``（或删掉它）即得到“不满足契约”的替身，用于失败断言。
    """
    attributes: dict[str, Any] = {
        "run_id": run_id,
        "run_directory": (
            Path("runs") / run_id if run_directory is _UNSET else run_directory
        ),
        "run_repository": object() if run_repository is _UNSET else run_repository,
        "hardware_connection_path": (
            _default_hardware_connection_path(run_id)
            if hardware_connection_path is _UNSET
            else hardware_connection_path
        ),
    }
    attributes.update(overrides)
    return type("ContractRun", (), attributes)()


class RecordingStageRecorder:
    """阶段记录器替身：满足用例所需接口，记录调用而不触碰磁盘。

    正式测量链路的结果只能落在运行目录，因此用例测试必须注入一个记录器；本替身
    提供与 :class:`application.run_stages.RunStageRecorder` 相同的方法签名，并把
    每次调用记在 ``started`` / ``completed`` / ``failed`` / ``cancelled`` /
    ``skipped`` / ``verified`` 里，便于断言“结果只写当前运行”。

    ``missing_stages`` 指定的阶段在 :meth:`verified_result_path` 上抛出
    :class:`application.run_stages.StageInputUnavailable`，用于“前置输入缺失必须
    失败”的失败断言（清单第 3.0 节第 3 条）。
    """

    def __init__(
        self,
        run_id: str = DEFAULT_RUN_ID,
        *,
        inputs: dict[str, str] | None = None,
        missing_stages: tuple[str, ...] = (),
    ) -> None:
        self.run_id = run_id
        self.inputs = dict(inputs or {})
        self.missing_stages = tuple(missing_stages)
        self.started: list[str] = []
        self.completed: list[tuple[str, Any, dict[str, Any]]] = []
        self.failed: list[tuple[str, str]] = []
        self.cancelled: list[tuple[str, str | None]] = []
        self.skipped: list[tuple[str, str | None]] = []
        self.verified: list[str] = []
        self.completed_runs = 0

    def start(self, stage: str, *, now: Any = None) -> None:
        self.started.append(stage)

    def complete(
        self,
        stage: str,
        payload: Any,
        *,
        result_path: str | None = None,
        extra_files: Any = None,
        description: str | None = None,
        now: Any = None,
    ) -> Any:
        path = result_path or f"inputs/{stage}.json"
        self.completed.append(
            (stage, payload, {"result_path": path, "extra_files": dict(extra_files or {})})
        )
        return SimpleNamespace(path=path)

    def fail(self, stage: str, error: str, *, now: Any = None) -> None:
        self.failed.append((stage, error))

    def cancel(self, stage: str, reason: str | None = None, *, now: Any = None) -> None:
        self.cancelled.append((stage, reason))

    def skip(self, stage: str, *, note: str | None = None, now: Any = None) -> None:
        self.skipped.append((stage, note))

    def complete_run(self, *, now: Any = None) -> None:
        self.completed_runs += 1

    def verified_result_path(self, stage: str) -> Path:
        from application.run_stages import StageInputUnavailable

        if stage in self.missing_stages:
            raise StageInputUnavailable(
                f"当前运行 {self.run_id} 的阶段 {stage} 尚未完成，不能作为后续阶段的输入"
            )
        self.verified.append(stage)
        relative = self.inputs.get(stage, f"inputs/{stage}.json")
        return Path("runs") / self.run_id / relative

    def result_paths(self) -> dict[str, str]:
        """返回本次测试写入的阶段结果路径（阶段 → 相对路径）。"""
        return {stage: details["result_path"] for stage, _payload, details in self.completed}


class MappingInputReader:
    """测量输入读取器替身：按阶段返回固定文档，并记下被读取的路径。"""

    def __init__(
        self,
        *,
        cable_loss: Any = None,
        driver_mapping: Any = None,
        error: Exception | None = None,
    ) -> None:
        self.cable_loss = cable_loss
        self.driver_mapping = driver_mapping
        self.error = error
        self.paths: list[Path] = []

    def read_cable_loss(self, path: Any) -> Any:
        self.paths.append(Path(path))
        if self.error is not None:
            raise self.error
        if self.cable_loss is None:
            raise FileNotFoundError(f"线损输入不存在: {path}")
        return self.cable_loss

    def read_driver_mapping(self, path: Any) -> Any:
        self.paths.append(Path(path))
        if self.error is not None:
            raise self.error
        if self.driver_mapping is None:
            raise FileNotFoundError(f"驱动映射输入不存在: {path}")
        return self.driver_mapping


__all__ = [
    "DEFAULT_RUN_ID",
    "contract_run",
    "RecordingStageRecorder",
    "MappingInputReader",
]
