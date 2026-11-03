"""发现页发牌画面的场景矩阵（技术债重构步骤 9）。

``tests/ui/test_card_deal_frames.py`` 在纯动画对象上钉住六个关键帧；这里把它放回真实
发现页，确认几种真实结果都能走完同一段画面：

- 没有设备：动画根本不启动，覆盖层里没有一帧牌；
- 只有一台设备：牌仍然从牌堆飞向唯一槽位，翻完才交回卡片；
- 多台设备：每张卡都有自己的落点，翻完最后一张才恢复交互；
- 窄窗口：卡片换行后落点仍在页面覆盖层内，画面不空；
- 发牌中取消：覆盖层清空，真实卡片恢复可见、可点。

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


def _empty_factory(_mode):
    return build_scanner(
        registry=registry(),
        mode=SCAN_MODE_SIMULATED,
        responses={},
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

    # ---- 无设备 -------------------------------------------------------------

    def test_no_devices_never_paints_a_deal_frame(self):
        """没有设备就不发牌：覆盖层里不能出现任何一帧牌。"""
        panel = self.make_panel(_empty_factory, size=(1000, 800))
        self._scan_until_result(panel)

        self.assertIs(panel.phase(), DiscoveryPhase.IDLE)
        layer = panel.deal_animation
        self.assertFalse(layer.is_running())
        self.assertFalse(layer.isVisible())
        self.assertEqual(layer.item_count(), 0)
        snapshot = layer.diagnostics()
        self.assertEqual(snapshot["event"], "skip")
        self.assertEqual(snapshot["card_count"], 0)
        self.assertEqual(snapshot["paint_frames"], 0, "空结果不应绘制发牌画面")
        self.assertEqual(snapshot["drawn_cards"], 0)
        self.assertEqual(
            self._overlay_ink(panel)[0], 0, "没有设备时覆盖层应保持空白"
        )

    # ---- 单设备与多设备 -----------------------------------------------------

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

    def test_multiple_devices_keep_their_own_targets_until_the_last_flip(self):
        """多台设备：每张卡一个落点，最后一张翻完才恢复交互。"""
        panel = self._dealing()

        cards = panel.cards()
        layer = panel.deal_animation
        self.assertGreater(len(cards), 1)
        self.assertEqual(layer.item_count(), len(cards))
        targets = layer.targets()
        self.assertEqual(len(targets), len(cards))
        self.assertEqual(
            len({(round(t.x()), round(t.y())) for t in targets}),
            len(cards),
            "每张卡都要有自己的落点，不能叠在同一格",
        )
        ink, _image, _layer = self._overlay_ink(panel, layer.duration_ms())
        self.assertGreater(ink, 0, "多张牌翻完后覆盖层仍是满的")

        layer.finish()
        self.app.processEvents()

        self.assertIs(panel.phase(), DiscoveryPhase.INTERACTIVE)
        self.assertTrue(panel.status_label.isVisible(), "翻完最后一张才显示统计")
        for card in cards:
            self.assertIsNone(card.graphicsEffect())
            self.assertTrue(card.isVisible())

    # ---- 窄窗口 -------------------------------------------------------------

    def test_a_narrow_panel_wraps_cards_and_still_paints_the_deal(self):
        """窄窗口：卡片换到多行，落点仍在页面覆盖层里，发牌画面不空。"""
        panel = self._dealing(six_device_factory, size=(520, 900))

        self.assertEqual(panel.card_count(), 6)
        layer = panel.deal_animation
        overlay_rect = QRectF(self._canvas(panel).rect())
        targets = layer.targets()
        self.assertEqual(len(targets), 6)
        self.assertGreaterEqual(
            len({round(target.y()) for target in targets}),
            2,
            "窄窗口下卡片应换行，发牌落点因此有多个纵坐标",
        )
        for target in targets:
            self.assertTrue(
                overlay_rect.intersects(target), "落点必须在页面覆盖层之内"
            )
        clip = layer.clip_rect()
        self.assertIsNotNone(clip, "卡片区可见时应有裁剪遮罩")
        self.assertTrue(
            any(clip.intersects(target) for target in targets),
            "至少有一张牌落在可见的卡片区里",
        )
        ink, _image, _layer = self._overlay_ink(panel, layer.duration_ms())
        self.assertGreater(ink, 0, "窄窗口下也要画出牌面")

        layer.finish()
        self.app.processEvents()
        self.assertIs(panel.phase(), DiscoveryPhase.INTERACTIVE)

    # ---- 取消后恢复 ---------------------------------------------------------

    def test_cancelling_mid_deal_restores_the_real_cards_and_clears_the_overlay(self):
        """发牌中取消：覆盖层清空，真实卡片恢复可见、可点，统计重新出现。"""
        panel = self._dealing()
        layer = panel.deal_animation
        ink_before, _image, _layer = self._overlay_ink(panel, 200.0)
        self.assertGreater(ink_before, 0, "取消前画面是满的")
        # 取帧时为了钉住时刻停过表；取消入口按“正在播”判断，这里恢复计时器。
        layer._timer.start()

        self.assertTrue(
            panel.cancel_deal_reveal(reveal=True, reason="test-cancel"),
            "确实取消了一次正在播的发牌",
        )
        self.app.processEvents()

        self.assertIs(panel.phase(), DiscoveryPhase.INTERACTIVE)
        self.assertFalse(layer.is_running())
        self.assertFalse(layer.isVisible())
        snapshot = layer.diagnostics()
        self.assertEqual(snapshot["event"], "cancel")
        self.assertEqual(snapshot["cancel_reason"], "test-cancel")
        self.assertEqual(snapshot["restore_result"], "revealed")
        self.assertTrue(panel.cards_area.isEnabled(), "取消后卡片区恢复可点")
        self.assertTrue(panel.status_label.isVisible(), "取消后结果统计也要放出来")
        for card in panel.cards():
            self.assertIsNone(card.graphicsEffect(), "取消后卡片不再被藏起来")
            self.assertTrue(card.isVisible())
        self.assertEqual(
            self._overlay_ink(panel)[0], 0, "取消后覆盖层必须回到空白"
        )


if __name__ == "__main__":
    unittest.main()
