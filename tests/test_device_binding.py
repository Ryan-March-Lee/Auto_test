"""设备绑定应用服务测试（GUI 重构步骤 5，不需要 Qt）。

覆盖写入成功、空选择、角色/引用/地址/身份问题、同一台设备重复绑定、模拟数据标记与
“整批拒绝不留下半份绑定”，以及绑定记录的身份自洽判定。页面侧只验证它把写入委托给
application 服务，不再自己保留三轮校验循环。
"""

import ast
import unittest
from pathlib import Path

from application.device_binding import (
    ApplyDeviceSelectionRequest,
    DeviceBindingResult,
    DeviceBindingService,
    LEGACY_BINDING_SOURCE,
    POWER_SUPPLY_BINDINGS,
    SelectedDevice,
    binding_device_key,
    binding_identity_problem,
    legacy_binding_count,
    supply_id_problem,
)
from domain.instruments.models import DEVICE_ROLES, POWER_SUPPLY_ROLE

ROOT = Path(__file__).resolve().parents[1]


def signal_generator(**overrides):
    """一台可直接写入的信号源；覆盖字段用来构造各类问题。"""
    device = {
        "role": "signal_generator",
        "address": "TCPIP0::192.168.1.201::inst0::INSTR",
        "vendor": "Rohde&Schwarz",
        "model": "SMW200A",
        "label": "5462",
        "full_code": "105462",
        "device_key": "rohde&schwarz:105462",
        "ip": "192.168.1.201",
        "port": 5025,
        "known": True,
    }
    device.update(overrides)
    return device


def power_supply(supply_id="DP8B1519", full_code="DP8B214901519", index=102, **overrides):
    """一台可直接写入的电源；``supply_id`` 是配置引用 ID。"""
    device = {
        "role": "power_supply",
        "supply_id": supply_id,
        "label": supply_id,
        "address": f"TCPIP0::192.168.1.{index}::inst0::INSTR",
        "vendor": "RIGOL TECHNOLOGIES",
        "model": "DP832A",
        "full_code": full_code,
        "device_key": f"rigol technologies:{full_code.casefold()}",
        "ip": f"192.168.1.{index}",
        "port": 5555,
        "known": True,
    }
    device.update(overrides)
    return device


def apply(*devices):
    return DeviceBindingService().apply(
        ApplyDeviceSelectionRequest.from_assignments(devices)
    )


class DeviceBindingSuccessTests(unittest.TestCase):
    """成功路径：整批替换后的绑定结构与显示摘要。"""

    def test_role_devices_and_power_supplies_are_written_in_one_batch(self):
        result = apply(
            signal_generator(),
            power_supply(),
            power_supply("DP8B0885", "DP8B174400885", 108),
        )

        self.assertTrue(result.ok, result.messages())
        self.assertEqual(
            set(result.bindings), {"signal_generator", POWER_SUPPLY_BINDINGS}
        )
        signal = result.bindings["signal_generator"]
        self.assertEqual(signal["visa_address"], "TCPIP0::192.168.1.201::inst0::INSTR")
        self.assertEqual(signal["device_key"], "rohde&schwarz:105462")
        self.assertEqual(signal["label"], "5462")
        self.assertEqual(signal["source"], "scan")
        self.assertTrue(signal["known"])
        self.assertEqual(
            list(result.bindings[POWER_SUPPLY_BINDINGS]), ["DP8B1519", "DP8B0885"]
        )
        self.assertEqual(
            result.bindings[POWER_SUPPLY_BINDINGS]["DP8B1519"]["visa_address"],
            "TCPIP0::192.168.1.102::inst0::INSTR",
        )
        # 集合的键就是引用 ID：记录里不再重复写 label/supply_id。
        for field in ("label", "supply_id", "role"):
            self.assertNotIn(field, result.bindings[POWER_SUPPLY_BINDINGS]["DP8B1519"])

    def test_applied_summary_carries_what_the_page_needs_to_display(self):
        result = apply(signal_generator(), power_supply())

        self.assertEqual(len(result.applied), 2)
        signal, supply = result.applied
        self.assertEqual(signal.role, "signal_generator")
        self.assertEqual(signal.model, "SMW200A")
        self.assertIsNone(signal.supply_id)
        self.assertEqual(supply.role, "power_supply")
        self.assertEqual(supply.supply_id, "DP8B1519")

    def test_simulated_devices_are_marked_in_the_record_and_the_result(self):
        result = apply(signal_generator(simulated=True))

        self.assertTrue(result.simulated)
        self.assertEqual(result.bindings["signal_generator"]["source"], "simulated")

    def test_result_snapshots_the_written_bindings(self):
        result = apply(signal_generator())
        exported = result.to_mapping()
        exported["signal_generator"]["visa_address"] = "TCPIP0::1.2.3.4::inst0::INSTR"

        self.assertEqual(
            result.bindings["signal_generator"]["visa_address"],
            "TCPIP0::192.168.1.201::inst0::INSTR",
        )
        self.assertEqual(
            result.to_mapping()["signal_generator"]["visa_address"],
            "TCPIP0::192.168.1.201::inst0::INSTR",
        )

    def test_the_request_accepts_explicit_selected_devices(self):
        request = ApplyDeviceSelectionRequest(
            devices=(
                SelectedDevice(
                    role="signal_generator",
                    address="TCPIP0::192.168.1.201::inst0::INSTR",
                    vendor="Rohde&Schwarz",
                    model="SMW200A",
                    label="5462",
                    full_code="105462",
                    device_key="rohde&schwarz:105462",
                ),
            )
        )
        result = DeviceBindingService().apply(request)

        self.assertTrue(result.ok, result.messages())
        self.assertIn("signal_generator", result.bindings)


class DeviceBindingRejectionTests(unittest.TestCase):
    """失败路径：整批拒绝、返回结构化问题、不产生半份绑定。"""

    def assert_rejected(self, result: DeviceBindingResult, fragment: str):
        self.assertIsInstance(result, DeviceBindingResult)
        self.assertFalse(result.ok)
        self.assertEqual(result.to_mapping(), {})
        self.assertEqual(len(result.issues), 1)
        self.assertIn(fragment, result.issues[0].message)
        self.assertTrue(result.issues[0].code.startswith("device_binding."))
        self.assertTrue(result.issues[0].path)

    def test_empty_selection_is_rejected(self):
        self.assert_rejected(apply(), "请先在发现结果中勾选本次测试所需设备")

    def test_unknown_role_is_rejected(self):
        self.assert_rejected(apply({"role": "nope"}), "未知的测试角色")

    def test_duplicate_single_device_roles_are_rejected(self):
        result = apply(
            signal_generator(),
            signal_generator(ip="192.168.1.211", full_code="999999"),
        )
        self.assert_rejected(result, "重复指定")

    def test_power_supply_without_a_reference_is_rejected(self):
        result = apply(
            {
                "role": "power_supply",
                "address": "TCPIP0::192.168.1.102::inst0::INSTR",
                "vendor": "RIGOL TECHNOLOGIES",
                "full_code": "DP8B214901519",
                "device_key": "rigol technologies:dp8b214901519",
            }
        )
        self.assert_rejected(result, "还没有缩写设备码")

    def test_unusable_power_reference_is_rejected(self):
        self.assert_rejected(
            apply(power_supply("DP8B/1519")), "不能作为配置引用"
        )
        self.assert_rejected(apply(power_supply("DP8B 1519")), "不能作为配置引用")

    def test_two_rows_pointing_at_the_same_power_reference_are_rejected(self):
        self.assert_rejected(
            apply(power_supply(), power_supply(index=8)), "都指向电源"
        )

    def test_empty_address_is_rejected(self):
        self.assert_rejected(
            apply(signal_generator(address="  ")), "发现地址为空"
        )

    def test_missing_full_code_is_rejected(self):
        self.assert_rejected(
            apply(signal_generator(full_code="")), "缺少设备身份（完整设备码）"
        )

    def test_identity_disagreeing_with_vendor_and_full_code_is_rejected(self):
        self.assert_rejected(
            apply(signal_generator(device_key="keysight technologies:105462")),
            "设备身份与完整设备码不一致",
        )

    def test_device_key_without_vendor_must_carry_the_same_full_code(self):
        self.assert_rejected(
            apply(
                signal_generator(
                    vendor="", device_key="rohde&schwarz:999999"
                )
            ),
            "设备身份与完整设备码不一致",
        )

    def test_power_supply_without_a_usable_identity_is_rejected(self):
        self.assert_rejected(
            apply(
                power_supply(
                    vendor="",
                    device_key="",
                    label="DP8B1519",
                )
            ),
            "缺少可用于绑定的设备身份",
        )

    def test_the_same_device_cannot_be_bound_as_two_power_supplies(self):
        first = power_supply("DP8B1519")
        second = power_supply(
            "DP8B0885",
            "DP8B214901519",
            108,
            device_key="rigol technologies:dp8b214901519",
        )
        self.assert_rejected(apply(first, second), "同一台设备不能重复绑定")

    def test_a_rejected_batch_keeps_none_of_the_earlier_valid_records(self):
        result = apply(
            signal_generator(),
            power_supply("DP8B/1519"),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.bindings, {})
        self.assertEqual(result.applied, ())


class BindingRecordIdentityTests(unittest.TestCase):
    """读取边界的记录级身份判定也归 application（R13 的记录级部分）。"""

    def test_a_consistent_record_has_no_problem(self):
        record = {
            "vendor": "RIGOL TECHNOLOGIES",
            "full_code": "DP8B214901519",
            "device_key": "rigol technologies:dp8b214901519",
        }
        self.assertEqual(binding_device_key(record), "rigol technologies:dp8b214901519")
        self.assertIsNone(binding_identity_problem(record))

    def test_a_record_with_only_a_device_key_is_complete(self):
        record = {"device_key": "rigol technologies:dp8b214901519"}
        self.assertIsNone(binding_identity_problem(record))

    def test_identity_fragments_must_match_the_device_key(self):
        mismatched = {
            "device_key": "rigol technologies:dp8b214901519",
            "vendor": "KEYSIGHT TECHNOLOGIES",
        }
        self.assertIn("设备身份与厂商不一致", binding_identity_problem(mismatched))
        mismatched_code = {
            "device_key": "rigol technologies:dp8b214901519",
            "full_code": "DP8B174400885",
        }
        self.assertIn(
            "设备身份与完整设备码不一致",
            binding_identity_problem(mismatched_code),
        )

    def test_legacy_full_code_field_is_read_at_the_history_boundary(self):
        record = {
            "serial": "DP8B214901519",
            "device_key": "rigol technologies:dp8b214901519",
        }
        self.assertIsNone(binding_identity_problem(record))

    def test_a_record_without_any_identity_is_reported(self):
        self.assertIn(
            "缺少设备身份", binding_identity_problem({"visa_address": "sim::ps"})
        )

    def test_supply_reference_rules_are_owned_here(self):
        self.assertIsNone(supply_id_problem("DP8B1519"))
        self.assertIn("不得含空白", supply_id_problem("DP8B 1519"))
        self.assertIn("首尾空白", supply_id_problem(" DP8B1519"))
        self.assertIn("不得含通道分隔符", supply_id_problem("DP8B/1519"))


class PresentationDelegationTests(unittest.TestCase):
    """页面不再自己校验：写入只经过 application 服务。"""

    @classmethod
    def setUpClass(cls):
        source_path = ROOT / "presentation" / "qt" / "config_page.py"
        cls.source = source_path.read_text(encoding="utf-8-sig")
        cls.tree = ast.parse(cls.source)
        cls.page = next(
            node
            for node in cls.tree.body
            if isinstance(node, ast.ClassDef) and node.name == "ConfigPage"
        )
        cls.method = next(
            node
            for node in cls.page.body
            if isinstance(node, ast.FunctionDef) and node.name == "apply_device_selection"
        )
        cls.method_source = ast.get_source_segment(cls.source, cls.method)

    def test_page_submits_the_selection_to_the_view_model(self):
        """步骤 8 起页面经视图模型提交勾选结果；请求 DTO 与服务调用都在 application。"""
        self.assertIn("self.view_model.apply_device_selection(", self.method_source)
        self.assertNotIn("DeviceBindingService()", self.method_source)
        # “下一步”的兼容入口只把字典列表收敛成命令 DTO，再转给上面的写入方法。
        compat = next(
            node
            for node in self.page.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "apply_discovered_devices"
        )
        compat_source = ast.get_source_segment(self.source, compat)
        self.assertIn("self.apply_device_selection(request)", compat_source)

    def test_page_has_no_binding_validation_loop_left(self):
        for fragment in (
            "未知的测试角色",
            "make_device_key",
            "normalize_full_code",
            "supply_id_problem",
            "valid_roles",
            "同一台设备不能重复绑定",
        ):
            self.assertNotIn(fragment, self.method_source)

    def test_the_view_model_submits_the_request_to_the_application_service(self):
        view_model_source = (
            ROOT / "application" / "configuration_view_model.py"
        ).read_text(encoding="utf-8-sig")
        modules = {
            node.module
            for node in ast.walk(ast.parse(view_model_source))
            if isinstance(node, ast.ImportFrom) and not node.level
        }
        self.assertIn("application.device_binding", modules)
        self.assertIn(
            "ApplyDeviceSelectionRequest.from_assignments(", view_model_source
        )
        self.assertIn("self._binding_service.apply(", view_model_source)


class BindingSourceConstantTests(unittest.TestCase):
    """绑定来源标记只有一份定义：表现层只再导出，不另写字面量。"""

    def test_presentation_reexports_the_application_marker(self):
        from presentation.qt.config_form_bindings import (
            LEGACY_BINDING_SOURCE as presentation_source,
        )

        self.assertIs(presentation_source, LEGACY_BINDING_SOURCE)

    def test_only_the_binding_module_declares_the_literal(self):
        canonical = ROOT / "application" / "device_binding.py"
        literal = f'"{LEGACY_BINDING_SOURCE}"'
        for package in ("application", "presentation"):
            for path in (ROOT / package).rglob("*.py"):
                if path == canonical:
                    continue
                with self.subTest(path=path.relative_to(ROOT).as_posix()):
                    self.assertNotIn(
                        literal, path.read_text(encoding="utf-8-sig")
                    )

    def test_legacy_binding_count_reads_current_and_legacy_structures(self):
        current = {
            "signal_generator": {"source": LEGACY_BINDING_SOURCE},
            "spectrum_analyzer": {"source": "scan"},
            "power_supplies": {
                "DP8B1519": {"source": LEGACY_BINDING_SOURCE},
                "DP8B1520": {"source": "scan"},
            },
        }
        self.assertEqual(legacy_binding_count(current), 2)
        self.assertEqual(
            legacy_binding_count({"PS1": {"source": LEGACY_BINDING_SOURCE}}), 1
        )
        self.assertEqual(legacy_binding_count({"signal_generator": "not-a-record"}), 0)
        self.assertEqual(legacy_binding_count(None), 0)


class RequestDtoTests(unittest.TestCase):
    """命令 DTO 只接受显式设备记录，避免调用方偷偷塞进任意对象。"""

    def test_selected_device_reads_the_discovery_fields(self):
        device = SelectedDevice.from_mapping(signal_generator())
        self.assertEqual(device.role, "signal_generator")
        self.assertEqual(device.label, "5462")
        self.assertEqual(SelectedDevice.from_mapping(power_supply()).reference, "DP8B1519")

    def test_request_rejects_arbitrary_objects(self):
        with self.assertRaises(TypeError):
            ApplyDeviceSelectionRequest(devices=({"role": "signal_generator"},))
        with self.assertRaises(TypeError):
            DeviceBindingService().apply({"role": "signal_generator"})

    def test_a_non_object_device_record_is_a_caller_error_not_an_input_problem(self):
        """非对象条目是发现面板的契约错误：抛 ``TypeError``，不伪装成“未知角色”。"""
        with self.assertRaises(TypeError):
            ApplyDeviceSelectionRequest.from_assignments(["signal_generator"])
        with self.assertRaises(TypeError):
            SelectedDevice.from_mapping(None)

    def test_every_registered_role_is_accepted(self):
        """可写角色来自 ``domain.instruments`` 的 ``DEVICE_ROLES``，不在服务里另立清单。"""
        for role in DEVICE_ROLES:
            with self.subTest(role=role):
                device = (
                    power_supply()
                    if role == POWER_SUPPLY_ROLE
                    else signal_generator(role=role)
                )
                result = apply(device)
                self.assertTrue(result.ok, result.messages())
                expected_key = (
                    POWER_SUPPLY_BINDINGS if role == POWER_SUPPLY_ROLE else role
                )
                self.assertIn(expected_key, result.bindings)

    def test_request_from_assignments_accepts_none(self):
        self.assertEqual(ApplyDeviceSelectionRequest.from_assignments(None).devices, ())


if __name__ == "__main__":
    unittest.main()
