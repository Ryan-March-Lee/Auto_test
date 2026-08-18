"""
连接示意图生成和显示模块
"""

import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.figure import Figure
from io import BytesIO
import base64

# 设置matplotlib支持中文显示
import matplotlib.font_manager as fm
plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial Unicode MS', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
# 清除字体缓存以确保设置生效
import matplotlib
matplotlib.font_manager._get_font.cache_clear()

class ConnectionDiagram:
    """连接示意图生成器"""
    
    @staticmethod
    def create_cable_loss_path1():
        """创建线损测量路径1的连接图"""
        fig, ax = plt.subplots(1, 1, figsize=(12, 4))
        
        # 设备位置
        devices = {
            '信号源': (1, 2),
            '线缆①': (2.5, 2),
            '衰减器': (4, 2),
            '线缆②': (5.5, 2),
            '频谱仪': (7, 2)
        }
        
        # 绘制设备
        for name, (x, y) in devices.items():
            if name in ['线缆①', '线缆②']:
                # 线缆用线条表示
                ax.plot([x-0.3, x+0.3], [y, y], 'k-', linewidth=5)
                ax.text(x, y-0.3, name, ha='center', va='top', fontsize=10)
            else:
                # 设备用方框表示
                rect = patches.Rectangle((x-0.3, y-0.2), 0.6, 0.4, 
                                       linewidth=2, edgecolor='blue', facecolor='lightblue')
                ax.add_patch(rect)
                ax.text(x, y, name, ha='center', va='center', fontsize=9, weight='bold')
        
        # 连接线
        connections = [(1.3, 2.2), (2.2, 2.2), (2.8, 2.2), (3.7, 2.2), 
                      (4.3, 2.2), (5.2, 2.2), (5.8, 2.2), (6.7, 2.2)]
        
        for i in range(0, len(connections), 2):
            x1, y1 = connections[i]
            x2, y2 = connections[i+1]
            ax.plot([x1, x2], [y1, y2], 'r-', linewidth=2)
            ax.plot(x2, y2, 'r>', markersize=8)
        
        ax.set_xlim(0.5, 7.5)
        ax.set_ylim(1, 3)
        ax.set_title('线损测量 - 路径1连接图\n信号源 → 线缆① → 衰减器 → 线缆② → 频谱仪', 
                    fontsize=12, weight='bold')
        ax.axis('off')
        
        plt.tight_layout()
        return fig
    
    @staticmethod
    def create_cable_loss_path2():
        """创建线损测量路径2的连接图"""
        fig, ax = plt.subplots(1, 1, figsize=(14, 4))
        
        # 设备位置
        devices = {
            '信号源': (1, 2),
            '线缆①': (2.2, 2),
            '线缆③': (3.4, 2),
            '线缆④': (4.6, 2),
            '衰减器': (5.8, 2),
            '线缆②': (7, 2),
            '频谱仪': (8.2, 2)
        }
        
        # 绘制设备
        for name, (x, y) in devices.items():
            if name in ['线缆①', '线缆②', '线缆③', '线缆④']:
                # 线缆用线条表示
                ax.plot([x-0.25, x+0.25], [y, y], 'k-', linewidth=5)
                ax.text(x, y-0.3, name, ha='center', va='top', fontsize=10)
            else:
                # 设备用方框表示
                rect = patches.Rectangle((x-0.25, y-0.2), 0.5, 0.4, 
                                       linewidth=2, edgecolor='blue', facecolor='lightblue')
                ax.add_patch(rect)
                ax.text(x, y, name, ha='center', va='center', fontsize=9, weight='bold')
        
        # 连接线
        positions = list(devices.values())
        for i in range(len(positions) - 1):
            x1, y1 = positions[i]
            x2, y2 = positions[i + 1]
            start_x = x1 + 0.25 if i < len(positions) - 2 else x1 + 0.25
            end_x = x2 - 0.25
            ax.plot([start_x, end_x], [y1 + 0.1, y2 + 0.1], 'r-', linewidth=2)
            ax.plot(end_x, y2 + 0.1, 'r>', markersize=8)
        
        ax.set_xlim(0.5, 8.7)
        ax.set_ylim(1, 3)
        ax.set_title('线损测量 - 路径2连接图\n信号源 → 线缆① → 线缆③ → 线缆④ → 衰减器 → 线缆② → 频谱仪', 
                    fontsize=12, weight='bold')
        ax.axis('off')
        
        plt.tight_layout()
        return fig
    
    @staticmethod
    def create_driver_mapping():
        """创建驱动功放映射连接图"""
        fig, ax = plt.subplots(1, 1, figsize=(14, 4))
        
        # 设备位置
        devices = {
            '信号源': (1, 2),
            '线缆①': (2.2, 2),
            '驱动功放': (3.8, 2),
            '线缆③': (5.4, 2),
            '衰减器': (6.6, 2),
            '线缆②': (7.8, 2),
            '频谱仪': (9, 2)
        }
        
        # 绘制设备
        for name, (x, y) in devices.items():
            if name in ['线缆①', '线缆②', '线缆③']:
                # 线缆用线条表示
                ax.plot([x-0.25, x+0.25], [y, y], 'k-', linewidth=5)
                ax.text(x, y-0.3, name, ha='center', va='top', fontsize=10)
            elif name == '驱动功放':
                # 驱动功放用特殊颜色
                rect = patches.Rectangle((x-0.4, y-0.25), 0.8, 0.5, 
                                       linewidth=2, edgecolor='red', facecolor='lightcoral')
                ax.add_patch(rect)
                ax.text(x, y, name, ha='center', va='center', fontsize=9, weight='bold')
            else:
                # 其他设备用方框表示
                rect = patches.Rectangle((x-0.3, y-0.2), 0.6, 0.4, 
                                       linewidth=2, edgecolor='blue', facecolor='lightblue')
                ax.add_patch(rect)
                ax.text(x, y, name, ha='center', va='center', fontsize=9, weight='bold')
        
        # 连接线
        positions = [(1.3, 2.1), (1.95, 2.1), (2.45, 2.1), (3.4, 2.1), (4.2, 2.1), (5.15, 2.1),
                    (5.65, 2.1), (6.3, 2.1), (6.9, 2.1), (7.5, 2.1), (8.05, 2.1), (8.7, 2.1)]
        
        for i in range(0, len(positions), 2):
            x1, y1 = positions[i]
            x2, y2 = positions[i+1]
            ax.plot([x1, x2], [y1, y2], 'r-', linewidth=2)
            ax.plot(x2, y2, 'r>', markersize=8)
        
        ax.set_xlim(0.5, 9.5)
        ax.set_ylim(1, 3)
        ax.set_title('驱动功放映射连接图\n信号源 → 线缆① → 驱动功放 → 线缆③ → 衰减器 → 线缆② → 频谱仪', 
                    fontsize=12, weight='bold')
        ax.axis('off')
        
        plt.tight_layout()
        return fig
    
    @staticmethod
    def create_amplifier_test():
        """创建主功放测试连接图"""
        fig, ax = plt.subplots(1, 1, figsize=(16, 5))
        
        # 设备位置 - 分两行显示
        devices = {
            '信号源': (1, 3),
            '线缆①': (2.2, 3),
            '驱动功放': (3.8, 3),
            '线缆③': (5.4, 3),
            '主功放': (7, 2),
            '线缆④': (8.6, 2),
            '衰减器': (10.2, 2),
            '线缆②': (11.4, 2),
            '频谱仪': (12.6, 2)
        }
        
        # 绘制设备
        for name, (x, y) in devices.items():
            if name in ['线缆①', '线缆②', '线缆③', '线缆④']:
                # 线缆用线条表示
                ax.plot([x-0.25, x+0.25], [y, y], 'k-', linewidth=5)
                ax.text(x, y-0.3, name, ha='center', va='top', fontsize=10)
            elif name == '驱动功放':
                # 驱动功放用特殊颜色
                rect = patches.Rectangle((x-0.4, y-0.25), 0.8, 0.5, 
                                       linewidth=2, edgecolor='red', facecolor='lightcoral')
                ax.add_patch(rect)
                ax.text(x, y, name, ha='center', va='center', fontsize=9, weight='bold')
            elif name == '主功放':
                # 主功放用特殊颜色和大小
                rect = patches.Rectangle((x-0.5, y-0.3), 1.0, 0.6, 
                                       linewidth=3, edgecolor='darkred', facecolor='orange')
                ax.add_patch(rect)
                ax.text(x, y, name, ha='center', va='center', fontsize=11, weight='bold')
            else:
                # 其他设备用方框表示
                rect = patches.Rectangle((x-0.3, y-0.2), 0.6, 0.4, 
                                       linewidth=2, edgecolor='blue', facecolor='lightblue')
                ax.add_patch(rect)
                ax.text(x, y, name, ha='center', va='center', fontsize=9, weight='bold')
        
        # 连接线
        # 水平连接
        connections = [
            (1.3, 3.1, 1.95, 3.1),  # 信号源到线缆①
            (2.45, 3.1, 3.4, 3.1),  # 线缆①到驱动功放
            (4.2, 3.1, 5.15, 3.1),  # 驱动功放到线缆③
            (8.1, 2.1, 8.1, 2.1),   # 线缆③到主功放（需要弯曲）
            (7.5, 2.1, 8.1, 2.1),   # 主功放到线缆④
            (9.1, 2.1, 9.7, 2.1),   # 线缆④到衰减器
            (10.5, 2.1, 11.1, 2.1), # 衰减器到线缆②
            (11.7, 2.1, 12.3, 2.1)  # 线缆②到频谱仪
        ]
        
        for x1, y1, x2, y2 in connections:
            ax.plot([x1, x2], [y1, y2], 'r-', linewidth=2)
            ax.plot(x2, y2, 'r>', markersize=8)
        
        # 垂直连接（线缆③到主功放）
        ax.plot([5.65, 5.65], [3.1, 2.1], 'r-', linewidth=2)
        ax.plot([5.65, 6.5], [2.1, 2.1], 'r-', linewidth=2)
        ax.plot(6.5, 2.1, 'r>', markersize=8)
        
        ax.set_xlim(0.5, 13.5)
        ax.set_ylim(1, 4)
        ax.set_title('主功放测试连接图\n信号源 → 线缆① → 驱动功放 → 线缆③ → 主功放 → 线缆④ → 衰减器 → 线缆② → 频谱仪', 
                    fontsize=12, weight='bold')
        ax.axis('off')
        
        plt.tight_layout()
        return fig
    
    @staticmethod
    def create_amplifier_test_no_driver():
        """创建无驱动模式的主功放测试连接图"""
        fig, ax = plt.subplots(1, 1, figsize=(14, 4))
        
        # 设备位置 - 无驱动功放的直接连接
        devices = {
            '信号源': (1, 2),
            '线缆①': (2.5, 2),
            '主功放': (4.5, 2),
            '线缆④': (6.5, 2),
            '衰减器': (8, 2),
            '线缆②': (9.5, 2),
            '频谱仪': (11, 2)
        }
        
        # 绘制设备
        for name, (x, y) in devices.items():
            if name in ['线缆①', '线缆②', '线缆④']:
                # 线缆用线条表示
                ax.plot([x-0.25, x+0.25], [y, y], 'k-', linewidth=5)
                ax.text(x, y-0.3, name, ha='center', va='top', fontsize=10)
            elif name == '主功放':
                # 主功放用特殊颜色和大小
                rect = patches.Rectangle((x-0.6, y-0.3), 1.2, 0.6, 
                                       linewidth=3, edgecolor='darkred', facecolor='orange')
                ax.add_patch(rect)
                ax.text(x, y, name, ha='center', va='center', fontsize=11, weight='bold')
            else:
                # 其他设备用方框表示
                rect = patches.Rectangle((x-0.3, y-0.2), 0.6, 0.4, 
                                       linewidth=2, edgecolor='blue', facecolor='lightblue')
                ax.add_patch(rect)
                ax.text(x, y, name, ha='center', va='center', fontsize=9, weight='bold')
        
        # 连接线
        connections = [
            (1.3, 2.1, 2.25, 2.1),   # 信号源到线缆①
            (2.75, 2.1, 3.9, 2.1),   # 线缆①到主功放
            (5.1, 2.1, 6.25, 2.1),   # 主功放到线缆④
            (6.75, 2.1, 7.7, 2.1),   # 线缆④到衰减器
            (8.3, 2.1, 9.25, 2.1),   # 衰减器到线缆②
            (9.75, 2.1, 10.7, 2.1)   # 线缆②到频谱仪
        ]
        
        for x1, y1, x2, y2 in connections:
            ax.plot([x1, x2], [y1, y2], 'r-', linewidth=2)
            ax.plot(x2, y2, 'r>', markersize=8)
        
        ax.set_xlim(0.5, 11.5)
        ax.set_ylim(1, 3)
        ax.set_title('主功放测试连接图（无驱动模式）\n信号源 → 线缆① → 主功放 → 线缆④ → 衰减器 → 线缆② → 频谱仪', 
                    fontsize=12, weight='bold')
        ax.axis('off')
        
        plt.tight_layout()
        return fig
    
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


def create_all_diagrams():
    """创建所有连接示意图并保存"""
    diagrams = {
        'cable_loss_path1': ConnectionDiagram.create_cable_loss_path1(),
        'cable_loss_path2': ConnectionDiagram.create_cable_loss_path2(),
        'driver_mapping': ConnectionDiagram.create_driver_mapping(),
        'amplifier_test': ConnectionDiagram.create_amplifier_test()
    }
    
    # 保存为图片文件
    for name, fig in diagrams.items():
        fig.savefig(f'{name}_diagram.png', dpi=150, bbox_inches='tight')
        plt.close(fig)
    
    print("所有连接示意图已保存完成！")


if __name__ == "__main__":
    create_all_diagrams()
