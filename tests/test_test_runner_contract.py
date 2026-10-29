"""门禁：测试入口把分层、门禁顺序和并行排除清单固化成可断言的数据。

``tools/run_tests.py`` 取代了原先写死在 ``run_tests.ps1`` 里的规则。规则改为可导入的纯函数，
这样“纯界面改动不该跑全量”“UI 层必须先过全部门禁”“硬件测试不得进入并行全量”就不再只是
README 里的一句话。
"""

import os
import subprocess
import sys
import tempfile
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
    STATIC_PRE_CHECK_MODULES,
    build_plan,
    prepend_runtime_path,
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
        self.assertEqual(plan[-1].arguments[-5:], ("-s", "tests", "-p", "test_*.py", "-v"))
        self.assertNotIn("--parallel", _flatten(plan))

    def test_a_ui_change_runs_all_gates_and_only_the_presentation_tests(self):
        plan = build_plan(layer="ui")
        self.assertEqual(_labels(plan), ["编译检查", "依赖核对", "仓库门禁", "表现层测试"])
        for module in GATE_MODULES:
            self.assertIn(module, plan[2].arguments)
        self.assertIn("tests/ui", plan[3].arguments)
        self.assertNotIn("tests/integration", _flatten(plan))

    def test_an_integration_change_adds_the_wiring_tests(self):
        plan = build_plan(layer="integration")
        self.assertEqual(
            _labels(plan),
            ["编译检查", "依赖核对", "仓库门禁", "表现层测试", "接线测试"],
        )
        self.assertIn("tests/integration", plan[4].arguments)

    def test_narrow_layers_run_the_static_gates_before_their_tests(self):
        for layer in ("unit", "simulation"):
            with self.subTest(layer=layer):
                plan = build_plan(layer=layer)
                self.assertEqual(_labels(plan)[2], "静态质量门禁")
                for module in STATIC_PRE_CHECK_MODULES:
                    self.assertIn(module, plan[2].arguments)
                self.assertIn(f"tests/{layer}", plan[3].arguments)
                self.assertIn("-v", plan[3].arguments)

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

    def test_parallel_worker_count_is_capped(self):
        from tools.run_tests import MAX_PARALLEL_WORKERS, parallel_arguments

        arguments = parallel_arguments()
        workers = int(arguments[arguments.index("-n") + 1])
        self.assertGreaterEqual(workers, 1)
        self.assertLessEqual(workers, MAX_PARALLEL_WORKERS)

    def test_the_hardware_layer_never_runs_through_parallel_discovery(self):
        plan = build_plan(layer="hardware")
        self.assertEqual(_labels(plan)[-1], "硬件冒烟")
        self.assertIn("tests/hardware", plan[-1].arguments)

    def test_explicit_unittest_arguments_replace_the_default_discovery(self):
        plan = build_plan(unittest_arguments=["discover", "-s", "tests", "-v"])
        self.assertEqual(_labels(plan), ["编译检查", "依赖核对", "自定义测试参数"])
        self.assertEqual(plan[-1].arguments, ("discover", "-s", "tests", "-v"))


class TestRunnerInterpreterTests(unittest.TestCase):
    """解释器与运行时目录的处理不再依赖 PowerShell 助手。"""

    def test_runner_reuses_the_shared_interpreter_contract(self):
        source = (ROOT / "tools" / "run_tests.py").read_text(encoding="utf-8")
        self.assertIn("python_version_supported", source)
        self.assertIn("configured_python", source)
        self.assertNotIn('"3.11"', source)

    def test_conda_runtime_directories_are_prepended_without_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory)
            library_bin = prefix / "Library" / "bin"
            library_bin.mkdir(parents=True)
            environment = {"PATH": os.pathsep.join([str(library_bin), "C:\\Windows"])}
            added = prepend_runtime_path(environment, prefix)
            entries = environment["PATH"].split(os.pathsep)
            self.assertEqual(added, 1)
            self.assertEqual(entries[0], str(prefix))
            self.assertEqual(entries[-1], "C:\\Windows")
            self.assertEqual(environment["PATH"].count(str(library_bin)), 1)

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
