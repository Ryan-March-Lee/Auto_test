"""
VSCode专用启动包装器
解决conda run导致的输出缓冲问题
"""

# 强制设置输出为立即模式
import os
import sys

# 在最开始就设置环境变量
os.environ['PYTHONUNBUFFERED'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'

# 重新配置输出流
class ImmediateOutput:
    def __init__(self, stream):
        self.stream = stream
    def write(self, text):
        self.stream.write(text)
        self.stream.flush()
        return len(text)
    def __getattr__(self, name):
        return getattr(self.stream, name)

sys.stdout = ImmediateOutput(sys.stdout)
sys.stderr = ImmediateOutput(sys.stderr)

import subprocess
from pathlib import Path

def main():
    """VSCode专用启动器 - 静默启动GUI"""
    os.chdir(Path(__file__).resolve().parent)
    
    # 添加静默模式参数
    if '--silent' not in sys.argv and '--gui' not in sys.argv:
        sys.argv.append('--silent')
    
    # Windows编码设置
    if sys.platform.startswith('win'):
        try:
            subprocess.run('chcp 65001', shell=True, capture_output=True)
        except:
            pass
    
    try:
        # 直接导入并运行launcher模块，使用静默模式
        import launcher
        return launcher.main()
        
    except Exception as e:
        print(f"启动失败: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
