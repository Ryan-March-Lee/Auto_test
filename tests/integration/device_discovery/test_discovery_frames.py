"""发现页发牌接线的单设备冒烟。

帧级矩阵已收缩到 ``tests/ui/test_card_deal_frames.py`` 的一条取消用例；这里只确认
一台真实发现结果能进入发牌、覆盖层确实画出牌，并在动画结束后恢复真实卡片与交互。

判断“覆盖层有没有画出东西”用同一块画布上“与角落底色不同的采样点数量”：覆盖层自己
不画任何东西，差值只可能来自发牌动画。这样不依赖某台设备的卡片颜色，也能直接发现
“计时器在走但覆盖层是空的”。
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRectF  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402

from infrastructure.discovery.instrument_discovery import (  # noqa: E402
    SCAN_MODE_SIMULATED,
    build_scanner,
)
from presentation.qt.instrument_discovery_panel import DiscoveryPhase  # noqa: E402

from .support import (  # noqa: E402
    SMW_IDN,
    DiscoveryPanelTestCase,
    registry,
    six_device_factory,
)


def _single_device_factory(_mode):
    """只发现一台已登记的信号源：覆盖“单设备”分支。"""
    return build_scanner(
        registry=registry(),
        mode=SCAN_MODE_SIMULATED,
        responses={("192.168.1.201", 5025): SMW_IDN},
        timeout_s=0.02,
    )


def _sample(image, point: QPoint) -> QColor:
    ratio = image.devicePixelRatio()
    x = int(round(point.x() * ratio))
    y = int(round(point.y() * ratio))
    x = max(0, min(image.width() - 1, x))
    y = max(0, min(image.height() - 1, y))
    return image.pixelColor(x, y)


def _is_close(actual: QColor, expected: QColor, tolerance: int = 12) -> bool:
    return max(
        abs(actual.red() - expected.red()),
        abs(actual.green() - expected.green()),
        abs(actual.blue() - expected.blue()),
    ) <= tolerance


def _background(image) -> QColor:
    """画面左上角：覆盖层里没有任何牌时会一直保持底色。"""
    return _sample(image, QPoint(0, 0))


def _ink(image, step: int = 6) -> int:
    """与底色不同的采样点数量；覆盖层自己什么也不画，差值只来自发牌动画。"""
    background = _background(image)
    count = 0
    for y in range(0, image.height(), step):
        for x in range(0, image.width(), step):
            if not _is_close(image.pixelColor(x, y), background):
                count += 1
    return count


class DiscoveryFrameTests(DiscoveryPanelTestCase):
    """真实发现页上的发牌画面：无设备、单设备、多设备、窄窗口与取消。"""

    def _scan_until_result(self, panel) -> None:
        panel.request_scan()
        self.assertTrue(panel.worker is not None)
        panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()
        self.assertIsNone(panel.worker)
        panel.wait_stack.finish_cycle()
        self.app.processEvents()
        self.app.processEvents()

    def _dealing(self, factory=None, size=(1000, 800)):
        """扫描并进入 ``DEALING``，但不替它收尾：返回仍在播放的面板。"""
        panel = self.make_panel(factory or self.default_factory, size=size)
        self._scan_until_result(panel)
        self.assertIs(panel.phase(), DiscoveryPhase.DEALING)
        self.assertTrue(panel.deal_animation.is_running(), "有设备时应进入发牌")
        return panel

    @staticmethod
    def _canvas(panel):
        """动画画布：接线到页面时是页面级覆盖层，单独测试面板时是结果区里的发牌层。"""
        return panel.animation_overlay() or panel.deal_animation

    def _overlay_ink(self, panel, elapsed_ms: float | None = None):
        """画布在某一时刻的画面与“与底色不同的采样点数量”。"""
        layer = panel.deal_animation
        layer._timer.stop()
        if elapsed_ms is not None:
            layer._elapsed = float(elapsed_ms)
        image = self._canvas(panel).grab().toImage()
        return _ink(image), image, layer

    def test_a_single_device_is_dealt_before_its_card_appears(self):
        """只有一台设备：先发一张牌，翻完才恢复真实卡片与交互。"""
        panel = self._dealing(_single_device_factory)

        self.assertEqual(panel.card_count(), 1)
        self.assertEqual(panel.deal_animation.item_count(), 1)
        self.assertEqual(len(panel.deal_animation.targets()), 1)
        # 刚起飞时画面里至少是牌堆/飞行中的牌，不能是空覆盖层。
        taking_off, _image, layer = self._overlay_ink(panel, 40.0)
        self.assertGreater(taking_off, 0, "单设备也要真的画出牌")
        self.assertGreater(layer.diagnostics()["paint_frames"], 0)
        # 最后一张翻完之前，覆盖层上仍是这张牌的正面。
        flipped, _image, _layer = self._overlay_ink(panel, layer.duration_ms())
        self.assertGreater(flipped, 0, "翻完之前覆盖层不能提前空掉")
        for card in panel.cards():
            self.assertIsNotNone(card.graphicsEffect(), "发牌期间真实卡片先藏起来")

        layer.finish()
        self.app.processEvents()

        self.assertIs(panel.phase(), DiscoveryPhase.INTERACTIVE)
        card = panel.cards()[0]
        self.assertIsNone(card.graphicsEffect())
        self.assertTrue(card.isVisible())
        self.assertTrue(panel.cards_area.isEnabled())


if __name__ == "__main__":
    unittest.main()
