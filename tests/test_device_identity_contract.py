"""门禁：设备身份字段统一（重构计划步骤 20）。

可执行契约：

1. ``Instrument`` / ``InstrumentIdentity`` 只有 ``full_code``，没有 ``serial``、``name``、
   ``model_index``；缩写设备码字段是 ``label``，身份键只由
   :func:`domain.instruments.models.make_device_key` 生成；
2. 注册表保存出的新条目只写 ``full_code`` / ``label`` / 角色与地址，不写旧字段；
3. 生产代码只在登记过的历史读取边界出现 ``serial``，不再有 ``build_instrument_name``、
   ``get_by_name``、``get_by_serial``、``next_model_index``、``known_name``；
4. 新写出的契约文档（README、正式 API 清单、设备发现契约、fixture README）只在“历史读取
   边界”的说明里提到 ``serial``，不再把它当成现行字段。
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from domain.instruments.models import Instrument, InstrumentIdentity
from infrastructure.persistence.instrument_registry import (
    SCHEMA_VERSION,
    InstrumentRegistry,
)

ROOT = Path(__file__).parents[1]

# 正式运行时包与根目录入口脚本；``tools/`` 由下面的初始化脚本断言单独覆盖。
PRODUCTION_PACKAGES = (
    "app",
    "application",
    "domain",
    "infrastructure",
    "instrument",
    "presentation",
    "analysis",
)
ROOT_MODULES = ("launcher.py", "release_manifest.py", "collect_baseline.py")

# 历史读取边界：旧设备表 / 旧运行快照 / 旧绑定里的完整设备码写在 ``serial`` 里。
# 这些模块读入后立即投影为 ``full_code``；除此之外生产代码不得再出现这个字段名。
SERIAL_HISTORY_BOUNDARY_MODULES = frozenset(
    {
        "domain/configuration/models.py",
        "domain/models.py",
        "infrastructure/config/legacy_power_supply_migration.py",
        "infrastructure/persistence/instrument_registry.py",
        "application/device_binding.py",
        "presentation/qt/config_form_bindings.py",
    }
)

RETIRED_IDENTIFIERS = (
    "build_instrument_name",
    "get_by_name",
    "get_by_serial",
    "next_model_index",
    "known_name",
)

# 新写出的契约文档：出现 ``serial`` 的行必须明确说明它是历史字段。
CONTRACT_DOCUMENTS = (
    "README.md",
    "docs/正式API目录清单.md",
    "docs/设备发现MVP.md",
    "tests/fixtures/power_supply_identity/README.md",
)
_HISTORY_MARKERS = ("历史", "旧", "退役", "不再", "兼容", "读取边界")

_SERIAL_TOKEN = re.compile(r"\bserial\b", re.IGNORECASE)


def _production_sources():
    for package in PRODUCTION_PACKAGES:
        yield from sorted((ROOT / package).rglob("*.py"))
    for name in ROOT_MODULES:
        yield ROOT / name


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class ModelContractTests(unittest.TestCase):
    def test_models_carry_full_code_and_not_retired_identity_fields(self):
        identity_fields = InstrumentIdentity.__dataclass_fields__
        instrument_fields = Instrument.__dataclass_fields__

        self.assertIn("full_code", identity_fields)
        self.assertIn("full_code", instrument_fields)
        self.assertIn("label", instrument_fields)
        for retired in ("serial", "name", "model_index"):
            self.assertNotIn(retired, identity_fields)
            self.assertNotIn(retired, instrument_fields)

    def test_saved_registry_entry_has_only_current_identity_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instruments.json"
            registry = InstrumentRegistry.load(path)
            registry.upsert(
                InstrumentIdentity.from_idn(
                    "RIGOL TECHNOLOGIES,DP832A,DP8B214901519,00.01.16"
                ),
                role="power_supply",
            )
            registry.save()
            document = json.loads(path.read_text(encoding="utf-8"))

        entry = document["instruments"][0]
        self.assertEqual(document["schema_version"], SCHEMA_VERSION)
        self.assertEqual(entry["full_code"], "DP8B214901519")
        self.assertEqual(entry["label"], "DP8B1519")
        for retired in ("serial", "name", "model_index"):
            self.assertNotIn(retired, entry)


class ProductionSourceContractTests(unittest.TestCase):
    def test_serial_only_appears_in_documented_history_boundaries(self):
        offenders = []
        for path in _production_sources():
            relative = _relative(path)
            if relative in SERIAL_HISTORY_BOUNDARY_MODULES:
                continue
            for number, line in enumerate(_text(path).splitlines(), 1):
                if _SERIAL_TOKEN.search(line):
                    offenders.append(f"{relative}:{number}: {line.strip()}")
        self.assertEqual(
            offenders,
            [],
            "生产代码不得再以 serial 命名完整设备码；旧字段只允许在历史读取边界出现: "
            + "; ".join(offenders),
        )

    def test_retired_identity_helpers_are_gone(self):
        offenders = []
        for path in _production_sources():
            text = _text(path)
            for name in RETIRED_IDENTIFIERS:
                if name in text:
                    offenders.append(f"{_relative(path)}: {name}")
        self.assertEqual(
            offenders,
            [],
            "设备身份只按 device_key 查找，不再按资产名/序号/旧字段查找: "
            + "; ".join(offenders),
        )

    def test_init_script_does_not_use_retired_identity_fields(self):
        text = _text(ROOT / "tools" / "init_instrument_registry.py")
        for retired in ('"name"', '"serial"', "model_index", "build_instrument_name"):
            self.assertNotIn(retired, text)


class ContractDocumentTests(unittest.TestCase):
    def test_new_contract_documents_only_mention_serial_as_history(self):
        offenders = []
        for name in CONTRACT_DOCUMENTS:
            path = ROOT / name
            for number, line in enumerate(_text(path).splitlines(), 1):
                if not _SERIAL_TOKEN.search(line):
                    continue
                if not any(marker in line for marker in _HISTORY_MARKERS):
                    offenders.append(f"{name}:{number}: {line.strip()}")
        self.assertEqual(
            offenders,
            [],
            "现行契约文档不得把 serial 当成字段描述；只允许在历史读取边界说明里提到它: "
            + "; ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
