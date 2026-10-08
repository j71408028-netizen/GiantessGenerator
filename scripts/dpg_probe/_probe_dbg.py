"""进程外迷你调试器：用 DEBUG_ONLY_THIS_PROCESS 起子进程，捕获原生异常并
报告「故障地址所属模块/符号 + 栈扫描链」。不改动子进程（无需注入、无 VE 处理器）。

用法：python _probe_dbg.py <超时秒> <命令...>

产物：本目录下的 _probe_dbg_log.txt（探针日志）与 _probe_child_out.txt（子进程输出）。
"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_psapi = ctypes.WinDLL("psapi", use_last_error=True)

DEBUG_ONLY_THIS_PROCESS = 0x00000002
DBG_CONTINUE = 0x00010002
DBG_EXCEPTION_NOT_HANDLED = 0x80010001

_k32.GetCurrentProcess.restype = wintypes.HANDLE
_k32.WaitForDebugEvent.argtypes = [ctypes.c_void_p, wintypes.DWORD]
_k32.WaitForDebugEvent.restype = wintypes.BOOL
_k32.ContinueDebugEvent.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
_k32.ContinueDebugEvent.restype = wintypes.BOOL
_k32.GetThreadContext.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
_k32.GetThreadContext.restype = wintypes.BOOL
_k32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
_k32.ReadProcessMemory.restype = wintypes.BOOL
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_psapi.EnumProcessModules.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                      wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
_psapi.GetModuleFileNameExW.argtypes = [wintypes.HANDLE, wintypes.HMODULE,
                                        wintypes.LPWSTR, wintypes.DWORD]

CONTEXT_FLAGS_OFF = 48
CONTEXT_FLAGS_VALUE = 0x0010000B       # CONTROL | INTEGER | SEGMENTS
CONTEXT_RSP = 152
CONTEXT_RBP = 160
CONTEXT_RIP = 248
CONTEXT_SIZE = 1232

EXCEPTION_DEBUG_EVENT = 1
CREATE_THREAD_DEBUG_EVENT = 2
CREATE_PROCESS_DEBUG_EVENT = 3
EXIT_THREAD_DEBUG_EVENT = 4
EXIT_PROCESS_DEBUG_EVENT = 5
LOAD_DLL_DEBUG_EVENT = 6
UNLOAD_DLL_DEBUG_EVENT = 7
OUTPUT_DEBUG_STRING_EVENT = 8
RIP_EVENT = 9


class EXCEPTION_RECORD64(ctypes.Structure):
    _fields_ = [("ExceptionCode", wintypes.DWORD),
                ("ExceptionFlags", wintypes.DWORD),
                ("ExceptionRecord", ctypes.c_void_p),
                ("ExceptionAddress", ctypes.c_void_p),
                ("NumberParameters", wintypes.DWORD),
                ("_pad", wintypes.DWORD),
                ("ExceptionInformation", ctypes.c_ulonglong * 15)]


class EXCEPTION_DEBUG_INFO(ctypes.Structure):
    _fields_ = [("ExceptionRecord", EXCEPTION_RECORD64),
                ("dwFirstChance", wintypes.DWORD)]


class _Union(ctypes.Union):
    _fields_ = [("Exception", EXCEPTION_DEBUG_INFO),
                ("raw", ctypes.c_ubyte * 192)]


class DEBUG_EVENT(ctypes.Structure):
    _fields_ = [("dwDebugEventCode", wintypes.DWORD),
                ("dwProcessId", wintypes.DWORD),
                ("dwThreadId", wintypes.DWORD),
                ("_pad", wintypes.DWORD),
                ("u", _Union)]



class ChildProcess:
    """子进程模块表 + 符号解析（dbghelp 支持远程进程）。"""

    def __init__(self, hproc):
        self.hproc = hproc
        self.modules = []
        self._buf = ctypes.create_string_buffer(4096)

    def init(self):
        need = wintypes.DWORD(0)
        arr = (wintypes.HMODULE * 2048)()
        _psapi.EnumProcessModules(self.hproc, ctypes.byref(arr), ctypes.sizeof(arr),
                                  ctypes.byref(need))
        count = need.value // ctypes.sizeof(wintypes.HMODULE)
        for i in range(min(count, 2048)):
            hm = arr[i]
            if not hm:
                continue
            base = ctypes.cast(hm, ctypes.c_void_p).value
            buf = ctypes.create_unicode_buffer(1024)
            _psapi.GetModuleFileNameExW(self.hproc, hm, buf, 1024)
            self.modules.append([base, self._pe_size(base), buf.value])

    def _pe_size(self, base):
        hdr = ctypes.create_string_buffer(0x400)
        got = ctypes.c_size_t(0)
        if not _k32.ReadProcessMemory(self.hproc, base, hdr,
                                      ctypes.sizeof(hdr), ctypes.byref(got)):
            return 0x10000000
        e_lfanew = int.from_bytes(hdr.raw[0x3C:0x40], "little")
        if 0 < e_lfanew < 0x300:
            size = int.from_bytes(hdr.raw[e_lfanew + 0x50:e_lfanew + 0x54], "little")
            if 0x1000 <= size < 0x40000000:
                return size
        return 0x10000000

    def describe(self, addr):
        for base, size, path in self.modules:
            if base <= addr < base + size:
                symbol = self.symbol(addr)
                text = f"{os.path.basename(path)}+0x{addr - base:x}"
                return f"{text} ({symbol})" if symbol else text
        return f"0x{addr:x} <unknown>"

    def in_code(self, addr):
        for base, size, _p in self.modules:
            if base <= addr < base + size:
                return True
        return False

    def sym_init(self):
        self._dbg = None
        try:
            dbg = ctypes.WinDLL("dbghelp", use_last_error=True)
            dbg.SymSetOptions.argtypes = [wintypes.DWORD]
            dbg.SymInitializeW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR,
                                           wintypes.BOOL]
            dbg.SymFromAddrW.argtypes = [wintypes.HANDLE, ctypes.c_ulonglong,
                                         ctypes.POINTER(ctypes.c_ulonglong),
                                         ctypes.c_void_p]
            dbg.SymLoadModuleExW.argtypes = [wintypes.HANDLE, wintypes.HANDLE,
                                             wintypes.LPCWSTR, wintypes.LPCWSTR,
                                             ctypes.c_ulonglong, wintypes.DWORD,
                                             ctypes.c_void_p, wintypes.DWORD]
            dbg.SymSetOptions(0x02 | 0x04 | 0x200)
            dbg.SymInitializeW(self.hproc, None, False)
            for base, size, path in self.modules:
                dbg.SymLoadModuleExW(self.hproc, None, path, None, base, size,
                                     None, 0)
            self._dbg = dbg
        except Exception as exc:
            out(f"[dbg] symbol init failed: {exc}")

    def symbol(self, addr):
        if not getattr(self, "_dbg", None):
            return ""
        try:
            sym = ctypes.addressof(self._buf)
            ctypes.memset(sym, 0, 128)
            ctypes.c_ulong.from_address(sym).value = 88        # SizeOfStruct
            ctypes.c_ulong.from_address(sym + 80).value = 256  # MaxNameLen
            displ = ctypes.c_ulonglong(0)
            if self._dbg.SymFromAddrW(self.hproc, addr, ctypes.byref(displ),
                                      ctypes.c_void_p(sym)):
                name = ctypes.string_at(sym + 84).decode("ascii", "replace")
                return f"{name}+0x{displ.value:x}" if displ.value else name
        except Exception:
            pass
        return ""

    def read_u64(self, addr):
        val = ctypes.c_ulonglong(0)
        got = ctypes.c_size_t(0)
        if _k32.ReadProcessMemory(self.hproc, ctypes.c_void_p(addr),
                                  ctypes.byref(val), 8, ctypes.byref(got)):
            return val.value
        return None

    def read_bytes(self, addr, size):
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t(0)
        if _k32.ReadProcessMemory(self.hproc, ctypes.c_void_p(addr), buf, size,
                                  ctypes.byref(got)) and got.value == size:
            return buf.raw
        return None

    def context(self, hthread):
        ctx = ctypes.create_string_buffer(CONTEXT_SIZE)
        addr = ctypes.addressof(ctx)
        ctypes.c_ulong.from_address(addr + CONTEXT_FLAGS_OFF).value = CONTEXT_FLAGS_VALUE
        if not _k32.GetThreadContext(hthread, ctypes.c_void_p(addr)):
            return None
        regs = {"rip": CONTEXT_RIP, "rsp": CONTEXT_RSP, "rbp": CONTEXT_RBP,
                "rcx": 128, "rdx": 136, "r8": 184, "r9": 192, "rsi": 168,
                "rdi": 176, "rax": 120, "rbx": 144}
        return {name: ctypes.c_ulonglong.from_address(addr + off).value
                for name, off in regs.items()}


_LOG = open(_outdir.out("_probe_dbg_log.txt"), "w",
            encoding="utf-8", errors="replace")


def out(text):
    print(text, flush=True)
    _LOG.write(text + "\n")
    _LOG.flush()


def dump_exception(child, rec, ctx, first_chance):
    addr = rec.ExceptionAddress or 0
    if not addr and ctx:
        addr = ctx["rip"]
    code = rec.ExceptionCode & 0xFFFFFFFF
    tag = "FIRST-CHANCE" if first_chance else "SECOND-CHANCE (fatal)"
    out(f"=========== EXCEPTION {tag} code=0x{code:08X} ===========")
    out(f"modules loaded: {len(child.modules)}")
    out(f"addr : 0x{addr:016x} -> {child.describe(addr)}")
    near = [m for m in child.modules if m[0] <= addr]
    if near:
        base, size, path = max(near, key=lambda m: m[0])
        out(f"nearest module below: {os.path.basename(path)}+0x{addr - base:x} "
            f"(base=0x{base:x} size=0x{size:x})")
    params = [rec.ExceptionInformation[i] for i in range(rec.NumberParameters)]
    if params:
        out("params: " + ", ".join(f"0x{p:x}" for p in params))
        if code == 0xC0000005 and len(params) >= 2:
            kind = {0: "READ", 1: "WRITE", 8: "EXECUTE"}.get(params[0], str(params[0]))
            out(f"        AV {kind} at 0x{params[1]:x} -> {child.describe(params[1])}")
    if not ctx:
        out("(no thread context)")
        return
    out(f"rip=0x{ctx['rip']:016x} rsp=0x{ctx['rsp']:016x} rbp=0x{ctx['rbp']:016x}")
    out(f"rip -> {child.describe(ctx['rip'])}")
    code = child.read_bytes(ctx["rip"] - 24, 40)
    if code:
        out(f"code @rip-24: {code.hex(' ', 4)}")
    for reg in ("rcx", "rdx", "r8", "r9", "rsi", "rdi"):
        out(f"  {reg} = 0x{ctx.get(reg, 0):016x}")
    out("--- stack scan ---")
    seen, shown = set(), 0
    for i in range(0, 0x8000 // 8):
        val = child.read_u64(ctx["rsp"] + i * 8)
        if not val or val in seen or not child.in_code(val):
            continue
        seen.add(val)
        out(f"  +0x{i * 8:05x} 0x{val:016x} {child.describe(val)}")
        shown += 1
        if shown >= 45:
            out("  ...(truncated)")
            break
    if not shown:
        out("  (no code addresses on stack)")
    out("========================================================")


def main():
    timeout = float(sys.argv[1])
    cmd = sys.argv[2:]
    out(f"[dbg] launching: {' '.join(cmd)}")
    child_out = open(_outdir.out("_probe_child_out.txt"), "w",
                     encoding="utf-8", errors="replace")
    proc = subprocess.Popen(cmd, creationflags=DEBUG_ONLY_THIS_PROCESS,
                            stdout=child_out, stderr=subprocess.STDOUT)
    hproc = wintypes.HANDLE(int(proc._handle))
    child = None
    av_seen = 0
    exit_code = None
    main_tid = None
    t0 = time.time()
    ev = DEBUG_EVENT()
    while True:
        if time.time() - t0 > timeout:
            out(f"[dbg] timeout after {timeout}s -> terminate")
            _k32.TerminateProcess(hproc, 1)
            break
        if not _k32.WaitForDebugEvent(ctypes.byref(ev), 500):
            if child is not None and main_tid:
                _sample(child, main_tid, t0)
            continue
        code = ev.dwDebugEventCode
        status = DBG_CONTINUE
        if code == CREATE_PROCESS_DEBUG_EVENT:
            child = ChildProcess(hproc)
            child.init()
            child.sym_init()
            b = int.from_bytes(bytes(ev.u.raw[24:32]), "little")
            out(f"[dbg] process created; image base 0x{b:x}; "
                f"{len(child.modules)} modules")
        elif code == EXCEPTION_DEBUG_EVENT:
            rec = ev.u.Exception.ExceptionRecord
            exc_code = rec.ExceptionCode & 0xFFFFFFFF
            if child is not None:
                child.init()          # 每次异常都刷新（CREATE_PROCESS 时模块表还空）
                child.sym_init()
            if exc_code in (0xC0000005, 0xC0000409, 0xC0000374, 0xC00000FD,
                            0x80000003):
                hthread = wintypes.HANDLE(_thread_handle(ev.dwThreadId))
                ctx = child.context(hthread) if child else None
                if exc_code == 0x80000003 and ev.u.Exception.dwFirstChance:
                    out(f"[dbg] initial breakpoint (tid={ev.dwThreadId})")
                    main_tid = ev.dwThreadId
                else:
                    av_seen += 1
                    if av_seen <= 5:
                        out(f"[dbg] tid={ev.dwThreadId}")
                        dump_exception(child, rec, ctx, bool(ev.u.Exception.dwFirstChance))
            status = DBG_EXCEPTION_NOT_HANDLED
        elif code == EXIT_PROCESS_DEBUG_EVENT:
            exit_code = int.from_bytes(bytes(ev.u.raw[0:4]), "little")
            _k32.ContinueDebugEvent(ev.dwProcessId, ev.dwThreadId, DBG_CONTINUE)
            break
        _k32.ContinueDebugEvent(ev.dwProcessId, ev.dwThreadId, status)
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    child_out.close()
    out(f"[dbg] exit code = {exit_code} "
        f"(pid {proc.pid}); exception events seen = {av_seen}")
    if exit_code:
        out(f"[dbg] exit code hex = 0x{exit_code & 0xFFFFFFFF:08X}")
    return 0


def _thread_handle(tid):
    THREAD_ALL_ACCESS = 0x1FFFFF
    h = _k32.OpenThread(THREAD_ALL_ACCESS, False, tid)
    return h


_samples = []


def _sample(child, tid, t0):
    """空闲时抽样主线程 RIP（粗粒度 profiler：看渲染那几秒花在哪）。"""
    h = _thread_handle(tid)
    if not h:
        return
    try:
        ctx = child.context(h)
        if ctx:
            _samples.append(ctx["rip"])
            out(f"[sample +{time.time() - t0:5.1f}s] {child.describe(ctx['rip'])}")
    finally:
        _k32.CloseHandle(h)


_k32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.OpenThread.restype = wintypes.HANDLE
_k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]


if __name__ == "__main__":
    sys.exit(main())

