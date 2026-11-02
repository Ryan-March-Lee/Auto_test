"""离线演示用的“现场设备网络”。

开发机没有仪器网段时，扫描按钮仍需要能演示完整交互：这里给出与现场扫描一致的
``*IDN?`` 响应，其中包含**一台未登记的未知设备**，用于验证“未知设备也出现在结果里”
这条产品要求。

已登记的设备不再手写第二份清单：传入设备表时，按每台设备的当前地址、探测端口与身份
（厂商、型号、完整设备码、固件）补出响应。因此离线演示展示的就是这台机器上
``instruments.json`` 里已经登记的仪器——设备表增删设备后不必再同步维护这里，也不会
出现“配置里 5 台、演示只认得 3 台”的漂移。未传入设备表时退回 :data:`DEMO_IDN_RESPONSES`
里的固定条目，保持单元测试与工具脚本可以直接调用。

这些数据只用于模拟模式（``SCAN_MODE_SIMULATED``），不会被当成现场扫描结果，也不会
写入设备表；真实扫描走 :class:`infrastructure.discovery.instrument_discovery.TcpIdnProbe`。
"""

from __future__ import annotations

from typing import Any

from application.ports.device_discovery import DEMO_SUBNET
from domain.instruments.models import InstrumentIdentity

# (host, port) -> *IDN? 响应。端口与现场一致：R&S / Ceyear 5025，RIGOL DP832A 5555。
# 这里的三条已登记设备只是没有设备表时的固定回放（与项目自带的 ``instruments.json``
# 一致）；装配根会把真实设备表交给 :func:`demo_idn_responses`，按设备表补全其余设备。
DEMO_IDN_RESPONSES: dict[tuple[str, int], str] = {
    ("192.168.1.201", 5025): "Rohde&Schwarz,SMW200A,1412.0000K02/105462,5.20.043.47",
    ("192.168.1.202", 5025): "Ceyear Technologies,4082F,QZNI000866,1.12.04",
    ("192.168.1.102", 5555): "RIGOL TECHNOLOGIES,DP832A,DP8B214901519,00.01.16",
    # 设备表里没有的序列号：演示“发现未知设备”。
    ("192.168.1.150", 5025): "Keysight Technologies,N9020A,MY51234567,A.18.21",
}

#: 设备表没记探测端口时的默认端口：R&S / Ceyear 在 5025，RIGOL DP832A 在 5555。
_DEFAULT_IDN_PORT = 5025
_RIGOL_IDN_PORT = 5555


def _demo_port(instrument: Any) -> int:
    """设备表里登记的探测端口；没有记录时按厂商/型号取现场常用端口。"""
    endpoint = getattr(instrument, "current_endpoint", None)
    for value in (
        getattr(endpoint, "idn_port", None),
        getattr(endpoint, "scpi_port", None),
    ):
        if isinstance(value, bool):
            continue
        try:
            port = int(value)
        except (TypeError, ValueError):
            continue
        if 0 < port < 65536:
            return port
    vendor = str(getattr(instrument, "vendor", "") or "").casefold()
    model = str(getattr(instrument, "model", "") or "").casefold()
    if "rigol" in vendor or model.startswith("dp"):
        return _RIGOL_IDN_PORT
    return _DEFAULT_IDN_PORT


def _idn_for_instrument(instrument: Any) -> str:
    """把设备表条目还原成一条 ``*IDN?`` 响应。

    顺序与现场一致：``厂商,型号,订单号/完整设备码,固件``；没有订单号时第三段只写完整
    设备码。解析回 :class:`domain.instruments.models.InstrumentIdentity` 后，设备键与设备
    表里的条目相同，发现结果因此会被识别为“已登记”。
    """
    vendor = str(getattr(instrument, "vendor", "") or "").strip()
    model = str(getattr(instrument, "model", "") or "").strip()
    full_code = str(getattr(instrument, "full_code", "") or "").strip()
    order_no = str(getattr(instrument, "order_no", "") or "").strip()
    code_field = f"{order_no}/{full_code}" if order_no else full_code
    parts = [vendor, model, code_field]
    firmware = str(getattr(instrument, "firmware", "") or "").strip()
    if firmware:
        parts.append(firmware)
    return ",".join(parts)


def _registered_idn_responses(registry: Any) -> dict[tuple[str, int], str]:
    """为设备表里的每台已登记设备补一条演示响应（按当前 IP 与探测端口）。"""
    responses: dict[tuple[str, int], str] = {}
    for instrument in registry.list():
        ip = str(getattr(instrument, "current_ip", "") or "").strip()
        if not ip:
            # 没有当前地址就无法模拟“这个地址上有这台设备”；不从历史地址里猜。
            continue
        responses[(ip, _demo_port(instrument))] = _idn_for_instrument(instrument)
    return responses


def _registered_keys_with_address(registry: Any) -> set[str]:
    """返回设备表里有当前地址的设备的键；固定的旧地址据此让位给设备表。"""
    keys: set[str] = set()
    for instrument in registry.list():
        key = str(getattr(instrument, "device_key", "") or "").strip().casefold()
        if key and str(getattr(instrument, "current_ip", "") or "").strip():
            keys.add(key)
    return keys


def demo_idn_responses(registry: Any = None) -> dict[tuple[str, int], str]:
    """返回一份可变副本，避免调用方修改模块级常量。

    传入设备表（``InstrumentRegistry``）时，设备表里的每台已登记设备都会按它的当前
    IP、探测端口与身份得到一条 ``*IDN?`` 响应：离线演示因此能展示配置里登记的全部仪器，
    而不是一份会与设备表脱节的固定清单。固定的未登记演示设备始终保留，用来验证
    “未知设备也出现在结果里”。

    固定条目里属于“设备表当前地址已经覆盖”的那几台不会重复出现：设备换了 IP 时，演示
    网络跟着设备表走，旧地址不再冒出一台同身份的幽灵设备。
    """
    if registry is None:
        return dict(DEMO_IDN_RESPONSES)

    responses = _registered_idn_responses(registry)
    with_ip = _registered_keys_with_address(registry)
    for address, idn in DEMO_IDN_RESPONSES.items():
        try:
            key = InstrumentIdentity.from_idn(idn).device_key
        except ValueError:
            # 解析不了的固定条目照样保留：它本来就是“看得见但认不出”的演示素材。
            responses[address] = idn
            continue
        if key not in with_ip:
            responses[address] = idn
    return responses


__all__ = ["DEMO_IDN_RESPONSES", "DEMO_SUBNET", "demo_idn_responses"]
