"""发牌与翻牌在固定时刻的画面（技术债重构步骤 9）。

步骤 7 的 ``test_card_deal_animation.py`` 验证时间轴、信号与收尾；这里只回答“那一帧
究竟画出了什么”：

- 第一张刚离开牌堆时，牌堆还在、牌在飞、正面还没有露出来；
- 多张牌同时在空中，而且各自画的是自己的牌背；
- 全部落位但尚未翻牌；
- 第一张翻到一半：正面开始出现，画面被压窄；
- 最后一张翻完：所有正面可见，覆盖层随即收起；
- 取消之后画面清空，每张牌都已交回页面；
- 单设备、窄画布与“计时器在走但画面是空的”也能被同一组断言发现。

固定时刻的做法是停住计时器、把 ``_elapsed`` 钉在那一刻再 ``grab()``：走的是运行时
同一个 ``paintEvent``，不为测试另写一套绘制。除逐点取色外，关键几帧还与
``visual_baselines/card_deal_frames/`` 下的基准截图做低精度比对：整帧只要求绝大多数
采样点接近，给抗锯齿与平台差异留余地；卡片落点区域再按更密的采样单独比对一次，
防止“整帧容差放过一小块错误”。有意改动画面后，设置环境变量
``UPDATE_DEAL_FRAME_BASELINES=1`` 再运行本模块即可更新基准。
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QRectF  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402

#: 关键帧基准图：与测试同一个离屏平台渲染，纯色资产，不依赖字体或系统主题。
_BASELINE_DIR = (
    Path(__file__).resolve().parent / "visual_baselines" / "card_deal_frames"
)
#: 基准比对每隔几个像素取一个采样点：抓的是整体画面，不是逐像素相等。
_BASELINE_STEP = 4
#: 单个通道允许的偏差（抗锯齿、投影柔边与平台光栅化差异）。
_BASELINE_CHANNEL_TOLERANCE = 24
#: 允许的采样点不一致比例：空画面/错画面会远高于此值。
_BASELINE_MAX_MISMATCH = 0.04
#: 牌面区域用更密的采样与更严的比例再比一次：整帧容差会放过一小块局部错误。
_REGION_BASELINE_STEP = 2
_REGION_BASELINE_MAX_MISMATCH = 0.02
_UPDATE_ENV = "UPDATE_DEAL_FRAME_BASELINES"

#: 每张卡的正反面用不同的纯色：取色就能分清“这一帧画的是背面还是正面”。
_FRONTS = (
    QColor(230, 30, 30),
    QColor(30, 190, 80),
    QColor(220, 120, 20),
    QColor(160, 40, 200),
)
_BACKS = (
    QColor(20, 40, 200),
    QColor(20, 160, 200),
    QColor(30, 120, 40),
    QColor(80, 30, 140),
)


def _application():
    from presentation.qt.main_window import build_application

    # 走正式启动路径：调色板、字体与运行时有同一份。
    return build_application([])


def _front_colour(index: int) -> QColor:
    return _FRONTS[index % len(_FRONTS)]


def _back_colour(index: int) -> QColor:
    return _BACKS[index % len(_BACKS)]


def _solid_asset(front: QColor, back: QColor, *, width: int = 60, height: int = 100):
    """一张纯色资产：只为验证“这一帧画的是哪一面”，不依赖卡片内容。"""
    from presentation.qt.card_assets import CardAsset

    front_pixmap = QPixmap(width, height)
    front_pixmap.fill(front)
    back_pixmap = QPixmap(width, height)
    back_pixmap.fill(back)
    return CardAsset(
        front=front_pixmap,
        back=back_pixmap,
        width=width,
        height=height,
        radius=8.0,
        shadow_margin=12,
    )


def _sample(image: QImage, point: QPoint | QPointF) -> QColor:
    """按设备像素比把逻辑坐标换算成图像坐标后取色。"""
    ratio = image.devicePixelRatio()
    x = int(round(point.x() * ratio))
    y = int(round(point.y() * ratio))
    x = max(0, min(image.width() - 1, x))
    y = max(0, min(image.height() - 1, y))
    return image.pixelColor(x, y)


def _background(image: QImage) -> QColor:
    """画面右下角一定是空的：用它当这一帧的背景色，不硬编码主题色。"""
    return _sample(image, QPoint(image.width() - 1, image.height() - 1))


def _is_close(actual: QColor, expected: QColor, tolerance: int = 12) -> bool:
    return max(
        abs(actual.red() - expected.red()),
        abs(actual.green() - expected.green()),
        abs(actual.blue() - expected.blue()),
    ) <= tolerance


class CardDealFrameTests(unittest.TestCase):
    """按时间轴钉住六个关键帧，验证画面不是空的，并看清正反面与位置变化。"""

    @classmethod
    def setUpClass(cls):
        cls.app = _application()

    def _animation(
        self,
        count: int,
        *,
        host_size=(400, 400),
        targets=None,
        deck: QPointF | None = None,
        clip_rect: QRectF | None = None,
    ):
        """一块底板加一组纯色 item；``start`` 之后立刻停表，按给定时刻取帧。"""
        from presentation.qt.card_deal_animation import (
            CardDealAnimation,
            CardDealItem,
        )

        host = QWidget()
        host.resize(*host_size)
        layer = CardDealAnimation(host)
        layer.setGeometry(host.rect())
        assets = [
            _solid_asset(_front_colour(index), _back_colour(index))
            for index in range(count)
        ]
        if targets is None:
            targets = [
                QRectF(30.0 + index * 90.0, 40.0, 60.0, 100.0)
                for index in range(count)
            ]
        items = [
            CardDealItem(asset=asset, target_rect=target, index=index)
            for index, (asset, target) in enumerate(zip(assets, targets))
        ]
        if deck is None:
            deck = QPointF(host.width() / 2.0, host.height() - 60.0)
        layer.start(items, deck, clip_rect=clip_rect)
        layer._timer.stop()
        self.addCleanup(host.close)
        return layer, items, assets

    @staticmethod
    def _frame(layer, elapsed_ms: float) -> QImage:
        """把动画钉在 ``elapsed_ms`` 再抓图：与运行时共用同一个绘制路径。

        只钉时间，不改 ``_phase``（阶段由运行时的 ``_sync_phase`` 推进）；需要判断
        阶段时测试自己用 ``phase_at(elapsed)``。
        """
        layer._timer.stop()
        layer._elapsed = float(elapsed_ms)
        return layer.grab().toImage()

    @staticmethod
    def _flight_centre(layer, index: int, elapsed_ms: float) -> QPointF:
        """这一时刻第 ``index`` 张牌的中心：与 ``paintEvent`` 用同一条飞行弧线。"""
        progress = layer.deal_progress(index, elapsed_ms)
        return layer._flight_center(index, progress)

    def _assert_close(self, actual: QColor, expected: QColor, message: str) -> None:
        self.assertTrue(
            _is_close(actual, expected),
            f"{message}：取到 {actual.getRgb()}，期望 {expected.getRgb()}",
        )

    @staticmethod
    def _colour_count(
        image: QImage,
        rect: QRectF,
        colour: QColor,
        tolerance: int = 16,
        step: int = 1,
    ) -> int:
        """``rect`` 里有多少像素接近 ``colour``（按屏幕像素比换算）。"""
        ratio = image.devicePixelRatio()
        left = max(0, int(rect.left() * ratio))
        top = max(0, int(rect.top() * ratio))
        right = min(image.width() - 1, int(rect.right() * ratio))
        bottom = min(image.height() - 1, int(rect.bottom() * ratio))
        count = 0
        for y in range(top, bottom + 1, step):
            for x in range(left, right + 1, step):
                if _is_close(image.pixelColor(x, y), colour, tolerance):
                    count += 1
        return count

    @staticmethod
    def _span(
        image: QImage,
        rect: QRectF,
        predicate,
    ) -> int:
        """``rect`` 里满足 ``predicate(colour)`` 的像素横向跨度；没有时返回 0。"""
        ratio = image.devicePixelRatio()
        left = max(0, int(rect.left() * ratio) - 4)
        top = max(0, int(rect.top() * ratio))
        right = min(image.width() - 1, int(rect.right() * ratio) + 4)
        bottom = min(image.height() - 1, int(rect.bottom() * ratio))
        xs = []
        for y in range(top, bottom + 1):
            for x in range(left, right + 1):
                if predicate(image.pixelColor(x, y)):
                    xs.append(x)
        if not xs:
            return 0
        return max(xs) - min(xs) + 1

    def _assert_against_baseline(self, image: QImage, name: str, *, regions=()) -> None:
        """与 ``visual_baselines`` 下的关键帧比对；允许少量边缘像素不同。

        ``regions`` 是 ``(label, rect)``：整帧比对通过后，再在这些区域（通常是每个
        卡片落点）按更密的采样单独比一次，避免整帧容差漏掉一小块局部错误。
        """
        path = _BASELINE_DIR / f"{name}.png"
        if os.environ.get(_UPDATE_ENV) == "1":
            path.parent.mkdir(parents=True, exist_ok=True)
            self.assertTrue(image.save(str(path)), f"无法写入基准图 {path}")
            return
        self.assertTrue(
            path.is_file(),
            f"缺少基准图 {path}；设置 {_UPDATE_ENV}=1 重新生成",
        )
        baseline = QImage(str(path)).convertToFormat(QImage.Format_RGBA8888)
        current = image.convertToFormat(QImage.Format_RGBA8888)
        self.assertEqual(
            current.size(),
            baseline.size(),
            "基准图尺寸必须与这一帧一致；换设备像素比后要重建基准",
        )
        mismatches = 0
        total = 0
        for y in range(0, current.height(), _BASELINE_STEP):
            for x in range(0, current.width(), _BASELINE_STEP):
                total += 1
                if not _is_close(
                    current.pixelColor(x, y),
                    baseline.pixelColor(x, y),
                    _BASELINE_CHANNEL_TOLERANCE,
                ):
                    mismatches += 1
        ratio = mismatches / max(1, total)
        self.assertLessEqual(
            ratio,
            _BASELINE_MAX_MISMATCH,
            f"{name} 与视觉基准不一致：{mismatches}/{total} 个采样点",
        )
        for label, rect in regions:
            self._assert_region_against_baseline(
                current, baseline, rect, label, name
            )

    def _assert_region_against_baseline(
        self,
        current: QImage,
        baseline: QImage,
        rect: QRectF,
        label: str,
        name: str,
    ) -> None:
        """只在 ``rect`` 里加密比对：整帧容差不会把局部错误平均掉。"""
        ratio = current.devicePixelRatio()
        left = max(0, int(rect.left() * ratio))
        top = max(0, int(rect.top() * ratio))
        right = min(current.width() - 1, int(rect.right() * ratio))
        bottom = min(current.height() - 1, int(rect.bottom() * ratio))
        mismatches = 0
        total = 0
        for y in range(top, bottom + 1, _REGION_BASELINE_STEP):
            for x in range(left, right + 1, _REGION_BASELINE_STEP):
                total += 1
                if not _is_close(
                    current.pixelColor(x, y),
                    baseline.pixelColor(x, y),
                    _BASELINE_CHANNEL_TOLERANCE,
                ):
                    mismatches += 1
        self.assertGreater(total, 0, f"{name} 的 {label} 区域必须落在画面里")
        self.assertLessEqual(
            mismatches / total,
            _REGION_BASELINE_MAX_MISMATCH,
            f"{name} 的 {label} 区域与视觉基准不一致：{mismatches}/{total}",
        )

    # ---- 六个固定时刻 -------------------------------------------------------

    def test_the_first_card_leaves_the_deck_while_the_rest_stay_stacked(self):
        """第一张刚离开牌堆：牌堆还在、牌在飞、正面还没露出来。"""
        layer, items, _assets = self._animation(4)
        elapsed = 40.0
        image = self._frame(layer, elapsed)

        self.assertGreater(layer.deal_progress(0, elapsed), 0.0, "第一张已经起飞")
        self.assertEqual(layer.deal_progress(1, elapsed), 0.0, "第二张仍叠在牌堆里")
        background = _background(image)

        deck = _sample(image, layer._deck)
        self._assert_close(deck, _back_colour(0), "牌堆还留在原处，背面朝上")
        centre = self._flight_centre(layer, 0, elapsed)
        self.assertGreater(
            abs(centre.x() - layer._deck.x()) + abs(centre.y() - layer._deck.y()),
            40.0,
            "飞出去的牌要离开牌堆中心",
        )
        self._assert_close(
            _sample(image, centre), _back_colour(0), "飞出去的那张仍是牌背"
        )
        self._assert_close(
            _sample(image, items[0].target_rect.center()),
            background,
            "还没翻牌，落点处不应出现正面",
        )
        self.assertEqual(
            self._colour_count(image, items[0].target_rect, _front_colour(0)),
            0,
            "这一帧的落点处不能有正面颜色",
        )
        self.assertGreater(
            layer.diagnostics()["drawn_cards"], 0, "计时器在走，画面不能是空的"
        )
        self._assert_against_baseline(
            image,
            "01-first-card-leaves-deck",
            regions=[
                (
                    "牌堆",
                    QRectF(
                        layer._deck.x() - 30.0,
                        layer._deck.y() - 50.0,
                        60.0,
                        100.0,
                    ),
                ),
                ("落点", items[0].target_rect),
            ],
        )

    def test_multiple_cards_fly_at_the_same_time(self):
        """多张牌同时在空中：每张飞的都是自己的牌背，位置互不重合。"""
        layer, _items, _assets = self._animation(4)
        elapsed = 130.0
        image = self._frame(layer, elapsed)

        self.assertGreater(layer.deal_progress(0, elapsed), 0.0)
        self.assertGreater(layer.deal_progress(1, elapsed), 0.0)
        self.assertEqual(layer.deal_progress(2, elapsed), 0.0, "第三张还没出发")
        first = self._flight_centre(layer, 0, elapsed)
        second = self._flight_centre(layer, 1, elapsed)
        self.assertGreater(
            abs(first.x() - second.x()) + abs(first.y() - second.y()),
            40.0,
            "两张同时在飞的牌不能画在同一个位置",
        )
        self._assert_close(_sample(image, first), _back_colour(0), "第一张画自己的牌背")
        self._assert_close(_sample(image, second), _back_colour(1), "第二张画自己的牌背")

        self.assertGreater(
            layer.diagnostics()["drawn_cards"], 1, "第一帧里至少有两张可见的牌"
        )
        self._assert_against_baseline(image, "02-two-cards-in-flight")

    def test_all_cards_land_face_down_before_any_flip(self):
        """所有牌落位但尚未翻牌：每张槽位都是自己的牌背，正面一张也没有。"""
        from presentation.qt.card_deal_animation import (
            DEAL_FLY_MS,
            DEAL_SETTLE_MS,
            DEAL_STAGGER_MS,
        )

        layer, _items, _assets = self._animation(4)
        deal_end = 3 * DEAL_STAGGER_MS + DEAL_FLY_MS
        elapsed = deal_end + DEAL_SETTLE_MS / 2.0
        image = self._frame(layer, elapsed)

        self.assertEqual(layer.phase_at(elapsed), "settling")
        background = _background(image)
        self._assert_close(
            _sample(image, layer._deck), background, "牌已经全部发完，牌堆应该收走"
        )
        for index in range(4):
            target = layer._target_rect(index)
            self.assertEqual(layer.deal_progress(index, elapsed), 1.0)
            self.assertEqual(layer.flip_progress(index, elapsed), 0.0)
            self._assert_close(
                _sample(image, target.center()), _back_colour(index), "落定后先是牌背"
            )
            self.assertEqual(
                self._colour_count(image, target, _front_colour(index)),
                0,
                "全部落位、尚未翻牌时不能出现正面",
            )
        self.assertGreater(
            layer.diagnostics()["drawn_cards"], 3, "四张牌都要画出来"
        )
        self._assert_against_baseline(
            image,
            "03-all-cards-landed",
            regions=[
                (f"第 {index} 张落点", layer._target_rect(index))
                for index in range(4)
            ],
        )

    def test_the_first_card_shows_its_front_at_half_flip(self):
        """第一张翻到一半：正面开始出现，卡片被压窄，其他牌仍是背面。"""
        from presentation.qt.card_deal_animation import (
            DEAL_SETTLE_MS,
            DEAL_STAGGER_MS,
            DEAL_FLY_MS,
            FLIP_MS,
        )

        layer, _items, _assets = self._animation(4)
        deal_end = 3 * DEAL_STAGGER_MS + DEAL_FLY_MS
        elapsed = deal_end + DEAL_SETTLE_MS + 0.6 * FLIP_MS
        image = self._frame(layer, elapsed)

        self.assertEqual(layer.phase_at(elapsed), "flipping")
        self.assertAlmostEqual(layer.flip_progress(0, elapsed), 0.6, places=3)
        target = layer._target_rect(0)
        centre = _sample(image, target.center())
        self.assertGreater(centre.red(), 120, "翻到后半程应露出正面")
        self.assertGreater(
            centre.red(), centre.blue() + 80, "取到的是正面而不是蓝色牌背"
        )
        span = self._span(
            image,
            target,
            lambda colour: colour.red() > 100 and colour.red() > colour.blue() + 60,
        )
        self.assertGreater(span, 4, "半翻的牌仍要有可见宽度")
        self.assertLess(
            span,
            int(target.width() * 0.6),
            "翻到一半时卡片应被压窄，而不是整张正面",
        )
        self.assertLess(layer.flip_progress(1, elapsed), 0.5)
        self.assertGreater(
            _sample(image, layer._target_rect(1).center()).blue(),
            100,
            "第二张还在翻的前半程，应仍是牌背",
        )
        self._assert_against_baseline(
            image, "04-first-card-mid-flip", regions=[("半翻的牌", target)]
        )

    def test_the_last_card_reveals_its_front_and_then_the_overlay_closes(self):
        """最后一张翻完：每张槽位都是正面；随后覆盖层收起、全部交回页面。"""
        layer, _items, _assets = self._animation(4)
        revealed: list[int] = []
        finished: list[bool] = []
        layer.card_revealed.connect(revealed.append)
        layer.finished.connect(lambda: finished.append(True))
        elapsed = layer.duration_ms()
        image = self._frame(layer, elapsed)

        for index in range(4):
            target = layer._target_rect(index)
            self._assert_close(
                _sample(image, target.center()),
                _front_colour(index),
                "翻完之后每张牌都应露出正面",
            )
            self.assertGreater(
                self._colour_count(image, target, _front_colour(index), step=2),
                0,
                "最后一帧的每个槽位都要真的画出正面，不能是空画面",
            )
        # 最后一帧的非空判断直接看像素：每个槽位都要有正面颜色，不引用
        # drawn_cards（它的语义是最近一帧的牌面数，不单独承担“画对了”的证明）。
        self._assert_against_baseline(
            image,
            "05-all-cards-flipped",
            regions=[
                (f"第 {index} 张正面", layer._target_rect(index))
                for index in range(4)
            ],
        )

        layer.finish()

        self.assertEqual(revealed, [0, 1, 2, 3], "翻完才逐张交回页面")
        self.assertEqual(finished, [True])
        self.assertFalse(layer.is_running())
        self.assertFalse(layer.isVisible(), "整段结束后覆盖层必须收起")
        self.assertEqual(layer.revealed_count, 4)

    def test_cancelling_mid_deal_clears_the_frame_and_hands_every_card_back(self):
        """取消动画后恢复状态：画面清空、每张牌都交回页面、不再有定时器。"""
        layer, _items, _assets = self._animation(3)
        before = self._frame(layer, 200.0)
        self.assertGreater(
            layer.diagnostics()["drawn_cards"], 0, "取消之前画面是满的"
        )
        revealed: list[int] = []
        layer.card_revealed.connect(revealed.append)

        layer.cancel(reveal=True, reason="results-cleared")

        self.assertEqual(revealed, [0, 1, 2], "取消也要把每一张交回页面恢复")
        self.assertFalse(layer.is_running(), "取消后计时器必须停下")
        self.assertFalse(layer.isVisible(), "取消后覆盖层必须收起")
        snapshot = layer.diagnostics()
        self.assertEqual(snapshot["event"], "cancel")
        self.assertEqual(snapshot["cancel_reason"], "results-cleared")
        self.assertEqual(snapshot["restore_result"], "revealed")
        after = layer.grab().toImage()
        frame = QRectF(after.rect())
        for index in range(3):
            self.assertEqual(
                self._colour_count(after, frame, _front_colour(index), step=4),
                0,
                "取消之后不能留下任何正面",
            )
            self.assertEqual(
                self._colour_count(after, frame, _back_colour(index), step=4),
                0,
                "取消之后不能留下任何牌背",
            )
        self.assertTrue(
            _is_close(_sample(before, QPoint(390, 390)), _background(after)),
            "取消后的画面应回到空底板",
        )

    # ---- 单设备、窄画布与空画面 ---------------------------------------------

    def test_a_single_device_walks_through_deck_flight_land_and_flip(self):
        """只有一台设备时同样要经过起飞、落定与翻牌，而不是直接闪现。"""
        from presentation.qt.card_deal_animation import (
            DEAL_FLY_MS,
            DEAL_SETTLE_MS,
            FLIP_MS,
        )

        target = QRectF(170.0, 40.0, 60.0, 100.0)
        layer, items, _assets = self._animation(1, targets=[target])

        flying = self._frame(layer, 120.0)
        centre = self._flight_centre(layer, 0, 120.0)
        self._assert_close(_sample(flying, centre), _back_colour(0), "单张卡也在飞")
        self.assertEqual(
            self._colour_count(flying, items[0].target_rect, _front_colour(0)),
            0,
            "飞行途中不能提前露出正面",
        )

        landed = self._frame(layer, DEAL_FLY_MS + DEAL_SETTLE_MS / 2.0)
        self._assert_close(
            _sample(landed, target.center()), _back_colour(0), "落位后背面朝上"
        )

        half = self._frame(layer, DEAL_FLY_MS + DEAL_SETTLE_MS + 0.6 * FLIP_MS)
        half_centre = _sample(half, target.center())
        self.assertGreater(half_centre.red(), 120)
        self.assertGreater(half_centre.red(), half_centre.blue() + 80, "单张卡也要真的翻面")

        final = self._frame(layer, layer.duration_ms())
        self._assert_close(
            _sample(final, target.center()), _front_colour(0), "最后露出正面"
        )
        self._assert_against_baseline(
            flying,
            "06-single-card-in-flight",
            regions=[
                (
                    "飞行中的牌",
                    QRectF(centre.x() - 33.0, centre.y() - 55.0, 66.0, 110.0),
                )
            ],
        )

    def test_a_narrow_stage_still_paints_only_inside_its_clip(self):
        """窄窗口：牌仍要画出来，但不能画到卡片区裁剪区之外。"""
        from presentation.qt.card_deal_animation import DEAL_FLY_MS

        clip = QRectF(0.0, 0.0, 240.0, 250.0)
        targets = [
            QRectF(10.0, 20.0, 60.0, 100.0),
            QRectF(90.0, 20.0, 60.0, 100.0),
            QRectF(50.0, 140.0, 60.0, 100.0),
        ]
        layer, _items, _assets = self._animation(
            3,
            host_size=(240, 260),
            targets=targets,
            deck=QPointF(120.0, 230.0),
            clip_rect=clip,
        )

        taking_off = self._frame(layer, 40.0)
        self.assertGreater(
            layer.diagnostics()["drawn_cards"], 0, "窄画布也要画出可见的牌"
        )

        # 牌堆不画在裁剪遮罩里（它是发牌起点，不一定落在卡片区），只看飞出去的牌。
        in_flight = self._frame(layer, DEAL_FLY_MS + 40.0)
        self.assertGreater(
            layer.diagnostics()["drawn_cards"], 0, "飞行中的牌同样要画出来"
        )
        outside = QRectF(0.0, clip.bottom() + 1.0, 240.0, 9.0)
        for index in range(3):
            for colour in (_back_colour(index), _front_colour(index)):
                self.assertEqual(
                    self._colour_count(in_flight, outside, colour),
                    0,
                    "裁剪区之外不能有牌面",
                )

    def test_a_pinned_frame_with_an_empty_overlay_is_detected(self):
        """停表钉住的空画面：诊断必须给出 ``drawn_cards == 0``，画面里没有牌。"""
        layer, _items, _assets = self._animation(
            2,
            host_size=(200, 220),
            targets=[
                QRectF(600.0, 600.0, 60.0, 100.0),
                QRectF(700.0, 600.0, 60.0, 100.0),
            ],
            deck=QPointF(-500.0, -500.0),
        )
        layer._timer.start()

        try:
            layer._elapsed = 120.0
            image = layer.grab().toImage()
            snapshot = layer.diagnostics()
            self.assertTrue(snapshot["running"], "这一帧确实是计时器在走时画的")
            self.assertGreater(snapshot["paint_frames"], 0, "确实进入过绘制")
            self.assertEqual(
                snapshot["drawn_cards"], 0, "所有牌都在覆盖层之外，不能算画出来"
            )
            self.assertEqual(
                snapshot["first_drawn_cards"], 0, "首帧同样是空的"
            )
            frame = QRectF(image.rect())
            for index in range(2):
                self.assertEqual(
                    self._colour_count(image, frame, _back_colour(index), step=2), 0
                )
        finally:
            layer._timer.stop()
            layer.cancel(reveal=False, reason="probe-empty")

    def test_a_live_timer_advances_the_frame_while_the_overlay_stays_empty(self):
        """真实计时器 tick 会推进时间轴，而这一刻的空覆盖层仍然没有牌面。"""
        layer, _items, _assets = self._animation(
            2,
            host_size=(200, 220),
            targets=[
                QRectF(600.0, 600.0, 60.0, 100.0),
                QRectF(700.0, 600.0, 60.0, 100.0),
            ],
            deck=QPointF(-500.0, -500.0),
        )
        layer._elapsed = 0.0
        layer._clock.restart()
        frames_before = layer.diagnostics()["paint_frames"]
        layer._timer.start()

        try:
            for _ in range(100):
                QTest.qWait(10)
                if layer.diagnostics()["elapsed_ms"] > 0.0:
                    break
            snapshot = layer.diagnostics()
            self.assertTrue(snapshot["running"], "计时器必须还在走")
            self.assertGreater(snapshot["elapsed_ms"], 0.0, "tick 必须推进时间轴")
            # 不依赖平台是否自动投递 paint：显式抓一帧，验证这一时刻的画面。
            image = layer.grab().toImage()
            snapshot = layer.diagnostics()
            self.assertGreater(
                snapshot["paint_frames"], frames_before, "tick 之后必须真的绘制"
            )
            self.assertEqual(snapshot["drawn_cards"], 0, "空覆盖层不能有牌面")
            frame = QRectF(image.rect())
            for index in range(2):
                self.assertEqual(
                    self._colour_count(image, frame, _back_colour(index), step=2),
                    0,
                    "空覆盖层里不能出现牌背",
                )
        finally:
            layer._timer.stop()
            layer.cancel(reveal=False, reason="probe-empty-live")


if __name__ == "__main__":
    unittest.main()
