"""步骤 9 门禁：删除动态仪器路由，生产装配只使用显式端口。

本文件是 [技术债分阶段重构计划](../docs/技术债分阶段重构计划.md) 步骤 9 的可执行产出：

- 生产装配（``instrument.measurement_adapter`` / ``instrument.measurement_factory``）
  不定义 ``__getattr__``，也不按方法名转发设备调用；
- 旧动态路由实现（``instrument.legacy_control`` / ``instrument.power_roles``）已整文件退役；
- 自动发现只保留在硬件 smoke 入口，生产装配不调用 ``list_resources()``；
- 电源、信号源、频谱仪的方法在适配器上显式定义。

验证方式：

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" -m unittest tests.test_instrument_routing -v
```
"""

import ast
import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
ADAPTER_SOURCE = ROOT / "instrument" / "measurement_adapter.py"
FACTORY_SOURCE = ROOT / "instrument" / "measurement_factory.py"
ASSEMBLY_SOURCES = (ADAPTER_SOURCE, FACTORY_SOURCE)
PRODUCTION_PACKAGES = (
    "analysis",
    "app",
    "application",
    "domain",
    "infrastructure",
    "instrument",
    "presentation",
    "launcher.py",
)
RETIRED_MODULES = {
    "instrument.legacy_control": ROOT / "instrument" / "legacy_control.py",
    "instrument.power_roles": ROOT / "instrument" / "power_roles.py",
}
EXPLICIT_ADAPTER_METHODS = {
    "set_frequency",
    "set_power",
    "set_center_frequency",
    "set_span",
    "measure_power_with_average",
    "rf_output_on",
    "rf_output_off",
    "set_voltage",
    "set_current",
    "read_voltage",
    "read_current",
    "setup_driver_amplifier_power",
    "power_on_driver",
    "power_off_driver",
    "setup_dut_power",
    "power_on_sequence",
    "power_off_sequence",
    "close_all",
}


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8-sig"), str(path))


def _production_sources():
    sources = []
    for package in PRODUCTION_PACKAGES:
        path = ROOT / package
        if path.is_dir():
            sources.extend(sorted(path.rglob("*.py")))
        elif path.is_file():
            sources.append(path)
    return sources


class DynamicInstrumentRoutingTests(unittest.TestCase):
    def test_assembly_defines_no_attribute_forwarding(self):
        for path in ASSEMBLY_SOURCES:
            with self.subTest(path=path.name):
                tree = _tree(path)
                forwarding = [
                    node.name
                    for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef) and node.name == "__getattr__"
                ]
                self.assertEqual(forwarding, [], f"{path.name} 不得用 __getattr__ 路由设备调用")
                dynamic_calls = [
                    f"{node.func.id} @ line {node.lineno}"
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in {"getattr", "hasattr"}
                ]
                self.assertEqual(
                    dynamic_calls,
                    [],
                    f"{path.name} 不得按方法名探测/转发设备能力: " + ", ".join(dynamic_calls),
                )

    def test_adapter_exposes_explicit_device_methods(self):
        methods = {
            node.name
            for node in ast.walk(_tree(ADAPTER_SOURCE))
            if isinstance(node, ast.FunctionDef)
        }
        self.assertEqual(
            EXPLICIT_ADAPTER_METHODS - methods,
            set(),
            "适配器必须以显式方法暴露测量服务依赖的设备能力",
        )

    def test_retired_routing_modules_are_gone(self):
        for module, path in RETIRED_MODULES.items():
            with self.subTest(module=module):
                self.assertFalse(path.is_file(), f"{module} 源文件已登记退役")
                self.assertIsNone(
                    importlib.util.find_spec(module),
                    f"{module} 不得再被导入",
                )

    def test_production_code_does_not_reference_retired_modules(self):
        violations = []
        for source in _production_sources():
            text = source.read_text(encoding="utf-8-sig")
            for module in RETIRED_MODULES:
                if module in text:
                    violations.append(f"{source.relative_to(ROOT)} references {module}")
        self.assertEqual(violations, [])

    def test_production_assembly_does_not_discover_power_supplies(self):
        discovery = ROOT / "hardware" / "power_supply_discovery.py"
        self.assertTrue(discovery.is_file(), "硬件 smoke 的受控发现逻辑必须保留")
        factory = FACTORY_SOURCE.read_text(encoding="utf-8-sig")
        self.assertNotIn("list_resources", factory, "生产装配不得枚举 VISA 资源")
        self.assertNotIn("_DiscoveredPowerSupply", factory)
        self.assertNotIn("from hardware", factory, "生产装配不得依赖硬件 smoke 包")
        self.assertNotIn("import hardware", factory, "生产装配不得依赖硬件 smoke 包")


if __name__ == "__main__":
    unittest.main()
