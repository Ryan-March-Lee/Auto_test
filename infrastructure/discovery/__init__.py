"""仪器发现包：按网段探测 SCPI 端口并读取 ``*IDN?``。"""

from .instrument_discovery import (
    DEFAULT_IDN_PORTS,
    SCAN_MODE_NETWORK,
    SCAN_MODE_SIMULATED,
    DiscoveredDevice,
    DiscoveryStatus,
    InstrumentDiscoveryError,
    InstrumentScanner,
    ScanResult,
    SimulatedIdnProbe,
    TcpIdnProbe,
    build_scanner,
    parse_scan_subnet,
)

__all__ = [
    "DEFAULT_IDN_PORTS",
    "SCAN_MODE_NETWORK",
    "SCAN_MODE_SIMULATED",
    "DiscoveredDevice",
    "DiscoveryStatus",
    "InstrumentDiscoveryError",
    "InstrumentScanner",
    "ScanResult",
    "SimulatedIdnProbe",
    "TcpIdnProbe",
    "build_scanner",
    "parse_scan_subnet",
]
