"""电源通道卡片：数据、几何、绘制、角色滚筒与两个保护勾选框。

卡片是配置页的电源卡面资产，只有正面。这里锁住四类契约：

- :class:`PowerCardDataTests`：两个通道、OFF/DUT/Driver 角色、两个工作值必须是
  有限非负数，两个保护开关必须是 bool；
- :class:`PowerCardLayoutTests`：卡面里每个角色滚筒、数值框与勾选框都在自己的通道内；
  通道底色从卡片顶边铺到卡底，两个滚筒贴住左右上角，设备码铭牌在顶部正中；第一行是
  ``VS / IS``，第二行是左右两个勾选框（文字都写「10%保护」，左右位置区分电压/电流），
  卡片到勾选行结束，没有底部说明行；
- :class:`PowerCardRenderTests`：导出的位图与页面控件共用一份绘制，OFF/DUT/Driver
  的通道底色、数值框、勾选框与深色滚筒都按状态变化；
- :class:`PowerSupplyCardWidgetTests`：角色滚筒与保护勾选框可点击、可键盘操作、
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

    def test_channel_mode_and_enabled_stay_consistent(self):
        data = _data()
        updated = data.with_channel_mode("CH2", "dut")
        off = updated.with_channel_mode("CH2", "off")
        compat = off.with_channel_enabled("CH2", True)

        self.assertEqual(data.channel("CH2").mode, "driver")
        self.assertEqual(updated.channel("CH2").mode, "dut")
        self.assertTrue(updated.channel("CH2").enabled)
        self.assertEqual(off.channel("CH2").mode, "off")
        self.assertFalse(off.channel("CH2").enabled)
        self.assertEqual(compat.channel("CH2").mode, "driver")
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

    def test_unknown_channel_or_kind_is_a_key_error(self):
        with self.assertRaises(KeyError):
            _data().channel("CH3")
        with self.assertRaises(KeyError):
            _data().with_channel_mode("CH3", "off")
        with self.assertRaises(KeyError):
            _data().channel_protection("CH1", "power")
        with self.assertRaises(KeyError):
            _data().with_channel_protection("CH1", "power", True)


class PowerCardLayoutTests(unittest.TestCase):
    """卡面几何：两个通道各占一半，值、勾选框与提示都落在自己的通道里。"""

    def test_channels_split_the_card_into_two_halves(self):
        layout = power_card_layout()

        self.assertEqual(layout.card.width(), POWER_CARD_WIDTH)
        self.assertEqual(layout.card.height(), POWER_CARD_HEIGHT)
        self.assertEqual(len(layout.channels), 2)
        first, second = layout.channels
        self.assertEqual(first.channel, "CH1")
        self.assertEqual(second.channel, "CH2")
        # ``panel`` 仍是半个卡片的命中区。
        self.assertAlmostEqual(first.panel.width(), POWER_CARD_WIDTH / 2.0)
        self.assertAlmostEqual(first.panel.right(), second.panel.left())
        self.assertAlmostEqual(first.panel.top(), second.panel.top())
        for channel in layout.channels:
            self.assertAlmostEqual(channel.panel.top(), layout.card.top())
            self.assertAlmostEqual(channel.panel.bottom(), layout.card.bottom())

    def test_channel_surfaces_are_inset_blocks_with_a_white_seam(self):
        """通道底色是内缩 5 的圆角色块，两块之间留 10 宽白缝。"""
        layout = power_card_layout()
        first, second = layout.channels

        for channel in layout.channels:
            with self.subTest(channel=channel.channel):
                self.assertTrue(channel.panel.contains(channel.panel_surface))
                self.assertAlmostEqual(channel.panel_surface.width(), 210.0)
                self.assertAlmostEqual(
                    channel.panel_surface.height(), POWER_CARD_HEIGHT - 10.0
                )
        self.assertAlmostEqual(
            first.panel_surface.left(), first.panel.left() + 5.0
        )
        self.assertAlmostEqual(
            second.panel_surface.right(), second.panel.right() - 5.0
        )
        self.assertAlmostEqual(first.panel_surface.top(), 5.0)
        self.assertAlmostEqual(
            first.panel_surface.bottom(), POWER_CARD_HEIGHT - 5.0
        )
        seam = second.panel_surface.left() - first.panel_surface.right()
        self.assertAlmostEqual(seam, 10.0, msg="中央白缝宽 10")
        self.assertAlmostEqual(
            first.panel_surface.center().x(),
            POWER_CARD_WIDTH / 4.0,
            msg="左右色块各自在半个卡片里居中",
        )

    def test_each_channel_controls_are_inside_its_panel_and_do_not_overlap(self):
        for channel in power_card_layout().channels:
            with self.subTest(channel=channel.channel):
                self.assertTrue(channel.panel.contains(channel.mode_selector))
                self.assertTrue(
                    channel.panel_surface.contains(channel.mode_selector),
                    "滚筒落在真正画出来的圆角色块里",
                )
                self.assertEqual(len(channel.value_labels), 2)
                self.assertEqual(len(channel.value_boxes), 2)
                self.assertEqual(len(channel.protection_checks), 2)
                self.assertEqual(len(channel.protection_indicators), 2)
                self.assertEqual(len(channel.protection_labels), 2)
                self.assertEqual(len(channel.power_labels), 2)
                self.assertEqual(len(channel.power_controls), 2)
                for label, box in zip(channel.value_labels, channel.value_boxes):
                    self.assertTrue(channel.panel.contains(label))
                    self.assertTrue(channel.panel.contains(box))
                    self.assertTrue(channel.panel_surface.contains(label))
                    self.assertTrue(channel.panel_surface.contains(box))
                    self.assertLessEqual(label.right(), box.left())
                    self.assertEqual(label.center().y(), box.center().y())
                self.assertLessEqual(
                    channel.mode_selector.bottom(), channel.value_boxes[0].top()
                )
                for index, (check, indicator, label_rect) in enumerate(
                    zip(
                        channel.protection_checks,
                        channel.protection_indicators,
                        channel.protection_labels,
                    )
                ):
                    self.assertTrue(channel.panel.contains(check))
                    self.assertTrue(channel.panel_surface.contains(check))
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
                # 保护勾选紧贴数值行，最下面是顺序/间隔一行。
                self.assertLessEqual(
                    channel.protection_checks[0].bottom(),
                    channel.power_labels[0].top(),
                )
                for label, control in zip(
                    channel.power_labels, channel.power_controls
                ):
                    self.assertTrue(channel.panel.contains(label))
                    self.assertTrue(channel.panel.contains(control))
                    self.assertTrue(channel.panel_surface.contains(label))
                    self.assertTrue(channel.panel_surface.contains(control))
                    self.assertLessEqual(label.right(), control.left())
                    self.assertAlmostEqual(label.center().y(), control.center().y())
                self.assertLessEqual(
                    channel.power_controls[0].bottom(),
                    channel.panel.bottom(),
                )

    def test_power_row_sits_below_the_protection_row(self):
        channel = power_card_layout().channels[0]

        self.assertAlmostEqual(
            channel.power_labels[0].top(),
            channel.protection_checks[0].bottom() + 6.0,
        )
        self.assertAlmostEqual(channel.power_controls[0].height(), 28.0)
        self.assertLess(
            channel.power_controls[1].left(), channel.panel.right()
        )
        self.assertGreater(
            channel.power_controls[1].left(), channel.power_controls[0].right()
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

    def test_mode_selectors_sit_at_the_outer_top_corners(self):
        """两个竖直滚筒分别贴住左右上角，顶部正中留给设备码铭牌。"""
        layout = power_card_layout()
        first, second = layout.channels

        self.assertEqual(
            first.mode_selector.center().y(), second.mode_selector.center().y()
        )
        self.assertAlmostEqual(
            first.mode_selector.left(), first.panel.left() + 12.0
        )
        self.assertAlmostEqual(
            second.mode_selector.right(), second.panel.right() - 12.0
        )
        self.assertAlmostEqual(first.mode_selector.width(), 84.0)
        self.assertAlmostEqual(first.mode_selector.height(), 48.0)
        self.assertAlmostEqual(first.mode_selector.top(), 6.0)
        self.assertAlmostEqual(first.mode_selector.bottom(), 54.0)
        self.assertLess(first.mode_selector.right(), layout.title.left())
        self.assertLess(layout.title.right(), second.mode_selector.left())
        self.assertGreater(
            first.mode_selector.center().y(),
            layout.title.center().y() - 4.0,
            "滚筒比铭牌更高，中心略低于铭牌中线",
        )
        self.assertAlmostEqual(
            layout.title.center().x(), layout.card.center().x(), msg="铭牌水平居中"
        )

    def test_card_has_no_channel_label_geometry(self):
        """``CH1`` / ``CH2`` 字样已从卡面删除：通道布局里不再有标题框。"""
        for channel in power_card_layout().channels:
            self.assertFalse(hasattr(channel, "label"))


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
        first_surface = layout.channels[0].panel_surface
        second_surface = layout.channels[1].panel_surface
        # 取色点放在色块外沿一侧、下半张：避开中央白缝的贴缝阴影与顶部渐变。
        open_left = first_surface.left() + 8.0
        open_right = second_surface.right() - 8.0
        sample_y = first_surface.bottom() - 6.0

        both_on = render_power_card(_data())
        self.assertEqual(
            _color_at(both_on, open_left, sample_y).name(),
            TOKENS["channel_cool"].lower(),
        )
        self.assertEqual(
            _color_at(both_on, open_right, sample_y).name(),
            TOKENS["channel_warm"].lower(),
        )
        self.assertEqual(
            _color_at(both_on, POWER_CARD_WIDTH / 2.0, sample_y).name(),
            TOKENS["surface"].lower(),
            "两块色块之间是卡片白底（白缝）",
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

    def test_protection_labels_draw_ink(self):
        """四个勾选框右侧都画「10%保护」，卡面不再有底部灰字。"""
        image = render_power_card(_data()).toImage()
        for channel in power_card_layout().channels:
            for label_rect in channel.protection_labels:
                with self.subTest(channel=channel.channel, rect=label_rect):
                    self.assertGreater(
                        _ink_count(image, label_rect.adjusted(2.0, 2.0, -2.0, -2.0)),
                        0,
                        "勾选框右侧应画出「10%保护」",
                    )
        self.assertFalse(
            hasattr(power_card_layout().channels[0], "hint"),
            "底部自动阈值提示行已删除",
        )

    def test_mode_selector_draws_a_dark_slot_and_red_accent(self):
        """深色槽体与左侧红色指示点来自参考实现；两种通道都保留。"""
        layout = power_card_layout()
        for channel in layout.channels:
            with self.subTest(channel=channel.channel):
                rect = channel.mode_selector
                # 取槽体右下角：避开当前档白字与左侧红点。
                body = _color_at(
                    render_power_card(_data()),
                    rect.right() - 5.0,
                    rect.bottom() - 5.0,
                )
                self.assertLess(
                    body.lightness(),
                    60,
                    "槽体应是深色玻璃，而不是通道浅底色",
                )
                dot = _color_at(
                    render_power_card(_data()),
                    rect.left() + 10.0,
                    rect.center().y(),
                )
                self.assertGreater(dot.red(), dot.green() + 40)
                self.assertGreater(dot.red(), dot.blue() + 40)

    def test_mode_selector_changes_with_the_channel_mode(self):
        """OFF / DUT / Driver 三档画出的滚筒内容不同。"""
        rect = power_card_layout().channels[0].mode_selector.toRect()
        images = []
        for mode in CHANNEL_MODES:
            data = _data().with_channel_mode("CH1", mode)
            image = render_power_card(data).toImage()
            images.append(image.copy(rect))
        for index in range(len(images)):
            for other in range(index + 1, len(images)):
                with self.subTest(first=CHANNEL_MODES[index], second=CHANNEL_MODES[other]):
                    self.assertNotEqual(
                        images[index],
                        images[other],
                        "不同档位的滚筒画面必须不同",
                    )

    def test_driver_label_is_dimmed_when_driver_mode_is_unavailable(self):
        """驱放测试模式关闭时 Driver 档被划掉；同一份数据只改可选状态。"""
        rect = power_card_layout().channels[0].mode_selector.toRect()
        available = render_power_card(_data(), driver_available=True).toImage()
        unavailable = render_power_card(_data(), driver_available=False).toImage()
        self.assertNotEqual(
            available.copy(rect),
            unavailable.copy(rect),
            "Driver 不可选时滚筒外观必须变化",
        )

    def test_empty_code_still_draws_a_placeholder(self):
        """没有设备码时铭牌里画占位横线，而不是留下一块空白牌。"""
        badge = power_card_layout().title
        inner = badge.adjusted(14.0, 12.0, -14.0, -12.0)
        placeholder = render_power_card(
            PowerSupplyCardData(code="", channels=_data().channels)
        ).toImage()
        named = render_power_card(_data()).toImage()

        placeholder_ink = _ink_count(placeholder, inner)
        self.assertGreater(placeholder_ink, 0)
        self.assertLess(
            placeholder_ink,
            _ink_count(named, inner),
            "占位横线不应画出完整设备码的墨量",
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
            selector = card.channel_mode_selector(channel.channel)
            self.assertIs(selector.parent(), card)
            self.assertEqual(
                (
                    selector.geometry().x(),
                    selector.geometry().y(),
                    selector.geometry().width(),
                    selector.geometry().height(),
                ),
                (
                    round(channel.mode_selector.x()),
                    round(channel.mode_selector.y()),
                    round(channel.mode_selector.width()),
                    round(channel.mode_selector.height()),
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

    def test_reel_animation_moves_continuously_before_settling(self):
        card = self.widget()
        self.assertEqual(card.channel_mode("CH1"), "driver")
        selector = card.channel_mode_selector("CH1")

        # Driver（第 2 档）→ OFF（第 0 档）：滚筒向上滚一档后取模回到 OFF。
        QTest.mouseClick(selector, Qt.LeftButton, pos=selector.rect().center())
        QTest.qWait(100)

        self.assertGreater(card._reel_positions["CH1"], 2.0)
        self.assertLess(card._reel_positions["CH1"], 3.0)

        QTest.qWait(900)
        self.assertEqual(card._reel_positions["CH1"], 0.0)

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

    def test_set_data_backfills_the_power_row_without_emitting(self):
        card = self.widget()
        events: list[tuple[str, int]] = []
        card.power_order_changed.connect(
            lambda channel, order: events.append((channel, order))
        )
        data = _data().with_channel_power("CH1", 2, 3, 4.0)

        card.set_data(data)

        self.assertEqual(card.channel_power_order("CH1"), 2)
        self.assertAlmostEqual(card.channel_power_delay("CH1"), 4.0)
        self.assertEqual(events, [])

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

    def test_keyboard_space_cycles_and_keeps_focus(self):
        card = self.widget()
        selector = card.channel_mode_selector("CH1")
        selector.setFocus()
        self.application.processEvents()

        QTest.keyClick(selector, Qt.Key_Space)

        self.assertEqual(card.channel_mode("CH1"), "off")
        self.assertTrue(selector.hasFocus())

    def test_keyboard_space_toggles_a_protection_check(self):
        card = self.widget()
        check = card.protection_check("CH2", "current")
        check.setFocus()
        self.application.processEvents()

        QTest.keyClick(check, Qt.Key_Space)

        self.assertFalse(card.channel_protection("CH2", "current"))

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

    def test_programmatic_mode_set_is_silent_unless_asked_to_emit(self):
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

        card.set_channel_mode("CH2", "off")
        card.set_protection_enabled("CH2", "voltage", True)
        self.assertEqual(mode_events, [])
        self.assertEqual(protection_events, [])
        self.assertFalse(card.channel_enabled("CH2"))
        self.assertTrue(card.channel_protection("CH2", "voltage"))

        card.set_channel_mode("CH2", "dut", emit=True)
        card.set_protection_enabled("CH2", "voltage", False, emit=True)
        self.assertEqual(mode_events, [("CH2", "dut")])
        self.assertEqual(protection_events, [("CH2", "voltage", False)])
        self.assertTrue(card.channel_enabled("CH2"))
        self.assertFalse(card.channel_protection("CH2", "voltage"))

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

    def test_tooltips_explain_units_ranges_and_the_role_boundary(self):
        card = self.widget()

        self.assertIn("VS 12.00 V", card.toolTip())
        self.assertIn("VS 电压设定", card.toolTip())
        self.assertIn("SN-1519", card.toolTip(), "身份详情放在卡面悬浮提示里")
        selector_tip = card.channel_mode_selector("CH1").toolTip()
        self.assertIn("Driver", selector_tip)
        self.assertIn("DUT", selector_tip)
        self.assertIn("上电计划", selector_tip)
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
            layout.channels[0].panel_surface.left() + 8.0,
            layout.channels[0].panel_surface.bottom() - 6.0,
        )
        sample_right = (
            layout.channels[1].panel_surface.right() - 8.0,
            layout.channels[1].panel_surface.bottom() - 6.0,
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
