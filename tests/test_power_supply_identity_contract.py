"""门禁：电源设备身份与配置数据契约（重构计划步骤 1）。

本文件是这份数据契约的可执行版本，与 `tests/fixtures/power_supply_identity/` 里的样例一一
对应。它校验样例数据是否自洽，并守住两条边界：样例与生产代码都不许重新依赖固定槽位
（`PS1..PS4` 不再是可识别的角色或电源键）。

覆盖：

- 每份 fixture 都是可解析的 JSON，登记的样例都确实存在；
- 注册表的 `label` 与短标签规则（前缀 + 末四位，冲突加宽）给出的期望标签逐步一致，
  `device_key` 由 `厂商:完整设备码` 生成；标签由完整设备码派生、对所有登记设备生效（电源的
  标签同时是 `supply_id`），未登记角色的设备同样有标签，但不会被猜成电源；
- 配置里的电源键、条目内 `label` 与分配引用的 `supply_id` 是同一个字符串，且分配不引用
  不存在的电源；
- 1 台 / 2 台 / 5 台三种规模都能表达；
- 运行快照只冻结实际使用的电源，通道一律写 `supply_id/CHx`；
- 生产代码（含 `tools/` 脚本）不再硬编码槽位编号，也不从序号拼电源键；初始化脚本登记的是
  规范类别 `power_supply`（未确认类别的设备仍留 `null`，不猜成电源）。
"""

import ast
import json
import re
import sys
import unittest
from pathlib import Path

from domain.instruments.models import (
    canonical_role,
    make_device_key,
    normalize_full_code,
)


ROOT = Path(__file__).parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "power_supply_identity"

# 短标签规则（前缀 + 末四位，冲突时加宽）：现场三台 DP832A 与两台样例设备的期望标签。
EXPECTED_LABELS = {
    "DP8B214901519": "DP8B1519",
    "DP8B174700950": "DP8B0950",
    "DP8B174400885": "DP8B0885",
    "DP8B234501191": "DP8B1191",
    "DP8B234701216": "DP8B1216",
}

# 当前目标格式的配置样例：1 台、2 台、5 台。
DYNAMIC_CONFIGS = (
    "config_one_supply.json",
    "config_two_supplies.json",
    "config_five_supplies.json",
)
EXPECTED_SCALES = {
    "config_one_supply.json": 1,
    "config_two_supplies.json": 2,
    "config_five_supplies.json": 5,
}
REGISTRY_FIXTURES = (
    "registry_migrated_v4.json",
    "registry_migrated_v4_all_registered.json",
)
# 已经写完标签的当前设备表。
MIGRATED_REGISTRIES = (
    "registry_migrated_v4.json",
    "registry_migrated_v4_all_registered.json",
)

# 生产代码范围：正式包、分析层、脚本与硬件/持久化目录都要守住“不再依赖固定槽位”。
PRODUCTION_PACKAGES = (
    "app",
    "application",
    "domain",
    "infrastructure",
    "instrument",
    "presentation",
    "analysis",
    "tools",
    "hardware",
    "persistence",
    "format_convert",
)
PRODUCTION_ROOT_FILES = ("launcher.py", "release_manifest.py", "collect_baseline.py")

# 槽位名本体（``PS1``、``PS5``……）。``PS`` 单独一段不是槽位编号（设备码徽标就是 ``PS``）。
_SLOT_NAME = re.compile(r"^PS\d+$")
# 拿来拼槽位名的模板（``PS``、``PS1``、``PS%s``、``PS{}``……）：本身不一定是槽位名，但按序号
# 填进去就变成了固定槽位依赖。单独的 ``PS`` 只有在这种“拼”的上下文里才算：它本身只是类别
# 缩写，不是“第几台”。
_SLOT_TEMPLATE = re.compile(r"^PS(?:\d*|[{:%.].*)$", re.IGNORECASE | re.DOTALL)


def _production_modules():
    """生产代码里的每一个 ``.py``：正式包、脚本目录与根目录入口脚本。"""
    for package in PRODUCTION_PACKAGES:
        directory = ROOT / package
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.py")):
            yield path
    for name in PRODUCTION_ROOT_FILES:
        path = ROOT / name
        if path.is_file():
            yield path


def _code_string_constants(tree):
    """遍历字符串字面量，跳过文档字符串（说明文字不是依赖，注释也不在 AST 里）。"""
    docstrings = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                yield node


def _generated_slot_names(tree):
    """按序号拼槽位名的写法也算固定槽位依赖。

    覆盖门禁声称的范围：``f"PS{index}"``（含 ``f"PS1{index}"``）、``"PS" + str(index)``、
    ``"PS%s" % index``、``"PS{}".format(index)``。更绕的构造（例如先拼出 ``"P"``、``"S"``
    再相加）在 AST 上无法与普通字符串运算区分，这里不声称覆盖，靠“新键来自短标签”的
    契约与代码评审兜底。
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr) and node.values:
            first = node.values[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                if _SLOT_TEMPLATE.match(first.value.strip()):
                    yield node
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
            for operand in (node.left, node.right):
                if isinstance(operand, ast.Constant) and isinstance(operand.value, str):
                    if _SLOT_TEMPLATE.match(operand.value.strip()):
                        yield node
        elif isinstance(node, ast.Call):
            formatted = getattr(node.func, "attr", None)
            receiver = getattr(node.func, "value", None)
            if formatted == "format" and isinstance(receiver, ast.Constant):
                if isinstance(receiver.value, str) and _SLOT_TEMPLATE.match(receiver.value.strip()):
                    yield node


def _slot_literal_violations(tree, allowed_ids=frozenset()):
    """一段源码里的槽位依赖：写死的槽位名与按序号拼接的槽位名。"""
    violations = []
    for node in _code_string_constants(tree):
        if id(node) in allowed_ids:
            continue
        if _SLOT_NAME.match(node.value.strip()):
            violations.append(f"第 {node.lineno} 行硬编码槽位名 {node.value!r}")
    for node in _generated_slot_names(tree):
        violations.append(f"第 {node.lineno} 行按序号拼槽位名")
    return violations


def _load(name: str) -> dict:
    path = FIXTURE_DIR / name
    if not path.is_file():
        raise AssertionError(f"缺少 fixture: {name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _instruments(document: dict) -> list:
    return list(document.get("instruments") or [])


def _supplies(config: dict) -> dict:
    return dict((config.get("instruments") or {}).get("power_supplies") or {})


def _assignments(config: dict) -> list:
    """返回所有分配条目 (组名, 角色键, 条目)。"""
    groups = config.get("power_supply_assignment") or {}
    entries = []
    for group_name, group in groups.items():
        for role_key, entry in (group or {}).get("supplies", {}).items():
            entries.append((group_name, role_key, entry))
    return entries


def _used_supply_ids(config: dict) -> set:
    return {
        entry.get("supply_id")
        for _, _, entry in _assignments(config)
        if entry.get("supply_id")
    }


def _stable_serial(value: str) -> str:
    """归一化到稳定值。``normalize_full_code`` 自己已循环到稳定，这里只验证。"""
    stable = normalize_full_code(value)
    if normalize_full_code(stable) != stable:
        raise AssertionError(f"完整设备码归一化不稳定: {value!r} -> {stable!r}")
    return stable


def _record_full_code(record):
    """读取 fixture 里的完整设备码；当前格式只有 ``full_code``。"""
    return record.get("full_code")


def _fixture_serials():
    """(fixture 名, 完整设备码) —— 当前注册表、配置电源条目与运行快照里的全部值。"""
    for name in REGISTRY_FIXTURES:
        for record in _instruments(_load(name)):
            yield name, _record_full_code(record)
    for name in DYNAMIC_CONFIGS:
        config = _load(name)
        for entry in _supplies(config).values():
            yield name, _record_full_code(entry)
        for entry in (config.get("run_mapping") or {}).get("instruments", {}).get(
            "power_supplies", {}
        ).values():
            yield name, _record_full_code(entry)
    mapping = _load("run_mapping_snapshot_two_supplies.json")
    for entry in (mapping.get("instruments") or {}).get("power_supplies", {}).values():
        yield "run_mapping_snapshot_two_supplies.json", _record_full_code(entry)


class FixtureInventoryTests(unittest.TestCase):
    """fixture 目录本身就是契约的一部分：少一个文件或多一个未登记的 JSON 都要被发现。"""

    def test_every_fixture_parses_as_json(self):
        files = sorted(FIXTURE_DIR.glob("*.json"))
        self.assertTrue(files, "fixture 目录不能为空")
        for path in files:
            with self.subTest(fixture=path.name):
                document = json.loads(path.read_text(encoding="utf-8"))
                self.assertIsInstance(document, dict)

    def test_documented_fixtures_all_exist(self):
        for name in REGISTRY_FIXTURES + DYNAMIC_CONFIGS:
            with self.subTest(fixture=name):
                self.assertTrue((FIXTURE_DIR / name).is_file(), f"登记的 fixture 不存在: {name}")


class RegistryContractTests(unittest.TestCase):
    """注册表的身份字段与缩写设备码。"""

    def test_labels_follow_the_documented_rule(self):
        for name in MIGRATED_REGISTRIES:
            for record in _instruments(_load(name)):
                label = record.get("label")
                serial = _record_full_code(record)
                with self.subTest(fixture=name, full_code=serial):
                    if label is None:
                        self.assertIsNone(
                            serial,
                            "只有缺序列号的设备才允许没有标签：标签由序列号派生，与角色无关",
                        )
                        continue
                    self.assertIn(
                        serial,
                        EXPECTED_LABELS,
                        "出现未收录期望标签的序列号：先更新短标签规则与 EXPECTED_LABELS",
                    )
                    self.assertEqual(label, EXPECTED_LABELS[serial])

    def test_labels_are_unique_inside_a_registry(self):
        for name in REGISTRY_FIXTURES:
            labels = [
                record["label"]
                for record in _instruments(_load(name))
                if record.get("label")
            ]
            with self.subTest(fixture=name):
                self.assertEqual(len(labels), len(set(labels)), f"标签冲突: {labels}")

    def test_instrument_identity_is_vendor_plus_full_code(self):
        for name in REGISTRY_FIXTURES:
            for record in _instruments(_load(name)):
                full_code = _record_full_code(record)
                with self.subTest(fixture=name, full_code=full_code):
                    key = make_device_key(record.get("vendor"), full_code)
                    self.assertEqual(key, (record["vendor"] + ":" + full_code).casefold())

    def test_serials_are_stored_in_normalized_form(self):
        checked = 0
        for name, serial in _fixture_serials():
            if serial is None:
                continue
            checked += 1
            with self.subTest(fixture=name, full_code=serial):
                self.assertEqual(
                    serial, _stable_serial(serial), "fixture 里的序列号必须是归一化后的稳定形态"
                )
                self.assertEqual(serial, serial.strip(), "序列号不能带首尾空白")
                self.assertEqual(serial, serial.upper(), "序列号必须大写")
                self.assertNotIn(" ", serial, "序列号含内部空白时标签生成会被拒绝，样例不能这样写")
        self.assertGreater(checked, 0, "没有任何序列号被检查")


    def test_registering_a_device_explicitly_adds_its_label(self):
        records = {
            record["full_code"]: record
            for record in _instruments(_load("registry_migrated_v4_all_registered.json"))
        }
        for serial in ("DP8B214901519", "DP8B174700950", "DP8B174400885"):
            with self.subTest(full_code=serial):
                self.assertEqual(records[serial]["role"], "power_supply")
                self.assertEqual(records[serial]["label"], EXPECTED_LABELS[serial])


class ConfigContractTests(unittest.TestCase):
    """配置里的电源集合、分配引用与运行映射。"""

    def test_power_supply_keys_are_labels_and_carry_identity(self):
        for name in DYNAMIC_CONFIGS:
            supplies = _supplies(_load(name))
            with self.subTest(fixture=name):
                self.assertTrue(supplies, "目标格式的配置必须至少有一台电源")
            for supply_id, entry in supplies.items():
                with self.subTest(fixture=name, supply_id=supply_id):
                    self.assertEqual(entry.get("label"), supply_id, "集合键必须等于条目内的 label")
                    self.assertNotIn("supply_id", entry, "条目内部不再重复写 supply_id")
                    self.assertEqual(
                        entry.get("device_key"),
                        make_device_key("RIGOL TECHNOLOGIES", entry.get("full_code")),
                        "device_key 必须由厂商与序列号生成",
                    )
                    self.assertIn(
                        entry.get("full_code"),
                        EXPECTED_LABELS,
                        "出现未收录期望标签的序列号：先更新 EXPECTED_LABELS",
                    )
                    self.assertEqual(supply_id, EXPECTED_LABELS[entry["full_code"]])
                    self.assertEqual(entry.get("model"), "DP832A")
                    self.assertIn("channels", entry)

    def test_assignments_only_reference_existing_power_supplies(self):
        for name in DYNAMIC_CONFIGS:
            config = _load(name)
            supplies = _supplies(config)
            for group_name, role_key, entry in _assignments(config):
                with self.subTest(fixture=name, group=group_name, role=role_key):
                    self.assertIn("supply_id", entry, "分配引用必须写 supply_id")
                    supply_id = entry.get("supply_id")
                    if supply_id is not None:
                        self.assertIn(supply_id, supplies, "分配引用了不存在的电源")
                        if "name" in entry:
                            self.assertEqual(entry["name"], supply_id, "过渡期的 name 必须与 supply_id 相同")
                    else:
                        self.assertNotIn("name", entry, "未绑定的引用不保留槽位名")

    def test_one_two_and_five_power_supplies_are_expressible(self):
        for name, expected in EXPECTED_SCALES.items():
            with self.subTest(fixture=name):
                self.assertEqual(len(_supplies(_load(name))), expected)


    def test_run_mapping_freezes_only_the_supplies_in_use(self):
        for name in DYNAMIC_CONFIGS:
            config = _load(name)
            mapping = config["run_mapping"]
            frozen = mapping["instruments"]["power_supplies"]
            with self.subTest(fixture=name):
                self.assertEqual(
                    sorted(frozen), sorted(_used_supply_ids(config)), "快照只登记实际使用的电源"
                )
            for supply_id, entry in frozen.items():
                with self.subTest(fixture=name, supply_id=supply_id):
                    for field in ("label", "model", "full_code", "device_key", "visa_address"):
                        self.assertTrue(entry.get(field), f"快照条目缺少 {field}")
                    self.assertEqual(entry["label"], supply_id)
                    self.assertEqual(
                        entry["device_key"],
                        _supplies(config)[supply_id]["device_key"],
                        "快照身份必须与配置里的电源条目一致",
                    )
            for channel in mapping.get("dut_power_channels", []):
                with self.subTest(fixture=name, channel=channel.get("channel")):
                    token = channel.get("channel") or ""
                    self.assertIn("/", token, "通道必须写成 supply_id/CHx")
                    self.assertIn(token.split("/", 1)[0], frozen, "通道路由必须指到快照里的电源")
            if "dut_power_plan" in mapping:
                with self.subTest(fixture=name):
                    self.assertNotIn("dut_power_channels", mapping)
                    self.assertTrue(
                        all("role" not in row for row in mapping["dut_power_plan"]["power_sequence"]),
                        "上电顺序只写物理路由，不再带角色",
                    )








class RunSnapshotContractTests(unittest.TestCase):
    """运行目录里的快照：电源集合自带身份与显式路由。"""


    def test_new_snapshot_carries_identity_for_every_supply(self):
        mapping = _load("run_mapping_snapshot_two_supplies.json")
        frozen = mapping["instruments"]["power_supplies"]
        self.assertEqual(sorted(frozen), ["DP8B0885", "DP8B1519"])
        for supply_id, entry in frozen.items():
            with self.subTest(supply_id=supply_id):
                for field in ("label", "model", "full_code", "device_key", "visa_address"):
                    self.assertTrue(entry.get(field), f"快照条目缺少 {field}")
                self.assertEqual(entry["device_key"], make_device_key("RIGOL TECHNOLOGIES", entry["full_code"]))
        plan = mapping["dut_power_plan"]
        self.assertNotIn("dut_power_channels", mapping)
        self.assertEqual(
            [(row["supply_id"], row["channel"]) for row in plan["power_sequence"]],
            [("DP8B1519", "CH1"), ("DP8B0885", "CH2")],
            "上电计划必须按用户顺序冻结物理路由",
        )
        self.assertTrue(
            all("role" not in row for row in plan["power_sequence"]),
            "1.1 快照不再写通道角色",
        )
        self.assertTrue(mapping["run_id"], "正式快照要有 run_id 才能被历史页找到")

    def test_new_snapshot_carries_identity_for_every_instrument(self):
        """信号源与频谱仪也冻结身份：运行期间不回查注册表，不按型号后缀或 IP 补全。"""
        mapping = _load("run_mapping_snapshot_two_supplies.json")
        for name in ("signal_generator", "spectrum_analyzer"):
            entry = mapping["instruments"][name]
            with self.subTest(instrument=name):
                for field in ("label", "model", "full_code", "device_key", "visa_address"):
                    self.assertTrue(entry.get(field), f"快照条目缺少 {field}")
                self.assertNotIn("serial", entry, "完整设备码只写 full_code")


class TargetConfigurationContractTests(unittest.TestCase):
    """人工重扫并绑定后的配置样例：身份字段齐全，旧字段名不再出现。"""

    def test_target_configs_carry_identity_and_no_legacy_fields(self):
        for name in ("config_one_supply.json", "config_two_supplies.json", "config_five_supplies.json"):
            document = _load(name)
            for role in ("signal_generator", "spectrum_analyzer"):
                entry = document["instruments"][role]
                snapshot = document["run_mapping"]["instruments"][role]
                for source, fields in (
                    (entry, ("label", "model", "full_code", "device_key", "address")),
                    (snapshot, ("label", "model", "full_code", "device_key", "visa_address")),
                ):
                    for field in fields:
                        with self.subTest(fixture=name, instrument=role, field=field):
                            self.assertTrue(source.get(field), f"缺少 {field}")
                    self.assertNotIn("serial", source, "完整设备码只写 full_code")
                with self.subTest(fixture=name, role=role):
                    self.assertNotIn("serial", document["device_bindings"][role])
                    self.assertNotIn("name", document["device_bindings"][role])
            for supply_id, record in document["device_bindings"]["power_supplies"].items():
                with self.subTest(fixture=name, supply_id=supply_id):
                    self.assertNotIn("serial", record)
                    self.assertNotIn("name", record)


class NoFixedSlotDependencyTests(unittest.TestCase):
    """门禁：生产代码不再依赖固定槽位（重构计划步骤 15）。

    旧槽位不再被读取或识别；生产代码也不得再写 ``PS1..PS4``，不得拿序号拼电源键：新增
    第 5 台电源只按 ``supply_id`` 落键，不需要改任何常量。门禁覆盖写死的槽位名与按序号拼接
    的写法，说明文字与设备码不算。
    """

    def test_production_code_does_not_hard_code_power_supply_slots(self):
        violations = []
        for path in _production_modules():
            relative = path.relative_to(ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            for violation in _slot_literal_violations(tree):
                violations.append(f"{relative}: {violation}")
        self.assertEqual(
            violations,
            [],
            "生产代码不得硬编码电源槽位编号；电源键只按 supply_id 生成",
        )

    def test_legacy_slot_roles_are_not_recognised(self):
        for slot in ("PS1", "PS2", "PS3", "PS4"):
            with self.subTest(slot=slot):
                self.assertIsNone(canonical_role(slot))

    def test_init_script_registers_canonical_device_roles(self):
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from tools.init_instrument_registry import SCANNED_DEVICES

        self.assertTrue(SCANNED_DEVICES, "初始化脚本必须有现场扫描结果")
        for device in SCANNED_DEVICES:
            role = device.get("role")
            with self.subTest(idn=device.get("idn")):
                self.assertEqual(
                    canonical_role(role),
                    role,
                    "初始化脚本只能登记规范类别（power_supply 等），不能写回旧槽位编号",
                )


if __name__ == "__main__":
    unittest.main()
