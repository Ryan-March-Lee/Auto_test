"""步骤 14：电源在显示、日志与运行记录里一律用短标签，序列号留给详情与审计。

固定的行为：

- **显示文本只有一份实现**（:func:`domain.instruments.labels.supply_display_label`）：
  电源标签页标题、分配下拉框与日志都用它；型号缺失、型号与标签相同（旧配置把槽位名填进
  ``model``）时只显示标签；
- **其它仪器只有一份实现**（:func:`domain.instruments.labels.instrument_display_label`）：
  显示成 ``型号（短标签）``（如 ``SMW200A（5462）``），型号在前——短标签只用来区分同型号的
  另一台，不再靠资产名后缀 ``-1`` / ``-2``；
- **详情/审计文本**（:func:`domain.instruments.labels.supply_identity_text`）按已知字段拼装：
  短标签 · 型号 · 完整序列号 · 设备键，缺哪个字段就少哪一段，**绝不凭空造标签**；
- **旧记录按旧字段显示**：旧槽位条目（``PS1``、没有序列号）就显示 ``PS1``，不为它推导一个
  不存在的短标签，历史运行因此仍可读取；
- **装配计划把详情文本交给日志**：连接每台电源时写一行身份核对，日志里既有现场短标签，
  也有完整序列号与设备自己应答的 ``*IDN?`` 原文，可追溯但不靠编号认设备。

验证方式：

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" -m unittest tests.test_power_supply_display -v
```
"""

import unittest
from pathlib import Path

from domain.instruments.labels import (
    DISPLAY_SEPARATOR,
    IDENTITY_MATCHED,
    IDENTITY_MISMATCHED,
    IDENTITY_UNCOMPARABLE,
    instrument_display_label,
    supply_channel_text,
    supply_display_label,
    supply_identity_text,
    supply_identity_verdict,
)
from instrument.assembly_plan import hardware_assembly_plan
from presentation.qt.config_projection import supply_option_label

ROOT = Path(__file__).parents[1]
PROJECTION_SOURCE = ROOT / "presentation" / "qt" / "config_projection.py"
POWER_SECTION_SOURCE = ROOT / "presentation" / "qt" / "config_power_section.py"
PLAN_SOURCE = ROOT / "instrument" / "assembly_plan.py"
ROUTING_SOURCE = ROOT / "instrument" / "power_routing.py"
FACTORY_SOURCE = ROOT / "instrument" / "measurement_factory.py"
POWER_CONTROL_SOURCE = ROOT / "instrument" / "power_control.py"
ADAPTER_SOURCE = ROOT / "instrument" / "measurement_adapter.py"
DIAGRAMS_SOURCE = ROOT / "presentation" / "qt" / "connection_diagrams.py"

SIGNAL = "TCPIP0::192.168.1.111::inst0::INSTR"
SPECTRUM = "TCPIP0::192.168.1.112::inst0::INSTR"
SUPPLY_A = "TCPIP0::192.168.1.102::inst0::INSTR"
SUPPLY_B = "TCPIP0::192.168.1.103::inst0::INSTR"


def _channels() -> dict:
    return {
        "CH1": {"role": "gate", "voltage": {"value": 2.8}, "current": {"value": 0.1}},
        "CH2": {"role": "drain", "voltage": {"value": 28.0}, "current": {"value": 2.0}},
    }


def _two_supply_config() -> dict:
    """两台电源：一台带完整身份，一台只有集合键（旧配置的常见形状）。"""
    return {
        "instruments": {
            "signal_generator": {"address": SIGNAL, "enabled": True},
            "spectrum_analyzer": {"address": SPECTRUM, "enabled": True},
            "power_supplies": {
                "DP8B1519": {
                    "address": SUPPLY_A,
                    "enabled": True,
                    "label": "DP8B1519",
                    "model": "DP832A",
                    "serial": "DP8B214901519",
                    "device_key": "rigol technologies:dp8b214901519",
                    "channels": _channels(),
                },
                "DP8B0950": {
                    "address": SUPPLY_B,
                    "enabled": True,
                    "channels": _channels(),
                },
            },
        },
        "power_supply_assignment": {
            "dut_amplifier": {
                "supplies": {
                    "carrier": {
                        "supply_id": "DP8B1519",
                        "name": "DP8B1519",
                        "channel": ["CH1", "CH2"],
                    }
                }
            },
            "driver_amplifier": {
                "supplies": {
                    "main": {
                        "supply_id": "DP8B0950",
                        "name": "DP8B0950",
                        "channel": ["CH1", "CH2"],
                    }
                }
            },
        },
    }


class DisplayLabelTests(unittest.TestCase):
    """列表、标签页标题与下拉框上的 ``短标签（型号）``。"""

    def test_label_and_model_are_shown_together(self):
        self.assertEqual(
            supply_display_label("DP8B1519", "DP832A"), "DP8B1519（DP832A）"
        )

    def test_missing_or_duplicate_model_shows_the_label_only(self):
        self.assertEqual(supply_display_label("DP8B1519"), "DP8B1519")
        self.assertEqual(supply_display_label("DP8B1519", "   "), "DP8B1519")
        # 旧配置把槽位名填在 ``model`` 里：不能显示成 ``PS1（PS1）``。
        self.assertEqual(supply_display_label("PS1", "PS1"), "PS1")

    def test_label_is_never_derived_from_the_model(self):
        # 型号不是标签：只有型号时显示型号，不造一个看起来像 ``supply_id`` 的字符串。
        self.assertEqual(supply_display_label(None, "DP832A"), "DP832A")
        self.assertEqual(supply_display_label("", "DP832A"), "DP832A")
        self.assertEqual(supply_display_label(None, None), "")

    def test_old_slot_keys_are_shown_verbatim(self):
        # 旧记录按旧字段显示：不推导不存在的标签，也不把 ``PS1`` 换成某台设备的标签。
        for key in ("PS1", "PS2", "PS3", "PS4"):
            with self.subTest(key=key):
                self.assertEqual(supply_display_label(key), key)

    def test_non_string_values_are_treated_as_missing(self):
        self.assertEqual(supply_display_label({"label": "DP8B1519"}, None), "")
        self.assertEqual(supply_display_label("DP8B1519", ["DP832A"]), "DP8B1519")
        self.assertEqual(supply_display_label(105462, "DP832A"), "105462（DP832A）")

    def test_display_text_is_shared_with_the_projection(self):
        # 下拉框与标签页标题共用同一份规则：同一输入必须给出同一文本。
        for label, model in (
            ("DP8B1519", "DP832A"),
            ("DP8B1519", None),
            ("PS1", "PS1"),
            ("DP8B0950", "DP832A"),
        ):
            with self.subTest(label=label, model=model):
                self.assertEqual(
                    supply_option_label(label, model), supply_display_label(label, model)
                )


class InstrumentDisplayLabelTests(unittest.TestCase):
    """其它仪器的 ``型号（短标签）``：型号在前，短标签只在括号里区分同型号的另一台。"""

    def test_model_comes_first(self):
        self.assertEqual(
            instrument_display_label("5462", "SMW200A"), "SMW200A（5462）"
        )
        self.assertEqual(
            instrument_display_label("QZNI0866", "4082F"), "4082F（QZNI0866）"
        )

    def test_role_text_joins_the_same_parentheses(self):
        # 结果表的一行是“型号（短标签，角色）”，不写成两层括号。
        self.assertEqual(
            instrument_display_label("5462", "SMW200A", "信号发生器"),
            "SMW200A（5462，信号发生器）",
        )
        self.assertEqual(
            instrument_display_label("5462", "", "信号发生器"), "5462，信号发生器"
        )
        self.assertEqual(instrument_display_label("5462", "SMW200A", ""), "SMW200A（5462）")

    def test_missing_pieces_are_dropped_instead_of_invented(self):
        self.assertEqual(instrument_display_label(None, "SMW200A"), "SMW200A")
        self.assertEqual(instrument_display_label("", "SMW200A"), "SMW200A")
        self.assertEqual(instrument_display_label("5462"), "5462")
        self.assertEqual(instrument_display_label(None, None, "信号发生器"), "信号发生器")
        self.assertEqual(instrument_display_label(None, None), "")

    def test_non_string_values_are_treated_as_missing(self):
        self.assertEqual(instrument_display_label({"label": "5462"}, "SMW200A"), "SMW200A")
        self.assertEqual(instrument_display_label(5462, "SMW200A"), "SMW200A（5462）")


class IdentityTextTests(unittest.TestCase):
    """详情与审计：短标签 · 型号 · 完整序列号 · 设备键。"""
    def test_full_identity_lists_every_known_field_in_order(self):
        text = supply_identity_text(
            "DP8B1519", "DP832A", "DP8B214901519", "rigol technologies:dp8b214901519"
        )
        self.assertEqual(
            text,
            "DP8B1519（DP832A）"
            + DISPLAY_SEPARATOR
            + "序列号 DP8B214901519"
            + DISPLAY_SEPARATOR
            + "设备键 rigol technologies:dp8b214901519",
        )
        # 现场读得出来的短标签在最前，系统身份在最后。
        self.assertTrue(text.startswith("DP8B1519"))
        self.assertTrue(text.endswith("rigol technologies:dp8b214901519"))

    def test_missing_fields_are_omitted_not_invented(self):
        self.assertEqual(
            supply_identity_text("DP8B1519", "DP832A"), "DP8B1519（DP832A）"
        )
        self.assertEqual(supply_identity_text("DP8B1519"), "DP8B1519")
        # 没有标签也没有型号时只给序列号，不把序列号当标签显示。
        self.assertEqual(
            supply_identity_text(None, None, "DP8B214901519"), "序列号 DP8B214901519"
        )
        self.assertEqual(supply_identity_text(None, None), "")

    def test_old_entry_without_serial_shows_its_old_key_only(self):
        # 旧槽位条目没有序列号：提示里只有旧键，不出现“序列号”一段。
        text = supply_identity_text("PS1", None, None, None)
        self.assertEqual(text, "PS1")
        self.assertNotIn("序列号", text)


class ChannelTextTests(unittest.TestCase):
    """日志与错误里的通道名：``短标签/CHx``，路由值本身不被改写。"""

    def test_channel_text_uses_the_short_label_not_the_collection_key(self):
        # 集合键与短标签不同时，日志写现场短标签，路由仍是 ``supply_id/CHx``。
        self.assertEqual(
            supply_channel_text("DP8B1519", "CH1", "现场 A 电源"), "现场 A 电源/CH1"
        )

    def test_missing_label_falls_back_to_the_supply_id(self):
        self.assertEqual(supply_channel_text("DP8B1519", "CH1"), "DP8B1519/CH1")
        self.assertEqual(supply_channel_text("DP8B1519", "CH1", "   "), "DP8B1519/CH1")
        # 旧槽位条目：键就是旧字段，原样显示，不造新标签。
        self.assertEqual(supply_channel_text("PS1", "CH1"), "PS1/CH1")

    def test_missing_parts_leave_no_stray_separator(self):
        self.assertEqual(supply_channel_text(None, "CH1"), "CH1")
        self.assertEqual(supply_channel_text("DP8B1519", None), "DP8B1519")
        self.assertEqual(supply_channel_text(None, None), "")
        self.assertEqual(supply_channel_text({"label": "x"}, {"channel": "CH1"}), "")


class IdentityVerdictTests(unittest.TestCase):
    """配置序列号与设备 ``*IDN?`` 应答的核对：说清楚一致、不一致、无法比较。"""

    IDN = "RIGOL TECHNOLOGIES,DP832A,DP8B214901519,00.01.16"

    def test_matching_serial_is_reported_as_matched(self):
        status, text = supply_identity_verdict("DP8B214901519", self.IDN)
        self.assertEqual(status, IDENTITY_MATCHED)
        self.assertEqual(text, IDENTITY_MATCHED)

    def test_normalized_serial_still_matches(self):
        # 引号/空白/大小写差异是同一个身份，不能报成不一致。
        for serial in (' "dp8b214901519" ', " DP8B214901519 "):
            with self.subTest(serial=serial):
                self.assertEqual(
                    supply_identity_verdict(serial, self.IDN)[0], IDENTITY_MATCHED
                )

    def test_different_serial_reports_both_values(self):
        status, text = supply_identity_verdict("DP8B174400885", self.IDN)
        self.assertEqual(status, IDENTITY_MISMATCHED)
        self.assertIn("DP8B174400885", text)
        self.assertIn("DP8B214901519", text)

    def test_old_entry_without_a_serial_is_uncomparable_not_mismatched(self):
        for serial in (None, "", "   ", {"serial": "DP8B214901519"}):
            with self.subTest(serial=serial):
                status, text = supply_identity_verdict(serial, self.IDN)
                self.assertEqual(status, IDENTITY_UNCOMPARABLE)
                self.assertIn("配置没有序列号", text)
                self.assertNotIn(IDENTITY_MISMATCHED, text)

    def test_unparseable_idn_is_uncomparable(self):
        # 读不出身份要说“无法比较”：会话占用或重启不该看起来像接错了设备。
        for reply in (None, "", "RIGOL TECHNOLOGIES,DP832A", " ,DP832A,,"):
            with self.subTest(reply=reply):
                status, text = supply_identity_verdict("DP8B214901519", reply)
                self.assertEqual(status, IDENTITY_UNCOMPARABLE)
                self.assertIn("*IDN? 应答读不出设备身份", text)

    def test_order_number_serial_form_is_compared_on_the_serial_part(self):
        # R&S 的 ``订单号/序列号``：只比序列号，订单号不是身份。
        idn = "Rohde&Schwarz,SMW200A,1412.0000K02/105462,5.20.043.47"
        self.assertEqual(supply_identity_verdict("105462", idn)[0], IDENTITY_MATCHED)
        self.assertEqual(
            supply_identity_verdict("1412.0000K02", idn)[0], IDENTITY_MISMATCHED
        )


class AuditPlanTests(unittest.TestCase):
    """装配计划把详情文本交给日志，逐台电源一行身份核对。"""

    def test_plan_carries_the_identity_text_of_every_routed_supply(self):
        plan = hardware_assembly_plan(_two_supply_config())
        identity = {supply.supply_id: supply.identity_text for supply in plan.supplies}
        self.assertEqual(
            identity["DP8B1519"],
            supply_identity_text(
                "DP8B1519",
                "DP832A",
                "DP8B214901519",
                "rigol technologies:dp8b214901519",
            ),
        )
        # 只有集合键的电源：details 就只有它的键，不推导型号/序列号。
        self.assertEqual(identity["DP8B0950"], "DP8B0950")
        self.assertEqual(set(identity), set(plan.supply_labels))

    def test_plan_details_do_not_change_the_routing_tokens(self):
        # 显示与审计文本不参与路由：通道仍然是显式的 ``supply_id/CHx``。
        plan = hardware_assembly_plan(_two_supply_config())
        self.assertEqual(
            plan.all_channels,
            {
                "gate": "DP8B1519/CH1",
                "drain": "DP8B1519/CH2",
            },
        )
        self.assertEqual(plan.power_settings["gate"]["voltage_v"], 2.8)

    def test_plan_maps_every_routing_token_to_its_log_text(self):
        """通道动作日志的显示文本与路由 token 一一对应，且在拼 token 的地方生成。"""
        config = _two_supply_config()
        config["instruments"]["power_supplies"]["DP8B1519"]["label"] = "现场 A 电源"
        plan = hardware_assembly_plan(config)

        self.assertEqual(
            {
                token: text
                for token, text in plan.channel_labels.items()
                if token.startswith("DP8B1519/")
            },
            {
                "DP8B1519/CH1": "现场 A 电源/CH1",
                "DP8B1519/CH2": "现场 A 电源/CH2",
            },
        )
        # 没有标签的电源：显示文本就等于路由 token，不凭空补一个标签。
        self.assertEqual(plan.channel_labels["DP8B0950/CH1"], "DP8B0950/CH1")
        # 路由值不受显示文本影响。
        self.assertEqual(plan.all_channels["gate"], "DP8B1519/CH1")
        # 每个被分配的通道（DUT 与驱动功放）都有日志写法，键就是路由 token。
        assigned = set(plan.dut_channels.values()) | set(plan.driver_channels.values())
        self.assertEqual(set(plan.channel_labels), assigned)


class SharedImplementationTests(unittest.TestCase):
    """显示/日志文本不各写一套：静态守住调用点。"""

    def test_projection_delegates_to_the_domain_display_rule(self):
        source = PROJECTION_SOURCE.read_text(encoding="utf-8")
        self.assertIn("from domain.instruments.labels import supply_display_label", source)
        self.assertIn("return supply_display_label(supply_id, model)", source)

    def test_power_tabs_use_the_shared_identity_text(self):
        # 电源标签页的标题与提示由 ``config_power_section`` 构建（步骤 13 拆出），因此
        # 门禁盯的是那个模块：身份文本仍然只有 ``supply_identity_text`` 一份实现。
        source = POWER_SECTION_SOURCE.read_text(encoding="utf-8")
        self.assertIn("from domain.instruments.labels import supply_identity_text", source)
        self.assertIn("page.power_tabs.setTabToolTip(", source)

    def test_measurement_factory_logs_the_identity_line(self):
        # 计划侧（步骤 15 拆出 ``instrument.assembly_plan``）：详情文本只算一次，
        # 以 ``SupplyPlan.identity_text`` 交给构造方，不再各自从配置里挑字段。
        plan = PLAN_SOURCE.read_text(encoding="utf-8")
        self.assertIn("identity_text=supply_identity_text(", plan)
        # 构造侧：连接每台电源时写一行身份核对，并给出核对结论。
        source = FACTORY_SOURCE.read_text(encoding="utf-8")
        self.assertIn("生产电源身份核对", source)
        self.assertIn("设备应答 *IDN?", source)
        # 身份核对必须给出结论，不能只把两份身份并排打印。
        self.assertIn("supply_identity_verdict(", source)
        self.assertIn("核对结果", source)
        self.assertIn("supply.identity_text", source)

    def test_channel_logs_use_the_shared_channel_text(self):
        # 通道动作日志（供电控制与急停）用同一份 ``短标签/CHx`` 写法，不各自拼。
        for source_path in (POWER_CONTROL_SOURCE, ADAPTER_SOURCE):
            with self.subTest(path=source_path.name):
                source = source_path.read_text(encoding="utf-8")
                self.assertIn("channel_labels", source)
                self.assertIn("channel_display", source)
        # 日志写法在拼路由 token 的同一处生成（步骤 15 之后在供电分配解析模块里）。
        routing = ROUTING_SOURCE.read_text(encoding="utf-8")
        self.assertIn('{**driver_channel_labels, **dut_channel_labels}', routing)
        self.assertIn("supply_channel_text(", routing)


class ConnectionDiagramTests(unittest.TestCase):
    """连接图是静态拓扑示意：不含电源编号，也就没有要改成短标签的文本。"""

    def test_connection_diagrams_do_not_name_power_slots(self):
        source = DIAGRAMS_SOURCE.read_text(encoding="utf-8")
        for slot in ("PS1", "PS2", "PS3", "PS4"):
            with self.subTest(slot=slot):
                self.assertNotIn(slot, source)


if __name__ == "__main__":
    unittest.main()
