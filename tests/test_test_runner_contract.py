"""门禁：测试入口保留分层、门禁顺序和并行排除清单。

``tools/run_tests.py`` 的规则仍必须是可断言的数据：纯界面改动不跑全量、UI 层先过仓库
门禁、硬件测试不进入并行全量。
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_tests import (  # noqa: E402  (需先补齐 sys.path)
    GATE_MODULES,
    LAYERS,
    PARALLEL_EXCLUSIONS,
    PARALLEL_SERIAL_MODULES,
    build_plan,
)


def _labels(plan):
    return [step.label for step in plan]


def _flatten(plan):
    return [argument for step in plan for argument in step.arguments]


class TestRunnerPlanTests(unittest.TestCase):
    """每个改动面的步骤顺序。"""

    def test_every_layer_runs_compile_then_dependency_checks_first(self):
        for layer in LAYERS:
            with self.subTest(layer=layer):
                plan = build_plan(layer=layer)
                self.assertEqual(_labels(plan)[:2], ["编译检查", "依赖核对"])
                self.assertIn("tools/compile_check.py", plan[0].arguments)
                self.assertIn("tools/export_dependency_lock.py", plan[1].arguments)

    def test_default_layer_is_the_full_offline_suite(self):
        plan = build_plan()
        self.assertEqual(_labels(plan)[-1], "全量离线测试")
        # 全量发现必须以仓库根为顶层目录（``-t .``），避免 tests.integration
        # 以 integration.* 再导入一份同名模块。
        self.assertEqual(
            plan[-1].arguments,
            (
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-t",
                ".",
                "-p",
                "test_*.py",
                "-v",
            ),
        )
        self.assertNotIn("--parallel", _flatten(plan))

    def test_a_ui_change_runs_all_gates_and_only_the_presentation_tests(self):
        plan = build_plan(layer="ui")
        self.assertEqual(_labels(plan), ["编译检查", "依赖核对", "仓库门禁", "表现层测试"])
        for module in GATE_MODULES:
            self.assertIn(module, plan[2].arguments)
        self.assertIn("tests/ui", plan[3].arguments)
        self.assertNotIn("tests/integration", _flatten(plan))

    def test_parallel_plan_excludes_hardware_qt_flow_and_global_scan(self):
        plan = build_plan(parallel=True, workers=2)
        self.assertEqual(_labels(plan)[-2:], ["并行离线测试", "串行补跑"])
        parallel = plan[-2].arguments
        self.assertIn("pytest", parallel)
        self.assertIn("2", parallel)
        for exclusion in PARALLEL_EXCLUSIONS:
            self.assertIn(exclusion, parallel)
        for module in PARALLEL_SERIAL_MODULES:
            self.assertIn(module, plan[-1].arguments)

    def test_the_hardware_layer_never_runs_through_parallel_discovery(self):
        plan = build_plan(layer="hardware")
        self.assertEqual(_labels(plan)[-1], "硬件冒烟")
        self.assertIn("tests/hardware", plan[-1].arguments)


class TestRunnerInterpreterTests(unittest.TestCase):
    def test_the_runner_can_be_started_without_powershell(self):
        completed = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "run_tests.py"), "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=dict(os.environ, PYTHONIOENCODING="utf-8"),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        for layer in LAYERS:
            self.assertIn(layer, completed.stdout)


if __name__ == "__main__":
    unittest.main()
