"""最小访问违例样本：故意读地址 1，用来验收原生崩溃处理器确实抓得到 AV。

刻意不做任何防护——它崩是预期结果。配合使用：
    python _probe_crash_run.py _probe_av.py     # 应报 0xC0000005 + 符号化地址
    python _probe_plain_run.py _probe_av.py     # 对照组：无处理器，只有裸退出码
"""
import ctypes
import time

print("before-av", flush=True)
time.sleep(0.2)
ctypes.string_at(1)
print("unreachable", flush=True)
