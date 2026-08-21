"""PA 自动测试系统启动器。"""

import os
import sys
import argparse
import importlib
import importlib.util
import traceback
from pathlib import Path
from typing import Dict

from config_validation import ConfigValidationResult, validate_config_file
from app_logging import setup_logging


PROJECT_ROOT = Path(__file__).resolve().parent
EXIT_SUCCESS = 0
EXIT_CONFIG_ERROR = 2
EXIT_CONFIG_FILE_ERROR = 3
EXIT_ENVIRONMENT_ERROR = 4
EXIT_DEPENDENCY_ERROR = 5
EXIT_GUI_ERROR = 6
EXIT_INTERRUPTED = 130


def print_header() -> None:
    """打印标题。"""
    print("=" * 60)
    print("   PA 自动测试系统 - 启动器")
    print("   Power Amplifier Auto Test System")
    print("=" * 60)


def check_environment(silent: bool = False) -> bool:
    """检查运行环境。"""
    if not silent:
        print("正在检查运行环境...")
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if not silent:
        print(f"   Python 版本: {python_version}")
    if sys.version_info < (3, 7):
        if not silent:
            print("   需要 Python 3.7 或更高版本")
        return False
    if not silent:
        print(f"   Conda 环境: {get_conda_environment_name()}")
    return True


def get_conda_environment_name() -> str:
    """获取当前 Conda 环境名，兼容直接调用环境解释器的场景。"""
    configured_name = os.environ.get("CONDA_DEFAULT_ENV")
    if configured_name:
        return configured_name
    prefix_name = Path(sys.prefix).name
    return prefix_name or "base"


def check_packages(silent: bool = False) -> Dict[str, Dict[str, object]]:
    """检查 GUI 启动所需依赖包。"""
    if not silent:
        print("正在检查依赖包...")
    packages: Dict[str, Dict[str, object]] = {
        "PySide6": {"required": True, "installed": False, "version": ""},
        "matplotlib": {"required": True, "installed": False, "version": ""},
        "numpy": {"required": True, "installed": False, "version": ""},
        "pandas": {"required": True, "installed": False, "version": ""},
        "pyvisa": {"required": True, "installed": False, "version": "", "error": ""},
        "seaborn": {"required": True, "installed": False, "version": "", "error": ""},
        "markdown": {"required": True, "installed": False, "version": "", "error": ""},
        "requests": {"required": True, "installed": False, "version": "", "error": ""},
    }
    for package_name, package in packages.items():
        try:
            if importlib.util.find_spec(package_name) is None:
                package["error"] = "未找到模块"
                continue
            module = importlib.import_module(package_name)
        except Exception as error:
            package["error"] = f"导入失败: {error}"
            continue
        package["installed"] = True
        package["version"] = getattr(module, "__version__", "未知版本")
    if not silent:
        for name, info in packages.items():
            status = "已安装" if info["installed"] else "未安装"
            required = "必需" if info["required"] else "可选"
            version = f" ({info['version']})" if info["version"] else ""
            reason = f" - {info['error']}" if info.get("error") else ""
            print(f"   {name}: {status}{version} [{required}]{reason}")
    return packages


def has_missing_required_packages(packages: Dict[str, Dict[str, object]]) -> bool:
    """判断是否缺少必需依赖。"""
    return any(bool(info["required"]) and not bool(info["installed"]) for info in packages.values())


def print_config_validation(result: ConfigValidationResult) -> None:
    """输出配置校验结果。"""
    if result.valid:
        print("配置校验通过。")
    else:
        print("配置校验失败:", file=sys.stderr)
        for issue in result.errors:
            print(f"   错误 [{issue.path}]: {issue.message}", file=sys.stderr)
    for issue in result.warnings:
        print(f"   警告 [{issue.path}]: {issue.message}")


def validate_default_config() -> int:
    """校验默认生产配置，且不连接真实仪器。"""
    try:
        result = validate_config_file()
    except FileNotFoundError:
        print("配置文件不存在: config.json", file=sys.stderr)
        return EXIT_CONFIG_FILE_ERROR
    except UnicodeDecodeError:
        print("配置文件不是有效的 UTF-8 文本: config.json", file=sys.stderr)
        return EXIT_CONFIG_FILE_ERROR
    except ValueError as error:
        print(f"配置文件不是有效的 JSON: {error}", file=sys.stderr)
        return EXIT_CONFIG_FILE_ERROR
    print_config_validation(result)
    return EXIT_SUCCESS if result.valid else EXIT_CONFIG_ERROR


def get_gui_version(packages: Dict[str, Dict[str, object]]) -> str:
    """保留旧版启动器接口，当前始终使用增强版 GUI。"""
    return "enhanced" if packages.get("PySide6", {}).get("installed") else "none"


def launch_gui_version(version=None, packages=None, silent: bool = False) -> bool:
    """启动当前唯一支持的 GUI 版本。"""
    # 兼容旧调用：launch_gui_version(version, packages, silent=False)。
    if isinstance(version, bool) and packages is None:
        silent = version
    elif version == "none":
        return False
    gui_file = PROJECT_ROOT / "enhanced_main_gui.py"
    if not gui_file.exists():
        if not silent:
            print("GUI 启动失败: enhanced_main_gui.py 文件不存在")
        return False
    if not silent:
        print("正在启动增强版 GUI...")
    try:
        import enhanced_main_gui

        enhanced_main_gui.main()
        return True
    except Exception as error:
        if not silent:
            print(f"GUI 启动失败: {error}")
            traceback.print_exc()
        return False


def show_installation_guide(packages: Dict[str, Dict[str, object]]) -> None:
    """显示缺失依赖的安装建议。"""
    missing_required = [name for name, info in packages.items() if info["required"] and not info["installed"]]
    if not missing_required:
        return
    print("缺少必需依赖: " + ", ".join(missing_required))
    print("安装命令: pip install " + " ".join(missing_required))
    print("推荐环境: conda activate Auto_test")


def show_help() -> None:
    """显示帮助信息。"""
    print(
        """
使用方法:
    python launcher.py                       启动 GUI
    python launcher.py --check               检查 Python 和依赖包
    python launcher.py --validate-config     只读校验 config.json，不连接仪器
    python launcher.py --help                显示本帮助

退出码:
    0  成功
    2  配置校验失败
    3  配置文件不存在或 JSON 无效
    4  Python 运行环境不满足要求
    5  缺少 GUI 必需依赖
    6  GUI 启动失败
""".strip()
    )


def build_argument_parser() -> argparse.ArgumentParser:
    """构造启动器参数解析器。"""
    parser = argparse.ArgumentParser(description="PA 自动测试系统启动器")
    command_group = parser.add_mutually_exclusive_group()
    command_group.add_argument("--check", "-c", action="store_true", help="检查 Python 和 GUI 依赖")
    command_group.add_argument("--validate-config", action="store_true", help="只读校验配置，不连接仪器")
    parser.add_argument("--silent", action="store_true", help="隐藏常规启动输出")
    parser.add_argument("--gui", action="store_true", help="兼容旧版静默 GUI 参数")
    return parser


def main(argv: object = None) -> int:
    """运行启动器并返回可供脚本使用的退出码。"""
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = build_argument_parser()
    try:
        parsed = parser.parse_args(arguments)
    except SystemExit as error:
        return int(error.code)
    silent_mode = parsed.silent or parsed.gui
    try:
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        os.chdir(PROJECT_ROOT)
        if parsed.validate_config:
            # 只读校验命令：保持无文件副作用，不初始化日志。
            return validate_default_config()
        if not silent_mode:
            print_header()
        if not check_environment(silent_mode):
            return EXIT_ENVIRONMENT_ERROR
        packages = check_packages(silent_mode)
        if parsed.check:
            # 依赖检查命令：保持无文件副作用，不初始化日志。
            show_installation_guide(packages)
            return EXIT_DEPENDENCY_ERROR if has_missing_required_packages(packages) else EXIT_SUCCESS
        if has_missing_required_packages(packages):
            if not silent_mode:
                show_installation_guide(packages)
            return EXIT_DEPENDENCY_ERROR

        config_exit_code = validate_default_config()
        if config_exit_code != EXIT_SUCCESS:
            return config_exit_code
        # 阶段 0.3：进入 GUI 前初始化最小日志；失败不阻断启动。
        try:
            setup_logging()
        except Exception as logging_error:
            if not silent_mode:
                print(f"日志初始化失败（忽略）: {logging_error}")
        return EXIT_SUCCESS if launch_gui_version(silent=silent_mode) else EXIT_GUI_ERROR
    except KeyboardInterrupt:
        if not silent_mode:
            print("\n用户中断程序")
        return EXIT_INTERRUPTED
    except Exception as error:
        if not silent_mode:
            print(f"启动器发生未处理异常: {error}", file=sys.stderr)
            traceback.print_exc()
        return EXIT_GUI_ERROR


if __name__ == "__main__":
    sys.exit(main())
