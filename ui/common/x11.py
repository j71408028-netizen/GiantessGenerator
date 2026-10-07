"""Linux/X11 原生支持：XInitThreads 引导 + EWMH 窗口置顶。

Tk（专业模式 / 挂件模式）与 Dear PyGui/GLFW（副本视口）在同一个进程里共用同一个
X server，这在 X11 上是**两个独立的 display 连接**。libX11 默认按「单连接单线程」
编译，两套工具包各带自己的线程去碰 X，是 BadWindow / BadDrawable 与
``ImGui_ImplGlfw_WindowFocusCallback`` 段错误的经典成因。

本模块提供两件事：

1. :func:`init_x_threads`——尽早（**任何** display 连接建立之前）调用
   ``XInitThreads()``，让 libX11 全程加锁。这是全进程一次性生效的开关，调用点
   必须是入口文件的最前面（``main.py`` 顶部、GUI 冒烟脚本顶部），晚于
   ``tkinter`` / ``dearpygui`` 的首次连接就只能返回 False 了；
2. :func:`set_above` / :func:`supports_above`——直接写 EWMH 的
   ``_NET_WM_STATE_ABOVE``。Tk 的 ``-topmost`` 在部分桌面环境（实测 GNOME/X11）
   写不进去也不报错，而 EWMH 的 ClientMessage 是这套协议的正路，能回读确认。

设计约束：**纯 stdlib ctypes**，不引入 python-xlib / pywinctl；所有失败都静默
降级（返回 False），调用方据此走 UI 降级提示，绝不因为置顶失败而影响启动。
"""

import ctypes
import ctypes.util
import os
import sys

#: 关掉本模块一切原生 X11 动作的开关（只用于压测对比，不作为功能开关）。
#: ``GIANTESS_X11=0`` 时 :func:`init_x_threads` 直接返回 False——XInitThreads 的
#: 有效/无效对比压测靠它。
ENV_DISABLE = "GIANTESS_X11"


def x11_enabled() -> bool:
    """本模块的原生 X11 动作当前是否启用（平台 + 环境开关）。"""
    if not sys.platform.startswith("linux"):
        return False
    return os.environ.get(ENV_DISABLE, "").strip() not in ("0", "false", "False")


# ---------------- 加载 libX11（惰性、只一次） ----------------

_X11 = None
_LOAD_FAILED = False
_FUNCS_READY = False

#: ``XInitThreads()`` 只需要成功一次；这之后所有连接都是线程安全的。
_THREADS_INITED = {"done": False, "ok": False}


def _lib():
    """加载并声明 libX11 的函数原型；失败返回 None（并记住，不反复尝试）。"""
    global _X11, _LOAD_FAILED, _FUNCS_READY
    if _X11 is not None:
        return _X11
    if _LOAD_FAILED:
        return None
    try:
        name = ctypes.util.find_library("X11") or "libX11.so.6"
        lib = ctypes.CDLL(name)
        lib.XInitThreads.argtypes = []
        lib.XInitThreads.restype = ctypes.c_int
        lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        lib.XOpenDisplay.restype = ctypes.c_void_p
        lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        lib.XCloseDisplay.restype = ctypes.c_int
        lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        lib.XDefaultRootWindow.restype = ctypes.c_ulong
        lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        lib.XInternAtom.restype = ctypes.c_ulong
        lib.XGetWindowProperty.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long,
            ctypes.c_long, ctypes.c_int, ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong))]
        lib.XGetWindowProperty.restype = ctypes.c_int
        lib.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                                   ctypes.c_long, ctypes.c_void_p]
        lib.XSendEvent.restype = ctypes.c_int
        lib.XFlush.argtypes = [ctypes.c_void_p]
        lib.XFlush.restype = ctypes.c_int
        lib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.XSync.restype = ctypes.c_int
        lib.XQueryTree.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(
                ctypes.POINTER(ctypes.c_ulong)), ctypes.POINTER(ctypes.c_uint)]
        lib.XQueryTree.restype = ctypes.c_int
        lib.XFree.argtypes = [ctypes.c_void_p]
        lib.XFree.restype = ctypes.c_int
        lib.XGetErrorText.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                      ctypes.c_char_p, ctypes.c_int]
        lib.XGetErrorText.restype = ctypes.c_int
        lib.XSetErrorHandler.argtypes = [ctypes.c_void_p]
        lib.XSetErrorHandler.restype = ctypes.c_void_p
        _X11 = lib
        _FUNCS_READY = True
        return _X11
    except Exception as exc:  # pragma: no cover - 只在缺 libX11 的环境触发
        _LOAD_FAILED = True
        print(f"[Warning] 加载 libX11 失败，Linux/X11 原生支持关闭: {exc}")
        return None


# ---------------- 1. XInitThreads 引导 ----------------

def init_x_threads() -> bool:
    """在任何 X display 连接建立之前调用 ``XInitThreads()``。

    必须在 ``tkinter`` / ``customtkinter`` / ``dearpygui`` 首次建立连接之前执行。
    调用点放晚了 libX11 不会报错，但那时它已经按单线程模式跑起来，加锁不再生效——
    本函数**无法**检测"已经开过连接"，只能靠调用点自律（各入口文件顶部都写了注释，
    见 docs/linux.md §3）。

    非 Linux 或 ``GIANTESS_X11=0`` 时返回 False（平台不适用，不是故障）；libX11
    加载不出来时也返回 False，绝不让引导步骤挡住启动。
    """
    if not x11_enabled():
        return False
    if _THREADS_INITED["done"]:
        return _THREADS_INITED["ok"]
    lib = _lib()
    if lib is None:
        _THREADS_INITED.update(done=True, ok=False)
        return False
    try:
        ok = bool(lib.XInitThreads())
    except Exception as exc:  # pragma: no cover
        print(f"[Warning] XInitThreads 调用失败: {exc}")
        ok = False
    _THREADS_INITED.update(done=True, ok=ok)
    return ok


def threads_inited() -> bool:
    """``XInitThreads()`` 是否已经成功调用过（供诊断/文档核对）。"""
    return bool(_THREADS_INITED["done"] and _THREADS_INITED["ok"])


# ---------------- 2. EWMH 置顶 ----------------

_NET_WM_STATE = "_NET_WM_STATE"
_NET_WM_STATE_ABOVE = "_NET_WM_STATE_ABOVE"
_NET_SUPPORTED = "_NET_SUPPORTED"
_WM_CLASS = "WM_CLASS"

_STATE_REMOVE = 0
_STATE_ADD = 1
_STATE_TOGGLE = 2

_XA_ATOM = 4
#: SubstructureRedirectMask | SubstructureNotifyMask —— EWMH 规定发给 root 的掩码
_SUBSTRUCTURE_MASK = (1 << 20) | (1 << 19)
_CLIENT_MESSAGE = 33

#: 缓存：display 指针 -> {atom 名: atom}；root 窗口 -> 是否支持 _NET_WM_STATE_ABOVE
_ATOM_CACHE = {}
_SUPPORT_CACHE = {}


class XClientMessageEvent(ctypes.Structure):
    """Xlib 的 ``XClientMessageEvent``（EWMH 消息本体）。"""

    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("message_type", ctypes.c_ulong),
        ("format", ctypes.c_int),
        ("data", ctypes.c_long * 5),
    ]


class XEvent(ctypes.Union):
    """够大的 ``XEvent`` 联合体：只需前 24 个 long 的口径（64 位下 192 字节）。"""

    _fields_ = [
        ("type", ctypes.c_int),
        ("xclient", XClientMessageEvent),
        ("pad", ctypes.c_long * 24),
    ]


def open_display():
    """打开一个**自有**的 X display 连接（调用方负责 :func:`close_display`）。

    刻意不复用 Tk 内部的 Display 指针：那是 Tk 的私有实现细节，跨工具包取用
    属于未定义行为；自有连接由本模块独占，配合 XInitThreads 是线程安全的。
    """
    if not x11_enabled():
        return None
    lib = _lib()
    if lib is None:
        return None
    try:
        return lib.XOpenDisplay(None) or None
    except Exception:
        return None


def close_display(display) -> None:
    """关闭 :func:`open_display` 打开的连接（传 None 安全）。"""
    if not display:
        return
    lib = _lib()
    if lib is None:
        return
    try:
        lib.XCloseDisplay(display)
    except Exception:
        pass


def _atom(lib, display, name: str):
    """按连接缓存 InternAtom 结果。"""
    cache = _ATOM_CACHE.setdefault(display, {})
    if name not in cache:
        try:
            cache[name] = lib.XInternAtom(display, name.encode("ascii"), 0)
        except Exception:
            cache[name] = 0
    return cache[name]


def _read_atoms(lib, display, window, prop_name: str):
    """读一个 ATOM 数组属性；不存在/类型不符时返回空列表。"""
    prop = _atom(lib, display, prop_name)
    if not prop:
        return []
    actual_type = ctypes.c_ulong()
    actual_format = ctypes.c_int()
    nitems = ctypes.c_ulong()
    bytes_after = ctypes.c_ulong()
    data = ctypes.POINTER(ctypes.c_ulong)()
    try:
        status = lib.XGetWindowProperty(
            display, ctypes.c_ulong(window), ctypes.c_ulong(prop),
            ctypes.c_long(0), ctypes.c_long(4096), ctypes.c_int(0),
            ctypes.c_ulong(_XA_ATOM), ctypes.byref(actual_type),
            ctypes.byref(actual_format), ctypes.byref(nitems),
            ctypes.byref(bytes_after), ctypes.byref(data))
    except Exception:
        return []
    if status != 0 or not data:
        return []
    try:
        if actual_format.value != 32:
            return []
        return [int(data[i]) for i in range(int(nitems.value))]
    finally:
        try:
            lib.XFree(data)
        except Exception:
            pass


def supports_above(root=0, display=None) -> bool:
    """目标 root 窗口是否在 ``_NET_SUPPORTED`` 里声明支持 ``_NET_WM_STATE_ABOVE``。

    传 ``display`` 时复用调用方的连接，不传则自己开关一条（供启动期探测）。
    返回 False 的含义是「这个桌面环境不认 EWMH 置顶」——调用方应当据此把置顶
    开关置灰，而不是写一个永远不会生效的属性。
    """
    if not x11_enabled():
        return False
    lib = _lib()
    if lib is None:
        return False
    own = display is None
    if own:
        display = open_display()
        if not display:
            return False
    try:
        if not root:
            root = lib.XDefaultRootWindow(display)
        cached = _SUPPORT_CACHE.get(root)
        above = _atom(lib, display, _NET_WM_STATE_ABOVE)
        if cached is None:
            cached = above in set(_read_atoms(lib, display, root, _NET_SUPPORTED))
            _SUPPORT_CACHE[root] = cached
        return bool(cached)
    except Exception:
        return False
    finally:
        if own:
            close_display(display)


def state_above(window, enabled: bool, display=None) -> bool:
    """写 ``_NET_WM_STATE_ABOVE``（``enabled=False`` 时撤销），成功返回 True。

    ``window`` 必须是**顶层**窗口 id；调用方用 :func:`top_level_window` 从
    Tk 的 ``winfo_id()`` 上溯。写完会回读 ``_NET_WM_STATE`` 确认，避免"发了消息
    就当成功"——GNOME 这类 WM 完全可能收到消息后什么也不做。
    """
    if not x11_enabled() or not window:
        return False
    lib = _lib()
    if lib is None:
        return False
    own = display is None
    if own:
        display = open_display()
        if not display:
            return False
    try:
        root = lib.XDefaultRootWindow(display)
        event = XEvent()
        event.xclient.type = _CLIENT_MESSAGE
        event.xclient.serial = 0
        event.xclient.send_event = 1
        event.xclient.display = display
        event.xclient.window = int(window)
        event.xclient.message_type = _atom(lib, display, _NET_WM_STATE)
        event.xclient.format = 32
        event.xclient.data[0] = _STATE_ADD if enabled else _STATE_REMOVE
        event.xclient.data[1] = _atom(lib, display, _NET_WM_STATE_ABOVE)
        event.xclient.data[2] = 0
        event.xclient.data[3] = 1
        event.xclient.data[4] = 0
        lib.XSendEvent(display, ctypes.c_ulong(root), ctypes.c_int(0),
                       ctypes.c_long(_SUBSTRUCTURE_MASK), ctypes.byref(event))
        lib.XSync(display, ctypes.c_int(0))
        # WM 是异步处理的：连发两次、各回读一次，避免"消息刚发出、状态还没落"的
        # 假失败（实测偶发）。
        for attempt in range(2):
            if bool(is_above(window, display=display)) == bool(enabled):
                return True
            if attempt == 0:
                lib.XSendEvent(display, ctypes.c_ulong(root), ctypes.c_int(0),
                               ctypes.c_long(_SUBSTRUCTURE_MASK),
                               ctypes.byref(event))
                lib.XSync(display, ctypes.c_int(0))
        return bool(is_above(window, display=display)) == bool(enabled)
    except Exception:
        return False
    finally:
        if own:
            close_display(display)


def is_above(window, display=None) -> bool:
    """回读 ``_NET_WM_STATE`` 里有没有 ``_NET_WM_STATE_ABOVE``。"""
    if not x11_enabled() or not window:
        return False
    lib = _lib()
    if lib is None:
        return False
    own = display is None
    if own:
        display = open_display()
        if not display:
            return False
    try:
        above = _atom(lib, display, _NET_WM_STATE_ABOVE)
        return above in set(_read_atoms(lib, display, window, _NET_WM_STATE))
    except Exception:
        return False
    finally:
        if own:
            close_display(display)


def _query_parent(lib, display, window):
    """返回 (parent, root, children_count)；失败返回 (0, 0, -1)。

    注意 ``XQueryTree`` 返回的是 Status：**0 才是失败**（早期版本这里按
    ``!= 0`` 判失败，于是每次都能"成功"地拿到 parent，实际却把所有窗口都当成
    顶层窗口）。
    """
    root_return = ctypes.c_ulong()
    parent_return = ctypes.c_ulong()
    children = ctypes.POINTER(ctypes.c_ulong)()
    nchildren = ctypes.c_uint()
    try:
        status = lib.XQueryTree(display, ctypes.c_ulong(window),
                                ctypes.byref(root_return),
                                ctypes.byref(parent_return),
                                ctypes.byref(children),
                                ctypes.byref(nchildren))
    except Exception:
        return 0, 0, -1
    if children:
        try:
            lib.XFree(children)
        except Exception:
            pass
    if not status:
        return 0, 0, -1
    return int(parent_return.value), int(root_return.value), int(nchildren.value)


def top_level_window(window, display=None):
    """把窗口 id 上溯到顶层：X 下 Tk 的 ``winfo_id()`` 通常只是**客户区**。

    实测（GNOME Shell + XWayland）：``tk.Tk`` 的 ``winfo_id()`` 返回的 0xa00009
    只是客户端自己的子窗口，它上面还有一层（0xa0000a，``WM_CLASS = tk``）——那才是
    窗口管理器认的顶层窗口，``_NET_CLIENT_LIST`` 里登记的就是它。因此这里一路
    上溯到 **root 的直接子窗口**为止；中途任何一次查询失败就返回当前值（宁可写错
    一次也不抛异常打断界面）。
    """
    if not x11_enabled() or not window:
        return int(window or 0)
    lib = _lib()
    if lib is None:
        return int(window)
    own = display is None
    if own:
        display = open_display()
        if not display:
            return int(window)
    try:
        root = lib.XDefaultRootWindow(display)
        current = int(window)
        for _ in range(16):
            parent, _root, _kids = _query_parent(lib, display, current)
            if not parent or parent == root or parent == current:
                break
            current = parent
        return current
    except Exception:
        return int(window)
    finally:
        if own:
            close_display(display)


def window_class(window, display=None) -> str:
    """读 ``WM_CLASS`` 诊断串（``"instance\\0class\\0"``），失败返回空串。"""
    if not x11_enabled() or not window:
        return ""
    lib = _lib()
    if lib is None:
        return ""
    own = display is None
    if own:
        display = open_display()
        if not display:
            return ""
    try:
        prop = _atom(lib, display, _WM_CLASS)
        if not prop:
            return ""
        actual_type = ctypes.c_ulong()
        actual_format = ctypes.c_int()
        nitems = ctypes.c_ulong()
        bytes_after = ctypes.c_ulong()
        data = ctypes.POINTER(ctypes.c_ubyte)()
        # XA_STRING = 31
        status = lib.XGetWindowProperty(
            display, ctypes.c_ulong(window), ctypes.c_ulong(prop),
            ctypes.c_long(0), ctypes.c_long(256), ctypes.c_int(0),
            ctypes.c_ulong(31), ctypes.byref(actual_type),
            ctypes.byref(actual_format), ctypes.byref(nitems),
            ctypes.byref(bytes_after), ctypes.byref(data))
        if status != 0 or not data:
            return ""
        try:
            raw = bytes(data[i] for i in range(int(nitems.value)))
            return raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()
        finally:
            try:
                lib.XFree(data)
            except Exception:
                pass
    except Exception:
        return ""
    finally:
        if own:
            close_display(display)


def error_text(code: int) -> str:
    """X 错误码 -> 人类可读文本（用于过程日志），失败返回空串。"""
    lib = _lib()
    if lib is None:
        return ""
    display = open_display()
    if not display:
        return ""
    try:
        buf = ctypes.create_string_buffer(256)
        lib.XGetErrorText(display, ctypes.c_int(int(code)), buf,
                          ctypes.c_int(len(buf)))
        return buf.value.decode("utf-8", "replace")
    except Exception:
        return ""
    finally:
        close_display(display)
