"""VEH 处理器自检：装一次处理器后故意写地址 0x10，验证它能打印现场再放行。

输出写 _out/dpg_probe/_probe_veh_self.txt（第一行是开始标记，之后是处理器抓到的现场），
进程随后照常崩掉——"崩"是预期结果。

用法：python _probe_veh_selftest.py
"""
import ctypes, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _outdir
import _probe_veh as P

P.OUT = _outdir.out("_probe_veh_self.txt")
with open(P.OUT, "w", encoding="utf-8") as f:
    f.write("selftest started\n")

proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.POINTER(P.EXCEPTION_POINTERS))
cb = proto(P.handler)
_keep = [cb]
h = P.k32.AddVectoredExceptionHandler(1, cb)
print("[selftest] VEH:", h, flush=True)
# 故意触发访问违例：写地址 0
ctypes.memset(ctypes.c_void_p(0x10), 0, 4)
print("[selftest] survived?!", flush=True)
