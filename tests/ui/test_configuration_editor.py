"""步骤 13：两个配置页共享编辑状态的装配器测试。

这里只验证装配器自己的契约：同一份草稿要么两页都回填成功，要么两页都回到调用前
的读数；页面校验失败的错误提示归页面自己的通道，不由装配器改写。具体控件行为在
``test_power_configuration_page`` / ``test_config_page`` 里覆盖。
"""

import unittest

from presentation.qt.config_projection import ConfigBackfillError
from presentation.qt.configuration_editor import (
    ConfigurationEditor,
    apply_config_to_pages,
)


class _Signals:
    """Qt 信号的替身：装配器只要求 ``connect`` 存在。"""

    def connect(self, *_args, **_kwargs):
        return None


class _Page:
    """页面窄接口替身：状态就是一份可回填的文档读数。"""

    def __init__(self, name, *, fail=False):
        self.name = name
        self.fail = fail
        self.value = name
        self.apply_calls = 0

    def build_config(self, base=None):
        return {"value": self.value}

    def apply_config(self, config):
        self.apply_calls += 1
        if self.fail:
            raise ConfigBackfillError(f"{self.name} 回填失败")
        self.value = config["value"]


class _EditorPage(_Page):
    """装配器需要的完整页面能力集合（信号 + 状态转发）。"""

    pa_unit_count_changed = _Signals()
    device_selection_finished = _Signals()
    validation_failed = _Signals()
    driver_mode_changed = _Signals()

    def is_dirty(self):
        return False

    def mark_clean(self):
        return None

    def set_test_mode(self, _enabled):
        return None

    def set_finalizing(self, _active):
        return None

    def set_pa_unit_count(self, _count):
        return None

    def apply_device_bindings(self, _bindings):
        return None


class AtomicBackfillTests(unittest.TestCase):
    def test_a_failing_second_page_rolls_the_first_page_back(self):
        first = _Page("first")
        second = _Page("second", fail=True)

        with self.assertRaises(ConfigBackfillError):
            apply_config_to_pages([first, second], {"value": "new"})

        self.assertEqual(first.value, "first")
        self.assertEqual(second.value, "second")

    def test_non_mapping_payload_is_rejected_before_touching_any_page(self):
        page = _Page("page")

        with self.assertRaises(ConfigBackfillError):
            apply_config_to_pages([page], None)

        self.assertEqual(page.apply_calls, 0)
        self.assertEqual(page.value, "page")

    def test_the_editor_uses_the_same_atomic_backfill(self):
        first = _EditorPage("first")
        second = _EditorPage("second", fail=True)
        editor = ConfigurationEditor(first, second)

        with self.assertRaises(ConfigBackfillError):
            editor.apply_config({"value": "new"})

        self.assertEqual(first.value, "first")
        self.assertEqual(second.value, "second")


if __name__ == "__main__":
    unittest.main()
