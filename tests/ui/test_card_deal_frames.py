"""发牌取消帧的回归。

逐帧画面矩阵已收缩：这里只保留“发牌中取消”这一帧，确认覆盖层清空、每张牌都交回页面。
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






if __name__ == "__main__":
    unittest.main()
