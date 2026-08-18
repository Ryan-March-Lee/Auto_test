import os
import shutil
import tempfile
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from PIL import Image
import io

def get_desktop_path():
    """获取桌面路径"""
    home = Path.home()
    desktop = home / "Desktop"
    if not desktop.exists():
        desktop = home / "桌面"
    return desktop


def svg_to_png_chrome(svg_path, output_path=None, dpi=300, quality='high', save_to_desktop=True):
    """
    使用 Chrome 渲染 SVG 为透明背景 PNG
    
    参数:
        svg_path: SVG 文件路径
        output_path: 输出文件路径（可选）
        dpi: 分辨率，影响最终图片尺寸和清晰度
             - 96: 标准屏幕（默认）
             - 150: 一般打印
             - 300: 高质量打印（推荐）
             - 600: 超高清
             - 1200: 印刷级
        quality: 渲染质量预设
             - 'draft': 草稿（快速预览）
             - 'normal': 普通
             - 'high': 高质量（推荐）
             - 'ultra': 超高质量
        save_to_desktop: 是否保存到桌面
    """
    
    if not os.path.exists(svg_path):
        raise FileNotFoundError(f"❌ 找不到输入文件: {svg_path}")

    # 质量预设
    quality_presets = {
        'draft': {'dpi': 96, 'window': 2000, 'scale': 1},
        'normal': {'dpi': 150, 'window': 3000, 'scale': 1.5},
        'high': {'dpi': 300, 'window': 4000, 'scale': 3},
        'ultra': {'dpi': 600, 'window': 6000, 'scale': 6},
    }
    
    if quality in quality_presets:
        preset = quality_presets[quality]
        dpi = preset['dpi']
        window_size = preset['window']
        scale_factor = preset['scale']
    else:
        # 自定义 DPI，自动计算其他参数
        scale_factor = dpi / 96  # 96 是基准 DPI
        window_size = int(2000 * scale_factor)

    # 确定输出路径
    if save_to_desktop and output_path is None:
        desktop = get_desktop_path()
        output_filename = f"{Path(svg_path).stem}_transparent_{dpi}dpi.png"
        output_path = str(desktop / output_filename)
    elif output_path is None:
        output_path = f"{os.path.splitext(svg_path)[0]}_transparent_{dpi}dpi.png"

    print("=" * 60)
    print(f"🎨 SVG → 透明 PNG 转换（高质量渲染）")
    print("=" * 60)
    print(f"📄 输入: {svg_path}")
    print(f"💾 输出: {output_path}")
    print(f"🎯 质量: {quality if quality in quality_presets else 'custom'}")
    print(f"📐 分辨率: {dpi} DPI")
    print(f"🔍 缩放: {scale_factor}x")
    print(f"🖥️  窗口: {window_size}x{window_size}")
    print("=" * 60)

    # 读取 SVG
    with open(svg_path, 'r', encoding='utf-8') as f:
        svg_content = f.read()
    
    # HTML 包装（添加高质量渲染 CSS）
    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        * {{ 
            margin: 0; 
            padding: 0; 
        }}
        html, body {{ 
            background: transparent !important;
            width: 100%;
            height: 100%;
        }}
        svg {{
            /* 高质量渲染设置 */
            shape-rendering: geometricPrecision;
            text-rendering: geometricPrecision;
            image-rendering: -webkit-optimize-contrast;
            image-rendering: crisp-edges;
        }}
    </style>
</head>
<body>{svg_content}</body>
</html>"""
    
    # 临时文件
    tmpdir = tempfile.mkdtemp()
    temp_html = os.path.join(tmpdir, 'temp.html')
    
    try:
        with open(temp_html, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        # Chrome 高质量渲染设置
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument(f"--window-size={window_size},{window_size}")
        chrome_options.add_argument("--force-device-scale-factor=1")
        chrome_options.add_argument("--force-color-profile=srgb")
        chrome_options.add_argument("--disable-software-rasterizer")
        
        # 高 DPI 设置
        chrome_options.add_argument(f"--force-device-scale-factor={scale_factor}")
        
        print("🧩 启动 Chrome 渲染引擎...")
        driver = webdriver.Chrome(options=chrome_options)
        driver.get(f"file:///{os.path.abspath(temp_html)}")
        
        # 等待渲染完成
        driver.implicitly_wait(2)
        
        print("📸 截取高质量图像...")
        # 截图
        svg_element = driver.find_element("tag name", "svg")
        png_bytes = svg_element.screenshot_as_png
        driver.quit()
        
        print("🎨 处理透明背景...")
        # PIL 处理
        img = Image.open(io.BytesIO(png_bytes))
        
        # 显示原始尺寸
        print(f"📏 渲染尺寸: {img.size[0]}x{img.size[1]} 像素")
        
        if img.mode == 'RGB':
            img = img.convert('RGBA')
            datas = img.getdata()
            newData = []
            for item in datas:
                # 白色转透明
                if item[0] > 250 and item[1] > 250 and item[2] > 250:
                    newData.append((255, 255, 255, 0))
                else:
                    newData.append(item)
            img.putdata(newData)
        
        # 保存为高质量 PNG
        img.save(output_path, 'PNG', optimize=False, compress_level=1)
        
        # 文件大小
        file_size = os.path.getsize(output_path) / (1024 * 1024)  # MB
        
        print("=" * 60)
        print(f"✅ 转换完成!")
        print(f"📦 文件大小: {file_size:.2f} MB")
        print(f"💾 保存位置: {output_path}")
        print("=" * 60)
        
        return output_path
        
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("=" * 60)
        print("🎨 SVG → 透明 PNG 高质量转换工具")
        print("=" * 60)
        print("\n用法:")
        print("  python svg_to_png_hq.py <svg文件> [质量预设] [自定义DPI]")
        print("\n质量预设:")
        print("  draft  - 草稿 (96 DPI, 快速预览)")
        print("  normal - 普通 (150 DPI, 日常使用)")
        print("  high   - 高质量 (300 DPI, 推荐, 默认)")
        print("  ultra  - 超高清 (600 DPI, 印刷级)")
        print("\n示例:")
        print("  python svg_to_png_hq.py 元素周期表.svg")
        print("  python svg_to_png_hq.py 元素周期表.svg high")
        print("  python svg_to_png_hq.py 元素周期表.svg ultra")
        print("  python svg_to_png_hq.py 元素周期表.svg custom 1200")
        print("\n💡 输出文件将自动保存到桌面")
        print("=" * 60)
    else:
        svg_file = sys.argv[1]
        
        # 解析质量参数
        if len(sys.argv) >= 3:
            quality_arg = sys.argv[2]
            if quality_arg == 'custom' and len(sys.argv) >= 4:
                # 自定义 DPI
                dpi = int(sys.argv[3])
                svg_to_png_chrome(svg_file, dpi=dpi, quality='custom')
            else:
                # 预设质量
                svg_to_png_chrome(svg_file, quality=quality_arg)
        else:
            # 默认高质量
            svg_to_png_chrome(svg_file, quality='high')