"""用户命名配置快照：名称规则、目录布局、覆盖语义与显式加载。"""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import infrastructure.persistence.configuration_snapshot_repository as repository_module
from application.dto.configuration_snapshots import (
    MAX_CONFIGURATION_NAME_LENGTH,
    configuration_folder_name,
    validate_configuration_name,
)
from infrastructure.persistence.configuration_snapshot_repository import (
    FileConfigurationSnapshotRepository,
)


class ConfigurationNameTests(unittest.TestCase):
    def test_accepts_chinese_space_and_common_punctuation(self):
        self.assertEqual(validate_configuration_name("  功放 测试-01 "), "功放 测试-01")

    def test_rejects_empty_and_overlong_names(self):
        for value in ("", "   ", "\t"):
            with self.assertRaises(ValueError):
                validate_configuration_name(value)
        with self.assertRaises(ValueError):
            validate_configuration_name("配" * (MAX_CONFIGURATION_NAME_LENGTH + 1))

    def test_rejects_path_fragments_and_reserved_names(self):
        for value in ("..", "a/b", "a\\b", "C:name", "a*b", "CON", "lpt1.config"):
            with self.assertRaises(ValueError, msg=value):
                validate_configuration_name(value)

    def test_folder_name_appends_local_year_to_minute(self):
        name = configuration_folder_name(
            "功放测试", now=datetime(2026, 11, 14, 9, 5)
        )
        self.assertEqual(name, "功放测试_20261114_0905")


class ConfigurationSnapshotRepositoryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / "configurations"
        self.repository = FileConfigurationSnapshotRepository(self.root)

    def _document(self, frequency=2.4):
        return {
            "schema_version": "1.2",
            "test_frequencies": [frequency],
            "note": "现场配置",
        }

    def test_save_creates_dated_folder_with_named_configuration(self):
        snapshot = self.repository.save(
            "功放测试", self._document(), now=datetime(2026, 11, 14, 9, 5)
        )

        self.assertEqual(snapshot.directory.name, "功放测试_20261114_0905")
        self.assertEqual(snapshot.configuration_path.name, "功放测试.json")
        self.assertTrue(snapshot.configuration_path.is_file())
        self.assertEqual(
            json.loads(snapshot.configuration_path.read_text(encoding="utf-8")),
            self._document(),
        )
        metadata = json.loads(snapshot.metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(metadata["display_name"], "功放测试")
        self.assertEqual(metadata["configuration_file"], "功放测试.json")
        self.assertEqual(snapshot.saved_at, "2026-11-14T09:05:00Z")

    def test_same_name_overwrites_the_existing_folder(self):
        first = self.repository.save(
            "功放测试", self._document(), now=datetime(2026, 11, 14, 9, 5)
        )
        second = self.repository.save(
            "功放测试", self._document(3.5), now=datetime(2026, 11, 15, 18, 30)
        )

        self.assertEqual(second.directory, first.directory)
        folders = [path for path in self.root.iterdir() if path.is_dir()]
        self.assertEqual(folders, [first.directory])
        self.assertEqual(
            json.loads(second.configuration_path.read_text(encoding="utf-8")),
            self._document(3.5),
        )
        self.assertEqual(second.saved_at, "2026-11-15T18:30:00Z")

    def test_different_names_create_separate_folders(self):
        self.repository.save(
            "A", self._document(), now=datetime(2026, 11, 14, 9, 5)
        )
        self.repository.save(
            "B", self._document(), now=datetime(2026, 11, 14, 9, 6)
        )
        self.assertEqual(len(self.repository.list()), 2)

    def test_load_round_trips_user_selected_file(self):
        snapshot = self.repository.save("功放测试", self._document())
        self.assertEqual(
            self.repository.load(snapshot.configuration_path), self._document()
        )

    def test_load_rejects_metadata_nested_and_outside_paths(self):
        snapshot = self.repository.save("功放测试", self._document())
        with self.assertRaises(ValueError):
            self.repository.load(snapshot.metadata_path)
        nested = snapshot.directory / "inputs" / "other.json"
        nested.parent.mkdir(parents=True)
        nested.write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.repository.load(nested)
        outside = Path(tempfile.mkdtemp()) / "other.json"
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        outside.write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.repository.load(outside)

    def test_load_rejects_an_unregistered_json_in_a_snapshot_folder(self):
        """快照目录里的任意 JSON 不是快照：必须由 metadata.json 登记。"""
        snapshot = self.repository.save("功放测试", self._document())
        rogue = snapshot.directory / "手工放进来的.json"
        rogue.write_text("{}", encoding="utf-8")

        with self.assertRaises(ValueError):
            self.repository.load(rogue)

    def test_failed_metadata_write_keeps_the_previous_generation(self):
        first = self.repository.save("功放测试", self._document())
        original = repository_module.write_json_atomic

        def fail_on_metadata(path, payload, **kwargs):
            if Path(path).name == "metadata.json":
                raise OSError("磁盘已满")
            return original(path, payload, **kwargs)

        with patch.object(
            repository_module, "write_json_atomic", side_effect=fail_on_metadata
        ):
            with self.assertRaises(OSError):
                self.repository.save("功放测试", self._document(3.5))

        # 失败的一代不能留下“新配置配旧元数据”，也不能留下暂存目录。
        self.assertEqual(
            json.loads(first.configuration_path.read_text(encoding="utf-8")),
            self._document(),
        )
        self.assertEqual(
            [item.configuration_path for item in self.repository.list()],
            [first.configuration_path],
        )
        self.assertEqual(
            [
                path.name
                for path in self.root.iterdir()
                if path.is_dir() and path.name.startswith(".")
            ],
            [],
        )

    def test_interrupted_directory_swap_is_recovered(self):
        """进程在“移走旧代、换上新代”之间中断时，下次访问要恢复旧代。"""
        snapshot = self.repository.save("功放测试", self._document())
        backup = self.root / f".{snapshot.directory.name}.backup-{'a' * 32}"
        snapshot.directory.rename(backup)
        self.assertFalse(snapshot.directory.exists())

        recovered = self.repository.list()

        self.assertEqual(
            [item.configuration_path for item in recovered],
            [snapshot.configuration_path],
        )
        self.assertTrue(snapshot.configuration_path.is_file())
        self.assertFalse(backup.exists())

    def test_list_sorts_by_saved_at_and_skips_broken_entries(self):
        older = self.repository.save(
            "旧配置", self._document(), now=datetime(2026, 11, 13, 8, 0)
        )
        newer = self.repository.save(
            "新配置", self._document(), now=datetime(2026, 11, 14, 9, 0)
        )
        broken = self.root / "坏配置_20261114_1000"
        broken.mkdir()
        (broken / "metadata.json").write_text("{不是 JSON", encoding="utf-8")

        self.assertEqual(
            [item.configuration_path for item in self.repository.list()],
            [newer.configuration_path, older.configuration_path],
        )

    def test_missing_root_lists_nothing(self):
        self.assertEqual(self.repository.list(), ())
        self.assertFalse(self.root.exists())
