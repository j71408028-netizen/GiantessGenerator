"""符号解析自检：确认 _probe_native 的 dbghelp 符号表能给出可读的函数名。

只验证解析链路（列出模块基址 → describe 一个已知地址 / 一个野地址），
不触发任何异常，可随时安全运行。

用法：python _probe_symtest.py
"""
import ctypes
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import _probe_native as p

p._sym.init()
print("modules:", len(p._sym.modules))
for m in p._sym.modules[:8]:
    print(f"  {m[0]:#x} size={m[1]:#x} {m[2]}")
addr = ctypes.cast(ctypes.windll.kernel32.GetCurrentProcess, ctypes.c_void_p).value
print("describe(GetCurrentProcess):", p._sym.describe(addr))
print("describe(0x10000):", p._sym.describe(0x10000))
