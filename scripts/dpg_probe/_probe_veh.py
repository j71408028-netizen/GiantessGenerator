# -*- coding: utf-8 -*-
"""VEH 探针：在崩溃发生的瞬间抓原生信息（模块 / 地址 / 指令 / 线程表）。

用法：
    python _probe_veh.py [game_id] [--target N]

在所有线程上安装 Vectored Exception Handler（第一优先），命中访问违例时
把发生地址解析到模块+偏移、抓取线程表和当前 RIP，写 _out/dpg_probe/_probe_veh.txt，
然后返回 CONTINUE_SEARCH 让进程照常崩。
"""

import ctypes
import os
import sys
import traceback
from ctypes import wintypes

import _outdir  # 同目录探针共享的产物目录（_out/dpg_probe/）

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = _outdir.out("_probe_veh.txt")

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)

ULONG_PTR = ctypes.c_uint64

STATUS_CODES = {
    0xC0000005: "ACCESS_VIOLATION",
    0xC000001D: "ILLEGAL_INSTRUCTION",
    0xC0000094: "INT_DIVIDE_BY_ZERO",
    0xC0000096: "PRIV_INSTRUCTION",
    0xC00000FD: "STACK_OVERFLOW",
    0xC0000374: "HEAP_CORRUPTION",
    0xC0000409: "STACK_BUFFER_OVERRUN",
    0xC0000602: "FAIL_FAST_EXCEPTION",
    0x80000003: "BREAKPOINT",
}


class EXCEPTION_RECORD(ctypes.Structure):
    pass


EXCEPTION_RECORD._fields_ = [
    ("ExceptionCode", wintypes.DWORD),
    ("ExceptionFlags", wintypes.DWORD),
    ("ExceptionRecord", ctypes.POINTER(EXCEPTION_RECORD)),
    ("ExceptionAddress", ctypes.c_void_p),
    ("NumberParameters", wintypes.DWORD),
    ("ExceptionInformation", ULONG_PTR * 15),
]


class EXCEPTION_POINTERS(ctypes.Structure):
    _fields_ = [("ExceptionRecord", ctypes.POINTER(EXCEPTION_RECORD)),
                ("ContextRecord", ctypes.c_void_p)]


class MODULEINFO(ctypes.Structure):
    _fields_ = [("lpBaseOfDll", ctypes.c_void_p),
                ("SizeOfImage", wintypes.DWORD),
                ("EntryPoint", ctypes.c_void_p)]


class THREADENTRY32(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ThreadID", wintypes.DWORD),
                ("th32OwnerProcessID", wintypes.DWORD),
                ("tpBasePri", ctypes.c_long),
                ("tpDeltaPri", ctypes.c_long),
                ("dwFlags", wintypes.DWORD)]


# x64 CONTEXT 的前半段（到 Rip 为止；Rip 在 0xF8）
class CONTEXT64(ctypes.Structure):
    _fields_ = [
        ("P1Home", ctypes.c_uint64), ("P2Home", ctypes.c_uint64),
        ("P3Home", ctypes.c_uint64), ("P4Home", ctypes.c_uint64),
        ("P5Home", ctypes.c_uint64), ("P6Home", ctypes.c_uint64),
        ("ContextFlags", wintypes.DWORD), ("MxCsr", wintypes.DWORD),
        ("SegCs", wintypes.WORD), ("SegDs", wintypes.WORD),
        ("SegEs", wintypes.WORD), ("SegFs", wintypes.WORD),
        ("SegGs", wintypes.WORD), ("SegSs", wintypes.WORD),
        ("EFlags", wintypes.DWORD),
        ("Dr0", ctypes.c_uint64), ("Dr1", ctypes.c_uint64),
        ("Dr2", ctypes.c_uint64), ("Dr3", ctypes.c_uint64),
        ("Dr6", ctypes.c_uint64), ("Dr7", ctypes.c_uint64),
        ("Rax", ctypes.c_uint64), ("Rcx", ctypes.c_uint64),
        ("Rdx", ctypes.c_uint64), ("Rbx", ctypes.c_uint64),
        ("Rsp", ctypes.c_uint64), ("Rbp", ctypes.c_uint64),
        ("Rsi", ctypes.c_uint64), ("Rdi", ctypes.c_uint64),
        ("R8", ctypes.c_uint64), ("R9", ctypes.c_uint64),
        ("R10", ctypes.c_uint64), ("R11", ctypes.c_uint64),
        ("R12", ctypes.c_uint64), ("R13", ctypes.c_uint64),
        ("R14", ctypes.c_uint64), ("R15", ctypes.c_uint64),
        ("Rip", ctypes.c_uint64),
    ]


assert ctypes.sizeof(CONTEXT64) == 256, ctypes.sizeof(CONTEXT64)

# 关键：不声明 restype，伪句柄 -1 会被截断成 32 位，GetModuleInformation 直接失败
k32.GetCurrentProcess.restype = wintypes.HANDLE
k32.GetCurrentProcess.argtypes = []
k32.GetModuleHandleExW.restype = wintypes.BOOL
k32.GetModuleHandleExW.argtypes = [wintypes.DWORD, ctypes.c_void_p,
                                   ctypes.POINTER(wintypes.HMODULE)]
k32.GetModuleFileNameW.restype = wintypes.DWORD
k32.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR,
                                   wintypes.DWORD]
k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
k32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREADENTRY32)]
k32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREADENTRY32)]
k32.OpenThread.restype = wintypes.HANDLE
k32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.SuspendThread.restype = wintypes.DWORD
k32.SuspendThread.argtypes = [wintypes.HANDLE]
k32.ResumeThread.restype = wintypes.DWORD
k32.ResumeThread.argtypes = [wintypes.HANDLE]
k32.GetThreadContext.restype = wintypes.BOOL
k32.GetThreadContext.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
k32.CloseHandle.argtypes = [wintypes.HANDLE]
k32.GetCurrentThreadId.restype = wintypes.DWORD
k32.GetCurrentThreadId.argtypes = []
k32.GetCurrentProcessId.restype = wintypes.DWORD
k32.GetCurrentProcessId.argtypes = []
psapi.GetModuleInformation.restype = wintypes.BOOL
psapi.GetModuleInformation.argtypes = [wintypes.HANDLE, wintypes.HMODULE,
                                       ctypes.POINTER(MODULEINFO),
                                       wintypes.DWORD]
ntdll.NtQueryInformationThread.restype = ctypes.c_long
ntdll.NtQueryInformationThread.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                           ctypes.c_void_p, wintypes.ULONG,
                                           ctypes.c_void_p]


_buf = ctypes.create_unicode_buffer(1024)
_lines = []


def module_of(addr):
    """地址 → (模块路径, 模块基址, 偏移)。"""
    try:
        hmod = wintypes.HMODULE()
        flags = 0x4 | 0x2  # FROM_ADDRESS | UNCHANGED_REFCOUNT
        ok = k32.GetModuleHandleExW(flags, ctypes.c_void_p(addr),
                                    ctypes.byref(hmod))
        if not ok or not hmod.value:
            return ("<unknown>", 0, addr)
        info = MODULEINFO()
        base = 0
        if psapi.GetModuleInformation(k32.GetCurrentProcess(), hmod,
                                      ctypes.byref(info),
                                      ctypes.sizeof(info)):
            base = info.lpBaseOfDll or 0
        n = k32.GetModuleFileNameW(hmod, _buf, 1024)
        path = _buf.value if n else "<noname>"
        return (path, base, addr - base)
    except Exception as exc:  # noqa: BLE001
        return (f"<resolve failed: {exc}>", 0, addr)


def thread_table(crash_tid=None, crash_rip=0):
    """枚举进程内全部线程：tid / 起始地址所属模块 / 当前 RIP 所属模块。"""
    lines = []
    TH32CS_SNAPTHREAD = 0x00000004
    THREAD_QUERY_INFORMATION = 0x0040
    THREAD_SUSPEND_RESUME = 0x0002
    THREAD_GET_CONTEXT = 0x0008
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if snap == wintypes.HANDLE(-1).value or not snap:
        return ["  <thread snapshot failed>"]
    entry = THREADENTRY32()
    entry.dwSize = ctypes.sizeof(THREADENTRY32)
    pid = k32.GetCurrentProcessId()
    cur_tid = k32.GetCurrentThreadId()
    try:
        ok = k32.Thread32First(snap, ctypes.byref(entry))
        while ok:
            if entry.th32OwnerProcessID == pid:
                tid = entry.th32ThreadID
                start = ctypes.c_uint64(0)
                h = k32.OpenThread(THREAD_QUERY_INFORMATION, False, tid)
                if h:
                    ntdll.NtQueryInformationThread(h, 9, ctypes.byref(start),
                                                   8, None)
                    k32.CloseHandle(h)
                spath, _sbase, soff = module_of(start.value)
                line = (f"  tid={tid} start=0x{start.value:x} "
                        f"[{os.path.basename(spath)}+0x{soff:x}]")
                if tid == crash_tid:
                    cpath, _b, coff = module_of(crash_rip)
                    line += (f" <== CRASHED rip=0x{crash_rip:x} "
                             f"[{os.path.basename(cpath)}+0x{coff:x}]")
                elif tid == cur_tid:
                    line += " <== handler thread (skip ctx)"
                else:
                    h2 = k32.OpenThread(THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT,
                                        False, tid)
                    if h2:
                        k32.SuspendThread(h2)
                        ctx = CONTEXT64()
                        ctx.ContextFlags = 0x00100000
                        if k32.GetThreadContext(h2, ctypes.byref(ctx)):
                            cpath, _b, coff = module_of(ctx.Rip)
                            line += (f" rip=0x{ctx.Rip:x} "
                                     f"[{os.path.basename(cpath)}+0x{coff:x}]")
                        k32.ResumeThread(h2)
                        k32.CloseHandle(h2)
                lines.append(line)
            ok = k32.Thread32Next(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return lines


def handler(exception_pointers):
    try:
        rec = exception_pointers.contents.ExceptionRecord.contents
        code = rec.ExceptionCode
        if code >= 0x80000000:
            addr = rec.ExceptionAddress or 0
            path, _b, off = module_of(addr)
            _emit([f"[veh] code=0x{code:08X} at {os.path.basename(path)}+0x{off:x}"])
        if code not in STATS_OF_INTEREST:
            if code == 0xE06D7363:
                return -1  # C++ throw：直接放行（CONTINUE_EXECUTION 会吞掉异常）
            if code >= 0xC0000000:
                return 0xC0000000  # CONTINUE_EXECUTION（本探查不处理）
            return 0  # CONTINUE_SEARCH：交给 SEH / 语言运行时
        addr = rec.ExceptionAddress or 0
        path, base, off = module_of(addr)
        name = STATUS_CODES.get(code, "0x%08X" % code)
        lines = [f"=== NATIVE FAULT: {name} ===",
                 f"fault addr = 0x{addr:x}  ->  {path}+0x{off:x} "
                 f"(base 0x{base:x})"]
        n = min(rec.NumberParameters, 15)
        for i in range(n):
            lines.append(f"  param[{i}] = 0x{rec.ExceptionInformation[i]:x}")
        if code == 0xC0000005 and n >= 2:
            access, target = rec.ExceptionInformation[0], rec.ExceptionInformation[1]
            kind = {0: "READ", 1: "WRITE", 8: "EXECUTE"}.get(access, str(access))
            lines.append(f"  -> {kind} of address 0x{target:x}")
        lines.append("--- threads ---")
        ctx_ptr = exception_pointers.contents.ContextRecord
        crash_tid = k32.GetCurrentThreadId()
        crash_rip = 0
        if ctx_ptr:
            crash_rip = CONTEXT64.from_address(ctx_ptr).Rip
        lines.extend(thread_table(crash_tid, crash_rip))
        _emit(lines)
    except Exception:
        _emit(["=== handler error ===", traceback.format_exc()])
    return 0  # EXCEPTION_CONTINUE_SEARCH：交给运行时正常崩溃


def _emit(lines):
    """立刻落盘（AV 不可捕获，finally 不会执行）。"""
    try:
        with open(OUT, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        pass


STATS_OF_INTEREST = {0xC0000005, 0xC000001D, 0xC0000094, 0xC0000096,
                     0xC00000FD, 0xC0000374, 0xC0000409, 0xC0000602}


def main():
    # 重置日志
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("probe started\n")

    proto = ctypes.WINFUNCTYPE(ctypes.c_long,
                               ctypes.POINTER(EXCEPTION_POINTERS))
    cb = proto(handler)
    _keep.append(cb)
    h = k32.AddVectoredExceptionHandler(1, cb)
    print(f"[probe] VEH installed: {h}", flush=True)

    game = sys.argv[1] if len(sys.argv) > 1 else "escape_giantess"
    target = None
    if "--target" in sys.argv:
        target = sys.argv[sys.argv.index("--target") + 1]

    sys.argv = ["minigame_preview.py", game] + (["--target", target] if target else [])
    try:
        import runpy
        runpy.run_path(os.path.join(HERE, "developer_tools", "minigame_preview.py"),
                       run_name="__main__")
    finally:
        with open(OUT, "a", encoding="utf-8") as f:
            f.write("\n".join(_lines) if _lines else
                    "no fatal exception captured (clean exit)\n")
        print("[probe] wrote", OUT)


_keep = []

if __name__ == "__main__":
    main()
