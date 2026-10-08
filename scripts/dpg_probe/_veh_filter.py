# -*- coding: utf-8 -*-
"""零干扰崩溃探针：SetUnhandledExceptionFilter + MiniDumpWriteDump。

VEH 方案会改变 first-chance 异常分发（本机表现为启动卡死），不可用。
UnhandledExceptionFilter 只在进程注定要死时调用，不影响正常运行路径——
崩溃应照常复现，且我们在死前拿到：异常码/故障地址/rip+栈扫描的模块偏移/
完整 minidump（供事后离线分析）。

用法：python _veh_filter.py escape_giantess --target 1
输出：_out/dpg_probe/ 下的 _veh_filter_report.txt + crash.dmp + stdout 面包屑
"""
import ctypes
import os
import runpy
import sys
from ctypes import wintypes

import _outdir  # 同目录探针共享的产物目录（_out/dpg_probe/）

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = _outdir.out("_veh_filter_report.txt")
DMP = _outdir.out("crash.dmp")

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)
dbg = ctypes.WinDLL("dbghelp", use_last_error=True)


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


class MODULEINFO(ctypes.Structure):
    _fields_ = [("lpBaseOfDll", ctypes.c_void_p),
                ("SizeOfImage", wintypes.DWORD),
                ("EntryPoint", ctypes.c_void_p)]


CONTEXT_RSP = 0x98
CONTEXT_RIP = 0xF8

k32.GetCurrentProcess.restype = wintypes.HANDLE
k32.VirtualQuery.restype = ctypes.c_size_t
k32.VirtualQuery.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
psapi.EnumProcessModules.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                     wintypes.DWORD,
                                     ctypes.POINTER(wintypes.DWORD)]
psapi.EnumProcessModules.restype = wintypes.BOOL
psapi.GetModuleFileNameExW.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                       wintypes.LPWSTR, wintypes.DWORD]
psapi.GetModuleFileNameExW.restype = wintypes.DWORD
psapi.GetModuleInformation.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                       ctypes.POINTER(MODULEINFO),
                                       wintypes.DWORD]
psapi.GetModuleInformation.restype = wintypes.BOOL
k32.SetUnhandledExceptionFilter.restype = ctypes.c_void_p
k32.SetUnhandledExceptionFilter.argtypes = [ctypes.c_void_p]
dbg.MiniDumpWriteDump.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                  wintypes.HANDLE, ctypes.c_int,
                                  ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.c_void_p]
dbg.MiniDumpWriteDump.restype = wintypes.BOOL

MODULES = []


def snapshot_modules():
    h = k32.GetCurrentProcess()
    need = wintypes.DWORD(0)
    arr = (ctypes.c_void_p * 4096)()
    if not psapi.EnumProcessModules(h, ctypes.byref(arr), ctypes.sizeof(arr),
                                    ctypes.byref(need)):
        return
    for i in range(min(need.value // ctypes.sizeof(ctypes.c_void_p), 4096)):
        base = arr[i]
        if not base:
            continue
        info = MODULEINFO()
        size = 0
        if psapi.GetModuleInformation(h, ctypes.c_void_p(base),
                                      ctypes.byref(info),
                                      ctypes.sizeof(info)):
            size = info.SizeOfImage
        buf = ctypes.create_unicode_buffer(1024)
        psapi.GetModuleFileNameExW(h, ctypes.c_void_p(base), buf, 1024)
        MODULES.append((base, size, os.path.basename(buf.value)))


def describe(addr):
    for base, size, name in MODULES:
        if base <= addr < base + size:
            return f"{name}+0x{addr - base:x}"
    return None


def _readable(addr, length=8):
    class MBI(ctypes.Structure):
        _fields_ = [("BaseAddress", ctypes.c_ulonglong),
                    ("AllocationBase", ctypes.c_ulonglong),
                    ("AllocationProtect", wintypes.DWORD),
                    ("_p1", wintypes.DWORD),
                    ("RegionSize", ctypes.c_ulonglong),
                    ("State", wintypes.DWORD),
                    ("Protect", wintypes.DWORD),
                    ("_p2", wintypes.DWORD)]
    info = MBI()
    if not k32.VirtualQuery(ctypes.c_void_p(addr), ctypes.byref(info),
                            ctypes.sizeof(info)):
        return False
    if info.State != 0x1000 or info.Protect & 0x100:
        return False
    return addr + length <= info.BaseAddress + info.RegionSize


def _write(lines):
    with open(OUT, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
        f.flush()
        os.fsync(f.fileno())


def filter_proc(ep):
    try:
        # 启动时快照不包含后加载的 DLL（pyd / 显卡驱动等），崩溃时重新枚举
        snapshot_modules()
        rec = ep.contents.ExceptionRecord.contents
        ctx = ep.contents.ContextRecord
        code = rec.ExceptionCode & 0xFFFFFFFF
        addr = rec.ExceptionAddress or 0
        lines = [f"=== UNHANDLED code=0x{code:08X} at 0x{addr:016x} "
                 f"({describe(addr) or '<unknown>'}) ==="]
        n = min(rec.NumberParameters, 15)
        for i in range(n):
            lines.append(f"  param[{i}]=0x{rec.ExceptionInformation[i]:x}")
        if code == 0xC0000005 and n >= 2:
            kind = {0: "READ", 1: "WRITE", 8: "EXEC"}.get(
                rec.ExceptionInformation[0], "?")
            target = rec.ExceptionInformation[1]
            lines.append(f"  -> {kind} at 0x{target:x} "
                         f"({describe(target) or 'heap/stack'})")
        if ctx:
            base = ctypes.cast(ctx, ctypes.c_void_p).value
            regs = {}
            for name, off in (("rax", 0x78), ("rcx", 0x80), ("rdx", 0x88),
                              ("rbx", 0x90), ("rsp", 0x98), ("rbp", 0xA0),
                              ("rsi", 0xA8), ("rdi", 0xB0), ("r8", 0xB8),
                              ("r9", 0xC0), ("r10", 0xC8), ("r11", 0xD0),
                              ("r12", 0xD8), ("r13", 0xE0), ("r14", 0xE8),
                              ("r15", 0xF0), ("rip", 0xF8)):
                regs[name] = ctypes.c_ulonglong.from_address(base + off).value
            lines.append("regs: " + " ".join(
                f"{k}=0x{v:x}" for k, v in regs.items()))
            rip, rsp = regs["rip"], regs["rsp"]
            try:
                bs = (ctypes.c_ubyte * 16).from_address(rip)
                lines.append("instr: " + " ".join(f"{b:02x}" for b in bs))
            except Exception:
                pass
            # RIP 所在内存区域（匿名可执行页 = JIT/ffi 闭包）
            class MBI(ctypes.Structure):
                _fields_ = [("BaseAddress", ctypes.c_ulonglong),
                            ("AllocationBase", ctypes.c_ulonglong),
                            ("AllocationProtect", wintypes.DWORD),
                            ("__align1", ctypes.c_uint),
                            ("RegionSize", ctypes.c_ulonglong),
                            ("State", wintypes.DWORD),
                            ("Protect", wintypes.DWORD),
                            ("Type", wintypes.DWORD),
                            ("__align2", ctypes.c_uint)]
            info = MBI()
            ok_vq = k32.VirtualQuery(ctypes.c_void_p(rip), ctypes.byref(info),
                                     ctypes.sizeof(info))
            if ok_vq:
                lines.append(
                    f"rip region: base=0x{info.BaseAddress:x} "
                    f"size=0x{info.RegionSize:x} protect=0x{info.Protect:x} "
                    f"type=0x{info.Type:x} alloc_base=0x{info.AllocationBase:x}")
            # 识别映射镜像：读分配基址 PE 头 + 字符串嗅探
            ab = info.AllocationBase if ok_vq else 0
            try:
                head = (ctypes.c_ubyte * 0x400).from_address(ab)
                hb = bytes(head)
                lines.append(f"alloc_base head: {hb[:16].hex()} "
                             f"ascii={hb[:2]!r}")
                if hb[:2] == b"MZ":
                    e_lfanew = int.from_bytes(hb[0x3C:0x40], "little")
                    pe = (ctypes.c_ubyte * 0x400).from_address(ab + e_lfanew)
                    pb = bytes(pe)
                    if pb[:4] == b"PE\0\0":
                        machine = int.from_bytes(pb[4:6], "little")
                        nsec = int.from_bytes(pb[6:8], "little")
                        stamp = int.from_bytes(pb[8:12], "little")
                        lines.append(
                            f"PE: machine=0x{machine:x} sections={nsec} "
                            f"timestamp=0x{stamp:x} ({stamp})")
                # 字符串嗅探（前 64KB）
                blob = (ctypes.c_ubyte * 0x10000).from_address(ab)
                bb = bytes(blob)
                found = set()
                cur = b""
                for b in bb:
                    if 32 <= b < 127:
                        cur += bytes((b,))
                    else:
                        if len(cur) >= 6:
                            found.add(cur)
                        cur = b""
                hits = sorted(found)[:40]
                lines.append("strings: " + " | ".join(
                    s.decode("ascii", "replace") for s in hits))
            except Exception as exc:
                lines.append(f"alloc_base read failed: {exc!r}")
            lines.append("--- first raw stack slots (unresolved incl. closures) ---")
            for i in range(24):
                a = rsp + i * 8
                if not _readable(a):
                    continue
                val = ctypes.c_ulonglong.from_address(a).value
                lines.append(f"  rsp+0x{i * 8:02x} 0x{val:016x} "
                             f"{describe(val) or ''}")
            lines.append("--- stack scan (values resolving into modules) ---")
            shown = 0
            seen = set()
            for i in range(0x8000 // 8):
                a = rsp + i * 8
                if not _readable(a):
                    continue
                val = ctypes.c_ulonglong.from_address(a).value
                if not val or val in seen:
                    continue
                d = describe(val)
                if d:
                    seen.add(val)
                    lines.append(f"  rsp+0x{i * 8:04x} 0x{val:016x} {d}")
                    shown += 1
                    if shown >= 80:
                        lines.append("  ... (truncated)")
                        break
        _write(lines)
        # 读 DPG 全局指针链实况：定位到底是哪个指针为 NULL
        try:
            dpg_mod = next((m for m in MODULES if m[2] == "_dearpygui.pyd"), None)
            if dpg_mod:
                base = dpg_mod[0]
                def rd(rva):
                    a = base + rva
                    if not _readable(a):
                        return None
                    return ctypes.c_ulonglong.from_address(a).value
                def rda(addr):
                    if not addr or not _readable(addr, 8):
                        return None
                    return ctypes.c_ulonglong.from_address(addr).value
                gB = rd(0x5BFE38)        # fB 的全局
                bB = (gB + 0x30) if gB else 0
                lines.append("--- dpg globals ---")
                lines.append(f"globalB=0x{gB or 0:x} fB()=0x{bB:x}")
                if bB:
                    for off in (0x30, 0x38, 0x78, 0x98, 0xa0, 0xa8, 0xb0,
                                0xe0, 0xe8):
                        v = rda(bB + off)
                        lines.append(f"  fB()+0x{off:x} = 0x{v or 0:x}")
                _write(lines)
        except Exception as exc:
            lines.append(f"global peek failed: {exc!r}")
            _write(lines)
        # Python 侧现场：确认崩溃发生在哪个 dpg 调用
        try:
            import faulthandler
            import tempfile
            with tempfile.TemporaryFile("w+", encoding="utf-8") as tf:
                faulthandler.dump_traceback(file=tf, all_threads=True)
                tf.seek(0)
                _write(["--- python traceback ---", tf.read()])
        except Exception as exc:
            _write([f"faulthandler dump failed: {exc!r}"])
        # 完整 minidump（含全部线程与堆）供事后分析
        try:
            class MINIDUMP_EXCEPTION_INFORMATION(ctypes.Structure):
                _fields_ = [("ThreadId", wintypes.DWORD),
                            ("ExceptionPointers",
                             ctypes.POINTER(EXCEPTION_POINTERS)),
                            ("ClientPointers", wintypes.BOOL)]

            mei = MINIDUMP_EXCEPTION_INFORMATION()
            mei.ThreadId = k32.GetCurrentThreadId()
            mei.ExceptionPointers = ep
            mei.ClientPointers = False
            with open(DMP, "wb") as fh:
                ok = dbg.MiniDumpWriteDump(
                    k32.GetCurrentProcess(), os.getpid(),
                    wintypes.HANDLE(msvcrt.get_osfhandle(fh.fileno())),
                    0x2 | 0x4,  # WithFullMemory | WithHandleData
                    ctypes.byref(mei), None, None)
            _write([f"minidump written={bool(ok)} err={ctypes.get_last_error()} -> {DMP}"])
        except Exception as exc:
            _write([f"minidump failed: {exc!r}"])
    except Exception:
        try:
            import traceback
            _write(["filter error:", traceback.format_exc()])
        except Exception:
            pass
    return 1  # EXCEPTION_EXECUTE_HANDLER：正常终止进程


def msvcrt_handle(fh):
    return msvcrt.get_osfhandle(fh.fileno())


k32.GetCurrentThreadId.restype = wintypes.DWORD
k32.GetCurrentThreadId.argtypes = []

import msvcrt  # noqa: E402

_CB = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.POINTER(EXCEPTION_POINTERS))
CB_REF = _CB(filter_proc)

_T0 = None
_UPDATES = [0]
_LAST_BEAT = [0.0]
_AUTO_CLOSE = float(os.environ.get("AUTO_CLOSE", "0") or 0)


def _beat(msg):
    import time
    print(f"[crumb {time.monotonic() - _T0:7.3f}] {msg}", flush=True)


def install_breadcrumbs():
    global _T0
    import time
    _T0 = time.monotonic()
    import dearpygui.dearpygui as dpg

    # --- fB()+0xa8 逐帧轮询：定位该指针何时为空 ---
    def _pyd_base():
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        h = k32.GetCurrentProcess()
        need = wintypes.DWORD(0)
        arr = (ctypes.c_void_p * 4096)()
        if not psapi.EnumProcessModules(h, ctypes.byref(arr),
                                        ctypes.sizeof(arr), ctypes.byref(need)):
            return None
        for i in range(need.value // ctypes.sizeof(ctypes.c_void_p)):
            hm = arr[i]
            if not hm:
                continue
            buf = ctypes.create_unicode_buffer(1024)
            psapi.GetModuleFileNameExW(h, ctypes.c_void_p(hm), buf, 1024)
            if os.path.basename(buf.value) == "_dearpygui.pyd":
                return hm
        return None

    _poll = {"base": None, "last": None}

    def _poll_ptr():
        if _poll["base"] is None:
            _poll["base"] = _pyd_base()
            if _poll["base"] is None:
                return None
        gB = ctypes.c_ulonglong.from_address(_poll["base"] + 0x5BFE38).value
        if not gB:
            return "gB=0"
        addr = gB + 0x30 + 0xa8
        v = ctypes.c_ulonglong.from_address(addr).value
        return v

    def _mark(step):
        v = _poll_ptr()
        _beat(f"ptr@{step} = 0x{v:x}" if isinstance(v, int) else f"ptr@{step} = {v}")
        _poll["last"] = v

    # 宿主差异开关（与副本会话对齐用）：MC=手动回调管理 NOMAX=不最大化
    # NORESIZE=不设 resize 回调
    _host = {p.strip() for p in os.environ.get("PROBE_HOST", "").split(",") if p.strip()}
    _orig_create_context = dpg.create_context

    def create_context(*a, **k):
        r = _orig_create_context(*a, **k)
        if "MC" in _host:
            dpg.configure_app(manual_callback_management=True)
            _beat("manual callback management ON")
        _mark("create_context")
        return r

    dpg.create_context = create_context
    _orig_setup = dpg.setup_dearpygui

    def setup(*a, **k):
        r = _orig_setup(*a, **k)
        _mark("setup_dearpygui")
        return r

    dpg.setup_dearpygui = setup
    _orig_show = dpg.show_viewport

    def show(*a, **k):
        r = _orig_show(*a, **k)
        _mark("show_viewport")
        return r

    dpg.show_viewport = show
    if "NOMAX" in _host:
        dpg.maximize_viewport = lambda *a, **k: None
    if "NORESIZE" in _host:
        dpg.set_viewport_resize_callback = lambda *a, **k: None

    orig_render = dpg.render_dearpygui_frame

    def render():
        _FRAMES[0] += 1
        now = time.monotonic()
        if now - _LAST_BEAT[0] >= 1.0:
            _LAST_BEAT[0] = now
            _beat(f"alive frames={_FRAMES[0]} updates={_UPDATES[0]}")
        v = _poll_ptr()
        if v != _poll["last"]:
            _poll["last"] = v
            _beat(f"ptr changed -> 0x{v:x}" if isinstance(v, int) else f"ptr changed -> {v}")
        if _AUTO_CLOSE and now - _T0 > _AUTO_CLOSE:
            _beat("auto-close reached")
            dpg.stop_dearpygui()
            return None
        return orig_render()

    dpg.render_dearpygui_frame = render

    from dungeon.window.minigame import stage as stage_mod
    orig_build = stage_mod._MiniGameStage.build

    def build(self):
        _beat(f"stage.build begin ({self._game_cls.__name__})")
        ok = orig_build(self)
        _beat(f"stage.build -> {ok}")
        if ok and self._game is not None:
            gu = self._game.update

            def upd(dt, _gu=gu):
                _UPDATES[0] += 1
                return _gu(dt)

            self._game.update = upd
        return ok

    stage_mod._MiniGameStage.build = build


_FRAMES = [0]


def main():
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("=== _veh_filter run ===\n")
    snapshot_modules()
    prev = k32.SetUnhandledExceptionFilter(
        ctypes.cast(CB_REF, ctypes.c_void_p))
    print(f"[filter] installed (prev=0x{prev or 0:x})", flush=True)
    with open(_outdir.out("_veh_pid.txt"), "w") as f:
        f.write(str(os.getpid()))
    install_breadcrumbs()
    sys.argv = ["minigame_preview.py"] + sys.argv[1:]
    try:
        runpy.run_path(os.path.join(HERE, "developer_tools", "minigame_preview.py"),
                       run_name="__main__")
    except SystemExit as exc:
        _beat(f"SystemExit {exc.code}")
    _beat("script returned")


if __name__ == "__main__":
    main()
