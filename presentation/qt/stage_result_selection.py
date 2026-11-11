"""测量页显式选择阶段结果文件的窗口侧入口。

页面只发出“用户想加载某类结果”的意图；本模块负责打开文件选择器并把选中的路径
交给应用层。文件选择器只给出路径，阶段目录边界、JSON、正式 DTO、类型、状态、
schema 与身份全部由阶段结果仓库校验，这里不扫描目录，也不回退到“最新结果”。
应用层返回的 SHA-256 与大小会随路径一起交回页面：测量启动时据此复核文件在用户
选择之后没有被替换，缺少这两项事实的选择不会变成可启动的输入。
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtWidgets import QFileDialog, QWidget

from application.ports.gui_data_service import LoadedResultDto
from application.ports.stage_result_repository import MeasurementStage
from infrastructure.logging.app_logging import get_logger

from .measurement_state import MeasurementKind, MeasurementResultReference

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


def _choose(
    window: Any,
    stage: MeasurementStage,
    *,
    title: str,
    label: str,
    directory: Any,
) -> tuple[str, LoadedResultDto] | None:
    """四个入口共用的“选文件并按显式路径加载”；取消或失败返回 ``None``。"""
    return choose_stage_result(
        window, results=window.gui_data_service.results,
        directory=directory, stage=stage, title=title, label=label,
        log=window.add_log_message,
    )


def _choose_and_show(
    window: Any,
    stage: MeasurementStage,
    *,
    title: str,
    label: str,
    directory: Any,
    show: Callable[..., Any],
) -> None:
    """选文件、按显式路径加载，再把来源信息与文件事实交回页面。

    页面保存路径、来源显示信息和选择时的摘要；取消或失败保持页面原样。缺少摘要说明
    查询实现没有返回可复核的文件事实，这份选择不能进入测量启动链路，因此按加载失败
    处理，而不是留下一个“看起来能开始”的空壳选择。
    """
    selected = _choose(window, stage, title=title, label=label, directory=directory)
    if selected is None:
        return
    path, loaded = selected
    sha256 = getattr(loaded, "sha256", None)
    size_bytes = getattr(loaded, "size_bytes", None)
    if sha256 is None or size_bytes is None:
        logger.warning("阶段结果 %s 缺少文件摘要，不能作为测量输入", loaded.filename)
        window.add_log_message(f"加载{label}失败: 结果缺少文件摘要")
        return
    show(
        path=path,
        filename=loaded.filename,
        created_at=loaded.result.created_at,
        sha256=sha256,
        size_bytes=size_bytes,
    )


def load_cable_loss_result(window: Any) -> None:
    """由用户显式选择线损页展示的结果文件；取消或失败都保留当前显示。"""
    selected = _choose(
        window, MeasurementStage.CABLE_LOSS,
        title="选择线损测量结果", label="线损测量结果",
        directory=window.paths.cable_loss_results_dir,
    )
    if selected is None:
        return
    _path, loaded = selected
    window.cable_loss_page.show_loaded_result(
        MeasurementResultReference(
            result_id=loaded.result_id,
            kind=MeasurementKind.CABLE_LOSS,
            value=loaded.result,
            source="stage_result",
        ),
        filename=loaded.filename,
    )


def load_driver_mapping_cable_loss(window: Any) -> None:
    """显式选择驱动映射使用的线损结果；页面回显来源文件与测量时间。"""
    _choose_and_show(
        window, MeasurementStage.CABLE_LOSS,
        title="选择驱动映射使用的线损测量结果",
        label="驱动映射使用的线损测量结果",
        directory=window.paths.cable_loss_results_dir,
        show=window.driver_mapping_page.show_loaded_cable_loss,
    )


def load_amplifier_cable_loss(window: Any) -> None:
    """显式选择主功放使用的线损结果；页面回显来源文件与测量时间。"""
    _choose_and_show(
        window, MeasurementStage.CABLE_LOSS,
        title="选择主功放使用的线损测量结果",
        label="主功放使用的线损测量结果",
        directory=window.paths.cable_loss_results_dir,
        show=window.amplifier_page.show_loaded_cable_loss,
    )


def load_amplifier_driver_mapping(window: Any) -> None:
    """显式选择主功放使用的驱动映射结果；与线损选择互不覆盖。"""
    _choose_and_show(
        window, MeasurementStage.DRIVER_MAPPING,
        title="选择主功放使用的驱动映射结果",
        label="主功放使用的驱动映射结果",
        directory=window.paths.driver_mapping_results_dir,
        show=window.amplifier_page.show_loaded_driver_mapping,
    )


__all__ = [
    "JSON_FILTER",
    "choose_stage_result",
    "load_amplifier_cable_loss",
    "load_amplifier_driver_mapping",
    "load_cable_loss_result",
    "load_driver_mapping_cable_loss",
]
