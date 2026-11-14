"""设备注册表保存边界。

只接受当前 schema：旧版本、旧字段与旧槽位角色都会被明确拒绝。这里固定当前 schema 的
标签冲突、``supply_id`` 可用性、版本拒绝与写入事务。
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import infrastructure.persistence.instrument_registry as registry_module
from domain.instruments.models import POWER_SUPPLY_ROLE
from infrastructure.persistence.instrument_registry import (
    SCHEMA_VERSION,
    InstrumentRegistry,
    InstrumentRegistryError,
)


def _entry(full_code, *, model="DP832A", role=None, label=None):
    entry = {
        "vendor": "RIGOL TECHNOLOGIES",
        "model": model,
        "full_code": full_code,
        "order_no": None,
        "firmware": None,
        "options": [],
        "status": "online",
        "current_ip": "",
        "notes": None,
        "role": role,
        "first_seen_at": None,
        "last_seen_at": None,
        "known_ips": [],
    }
    if label is not None:
        entry["label"] = label
    return entry


def _document(instruments, schema_version=SCHEMA_VERSION):
    return {
        "schema_version": schema_version,
        "updated_at": "2026-10-29T12:00:00+08:00",
        "instruments": instruments,
    }


class RegistryFileTestCase(unittest.TestCase):
    """每个用例在临时目录里操作一份设备表副本，绝不改写仓库里的现场文件。"""

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.path = Path(self.temporary_directory.name) / "instruments.json"

    def write(self, document):
        self.path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return self.path


class ConflictGateTests(RegistryFileTestCase):
    """冲突与不可派生的序列号必须阻止保存，并指出是哪台设备。"""

    def test_duplicate_labels_are_reported_with_both_devices(self):
        self.write(
            _document(
                [
                    _entry(
                        "B0001",
                        model="FAKE",
                        role=POWER_SUPPLY_ROLE,
                        label="DP8B1519",
                    ),
                    _entry(
                        "DP8B214901519",
                        role=POWER_SUPPLY_ROLE,
                        label="DP8B1519",
                    ),
                ]
            )
        )

        with self.assertRaises(InstrumentRegistryError) as raised:
            InstrumentRegistry.load(self.path)

        message = str(raised.exception)
        self.assertIn("缩写设备码冲突", message)
        self.assertIn("FAKE", message)
        self.assertIn("DP832A", message)

    def test_label_must_be_usable_as_a_supply_id(self):
        for label in ("DP8B/1519", "DP8B 1519"):
            with self.subTest(label=label):
                self.write(
                    _document(
                        [
                            _entry(
                                "DP8B214901519",
                                role=POWER_SUPPLY_ROLE,
                                label=label,
                            )
                        ]
                    )
                )
                with self.assertRaises(InstrumentRegistryError) as raised:
                    InstrumentRegistry.load(self.path)
                self.assertIn("supply_id", str(raised.exception))

    def test_newer_schema_version_is_rejected_instead_of_downgraded(self):
        self.write(_document([], schema_version=SCHEMA_VERSION + 1))

        with self.assertRaises(InstrumentRegistryError) as raised:
            InstrumentRegistry.load(self.path)

        self.assertIn("schema_version", str(raised.exception))

    def test_older_schema_version_is_rejected(self):
        self.write(_document([], schema_version=SCHEMA_VERSION - 1))

        with self.assertRaises(InstrumentRegistryError) as raised:
            InstrumentRegistry.load(self.path)

        self.assertIn("schema_version", str(raised.exception))


class SaveTransactionTests(RegistryFileTestCase):
    """保存是一次事务：校验、写盘任何一步失败都不改动实例状态与文件。"""

    def test_write_failure_keeps_the_document_and_file_unchanged(self):
        self.write(
            _document(
                [
                    _entry(
                        "DP8B214901519",
                        role=POWER_SUPPLY_ROLE,
                        label="DP8B1519",
                    )
                ]
            )
        )
        registry = InstrumentRegistry.load(self.path)
        before_document = registry.raw
        before_bytes = self.path.read_bytes()

        with mock.patch.object(
            registry_module, "write_json_atomic", side_effect=OSError("磁盘已满")
        ):
            with self.assertRaises(OSError):
                registry.save()

        self.assertEqual(registry.raw, before_document, "写盘失败不得提交保存结果")
        self.assertEqual(self.path.read_bytes(), before_bytes, "写盘失败不得改写原文件")


if __name__ == "__main__":
    unittest.main()
