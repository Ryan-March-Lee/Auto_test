"""发牌与翻牌动画：只认资产、落点和顺序，不认卡片控件或页面状态。

技术债重构步骤 7：原来 ``DealRevealLayer`` 既画发牌画面，又持有 ``InstrumentCard``
控件、在翻到某一张时替页面恢复控件。现在画面被拆成这一层的独立对象：

.. code-block:: text

    CardDealItem
    ├── asset        # CardAsset：正面、牌背与几何
    ├── target_rect  # 落点（覆盖层坐标），开始时冻结
    └── index        # 这一张的身份，翻到时通过 card_revealed 交回页面

:class:`CardDealAnimation` 只负责时间轴与绘制：

- 不访问扫描结果、页面状态或页面布局；
- 不持有任何 ``QWidget``，也不调用 ``QWidget.grab()``；
- 翻到哪一张就发 :attr:`CardDealAnimation.card_revealed`，由页面自己把真实卡片
  恢复显示；全部翻完后发 :attr:`CardDealAnimation.finished`，页面据此恢复交互；
- ``cancel(reveal=True)`` 同样只发 ``card_revealed``，不替页面改控件。

反方向是 :meth:`CardDealAnimation.start_return`：按“返回”时严格分两段，先把同一组
卡片全部翻回背面，再沿同一条弧线一起飞回牌堆。收牌不交回 ``card_revealed``（页面
已经清空结果），只在自然播完后发 ``finished``；诊断里的 ``direction`` 区分两个方向。

因此它可以在没有扫描线程、没有发现页的情况下单独播放：建一块宿主控件、给几份
:class:`~presentation.qt.card_assets.CardAsset` 和目标矩形即可。牌背取
``CardAsset.back``（绿色光子卡面，与等待动画最上面那张牌同一张位图），正面取
``CardAsset.front``；``CardAsset.back_particles`` 打开时，牌背朝上的每一帧再按相位叠
一层外飘的绿色粒子（画法与等待动画共用
:func:`presentation.qt.photon_card.paint_photon_particles`）。发牌层因此不再依赖按钮
卡面或等待动画的内部绘制函数。

坐标约定与步骤 6 一致：``deck_origin``、``target_rect`` 与 ``clip_rect`` 都是
**覆盖层坐标**。面板在动画开始前一次性换算好，动画期间不再逐帧 ``mapTo`` 追踪
卡片控件；页面根容器或卡片区变化时由调用方取消，而不是让画面跟着布局漂移。

``clip_rect`` 只约束**已经落定**的牌（挡住滚动后看不见的结果行）：牌堆按设计就在
卡片视口之外（它继承“查找设备”按钮的位置），飞行段从牌堆出发、必须整段可见，否则
牌会在卡片区边界上被平切成“从外面进来”的样子。落点自己就在视口外的牌不画，
免得飞行轨迹压到状态行/动作行上。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from .animation_diagnostics import log_event, timestamp, widget_label
from .card_assets import CardAsset
from .photon_card import PARTICLE_PERIOD_MS, paint_photon_particles

__all__ = [
    "CardDealAnimation",
    "CardDealItem",
    "DEAL_FLY_MS",
    "DEAL_STAGGER_MS",
    "DEAL_SETTLE_MS",
    "FLIP_MS",
    "FLIP_STAGGER_MS",
    "DEAL_FRAME_MS",
    "DEAL_PHASE_IDLE",
    "DEAL_PHASE_DEALING",
    "DEAL_PHASE_SETTLING",
    "DEAL_PHASE_FLIPPING",
    "DEAL_PHASE_FINISHED",
    "DEAL_PHASE_FLIPPING_BACK",
    "DEAL_PHASE_RETURNING",
    "DEAL_FRONT_SOURCE_ASSET",
    "DEAL_FRONT_SOURCE_SCREENSHOT",
    "DIRECTION_DEAL",
    "DIRECTION_RETURN",
    "RETURN_FLIP_MS",
    "RETURN_FLIP_STAGGER_MS",
    "RETURN_FLY_MS",
    "RETURN_FLY_STAGGER_MS",
]

# ---- 时间轴 ------------------------------------------------------------------

#: 每张牌从牌堆飞到结果位的时长。
DEAL_FLY_MS = 320
#: 相邻两张开始发牌的间隔：牌是一张张出去的，不是整把一起飞。
DEAL_STAGGER_MS = 70
#: 飞行弧线在起点与落点连线中点之上再抬多高（px）：从下方牌堆抛上来，有一点弧。
DEAL_ARC_LIFT = 36.0
#: 起飞时的缩放与角度：牌从牌堆里被抽出来时略小、略歪。
DEAL_START_SCALE = 0.84
DEAL_START_ANGLE = 7.0
#: 最后一张落定后停一下，再开始翻第一张。
DEAL_SETTLE_MS = 140
#: 一张牌翻面的时长与相邻两张开始翻的间隔。
FLIP_MS = 260
FLIP_STAGGER_MS = 105
#: 发牌动画的帧间隔（约 60fps）：每帧只旋转/缩放着贴图。
DEAL_FRAME_MS = 16
#: 收牌（按“返回”）时间轴，严格分成两段：先把所有牌翻回背面，再一起飞回牌堆。
#: 翻面阶段与飞行阶段各自有“一张的时长”和“相邻两张的间隔”。最后一张先翻，
#: 全部翻完后最后一张先飞，回到牌堆的牌按顺序叠回去。
RETURN_FLIP_MS = 230
RETURN_FLIP_STAGGER_MS = 60
RETURN_FLY_MS = 300
RETURN_FLY_STAGGER_MS = 55
#: 牌背上绿色粒子的相位错开量（粒子一轮是 :data:`photon_card.PARTICLE_PERIOD_MS`）：
#: 相邻两张差 0.13 轮，六张牌不会一起亮、一起灭。
_BACK_PARTICLE_PHASE_STEP = 0.13
#: 牌堆最多叠几张：发走一张，剩下的少一层，看得出“牌是从这叠里出去的”。
DEAL_DECK_DEPTH = 3
#: 发牌/翻牌阶段名：运行时日志与 :meth:`CardDealAnimation.diagnostics` 使用固定名称。
DEAL_PHASE_IDLE = "idle"
DEAL_PHASE_DEALING = "dealing"
DEAL_PHASE_SETTLING = "settling"
DEAL_PHASE_FLIPPING = "flipping"
DEAL_PHASE_FINISHED = "finished"
#: 收牌（按“返回”）的第一段：所有牌先原地翻回背面。
DEAL_PHASE_FLIPPING_BACK = "flipping-back"
#: 收牌（按“返回”）的第二段：翻完的牌沿发牌弧线飞回牌堆。
DEAL_PHASE_RETURNING = "returning"
#: 动画方向（诊断字段 ``direction``）：``deal`` 是发牌/翻牌，``return`` 是收牌。
DIRECTION_DEAL = "deal"
DIRECTION_RETURN = "return"
#: 发牌正面的来源（诊断字段 ``front_source``）：默认由卡片资产渲染；``screenshot``
#: 是面板的排障回退（旧 ``QWidget.grab()`` 截图），不作为长期路径。
DEAL_FRONT_SOURCE_ASSET = "asset"
DEAL_FRONT_SOURCE_SCREENSHOT = "screenshot"

# ---- 投影 --------------------------------------------------------------------

#: 用几层逐渐收窄的描边叠出投影柔边（Qt 没有便宜的模糊）。
_SHADOW_LAYERS = 5
#: 投影相对卡片向下偏移的像素。
_SHADOW_DROP = 3


@dataclass(frozen=True)
class CardDealItem:
    """发牌动画的一张牌：画面、落点与身份，全是只读数据。

    ``index`` 是这一张在整组里的位置，必须构成 ``0..N-1`` 的连续编号（面板里就是
    卡片在结果区里的显示顺序）。动画按 ``index`` 排序发牌、翻牌，并在翻开时通过
    :attr:`CardDealAnimation.card_revealed` 原样交回，页面据此恢复对应的真实卡片；
    编号必须连续，页面才能把交回的 ``index`` 直接当作卡片列表的下标。``target_rect``
    是覆盖层坐标；动画开始时复制一份，之后调用方再改也不会影响已经开始的画面。
    """

    asset: CardAsset
    target_rect: QRectF
    index: int


def _ease_out_cubic(phase: float) -> float:
    """发牌飞行的缓动：起步快、落点慢，牌是“放”到结果位上的。"""
    phase = max(0.0, min(1.0, float(phase)))
    return 1.0 - (1.0 - phase) ** 3


@lru_cache(maxsize=16)
def _render_shadow(
    width: int, height: int, radius: float, margin: int, ratio: float
) -> QPixmap:
    """一张卡片的投影：比卡片四边各大 ``margin``，每帧只平移着贴。

    资产（:class:`~presentation.qt.card_assets.CardAsset`）只含卡片本身，投影要画在
    卡片外面，因此动画按 ``shadow_margin`` 预留边距并在这里预渲染一次。键里带屏幕
    像素比，高分屏不会把柔边贴成糊的。
    """
    pad = max(0, int(margin))
    pixmap = QPixmap(
        QSize(
            max(1, round((width + 2 * pad) * ratio)),
            max(1, round((height + 2 * pad) * ratio)),
        )
    )
    pixmap.fill(Qt.transparent)
    # 带 dpr 的画布由 Qt 换算成逻辑坐标，这里不再自己缩放一次。
    pixmap.setDevicePixelRatio(ratio)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.translate(float(pad), float(pad))
    card = QRectF(0.0, 0.0, float(width), float(height))
    painter.setBrush(Qt.NoBrush)
    for step in range(_SHADOW_LAYERS):
        width_px = 3.0 + step * 3.5
        painter.setPen(QPen(QColor(0, 0, 0, 30 - step * 6), width_px))
        painter.drawRoundedRect(card, radius, radius)
    painter.end()
    return pixmap


@lru_cache(maxsize=8)
def _card_silhouette(width: float, height: float, radius: float) -> QPainterPath:
    """卡片圆角轮廓（只读缓存）：牌背粒子每帧都按它裁一次，不重画路径。"""
    path = QPainterPath()
    path.addRoundedRect(QRectF(0.0, 0.0, width, height), radius, radius)
    return path


class CardDealAnimation(QWidget):
    """发牌与翻牌：从牌堆往目标槽位发牌，背面朝上，再逐张翻开。

    接口只有几件事：:meth:`start` 给一组 :class:`CardDealItem` 与牌堆起点，
    :meth:`cancel` / :meth:`finish` 收尾，:attr:`finished` 表示整段走完，
    :attr:`card_revealed` 表示“这一张翻开了、可以显示真实卡片”。它只是画面：
    不吃鼠标（``WA_TransparentForMouseEvents``）、不占焦点、不进布局。

    动作：牌从 ``deck_origin`` 一张张飞向各自的 ``target_rect``（背面朝上），全部
    落定后停一下，再按 ``index`` 从小到大逐张翻面，把 ``asset.front`` 露出来。
    翻牌仍是原来的二维宽度压缩，不追求三维效果。:meth:`start_return` 播放相反的
    收牌画面，严格分成两段：先按 ``index`` 从大到小把所有牌翻回背面，再按同样顺序
    沿同一条弧线一起飞回 ``deck_origin``。

    收尾有两条路，都会停掉计时器、收起覆盖层并清理本次输入：

    - :meth:`finish`（自然播完或调用方要求立刻走完）：先为每张牌发
      :attr:`card_revealed`，再发 :attr:`finished`；
    - :meth:`cancel`：``reveal=True`` 时同样为每张牌发 :attr:`card_revealed`，
      让页面把卡片恢复显示；``reveal=False`` 用于卡片即将被删除。

    被页面藏起来（切页、标签页切走、窗口最小化）时也走 ``cancel(reveal=True)``：通用
    过渡约定是“看不见的动画立即收尾”，计时器不留给隐藏的页面；这一步由动画的宿主
    （页面覆盖层生命周期或发现面板）在隐藏事件里调用。

    空输入（没有 item）走 :meth:`skip`：它先把还在播的一次按完成路径收尾，再记录
    “这一次没有牌”，因此页面不会因为空结果而留下透明的卡片。

    动画本身不保存控件，因此中断、返回或关闭页面之后不会留下对卡片的悬空引用，
    也不会替页面修改透明度效果；这些都由页面在信号回调里统一处理。
    """

    #: 所有牌都翻开、覆盖层收起时发出。
    finished = Signal()
    #: 第 ``index`` 张牌翻开了（``index`` 就是 :attr:`CardDealItem.index`）。
    card_revealed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("cardDealAnimation")
        # 动画不是控件：点击、悬停与键盘焦点都留给下面的卡片与按钮。
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)
        #: 本次要发的牌（按 ``index`` 排序）：只含资产、落点与身份，没有控件。
        self._items: list[CardDealItem] = []
        #: 已经翻开的牌：翻完就通知页面恢复对应的真实卡片，不再由覆盖层画。
        self._revealed: list[bool] = []
        #: 牌堆在覆盖层坐标里的中心（发牌起点 / 收牌终点）。
        self._deck = QPointF()
        #: 收牌时牌堆卡片的尺寸；``None`` 表示沿用品第一张牌的资产尺寸。
        self._deck_extent: tuple[float, float] | None = None
        #: 卡片区的可见矩形（覆盖层坐标），开始时冻结；结果多到要滚动时，
        #: 看不见的行不会画到状态行/动作行上。
        self._clip: QRectF | None = None
        self._elapsed = 0.0
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(DEAL_FRAME_MS)
        self._timer.timeout.connect(self._advance)
        #: 运行时诊断（步骤 2）：开始/结束时间、阶段、绘制帧数与取消原因。
        #: 只写日志与 :meth:`diagnostics` 快照，不显示给最终用户。
        self._started_at: str | None = None
        self._ended_at: str | None = None
        self._phase = DEAL_PHASE_IDLE
        self._paint_frames = 0
        self._first_paint_ms: float | None = None
        self._drawn_cards = 0
        self._first_drawn_cards = 0
        self._cancel_reason: str | None = None
        self._restore_result: str | None = None
        self._last_event = DEAL_PHASE_IDLE
        #: 本次动画方向：``deal``（发牌/翻牌）或 ``return``（收牌回牌堆）。
        self._direction = DIRECTION_DEAL
        #: 本次发牌正面的来源：``asset``（默认，卡片资产渲染）或 ``screenshot``
        #: （面板的排障回退，旧 ``QWidget.grab()`` 截图）。只进日志与诊断快照。
        self._front_source = DEAL_FRONT_SOURCE_ASSET
        #: 最近一次动画收尾时的快照：取消/完成后本次输入会被清空，
        #: 快照保证 :meth:`diagnostics` 仍能给出同一时刻的完整数据。
        self._last_run: dict[str, Any] | None = None

    # ---- 时间轴 -------------------------------------------------------------

    def is_running(self) -> bool:
        """发牌/翻牌是否正在走。"""
        return self._timer.isActive()

    @property
    def revealed_count(self) -> int:
        """已经翻开的张数（测试与收尾用）。"""
        return sum(1 for revealed in self._revealed if revealed)

    def item_count(self) -> int:
        """本次要发的牌数；没有在播时为 0。"""
        return len(self._items)

    def direction(self) -> str:
        """本次动画方向：``deal`` 或 ``return``。"""
        return self._direction

    def duration_ms(self) -> float:
        """这一次动画的整段时长；没有牌时为 0。"""
        count = len(self._items)
        if count <= 0:
            return 0.0
        if self._direction == DIRECTION_RETURN:
            return float(
                self._return_flip_end_ms()
                + (count - 1) * RETURN_FLY_STAGGER_MS
                + RETURN_FLY_MS
            )
        deal_end = (count - 1) * DEAL_STAGGER_MS + DEAL_FLY_MS
        return float(
            deal_end + DEAL_SETTLE_MS + (count - 1) * FLIP_STAGGER_MS + FLIP_MS
        )

    def _return_flip_end_ms(self) -> float:
        """收牌第一段（全部翻回背面）结束的时刻。"""
        count = len(self._items)
        if count <= 0:
            return 0.0
        return float((count - 1) * RETURN_FLIP_STAGGER_MS + RETURN_FLIP_MS)

    def phase_at(self, elapsed_ms: float | None = None) -> str:
        """按已走时长判断阶段。

        发牌：``dealing`` → ``settling`` → ``flipping`` → ``finished``；
        收牌：``flipping-back`` → ``returning`` → ``finished``。
        """
        count = len(self._items)
        if count <= 0:
            return DEAL_PHASE_IDLE
        elapsed = self._elapsed if elapsed_ms is None else float(elapsed_ms)
        if self._direction == DIRECTION_RETURN:
            if elapsed < self._return_flip_end_ms():
                return DEAL_PHASE_FLIPPING_BACK
            if elapsed < self.duration_ms():
                return DEAL_PHASE_RETURNING
            return DEAL_PHASE_FINISHED
        deal_end = (count - 1) * DEAL_STAGGER_MS + DEAL_FLY_MS
        if elapsed < deal_end:
            return DEAL_PHASE_DEALING
        if elapsed < deal_end + DEAL_SETTLE_MS:
            return DEAL_PHASE_SETTLING
        if elapsed < self.duration_ms():
            return DEAL_PHASE_FLIPPING
        return DEAL_PHASE_FINISHED

    def diagnostics(self) -> dict[str, Any]:
        """最近一次动画的诊断快照（字段名见 :mod:`presentation.qt.animation_diagnostics`）。

        运行中返回当前值；结束或取消后返回那次动画收尾时的快照，因此 ``card_count``、
        ``elapsed_ms``、``phase`` 与 ``event=cancel`` / ``event=finish`` 是同一时刻的
        数据（不会出现“卡片已清空却还留着取消原因”的矛盾）。快照只给日志与测试看，
        不进入用户界面。
        """
        if self._last_run is not None:
            return dict(self._last_run)
        return self._live_snapshot()

    def _live_snapshot(self) -> dict[str, Any]:
        """当场读出运行中的诊断字段。"""
        return {
            "event": self._last_event,
            "running": self.is_running(),
            "phase": self.phase_at(),
            "started_at": self._started_at,
            "ended_at": self._ended_at,
            "elapsed_ms": float(self._elapsed),
            "duration_ms": self.duration_ms(),
            "card_count": len(self._items),
            "direction": self._direction,
            "front_source": self._front_source,
            "deck_x": float(self._deck.x()),
            "deck_y": float(self._deck.y()),
            "target_rects": self._target_labels(),
            "overlay_visible": self.isVisible(),
            "overlay_width": int(self.width()),
            "overlay_height": int(self.height()),
            "overlay_parent": widget_label(self.parentWidget()),
            "paint_frames": self._paint_frames,
            "first_paint_ms": self._first_paint_ms,
            "drawn_cards": self._drawn_cards,
            "first_drawn_cards": self._first_drawn_cards,
            "revealed_count": self.revealed_count,
            "cancel_reason": self._cancel_reason,
            "restore_result": self._restore_result,
        }

    def _capture_run_end(self, event: str) -> None:
        """在清空本次动画状态之前留下最终快照，供 :meth:`diagnostics` 读取。"""
        self._last_event = event
        snapshot = self._live_snapshot()
        snapshot["event"] = event
        self._last_run = snapshot

    def _record(self, event: str, **fields: Any) -> None:
        """更新内部快照并写一条固定字段的诊断日志。"""
        self._last_event = event
        log_event(event, **fields)

    def _target_labels(self) -> tuple[str, ...]:
        """每张卡的落点 ``(x,y,w,h)``，供日志与快照使用（不参与绘制）。"""
        labels = []
        for item in self._items:
            rect = item.target_rect
            labels.append(
                f"({rect.x():.1f},{rect.y():.1f},"
                f"{rect.width():.1f},{rect.height():.1f})"
            )
        return tuple(labels)

    def _sync_phase(self) -> None:
        """阶段变了就记一条日志：``dealing`` / ``settling`` / ``flipping``。"""
        phase = self.phase_at()
        if phase == self._phase:
            return
        self._phase = phase
        self._record(
            "phase",
            phase=phase,
            elapsed_ms=float(self._elapsed),
            card_count=len(self._items),
            direction=self._direction,
        )

    def _record_paint(self, drawn_cards: int) -> None:
        """每一帧真的进入绘制就计数；第一帧额外记 ``first_paint``。

        ``drawn_cards`` 是**最近一帧**里实际画到可见区域（覆盖层与卡片裁剪区之内）的
        牌面数，每帧刷新；``first_drawn_cards`` 保留第一帧的值，供 ``first_paint``
        诊断使用。两者为 0 都表示那一帧是空的（贴图为空或全在可见区域之外）。
        """
        if self._phase in (DEAL_PHASE_IDLE, DEAL_PHASE_FINISHED):
            return
        self._paint_frames += 1
        self._drawn_cards = int(drawn_cards)
        if self._first_paint_ms is not None:
            return
        self._first_drawn_cards = int(drawn_cards)
        self._first_paint_ms = (
            float(self._clock.elapsed()) if self._clock.isValid() else 0.0
        )
        self._record(
            "first_paint",
            first_paint_ms=self._first_paint_ms,
            drawn_cards=self._first_drawn_cards,
            card_count=len(self._items),
            overlay_visible=self.isVisible(),
            overlay_width=int(self.width()),
            overlay_height=int(self.height()),
            overlay_parent=widget_label(self.parentWidget()),
            phase=self._phase,
        )

    def deal_progress(self, index: int, elapsed_ms: float | None = None) -> float:
        """第 ``index`` 张牌飞到了哪里（0 刚离开牌堆，1 已落定）。"""
        if index < 0 or index >= len(self._items):
            return 0.0
        elapsed = self._elapsed if elapsed_ms is None else float(elapsed_ms)
        local = elapsed - index * DEAL_STAGGER_MS
        return max(0.0, min(1.0, local / DEAL_FLY_MS))

    def flip_progress(self, index: int, elapsed_ms: float | None = None) -> float:
        """第 ``index`` 张牌翻到了哪里（0 还没动，0.5 侧对屏幕，1 已翻开）。"""
        if index < 0 or index >= len(self._items):
            return 0.0
        elapsed = self._elapsed if elapsed_ms is None else float(elapsed_ms)
        count = len(self._items)
        deal_end = (count - 1) * DEAL_STAGGER_MS + DEAL_FLY_MS
        start = deal_end + DEAL_SETTLE_MS + index * FLIP_STAGGER_MS
        return max(0.0, min(1.0, (elapsed - start) / FLIP_MS))

    def return_flip_progress(
        self, index: int, elapsed_ms: float | None = None
    ) -> float:
        """收牌时第 ``index`` 张翻回背面到了哪里（0 还是正面，1 已是背面）。"""
        if index < 0 or index >= len(self._items):
            return 0.0
        elapsed = self._elapsed if elapsed_ms is None else float(elapsed_ms)
        slot = len(self._items) - 1 - index
        start = slot * RETURN_FLIP_STAGGER_MS
        return max(0.0, min(1.0, (elapsed - start) / RETURN_FLIP_MS))

    def return_fly_progress(
        self, index: int, elapsed_ms: float | None = None
    ) -> float:
        """收牌时第 ``index`` 张飞回牌堆到了哪里（0 还在结果位，1 已落回牌堆）。"""
        if index < 0 or index >= len(self._items):
            return 0.0
        elapsed = self._elapsed if elapsed_ms is None else float(elapsed_ms)
        slot = len(self._items) - 1 - index
        start = self._return_flip_end_ms() + slot * RETURN_FLY_STAGGER_MS
        return max(0.0, min(1.0, (elapsed - start) / RETURN_FLY_MS))

    def targets(self) -> tuple[QRectF, ...]:
        """本次动画冻结的落点（覆盖层坐标）；没有在播时为空。"""
        return tuple(QRectF(item.target_rect) for item in self._items)

    def clip_rect(self) -> QRectF | None:
        """本次动画冻结的裁剪遮罩（覆盖层坐标）；没有裁剪时为空。"""
        return QRectF(self._clip) if self._clip is not None else None

    def refresh_clip_rect(self, clip_rect: QRectF | None) -> None:
        """只换裁剪遮罩（卡片区可见区域），不改变任何冻结的落点。

        用于布局落定后视口尺寸微调、而卡片位置没变的场景：画面继续按原落点播完，
        但不会画到新的状态行/动作行上。
        """
        if not self._items:
            return
        self._clip = QRectF(clip_rect) if clip_rect is not None else None
        self.update()

    def refresh_deck_origin(self, deck_origin: QPointF) -> None:
        """收牌期间只换牌堆目的地（返回后的版面再落定一次时用），落点不变。

        发牌方向没有“目的地后补”的场景，调用会被忽略；收牌时牌堆与按钮必须在同一
        位置，布局晚一拍落定时用它把牌堆钉到按钮的最终位置。
        """
        if not self._items or self._direction != DIRECTION_RETURN:
            return
        self._deck = QPointF(deck_origin)
        self.update()

    # ---- 生命周期 -----------------------------------------------------------

    def start(
        self,
        items: Sequence[CardDealItem],
        deck_origin: QPointF,
        *,
        clip_rect: QRectF | None = None,
        front_source: str = DEAL_FRONT_SOURCE_ASSET,
    ) -> None:
        """从 ``deck_origin`` 开始发 ``items``；每个 item 自带资产与落点。

        ``items`` 的 ``index`` 必须是 ``0..N-1`` 的连续编号：动画按它排序，翻开时也把
        它交回页面，页面因此能直接用它索引自己的卡片列表；重复、负数或中间缺口都会
        让“翻开的第几张”与页面上的卡片对不上。没有 item 时走 :meth:`skip`（若上一次
        发牌还在播，会先按完成路径把卡片交回页面），不需要资产。

        ``clip_rect`` 是卡片区的可见视口（覆盖层坐标）；省略时不裁剪。它只约束已经
        落定的牌：牌堆与飞行段整段可见，否则牌会从卡片区边界平切进来。布局落定后
        只有可见区域变化（卡片没动）时，调用方用 :meth:`refresh_clip_rect` 换遮罩；
        落点始终不变。

        ``front_source`` 只写进日志与诊断快照，用来区分默认的 ``asset`` 与排障时的
        ``screenshot`` 回退；写错会让诊断分不清资产路径与截图回退。

        上一次发牌还在播时直接报错：动画不会替页面收掉上一条时间轴，页面也就收不到
        上一组卡片的 ``card_revealed`` / ``finished``，卡片会一直透明。调用方要先
        :meth:`cancel`（或 :meth:`finish`）再开始新的一次。
        """
        self._begin(
            items,
            deck_origin,
            clip_rect=clip_rect,
            front_source=front_source,
            direction=DIRECTION_DEAL,
        )

    def start_return(
        self,
        items: Sequence[CardDealItem],
        deck_origin: QPointF,
        *,
        clip_rect: QRectF | None = None,
        front_source: str = DEAL_FRONT_SOURCE_ASSET,
        deck_extent: tuple[float, float] | None = None,
    ) -> None:
        """收牌：把 ``items`` 从各自的结果位翻回背面，再沿原弧线飞回牌堆。

        ``items`` 的落点就是发牌时冻结的同一组位置（覆盖层坐标）。整段严格分两步：
        先按 ``index`` 从大到小逐张翻回背面，全部翻完后按同样顺序飞回牌堆，先发的牌
        最后落回、压在牌堆最上面。收牌不交回任何 ``card_revealed``：页面通常在按下
        “返回”时已经把结果清空，这一层只负责把“卡片飞回去”的画面补完；自然播完发
        :attr:`finished`。

        ``deck_extent`` 是收牌牌堆的卡片尺寸（宽、高）；结果卡片被窗口收窄过时，
        牌堆仍按“查找设备”卡片的大小叠，落回的位置和按钮完全一致。省略时沿用品
        第一张牌的资产尺寸。
        """
        self._begin(
            items,
            deck_origin,
            clip_rect=clip_rect,
            front_source=front_source,
            direction=DIRECTION_RETURN,
            deck_extent=deck_extent,
        )

    def _begin(
        self,
        items: Sequence[CardDealItem],
        deck_origin: QPointF,
        *,
        clip_rect: QRectF | None,
        front_source: str,
        direction: str,
        deck_extent: tuple[float, float] | None = None,
    ) -> None:
        """两种方向共用的启动路径：校验输入、冻结落点并开始时间轴。"""
        ordered = self._normalize_items(items)
        if not ordered:
            self.skip(reason="no-cards")
            return
        if front_source not in (
            DEAL_FRONT_SOURCE_ASSET,
            DEAL_FRONT_SOURCE_SCREENSHOT,
        ):
            raise ValueError(
                "发牌正面来源只接受 "
                f"{DEAL_FRONT_SOURCE_ASSET!r} 或 {DEAL_FRONT_SOURCE_SCREENSHOT!r}，"
                f"收到 {front_source!r}"
            )
        if direction not in (DIRECTION_DEAL, DIRECTION_RETURN):
            raise ValueError(
                f"动画方向只接受 {DIRECTION_DEAL!r} 或 {DIRECTION_RETURN!r}，"
                f"收到 {direction!r}"
            )
        if self.is_running():
            raise RuntimeError(
                "上一次动画还在播：先 cancel() 或 finish() 再开始新的一次，"
                "否则页面收不到上一组卡片的 card_revealed / finished"
            )
        self._timer.stop()
        self._items = ordered
        self._revealed = [False] * len(ordered)
        self._direction = direction
        self._front_source = str(front_source)
        self._deck = QPointF(deck_origin)
        self._deck_extent = (
            (float(deck_extent[0]), float(deck_extent[1]))
            if direction == DIRECTION_RETURN and deck_extent is not None
            else None
        )
        self._clip = QRectF(clip_rect) if clip_rect is not None else None
        self._elapsed = 0.0
        self._started_at = None
        self._ended_at = None
        self._phase = DEAL_PHASE_IDLE
        self._paint_frames = 0
        self._first_paint_ms = None
        self._drawn_cards = 0
        self._first_drawn_cards = 0
        self._cancel_reason = None
        self._restore_result = None
        self._last_run = None
        self._started_at = timestamp()
        self._clock.start()
        self.setVisible(True)
        self.raise_()
        self._phase = self.phase_at(0.0)
        self._record(
            "start",
            started_at=self._started_at,
            card_count=len(self._items),
            direction=self._direction,
            front_source=self._front_source,
            duration_ms=self.duration_ms(),
            deck_x=float(self._deck.x()),
            deck_y=float(self._deck.y()),
            target_rects=self._target_labels(),
            overlay_visible=self.isVisible(),
            overlay_width=int(self.width()),
            overlay_height=int(self.height()),
            overlay_parent=widget_label(self.parentWidget()),
            phase=self._phase,
        )
        self.update()
        self._timer.start()

    @staticmethod
    def _normalize_items(items: Sequence[CardDealItem]) -> list[CardDealItem]:
        """校验并冻结输入：按 ``index`` 排序，落点复制一份，坏输入直接报错。

        这一步在改动任何运行状态之前完成：报错时上一次动画（若在播）保持原样，
        不会留下“卡片被清空、计时器还在走”的半状态。

        ``index`` 必须是 ``0..N-1`` 的连续编号：页面按交回的 ``index`` 直接索引自己的
        卡片列表，缺口或重复会让恢复动作落到别的卡片上（或什么也不做），画面因此与
        真实卡片错位；这种调用错误在启动前就停下。
        """
        ordered: list[CardDealItem] = []
        for item in items:
            if not isinstance(item, CardDealItem):
                raise TypeError(
                    "发牌项目必须是 CardDealItem："
                    f"收到 {type(item).__name__}"
                )
            ordered.append(replace(item, target_rect=QRectF(item.target_rect)))
        ordered.sort(key=lambda item: item.index)
        indices = [item.index for item in ordered]
        if indices != list(range(len(indices))):
            raise ValueError(
                "发牌项目的 index 必须是 0..N-1 的连续编号："
                f"收到 {indices}"
            )
        return ordered

    def skip(self, *, reason: str, card_count: int = 0) -> None:
        """记录一次没有启动的动画：只写 ``skip`` 事件并收好覆盖层。

        ``reason`` 由调用方给出业务原因（例如 ``no-cards``）；这是“动画没有启动”
        的唯一记录点，面板不再自己拼一条日志。

        空输入只表示“这一次没有牌”，不是中断：若上一次发牌还在播，页面正等着它的
        ``card_revealed`` / ``finished`` 把卡片恢复出来，直接清空会让卡片一直透明。
        因此先按完成路径收尾那一次（:meth:`_end_active_run`），再记录 ``skip``。
        """
        self._end_active_run()
        self._timer.stop()
        self.hide()
        self._items = []
        self._revealed = []
        self._clip = None
        self._deck_extent = None
        self._elapsed = 0.0
        self._phase = DEAL_PHASE_IDLE
        self._paint_frames = 0
        self._first_paint_ms = None
        self._drawn_cards = 0
        self._first_drawn_cards = 0
        self._started_at = None
        self._ended_at = None
        self._cancel_reason = None
        self._restore_result = None
        self._direction = DIRECTION_DEAL
        self._front_source = DEAL_FRONT_SOURCE_ASSET
        self._last_run = None
        self._record(
            "skip",
            skip_reason=reason,
            card_count=int(card_count),
            phase=DEAL_PHASE_IDLE,
            overlay_visible=self.isVisible(),
            overlay_width=int(self.width()),
            overlay_height=int(self.height()),
            overlay_parent=widget_label(self.parentWidget()),
        )

    def finish(self) -> None:
        """立刻走完这一段：收起覆盖层并发出 :attr:`finished`。

        发牌方向会把所有牌翻开（页面据此恢复卡片）；收牌方向不交回任何牌，只结束
        画面。已经自然播完的一次只保留已有结果：重复调用不会再发一次 ``finished``，
        页面因此不会收到两遍“整段结束”。
        """
        if not self._items or self._phase == DEAL_PHASE_FINISHED:
            return
        self._complete()

    def _end_active_run(self) -> None:
        """把还在播的一次按完成路径收尾，供 :meth:`skip` 在清空输入之前调用。

        页面靠 ``card_revealed`` / ``finished`` 恢复卡片与交互；清空输入而不发这两个
        信号，卡片就会一直透明。已经收尾过的一次（``finished`` 已发出）不再重复。
        """
        if not self._items or self._phase == DEAL_PHASE_FINISHED:
            return
        self._complete()

    def cancel(self, *, reveal: bool = True, reason: str = "unspecified") -> None:
        """中途取消；``reason`` 写进诊断日志，``reveal=True`` 时通知页面恢复卡片。

        已经自然播完的动画只做清理，不再记 ``cancel``：否则“播完后换一批结果”会被
        误读成一次中断，也会盖掉 ``finish`` 快照。

        动画不持有控件，``reveal=True`` 只是为每张牌发一次 :attr:`card_revealed`，
        由页面把对应的真实卡片恢复显示；与自然播完走的是同一条恢复路径。
        """
        had_items = bool(self._items)
        was_running = self.is_running()
        interrupted = had_items and self._phase != DEAL_PHASE_FINISHED
        if interrupted:
            self._cancel_reason = reason
            # 收牌方向本来就没有真实卡片等着恢复；取消时只能记“没有恢复”。
            self._restore_result = (
                "revealed"
                if reveal and self._direction != DIRECTION_RETURN
                else "hidden"
            )
            self._ended_at = timestamp()
        self._stop()
        # 收牌方向没有“真实卡片等着恢复”：页面在按下返回时已经清空结果，交回
        # card_revealed 反而会让页面去找一组已经不存在的卡片。
        if reveal and self._direction != DIRECTION_RETURN:
            self._reveal_all()
        if interrupted:
            self._record(
                "cancel",
                started_at=self._started_at,
                ended_at=self._ended_at,
                elapsed_ms=float(self._elapsed),
                duration_ms=self.duration_ms(),
                card_count=len(self._items),
                direction=self._direction,
                revealed_count=self.revealed_count,
                paint_frames=self._paint_frames,
                first_paint_ms=self._first_paint_ms,
                front_source=self._front_source,
                cancel_reason=self._cancel_reason,
                restore_result=self._restore_result,
                was_running=was_running,
                phase=self.phase_at(),
            )
            # 取消后本次输入会被清空：先把同一时刻的完整快照留下来。
            self._capture_run_end("cancel")
        self._items = []
        self._revealed = []
        self._clip = None
        self._deck_extent = None
        self._elapsed = 0.0
        self._phase = DEAL_PHASE_IDLE
        self._direction = DIRECTION_DEAL
        self._front_source = DEAL_FRONT_SOURCE_ASSET

    def _stop(self) -> None:
        self._timer.stop()
        self.hide()

    def _complete(self) -> None:
        """自然走完或 ``finish()`` 的收尾：记录结束诊断并收起覆盖层。

        发牌方向先把每张牌交回页面（恢复真实卡片），收牌方向不交回任何牌。
        """
        self._elapsed = self.duration_ms()
        if self._direction != DIRECTION_RETURN:
            self._reveal_all()
        self._stop()
        self._phase = DEAL_PHASE_FINISHED
        self._ended_at = timestamp()
        self._record(
            "finish",
            started_at=self._started_at,
            ended_at=self._ended_at,
            elapsed_ms=float(self._elapsed),
            duration_ms=self.duration_ms(),
            card_count=len(self._items),
            direction=self._direction,
            revealed_count=self.revealed_count,
            paint_frames=self._paint_frames,
            first_paint_ms=self._first_paint_ms,
            phase=self._phase,
        )
        self._capture_run_end("finish")
        self.finished.emit()

    def _advance(self) -> None:
        # 取消/完成后可能还有一个已经排队的 timeout：这时本次输入已经清空，直接返回，
        # 不要用空输入再走一遍收尾（否则会多记一次 finish、多发一次 finished）。
        if not self._items or not self._timer.isActive():
            return
        total = self.duration_ms()
        self._elapsed = float(min(total, self._clock.elapsed()))
        self._sync_phase()
        if self._direction != DIRECTION_RETURN:
            for index in range(len(self._items)):
                if not self._revealed[index] and self.flip_progress(index) >= 1.0:
                    self._reveal(index)
        self.update()
        if self._elapsed >= total:
            self._complete()

    def _reveal(self, index: int) -> None:
        """翻完一张：把它的身份交回页面，让页面恢复真正的卡片控件。"""
        if index < 0 or index >= len(self._items) or self._revealed[index]:
            return
        self._revealed[index] = True
        self.card_revealed.emit(int(self._items[index].index))

    def _reveal_all(self) -> None:
        for index in range(len(self._items)):
            self._reveal(index)
        self.update()

    # ---- 几何 ---------------------------------------------------------------

    def _target_rect(self, index: int) -> QRectF:
        """第 ``index`` 张卡在覆盖层里的落点：开始时就冻结好的坐标，不再现取。"""
        if index < 0 or index >= len(self._items):
            return QRectF()
        return QRectF(self._items[index].target_rect)

    def _flight_center(self, index: int, progress: float) -> QPointF:
        """飞行中的牌中心：从牌堆到落点的一条向上拱起的二次曲线。"""
        target = self._target_rect(index).center()
        start = self._deck
        control = QPointF(
            (start.x() + target.x()) / 2.0,
            min(start.y(), target.y()) - DEAL_ARC_LIFT,
        )
        eased = _ease_out_cubic(progress)
        inverse = 1.0 - eased
        return QPointF(
            start.x() * inverse * inverse
            + control.x() * 2.0 * inverse * eased
            + target.x() * eased * eased,
            start.y() * inverse * inverse
            + control.y() * 2.0 * inverse * eased
            + target.y() * eased * eased,
        )

    def _return_flight_center(self, index: int, progress: float) -> QPointF:
        """收牌飞行中的牌中心：沿发牌同一条弧线，从落点飞回牌堆。"""
        target = self._target_rect(index).center()
        deck = self._deck
        control = QPointF(
            (target.x() + deck.x()) / 2.0,
            min(target.y(), deck.y()) - DEAL_ARC_LIFT,
        )
        eased = _ease_out_cubic(progress)
        inverse = 1.0 - eased
        return QPointF(
            target.x() * inverse * inverse
            + control.x() * 2.0 * inverse * eased
            + deck.x() * eased * eased,
            target.y() * inverse * inverse
            + control.y() * 2.0 * inverse * eased
            + deck.y() * eased * eased,
        )

    # ---- 绘制 ---------------------------------------------------------------

    def _draw_surface(
        self,
        painter: QPainter,
        rect: QRectF,
        pixmap: QPixmap,
        asset: CardAsset,
        angle: float = 0.0,
        *,
        particle_phase: float | None = None,
    ) -> bool:
        """把一张卡（投影 + 卡面）按 ``rect`` 缩放、旋转着贴上去。

        ``particle_phase`` 不为 ``None`` 时，再在牌背上叠一层绿色粒子：粒子是动效，
        不能烘进 ``asset.back``，只能每帧按相位画在卡片自己的坐标系里（圆角裁切与
        卡片一致）。文件本身给出 ``0..1`` 的相位；错开由调用方决定。

        返回是否真的画到了可见区域：``False`` 表示这一笔要么没有贴图，要么整块落在
        覆盖层/裁剪区之外，不能算作“卡片已画出”。
        """
        if pixmap.isNull() or not self._is_visible_draw(painter, rect, asset):
            return False
        ratio = float(self.devicePixelRatioF())
        shadow = _render_shadow(
            int(asset.width),
            int(asset.height),
            float(asset.radius),
            int(asset.shadow_margin),
            ratio,
        )
        painter.save()
        painter.translate(rect.center())
        painter.rotate(float(angle))
        painter.scale(
            rect.width() / float(asset.width),
            rect.height() / float(asset.height),
        )
        painter.translate(-asset.width / 2.0, -asset.height / 2.0)
        margin = float(asset.shadow_margin)
        painter.drawPixmap(
            QPointF(-margin, -margin + float(_SHADOW_DROP)),
            shadow,
        )
        # 资产位图按卡片逻辑尺寸（含屏幕像素比）渲染，这里不再自己缩放一次。
        painter.drawPixmap(QPointF(0.0, 0.0), pixmap)
        if particle_phase is not None:
            self._paint_back_particles(painter, asset, particle_phase)
        painter.restore()
        return True

    def _paint_back_particles(
        self, painter: QPainter, asset: CardAsset, phase: float
    ) -> None:
        """在牌背上画那六个绿色粒子：坐标系已经在卡片里（原点在卡片左上角）。"""
        card = QRectF(0.0, 0.0, float(asset.width), float(asset.height))
        painter.save()
        painter.setClipPath(
            _card_silhouette(
                float(asset.width), float(asset.height), float(asset.radius)
            )
        )
        paint_photon_particles(painter, card, phase)
        painter.restore()

    def _back_particle_phase(self, index: int) -> float:
        """牌背粒子的相位：按“第几张”错开一点，六张牌不会一起亮、一起灭。"""
        return (
            self._elapsed / float(PARTICLE_PERIOD_MS) + index * _BACK_PARTICLE_PHASE_STEP
        ) % 1.0

    def _draw_back(
        self,
        painter: QPainter,
        rect: QRectF,
        asset: CardAsset,
        angle: float = 0.0,
        *,
        index: int = 0,
    ) -> bool:
        """按 ``rect`` 缩放、旋转着贴牌背（投影由 :meth:`_draw_surface` 补）。

        ``asset.back_particles`` 打开时，牌背朝上的每一帧都叠着绿色粒子；相位按
        ``index`` 错开。
        """
        phase = self._back_particle_phase(index) if asset.back_particles else None
        return self._draw_surface(
            painter, rect, asset.back, asset, angle, particle_phase=phase
        )

    def _draw_front(self, painter: QPainter, rect: QRectF, index: int) -> bool:
        """把第 ``index`` 张的正面按 ``rect`` 缩放贴上去，并在下面补一层同样的投影。"""
        if index < 0 or index >= len(self._items):
            return False
        return self._draw_surface(
            painter, rect, self._items[index].asset.front, self._items[index].asset
        )

    def _is_visible_draw(
        self, painter: QPainter, rect: QRectF, asset: CardAsset
    ) -> bool:
        """``rect``（含投影边距）是否与覆盖层当前的可见区域相交。"""
        if rect.width() <= 0.0 or rect.height() <= 0.0:
            return False
        pad = float(asset.shadow_margin)
        padded = rect.adjusted(-pad, -pad, pad, pad)
        visible = painter.clipBoundingRect()
        if visible.isNull():
            visible = QRectF(self.rect())
        return padded.intersects(visible)

    def paintEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        """先画剩下的牌堆，再画飞出去的牌 / 落定的背面；翻到后半程贴正面。

        牌堆与飞行中的牌整段可见（不套卡片区裁剪），只有已经落定的牌才受裁剪约束：
        裁剪是为了挡住滚动后看不见的结果行，而飞行段必须从牌堆一路连到槽位，不能
        在卡片区边界上被平切。
        """
        if not self._items:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        if self._direction == DIRECTION_RETURN:
            self._paint_return(painter)
            return
        count = len(self._items)
        drawn = 0
        dealt = sum(
            1 for index in range(count) if self.deal_progress(index) > 0.0
        )
        remaining = count - dealt
        if remaining > 0:
            # 牌堆最多叠三张：发走一张，剩下的少一层，看得出“牌是从这叠里出去的”。
            deck_asset = self._items[0].asset
            for depth in range(min(DEAL_DECK_DEPTH, remaining) - 1, -1, -1):
                rect = QRectF(
                    self._deck.x() - deck_asset.width / 2.0,
                    self._deck.y() - deck_asset.height / 2.0 - depth * 2.0,
                    float(deck_asset.width),
                    float(deck_asset.height),
                )
                if self._draw_back(painter, rect, deck_asset, index=depth):
                    drawn += 1
        clip = self._cards_clip_rect()
        for index in range(count):
            if self._revealed[index]:
                continue
            deal = self.deal_progress(index)
            if deal <= 0.0:
                continue
            target = self._target_rect(index)
            if clip is not None and not clip.intersects(target):
                # 落点不在可见的卡片区里：这张牌怎么飞都看不见，不要画到状态行/
                # 动作行上（发牌期间它们仍然占着自己的位置）。
                continue
            if deal < 1.0:
                # 飞行段从牌堆出发，而牌堆按设计就在卡片视口之外；这一段不套卡片区
                # 裁剪，否则牌只会从卡片区边界“平切”进来（见模块 docstring）。
                eased = _ease_out_cubic(deal)
                scale = DEAL_START_SCALE + (1.0 - DEAL_START_SCALE) * eased
                angle = (
                    DEAL_START_ANGLE
                    * (1.0 - eased)
                    * (1.0 if index % 2 == 0 else -1.0)
                )
                width = target.width() * scale
                height = target.height() * scale
                center = self._flight_center(index, deal)
                rect = QRectF(
                    center.x() - width / 2.0,
                    center.y() - height / 2.0,
                    width,
                    height,
                )
                painted = self._draw_back(
                    painter, rect, self._items[index].asset, angle, index=index
                )
            else:
                painted = self._paint_settled(painter, target, index, clip)
            if painted:
                drawn += 1
        self._record_paint(drawn)

    def _paint_return(self, painter: QPainter) -> None:
        """收牌画面：落回牌堆的牌叠成一叠，其余先翻回背面，再飞回牌堆。

        牌堆画在 ``_deck``（“查找设备”卡片的位置）。已经到位的牌按
        :data:`DEAL_DECK_DEPTH` 叠成牌堆；还在结果位的牌先原地翻面，翻完后沿发牌
        弧线飞回牌堆。与发牌一致，只有停在结果位上的牌受卡片区裁剪约束，飞行段
        整段可见。
        """
        count = len(self._items)
        drawn = 0
        landed = sum(
            1
            for index in range(count)
            if self.return_fly_progress(index) >= 1.0
        )
        # 牌堆只画已经落回的牌：收牌刚开始时目的地还是空的，先在那里放一张牌背会让
        # 人看到下方“凭空多出一张牌”。第一张牌落回才出现第一层，之后按落回顺序叠高。
        deck_layers = min(DEAL_DECK_DEPTH, landed)
        deck_asset = self._items[0].asset
        deck_width, deck_height = self._deck_extent or (
            float(deck_asset.width),
            float(deck_asset.height),
        )
        for depth in range(deck_layers - 1, -1, -1):
            rect = QRectF(
                self._deck.x() - deck_width / 2.0,
                self._deck.y() - deck_height / 2.0 - depth * 2.0,
                deck_width,
                deck_height,
            )
            if self._draw_back(painter, rect, deck_asset, index=depth):
                drawn += 1
        clip = self._cards_clip_rect()
        # 倒着画：最后才飞走的第一张压在其它牌上面，收牌过程不会有牌中途“钻到”
        # 还没动的那张下面。
        for index in range(count - 1, -1, -1):
            target = self._target_rect(index)
            fly = self.return_fly_progress(index)
            if fly >= 1.0:
                continue
            if clip is not None and not clip.intersects(target):
                # 这张牌的落点本来就不在可见的卡片区里：收牌时也不把它画到
                # 状态行/动作行上。
                continue
            flip = self.return_flip_progress(index)
            if flip < 1.0:
                if clip is not None:
                    painter.save()
                    painter.setClipRect(clip, Qt.IntersectClip)
                try:
                    painted = self._paint_flip(painter, target, index, 1.0 - flip)
                finally:
                    if clip is not None:
                        painter.restore()
            else:
                eased = _ease_out_cubic(fly)
                scale = 1.0 - (1.0 - DEAL_START_SCALE) * eased
                angle = (
                    DEAL_START_ANGLE
                    * eased
                    * (1.0 if index % 2 == 0 else -1.0)
                )
                width = target.width() * scale
                height = target.height() * scale
                center = self._return_flight_center(index, fly)
                rect = QRectF(
                    center.x() - width / 2.0,
                    center.y() - height / 2.0,
                    width,
                    height,
                )
                painted = self._draw_back(
                    painter, rect, self._items[index].asset, angle, index=index
                )
            if painted:
                drawn += 1
        self._record_paint(drawn)

    def _paint_settled(
        self, painter: QPainter, target: QRectF, index: int, clip: QRectF | None
    ) -> bool:
        """画一张已经落定的牌（背面、翻面过程或正面），只在这时套卡片区裁剪。

        裁剪的用途是“结果多到要滚动时，看不见的行不画到状态行/动作行上”，只对已经
        停在槽位上的牌成立。牌堆与飞行中的牌不裁剪：牌堆按设计就在卡片视口之外
        （它继承“查找设备”按钮的位置），套上视口裁剪会让牌从边界平切进来，看起来
        不是从牌堆发出来的。
        """
        if clip is not None:
            painter.save()
            painter.setClipRect(clip, Qt.IntersectClip)
        try:
            flip = self.flip_progress(index)
            if flip <= 0.0:
                return self._draw_back(
                    painter, target, self._items[index].asset, index=index
                )
            if flip < 1.0:
                return self._paint_flip(painter, target, index, flip)
            # 翻完了但收尾信号还没到：直接贴正面，下一帧就会恢复真正的卡片。
            return self._draw_front(painter, target, index)
        finally:
            if clip is not None:
                painter.restore()

    def _cards_clip_rect(self) -> QRectF | None:
        """卡片区的可见矩形（覆盖层坐标）：开始时冻结，之后不再追踪滚动。

        只用于已经落定的牌；飞行段与牌堆不裁剪（见 :meth:`paintEvent`）。
        """
        if self._clip is None or self._clip.isEmpty():
            return None
        return QRectF(self._clip)

    def _paint_flip(
        self, painter: QPainter, target: QRectF, index: int, progress: float
    ) -> bool:
        """绕竖直轴翻面：前半程背面收窄，后半程正面展开；返回是否画到可见区域。"""
        asset = self._items[index].asset
        squeeze = abs(math.cos(math.pi * progress))
        squeeze = max(0.035, squeeze)
        grow = 1.0 + 0.04 * math.sin(math.pi * progress)
        width = target.width() * squeeze
        height = target.height() * grow
        rect = QRectF(
            target.center().x() - width / 2.0,
            target.center().y() - height / 2.0,
            width,
            height,
        )
        if progress < 0.5:
            painted = self._draw_back(painter, rect, asset, index=index)
        else:
            painted = self._draw_front(painter, rect, index)
        shade = round(70 * math.sin(math.pi * progress))
        if shade:
            path = QPainterPath()
            radius = max(1.0, asset.radius * (rect.width() / asset.width))
            path.addRoundedRect(rect, radius, radius)
            painter.fillPath(path, QColor(0, 0, 0, shade))
        return painted
