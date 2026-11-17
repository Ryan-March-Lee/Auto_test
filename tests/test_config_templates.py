"""入库配置模板的选择、读取与首次启动回退（当前配置不入库）。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from infrastructure.config.config_templates import (
    CONFIG_TEMPLATE_ENV,
    DEFAULT_CONFIG_TEMPLATE,
    MOCK_CONFIG_TEMPLATE,
    load_config_template,
    selected_config_template,
)


class ConfigTemplateSelectionTests(unittest.TestCase):
    def test_defaults_to_the_shipped_default_template(self):
        with patch(
            "infrastructure.config.config_templates.load_env_value",
            return_value=None,
        ) as load_env:
            path = selected_config_template(Path("D:/project"))

        self.assertEqual(path, Path("D:/project") / DEFAULT_CONFIG_TEMPLATE)
        load_env.assert_called_once_with(CONFIG_TEMPLATE_ENV)

    def test_relative_override_is_resolved_against_the_project_root(self):
        with patch(
            "infrastructure.config.config_templates.load_env_value",
            return_value=MOCK_CONFIG_TEMPLATE,
        ):
            path = selected_config_template(Path("D:/project"))

        self.assertEqual(path, Path("D:/project") / MOCK_CONFIG_TEMPLATE)

    def test_absolute_override_is_preserved(self):
        configured = Path("C:/shared/site-config.json")
        with patch(
            "infrastructure.config.config_templates.load_env_value",
            return_value=str(configured),
        ):
            path = selected_config_template(Path("D:/project"))

        self.assertEqual(path, configured)


class ConfigTemplateLoadingTests(unittest.TestCase):
    def test_loads_a_json_object(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.default.json"
            path.write_text(json.dumps({"schema_version": "1.2"}), encoding="utf-8")

            self.assertEqual(load_config_template(path), {"schema_version": "1.2"})

    def test_rejects_a_non_object_root(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.default.json"
            path.write_text("[]", encoding="utf-8")

            with self.assertRaises(ValueError):
                load_config_template(path)

    def test_missing_template_raises_file_not_found(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                load_config_template(Path(directory) / "absent.json")


if __name__ == "__main__":
    unittest.main()
