"""连接示意图生成逻辑归属与内容/输出回归（不渲染图像）。

生成逻辑归属 presentation 层（GUI “连接说明”对话框）。用例直接断言模块公开的
设备布局与标题数据来固定图内容，并通过替换图形对象验证 Base64 输出。

当前环境的 matplotlib/numpy 在构建图元与保存图像时可能原生崩溃，因此本文件不做真实
渲染；真实渲染由 ``tests/test_connection_diagrams_rendering.py`` 在可用环境中验证。
"""

import base64
import unittest
from pathlib import Path
from unittest.mock import patch

from presentation.qt import connection_diagrams as diagrams
from presentation.qt.connection_diagrams import ConnectionDiagram


# 每张图从左到右的接线链；同时定义了设备的插入顺序。
EXPECTED_CHAINS = {
    "cable_loss_path1": ["信号源", "线缆①", "衰减器", "线缆②", "频谱仪"],
    "cable_loss_path2": ["信号源", "线缆①", "线缆③", "线缆④", "衰减器", "线缆②", "频谱仪"],
    "driver_mapping": ["信号源", "线缆①", "驱动功放", "线缆③", "衰减器", "线缆②", "频谱仪"],
    "amplifier_test": [
        "信号源", "线缆①", "驱动功放", "线缆③", "主功放", "线缆④", "衰减器",
        "线缆②", "频谱仪",
    ],
    "amplifier_test_no_driver": [
        "信号源", "线缆①", "主功放", "线缆④", "衰减器", "线缆②", "频谱仪",
    ],
}


class _FakeFigure:
    """记录 savefig 调用，替代真实 matplotlib Figure。"""

    def __init__(self, recorder, payload=b"\x89PNG\r\n\x1a\n"):
        self.recorder = recorder
        self.payload = payload

    def savefig(self, target, **_kwargs):
        self.recorder.append(target)
        if hasattr(target, "write"):
            target.write(self.payload)


class ConnectionDiagramContentTests(unittest.TestCase):
    def test_diagram_device_layout_matches_wiring_chain(self):
        self.assertEqual(
            set(diagrams.DIAGRAM_DEVICES), set(EXPECTED_CHAINS)
        )
        self.assertEqual(set(diagrams.DIAGRAM_TITLES), set(EXPECTED_CHAINS))
        for key, chain in EXPECTED_CHAINS.items():
            with self.subTest(diagram=key):
                # 设备顺序即接线顺序。
                self.assertEqual(list(diagrams.DIAGRAM_DEVICES[key]), chain)
                lines = diagrams.DIAGRAM_TITLES[key].split("\n")
                self.assertEqual(len(lines), 2)
                self.assertIn("连接图", lines[0])
                self.assertEqual(lines[1], " → ".join(chain))

    def test_diagram_device_positions_are_pinned(self):
        expected_positions = {
            "cable_loss_path1": {
                "信号源": (1, 2), "线缆①": (2.5, 2), "衰减器": (4, 2),
                "线缆②": (5.5, 2), "频谱仪": (7, 2),
            },
            "amplifier_test_no_driver": {
                "信号源": (1, 2), "线缆①": (2.5, 2), "主功放": (4.5, 2),
                "线缆④": (6.5, 2), "衰减器": (8, 2), "线缆②": (9.5, 2),
                "频谱仪": (11, 2),
            },
        }
        for key, positions in expected_positions.items():
            with self.subTest(diagram=key):
                self.assertEqual(diagrams.DIAGRAM_DEVICES[key], positions)

    def test_no_driver_diagram_omits_driver_amplifier(self):
        self.assertNotIn("驱动功放", diagrams.DIAGRAM_DEVICES["amplifier_test_no_driver"])
        self.assertIn("驱动功放", diagrams.DIAGRAM_DEVICES["amplifier_test"])


class ConnectionDiagramAssetTests(unittest.TestCase):
    """电缆与仪器照片是运行时资源：文件必须存在，电缆按“接头不变形”切片。"""

    def test_declared_pictures_exist(self):
        names = {path.name for path in diagrams.PICTURE_FILES}
        self.assertIn("cable.png", names)
        self.assertIn("signal_source.png", names)
        self.assertIn("spectrum_analyzer.png", names)
        for path in diagrams.PICTURE_FILES:
            with self.subTest(picture=path.name):
                self.assertTrue(path.is_file(), f"缺少连接图图片: {path}")

    def test_cable_pieces_stretch_only_the_middle(self):
        wide = [length for _part, length in diagrams._cable_pieces(3.0, 0.1)]
        self.assertEqual(len(wide), 3)
        # 两端接头等长；加长间距只加长中段，接头不跟着拉长。
        self.assertAlmostEqual(wide[0], wide[2], places=6)
        self.assertGreater(wide[1], wide[0] * 2)

    def test_short_span_scales_the_whole_cable(self):
        narrow = [length for _part, length in diagrams._cable_pieces(0.2, 0.1)]
        self.assertAlmostEqual(sum(narrow), 0.2, places=6)
        self.assertAlmostEqual(narrow[0], narrow[2], places=6)

    def test_module_draws_no_arrow_markers(self):
        source = (
            Path(__file__).resolve().parents[2]
            / "presentation" / "qt" / "connection_diagrams.py"
        ).read_text(encoding="utf-8-sig")
        for marker in ("'r>'", '"r>"', "'>'"):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, source)


class ConnectionDiagramStyleTests(unittest.TestCase):
    def test_configure_diagram_style_is_idempotent(self):
        import matplotlib.pyplot as plt

        diagrams.configure_diagram_style()
        first = list(plt.rcParams["font.sans-serif"])
        diagrams.configure_diagram_style()
        self.assertEqual(list(plt.rcParams["font.sans-serif"]), first)
        self.assertIn("Microsoft YaHei", first)

    def test_module_has_no_import_time_global_style_side_effects(self):
        source = (
            Path(__file__).resolve().parents[2]
            / "presentation" / "qt" / "connection_diagrams.py"
        ).read_text(encoding="utf-8-sig")
        self.assertNotIn("_get_font", source)
        for line in source.splitlines():
            if line.startswith("plt.rcParams") or line.startswith("matplotlib.font_manager"):
                self.fail(f"模块顶层仍修改全局样式: {line}")


class ConnectionDiagramOutputTests(unittest.TestCase):
    def test_save_figure_as_base64_returns_png_payload(self):
        recorded = []
        fake = _FakeFigure(recorded)
        with patch.object(diagrams.plt, "close", lambda *_args, **_kwargs: None):
            payload = ConnectionDiagram.save_figure_as_base64(fake)
        self.assertEqual(len(recorded), 1)
        self.assertTrue(payload.startswith("iVBOR"))
        self.assertTrue(base64.b64decode(payload).startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
