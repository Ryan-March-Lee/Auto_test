"""初始化并灌入设备表（instruments.json）。

首次运行创建 ``instruments.json``，并用一次现场扫描结果写入 5 台仪器。
脚本可重复执行：设备按 ``(厂商, 序列号)`` 命中已有记录时只刷新固件/选件/状态/地址，
不会重复建档，也不会改动已分配的资产名与同型号序号。

用法::

    python tools/init_instrument_registry.py           # 建表/更新并打印
    python tools/init_instrument_registry.py --show    # 只打印现有设备表
    python tools/init_instrument_registry.py --file other.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from domain.instruments.models import DeviceStatus, InstrumentIdentity  # noqa: E402
from infrastructure.persistence.instrument_registry import (  # noqa: E402
    InstrumentRegistry,
    default_registry_path,
)

# 2026-10-28 现场扫描结果（信号源 1 台、频谱仪 1 台、直流电源 3 台）。
# idn_port 是实际能读到 *IDN? 的端口：R&S / Ceyear 用 SCPI Raw 5025，RIGOL DP832A 用 5555。
# role 是这台设备固定的测试角色（界面不再让用户选择）；电源按现场已确认的接线登记，
# 没有确认过角色的设备留 None，界面显示“未分配角色”且不能应用。
SCANNED_DEVICES = [
    {
        "ip": "192.168.1.201",
        "mac": "00-E0-33-05-C5-FE",
        "idn": "Rohde&Schwarz,SMW200A,1412.0000K02/105462,5.20.043.47",
        "idn_port": 5025,
        "scpi_port": 5025,
        "firmware": "5.20.043.47",
        "role": "signal_generator",
        "options": [
            "SMW-B9", "SMW-B13XT", "SMW-B140", "SMW-K17", "SMW-K144",
            "SMW-K255", "SMW-K262", "SMW-K300", "SMW-K526",
        ],
    },
    {
        "ip": "192.168.1.202",
        "mac": "74-FE-48-86-AD-FF",
        "idn": "Ceyear Technologies,4082F,QZNI000866,1.12.04",
        "idn_port": 5025,
        "scpi_port": 5025,
        "firmware": "1.12.04",
        "role": "spectrum_analyzer",
        "options": [],
    },
    {
        "ip": "192.168.1.102",
        "mac": "00-19-AF-5B-DD-37",
        "idn": "RIGOL TECHNOLOGIES,DP832A,DP8B214901519,00.01.16",
        "idn_port": 5555,
        "scpi_port": 5555,
        "firmware": "00.01.16.00.02",
        "role": "PS3",
        "options": [],
    },
    {
        "ip": "192.168.1.106",
        "mac": "00-19-AF-5B-4D-67",
        "idn": "RIGOL TECHNOLOGIES,DP832A,DP8B174700950,00.01.14",
        "idn_port": 5555,
        "scpi_port": 5555,
        "firmware": "00.01.14.00.03",
        "role": None,
        "options": [],
    },
    {
        "ip": "192.168.1.108",
        "mac": "00-19-AF-5B-4D-68",
        "idn": "RIGOL TECHNOLOGIES,DP832A,DP8B174400885,00.01.14",
        "idn_port": 5555,
        "scpi_port": 5555,
        "firmware": "00.01.14.00.03",
        "role": "PS4",
        "options": [],
    },
]


def seed(registry: InstrumentRegistry, devices) -> int:
    created_count = 0
    for device in devices:
        identity = InstrumentIdentity.from_idn(device["idn"])
        instrument, created = registry.upsert(
            identity,
            firmware=device.get("firmware"),
            options=device.get("options", ()),
            status=DeviceStatus.ONLINE,
            role=device.get("role"),
        )
        registry.record_endpoint(
            instrument,
            ip=device["ip"],
            mac=device["mac"],
            idn_port=device.get("idn_port"),
            scpi_port=device.get("scpi_port"),
            visa_address=f"TCPIP0::{device['ip']}::inst0::INSTR",
            source="scan",
        )
        created_count += 1 if created else 0
    registry.save()
    return created_count


def print_table(registry: InstrumentRegistry) -> None:
    instruments = registry.list()
    print(f"{'资产名':<12}{'型号':<10}{'角色':<18}{'序列号':<16}{'当前IP':<16}{'MAC':<20}{'状态':<12}{'固件'}")
    print("-" * 120)
    for item in instruments:
        endpoint = item.current_endpoint
        print(
            f"{item.name:<12}{item.model:<10}{(item.role or '未分配'):<18}{item.serial:<16}"
            f"{(endpoint.ip if endpoint else '-'):<16}"
            f"{(endpoint.mac if endpoint else '-'):<20}"
            f"{item.status.value:<12}{item.firmware or '-'}"
        )
    print(f"\n合计 {len(instruments)} 台")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="初始化并灌入设备表")
    parser.add_argument("--file", default=None, help="设备表文件路径，默认项目根目录 instruments.json")
    parser.add_argument("--show", action="store_true", help="只打印现有设备表，不写入")
    args = parser.parse_args(argv)

    target = Path(args.file) if args.file else default_registry_path()
    registry = InstrumentRegistry.load(target)
    if not args.show:
        created_count = seed(registry, SCANNED_DEVICES)
        print(f"本次新建 {created_count} 台设备")
    print(f"设备表: {target}")
    print(f"schema_version = {registry.schema_version}\n")
    print_table(registry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
