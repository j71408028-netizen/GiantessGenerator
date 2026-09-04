"""注册地址输入对话框。

风格注册地址 = 世界观 + 该风格注册的若干级（如 ``ea1-032-2``）；
地标注册地址 = 补在风格注册级别之下的剩余级别（风格未注册时需自带世界观，
如 ``ea1-032-2-0``）。两者都允许留空（空 = 未注册 / 到处可用）。
"""

import tkinter as tk

import customtkinter as ctk

import ui.common.dialogs
from ui.common.dialogs import BaseDialog
from ui.common import fonts as ui_fonts
from ui.common.theme import (
    SOFT, TEXT, TEXT_MUTED, TEXT_DISABLED,
    BORDER_ALT, HOVER_ALT,
    STATUS_OK, OK_HOVER, PNL_BG, CLEAR_BG, CLEAR_BORDER, STATUS_ERR,
)
from address_model import validate_address_text, format_addr_verbose, world_of


class AddressTextDialog(BaseDialog):
    """输入一个注册地址文本（风格级或地标级）。"""

    def __init__(self, parent, title: str, description: str, initial: str = "",
                 allow_empty: bool = True):
        super().__init__(parent.winfo_toplevel())
        self.title(title)
        self.description = description
        self.result = None
        self._parent = parent.winfo_toplevel()
        self.transient(self._parent)
        self.grab_set()
        self.allow_empty = allow_empty

        self._create_widgets(initial)
        self.geometry("540x300")
        self._center_dialog(parent)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.wait_window()

    def _create_widgets(self, initial):
        ctk.CTkLabel(self, text=self.description, font=self.UI_FONT,
                     text_color=SOFT, justify='left', wraplength=500).pack(
            anchor='w', padx=20, pady=(16, 8))
        ctk.CTkLabel(self, text="注册地址：", font=self.UI_FONT_BOLD,
                     text_color=TEXT).pack(anchor='w', padx=20, pady=(4, 2))
        self.var = tk.StringVar(value=initial or "")
        self.entry = ctk.CTkEntry(self, textvariable=self.var, width=500, height=30,
                                  font=self.UI_FONT,
                                  fg_color=PNL_BG,
                                  border_color=BORDER_ALT)
        self.entry.pack(padx=20, pady=4)
        self.entry.bind("<KeyRelease>", self._refresh_hint)
        self.entry.bind("<<Paste>>", self._refresh_hint)

        self.status_var = tk.StringVar(value="")
        self.status_label = ctk.CTkLabel(self, textvariable=self.status_var,
                                         font=ui_fonts.ui_font(10),
                                         text_color=TEXT_MUTED, justify='left',
                                         wraplength=500, anchor='w')
        self.status_label.pack(fill='x', padx=20, pady=(2, 0))

        ctk.CTkLabel(
            self,
            text="地址格式：<规模>@<私有名!><世界观>-<一级地域>-<二级地域>-<三级地域>，例如 1e5_5e3_1e2@abc!ea1-1-2 "
                 "（@前分别为三级地域的规模（米）；@后为可选私有名，! 表示要求规模组相同）。"
                 "风格注册最上面的若干级，地标可补余下级别。空地址表示未注册（到处可用）。",
            font=ui_fonts.ui_font(10), text_color=TEXT_DISABLED,
            justify='left', wraplength=500).pack(
            anchor='w', padx=20, pady=(10, 0))

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(side='bottom', fill='x', padx=20, pady=14)
        ctk.CTkButton(btn_frame, text="确定", width=90, height=28,
                      font=self.UI_FONT, command=self._on_ok,
                      fg_color="transparent", border_width=1,
                      border_color=STATUS_OK, text_color=STATUS_OK,
                      hover_color=OK_HOVER).pack(side='left', padx=(0, 8))
        ctk.CTkButton(btn_frame, text="消除", width=90, height=28,
                      font=self.UI_FONT, command=self._clear,
                      fg_color="transparent", border_width=1,
                      border_color=CLEAR_BORDER, text_color=STATUS_ERR,
                      hover_color=CLEAR_BG).pack(side='left')
        ctk.CTkButton(btn_frame, text="取消", width=90, height=28,
                      font=self.UI_FONT, command=self._on_close,
                      fg_color="transparent", border_width=1,
                      border_color=BORDER_ALT, text_color=TEXT_MUTED,
                      hover_color=HOVER_ALT).pack(side='right')

    def _refresh_hint(self, _event=None):
        text = self.var.get().strip()
        if not text:
            self.status_var.set("未注册：到处可用（不参与地址规则）。")
            return
        err = validate_address_text(text)
        if err:
            self.status_var.set(f"格式错误：{err}")
            return
        world = world_of(text)
        if world:
            self.status_var.set(
                f"✅ 已注册（世界观 {world}）。可读位置：{format_addr_verbose(text)}")
        else:
            self.status_var.set("✅ 地址可用。")

    def _clear(self):
        self.var.set("")
        self._refresh_hint()

    def _on_ok(self):
        text = self.var.get().strip()
        if not text and not self.allow_empty:
            ui.common.dialogs.showwarning("警告", "请填写注册地址")
            return
        if text:
            err = validate_address_text(text)
            if err:
                ui.common.dialogs.showwarning("警告", f"地址格式错误：{err}")
                return
        self.result = text
        self._on_close()

    def _on_close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.withdraw()
        self.destroy()
