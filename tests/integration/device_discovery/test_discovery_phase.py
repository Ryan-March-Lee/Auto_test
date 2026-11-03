"""发现页状态机（计划步骤 8）：扫描 / 等待 / 发牌 / 可交互只有一条转移路径。

发牌画面本身在 ``tests/ui/test_card_deal_animation.py``，卡片外观在
``test_panel_presentation.py``；这里只看流程状态：什么时候进入 ``DEALING``、什么时候
真实卡片才可交互、空结果与失败怎么落回查找画面，以及结果消费策略是否是显式的。
另外覆盖状态机的边界：清空/关闭之后迟到的扫描结论必须作废，未定义的跳转被拒绝。
"""

import threading
import unittest
from unittest import mock

from infrastructure.discovery.instrument_discovery import (
    SCAN_MODE_SIMULATED,
    InstrumentDiscoveryError,
    build_scanner,
)
from infrastructure.discovery.simulated_network import demo_idn_responses
from presentation.qt import instrument_discovery_panel as panel_module
from presentation.qt.animation_diagnostics import logger as animation_logger
from presentation.qt.instrument_discovery_panel import (
    BOX_STATE_BUSY,
    BOX_STATE_IDLE,
    BOX_STATE_RESULTS,
    DiscoveryPhase,
    DiscoveryWorker,
)

from .support import DiscoveryPanelTestCase, registry


def demo_result():
    """一次真实的模拟扫描结果：用来给“迟到的结论”当载荷。"""
    return build_scanner(
        registry=registry(),
        mode=SCAN_MODE_SIMULATED,
        responses=demo_idn_responses(),
        timeout_s=0.02,
    ).scan("192.168.1.200/30")


class _GatedScanner:
    """扫描阻塞在测试放行之前，用来制造“扫描还没结束就清空结果”的窗口。"""

    def __init__(self, result, gate):
        self.result = result
        self.gate = gate
        self.cancel_event = None
        self.cancelled = False

    def set_cancel_event(self, event):
        self.cancel_event = event

    def request_cancel(self):
        self.cancelled = True

    def scan(self, subnet, progress=None):
        self.gate.wait(5)
        return self.result


class DiscoveryPhaseTests(DiscoveryPanelTestCase):
    """一次查找的完整状态路径，以及空结果、失败与显式策略。"""

    def _finish_worker(self, panel) -> None:
        panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()

    def _empty_factory(self, _mode):
        return build_scanner(
            registry=registry(),
            mode=SCAN_MODE_SIMULATED,
            responses={},
            timeout_s=0.02,
        )

    def test_a_successful_scan_is_dealt_and_only_then_becomes_interactive(self):
        panel = self.make_panel()
        phases: list[str] = []
        panel.phase_changed.connect(phases.append)
        self.assertIs(panel.phase(), DiscoveryPhase.IDLE)

        panel.request_scan()
        self.app.processEvents()
        self.assertIs(panel.phase(), DiscoveryPhase.SCANNING)
        self.assertEqual(panel.box_state(), BOX_STATE_BUSY)

        self._finish_worker(panel)
        self.assertIs(panel.phase(), DiscoveryPhase.RESULT_PENDING)
        self.assertEqual(panel.box_state(), BOX_STATE_BUSY)
        self.assertTrue(panel.wait_stack.isVisible(), "等待牌堆仍在扫描期间播放")

        panel.wait_stack.finish_cycle()
        self.app.processEvents()
        self.app.processEvents()

        # 结果一消费就进入 DEALING：卡片按最终版面就位，但真实控件藏起来且不可点。
        self.assertIs(panel.phase(), DiscoveryPhase.DEALING)
        self.assertEqual(panel.box_state(), BOX_STATE_RESULTS)
        self.assertTrue(panel.deal_animation.is_running())
        self.assertTrue(panel.cards_area.isVisible())
        self.assertFalse(panel.cards_area.isEnabled(), "发牌期间真实卡片不能点")
        self.assertFalse(panel.status_label.isVisible(), "牌还背面朝上时先不剧透统计")
        for card in panel.cards():
            self.assertIsNotNone(card.graphicsEffect())

        panel.deal_animation.finish()
        self.app.processEvents()

        self.assertIs(panel.phase(), DiscoveryPhase.INTERACTIVE)
        self.assertTrue(panel.is_interactive())
        self.assertTrue(panel.cards_area.isEnabled())
        self.assertTrue(panel.status_label.isVisible())
        for card in panel.cards():
            self.assertIsNone(card.graphicsEffect())

        self.assertEqual(
            phases,
            ["scanning", "result-pending", "dealing", "interactive"],
            "状态只按扫描 → 等待 → 发牌 → 可交互推进",
        )

    def test_an_empty_result_skips_dealing_and_returns_to_the_scan_button(self):
        panel = self.make_panel(self._empty_factory)
        phases: list[str] = []
        panel.phase_changed.connect(phases.append)

        panel.request_scan()
        self._finish_worker(panel)
        self.assertIs(panel.phase(), DiscoveryPhase.RESULT_PENDING)
        panel.wait_stack.finish_cycle()
        self.app.processEvents()
        self.app.processEvents()

        self.assertIs(panel.phase(), DiscoveryPhase.IDLE)
        self.assertEqual(panel.box_state(), BOX_STATE_IDLE)
        self.assertNotIn("dealing", phases, "空结果不进入发牌")
        self.assertEqual(panel.card_count(), 0)
        self.assertFalse(panel.deal_animation.is_running())
        self.assertEqual(
            panel.deal_animation.diagnostics()["event"], "skip", "没有卡片时由动画统一记 skip"
        )
        self.assertTrue(panel.scan_btn.isVisible())
        self.assertTrue(panel.status_label.isVisible())
        self.assertIn("发现 0 台设备", panel.status_label.text())

    def test_a_failed_scan_returns_to_the_idle_layout_after_the_wait_cycle(self):
        def factory(_mode):
            from infrastructure.discovery.instrument_discovery import InstrumentDiscoveryError

            raise InstrumentDiscoveryError("设备表损坏")

        panel = self.make_panel(factory)
        panel.request_scan()
        self._finish_worker(panel)
        self.assertIs(panel.phase(), DiscoveryPhase.RESULT_PENDING)
        panel.wait_stack.finish_cycle()
        self.app.processEvents()
        self.app.processEvents()

        self.assertIs(panel.phase(), DiscoveryPhase.IDLE)
        self.assertEqual(panel.box_state(), BOX_STATE_IDLE)
        self.assertIn("扫描失败", panel.status_label.text())
        self.assertIn("设备表损坏", panel.status_label.text())
        self.assertEqual(panel.card_count(), 0)

    def test_clearing_results_and_shutdown_are_phases_too(self):
        panel = self.run_scan(self.make_panel())
        self.assertIs(panel.phase(), DiscoveryPhase.INTERACTIVE)

        panel.clear_results()
        self.app.processEvents()
        self.assertIs(panel.phase(), DiscoveryPhase.IDLE)
        self.assertEqual(panel.box_state(), BOX_STATE_IDLE)
        self.assertEqual(panel.card_count(), 0)

        self.assertTrue(panel.shutdown())
        self.assertIs(panel.phase(), DiscoveryPhase.CLOSED)

    def test_hiding_a_standalone_panel_stops_the_deal_and_restores_the_cards(self):
        """面板单独使用（没有页面级覆盖层）：自己被藏起来时按 ``page-hidden`` 收尾。

        计划步骤 12 的约定是“看不见的动画立即收尾”，页面级覆盖层由
        ``OverlayLifecycle`` 负责；面板单独使用时由面板自己的隐藏事件走同一个公开入口，
        因此计时器不会留在隐藏的控件上，卡片也不会停在透明、不可点的半状态。
        """
        panel = self.make_panel()
        panel.request_scan()
        self._finish_worker(panel)
        panel.wait_stack.finish_cycle()
        self.app.processEvents()
        self.app.processEvents()
        self.assertIs(panel.phase(), DiscoveryPhase.DEALING)
        self.assertTrue(panel.deal_animation.is_running())

        panel.hide()
        self.app.processEvents()

        self.assertFalse(panel.deal_animation.is_running(), "隐藏后发牌不能还在走")
        diagnostics = panel.deal_animation.diagnostics()
        self.assertEqual(diagnostics["cancel_reason"], "page-hidden")
        self.assertEqual(diagnostics["restore_result"], "revealed")
        self.assertIs(panel.phase(), DiscoveryPhase.INTERACTIVE)
        self.assertTrue(panel.cards_area.isEnabled())
        for card in panel.cards():
            self.assertIsNone(card.graphicsEffect())

    def test_the_full_cycle_policy_is_explicit_and_can_be_disabled(self):
        """产品要求完整一轮时由 ``WAIT_FOR_FULL_CYCLE`` 表达；关掉后结果一到就发牌。"""
        with mock.patch.object(panel_module, "WAIT_FOR_FULL_CYCLE", False):
            panel = self.make_panel()
            panel.request_scan()
            self._finish_worker(panel)

            # 没有发出 cycle_finished：结果照样进入 DEALING，说明不再隐含依赖牌堆周期。
            self.assertIs(panel.phase(), DiscoveryPhase.DEALING)
            self.assertTrue(panel.deal_animation.is_running())

            panel.deal_animation.finish()
            self.app.processEvents()
            self.assertIs(panel.phase(), DiscoveryPhase.INTERACTIVE)

    def test_a_failed_deal_start_leaves_the_page_interactive(self):
        panel = self.make_panel()
        panel.request_scan()
        self._finish_worker(panel)
        self.assertIs(panel.phase(), DiscoveryPhase.RESULT_PENDING)

        with mock.patch.object(
            panel.deal_animation, "start", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(RuntimeError):
                panel._consume_pending_outcome()

        self.app.processEvents()
        self.assertIs(panel.phase(), DiscoveryPhase.INTERACTIVE)
        self.assertEqual(panel.box_state(), BOX_STATE_RESULTS)
        self.assertTrue(panel.cards_area.isEnabled())
        for card in panel.cards():
            self.assertIsNone(card.graphicsEffect())

    def test_every_phase_transition_is_written_to_the_animation_log(self):
        panel = self.make_panel()
        with self.assertLogs(animation_logger, level="INFO") as captured:
            panel.request_scan()
            self._finish_worker(panel)
            panel.wait_stack.finish_cycle()
            self.app.processEvents()
            self.app.processEvents()
            panel.deal_animation.finish()
            self.app.processEvents()

        lines = [
            line for line in captured.output if "event=discovery_phase" in line
        ]
        self.assertTrue(lines, "状态转移要写固定字段日志，便于复现一次查找")
        expected_pairs = (
            ("scanning", "idle"),
            ("result-pending", "scanning"),
            ("dealing", "result-pending"),
            ("interactive", "dealing"),
        )
        for phase, previous in expected_pairs:
            self.assertTrue(
                any(
                    f"phase={phase} previous={previous}" in line for line in lines
                ),
                f"日志里缺少状态转移 {previous} → {phase}: {lines}",
            )
        self.assertTrue(all("box_state=" in line for line in lines))

    def test_clearing_results_during_a_scan_discards_its_late_result(self):
        """扫描还没结束时清空结果：这次扫描作废，迟到的结论不能把结果画回来。"""
        gate = threading.Event()
        scanner = _GatedScanner(demo_result(), gate)
        panel = self.make_panel(lambda _mode: scanner)

        panel.request_scan()
        self.assertIs(panel.phase(), DiscoveryPhase.SCANNING)
        worker = panel.worker
        self.assertIsNotNone(worker)

        panel.clear_results()
        self.app.processEvents()
        self.assertIs(panel.phase(), DiscoveryPhase.IDLE)
        self.assertTrue(panel.scan_btn.isVisible())

        gate.set()
        worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()

        self.assertIs(panel.phase(), DiscoveryPhase.IDLE)
        self.assertEqual(panel.box_state(), BOX_STATE_IDLE)
        self.assertEqual(panel.card_count(), 0)
        self.assertIsNone(panel._pending_outcome)
        self.assertIsNone(panel.worker, "线程结束后照常释放引用")
        self.assertTrue(scanner.cancelled, "作废旧扫描时同时请求协作取消")

    def test_clearing_results_while_waiting_discards_the_pending_outcome(self):
        panel = self.make_panel()
        panel.request_scan()
        self._finish_worker(panel)
        self.assertIs(panel.phase(), DiscoveryPhase.RESULT_PENDING)

        panel.clear_results()
        self.app.processEvents()
        self.assertIs(panel.phase(), DiscoveryPhase.IDLE)

        # 迟到的“这一轮走完了”不能把已经丢弃的结论补回来。
        panel.wait_stack.finish_cycle()
        self.app.processEvents()
        self.assertIs(panel.phase(), DiscoveryPhase.IDLE)
        self.assertEqual(panel.card_count(), 0)
        self.assertIsNone(panel._pending_outcome)

    def test_a_thread_start_failure_returns_to_the_idle_layout(self):
        """线程起不来时不能停在“正在查找”：回到可重试的空闲态，异常继续传播。"""
        panel = self.make_panel()

        with mock.patch.object(
            DiscoveryWorker, "start", side_effect=RuntimeError("线程已耗尽")
        ):
            with self.assertRaises(RuntimeError):
                panel.request_scan()

        self.app.processEvents()
        self.assertIs(panel.phase(), DiscoveryPhase.IDLE)
        self.assertEqual(panel.box_state(), BOX_STATE_IDLE)
        self.assertIsNone(panel.worker)
        self.assertTrue(panel.scan_btn.isVisible())
        self.assertTrue(panel.scan_btn.isEnabled())
        self.assertIn("无法启动扫描线程", panel.status_label.text())

    def test_undefined_phase_transitions_are_rejected(self):
        panel = self.make_panel()

        with self.assertRaises(ValueError):
            panel._set_phase(DiscoveryPhase.INTERACTIVE)
        self.assertIs(panel.phase(), DiscoveryPhase.IDLE, "被拒绝的转移不改状态")

        self.assertTrue(panel.shutdown())
        with self.assertRaises(ValueError):
            panel._set_phase(DiscoveryPhase.SCANNING)
        self.assertIs(panel.phase(), DiscoveryPhase.CLOSED, "CLOSED 是终态")

    def test_closed_ignores_late_scan_signals(self):
        panel = self.make_panel()
        panel.request_scan()
        self.assertTrue(panel.shutdown())
        self.assertIs(panel.phase(), DiscoveryPhase.CLOSED)

        panel._on_completed(demo_result())
        panel._on_failed("迟到的失败")
        panel._on_cancelled()
        panel._on_wait_cycle_finished()
        self.app.processEvents()

        self.assertIs(panel.phase(), DiscoveryPhase.CLOSED)
        self.assertEqual(panel.card_count(), 0)
        self.assertIsNone(panel._pending_outcome)

    def test_disabling_the_full_cycle_policy_also_covers_failure_and_empty(self):
        """策略关闭时，失败与空结果同样不等牌堆周期，直接落回查找画面。"""

        def broken(_mode):
            raise InstrumentDiscoveryError("设备表损坏")

        with mock.patch.object(panel_module, "WAIT_FOR_FULL_CYCLE", False):
            failed = self.make_panel(broken)
            failed.request_scan()
            self._finish_worker(failed)
            self.assertIs(failed.phase(), DiscoveryPhase.IDLE)
            self.assertIn("扫描失败", failed.status_label.text())

            empty = self.make_panel(self._empty_factory)
            empty.request_scan()
            self._finish_worker(empty)
            self.assertIs(empty.phase(), DiscoveryPhase.IDLE)
            self.assertEqual(empty.card_count(), 0)
            self.assertIn("发现 0 台设备", empty.status_label.text())

    def test_programmatic_scan_requests_are_refused_mid_deal(self):
        """发牌期间不接新的查找：卡片与覆盖层都还指着这一次结果。"""
        panel = self.make_panel()
        panel.request_scan()
        self._finish_worker(panel)
        panel.wait_stack.finish_cycle()
        self.app.processEvents()
        self.app.processEvents()
        self.assertIs(panel.phase(), DiscoveryPhase.DEALING)

        panel.request_scan()
        self.assertIs(panel.phase(), DiscoveryPhase.DEALING)
        self.assertTrue(panel.deal_animation.is_running())


if __name__ == "__main__":
    unittest.main()
