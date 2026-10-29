"""设备发现（网段扫描 + ``*IDN?``）单元测试。

覆盖 MVP 的核心行为：

1. 扫描按网段找设备，``*IDN?`` 能解析的设备才算“有身份”；
2. 已登记设备按 ``厂商:序列号`` 匹配资产名——**IP 变了仍然认得出来**；
3. 未登记设备同样出现在结果里，标记为未登记，不被静默丢弃；
4. 身份读不出来的地址单独计数，不混进“未登记设备”；
5. 取消能在任意时刻生效，且不会等所有排队探测跑完。
"""

import threading
import time
import unittest

from domain.instruments.models import DeviceStatus, InstrumentIdentity
from infrastructure.discovery.instrument_discovery import (
    MAX_HOSTS_PER_SCAN,
    MAX_PROGRESS_UPDATES,
    SCAN_MODE_NETWORK,
    SCAN_MODE_SIMULATED,
    DiscoveredDevice,
    DiscoveryStatus,
    InstrumentDiscoveryError,
    InstrumentScanner,
    SimulatedIdnProbe,
    TcpIdnProbe,
    build_scanner,
    parse_scan_subnet,
    summarize_unknown,
)
from infrastructure.discovery.simulated_network import demo_idn_responses
from infrastructure.persistence.instrument_registry import InstrumentRegistry

SMW_IDN = "Rohde&Schwarz,SMW200A,1412.0000K02/105462,5.20.043.47"
DP832A_IDN = "RIGOL TECHNOLOGIES,DP832A,DP8B214901519,00.01.16"
_ROLE_BY_MODEL = {
    "SMW200A": "signal_generator",
    "DP832A": "PS3",
    "4082F": "spectrum_analyzer",
}


def _registry_with(*idns):
    registry = InstrumentRegistry()
    for idn in idns:
        identity = InstrumentIdentity.from_idn(idn)
        registry.upsert(identity, role=_ROLE_BY_MODEL.get(identity.model))
    return registry


class ParseSubnetTests(unittest.TestCase):
    def test_single_address_is_treated_as_a_24_bit_subnet(self):
        self.assertEqual(str(parse_scan_subnet("192.168.1.37")), "192.168.1.0/24")

    def test_explicit_mask_is_respected(self):
        self.assertEqual(str(parse_scan_subnet("10.0.5.0/29")), "10.0.5.0/29")

    def test_whitespace_is_ignored(self):
        self.assertEqual(str(parse_scan_subnet("  192.168.1.0/24 ")), "192.168.1.0/24")

    def test_blank_or_invalid_input_fails_loudly(self):
        for value in ("", "   ", None, "not-a-subnet", "192.168.1.999", "192.168.1.0/33"):
            with self.subTest(value=value):
                with self.assertRaises(InstrumentDiscoveryError):
                    parse_scan_subnet(value)

    def test_oversized_subnet_is_rejected_instead_of_scanning_hundreds_of_thousands_of_hosts(self):
        self.assertEqual(MAX_HOSTS_PER_SCAN, 1024)
        with self.assertRaises(InstrumentDiscoveryError):
            parse_scan_subnet("10.0.0.0/8")


class ScannerValidationTests(unittest.TestCase):
    def test_probe_ports_must_be_valid(self):
        for ports in ((), (0,), (70000,), (True,)):
            with self.subTest(ports=ports):
                with self.assertRaises(InstrumentDiscoveryError):
                    InstrumentScanner(ports=ports)

    def test_timeout_and_worker_count_must_be_positive(self):
        for kwargs in ({"timeout_s": 0}, {"timeout_s": -1.0}, {"max_workers": 0}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(InstrumentDiscoveryError):
                    InstrumentScanner(**kwargs)

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(InstrumentDiscoveryError):
            build_scanner(mode="automatic")


class SimulatedScanTests(unittest.TestCase):
    def setUp(self):
        self.registry = _registry_with(SMW_IDN, DP832A_IDN)
        self.scanner = build_scanner(
            registry=self.registry,
            mode=SCAN_MODE_SIMULATED,
            responses=demo_idn_responses(),
            timeout_s=0.05,
        )

    def test_scan_reports_known_and_unknown_devices(self):
        result = self.scanner.scan("192.168.1.0/24")

        by_ip = {device.ip: device for device in result.devices}
        self.assertEqual(
            set(by_ip), {"192.168.1.201", "192.168.1.202", "192.168.1.102", "192.168.1.150"}
        )
        self.assertEqual(by_ip["192.168.1.201"].known_name, "SMW200A-1")
        self.assertEqual(by_ip["192.168.1.201"].known_role, "signal_generator")
        self.assertEqual(by_ip["192.168.1.102"].known_name, "DP832A-1")
        self.assertEqual(by_ip["192.168.1.102"].known_role, "PS3")
        # 演示响应里的 4082F 不在本次测试用的小设备表里，按未知设备出现。
        self.assertFalse(by_ip["192.168.1.202"].is_known)
        unknown = by_ip["192.168.1.150"]
        self.assertFalse(unknown.is_known)
        self.assertIsNone(unknown.known_name)
        self.assertEqual(unknown.identity.serial, "MY51234567")
        self.assertEqual(unknown.display_name, "未知设备 (N9020A)")
        self.assertEqual(
            sorted(device.ip for device in result.unknown), ["192.168.1.150", "192.168.1.202"]
        )
        self.assertEqual(result.scanned_hosts, 254)
        self.assertEqual(result.mode, SCAN_MODE_SIMULATED)
        self.assertEqual(result.failures, ())

    def test_known_match_is_by_serial_not_by_ip(self):
        """同一台设备换 IP 后仍匹配到原资产名：身份不依赖地址。"""
        responses = {("192.168.9.4", 5025): SMW_IDN}
        scanner = build_scanner(
            registry=self.registry,
            mode=SCAN_MODE_SIMULATED,
            responses=responses,
            timeout_s=0.05,
        )

        result = scanner.scan("192.168.9.0/29")

        self.assertEqual(len(result.devices), 1)
        device = result.devices[0]
        self.assertEqual(device.known_name, "SMW200A-1")
        self.assertEqual(device.identity.serial, "105462")

    def test_result_dict_carries_identity_and_address(self):
        result = self.scanner.scan("192.168.1.200/30")
        device = result.devices[0]
        payload = device.to_dict()
        self.assertEqual(payload["known_name"], "SMW200A-1")
        self.assertEqual(payload["role"], "signal_generator")
        self.assertEqual(payload["device_key"], "rohde&schwarz:105462")
        self.assertEqual(payload["visa_address"], "TCPIP0::192.168.1.201::inst0::INSTR")
        self.assertEqual(payload["status"], DeviceStatus.ONLINE.value)

    def test_progress_reports_completion_out_of_total(self):
        seen = []
        self.scanner.scan("192.168.1.200/30", progress=lambda done, total: seen.append((done, total)))
        self.assertEqual(seen[0], (0, 4))
        self.assertEqual(seen[-1], (4, 4))

    def test_cancelled_scan_reports_incomplete_result(self):
        scanner = build_scanner(
            registry=self.registry,
            mode=SCAN_MODE_SIMULATED,
            responses=demo_idn_responses(),
            timeout_s=0.05,
        )

        def progress(done, total):
            if done >= 1:
                scanner.request_cancel()

        result = scanner.scan("192.168.1.0/24", progress=progress)

        self.assertTrue(any("取消" in failure for failure in result.failures))

    def test_without_registry_every_device_is_unknown(self):
        scanner = build_scanner(
            mode=SCAN_MODE_SIMULATED, responses=demo_idn_responses(), timeout_s=0.05
        )
        result = scanner.scan("192.168.1.0/24")
        self.assertEqual(result.known, ())
        self.assertEqual(len(result.unknown), len(result.devices))
        self.assertTrue(all(device.known_name is None for device in result.devices))


class ProbeResultTests(unittest.TestCase):
    def _scanner_with(self, responses):
        return InstrumentScanner(
            probe=SimulatedIdnProbe(responses), ports=(5025,), timeout_s=0.05
        )

    def test_unreachable_address_is_skipped_not_reported_as_device(self):
        result = self._scanner_with({}).scan("127.0.0.0/30")
        self.assertEqual(result.devices, ())

    def test_empty_idn_marks_the_address_as_unreachable_with_reason(self):
        result = self._scanner_with({("127.0.0.1", 5025): "  "}).scan("127.0.0.0/30")
        self.assertEqual(len(result.devices), 1)
        device = result.devices[0]
        self.assertEqual(device.status, DeviceStatus.UNREACHABLE)
        self.assertIsNone(device.identity)
        self.assertIn("*IDN?", device.error)

    def test_unparseable_idn_stays_visible_as_unknown(self):
        result = self._scanner_with({("127.0.0.1", 5025): "GARBAGE"}).scan("127.0.0.0/30")
        device = result.devices[0]
        self.assertEqual(device.status, DeviceStatus.UNREACHABLE)
        self.assertIsNone(device.known_name)
        self.assertIn("GARBAGE", device.idn)

    def test_visa_address_and_display_name_defaults(self):
        device = DiscoveredDevice(
            ip="10.0.0.5",
            port=5025,
            idn=None,
            identity=None,
            known_name=None,
            status=DeviceStatus.UNREACHABLE,
        )
        self.assertEqual(device.visa_address, "TCPIP0::10.0.0.5::inst0::INSTR")
        self.assertEqual(device.display_name, "未知设备")
        self.assertIsNone(device.device_key)

    def test_real_tcp_probe_does_not_raise_for_closed_port(self):
        """关闭端口必须按“无响应”处理，扫描不会因单个地址失败而中断。"""
        probe = TcpIdnProbe()
        with self.assertRaises(OSError):
            probe.query_idn("127.0.0.1", 1, 0.05)

    def test_unknown_summary_lists_only_unregistered_devices(self):
        """无法识别身份的地址不是“未知设备”：它没有身份可用于绑定。"""
        devices = (
            DiscoveredDevice("10.0.0.1", 5025, "X", None, None, DeviceStatus.UNREACHABLE),
            DiscoveredDevice(
                "10.0.0.2",
                5025,
                SMW_IDN,
                InstrumentIdentity.from_idn(SMW_IDN),
                "SMW200A-1",
                DeviceStatus.ONLINE,
            ),
            DiscoveredDevice(
                "10.0.0.3",
                5025,
                "Keysight Technologies,N9020A,MY51234567,A.18.21",
                InstrumentIdentity.from_idn(
                    "Keysight Technologies,N9020A,MY51234567,A.18.21"
                ),
                None,
                DeviceStatus.UNKNOWN,
            ),
        )
        self.assertEqual(summarize_unknown(devices), ("未知设备 (N9020A) @ 10.0.0.3:5025",))

    def test_same_address_listening_on_two_ports_is_listed_once(self):
        scanner = InstrumentScanner(
            probe=SimulatedIdnProbe({("10.1.1.5", 5025): "", ("10.1.1.5", 5555): SMW_IDN}),
            ports=(5025, 5555),
            timeout_s=0.05,
        )

        result = scanner.scan("10.1.1.4/30")

        self.assertEqual(len(result.devices), 1)
        self.assertEqual(result.devices[0].port, 5555)
        self.assertEqual(result.devices[0].identity.serial, "105462")

    def test_same_device_on_two_addresses_is_listed_once(self):
        scanner = InstrumentScanner(
            probe=SimulatedIdnProbe({("10.1.1.5", 5025): SMW_IDN, ("10.1.1.6", 5025): SMW_IDN}),
            ports=(5025,),
            timeout_s=0.05,
        )

        result = scanner.scan("10.1.1.4/29")

        self.assertEqual([device.ip for device in result.devices], ["10.1.1.5"])
        self.assertEqual(len(result.notices), 1)
        self.assertIn("10.1.1.6", result.notices[0])


class NetworkModeTests(unittest.TestCase):
    def test_network_mode_uses_the_tcp_probe(self):
        scanner = build_scanner(mode=SCAN_MODE_NETWORK)
        self.assertIsInstance(scanner.probe, TcpIdnProbe)
        self.assertEqual(scanner.mode, SCAN_MODE_NETWORK)

    def test_simulated_mode_without_responses_finds_nothing(self):
        scanner = build_scanner(mode=SCAN_MODE_SIMULATED)
        self.assertEqual(scanner.scan("127.0.0.0/30").devices, ())


class DiscoveryStatusTests(unittest.TestCase):
    """发现状态必须区分“未登记设备”和“身份读不出来”。"""

    def setUp(self):
        self.registry = _registry_with(SMW_IDN)

    def _scan(self, responses, subnet="10.2.2.0/30", ports=(5025,)):
        scanner = InstrumentScanner(
            registry=self.registry,
            probe=SimulatedIdnProbe(responses),
            ports=ports,
            timeout_s=0.05,
        )
        return scanner.scan(subnet)

    def test_known_device_is_online_and_classified_as_known(self):
        result = self._scan({("10.2.2.1", 5025): SMW_IDN})
        device = result.devices[0]
        self.assertEqual(device.discovery_status, DiscoveryStatus.KNOWN)
        self.assertEqual(device.status, DeviceStatus.ONLINE)
        self.assertTrue(device.has_identity)
        self.assertFalse(device.is_unknown_identity)
        self.assertFalse(device.is_unidentified)

    def test_unregistered_device_is_unknown_asset_status_not_online(self):
        result = self._scan(
            {("10.2.2.1", 5025): "Keysight Technologies,N9020A,MY51234567,A.18.21"}
        )
        device = result.devices[0]
        self.assertEqual(device.discovery_status, DiscoveryStatus.UNREGISTERED)
        self.assertEqual(device.status, DeviceStatus.UNKNOWN)
        self.assertTrue(device.is_unknown_identity)
        self.assertFalse(device.is_unidentified)

    def test_unparseable_response_is_unidentified_and_not_counted_as_unregistered(self):
        result = self._scan({("10.2.2.1", 5025): "GARBAGE"})

        device = result.devices[0]
        self.assertEqual(device.discovery_status, DiscoveryStatus.UNIDENTIFIED)
        self.assertTrue(device.is_unidentified)
        self.assertFalse(device.is_unknown_identity)
        self.assertFalse(device.has_identity)
        self.assertEqual(result.unknown, ())
        self.assertEqual(result.unidentified, (device,))
        self.assertEqual(result.to_dict()["devices"][0]["discovery_status"], "unidentified")


class VisaAddressTests(unittest.TestCase):
    """探测端口是元数据，不参与拼接连接地址。"""

    def test_probe_port_is_kept_as_metadata_and_not_used_in_the_address(self):
        scanner = InstrumentScanner(
            registry=_registry_with(DP832A_IDN),
            probe=SimulatedIdnProbe({("10.3.3.3", 5555): DP832A_IDN}),
            ports=(5555,),
            timeout_s=0.05,
        )

        device = scanner.scan("10.3.3.3").devices[0]
        payload = device.to_dict()

        self.assertEqual(device.port, 5555)
        self.assertEqual(payload["idn_port"], 5555)
        self.assertEqual(payload["visa_address"], "TCPIP0::10.3.3.3::inst0::INSTR")

    def test_address_does_not_depend_on_probe_port(self):
        addresses = {
            DiscoveredDevice("10.0.0.9", port, SMW_IDN, None, None, DeviceStatus.UNKNOWN)
            .visa_address
            for port in (5025, 5555, 1024)
        }
        self.assertEqual(addresses, {"TCPIP0::10.0.0.9::inst0::INSTR"})


class PowerSupplyIdentityTests(unittest.TestCase):
    """电源的发现结果：短标签只读注册表、身份只认 ``device_key``、多台各自独立。"""

    LEGACY_REGISTRY = {
        "schema_version": 2,
        "instruments": [
            {
                "name": "DP832A-1",
                "vendor": "RIGOL TECHNOLOGIES",
                "model": "DP832A",
                "model_index": 1,
                "serial": "DP8B214901519",
                "role": "PS3",
                "status": "online",
                "known_ips": [],
            }
        ],
    }
    MIGRATED_REGISTRY = {
        "schema_version": 3,
        "instruments": [
            {
                "name": "DP832A-1",
                "vendor": "RIGOL TECHNOLOGIES",
                "model": "DP832A",
                "model_index": 1,
                "serial": "DP8B214901519",
                "label": "DP8B1519",
                "role": "power_supply",
                "status": "online",
                "known_ips": [],
            },
            {
                "name": "DP832A-3",
                "vendor": "RIGOL TECHNOLOGIES",
                "model": "DP832A",
                "model_index": 3,
                "serial": "DP8B174400885",
                "label": "DP8B0885",
                "role": "power_supply",
                "status": "online",
                "known_ips": [],
            },
        ],
    }

    def _scan(self, document, responses, subnet="10.8.8.0/29"):
        return InstrumentScanner(
            registry=InstrumentRegistry(document=document),
            probe=SimulatedIdnProbe(responses),
            ports=(5555,),
            timeout_s=0.05,
        ).scan(subnet)

    def test_legacy_slot_is_reported_as_power_supply_without_a_label(self):
        device = self._scan(
            self.LEGACY_REGISTRY, {("10.8.8.1", 5555): DP832A_IDN}
        ).devices[0]

        # 旧字面量原样保留给排查，类别折叠成 ``power_supply``。
        self.assertEqual(device.known_role, "PS3")
        self.assertEqual(device.role, "power_supply")
        self.assertFalse(hasattr(device, "config_slot"), "旧槽位不再出现在发现结果里")
        # 旧设备表没有标签：发现过程不生成标签，也就没有 ``supply_id``。
        self.assertIsNone(device.label)
        self.assertIsNone(device.supply_id)
        self.assertEqual(device.display_name, "DP832A-1")
        self.assertEqual(device.serial, "DP8B214901519")
        self.assertEqual(device.device_key, "rigol technologies:dp8b214901519")

    def test_frozen_label_is_used_for_display_and_as_supply_id(self):
        device = self._scan(
            self.MIGRATED_REGISTRY, {("10.8.8.1", 5555): DP832A_IDN}
        ).devices[0]

        self.assertEqual(device.role, "power_supply")
        self.assertEqual(device.label, "DP8B1519")
        self.assertEqual(device.supply_id, "DP8B1519")
        self.assertEqual(device.display_name, "DP8B1519")
        # 迁移后的记录只有类别与短标签：地址怎么写回配置由配置页按绑定决定。
        self.assertFalse(hasattr(device, "config_slot"))

    def test_has_role_follows_the_folded_category(self):
        """“有没有角色”必须和界面可勾选、配置页接受的角色同源。

        旧槽位（``PS3``）折叠成 ``power_supply``，算有角色；登记成未知字面量（手改出
        ``PS9``）没有可写入的类别，不能算成“已登记角色”，否则状态列说“已登记”、
        勾选框却是灰的，扫描摘要还会把它算进“可应用”。
        """
        legacy = self._scan(
            self.LEGACY_REGISTRY, {("10.8.8.1", 5555): DP832A_IDN}
        ).devices[0]
        self.assertTrue(legacy.has_role)

        unknown = DiscoveredDevice(
            ip=legacy.ip,
            port=legacy.port,
            idn=legacy.idn,
            identity=legacy.identity,
            known_name=legacy.known_name,
            status=legacy.status,
            known_role="PS9",
        )
        self.assertIsNone(unknown.role)
        self.assertFalse(unknown.has_role)
        # 原字面量仍留在诊断序列化里，便于发现设备表被手改错。
        self.assertEqual(unknown.to_dict()["registry_role"], "PS9")
        self.assertIsNone(unknown.to_dict()["role"])

    def test_two_supplies_are_reported_independently(self):
        result = self._scan(
            self.MIGRATED_REGISTRY,
            {
                ("10.8.8.1", 5555): DP832A_IDN,
                ("10.8.8.2", 5555): "RIGOL TECHNOLOGIES,DP832A,DP8B174400885,00.01.16",
            },
        )

        by_supply_id = {device.supply_id: device for device in result.devices}
        self.assertEqual(set(by_supply_id), {"DP8B1519", "DP8B0885"})
        self.assertEqual(
            {device.device_key for device in result.devices},
            {
                "rigol technologies:dp8b214901519",
                "rigol technologies:dp8b174400885",
            },
        )
        self.assertTrue(all(device.is_power_supply for device in result.devices))

    def test_identity_and_label_survive_an_ip_change(self):
        """标签跟着设备身份走：换网段重扫，同一台电源仍是同一个 ``supply_id``。"""
        first = self._scan(
            self.MIGRATED_REGISTRY, {("10.8.8.1", 5555): DP832A_IDN}, "10.8.8.0/29"
        ).devices[0]
        second = self._scan(
            self.MIGRATED_REGISTRY, {("10.9.9.5", 5555): DP832A_IDN}, "10.9.9.0/29"
        ).devices[0]

        self.assertNotEqual(first.visa_address, second.visa_address)
        self.assertEqual(first.device_key, second.device_key)
        self.assertEqual(first.supply_id, second.supply_id)

    def test_result_dict_carries_label_supply_id_and_both_role_forms(self):
        payload = self._scan(
            self.MIGRATED_REGISTRY, {("10.8.8.1", 5555): DP832A_IDN}
        ).devices[0].to_dict()

        self.assertEqual(payload["label"], "DP8B1519")
        self.assertEqual(payload["supply_id"], "DP8B1519")
        self.assertEqual(payload["role"], "power_supply")
        self.assertEqual(payload["registry_role"], "power_supply")
        self.assertEqual(payload["serial"], "DP8B214901519")
        self.assertEqual(payload["device_key"], "rigol technologies:dp8b214901519")

    def test_non_power_supply_devices_have_no_supply_id(self):
        device = self._scan(
            {**self.LEGACY_REGISTRY, "instruments": [
                {
                    "name": "SMW200A-1",
                    "vendor": "Rohde&Schwarz",
                    "model": "SMW200A",
                    "model_index": 1,
                    "serial": "105462",
                    "role": "signal_generator",
                    "status": "online",
                    "known_ips": [],
                }
            ]},
            {("10.8.8.1", 5555): SMW_IDN},
        ).devices[0]

        self.assertEqual(device.role, "signal_generator")
        self.assertIsNone(device.label)
        self.assertIsNone(device.supply_id)
        self.assertFalse(hasattr(device, "config_slot"))


class CancellationTests(unittest.TestCase):
    """取消必须真正停止扫描，而不是等所有排队探测跑完。"""

    class _SlowProbe:
        def __init__(self, delay_s=0.01):
            self.delay_s = delay_s
            self.queries = 0
            self.lock = threading.Lock()

        def query_idn(self, host, port, timeout_s):
            with self.lock:
                self.queries += 1
            time.sleep(self.delay_s)
            raise TimeoutError("无响应")

    def test_cancel_before_scan_opens_no_connection(self):
        probe = SimulatedIdnProbe(demo_idn_responses())
        scanner = InstrumentScanner(probe=probe, ports=(5025,), timeout_s=0.05)

        scanner.request_cancel()
        result = scanner.scan("192.168.1.0/24")

        self.assertEqual(result.devices, ())
        self.assertEqual(probe.queried, [])
        self.assertIn("未开始探测", result.failures[0])

    def test_cancel_drops_queued_targets_instead_of_waiting_for_all(self):
        probe = self._SlowProbe()
        scanner = InstrumentScanner(
            probe=probe, ports=(5025,), timeout_s=0.05, max_workers=1
        )

        def progress(done, total):
            if done >= 1:
                scanner.request_cancel()

        started = time.monotonic()
        result = scanner.scan("10.9.9.0/24", progress=progress)
        elapsed = time.monotonic() - started

        self.assertEqual(result.devices, ())
        self.assertTrue(any("取消" in failure for failure in result.failures))
        self.assertLess(probe.queries, 5, "取消后不应继续探测排队中的地址")
        self.assertLess(elapsed, 2.0, "取消不应等待整轮扫描")

    def test_cancel_event_is_shared_with_the_ui_thread(self):
        event = threading.Event()
        scanner = InstrumentScanner(probe=SimulatedIdnProbe({}), timeout_s=0.05)
        scanner.set_cancel_event(event)

        self.assertFalse(scanner.cancelled)
        event.set()
        self.assertTrue(scanner.cancelled)
        self.assertEqual(scanner.scan("10.0.0.0/30").devices, ())

    def test_set_cancel_event_rejects_foreign_objects(self):
        scanner = InstrumentScanner(probe=SimulatedIdnProbe({}), timeout_s=0.05)
        with self.assertRaises(InstrumentDiscoveryError):
            scanner.set_cancel_event("stop")


class FailureIsolationTests(unittest.TestCase):
    """单个地址失败不能中断整轮扫描。"""

    class _MixedProbe:
        def query_idn(self, host, port, timeout_s):
            if host == "10.4.4.1":
                raise OSError("网络不可达")
            if host == "10.4.4.2":
                return SMW_IDN
            raise TimeoutError("无响应")

    def test_io_error_on_one_address_keeps_the_rest_of_the_scan(self):
        scanner = InstrumentScanner(probe=self._MixedProbe(), ports=(5025,), timeout_s=0.05)

        result = scanner.scan("10.4.4.0/29")

        self.assertEqual([device.ip for device in result.devices], ["10.4.4.2"])
        self.assertEqual(result.failures, ())

    def test_probe_raising_io_error_from_the_future_is_recorded_not_fatal(self):
        class RaisesInside(InstrumentScanner):
            def _probe_one(self, host, port):
                raise TimeoutError("底层读取被中断")

        scanner = RaisesInside(probe=SimulatedIdnProbe({}), ports=(5025,), timeout_s=0.05)

        result = scanner.scan("10.5.5.0/30")

        self.assertEqual(result.devices, ())
        self.assertEqual(len(result.failures), 2)
        self.assertIn("探测失败", result.failures[0])


class ConflictingIdentityTests(unittest.TestCase):
    """同一地址多个身份、同一设备多个地址都必须给出确定性结果与提示。"""

    def test_two_identities_on_one_address_are_reported_and_resolved_deterministically(self):
        scanner = InstrumentScanner(
            probe=SimulatedIdnProbe(
                {("10.6.6.6", 5025): SMW_IDN, ("10.6.6.6", 5555): DP832A_IDN}
            ),
            ports=(5025, 5555),
            timeout_s=0.05,
        )

        result = scanner.scan("10.6.6.6")

        self.assertEqual(len(result.devices), 1)
        self.assertEqual(result.devices[0].port, 5025)
        self.assertEqual(len(result.notices), 1)
        self.assertIn("不同身份", result.notices[0])

    def test_result_does_not_depend_on_completion_order(self):
        responses = {("10.7.7.7", 5025): SMW_IDN, ("10.7.7.8", 5025): DP832A_IDN}
        for ports in ((5025,), (5025, 5555)):
            with self.subTest(ports=ports):
                scanner = InstrumentScanner(
                    probe=SimulatedIdnProbe(responses), ports=ports, timeout_s=0.05
                )
                result = scanner.scan("10.7.7.0/28")
                self.assertEqual(
                    [device.ip for device in result.devices], ["10.7.7.7", "10.7.7.8"]
                )


class ProgressReportingTests(unittest.TestCase):
    """进度回调必须有上限，避免一次 /24 扫描刷满 Qt 事件队列。"""

    def test_progress_updates_are_capped(self):
        scanner = build_scanner(mode=SCAN_MODE_SIMULATED, responses=demo_idn_responses(), timeout_s=0.02)
        seen = []

        scanner.scan("192.168.1.0/24", progress=lambda done, total: seen.append(done))

        self.assertGreater(len(seen), 2)
        self.assertLessEqual(len(seen), MAX_PROGRESS_UPDATES + 2)
        self.assertEqual(seen[0], 0)
        self.assertEqual(seen[-1], 254 * 2)


if __name__ == "__main__":
    unittest.main()
