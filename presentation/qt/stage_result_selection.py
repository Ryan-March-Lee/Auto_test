"""测量页显式选择阶段结果文件的窗口侧入口。

页面只发出“用户想加载某类结果”的意图；本模块负责打开文件选择器并把选中的路径
交给应用层。文件选择器只给出路径，阶段目录边界、JSON、正式 DTO、类型、状态、
schema 与身份全部由阶段结果仓库校验，这里不扫描目录，也不回退到“最新结果”。
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtWidgets import QFileDialog, QWidget

from application.ports.gui_data_service import LoadedResultDto
from application.ports.stage_result_repository import MeasurementStage
from infrastructure.logging.app_logging import get_logger

logger = get_logger(__name__)

JSON_FILTER = "JSON 文件 (*.json)"


def choose_stage_result(
    parent: QWidget,
    *,
    results: Any,
    directory: Any,
    stage: MeasurementStage | str,
    title: str,
    label: str,
    log: Callable[[str], Any],
) -> tuple[str, LoadedResultDto] | None:
    """选择并加载一个阶段结果文件；用户取消或加载失败时返回 ``None``。

    可预期的失败只有两类：用户取消选择，以及文件不可读/不是对应阶段的正式结果
    （``OSError``/``ValueError``）；两者都通过日志呈现，不吞掉其他程序错误。
    调用方在返回 ``None`` 时保持页面原样，不覆盖已有选择。
    """
    path, _selected_filter = QFileDialog.getOpenFileName(
        parent, title, str(directory), JSON_FILTER
    )
    if not path:
        log(f"已取消选择{label}")
        return None
    try:
        loaded = results.load_stage_result(stage, path)
    except (OSError, ValueError) as error:
        logger.warning("加载%s失败: %s", label, error)
        log(f"加载{label}失败: {error}")
        return None
    return path, loaded


__all__ = ["JSON_FILTER", "choose_stage_result"]
