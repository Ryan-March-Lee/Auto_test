"""测量页显式选择阶段结果文件的窗口侧入口。

页面只发出“用户想加载某类结果”的意图；本模块负责打开文件选择器并把选中的路径
交给应用层。文件选择器只给出路径，阶段目录边界、JSON、正式 DTO、类型、状态、
schema 与身份全部由阶段结果仓库校验，这里不扫描目录，也不回退到“最新结果”。
应用层返回的 SHA-256 与大小会随路径一起交回页面：测量启动时据此复核文件在用户
选择之后没有被替换，缺少这两项事实的选择不会变成可启动的输入。
"""

from __future__ import annotations

from collections.abc import Mapping
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


def _result_paths(window: Any) -> Any:
    """当前作用域的结果目录：仿真运行返回仿真路径，正式运行返回真实路径。"""
    coordinator = getattr(window, "coordinator", None)
    provider = getattr(coordinator, "current_result_paths", None)
    if callable(provider):
        return provider()
    return window.paths


def _result_query(window: Any) -> Any:
    """当前作用域的阶段结果查询；不按模式回退到全局真实结果查询。"""
    coordinator = getattr(window, "coordinator", None)
    provider = getattr(coordinator, "current_result_query", None)
    if callable(provider):
        return provider()
    return window.gui_data_service.results


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
        window, results=_result_query(window),
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
        directory=_result_paths(window).cable_loss_results_dir,
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
        directory=_result_paths(window).cable_loss_results_dir,
        show=window.driver_mapping_page.show_loaded_cable_loss,
    )


def load_amplifier_cable_loss(window: Any) -> None:
    """显式选择主功放使用的线损结果；页面回显来源文件与测量时间。"""
    _choose_and_show(
        window, MeasurementStage.CABLE_LOSS,
        title="选择主功放使用的线损测量结果",
        label="主功放使用的线损测量结果",
        directory=_result_paths(window).cable_loss_results_dir,
        show=window.amplifier_page.show_loaded_cable_loss,
    )


def load_amplifier_driver_mapping(window: Any) -> None:
    """显式选择主功放使用的驱动映射结果；与线损选择互不覆盖。"""
    _choose_and_show(
        window, MeasurementStage.DRIVER_MAPPING,
        title="选择主功放使用的驱动映射结果",
        label="主功放使用的驱动映射结果",
        directory=_result_paths(window).driver_mapping_results_dir,
        show=window.amplifier_page.show_loaded_driver_mapping,
    )


#: 阶段完成后需要回填的下游页面：``(页面标签, 页面属性, 回填方法, 选择属性)``。
#: 线损结果同时是驱动映射与主功放的上游校准输入；驱动映射结果只回填主功放。
_SIMULATION_BACKFILL_TARGETS = {
    MeasurementKind.CABLE_LOSS: (
        (
            "驱动映射使用的线损结果",
            "driver_mapping_page",
            "show_loaded_cable_loss",
            "cable_loss_selection",
        ),
        (
            "主功放使用的线损结果",
            "amplifier_page",
            "show_loaded_cable_loss",
            "cable_loss_selection",
        ),
    ),
    MeasurementKind.DRIVER_MAPPING: (
        (
            "主功放使用的驱动映射结果",
            "amplifier_page",
            "show_loaded_driver_mapping",
            "driver_mapping_selection",
        ),
    ),
}

_STAGE_FOR_KIND = {
    MeasurementKind.CABLE_LOSS: MeasurementStage.CABLE_LOSS,
    MeasurementKind.DRIVER_MAPPING: MeasurementStage.DRIVER_MAPPING,
}


def backfill_simulated_calibration(window: Any, reference: Any) -> None:
    """仿真运行阶段完成后，用仿真作用域结果查询按显式路径回填下游校准来源。

    只在仿真运行内生效：正式运行的校准输入仍由用户显式选择，页面不会因为一次测量
    完成就悄悄换掉输入。路径取本次结果自带的 ``archive_path``（阶段结果权威文件），
    不扫描目录、不猜最新文件；文件内容、阶段边界与摘要仍由结果查询实现校验。

    用户手动选择的另一份结果不会被覆盖：只有当前选择为空、或正好指向本次刚完成的
    同一份文件时才替换；保留时在日志中说明原因。回填失败只记录日志，不阻断已经完成
    的测量，也不改写页面的现有选择。
    """
    coordinator = getattr(window, "coordinator", None)
    if coordinator is None or not coordinator.is_simulated_run_active():
        return
    kind = getattr(reference, "kind", None)
    targets = _SIMULATION_BACKFILL_TARGETS.get(kind)
    if not targets:
        return
    value = getattr(reference, "value", None)
    result_reference = getattr(value, "result_reference", None)
    if not isinstance(result_reference, Mapping):
        window.add_log_message("仿真结果缺少结果引用，无法自动回填校准来源")
        return
    path = result_reference.get("archive_path")
    if not isinstance(path, str) or not path.strip():
        window.add_log_message("仿真结果缺少阶段结果路径，无法自动回填校准来源")
        return
    path = path.strip()
    stage = _STAGE_FOR_KIND[kind]
    query = _result_query(window)
    for label, page_attribute, shower_name, selection_attribute in targets:
        page = getattr(window, page_attribute, None)
        if page is None:
            continue
        selection = getattr(page, selection_attribute, None)
        selected_path = getattr(selection, "path", None)
        if selection is not None and selected_path != path:
            window.add_log_message(
                f"{label}保留当前手动选择；本次完成的仿真结果未自动回填"
            )
            continue
        try:
            loaded = query.load_stage_result(stage, path)
            sha256 = getattr(loaded, "sha256", None)
            size_bytes = getattr(loaded, "size_bytes", None)
            if sha256 is None or size_bytes is None:
                raise ValueError("结果缺少文件摘要")
            getattr(page, shower_name)(
                path=path,
                filename=loaded.filename,
                created_at=loaded.result.created_at,
                sha256=sha256,
                size_bytes=size_bytes,
            )
        except (OSError, ValueError) as error:
            logger.warning("仿真校准来源自动回填失败: %s", error)
            window.add_log_message(
                f"{label}自动回填失败（可手动选择）: {error}"
            )
            continue
        window.add_log_message(
            f"{label}已按本次仿真结果自动回填：{loaded.filename}"
        )


__all__ = [
    "JSON_FILTER",
    "backfill_simulated_calibration",
    "choose_stage_result",
    "load_amplifier_cable_loss",
    "load_amplifier_driver_mapping",
    "load_cable_loss_result",
    "load_driver_mapping_cable_loss",
]
