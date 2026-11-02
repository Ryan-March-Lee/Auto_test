"""仓库大资源预算：界面运行时图片的入库规则。

规则与理由见 ``docs/静态资源版本管理.md``。这里只冻结可观察结果：哪些文件随仓库交付、
单文件与目录的体积上限、图片能否被 Qt 读出并保留透明通道。图片压缩的具体编码参数不绑定。
"""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from PySide6.QtGui import QImage, QImageReader

from presentation.qt.device_cards import instrument_picture_path
from presentation.qt.theme import WELCOME_BACKGROUND_FILE
from presentation.qt.ui_loader import UI_DIRECTORY
from release_manifest import RELEASE_FILES, build_manifest


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
INSTRUMENT_PICTURES = ASSETS / "instrument_pic"

#: 设备卡片的三个角色到图片文件的固定映射；新增设备图时同时更新这里与压缩规则。
ROLE_PICTURES = {
    "signal_generator": "signal_source.png",
    "spectrum_analyzer": "spectrum_analyzer.png",
    "power_supply": "DC_source.png",
}

KB = 1024
MB = 1024 * 1024


def _files(directory: Path) -> list[Path]:
    """目录下的普通文件；跳过解释器缓存，缓存不是入库资源。"""
    return sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and "__pycache__" not in path.relative_to(directory).parts
    )


class RuntimePictureBudgetTests(unittest.TestCase):
    """界面运行必需的图片：体积、尺寸、透明通道与引用一致。"""

    def test_assets_directory_stays_within_budget(self):
        files = _files(ASSETS)
        total = sum(path.stat().st_size for path in files)
        self.assertLessEqual(total, 2 * MB, f"assets/ 总量 {total} 字节超预算")
        for path in files:
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertLessEqual(
                    path.stat().st_size, 512 * KB, "单个运行时资源不得超过 512 KB"
                )

    def test_instrument_pictures_are_scaled_for_the_card(self):
        for name in ROLE_PICTURES.values():
            path = INSTRUMENT_PICTURES / name
            with self.subTest(picture=name):
                self.assertTrue(path.is_file(), f"缺少运行时图片: {name}")
                image = QImage(str(path))
                self.assertFalse(image.isNull(), f"QImage 无法读取: {name}")
                self.assertEqual(QImageReader(str(path)).format(), b"png")
                self.assertLessEqual(max(image.width(), image.height()), 800)
                self.assertGreater(min(image.width(), image.height()), 0)

    def test_instrument_pictures_keep_their_translucent_padding(self):
        for name in ROLE_PICTURES.values():
            path = INSTRUMENT_PICTURES / name
            with self.subTest(picture=name):
                image = QImage(str(path))
                self.assertTrue(image.hasAlphaChannel(), f"{name} 丢失透明通道")
                self.assertEqual(
                    image.pixelColor(0, 0).alpha(), 0, f"{name} 的透明留白被填实"
                )

    def test_welcome_background_is_a_photo_without_alpha(self):
        path = WELCOME_BACKGROUND_FILE
        self.assertTrue(path.is_file(), f"欢迎页背景不存在: {path}")
        self.assertEqual(path.suffix.lower(), ".jpg")
        self.assertLessEqual(path.stat().st_size, 512 * KB)
        image = QImage(str(path))
        self.assertFalse(image.isNull())
        self.assertEqual(QImageReader(str(path)).format(), b"jpeg")
        self.assertFalse(image.hasAlphaChannel(), "不透明背景不应带 alpha 通道")
        self.assertLessEqual(image.width(), 1920)
        self.assertLessEqual(image.height(), 1080)

    def test_instrument_picture_directory_has_no_unreferenced_large_files(self):
        expected = set(ROLE_PICTURES.values())
        actual = {path.name for path in _files(INSTRUMENT_PICTURES)}
        self.assertEqual(actual, expected, "instrument_pic/ 只能保留被卡片引用的图片")

    def test_production_references_resolve_to_real_files(self):
        self.assertTrue(WELCOME_BACKGROUND_FILE.is_file())
        for role, name in ROLE_PICTURES.items():
            with self.subTest(role=role):
                resolved = instrument_picture_path(role)
                self.assertIsNotNone(resolved, f"角色 {role} 解析不到图片")
                self.assertEqual(resolved.name, name)

    def test_release_manifest_registers_the_runtime_assets(self):
        """发布清单把界面资源登记进发布边界；清单只核对路径存在，不承担打包。"""
        self.assertIn("assets", RELEASE_FILES)
        manifest = build_manifest(ROOT)
        self.assertIn("assets", manifest["release_files"])
        runtime_files = [WELCOME_BACKGROUND_FILE] + [
            INSTRUMENT_PICTURES / name for name in ROLE_PICTURES.values()
        ]
        for path in runtime_files:
            relative = path.relative_to(ROOT).as_posix()
            with self.subTest(asset=relative):
                self.assertTrue(
                    any(
                        relative == entry or relative.startswith(f"{entry}/")
                        for entry in manifest["release_files"]
                    ),
                    f"{relative} 不在发布清单登记的边界内",
                )


class DesignerUiResourceTests(unittest.TestCase):
    """Qt Designer 静态布局是运行时资源：随发布边界交付，且必须能被 XML 解析。"""

    def test_designer_files_ship_with_the_release_boundaries(self):
        files = sorted(UI_DIRECTORY.rglob("*.ui"))
        self.assertTrue(files, "至少应有一个 Qt Designer 静态布局文件")
        manifest = build_manifest(ROOT)
        for path in files:
            relative = path.relative_to(ROOT).as_posix()
            with self.subTest(ui=relative):
                self.assertLessEqual(path.stat().st_size, 256 * KB, "单个 .ui 不应是资源包")
                self.assertEqual(ElementTree.parse(path).getroot().tag, "ui")
                self.assertTrue(
                    any(
                        relative == entry or relative.startswith(f"{entry}/")
                        for entry in manifest["release_files"]
                    ),
                    f"{relative} 不在发布清单登记的边界内",
                )


if __name__ == "__main__":
    unittest.main()
