"""已停用的旧主功放装配入口。

正式入口是 :func:`app.gui_runtime.create_amplifier_measurement` 与
:class:`application.measurements.AmplifierMeasurementUseCase`。本模块只保留到步骤 4 删除文件，
任何构造都会立即失败，不读取配置、不连接仪器、不创建运行目录或写结果。
"""

from __future__ import annotations

from typing import Optional

from infrastructure.filesystem.paths import PathService


class AmplifierMeasurement:
    """旧 ``AmplifierMeasurement`` 名称的停用守卫。"""

    def __init__(
        self,
        config_path=None,
        loss_data_path=None,
        driver_mapping_path: Optional[str] = None,
        run_id: Optional[str] = None,
        sleep_fn=None,
        measurement_port=None,
        paths: PathService | None = None,
    ):
        raise RuntimeError(
            "AmplifierMeasurement 旧入口已停用：请通过 "
            "app.gui_runtime.create_amplifier_measurement() 创建带运行契约的正式主功放测量"
        )


__all__ = ["AmplifierMeasurement"]
