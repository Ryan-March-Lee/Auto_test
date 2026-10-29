"""设备表（仪器注册表）单元测试。

覆盖三条核心不变量：

1. 身份只认 ``厂商 + 序列号``，IP 变化不影响归属；
2. 资产名 ``型号-序号`` 与同型号序号一经分配不再变化，且序号只增不复用；
3. 手改 JSON 写错（重复序列号、名称与序号不符、current_ip 与 known_ips 不一致）必须报错。
"""

import json
import tempfile
import unittest
from pathlib import Path

from domain.instruments.models import (
    DeviceStatus,
    InstrumentIdentity,
    build_instrument_name,
    make_device_key,
    normalize_serial,
)
from infrastructure.filesystem.paths import PathService
from infrastructure.persistence.instrument_registry import (
    MAX_KNOWN_IPS,
    SCHEMA_VERSION,
    InstrumentRegistry,
    InstrumentRegistryError,
)

SMW_IDN = "Rohde&Schwarz,SMW200A,1412.0000K02/105462,5.20.043.47"
DP832A_IDN = "RIGOL TECHNOLOGIES,DP832A,DP8B214901519,00.01.16"
CEYEAR_IDN = "Ceyear Technologies,4082F,QZNI000866,1.12.04"


def _document(instruments):
    return {"schema_version": SCHEMA_VERSION, "updated_at": "2026-10-28T14:39:46+08:00",
            "instruments": instruments}


def _entry(**overrides):
    entry = {
        "name": "SMW200A-1",
        "vendor": "Rohde&Schwarz",
        "model": "SMW200A",
        "model_index": 1,
        "serial": "105462",
        "order_no": "1412.0000K02",
        "firmware": "5.20.043.47",
        "options": ["SMW-K526"],
        "status": "online",
        "current_ip": "192.168.1.201",
        "notes": None,
        "first_seen_at": "2026-10-28T14:39:46+08:00",
        "last_seen_at": "2026-10-28T14:39:46+08:00",
        "known_ips": [
            {
                "ip": "192.168.1.201",
                "mac": "00-E0-33-05-C5-FE",
                "idn_port": 5025,
                "scpi_port": 5025,
                "visa_address": "TCPIP0::192.168.1.201::inst0::INSTR",
                "source": "scan",
                "last_seen": "2026-10-28T14:39:46+08:00",
            }
        ],
    }
    entry.update(overrides)
    return entry


class InstrumentIdentityTests(unittest.TestCase):
    def test_r_and_s_identity_splits_order_number_and_serial(self):
        identity = InstrumentIdentity.from_idn(SMW_IDN)
        self.assertEqual(identity.vendor, "Rohde&Schwarz")
        self.assertEqual(identity.model, "SMW200A")
        self.assertEqual(identity.order_no, "1412.0000K02")
        self.assertEqual(identity.serial, "105462")
        self.assertEqual(identity.firmware, "5.20.043.47")
        self.assertEqual(identity.raw_idn, SMW_IDN)

    def test_rigol_identity_without_order_number(self):
        identity = InstrumentIdentity.from_idn(DP832A_IDN)
        self.assertIsNone(identity.order_no)
        self.assertEqual(identity.serial, "DP8B214901519")

    def test_ceyear_identity(self):
        identity = InstrumentIdentity.from_idn(CEYEAR_IDN)
        self.assertEqual(identity.vendor, "Ceyear Technologies")
        self.assertEqual(identity.model, "4082F")
        self.assertEqual(identity.serial, "QZNI000866")

    def test_serial_is_normalized_to_upper_case_without_quotes(self):
        identity = InstrumentIdentity.from_idn('Rigol,dp832a, "dp8b214901519" ,00.01.16')
        self.assertEqual(identity.model, "DP832A")
        self.assertEqual(identity.serial, "DP8B214901519")

    def test_serial_normalization_is_idempotent(self):
        # 引号与空白并存时一轮剥不完；模型自己循环到稳定，保证 serial、device_key
        # 与电源短标签用的是同一个值。
        cases = {
            ' " dp8b214901519 " ': "DP8B214901519",
            '"105462"': "105462",
            "  105462  ": "105462",
            "DP8B 214901519": "DP8B 214901519",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_serial(raw), expected)
                self.assertEqual(normalize_serial(expected), expected)

    def test_incomplete_identity_is_rejected(self):
        for raw in ("", "   ", "Rohde&Schwarz,SMW200A", "Rohde&Schwarz,SMW200A,,1.0"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    InstrumentIdentity.from_idn(raw)

    def test_device_key_requires_both_vendor_and_serial(self):
        self.assertEqual(make_device_key("Rohde&Schwarz", "105462"), "rohde&schwarz:105462")
        with self.assertRaises(ValueError):
            make_device_key("", "105462")
        with self.assertRaises(ValueError):
            make_device_key("Rohde&Schwarz", "  ")


class InstrumentNameTests(unittest.TestCase):
    def test_name_is_built_from_model_and_index(self):
        self.assertEqual(build_instrument_name("DP832A", 2), "DP832A-2")
        self.assertEqual(build_instrument_name("  smw200a ", 1), "smw200a-1")

    def test_invalid_index_is_rejected(self):
        for index in (0, -1):
            with self.subTest(index=index):
                with self.assertRaises(ValueError):
                    build_instrument_name("DP832A", index)

    def test_path_service_exposes_instruments_file(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(PathService(directory).instruments_file,
                             Path(directory) / "instruments.json")


class RegistryUpsertTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.path = Path(self.temporary_directory.name) / "instruments.json"
        self.registry = InstrumentRegistry.load(self.path)

    def test_first_device_of_a_model_gets_index_one(self):
        instrument, created = self.registry.upsert(InstrumentIdentity.from_idn(SMW_IDN))
        self.assertTrue(created)
        self.assertEqual(instrument.name, "SMW200A-1")
        self.assertEqual(instrument.model_index, 1)

    def test_fixed_role_is_stored_and_survives_a_reload(self):
        """测试角色随设备登记：界面直接读它，不再让用户选择。"""
        self.registry.upsert(InstrumentIdentity.from_idn(SMW_IDN), role="signal_generator")
        self.registry.save()

        reloaded = InstrumentRegistry.load(self.path)

        self.assertEqual(reloaded.get_by_name("SMW200A-1").role, "signal_generator")

    def test_scan_without_role_keeps_the_registered_role(self):
        """扫描不读角色，只是更新状态：不能把已登记的角色清空。"""
        identity = InstrumentIdentity.from_idn(SMW_IDN)
        self.registry.upsert(identity, role="signal_generator")

        instrument, _ = self.registry.upsert(identity, status=DeviceStatus.ONLINE)

        self.assertEqual(instrument.role, "signal_generator")

    def test_unknown_role_is_rejected(self):
        identity = InstrumentIdentity.from_idn(SMW_IDN)
        with self.assertRaises(InstrumentRegistryError):
            self.registry.upsert(identity, role="amplifier")

    def test_set_role_can_assign_and_clear_a_role(self):
        instrument, _ = self.registry.upsert(InstrumentIdentity.from_idn(DP832A_IDN))
        self.assertIsNone(instrument.role)

        assigned = self.registry.set_role(instrument, "PS3")
        self.assertEqual(assigned.role, "PS3")

        cleared = self.registry.set_role("DP832A-1", None)
        self.assertIsNone(cleared.role)

    def test_manually_written_invalid_role_fails_validation(self):
        payload = _document([_entry(role="not-a-role")])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instruments.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(InstrumentRegistryError):
                InstrumentRegistry.load(path)

    def test_same_model_gets_next_index_and_never_reuses_a_retired_one(self):
        first, _ = self.registry.upsert(InstrumentIdentity.from_idn(DP832A_IDN))
        second, _ = self.registry.upsert(
            InstrumentIdentity.from_idn("RIGOL TECHNOLOGIES,DP832A,DP8B174700950,00.01.14")
        )
        self.registry.set_status(first, DeviceStatus.OFFLINE)
        third, _ = self.registry.upsert(
            InstrumentIdentity.from_idn("RIGOL TECHNOLOGIES,DP832A,DP8B174400885,00.01.14")
        )
        self.assertEqual([first.name, second.name, third.name],
                         ["DP832A-1", "DP832A-2", "DP832A-3"])

    def test_repeated_scan_is_idempotent(self):
        self.registry.upsert(InstrumentIdentity.from_idn(SMW_IDN))
        instrument, created = self.registry.upsert(InstrumentIdentity.from_idn(SMW_IDN))
        self.assertFalse(created)
        self.assertEqual(instrument.name, "SMW200A-1")
        self.assertEqual(len(self.registry.list()), 1)

    def test_scan_without_options_keeps_stored_options(self):
        """回归：读不到 *OPT? 的扫描不得清空已登记选件。"""
        identity = InstrumentIdentity.from_idn(SMW_IDN)
        self.registry.upsert(identity, options=["SMW-K526", "SMW-B140"], firmware="5.20.043.47")
        instrument, _ = self.registry.upsert(identity)
        self.assertEqual(sorted(instrument.options), ["SMW-B140", "SMW-K526"])
        self.assertEqual(instrument.firmware, "5.20.043.47")
        self.assertEqual(instrument.order_no, "1412.0000K02")

    def test_explicit_empty_options_clears_them(self):
        identity = InstrumentIdentity.from_idn(SMW_IDN)
        self.registry.upsert(identity, options=["SMW-K526"])
        instrument, _ = self.registry.upsert(identity, options=[])
        self.assertEqual(instrument.options, ())

    def test_option_matching_is_case_insensitive(self):
        instrument, _ = self.registry.upsert(
            InstrumentIdentity.from_idn(SMW_IDN), options=["smw-k526 ", "SMW-B140"]
        )
        self.assertTrue(instrument.has_options(["SMW-K526"]))
        self.assertTrue(instrument.has_options(["smw-b140", "SMW-K526"]))
        self.assertFalse(instrument.has_options(["SMW-K999"]))
        self.assertTrue(instrument.has_options([]))

    def test_list_filters_by_model_and_status(self):
        self.registry.upsert(InstrumentIdentity.from_idn(SMW_IDN))
        self.registry.upsert(InstrumentIdentity.from_idn(DP832A_IDN))
        self.registry.set_status("DP832A-1", DeviceStatus.OFFLINE)
        self.assertEqual([i.name for i in self.registry.list(model="DP832A")], ["DP832A-1"])
        self.assertEqual([i.name for i in self.registry.list(status=DeviceStatus.OFFLINE)],
                         ["DP832A-1"])
        self.assertEqual([i.name for i in self.registry.list()], ["DP832A-1", "SMW200A-1"])


class RegistryEndpointTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.registry = InstrumentRegistry.load(
            Path(self.temporary_directory.name) / "instruments.json"
        )
        self.identity = InstrumentIdentity.from_idn(SMW_IDN)
        instrument, _ = self.registry.upsert(self.identity, options=["SMW-K526"])
        self.instrument = self.registry.record_endpoint(
            instrument, ip="192.168.1.201", mac="00:e0:33:05:c5:fe", idn_port=5025
        )

    def test_first_endpoint_becomes_current(self):
        self.assertEqual(self.instrument.current_ip, "192.168.1.201")
        self.assertEqual(self.instrument.current_endpoint.mac, "00-E0-33-05-C5-FE")
        self.assertEqual(self.instrument.current_endpoint.idn_port, 5025)

    def test_ip_drift_keeps_identity_and_keeps_history(self):
        drifted = self.registry.record_endpoint(self.instrument, ip="192.168.1.211", idn_port=5025)
        self.assertEqual(drifted.name, "SMW200A-1")
        self.assertEqual(drifted.model_index, 1)
        self.assertEqual(drifted.serial, "105462")
        self.assertEqual(drifted.current_ip, "192.168.1.211")
        self.assertEqual(
            [(endpoint.ip, endpoint.is_current) for endpoint in drifted.endpoints],
            [("192.168.1.211", True), ("192.168.1.201", False)],
        )

    def test_known_ips_history_is_capped(self):
        for suffix in (211, 212, 213, 214):
            self.instrument = self.registry.record_endpoint(
                self.instrument, ip=f"192.168.1.{suffix}", idn_port=5025
            )
        self.assertEqual(len(self.instrument.endpoints), MAX_KNOWN_IPS)
        self.assertEqual([endpoint.ip for endpoint in self.instrument.endpoints],
                         ["192.168.1.214", "192.168.1.213", "192.168.1.212"])
        self.assertEqual(self.instrument.current_ip, "192.168.1.214")

    def test_returning_to_a_known_ip_does_not_duplicate_history(self):
        self.instrument = self.registry.record_endpoint(self.instrument, ip="192.168.1.211")
        self.instrument = self.registry.record_endpoint(self.instrument, ip="192.168.1.201")
        self.assertEqual([endpoint.ip for endpoint in self.instrument.endpoints],
                         ["192.168.1.201", "192.168.1.211"])
        self.assertEqual(self.instrument.current_ip, "192.168.1.201")

    def test_at_ip_finds_the_current_owner(self):
        self.assertEqual(self.registry.at_ip("192.168.1.201").name, "SMW200A-1")
        self.assertIsNone(self.registry.at_ip("192.168.1.99"))

    def test_offline_device_keeps_identity_and_address(self):
        offline = self.registry.set_status("SMW200A-1", DeviceStatus.OFFLINE)
        self.assertEqual(offline.status, DeviceStatus.OFFLINE)
        self.assertEqual(self.registry.get_by_serial("105462")[0].current_ip, "192.168.1.201")

    def test_recording_an_unknown_device_is_rejected(self):
        with self.assertRaises(InstrumentRegistryError):
            self.registry.record_endpoint("SMW200A-9", ip="192.168.1.201")

    def test_blank_ip_is_rejected(self):
        with self.assertRaises(InstrumentRegistryError):
            self.registry.record_endpoint(self.instrument, ip="   ")


class RegistryValidationTests(unittest.TestCase):
    """手改 JSON 的容错：写错必须报错，而不是静默接受。"""

    def _load(self, instruments):
        return InstrumentRegistry(document=_document(instruments))

    def test_duplicate_serial_is_rejected(self):
        with self.assertRaises(InstrumentRegistryError):
            self._load([_entry(), _entry(name="SMW200A-2", model_index=2)])

    def test_duplicate_name_is_rejected(self):
        with self.assertRaises(InstrumentRegistryError):
            self._load([
                _entry(),
                _entry(model="DP832A", serial="DP8B214901519", name="SMW200A-1", model_index=9),
            ])

    def test_name_must_match_model_and_index(self):
        with self.assertRaises(InstrumentRegistryError):
            self._load([_entry(name="SMW200A-7")])

    def test_duplicate_model_index_is_rejected(self):
        with self.assertRaises(InstrumentRegistryError):
            self._load([
                _entry(),
                _entry(serial="999888", name="SMW200A-1", model_index=1),
            ])

    def test_current_ip_must_exist_in_known_ips(self):
        with self.assertRaises(InstrumentRegistryError):
            self._load([_entry(current_ip="192.168.1.99")])

    def test_invalid_status_is_rejected(self):
        with self.assertRaises(InstrumentRegistryError):
            self._load([_entry(status="powered-on")])

    def test_missing_serial_vendor_or_model_is_rejected(self):
        for overrides in ({"serial": ""}, {"vendor": ""}, {"model": ""}, {"name": ""}):
            with self.subTest(overrides=overrides):
                with self.assertRaises(InstrumentRegistryError):
                    self._load([_entry(**overrides)])

    def test_non_positive_model_index_is_rejected(self):
        for index in (0, -2):
            with self.subTest(index=index):
                with self.assertRaises(InstrumentRegistryError):
                    self._load([_entry(model_index=index, name="SMW200A-0")])


class RegistryPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.path = Path(self.temporary_directory.name) / "instruments.json"

    def test_loading_a_missing_file_returns_an_empty_registry_without_writing(self):
        registry = InstrumentRegistry.load(self.path)
        self.assertEqual(registry.list(), [])
        self.assertFalse(self.path.exists())

    def test_save_then_load_round_trips(self):
        registry = InstrumentRegistry.load(self.path)
        instrument, _ = registry.upsert(
            InstrumentIdentity.from_idn(SMW_IDN), options=["SMW-K526"], firmware="5.20.043.47"
        )
        registry.record_endpoint(instrument, ip="192.168.1.201", mac="00-E0-33-05-C5-FE",
                                 idn_port=5025)
        registry.save()

        reloaded = InstrumentRegistry.load(self.path).get_by_name("SMW200A-1")
        self.assertEqual(reloaded.serial, "105462")
        self.assertEqual(reloaded.order_no, "1412.0000K02")
        self.assertEqual(reloaded.firmware, "5.20.043.47")
        self.assertEqual(list(reloaded.options), ["SMW-K526"])
        self.assertEqual(reloaded.current_ip, "192.168.1.201")
        self.assertEqual(reloaded.current_endpoint.mac, "00-E0-33-05-C5-FE")

    def test_saved_file_is_plain_readable_json(self):
        registry = InstrumentRegistry.load(self.path)
        registry.upsert(InstrumentIdentity.from_idn(SMW_IDN))
        registry.save()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        self.assertIn("updated_at", payload)
        self.assertEqual(payload["instruments"][0]["name"], "SMW200A-1")

    def test_manual_extra_fields_survive_a_scan_round_trip(self):
        """人工在 JSON 里补充的字段（如校准到期日）不能被扫描流程抹掉。"""
        payload = _document([_entry(calibration_due="2027-03-01")])
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        registry = InstrumentRegistry.load(self.path)
        instrument, created = registry.upsert(InstrumentIdentity.from_idn(SMW_IDN))
        self.assertFalse(created)
        registry.record_endpoint(instrument, ip="192.168.1.211", idn_port=5025)
        registry.save()

        reloaded = InstrumentRegistry.load(self.path)
        self.assertEqual(reloaded.raw["instruments"][0]["calibration_due"], "2027-03-01")
        self.assertEqual(reloaded.get_by_name("SMW200A-1").current_ip, "192.168.1.211")

    def test_no_temporary_file_is_left_behind(self):
        registry = InstrumentRegistry.load(self.path)
        registry.upsert(InstrumentIdentity.from_idn(SMW_IDN))
        registry.save()
        leftovers = [item.name for item in self.path.parent.iterdir() if item.name != self.path.name]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
