"""设备缩写码生成与显示文本：把完整设备码派生成现场可读、可长期引用的缩写设备码。

缩写设备码（契约名 ``short_code``，配置与注册表字段 ``label``）对**所有登记设备**生效，
不只电源：它只由完整设备码派生，与角色无关，因此同一台设备换角色、换 IP、改型号都不会
换码，也不会因为“后来才登记角色”而改变。电源把缩写码当 ``supply_id`` 用（硬要求：路由
与配置引用只认它）；其它仪器只用它做展示名，派生失败时留空、显示退回型号，不阻断设备表
保存。

缩写码规则（样例数据见 ``tests/fixtures/power_supply_identity/``）：

- 归一化直接调用 :func:`domain.instruments.models.normalize_full_code`：该函数自己反复应用
  直到稳定（引号与空白并存时需要两轮），本模块不再另写一套；归一化后仍有内部空白或结果
  为空，视为解析异常；
- 缩写码 = 完整设备码前缀 + 末四位，例如 ``DP8B214901519 -> DP8B1519``、
  ``DP8B174700950 -> DP8B0950``；
- 前缀取完整设备码开头的字母数字段：字母保留，数字只在其后紧跟字母时保留（``DP8B`` 里的
  ``8``），遇到连续两位及以上的数字段或非字母数字字符立即停止，前缀可以为空；
- 与已有缩写码冲突时依次扩展到末五位、末六位，最后退回完整归一化设备码，扩展同样保留
  前缀；加宽不会把前缀拼两遍，也不会越过通道分隔符 ``/``（缩写码要当 ``supply_id`` 用）；
- 冲突绝不覆盖已有缩写码：调用方传入的集合只读，全部候选都被占用时抛
  :class:`InstrumentLabelError`，由调用方决定报错还是重新扫描。

本模块是纯计算：不读注册表、不写文件、不依赖 Qt 与网络。缩写码第一次生成后由调用方显式
保存，之后不因 IP 变化、型号变化或重新扫描而重算。

除了“生成缩写码”，本模块也定义**怎么显示**——电源是 :func:`supply_display_label`，其它
仪器是 :func:`instrument_display_label`——以及**详情/审计怎么写**
（:func:`supply_identity_text`）、**日志里的通道名怎么写**（:func:`supply_channel_text`）
和**身份核对的结论怎么写**（:func:`supply_identity_verdict`）：日志、标签页标题、下拉框、
设备卡片与悬浮提示都从这里取文本，不各自拼一套。所有显示都采用“型号（缩写设备码）”，
业务值始终是 ``device_key`` / ``supply_id``，任何读取方都不得从显示文本反解析身份。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from domain.instruments.models import InstrumentIdentity, normalize_full_code, normalize_text

# 默认取完整设备码末四位；冲突时依次加宽到末五位、末六位……
LABEL_TAIL_WIDTH: Final = 4
# 缩写码同时是 ``supply_id``，会被拼成 ``supply_id/CHx``（见 ``instrument.measurement_factory``
# 的 ``supply/channel`` 路由），所以缩写码不能含通道分隔符，否则路由会把缩写码截断。
_LABEL_SEPARATOR: Final = "/"


class InstrumentLabelError(ValueError):
    """完整设备码无法派生成合法缩写码。

    覆盖四种情形：空完整设备码、归一化后仍有内部空白、候选含通道分隔符 ``/``（无法当
    ``supply_id`` 用）以及批量输入里重复的完整设备码。候选全部被占用也抛这个错误，见
    :func:`generate_instrument_label`。

    领域层异常，与 ``domain.measurement.values`` 的数值错误同一层：不进入
    ``application.errors`` 的跨层分类，由调用方在边界处决定报错方式——电源必须报错
    （没有缩写码就没有 ``supply_id``），其它设备的缩写码只用于展示，可以留空。
    """


def normalize_label_full_code(value: object) -> str:
    """归一化完整设备码并确认它可以派生成缩写码。

    :func:`domain.instruments.models.normalize_full_code` 本身已经反复应用直到稳定，这里只补
    领域校验：归一化后为空，或仍有内部空白（``DP8B 2149 01519``），抛
    :class:`InstrumentLabelError`。内部空白属于解析异常，静默删除会让缩写码与设备身份不同源；
    身份层保留原样，由操作员修正完整设备码后重新扫描。
    """
    stable = normalize_full_code(value)
    if not stable:
        raise InstrumentLabelError(f"完整设备码为空，无法生成设备缩写码: {value!r}")
    if any(char.isspace() for char in stable):
        raise InstrumentLabelError(
            f"完整设备码含内部空白，无法生成设备缩写码: {stable!r}；请修正完整设备码或重新扫描"
        )
    return stable


def label_prefix(full_code: object) -> str:
    """取完整设备码的系列前缀；输入可以是原始设备码，内部先归一化。

    ``DP8B214901519 -> 'DP8B'``（数字 ``8`` 后面紧跟字母 ``B``）、``105462 -> ''``、
    ``1412.0000K02/105462 -> ''``（第一个字符就是连续数字段）。
    """
    return _prefix_of(normalize_label_full_code(full_code))


def label_candidates(full_code: object) -> tuple[str, ...]:
    """返回按冲突优先级排列的全部候选缩写码，第一个就是首选。

    顺序是前缀 + 末四位、末五位、……，最后是完整归一化设备码。三条边界在这里固定：

    1. 加宽到“尾段正好覆盖整段非前缀主体”时，前缀 + 尾段已经等于完整设备码，不再继续加宽，
       否则会把前缀拼两遍（``DP8B1951519`` 拼成 ``DP8BDP8B1951519``）；
    2. 没有可加宽的尾部时（前缀占满整个设备码，如 ``ABCDE``）或归一化设备码不超过四位时
       （如 ``A12``），候选只有完整归一化设备码，不会拼成 ``ABCDEABCDE`` / ``AA12``；
    3. 缩写码要当 ``supply_id`` 用，会拼成 ``supply_id/CHx``，所以尾段一旦出现 ``/`` 就停止
       加宽，完整设备码含 ``/`` 时也不算候选；一个可用候选都没有时抛
       :class:`InstrumentLabelError`，交回操作员修正完整设备码。
    """
    stable = normalize_label_full_code(full_code)
    prefix = _prefix_of(stable)
    body = len(stable) - len(prefix)
    if len(stable) <= LABEL_TAIL_WIDTH or body == 0:
        return (_require_usable_label(stable, stable),)
    candidates: list[str] = []
    seen: set[str] = set()
    for width in range(min(LABEL_TAIL_WIDTH, body), body + 1):
        tail = stable[-width:]
        if _LABEL_SEPARATOR in tail:
            # 尾段已经跨过分隔符，继续加宽只会一直包含它。
            break
        candidate = f"{prefix}{tail}"
        if candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)
    if stable not in seen and _is_usable_label(stable):
        candidates.append(stable)
    if not candidates:
        raise InstrumentLabelError(
            f"完整设备码 {stable!r} 无法生成可用缩写码：每个候选都含通道分隔符 "
            f"{_LABEL_SEPARATOR!r}，不能作为 supply_id 使用；请修正完整设备码或重新扫描"
        )
    return tuple(candidates)


def generate_instrument_label(
    full_code: object, existing_labels: Iterable[str | None] = ()
) -> str:
    """为完整设备码挑选一个不与 ``existing_labels`` 冲突的稳定缩写码。

    ``existing_labels`` 是注册表里已经冻结的缩写码（含退役设备）；比较时忽略首尾空白与
    大小写，但既不修改也不覆盖它们。候选全部被占用时抛 :class:`InstrumentLabelError`，
    报告完整设备码和全部候选，交回操作员处理。
    """
    return _first_free_label(normalize_label_full_code(full_code), _label_keys(existing_labels))


def generate_instrument_labels(
    full_codes: Iterable[object], existing_labels: Iterable[str | None] = ()
) -> dict[str, str]:
    """按输入顺序为多个完整设备码生成互相不冲突的缩写码：``归一化设备码 -> 缩写码``。

    同一批设备也会互相避开：先处理的设备占住缩写码，后面的设备按冲突规则扩展。返回的字典
    按输入顺序排列；重复出现同一完整设备码说明是同一台设备，抛 :class:`InstrumentLabelError`。
    """
    taken = _label_keys(existing_labels)
    labels: dict[str, str] = {}
    for full_code in full_codes:
        stable = normalize_label_full_code(full_code)
        if stable in labels:
            raise InstrumentLabelError(f"同一完整设备码重复参与缩写码生成: {stable!r}")
        label = _first_free_label(stable, taken)
        taken.add(_label_key(label))
        labels[stable] = label
    return labels


# ---- 显示与审计文本 ---------------------------------------------------------

#: 身份文本里各字段之间的分隔符：详情、悬浮提示与身份核对日志共用同一份。
DISPLAY_SEPARATOR: Final = " · "


def supply_display_label(label: object, model: object = None) -> str:
    """电源在列表、标签页标题和下拉框上的显示文本：``DP832A（DP8B1519）``。

    三条规则：

    1. 缩写设备码是唯一识别项，型号只是补充信息：型号缺失时只显示缩写码；
    2. **不推导缩写码**：缩写码缺失时原样显示型号，旧槽位条目就是 ``PS1``，绝不从型号
       或完整设备码造一个看起来像缩写码的字符串——那是第二套编号；
    3. 显示文本只给人读：业务值始终是 ``supply_id``，任何读取方都不得从它反解析身份
       （见 ``presentation.qt.config_form_state._combo_supply_id``）。

    顺序与 :func:`instrument_display_label` 一致（型号在前、缩写码在括号里）：现场先认
    型号，再看是哪一台；两种设备不再各用一种括号顺序。
    """
    model_text = _display_text(model)
    text = _display_text(label)
    if not model_text:
        return text
    if text and text != model_text:
        return f"{model_text}（{text}）"
    return model_text


def instrument_display_label(
    label: object, model: object = None, detail: object = None
) -> str:
    """非电源仪器的显示文本：``SMW200A（5462，信号发生器）``。

    型号放在最前面（现场先认型号，再看是哪一台），缩写设备码在括号里起区分作用；型号与
    缩写码缺失时只剩有的那一段，两段都缺时退回 ``detail``，全缺时返回空串——不凭空补字。

    两处显示都是**唯一的实现**，界面不再各自拼括号；业务值始终是 ``device_key``，显示文本
    不参与识别。
    """
    model_text = _display_text(model)
    label_text = _display_text(label)
    detail_text = _display_text(detail)
    parts = [part for part in (label_text, detail_text) if part]
    if not model_text:
        return "，".join(parts)
    if not parts:
        return model_text
    return f"{model_text}（{'，'.join(parts)}）"


def supply_identity_text(
    label: object,
    model: object = None,
    full_code: object = None,
    device_key: object = None,
) -> str:
    """详情与审计用的完整身份文本：``DP832A（DP8B1519） · 完整设备码 DP8B214901519``。

    只列出**已知**字段：旧条目没有完整设备码时不会凭空出现“完整设备码”一段，也不会为了
    凑格式去查注册表。``device_key`` 放在最后——它是系统匹配用的身份（``厂商:完整设备码``，
    小写），现场读不出来，只在审计与排查时需要。缩写码缺失时按型号显示，仍然不推导缩写码。
    """
    parts = [supply_display_label(label, model)]
    code_text = _display_text(full_code)
    if code_text:
        parts.append(f"完整设备码 {code_text}")
    key_text = _display_text(device_key)
    if key_text:
        parts.append(f"设备键 {key_text}")
    return DISPLAY_SEPARATOR.join(part for part in parts if part)


def supply_channel_text(supply_id: object, channel: object, label: object = None) -> str:
    """日志与错误里的通道名：``缩写码/CHx``（缩写码缺失时退回 ``supply_id/CHx``）。

    通道的**业务值**始终是显式路由 ``supply_id/CHx``，物理路由只认它；日志要写现场缩写码。
    两者在装配计划里同时可得，所以这里只做拼接，**不从已经拼好的 token 反解析**：把
    ``supply_id/CHx`` 拆出设备身份，等于让显示文本承担身份，改一次缩写码就会读错设备。
    """
    supply_text = _display_text(label) or _display_text(supply_id)
    channel_text = _display_text(channel)
    if not supply_text:
        return channel_text
    if not channel_text:
        return supply_text
    return f"{supply_text}/{channel_text}"


# 身份核对的三种结论：日志、测试与文档共用这几个词，不再逐处手写措辞。
IDENTITY_MATCHED: Final = "一致"
IDENTITY_MISMATCHED: Final = "不一致"
IDENTITY_UNCOMPARABLE: Final = "无法比较"


def supply_identity_verdict(full_code: object, idn_reply: object) -> tuple[str, str]:
    """核对配置里的完整设备码与设备自己应答的 ``*IDN?``，返回 ``(结论, 可写进日志的说明)``。

    结论只会是 :data:`IDENTITY_MATCHED` / :data:`IDENTITY_MISMATCHED` /
    :data:`IDENTITY_UNCOMPARABLE` 之一：

    - ``一致``：两边都解析出完整设备码且归一化后相同；
    - ``不一致``：两边都解析出完整设备码但不同——配置与实物已经不是同一台，必须能被看见；
    - ``无法比较``：配置没有完整设备码（旧条目），或 ``*IDN?`` 读不出三段身份。

    只有两边都能解析时才可能判“不一致”：把“读不出来”说成“不一致”，会让一次会话占用
    或重启抖动看起来像接错了设备。核对只用于审计与提示，**不改写**地址、配置或路由：
    物理连接由地址决定，身份结论不能替操作者改接线。
    """
    configured = normalize_full_code(full_code) if _display_text(full_code) else ""
    try:
        device = InstrumentIdentity.from_idn(idn_reply)
    except ValueError:
        return (
            IDENTITY_UNCOMPARABLE,
            f"{IDENTITY_UNCOMPARABLE}（*IDN? 应答读不出设备身份）",
        )
    if not configured:
        return (
            IDENTITY_UNCOMPARABLE,
            f"{IDENTITY_UNCOMPARABLE}（配置没有完整设备码，设备完整设备码 {device.full_code}）",
        )
    if configured != device.full_code:
        return (
            IDENTITY_MISMATCHED,
            f"{IDENTITY_MISMATCHED}（配置 {configured}，设备完整设备码 {device.full_code}）",
        )
    return IDENTITY_MATCHED, IDENTITY_MATCHED


def _display_text(value: object) -> str:
    """显示用文本：字符串只折叠首尾空白，其余非字符串值按“没有该字段”处理。

    身份字段在注册表里是字符串；数字完整设备码（手写配置）也能显示，但空值、布尔值与
    结构体一律当作缺失——显示层不能把 ``{'model': ...}`` 直接拼进标签。
    """
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return ""


def _prefix_of(stable: str) -> str:
    """在已归一化的完整设备码上取前缀（调用方保证输入非空、无内部空白）。"""
    end = 0
    while end < len(stable):
        char = stable[end]
        if char.isalpha():
            end += 1
            continue
        if char.isdigit() and end + 1 < len(stable) and stable[end + 1].isalpha():
            end += 1
            continue
        break
    return stable[:end]


def _is_usable_label(candidate: str) -> bool:
    """缩写码要作为 ``supply_id`` 使用：非空、无空白、不含通道分隔符 ``/``。"""
    if not candidate or _LABEL_SEPARATOR in candidate:
        return False
    return not any(char.isspace() for char in candidate)


def _require_usable_label(candidate: str, stable: str) -> str:
    if not _is_usable_label(candidate):
        raise InstrumentLabelError(
            f"完整设备码 {stable!r} 无法生成可用缩写码：{candidate!r} 含通道分隔符 "
            f"{_LABEL_SEPARATOR!r}，不能作为 supply_id 使用；请修正完整设备码或重新扫描"
        )
    return candidate


def _first_free_label(stable: str, taken: set[str]) -> str:
    candidates = label_candidates(stable)
    for candidate in candidates:
        if _label_key(candidate) not in taken:
            return candidate
    raise InstrumentLabelError(
        f"完整设备码 {stable!r} 的候选缩写码全部被占用，无法生成新缩写码: {list(candidates)}"
    )


def _label_key(label: object) -> str:
    """缩写码的比较键：忽略首尾空白与大小写，空值返回空串（永远不会命中候选）。"""
    return normalize_text(label).upper()


def _label_keys(labels: Iterable[str | None]) -> set[str]:
    keys = {_label_key(label) for label in labels}
    keys.discard("")
    return keys


__all__ = [
    "DISPLAY_SEPARATOR",
    "IDENTITY_MATCHED",
    "IDENTITY_MISMATCHED",
    "IDENTITY_UNCOMPARABLE",
    "InstrumentLabelError",
    "LABEL_TAIL_WIDTH",
    "generate_instrument_label",
    "generate_instrument_labels",
    "instrument_display_label",
    "label_candidates",
    "label_prefix",
    "normalize_label_full_code",
    "supply_channel_text",
    "supply_display_label",
    "supply_identity_text",
    "supply_identity_verdict",
]
