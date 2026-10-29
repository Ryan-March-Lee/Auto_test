"""单元测试：电源设备短标签生成器（重构计划步骤 2）。

规则来源是[电源设备身份与配置数据契约](../docs/电源设备身份与配置数据契约.md)第 3 节：
归一化、前缀提取、末四位与冲突扩展。覆盖验收要求——当前三台设备的期望结果、前导零、
大小写、空白、碰撞、纯数字序列号，以及无法生成时的可识别领域异常。

标签生成器只做纯计算：不写注册表、不读文件、不依赖 Qt 与网络（由 ``PurityTests`` 守住）。
"""

import ast
import json
import unittest
from pathlib import Path

from domain.instruments.labels import (
    LABEL_TAIL_WIDTH,
    PowerSupplyLabelError,
    generate_supply_label,
    generate_supply_labels,
    normalize_supply_serial,
    supply_label_candidates,
    supply_label_prefix,
)


ROOT = Path(__file__).parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "power_supply_identity"
LABELS_SOURCE = ROOT / "domain" / "instruments" / "labels.py"

# 契约第 3.2 节：现场三台 DP832A 与两台样例设备的期望标签。
FIELD_DEVICES = {
    "DP8B214901519": "DP8B1519",
    "DP8B174700950": "DP8B0950",
    "DP8B174400885": "DP8B0885",
    "DP8B234501191": "DP8B1191",
    "DP8B234701216": "DP8B1216",
}


def _fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


class NormalizeTests(unittest.TestCase):
    """序列号归一化：大小写、引号、空白与不可解析输入。"""

    def test_normalization_uppercases_and_strips_once(self):
        self.assertEqual(normalize_supply_serial(" dp8b214901519 "), "DP8B214901519")
        self.assertEqual(normalize_supply_serial('"dp8b214901519"'), "DP8B214901519")
        # 连续空白先折叠成一个内部空白，随即按“内部空白不可解析”拒绝。
        with self.assertRaises(PowerSupplyLabelError):
            normalize_supply_serial("DP8B  214901519")

    def test_quotes_and_spaces_need_two_rounds(self):
        # 契约第 3.1 节：单次 normalize_serial 只剥掉一层引号。
        self.assertEqual(normalize_supply_serial(' " dp8b214901519 " '), "DP8B214901519")

    def test_internal_whitespace_is_rejected_instead_of_silently_removed(self):
        with self.assertRaises(PowerSupplyLabelError) as raised:
            normalize_supply_serial("DP8B 2149 01519")
        self.assertIn("内部空白", str(raised.exception))

    def test_empty_serial_is_rejected(self):
        for value in ("", "   ", "''", '""', None):
            with self.subTest(value=value), self.assertRaises(PowerSupplyLabelError):
                normalize_supply_serial(value)

    def test_error_is_a_value_error_for_existing_handlers(self):
        self.assertTrue(issubclass(PowerSupplyLabelError, ValueError))


class PrefixTests(unittest.TestCase):
    """前缀：字母保留，数字仅在后紧跟字母时保留，遇到数字段或非字母数字即停止。"""

    def test_letter_digit_letter_prefix_is_kept(self):
        self.assertEqual(supply_label_prefix("DP8B214901519"), "DP8B")
        self.assertEqual(supply_label_prefix("DP8B174700950"), "DP8B")

    def test_pure_digit_serial_has_no_prefix(self):
        self.assertEqual(supply_label_prefix("105462"), "")

    def test_non_alphanumeric_terminates_prefix(self):
        # 契约第 3.3 节：点与斜杠终止前缀，前缀为空。
        self.assertEqual(supply_label_prefix("1412.0000K02/105462"), "")

    def test_prefix_stops_at_a_digit_run(self):
        self.assertEqual(supply_label_prefix("DP832A1000"), "DP")  # ``83`` 是连续数字段
        self.assertEqual(supply_label_prefix("ABC12345"), "ABC")


class CandidateTests(unittest.TestCase):
    """候选顺序：前缀 + 末四位 → 末五位 → …… → 完整归一化序列号。"""

    def test_candidates_start_with_last_four_then_widen(self):
        candidates = supply_label_candidates("DP8B151519")
        self.assertEqual(candidates[0], "DP8B1519")
        self.assertEqual(candidates[1], "DP8B51519")
        self.assertEqual(len(candidates), len(set(candidates)), "候选不得重复")

    def test_full_serial_is_the_last_resort_and_is_not_prefixed_twice(self):
        candidates = supply_label_candidates("DP8B1951519")
        self.assertEqual(candidates[-1], "DP8B1951519")
        self.assertNotIn("DP8BDP8B1951519", candidates)

    def test_short_serial_uses_itself_as_the_only_candidate(self):
        self.assertEqual(supply_label_candidates("A12"), ("A12",))
        self.assertEqual(supply_label_candidates("A1234"), ("A1234",))

    def test_prefix_that_almost_fills_the_serial_leaves_the_full_serial(self):
        # 前缀占满主体时，末四位会吞掉前缀（``ABCDBCD1``），候选只保留完整序列号。
        self.assertEqual(supply_label_candidates("ABCD1"), ("ABCD1",))

    def test_default_tail_width_is_four(self):
        self.assertEqual(LABEL_TAIL_WIDTH, 4)

    def test_candidates_reject_unusable_serials(self):
        with self.assertRaises(PowerSupplyLabelError):
            supply_label_candidates("DP8B 2149 01519")


class GenerateTests(unittest.TestCase):
    """标签生成：现场期望结果与契约第 3.3 节的边界情形。"""

    def test_current_field_devices_get_expected_labels(self):
        for serial, expected in FIELD_DEVICES.items():
            with self.subTest(serial=serial):
                self.assertEqual(generate_supply_label(serial), expected)

    def test_leading_zeros_are_preserved(self):
        self.assertEqual(generate_supply_label("DP8B174700950"), "DP8B0950")

    def test_case_and_whitespace_are_normalized_before_generating(self):
        self.assertEqual(generate_supply_label(" dp8b214901519 "), "DP8B1519")
        self.assertEqual(generate_supply_label(' " dp8b214901519 " '), "DP8B1519")

    def test_pure_numeric_serial_uses_last_four_digits(self):
        self.assertEqual(generate_supply_label("105462"), "5462")

    def test_serial_shorter_than_four_characters_is_not_padded(self):
        self.assertEqual(generate_supply_label("A12"), "A12")

    def test_prefix_may_be_empty_when_serial_starts_with_a_digit_run(self):
        self.assertEqual(generate_supply_label("1412.0000K02/105462"), "5462")

    def test_collision_extends_to_five_then_six_digits_then_full_serial(self):
        cases = (
            ({"DP8B1519"}, "DP8B151519", "DP8B51519"),
            ({"DP8B1519", "DP8B51519"}, "DP8B126951519", "DP8B951519"),
            ({"DP8B1519", "DP8B51519", "DP8B951519"}, "DP8B1951519", "DP8B1951519"),
        )
        for existing, serial, expected in cases:
            with self.subTest(serial=serial, existing=existing):
                self.assertEqual(
                    generate_supply_label(serial, existing_labels=existing), expected
                )

    def test_existing_labels_are_compared_ignoring_case_and_outer_spaces(self):
        self.assertEqual(
            generate_supply_label("DP8B151519", existing_labels=[" dp8b1519 "]),
            "DP8B51519",
        )

    def test_existing_label_collection_is_not_modified(self):
        existing = ["DP8B1519"]
        generate_supply_label("DP8B11111519", existing_labels=existing)
        self.assertEqual(existing, ["DP8B1519"])

    def test_same_input_twice_yields_the_same_label(self):
        first = generate_supply_label("DP8B214901519", existing_labels=["DP8B0885"])
        second = generate_supply_label("DP8B214901519", existing_labels=["DP8B0885"])
        self.assertEqual(first, second)

    def test_all_candidates_occupied_raises_with_the_serial_and_candidates(self):
        occupied = ["DP8B1519", "DP8B51519", "DP8B951519", "DP8B1951519"]
        with self.assertRaises(PowerSupplyLabelError) as raised:
            generate_supply_label("DP8B1951519", existing_labels=occupied)
        message = str(raised.exception)
        self.assertIn("DP8B1951519", message)
        self.assertIn("候选标签全部被占用", message)

    def test_short_serial_conflicting_with_itself_raises(self):
        with self.assertRaises(PowerSupplyLabelError):
            generate_supply_label("A12", existing_labels=["A12"])


class BatchTests(unittest.TestCase):
    """批量生成：同一批设备互相避让，返回归一化序列号到标签的映射。"""

    def test_field_devices_get_expected_labels_in_one_batch(self):
        labels = generate_supply_labels(FIELD_DEVICES)
        self.assertEqual(labels, FIELD_DEVICES)

    def test_batch_order_follows_input_order(self):
        labels = generate_supply_labels(["DP8B174400885", "DP8B214901519"])
        self.assertEqual(list(labels), ["DP8B174400885", "DP8B214901519"])

    def test_devices_inside_one_batch_avoid_each_other(self):
        labels = generate_supply_labels(["DP8B214901519", "DP8B151519"])
        self.assertEqual(labels["DP8B214901519"], "DP8B1519")
        self.assertEqual(labels["DP8B151519"], "DP8B51519")
        self.assertEqual(len(set(labels.values())), 2)

    def test_batch_avoids_already_frozen_labels(self):
        labels = generate_supply_labels(
            ["DP8B151519"], existing_labels=["DP8B1519"]
        )
        self.assertEqual(labels, {"DP8B151519": "DP8B51519"})

    def test_repeated_serial_in_one_batch_is_rejected(self):
        with self.assertRaises(PowerSupplyLabelError):
            generate_supply_labels(["DP8B214901519", " dp8b214901519 "])


class ContractFixtureTests(unittest.TestCase):
    """契约 fixture 里的标签必须能由本生成器复现（身份与标签同源）。"""

    def test_registry_fixture_labels_match_the_generator(self):
        for name in ("registry_migrated_v3.json", "registry_migrated_v3_all_registered.json"):
            records = [
                record
                for record in _fixture(name).get("instruments", [])
                if record.get("label")
            ]
            self.assertTrue(records, f"{name} 应至少有一条带标签的记录")
            for record in records:
                other_labels = {
                    other["label"] for other in records if other["label"] != record["label"]
                }
                with self.subTest(fixture=name, serial=record["serial"]):
                    self.assertEqual(record["serial"], normalize_supply_serial(record["serial"]))
                    self.assertEqual(
                        generate_supply_label(record["serial"], existing_labels=other_labels),
                        record["label"],
                    )


class PurityTests(unittest.TestCase):
    """步骤 2 的执行策略：纯 Python，不依赖 Qt、文件系统或网络。"""

    ALLOWED_IMPORTS = {
        "__future__",
        "collections.abc",
        "domain.instruments.models",
        "typing",
    }

    def _tree(self):
        return ast.parse(LABELS_SOURCE.read_text(encoding="utf-8-sig"), str(LABELS_SOURCE))

    def test_module_imports_only_domain_and_stdlib(self):
        tree = self._tree()
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.add(node.module or "")
        self.assertEqual(modules - self.ALLOWED_IMPORTS, set())

    def test_module_reads_no_files_and_prints_nothing(self):
        tree = self._tree()
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertEqual(calls & {"open", "print", "eval", "exec", "__import__"}, set())


if __name__ == "__main__":
    unittest.main()
