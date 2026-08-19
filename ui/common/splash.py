"""独立进程启动屏。

启动屏跑在独立的子进程里，拥有自己的 Tk 事件循环。主进程在同步构建
真实界面时，无论阻塞多久都不会影响启动屏的拖动/放大流畅度。主进程通过
双向 Pipe 与启动屏通信：发进度、请求几何信息；启动屏回报几何信息用于
原位替换。
"""

import ctypes
import os
import sys

import customtkinter as ctk

from paths import assets_dir, ensure_cwd
from ui.common.loading import LoadingPage

_DEFAULT_W = 1280
_DEFAULT_H = 720
_POLL_MS = 30


def _center(win, w=_DEFAULT_W, h=_DEFAULT_H):
    try:
        win.update_idletasks()
        x = max(0, (win.winfo_screenwidth() - w) // 2)
        y = max(0, (win.winfo_screenheight() - h) // 3)
        win.geometry(f"+{x}+{y}")
    except Exception:
        pass


def _apply_app_icon(win):
    """设置应用图标（assets/icon.ico），缺失时回退到 customtkinter 图标。"""
    if not sys.platform.startswith("win"):
        return
    try:
        icon_path = os.path.join(assets_dir(), "icon.ico")
        if not os.path.exists(icon_path):
            icon_path = os.path.join(
                os.path.dirname(ctk.__file__), "assets", "icons",
                "CustomTkinter_icon_Windows.ico")
        if os.path.exists(icon_path):
            win.iconbitmap(icon_path)
    except Exception:
        pass


def _apply_titlebar_theme(win):
    """按当前外观模式直接设置 Windows 标题栏颜色（不走 withdraw/deiconify）。"""
    if not sys.platform.startswith("win"):
        return
    try:
        dark = ctk.get_appearance_mode().lower() == "dark"
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        if not hwnd:
            return
        value = ctypes.c_int(1 if dark else 0)
        if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(value), ctypes.sizeof(value)) != 0:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 19, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


def splash_process(conn, theme_mode, color_theme, title):
    """子进程入口（multiprocessing.Process 目标）。

    协议：
    - 主进程 -> 启动屏: ("progress", value, detail)
    - 主进程 -> 启动屏: ("error", message)
    - 主进程 -> 启动屏: ("request_geometry",)
    - 主进程 -> 启动屏: None  (关闭)
    - 启动屏 -> 主进程: ("geometry", state, geometry_str)
    """
    ensure_cwd()
    ctk.set_appearance_mode(theme_mode)
    ctk.set_default_color_theme(color_theme)

    root = ctk.CTk()
    root.title("巨大娘生成器")
    root.geometry(f"{_DEFAULT_W}x{_DEFAULT_H}")
    root.minsize(640, 480)
    # 禁用 CTk 自带的 withdraw/deiconify 标题栏处理，避免出现时闪烁。
    root._deactivate_windows_window_header_manipulation = True

    def on_close():
        # 通知主进程：用户点击了关闭，主进程将中断初始化并安全退出。
        try:
            conn.send(("close_requested",))
        except Exception:
            pass
        root.quit()

    root.protocol("WM_DELETE_WINDOW", on_close)

    page = LoadingPage(root, title=title)
    page.pack(fill='both', expand=True)

    # 首次映射后再居中并应用图标与标题栏主题。
    root.after(0, lambda: (_center(root), _apply_app_icon(root), _apply_titlebar_theme(root)))

    def poll_pipe():
        try:
            while conn.poll(0):
                item = conn.recv()
                if item is None:
                    root.quit()
                    return
                kind = item[0]
                if kind == "progress":
                    page.update_progress(item[1], item[2])
                elif kind == "error":
                    page.show_error(item[1])
                elif kind == "request_geometry":
                    try:
                        conn.send(("geometry", root.state(), root.geometry()))
                    except Exception:
                        pass
        except (EOFError, OSError):
            root.quit()
            return
        except Exception:
            root.quit()
            return
        root.after(_POLL_MS, poll_pipe)

    poll_pipe()
    root.mainloop()

    try:
        conn.close()
    except Exception:
        pass
