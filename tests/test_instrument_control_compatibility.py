"""``instrument_control`` 根兼容入口的契约测试。

本模块验证根入口是显式符号转发、转发对象与 ``instrument`` 正式包实现同一，
并通过根入口名构造历史兼容控制器以证明转发确实生效。

历史内部 patch 点（``pyvisa.ResourceManager`` / ``load_config_file``）已随实现
迁到 :mod:`instrument.legacy_control`，不再是根入口的受支持契约；这里不做
“必须消失”的反向断言，只固定仍然承诺的公开契约。
"""

import ast
import json
import tempfile
import unittest
from pathlib import Path

import instrument_control
from instrument import legacy_control, power_roles
from tests.fakes import FakeResourceManager, FakeVisaResource


ROOT = Path(__file__).resolve().parents[1]
ROOT_MODULE = ROOT / "instrument_control.py"
SUPPORTED_SYMBOLS = ("InstrumentControl", "resolve_power_channel_role")


def _compat_config():
    return {
        "instruments": {
            "signal_generator": {"address": "FAKE::SG", "enabled": True},
            "spectrum_analyzer": {"address": "FAKE::SA", "enabled": False},
            "power_supplies": {
                "PS": {
                    "address": "FAKE::PS",
                    "enabled": True,
                    "channels": {
                        "CH1": {
                            "voltage": {"value": 2.8, "protection": 3.0, "protection_enabled": True},
                            "current": {"value": 0.1, "protection": 0.2, "protection_enabled": True},
                        },
                        "CH2": {
                            "voltage": {"value": 28.0, "protection": 30.0, "protection_enabled": False},
                            "current": {"value": 1.0, "protection": 1.2, "protection_enabled": False},
                        },
                    },
                }
            },
        },
        "driver_mode": {"enabled": False},
        "power_supply_assignment": {
            "driver_amplifier": {"supplies": {}},
            "dut_amplifier": {"supplies": {"dut": {"name": "PS", "channel": ["CH1", "CH2"]}}},
        },
    }


class InstrumentControlCompatibilityTests(unittest.TestCase):
    def test_root_module_forwards_canonical_symbols(self):
        self.assertIs(
            instrument_control.InstrumentControl,
            legacy_control.InstrumentControl,
        )
        self.assertIs(
            instrument_control.resolve_power_channel_role,
            power_roles.resolve_power_channel_role,
        )

    def test_root_module_public_api_is_limited_to_supported_symbols(self):
        self.assertEqual(list(instrument_control.__all__), list(SUPPORTED_SYMBOLS))
        for name in SUPPORTED_SYMBOLS:
            self.assertTrue(hasattr(instrument_control, name), name)

    def test_root_module_has_no_business_definitions(self):
        tree = ast.parse(ROOT_MODULE.read_text(encoding="utf-8-sig"), str(ROOT_MODULE))
        definitions = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        self.assertEqual(definitions, [])
        self.assertTrue(
            any(
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "__all__"
                    for target in node.targets
                )
                for node in tree.body
            ),
            "instrument_control.py 必须显式声明 __all__",
        )

    def test_forwarded_controller_preserves_legacy_safety_order(self):
        """通过根入口构造，仍保持 RF -> 掉电(Drain -> Gate) -> 连接关闭 的安全顺序。"""
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text(json.dumps(_compat_config()), encoding="utf-8")
            signal_generator = FakeVisaResource("FAKE::SG")
            power_supply = FakeVisaResource("FAKE::PS")
            manager = FakeResourceManager(
                {"FAKE::SG": signal_generator, "FAKE::PS": power_supply}
            )
            controller = instrument_control.InstrumentControl(
                config_path,
                resource_manager=manager,
                sleep_fn=lambda _: None,
            )

            events = []
            original_rf_off = controller.rf_output_off
            original_power_off = controller.power_supply_off
            original_signal_close = signal_generator.close
            original_power_close = power_supply.close
            original_manager_close = manager.close

            def record_rf_off():
                events.append(("rf_off", None))
                return original_rf_off()

            def record_power_off(ps_name, channel):
                events.append(("power_off", (ps_name, channel)))
                return original_power_off(ps_name, channel)

            def record_signal_close():
                events.append(("close", "SG"))
                return original_signal_close()

            def record_power_close():
                events.append(("close", "PS"))
                return original_power_close()

            def record_manager_close():
                events.append(("close", "RM"))
                return original_manager_close()

            controller.rf_output_off = record_rf_off
            controller.power_supply_off = record_power_off
            signal_generator.close = record_signal_close
            power_supply.close = record_power_close
            manager.close = record_manager_close

            controller.safe_shutdown()

        rf_indexes = [index for index, (kind, _) in enumerate(events) if kind == "rf_off"]
        power_indexes = [index for index, (kind, _) in enumerate(events) if kind == "power_off"]
        close_indexes = [index for index, (kind, _) in enumerate(events) if kind == "close"]
        self.assertTrue(rf_indexes, "信号源应先执行 RF 关闭")
        self.assertTrue(power_indexes, "电源应执行掉电")
        self.assertTrue(close_indexes, "资源应执行关闭")
        self.assertLess(max(rf_indexes), min(power_indexes))
        self.assertLess(max(power_indexes), min(close_indexes))
        self.assertEqual(
            [value for kind, value in events if kind == "power_off"],
            [("PS", "CH2"), ("PS", "CH1")],
            "掉电顺序必须是 Drain -> Gate",
        )
        # 历史兼容控制器按既有契约在清理时关闭资源与注入的 ResourceManager；
        # 正式 ManagedInstrumentSession 只关闭自己拥有的端口，二者边界不同。
        self.assertTrue(signal_generator.closed)
        self.assertTrue(power_supply.closed)
        self.assertTrue(manager.closed)


if __name__ == "__main__":
    unittest.main()
