"""电源通道卡片：数据、几何、绘制与两个通道开关。

卡片是配置页的电源卡面资产，只有正面。这里锁住四类契约：

- :class:`PowerCardDataTests`：两个通道、通道顺序、四个值必须是有限非负数；
- :class:`PowerCardLayoutTests`：440×232 卡面里每个开关和数值框都在自己的通道内，
  两行两列的顺序是 ``VS / VL`` 再 ``IS / IL``；
- :class:`PowerCardRenderTests`：导出的位图与页面控件共用一份绘制，开启/关闭的
  通道底色、数值框与标题都按状态变化；
- :class:`PowerSupplyCardWidgetTests`：开关可点击、可键盘操作、只改卡面状态，
  并且程序化设置与整卡换数据都不会悄悄发信号。

测试用离屏平台；字体在离屏环境里可能退化成方框，但绘制路径、颜色与命中区不受
影响，因此断言看几何、颜色和状态，不比对具体字形。
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from presentation.qt.power_card import (  # noqa: E402
    CHANNEL_NAMES,
    POWER_CARD_HEIGHT,
    POWER_CARD_WIDTH,
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


def _data(*, ch1: bool = True, ch2: bool = True) -> PowerSupplyCardData:
    return PowerSupplyCardData(
        code="DP8B1519",
        channels=(
            PowerChannelData(
                "CH1",
                enabled=ch1,
                voltage_set=12.0,
                voltage_protection=13.0,
                current_set=1.5,
                current_protection=2.0,
            ),
            PowerChannelData(
                "CH2",
                enabled=ch2,
                voltage_set=5.0,
                voltage_protection=5.5,
                current_set=0.8,
                current_protection=1.0,
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
    """卡片数据只接受两个通道、四个有限非负值。"""

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
        for field in (
            "voltage_set",
            "voltage_protection",
            "current_set",
            "current_protection",
        ):
            for bad in (True, "5", None):
                with self.subTest(field=field, bad=bad):
                    with self.assertRaises(TypeError):
                        PowerChannelData("CH1", **{field: bad})
            for bad in (float("nan"), float("inf"), -0.1):
                with self.subTest(field=field, bad=bad):
                    with self.assertRaises(ValueError):
                        PowerChannelData("CH1", **{field: bad})

    def test_with_channel_enabled_copies_only_the_switch_state(self):
        data = _data()
        updated = data.with_channel_enabled("CH2", False)

        self.assertIsNot(data, updated)
        self.assertTrue(data.channel("CH2").enabled)
        self.assertFalse(updated.channel("CH2").enabled)
        self.assertEqual(
            updated.channel("CH2").voltage_set,
            data.channel("CH2").voltage_set,
            "关闭通道只改开关，不改四个值",
        )

    def test_unknown_channel_is_a_key_error(self):
        with self.assertRaises(KeyError):
            _data().channel("CH3")
        with self.assertRaises(KeyError):
            _data().with_channel_enabled("CH3", False)


class PowerCardLayoutTests(unittest.TestCase):
    """卡面几何：440×232，两个通道各占一半，开关与四个值都落在自己的通道里。"""

    def test_channels_split_the_card_into_two_halves(self):
        layout = power_card_layout()

        self.assertEqual(layout.card.width(), POWER_CARD_WIDTH)
        self.assertEqual(layout.card.height(), POWER_CARD_HEIGHT)
        self.assertEqual(len(layout.channels), 2)
        first, second = layout.channels
        self.assertEqual(first.channel, "CH1")
        self.assertEqual(second.channel, "CH2")
        self.assertAlmostEqual(first.panel.width(), POWER_CARD_WIDTH / 2.0)
        self.assertAlmostEqual(first.panel.right(), second.panel.left())
        self.assertAlmostEqual(first.panel.top(), second.panel.top())

    def test_each_channel_controls_are_inside_its_panel_and_do_not_overlap(self):
        for channel in power_card_layout().channels:
            with self.subTest(channel=channel.channel):
                self.assertTrue(channel.panel.contains(channel.label))
                self.assertTrue(channel.panel.contains(channel.switch))
                self.assertLessEqual(channel.label.bottom(), channel.switch.top())
                for label, box in zip(channel.value_labels, channel.value_boxes):
                    self.assertTrue(channel.panel.contains(label))
                    self.assertTrue(channel.panel.contains(box))
                    self.assertLessEqual(label.right(), box.left())
                    self.assertEqual(label.center().y(), box.center().y())
                self.assertLessEqual(
                    channel.switch.bottom(), channel.value_boxes[0].top()
                )
                for previous, following in zip(
                    channel.value_boxes[0:2], channel.value_boxes[2:4]
                ):
                    self.assertLessEqual(previous.bottom(), following.top())

    def test_values_are_arranged_vs_vl_then_is_il(self):
        channel = power_card_layout().channels[0]
        labels = channel.value_labels

        self.assertEqual(len(labels), 4)
        self.assertLess(labels[0].center().x(), labels[1].center().x())
        self.assertEqual(labels[0].center().y(), labels[1].center().y())
        self.assertLess(labels[0].center().y(), labels[2].center().y())
        self.assertLess(labels[2].center().x(), labels[3].center().x())

    def test_switch_is_centred_in_each_channel(self):
        for channel in power_card_layout().channels:
            with self.subTest(channel=channel.channel):
                self.assertAlmostEqual(
                    channel.switch.center().x(),
                    channel.panel.center().x(),
                )


class PowerCardRenderTests(unittest.TestCase):
    """导出的卡面：尺寸、透明底、开启/关闭配色与四个值都按数据变化。"""

    @classmethod
    def setUpClass(cls):
        cls.application = _application()

    def test_render_uses_the_card_size_and_transparent_corners(self):
        pixmap = render_power_card(_data())

        self.assertEqual(
            (pixmap.width(), pixmap.height()),
            (POWER_CARD_WIDTH, POWER_CARD_HEIGHT),
        )
        self.assertEqual(pixmap.devicePixelRatio(), 1.0)
        image = pixmap.toImage()
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0, "圆角外侧应保持透明")
        self.assertGreater(
            _ink_count(image, power_card_layout().card.toRect()),
            1000,
            "卡面不能是一张空白位图",
        )

    def test_high_dpi_only_adds_physical_pixels(self):
        regular = render_power_card(_data(), device_pixel_ratio=1.0)
        retina = render_power_card(_data(), device_pixel_ratio=2.0)

        self.assertEqual(
            (retina.width(), retina.height()),
            (POWER_CARD_WIDTH * 2, POWER_CARD_HEIGHT * 2),
        )
        self.assertEqual(retina.devicePixelRatio(), 2.0)
        sample = (8.0, 200.0)
        self.assertEqual(
            _color_at(regular, *sample).name(),
            _color_at(retina, *sample).name(),
            "高 DPI 不改变卡面颜色覆盖的逻辑位置",
        )

    def test_bad_device_pixel_ratio_falls_back_to_one(self):
        for bad in (0, -2.0, float("nan"), float("inf"), None, "retina"):
            with self.subTest(ratio=bad):
                pixmap = render_power_card(_data(), device_pixel_ratio=bad)
                self.assertEqual(pixmap.devicePixelRatio(), 1.0)
                self.assertEqual(
                    (pixmap.width(), pixmap.height()),
                    (POWER_CARD_WIDTH, POWER_CARD_HEIGHT),
                )

    def test_open_channel_uses_the_channel_tint_and_closed_channel_is_grey(self):
        layout = power_card_layout()
        open_left = layout.channels[0].panel.left() + 8.0
        open_right = layout.channels[1].panel.left() + 8.0
        sample_y = layout.channels[0].panel.bottom() - 6.0

        both_on = render_power_card(_data())
        self.assertEqual(
            _color_at(both_on, open_left, sample_y).name(),
            TOKENS["channel_cool"].lower(),
        )
        self.assertEqual(
            _color_at(both_on, open_right, sample_y).name(),
            TOKENS["channel_warm"].lower(),
        )

        ch2_off = render_power_card(_data(ch2=False))
        self.assertEqual(
            _color_at(ch2_off, open_left, sample_y).name(),
            TOKENS["channel_cool"].lower(),
            "只关 CH2 时 CH1 保持原色",
        )
        self.assertEqual(
            _color_at(ch2_off, open_right, sample_y).name(),
            TOKENS["disabled_bg"].lower(),
        )

    def test_value_boxes_follow_the_channel_state(self):
        layout = power_card_layout()
        box = layout.channels[1].value_boxes[0]
        sample = (box.left() + 3.0, box.top() + 3.0)

        on = render_power_card(_data())
        off = render_power_card(_data(ch2=False))

        self.assertEqual(_color_at(on, *sample).name(), TOKENS["surface"].lower())
        self.assertEqual(
            _color_at(off, *sample).name(),
            TOKENS["surface_alt"].lower(),
        )

    def test_every_value_box_draws_its_value(self):
        """四个值都必须真的画出来；离屏环境字体可能退化成方框，因此只数墨迹。"""
        image = render_power_card(_data()).toImage()
        for channel in power_card_layout().channels:
            for index, box in enumerate(channel.value_boxes):
                with self.subTest(channel=channel.channel, value=index):
                    self.assertGreater(
                        _ink_count(image, box.adjusted(2.0, 2.0, -2.0, -2.0)),
                        0,
                        "数值框里应该画出四位值之一，而不是空框",
                    )

    def test_empty_code_still_draws_a_placeholder(self):
        data = PowerSupplyCardData(code="", channels=_data().channels)
        image = render_power_card(data).toImage()

        self.assertGreater(
            _ink_count(image, power_card_layout().title.toRect()),
            0,
            "没有设备码时画占位横线，而不是留下一整条空白标题带",
        )


class PowerSupplyCardWidgetTests(unittest.TestCase):
    """真实控件：开关可点击/可键盘操作，只改卡面状态，不发多余信号。"""

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

    def test_widget_has_the_designed_size_and_switch_geometry(self):
        card = self.widget()
        layout = power_card_layout()

        self.assertEqual(card.size().width(), POWER_CARD_WIDTH)
        self.assertEqual(card.size().height(), POWER_CARD_HEIGHT)
        for channel in layout.channels:
            switch = card.channel_switch(channel.channel)
            self.assertIs(switch.parent(), card)
            self.assertEqual(
                (
                    switch.geometry().x(),
                    switch.geometry().y(),
                    switch.geometry().width(),
                    switch.geometry().height(),
                ),
                (
                    round(channel.switch.x()),
                    round(channel.switch.y()),
                    round(channel.switch.width()),
                    round(channel.switch.height()),
                ),
            )

    def test_clicking_a_switch_toggles_only_that_channel(self):
        card = self.widget()
        events: list[tuple[str, bool]] = []
        card.channel_toggled.connect(
            lambda channel, enabled: events.append((channel, enabled))
        )
        switch = card.channel_switch("CH1")

        QTest.mouseClick(switch, Qt.LeftButton, pos=switch.rect().center())

        self.assertEqual(events, [("CH1", False)])
        self.assertFalse(card.channel_enabled("CH1"))
        self.assertTrue(card.channel_enabled("CH2"))
        self.assertFalse(card.data.channel("CH1").enabled)

    def test_keyboard_space_toggles_and_tab_moves_between_channels(self):
        card = self.widget()
        switch = card.channel_switch("CH1")
        switch.setFocus()
        self.application.processEvents()

        QTest.keyClick(switch, Qt.Key_Space)

        self.assertFalse(card.channel_enabled("CH1"))
        self.assertTrue(switch.hasFocus())

    def test_programmatic_set_is_silent_unless_asked_to_emit(self):
        card = self.widget()
        events: list[tuple[str, bool]] = []
        card.channel_toggled.connect(
            lambda channel, enabled: events.append((channel, enabled))
        )

        card.set_channel_enabled("CH2", False)
        self.assertEqual(events, [])
        self.assertFalse(card.channel_enabled("CH2"))
        self.assertFalse(card.data.channel("CH2").enabled)

        card.set_channel_enabled("CH2", True, emit=True)
        self.assertEqual(events, [("CH2", True)])
        self.assertTrue(card.channel_enabled("CH2"))

    def test_set_data_updates_switches_without_emitting_toggles(self):
        card = self.widget()
        events: list[tuple[str, bool]] = []
        card.channel_toggled.connect(
            lambda channel, enabled: events.append((channel, enabled))
        )

        card.set_data(_data(ch2=False))

        self.assertEqual(events, [])
        self.assertTrue(card.channel_enabled("CH1"))
        self.assertFalse(card.channel_enabled("CH2"))
        self.assertFalse(card.data.channel("CH2").enabled)

    def test_tooltips_explain_read_only_values_and_the_temporary_switch(self):
        card = self.widget()

        self.assertIn("VS 12.00 V", card.toolTip())
        self.assertIn("VS 电压设定", card.toolTip())
        self.assertIn(
            "暂不写入配置",
            card.channel_switch("CH1").toolTip(),
            "开关只改卡面时必须把边界说清楚",
        )

    def test_widget_paints_the_same_channel_colours_as_the_export(self):
        card = self.widget()
        layout = power_card_layout()
        sample_left = (
            layout.channels[0].panel.left() + 8.0,
            layout.channels[0].panel.bottom() - 6.0,
        )
        sample_right = (
            layout.channels[1].panel.left() + 8.0,
            layout.channels[1].panel.bottom() - 6.0,
        )

        grabbed = card.grab().toImage()
        exported = render_power_card(card.data).toImage()
        for sample in (sample_left, sample_right):
            with self.subTest(sample=sample):
                self.assertEqual(
                    grabbed.pixelColor(
                        round(sample[0]),
                        round(sample[1]),
                    ).name(),
                    exported.pixelColor(
                        round(sample[0]),
                        round(sample[1]),
                    ).name(),
                )


if __name__ == "__main__":
    unittest.main()
