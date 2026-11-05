"""配置页的 Qt 表单适配器（GUI 重构步骤 7）。

本模块是配置页控件的**唯一读写边界**：页面只登记控件与连接信号，控件的文本/数值/
业务值读取、整份配置文档的组装、回填投影与错误提示都由这里完成。它依赖的是控件的
最小协议（``text``、``value``、``isChecked``、``currentText``；漏极下拉框另外要求
``currentIndex`` + ``itemData``），不直接依赖 PySide6，因此可以用控件替身测试。

职责边界：

- 读数与投影规则仍是 :mod:`presentation.qt.config_form_state` 与
  :mod:`presentation.qt.config_projection` 的实现，本模块只按页面的调用顺序把它们
  组合起来；不改写字段语义、不挑默认设备、不生成正式 ``test_plan`` / ``run_mapping``。
- 器件电源的选择不在本模块：上电计划里的 ``(supply_id, channel)`` 路由是唯一来源，
  下拉框只用来选漏极，业务值仍从 ``itemData`` 读取。
- 设备绑定与正式配置业务规则分别在 :mod:`application.device_binding` 与
  :mod:`application.configuration_authoring`；本模块不包含这些规则。
- 错误显示只把已得到的用户文案合并后交给页面的错误通道，不重新解释业务判据。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from application.configuration_view_model import ConfigurationTestParameters

from .config_form_state import ConfigFormState
from .config_projection import (
    project_authoring_config,
    project_power_config,
)

__all__ = ["ConfigurationFormAdapter", "ConfigurationFormReading"]


def _test_parameter_snapshot(
    widgets: Any, fragment: Mapping[str, Any]
) -> ConfigurationTestParameters:
    """把同一次读出的测试参数片段转成类型化快照。

    DUT 输入上限与单元数量不在片段里，这里补上；非法频率文本本来就不会进入片段，
    快照因此保持空频率，不把基线里的旧值当成这次读到的新值。
    """
    document = dict(fragment)
    document["dut_config"] = {
        "max_input_power": widgets.max_input_power.value(),
        "power_supply_count": int(widgets.pa_unit_count_combo.currentText()),
    }
    parameters, _issues = ConfigurationTestParameters.from_document(document)
    return parameters


@dataclass(frozen=True)
class ConfigurationFormReading:
    """一次控件读数得到的完整配置文档与测试参数快照。

    ``document`` 是页面组装出的完整配置文档；``test_parameters`` 由**同一份**片段
    转换而来。页面把它们一起交给视图模型，避免为了测试参数快照再次读取控件，
    也就不会出现“文档与快照来自两次读数”的分叉。
    """

    document: dict[str, Any]
    test_parameters: ConfigurationTestParameters

    def __post_init__(self) -> None:
        if not isinstance(self.document, dict):
            raise TypeError(
                f"配置文档必须是对象，收到 {type(self.document).__name__}"
            )
        if not isinstance(self.test_parameters, ConfigurationTestParameters):
            raise TypeError(
                "test_parameters 必须是 ConfigurationTestParameters，"
                f"收到 {type(self.test_parameters).__name__}"
            )


class ConfigurationFormAdapter:
    """配置页控件读写的集中适配器。

    ``widgets`` 是页面（或测试里同形状的替身），适配器只按属性名访问控件与页面状态；
    ``on_error`` 是页面提供的错误显示通道（例如 ``validation_failed.emit``），
    只在确有用户可见错误时调用一次。

    适配器本身不保存控件读数，页面按需创建它（见
    :meth:`presentation.qt.configuration_page.ConfigurationPage.form_adapter`）：这样页面与适配器之间
    不形成引用环，Qt 控件不会因为后台线程触发的循环垃圾回收而在非主线程析构。
    """

    def __init__(
        self,
        widgets: Any,
        *,
        on_error: Callable[[str], Any] | None = None,
    ) -> None:
        self.widgets = widgets
        self._on_error = on_error

    # ---- 控件读取 -----------------------------------------------------------

    def read_form(self, base: Mapping[str, Any] | None = None) -> ConfigurationFormReading:
        """读一次控件，同时得到完整配置文档与测试参数快照。

        ``base`` 是组装基线（缺省为页面自己的配置副本）。测试参数片段只读一次：
        完整文档复用它，类型化快照也由它转换，页面因此不会拿到两份来自不同读数的
        测试参数。
        """
        page_config = getattr(self.widgets, "config", None)
        base_config = page_config if base is None else base
        form = ConfigFormState(base_config, self.widgets)
        fragment = form.read_test_parameters()
        return ConfigurationFormReading(
            document=form.build_config(test_parameters=fragment),
            test_parameters=_test_parameter_snapshot(self.widgets, fragment),
        )

    def read_configuration(self, base: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """按当前控件读数组装完整配置文档；``base`` 是组装基线。"""
        return self.read_form(base).document

    def read_test_parameters(self) -> ConfigurationTestParameters:
        """读出“测试参数”一组的类型化快照。

        读数边界与页面校验一致：频率文本非法时片段里没有 ``test_frequencies``，
        快照因此是空频率，不把基线里的旧值当成这次读到的新值。
        """
        page_config = getattr(self.widgets, "config", None)
        fragment = ConfigFormState(page_config, self.widgets).read_test_parameters()
        return _test_parameter_snapshot(self.widgets, fragment)

    def read_power_channel_snapshot(self) -> dict[str, dict]:
        """读取当前电源标签页的通道参数快照（重建标签页时保留用户刚填的值）。"""
        page_config = getattr(self.widgets, "config", None)
        fragment = ConfigFormState(page_config, self.widgets).read_power_supply_config()
        return fragment.get("instruments", {}).get("power_supplies", {}) or {}

    def read_power_fragment(self, base: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """读出独立电源页负责的配置片段（通道参数、计划与漏极）。"""
        page_config = getattr(self.widgets, "config", None)
        return ConfigFormState(
            page_config if base is None else base, self.widgets
        ).read_power_fragment()

    def build_power_configuration(
        self, base: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """把电源片段合并进基线，返回完整编辑文档（不读取测试参数控件）。"""
        page_config = getattr(self.widgets, "config", None)
        return ConfigFormState(
            page_config if base is None else base, self.widgets
        ).build_power_config()

    def power_validation_errors(
        self, base: Mapping[str, Any] | None = None
    ) -> list[str]:
        """独立电源页要显示的用户错误：只按电源读数做正式校验。"""
        page_config = getattr(self.widgets, "config", None)
        return ConfigFormState(
            page_config if base is None else base, self.widgets
        ).power_validation_errors()

    def validation_errors(self) -> list[str]:
        """按当前控件读数给出页面要显示的用户错误（顺序与文案保持原样）。"""
        page_config = getattr(self.widgets, "config", None)
        return ConfigFormState(page_config, self.widgets).validation_errors()

    # ---- 控件赋值 -----------------------------------------------------------

    def apply_configuration(
        self,
        config: Any,
        *,
        driver_plan_active: bool | None = None,
    ) -> dict[str, Any]:
        """把一份配置编写文档确定性地投影到控件，返回文档副本。

        测试参数、通道参数与供电方式走 :func:`project_authoring_config`；组上电计划与
        漏极属于独立电源表单，若控件提供 ``apply_power_plan_config``，就在同一入口里
        交给它，页面不需要再直接操作电源控件。
        """
        projected = project_authoring_config(self.widgets, config)
        apply_plans = getattr(self.widgets, "apply_power_plan_config", None)
        if callable(apply_plans):
            apply_plans(config, driver_plan_active=driver_plan_active)
        return projected

    def apply_power_configuration(
        self,
        config: Any,
        *,
        driver_plan_active: bool | None = None,
    ) -> dict[str, Any]:
        """把文档里的电源字段投影到独立电源页，返回文档副本。

        测试参数不由本页回填；与整份回填共用 ``config_projection`` 的通道/供电方式
        重置规则，计划与漏极仍交给表单组件的同一入口。
        """
        projected = project_power_config(self.widgets, config)
        apply_plans = getattr(self.widgets, "apply_power_plan_config", None)
        if callable(apply_plans):
            apply_plans(config, driver_plan_active=driver_plan_active)
        return projected

    # ---- 错误显示 -----------------------------------------------------------

    def report_errors(self, errors: Iterable[str]) -> bool:
        """把一组用户文案合并成一条提示交给页面错误通道；没有错误时返回成功。"""
        messages = [str(message) for message in errors if str(message)]
        if not messages:
            return True
        if self._on_error is not None:
            self._on_error("；".join(messages))
        return False
