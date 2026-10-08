"""原生栈抓取：纯 ctypes + dbghelp，无需安装调试器。

挂一个进程级 vectored exception handler，捕获 0xC0000005 之类的致命原生异常，
打印：异常码 + 故障地址（模块!符号+偏移）+ 栈扫描得到的候选返回地址链。
符号来自 dbghelp（有 PDB 用 PDB，没有则退化为导出表符号）。
"""
import ctypes
import os
from ctypes import wintypes

import _outdir  # 同目录探针共享的产物目录（_out/dpg_probe/）

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_psapi = ctypes.WinDLL("psapi", use_last_error=True)
_dbg = ctypes.WinDLL("dbghelp", use_last_error=True)

EXCEPTION_CONTINUE_SEARCH = 0

_CONTEXT_RSP = 152
_CONTEXT_RBP = 160
_CONTEXT_RIP = 248


class EXCEPTION_RECORD64(ctypes.Structure):
    _fields_ = [("ExceptionCode", wintypes.DWORD),
                ("ExceptionFlags", wintypes.DWORD),
                ("ExceptionRecord", ctypes.c_void_p),
                ("ExceptionAddress", ctypes.c_void_p),
                ("NumberParameters", wintypes.DWORD),
                ("_pad", wintypes.DWORD),
                ("ExceptionInformation", ctypes.c_ulonglong * 15)]


class EXCEPTION_POINTERS(ctypes.Structure):
    _fields_ = [("ExceptionRecord", ctypes.POINTER(EXCEPTION_RECORD64)),
                ("ContextRecord", ctypes.c_void_p)]


class SYMBOL_INFO64(ctypes.Structure):
    _fields_ = [("SizeOfStruct", wintypes.ULONG),
                ("TypeIndex", wintypes.ULONG),
                ("Reserved", ctypes.c_ulonglong * 2),
                ("Index", wintypes.ULONG),
                ("Size", wintypes.ULONG),
                ("ModBase", ctypes.c_ulonglong),
                ("Flags", wintypes.ULONG),
                ("Value", ctypes.c_ulonglong),
                ("Address", ctypes.c_ulonglong),
                ("Register", wintypes.ULONG),
                ("Scope", wintypes.ULONG),
                ("Tag", wintypes.ULONG),
                ("NameLen", wintypes.ULONG),
                ("MaxNameLen", wintypes.ULONG),
                ("Name", ctypes.c_char * 512)]


class MEMORY_BASIC_INFORMATION64(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_ulonglong),
                ("AllocationBase", ctypes.c_ulonglong),
                ("AllocationProtect", wintypes.DWORD),
                ("_pad1", wintypes.DWORD),
                ("RegionSize", ctypes.c_ulonglong),
                ("State", wintypes.DWORD),
                ("Protect", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("_pad2", wintypes.DWORD)]


_NO_ACCESS = 0x01
_GUARD = 0x100

# ---- 原型（不声明 argtypes 时 ctypes 的隐式转换会让 SymInitialize 失败） ----
_k32.GetCurrentProcess.restype = wintypes.HANDLE
_k32.VirtualQuery.restype = ctypes.c_size_t
_k32.VirtualQuery.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
_k32.AddVectoredExceptionHandler.restype = ctypes.c_void_p
_k32.AddVectoredExceptionHandler.argtypes = [wintypes.ULONG, ctypes.c_void_p]
_psapi.EnumProcessModules.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                      wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
_psapi.EnumProcessModules.restype = wintypes.BOOL
_psapi.GetModuleFileNameExW.argtypes = [wintypes.HANDLE, wintypes.HMODULE,
                                        wintypes.LPWSTR, wintypes.DWORD]
_psapi.GetModuleFileNameExW.restype = wintypes.DWORD
_dbg.SymSetOptions.argtypes = [wintypes.DWORD]
_dbg.SymSetOptions.restype = wintypes.DWORD
_dbg.SymInitializeW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.BOOL]
_dbg.SymInitializeW.restype = wintypes.BOOL
_dbg.SymFromAddrW.argtypes = [wintypes.HANDLE, ctypes.c_ulonglong,
                              ctypes.POINTER(ctypes.c_ulonglong), ctypes.c_void_p]
_dbg.SymFromAddrW.restype = wintypes.BOOL


def _w(path):
    return os.path.basename(path)


class Symbolizer:
    """模块表 + dbghelp 符号解析（首次使用时初始化）。"""

    def __init__(self):
        self.modules = []
        self._ready = False
        self._buf = ctypes.create_string_buffer(4096)

    def init(self):
        if self._ready:
            return
        self._ready = True
        try:
            h = _k32.GetCurrentProcess()
            _dbg.SymSetOptions(0x02 | 0x04 | 0x10 | 0x200)  # UNDNAME|DEFERRED|LINES|FAILCRIT
            if not _dbg.SymInitializeW(h, None, True):
                os.write(2, b"[native] SymInitialize failed\n")
            need = wintypes.DWORD(0)
            arr = (wintypes.HMODULE * 2048)()
            _psapi.EnumProcessModules(h, ctypes.byref(arr), ctypes.sizeof(arr),
                                      ctypes.byref(need))
            count = need.value // ctypes.sizeof(wintypes.HMODULE)
            for i in range(min(count, 2048)):
                hm = arr[i]
                if not hm:
                    continue
                base = ctypes.cast(hm, ctypes.c_void_p).value
                buf = ctypes.create_unicode_buffer(1024)
                _psapi.GetModuleFileNameExW(h, hm, buf, 1024)
                self.modules.append([base, _module_size(base), buf.value])
        except Exception as exc:  # pragma: no cover
            os.write(2, f"[native] module enum failed: {exc}\n".encode())

    def describe(self, addr):
        for base, size, path in self.modules:
            if base <= addr < base + size:
                sym = self.symbol(addr)
                text = f"{_w(path)}+0x{addr - base:x}"
                if sym:
                    text += f" ({sym})"
                return text
        return f"0x{addr:x} <unknown>"

    def symbol(self, addr):
        try:
            sym = ctypes.cast(self._buf, ctypes.POINTER(SYMBOL_INFO64))
            sym.contents.SizeOfStruct = ctypes.sizeof(SYMBOL_INFO64)
            sym.contents.MaxNameLen = 256
            displ = ctypes.c_ulonglong(0)
            ok = _dbg.SymFromAddrW(_k32.GetCurrentProcess(), addr,
                                   ctypes.byref(displ), ctypes.byref(self._buf))
            if ok:
                name = sym.contents.Name.decode("ascii", "replace")
                return f"{name}+0x{displ.value:x}" if displ.value else name
        except Exception:
            pass
        return ""

    def in_code(self, addr):
        for base, size, _p in self.modules:
            if base <= addr < base + size:
                return True
        return False


def _module_size(base):
    """PE 头里的 SizeOfImage（VirtualQuery 只能拿到首个 region，会低估）。"""
    try:
        e_lfanew = ctypes.c_ulong.from_address(base + 0x3C).value
        size = ctypes.c_ulong.from_address(base + e_lfanew + 0x50).value
        if 0x1000 <= size < 0x40000000:
            return size
    except Exception:
        pass
    info = MEMORY_BASIC_INFORMATION64()
    if _k32.VirtualQuery(base, ctypes.byref(info), ctypes.sizeof(info)):
        return max(info.RegionSize, 0x1000)
    return 0x10000000


_sym = Symbolizer()


def _readable(addr, length=8):
    info = MEMORY_BASIC_INFORMATION64()
    if not _k32.VirtualQuery(addr, ctypes.byref(info), ctypes.sizeof(info)):
        return False
    if info.State != 0x1000:
        return False
    if info.Protect & _NO_ACCESS or info.Protect & _GUARD:
        return False
    return addr + length <= info.BaseAddress + info.RegionSize


def _read_u64(addr):
    if not _readable(addr, 8):
        return None
    try:
        return ctypes.c_ulonglong.from_address(addr).value
    except Exception:
        return None


def _out(text):
    try:
        os.write(2, (text + "\n").encode("utf-8", "replace"))
    except Exception:
        pass
    try:
        with open(_LOG_PATH, "a", encoding="utf-8", errors="replace") as fh:
            fh.write(text + "\n")
    except Exception:
        pass


_LOG_PATH = os.environ.get("PROBE_NATIVE_LOG",
                          _outdir.out("_probe_native_log.txt"))
_exc_count = [0]


def _count_exc():
    _exc_count[0] += 1
    return _exc_count[0]


_in_handler = [False]


def _handler(exception_pointers):
    if _in_handler[0]:
        return EXCEPTION_CONTINUE_SEARCH
    _in_handler[0] = True
    try:
        _dump(exception_pointers)
    except Exception as exc:  # pragma: no cover
        _out(f"[native] handler error: {exc}")
    finally:
        _in_handler[0] = False
    return EXCEPTION_CONTINUE_SEARCH


def _dump(exception_pointers):
    ep = exception_pointers.contents
    rec = ep.ExceptionRecord.contents
    ctx = ep.ContextRecord
    code = rec.ExceptionCode & 0xFFFFFFFF
    addr = rec.ExceptionAddress or 0
    n = _count_exc()
    if n > 4:
        if n == 5:
            _out(f"[native] (further exceptions suppressed; total>{n - 1})")
        return
    _sym.init()
    _out("")
    _out(f"================ NATIVE EXCEPTION #{n} ================")
    _out(f"code       : 0x{code:08X}")
    _out(f"fault addr : 0x{addr:016x}  -> {_sym.describe(addr)}")
    if rec.NumberParameters:
        params = [rec.ExceptionInformation[i] for i in range(rec.NumberParameters)]
        _out("params     : " + ", ".join(f"0x{p:x}" for p in params))
        if code == 0xC0000005 and len(params) >= 2:
            kinds = {0: "READ", 1: "WRITE", 8: "EXECUTE"}
            kind = kinds.get(params[0], str(params[0]))
            _out(f"             (AV {kind} at 0x{params[1]:x}"
                 f" -> {_sym.describe(params[1])})")
    if ctx:
        def rd(off):
            return ctypes.c_ulonglong.from_address(ctx + off).value
        rip, rsp, rbp = rd(_CONTEXT_RIP), rd(_CONTEXT_RSP), rd(_CONTEXT_RBP)
        _out(f"rip=0x{rip:016x} rsp=0x{rsp:016x} rbp=0x{rbp:016x}")
        _out("--- rip symbol ---")
        _out(f"  {_sym.describe(rip)}")
        _out("--- stack scan (candidate return addresses, innermost first) ---")
        seen = set()
        shown = 0
        for i in range(0, 0x4000 // 8):
            val = _read_u64(rsp + i * 8)
            if not val or not _sym.in_code(val):
                continue
            if val in seen:
                continue
            seen.add(val)
            _out(f"  +0x{i * 8:04x} 0x{val:016x} {_sym.describe(val)}")
            shown += 1
            if shown >= 40:
                _out("  ... (truncated)")
                break
    try:
        import faulthandler
        faulthandler.dump_traceback(all_threads=True)
    except Exception:
        pass
    _out("==================================================")


_CB = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.POINTER(EXCEPTION_POINTERS))
_CB_REF = _CB(_handler)


def install():
    """挂上进程级 VE 处理器（必须在崩溃前调用）。"""
    handle = _k32.AddVectoredExceptionHandler(1, _CB_REF)
    _sym.init()
    _out(f"[native] installed (pid={os.getpid()}) log={_LOG_PATH}")
    return handle


if __name__ == "__main__":
    import faulthandler
    faulthandler.enable()
    install()
    os.write(2, b"[native] installed; triggering deliberate AV\n")
    ctypes.string_at(1)
