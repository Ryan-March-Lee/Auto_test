"""连接示意图的真实渲染回归。

真实调用 matplotlib 构建图形并保存 Base64，验证图元（文本、矩形、连线）与图片
输出。运行环境无法渲染时自动跳过（见 ``tests/_rendering.py``）。
"""

import base64
import unittest

from matplotlib.figure import Figure

from tests._rendering import rendering_available
from presentation.qt.connection_diagrams import DIAGRAM_DEVICES, ConnectionDiagram


@unittest.skipUnless(rendering_available(), "当前环境无法渲染 matplotlib 图像")
class ConnectionDiagramRenderingTests(unittest.TestCase):
    def test_each_diagram_renders_expected_artists(self):
        for name, expected_devices in DIAGRAM_DEVICES.items():
            with self.subTest(diagram=name):
                figure = getattr(ConnectionDiagram, f"create_{name}")()
                self.assertIsInstance(figure, Figure)
                (axis,) = figure.axes
                self.assertEqual({text.get_text() for text in axis.texts}, set(expected_devices))
                self.assertTrue(axis.patches, "设备方框未绘制")
                self.assertTrue(axis.lines, "连线未绘制")
                self.assertIn("→", axis.get_title())

    def test_save_figure_as_base64_renders_png(self):
        payload = ConnectionDiagram.save_figure_as_base64(
            ConnectionDiagram.create_amplifier_test()
        )
        self.assertTrue(payload.startswith("iVBOR"))
        self.assertTrue(base64.b64decode(payload).startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
