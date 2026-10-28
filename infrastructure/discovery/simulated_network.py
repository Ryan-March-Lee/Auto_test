"""离线演示用的“现场设备网络”。

开发机没有仪器网段时，扫描按钮仍需要能演示完整交互：这里给出与现场扫描一致的
``*IDN?`` 响应，其中包括**一台未登记的未知设备**，用于验证“未知设备也出现在结果里”
这条产品要求。

这些数据只用于模拟模式（``SCAN_MODE_SIMULATED``），不会被当成现场扫描结果，也不会
写入设备表；真实扫描走 :class:`infrastructure.discovery.instrument_discovery.TcpIdnProbe`。
"""

from __future__ import annotations

DEMO_SUBNET = "192.168.1.0/24"

# (host, port) -> *IDN? 响应。端口与现场一致：R&S / Ceyear 5025，RIGOL DP832A 5555。
DEMO_IDN_RESPONSES: dict[tuple[str, int], str] = {
    ("192.168.1.201", 5025): "Rohde&Schwarz,SMW200A,1412.0000K02/105462,5.20.043.47",
    ("192.168.1.202", 5025): "Ceyear Technologies,4082F,QZNI000866,1.12.04",
    ("192.168.1.102", 5555): "RIGOL TECHNOLOGIES,DP832A,DP8B214901519,00.01.16",
    # 设备表里没有的序列号：演示“发现未知设备”。
    ("192.168.1.150", 5025): "Keysight Technologies,N9020A,MY51234567,A.18.21",
}


def demo_idn_responses() -> dict[tuple[str, int], str]:
    """返回一份可变副本，避免调用方修改模块级常量。"""
    return dict(DEMO_IDN_RESPONSES)


__all__ = ["DEMO_IDN_RESPONSES", "DEMO_SUBNET", "demo_idn_responses"]
