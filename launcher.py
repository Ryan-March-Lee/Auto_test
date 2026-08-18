"""
PA自动测试系统启动器
自动检测环境并选择合适的GUI版本
"""

import sys
import os
import subprocess
from pathlib import Path
import json

def print_header():
    """打印标题"""
    print("=" * 60)
    print("   PA自动测试系统 - 智能启动器")
    print("   Power Amplifier Auto Test System")  
    print("=" * 60)

def check_environment(silent=False):
    """检查运行环境"""
    if not silent:
        print("🔍 正在检查运行环境...")
    
    # 检查Python版本
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if not silent:
        print(f"   Python版本: {python_version}")
    
    if sys.version_info < (3, 7):
        if not silent:
            print("   ❌ 需要Python 3.7或更高版本")
        return False
    
    # 检查conda环境
    conda_env = os.environ.get('CONDA_DEFAULT_ENV', 'base')
    if not silent:
        print(f"   Conda环境: {conda_env}")
    
    return True

def check_packages(silent=False):
    """检查必需的包"""
    if not silent:
        print("🔍 正在检查依赖包...")
    
    packages = {
        'PySide6': {'required': True, 'installed': False, 'version': ''},
        'matplotlib': {'required': True, 'installed': False, 'version': ''},
        'numpy': {'required': True, 'installed': False, 'version': ''},
        'pandas': {'required': True, 'installed': False, 'version': ''},
        'pyvisa': {'required': False, 'installed': False, 'version': ''},
        'seaborn': {'required': False, 'installed': False, 'version': ''}
    }
    
    for package_name in packages.keys():
        try:
            if package_name == 'PySide6':
                import PySide6
                packages[package_name]['installed'] = True
                packages[package_name]['version'] = PySide6.__version__
            elif package_name == 'matplotlib':
                import matplotlib
                packages[package_name]['installed'] = True  
                packages[package_name]['version'] = matplotlib.__version__
            elif package_name == 'numpy':
                import numpy
                packages[package_name]['installed'] = True
                packages[package_name]['version'] = numpy.__version__
            elif package_name == 'pandas':
                import pandas
                packages[package_name]['installed'] = True
                packages[package_name]['version'] = pandas.__version__
            elif package_name == 'pyvisa':
                import pyvisa
                packages[package_name]['installed'] = True
                packages[package_name]['version'] = pyvisa.__version__
            elif package_name == 'seaborn':
                import seaborn
                packages[package_name]['installed'] = True
                packages[package_name]['version'] = seaborn.__version__
        except ImportError:
            pass
    
    # 显示结果
    if not silent:
        for name, info in packages.items():
            status = "✓" if info['installed'] else "✗"
            version = f" ({info['version']})" if info['version'] else ""
            required = " [必需]" if info['required'] else " [可选]"
            print(f"   {status} {name}{version}{required}")
    
    return packages

def get_gui_version(packages):
    """根据可用包选择GUI版本"""
    if packages['PySide6']['installed']:
        return 'enhanced'  # 只使用完整版
    else:
        return 'none'      # 无法启动

def launch_gui_version(version, packages, silent=False):
    """启动指定版本的GUI"""
    if version == 'none':
        if not silent:
            print("❌ 无法启动GUI - 缺少必需的依赖包")
        return False
        
    if not silent:
        print(f"🚀 正在启动 {version} 版本的GUI...")
    
    if version == 'enhanced':
        try:
            # 启动增强版GUI
            if Path("enhanced_main_gui.py").exists():
                if not silent:
                    print("   使用文件: enhanced_main_gui.py")
                import enhanced_main_gui
                enhanced_main_gui.main()
                return True
            else:
                if not silent:
                    print("   ❌ enhanced_main_gui.py 文件不存在")
                return False
        except Exception as e:
            if not silent:
                print(f"   ❌ GUI启动失败: {e}")
                import traceback
                print("   详细错误信息:")
                traceback.print_exc()
            return False
    
    return False

def show_installation_guide(packages, silent=False):
    """显示安装指南"""
    if silent:
        return
        
    print("\n" + "=" * 60)
    print("📦 依赖包安装指南")
    print("=" * 60)
    
    missing_required = [name for name, info in packages.items() 
                       if info['required'] and not info['installed']]
    
    if missing_required:
        print("⚠️  缺少必需的依赖包:")
        for pkg in missing_required:
            print(f"   • {pkg}")
        
        print("\n🔧 安装命令:")
        print(f"   pip install {' '.join(missing_required)}")
        print("\n   或使用conda:")
        print(f"   conda install {' '.join(missing_required)}")
        
    print("\n🎯 推荐完整安装:")
    print("   pip install PySide6 matplotlib numpy pandas seaborn pyvisa")
    
    print("\n🐍 或使用预配置的conda环境:")
    print("   conda activate VISA_demo")
    print("   python launcher.py")

def main():
    """主函数"""
    try:
        # 检查是否为静默模式（GUI模式）
        silent_mode = '--silent' in sys.argv or '--gui' in sys.argv
        
        if not silent_mode:
            # 打印标题
            print_header()
        
        # 处理命令行参数
        if len(sys.argv) > 1:
            if sys.argv[1] in ['--help', '-h']:
                show_help()
                return
            elif sys.argv[1] in ['--check', '-c']:
                check_environment()
                packages = check_packages()
                show_installation_guide(packages, False)
                return
        
        # 检查环境
        if not check_environment(silent_mode):
            if not silent_mode:
                print("❌ 环境检查失败")
            return
        
        # 检查包依赖
        packages = check_packages(silent_mode)
        
        # 选择GUI版本
        version = get_gui_version(packages)
        if not silent_mode:
            print(f"\n🎯 自动选择GUI版本: {version}")
            
            version_descriptions = {
                'enhanced': '完整版 (PySide6 + matplotlib + 科学计算包)',
                'none': '缺少依赖，无法启动'
            }
            print(f"   {version_descriptions.get(version, '未知版本')}")
        
        # 启动GUI
        success = launch_gui_version(version, packages, silent_mode)
        
        if not success:
            if not silent_mode:
                print("\n❌ GUI启动失败")
                show_installation_guide(packages, silent_mode)
        
    except KeyboardInterrupt:
        if not silent_mode:
            print("\n\n👋 用户中断程序")
    except Exception as e:
        if not silent_mode:
            print(f"\n❌ 程序异常: {e}")
            import traceback
            traceback.print_exc()
    
    if not silent_mode:
        print("\n按回车键退出...")
        try:
            input()
        except:
            pass

def show_help():
    """显示帮助信息"""
    help_text = """
🔧 PA自动测试系统启动器 - 帮助信息

📋 使用方法:
    python launcher.py          # 自动选择并启动GUI
    python launcher.py -h       # 显示此帮助信息  
    python launcher.py -c       # 仅检查环境和依赖

🎮 GUI版本说明:
    enhanced    完整版 - PySide6 + matplotlib + 科学计算包
                提供完整功能：实时可视化、连接图表、数据导出
                要求安装所有依赖包，启动失败将显示详细错误

🛠️ 环境要求:
    • Python 3.7+
    • 推荐使用conda环境：VISA_demo  
    
📦 依赖安装:
    # 完整安装（推荐）
    pip install PySide6 matplotlib numpy pandas seaborn pyvisa
    
    # 最小安装
    pip install PySide6
    
    # 使用conda
    conda install pyside6 matplotlib numpy pandas seaborn pyvisa

🚀 快速启动:
    # 如果已有VISA_demo环境
    conda activate VISA_demo
    python launcher.py
    
    # 或者使用批处理文件
    start_gui.bat

📞 技术支持:
    确保config.json文件存在并配置正确
    仪器地址格式：TCPIP::IP地址::INSTR
    测试频率格式：[4.0, 4.4, 4.8, 5.2, 5.6, 5.8]
    
    出现问题时运行: python launcher.py -c
    查看详细环境和依赖状态
"""
    print(help_text)

if __name__ == "__main__":
    main()
