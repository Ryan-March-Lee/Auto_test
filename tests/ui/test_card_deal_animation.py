"""发牌与翻牌动画：只认资产、落点与顺序，不认卡片控件或页面状态。

技术债重构步骤 7：发牌画面从 ``DealRevealLayer`` 拆成独立的
:class:`presentation.qt.card_deal_animation.CardDealAnimation`。这里验证它可以在没有
扫描线程、没有发现页的情况下单独播放，并且：

- 每个 item 只带资产、落点与 ``index``，动画不持有 ``QWidget``；
- 翻开某一张时通过 ``card_revealed(index)`` 把身份交回页面，页面自己恢复真实卡片；
- 完成、取消、中断都停掉计时器并清空本次输入，不留下透明度效果或悬空引用；
- 诊断、落点冻结与裁剪遮罩等步骤 2 / 6 的约束继续成立。
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QRectF  # noqa: E402
from PySide6.QtGui import QColor, QPixmap  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402


def _application():
    from presentation.qt.main_window import build_application

    # 走正式启动路径：主题（样式表、调色板、字体）必须与运行时有同一份。
    return build_application([])


def _solid_asset(
    front: QColor | None = None,
    back: QColor | None = None,
    *,
    width: int = 60,
    height: int = 100,
):
    """一张纯色资产：不依赖卡片数据，只为验证“画的是不是这份资产”。"""
    from presentation.qt.card_assets import CardAsset

    front_pixmap = QPixmap(width, height)
    front_pixmap.fill(front or QColor(210, 220, 230))
    back_pixmap = QPixmap(width, height)
    back_pixmap.fill(back or QColor(20, 24, 30))
    return CardAsset(
        front=front_pixmap,
        back=back_pixmap,
        width=width,
        height=height,
        radius=8.0,
        shadow_margin=12,
    )


def _sample_colour(image, point: QPoint) -> QColor:
    """按设备像素比把逻辑坐标换算成图像坐标后取色。"""
    ratio = image.devicePixelRatio()
    if ratio != 1.0:
        point = QPoint(int(point.x() * ratio), int(point.y() * ratio))
    return image.pixelColor(point)


class CardDealAnimationTests(unittest.TestCase):
    """发牌动画：所有牌先背面朝上落定，再一张接一张翻开。"""

    @classmethod
    def setUpClass(cls):
        cls.app = _application()

    def _animation(
        self,
        count: int,
        *,
        assets=None,
        targets=None,
        host_size=(400, 400),
        deck: QPointF | None = None,
    ):
        """一块透明底板 + 一组只读 item；计时器先停住，按给定时刻取进度。"""
        from presentation.qt.card_deal_animation import (
            CardDealAnimation,
            CardDealItem,
        )

        host = QWidget()
        host.resize(*host_size)
        layer = CardDealAnimation(host)
        layer.setGeometry(host.rect())
        if assets is None:
            assets = [_solid_asset() for _ in range(count)]
        if targets is None:
            targets = [
                QRectF(20.0 + index * 80.0, 40.0, 60.0, 100.0)
                for index in range(count)
            ]
        items = [
            CardDealItem(asset=asset, target_rect=target, index=index)
            for index, (asset, target) in enumerate(zip(assets, targets))
        ]
        if deck is None:
            deck = QPointF(host.width() / 2.0, host.height() - 40.0)
        layer.start(items, deck)
        layer._timer.stop()
        self.addCleanup(host.close)
        return layer, items

    # ---- 时间轴 -------------------------------------------------------------


    def test_phases_are_named_dealing_settling_flipping(self):
        from presentation.qt.card_deal_animation import (
            DEAL_FLY_MS,
            DEAL_SETTLE_MS,
            DEAL_STAGGER_MS,
        )

        layer, _items = self._animation(2)
        deal_end = DEAL_STAGGER_MS + DEAL_FLY_MS

        self.assertEqual(layer.phase_at(0.0), "dealing")
        self.assertEqual(layer.phase_at(deal_end + DEAL_SETTLE_MS / 2.0), "settling")
        self.assertEqual(layer.phase_at(deal_end + DEAL_SETTLE_MS + 1.0), "flipping")
        self.assertEqual(layer.phase_at(layer.duration_ms()), "finished")

    # ---- 画面来自资产 -------------------------------------------------------








    # ---- 完成与取消 ---------------------------------------------------------

    def test_finishing_announces_every_card_and_hides_the_layer(self):
        layer, _items = self._animation(4)
        revealed: list[int] = []
        finished: list[bool] = []
        layer.card_revealed.connect(revealed.append)
        layer.finished.connect(lambda: finished.append(True))

        layer.finish()

        self.assertEqual(revealed, [0, 1, 2, 3], "每张牌只翻开一次，按顺序交回页面")
        self.assertEqual(finished, [True])
        self.assertEqual(layer.revealed_count, 4, "收尾时每张牌都要翻开")
        self.assertFalse(layer.is_running())
        self.assertFalse(layer.isVisible(), "翻完就把覆盖层收起来")

    def test_cancel_clears_the_items_and_stops_every_animation(self):
        layer, _items = self._animation(3)

        layer.cancel(reveal=True, reason="results-cleared")

        self.assertFalse(layer.is_running())
        self.assertEqual(layer._items, [], "取消后不再持有本次输入")
        self.assertEqual(layer._revealed, [])
        self.assertIsNone(layer.clip_rect())








    def test_start_refuses_to_replace_a_running_deal(self):
        """上一次还在播时不能再开一次：先 cancel/finish，否则页面收不到恢复信号。"""
        from presentation.qt.card_deal_animation import (
            CardDealAnimation,
            CardDealItem,
        )

        host = QWidget()
        host.resize(300, 300)
        layer = CardDealAnimation(host)
        layer.setGeometry(host.rect())
        self.addCleanup(host.close)
        self.addCleanup(layer.cancel, reveal=False, reason="test-cleanup")
        items = [
            CardDealItem(
                asset=_solid_asset(),
                target_rect=QRectF(20.0, 20.0, 60.0, 100.0),
                index=0,
            )
        ]

        layer.start(items, QPointF(10.0, 10.0))
        with self.assertRaises(RuntimeError):
            layer.start(items, QPointF(10.0, 10.0))

        self.assertTrue(layer.is_running(), "被拒绝的调用不能停掉原来那一次")
        self.assertEqual(layer.item_count(), 1)



    # ---- 输入契约 -----------------------------------------------------------

    def test_start_rejects_duplicate_indices(self):
        """``index`` 不能重复：否则翻开的牌会与页面上的卡片对不上。"""
        from presentation.qt.card_deal_animation import CardDealAnimation, CardDealItem

        host = QWidget()
        host.resize(300, 300)
        layer = CardDealAnimation(host)
        layer.setGeometry(host.rect())
        self.addCleanup(host.close)
        asset = _solid_asset()

        with self.assertRaises(ValueError):
            layer.start(
                [
                    CardDealItem(asset=asset, target_rect=QRectF(0, 0, 60, 100), index=1),
                    CardDealItem(asset=asset, target_rect=QRectF(80, 0, 60, 100), index=1),
                ],
                QPointF(10.0, 10.0),
            )

        self.assertFalse(layer.is_running(), "被拒绝的调用不应启动时间轴")
        self.assertEqual(layer.diagnostics()["card_count"], 0)







    # ---- 运行时诊断（步骤 2） ------------------------------------------------








if __name__ == "__main__":
    unittest.main()
