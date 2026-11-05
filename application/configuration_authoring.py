"""正式运行配置文档的唯一生成入口（GUI 重构步骤 4）。

归属（见 `docs/配置领域词汇与模块归属.md`）：application。
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
编写再调用 domain 的编写文档校验、正式运行校验与硬件连接校验，返回结构化的
:class:`application.configuration_editing.ValidationResult`。表现层只展示结果，不再
直接导入 ``domain.configuration.models`` / ``domain.configuration.rules`` 做正式校验。

约定：

- 表单缺失或非法的字段保持 ``None`` / 空列表，由正式校验在连接仪器前明确失败，
  不生成“待人工确认”的半成品。
- 用户显式选择的上电顺序、组间隔与漏极引用**原样**搬运到正式段：编辑段的
  ``power_supply_assignment.<group>.power_plan`` 冻结为 ``run_mapping`` 的
  ``dut_power_plan`` / ``driver_mode.power_plan``，``dut_config.drain_channel`` 写成
  ``test_plan.dut.drain_channel``。本服务不推断角色/顺序，也不按 ``CH1``/``CH2`` 猜漏极。
- 1.1 正式段不再写通道 ``role``、``power_roles``、``power_channels``、``connection``
  与 ``dut_power_channels``；每路由工作值/保护值只写 ``channel_settings``（无序集合）。
- authoring 同时是**版本门禁**：输入缺失版本（新建草稿）或等于当前版本才允许保存；
  1.0、更高版本，以及“无版本但含 1.0 遗留电源字段”的文档一律拒绝。旧配置只能先经
  迁移器升级（§6.11.3、§6.14），authoring 不做隐式升级。
- 正式 ``run_mapping`` 的电源段是**集合** ``instruments.power_supplies.<supply_id>``：
  每台被供电分配实际引用的电源各占一条，条目自带 ``label`` / ``model`` / ``full_code`` /
  ``device_key`` / ``visa_address``。快照只登记实际使用的电源，绑定了但没被分配的电源
  不会进快照。集合大小由分配与绑定共同决定，1 台和多台走同一条路径，不再有“只能登记
  一个本机电源”的限制。
- 通道参数按物理路由 ``(supply_id, channel)`` 标识，DUT 的漏极与其他通道可以来自不同
  电源。同一物理通道被分配两次时这里只写一条 ``channel_settings``（集合语义），重复
  分配仍由编辑文档校验报告，不在这里伪造两条参数。
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
    LEGACY_SCHEMA_VERSION,
    RunConfiguration,
    RunResourceMapping,
    SUPPORTED_SCHEMA_VERSION,
    TestPlan,
    validate_run_configuration,
)
from domain.configuration.rules import (
    assignment_supply_id,
    validate_hardware_connections,
    validate_power_plan,
    validate_power_supply_assignment_references,
)
from domain.configuration.types import ConfigIssue
from domain.instruments.models import is_legacy_power_supply_slot

__all__ = [
    "ConfigurationAuthoringError",
    "ConfigurationAuthoringService",
    "ConfigurationEditingDocument",
    "EDITING_SECTION_FIELDS",
    "OFFICIAL_SCHEMA_VERSION",
    "author_official_configuration",
    "has_editing_section",
    "parse_db_value",
]

# 写入版本只有一处来源（domain）；保留导出名，减少调用方改名。
OFFICIAL_SCHEMA_VERSION = SUPPORTED_SCHEMA_VERSION
_DB_VALUE_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*dB\s*$", re.IGNORECASE)
_ASSIGNMENT_GROUPS = ("dut_amplifier", "driver_amplifier")
# 配置编辑段字段（`ConfigurationDraft.to_document` 的键）。组合配置里只要出现任意
# 一个，就说明这份文件带编辑段，读取时必须执行 §6.1.1 的“正式段 == 重写结果”比对。
# 判据只有这一份：仓储层不另写字段清单，也不用某个单独字段（例如 test_frequencies）
# 充当“有没有编辑段”的标志。
EDITING_SECTION_FIELDS = frozenset(
    {
        "test_frequencies",
        "signal_source",
        "compression_point",
        "attenuator",
        "driver_mode",
        "dut_config",
        "instruments",
        "power_supply_assignment",
        "device_bindings",
    }
)
# 正式校验问题的稳定错误码：只标识来源边界，不随文案变化。
_CODE_POWER_PLAN = "configuration.power_plan"
_CODE_POWER_ASSIGNMENT_REFERENCES = "configuration.power_assignment_references"
_CODE_RUN_VALIDATION = "configuration.run_validation"
_CODE_HARDWARE_CONNECTIONS = "configuration.hardware_connections"


class ConfigurationAuthoringError(ConfigurationError):
    """当前表单无法如实表达为一份正式运行配置。

    这只用于“正式 schema 根本表达不了”的情况（例如版本门禁拒绝保存），缺失或非法
    字段仍写成 ``None`` / 空列表，交给正式校验报告，不在这里提前猜测或补默认值。
    ``path`` 是结构化错误位置，便于调用方把问题定位到 ``schema_version`` 或具体遗留字段。
    """

    def __init__(self, message: str, *, path: str = "configuration") -> None:
        super().__init__(message)
        self.path = path


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
        _require_saveable_document(values)
        _require_consistent_legacy_references(values)
        _strip_legacy_editing_fields(values)
        values["schema_version"] = OFFICIAL_SCHEMA_VERSION
        values["test_plan"] = _author_test_plan(values)
        values["run_mapping"] = _author_run_mapping(values)
        return values

    def stale_formal_sections(
        self, document: ConfigurationEditingDocument
    ) -> tuple[ValidationIssue, ...]:
        """1.1 编辑文档的正式段是否与“用编辑段重新编写”的结果不一致。

        配置文件同时保存编辑段与派生段；若手改文件或写入中断，页面上的顺序与运行时的
        顺序会分叉。这里只比较语义数据（键序/缩进不参与），并忽略运行元数据
        ``run_id`` / ``run_datetime`` / ``operator``。1.0 文档不参与该比对（只读，走迁移器）。
        """
        values = document.to_mapping()
        if values.get("schema_version") != OFFICIAL_SCHEMA_VERSION:
            return ()
        if "test_plan" not in values and "run_mapping" not in values:
            return ()
        authored = self.author_document(document)
        issues: list[ValidationIssue] = []
        for section in ("test_plan", "run_mapping"):
            if section not in values:
                continue
            if _normalized_section(values.get(section), section) != _normalized_section(
                authored.get(section), section
            ):
                issues.append(
                    ValidationIssue(
                        path=section,
                        code="configuration.stale_formal_sections",
                        message="正式段与编辑段不一致：文件可能被手改或写入中断；"
                        "请重新保存后再创建运行",
                        severity="error",
                    )
                )
        return tuple(issues)

    def validate_document(
        self, document: ConfigurationEditingDocument
    ) -> ValidationResult:
        """编写并校验一份配置编辑文档，返回结构化的正式校验问题。

        这里只做编排：正式 ``test_plan`` / ``run_mapping`` 的判据仍由 domain 的
        ``validate_run_configuration`` 定义，硬件装配段的连接冲突仍由
        ``validate_hardware_connections`` 定义；编写文档里的上电计划、漏极引用与供电
        分配引用也由 domain 的 ``validate_power_plan`` /
        ``validate_power_supply_assignment_references`` 定义。本方法只把结果转成结构化
        问题，不复制、不改写任何判据。

        问题顺序与原页面一致：先上电计划/漏极，再供电分配引用，然后正式运行校验
        （错误 + 警告），最后硬件装配校验。页面通常只展示
        :attr:`ValidationResult.errors`，所以用户可见顺序仍是“计划/分配问题在前、正式
        段问题居中、连接冲突在后”。编写失败（``ConfigurationError``）照常抛给调用方，
        不在这里改写成“校验问题”。
        """
        authored = self.author_document(document)
        # 注意：stale 检查不属于“当前编辑是否合法”。页面在保存前本来就持有上一版
        # 派生段，编辑段一旦变化它就必然过期；把 stale 当成本次校验错误会让正常编辑
        # 无法保存。需要 stale 的读取/创建运行边界显式调用 :meth:`stale_formal_sections`。
        source_issues = (
            (
                validate_power_plan(authored),
                _CODE_POWER_PLAN,
            ),
            (
                validate_power_supply_assignment_references(authored),
                _CODE_POWER_ASSIGNMENT_REFERENCES,
            ),
        )
        run_issues = validate_run_configuration(
            RunConfiguration(
                TestPlan.from_dict(authored.get("test_plan") or {}),
                RunResourceMapping.from_dict(authored.get("run_mapping") or {}),
            )
        )
        connection_issues = validate_hardware_connections(authored)
        issues = tuple(
            _validation_issue(issue, code)
            for result, code in source_issues
            for issue in (*result.errors, *result.warnings)
        ) + tuple(
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
    控件、不写文件，行为与原页面编写入口相同（该页面入口已在步骤 13 删除）。
    """
    return ConfigurationAuthoringService().author_document(
        ConfigurationEditingDocument.from_mapping(config)
    )


def has_editing_section(document: Mapping[str, Any] | None) -> bool:
    """这份组合文档是否带配置编辑段。

    读取边界用它决定是否执行 stale 比对（§6.1.1）：只有带编辑段的 1.1 组合文件才有
    “编辑段与派生段可能分叉”的问题；拆分的正式文件与运行快照不经过该判据。
    """
    if not isinstance(document, Mapping):
        return False
    return any(field in document for field in EDITING_SECTION_FIELDS)


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


def _require_saveable_document(document: Mapping[str, Any]) -> None:
    """保存版本门禁（v1.1 §6.11.3、§6.12 规则 20）。

    只有“缺失版本的新草稿”或“版本等于当前写入版本”的文档允许进入 authoring。
    1.0 与更高版本都拒绝，唯一升级路径是迁移器；无版本但含 1.0 遗留电源字段的文档
    同样按 1.0 处理，避免一份旧文件被“打开→保存”悄悄写成半新半旧。
    """
    version = document.get("schema_version")
    if version is not None:
        if version == OFFICIAL_SCHEMA_VERSION:
            # 当前版本允许编辑段残留旧角色字段：它们在写出时被剥离（见
            # ``_strip_legacy_editing_fields``），不会进入 1.1 输出。
            return
        if version == LEGACY_SCHEMA_VERSION:
            raise ConfigurationAuthoringError(
                "1.0 配置只能查看，不能保存；请先运行迁移器升级到 "
                f"{OFFICIAL_SCHEMA_VERSION}",
                path="schema_version",
            )
        raise ConfigurationAuthoringError(
            f"配置版本 {version!r} 不能由当前程序保存；"
            f"当前写入版本是 {OFFICIAL_SCHEMA_VERSION}",
            path="schema_version",
        )
    retired_field_path = _legacy_power_field_path(document)
    if retired_field_path is not None:
        raise ConfigurationAuthoringError(
            "配置没有 schema_version，但包含 1.0 遗留电源字段；"
            f"请先运行迁移器升级到 {OFFICIAL_SCHEMA_VERSION}（问题字段: {retired_field_path}）",
            path=retired_field_path,
        )


def _legacy_power_field_path(document: Mapping[str, Any]) -> str | None:
    """返回第一个 1.0 遗留电源字段的路径；没有则返回 ``None``。"""
    for section in ("test_plan", "run_mapping"):
        block = _mapping(document.get(section))
        if section == "test_plan":
            dut = _mapping(block.get("dut"))
            driver = _mapping(block.get("driver_mode"))
            for key in ("power_roles", "power_channels"):
                if key in dut:
                    return f"test_plan.dut.{key}"
                if key in driver:
                    return f"test_plan.driver_mode.{key}"
        else:
            if "dut_power_channels" in block:
                return "run_mapping.dut_power_channels"
            if "power_channels" in _mapping(block.get("driver_mode")):
                return "run_mapping.driver_mode.power_channels"

    instruments = _mapping(document.get("instruments"))
    for supply_id, supply in _mapping(instruments.get("power_supplies")).items():
        for channel, channel_config in _mapping(
            _mapping(supply).get("channels")
        ).items():
            if "role" in _mapping(channel_config):
                return (
                    f"instruments.power_supplies.{supply_id}"
                    f".channels.{channel}.role"
                )

    assignments = _mapping(document.get("power_supply_assignment"))
    for group in _ASSIGNMENT_GROUPS:
        supplies = _mapping(_mapping(assignments.get(group)).get("supplies"))
        for slot, entry in supplies.items():
            item = _mapping(entry)
            if "name" in item and "supply_id" not in item:
                return (
                    f"power_supply_assignment.{group}.supplies.{slot}.name"
                )
    return None


def _require_consistent_legacy_references(document: Mapping[str, Any]) -> None:
    """1.1 编辑段的旧 ``name`` 别名只能与 ``supply_id`` 同值。

    ``name`` 是过渡别名，保存时必须被剥离；如果先剥离再校验，一条
    ``{"supply_id": "PS1", "name": "PS2"}`` 就会悄悄变成 ``PS1``，把“两个身份”的
    数据错误藏起来。这里在剥离前检查：两者同时出现且不同值直接拒绝；只有别名
    没有主字段的 1.1 文档同样拒绝（1.1 的引用字段只有 ``supply_id``，旧槽位迁移
    属于迁移器）。``supply_id: null`` 表示“明确未绑定”，此时 ``name`` 只是残留，
    不参与引用，也不提升成某台电源。
    """
    assignments = _mapping(document.get("power_supply_assignment"))
    for group in _ASSIGNMENT_GROUPS:
        supplies = _mapping(_mapping(assignments.get(group)).get("supplies"))
        for slot, entry in supplies.items():
            item = _mapping(entry)
            if "name" not in item:
                continue
            path = f"power_supply_assignment.{group}.supplies.{slot}.name"
            if "supply_id" not in item:
                raise ConfigurationAuthoringError(
                    "1.1 供电分配只能使用 supply_id 引用电源；"
                    "旧字段 name 需先经迁移器升级",
                    path=path,
                )
            supply_id = item.get("supply_id")
            name = item.get("name")
            if (
                isinstance(supply_id, str)
                and supply_id.strip()
                and isinstance(name, str)
                and name.strip()
                and supply_id.strip() != name.strip()
            ):
                raise ConfigurationAuthoringError(
                    f"过渡字段 name 必须与 supply_id 同值: {name!r} != {supply_id!r}",
                    path=path,
                )


def _strip_legacy_editing_fields(document: dict[str, Any]) -> None:
    """把 1.1 编辑段里残留的旧角色/旧槽位别名从写出内容中剥离。

    角色和 ``name`` 引用都只属于 1.0 读取兼容；1.1 的唯一引用是 ``supply_id``，
    通道参数由物理路由标识。这里只改要写出的副本，不动调用方的文档。
    """
    instruments = _mapping(document.get("instruments"))
    for supply in _mapping(instruments.get("power_supplies")).values():
        for channel in _mapping(_mapping(supply).get("channels")).values():
            if isinstance(channel, dict):
                channel.pop("role", None)
    assignments = _mapping(document.get("power_supply_assignment"))
    for group in _ASSIGNMENT_GROUPS:
        for entry in _mapping(_mapping(assignments.get(group)).get("supplies")).values():
            if isinstance(entry, dict):
                entry.pop("name", None)


def _normalized_section(value: Any, section: str) -> Any:
    """stale 比对用的语义规范化。

    - ``run_mapping`` 忽略运行元数据 ``run_id`` / ``run_datetime`` / ``operator``；
    - ``channel_settings`` 是无序集合（§6.1），比较前按路由排序；
    - 上电顺序与列表顺序其余部分照常比较，``2`` 与 ``2.0`` 按数值相等。
    """
    data = copy.deepcopy(dict(value)) if isinstance(value, Mapping) else value
    if not isinstance(data, dict):
        return data
    if section == "run_mapping":
        for key in ("run_id", "run_datetime", "operator"):
            data.pop(key, None)
        return data
    if section == "test_plan":
        for block, key in (
            (data.get("dut"), "channel_settings"),
            (data.get("driver_mode"), "channel_settings"),
        ):
            if not isinstance(block, dict):
                continue
            settings = block.get(key)
            if isinstance(settings, list):
                block[key] = sorted(
                    (item for item in settings),
                    key=lambda item: (
                        str(item.get("supply_id")) if isinstance(item, Mapping) else "",
                        str(item.get("channel")) if isinstance(item, Mapping) else "",
                    ),
                )
    return data


def _author_test_plan(document: Mapping[str, Any]) -> dict[str, Any]:
    signal_source = _mapping(document.get("signal_source"))
    compression = _mapping(document.get("compression_point"))
    attenuator = _mapping(document.get("attenuator"))
    dut_config = _mapping(document.get("dut_config"))
    driver_mode = _mapping(document.get("driver_mode"))
    driver_routes = _assignment_routes(document, "driver_amplifier")
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
            # 漏极是用户配置的测量引用，原样冻结；authoring 不按通道名推断。
            "drain_channel": copy.deepcopy(dut_config.get("drain_channel")),
            "channel_settings": _author_channel_settings(document, "dut_amplifier"),
        },
        "driver_mode": {
            "enabled": bool(driver_mode.get("enabled")),
            "power_source": _driver_power_source(document, driver_routes),
            "channel_settings": _author_channel_settings(document, "driver_amplifier"),
        },
        "other_parameters": {},
    }


def _author_run_mapping(document: Mapping[str, Any]) -> dict[str, Any]:
    instruments = _mapping(document.get("instruments"))
    signal_generator = _mapping(instruments.get("signal_generator"))
    spectrum_analyzer = _mapping(instruments.get("spectrum_analyzer"))
    driver_routes = _assignment_routes(document, "driver_amplifier")
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
            "power_source": _driver_power_source(document, driver_routes),
            "power_plan": _author_power_plan(document, "driver_amplifier"),
        },
        "dut_power_plan": _author_power_plan(document, "dut_amplifier"),
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


def _driver_power_source(document: Mapping[str, Any], driver_routes: Any) -> str | None:
    """显式供电方式优先；旧配置按驱动通道是否存在推断。"""
    mode = _mapping(document.get("driver_mode"))
    if not mode.get("enabled"):
        return None
    source = mode.get("power_source")
    if source in ("dc_supply", "external_ac"):
        return source
    return "dc_supply" if driver_routes else "external_ac"


def _author_power_plan(document: Mapping[str, Any], group: str) -> Any:
    """原样冻结一组的编辑段上电计划。

    缺少 ``power_plan`` 键按空计划处理（迁移产物与尚未排序的 1.1 文档）；键存在但
    形状非法时原样写出，交给正式校验报告，不在这里补空序列或 ``0.0``。
    """
    assignment = _mapping(
        _mapping(document.get("power_supply_assignment")).get(group)
    )
    if "power_plan" not in assignment:
        return {"power_sequence": [], "inter_channel_delay_s": 0.0}
    return copy.deepcopy(assignment.get("power_plan"))


def _author_channel_settings(
    document: Mapping[str, Any], group: str
) -> list[dict[str, Any]]:
    """按物理路由写出 ``channel_settings``：路由两段平铺 + 工作值/保护值。

    顺序无关（集合语义）；这里按分配出现顺序稳定输出，同一物理路由只写一条
    （重复分配由编辑文档校验负责报告）。缺少工作值时写 ``None``，让运行校验在连接
    前明确失败，不补默认电压/电流。
    """
    settings: list[dict[str, Any]] = []
    for supply_id, channel in _assignment_routes(document, group):
        channel_config = _mapping(_supply_channels(document, supply_id).get(channel))
        settings.append(
            {
                "supply_id": supply_id,
                "channel": channel,
                "voltage": _setting(channel_config, "voltage", "value"),
                "current": _setting(channel_config, "current", "value"),
                "voltage_protection": _setting(channel_config, "voltage", "protection"),
                "current_protection": _setting(channel_config, "current", "protection"),
                "unit_voltage": "V",
                "unit_current": "A",
            }
        )
    return settings


def _assignment_routes(
    document: Mapping[str, Any], group: str
) -> list[tuple[str, str]]:
    """一组分配引用的物理路由，按出现顺序去重。

    只收集可解析的引用：未选择电源、通道名非字符串或含 ``/`` 的条目留给正式校验
    报告，不在这里猜测成某台电源/某个通道。
    """
    routes: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for assignment in _assignment_supplies(document, group).values():
        supply_id = _assigned_supply_id(assignment)
        if supply_id is None or "/" in supply_id:
            continue
        for channel in _channel_list(assignment.get("channel")):
            if not channel.strip() or "/" in channel:
                continue
            route = (supply_id, channel.strip())
            if route in seen:
                continue
            seen.add(route)
            routes.append(route)
    return routes


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
