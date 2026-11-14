"""电源通道卡片的数据契约与页面交互。

只覆盖配置页仍依赖的行为：通道/角色/工作值/保护标志的取值规则，以及滚筒、``Cal.``、
保护勾选框和功率行的编辑信号。绘制几何与像素颜色不再作为回归门禁。
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QWidget  # noqa: E402

from application.dto.amplifier_inputs import MAX_INTER_CHANNEL_DELAY_S  # noqa: E402

from presentation.qt.power_card import (  # noqa: E402
    CHANNEL_MODES,
    CHANNEL_NAMES,
    POWER_CARD_HEIGHT,
    POWER_CARD_WIDTH,
    PROTECTION_KINDS,
    PowerChannelData,
    PowerSupplyCard,
    PowerSupplyCardData,
    power_card_layout,
    render_power_card,
)
from presentation.qt.theme import TOKENS  # noqa: E402


def _application():
    """离屏平台下复用已有 ``QApplication``；没有时创建一个。"""
    return QApplication.instance() or QApplication([])


def _data(
    *,
    ch1: bool = True,
    ch2: bool = True,
    ch1_voltage: bool = True,
    ch1_current: bool = True,
    ch2_voltage: bool = False,
    ch2_current: bool = True,
    ch1_cal: bool = False,
    ch2_cal: bool = False,
) -> PowerSupplyCardData:
    return PowerSupplyCardData(
        code="DP8B1519",
        model="DP832A",
        full_code="SN-1519",
        device_key="rigol technologies:dp8b1519",
        channels=(
            PowerChannelData(
                "CH1",
                enabled=ch1,
                voltage_set=12.0,
                current_set=1.5,
                voltage_protection_enabled=ch1_voltage,
                current_protection_enabled=ch1_current,
                efficiency_calibration_enabled=ch1_cal,
            ),
            PowerChannelData(
                "CH2",
                enabled=ch2,
                voltage_set=5.0,
                current_set=0.8,
                voltage_protection_enabled=ch2_voltage,
                current_protection_enabled=ch2_current,
                efficiency_calibration_enabled=ch2_cal,
            ),
        ),
    )


def _color_at(pixmap, x: float, y: float) -> QColor:
    """取位图在逻辑坐标 ``(x, y)`` 处的颜色（自动换算物理像素）。"""
    ratio = float(pixmap.devicePixelRatio())
    image = pixmap.toImage()
    return QColor.fromRgba(image.pixel(round(x * ratio), round(y * ratio)))


def _ink_count(image, rect) -> int:
    """``rect`` 内既不是全透明、也不是白底的像素数（文字、色块、描边都算）。"""
    count = 0
    for y in range(max(0, int(rect.top())), min(image.height(), int(rect.bottom()) + 1)):
        for x in range(max(0, int(rect.left())), min(image.width(), int(rect.right()) + 1)):
            color = QColor.fromRgba(image.pixel(x, y))
            is_white = color.red() > 250 and color.green() > 250 and color.blue() > 250
            if color.alpha() > 20 and not is_white:
                count += 1
    return count


class PowerCardDataTests(unittest.TestCase):
    """卡片数据只接受两个通道、两个有限非负工作值与两个 bool 保护开关。"""

    def test_channels_are_normalised_to_ch1_then_ch2(self):
        data = PowerSupplyCardData(
            code=" DP8B1519 ",
            channels=(
                PowerChannelData("CH2"),
                PowerChannelData("CH1"),
            ),
        )

        self.assertEqual(data.code, "DP8B1519")
        self.assertEqual(
            [channel.channel for channel in data.channels],
            list(CHANNEL_NAMES),
        )

    def test_two_distinct_channels_are_required(self):
        for channels in (
            (PowerChannelData("CH1"),),
            (PowerChannelData("CH1"), PowerChannelData("CH1")),
            (
                PowerChannelData("CH1"),
                PowerChannelData("CH2"),
                PowerChannelData("CH2"),
            ),
        ):
            with self.subTest(channels=[item.channel for item in channels]):
                with self.assertRaises(ValueError):
                    PowerSupplyCardData(code="DP8B1519", channels=channels)

    def test_channel_values_must_be_finite_and_non_negative(self):
        for field in ("voltage_set", "current_set"):
            for bad in (True, "5", None):
                with self.subTest(field=field, bad=bad):
                    with self.assertRaises(TypeError):
                        PowerChannelData("CH1", **{field: bad})
            for bad in (float("nan"), float("inf"), -0.1):
                with self.subTest(field=field, bad=bad):
                    with self.assertRaises(ValueError):
                        PowerChannelData("CH1", **{field: bad})

    def test_protection_flags_must_be_bool(self):
        for field in (
            "voltage_protection_enabled",
            "current_protection_enabled",
        ):
            for bad in (1, "yes", None):
                with self.subTest(field=field, bad=bad):
                    with self.assertRaises(TypeError):
                        PowerChannelData("CH1", **{field: bad})

    def test_channel_mode_and_enabled_stay_consistent(self):
        data = _data()
        updated = data.with_channel_mode("CH2", "dut")
        off = updated.with_channel_mode("CH2", "off")
        enabled = off.with_channel_mode("CH2", "driver")

        self.assertEqual(data.channel("CH2").mode, "driver")
        self.assertEqual(updated.channel("CH2").mode, "dut")
        self.assertTrue(updated.channel("CH2").enabled)
        self.assertEqual(off.channel("CH2").mode, "off")
        self.assertFalse(off.channel("CH2").enabled)
        self.assertEqual(enabled.channel("CH2").mode, "driver")
        self.assertEqual(
            updated.channel("CH2").voltage_set,
            data.channel("CH2").voltage_set,
            "换档只改角色，不改工作值",
        )
        self.assertEqual(
            updated.channel("CH2").protection_enabled("current"),
            data.channel("CH2").protection_enabled("current"),
            "换档只改角色，不改保护状态",
        )

    def test_channel_mode_must_be_off_driver_or_dut(self):
        for bad in ("on", "DUT", "", None):
            with self.subTest(mode=bad):
                with self.assertRaises(ValueError):
                    PowerChannelData("CH1", mode=bad)
        with self.assertRaises(ValueError):
            _data().with_channel_mode("CH1", "on")

    def test_power_setting_defaults_to_two_seconds_and_no_order(self):
        channel = _data().channel("CH1")

        self.assertIsNone(channel.power_order)
        self.assertEqual(channel.power_order_count, 0)
        self.assertEqual(channel.power_delay_s, 2.0)

    def test_power_setting_updates_only_the_selected_channel(self):
        data = _data().with_channel_power("CH1", 2, 3, 4.5)

        self.assertEqual(data.channel("CH1").power_order, 2)
        self.assertEqual(data.channel("CH1").power_order_count, 3)
        self.assertEqual(data.channel("CH1").power_delay_s, 4.5)
        self.assertIsNone(data.channel("CH2").power_order)
        self.assertEqual(data.channel("CH2").power_delay_s, 2.0)

    def test_power_setting_rejects_invalid_order_or_delay(self):
        for order in (0, -1, 4):
            with self.subTest(order=order):
                with self.assertRaises(ValueError):
                    _data().with_channel_power("CH1", order, 3, 2.0)
        with self.assertRaises(ValueError):
            _data().with_channel_power("CH1", None, -1, 2.0)
        for delay in (-0.1, MAX_INTER_CHANNEL_DELAY_S + 1):
            with self.subTest(delay=delay):
                with self.assertRaises(ValueError):
                    _data().with_channel_power("CH1", 1, 3, delay)

    def test_with_channel_protection_copies_only_that_flag(self):
        data = _data()
        updated = data.with_channel_protection("CH1", "voltage", False)

        self.assertFalse(updated.channel_protection("CH1", "voltage"))
        self.assertTrue(updated.channel_protection("CH1", "current"))
        self.assertEqual(updated.channel("CH1").voltage_set, 12.0)
        self.assertTrue(data.channel_protection("CH1", "voltage"), "原快照不变")
        self.assertEqual(
            updated.channel("CH2"),
            data.channel("CH2"),
            "只改一个通道的一种保护",
        )

    def test_efficiency_calibration_is_independent_per_channel(self):
        data = _data()
        self.assertFalse(data.channel_efficiency_calibration("CH1"))
        self.assertFalse(data.channel_efficiency_calibration("CH2"))

        updated = data.with_channel_efficiency_calibration("CH1", True)

        self.assertTrue(updated.channel_efficiency_calibration("CH1"))
        self.assertFalse(updated.channel_efficiency_calibration("CH2"))
        self.assertFalse(data.channel_efficiency_calibration("CH1"), "原快照不变")
        self.assertTrue(updated.channel("CH1").enabled, "不改变通道开关")

    def test_efficiency_calibration_must_be_bool(self):
        for bad in ("yes", 1, None):
            with self.subTest(value=bad):
                with self.assertRaises(TypeError):
                    PowerChannelData("CH1", efficiency_calibration_enabled=bad)
                with self.assertRaises(TypeError):
                    _data().with_channel_efficiency_calibration("CH1", bad)

    def test_unknown_channel_or_kind_is_a_key_error(self):
        with self.assertRaises(KeyError):
            _data().channel("CH3")
        with self.assertRaises(KeyError):
            _data().with_channel_mode("CH3", "off")
        with self.assertRaises(KeyError):
            _data().channel_protection("CH1", "power")
        with self.assertRaises(KeyError):
            _data().with_channel_protection("CH1", "power", True)






class PowerSupplyCardWidgetTests(unittest.TestCase):
    """真实控件：开关与保护勾选框可点击/可键盘操作，只改卡面状态，不发多余信号。"""

    @classmethod
    def setUpClass(cls):
        cls.application = _application()

    def widget(self) -> PowerSupplyCard:
        host = QWidget()
        host.resize(POWER_CARD_WIDTH + 40, POWER_CARD_HEIGHT + 40)
        host.show()
        self.addCleanup(host.deleteLater)
        card = PowerSupplyCard(_data(), host)
        card.move(20, 20)
        card.show()
        self.application.processEvents()
        return card




    def test_editing_a_value_changes_the_snapshot_and_emits(self):
        card = self.widget()
        events: list[tuple[str, str, float]] = []
        card.value_changed.connect(lambda *args: events.append(args))
        field = card.value_field("CH1", "voltage")
        field.setValue(7.5)

        self.assertAlmostEqual(card.data.channel("CH1").voltage_set, 7.5)
        self.assertEqual(events, [("CH1", "voltage", 7.5)])
        self.assertAlmostEqual(card.channel_value("CH1", "voltage"), 7.5)

    def test_set_data_syncs_values_without_emitting(self):
        card = self.widget()
        events: list[tuple[str, str, float]] = []
        card.value_changed.connect(lambda *args: events.append(args))

        card.set_data(_data(ch2=False))

        self.assertEqual(events, [])
        self.assertAlmostEqual(card.channel_value("CH2", "voltage"), 5.0)
        self.assertFalse(card.channel_enabled("CH2"))
        self.assertFalse(card.value_field("CH2", "voltage").isEnabled())

    def test_clicking_the_reel_cycles_only_that_channel(self):
        card = self.widget()
        events: list[tuple[str, str]] = []
        card.channel_mode_changed.connect(
            lambda channel, mode: events.append((channel, mode))
        )
        card.set_channel_mode("CH1", "off")
        selector = card.channel_mode_selector("CH1")
        self.assertEqual(card.channel_mode("CH1"), "off")

        # 点一下切到 DUT：通道开启，但还不碰驱放计划。
        QTest.mouseClick(selector, Qt.LeftButton, pos=selector.rect().center())
        self.assertEqual(events, [("CH1", "dut")])
        self.assertEqual(card.channel_mode("CH1"), "dut")
        self.assertTrue(card.value_field("CH1", "voltage").isEnabled())

        # 点两下切到 Driver。
        QTest.mouseClick(selector, Qt.LeftButton, pos=selector.rect().center())
        self.assertEqual(events, [("CH1", "dut"), ("CH1", "driver")])
        self.assertEqual(card.channel_mode("CH1"), "driver")
        self.assertEqual(card.channel_mode("CH2"), "driver")

        # 第三下回到 OFF，其余通道不受影响。
        QTest.mouseClick(selector, Qt.LeftButton, pos=selector.rect().center())
        self.assertEqual(
            events, [("CH1", "dut"), ("CH1", "driver"), ("CH1", "off")]
        )
        self.assertEqual(card.channel_mode("CH1"), "off")
        self.assertEqual(card.channel_mode("CH2"), "driver")
        self.assertFalse(card.value_field("CH1", "voltage").isEnabled())

    def test_driver_mode_unavailable_turns_driver_channels_off_and_skips_the_stop(self):
        card = self.widget()
        self.assertEqual(card.channel_mode("CH1"), "driver")

        card.set_driver_available(False)
        self.assertEqual(card.channel_mode("CH1"), "off")
        self.assertFalse(card.value_field("CH1", "voltage").isEnabled())

        selector = card.channel_mode_selector("CH1")
        QTest.mouseClick(selector, Qt.LeftButton, pos=selector.rect().center())
        self.assertEqual(card.channel_mode("CH1"), "dut")
        QTest.mouseClick(selector, Qt.LeftButton, pos=selector.rect().center())
        self.assertEqual(card.channel_mode("CH1"), "off")


    def test_power_row_shows_order_and_delay_and_emits_changes(self):
        card = self.widget()
        order_events: list[tuple[str, int]] = []
        delay_events: list[tuple[str, float]] = []
        card.power_order_changed.connect(
            lambda channel, order: order_events.append((channel, order))
        )
        card.power_delay_changed.connect(
            lambda channel, delay: delay_events.append((channel, delay))
        )
        card.set_channel_power("CH1", 2, 3, 2.5)

        combo = card.order_combo("CH1")
        field = card.delay_field("CH1")
        self.assertEqual(combo.count(), 3)
        self.assertEqual(
            [combo.itemData(index) for index in range(combo.count())],
            [1, 2, 3],
        )
        self.assertEqual(card.channel_power_order("CH1"), 2)
        self.assertAlmostEqual(card.channel_power_delay("CH1"), 2.5)

        combo.setCurrentIndex(0)
        self.assertEqual(order_events, [("CH1", 1)])
        self.assertEqual(card.channel_power_order("CH1"), 1)

        field.setValue(4.0)
        self.assertEqual(delay_events, [("CH1", 4.0)])
        self.assertAlmostEqual(card.channel_power_delay("CH1"), 4.0)

    def test_power_row_is_disabled_when_the_channel_is_off_or_has_no_plan(self):
        card = self.widget()
        card.set_channel_power("CH1", 1, 3, 2.0)
        self.assertTrue(card.order_combo("CH1").isEnabled())
        self.assertTrue(card.delay_field("CH1").isEnabled())

        card.set_channel_mode("CH1", "off")
        self.assertFalse(card.order_combo("CH1").isEnabled())
        self.assertFalse(card.delay_field("CH1").isEnabled())

        # CH2 仍在 Driver 档，但不在任何计划里：顺序无意义，间隔仍可设置。
        self.assertEqual(card.channel_mode("CH2"), "driver")
        self.assertFalse(card.order_combo("CH2").isEnabled())
        self.assertTrue(card.delay_field("CH2").isEnabled())



    def test_clicking_calibration_marks_multiple_channels(self):
        card = self.widget()
        events: list[tuple[str, bool]] = []
        card.efficiency_calibration_toggled.connect(
            lambda channel, marked: events.append((channel, marked))
        )

        ch1 = card.calibration_toggle("CH1")
        ch2 = card.calibration_toggle("CH2")
        QTest.mouseClick(ch1, Qt.LeftButton, pos=ch1.rect().center())
        QTest.mouseClick(ch2, Qt.LeftButton, pos=ch2.rect().center())

        self.assertEqual(events, [("CH1", True), ("CH2", True)])
        self.assertTrue(card.channel_efficiency_calibration("CH1"))
        self.assertTrue(card.channel_efficiency_calibration("CH2"))
        self.assertTrue(
            card.data.channel_efficiency_calibration("CH1")
            and card.data.channel_efficiency_calibration("CH2")
        )





    def test_clicking_a_protection_check_toggles_only_that_flag(self):
        card = self.widget()
        events: list[tuple[str, str, bool]] = []
        card.protection_toggled.connect(
            lambda channel, kind, enabled: events.append((channel, kind, enabled))
        )
        check = card.protection_check("CH1", "voltage")

        QTest.mouseClick(check, Qt.LeftButton, pos=check.rect().center())

        self.assertEqual(events, [("CH1", "voltage", False)])
        self.assertFalse(card.channel_protection("CH1", "voltage"))
        self.assertTrue(card.channel_protection("CH1", "current"))
        self.assertFalse(card.data.channel_protection("CH1", "voltage"))



    def test_off_channel_disables_its_protection_checks(self):
        card = self.widget()
        check = card.protection_check("CH2", "voltage")
        field = card.value_field("CH2", "voltage")
        selector = card.channel_mode_selector("CH2")

        # CH2 默认 Driver；点一下到 OFF。
        QTest.mouseClick(selector, Qt.LeftButton, pos=selector.rect().center())
        self.assertEqual(card.channel_mode("CH2"), "off")
        self.assertFalse(check.isEnabled())
        self.assertFalse(field.isEnabled())
        self.assertFalse(check.hasFocus())
        card.set_channel_mode("CH2", "dut")
        self.assertTrue(check.isEnabled())
        self.assertTrue(field.isEnabled())


    def test_set_data_updates_modes_and_checks_without_emitting(self):
        card = self.widget()
        mode_events: list[tuple[str, str]] = []
        protection_events: list[tuple[str, str, bool]] = []
        card.channel_mode_changed.connect(
            lambda channel, mode: mode_events.append((channel, mode))
        )
        card.protection_toggled.connect(
            lambda channel, kind, enabled: protection_events.append(
                (channel, kind, enabled)
            )
        )

        card.set_data(_data(ch2=False, ch1_voltage=False))

        self.assertEqual(mode_events, [])
        self.assertEqual(protection_events, [])
        self.assertEqual(card.channel_mode("CH1"), "driver")
        self.assertEqual(card.channel_mode("CH2"), "off")
        self.assertFalse(card.channel_protection("CH1", "voltage"))
        self.assertTrue(card.channel_protection("CH1", "current"))




if __name__ == "__main__":
    unittest.main()
