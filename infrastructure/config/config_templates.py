"""入库配置模板的选择与读取。

当前工作配置 ``config.json`` 不再入库（见 ``.gitignore``），首次启动缺失时由
:meth:`infrastructure.gui_data_service.JsonConfigurationAdapter.save` 的同一写入口
从一份入库模板生成。模板的角色只有“初值”：

- ``config.default.json``：默认模板，必须是完整、可通过 ``launcher --validate-config``
  的正式配置；
- ``config.mock.json``：离线演示/开发用的可选模板，不自动启用，必须用
  ``AUTO_TEST_CONFIG_TEMPLATE`` 显式选择，避免演示值悄悄变成现场运行配置。

模板是只读的：程序生成当前配置后只读写 ``config.json``，不会回写模板；也不是
“每层默认值合并”，缺失字段仍由正式校验在连接仪器前报告。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from infrastructure.config.json_io import load_json_object
from infrastructure.runtime.runtime_contract import load_env_value

__all__ = [
    "CONFIG_TEMPLATE_ENV",
    "DEFAULT_CONFIG_TEMPLATE",
    "MOCK_CONFIG_TEMPLATE",
    "load_config_template",
    "selected_config_template",
]

DEFAULT_CONFIG_TEMPLATE = "config.default.json"
MOCK_CONFIG_TEMPLATE = "config.mock.json"
#: 选择模板文件名的环境变量（相对路径按项目根目录解析）。
CONFIG_TEMPLATE_ENV = "AUTO_TEST_CONFIG_TEMPLATE"


def selected_config_template(root: Path) -> Path:
    """返回本次应使用的配置模板路径。

    未设置 :data:`CONFIG_TEMPLATE_ENV` 时使用默认模板；显式设置时可以指向项目内
    任意模板（例如 ``config.mock.json``）或绝对路径。选择结果不缓存：同一进程内
    读取环境变量与调用方传入的根目录保持一致，测试可以按需替换。
    """
    root = Path(root).expanduser()
    configured = load_env_value(CONFIG_TEMPLATE_ENV)
    name = configured or DEFAULT_CONFIG_TEMPLATE
    candidate = Path(name).expanduser()
    return candidate if candidate.is_absolute() else root / candidate


def load_config_template(path: Path) -> dict[str, Any]:
    """读取一份配置模板，要求根节点是 JSON 对象。

    读取复用 :func:`infrastructure.config.json_io.load_json_object`（与当前配置、
    启动器校验同一个读取边界）；模板内容是否可通过正式校验仍由调用方决定。
    """
    document = load_json_object(path)
    if not isinstance(document, dict):
        raise ValueError(f"配置模板根节点必须是对象: {Path(path)}")
    return document
