r"""步骤 14 门禁：固定依赖与运行时契约。

对应 [技术债分阶段重构计划](../docs/技术债分阶段重构计划.md) 步骤 14，固定五点：

1. 解释器只有 3.11 系列，启动器不再接受 ``>=3.7`` 之类过宽版本。
2. 主系统依赖有精确锁定清单 ``requirements.lock.txt``，直接依赖与之保持一致。
3. 锁文件按**整份环境**核对（含传递依赖），解析失败不静默降级。
4. 测试、编译、锁定清单核对、启动检查、配置校验和发布清单使用同一份解释器命令。
5. 主系统与 ``DPD_auto_test`` 依赖保持隔离，锁文件不掺入 DPD 依赖。

验证方式：

```powershell
& $env:AUTO_TEST_PYTHON -m unittest tests.test_runtime_dependency_contract -v
```
"""

import hashlib
import importlib.util
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).parents[1]
DIRECT_REQUIREMENTS = ROOT / "requirements.txt"
LOCK_FILE = ROOT / "requirements.lock.txt"
DPD_REQUIREMENTS = ROOT / "requirements-dpd.txt"
CONTRACT_DOC = ROOT / "docs" / "依赖与运行时契约.md"
EXPORT_TOOL = ROOT / "tools" / "export_dependency_lock.py"

# DPD_auto_test 专属依赖：主系统直接依赖和锁文件里都不允许出现。
DPD_ONLY_PACKAGES = {
    "tensorflow",
    "keras",
    "jupyter",
    "notebook",
    "ipykernel",
    "scipy",
    "nptdms",
}

# 打包工具由安装环境自带，不属于应用依赖，不进入锁文件。
PACKAGING_TOOLS = {"pip", "setuptools", "wheel"}


def normalize(name: str) -> str:
    return name.strip().lower().replace("-", "_").replace(".", "_")


def parse_pins(path: Path) -> dict:
    """解析 ``包名==版本`` 清单；任何非注释行都必须是精确版本。"""
    pins = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "==" not in line:
            raise AssertionError(f"{path.name} 存在未固定版本的行: {line}")
        name, version = line.split("==", 1)
        pins[normalize(name)] = version.strip()
    return pins


def load_export_tool():
    """按文件路径加载导出工具，避免依赖 ``tools`` 是否可导入。"""
    spec = importlib.util.spec_from_file_location("export_dependency_lock_tool", EXPORT_TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PythonVersionContractTests(unittest.TestCase):
    """解释器版本契约：唯一常量、唯一判定函数。"""

    def test_requirement_is_the_311_series(self):
        from infrastructure.runtime import (
            REQUIRED_PYTHON_SERIES,
            python_version_requirement,
        )

        self.assertEqual(REQUIRED_PYTHON_SERIES, "3.11")
        self.assertEqual(python_version_requirement(), "3.11.x")

    def test_supported_versions_are_the_311_series_only(self):
        from types import SimpleNamespace

        from infrastructure.runtime import python_version_supported

        def info(major, minor, micro=0):
            return SimpleNamespace(major=major, minor=minor, micro=micro)

        ok, detail = python_version_supported(info(3, 11, 16))
        self.assertTrue(ok)
        self.assertEqual(detail, "3.11.16")
        for rejected in (info(3, 7), info(3, 10, 14), info(3, 12, 1), info(4, 0)):
            with self.subTest(version=f"{rejected.major}.{rejected.minor}"):
                ok, detail = python_version_supported(rejected)
                self.assertFalse(ok)
                self.assertIn("3.11.x", detail)

    def test_current_interpreter_satisfies_the_contract(self):
        from infrastructure.runtime import python_version_supported

        ok, detail = python_version_supported()
        self.assertTrue(ok, f"当前解释器不满足项目要求: {detail}")

    def test_launcher_dropped_the_wide_37_check(self):
        source = (ROOT / "launcher.py").read_text(encoding="utf-8-sig")
        self.assertNotIn("(3, 7)", source)
        self.assertNotIn("需要 Python 3.7", source)
        self.assertIn("python_version_supported", source)

    def test_launcher_environment_check_rejects_a_wrong_python_series(self):
        import launcher

        with patch.object(launcher, "python_version_supported", return_value=(False, "需要 Python 3.11.x")):
            self.assertFalse(launcher.check_environment(silent=True))

    def test_read_only_commands_reject_a_wrong_python_series(self):
        import io
        from contextlib import redirect_stderr

        import launcher

        error = io.StringIO()
        with patch.object(
            launcher, "python_version_supported", return_value=(False, "需要 Python 3.11.x，当前为 3.12.1")
        ), patch.object(
            launcher, "validate_default_config", side_effect=AssertionError("不应校验配置")
        ), redirect_stderr(error):
            self.assertEqual(launcher.main(["--validate-config"]), launcher.EXIT_ENVIRONMENT_ERROR)
        self.assertIn("3.11.x", error.getvalue())

    def test_powershell_entry_scripts_check_the_same_series(self):
        helper = (ROOT / "tools" / "runtime_environment.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('"3.11"', helper)
        self.assertIn("Python 3.11.x is required", helper)


class DependencyLockTests(unittest.TestCase):
    """主系统依赖锁定清单必须可复现且与直接依赖一致。"""

    def test_lock_file_exists_and_pins_every_entry(self):
        self.assertTrue(LOCK_FILE.is_file(), f"缺少依赖锁定清单: {LOCK_FILE}")
        pins = parse_pins(LOCK_FILE)
        self.assertGreater(len(pins), 10)
        for name, version in pins.items():
            self.assertRegex(version, r"^\d", f"{name} 的版本不是精确版本: {version}")

    def test_lock_file_has_no_duplicate_entries(self):
        lines = [
            line.strip()
            for line in LOCK_FILE.read_text(encoding="utf-8-sig").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        names = [normalize(line.split("==", 1)[0]) for line in lines]
        duplicates = {name for name in names if names.count(name) > 1}
        self.assertEqual(duplicates, set(), f"锁定清单重复条目: {sorted(duplicates)}")

    def test_direct_requirements_are_pinned_and_match_the_lock(self):
        direct = parse_pins(DIRECT_REQUIREMENTS)
        locked = parse_pins(LOCK_FILE)
        self.assertTrue(direct, "requirements.txt 不能为空")
        for name, version in direct.items():
            with self.subTest(package=name):
                self.assertIn(name, locked, f"{name} 未出现在 requirements.lock.txt")
                self.assertEqual(locked[name], version, f"{name} 直接依赖与锁定版本不一致")

    def test_lock_file_excludes_packaging_tools(self):
        locked = parse_pins(LOCK_FILE)
        leaked = locked.keys() & PACKAGING_TOOLS
        self.assertEqual(leaked, set(), f"锁文件不应锁定打包工具: {sorted(leaked)}")

    def test_main_dependencies_stay_free_of_dpd_packages(self):
        direct = set(parse_pins(DIRECT_REQUIREMENTS))
        locked = set(parse_pins(LOCK_FILE))
        for surface, name in (("requirements.txt", direct), ("requirements.lock.txt", locked)):
            leaked = {package for package in name if normalize(package) in DPD_ONLY_PACKAGES}
            self.assertEqual(leaked, set(), f"{surface} 掺入了 DPD 依赖: {sorted(leaked)}")

    def test_dpd_requirements_stay_isolated(self):
        text = DPD_REQUIREMENTS.read_text(encoding="utf-8-sig")
        self.assertIn("tensorflow", text.lower())
        self.assertIn("jupyter", text.lower())
        self.assertIn("requirements-dpd.lock.txt", text)
        lock_text = LOCK_FILE.read_text(encoding="utf-8-sig")
        for package in DPD_ONLY_PACKAGES:
            self.assertNotIn(package, lock_text.lower(), f"锁文件混入了 DPD 依赖: {package}")

    def test_launcher_required_packages_are_declared(self):
        import launcher

        declared = set(parse_pins(DIRECT_REQUIREMENTS))
        packages = launcher.check_packages(silent=True)
        required = {normalize(name) for name, info in packages.items() if info["required"]}
        missing = required - declared
        self.assertEqual(missing, set(), f"启动器必需依赖未在 requirements.txt 声明: {sorted(missing)}")

    def test_installed_environment_matches_the_whole_lock(self):
        import launcher

        drift = launcher.locked_dependency_drift()
        self.assertEqual(drift.blocking_messages(), [], "当前环境与锁定清单存在阻断性差异")
        self.assertEqual(drift.unexpected, (), "当前环境存在锁文件未声明的包，请确认是否应写入锁文件")


class StrictLockParsingTests(unittest.TestCase):
    """锁文件解析必须严格：格式错误不静默降级为半份清单。"""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "requirements.lock.txt"

    def _write(self, text):
        self.path.write_text(text, encoding="utf-8")
        return self.path

    def test_valid_lock_ignores_comments_and_blank_lines(self):
        from infrastructure.runtime.dependency_lock import read_lock_packages

        packages = read_lock_packages(self._write("# 注释\n\nnumpy==2.4.6\nPySide6==6.11.2\n"))
        self.assertEqual(set(packages), {"numpy", "pyside6"})
        self.assertEqual(packages["pyside6"].version, "6.11.2")
        self.assertEqual(packages["pyside6"].name, "PySide6")

    def test_unpinned_requirement_is_rejected(self):
        from infrastructure.runtime.dependency_lock import DependencyLockError, read_lock_packages

        with self.assertRaises(DependencyLockError) as context:
            read_lock_packages(self._write("numpy>=1.26\n"))
        self.assertIn("不是精确版本", str(context.exception))

    def test_version_range_is_rejected(self):
        from infrastructure.runtime.dependency_lock import DependencyLockError, read_lock_packages

        with self.assertRaises(DependencyLockError):
            read_lock_packages(self._write("numpy==2.*\n"))

    def test_duplicate_entry_with_another_spelling_is_rejected(self):
        from infrastructure.runtime.dependency_lock import DependencyLockError, read_lock_packages

        with self.assertRaises(DependencyLockError) as context:
            read_lock_packages(self._write("PySide6==6.11.2\npyside6==6.10.0\n"))
        self.assertIn("重复声明", str(context.exception))

    def test_missing_lock_file_is_rejected(self):
        from infrastructure.runtime.dependency_lock import DependencyLockError, read_lock_packages

        with self.assertRaises(DependencyLockError) as context:
            read_lock_packages(Path(self.directory.name) / "absent.lock.txt")
        self.assertIn("缺少依赖锁定清单", str(context.exception))

    def test_empty_lock_file_is_rejected(self):
        from infrastructure.runtime.dependency_lock import DependencyLockError, read_lock_packages

        with self.assertRaises(DependencyLockError):
            read_lock_packages(self._write("# 只有注释\n"))


class FullLockVerificationTests(unittest.TestCase):
    """核对范围必须是整份锁文件，而不是启动器列出的少数模块。"""

    def _locked(self, **pins):
        from infrastructure.runtime.dependency_lock import LockedPackage

        return {normalize(name): LockedPackage(name, version) for name, version in pins.items()}

    def _installed(self, **pins):
        from infrastructure.runtime.dependency_lock import InstalledPackage

        return {normalize(name): InstalledPackage(name, version) for name, version in pins.items()}

    def test_transitive_dependency_version_drift_blocks(self):
        from infrastructure.runtime.dependency_lock import lock_drift

        drift = lock_drift(
            self._locked(numpy="2.4.6", urllib3="2.8.0"),
            self._installed(numpy="2.4.6", urllib3="1.26.0"),
        )
        self.assertTrue(drift.blocking)
        self.assertEqual(drift.missing, ())
        self.assertEqual(len(drift.mismatched), 1)
        self.assertIn("urllib3", drift.mismatched[0])
        self.assertIn("2.8.0", drift.mismatched[0])

    def test_missing_transitive_dependency_blocks(self):
        from infrastructure.runtime.dependency_lock import lock_drift

        drift = lock_drift(
            self._locked(numpy="2.4.6", six="1.17.0"),
            self._installed(numpy="2.4.6"),
        )
        self.assertTrue(drift.blocking)
        self.assertEqual(drift.missing, ("six",))

    def test_extra_installed_package_is_only_a_warning(self):
        from infrastructure.runtime.dependency_lock import lock_drift

        drift = lock_drift(
            self._locked(numpy="2.4.6"),
            self._installed(numpy="2.4.6", tensorflow="2.13.0"),
        )
        self.assertFalse(drift.blocking)
        self.assertEqual(drift.unexpected, ("tensorflow 2.13.0",))

    def test_packaging_tools_are_not_reported_as_extra_packages(self):
        from infrastructure.runtime.dependency_lock import lock_drift

        drift = lock_drift(
            self._locked(numpy="2.4.6"),
            self._installed(numpy="2.4.6", pip="26.0", setuptools="80.0", wheel="0.45.0"),
        )
        self.assertFalse(drift.blocking)
        self.assertEqual(drift.unexpected, ())

    def test_launcher_check_fails_when_a_locked_package_is_missing(self):
        import io
        from contextlib import redirect_stdout

        import launcher
        from infrastructure.runtime.dependency_lock import LockDrift

        drift = LockDrift(missing=("urllib3",))
        with patch.object(launcher, "check_environment", return_value=True), patch.object(
            launcher, "check_packages", return_value={}
        ), patch.object(launcher, "locked_dependency_drift", return_value=drift):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(launcher.main(["--check"]), launcher.EXIT_DEPENDENCY_ERROR)

    def test_launcher_check_fails_when_the_lock_file_is_unusable(self):
        import io
        from contextlib import redirect_stdout

        import launcher
        from infrastructure.runtime.dependency_lock import DependencyLockError

        with patch.object(launcher, "check_environment", return_value=True), patch.object(
            launcher, "check_packages", return_value={}
        ), patch.object(
            launcher, "locked_dependency_drift", side_effect=DependencyLockError("坏锁文件")
        ):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(launcher.main(["--check"]), launcher.EXIT_DEPENDENCY_ERROR)

    def test_launcher_check_reports_success_on_the_verified_environment(self):
        import io
        from contextlib import redirect_stdout

        import launcher

        output = io.StringIO()
        with patch.object(launcher, "check_environment", return_value=True), redirect_stdout(output):
            self.assertEqual(launcher.main(["--check"]), launcher.EXIT_SUCCESS)
        self.assertIn("含传递依赖", output.getvalue())

    def test_launcher_check_warns_about_lock_external_packages(self):
        import io
        from contextlib import redirect_stdout

        import launcher
        from infrastructure.runtime.dependency_lock import InstalledPackage, installed_packages

        environment = dict(installed_packages())
        environment["tensorflow"] = InstalledPackage("tensorflow", "2.13.0")
        output = io.StringIO()
        with patch.object(launcher, "installed_packages", return_value=environment), patch.object(
            launcher, "check_environment", return_value=True
        ), redirect_stdout(output):
            self.assertEqual(launcher.main(["--check"]), launcher.EXIT_SUCCESS)
        self.assertIn("锁外多余包: tensorflow 2.13.0（锁文件未声明）", output.getvalue())


class LockExportToolTests(unittest.TestCase):
    """锁文件必须能由导出工具精确复现，不靠手工编辑。"""

    def test_repository_lock_is_reproducible_from_the_environment(self):
        tool = load_export_tool()
        self.assertEqual(
            tool.render_lock(),
            LOCK_FILE.read_text(encoding="utf-8"),
            "requirements.lock.txt 与导出工具结果不一致，请运行 tools/export_dependency_lock.py",
        )

    def test_export_tool_check_accepts_the_repository_lock(self):
        tool = load_export_tool()
        self.assertEqual(tool.main(["--check"]), tool.EXIT_OK)

    def test_export_tool_check_detects_a_tampered_lock(self):
        tool = load_export_tool()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tampered = LOCK_FILE.read_text(encoding="utf-8").replace("numpy==2.4.6", "numpy==2.3.0")
            (root / "requirements.lock.txt").write_text(tampered, encoding="utf-8")
            (root / "requirements.txt").write_text(DIRECT_REQUIREMENTS.read_text(encoding="utf-8"), encoding="utf-8")
            self.assertEqual(tool.main(["--check", "--root", str(root)]), tool.EXIT_DRIFT)

    def test_export_tool_check_does_not_fail_on_lock_external_packages(self):
        tool = load_export_tool()
        from infrastructure.runtime import dependency_lock
        from infrastructure.runtime.dependency_lock import InstalledPackage

        environment = dict(dependency_lock.installed_packages())
        environment["tensorflow"] = InstalledPackage("tensorflow", "2.13.0")
        with patch.object(dependency_lock, "installed_packages", return_value=environment):
            blocking, warnings = tool.check(ROOT)
            self.assertEqual(blocking, [])
            self.assertTrue(warnings)
            self.assertEqual(tool.main(["--check"]), tool.EXIT_OK)

    def test_export_tool_filters_packaging_tools(self):
        tool = load_export_tool()
        from infrastructure.runtime.dependency_lock import InstalledPackage

        text = tool.render_lock(
            {
                "numpy": InstalledPackage("numpy", "2.4.6"),
                "pip": InstalledPackage("pip", "26.0"),
                "setuptools": InstalledPackage("setuptools", "80.0"),
                "wheel": InstalledPackage("wheel", "0.45.0"),
            }
        )
        self.assertIn("numpy==2.4.6", text)
        for package in ("pip==", "setuptools==", "wheel=="):
            self.assertNotIn(package, text)


class UnifiedCommandTests(unittest.TestCase):
    """测试、编译、锁定核对、启动检查和发布清单共用一份解释器命令。"""

    def test_commands_never_start_with_bare_python(self):
        from infrastructure.runtime import verification_commands

        commands = verification_commands()
        self.assertEqual(
            set(commands),
            {
                "unit_tests",
                "compile",
                "dependency_lock",
                "launcher_check",
                "config_validation",
                "release_manifest",
            },
        )
        for key, command in commands.items():
            with self.subTest(command=key):
                self.assertFalse(
                    re.match(r"^(python|python\.exe|py)\s", command, re.IGNORECASE),
                    f"{key} 仍使用裸 python: {command}",
                )

    def test_compile_command_uses_the_in_memory_compile_check(self):
        from infrastructure.runtime import verification_commands

        compile_command = verification_commands()["compile"]
        self.assertIn("tools/compile_check.py", compile_command)
        self.assertNotIn("compileall", compile_command)

    def test_lock_command_uses_the_export_tool(self):
        from infrastructure.runtime import verification_commands

        lock_command = verification_commands()["dependency_lock"]
        self.assertIn("tools/export_dependency_lock.py", lock_command)
        self.assertIn("--check", lock_command)

    def test_release_manifest_reuses_the_same_commands(self):
        from infrastructure.runtime import verification_commands
        from release_manifest import build_manifest

        manifest = build_manifest(ROOT)
        self.assertEqual(manifest["verification"], verification_commands())
        self.assertEqual(manifest["python_requirement"], "3.11.x")

    def test_release_manifest_binds_the_dependency_lock(self):
        from release_manifest import build_manifest

        manifest = build_manifest(ROOT)
        dependencies = manifest["dependencies"]
        self.assertEqual(dependencies["direct"], "requirements.txt")
        self.assertEqual(dependencies["lock"], "requirements.lock.txt")
        self.assertEqual(
            dependencies["lock_sha256"],
            hashlib.sha256(LOCK_FILE.read_bytes()).hexdigest(),
        )
        self.assertEqual(dependencies["dpd_requirements"], "requirements-dpd.txt")
        self.assertTrue(dependencies["dpd_isolated"])

    def test_interpreter_command_quotes_paths_with_spaces(self):
        from infrastructure.runtime import interpreter_command

        with patch(
            "infrastructure.runtime.runtime_contract.interpreter_path",
            return_value=Path(r"C:\Program Files\Python311\python.exe"),
        ):
            command = interpreter_command("-m", "unittest")
        self.assertTrue(command.startswith('"C:\\Program Files\\Python311\\python.exe" '))

    def test_launcher_help_uses_the_shared_commands(self):
        import io
        from contextlib import redirect_stdout

        import launcher
        from infrastructure.runtime import interpreter_command

        output = io.StringIO()
        with redirect_stdout(output):
            launcher.show_help()
        text = output.getvalue()
        self.assertIn(interpreter_command("launcher.py", "--check"), text)
        self.assertIn(interpreter_command("launcher.py", "--validate-config"), text)
        self.assertIn(interpreter_command("tools/export_dependency_lock.py", "--check"), text)
        self.assertIn("不使用裸 python", text)
        self.assertIn("3.11.x", text)


class RuntimeContractDocumentationTests(unittest.TestCase):
    """文档必须让新环境能按锁定清单和同一解释器复现。"""

    def test_contract_document_exists_and_covers_the_five_contracts(self):
        self.assertTrue(CONTRACT_DOC.is_file(), f"缺少依赖与运行时契约文档: {CONTRACT_DOC}")
        document = CONTRACT_DOC.read_text(encoding="utf-8-sig")
        for marker in (
            "3.11",
            "requirements.lock.txt",
            "AUTO_TEST_PYTHON",
            "requirements-dpd.txt",
            "tools/compile_check.py",
            "tools/export_dependency_lock.py",
            "不在运行时自动安装依赖",
        ):
            self.assertIn(marker, document)

    def test_document_explains_the_new_environment_order(self):
        document = CONTRACT_DOC.read_text(encoding="utf-8-sig")
        # 先确定并写入 AUTO_TEST_PYTHON，再用同一个解释器安装锁文件。
        self.assertLess(
            document.index("AUTO_TEST_PYTHON"),
            document.index("pip install -r requirements.lock.txt"),
        )
        self.assertIn("--validate-config", document)

    def test_readme_points_at_the_lock_file(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8-sig")
        for marker in ("requirements.lock.txt", "Python：3.11", "依赖与运行时契约"):
            self.assertIn(marker, readme)
        self.assertNotIn("python launcher.py", readme)

    def test_startup_guide_uses_the_project_interpreter(self):
        guide = (ROOT / "README_启动说明.md").read_text(encoding="utf-8-sig")
        self.assertIn("AUTO_TEST_PYTHON", guide)
        self.assertNotIn("\npython launcher.py", guide)


if __name__ == "__main__":
    unittest.main()
