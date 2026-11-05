"""配置编辑页的状态视图模型（GUI 重构步骤 6）。

归属（见 `docs/配置领域词汇与模块归属.md`）：application 页面状态边界，纯 Python。
本模块让配置页从“控件就是状态源”改为“视图模型持有状态、页面按状态渲染”：

- 状态包含配置草稿 :class:`ConfigurationDraft`、设备绑定快照、最近一次结构化问题、
  运行状态文本和 dirty 标记；
- 每个动作是显式方法，返回新的不可变状态或结构化结果：``load``、``update_test_parameters``、
  ``apply_device_selection``、``validate``、``save``、``create_run``、``set_run_status``、
  ``mark_clean``；
- 不导入 PySide6，不读控件，不读写文件，不创建仪器，不弹窗。文件保存与运行创建仍由
  ``ApplicationCoordinator`` / 应用端口执行，因此 :meth:`ConfigurationViewModel.save` /
  :meth:`ConfigurationViewModel.create_run` 只返回一个明确的页面 Effect
  (:class:`ConfigurationAction`)，由页面/协调器决定何时真正落盘。

迁移边界（步骤 6 先接入“测试参数 + 设备绑定”；步骤 7 收口整份表单）：控件读数经
:class:`presentation.qt.config_form_adapter.ConfigurationFormAdapter` 集中读出后，
:meth:`ConfigurationViewModel.update_form` 同步整份配置编辑文档，草稿因此覆盖电源通道
参数与上电计划。页面不再自己组装文档，``build_config`` 只是 adapter 读数 + 本方法的
短委托。因此：

- 视图模型对整份配置页业务状态（草稿、设备绑定、运行状态、dirty）是唯一来源；
- ``save`` / ``create_run`` 返回的 Effect 文档现在是完整的配置编辑文档（含电源通道、
  上电计划与设备绑定）；实际保存/创建运行仍由 ``ApplicationCoordinator`` 调用页面的
  ``build_config`` 执行，本步骤不改保存链路，也不在视图模型里写文件。

校验问题的边界与步骤 3–5 一致：用户可修正的配置问题通过
:class:`application.configuration_editing.ValidationResult` 返回；调用方契约错误（文档不是
对象、测试参数不是 DTO、方法参数类型不对）抛 ``TypeError``；编写不出正式配置仍由
:class:`application.configuration_authoring.ConfigurationAuthoringError` 报告，不在本模块
改写成普通校验问题。
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any

from application.configuration_authoring import (
    ConfigurationAuthoringService,
    ConfigurationEditingDocument,
)
from application.configuration_draft import ConfigurationDraft, DeviceBindingsDraft
from application.configuration_editing import ValidationIssue, ValidationResult
from application.device_binding import (
    ApplyDeviceSelectionRequest,
    DeviceBindingResult,
    DeviceBindingService,
    legacy_binding_count as _count_legacy_bindings,
)
from application.dto.amplifier_inputs import PowerSweep
from domain.measurement.values import Decibels, Frequency, PowerDbm

__all__ = [
    "CREATE_RUN",
    "ConfigurationAction",
    "ConfigurationBindingOutcome",
    "ConfigurationPageState",
    "ConfigurationTestParameters",
    "ConfigurationViewState",
    "ConfigurationViewModel",
    "DEFAULT_RUN_STATUS",
    "SAVE_CONFIGURATION",
]

# 运行状态文本的初始值；与配置页原来挂在 ``run_status_label`` 上的文案一致。
DEFAULT_RUN_STATUS = "当前运行: 未创建"
# 页面 Effect 的种类：只描述“接下来该做什么”，不在这里执行。
SAVE_CONFIGURATION = "save_configuration"
CREATE_RUN = "create_run"
_ACTION_KINDS = frozenset({SAVE_CONFIGURATION, CREATE_RUN})

# 测试参数在配置编辑文档里的键：更新测试参数时只替换这些段，其余段（电源通道、分配、
# 设备绑定里不属于测试参数的部分）保持加载时的内容。
_TEST_PARAMETER_PATHS = (
    "test_frequencies",
    "signal_source",
    "compression_point",
    "attenuator",
    "driver_mode",
    "dut_config",
)


def _freeze(value: Any) -> Any:
    """把文档/绑定结构递归收敛为只读容器。

    只读必须覆盖嵌套容器：只包住顶层字典时，调用方仍可通过
    ``bindings["power_supplies"][...]`` 改写视图模型持有的记录，dirty 也会因此失真。
    映射转为 ``MappingProxyType``，列表/元组转为 ``tuple``。
    """
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    """把 :func:`_freeze` 的只读结构还原成可写的 JSON 形状副本。

    映射还原为 ``dict``，冻结时转成元组的序列还原为 ``list``；调用方拿到的副本可以
    自由修改，且不会影响视图模型内部的快照。值对象（``Frequency`` 等）按原样返回。
    """
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return copy.deepcopy(value)


def _bindings_snapshot(value: Any) -> Mapping[str, Any]:
    """把一份绑定结构收敛为只读快照；非对象输入属于调用方契约错误。"""
    if isinstance(value, DeviceBindingsDraft):
        value = value.to_mapping()
    if not isinstance(value, Mapping):
        raise TypeError(
            f"设备绑定必须是对象或 DeviceBindingsDraft，收到 {type(value).__name__}"
        )
    # 快照：调用方之后改写原字典（包括嵌套记录）不会改变视图模型持有的绑定。
    return _freeze(dict(value))


def _configured_instrument_addresses(document: Mapping[str, Any]) -> tuple[str, ...]:
    """读取配置编辑文档里已保存的仪器地址（扫描框在没有绑定时回退到它们）。

    顺序与配置文档一致：信号源、频谱仪，随后是电源集合的声明顺序。只收非空字符串；
    非对象段与非文本地址不参与回退，由配置校验负责报告。
    """
    instruments = document.get("instruments")
    if not isinstance(instruments, Mapping):
        return ()
    entries: list[Any] = [
        instruments.get("signal_generator"),
        instruments.get("spectrum_analyzer"),
    ]
    supplies = instruments.get("power_supplies")
    if isinstance(supplies, Mapping):
        entries.extend(supplies.values())
    addresses: list[str] = []
    for item in entries:
        if not isinstance(item, Mapping):
            continue
        address = item.get("address")
        if isinstance(address, str) and address.strip():
            addresses.append(address)
    return tuple(addresses)


def _without_issues(
    validation: ValidationResult, prefixes: tuple[str, ...]
) -> ValidationResult:
    """去掉已经被新读数替换掉的字段上的旧问题，避免展示过期提示。"""
    return ValidationResult(
        issues=tuple(
            issue
            for issue in validation.issues
            if not issue.path.startswith(prefixes)
        )
    )


def _merge_issues(
    validation: ValidationResult, extra: tuple[ValidationIssue, ...]
) -> ValidationResult:
    """按出现顺序合并问题并去重（同一句话只展示一次）。"""
    issues: list[ValidationIssue] = []
    seen: set[tuple[str, str, str, str]] = set()
    for issue in (*validation.issues, *extra):
        key = (issue.path, issue.code, issue.message, issue.severity)
        if key in seen:
            continue
        seen.add(key)
        issues.append(issue)
    return ValidationResult(issues=tuple(issues))


@dataclass(frozen=True)
class ConfigurationTestParameters:
    """配置页“测试参数”一组的显式快照（控件读数的类型化结果）。

    它是 :class:`ConfigurationDraft` 的测试参数子集：频率、扫描、压缩点、衰减器、DUT
    输入上限、驱放模式与 PA 单元数量。字段语义与判据都由草稿定义——构造时借用
    :class:`ConfigurationDraft` 的校验，避免在视图模型里复制第二套类型/范围规则。
    """

    frequencies: tuple[Frequency, ...] = ()
    sweep: PowerSweep | None = None
    compression_target: Decibels | None = None
    attenuator: Decibels | None = None
    max_dut_input_power: PowerDbm | None = None
    driver_enabled: bool | None = None
    driver_power_source: str | None = None
    dut_supply_count: int | None = None

    def __post_init__(self) -> None:
        # 逐字段的类型与数值判据只有一处实现（ConfigurationDraft）。这里构造一份只含测试
        # 参数的草稿：字段非法时它按同一规则抛 TypeError / ValueError，视图模型不再判断。
        ConfigurationDraft(
            frequencies=self.frequencies,
            sweep=self.sweep,
            compression_target=self.compression_target,
            attenuator=self.attenuator,
            max_dut_input_power=self.max_dut_input_power,
            driver_enabled=self.driver_enabled,
            driver_power_source=self.driver_power_source,
            dut_supply_count=self.dut_supply_count,
        )

    @classmethod
    def from_draft(cls, draft: ConfigurationDraft) -> "ConfigurationTestParameters":
        """从一份草稿取出测试参数；草稿类型不对属于调用方契约错误。"""
        if not isinstance(draft, ConfigurationDraft):
            raise TypeError(
                f"draft 必须是 ConfigurationDraft，收到 {type(draft).__name__}"
            )
        return cls(
            frequencies=draft.frequencies,
            sweep=draft.sweep,
            compression_target=draft.compression_target,
            attenuator=draft.attenuator,
            max_dut_input_power=draft.max_dut_input_power,
            driver_enabled=draft.driver_enabled,
            driver_power_source=draft.driver_power_source,
            dut_supply_count=draft.dut_supply_count,
        )

    @classmethod
    def from_document(
        cls, document: Mapping[str, Any] | None
    ) -> tuple["ConfigurationTestParameters", ValidationResult]:
        """把配置编辑文档的测试参数段转成快照，并返回转换阶段的结构化问题。

        文档里缺失的字段保持 ``None`` / 空元组；类型或语法非法的字段产生错误级问题，
        对应值保持缺失——与 :meth:`ConfigurationDraft.from_document` 的读取边界一致。
        """
        result = ConfigurationDraft.from_document(document)
        return cls.from_draft(result.draft), result.validation

    def fields(self) -> dict[str, Any]:
        """返回可直接交给 :func:`dataclasses.replace` 的字段映射。"""
        return {
            "frequencies": self.frequencies,
            "sweep": self.sweep,
            "compression_target": self.compression_target,
            "attenuator": self.attenuator,
            "max_dut_input_power": self.max_dut_input_power,
            "driver_enabled": self.driver_enabled,
            "driver_power_source": self.driver_power_source,
            "dut_supply_count": self.dut_supply_count,
        }


@dataclass(frozen=True)
class ConfigurationPageState:
    """配置页当前应呈现的完整业务状态（不可变快照）。

    页面只按这份状态渲染，不再从控件、临时变量和配置字典互相回读。``bindings`` 是本次
    选择的 ``device_bindings`` 结构快照，**嵌套容器也是只读的**（映射只读、列表为元组），
    需要可写副本时用 :meth:`bindings_snapshot`；``validation`` 是最近一次转换或校验的
    问题，``run_status`` 是要显示在页面上的运行状态文本，``dirty`` 表示当前状态与上次
    干净基线不同。
    """

    draft: ConfigurationDraft
    bindings: Mapping[str, Any] = field(default_factory=dict)
    validation: ValidationResult = field(default_factory=ValidationResult)
    run_status: str = DEFAULT_RUN_STATUS
    dirty: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.draft, ConfigurationDraft):
            raise TypeError(
                f"draft 必须是 ConfigurationDraft，收到 {type(self.draft).__name__}"
            )
        if not isinstance(self.validation, ValidationResult):
            raise TypeError(
                "validation 必须是 ValidationResult，"
                f"收到 {type(self.validation).__name__}"
            )
        if not isinstance(self.run_status, str):
            raise TypeError(
                f"run_status 必须是字符串，收到 {type(self.run_status).__name__}"
            )
        if not isinstance(self.dirty, bool):
            raise TypeError(
                f"dirty 必须是布尔值，收到 {type(self.dirty).__name__}"
            )
        object.__setattr__(self, "bindings", _bindings_snapshot(self.bindings))

    def bindings_snapshot(self) -> dict[str, Any]:
        """输出绑定集合的可写副本；调用方不能通过返回值改写状态内部结构。"""
        return _thaw(self.bindings)


@dataclass(frozen=True)
class ConfigurationViewState:
    """连接页需要的只读配置视图（GUI 重构步骤 8）。

    它是 :class:`ConfigurationViewModel` 对外提供的窄读取面：设备发现页只需要
    “当前绑定集合”（填扫描框初值、标记旧绑定）、配置里已保存的仪器地址（没有绑定时
    的网段回退）和“沿用已保存地址、未扫描确认”的旧绑定条数。页面据此渲染扫描框与
    日志，不再读取另一个页面的 ``config`` / ``device_bindings`` 内部状态。

    ``bindings`` 与 :class:`ConfigurationPageState` 一样是只读快照（嵌套容器也不可
    改写）；``instrument_addresses`` 按配置文档中的出现顺序保留，空串与非文本地址
    不会进入视图。
    """

    bindings: Mapping[str, Any] = field(default_factory=dict)
    instrument_addresses: tuple[str, ...] = ()
    legacy_binding_count: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "bindings", _bindings_snapshot(self.bindings))
        addresses = tuple(self.instrument_addresses)
        for index, address in enumerate(addresses):
            if not isinstance(address, str):
                raise TypeError(
                    f"instrument_addresses[{index}] 必须是字符串，"
                    f"收到 {type(address).__name__}"
                )
        object.__setattr__(self, "instrument_addresses", addresses)
        count = self.legacy_binding_count
        if isinstance(count, bool) or not isinstance(count, int):
            raise TypeError(
                "legacy_binding_count 必须是整数，"
                f"收到 {type(count).__name__}"
            )
        if count < 0:
            raise ValueError("legacy_binding_count 不能为负数")
        object.__setattr__(self, "legacy_binding_count", count)

    def bindings_snapshot(self) -> dict[str, Any]:
        """输出绑定集合的可写副本；调用方不能通过返回值改写视图内部结构。"""
        return _thaw(self.bindings)


@dataclass(frozen=True)
class ConfigurationBindingOutcome:
    """一次“应用设备选择”的结果：绑定服务的结构化结果 + 操作后的页面状态。

    ``result.ok`` 为假时 ``state`` 保持操作前的状态（旧绑定不被替换），调用方按
    ``result`` 的文案提示；为真时 ``state.bindings`` 就是新的整批绑定。
    """

    result: DeviceBindingResult
    state: ConfigurationPageState

    def __post_init__(self) -> None:
        if not isinstance(self.result, DeviceBindingResult):
            raise TypeError(
                "result 必须是 DeviceBindingResult，"
                f"收到 {type(self.result).__name__}"
            )
        if not isinstance(self.state, ConfigurationPageState):
            raise TypeError(
                "state 必须是 ConfigurationPageState，"
                f"收到 {type(self.state).__name__}"
            )

    @property
    def ok(self) -> bool:
        return self.result.ok

    @property
    def issues(self) -> tuple[ValidationIssue, ...]:
        return self.result.issues

    def messages(self) -> tuple[str, ...]:
        return self.result.messages()


@dataclass(frozen=True)
class ConfigurationAction:
    """配置页请求的下一步动作（Effect）：保存配置或创建运行。

    视图模型不写文件、不创建运行；它只回答“以什么文档、在什么校验结论下执行哪个动作”。
    ``validation`` 不是 ``valid`` 时 ``document`` 为空，调用方不得落盘。

    接入状态：步骤 7 已把整份控件读数收进视图模型，因此这里的 ``document`` 是完整的
    配置编辑文档。实际保存/创建运行仍由 ``ApplicationCoordinator`` 调用页面的
    ``build_config`` 执行，本 Effect 只提供“动作是否可执行、要执行什么文档”的明确
    结果，不在视图模型里写文件或创建运行。
    """

    kind: str
    document: Mapping[str, Any] = field(default_factory=dict)
    validation: ValidationResult = field(default_factory=ValidationResult)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or self.kind not in _ACTION_KINDS:
            raise ValueError(
                f"动作种类必须是 {'、'.join(sorted(_ACTION_KINDS))} 之一，"
                f"收到 {self.kind!r}"
            )
        if not isinstance(self.validation, ValidationResult):
            raise TypeError(
                "validation 必须是 ValidationResult，"
                f"收到 {type(self.validation).__name__}"
            )
        if not isinstance(self.document, Mapping):
            raise TypeError(
                f"document 必须是对象，收到 {type(self.document).__name__}"
            )
        object.__setattr__(self, "document", _freeze(dict(self.document)))

    @property
    def ok(self) -> bool:
        """动作可以执行（校验没有错误）。"""
        return self.validation.valid

    def to_document(self) -> dict[str, Any]:
        """输出动作文档的可写副本；调用方不能通过返回值改写动作内部状态。"""
        return _thaw(self.document)


class ConfigurationViewModel:
    """配置编辑页的状态视图模型：纯 Python、显式方法、不碰控件与文件。

    所有方法都返回新的 :class:`ConfigurationPageState`（或明确的结构化结果），内部状态
    只在方法内更新；外部拿到的状态是快照，之后修改原字典或状态副本不会影响下一次状态。
    """

    def __init__(
        self,
        *,
        authoring: ConfigurationAuthoringService | None = None,
        binding_service: DeviceBindingService | None = None,
    ) -> None:
        self._authoring = authoring or ConfigurationAuthoringService()
        self._binding_service = binding_service or DeviceBindingService()
        self._document: dict[str, Any] = {}
        self._draft = ConfigurationDraft()
        self._bindings: Mapping[str, Any] = MappingProxyType({})
        self._validation = ValidationResult()
        # 最近一次问题来自正式校验（validate()）还是文档转换（load/apply）：正式校验的
        # 结论描述的是那一刻的整份配置，任何字段再被改写后都不能只删一部分继续展示。
        self._validation_is_formal = False
        self._run_status = DEFAULT_RUN_STATUS
        # 干净基线与当前状态用同一套投影计算，避免“刚打开就显示已修改”。
        self._clean_baseline = self._authoring_document()

    # ---- 读取 ---------------------------------------------------------------

    @property
    def state(self) -> ConfigurationPageState:
        """当前页面状态的不可变快照。"""
        return ConfigurationPageState(
            draft=self._draft,
            bindings=self._bindings,
            validation=self._validation,
            run_status=self._run_status,
            dirty=self.dirty,
        )

    @property
    def draft(self) -> ConfigurationDraft:
        return self._draft

    @property
    def bindings(self) -> Mapping[str, Any]:
        return self._bindings

    @property
    def validation(self) -> ValidationResult:
        return self._validation

    @property
    def run_status(self) -> str:
        return self._run_status

    @property
    def dirty(self) -> bool:
        """当前配置文档与上次干净基线不同。"""
        return self._authoring_document() != self._clean_baseline

    def view_state(self) -> ConfigurationViewState:
        """返回连接页读取的只读配置视图（步骤 8）。

        绑定取当前绑定快照；仪器地址与旧绑定条数来自当前编辑文档与绑定集合，因此页面
        不需要再读配置字典。这个方法只读，不改变状态、不重置基线。
        """
        return ConfigurationViewState(
            bindings=self._bindings,
            instrument_addresses=_configured_instrument_addresses(self._document),
            # 绑定结构的解释只在 application.device_binding，这里不另立一份遍历规则。
            legacy_binding_count=_count_legacy_bindings(self._bindings),
        )

    # ---- 动作 ---------------------------------------------------------------

    def load(
        self,
        document: Mapping[str, Any] | None,
        *,
        bindings: Mapping[str, Any] | DeviceBindingsDraft | None = None,
    ) -> ConfigurationPageState:
        """载入一份配置编辑文档（可附带已规范化的设备绑定），重置干净基线。

        ``bindings`` 用于页面在读取边界已经折叠好的绑定结构；不传时读取文档里的
        ``device_bindings``。缺失字段保持草稿的缺失状态，非法字段转成结构化问题，
        不猜默认设备。
        """
        self._read_document(document, bindings)
        self._clean_baseline = self._authoring_document()
        return self.state

    def update_form(
        self,
        document: Mapping[str, Any] | None,
        *,
        bindings: Mapping[str, Any] | DeviceBindingsDraft | None = None,
    ) -> ConfigurationPageState:
        """用页面当前读数同步整份编辑文档，**不重置**干净基线。

        这是步骤 7 的控件适配路径：:class:`ConfigurationFormAdapter` 把控件读数组装成
        文档后交给这里，草稿随之更新（包括电源通道参数与上电计划）。与 :meth:`load`
        的区别是它表示“用户正在编辑”，因此：

        - 干净基线保持不变，dirty 仍按上次保存/回填/切换模式的基线判定；
        - 运行状态文本保持不变；
        - 正式校验结论描述的是改写前的整份配置，这里直接丢弃；转换阶段的问题按新读数
          重新计算，不把旧字段的提示留在页面上。
        """
        self._read_document(document, bindings)
        return self.state

    def update_test_parameters(
        self, parameters: ConfigurationTestParameters
    ) -> ConfigurationPageState:
        """用一组类型化的测试参数替换草稿里的对应字段。

        新值已由 :class:`ConfigurationTestParameters` 校验，因此测试参数段上的旧问题
        随之清除；电源通道、分配与设备绑定保持原样（步骤 7 再接入）。如果最近一次问题是
        :meth:`validate` 的正式结论，它描述的是改写之前的整份配置，直接清空而不是只删
        测试参数那几行。

        读数边界：页面交给这里的是**已经转换成功**的字段片段（无法解析的频率文本不会
        进入片段，页面校验负责提示），所以本方法不接收也无法推断原文；它只更新快照，
        不替用户猜一个值，也不把“这次没读到”当作“配置有效”。
        """
        if not isinstance(parameters, ConfigurationTestParameters):
            raise TypeError(
                "parameters 必须是 ConfigurationTestParameters，"
                f"收到 {type(parameters).__name__}"
            )
        self._draft = replace(self._draft, **parameters.fields())
        self._validation = (
            ValidationResult()
            if self._validation_is_formal
            else _without_issues(self._validation, _TEST_PARAMETER_PATHS)
        )
        self._validation_is_formal = False
        return self.state

    def apply_device_selection(
        self, request: ApplyDeviceSelectionRequest
    ) -> ConfigurationBindingOutcome:
        """应用一次设备勾选结果；成功时整批替换绑定，失败时状态不变。

        参数只能是 :class:`ApplyDeviceSelectionRequest`：发现面板交付的字典列表在
        交付边界（连接页/设备绑定端口）收敛成命令 DTO，本方法不再保留第二套入参形态
        （步骤 13）。业务规则仍只在 :class:`DeviceBindingService`：这里只决定何时调用、
        如何把结果写进页面状态。
        """
        if not isinstance(request, ApplyDeviceSelectionRequest):
            raise TypeError(
                "request 必须是 ApplyDeviceSelectionRequest，"
                f"收到 {type(request).__name__}"
            )
        result = self._binding_service.apply(request)
        if not result.ok:
            # 整批拒绝：不替换旧绑定，也不写半份配置。
            return ConfigurationBindingOutcome(result=result, state=self.state)

        self._bindings = _bindings_snapshot(result.to_mapping())
        read = ConfigurationDraft.from_document({}, bindings=self._bindings)
        self._draft = replace(self._draft, bindings=read.draft.bindings)
        previous = (
            ValidationResult()
            if self._validation_is_formal
            else _without_issues(self._validation, ("device_bindings",))
        )
        self._validation = _merge_issues(previous, read.validation.issues)
        self._validation_is_formal = False
        return ConfigurationBindingOutcome(result=result, state=self.state)

    def validate(self) -> ValidationResult:
        """按当前状态编写并校验正式配置，返回结构化的校验问题。

        正式段生成与 domain 校验仍是 :class:`ConfigurationAuthoringService` 的职责；
        这里只把当前草稿包成配置编辑文档并保存最近一次的结论。编写不出正式配置时
        ``ConfigurationAuthoringError`` 照常抛给调用方（程序/配置整体失败，不是字段提示）。
        """
        self._validation = self._authoring.validate_document(
            ConfigurationEditingDocument.from_mapping(self._authoring_document())
        )
        self._validation_is_formal = True
        return self._validation

    def save(self) -> ConfigurationAction:
        """请求保存配置：返回带正式文档的 Effect；校验失败时文档为空。"""
        return self._action(SAVE_CONFIGURATION)

    def create_run(self) -> ConfigurationAction:
        """请求创建运行：与 :meth:`save` 使用同一份校验与正式文档。"""
        return self._action(CREATE_RUN)

    def set_run_status(self, text: str) -> ConfigurationPageState:
        """更新运行状态文本（由协调器在创建/加载运行后调用）。"""
        if not isinstance(text, str):
            raise TypeError(f"运行状态必须是字符串，收到 {type(text).__name__}")
        if not text.strip():
            raise ValueError("运行状态不能为空")
        self._run_status = text
        return self.state

    def mark_clean(self) -> ConfigurationPageState:
        """把当前状态记为干净基线（保存成功、回填或切换测试模式后调用）。"""
        self._clean_baseline = self._authoring_document()
        return self.state

    # ---- 内部 ---------------------------------------------------------------

    def _read_document(
        self,
        document: Mapping[str, Any] | None,
        bindings: Mapping[str, Any] | DeviceBindingsDraft | None,
    ) -> None:
        """把一份文档读数写进当前状态（不改基线、不改运行状态）。"""
        if document is None:
            document = {}
        if not isinstance(document, Mapping):
            raise TypeError(
                f"配置文档必须是对象，收到 {type(document).__name__}"
            )
        self._document = copy.deepcopy(dict(document))
        if bindings is None:
            raw_bindings = self._document.get("device_bindings")
            snapshot = (
                _bindings_snapshot(raw_bindings)
                if isinstance(raw_bindings, Mapping)
                else MappingProxyType({})
            )
        else:
            snapshot = _bindings_snapshot(bindings)
        read = ConfigurationDraft.from_document(self._document, bindings=snapshot)
        self._draft = read.draft
        self._bindings = snapshot
        self._validation = read.validation
        self._validation_is_formal = False

    def _action(self, kind: str) -> ConfigurationAction:
        validation = self.validate()
        if not validation.valid:
            return ConfigurationAction(kind=kind, validation=validation)
        document = self._authoring.author_document(
            ConfigurationEditingDocument.from_mapping(self._authoring_document())
        )
        return ConfigurationAction(
            kind=kind, document=document, validation=validation
        )

    def _authoring_document(self) -> dict[str, Any]:
        """当前状态对应的配置编辑文档。

        测试参数、电源参数、上电计划与设备绑定由草稿生成；草稿没有建模的顶层键（例如
        调用方保留的未知块或已保存的正式段）原样保留，避免视图模型在保存时静默丢字段。
        草稿不再建模通道 ``role``，因此旧配置里已有的取值也不会被写回这份文档：角色
        只属于旧配置的兼容读取，不能借“保留未建模字段”重新进入保存链路。
        """
        document = copy.deepcopy(self._document)
        document.update(self._draft.to_document())
        bindings = _thaw(self._bindings)
        if bindings:
            document["device_bindings"] = bindings
        else:
            document.pop("device_bindings", None)
        return document
