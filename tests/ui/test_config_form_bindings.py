"""设备绑定读取与规范化模块的独立测试。

这一层只做纯数据：``device_bindings`` -> ``supply_id`` 集合、旧结构折叠、不可用记录
的问题清单。因此这里的用例不需要 QApplication、不需要表单控件，也不需要另一块表单
（测试参数、电源通道、供电分配）的 Qt 状态。

最后一组是职责边界：绑定规范化只有 ``config_form_bindings`` 一处实现，表单状态模块
不再重复定义这些函数，也不再自己读写绑定结构。
"""

import ast
import unittest
from pathlib import Path

from presentation.qt import config_form_bindings as bindings_module
from presentation.qt.config_form_bindings import (
    LEGACY_BINDING_SOURCE,
    POWER_SUPPLY_BINDINGS,
    binding_address,
    binding_device_key,
    binding_records,
    bound_power_supplies,
    bound_power_supply_entries,
    effective_device_bindings,
    power_supply_binding_problems,
    power_supply_bindings,
    supply_id_problem,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BINDINGS_SOURCE = PROJECT_ROOT / "presentation" / "qt" / "config_form_bindings.py"
STATE_SOURCE = PROJECT_ROOT / "presentation" / "qt" / "config_form_state.py"

# 绑定规范化的公开入口：只在这一个模块里实现。
BINDING_ENTRY_POINTS = (
    "binding_address",
    "binding_device_key",
    "binding_records",
    "bound_power_supplies",
    "bound_power_supply_entries",
    "effective_device_bindings",
    "power_supply_binding_problems",
    "power_supply_bindings",
    "supply_id_problem",
)


def bound_supplies(*supply_ids):
    """按给定 ``supply_id`` 造一份电源绑定集合（每台各带地址与身份）。"""
    return {
        POWER_SUPPLY_BINDINGS: {
            supply_id: {
                "device_key": f"rigol technologies:{supply_id.casefold()}",
                "visa_address": f"TCPIP0::192.168.1.{index}::inst0::INSTR",
            }
            for index, supply_id in enumerate(supply_ids, 1)
        }
    }


class BindingReadTests(unittest.TestCase):
    """读取绑定记录：地址、身份与扁平展开。"""

    def test_binding_address_reads_only_the_visa_address(self):
        self.assertEqual(
            binding_address({"visa_address": " TCPIP0::192.168.1.102::inst0::INSTR "}),
            "TCPIP0::192.168.1.102::inst0::INSTR",
        )
        # 地址不是文本、记录不是对象：都按“没有地址”处理，不去猜别的字段。
        self.assertEqual(binding_address({"visa_address": None}), "")
        self.assertEqual(binding_address({"address": "TCPIP0::192.168.1.102::inst0::INSTR"}), "")
        self.assertEqual(binding_address(None), "")

    def test_binding_device_key_prefers_the_record_identity(self):
        self.assertEqual(
            binding_device_key({"device_key": "RIGOL TECHNOLOGIES:DP8B214901519"}),
            "rigol technologies:dp8b214901519",
        )
        # 旧记录只有厂商与序列号：拼出同一个键，不按序列号单独匹配。
        self.assertEqual(
            binding_device_key({"vendor": "RIGOL TECHNOLOGIES", "serial": "DP8B214901519"}),
            "rigol technologies:dp8b214901519",
        )
        self.assertIsNone(binding_device_key({"serial": "DP8B214901519"}))

    def test_binding_records_flatten_roles_and_bound_supplies(self):
        records = binding_records(
            {
                "signal_generator": {"visa_address": "TCPIP0::192.168.1.201::inst0::INSTR"},
                **bound_supplies("DP8B1519"),
            }
        )

        self.assertEqual(
            [key for key, _record in records], ["signal_generator", "DP8B1519"]
        )

    def test_legacy_config_addresses_become_unconfirmed_bindings_for_single_devices(self):
        """只有地址的旧配置迁移成信号源/频谱仪绑定；电源地址不是身份，要求重新扫描。"""
        config = {
            "instruments": {
                "signal_generator": {"address": "TCPIP0::192.168.1.201::inst0::INSTR"},
                "spectrum_analyzer": {
                    "address": "TCPIP0::192.168.1.202::inst0::INSTR",
                    "enabled": False,
                },
                "power_supplies": {
                    "PS1": {"address": "TCPIP0::192.168.1.101::inst0::INSTR", "enabled": True},
                    "PS2": {"address": "TCPIP0::192.168.1.102::inst0::INSTR", "enabled": False},
                },
            }
        }

        bindings = effective_device_bindings(config)

        # 启用且地址非空的项被迁移；禁用项不会被迁移动作重新启用。
        self.assertEqual(
            bindings["signal_generator"]["visa_address"], "TCPIP0::192.168.1.201::inst0::INSTR"
        )
        self.assertEqual(bindings["signal_generator"]["source"], LEGACY_BINDING_SOURCE)
        self.assertNotIn("spectrum_analyzer", bindings)
        self.assertNotIn(POWER_SUPPLY_BINDINGS, bindings)
        self.assertEqual(bound_power_supplies(bindings), [])

    def test_explicit_empty_bindings_do_not_resurrect_legacy_addresses(self):
        """配置里已有（空的）device_bindings 时，旧地址不得再被当成绑定。"""
        config = {
            "device_bindings": {},
            "instruments": {
                "signal_generator": {"address": "TCPIP0::192.168.1.201::inst0::INSTR"}
            },
        }

        self.assertEqual(effective_device_bindings(config), {})


class BindingNormalizationTests(unittest.TestCase):
    """规范化：可用集合的成员、顺序与不合格记录的诊断。"""

    def test_legacy_power_bindings_migrate_by_identity_and_label(self):
        """步骤 5 的过渡绑定（按旧槽位落键、记录内带短标签与身份）折叠成 supply_id 集合。"""
        bindings = effective_device_bindings(
            {
                "device_bindings": {
                    "PS3": {
                        "role": "power_supply",
                        "label": "DP8B1519",
                        "supply_id": "DP8B1519",
                        "serial": "DP8B214901519",
                        "device_key": "rigol technologies:dp8b214901519",
                        "visa_address": "TCPIP0::192.168.1.102::inst0::INSTR",
                        "source": "scan",
                    },
                    "PS1": {
                        # 只有地址的旧绑定：没有序列号也没有短标签，不迁移。
                        "visa_address": "TCPIP0::192.168.1.101::inst0::INSTR",
                        "source": LEGACY_BINDING_SOURCE,
                    },
                }
            }
        )

        self.assertEqual(list(bindings[POWER_SUPPLY_BINDINGS]), ["DP8B1519"])
        record = bindings[POWER_SUPPLY_BINDINGS]["DP8B1519"]
        self.assertEqual(record["device_key"], "rigol technologies:dp8b214901519")
        # 集合的键就是引用 ID：记录里不再重复写 role/label/supply_id。
        for field in ("role", "label", "supply_id"):
            self.assertNotIn(field, record)
        self.assertEqual(bound_power_supplies(bindings), ["DP8B1519"])

    def test_duplicate_power_bindings_for_one_device_keep_the_first(self):
        """同一台设备不能出现两次：手改文件造出的重复身份只保留第一条。"""
        supplies = power_supply_bindings(
            {
                POWER_SUPPLY_BINDINGS: {
                    "DP8B1519": {
                        "device_key": "rigol technologies:dp8b214901519",
                        "visa_address": "TCPIP0::192.168.1.102::inst0::INSTR",
                    },
                    "DP8B1519-COPY": {
                        "device_key": "RIGOL TECHNOLOGIES:DP8B214901519",
                        "visa_address": "TCPIP0::192.168.1.109::inst0::INSTR",
                    },
                }
            }
        )

        self.assertEqual(list(supplies), ["DP8B1519"])

    def test_power_binding_without_identity_is_not_a_bound_supply(self):
        """新结构里的记录也必须带身份：只有地址的记录不能成为可分配电源。"""
        bindings = {
            POWER_SUPPLY_BINDINGS: {
                "DP8B1519": {"visa_address": "TCPIP0::192.168.1.102::inst0::INSTR"}
            }
        }

        self.assertEqual(power_supply_bindings(bindings), {})
        self.assertEqual(bound_power_supplies(bindings), [])
        problems = power_supply_binding_problems(bindings)
        self.assertEqual(len(problems), 1)
        self.assertIn("DP8B1519", problems[0])
        self.assertIn("缺少设备身份", problems[0])

    def test_power_binding_identity_must_match_vendor_and_serial(self):
        """``device_key`` 与厂商/序列号矛盾时不得成为可用电源。"""
        bindings = {
            POWER_SUPPLY_BINDINGS: {
                "DP8B1519": {
                    "device_key": "rigol technologies:dp8b174400885",
                    "vendor": "RIGOL TECHNOLOGIES",
                    "serial": "DP8B214901519",
                    "visa_address": "TCPIP0::192.168.1.102::inst0::INSTR",
                }
            }
        }

        self.assertEqual(power_supply_bindings(bindings), {})
        problems = power_supply_binding_problems(bindings)
        self.assertIn("设备身份与厂商/序列号不一致", "；".join(problems))

    def test_binding_with_only_a_vendor_must_match_the_device_key(self):
        """只声明厂商时同样要核对：厂商写着 A、键指向 B 的记录不得成为可用电源。

        只声明厂商、没有序列号是“身份片段不全”，不是“不用核对”：放过它等于让地址连到
        一台键与厂商各说一半的设备。
        """
        mismatched = {
            POWER_SUPPLY_BINDINGS: {
                "DP8B1519": {
                    "device_key": "rigol technologies:dp8b214901519",
                    "vendor": "KEYSIGHT TECHNOLOGIES",
                    "visa_address": "TCPIP0::192.168.1.102::inst0::INSTR",
                }
            }
        }
        self.assertEqual(power_supply_bindings(mismatched), {})
        problems = "；".join(power_supply_binding_problems(mismatched))
        self.assertIn("设备身份与厂商不一致", problems)

        matched = {
            POWER_SUPPLY_BINDINGS: {
                "DP8B1519": {
                    "device_key": "RIGOL TECHNOLOGIES:DP8B214901519",
                    # 厂商只是大小写与空白不同：同一个厂商标识，不算矛盾。
                    "vendor": " rigol  technologies ",
                    "visa_address": "TCPIP0::192.168.1.102::inst0::INSTR",
                }
            }
        }
        self.assertEqual(list(power_supply_bindings(matched)), ["DP8B1519"])
        self.assertEqual(power_supply_binding_problems(matched), [])

    def test_binding_with_only_a_serial_must_match_the_device_key(self):
        """只声明序列号时同样要核对：序列号与键尾不一致的记录不得成为可用电源。"""
        mismatched = {
            POWER_SUPPLY_BINDINGS: {
                "DP8B1519": {
                    "device_key": "rigol technologies:dp8b174400885",
                    "serial": "DP8B214901519",
                    "visa_address": "TCPIP0::192.168.1.102::inst0::INSTR",
                }
            }
        }
        self.assertEqual(power_supply_bindings(mismatched), {})
        self.assertIn(
            "设备身份与序列号不一致", "；".join(power_supply_binding_problems(mismatched))
        )

        matched = {
            POWER_SUPPLY_BINDINGS: {
                "DP8B1519": {
                    "device_key": "rigol technologies:dp8b214901519",
                    "serial": "dp8b214901519",
                    "visa_address": "TCPIP0::192.168.1.102::inst0::INSTR",
                }
            }
        }
        self.assertEqual(list(power_supply_bindings(matched)), ["DP8B1519"])

    def test_declared_identity_parts_need_a_vendor_serial_device_key(self):
        """声明了厂商/序列号却给不出 ``厂商:序列号`` 形式的键：身份无法核对，不算可用。"""
        bindings = {
            POWER_SUPPLY_BINDINGS: {
                "DP8B1519": {
                    "device_key": "DP8B1519",
                    "serial": "DP8B214901519",
                    "visa_address": "TCPIP0::192.168.1.102::inst0::INSTR",
                }
            }
        }

        self.assertEqual(power_supply_bindings(bindings), {})
        self.assertIn(
            "设备身份不是 厂商:序列号 形式", "；".join(power_supply_binding_problems(bindings))
        )

    def test_device_key_alone_is_a_complete_identity(self):
        """只写 ``device_key`` 的记录仍然可用：键是唯一身份，没有第二份取值要核对。"""
        bindings = {
            POWER_SUPPLY_BINDINGS: {
                "DP8B1519": {
                    "device_key": "rigol technologies:dp8b214901519",
                    "visa_address": "TCPIP0::192.168.1.102::inst0::INSTR",
                }
            }
        }

        self.assertEqual(list(power_supply_bindings(bindings)), ["DP8B1519"])
        self.assertEqual(bound_power_supplies(bindings), ["DP8B1519"])
        self.assertEqual(power_supply_binding_problems(bindings), [])

    def test_power_binding_key_must_be_a_usable_supply_id(self):
        """含空白或通道分隔符的键不能当引用：它会被 ``supply_id/CHx`` 路由截断。"""
        bindings = {
            POWER_SUPPLY_BINDINGS: {
                "DP8B 1519": {
                    "device_key": "rigol technologies:dp8b214901519",
                    "visa_address": "TCPIP0::192.168.1.102::inst0::INSTR",
                },
                "DP8B/1519": {
                    "device_key": "rigol technologies:dp8b174400885",
                    "visa_address": "TCPIP0::192.168.1.108::inst0::INSTR",
                },
            }
        }

        self.assertEqual(power_supply_bindings(bindings), {})
        problems = "；".join(power_supply_binding_problems(bindings))
        self.assertIn("不得含空白", problems)
        self.assertIn("不得含通道分隔符", problems)
        self.assertEqual(supply_id_problem("DP8B1519"), None)
        self.assertIn("不得含空白", supply_id_problem("DP8B 1519"))
        # 首尾空白不裁剪：写错的引用必须报出来，不能被“洗干净”后当成另一台设备。
        self.assertIn("首尾空白", supply_id_problem(" DP8B1519"))
        self.assertEqual(
            power_supply_bindings(
                {
                    POWER_SUPPLY_BINDINGS: {
                        " DP8B1519": {
                            "device_key": "rigol technologies:dp8b214901519",
                            "visa_address": "TCPIP0::192.168.1.102::inst0::INSTR",
                        }
                    }
                }
            ),
            {},
        )

    def test_duplicate_power_binding_is_reported_not_only_dropped(self):
        """重复身份仍然只保留第一条，但必须能说出被丢掉的是哪一条。"""
        bindings = {
            POWER_SUPPLY_BINDINGS: {
                "DP8B1519": {
                    "device_key": "rigol technologies:dp8b214901519",
                    "visa_address": "TCPIP0::192.168.1.102::inst0::INSTR",
                },
                "DP8B1519-COPY": {
                    "device_key": "rigol technologies:dp8b214901519",
                    "visa_address": "TCPIP0::192.168.1.109::inst0::INSTR",
                },
            }
        }

        self.assertEqual(list(power_supply_bindings(bindings)), ["DP8B1519"])
        problems = "；".join(power_supply_binding_problems(bindings))
        self.assertIn("同一台设备只能有一条电源绑定", problems)
        self.assertIn("DP8B1519-COPY", problems)

    def test_bound_power_supplies_follow_the_bound_collection_size(self):
        """绑定 1、2、5 台电源时，可用集合就是这 1、2、5 个 ``supply_id``。"""
        self.assertEqual(bound_power_supplies(bound_supplies("DP8B1519")), ["DP8B1519"])
        self.assertEqual(
            bound_power_supplies(bound_supplies("DP8B1519", "DP8B0885")),
            ["DP8B1519", "DP8B0885"],
        )
        five = ("DP8B1519", "DP8B0885", "DP8B0950", "DP8B1191", "DP8B1216")
        self.assertEqual(bound_power_supplies(bound_supplies(*five)), list(five))
        # 没有地址的绑定连不上，也不算可用电源；集合大小仍由绑定决定，不看静态启用标志。
        bindings = bound_supplies("DP8B1519", "DP8B0885")
        bindings[POWER_SUPPLY_BINDINGS]["DP8B0885"].pop("visa_address")
        self.assertEqual(bound_power_supplies(bindings), ["DP8B1519"])

    def test_bound_supply_entries_carry_the_record_in_binding_order(self):
        """界面的候选条目与可用集合同序，并且带着绑定记录里的真实型号。"""
        bindings = bound_supplies("DP8B1519", "DP8B0885")
        bindings[POWER_SUPPLY_BINDINGS]["DP8B0885"]["model"] = "DP832A"

        entries = bound_power_supply_entries(bindings)

        self.assertEqual(list(entries), ["DP8B1519", "DP8B0885"])
        self.assertEqual(entries["DP8B0885"]["model"], "DP832A")
        # 与可用集合完全一致：没有地址的绑定不出现在任何一侧。
        bindings[POWER_SUPPLY_BINDINGS]["DP8B0885"].pop("visa_address")
        self.assertEqual(list(bound_power_supply_entries(bindings)), bound_power_supplies(bindings))


class BindingOwnershipTests(unittest.TestCase):
    """职责边界：绑定规范化只有一处实现，而且这一层不需要 Qt。"""

    def test_the_module_declares_its_public_entries(self):
        exported = set(bindings_module.__all__)

        for name in BINDING_ENTRY_POINTS:
            self.assertIn(name, exported, f"{name} 是绑定模块的公开入口")
            self.assertTrue(
                callable(getattr(bindings_module, name)), f"{name} 必须可直接调用"
            )

    def test_the_module_imports_no_qt(self):
        tree = ast.parse(BINDINGS_SOURCE.read_text(encoding="utf-8"))
        imported = {
            node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        }
        imported |= {
            alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
            for alias in node.names
        }

        self.assertFalse(
            [module for module in imported if module.split(".")[0] == "PySide6"],
            "绑定读取与规范化不碰 Qt：它必须能在没有 QApplication 的情况下测试",
        )

    def test_the_form_state_module_no_longer_implements_binding_normalization(self):
        """拆出来的职责不得在原模块留下第二份实现（也不留转发壳）。"""
        tree = ast.parse(STATE_SOURCE.read_text(encoding="utf-8"))
        defined = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}

        self.assertEqual(defined & set(BINDING_ENTRY_POINTS), set())

    def test_the_form_state_module_consumes_the_binding_module(self):
        tree = ast.parse(STATE_SOURCE.read_text(encoding="utf-8"))
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level
        }

        self.assertIn("config_form_bindings", imported)


if __name__ == "__main__":
    unittest.main()
