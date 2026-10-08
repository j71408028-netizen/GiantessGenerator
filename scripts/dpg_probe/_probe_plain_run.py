"""对照：与 _probe_crash_run 相同的 runpy 装载方式，但不挂原生处理器。"""
import os
import runpy
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

script = sys.argv[1]
sys.argv = [script] + sys.argv[2:]
sys.path.insert(0, os.path.dirname(os.path.abspath(script)))
runpy.run_path(script, run_name="__main__")
