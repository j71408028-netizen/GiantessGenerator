"""在原生崩溃处理器保护下运行一个脚本（模拟命令行）。

用法：python _probe_crash_run.py <脚本路径> [参数...]
"""
import os
import runpy
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _probe_native  # noqa: E402

_probe_native.install()

script = sys.argv[1]
sys.argv = [script] + sys.argv[2:]
sys.path.insert(0, os.path.dirname(os.path.abspath(script)))
runpy.run_path(script, run_name="__main__")
