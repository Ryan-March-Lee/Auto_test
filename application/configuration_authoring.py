"""正式运行配置文档的唯一生成入口（GUI 重构步骤 4）。

归属（见 `docs/配置领域词汇与模块归属.md` 与 `docs/GUI业务逻辑重构计划.md`）：application。
配置页只编辑一份面向现场硬件的组合文档；本服务用配置编辑段生成同一文档中的正式
``test_plan`` / ``run_mapping`` 段，使保存出的文件可以被
:class:`infrastructure.config.json_config_repository.JsonConfigurationRepository`
直接读取，而无需在加载时再做旧配置推断。

两个入口，同一实现：

- :meth:`ConfigurationAuthoringService.author` 接受 :class:`ConfigurationDraft`，
  是后续 ViewModel / 页面适配器使用的正式入口；
- :meth:`ConfigurationAuthoringService.author_document` 接受
  :class:`ConfigurationEditingDocument`（明确的配置编辑 DTO），是迁移期把仍在按文档
  读写的调用方接到本服务上的入口。两者最终都走同一套编写规则。

正式校验也在这个边界内：:meth:`ConfigurationAuthoringService.validate_document` 先
编写再调用 domain 的正式运行校验与硬件连接校验，返回结构化的
:class:`application.configuration_editing.ValidationResult`。表现层只展示结果，不再
直接导入 ``domain.configuration.models`` / ``domain.configuration.rules`` 做正式校验。

迁移期的 ``presentation.qt.configuration_authoring`` 只保留转发壳，不再新增规则。

约定：

- 表单缺失或非法的字段保持 ``None`` / 空列表，由正式校验在连接仪器前明确失败，
  不生成“待人工确认”的半成品。
- 现场固定接线的 DUT 通道角色（``CH1`` 栅极、``CH2`` 漏极）在这里作为**编辑约定**
  写入正式 ``test_plan`` / ``run_mapping`` 角色；不从旧配置文件反推，也不猜测未知
  通道的角色。
- ``instruments.power_supplies.<name>.channels.<channel>.role`` 属于接线事实，由配置页
  控件显式选择后随表单写入；本服务不改写它，也不为缺失角色补默认值。
- 正式 ``run_mapping`` 的电源段是**集合** ``instruments.power_supplies.<supply_id>``：
  每台被供电分配实际引用的电源各占一条，条目自带 ``label`` / ``model`` / ``full_code`` /
  ``device_key`` / ``visa_address``。快照只登记实际使用的电源，绑定了但没被分配的电源
  不会进快照。集合大小由分配与绑定共同决定，1 台和多台走同一条路径，不再有“只能登记
  一个本机电源”的限制。
- 通道连接统一写成 ``supply_id/CHx``（与 ``instrument.measurement_factory`` 的
  ``supply/channel`` 路由一致），因此 DUT 的 gate 与 drain 可以来自不同电源。同一条
  物理通道被分配两次时**不在这里去重**：重复条目原样写进映射，由正式校验
  （``通道重复分配`` / ``供电角色重复分配``）在连接前失败，而不是悄悄只留一条。
- 身份字段（``label`` / ``full_code`` / ``device_key``）只从该仪器自己的配置条目读取：
  信号源、频谱仪与每台电源的快照条目都带这组字段；条目缺少身份时写空，**不按地址、
  型号或显示名补全**，也不在运行期间回查设备表。
"""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from application.configuration_draft import ConfigurationDraft
from application.configuration_editing import ValidationIssue, ValidationResult
from application.errors import ConfigurationError
from domain.configuration.models import (
    RunConfiguration,
    RunResourceMapping,
    TestPlan,
    validate_run_configuration,
)
from domain.configuration.rules import assignment_supply_id, validate_hardware_connections
from domain.configuration.types import ConfigIssue
from domain.instruments.models import is_legacy_power_supply_slot

__all__ = [
    "ConfigurationAuthoringError",
    "ConfigurationAuthoringService",
    "ConfigurationEditingDocument",
    "DUT_CHANNEL_ROLES",
    "OFFICIAL_SCHEMA_VERSION",
    "author_official_configuration",
    "parse_db_value",
]

OFFICIAL_SCHEMA_VERSION = "1.0"
# 现场固定接线的编辑约定：DUT 的 CH1 是栅极、CH2 是漏极。未知通道不猜测角色。
DUT_CHANNEL_ROLES = {"CH1": "gate", "CH2": "drain"}
DRIVER_CHANNEL_ROLE = "driver"
_DB_VALUE_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*dB\s*$", re.IGNORECASE)
_ASSIGNMENT_GROUPS = ("dut_amplifier", "driver_amplifier")
# 正式校验问题的稳定错误码：只标识来源边界，不随文案变化。
_CODE_RUN_VALIDATION = "configuration.run_validation"
_CODE_HARDWARE_CONNECTIONS = "configuration.hardware_connections"


class ConfigurationAuthoringError(ConfigurationError):
    """当前表单无法如实表达为一份正式运行配置。

    这只用于“正式 schema 根本表达不了”的情况；缺失或非法字段仍写成 ``None`` /
    空列表，交给正式校验报告，不在这里提前猜测或补默认值。
    """


@dataclass(frozen=True)
class ConfigurationEditingDocument:
    """明确的配置编辑 DTO：配置页读出的编辑段文档（迁移期文档入口）。

    形状就是 :meth:`ConfigurationDraft.to_document` 产出的键（外加调用方保留的其它
    顶层字段，例如配置页读到的未知块）；它既不是草稿，也不是正式文档，不含
    ``test_plan`` / ``run_mapping`` 的业务含义。保存、创建运行等旧调用方仍按文档读写，
    通过这个 DTO 接到配置编写服务，避免在表现层保留第二套编写规则。

    非对象输入（``None``、列表等）按空文档处理：这是旧编写入口的既有行为，服务不
    替调用方猜测“应该是对象”。
    """

    values: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.values, Mapping):
            raise TypeError(
                f"配置编辑文档必须是对象，收到 {type(self.values).__name__}"
            )
        # 输入快照：调用方在构造之后改写原字典或其中的嵌套集合，不能改变 DTO 的内容。
        # 这里与旧编写入口一样做深拷贝；DTO 一旦建立，表示的就是“交给服务的那一份”。
        object.__setattr__(self, "values", copy.deepcopy(dict(self.values)))

    @classmethod
    def from_mapping(
        cls, document: Mapping[str, Any] | None
    ) -> "ConfigurationEditingDocument":
        """把配置编辑文档收敛为 DTO；非对象输入按空文档处理。"""
        if isinstance(document, Mapping):
            return cls(values=dict(document))
        return cls()

    def to_mapping(self) -> dict[str, Any]:
        """输出可写的文档副本；调用方不能通过返回值改写 DTO 的内部状态。"""
        return copy.deepcopy(dict(self.values))


class ConfigurationAuthoringService:
    """正式配置文档的唯一生成服务。

    服务无状态，调用方按需构造即可；它不读控件、不碰文件、不创建仪器，只把配置编辑
    数据写成正式 ``test_plan`` / ``run_mapping``。业务判据仍由 ``domain`` 的正式校验
    定义，这里只负责结构与字段的忠实转换。
    """

    def author(self, draft: ConfigurationDraft) -> dict[str, Any]:
        """把配置草稿写成正式配置文档。

        草稿是配置编辑阶段的唯一数据模型（步骤 3）；``to_document()`` 只输出编辑段，
        这里补齐 ``schema_version`` 并生成正式段。缺失值与空引用保持原样，交给正式
        校验报告，不在这里补默认设备或默认参数。
        """
        if not isinstance(draft, ConfigurationDraft):
            raise TypeError(
                f"draft 必须是 ConfigurationDraft，收到 {type(draft).__name__}"
            )
        return self.author_document(
            ConfigurationEditingDocument.from_mapping(draft.to_document())
        )

    def author_document(
        self, document: ConfigurationEditingDocument
    ) -> dict[str, Any]:
        """把一个配置编辑 DTO 写成正式配置文档。

        输入文档不会被修改；返回的是带 ``schema_version`` / ``test_plan`` /
        ``run_mapping`` 的新文档副本。缺失或非法字段只会导致正式校验失败，不会在这里
        抛异常。电源数量不再受“只能有一台”限制：分配引用了两台就生成两条电源条目。
        """
        if not isinstance(document, ConfigurationEditingDocument):
            raise TypeError(
                "document 必须是 ConfigurationEditingDocument，"
                f"收到 {type(document).__name__}"
            )
        values = document.to_mapping()
        values["schema_version"] = OFFICIAL_SCHEMA_VERSION
        values["test_plan"] = _author_test_plan(values)
        values["run_mapping"] = _author_run_mapping(values)
        return values

    def validate_document(
        self, document: ConfigurationEditingDocument
    ) -> ValidationResult:
        """编写并校验一份配置编辑文档，返回结构化的正式校验问题。

        这里只做编排：正式 ``test_plan`` / ``run_mapping`` 的判据仍由 domain 的
        ``validate_run_configuration`` 定义，硬件装配段的连接冲突仍由
        ``validate_hardware_connections`` 定义；本方法不复制、不改写任何判据。

        问题顺序与原页面一致：先正式运行校验（错误 + 警告），再硬件装配校验。页面通常
        只展示 :attr:`ValidationResult.errors`，所以用户可见顺序仍是“正式段问题在前、
        连接冲突在后”。编写失败（``ConfigurationError``）照常抛给调用方，不在这里
        改写成“校验问题”。
        """
        authored = self.author_document(document)
        run_issues = validate_run_configuration(
            RunConfiguration(
                TestPlan.from_dict(authored.get("test_plan") or {}),
                RunResourceMapping.from_dict(authored.get("run_mapping") or {}),
            )
        )
        connection_issues = validate_hardware_connections(authored)
        issues = tuple(
            _validation_issue(issue, _CODE_RUN_VALIDATION)
            for issue in (*run_issues.errors, *run_issues.warnings)
        ) + tuple(
            _validation_issue(issue, _CODE_HARDWARE_CONNECTIONS)
            for issue in (*connection_issues.errors, *connection_issues.warnings)
        )
        return ValidationResult(issues=issues)


def author_official_configuration(
    config: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """把配置编辑文档写成正式配置文档（迁移期文档入口）。

    仅供尚未迁移到 :meth:`ConfigurationAuthoringService.author` 的调用方使用；它不读
    控件、不写文件，行为与原 ``presentation.qt.configuration_authoring`` 相同。
    """
    return ConfigurationAuthoringService().author_document(
        ConfigurationEditingDocument.from_mapping(config)
    )


def _validation_issue(issue: ConfigIssue, code: str) -> ValidationIssue:
    """把 domain 的配置问题投影为配置编辑结果里的结构化问题。

    domain 的问题只有 level / path / message，没有稳定错误码；码由来源边界给出
    （正式运行校验或硬件连接校验）。级别只取 ``warning``，其余按 ``error`` 处理，避免
    domain 新增级别时让表现层收到无法展示的值。
    """
    return ValidationIssue(
        path=issue.path or "configuration",
        code=code,
        message=issue.message,
        severity="warning" if issue.level == "warning" else "error",
    )


def parse_db_value(value: Any) -> float | None:
    """把 ``30dB`` 形式的表单值转为数值；非法值返回 ``None``。"""
    if not isinstance(value, str):
        return None
    match = _DB_VALUE_PATTERN.fullmatch(value)
    if not match:
        return None
    parsed = float(match.group(1))
    return parsed if math.isfinite(parsed) else None


def _author_test_plan(document: Mapping[str, Any]) -> dict[str, Any]:
    signal_source = _mapping(document.get("signal_source"))
    compression = _mapping(document.get("compression_point"))
    attenuator = _mapping(document.get("attenuator"))
    dut_config = _mapping(document.get("dut_config"))
    driver_mode = _mapping(document.get("driver_mode"))
    driver_channels = _author_driver_role_plans(document)
    return {
        "schema_version": OFFICIAL_SCHEMA_VERSION,
        "template": False,
        "plan_id": "gui-configuration",
        "frequencies": {
            "values": _frequencies(document.get("test_frequencies")),
            "unit": "GHz",
        },
        "signal_source": {
            "start_power": signal_source.get("start_power"),
            "stop_power": signal_source.get("stop_power"),
            "step": signal_source.get("step"),
            "unit": "dBm",
        },
        "compression_point": {"type": compression.get("type")},
        "attenuator": {
            "value": parse_db_value(attenuator.get("type")),
            "unit": "dB",
        },
        "dut": {
            "max_input_power": dut_config.get("max_input_power"),
            "power_roles": _author_dut_role_plans(document),
        },
        "driver_mode": {
            "enabled": bool(driver_mode.get("enabled")),
            "power_source": _driver_power_source(document, driver_channels),
            "power_roles": driver_channels,
        },
        "other_parameters": {},
    }


def _author_run_mapping(document: Mapping[str, Any]) -> dict[str, Any]:
    instruments = _mapping(document.get("instruments"))
    signal_generator = _mapping(instruments.get("signal_generator"))
    spectrum_analyzer = _mapping(instruments.get("spectrum_analyzer"))
    driver_channels = _author_driver_channel_mappings(document)
    return {
        "schema_version": OFFICIAL_SCHEMA_VERSION,
        "template": False,
        "run_id": None,
        "run_datetime": None,
        "operator": None,
        "instruments": {
            "signal_generator": _author_instrument_entry(signal_generator),
            "spectrum_analyzer": _author_instrument_entry(spectrum_analyzer),
            "power_supplies": _author_power_supply_entries(document),
        },
        "driver_mode": {
            "enabled": bool(_mapping(document.get("driver_mode")).get("enabled")),
            "power_source": _driver_power_source(document, driver_channels),
            "power_channels": driver_channels,
        },
        "dut_power_channels": _author_dut_channel_mappings(document),
        "notes": None,
    }


def _author_instrument_entry(instrument: Mapping[str, Any]) -> dict[str, Any]:
    """信号源/频谱仪的运行快照条目：冻结型号、缩写码、完整设备码、设备键与地址。

    身份字段只从该仪器自己的配置条目读取；条目缺少身份时写空，由正式校验在连接前报错，
    不按地址或型号去猜设备。未启用的条目只冻结身份、不写连接地址。
    """
    return {
        "label": instrument.get("label"),
        "model": instrument.get("model"),
        "full_code": instrument.get("full_code"),
        "device_key": instrument.get("device_key"),
        "visa_address": (
            instrument.get("address") if instrument.get("enabled", True) else None
        ),
    }


def _driver_power_source(document: Mapping[str, Any], driver_channels: Any) -> str | None:
    """显式供电方式优先；旧配置按驱动通道是否存在推断。"""
    mode = _mapping(document.get("driver_mode"))
    if not mode.get("enabled"):
        return None
    source = mode.get("power_source")
    if source in ("dc_supply", "external_ac"):
        return source
    return "dc_supply" if driver_channels else "external_ac"


def _author_dut_role_plans(document: Mapping[str, Any]) -> dict[str, Any]:
    plans: dict[str, Any] = {}
    for assignment in _assignment_supplies(document, "dut_amplifier").values():
        supply_name = _assigned_supply_id(assignment)
        channels = _supply_channels(document, supply_name)
        for channel in _channel_list(assignment.get("channel")):
            role = DUT_CHANNEL_ROLES.get(channel)
            plans.setdefault(role or channel, _channel_plan(role, _mapping(channels.get(channel))))
    return plans


def _author_driver_role_plans(document: Mapping[str, Any]) -> dict[str, Any]:
    plans: dict[str, Any] = {}
    for assignment in _assignment_supplies(document, "driver_amplifier").values():
        supply_name = _assigned_supply_id(assignment)
        channels = _supply_channels(document, supply_name)
        for channel in _channel_list(assignment.get("channel")):
            plans.setdefault(
                DRIVER_CHANNEL_ROLE,
                _channel_plan(DRIVER_CHANNEL_ROLE, _mapping(channels.get(channel))),
            )
    return plans


def _author_dut_channel_mappings(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    mappings: list[dict[str, Any]] = []
    for assignment in _assignment_supplies(document, "dut_amplifier").values():
        supply_name = _assigned_supply_id(assignment)
        for channel in _channel_list(assignment.get("channel")):
            token = _channel_token(supply_name, channel)
            role = DUT_CHANNEL_ROLES.get(channel)
            connection = token
            if role:
                connection = f"{connection} -> DUT {'栅极' if role == 'gate' else '漏极'}"
            mappings.append(
                {"channel": token, "role": role, "connection": connection}
            )
    return mappings


def _author_driver_channel_mappings(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    mappings: list[dict[str, Any]] = []
    for assignment in _assignment_supplies(document, "driver_amplifier").values():
        supply_name = _assigned_supply_id(assignment)
        for channel in _channel_list(assignment.get("channel")):
            token = _channel_token(supply_name, channel)
            mappings.append(
                {
                    "channel": token,
                    "role": DRIVER_CHANNEL_ROLE,
                    "connection": f"{token} -> 驱动功放",
                }
            )
    return mappings


def _channel_plan(role: str | None, settings: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "role": role,
        "voltage": _setting(settings, "voltage", "value"),
        "current": _setting(settings, "current", "value"),
        "voltage_protection": _setting(settings, "voltage", "protection"),
        "current_protection": _setting(settings, "current", "protection"),
    }


def _setting(channel: Mapping[str, Any], section: str, key: str) -> Any:
    value = _mapping(channel.get(section)).get(key)
    return value if _is_finite_number(value) else None


def _assignment_supplies(document: Mapping[str, Any], assignment_name: str) -> Mapping[str, Any]:
    assignments = _mapping(document.get("power_supply_assignment"))
    return _mapping(_mapping(assignments.get(assignment_name)).get("supplies"))


def _supply_channels(document: Mapping[str, Any], supply_name: Any) -> Mapping[str, Any]:
    if not isinstance(supply_name, str):
        return {}
    supplies = _mapping(_mapping(document.get("instruments")).get("power_supplies"))
    return _mapping(_mapping(supplies.get(supply_name)).get("channels"))


def _used_supply_ids(document: Mapping[str, Any]) -> list[str]:
    """按出现顺序返回供电分配实际引用的 ``supply_id``（去重）。

    只认 ``supply_id``：它才是电源引用。旧记录没有该字段时回落读一次同名
    ``name``（过渡期两者同值），完全未绑定的引用（``supply_id: null``）不计入——
    它交给正式校验报告，不能被当成一台电源写进快照。
    """
    names: list[str] = []
    for group in _ASSIGNMENT_GROUPS:
        for assignment in _assignment_supplies(document, group).values():
            name = _assigned_supply_id(assignment)
            # 没有引用（``supply_id: null``、空白）的分配不算一台电源：它交给正式校验
            # 报告，不能被当成某台设备写进快照。
            if name is not None and name not in names:
                names.append(name)
    return names


def _assigned_supply_id(assignment: Mapping[str, Any]) -> str | None:
    """分配条目引用的电源 ``supply_id``。

    读引用规则只有一处实现（:func:`domain.configuration.rules.assignment_supply_id`）：
    ``supply_id`` 优先，只有完全没有该字段的旧记录才回落读一次 ``name``。配置页、正式
    运行映射与文件级校验都用它，不存在第二套编号。
    """
    return assignment_supply_id(assignment)


def _author_power_supply_entries(document: Mapping[str, Any]) -> dict[str, Any]:
    """生成正式 ``run_mapping.instruments.power_supplies``：每台**实际使用**的电源一条。

    条目自带身份（``label`` / ``model`` / ``full_code`` / ``device_key``）和地址，历史查看
    与重现不再回查设备注册表。身份只从该电源自己的配置条目读取，不按地址或型号推断。
    """
    supplies = _mapping(_mapping(document.get("instruments")).get("power_supplies"))
    entries: dict[str, Any] = {}
    for supply_id in _used_supply_ids(document):
        supply = _mapping(supplies.get(supply_id))
        entries[supply_id] = {
            "label": supply.get("label"),
            # 旧槽位条目的 ``model`` 里存的是槽位名（``PS1``），那不是型号；旧写法没有
            # 型号信息，宁可留空让正式校验报告，也不把编号当型号写进快照。槽位名的识别
            # 只有领域层一处实现（``is_legacy_power_supply_slot``），页面不再自带槽位清单。
            "model": None
            if is_legacy_power_supply_slot(supply.get("model"))
            else supply.get("model"),
            "full_code": supply.get("full_code"),
            "device_key": supply.get("device_key"),
            "visa_address": supply.get("address"),
        }
    return entries


def _channel_token(supply_id: str | None, channel: str) -> str:
    """通道的显式路由：``supply_id/CHx``。

    未选择电源时只写通道名，由正式校验报“通道路由指向快照里没有的电源”，
    而不是凭通道名猜一台电源。
    """
    return f"{supply_id}/{channel}" if supply_id else channel


def _channel_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _frequencies(value: Any) -> list[Any]:
    # 保留原始元素，让正式校验发现非法频率；不静默丢弃表单/文件中的坏值。
    return list(value) if isinstance(value, list) else []


def _is_finite_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
