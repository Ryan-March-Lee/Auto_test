"""已停用的旧线损测量根入口。

正式入口是 :func:`app.gui_runtime.create_cable_loss_measurement` 与
:class:`application.measurements.CableLossUseCase`。本模块只保留到步骤 4 删除文件，
任何构造都会立即失败，不读取配置、不连接仪器、不创建运行目录或写结果。
"""

from __future__ import annotations

from infrastructure.filesystem.paths import PathService


class CableLossMeasurement:
    def __init__(
        self,
        config_path=None,
        run_id=None,
        sleep_fn=None,
        measurement_port=None,
        paths: PathService | None = None,
    ):
        raise RuntimeError(
            "CableLossMeasurement 旧入口已停用：请通过 "
            "app.gui_runtime.create_cable_loss_measurement() 创建带运行契约的正式线损测量"
        )


__all__ = ["CableLossMeasurement"]
