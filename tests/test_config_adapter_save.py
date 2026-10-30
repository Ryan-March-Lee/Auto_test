"""配置写入适配器的契约测试。

覆盖重构计划步骤 1（已并入原步骤 2 的失败路径测试）固化的契约：

- 正常路径：保存结果与项目既有 JSON 格式一致、可被正式读取器直接读回、成功后不留
  临时文件、首次保存自动创建目标目录；
- 失败路径：序列化失败、fsync 失败、目标替换失败、目标目录创建失败或载荷不是映射时，
  已有配置文件**字节不变**、不出现半份新配置、失败原因原样上抛，且不留临时文件；
  清理临时文件失败只记录日志，**不得**顶替原始写入异常。

所有失败路径都使用隔离临时目录加故障注入，不读写真实配置文件；断言只绑定可观察契约，
不依赖临时文件的随机命名或内部实现步骤。
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from infrastructure.config.json_config_repository import ConfigurationRepository
from infrastructure.filesystem.atomic_json import write_json_atomic
from infrastructure.gui_data_service import JsonConfigurationAdapter
from tests.config_fixtures import complete_combined_config


def _directory_entries(directory: Path) -> list[str]:
    return sorted(entry.name for entry in directory.iterdir())


class _FailingEncoder(json.JSONEncoder):
    """模拟第三方编码器在序列化中途失败。"""

    def iterencode(self, o, _one_shot=False):
        raise ValueError("编码器失败")


class JsonConfigurationAdapterSaveTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.path = self.directory / "config.json"
        self.adapter = JsonConfigurationAdapter(self.path)

    def test_saved_configuration_is_readable_by_the_official_repository(self):
        configuration = complete_combined_config()

        self.adapter.save(configuration)

        self.assertEqual(self.adapter.load(), configuration)
        loaded = ConfigurationRepository().load(self.path)
        self.assertTrue(loaded.valid, loaded.validation.errors)
        self.assertEqual(loaded.configuration.test_plan.frequencies, [2.4])

    def test_save_keeps_the_project_json_formatting(self):
        """沿用既有格式：4 空格缩进、UTF-8、非 ASCII 字符不转义。"""
        configuration = dict(complete_combined_config(), note="现场配置")

        self.adapter.save(configuration)

        expected = json.dumps(configuration, indent=4, ensure_ascii=False)
        self.assertEqual(self.path.read_text(encoding="utf-8"), expected)
        self.assertIn("现场配置".encode("utf-8"), self.path.read_bytes())

    def test_successful_save_leaves_no_temporary_file_behind(self):
        self.adapter.save(complete_combined_config())

        self.assertEqual(_directory_entries(self.directory), ["config.json"])

    def test_first_save_creates_the_target_directory(self):
        """配置目录由写入原语创建，调用方不再自己管理目录。"""
        nested = self.directory / "fresh" / "config.json"

        JsonConfigurationAdapter(nested).save(complete_combined_config())

        self.assertTrue(nested.is_file())
        self.assertEqual(_directory_entries(nested.parent), ["config.json"])


class JsonConfigurationAdapterFailureTests(unittest.TestCase):
    """写入失败必须可观察：旧配置不损坏、异常可见、无残留临时文件。"""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.path = self.directory / "config.json"
        self.adapter = JsonConfigurationAdapter(self.path)
        self.adapter.save(complete_combined_config())
        self.original_bytes = self.path.read_bytes()

    def assert_previous_configuration_intact(self):
        self.assertEqual(self.path.read_bytes(), self.original_bytes)
        self.assertEqual(_directory_entries(self.directory), ["config.json"])

    def test_serialization_failure_keeps_the_previous_configuration(self):
        with self.assertRaises(TypeError):
            self.adapter.save(dict(complete_combined_config(), broken=object()))

        self.assert_previous_configuration_intact()

    def test_fsync_failure_keeps_the_previous_configuration(self):
        with patch(
            "infrastructure.filesystem.atomic_json.os.fsync",
            side_effect=OSError("fsync 失败"),
        ):
            with self.assertRaises(OSError) as caught:
                self.adapter.save(dict(complete_combined_config(), note="新配置"))

        self.assertEqual(str(caught.exception), "fsync 失败")
        self.assert_previous_configuration_intact()

    def test_unexpected_cleanup_failure_does_not_mask_the_write_failure(self):
        """非 OSError 清理异常同样不能顶替原始写入异常。"""
        with patch(
            "infrastructure.filesystem.atomic_json.os.replace",
            side_effect=OSError("替换失败"),
        ), patch.object(Path, "unlink", side_effect=RuntimeError("清理失败")):
            with self.assertLogs(
                "infrastructure.filesystem.atomic_json", level="WARNING"
            ) as logs:
                with self.assertRaises(OSError) as caught:
                    self.adapter.save(dict(complete_combined_config(), note="新配置"))

        self.assertEqual(str(caught.exception), "替换失败")
        self.assertTrue(any("清理失败" in line for line in logs.output), logs.output)
        self.assertEqual(self.path.read_bytes(), self.original_bytes)

    def test_replacement_failure_keeps_the_previous_configuration(self):
        with patch(
            "infrastructure.filesystem.atomic_json.os.replace",
            side_effect=OSError("替换失败"),
        ):
            with self.assertRaises(OSError) as caught:
                self.adapter.save(dict(complete_combined_config(), note="新配置"))

        self.assertEqual(str(caught.exception), "替换失败")
        self.assert_previous_configuration_intact()

    def test_non_mapping_payload_is_rejected_without_writing(self):
        with self.assertRaises(ValueError) as caught:
            self.adapter.save([("schema_version", "1.0")])

        self.assertEqual(str(caught.exception), "JSON 根节点必须是映射")
        self.assert_previous_configuration_intact()

    def test_failed_first_save_leaves_no_partial_configuration(self):
        nested = self.directory / "fresh" / "config.json"

        with patch(
            "infrastructure.filesystem.atomic_json.os.replace",
            side_effect=OSError("替换失败"),
        ):
            with self.assertRaises(OSError):
                JsonConfigurationAdapter(nested).save(complete_combined_config())

        self.assertFalse(nested.exists())
        self.assertEqual(list(nested.parent.iterdir()), [])

    def test_directory_creation_failure_writes_nothing(self):
        """目标目录创建失败时立即失败，不产生配置文件也不留临时文件。"""
        blocker = self.directory / "blocked"
        blocker.write_text("不是目录", encoding="utf-8")
        target = blocker / "config.json"

        with self.assertRaises(OSError):
            JsonConfigurationAdapter(target).save(complete_combined_config())

        self.assertFalse(target.exists())
        self.assertEqual(_directory_entries(self.directory), ["blocked", "config.json"])
        self.assertEqual(
            sorted(entry.name for entry in self.directory.iterdir()),
            ["blocked", "config.json"],
        )

    def test_cleanup_failure_does_not_mask_the_write_failure(self):
        """清理临时文件失败只记录日志，调用方看到的仍是原始写入异常。"""
        with patch(
            "infrastructure.filesystem.atomic_json.os.replace",
            side_effect=OSError("替换失败"),
        ), patch.object(Path, "unlink", side_effect=OSError("临时文件被占用")):
            with self.assertLogs(
                "infrastructure.filesystem.atomic_json", level="WARNING"
            ) as logs:
                with self.assertRaises(OSError) as caught:
                    self.adapter.save(dict(complete_combined_config(), note="新配置"))

        self.assertEqual(str(caught.exception), "替换失败")
        self.assertTrue(any("临时文件被占用" in line for line in logs.output), logs.output)
        self.assertEqual(self.path.read_bytes(), self.original_bytes)


class WriteJsonAtomicEncoderTests(unittest.TestCase):
    """编码器注入只能经由写入原语（适配器固定使用默认编码器）。"""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.path = self.directory / "config.json"
        write_json_atomic(self.path, {"schema_version": "1.0"})
        self.original_bytes = self.path.read_bytes()

    def test_encoder_failure_keeps_the_previous_document(self):
        with self.assertRaises(ValueError) as caught:
            write_json_atomic(self.path, {"schema_version": "2.0"}, encoder=_FailingEncoder)

        self.assertEqual(str(caught.exception), "编码器失败")
        self.assertEqual(self.path.read_bytes(), self.original_bytes)
        self.assertEqual(_directory_entries(self.directory), ["config.json"])


if __name__ == "__main__":
    unittest.main()
