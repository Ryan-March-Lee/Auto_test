"""电源短标签生成：把设备序列号派生成现场可读、可长期引用的短标签。

规则见[电源设备身份与配置数据契约](../../docs/电源设备身份与配置数据契约.md)第 3 节：

- 归一化复用 :func:`domain.instruments.models.normalize_serial`，并反复应用直到稳定
  （引号与空白并存时需要两轮）；归一化后仍有内部空白或结果为空，视为解析异常；
- 标签 = 序列号前缀 + 末四位，例如 ``DP8B214901519 -> DP8B1519``、``DP8B174700950 -> DP8B0950``；
- 前缀取序列号开头的字母数字段：字母保留，数字只在其后紧跟字母时保留（``DP8B`` 里的 ``8``），
  遇到连续两位及以上的数字段或非字母数字字符立即停止，前缀可以为空；
- 与已有标签冲突时依次扩展到末五位、末六位，最后退回完整归一化序列号，扩展同样保留前缀；
- 冲突绝不覆盖已有标签：调用方传入的集合只读，全部候选都被占用时抛
  :class:`PowerSupplyLabelError`，由调用方决定报错还是重新扫描。

本模块是纯计算：不读注册表、不写文件、不依赖 Qt 与网络。标签第一次生成后由调用方显式保存，
之后不因 IP 变化、型号序号变化或重新扫描而重算。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from domain.instruments.models import normalize_serial, normalize_text

# 默认取序列号末四位；冲突时依次加宽到末五位、末六位……
LABEL_TAIL_WIDTH: Final = 4
# 归一化最多应用的轮数：实测两轮收敛，留一轮余量后仍不收敛说明输入异常。
_NORMALIZE_PASSES: Final = 4


class PowerSupplyLabelError(ValueError):
    """序列号无法派生成合法短标签（空序列号、内部空白、候选全部被占用或输入重复）。

    领域层异常，与 ``domain.measurement.values`` 的数值错误同一层：不进入
    ``application.errors`` 的跨层分类，由调用方在边界处决定报错方式。
    """


def normalize_supply_serial(value: object) -> str:
    """归一化序列号：反复应用 ``normalize_serial`` 直到结果不再变化。

    单次调用只剥掉一层引号，``' " dp8b214901519 " '`` 这类 ``*IDN?`` 分段需要两轮。
    归一化后仍有内部空白（``DP8B 2149 01519``）或为空，抛 :class:`PowerSupplyLabelError`：
    内部空白属于解析异常，静默删除会让标签与设备身份不同源。
    """
    current = normalize_serial(value)
    for _ in range(_NORMALIZE_PASSES):
        following = normalize_serial(current)
        if following == current:
            break
        current = following
    else:
        raise PowerSupplyLabelError(f"序列号归一化不收敛: {value!r}")
    if not current:
        raise PowerSupplyLabelError(f"序列号为空，无法生成电源短标签: {value!r}")
    if any(char.isspace() for char in current):
        raise PowerSupplyLabelError(
            f"序列号含内部空白，无法生成电源短标签: {current!r}；请修正序列号或重新扫描"
        )
    return current


def supply_label_prefix(serial: object) -> str:
    """取序列号的系列前缀；输入可以是原始序列号，内部先归一化。

    ``DP8B214901519 -> 'DP8B'``（数字 ``8`` 后面紧跟字母 ``B``）、``105462 -> ''``、
    ``1412.0000K02/105462 -> ''``（第一个字符就是连续数字段）。
    """
    stable = normalize_supply_serial(serial)
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


def supply_label_candidates(serial: object) -> tuple[str, ...]:
    """返回按冲突优先级排列的全部候选标签，第一个就是首选标签。

    顺序是前缀 + 末四位、末五位、……，最后是完整归一化序列号。加宽到“尾段正好覆盖整段
    非前缀部分”时，前缀 + 尾段已经等于完整序列号，不再继续加宽——否则会把前缀拼两遍
    （``DP8B1951519`` 拼成 ``DP8BP8B1951519``）。归一化序列号不超过四位时只有一个候选
    （就是它本身），避免 ``A12`` 拼成 ``AA12``；前缀几乎占满序列号时同理只剩完整序列号。
    """
    stable = normalize_supply_serial(serial)
    if len(stable) <= LABEL_TAIL_WIDTH:
        return (stable,)
    prefix = supply_label_prefix(stable)
    body = len(stable) - len(prefix)
    candidates: list[str] = []
    seen: set[str] = set()
    for width in range(min(LABEL_TAIL_WIDTH, body), body + 1):
        candidate = f"{prefix}{stable[-width:]}"
        if candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)
    if stable not in seen:
        candidates.append(stable)
    return tuple(candidates)


def generate_supply_label(serial: object, existing_labels: Iterable[str | None] = ()) -> str:
    """为序列号挑选一个不与 ``existing_labels`` 冲突的稳定短标签。

    ``existing_labels`` 是注册表里已经冻结的标签（含退役设备）；比较时忽略首尾空白与大小写，
    但既不修改也不覆盖它们。候选全部被占用时抛 :class:`PowerSupplyLabelError`，
    报告序列号和全部候选，交回操作员处理。
    """
    return _first_free_label(normalize_supply_serial(serial), _label_keys(existing_labels))


def generate_supply_labels(
    serials: Iterable[object], existing_labels: Iterable[str | None] = ()
) -> dict[str, str]:
    """按输入顺序为多个序列号生成互相不冲突的标签：``归一化序列号 -> 标签``。

    同一批设备也会互相避开：先处理的设备占住标签，后面的设备按冲突规则扩展。返回的字典
    按输入顺序排列；重复出现同一序列号说明是同一台设备，抛 :class:`PowerSupplyLabelError`。
    """
    taken = _label_keys(existing_labels)
    labels: dict[str, str] = {}
    for serial in serials:
        stable = normalize_supply_serial(serial)
        if stable in labels:
            raise PowerSupplyLabelError(f"同一序列号重复参与标签生成: {stable!r}")
        label = _first_free_label(stable, taken)
        taken.add(_label_key(label))
        labels[stable] = label
    return labels


def _first_free_label(stable: str, taken: set[str]) -> str:
    candidates = supply_label_candidates(stable)
    for candidate in candidates:
        if _label_key(candidate) not in taken:
            return candidate
    raise PowerSupplyLabelError(
        f"序列号 {stable!r} 的候选标签全部被占用，无法生成新标签: {list(candidates)}"
    )


def _label_key(label: object) -> str:
    """标签的比较键：忽略首尾空白与大小写，空值返回空串（永远不会命中候选）。"""
    return normalize_text(label).upper()


def _label_keys(labels: Iterable[str | None]) -> set[str]:
    keys = {_label_key(label) for label in labels}
    keys.discard("")
    return keys


__all__ = [
    "LABEL_TAIL_WIDTH",
    "PowerSupplyLabelError",
    "generate_supply_label",
    "generate_supply_labels",
    "normalize_supply_serial",
    "supply_label_candidates",
    "supply_label_prefix",
]
