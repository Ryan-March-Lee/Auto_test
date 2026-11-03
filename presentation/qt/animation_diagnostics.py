"""发牌/翻牌动画的运行时诊断（技术债重构步骤 2）。

诊断只写专用日志与内部快照，不进入用户界面，也不靠截图判断动画是否发生。
一次发牌固定记录下面这些字段（名称为契约，后续复现问题时按字段检索）：

``event``
    事件名：``start`` / ``skip`` / ``phase`` / ``first_paint`` / ``finish`` / ``cancel``。
``started_at`` / ``ended_at``
    动画开始、结束的本地时间（ISO 8601，含毫秒与时区）。
``elapsed_ms`` / ``duration_ms``
    已走时长与本次整段时长。
``phase``
    当前阶段：``dealing`` / ``settling`` / ``flipping`` / ``finished`` / ``idle``。
``card_count``
    本次要发的卡片数量。
``front_source``
    发牌正面的来源：``asset``（默认，由 :mod:`presentation.qt.card_assets` 按卡片数据
    渲染）或 ``screenshot``（面板的排障回退，旧 ``QWidget.grab()`` 截图）。
``deck_x`` / ``deck_y``
    发牌起点（覆盖层坐标）。
``target_rects``
    每张卡的落点 ``(x,y,w,h)``，按显示顺序。
``overlay_visible`` / ``overlay_width`` / ``overlay_height`` / ``overlay_parent``
    覆盖层当时的可见性、尺寸与父控件。
``paint_frames`` / ``first_paint_ms`` / ``drawn_cards``
    已绘制的帧数（每个 ``paintEvent`` 计一次）、第一次绘制距开始多少毫秒、第一帧里
    实际画到可见区域（覆盖层与卡片裁剪区之内）的牌面数。
    ``first_paint`` 只说明“真的进入了绘制”，``drawn_cards=0`` 说明画了但画面是空的。
``revealed_count``
    已经翻开的卡片数。
``cancel_reason`` / ``restore_result`` / ``was_running``
    取消原因、取消后卡片的恢复方式（``revealed`` / ``hidden``）以及取消时是否还在播放。
    原因取固定名称：``results-cleared`` / ``new-scan`` / ``cards-refilled`` /
    ``page-shutdown`` / ``surface-changed``（页面根容器尺寸、页面滚动或卡片区视口/滚动
    发生变化） / ``overlay-attached``（动画中重挂覆盖层）。
``skip_reason``
    动画没有启动的原因（例如没有卡片）。``skip`` 只由 :meth:`CardDealAnimation.skip`
    记录，面板不再单独拼一条，避免同一次未启动出现重复事件。

除了发牌时间轴，发现页的流程状态（计划步骤 8）也写进同一份日志，事件名
``discovery_phase``：

```text
animation event=discovery_phase previous=scanning phase=result-pending box_state=busy pending=completed cards=0 outcome=completed
```

字段 ``previous`` / ``phase`` 是转移前后的 ``DiscoveryPhase`` 值，``box_state`` 是这次
转移后结果区的外观状态，``pending`` 是挂起结论的类型（没有时为空），``cards`` 是本次
结果的卡片数（已经建好，或刚拿到结果、进入发牌时即将建好），``outcome`` 是触发这次
转移的事件（``scan-started`` / ``completed`` / ``completed-empty`` / ``failed`` /
``cancelled`` / ``results-cleared`` / ``page-shutdown`` 等）。成功、空结果、失败与
取消因此都能按同一条固定字段序列复现。

``CardDealAnimation.diagnostics()`` 在运行中返回当前快照，在完成/取消后返回那次动画
收尾时的快照（``card_count``、``elapsed_ms``、``phase`` 都与结束事件同一时刻），
不会出现“卡片已清空却还留着取消原因”的矛盾。

据此可以区分三种故障：

- 只有 ``skip``、没有 ``start``：动画没有启动；
- 有 ``start``、没有 ``first_paint``：动画启动了，但覆盖层没有绘制出来；
- ``first_paint`` 里 ``overlay_visible=false``：画了，但覆盖层当时没有显示（父控件
  隐藏、未加入窗口或层级被盖住）；
- 有 ``first_paint`` 但 ``drawn_cards`` 为 0，或用户仍看不到：画面为空或问题出在
  覆盖层之后的可见性、裁剪与层级，而不是时间轴。

日志挂在 ``pa_auto_test.presentation.qt.animation_diagnostics`` 下，按固定字段拼接，
不依赖日志格式化参数，便于直接检索。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from infrastructure.logging.app_logging import get_logger

logger = get_logger(__name__)

#: 日志行里事件的开头标记：``animation event=start ...``。
EVENT_PREFIX = "animation"


def timestamp() -> str:
    """当前本地时间（ISO 8601，含毫秒与时区）。"""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def widget_label(widget: Any) -> str:
    """覆盖层父控件的稳定标签：``QFrame(discoveryResultsBox)``。"""
    if widget is None:
        return "-"
    name = widget.objectName() or "-"
    return f"{type(widget).__name__}({name})"


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.1f}"
    if isinstance(value, (tuple, list)):
        return "(" + "|".join(_format_value(item) for item in value) + ")"
    if value is None:
        return "-"
    return str(value)


def log_event(event: str, **fields: Any) -> None:
    """写一条固定字段格式的动画诊断事件。"""
    pairs = [f"event={event}"]
    pairs.extend(f"{name}={_format_value(fields[name])}" for name in sorted(fields))
    logger.info("%s %s", EVENT_PREFIX, " ".join(pairs))


__all__ = ["EVENT_PREFIX", "log_event", "timestamp", "widget_label"]
