"""Tk / CTk 主程序侧的宿主适配器。

把 ``dungeon.window.host.HostPort`` 的端口逐一映射到 Tkinter 与
``ui.common.dialogs``：**所有 Tk 相关代码都留在本模块**，``dungeon/window/``
因此不再依赖 UI 层（``scripts/check_dungeon_layering.py`` 会守住这条线）。

用法（在 Tk 回调里）：::

    from ui.common.tk_host import TkHost
    result = DungeonSessionWindow(..., host=TkHost(self)).run()
    if result.failed:
        ui.common.dialogs.showerror("错误", result.launch_error)
"""

import ctypes
import json
import sys
from ctypes import wintypes

import ui.common.dialogs as dialogs
from dungeon.window.host import (DIALOG_ASK, DIALOG_ERROR, DIALOG_INFO,
                                 DIALOG_WARNING, HostPort)
from ui.common.fonts import dungeon_font_default

_IS_WINDOWS = sys.platform.startswith("win")

#: ``discard_pending_quit()`` 用到的 Win32 常量
_WM_QUIT = 0x0012          #: DefWindowProc(WM_DESTROY) 投递的「退出进程」消息
_PM_REMOVE = 0x0001        #: PeekMessage 取出消息（不是只看一眼）
_MAX_PENDING_QUIT_DRAIN = 16   #: 一次最多清几条（正常只会有一条）



class TkHost(HostPort):
    """宿主端口在 Tk 上的实现。``widget`` 为任意 Tk 控件（通常是调用方的父控件）。"""

    def __init__(self, widget=None):
        self.widget = widget

    # ---------------- 内部工具 ----------------
    def _root(self):
        widget = self.widget
        if widget is not None and hasattr(widget, "winfo_toplevel"):
            try:
                return widget.winfo_toplevel()
            except Exception:
                pass
        return widget

    # ---------------- 尺寸与 DPI ----------------
    def viewport_metrics(self):
        """以宿主窗口客户区为基准算视口尺寸与 DPI 缩放。

        原先这段逻辑在 ``dungeon/window/base.py`` 里（含 winfo_* 与
        ``GetAncestor``/``GetDpiForWindow`` 取客户区），迁到宿主侧后
        window 层不再有 Tk / Win32 调用。
        """
        scale = 1.0
        main_cw = main_ch = 0
        widget = self.widget
        if widget is not None and hasattr(widget, "winfo_toplevel"):
            try:
                root = widget.winfo_toplevel()
                root.update_idletasks()
                if _IS_WINDOWS:
                    hwnd = ctypes.windll.user32.GetAncestor(root.winfo_id(), 2)  # GA_ROOT
                    dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
                    if dpi:
                        scale = dpi / 96.0
                    rect = wintypes.RECT()
                    if ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rect)):
                        if rect.right > 0 and rect.bottom > 0:
                            main_cw, main_ch = rect.right, rect.bottom
                if main_cw <= 0:
                    scale = float(root.winfo_fpixels("1i")) / 96.0
                    main_cw = root.winfo_width()
                    main_ch = root.winfo_height()
            except Exception:
                pass
        elif _IS_WINDOWS:
            try:
                scale = ctypes.windll.user32.GetDpiForSystem() / 96.0
            except Exception:
                pass
        scale = max(0.5, scale)
        if main_cw <= 0 or main_ch <= 0:
            main_cw, main_ch = round(1280 * scale), round(720 * scale)
        return (main_cw + round(16 * scale), main_ch + round(39 * scale),
                scale, main_cw, main_ch)

    # ---------------- 宿主窗口显隐 ----------------
    def hide_window(self):
        widget = self.widget
        if widget is not None and hasattr(widget, "withdraw"):
            try:
                widget.withdraw()
            except Exception:
                pass

    def show_window(self):
        widget = self.widget
        if widget is None:
            return
        try:
            if hasattr(widget, "deiconify"):
                widget.deiconify()
            if hasattr(widget, "lift"):
                widget.lift()
        except Exception:
            pass

    # ---------------- 事件泵 ----------------
    def pump_events(self):
        widget = self.widget
        update = getattr(widget, "update", None)
        if not callable(update):
            return
        try:
            update()
        except Exception:
            pass

    # ---------------- 原生关闭后的残留消息 ----------------
    def discard_pending_quit(self) -> int:
        """丢掉线程消息队列里残留的 ``WM_QUIT``，返回丢弃条数。

        见 ``dungeon/window/host.py`` 同名方法与窗口文档 §5-C12：用户点副本视口的
        关闭键（X）时 GLFW 会销毁自己的原生窗口，``DefWindowProc(WM_DESTROY)``
        随之往本线程投一条 ``WM_QUIT``。这条消息不会被 Tk 取走，却会让 Windows
        停止合成 ``WM_TIMER``——于是 Tk 的 ``after`` 计时器与模态对话框的
        ``tkwait``（就是收尾提示框本身）全部拿不到事件，主窗口看起来"卡死"。
        它本来只针对那个已销毁的 GLFW 窗口，与 Tk 主循环无关，故在此丢弃。
        """
        if not sys.platform.startswith("win"):
            return 0
        removed = 0
        msg = wintypes.MSG()
        while ctypes.windll.user32.PeekMessageW(
                ctypes.byref(msg), None, _WM_QUIT, _WM_QUIT, _PM_REMOVE):
            removed += 1
            if removed >= _MAX_PENDING_QUIT_DRAIN:
                break
        return removed

    # ---------------- 弹框 ----------------
    def dialog(self, kind, title, message):
        if kind == DIALOG_ASK:
            return bool(dialogs.askyesno(title, message))
        if kind == DIALOG_WARNING:
            dialogs.showwarning(title, message)
        elif kind == DIALOG_ERROR:
            dialogs.showerror(title, message)
        elif kind == DIALOG_INFO:
            dialogs.showinfo(title, message)
        return None

    # ---------------- 回放文件选择 ----------------
    def open_replay_file(self):
        """弹出 Tk 文件选择框并读取回放记录（L4：入口页"加载回放"走这里）。

        窗口内切换回放意味着选文件时副本窗口还开着（宿主处于 hide 状态）。
        Tk 的原生文件对话框会以自己的方式置顶，调用前无需额外处理显隐。
        """
        from tkinter import filedialog
        try:
            file_path = filedialog.askopenfilename(
                parent=self._root(),
                title="选择回放文件",
                filetypes=[("副本回放", "*.replay.json"), ("所有文件", "*.*")])
        except Exception as exc:
            dialogs.showerror("错误", f"打开文件选择框失败：{exc}")
            return None
        if not file_path:
            return None
        try:
            with open(file_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception as exc:
            dialogs.showerror("错误", f"加载回放失败：{exc}")
            return None
        if not isinstance(data, list) or not data:
            dialogs.showerror("错误", "回放文件格式错误")
            return None
        return data

    # ---------------- 活动窗口登记 ----------------
    def register_active_window(self, window):
        """沿控件链找到持有 ``_active_dungeon_window`` 的主窗口并登记。

        主程序退出时要停掉正在跑的副本窗口（``main_window_manager.close``），
        原先由窗口自己沿 ``master/parent`` 链摸宿主，现在收进宿主适配器。
        """
        obj = self.widget
        while obj is not None:
            if hasattr(obj, "_active_dungeon_window") and hasattr(obj, "_closing"):
                obj._active_dungeon_window = window
                return
            obj = getattr(obj, "master", None) or getattr(obj, "parent", None)

    def unregister_active_window(self, window):
        obj = self.widget
        while obj is not None:
            if hasattr(obj, "_active_dungeon_window") and obj._active_dungeon_window is window:
                obj._active_dungeon_window = None
                return
            obj = getattr(obj, "master", None) or getattr(obj, "parent", None)

    # ---------------- 字体 ----------------
    def default_font(self) -> str:
        return dungeon_font_default()
