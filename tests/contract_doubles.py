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


__all__ = ["DEFAULT_RUN_ID", "contract_run"]
