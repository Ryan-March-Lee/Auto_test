"""连接示意图的生成与输出。

生成逻辑归属 presentation 层：图形内容只服务于 GUI 的“连接说明”对话框，不参与
测量服务初始化、运行快照或硬件生命周期。模块只构建 matplotlib 图形，不读写结果文件。

图形内容有两条约定：

- **设备**：有实拍图的仪器（信号源、频谱仪）直接画 ``assets/instrument_pic/`` 里的照片，
  按透明通道裁掉留白后等比摆放；没有照片的设备（衰减器、驱动功放、主功放）画成主题
  圆角卡片，边框颜色取 :data:`presentation.qt.theme.TOKENS` 的语义色，和界面同一套配色。
- **线缆**：每个连接用 ``assets/cable.png`` 的电缆照片表示，不再画箭头。电缆两端接头
  保持比例，只拉伸中间线身（见 :func:`_cable_pieces`），所以间距变化不会把接头压扁；
  间距过短时整根电缆等比缩短。连续多根线缆分段相接；两行布局的拐角由一段横电缆和一段
  竖电缆拼成直角。

模块导入不修改全局 matplotlib 配置；中文字体样式由 :func:`configure_diagram_style`
在创建图形时显式应用。图片只在真正绘图时读取并缓存，导入模块不碰文件系统。
"""

from __future__ import annotations

import base64
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import matplotlib.image as mimage
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch

from .theme import TOKENS

_DIAGRAM_STYLE_CONFIGURED = False


def configure_diagram_style() -> None:
    """应用连接图所需的中文字体样式；幂等，可在创建图形前重复调用。"""
    global _DIAGRAM_STYLE_CONFIGURED
    if _DIAGRAM_STYLE_CONFIGURED:
        return
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial Unicode MS', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    _DIAGRAM_STYLE_CONFIGURED = True


# 每张连接图的设备布局与标题。绘图方法从这里取内容，回归测试也直接断言这些数据，
# 从而在不渲染图像的前提下固定设备组成与接线顺序。标题只写这是哪张图；接线链路由
# 图上的设备与电缆照片直接表达，不再重复成一行文字。线缆也是链上的一个节点，它的
# 坐标就是标注文字的位置；绘图时线缆画在相邻两台设备之间。
DIAGRAM_TITLES = {
    "cable_loss_path1": '线损测量 - 路径1连接图',
    "cable_loss_path2": '线损测量 - 路径2连接图',
    "driver_mapping": '驱动功放映射连接图',
    "amplifier_test": '主功放测试连接图',
    "amplifier_test_no_driver": '主功放测试连接图（无驱动模式）',
}

DIAGRAM_DEVICES = {
    "cable_loss_path1": {
        '信号源': (1, 2),
        '线缆①': (2.5, 2),
        '衰减器': (4, 2),
        '线缆②': (5.5, 2),
        '频谱仪': (7, 2),
    },
    "cable_loss_path2": {
        '信号源': (1, 2),
        '线缆①': (2.2, 2),
        '线缆③': (3.4, 2),
        '线缆④': (4.6, 2),
        '衰减器': (5.8, 2),
        '线缆②': (7, 2),
        '频谱仪': (8.2, 2),
    },
    "driver_mapping": {
        '信号源': (1, 2),
        '线缆①': (2.2, 2),
        '驱动功放': (3.8, 2),
        '线缆③': (5.4, 2),
        '衰减器': (6.6, 2),
        '线缆②': (7.8, 2),
        '频谱仪': (9, 2),
    },
    "amplifier_test": {
        '信号源': (1, 3),
        '线缆①': (2.2, 3),
        '驱动功放': (3.8, 3),
        '线缆③': (5.4, 3),
        '主功放': (7, 2),
        '线缆④': (8.6, 2),
        '衰减器': (10.2, 2),
        '线缆②': (11.4, 2),
        '频谱仪': (12.6, 2),
    },
    "amplifier_test_no_driver": {
        '信号源': (1, 2),
        '线缆①': (2.5, 2),
        '主功放': (4.5, 2),
        '线缆④': (6.5, 2),
        '衰减器': (8, 2),
        '线缆②': (9.5, 2),
        '频谱仪': (11, 2),
    },
}

#: 每张图的画布宽度与显示范围。范围只决定留白，不参与接线顺序；画布高度按显示范围的
#: 宽高比加标题空间推算，图不会被拉成一条细带、也不会留下大片空白。
DIAGRAM_VIEWS = {
    "cable_loss_path1": (12, (0.1, 7.9), (1.25, 2.75)),
    "cable_loss_path2": (14, (0.1, 9.1), (1.25, 2.75)),
    "driver_mapping": (14, (0.1, 9.9), (1.25, 2.75)),
    "amplifier_test": (16, (0.1, 13.5), (1.3, 3.7)),
    "amplifier_test_no_driver": (14, (0.1, 11.9), (1.25, 2.75)),
}
#: 单行标题（13 pt 加粗，含标题与坐标区之间的留白）占用的画布高度（英寸）。
_TITLE_HEIGHT_INCHES = 0.40

_ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets"
#: 连接图用到的图片资源：电缆与两台有实拍图的仪器。
CABLE_PICTURE_FILE = _ASSETS_DIR / "cable.png"
DEVICE_PICTURE_FILES = {
    "信号源": _ASSETS_DIR / "instrument_pic" / "signal_source.png",
    "频谱仪": _ASSETS_DIR / "instrument_pic" / "spectrum_analyzer.png",
}
#: 随界面交付的连接图图片（资源预算与发布清单按这份清单核对）。
PICTURE_FILES = (CABLE_PICTURE_FILE, *DEVICE_PICTURE_FILES.values())

_CABLE_NAMES = frozenset({"线缆①", "线缆②", "线缆③", "线缆④"})

#: 仪器照片的显示高度（数据坐标）；宽度按图片自身的宽高比换算，不拉伸照片。
DEVICE_PICTURE_HEIGHT = 0.66
#: 电缆照片的显示粗细（数据坐标）。
CABLE_THICKNESS = 0.10
#: 九宫格切片中两端接头占源图宽度的比例：这一段不横向拉伸。
_CABLE_CONNECTOR_FRACTION = 0.22

#: 没有照片的设备画成圆角卡片：边框与底色取界面语义色，三类设备一眼可分。
_CARD_STYLES = {
    "衰减器": (TOKENS["primary"], TOKENS["primary_soft"]),
    "驱动功放": (TOKENS["warning"], TOKENS["warning_soft"]),
    "主功放": (TOKENS["danger"], TOKENS["danger_soft"]),
}
_CARD_SIZES = {
    "衰减器": (1.10, 0.60),
    "驱动功放": (1.30, 0.68),
    "主功放": (1.50, 0.78),
}
_DEFAULT_CARD_SIZE = (1.15, 0.62)


@lru_cache(maxsize=None)
def _load_picture(path_text: str):
    """读取图片并裁掉透明留白；读不到时返回 ``None``，由调用方退回卡片画法。"""
    try:
        image = np.asarray(mimage.imread(path_text))
    except (OSError, ValueError):
        return None
    return _crop_to_content(_with_alpha(image))


def _with_alpha(image):
    """把灰度/RGB 图片补成 RGBA；预览图统一带 alpha，绘制时留白才透明。"""
    if image.ndim == 2:
        return np.dstack([image, image, image, np.ones_like(image)])
    if image.shape[2] == 3:
        return np.dstack([image, np.ones(image.shape[:2], dtype=image.dtype)])
    return image


def _crop_to_content(image):
    """按 alpha 通道裁到主体包围盒，让电缆/照片贴到设备可见边缘。"""
    alpha = image[..., 3]
    columns = np.flatnonzero(alpha.max(axis=0) > 0.02)
    rows = np.flatnonzero(alpha.max(axis=1) > 0.02)
    if columns.size == 0 or rows.size == 0:
        return image
    return image[rows[0]: rows[-1] + 1, columns[0]: columns[-1] + 1]


def _blit(axis, image, left, right, bottom, top, zorder):
    axis.imshow(
        image,
        extent=(left, right, bottom, top),
        aspect="auto",
        interpolation="bilinear",
        zorder=zorder,
    )


def _cable_pieces(span: float, thickness: float):
    """把一根电缆切成“接头 + 可拉伸线身 + 接头”三片。

    返回 ``[(图片切片, 该片长度), ...]``；间距比整根电缆的自然长度还短时三片等比
    缩短，接头形状仍然不变。电缆图片缺失时返回 ``None``。
    """
    image = _load_picture(str(CABLE_PICTURE_FILE))
    if image is None:
        return None
    height, width = image.shape[:2]
    connector = max(1, int(round(width * _CABLE_CONNECTOR_FRACTION)))
    natural = thickness * np.array([connector, width - 2 * connector, connector]) / height
    span = max(float(span), 1e-3)
    if span < natural.sum():
        lengths = natural * (span / natural.sum())
    else:
        lengths = np.array([natural[0], span - natural[0] - natural[2], natural[2]])
    slices = (
        image[:, :connector],
        image[:, connector:width - connector],
        image[:, width - connector:],
    )
    return list(zip(slices, lengths))


def _draw_horizontal_cable(axis, x0, x1, y, thickness=CABLE_THICKNESS):
    if x1 < x0:
        x0, x1 = x1, x0
    pieces = _cable_pieces(x1 - x0, thickness)
    if pieces is None:
        axis.plot(
            [x0, x1], [y, y],
            color=TOKENS["text"], linewidth=3, solid_capstyle="round", zorder=2,
        )
        return
    cursor = x0
    for part, length in pieces:
        _blit(axis, part, cursor, cursor + length, y - thickness / 2, y + thickness / 2, 2)
        cursor += length


def _draw_vertical_cable(axis, x, y0, y1, thickness=CABLE_THICKNESS):
    if y1 < y0:
        y0, y1 = y1, y0
    pieces = _cable_pieces(y1 - y0, thickness)
    if pieces is None:
        axis.plot(
            [x, x], [y0, y1],
            color=TOKENS["text"], linewidth=3, solid_capstyle="round", zorder=2,
        )
        return
    cursor = y0
    for part, length in pieces:
        # 竖段由同一张电缆照片旋转 90° 得到，接头形状不变。
        rotated = np.rot90(part)
        _blit(axis, rotated, x - thickness / 2, x + thickness / 2, cursor, cursor + length, 2)
        cursor += length


def _draw_device(axis, name, x, y):
    """画一台设备，返回它的显示尺寸 ``(宽, 高)``，供线缆计算端点。"""
    picture_path = DEVICE_PICTURE_FILES.get(name)
    image = _load_picture(str(picture_path)) if picture_path else None
    if image is not None:
        height = DEVICE_PICTURE_HEIGHT
        width = height * image.shape[1] / image.shape[0]
        _blit(axis, image, x - width / 2, x + width / 2, y - height / 2, y + height / 2, 4)
        axis.text(
            x, y - height / 2 - 0.08, name,
            ha="center", va="top", fontsize=10, weight="bold",
            color=TOKENS["text"], zorder=5,
        )
        return width, height

    width, height = _CARD_SIZES.get(name, _DEFAULT_CARD_SIZE)
    edge, face = _CARD_STYLES.get(
        name, (TOKENS["primary"], TOKENS["primary_soft"])
    )
    radius = min(width, height) * 0.18
    box_style = f"round,pad=0,rounding_size={radius:.3f}"
    shadow = FancyBboxPatch(
        (x - width / 2, y - height / 2 - 0.03), width, height,
        boxstyle=box_style, linewidth=0, facecolor=TOKENS["border"], zorder=3,
    )
    axis.add_patch(shadow)
    card = FancyBboxPatch(
        (x - width / 2, y - height / 2), width, height,
        boxstyle=box_style, linewidth=2.2, edgecolor=edge, facecolor=face, zorder=4,
    )
    axis.add_patch(card)
    axis.text(
        x, y, name,
        ha="center", va="center", fontsize=10.5, weight="bold",
        color=TOKENS["text"], zorder=5,
    )
    return width, height


def _cable_runs(devices):
    """按接线顺序把线缆分组：每段是“左设备 + 连续线缆 + 右设备”。"""
    names = list(devices)
    runs = []
    previous = None
    index = 0
    while index < len(names):
        if names[index] not in _CABLE_NAMES:
            previous = names[index]
            index += 1
            continue
        run = []
        while index < len(names) and names[index] in _CABLE_NAMES:
            run.append(names[index])
            index += 1
        if previous is not None and index < len(names):
            runs.append((previous, run, names[index]))
    return runs


def _segment_boundaries(start, end, run, devices):
    """连续线缆之间按标注位置的中点分段；单根线缆直接占满整段。"""
    if len(run) == 1:
        return [start, end]
    labels = [devices[name][0] for name in run]
    if end < start:
        labels = list(reversed(labels))
    internal = [(left + right) / 2 for left, right in zip(labels, labels[1:])]
    return [start, *internal, end]


def _clamped_label_x(name, x0, x1, devices):
    stored = devices[name][0]
    low, high = sorted((x0, x1))
    return min(max(stored, low), high)


def _draw_cable_label(axis, name, x0, x1, cable_y, devices):
    label_x = _clamped_label_x(name, x0, x1, devices)
    axis.text(
        label_x, cable_y - CABLE_THICKNESS / 2 - 0.05, name,
        ha="center", va="top", fontsize=9.5,
        color=TOKENS["text_secondary"], zorder=5,
    )


def _draw_cable_run(axis, left, run, right, metrics, devices):
    left_x, left_y, left_w, _ = metrics[left]
    right_x, right_y, right_w, right_h = metrics[right]
    if abs(left_y - right_y) < 0.01:
        if right_x >= left_x:
            start, end = left_x + left_w / 2, right_x - right_w / 2
        else:
            start, end = left_x - left_w / 2, right_x + right_w / 2
        boundaries = _segment_boundaries(start, end, run, devices)
        for index, name in enumerate(run):
            x0, x1 = boundaries[index], boundaries[index + 1]
            _draw_horizontal_cable(axis, x0, x1, left_y)
            _draw_cable_label(axis, name, x0, x1, left_y, devices)
        return

    # 两行布局：先沿出发设备的行水平走线，再在目标设备中线上垂直拐下去。
    target_x = right_x
    source_edge = left_x + left_w / 2 if target_x >= left_x else left_x - left_w / 2
    boundaries = _segment_boundaries(source_edge, target_x, run, devices)
    for index, name in enumerate(run):
        x0, x1 = boundaries[index], boundaries[index + 1]
        _draw_horizontal_cable(axis, x0, x1, left_y)
        _draw_cable_label(axis, name, x0, x1, left_y, devices)
    arrival_y = right_y + right_h / 2 if right_y < left_y else right_y - right_h / 2
    _draw_vertical_cable(axis, target_x, left_y, arrival_y)


def _render_diagram(key):
    """按 :data:`DIAGRAM_DEVICES` 画一张连接图。"""
    configure_diagram_style()
    width, x_limits, y_limits = DIAGRAM_VIEWS[key]
    x_span = x_limits[1] - x_limits[0]
    y_span = y_limits[1] - y_limits[0]
    height = width * y_span / x_span + _TITLE_HEIGHT_INCHES
    figure, axis = plt.subplots(1, 1, figsize=(width, height), layout="constrained")
    devices = DIAGRAM_DEVICES[key]
    metrics = {
        name: (x, y, *_draw_device(axis, name, x, y))
        for name, (x, y) in devices.items()
        if name not in _CABLE_NAMES
    }
    for left, run, right in _cable_runs(devices):
        _draw_cable_run(axis, left, run, right, metrics, devices)
    axis.set_xlim(*x_limits)
    axis.set_ylim(*y_limits)
    axis.set_aspect("equal")
    axis.set_title(DIAGRAM_TITLES[key], fontsize=13, weight='bold', color=TOKENS["text"], pad=16)
    axis.axis('off')

    return figure


class ConnectionDiagram:
    """连接示意图生成器"""

    @staticmethod
    def create_cable_loss_path1():
        """创建线损测量路径1的连接图"""
        return _render_diagram("cable_loss_path1")

    @staticmethod
    def create_cable_loss_path2():
        """创建线损测量路径2的连接图"""
        return _render_diagram("cable_loss_path2")

    @staticmethod
    def create_driver_mapping():
        """创建驱动功放映射连接图"""
        return _render_diagram("driver_mapping")

    @staticmethod
    def create_amplifier_test():
        """创建主功放测试连接图"""
        return _render_diagram("amplifier_test")

    @staticmethod
    def create_amplifier_test_no_driver():
        """创建无驱动模式的主功放测试连接图"""
        return _render_diagram("amplifier_test_no_driver")

    @staticmethod
    def save_figure_as_base64(fig):
        """将matplotlib图形保存为base64字符串"""
        buffer = BytesIO()
        fig.savefig(buffer, format='png', dpi=100, bbox_inches='tight')
        buffer.seek(0)
        img_base64 = base64.b64encode(buffer.read()).decode()
        buffer.close()
        plt.close(fig)
        return img_base64
