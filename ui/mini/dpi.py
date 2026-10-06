"""挂件窗口的 DPI 策略：让 Windows 像「独立挂件版」那样缩放本窗口。

ME 模式下从不调用 ``SetProcessDpiAwareness``，进程是
**DPI 非感知**的，Windows 于是按系统缩放把窗口位图拉伸。挂件这套界面本来就按
96 DPI 写死像素（``ui.mini.pixel`` 的负数像素字号 + 各处像素尺寸），被拉伸之后
大小正好。

从专业模式热切换过来时情况不同：``customtkinter`` 在建根窗口时会把进程设成
per-monitor 感知（``ScalingTracker.activate_high_dpi_awareness``），窗口不再被
拉伸，同一个 ``360x620`` 的挂件窗口就只剩一半大——使用者看到的是「切过去的挂件
没有应用 DPI 缩放」。实测（2026-09-28，系统 192 DPI / 200% 缩放）：

    独立挂件版 / 线程非感知： ``360x620`` 的窗口占 **752x1318** 物理像素
    专业模式切过来 / 线程感知： 同一个窗口占 **386x691** 物理像素

进程的 DPI 感知一经设定就改不回去，但 Win10 1607+ 的
``SetThreadDpiAwarenessContext`` 可以**按线程**覆盖，且优先级高于进程默认值。
Tk 的窗口、消息循环与全部界面代码都在主线程上，因此挂件存续期间把线程切回非感知
即可复现独立挂件版的表现；挂件退出时**必须还原**，否则接着重建的专业界面也会变成
非感知（CTk 只设「进程默认值」，动不了已被显式指定的线程上下文）。

非 Windows 平台本模块全是空操作（macOS 由 Tk 自己处理高 DPI，Linux 无此机制）。
"""

import contextlib
import ctypes
import sys

#: ``DPI_AWARENESS_CONTEXT_UNAWARE``：等价于「进程从未声明过 DPI 感知」
_DPI_AWARENESS_CONTEXT_UNAWARE = -1

#: ``GetAwarenessFromDpiAwarenessContext`` 的返回值：0 非感知 / 1 系统感知 / 2 每显示器
_AWARENESS_UNAWARE = 0


def _user32():
    """取 user32 并固定两个函数的签名；系统太老（无此 API）时返回 None。"""
    if not sys.platform.startswith("win"):
        return None
    try:
        user32 = ctypes.windll.user32
        user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.GetAwarenessFromDpiAwarenessContext.restype = ctypes.c_int
        user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        return user32
    except Exception:
        return None


def current_awareness():
    """当前线程的 DPI 感知：0 非感知 / 1 系统感知 / 2 每显示器感知。

    取不到（非 Windows 或系统太老）时返回 None。挂件建窗时这里应当是 0，
    自检用它守住这条不变量。
    """
    user32 = _user32()
    if user32 is None:
        return None
    try:
        return user32.GetAwarenessFromDpiAwarenessContext(
            user32.GetThreadDpiAwarenessContext())
    except Exception:
        return None


def enter_virtualized() -> object:
    """把本线程切成 DPI 非感知；返回用于还原的上下文（无需还原时返回 None）。

    已经是非感知（独立挂件版那条路）时直接返回 None，什么都不用做。
    """
    user32 = _user32()
    if user32 is None:
        return None
    try:
        current = user32.GetThreadDpiAwarenessContext()
        if user32.GetAwarenessFromDpiAwarenessContext(current) == _AWARENESS_UNAWARE:
            return None
        previous = user32.SetThreadDpiAwarenessContext(
            ctypes.c_void_p(_DPI_AWARENESS_CONTEXT_UNAWARE))
        return previous or None
    except Exception:
        return None


def leave_virtualized(token) -> None:
    """还原 :func:`enter_virtualized` 改掉的线程上下文（``token`` 为 None 时不动）。"""
    if token is None:
        return
    user32 = _user32()
    if user32 is None:
        return
    try:
        user32.SetThreadDpiAwarenessContext(token)
    except Exception:
        pass


@contextlib.contextmanager
def virtualized_dpi():
    """挂件存续期间把本线程切成 DPI 非感知，退出时还原。

    用法::

        with virtualized_dpi():
            root = tk.Tk()
            MiniApp(root, ...)
            root.mainloop()
    """
    token = enter_virtualized()
    try:
        yield
    finally:
        leave_virtualized(token)
