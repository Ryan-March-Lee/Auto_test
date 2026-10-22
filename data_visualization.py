"""数据可视化的历史兼容入口。

正式的纯分析与报告生成位于 :mod:`analysis.visualization`；保留旧路径式调用契约的
装配适配器位于 :mod:`app.legacy_visualization`。本模块只重新导出历史公开符号，不含
任何绘图、文件读写或隐式初始化。
"""

from app.legacy_visualization import (
    DataVisualization,
    load_measurement_result,
    load_result_model,
)


__all__ = ["DataVisualization", "load_measurement_result", "load_result_model"]
