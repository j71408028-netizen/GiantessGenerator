import calendar
import datetime
import os
import re
import tkinter as tk
from tkinter import filedialog

import ui.common.dialogs

import customtkinter
import customtkinter as ctk
from logic import get_predefined_tags
from services.image_service import ImageService
from ui.common.widgets import CTkScrollableDropdownFrame
from ui.common.theme import (
    TEXT, SOFT, BORDER_ALT,
    PNL_BG, HOVER_ALT, MENU_HOVER, BLUE_HOVER,
    STATUS_ERR, DLG_HOVER, DLG_BORDER,
    FB_CARD_BG, FB_ACCENT, FB_TEXT, FB_BLUE, FB_CHIP_BG, FB_CHIP_HOVER,
    FB_MUTED, FB_BTN, FB_BTN_HOVER, FB_TAG_BG, FB_TAG_FG,
    FB_TAG_HOVER, FB_TAG_BORDER,
    FB_MENU_BORDER, FB_MENU_SCROLL, FB_MENU_SCROLL_HOVER, FB_MENU_TEXT,
)
from ui.common import fonts as ui_fonts


class IntroPanel(ctk.CTkFrame):
    def __init__(self, parent, params_panel, generator_panel=None):
        super().__init__(parent, fg_color=FB_CARD_BG,
                         border_width=1, border_color=DLG_BORDER,
                         corner_radius=12)
        self.params_panel = params_panel
        self.generator_panel = generator_panel

        self._intro_editing = False
        self._state_mode_image_path = None
        self.ZWSP = '\u200B'
        self.is_expanded = True
        self._avatar_ctk_image = None
        self._avatar_cache_path = None

        self.tag_hints = {
            "涩涩": "可能会不受常规束缚做过分的行动。",
            "裙装": "更爱穿裙子出现在人们面前。",
            "制服": "更爱穿制服出现在人们面前。",
            "旅行": "更倾向于靠近有名地标。",
            "地理测量": "更倾向于观察城市结构或地理区域。",
            "能躺着不站着": "更可能以躺着的姿势被记录。",
            "四处走走": "更可能以站立的姿势被记录。",
            "摄影师请就位": "满足于呈现自己的宏观身体。",
            "细节控": "满足于呈现自己的身体细节。",
            "尺寸焦虑症": "不想被抓住分析身体尺寸的机会。",
        }

        self._build_content()

    # ---------- 公开方法 ----------
    def toggle(self):
        if self.is_expanded:
            self.pack_forget()
            self.is_expanded = False
        else:
            self.pack(fill='x', padx=5, pady=(0, 5))
            self.is_expanded = True

    def refresh_display(self):
        if self._is_state_mode():
            state = self.generator_panel.current_state
            visible = state.intro_visible if state else ""
            tags = state.selected_tags if state else []
            birthday = state.birthday if state else ""
            nick = state.nick if state else ""
        else:
            _, visible, tags = self.params_panel.get_intro_data()
            birthday = self.params_panel.get_birthday()
            nick = self.params_panel.nick_entry.get().strip()
        self.nick_display_label.configure(text=nick if nick else "")
        display_text = visible if visible else "（无公开介绍）"
        if len(display_text) > 30:
            display_text = display_text[:30] + "..."
        self.intro_visible_label.configure(text=display_text)
        self.intro_tags_label.configure(text="  ".join(f"#{t}" for t in tags) if tags else "（无标签）")
        self.intro_birthday_label.configure(text=f"🎂 {birthday}" if birthday else "")

    # ---------- 形象上传 ----------
    def _upload_image(self):
        file_path = filedialog.askopenfilename(
            title="选择角色形象",
            filetypes=[("图片文件", "*.png *.jpg *.jpeg *.gif *.bmp")]
        )
        if file_path:
            from ui.common.dialogs import ImageCropDialog
            dialog = ImageCropDialog(self, file_path, mode=ImageCropDialog.MODE_CHARACTER)
            cropped = dialog.get_cropped_path()
            if cropped is None:
                return
            file_path = cropped
            if self._is_state_mode():
                self._state_mode_image_path = file_path
            else:
                self.params_panel.uploaded_image_path = file_path
                self.params_panel.update_image_status_display()
            self.refresh_image_display()

    def _delete_image(self):
        if self._is_state_mode():
            self._state_mode_image_path = None
            self.refresh_image_display()
            return
        self.params_panel.uploaded_image_path = None
        self.params_panel.update_image_status_display()

    def refresh_image_display(self):
        if self._is_state_mode() and not self._state_mode_image_path:
            self._refresh_state_image(self.generator_panel.current_state)
            return
        path = self._state_mode_image_path if self._is_state_mode() else self.params_panel.uploaded_image_path
        self._load_avatar_cached(path)

    def _load_avatar_cached(self, path):
        """按路径缓存头像，面板反复加载时不重复读盘/裁剪，避免闪烁。"""
        if path == self._avatar_cache_path and self._avatar_ctk_image is not None:
            self._apply_cached_avatar()
            return
        self._show_avatar(ImageService.load_from_path(path))
        self._avatar_cache_path = path if self._avatar_ctk_image is not None else None

    def _apply_cached_avatar(self):
        for lbl in (self.avatar_label, getattr(self, 'edit_avatar_label', None)):
            if lbl is not None:
                lbl.configure(image=self._avatar_ctk_image, text="")

    def _show_avatar(self, pil_img):
        if pil_img is not None:
            try:
                self._avatar_ctk_image = ImageService.format_avatar(pil_img)
                self._apply_cached_avatar()
            except Exception:
                self._clear_image_safe()
                self._avatar_ctk_image = None
        else:
            self._clear_image_safe()
            self._avatar_ctk_image = None

    def _clear_image_safe(self):
        for lbl in (self.avatar_label, getattr(self, 'edit_avatar_label', None)):
            if lbl is None:
                continue
            ImageService.clear_ctk_label_image(lbl)
            try:
                lbl.configure(text="👤", font=("Segoe UI", 24))
            except Exception:
                pass

    def reset_state_mode(self):
        self._state_mode_image_path = None

    def _is_state_mode(self):
        return (self.generator_panel is not None
                and self.generator_panel.current_state is not None
                and self.generator_panel.current_panel == "state")

    def _refresh_state_image(self, state):
        if not state:
            self._clear_image_safe()
            self._avatar_ctk_image = None
            self._avatar_cache_path = None
            return
        character_repo = self.generator_panel.app._character_repo
        avatar_path = state.avatar_path
        if not avatar_path:
            app = self.generator_panel.app
            context = getattr(app, "context", None)
            if context is not None and app.settings.get("use_preview_image_as_avatar", False):
                avatar_path = context.ensure_avatar_for_state(state)
                state.avatar_path = avatar_path
        if not avatar_path:
            self._clear_image_safe()
            self._avatar_ctk_image = None
            self._avatar_cache_path = None
            return
        abspath = character_repo.get_avatar_abspath(state.giantess_id, avatar_path)
        self._load_avatar_cached(abspath)

    # ---------- UI 构建 ----------
    def _build_content(self):
        accent = ctk.CTkFrame(self, height=3, fg_color=FB_ACCENT)
        accent.pack(fill='x')

        # ---------- 显示模式 ----------
        self.intro_display_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.intro_display_frame.pack(fill='x', padx=12, pady=(8, 10))

        # 头像与简介自然嵌入
        intro_card_frame = ctk.CTkFrame(self.intro_display_frame, fg_color="transparent")
        intro_card_frame.pack(fill='x', pady=(0, 6))

        self.avatar_label = ctk.CTkLabel(intro_card_frame, text="", width=56, height=56,
                                           fg_color=FB_CHIP_BG,
                                           corner_radius=28)
        self.avatar_label.pack(side='left', padx=(0, 10))

        intro_text_frame = ctk.CTkFrame(intro_card_frame, fg_color="transparent")
        intro_text_frame.pack(side='left', fill='both', expand=True)

        nick_row = ctk.CTkFrame(intro_text_frame, fg_color="transparent")
        nick_row.pack(fill='x')

        self.nick_display_label = ctk.CTkLabel(nick_row, text="",
            font=ui_fonts.ui_font(12, "bold"), text_color=FB_TEXT,
            anchor="w")
        self.nick_display_label.pack(side='left', fill='x', expand=True)

        self.save_cost_label = ctk.CTkLabel(nick_row, text="",
            font=ui_fonts.ui_font(9, "bold"), text_color=FB_BLUE,
            height=0)

        self.intro_mode_btn = ctk.CTkButton(
            nick_row, text="✏️ 编辑", font=ui_fonts.ui_font(9),
            fg_color=FB_CHIP_BG, text_color=FB_BLUE,
            hover_color=FB_CHIP_HOVER, border_width=0,
            corner_radius=14, width=50, height=24, command=self._toggle_edit
        )
        self.intro_mode_btn.pack(side='right')

        intro_row = ctk.CTkFrame(intro_text_frame, fg_color="transparent")
        intro_row.pack(fill='x')
        self.intro_visible_label = ctk.CTkLabel(intro_row, text="",
            font=ui_fonts.ui_font(10), text_color=FB_TEXT,
            anchor="w", wraplength=400, justify='left')
        self.intro_visible_label.pack(fill='x', pady=(1, 4))

        self.intro_tags_label = ctk.CTkLabel(self.intro_display_frame, text="",
            font=ui_fonts.ui_font(9), text_color=FB_BLUE,
            anchor="w", wraplength=480)
        self.intro_tags_label.pack(fill='x', pady=(1, 2))

        self.intro_birthday_label = ctk.CTkLabel(self.intro_display_frame, text="",
                                                 font=ui_fonts.ui_font(9), text_color=SOFT,
                                                 anchor="w")
        self.intro_birthday_label.pack(fill='x', pady=(1, 2))

        # ---------- 编辑模式 ----------
        self.intro_edit_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.intro_edit_frame.pack(fill='x', padx=12, pady=(8, 10))
        self.intro_edit_frame.pack_forget()

        # 滚动内容区域
        self.intro_edit_scroll = ctk.CTkFrame(self.intro_edit_frame, fg_color="transparent", height=320)
        self.intro_edit_scroll.pack(fill='x', pady=0)
        self.intro_edit_scroll.pack_propagate(False)

        # 编辑模式中的形象上传与生日（同一行）
        edit_avatar_frame = ctk.CTkFrame(self.intro_edit_scroll, fg_color="transparent")
        edit_avatar_frame.pack(fill='x', pady=(10, 6))

        self.edit_avatar_label = ctk.CTkLabel(edit_avatar_frame, text="", width=48, height=48,
                                                fg_color=FB_CHIP_BG,
                                                corner_radius=24)
        self.edit_avatar_label.pack(side='left', padx=(0, 10))

        upload_btn = ctk.CTkButton(edit_avatar_frame, text="上传形象", font=ui_fonts.ui_font(9),
                                    fg_color=FB_CHIP_BG,
                                    text_color=FB_BLUE,
                                    hover_color=FB_CHIP_HOVER, border_width=0,
                                    corner_radius=14, width=70, height=24,
                                    command=self._upload_image)
        upload_btn.pack(side='left')

        self.delete_img_btn = ctk.CTkButton(edit_avatar_frame, text="删除", font=ui_fonts.ui_font(9),
                                              fg_color="transparent",
                                              text_color=STATUS_ERR,
                                              hover_color=DLG_HOVER,
                                              border_width=1,
                                              border_color=DLG_BORDER,
                                              corner_radius=14, width=50, height=24,
                                              command=self._delete_image)
        self.delete_img_btn.pack(side='left', padx=(4, 0))

        # 生日输入（与形象编辑同一行）
        self.birthday_clear_btn = ctk.CTkButton(edit_avatar_frame, text="✕", width=24, height=24,
                                                fg_color="transparent",
                                                text_color=STATUS_ERR,
                                                hover_color=DLG_HOVER,
                                                border_width=1,
                                                border_color=DLG_BORDER,
                                                corner_radius=12,
                                                font=ui_fonts.ui_font(9),
                                                command=self._clear_birthday_edit)
        self.birthday_clear_btn.pack(side='right', padx=(4,0))
        self.birthday_edit_entry = CTkBirthdayEntry(edit_avatar_frame, width=120,
                                                     font=ui_fonts.ui_font(11),
                                                     border_width=1,
                                                     border_color=DLG_BORDER,
                                                     fg_color=FB_CARD_BG)
        self.birthday_edit_entry.pack(side='right')
        ctk.CTkLabel(edit_avatar_frame, text="生日",
                     font=ui_fonts.ui_font(9),
                     text_color=FB_MUTED).pack(side='right', padx=4)


        ctk.CTkLabel(self.intro_edit_scroll, text="公开介绍", font=ui_fonts.ui_font(9),
                     text_color=FB_MUTED).pack(anchor='w')
        self.intro_visible_entry = ctk.CTkTextbox(self.intro_edit_scroll, height=65, wrap='word',
            font=ui_fonts.ui_font(11), border_width=1, border_color=DLG_BORDER,
            fg_color=FB_CARD_BG)
        self.intro_visible_entry.pack(fill='x', pady=(1, 6))
        self.intro_visible_text = self.intro_visible_entry._textbox
        self._setup_rich_text(self.intro_visible_text)
        self.intro_visible_text.bind("<FocusIn>", lambda e: self._on_text_focus(self.intro_visible_text), add="+")

        ctk.CTkLabel(self.intro_edit_scroll, text="隐藏设定", font=ui_fonts.ui_font(9),
                     text_color=FB_MUTED).pack(anchor='w')
        self.intro_hidden_entry = ctk.CTkTextbox(self.intro_edit_scroll, height=40, wrap='word',
            font=ui_fonts.ui_font(11), border_width=1, border_color=DLG_BORDER,
            fg_color=FB_CARD_BG)
        self.intro_hidden_entry.pack(fill='x', pady=(1, 6))
        self.intro_hidden_text = self.intro_hidden_entry._textbox
        self._setup_rich_text(self.intro_hidden_text)
        self.intro_hidden_text.bind("<FocusIn>", lambda e: self._on_text_focus(self.intro_hidden_text), add="+")
        self._last_text = self.intro_visible_text

        # 标签按钮（单行横向滚动，带悬停提示）
        tag_frame = ctk.CTkScrollableFrame(self.intro_edit_scroll, orientation="horizontal",
                                            fg_color="transparent", height=32,
                                            scrollbar_button_color=BORDER_ALT,
                                            scrollbar_button_hover_color=MENU_HOVER)
        tag_frame.pack(fill='x', pady=2)
        for tag in get_predefined_tags():
            btn = ctk.CTkButton(
                tag_frame, text=tag, width=80, height=24,
                font=ui_fonts.ui_font(9),
                fg_color=FB_TAG_BG,  # 浅色/深色背景
                hover_color=FB_TAG_HOVER,  # 悬停背景
                text_color=FB_TAG_FG,  # 文字颜色
                border_width=1,
                border_color=FB_TAG_BORDER,  # 边框颜色
                corner_radius=12,
                command=lambda t=tag: self._insert_tag(t)
            )
            btn.pack(side='left', padx=3)
            btn.bind("<Enter>", lambda e, t=tag: self._show_hint(t))
            btn.bind("<Leave>", lambda e: self._hide_hint())

        self._hint_label = ctk.CTkLabel(self.intro_edit_scroll, text="",
            font=ui_fonts.ui_font(10), text_color=FB_MUTED, height=0)
        self._hint_label.pack(fill='x', pady=(0, 2))

        # 底部按钮栏（保存/取消）
        edit_actions = ctk.CTkFrame(self.intro_edit_frame, fg_color="transparent")
        edit_actions.pack(fill='x', pady=0, side='bottom')

        btn_row = ctk.CTkFrame(edit_actions, fg_color="transparent")
        btn_row.pack(fill='x')
        ctk.CTkButton(btn_row, text="✓ 保存", font=ui_fonts.ui_font(10),
fg_color=FB_BTN, text_color="white",
                hover_color=FB_BTN_HOVER, border_width=0,
            corner_radius=14, width=64, height=26, command=self._save
        ).pack(side='right', padx=(4, 0), pady=0)
        ctk.CTkButton(btn_row, text="✖ 取消", font=ui_fonts.ui_font(10),
            fg_color="transparent", text_color=STATUS_ERR,
            hover_color=DLG_HOVER,
            border_width=1, border_color=DLG_BORDER,
            corner_radius=14, width=64, height=26, command=self._cancel
        ).pack(side='right', pady=0)

        # ===== AP 消耗标签容器（位于保存按钮上方） =====
        self.cost_frame = ctk.CTkFrame(self.intro_edit_frame, fg_color="transparent")
        self.cost_frame.pack(fill='x', pady=(4, 0), side='bottom')

        self.save_cost_label = ctk.CTkLabel(
            self.cost_frame, text="",
            font=ui_fonts.ui_font(9, "bold"),
            text_color=FB_BLUE,
            anchor="w"
        )
        self.save_cost_label.pack(fill='x', side="right", padx=14)

        self.refresh_display()

    # ---------- 富文本标签 ----------
    def _configure_tag_style(self, text_widget):
        """（重新）应用富文本标签配色。主题切换后原地刷新，避免闪烁。"""
        is_dark = ctk.get_appearance_mode() == "Dark"
        tag_bg = FB_TAG_BG[1] if is_dark else FB_TAG_BG[0]
        tag_fg = FB_TAG_FG[1] if is_dark else FB_TAG_FG[0]
        text_widget.tag_configure("tag", background=tag_bg, foreground=tag_fg,
            font=ui_fonts.ui_font(11), relief="flat", borderwidth=0)

    def update_theme(self, mode=None):
        """仅更新原生 Text 标签配色，不刷新布局或头像。"""
        self._configure_tag_style(self.intro_visible_text)
        self._configure_tag_style(self.intro_hidden_text)

    def _setup_rich_text(self, text_widget):
        self._configure_tag_style(text_widget)
        text_widget.bind("<Key>", lambda e: self._on_key(text_widget, e))
        text_widget.bind("<BackSpace>", lambda e: self._on_backspace(text_widget, e))
        text_widget.bind("<Delete>", lambda e: self._on_delete(text_widget, e))

    def _on_key(self, text_widget, event):
        if event.keysym in ("Left", "Right", "Up", "Down", "Control_L", "Control_R", "Shift_L", "Shift_R"):
            return
        if self._is_inside_tag(text_widget, text_widget.index(tk.INSERT)):
            return "break"

    def _is_inside_tag(self, text_widget, pos):
        return "tag" in text_widget.tag_names(pos)

    def _get_tag_range(self, text_widget, pos):
        ranges = text_widget.tag_ranges("tag")
        for i in range(0, len(ranges), 2):
            start, end = ranges[i], ranges[i+1]
            if text_widget.compare(pos, ">=", start) and text_widget.compare(pos, "<", end):
                return start, end
        return None, None

    def _on_backspace(self, text_widget, event):
        cursor = text_widget.index(tk.INSERT)
        if self._is_inside_tag(text_widget, cursor):
            start, end = self._get_tag_range(text_widget, cursor)
            if start and end:
                text_widget.delete(start, end)
                return "break"
        prev = text_widget.index(f"{cursor} - 1c")
        if text_widget.compare(prev, ">=", "1.0") and self._is_inside_tag(text_widget, prev):
            start, end = self._get_tag_range(text_widget, prev)
            if start and end:
                text_widget.delete(start, end)
                return "break"
        return None

    def _on_delete(self, text_widget, event):
        cursor = text_widget.index(tk.INSERT)
        if self._is_inside_tag(text_widget, cursor):
            start, end = self._get_tag_range(text_widget, cursor)
            if start and end:
                text_widget.delete(start, end)
                return "break"
        nxt = text_widget.index(f"{cursor} + 1c")
        if text_widget.compare(nxt, "<=", "end-1c") and self._is_inside_tag(text_widget, nxt):
            start, end = self._get_tag_range(text_widget, nxt)
            if start and end:
                text_widget.delete(start, end)
                return "break"
        return None

    def _rebuild_tags_from_markers(self, text_widget):
        import re
        text_widget.tag_remove("tag", "1.0", "end")
        content = text_widget.get("1.0", "end-1c")
        pattern = re.compile(self.ZWSP + r'([^' + self.ZWSP + r']+)' + self.ZWSP)
        for m in pattern.finditer(content):
            start = f"1.0 + {m.start()}c"
            end = f"1.0 + {m.end()}c"
            text_widget.tag_add("tag", start, end)

    def _insert_tag(self, tag_name):
        text_widget = getattr(self, '_last_text', self.intro_visible_text)
        cursor = text_widget.index(tk.INSERT)
        prev_pos = text_widget.index(f"{cursor} - 1c")
        if text_widget.compare(prev_pos, ">=", "1.0"):
            if text_widget.get(prev_pos) == self.ZWSP:
                text_widget.insert(cursor, "/")
                cursor = text_widget.index(f"{cursor} + 1c")
        tag_with_boundary = self.ZWSP + tag_name + self.ZWSP
        text_widget.insert(cursor, tag_with_boundary)
        start = cursor
        end = text_widget.index(f"{cursor} + {len(tag_with_boundary)}c")
        text_widget.tag_add("tag", start, end)
        text_widget.mark_set(tk.INSERT, end)
        text_widget.focus_set()

    def _show_hint(self, tag):
        hint = self.tag_hints.get(tag, "（无说明）")
        self._hint_label.configure(text=hint, height=20)

    def _hide_hint(self):
        self._hint_label.configure(text="", height=0)

    def _on_text_focus(self, widget):
        self._last_text = widget

    # ---------- 编辑流程 ----------
    def _toggle_edit(self):
        if self._intro_editing:
            return
        self._intro_editing = True

        if self._is_state_mode():
            state = self.generator_panel.current_state
            hidden = state.intro_hidden or ""
            visible = state.intro_visible or ""
            tags = state.selected_tags or []
            birthday = state.birthday or ""
            self._state_mode_image_path = None
        else:
            hidden, visible, tags = self.params_panel.get_intro_data()
            birthday = self.params_panel.get_birthday()
        self.intro_visible_entry.delete("1.0", "end")
        self.intro_visible_entry.insert("1.0", visible)
        self._rebuild_tags_from_markers(self.intro_visible_text)
        self.intro_hidden_entry.delete("1.0", "end")
        self.intro_hidden_entry.insert("1.0", hidden)
        self._rebuild_tags_from_markers(self.intro_hidden_text)
        self.birthday_edit_entry.delete(0, "end")
        if birthday:
            self.birthday_edit_entry.insert(0, birthday)
        self.refresh_image_display()

        if self._is_state_mode():
            state = self.generator_panel.current_state
            # 显示当前 AP 消耗提示
            self.save_cost_label.configure(text=f"- 75 AP")
        else:
            self.save_cost_label.configure(text="")  # 非状态模式不显示

        self.intro_display_frame.pack_forget()
        self.intro_edit_frame.pack(fill='x', padx=12, pady=(0, 10))
        self.intro_mode_btn.configure(text="✏️ 编辑中")

    def _save(self):
        visible = self.intro_visible_entry.get("1.0", "end-1c").strip()
        hidden = self.intro_hidden_entry.get("1.0", "end-1c").strip()

        found_tags = set()
        for tw in (self.intro_visible_text, self.intro_hidden_text):
            ranges = tw.tag_ranges("tag")
            for i in range(0, len(ranges), 2):
                tag_content = tw.get(ranges[i], ranges[i+1]).strip(self.ZWSP)
                if tag_content:
                    found_tags.add(tag_content)
        tags = list(found_tags)

        birthday = self.birthday_edit_entry.get().strip()

        if self._is_state_mode():
            state = self.generator_panel.current_state
            if state.action_points < 75:
                ui.common.dialogs.showerror("点数不足",
                    f"保存角色信息需要75行动点数，当前仅剩{state.action_points}点。")
                return
            state.action_points -= 75
            state.intro_hidden = hidden
            state.intro_visible = visible
            state.selected_tags = tags
            state.birthday = birthday
            if self._state_mode_image_path:
                try:
                    character_repo = self.generator_panel.app._character_repo
                    state.avatar_path = character_repo.save_avatar(
                        state.giantess_id, self._state_mode_image_path,
                        low_resolution=self.generator_panel.app.settings.get("save_low_resolution_image", False)
                    )
                except Exception:
                    pass
            elif self._state_mode_image_path is None:
                pass
            character_repo = self.generator_panel.app._character_repo
            character_repo.save(state)
            self.generator_panel.state_panel.update_state(state)
            self._state_mode_image_path = None
            self._intro_editing = False
            self.intro_edit_frame.pack_forget()
            self.intro_display_frame.pack(fill='x', padx=12, pady=(8, 10))
            self.intro_mode_btn.configure(text="✏️ 编辑",
                fg_color=FB_CHIP_BG, text_color=FB_BLUE)
            self.refresh_display()
            ui.common.dialogs.showinfo("保存成功", "角色信息已更新")
        else:
            self.params_panel.set_intro_data(hidden, visible, tags)
            self.params_panel.set_birthday(birthday)

            self._intro_editing = False
            self.intro_edit_frame.pack_forget()
            self.intro_display_frame.pack(fill='x', padx=12, pady=(8, 10))
            self.intro_mode_btn.configure(text="✏️ 编辑",
                fg_color=FB_CHIP_BG, text_color=FB_BLUE)
            self.refresh_display()

    def _cancel(self):
        self._intro_editing = False
        self.intro_edit_frame.pack_forget()
        self.intro_display_frame.pack(fill='x', padx=12, pady=(8, 10))
        self.intro_mode_btn.configure(text="✏️ 编辑",
            fg_color=FB_CHIP_BG, text_color=FB_BLUE)
        self.refresh_display()

    def _clear_birthday_edit(self):
        self.birthday_edit_entry.delete(0, "end")


class CTkBirthdayEntry(customtkinter.CTkEntry):
    def __init__(self, master, start_year=2000, end_year=None, **kwargs):
        """
        紧凑型生日输入框
        """
        self.final_command = kwargs.pop("command", None)
        super().__init__(master, **kwargs)

        self.start_year = start_year
        self.end_year = end_year if end_year else datetime.datetime.now().year

        # 1. 初始化基础年份数据
        self._all_years = [str(y) for y in range(self.start_year, self.end_year + 1)]

        # 2. 实例化下拉框 (不在此处开启 autocomplete，后面通过手动追踪统一控制)
        self.dropdown = CTkScrollableDropdownFrame(
            attach=self,
            values=self._all_years,
            autocomplete=False,
            command=self._on_dropdown_select,
            frame_border_width=1,
frame_border_color=FB_MENU_BORDER,
                fg_color=PNL_BG,
                button_color=PNL_BG,
                hover_color=BLUE_HOVER,
                scrollbar_button_color=FB_MENU_SCROLL,
                scrollbar_button_hover_color=FB_MENU_SCROLL_HOVER,
                text_color=FB_MENU_TEXT
        )

        # 3. 绑定 Entry 的全局文本变化，让手动输入、删除、粘贴无死角触发更新
        self._var = customtkinter.StringVar()
        self.configure(textvariable=self._var)
        self._var.trace_add("write", self._on_text_write)

    def _safe_configure_values(self, new_values):
        """ 安全地更新下拉框的值，防止原组件中的 image_values 导致 IndexError """
        self.dropdown.image_values = None
        self.dropdown.configure(values=new_values)

        # 确保新生成的按钮被渲染
        for key in self.dropdown.widgets.keys():
            self.dropdown.widgets[key].pack(fill="x", pady=2, padx=(self.dropdown.padding, 0))

    def _on_text_write(self, *args):
        """ 当输入框文本发生任何改变时（包括手动打字、退格），强制更新下拉菜单 """
        current_val = self.get().strip()

        if current_val == "" and self.final_command:
            self.final_command("保密")

        # 允许联想逻辑运转
        self.dropdown.appear = True
        self._custom_live_update(current_val, force=True)

    def _custom_live_update(self, string=None, force=False):
        """ 重写下拉框的联想逻辑 """
        if not force and (not self.dropdown.appear or self.dropdown.disable or self.dropdown.fade):
            return

        # 隐藏/清空现有布局
        for key in self.dropdown.widgets.keys():
            self.dropdown.widgets[key].pack_forget()
        self.no_match = getattr(self.dropdown, "no_match", None)
        if self.no_match:
            self.no_match.pack_forget()

        text = (string or "").strip().replace('-', '/').replace('.', '/')
        new_values = []

        # 1. 纯数字或空
        if text.isdigit() or text == "":
            if len(text) <= 4:
                new_values = [y for y in self._all_years if y.startswith(text)]
            else:
                text = f"{text[:4]}/{text[4:]}"

        # 2. 包含 '/' 的日期阶段
        if '/' in text:
            parts = text.split('/')
            year_part = parts[0]
            month_part = parts[1] if len(parts) > 1 else ""

            if len(year_part) == 4 and year_part.isdigit() and (self.start_year <= int(year_part) <= self.end_year):
                if len(parts) <= 2:
                    # 月份阶段
                    months = [f"{year_part}/{str(m).zfill(2)}" for m in range(1, 13)]
                    new_values = [m for m in months if m.replace('/', '').startswith(text.replace('/', ''))]
                else:
                    # 日期阶段
                    try:
                        y_int, m_int = int(year_part), int(month_part)
                        if 1 <= m_int <= 12:
                            _, max_days = calendar.monthrange(y_int, m_int)
                            days = [f"{year_part}/{str(m_int).zfill(2)}/{str(d).zfill(2)}" for d in
                                    range(1, max_days + 1)]
                            new_values = [d for d in days if d.replace('/', '').startswith(text.replace('/', ''))]
                    except ValueError:
                        pass

        # 更新下拉列表按钮布局
        if not new_values:
            if self.no_match:
                self.no_match.pack(fill="x", pady=2, padx=(self.dropdown.padding, 0))
            self.dropdown.button_num = 1
        else:
            self._safe_configure_values(new_values)
            self.dropdown.button_num = len(new_values)

        # 如果输入框有焦点，保持或呼出下拉菜单
        if self.focus_get() == self:
            self.dropdown.hide = False
            self.dropdown.place_dropdown()

        self.dropdown.frame._parent_canvas.yview_moveto(0.0)
        self.dropdown.appear = False

    def _on_dropdown_select(self, value):
        """ 接管下拉框的点击事件 """
        # 暂时解除监听，防止插入数据引起联想闭环死循环
        self._var.trace_remove("write", self._var.trace_info()[0][1])

        self.delete(0, "end")

        if re.match(r"^\d{4}/\d{2}/\d{2}$", value):
            self.insert(0, value)
            if self.final_command:
                self.final_command(value)
            # 对于完整日期，不需要手动关闭，稍后会自动调用 place_forget()
        else:
            # 如果是年份或月份，自动补齐 '/'
            self.insert(0, value + "/")
            self.dropdown.appear = True
            self._custom_live_update(self.get(), force=True)

            # 避开 _attach_key_press 强制执行的 place_forget() 和 hide = True
            def reopen_dropdown():
                self.dropdown.hide = False
                self.dropdown.place_dropdown()

            self.after(10, reopen_dropdown)

        # 重新绑定监听
        self._var.trace_add("write", self._on_text_write)
