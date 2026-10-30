"""发现结果的身份解析与卡片文字：网段、绑定键、设备描述与设备码。

这些行为不需要打开窗口：发现结果（``DiscoveredDevice``）进来，纯函数给出显示文本，
因此本文件的用例不启动 Qt 窗口。把文字渲染成卡片的部分在 ``test_panel_presentation.py``，
把勾选结果写进配置的部分在 ``test_binding_writes.py``。
"""

import unittest

from domain.instruments.models import DeviceStatus, InstrumentIdentity
from infrastructure.discovery.instrument_discovery import DiscoveredDevice
from presentation.qt.device_cards import device_code, instrument_picture_path
from presentation.qt.device_discovery_panel import (
    binding_device_key,
    card_heading,
    card_model,
    card_status_text,
    device_heading,
    subnet_from_address,
)

from .support import CEYEAR_IDN, DP832A_IDN, SMW_IDN


class SubnetSuggestionTests(unittest.TestCase):
    def test_visa_address_suggests_its_24_bit_subnet(self):
        self.assertEqual(
            subnet_from_address("TCPIP0::192.168.1.201::inst0::INSTR"), "192.168.1.0/24"
        )

    def test_missing_or_invalid_address_falls_back_to_none(self):
        for value in ("", None, "ASRL1::INSTR", 42, "TCPIP0::999.1.1.1::INSTR"):
            with self.subTest(value=value):
                self.assertIsNone(subnet_from_address(value))


class BindingKeyTests(unittest.TestCase):
    """身份键只由厂商 + 序列号决定；只有序列号时不做猜测。"""

    def test_device_key_is_used_when_present(self):
        self.assertEqual(
            binding_device_key({"device_key": "Rohde&Schwarz:105462"}),
            "rohde&schwarz:105462",
        )

    def test_vendor_and_serial_are_combined_when_key_is_missing(self):
        self.assertEqual(
            binding_device_key({"vendor": "Rohde&Schwarz", "serial": "105462"}),
            "rohde&schwarz:105462",
        )

    def test_serial_alone_or_malformed_input_yields_none(self):
        for record in ({"serial": "105462"}, {}, None, "not-a-record", {"device_key": "  "}):
            with self.subTest(record=record):
                self.assertIsNone(binding_device_key(record))


class DeviceHeadingTests(unittest.TestCase):
    """结果表的设备描述：电源用短标签（型号，电源），其他角色沿用“设备名（角色）”。"""

    def test_power_supply_shows_short_label_model_and_category(self):
        device = DiscoveredDevice(
            ip="10.0.0.1",
            port=5555,
            idn=DP832A_IDN,
            identity=InstrumentIdentity.from_idn(DP832A_IDN),
            known_name="DP832A-1",
            status=DeviceStatus.ONLINE,
            known_role="power_supply",
            known_label="DP8B1519",
        )

        self.assertEqual(device_heading(device), "DP8B1519（DP832A，电源）")
        self.assertEqual(device.supply_id, "DP8B1519")
        self.assertFalse(hasattr(device, "config_slot"), "旧槽位已从发现结果里删除")

    def test_legacy_slot_is_folded_into_the_power_supply_category(self):
        device = DiscoveredDevice(
            ip="10.0.0.1",
            port=5555,
            idn=DP832A_IDN,
            identity=InstrumentIdentity.from_idn(DP832A_IDN),
            known_name="DP832A-1",
            status=DeviceStatus.ONLINE,
            known_role="PS3",
        )

        self.assertEqual(device.role, "power_supply")
        self.assertFalse(hasattr(device, "config_slot"))
        self.assertIsNone(device.supply_id, "没有标签就没有 supply_id，发现过程不生成标签")
        self.assertEqual(device_heading(device), "DP832A-1（DP832A，电源）")

    def test_other_roles_and_unknown_devices_keep_their_heading(self):
        signal = DiscoveredDevice(
            ip="10.0.0.2",
            port=5025,
            idn=SMW_IDN,
            identity=InstrumentIdentity.from_idn(SMW_IDN),
            known_name="SMW200A-1",
            status=DeviceStatus.ONLINE,
            known_role="signal_generator",
        )
        unknown = DiscoveredDevice(
            ip="10.0.0.3",
            port=5025,
            idn=CEYEAR_IDN,
            identity=InstrumentIdentity.from_idn(CEYEAR_IDN),
            known_name=None,
            status=DeviceStatus.UNKNOWN,
        )

        self.assertEqual(device_heading(signal), "SMW200A-1（信号发生器）")
        self.assertEqual(device_heading(unknown), "未知设备 (4082F)")

    def test_signal_generator_with_a_label_shows_model_then_label(self):
        """设备表保存过后信号源也有短标签：标题写“型号（短标签，角色）”，不再有 ``-1``。"""
        signal = DiscoveredDevice(
            ip="10.0.0.2",
            port=5025,
            idn=SMW_IDN,
            identity=InstrumentIdentity.from_idn(SMW_IDN),
            known_name="SMW200A-1",
            known_label="5462",
            status=DeviceStatus.ONLINE,
            known_role="signal_generator",
        )

        self.assertEqual(device_heading(signal), "SMW200A（5462，信号发生器）")
        self.assertEqual(card_heading(signal), "SMW200A（5462）")
        # 卡片第一行只写仪器名（型号），图片挑选也按型号来。
        self.assertEqual(card_model(signal), "SMW200A")


class DeviceCardContentTests(unittest.TestCase):
    """卡片上的文字：设备名、型号与缩减版设备码都由发现结果直接生成。"""

    @staticmethod
    def device(**overrides):
        values = {
            "ip": "10.0.0.2",
            "port": 5025,
            "idn": SMW_IDN,
            "identity": InstrumentIdentity.from_idn(SMW_IDN),
            "known_name": "SMW200A-1",
            "status": DeviceStatus.ONLINE,
            "known_role": "signal_generator",
        }
        values.update(overrides)
        return DiscoveredDevice(**values)

    def test_device_code_uses_role_model_and_serial(self):
        code, detail = device_code(self.device())

        self.assertEqual(code, "SG/SMW200A/105462")
        # 附注只写连接细节：身份码在卡片正面，不在提示里重复。
        self.assertEqual(detail, "地址 TCPIP0::10.0.0.2::inst0::INSTR")

    def test_power_supply_code_is_its_short_label(self):
        """电源的身份码就是注册表短标签（= ``supply_id``），不是 ``PS/型号/序列号``。"""
        supply = self.device(
            identity=InstrumentIdentity.from_idn(DP832A_IDN),
            idn=DP832A_IDN,
            known_role="power_supply",
            known_name="DP832A-1",
            known_label="DP8B1519",
        )

        code, detail = device_code(supply)

        self.assertEqual(code, "DP8B1519")
        self.assertIn("TCPIP0::10.0.0.2::inst0::INSTR", detail)

    def test_power_supply_and_spectrum_analyzer_have_their_own_prefixes(self):
        supply = self.device(
            identity=InstrumentIdentity.from_idn(DP832A_IDN),
            idn=DP832A_IDN,
            known_role="power_supply",
            known_name="DP832A-1",
        )
        spectrum = self.device(
            identity=InstrumentIdentity.from_idn(CEYEAR_IDN),
            idn=CEYEAR_IDN,
            known_role="spectrum_analyzer",
            known_name="4082F-1",
        )

        self.assertEqual(device_code(supply)[0], "PS/DP832A/DP8B214901519")
        self.assertEqual(device_code(spectrum)[0], "SA/4082F/QZNI000866")
        # 旧设备表还没有标签：退回旧写法，绝不凭空造一个像短标签的字符串。
        self.assertIsNone(supply.label)

    def test_unregistered_device_falls_back_to_the_unknown_role_code(self):
        device = self.device(known_name=None, known_role=None)

        self.assertEqual(device_code(device)[0], "NA/SMW200A/105462")

    def test_unidentified_address_gets_a_visitor_number_instead_of_a_code(self):
        device = self.device(
            idn=None,
            identity=None,
            known_name=None,
            known_role=None,
            status=DeviceStatus.UNKNOWN,
            error="GARBAGE",
        )

        code, detail = device_code(device, visitor_index=3)

        self.assertEqual(code, "未识别 #3")
        # 读不出身份时也没有地址可写：失败原因由面板提示给出，附注为空。
        self.assertEqual(detail, "")
        self.assertEqual(card_heading(device, visitor_index=3), "未识别设备 #3")

    def test_card_heading_and_model_follow_the_device(self):
        device = self.device(
            identity=InstrumentIdentity.from_idn(DP832A_IDN),
            idn=DP832A_IDN,
            known_role="power_supply",
            known_name="DP832A-1",
            known_label="DP8B1519",
        )

        self.assertEqual(card_heading(device), "DP8B1519")
        self.assertEqual(card_model(device), "DP832A")
        self.assertEqual(card_status_text(device), "")

    def test_cards_explain_why_a_device_cannot_be_used(self):
        unregistered = self.device(known_name=None, known_role=None)
        no_role = self.device(known_role=None)

        self.assertEqual(card_status_text(unregistered), "未登记 · 不能应用")
        self.assertEqual(card_status_text(no_role), "未分配角色 · 不能应用")

    def test_pictures_are_chosen_by_role_then_by_model(self):
        self.assertEqual(
            instrument_picture_path("signal_generator", "").name, "signal_source.png"
        )
        self.assertEqual(
            instrument_picture_path("spectrum_analyzer", "").name, "spectrum_analyzer.png"
        )
        self.assertEqual(
            instrument_picture_path("power_supply", "").name, "DC_source.png"
        )
        # 未登记角色的设备只能按型号猜图；两个条件都不满足时没有图片（卡片留空底板）。
        self.assertEqual(instrument_picture_path("", "N9020A"), None)
        self.assertEqual(instrument_picture_path("", "DP832A").name, "DC_source.png")
