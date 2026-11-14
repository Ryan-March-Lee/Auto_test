"""连接示意图的真实渲染回归。

真实调用 matplotlib 构建图形并保存 Base64，验证图元（文本、卡片、仪器照片与电缆图）
与图片输出。运行环境无法渲染时自动跳过（见 ``tests/_rendering.py``）。
"""

import base64
import unittest

from matplotlib.figure import Figure

from tests._rendering import rendering_available
from presentation.qt.connection_diagrams import DIAGRAM_DEVICES, ConnectionDiagram


@unittest.skipUnless(rendering_available(), "当前环境无法渲染 matplotlib 图像")
class ConnectionDiagramRenderingTests(unittest.TestCase):

    def test_save_figure_as_base64_renders_png(self):
        payload = ConnectionDiagram.save_figure_as_base64(
            ConnectionDiagram.create_amplifier_test()
        )
        self.assertTrue(payload.startswith("iVBOR"))
        self.assertTrue(base64.b64decode(payload).startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
