import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config_io import load_config_file, load_json_object


class ConfigIoTests(unittest.TestCase):
    def test_load_json_object_requires_object_root(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps([]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "根节点必须是对象"):
                load_json_object(path)

    def test_load_config_file_uses_default_and_resolves_relative_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            default_path = root / "default.json"
            relative_path = root / "relative.json"
            default_path.write_text(json.dumps({"source": "default"}), encoding="utf-8")
            relative_path.write_text(json.dumps({"source": "relative"}), encoding="utf-8")

            with patch("config_io.CONFIG_FILE", default_path):
                self.assertEqual(load_config_file()["source"], "default")
            with patch("config_io.resolve_path", return_value=relative_path):
                self.assertEqual(load_config_file("relative.json")["source"], "relative")

    def test_load_config_file_preserves_utf8_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"说明": "现场配置"}, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(load_config_file(path)["说明"], "现场配置")


if __name__ == "__main__":
    unittest.main()
