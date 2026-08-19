import ctypes
import os
import sys

import customtkinter as ctk

from paths import assets_dir
from ui.common import fonts as ui_fonts
from ui.common.theme import BASE, HARD_LABEL, SOFT


class LoadingPage(ctk.CTkFrame):
    """用于初始化和主题切换期间遮挡主界面的加载页。"""

    def __init__(self, parent, title="正在加载", **kwargs):
        super().__init__(parent, corner_radius=0, fg_color=BASE, **kwargs)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=0, column=0)
        self.body = body

        ctk.CTkLabel(
            body, text="巨大娘生成器", font=ui_fonts.ui_font(26, "bold"),
            text_color=HARD_LABEL
        ).pack(pady=(0, 18))
        self.title_label = ctk.CTkLabel(
            body, text=title, font=ui_fonts.ui_font(16),
            text_color=SOFT
        )
        self.title_label.pack(pady=(0, 10))
        self.progress = ctk.CTkProgressBar(body, width=360, height=10, corner_radius=5)
        self.progress.pack()
        self.progress.set(0)
        self.detail_label = ctk.CTkLabel(
            body, text="", font=ui_fonts.ui_font(13),
            text_color=SOFT
        )
        self.detail_label.pack(pady=(10, 0))

    def update_progress(self, value, detail=None):
        self.progress.set(max(0, min(1, value)))
        if detail is not None:
            self.detail_label.configure(text=detail)
        self.update_idletasks()

    def show_error(self, message, title="初始化失败"):
        """错误状态：移除进度条与详情，改为可滚动的错误信息区域。"""
        if getattr(self, "body", None) is not None:
            self.body.destroy()
            self.body = None
        if getattr(self, "_error_box", None) is None:
            self.grid_rowconfigure(0, weight=0)
            self.grid_rowconfigure(1, weight=1)
            self.grid_rowconfigure(2, weight=0)
            self._error_title = ctk.CTkLabel(
                self, text=title, font=ui_fonts.ui_font(20, "bold"),
                text_color=HARD_LABEL)
            self._error_title.grid(row=0, column=0, pady=(48, 12))
            self._error_box = ctk.CTkTextbox(
                self, wrap="word", font=ui_fonts.ui_font(13),
                fg_color=BASE, text_color=HARD_LABEL)
            self._error_box.grid(
                row=1, column=0, sticky="nsew", padx=48, pady=(0, 12))
            self._error_hint = ctk.CTkLabel(
                self, text="初始化失败，可关闭本窗口退出程序。",
                font=ui_fonts.ui_font(12), text_color=SOFT)
            self._error_hint.grid(row=2, column=0, pady=(0, 16))
        else:
            self._error_title.configure(text=title)
        if not message:
            message = "未知错误，请查看控制台日志。"
        self._error_box.configure(state="normal")
        self._error_box.delete("1.0", "end")
        self._error_box.insert("1.0", message)
        self._error_box.configure(state="disabled")
        self.update_idletasks()


class LoadingWindow(ctk.CTkToplevel):
    """启动初始化期间的轻量加载窗口。

    与真实主窗口相互独立：初始化期间真实主窗口保持隐藏、不参与拖动/缩放
    事件，由本窗口负责呈现基础加载进度。窗口本体内容轻量，拖动与放大流畅，
    加载完成后其被拖动/缩放后的几何信息会被交给主窗口，实现原位替换。
    """

    def __init__(self, title="正在加载", on_close=None, **kwargs):
        # 禁用 CTkToplevel 自带的 withdraw/deiconify 标题栏处理，避免加载窗口
        # 首次出现时因切换标题栏颜色而闪烁。
        self._deactivate_windows_window_header_manipulation = True
        super().__init__(**kwargs)
        self.title("巨大娘生成器")
        self.geometry("1280x720")
        self.minsize(640, 480)
        self._on_close = on_close
        # 点击关闭：立即销毁本窗口并通知调用方中断初始化，安全退出。
        self.protocol("WM_DELETE_WINDOW", self._handle_close)

    def _handle_close(self):
        callback = self._on_close
        try:
            self.destroy()
        except Exception:
            pass
        if callback is not None:
            callback()

        self.page = LoadingPage(self, title=title)
        self.page.pack(fill='both', expand=True)

        # 首次映射后再居中并应用图标与标题栏主题。
        self.after(0, self._on_first_map)

    def _on_first_map(self):
        self._center_on_screen()
        self._apply_app_icon()
        self._apply_titlebar_theme()

    def _center_on_screen(self):
        try:
            self.update_idletasks()
            w = self.winfo_width() or 1280
            h = self.winfo_height() or 720
            x = max(0, (self.winfo_screenwidth() - w) // 2)
            y = max(0, (self.winfo_screenheight() - h) // 3)
            self.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def update_progress(self, value, detail=None):
        self.page.update_progress(value, detail)

    def show_error(self, message, title="初始化失败"):
        self.page.show_error(message, title)

    def _apply_app_icon(self):
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
                self.iconbitmap(icon_path)
        except Exception:
            pass

    def _apply_titlebar_theme(self):
        """按当前外观模式直接设置 Windows 标题栏颜色（不走 withdraw/deiconify）。"""
        if not sys.platform.startswith("win"):
            return
        try:
            dark = ctk.get_appearance_mode().lower() == "dark"
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            if not hwnd:
                return
            value = ctypes.c_int(1 if dark else 0)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 20, ctypes.byref(value), ctypes.sizeof(value)) != 0:
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 19, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:
            pass
