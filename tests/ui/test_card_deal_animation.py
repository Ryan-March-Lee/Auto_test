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

    def test_every_card_lands_before_the_first_one_turns_over(self):
        from presentation.qt.card_deal_animation import (
            DEAL_FLY_MS,
            DEAL_SETTLE_MS,
            DEAL_STAGGER_MS,
            FLIP_MS,
            FLIP_STAGGER_MS,
        )

        layer, _items = self._animation(4)
        deal_end = 3 * DEAL_STAGGER_MS + DEAL_FLY_MS
        for index in range(4):
            self.assertAlmostEqual(layer.deal_progress(index, deal_end), 1.0)
            self.assertEqual(
                layer.flip_progress(index, deal_end + DEAL_SETTLE_MS - 1), 0.0
            )
        self.assertGreater(
            layer.flip_progress(0, deal_end + DEAL_SETTLE_MS + FLIP_STAGGER_MS / 2.0),
            0.0,
        )
        self.assertEqual(
            layer.flip_progress(1, deal_end + DEAL_SETTLE_MS + FLIP_STAGGER_MS / 2.0),
            0.0,
            "翻牌是一张接一张：第二张要等第一张翻完才起步",
        )
        last_start = deal_end + DEAL_SETTLE_MS + 3 * FLIP_STAGGER_MS
        self.assertAlmostEqual(layer.flip_progress(3, last_start + FLIP_MS), 1.0)
        self.assertAlmostEqual(layer.duration_ms(), last_start + FLIP_MS)

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

    def test_the_back_and_the_front_both_come_from_the_asset(self):
        """发牌贴 ``asset.back``、翻开贴 ``asset.front``：动画不读控件画面。"""
        from presentation.qt.card_deal_animation import DEAL_FLY_MS

        asset = _solid_asset(QColor(255, 0, 0), QColor(0, 0, 255))
        layer, items = self._animation(1, assets=[asset])
        centre = items[0].target_rect.center().toPoint()

        layer._elapsed = float(DEAL_FLY_MS)
        back_pixel = _sample_colour(layer.grab().toImage(), centre)
        self.assertGreater(back_pixel.blue(), 200, "落定后应先露出资产牌背")
        self.assertLess(back_pixel.red(), 60)

        layer._elapsed = layer.duration_ms()
        front_pixel = _sample_colour(layer.grab().toImage(), centre)
        self.assertGreater(front_pixel.red(), 200, "翻完后应露出资产正面")
        self.assertLess(front_pixel.blue(), 60)

    def test_return_flips_from_front_to_back_then_flies_to_the_deck(self):
        """按“返回”的反向画面：先正面、再翻回背面，最后落在牌堆位置。"""
        from presentation.qt.card_deal_animation import (
            RETURN_FLIP_MS,
            RETURN_FLY_MS,
        )

        asset = _solid_asset(QColor(255, 0, 0), QColor(0, 0, 255))
        layer, items = self._animation(1, assets=[asset])
        layer._timer.stop()
        deck = QPointF(200.0, 360.0)
        layer.start_return(items, deck)
        layer._timer.stop()
        centre = items[0].target_rect.center().toPoint()

        self.assertEqual(layer.direction(), "return")
        self.assertEqual(layer.phase_at(0.0), "flipping-back")
        self.assertAlmostEqual(
            layer.duration_ms(), RETURN_FLIP_MS + RETURN_FLY_MS
        )
        self.assertEqual(
            layer.phase_at(float(RETURN_FLIP_MS) + 1.0), "returning"
        )

        front = _sample_colour(layer.grab().toImage(), centre)
        self.assertGreater(front.red(), 200, "收牌从卡片正面开始")
        self.assertLess(front.blue(), 60)

        layer._elapsed = float(RETURN_FLIP_MS)
        back = _sample_colour(layer.grab().toImage(), centre)
        self.assertGreater(back.blue(), 200, "翻完后回到牌背")
        self.assertLess(back.red(), 60)

        layer._elapsed = float(RETURN_FLIP_MS) + RETURN_FLY_MS * 0.95
        near_deck = _sample_colour(
            layer.grab().toImage(), deck.toPoint()
        )
        self.assertGreater(near_deck.blue(), 200, "牌已经飞到牌堆位置")
        self.assertEqual(layer.direction(), "return")
        self.assertAlmostEqual(
            layer.duration_ms(), RETURN_FLIP_MS + RETURN_FLY_MS
        )

    def test_return_does_not_show_a_deck_before_a_card_lands(self):
        """收牌开始时目的地是空的：不能先在下方凭空摆一张牌背。"""
        asset = _solid_asset(QColor(255, 0, 0), QColor(0, 0, 255))
        layer, items = self._animation(2, assets=[asset, asset])
        layer._timer.stop()
        deck = QPointF(200.0, 300.0)
        layer.start_return(items, deck)
        layer._timer.stop()

        # 翻面阶段（还没有牌落回）牌堆位置应和空白背景一样，没有牌背。
        layer._elapsed = 0.0
        image = layer.grab().toImage()
        self.assertEqual(
            _sample_colour(image, deck.toPoint()).getRgb(),
            _sample_colour(image, QPoint(350, 350)).getRgb(),
            "第一张牌落回之前，牌堆位置不能出现卡牌",
        )

        # 最后一张牌落回：牌堆这时才由落回的牌叠出来。
        layer._elapsed = layer.duration_ms()
        landed = _sample_colour(layer.grab().toImage(), deck.toPoint())
        self.assertGreater(landed.blue(), 200, "落回后牌堆位置应是资产牌背")
        self.assertLess(landed.red(), 60)

    def test_return_takes_the_last_card_back_first(self):
        """收牌顺序与发牌相反：最后一张先翻面，第一张最后回到牌堆。"""
        from presentation.qt.card_deal_animation import RETURN_FLIP_STAGGER_MS

        layer, items = self._animation(2)
        layer._timer.stop()
        layer.start_return(items, QPointF(200.0, 360.0))
        layer._timer.stop()

        halfway = RETURN_FLIP_STAGGER_MS / 2.0
        self.assertGreater(layer.return_flip_progress(1, halfway), 0.0)
        self.assertEqual(layer.return_flip_progress(0, halfway), 0.0)
        self.assertGreater(
            layer.return_fly_progress(1, layer.duration_ms()),
            0.9,
        )
        self.assertEqual(layer.return_flip_progress(0, layer.duration_ms()), 1.0)

    def test_return_finishes_without_handing_cards_back(self):
        """收牌不恢复真实卡片：页面已经清空结果，不能再收到 card_revealed。"""
        layer, items = self._animation(2)
        layer._timer.stop()
        layer.start_return(items, QPointF(200.0, 360.0))
        layer._timer.stop()
        revealed: list[int] = []
        finished: list[bool] = []
        layer.card_revealed.connect(revealed.append)
        layer.finished.connect(lambda: finished.append(True))

        layer.finish()

        self.assertEqual(revealed, [])
        self.assertEqual(finished, [True])
        self.assertEqual(layer.diagnostics()["direction"], "return")
        self.assertFalse(layer.isVisible())

    def test_the_photon_back_particles_move_while_the_card_is_face_down(self):
        """牌背粒子由资产开关控制：打开时两帧不同，关掉后画面只由静态位图决定。"""
        from dataclasses import replace

        from presentation.qt.card_assets import render_card_asset
        from presentation.qt.card_deal_animation import DEAL_FLY_MS
        from presentation.qt.device_cards import (
            CARD_HEIGHT,
            CARD_WIDTH,
            InstrumentCardData,
        )

        asset = render_card_asset(
            InstrumentCardData("DP832A（DP8B1519）", code="DP8B1519")
        )
        self.assertTrue(asset.back_particles)
        target = QRectF(20.0, 40.0, CARD_WIDTH, CARD_HEIGHT)
        host_size = (CARD_WIDTH + 40, CARD_HEIGHT + 80)
        layer, _items = self._animation(
            1, assets=[asset], targets=[target], host_size=host_size
        )

        layer._elapsed = float(DEAL_FLY_MS)
        first = layer.grab().toImage()
        layer._elapsed = float(DEAL_FLY_MS) + 110.0
        second = layer.grab().toImage()
        self.assertNotEqual(first, second, "牌背朝上时绿色粒子应在动")

        still_layer, _items = self._animation(
            1,
            assets=[replace(asset, back_particles=False)],
            targets=[target],
            host_size=host_size,
        )
        still_layer._elapsed = float(DEAL_FLY_MS)
        first_still = still_layer.grab().toImage()
        still_layer._elapsed = float(DEAL_FLY_MS) + 110.0
        second_still = still_layer.grab().toImage()
        self.assertEqual(first_still, second_still, "没有粒子的牌背不应逐帧变化")

    def test_the_animation_holds_no_widgets_even_without_a_discovery_page(self):
        """独立播放：item 里只有资产与几何，没有卡片控件，也没有页面依赖。"""
        from presentation.qt.card_deal_animation import CardDealItem

        layer, items = self._animation(2)
        layer.grab()

        self.assertTrue(all(isinstance(item, CardDealItem) for item in layer._items))
        self.assertFalse(
            any(isinstance(item, QWidget) for item in layer._items),
            "动画不能持有卡片控件：翻开由页面按信号恢复",
        )
        self.assertEqual(layer.item_count(), 2)
        self.assertGreater(layer.diagnostics()["drawn_cards"], 0)

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

    def test_a_queued_tick_after_cancel_does_not_finish_again(self):
        """取消后已经排队的 timeout 不能拿空输入再走一遍收尾。"""
        layer, _items = self._animation(2)
        finished: list[bool] = []
        layer.finished.connect(lambda: finished.append(True))
        layer.cancel(reveal=False, reason="results-cleared")

        layer._advance()

        self.assertEqual(finished, [], "取消之后不能再发 finished")
        snapshot = layer.diagnostics()
        self.assertEqual(snapshot["event"], "cancel")
        self.assertEqual(snapshot["cancel_reason"], "results-cleared")

    def test_finishing_records_the_end_time(self):
        layer, _items = self._animation(2)

        layer.finish()

        snapshot = layer.diagnostics()
        self.assertEqual(snapshot["event"], "finish")
        self.assertEqual(snapshot["phase"], "finished")
        self.assertFalse(snapshot["running"])
        self.assertTrue(snapshot["ended_at"], "结束时间要写进快照")
        self.assertEqual(snapshot["card_count"], 2, "完成后的快照仍是这次动画的 2 张")
        self.assertEqual(snapshot["revealed_count"], 2)

    def test_cancel_records_the_reason_and_the_restore_result(self):
        from presentation.qt.animation_diagnostics import logger

        layer, _items = self._animation(3)

        with self.assertLogs(logger, level="INFO") as captured:
            layer.cancel(reveal=False, reason="cards-cleared")

        snapshot = layer.diagnostics()
        self.assertEqual(snapshot["cancel_reason"], "cards-cleared")
        self.assertEqual(snapshot["restore_result"], "hidden")
        self.assertEqual(snapshot["event"], "cancel")
        # 取消后内部状态清空，但快照仍描述被取消的那一次，不能自相矛盾。
        self.assertEqual(snapshot["card_count"], 3)
        self.assertEqual(snapshot["phase"], "dealing")
        self.assertEqual(snapshot["revealed_count"], 0)
        self.assertFalse(snapshot["running"])
        output = "\n".join(captured.output)
        self.assertIn("event=cancel", output)
        self.assertIn("cancel_reason=cards-cleared", output)
        self.assertIn("restore_result=hidden", output)

    def test_cancel_with_reveal_hands_every_card_back_to_the_page(self):
        layer, _items = self._animation(3)
        revealed: list[int] = []
        layer.card_revealed.connect(revealed.append)

        layer.cancel(reveal=True, reason="results-cleared")

        snapshot = layer.diagnostics()
        self.assertEqual(snapshot["restore_result"], "revealed")
        self.assertEqual(snapshot["revealed_count"], 3, "恢复显示后的张数应与恢复结果一致")
        self.assertEqual(revealed, [0, 1, 2], "取消也要把每一张交回页面恢复")

    def test_cancel_after_finish_is_only_cleanup(self):
        """播完后清结果不算中断：finish 快照不能被 cancel 覆盖。"""
        from presentation.qt.animation_diagnostics import logger

        layer, _items = self._animation(2)
        layer.finish()

        with self.assertNoLogs(logger, level="INFO"):
            layer.cancel(reveal=False, reason="new-scan")

        snapshot = layer.diagnostics()
        self.assertEqual(snapshot["event"], "finish")
        self.assertEqual(snapshot["phase"], "finished")
        self.assertIsNone(snapshot["cancel_reason"])

    def test_finishing_twice_announces_finished_once(self):
        """重复收尾只算一次：页面不该收到两遍“整段结束”。"""
        layer, _items = self._animation(2)
        finished: list[bool] = []
        layer.finished.connect(lambda: finished.append(True))

        layer.finish()
        layer.finish()

        self.assertEqual(finished, [True])

    def test_an_empty_start_ends_the_running_deal_before_skipping(self):
        """运行中收到空输入：先按完成路径把卡片交回页面，再记录 skip。

        否则上一次发牌的 ``card_revealed`` / ``finished`` 永远不会发出，页面就会留下
        一直透明、不可点的卡片。
        """
        layer, _items = self._animation(2)
        revealed: list[int] = []
        finished: list[bool] = []
        layer.card_revealed.connect(revealed.append)
        layer.finished.connect(lambda: finished.append(True))
        layer._timer.start()

        layer.start([], QPointF(10.0, 10.0))

        self.assertEqual(revealed, [0, 1], "空输入也要先把上一次的每张牌交回页面")
        self.assertEqual(finished, [True], "页面靠 finished 恢复交互")
        self.assertFalse(layer.is_running())
        self.assertEqual(layer.item_count(), 0)
        self.assertEqual(layer.diagnostics()["event"], "skip")

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

    def test_a_start_without_items_reports_a_skip_instead_of_a_start(self):
        """没有 item 时只有 skip、没有 start：日志据此区分“动画没有启动”。"""
        from presentation.qt.card_deal_animation import CardDealAnimation

        host = QWidget()
        host.resize(300, 300)
        layer = CardDealAnimation(host)
        layer.setGeometry(host.rect())
        self.addCleanup(host.close)

        layer.start([], QPointF(10.0, 10.0))

        snapshot = layer.diagnostics()
        self.assertEqual(snapshot["event"], "skip")
        self.assertEqual(snapshot["phase"], "idle")
        self.assertEqual(snapshot["card_count"], 0)
        self.assertFalse(snapshot["running"])

    def test_skip_is_recorded_once_with_the_caller_reason(self):
        """``skip`` 由动画层统一记录一次，调用方只提供原因。"""
        from presentation.qt.animation_diagnostics import logger
        from presentation.qt.card_deal_animation import CardDealAnimation

        host = QWidget()
        host.resize(300, 300)
        layer = CardDealAnimation(host)
        layer.setGeometry(host.rect())
        self.addCleanup(host.close)

        with self.assertLogs(logger, level="INFO") as captured:
            layer.skip(reason="empty-results")

        skips = [line for line in captured.output if "event=skip" in line]
        self.assertEqual(len(skips), 1, "同一次未启动只允许一条 skip")
        self.assertIn("skip_reason=empty-results", skips[0])
        snapshot = layer.diagnostics()
        self.assertEqual(snapshot["event"], "skip")
        self.assertIsNone(snapshot["started_at"])

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

    def test_start_rejects_sparse_indices(self):
        """``index`` 必须连续：页面直接拿它当卡片列表下标，缺口会恢复错卡片。"""
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
                    CardDealItem(asset=asset, target_rect=QRectF(0, 0, 60, 100), index=0),
                    CardDealItem(asset=asset, target_rect=QRectF(80, 0, 60, 100), index=2),
                ],
                QPointF(10.0, 10.0),
            )

        self.assertFalse(layer.is_running(), "被拒绝的调用不应启动时间轴")
        self.assertEqual(layer.diagnostics()["card_count"], 0)

    def test_start_rejects_an_unknown_front_source(self):
        """``front_source`` 只认资产与截图两种值：写错就失去诊断意义。"""
        layer, _items = self._animation(2)

        with self.assertRaises(ValueError):
            layer.start(
                _items,
                QPointF(10.0, 10.0),
                front_source="grab",
            )

        self.assertFalse(layer.is_running(), "被拒绝的调用不应启动时间轴")

    def test_the_targets_are_copied_when_the_animation_starts(self):
        """落点在开始时复制一份：调用方之后再改 item 也不会挪动已经开始的牌。"""
        target = QRectF(30.0, 40.0, 60.0, 100.0)
        layer, items = self._animation(1, targets=[target])
        frozen = layer._target_rect(0)

        items[0].target_rect.moveTo(200.0, 200.0)

        self.assertEqual(layer._target_rect(0), frozen, "落点必须与开始时的坐标一致")

    def test_the_caller_hands_over_overlay_coordinates(self):
        """页面可以在开始前换算出覆盖层坐标：落点与裁剪区都按给的坐标冻结。"""
        target = QRectF(121.5, 242.25, 60.0, 100.0)
        clip = QRectF(0.0, 0.0, 300.0, 220.0)
        layer, _items = self._animation(1, targets=[target])
        # 重新按调用方坐标开始一次：验证 start 接受覆盖层坐标与裁剪矩形。
        layer.start(
            layer._items,
            QPointF(10.0, 10.0),
            clip_rect=clip,
        )
        layer._timer.stop()

        self.assertEqual(layer._target_rect(0), target)
        self.assertEqual(layer._cards_clip_rect(), clip)
        self.assertEqual(layer.diagnostics()["target_rects"][0], "(121.5,242.2,60.0,100.0)")

    def test_refreshing_the_clip_keeps_the_frozen_targets(self):
        """只换裁剪遮罩时落点不动：布局落定后的视口微调不该重算已经开始的动画。"""
        target = QRectF(30.0, 40.0, 60.0, 100.0)
        narrow = QRectF(0.0, 0.0, 300.0, 220.0)
        wide = QRectF(0.0, 0.0, 360.0, 220.0)
        layer, _items = self._animation(1, targets=[target])
        layer.start(layer._items, QPointF(10.0, 10.0), clip_rect=narrow)
        layer._timer.stop()

        layer.refresh_clip_rect(wide)

        self.assertEqual(layer._target_rect(0), target, "裁剪区变化不能挪动落点")
        self.assertEqual(layer._cards_clip_rect(), wide, "裁剪区要跟上新的可见区域")

    def test_the_layer_is_only_a_picture(self):
        from PySide6.QtCore import Qt

        layer, _items = self._animation(2)
        self.assertTrue(layer.testAttribute(Qt.WA_TransparentForMouseEvents))
        self.assertEqual(layer.focusPolicy(), Qt.NoFocus)

    # ---- 运行时诊断（步骤 2） ------------------------------------------------

    def test_diagnostics_expose_the_fixed_runtime_fields(self):
        """动画开始后，快照里能读到起点、落点、覆盖层与固定字段名。"""
        layer, _items = self._animation(2)

        snapshot = layer.diagnostics()

        for field in (
            "event",
            "running",
            "phase",
            "started_at",
            "ended_at",
            "elapsed_ms",
            "duration_ms",
            "card_count",
            "front_source",
            "deck_x",
            "deck_y",
            "target_rects",
            "overlay_visible",
            "overlay_width",
            "overlay_height",
            "overlay_parent",
            "paint_frames",
            "first_paint_ms",
            "drawn_cards",
            "first_drawn_cards",
            "revealed_count",
            "cancel_reason",
            "restore_result",
        ):
            self.assertIn(field, snapshot, f"诊断字段名固定为 {field}")
        self.assertEqual(snapshot["event"], "start")
        self.assertEqual(snapshot["phase"], "dealing")
        self.assertEqual(snapshot["card_count"], 2)
        self.assertEqual(len(snapshot["target_rects"]), 2)
        self.assertIsInstance(snapshot["overlay_visible"], bool)
        self.assertTrue(snapshot["overlay_parent"], "覆盖层父控件要写进快照")
        self.assertTrue(snapshot["started_at"], "动画开始时间要写进快照")

    def test_diagnostics_name_the_screenshot_fallback(self):
        """排障回退时快照写明 ``screenshot``：两种来源在日志里分得开。"""
        from presentation.qt.card_deal_animation import DEAL_FRONT_SOURCE_SCREENSHOT

        layer, items = self._animation(2)

        layer.start(
            items,
            QPointF(10.0, 10.0),
            front_source=DEAL_FRONT_SOURCE_SCREENSHOT,
        )
        layer._timer.stop()

        self.assertEqual(
            layer.diagnostics()["front_source"], DEAL_FRONT_SOURCE_SCREENSHOT
        )

    def test_every_painted_frame_is_counted(self):
        """``paint_frames`` 是绘制帧数，不是“第一帧之后就不再计数”。"""
        layer, _items = self._animation(3)

        self.assertEqual(layer.diagnostics()["paint_frames"], 0)
        layer.grab()
        layer.grab()

        snapshot = layer.diagnostics()
        self.assertGreaterEqual(snapshot["paint_frames"], 2, "每一帧都要计数")
        self.assertIsNotNone(snapshot["first_paint_ms"])
        self.assertGreater(
            snapshot["first_drawn_cards"], 0, "第一帧要真的画出牌堆/飞出的牌"
        )

    def test_drawn_cards_describe_the_latest_frame(self):
        """``drawn_cards`` 跟着最近一帧刷新；首帧的值留在 ``first_drawn_cards``。"""
        from presentation.qt.card_deal_animation import DEAL_FLY_MS, DEAL_STAGGER_MS

        layer, _items = self._animation(2, host_size=(200, 220))
        layer.grab()
        snapshot = layer.diagnostics()
        first = snapshot["drawn_cards"]
        self.assertGreater(first, 0, "第一帧有牌堆")
        self.assertEqual(snapshot["first_drawn_cards"], first)

        # 牌已发完（牌堆收起），再把裁剪区缩到左上角：这一帧一张也画不出来。
        layer._elapsed = DEAL_STAGGER_MS + DEAL_FLY_MS
        layer.refresh_clip_rect(QRectF(0.0, 0.0, 1.0, 1.0))
        layer.grab()

        snapshot = layer.diagnostics()
        self.assertEqual(snapshot["drawn_cards"], 0, "最近一帧没有画出任何牌")
        self.assertEqual(snapshot["first_drawn_cards"], first, "首帧的值保持不变")

    def test_cards_painted_outside_the_overlay_are_not_counted(self):
        """落在覆盖层/裁剪区之外的牌不算“画出来了”，否则 first_paint 会骗人。"""
        asset = _solid_asset()
        layer, _items = self._animation(
            2,
            assets=[asset, asset],
            targets=[
                QRectF(20.0, 20.0, 60.0, 100.0),
                QRectF(600.0, 600.0, 60.0, 100.0),
            ],
            host_size=(200, 220),
            deck=QPointF(-500.0, -500.0),
        )
        layer._elapsed = layer.duration_ms()
        layer.grab()

        snapshot = layer.diagnostics()
        self.assertEqual(snapshot["drawn_cards"], 1, "只有落在覆盖层里的那张算画出")
        self.assertEqual(snapshot["paint_frames"], 1)

    def test_the_log_separates_start_from_first_paint(self):
        """日志里同时留下 start 与 first_paint，复现“启动但没画出来”时有据可查。"""
        from presentation.qt.animation_diagnostics import logger

        layer, _items = self._animation(2)

        with self.assertLogs(logger, level="INFO") as captured:
            layer.start(
                layer._items,
                QPointF(160.0, 360.0),
            )
            layer._timer.stop()
            layer.grab()

        output = "\n".join(captured.output)
        self.assertIn("event=start", output)
        self.assertIn("front_source=asset", output)
        self.assertIn("event=first_paint", output)
        self.assertIn("card_count=2", output)
        self.assertIn("overlay_parent=", output)


if __name__ == "__main__":
    unittest.main()
