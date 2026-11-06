"""电源通道卡片：数据、几何、绘制、通道开关与两个保护勾选框。

卡片是配置页的电源卡面资产，只有正面。这里锁住四类契约：

- :class:`PowerCardDataTests`：两个通道、通道顺序、两个工作值必须是有限非负数，
  两个保护开关必须是 bool；
- :class:`PowerCardLayoutTests`：440×232 卡面里每个开关、数值框与勾选框都在自己的
  通道内；第一行是 ``VS / IS``，第二行是电压保护、电流保护两个勾选框，底部一行
  自动阈值提示；
- :class:`PowerCardRenderTests`：导出的位图与页面控件共用一份绘制，开启/关闭的
  通道底色、数值框、勾选框、提示与圆形电源键都按状态变化；
- :class:`PowerSupplyCardWidgetTests`：通道开关与保护勾选框可点击、可键盘操作、
  只改卡面状态，并且程序化设置与整卡换数据都不会悄悄发信号。

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
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QWidget  # noqa: E402

from presentation.qt.power_card import (  # noqa: E402
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
            ),
            PowerChannelData(
                "CH2",
                enabled=ch2,
                voltage_set=5.0,
                current_set=0.8,
                voltage_protection_enabled=ch2_voltage,
                current_protection_enabled=ch2_current,
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

    def test_with_channel_enabled_copies_only_the_switch_state(self):
        data = _data()
        updated = data.with_channel_enabled("CH2", False)

        self.assertIsNot(data, updated)
        self.assertTrue(data.channel("CH2").enabled)
        self.assertFalse(updated.channel("CH2").enabled)
        self.assertEqual(
            updated.channel("CH2").voltage_set,
            data.channel("CH2").voltage_set,
        )
        self.assertEqual(
            updated.channel("CH2").protection_enabled("current"),
            data.channel("CH2").protection_enabled("current"),
            "关闭通道只改开关，不改工作值与保护状态",
        )

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

    def test_unknown_channel_or_kind_is_a_key_error(self):
        with self.assertRaises(KeyError):
            _data().channel("CH3")
        with self.assertRaises(KeyError):
            _data().with_channel_enabled("CH3", False)
        with self.assertRaises(KeyError):
            _data().channel_protection("CH1", "power")
        with self.assertRaises(KeyError):
            _data().with_channel_protection("CH1", "power", True)


class PowerCardLayoutTests(unittest.TestCase):
    """卡面几何：440×232，两个通道各占一半，值、勾选框与提示都落在自己的通道里。"""

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
                self.assertTrue(channel.panel.contains(channel.hint))
                self.assertLessEqual(channel.switch.right(), channel.label.left())
                self.assertEqual(
                    channel.switch.center().y(),
                    channel.label.center().y(),
                    "开关与通道标题必须处在同一行",
                )
                self.assertEqual(len(channel.value_labels), 2)
                self.assertEqual(len(channel.value_boxes), 2)
                self.assertEqual(len(channel.protection_checks), 2)
                self.assertEqual(len(channel.protection_indicators), 2)
                self.assertEqual(len(channel.protection_labels), 2)
                for label, box in zip(channel.value_labels, channel.value_boxes):
                    self.assertTrue(channel.panel.contains(label))
                    self.assertTrue(channel.panel.contains(box))
                    self.assertLessEqual(label.right(), box.left())
                    self.assertEqual(label.center().y(), box.center().y())
                self.assertLessEqual(
                    channel.switch.bottom(), channel.value_boxes[0].top()
                )
                self.assertLessEqual(
                    channel.label.bottom(), channel.value_boxes[0].top()
                )
                for index, (check, indicator, label_rect) in enumerate(
                    zip(
                        channel.protection_checks,
                        channel.protection_indicators,
                        channel.protection_labels,
                    )
                ):
                    self.assertTrue(channel.panel.contains(check))
                    self.assertTrue(check.contains(indicator))
                    self.assertTrue(check.contains(label_rect))
                    self.assertLessEqual(
                        indicator.right(), label_rect.left(), "文字必须留在方块右侧"
                    )
                    self.assertAlmostEqual(
                        indicator.center().x(),
                        channel.value_labels[index].center().x(),
                        msg="勾选框方块与上一行 VS / IS 标签同一条中线",
                    )
                    self.assertAlmostEqual(
                        label_rect.left(),
                        channel.value_boxes[index].left(),
                        msg="勾选框文字与上一行数值框左对齐",
                    )
                self.assertLessEqual(
                    channel.value_boxes[0].bottom(),
                    channel.protection_checks[0].top(),
                )
                self.assertLessEqual(
                    channel.protection_checks[0].bottom(), channel.hint.top()
                )

    def test_values_are_arranged_vs_then_is(self):
        channel = power_card_layout().channels[0]
        labels = channel.value_labels

        self.assertEqual(len(labels), 2)
        self.assertLess(labels[0].center().x(), labels[1].center().x())
        self.assertEqual(labels[0].center().y(), labels[1].center().y())

    def test_protection_checks_are_on_one_row(self):
        for channel in power_card_layout().channels:
            with self.subTest(channel=channel.channel):
                first, second = channel.protection_checks
                self.assertLess(first.center().x(), second.center().x())
                self.assertEqual(first.center().y(), second.center().y())
                self.assertEqual(
                    channel.protection_indicators[0].center().y(),
                    channel.protection_indicators[1].center().y(),
                )

    def test_switch_and_label_form_one_centred_row(self):
        """开关在左、标题在右，两者作为一整组在通道里居中。"""
        for channel in power_card_layout().channels:
            with self.subTest(channel=channel.channel):
                self.assertLess(channel.switch.right(), channel.label.left())
                self.assertAlmostEqual(
                    (channel.switch.left() + channel.label.right()) / 2.0,
                    channel.panel.center().x(),
                )
                self.assertAlmostEqual(
                    channel.switch.center().y(),
                    channel.label.center().y(),
                )
                self.assertLessEqual(channel.switch.width(), channel.panel.width() / 3.0)


class PowerCardRenderTests(unittest.TestCase):
    """导出的卡面：尺寸、透明底、开启/关闭配色、两个值与两个勾选框都按数据变化。"""

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
        """两个工作值都必须真的画出来；离屏环境字体可能退化成方框，因此只数墨迹。"""
        image = render_power_card(_data()).toImage()
        for channel in power_card_layout().channels:
            for index, box in enumerate(channel.value_boxes):
                with self.subTest(channel=channel.channel, value=index):
                    self.assertGreater(
                        _ink_count(image, box.adjusted(2.0, 2.0, -2.0, -2.0)),
                        0,
                        "数值框里应该画出两个工作值之一，而不是空框",
                    )

    def test_protection_indicators_show_checked_state(self):
        """勾选后方块用通道强调色填满，未勾选只留白底描边。"""
        layout = power_card_layout()
        for index, channel in enumerate(layout.channels):
            for kind_index, indicator in enumerate(channel.protection_indicators):
                with self.subTest(channel=channel.channel, kind=kind_index):
                    sample = (indicator.left() + 4.0, indicator.top() + 4.0)
                    checked = _data(
                        ch1_voltage=True,
                        ch1_current=True,
                        ch2_voltage=True,
                        ch2_current=True,
                    )
                    unchecked = _data(
                        ch1_voltage=False,
                        ch1_current=False,
                        ch2_voltage=False,
                        ch2_current=False,
                    )
                    expected = (
                        TOKENS["primary"] if index == 0 else TOKENS["warning"]
                    ).lower()
                    self.assertEqual(
                        _color_at(render_power_card(checked), *sample).name(),
                        expected,
                    )
                    self.assertEqual(
                        _color_at(render_power_card(unchecked), *sample).name(),
                        TOKENS["surface"].lower(),
                    )

    def test_protection_labels_and_hint_draw_ink(self):
        image = render_power_card(_data()).toImage()
        for channel in power_card_layout().channels:
            for label_rect in channel.protection_labels:
                with self.subTest(channel=channel.channel, rect=label_rect):
                    self.assertGreater(
                        _ink_count(image, label_rect.adjusted(2.0, 2.0, -2.0, -2.0)),
                        0,
                        "勾选框右侧应画出“电压保护”/“电流保护”",
                    )
            self.assertGreater(
                _ink_count(image, channel.hint),
                0,
                "通道底部应画出自动阈值提示",
            )

    def test_power_switch_body_follows_the_channel_state(self):
        """圆形电源键：关闭是深灰金属面，点亮后转绿；采样点避开图标与内阴影。"""
        layout = power_card_layout()
        for index, channel in enumerate(layout.channels):
            with self.subTest(channel=channel.channel):
                sample = (
                    channel.switch.center().x(),
                    channel.switch.center().y() + channel.switch.height() * 0.06,
                )
                both_on = _data()
                one_off = _data(ch1=index != 0, ch2=index != 1)
                self.assertEqual(
                    _color_at(render_power_card(both_on), *sample).name(),
                    TOKENS["power_switch_on"].lower(),
                )
                self.assertEqual(
                    _color_at(render_power_card(one_off), *sample).name(),
                    TOKENS["power_switch_off"].lower(),
                )

    def test_lit_switch_emits_a_green_halo_outside_the_button(self):
        layout = power_card_layout()
        channel = layout.channels[0]
        sample = (channel.switch.right() + 6.0, channel.switch.center().y())

        lit = _color_at(render_power_card(_data()), *sample)
        dark = _color_at(render_power_card(_data(ch1=False)), *sample)

        self.assertNotEqual(lit.name(), dark.name(), "点亮后按钮外侧应有辉光")
        self.assertGreater(
            lit.green() - lit.red(),
            dark.green() - dark.red() + 5,
            "辉光应偏绿色，而不是把通道底色整体压暗",
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

    def test_widget_has_the_designed_size_and_control_geometry(self):
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
            for kind, rect in zip(PROTECTION_KINDS, channel.protection_checks):
                check = card.protection_check(channel.channel, kind)
                self.assertIs(check.parent(), card)
                self.assertEqual(
                    (
                        check.geometry().x(),
                        check.geometry().y(),
                        check.geometry().width(),
                        check.geometry().height(),
                    ),
                    (
                        round(rect.x()),
                        round(rect.y()),
                        round(rect.width()),
                        round(rect.height()),
                    ),
                )
            for kind, rect in zip(("voltage", "current"), channel.value_boxes):
                field = card.value_field(channel.channel, kind)
                self.assertIs(field.parent(), card)
                self.assertEqual(
                    (
                        field.geometry().x(),
                        field.geometry().y(),
                        field.geometry().width(),
                        field.geometry().height(),
                    ),
                    (
                        round(rect.x()),
                        round(rect.y()),
                        round(rect.width()),
                        round(rect.height()),
                    ),
                )

    def test_value_fields_use_the_channel_display_limits(self):
        card = self.widget()

        for kind, limits in (("voltage", (0.0, 50.0)), ("current", (0.0, 10.0))):
            with self.subTest(kind=kind):
                field = card.value_field("CH1", kind)
                self.assertEqual((field.minimum(), field.maximum()), limits)
                self.assertEqual(field.decimals(), 2)
                self.assertEqual(
                    field.buttonSymbols(), QAbstractSpinBox.ButtonSymbols.NoButtons
                )

    def test_value_fields_follow_the_card_scale(self):
        card = self.widget()
        card.set_scale(0.8)
        layout = power_card_layout()

        self.assertEqual(
            (card.width(), card.height()),
            (round(POWER_CARD_WIDTH * 0.8), round(POWER_CARD_HEIGHT * 0.8)),
        )
        box = layout.channels[0].value_boxes[0]
        field = card.value_field("CH1", "voltage")
        self.assertEqual(field.geometry().x(), round(box.x() * 0.8))
        self.assertEqual(field.geometry().width(), round(box.width() * 0.8))
        self.assertIsInstance(card.channel_controls("CH1"), dict)

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
        self.assertFalse(card.value_field("CH1", "voltage").isEnabled())

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

    def test_keyboard_space_toggles_and_tab_moves_between_controls(self):
        card = self.widget()
        switch = card.channel_switch("CH1")
        switch.setFocus()
        self.application.processEvents()

        QTest.keyClick(switch, Qt.Key_Space)

        self.assertFalse(card.channel_enabled("CH1"))
        self.assertTrue(switch.hasFocus())

    def test_keyboard_space_toggles_a_protection_check(self):
        card = self.widget()
        check = card.protection_check("CH2", "current")
        check.setFocus()
        self.application.processEvents()

        QTest.keyClick(check, Qt.Key_Space)

        self.assertFalse(card.channel_protection("CH2", "current"))

    def test_closed_channel_disables_its_protection_checks(self):
        card = self.widget()
        check = card.protection_check("CH2", "voltage")
        field = card.value_field("CH2", "voltage")
        switch = card.channel_switch("CH2")

        QTest.mouseClick(switch, Qt.LeftButton, pos=switch.rect().center())
        self.assertFalse(check.isEnabled())
        self.assertFalse(field.isEnabled())
        self.assertFalse(check.hasFocus())
        card.set_channel_enabled("CH2", True)
        self.assertTrue(check.isEnabled())
        self.assertTrue(field.isEnabled())

    def test_programmatic_set_is_silent_unless_asked_to_emit(self):
        card = self.widget()
        toggle_events: list[tuple[str, bool]] = []
        protection_events: list[tuple[str, str, bool]] = []
        card.channel_toggled.connect(
            lambda channel, enabled: toggle_events.append((channel, enabled))
        )
        card.protection_toggled.connect(
            lambda channel, kind, enabled: protection_events.append(
                (channel, kind, enabled)
            )
        )

        card.set_channel_enabled("CH2", False)
        card.set_protection_enabled("CH2", "voltage", True)
        self.assertEqual(toggle_events, [])
        self.assertEqual(protection_events, [])
        self.assertFalse(card.channel_enabled("CH2"))
        self.assertTrue(card.channel_protection("CH2", "voltage"))

        card.set_channel_enabled("CH2", True, emit=True)
        card.set_protection_enabled("CH2", "voltage", False, emit=True)
        self.assertEqual(toggle_events, [("CH2", True)])
        self.assertEqual(protection_events, [("CH2", "voltage", False)])
        self.assertTrue(card.channel_enabled("CH2"))
        self.assertFalse(card.channel_protection("CH2", "voltage"))

    def test_set_data_updates_switches_and_checks_without_emitting(self):
        card = self.widget()
        toggle_events: list[tuple[str, bool]] = []
        protection_events: list[tuple[str, str, bool]] = []
        card.channel_toggled.connect(
            lambda channel, enabled: toggle_events.append((channel, enabled))
        )
        card.protection_toggled.connect(
            lambda channel, kind, enabled: protection_events.append(
                (channel, kind, enabled)
            )
        )

        card.set_data(_data(ch2=False, ch1_voltage=False))

        self.assertEqual(toggle_events, [])
        self.assertEqual(protection_events, [])
        self.assertTrue(card.channel_enabled("CH1"))
        self.assertFalse(card.channel_enabled("CH2"))
        self.assertFalse(card.channel_protection("CH1", "voltage"))
        self.assertTrue(card.channel_protection("CH1", "current"))

    def test_tooltips_explain_units_ranges_and_the_edit_boundary(self):
        card = self.widget()

        self.assertIn("VS 12.00 V", card.toolTip())
        self.assertIn("VS 电压设定", card.toolTip())
        self.assertIn("SN-1519", card.toolTip(), "身份详情放在卡面悬浮提示里")
        self.assertIn(
            "不写入配置",
            card.channel_switch("CH1").toolTip(),
            "通道开关只是本次编辑的启用状态，必须把边界说清楚",
        )
        field_tip = card.value_field("CH1", "voltage").toolTip()
        self.assertIn("CH1", field_tip)
        self.assertIn("0–50", field_tip)
        protection_tip = card.protection_check("CH1", "voltage").toolTip()
        self.assertIn("电压保护", protection_tip)
        self.assertIn("高 10%", protection_tip)

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
