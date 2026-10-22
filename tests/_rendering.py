"""matplotlib 真实渲染可用性探测（测试辅助，非测试模块）。

当前开发环境的 matplotlib/numpy 组合在渲染图像、构建图元（Rectangle/beziers）或调用
线性代数时可能发生原生崩溃（进程级异常），无法用 try/except 捕获。这里改用子进程探测：
探测失败只返回 False，供渲染用例 ``skipUnless``，不会拖垮测试进程。
"""

from __future__ import annotations

import functools
import os
import subprocess
import sys
import tempfile
import textwrap

_PROBE = textwrap.dedent(
    """
    import sys
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(2, 2))
    ax.plot([0, 1], [0, 1], "o-")
    ax.text(0.5, 0.5, "probe")
    fig.tight_layout()
    fig.savefig(sys.argv[1], dpi=50)
    """
)


@functools.lru_cache(maxsize=1)
def rendering_available() -> bool:
    """子进程探测 matplotlib 是否能真实渲染并保存图像。"""
    with tempfile.TemporaryDirectory() as directory:
        target = os.path.join(directory, "probe.png")
        try:
            completed = subprocess.run(
                [sys.executable, "-c", _PROBE, target],
                capture_output=True,
                timeout=180,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        if completed.returncode != 0:
            return False
        return os.path.exists(target) and os.path.getsize(target) > 0
