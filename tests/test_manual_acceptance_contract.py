"""门禁：真实窗口验收脚本的判定规则必须可离线验证。

``tools/manual_acceptance.py`` 只在真实 Windows 桌面上跑完整场景，但它的判定
（环境门槛、等待周期、阶段序列、窗口可见性、截图有效性）是纯函数，可以在这里
固定下来：脚本不会因为环境不对、窗口被裁切、截图不是桌面合成画面或结果提前
消费而误报通过。
"""

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.manual_acceptance import (  # noqa: E402  (需先补齐 sys.path)
    EXPECTED_PHASES,
    MIN_WINDOW_VISIBLE_RATIO,
    SCENARIO_FORBIDDEN_PHASES,
    SCENARIO_KEYS,
    capture_verdict,
    environment_problem,
    phase_sequence_verdict,
    wait_cycle_verdict,
    window_visibility_verdict,
)


class EnvironmentVerdictTests(unittest.TestCase):
    """只有 Windows 桌面会话才接受为真实验收环境。"""

    def test_windows_desktop_is_accepted(self):
        self.assertIsNone(
            environment_problem(platform="windows", sys_platform="win32")
        )
        self.assertIsNone(
            environment_problem(platform="Windows", sys_platform="win32")
        )

    def test_offscreen_platform_is_rejected(self):
        problem = environment_problem(platform="offscreen", sys_platform="win32")
        self.assertIsNotNone(problem)
        self.assertIn("offscreen", problem)

    def test_minimal_platforms_are_rejected(self):
        for platform in ("minimal", "minimalegl"):
            with self.subTest(platform=platform):
                problem = environment_problem(
                    platform=platform, sys_platform="win32"
                )
                self.assertIsNotNone(problem)
                self.assertIn(platform, problem)

    def test_non_windows_system_is_rejected(self):
        for system in ("linux", "darwin"):
            with self.subTest(system=system):
                problem = environment_problem(
                    platform="windows", sys_platform=system
                )
                self.assertIsNotNone(problem)
                self.assertIn(system, problem)

    def test_non_windows_desktop_platform_is_rejected(self):
        problem = environment_problem(platform="cocoa", sys_platform="win32")
        self.assertIsNotNone(problem)


class PhaseSequenceVerdictTests(unittest.TestCase):
    """阶段记录必须完整、有序、无重复，失败/空结果不得进入发牌。"""

    def test_exact_sequence_passes(self):
        ok, detail = phase_sequence_verdict(
            ["scanning", "result-pending", "dealing", "interactive"],
            ["scanning", "result-pending", "dealing", "interactive"],
        )
        self.assertTrue(ok, detail)

    def test_missing_phase_fails(self):
        ok, detail = phase_sequence_verdict(
            ["scanning", "result-pending", "dealing"],
            ["scanning", "result-pending", "dealing", "interactive"],
        )
        self.assertFalse(ok)
        self.assertIn("预期", detail)

    def test_out_of_order_sequence_fails(self):
        ok, _detail = phase_sequence_verdict(
            ["scanning", "dealing", "result-pending", "interactive"],
            ["scanning", "result-pending", "dealing", "interactive"],
        )
        self.assertFalse(ok)

    def test_duplicate_phase_fails(self):
        ok, _detail = phase_sequence_verdict(
            ["scanning", "result-pending", "dealing", "dealing", "interactive"],
            ["scanning", "result-pending", "dealing", "interactive"],
        )
        self.assertFalse(ok)

    def test_forbidden_phase_fails_even_if_sequence_matches(self):
        ok, detail = phase_sequence_verdict(
            ["scanning", "result-pending", "dealing"],
            ["scanning", "result-pending", "dealing"],
            forbidden=["dealing"],
        )
        self.assertFalse(ok)
        self.assertIn("dealing", detail)

    def test_empty_observation_fails(self):
        ok, _detail = phase_sequence_verdict(
            [], ["scanning", "result-pending", "idle"]
        )
        self.assertFalse(ok)


class WaitCycleVerdictTests(unittest.TestCase):
    """结果要早于首轮结束返回，并且从扫描开始到结局至少跨过一整轮。"""

    def test_result_early_and_outcome_after_full_cycle_passes(self):
        ok, detail = wait_cycle_verdict(
            scan_ms=0.0, pending_ms=480.0, outcome_ms=2160.0, cycle_ms=2140.0
        )
        self.assertTrue(ok, detail)

    def test_outcome_before_full_cycle_fails(self):
        ok, detail = wait_cycle_verdict(
            scan_ms=0.0, pending_ms=200.0, outcome_ms=900.0, cycle_ms=2140.0
        )
        self.assertFalse(ok)
        self.assertIn("短于一轮", detail)

    def test_result_arriving_after_first_cycle_fails(self):
        ok, detail = wait_cycle_verdict(
            scan_ms=0.0, pending_ms=2500.0, outcome_ms=4300.0, cycle_ms=2140.0
        )
        self.assertFalse(ok)
        self.assertIn("首轮等待结束之后", detail)

    def test_small_scheduling_error_is_tolerated(self):
        ok, detail = wait_cycle_verdict(
            scan_ms=0.0, pending_ms=480.0, outcome_ms=2070.0, cycle_ms=2140.0
        )
        self.assertTrue(ok, detail)

    def test_large_shortfall_is_not_tolerated(self):
        ok, _detail = wait_cycle_verdict(
            scan_ms=0.0, pending_ms=480.0, outcome_ms=1800.0, cycle_ms=2140.0
        )
        self.assertFalse(ok)

    def test_custom_tolerance_is_respected(self):
        ok, _detail = wait_cycle_verdict(
            scan_ms=0.0,
            pending_ms=100.0,
            outcome_ms=1000.0,
            cycle_ms=2140.0,
            tolerance_ms=1200.0,
        )
        self.assertTrue(ok)


class WindowVisibilityVerdictTests(unittest.TestCase):
    """窗口被屏幕边缘裁切时不能作为通过证据。"""

    def test_fully_visible_window_passes(self):
        ok, detail = window_visibility_verdict(1.0)
        self.assertTrue(ok, detail)

    def test_threshold_ratio_passes(self):
        ok, _detail = window_visibility_verdict(MIN_WINDOW_VISIBLE_RATIO)
        self.assertTrue(ok)

    def test_clipped_window_fails(self):
        ok, detail = window_visibility_verdict(0.98)
        self.assertFalse(ok)
        self.assertIn("裁切", detail)

    def test_threshold_is_strict(self):
        self.assertGreaterEqual(MIN_WINDOW_VISIBLE_RATIO, 0.99)


class CaptureVerdictTests(unittest.TestCase):
    """只有桌面合成且写出内容的截图才能作为验收证据。"""

    def test_screen_capture_with_content_passes(self):
        ok, detail = capture_verdict(
            source="screen", width=1366, height=768, byte_count=1024
        )
        self.assertTrue(ok, detail)

    def test_widget_render_fails(self):
        ok, detail = capture_verdict(
            source="widget", width=1366, height=768, byte_count=1024
        )
        self.assertFalse(ok)
        self.assertIn("widget", detail)

    def test_zero_size_fails(self):
        ok, _detail = capture_verdict(
            source="screen", width=0, height=0, byte_count=1024
        )
        self.assertFalse(ok)

    def test_empty_file_fails(self):
        ok, _detail = capture_verdict(
            source="screen", width=1366, height=768, byte_count=0
        )
        self.assertFalse(ok)


class ScenarioEvidenceContractTests(unittest.TestCase):
    """场景表与阶段预期必须一一对应，避免新增场景时漏掉判定。"""

    def test_every_scenario_declares_expected_phases(self):
        self.assertEqual(set(EXPECTED_PHASES), set(SCENARIO_KEYS))

    def test_every_scenario_declares_forbidden_phases(self):
        self.assertEqual(set(SCENARIO_FORBIDDEN_PHASES), set(SCENARIO_KEYS))

    def test_every_sequence_starts_with_scanning_and_holds_result(self):
        for key, phases in EXPECTED_PHASES.items():
            with self.subTest(scenario=key):
                self.assertEqual(phases[0], "scanning")
                self.assertIn("result-pending", phases)

    def test_failure_and_empty_never_deal(self):
        for key in ("failure", "empty"):
            with self.subTest(scenario=key):
                self.assertIn("dealing", SCENARIO_FORBIDDEN_PHASES[key])
                self.assertNotIn("dealing", EXPECTED_PHASES[key])

    def test_every_sequence_ends_closed(self):
        for key, phases in EXPECTED_PHASES.items():
            with self.subTest(scenario=key):
                self.assertEqual(phases[-1], "closed")

    def test_deal_and_resize_end_interactive_before_close(self):
        for key in ("deal", "resize"):
            with self.subTest(scenario=key):
                self.assertEqual(EXPECTED_PHASES[key][-2], "interactive")

    def test_cancel_scenario_ends_idle_before_close(self):
        self.assertEqual(EXPECTED_PHASES["back"][-2], "idle")


if __name__ == "__main__":
    unittest.main()
