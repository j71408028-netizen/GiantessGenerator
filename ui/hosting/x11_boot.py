"""X11 引导：必须在任何 X display 连接建立之前 import 本模块。

单独成一个模块是为了让"尽早"这件事在入口文件里只有**一行**、且 import 顺序一眼
可查——``init_x_threads()`` 晚于 ``tkinter`` 的首次连接就没有意义了
（libX11 已经按单线程模式跑起来）。

纯 stdlib（ctypes/os/sys），不 import tkinter / imgui_bundle，因此可以在入口文件的
最顶部安全使用。

用法（入口文件顶部，仍在 tkinter 之前）::

    import ui.hosting.x11_boot  # noqa: F401  Linux/X11: 见模块 docstring

或者显式调用以拿到结果（诊断用）::

    from ui.hosting.x11_boot import boot_x11
    boot_x11()
"""

from ui.hosting.x11 import init_x_threads

__all__ = ["boot_x11"]


def boot_x11() -> bool:
    """调用 ``XInitThreads()``。非 Linux / 已调用过 / 环境关闭时返回 False。"""
    return init_x_threads()
