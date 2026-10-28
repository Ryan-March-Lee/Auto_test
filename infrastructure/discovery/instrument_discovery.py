"""仪器发现：按网段探测 SCPI 端口并读取 ``*IDN?``。

设计边界：

- **身份只认 ``*IDN?``**。探测到的 IP/端口只是“本次运行可达的地址”；设备身份仍由
  :func:`domain.instruments.models.make_device_key` 派生的 ``厂商:序列号`` 决定，与
  ``instruments.json`` 里的资产名无关。
- **未知设备同样出现在结果里**：身份可解析但设备表没有的标为
  :data:`DiscoveryStatus.UNREGISTERED`，端口有应答但读不出身份的标为
  :data:`DiscoveryStatus.UNIDENTIFIED`。两者都必须让用户看见，发现动作本身绝不静默
  建档，也不改写设备表。
- 扫描是只读探测：只建立 TCP 连接、发一条 ``*IDN?`` 并关闭，不发送任何会改变仪器
  状态的命令。设备表写入由调用方在用户确认后单独完成。
- 网络实现与模拟实现共用 :class:`IdnProbe` 协议，因此离线测试与现场扫描走同一条
  解析、匹配与结果汇总路径，不需要第二套“假扫描”。

**探测端口与连接地址是两件事**：探测走 5025/5555 的 SCPI Raw 端口，只为读出身份；
写入配置的连接地址统一是 ``TCPIP0::<ip>::inst0::INSTR``（VXI-11）。原因是测量链路
由 :class:`instrument.transport.visa_transport.VisaScpiTransport` 用
``resource.query()`` 收发，而 SOCKET 资源在未配置读终止符时会一直读到超时。探测端口
因此作为元数据保留在 ``port`` / ``idn_port`` 里，不参与拼接连接地址。
"""

from __future__ import annotations

import ipaddress
import socket
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Protocol, Sequence

from domain.instruments.models import (
    DeviceStatus,
    InstrumentIdentity,
    normalize_text,
)
from infrastructure.logging.app_logging import get_logger

logger = get_logger(__name__)

SCAN_MODE_NETWORK = "network"
SCAN_MODE_SIMULATED = "simulated"
# 现场已知可读到 *IDN? 的 SCPI Raw 端口：R&S / Ceyear 用 5025，RIGOL DP832A 用 5555。
DEFAULT_IDN_PORTS: tuple[int, ...] = (5025, 5555)
DEFAULT_TIMEOUT_S = 0.6
DEFAULT_MAX_WORKERS = 32
# 单次扫描最多探测的地址数，避免误填 10.0.0.0/8 时把界面卡死几十万个连接。
MAX_HOSTS_PER_SCAN = 1024
# 进度回调上限：一个 /24 × 2 端口就有 508 次完成事件，逐条 emit 会刷满 Qt 事件队列。
MAX_PROGRESS_UPDATES = 100
_IDN_COMMAND = b"*IDN?\n"


class InstrumentDiscoveryError(RuntimeError):
    """扫描参数非法或扫描无法开始（地址、端口或线程池不可用）。"""


class DiscoveryStatus(str, Enum):
    """一次发现中某个地址的身份判定结果。

    与设备表里的资产状态 :class:`domain.instruments.models.DeviceStatus` 分开：
    ``DeviceStatus`` 是“这台资产现在是否可用”，``DiscoveryStatus`` 是“这次扫描看到了
    什么”。混用会让“端口有应答但身份读不出来”被误统计成“未登记设备”。
    """

    KNOWN = "known"  # 身份可解析，且命中设备表
    UNREGISTERED = "unregistered"  # 身份可解析，但设备表没有这台设备
    UNIDENTIFIED = "unidentified"  # 端口有应答，但 *IDN? 为空或无法解析


@dataclass(frozen=True)
class DiscoveredDevice:
    """一次扫描发现的设备（含未知设备）。

    ``known_name`` 是设备表中已登记资产的名称（``型号-序号``）；未登记设备为
    ``None``，界面据此显示“未知设备”。``identity`` 为空表示端口有应答但身份读不出。
    """

    ip: str
    port: int
    idn: str | None
    identity: InstrumentIdentity | None
    known_name: str | None
    status: DeviceStatus
    error: str | None = None

    @property
    def device_key(self) -> str | None:
        return self.identity.device_key if self.identity else None

    @property
    def has_identity(self) -> bool:
        """是否解析出了设备身份（序列号）。这是能否绑定到角色的前提。"""
        return self.identity is not None

    @property
    def is_known(self) -> bool:
        return self.known_name is not None

    @property
    def is_unknown_identity(self) -> bool:
        """身份可解析但设备表没有登记：界面上是“未知设备/未登记”。"""
        return self.identity is not None and self.known_name is None

    @property
    def is_unidentified(self) -> bool:
        """端口有应答但身份读不出来：不能当作“未登记设备”统计。"""
        return self.identity is None

    @property
    def discovery_status(self) -> DiscoveryStatus:
        if self.identity is None:
            return DiscoveryStatus.UNIDENTIFIED
        return DiscoveryStatus.KNOWN if self.is_known else DiscoveryStatus.UNREGISTERED

    @property
    def visa_address(self) -> str:
        """本次扫描得到的连接地址，形如 ``TCPIP0::<ip>::inst0::INSTR``。

        探测端口不参与拼接，原因见模块文档：测量链路用 ``resource.query()`` 收发，
        SOCKET 资源需要额外的读终止符配置，未经验证不能写进配置。
        """
        return f"TCPIP0::{self.ip}::inst0::INSTR"

    @property
    def display_name(self) -> str:
        if self.known_name:
            return self.known_name
        if self.identity is not None:
            return f"未知设备 ({self.identity.model})"
        return "未知设备"

    def to_dict(self) -> dict[str, Any]:
        identity = self.identity
        return {
            "ip": self.ip,
            "port": self.port,
            "idn_port": self.port,
            "idn": self.idn,
            "vendor": identity.vendor if identity else None,
            "model": identity.model if identity else None,
            "serial": identity.serial if identity else None,
            "device_key": self.device_key,
            "known_name": self.known_name,
            "status": self.status.value,
            "discovery_status": self.discovery_status.value,
            "error": self.error,
            "visa_address": self.visa_address,
        }


@dataclass(frozen=True)
class ScanResult:
    """一次扫描的完整结果：发现的设备 + 扫描范围说明。

    ``failures`` 是需要用户处理的问题（网段非法、取消、探测异常）；
    ``notices`` 是需要用户知情的判定（同一地址多个身份、同一设备多个地址）。
    """

    devices: tuple[DiscoveredDevice, ...] = field(default_factory=tuple)
    subnet: str = ""
    ports: tuple[int, ...] = DEFAULT_IDN_PORTS
    mode: str = SCAN_MODE_NETWORK
    scanned_hosts: int = 0
    failures: tuple[str, ...] = field(default_factory=tuple)
    notices: tuple[str, ...] = field(default_factory=tuple)

    @property
    def known(self) -> tuple[DiscoveredDevice, ...]:
        return tuple(device for device in self.devices if device.is_known)

    @property
    def unknown(self) -> tuple[DiscoveredDevice, ...]:
        """身份可解析但未登记的设备；不含无法识别身份的地址。"""
        return tuple(device for device in self.devices if device.is_unknown_identity)

    @property
    def unidentified(self) -> tuple[DiscoveredDevice, ...]:
        return tuple(device for device in self.devices if device.is_unidentified)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subnet": self.subnet,
            "ports": list(self.ports),
            "mode": self.mode,
            "scanned_hosts": self.scanned_hosts,
            "devices": [device.to_dict() for device in self.devices],
            "failures": list(self.failures),
            "notices": list(self.notices),
        }


class IdnProbe(Protocol):
    """探测单个地址：返回 ``*IDN?`` 响应，端口不可达时抛异常。"""

    def query_idn(self, host: str, port: int, timeout_s: float) -> str: ...


def _read_idn(host: str, port: int, timeout_s: float) -> str:
    """用一次性 TCP 连接读取 ``*IDN?``；任何 IO 失败都由调用方计入未响应。"""
    with socket.create_connection((host, port), timeout=timeout_s) as connection:
        connection.settimeout(timeout_s)
        connection.sendall(_IDN_COMMAND)
        chunks: list[bytes] = []
        while True:
            chunk = connection.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break
    return b"".join(chunks).decode("ascii", errors="replace").strip()


class TcpIdnProbe:
    """真实网络探测：临时 TCP 连接 + SCPI ``*IDN?``，不创建 VISA 会话。"""

    def query_idn(self, host: str, port: int, timeout_s: float) -> str:
        return _read_idn(host, port, timeout_s)


class SimulatedIdnProbe:
    """离线演示探测：按 ``(host, port)`` 返回预置响应，其余地址按不可达处理。

    模拟数据只用于没有设备网络的开发机；它走与真实扫描完全相同的解析、匹配和结果
    汇总路径，因此界面行为与现场一致。
    """

    def __init__(self, responses: Mapping[tuple[str, int], str]) -> None:
        self.responses = dict(responses)
        self.queried: list[tuple[str, int]] = []

    def query_idn(self, host: str, port: int, timeout_s: float) -> str:
        self.queried.append((host, port))
        try:
            return self.responses[(host, port)]
        except KeyError:
            raise TimeoutError(f"模拟设备 {host}:{port} 无响应") from None


def parse_scan_subnet(value: Any) -> ipaddress.IPv4Network:
    """把一个网段文本解析为 IPv4 网络；接受 ``192.168.1.0/24`` 与单个地址。

    单个地址按 ``/24`` 解释（现场通常是 ``192.168.1.x`` 的仪器网段），但显式掩码优先。
    空值、非 IPv4 或超出单次扫描上限都明确失败，不做静默降级。
    """
    text = normalize_text(value)
    if not text:
        raise InstrumentDiscoveryError("扫描网段不能为空")
    if "/" not in text:
        try:
            address = ipaddress.IPv4Address(text)
        except ipaddress.AddressValueError as error:
            raise InstrumentDiscoveryError(f"无法识别的扫描网段: {text}") from error
        network = ipaddress.IPv4Network(f"{address}/24", strict=False)
    else:
        try:
            network = ipaddress.IPv4Network(text, strict=False)
        except (ipaddress.AddressValueError, ipaddress.NetmaskValueError) as error:
            raise InstrumentDiscoveryError(f"无法识别的扫描网段: {text}") from error
    if network.num_addresses - 2 > MAX_HOSTS_PER_SCAN:
        raise InstrumentDiscoveryError(
            f"扫描网段过大（{network.num_addresses - 2} 个地址，上限 {MAX_HOSTS_PER_SCAN}）；"
            "请缩小掩码范围"
        )
    return network


def _hosts(network: ipaddress.IPv4Network) -> list[str]:
    """返回可探测主机地址；``/31``、``/32`` 这类没有可用主机段的网络按全量处理。"""
    if network.prefixlen >= 31:
        return [str(address) for address in network]
    return [str(address) for address in network.hosts()]


def _ip_sort_key(ip: str) -> tuple[int, int, int, int]:
    try:
        packed = ipaddress.IPv4Address(ip).packed
    except ipaddress.AddressValueError:
        return (999, 999, 999, 999)
    return (packed[0], packed[1], packed[2], packed[3])


def _preference(device: DiscoveredDevice) -> tuple[int, int, int]:
    """同一地址命中多条结果时的确定性取舍：能读出身份优先，其次已登记，最后端口小优先。"""
    return (
        0 if device.identity is not None else 1,
        0 if device.is_known else 1,
        device.port,
    )


def _resolve_devices(
    devices: list[DiscoveredDevice],
) -> tuple[tuple[DiscoveredDevice, ...], tuple[str, ...]]:
    """同一 IP 只留一条、同一身份只留一个地址，并返回需要用户知情的判定。

    结果不依赖探测完成顺序：先按 :func:`_preference` 选每个 IP 的代表，再按 IP 排序，
    同一 ``device_key`` 只保留第一个地址。
    """
    by_ip: dict[str, DiscoveredDevice] = {}
    for device in devices:
        current = by_ip.get(device.ip)
        if current is None or _preference(device) < _preference(current):
            by_ip[device.ip] = device

    notices: list[str] = []
    identities_by_ip: dict[str, set[str]] = {}
    for device in devices:
        if device.device_key is not None:
            identities_by_ip.setdefault(device.ip, set()).add(device.device_key)
    for ip in sorted(identities_by_ip, key=_ip_sort_key):
        keys = identities_by_ip[ip]
        if len(keys) > 1:
            notices.append(
                f"{ip} 的不同端口返回了不同身份，已保留 {by_ip[ip].display_name}，请人工确认"
            )

    unique: list[DiscoveredDevice] = []
    address_by_key: dict[str, str] = {}
    for ip in sorted(by_ip, key=_ip_sort_key):
        device = by_ip[ip]
        key = device.device_key
        if key is not None and key in address_by_key:
            notices.append(
                f"{device.display_name} 同时出现在 {address_by_key[key]} 和 {ip}，"
                f"已保留 {address_by_key[key]}"
            )
            continue
        if key is not None:
            address_by_key[key] = ip
        unique.append(device)
    unique.sort(key=lambda item: (not item.is_known, _ip_sort_key(item.ip)))
    return tuple(unique), tuple(notices)


class InstrumentScanner:
    """按网段并发的 SCPI 发现器。

    ``registry`` 只做只读查询：已登记设备按 ``厂商:序列号`` 匹配资产名，未匹配的设备
    以未知身份返回。扫描不会写设备表、不会打开 VISA 会话，也不会发送除 ``*IDN?``
    之外的任何命令。

    取消通过 :meth:`request_cancel` 发起，可从任意线程调用；扫描会在下一个结果边界
    停止、丢弃排队中的探测，只等待已启动的少数探测按自身超时结束。取消状态在扫描
    开始前设置同样生效：不会建立任何连接。
    """

    def __init__(
        self,
        *,
        registry: Any = None,
        probe: IdnProbe | None = None,
        ports: Sequence[int] = DEFAULT_IDN_PORTS,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_workers: int = DEFAULT_MAX_WORKERS,
        mode: str = SCAN_MODE_NETWORK,
        cancel: threading.Event | None = None,
    ) -> None:
        if not ports:
            raise InstrumentDiscoveryError("至少需要一个 SCPI 探测端口")
        for port in ports:
            if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
                raise InstrumentDiscoveryError(f"非法端口: {port!r}")
        if not isinstance(timeout_s, (int, float)) or isinstance(timeout_s, bool) or timeout_s <= 0:
            raise InstrumentDiscoveryError("探测超时必须是正数秒")
        if not isinstance(max_workers, int) or isinstance(max_workers, bool) or max_workers < 1:
            raise InstrumentDiscoveryError("并发数必须是正整数")
        self.registry = registry
        self.probe = probe or TcpIdnProbe()
        self.ports = tuple(ports)
        self.timeout_s = float(timeout_s)
        self.max_workers = max_workers
        self.mode = mode
        self._cancel = cancel if cancel is not None else threading.Event()

    def request_cancel(self) -> None:
        """请求停止扫描；已启动的单次连接会按自身超时结束。"""
        self._cancel.set()

    def set_cancel_event(self, event: threading.Event) -> None:
        """替换取消信号。

        界面在扫描线程启动前注入自己的事件，因此“关闭页面时取消”与“扫描开始前取消”
        走同一条路径；事件已经置位时不会被这里清掉。
        """
        if not isinstance(event, threading.Event):
            raise InstrumentDiscoveryError("取消信号必须是 threading.Event")
        self._cancel = event

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def scan(self, subnet: Any, *, progress: Any = None) -> ScanResult:
        """扫描网段，返回发现结果；``progress(已完成, 总数)`` 可用于界面进度。"""
        network = parse_scan_subnet(subnet)
        hosts = _hosts(network)
        targets = [(host, port) for host in hosts for port in self.ports]
        total = len(targets)
        report = self._progress_reporter(progress, total)
        devices: list[DiscoveredDevice] = []
        failures: list[str] = []

        if self._cancel.is_set():
            # 取消可能发生在扫描开始之前（界面已关闭、用户已放弃）：一个连接都不建立。
            return ScanResult(
                subnet=str(network),
                ports=self.ports,
                mode=self.mode,
                scanned_hosts=len(hosts),
                failures=("扫描已被取消，未开始探测",),
            )

        report(0, total)
        done = 0
        executor = ThreadPoolExecutor(max_workers=self.max_workers)
        futures = {
            executor.submit(self._probe_one, host, port): (host, port)
            for host, port in targets
        }
        try:
            for future in as_completed(futures):
                done += 1
                host, port = futures[future]
                try:
                    device = future.result()
                except (OSError, TimeoutError) as error:
                    # _probe_one 已处理常规 IO 失败；这里兜住探测实现自行抛出的同类异常，
                    # 不让单个地址中断整轮扫描，也不把编程错误伪装成“设备不可达”。
                    logger.debug("扫描地址失败 %s:%s: %s", host, port, error)
                    failures.append(f"{host}:{port} 探测失败: {error}")
                    device = None
                if device is not None:
                    devices.append(device)
                report(done, total)
                if self._cancel.is_set():
                    failures.append("扫描已被用户取消，结果不完整")
                    break
        finally:
            for pending in futures:
                pending.cancel()
            # 排队中的探测直接丢弃；只等已启动的少数任务按自身超时收尾。
            executor.shutdown(wait=True, cancel_futures=True)

        resolved, notices = _resolve_devices(devices)
        return ScanResult(
            devices=resolved,
            subnet=str(network),
            ports=self.ports,
            mode=self.mode,
            scanned_hosts=len(hosts),
            failures=tuple(failures),
            notices=notices,
        )

    @staticmethod
    def _progress_reporter(progress: Any, total: int) -> Any:
        """把逐条完成事件压到最多 :data:`MAX_PROGRESS_UPDATES` 次回调。"""
        if progress is None:
            return lambda done, total: None
        # 向上取整：保证回调次数不超过 MAX_PROGRESS_UPDATES（含最后一次 100%）。
        step = max(1, -(-total // MAX_PROGRESS_UPDATES)) if total else 1
        last = -1

        def report(done: int, total: int) -> None:
            nonlocal last
            if done == 0 or done == total or done - last >= step:
                last = done
                progress(done, total)

        return report

    def _probe_one(self, host: str, port: int) -> DiscoveredDevice | None:
        """探测一个地址；不可达返回 ``None``（不是错误，扫描本来就会大量空响应）。"""
        try:
            response = self.probe.query_idn(host, port, self.timeout_s)
        except (OSError, TimeoutError) as error:
            logger.debug("扫描地址不可达 %s:%s: %s", host, port, error)
            return None
        text = normalize_text(response)
        if not text:
            return DiscoveredDevice(
                ip=host,
                port=port,
                idn=None,
                identity=None,
                known_name=None,
                status=DeviceStatus.UNREACHABLE,
                error="端口有应答但 *IDN? 为空",
            )
        try:
            identity = InstrumentIdentity.from_idn(text)
        except ValueError as error:
            # 有应答但读不出身份：保留在结果里，让用户看到“这个地址上有东西但不是可识别仪器”。
            return DiscoveredDevice(
                ip=host,
                port=port,
                idn=text,
                identity=None,
                known_name=None,
                status=DeviceStatus.UNREACHABLE,
                error=str(error),
            )
        known_name = self._known_name(identity)
        return DiscoveredDevice(
            ip=host,
            port=port,
            idn=text,
            identity=identity,
            known_name=known_name,
            # 未登记设备的资产状态是“未知”，不是“在线”：在线与否要等登记并确认。
            status=DeviceStatus.ONLINE if known_name else DeviceStatus.UNKNOWN,
        )

    def _known_name(self, identity: InstrumentIdentity) -> str | None:
        """在只读设备表里查设备名；没有设备表或未登记时返回 ``None``。"""
        if self.registry is None:
            return None
        instrument = self.registry.get_by_device_key(identity.device_key)
        return instrument.name if instrument is not None else None


def build_scanner(
    *,
    registry: Any = None,
    mode: str = SCAN_MODE_SIMULATED,
    responses: Mapping[tuple[str, int], str] | None = None,
    ports: Sequence[int] = DEFAULT_IDN_PORTS,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_workers: int = DEFAULT_MAX_WORKERS,
    cancel: threading.Event | None = None,
) -> InstrumentScanner:
    """按模式构造扫描器；模拟模式必须显式给出响应表，避免“假扫描”被当成现场结果。"""
    if mode == SCAN_MODE_SIMULATED:
        return InstrumentScanner(
            registry=registry,
            probe=SimulatedIdnProbe(responses or {}),
            ports=ports,
            timeout_s=timeout_s,
            max_workers=max_workers,
            mode=SCAN_MODE_SIMULATED,
            cancel=cancel,
        )
    if mode != SCAN_MODE_NETWORK:
        raise InstrumentDiscoveryError(f"未知扫描模式: {mode!r}")
    return InstrumentScanner(
        registry=registry,
        ports=ports,
        timeout_s=timeout_s,
        max_workers=max_workers,
        cancel=cancel,
    )


def summarize_unknown(devices: Iterable[DiscoveredDevice]) -> tuple[str, ...]:
    """把未知设备汇总成界面可直接展示的一行说明。"""
    return tuple(
        f"{device.display_name} @ {device.ip}:{device.port}"
        for device in devices
        if device.is_unknown_identity
    )


__all__ = [
    "DEFAULT_IDN_PORTS",
    "DEFAULT_MAX_WORKERS",
    "DEFAULT_TIMEOUT_S",
    "DiscoveredDevice",
    "DiscoveryStatus",
    "IdnProbe",
    "InstrumentDiscoveryError",
    "InstrumentScanner",
    "MAX_HOSTS_PER_SCAN",
    "MAX_PROGRESS_UPDATES",
    "SCAN_MODE_NETWORK",
    "SCAN_MODE_SIMULATED",
    "ScanResult",
    "SimulatedIdnProbe",
    "TcpIdnProbe",
    "build_scanner",
    "parse_scan_subnet",
    "summarize_unknown",
]
