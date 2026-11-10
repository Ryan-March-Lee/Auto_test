"""测量页失败产物的补生成转发。

测量页只发出“对某次运行补生成交付产物”的请求；本模块把请求转给历史运行用例，
并在成功时把归档结果交回发起请求的测量页。窗口只接线，不读路径、不判断产物状态。

失败原因统一转成用户可见文本：调用方负责写日志与对话框，页面提示条保持可见，
用户可以再次尝试。补生成使用已保存的正式结果，不会重跑测量。
"""

from __future__ import annotations

from typing import Any, Callable

from application.dto.archival import ArchivalStatus


def repair_failed_artifacts(
    service: Any,
    run_id: str,
    *,
    page_for_stage: Callable[[str], Any],
) -> tuple[Any | None, str | None]:
    """执行一次补生成，返回 ``(归档结果, 失败原因)``。

    ``service`` 是历史运行用例端口；``page_for_stage`` 按归档结果的阶段取回页面。
    成功时页面刷新提示条与日志；失败时不改动页面状态，也不把错误吞成成功。
    """
    if service is None or not callable(
        getattr(service, "regenerate_failed_artifacts", None)
    ):
        return None, "未装配历史运行服务，无法补生成交付产物"
    try:
        outcome = service.regenerate_failed_artifacts(run_id)
    except (OSError, ValueError, RuntimeError) as error:
        return None, str(error) or type(error).__name__
    page = page_for_stage(getattr(outcome, "stage", ""))
    if page is not None and callable(getattr(page, "show_archival", None)):
        page.show_archival(outcome)
    if outcome.archive_status is ArchivalStatus.PARTIAL:
        # 用例只在归档契约失败时抛异常；产物再次生成失败仍会返回结果，不能报成功。
        return outcome, "补生成后仍有交付产物未生成，请查看实时日志中的失败原因"
    return outcome, None


__all__ = ["repair_failed_artifacts"]
