import customtkinter as ctk

from ui.common.dialogs import BaseDialog
from ui.common import fonts as ui_fonts
from ui.common.theme import TEXT, HARD_TITLE
from datetime import date


class NewsDialog(BaseDialog):
    def __init__(self, parent, article):
        super().__init__(parent)
        self.title(date.today().strftime("%y/%m/%d"))
        self.resizable(False, False)
        self.geometry("450x220")
        self.protocol("WM_DELETE_WINDOW", self._close)

        ctk.CTkLabel(
            self, text="早报", font=ui_fonts.ui_font(16, "bold"),
            text_color=HARD_TITLE
        ).pack(anchor="w", padx=24, pady=(20, 10))
        ctk.CTkLabel(
            self, text=article.text, wraplength=400, justify="left",
            anchor="w", font=ui_fonts.ui_font(13),
            text_color=TEXT
        ).pack(fill="x", padx=24, pady=(0, 18))
        ctk.CTkButton(
            self, text="确定", width=92, height=32,
            command=self._close,
            font=ui_fonts.ui_font(12)
        ).pack(anchor="e", padx=24, pady=10)
        self.bind("<Return>", lambda _event: self._close())
        self.bind("<Escape>", lambda _event: self._close())
        self._show_modal()
