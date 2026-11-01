"""设备表（仪器注册表）单元测试。

覆盖四条核心不变量：

1. 身份只认 ``厂商 + 完整设备码``，IP 变化不影响归属；
2. **型号可以重复**；登记表不生成“型号-序号”资产名，设备之间靠全局唯一的缩写设备码
   ``label`` 区分；
3. 手改 JSON 写错（重复完整设备码、缩写码冲突、current_ip 与 known_ips 不一致）必须报错；
4. 旧 schema 的 ``serial`` / ``name`` / ``model_index`` 只在读取边界被识别，读入后立即
   投影为 ``full_code`` 并丢弃资产名与序号。
"""

import json
import tempfile
import unittest
from pathlib import Path

from domain.instruments.models import (
    DeviceStatus,
    InstrumentIdentity,
    make_device_key,
    normalize_full_code,
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
        "vendor": "Rohde&Schwarz",
        "model": "SMW200A",
        "full_code": "105462",
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
    def test_r_and_s_identity_splits_order_number_and_full_code(self):
        identity = InstrumentIdentity.from_idn(SMW_IDN)
        self.assertEqual(identity.vendor, "Rohde&Schwarz")
        self.assertEqual(identity.model, "SMW200A")
        self.assertEqual(identity.order_no, "1412.0000K02")
        self.assertEqual(identity.full_code, "105462")
        self.assertEqual(identity.firmware, "5.20.043.47")
        self.assertEqual(identity.raw_idn, SMW_IDN)

    def test_rigol_identity_without_order_number(self):
        identity = InstrumentIdentity.from_idn(DP832A_IDN)
        self.assertIsNone(identity.order_no)
        self.assertEqual(identity.full_code, "DP8B214901519")

    def test_ceyear_identity(self):
        identity = InstrumentIdentity.from_idn(CEYEAR_IDN)
        self.assertEqual(identity.vendor, "Ceyear Technologies")
        self.assertEqual(identity.model, "4082F")
        self.assertEqual(identity.full_code, "QZNI000866")

    def test_full_code_is_normalized_to_upper_case_without_quotes(self):
        identity = InstrumentIdentity.from_idn('Rigol,dp832a, "dp8b214901519" ,00.01.16')
        self.assertEqual(identity.model, "DP832A")
        self.assertEqual(identity.full_code, "DP8B214901519")

    def test_full_code_normalization_is_idempotent(self):
        # 引号与空白并存时一轮剥不完；模型自己循环到稳定，保证完整设备码、device_key
        # 与缩写设备码用的是同一个值。
        cases = {
            ' " dp8b214901519 " ': "DP8B214901519",
            '"105462"': "105462",
            "  105462  ": "105462",
            "DP8B 214901519": "DP8B 214901519",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_full_code(raw), expected)
                self.assertEqual(normalize_full_code(expected), expected)

    def test_incomplete_identity_is_rejected(self):
        for raw in ("", "   ", "Rohde&Schwarz,SMW200A", "Rohde&Schwarz,SMW200A,,1.0"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    InstrumentIdentity.from_idn(raw)

    def test_device_key_requires_both_vendor_and_full_code(self):
        self.assertEqual(make_device_key("Rohde&Schwarz", "105462"), "rohde&schwarz:105462")
        with self.assertRaises(ValueError):
            make_device_key("", "105462")
        with self.assertRaises(ValueError):
            make_device_key("Rohde&Schwarz", "  ")


class ModelReuseTests(unittest.TestCase):
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

    def test_new_device_has_no_asset_name_or_model_index(self):
        instrument, created = self.registry.upsert(InstrumentIdentity.from_idn(SMW_IDN))
        self.assertTrue(created)
        payload = instrument.to_dict()
        self.assertNotIn("name", payload)
        self.assertNotIn("model_index", payload)
        self.assertEqual(instrument.full_code, "105462")
        self.assertIsNone(instrument.label)

    def test_fixed_role_is_stored_and_survives_a_reload(self):
        """测试角色随设备登记：界面直接读它，不再让用户选择。"""
        self.registry.upsert(InstrumentIdentity.from_idn(SMW_IDN), role="signal_generator")
        self.registry.save()

        reloaded = InstrumentRegistry.load(self.path)

        self.assertEqual(
            reloaded.get_by_device_key(make_device_key("Rohde&Schwarz", "105462")).role,
            "signal_generator",
        )

    def test_scan_without_role_keeps_the_registered_role(self):
        """扫描不读角色，只是更新状态：不能把已登记的角色清空。"""
        identity = InstrumentIdentity.from_idn(SMW_IDN)
        self.registry.upsert(identity, role="signal_generator")

        instrument, _ = self.registry.upsert(identity, status=DeviceStatus.ONLINE)

        self.assertEqual(instrument.role, "signal_generator")

    def test_saving_gives_every_device_a_label_not_just_power_supplies(self):
        """缩写码由完整设备码派生、与角色无关：信号源、频谱仪与未登记角色的设备同样有。"""
        self.registry.upsert(InstrumentIdentity.from_idn(SMW_IDN), role="signal_generator")
        self.registry.upsert(InstrumentIdentity.from_idn(CEYEAR_IDN), role="spectrum_analyzer")
        unregistered, _ = self.registry.upsert(InstrumentIdentity.from_idn(DP832A_IDN))
        self.assertIsNone(unregistered.role)

        self.registry.save()

        saved = InstrumentRegistry.load(self.path)
        self.assertEqual(
            {item.model: item.label for item in saved.list()},
            {"SMW200A": "5462", "4082F": "QZNI0866", "DP832A": "DP8B1519"},
        )
        # 显示名一律用缩写码；型号与完整设备码仍可读。
        self.assertEqual(
            {item.display_name for item in saved.list()},
            {"QZNI0866", "DP8B1519", "5462"},
        )

    def test_unusable_full_code_only_blocks_the_save_for_power_supplies(self):
        """非电源的缩写码只用于展示：完整设备码含内部空白时留空，不阻断整份保存。"""
        self.registry.upsert(InstrumentIdentity.from_idn(SMW_IDN), role="signal_generator")
        self.registry.save()
        document = self.registry.raw
        document["instruments"][0]["full_code"] = "105 462"
        document["instruments"][0]["label"] = None
        self.path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

        reloaded = InstrumentRegistry.load(self.path)
        reloaded.save()

        saved = InstrumentRegistry.load(self.path).list()[0]
        self.assertIsNone(saved.label, "非电源派不出缩写码时留空，显示退回型号")
        self.assertEqual(saved.display_name, "SMW200A")

    def test_unknown_role_is_rejected(self):
        identity = InstrumentIdentity.from_idn(SMW_IDN)
        with self.assertRaises(InstrumentRegistryError):
            self.registry.upsert(identity, role="amplifier")

    def test_set_role_can_assign_and_clear_a_role(self):
        instrument, _ = self.registry.upsert(InstrumentIdentity.from_idn(DP832A_IDN))
        self.assertIsNone(instrument.role)

        assigned = self.registry.set_role(instrument, "PS3")
        self.assertEqual(assigned.role, "PS3")

        cleared = self.registry.set_role(instrument.device_key, None)
        self.assertIsNone(cleared.role)

    def test_manually_written_invalid_role_fails_validation(self):
        payload = _document([_entry(role="not-a-role")])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instruments.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(InstrumentRegistryError):
                InstrumentRegistry.load(path)

    def test_same_model_can_hold_multiple_devices(self):
        first, _ = self.registry.upsert(InstrumentIdentity.from_idn(DP832A_IDN))
        second, _ = self.registry.upsert(
            InstrumentIdentity.from_idn("RIGOL TECHNOLOGIES,DP832A,DP8B174700950,00.01.14")
        )
        third, _ = self.registry.upsert(
            InstrumentIdentity.from_idn("RIGOL TECHNOLOGIES,DP832A,DP8B174400885,00.01.14")
        )

        self.assertEqual({first.model, second.model, third.model}, {"DP832A"})
        self.assertEqual(len({item.device_key for item in (first, second, third)}), 3)
        self.registry.save()
        saved = {item.full_code: item for item in InstrumentRegistry.load(self.path).list()}
        self.assertEqual(len(saved), 3)
        self.assertEqual(
            {item.label for item in saved.values()},
            {"DP8B1519", "DP8B0950", "DP8B0885"},
        )

    def test_repeated_scan_is_idempotent(self):
        self.registry.upsert(InstrumentIdentity.from_idn(SMW_IDN))
        instrument, created = self.registry.upsert(InstrumentIdentity.from_idn(SMW_IDN))
        self.assertFalse(created)
        self.assertEqual(instrument.full_code, "105462")
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
        self.registry.set_status(
            make_device_key("RIGOL TECHNOLOGIES", "DP8B214901519"), DeviceStatus.OFFLINE
        )
        self.assertEqual(
            [i.full_code for i in self.registry.list(model="DP832A")], ["DP8B214901519"]
        )
        self.assertEqual(
            [i.full_code for i in self.registry.list(status=DeviceStatus.OFFLINE)],
            ["DP8B214901519"],
        )
        self.assertEqual(
            {i.full_code for i in self.registry.list()}, {"DP8B214901519", "105462"}
        )


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
        self.assertEqual(drifted.model, "SMW200A")
        self.assertEqual(drifted.full_code, "105462")
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
        self.assertEqual(
            self.registry.at_ip("192.168.1.201").full_code, "105462"
        )
        self.assertIsNone(self.registry.at_ip("192.168.1.99"))

    def test_offline_device_keeps_identity_and_address(self):
        offline = self.registry.set_status(self.instrument, DeviceStatus.OFFLINE)
        self.assertEqual(offline.status, DeviceStatus.OFFLINE)
        self.assertEqual(self.registry.get_by_full_code("105462")[0].current_ip,
                         "192.168.1.201")

    def test_recording_an_unknown_device_is_rejected(self):
        with self.assertRaises(InstrumentRegistryError):
            self.registry.record_endpoint("smw200a:unknown", ip="192.168.1.201")

    def test_blank_ip_is_rejected(self):
        with self.assertRaises(InstrumentRegistryError):
            self.registry.record_endpoint(self.instrument, ip="   ")


class RegistryValidationTests(unittest.TestCase):
    """手改 JSON 的容错：写错必须报错，而不是静默接受。"""

    def _load(self, instruments):
        return InstrumentRegistry(document=_document(instruments))

    def test_duplicate_full_code_is_rejected(self):
        with self.assertRaises(InstrumentRegistryError):
            self._load([_entry(), _entry(label="OTHER")])

    def test_duplicate_label_is_rejected(self):
        with self.assertRaises(InstrumentRegistryError):
            self._load([
                _entry(full_code="105462", label="5462"),
                _entry(vendor="RIGOL", model="DP832A", full_code="DP8B214901519", label="5462"),
            ])

    def test_duplicate_model_is_allowed(self):
        registry = self._load([
            _entry(full_code="105462", label="5462"),
            _entry(full_code="105463", label="5463"),
        ])
        self.assertEqual(len(registry.list(model="SMW200A")), 2)

    def test_retired_fields_are_projected_on_read_not_written(self):
        registry = self._load([
            {
                "name": "SMW200A-1",
                "vendor": "Rohde&Schwarz",
                "model": "SMW200A",
                "model_index": 1,
                "serial": "105462",
                "label": "5462",
                "status": "online",
                "known_ips": [],
            }
        ])
        entry = registry.raw["instruments"][0]
        self.assertEqual(entry["full_code"], "105462")
        self.assertNotIn("serial", entry)
        self.assertNotIn("name", entry)
        self.assertNotIn("model_index", entry)

    def test_current_ip_must_exist_in_known_ips(self):
        with self.assertRaises(InstrumentRegistryError):
            self._load([_entry(current_ip="192.168.1.99")])

    def test_invalid_status_is_rejected(self):
        with self.assertRaises(InstrumentRegistryError):
            self._load([_entry(status="powered-on")])

    def test_missing_full_code_vendor_or_model_is_rejected(self):
        for overrides in ({"full_code": ""}, {"vendor": ""}, {"model": ""}):
            with self.subTest(overrides=overrides):
                with self.assertRaises(InstrumentRegistryError):
                    self._load([_entry(**overrides)])

    def test_label_must_be_usable_as_supply_id(self):
        for label in ("AB CD", "AB/CD", " AB", "AB "):
            with self.subTest(label=label):
                with self.assertRaises(InstrumentRegistryError):
                    self._load([_entry(label=label)])


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

        reloaded = InstrumentRegistry.load(self.path).get_by_device_key(instrument.device_key)
        self.assertEqual(reloaded.full_code, "105462")
        self.assertEqual(reloaded.order_no, "1412.0000K02")
        self.assertEqual(reloaded.firmware, "5.20.043.47")
        self.assertEqual(list(reloaded.options), ["SMW-K526"])
        self.assertEqual(reloaded.current_ip, "192.168.1.201")
        self.assertEqual(reloaded.current_endpoint.mac, "00-E0-33-05-C5-FE")

    def test_saved_file_is_plain_readable_json_without_retired_fields(self):
        registry = InstrumentRegistry.load(self.path)
        registry.upsert(InstrumentIdentity.from_idn(SMW_IDN))
        registry.save()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        self.assertIn("updated_at", payload)
        entry = payload["instruments"][0]
        self.assertEqual(entry["full_code"], "105462")
        for retired in ("name", "model_index", "serial"):
            self.assertNotIn(retired, entry)

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
        self.assertEqual(
            reloaded.get_by_device_key(instrument.device_key).current_ip, "192.168.1.211"
        )

    def test_no_temporary_file_is_left_behind(self):
        registry = InstrumentRegistry.load(self.path)
        registry.upsert(InstrumentIdentity.from_idn(SMW_IDN))
        registry.save()
        leftovers = [item.name for item in self.path.parent.iterdir() if item.name != self.path.name]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
