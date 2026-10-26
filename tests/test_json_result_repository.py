"""运行目录结果存储实现（json_result_repository）的正式行为测试。

步骤 3 删除了 ``result_storage`` 兼容转发模块和全局兼容副本写入；这里只验证
唯一正式实现的原子写入、运行目录创建与文件名/运行 ID 契约。
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from infrastructure.persistence.json_result_repository import (
    create_run_directory,
    load_json_result,
    new_run_id,
    save_json_result,
    validate_result_filename,
    validate_run_id,
)
from infrastructure.persistence import json_result_repository as canonical_storage


class JsonResultStorageTests(unittest.TestCase):
    def test_save_adds_metadata_and_loads_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "result.json"
            save_json_result(path, {"results": {"1.0": {"gain": 2}}}, result_type="amplifier")
            saved = load_json_result(path)

        self.assertEqual(saved["results"]["1.0"]["gain"], 2)
        self.assertEqual(saved["schema_version"], "1.0")
        self.assertEqual(saved["result_type"], "amplifier")
        self.assertIn("saved_at", saved)

    def test_failed_replace_does_not_leave_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            with patch(
                "infrastructure.persistence.json_result_repository.os.replace",
                side_effect=OSError("替换失败"),
            ):
                with self.assertRaises(OSError):
                    save_json_result(path, {"value": 1}, result_type="test")
            self.assertFalse(path.exists())
            self.assertEqual(list(Path(directory).glob(".*.tmp")), [])

    def test_run_directory_requires_unique_name(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(canonical_storage, "TEST_RESULTS_DIR", Path(directory)):
                first = create_run_directory("run-1")
                self.assertEqual(first, Path(directory) / "run-1")
                with self.assertRaises(FileExistsError):
                    create_run_directory("run-1")

    def test_run_id_rejects_paths_and_accepts_generated_id(self):
        for value in ("../outside", "..\\outside", "C:\\outside", "", ".", "run/id"):
            with self.assertRaises(ValueError):
                validate_run_id(value)
        generated = new_run_id()
        self.assertEqual(validate_run_id(generated), generated)

    def test_result_filename_must_be_a_plain_json_file_name(self):
        self.assertEqual(
            validate_result_filename("cable_loss_results.json"),
            "cable_loss_results.json",
        )
        for value in (
            "",
            " result.json",
            "result.json ",
            "../result.json",
            "sub/result.json",
            "sub\\result.json",
            "C:result.json",
            ".",
            "..",
            "result.txt",
        ):
            with self.subTest(filename=value):
                with self.assertRaises(ValueError):
                    validate_result_filename(value)

    def test_legacy_history_scanning_is_not_exposed_by_the_result_storage(self):
        """步骤 3：旧历史目录枚举属于只读历史仓储，不属于正式结果存储。"""
        storage = canonical_storage
        for name in (
            "fixed_result_filename",
            "timestamped_result_prefix",
            "history_result_files",
            "latest_history_result_file",
            "HISTORY_RESULT_TYPES",
            "FIXED_RESULT_FILENAMES",
            "TIMESTAMPED_RESULT_PREFIXES",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(storage, name))

    def test_result_root_must_be_object(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text(json.dumps([]), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_json_result(path)


if __name__ == "__main__":
    unittest.main()
