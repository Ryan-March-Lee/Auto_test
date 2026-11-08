"""正式配置文件加载与文件级校验入口。

``validate_config_file`` 是 ``launcher --validate-config`` 与离线检查唯一使用的文件级校验入口，
同时检查两件事：

- **正式运行配置 schema**：根 ``schema_version`` 与 ``test_plan`` / ``run_mapping``
  两个正式段，由 :func:`infrastructure.config.json_config_repository.load_combined_configuration`
  解析并执行领域运行校验；
- **硬件装配段**：``instruments`` / ``power_supply_assignment`` 等硬件装配所读取的字段，
  由 :func:`domain.configuration.rules.validate_config` 校验。

只有两者都通过，``--validate-config`` 才认为这份生产配置可以进入运行创建流程。旧扁平配置的自动转换已
删除，因此只有扁平字段、缺少正式段的文件会在这里明确失败，而不是等到创建运行甚至连接仪器
时才发现问题。

“运行就绪”以外的演示数据不阻断启动：模拟扫描写入的设备绑定只产生 warning，配置照常
加载、界面照常打开；创建真实运行前由
:func:`app.gui_runtime._reject_simulated_bindings` 单独拒绝。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Union

from domain.configuration.rules import validate_config
from domain.configuration.types import ConfigValidationResult
from infrastructure.config.json_config_repository import load_combined_configuration
from infrastructure.config.json_io import load_config_file


PathLike = Union[str, Path]


def load_config(config_path: Optional[PathLike] = None) -> Dict[str, Any]:
    """加载 JSON 配置，不创建仪器连接，也不修改文件。"""
    return load_config_file(config_path)


def validate_official_config(document: Mapping[str, Any]) -> ConfigValidationResult:
    """校验一份**正式组合配置**文档，返回合并后的校验结果。

    正式段错误在前、硬件装配段错误在后；完全相同的（级别, 路径, 说明）只保留一条，
    避免两套规则对同一字段重复报告。
    """
    data = dict(document)
    formal = load_combined_configuration(data).validation
    hardware = validate_config(data)
    return _merge_results(formal, hardware)


def validate_saveable_configuration(document: Mapping[str, Any]) -> ConfigValidationResult:
    """校验一份**可以保存**的组合配置，但不要求它已经可以运行。

    与 :func:`validate_official_config` 的区别只有一处：跳过 v1.1 的“运行就绪”电源
    规则（空计划、未确认漏极等）。迁移器把旧配置升级成 1.1 时，上电顺序必须由用户
    在电源页确认，因此迁移结果天然是“结构完整、尚不可运行”的一份文档（§6.6、
    §6.14 规则 4）。保存门槛用它，创建运行仍走 :func:`validate_official_config`。
    """
    data = dict(document)
    formal = load_combined_configuration(data, require_runnable=False).validation
    hardware = validate_config(data)
    return _merge_results(formal, hardware)


def validate_config_file(config_path: Optional[PathLike] = None) -> ConfigValidationResult:
    """加载并校验指定配置文件的正式 schema 与硬件装配段。"""
    return validate_official_config(load_config(config_path))


def _merge_results(
    *results: ConfigValidationResult,
) -> ConfigValidationResult:
    errors = []
    warnings = []
    seen: set[tuple[str, str, str]] = set()
    for result in results:
        for target, issues in ((errors, result.errors), (warnings, result.warnings)):
            for issue in issues:
                key = (issue.level, issue.path, issue.message)
                if key in seen:
                    continue
                seen.add(key)
                target.append(issue)
    return ConfigValidationResult(errors=errors, warnings=warnings)


__all__ = [
    "ConfigValidationResult",
    "load_config",
    "validate_config",
    "validate_config_file",
    "validate_official_config",
    "validate_saveable_configuration",
]
