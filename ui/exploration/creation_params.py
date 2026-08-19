import json
import os
import tkinter as tk
from dataclasses import fields
from tkinter import filedialog

import ui.common.dialogs
from typing import Optional, Callable, Dict, Any

import customtkinter as ctk

from context import ExplorationContext

from models import Personality, BodyPreset
from persistence import PresetRepo, PersonalityRepo
from logic import format_size, length_unit_label
from ui.common.widgets import CTkSegmentedControl
from ui.exploration.creation_params_dlg import PersonalityCustomDialog, PresetCustomDialog
from ui.common.theme import (
    TEXT, HARD_TITLE, TITLE, SOFT, TEXT_MUTED,
    PLACEHOLDER, TEXT_DISABLED,
    PNL_BG, PNL_BORDER, BORDER_ALT, HOVER_ALT, MENU_HOVER,
    PROGRESS_BTN, PROGRESS_BTN_HOVER, SLIDER_TRACK,
    GOLD, GOLD_BTN, GOLD_BTN_HOVER,
)
from ui.common import fonts as ui_fonts


class CreationParamsPanel(ctk.CTkFrame):
    """优化版创建参数面板，适配浅色/深色模式。"""

    def __init__(
        self,
        parent,
        preset_repo: PresetRepo,
        personality_repo: PersonalityRepo,
        context: ExplorationContext,
        gui_ref,
        on_personality_changed: Optional[Callable] = None,
        on_preset_changed: Optional[Callable] = None,
        on_intro_edited: Optional[Callable] = None,
        on_image_uploaded: Optional[Callable] = None,
        on_intro_toggle: Optional[Callable] = None,
    ):
        """
        初始化面板，创建所有子控件。
        参数说明：
            parent: 父容器（通常为 left_frame）
            preset_repo: 身材仓库
            personality_repo: 性格仓库
            context: 探索上下文，提供逻辑、合并数据等
            gui_ref: 主界面实例（用于调用导出角色卡等方法）
            on_* : 可选回调
        """
        super().__init__(parent, fg_color=PNL_BG,
                         border_width=1, border_color=PNL_BORDER,
                         corner_radius=10)
        self.grid_columnconfigure(0, weight=1)
        self.parent = parent
        self.preset_repo = preset_repo
        self.personality_repo = personality_repo
        self.context = context
        self.gui_ref = gui_ref
        self.on_personality_changed = on_personality_changed
        self.on_preset_changed = on_preset_changed
        self.on_intro_edited = on_intro_edited
        self.on_image_uploaded = on_image_uploaded
        self.on_intro_toggle = on_intro_toggle

        # 从仓库加载数据
        self.body_presets = self.preset_repo.load()
        self.personalities = self.personality_repo.load()

        # ---- 内部状态变量 ----
        self.name_var = tk.StringVar(value="神秘少女")
        self.original_height_var = tk.StringVar(value="1.6")
        self.height_option = tk.StringVar(value="random")
        self.custom_height_var = tk.StringVar(value="100")
        self.greed_var = tk.IntVar(value=0)
        self.uploaded_image_path: Optional[str] = None
        self.intro_hidden = ""
        self.intro_visible = ""
        self.birthday_var = tk.StringVar(value="")
        self.selected_tags = []
        self.current_personality = None
        self.current_preset = None
        self.custom_personality: Optional[Personality] = None
        self.custom_preset: Optional[BodyPreset] = None
        self.greed_slider = None
        self.greed_label = None

        # ---- 设置世界设定（先存储值，稍后应用） ----
        self.world_setting = context.world_setting

        # ---- 构建UI ----
        self._build_name_input()
        self._build_height_input()
        self._build_greed_slider()
        self._build_personality_and_preset()

        self.update_personality_combo()
        self.update_preset_combo()

        self.update_world_setting(self.world_setting)
        self.toggle_height_options()

    # ---------- 公开方法 ----------
    def get_params(self) -> Dict[str, Any]:
        """返回当前所有参数，用于生成或导出角色卡。"""
        slider_val = self.greed_slider.get()
        # 四舍五入到5的倍数
        raw = round(slider_val / 5) * 5
        raw = max(-5, min(100, raw))
        will = raw >= 0
        greed = max(0, raw)  # 0~100
        return {
            "name": self.name_var.get().strip() or "神秘少女",
            "nick": self.nick_entry.get().strip(),
            "original_height": self.original_height_var.get(),
            "height_option": self.height_option.get(),
            "custom_height": self.custom_height_var.get(),
            "min_slider": self.min_slider.get(),
            "max_slider": self.max_slider.get(),
            "will": will,
            "greed": greed,
            "selected_personality_index": self._get_personality_index(),
            "current_personality_obj": self._get_current_personality_object(),
            "current_preset_obj": self._get_current_preset_object(),
            "intro_hidden": self.intro_hidden,
            "intro_visible": self.intro_visible,
            "selected_tags": self.selected_tags,
            "birthday": self.birthday_var.get(),
            "uploaded_image_path": self.uploaded_image_path,
        }

    def update_personality_combo(self):
        """刷新性格数据（表切换或数据变动时调用），并校正失效的当前选择。"""
        self.personalities = self.personality_repo.load()
        if self.current_personality is not None and not (
            (self.custom_personality is not None
             and self.current_personality is self.custom_personality)
            or any(p is self.current_personality for p in self.personalities)
        ):
            self.current_personality = self.custom_personality
        self._sync_personality_ui()

    def update_preset_combo(self):
        """刷新身材数据（表切换或数据变动时调用），并校正失效的当前选择。"""
        self.body_presets = self.preset_repo.load()
        if self.current_preset is not None and not (
            (self.custom_preset is not None
             and self.current_preset is self.custom_preset)
            or any(p is self.current_preset for p in self.body_presets)
        ):
            self.current_preset = self.custom_preset
        self._sync_preset_ui()

    def _height_unit(self) -> str:
        """当前模式下的输入单位标签：相对巨大化为“倍”，否则为基础长度单位。"""
        return "倍" if self.world_setting == "rel_giant" else length_unit_label()

    def update_world_setting(self, world_setting: str):
        """当世界设定变更时，更新单位标签和滑块范围显示。"""
        self.world_setting = world_setting
        text = "初始身高" if world_setting in ("abs_giant", "rel_giant") else "参照身高"
        self._ref_height_label.configure(text=text)
        self.orig_unit_label.configure(text=length_unit_label())
        self.custom_unit_label.configure(text=self._height_unit())
        self.on_slider_change()

    def toggle_height_options(self):
        self._update_mode_buttons()
        if self.height_option.get() == "custom":
            self.greed_slider.set(-5)
            self.on_greed_change(-5)
            self.greed_slider.configure(state="disabled",
                                         button_color=TEXT_DISABLED,
                                         button_hover_color=TEXT_DISABLED,
                                         progress_color=SLIDER_TRACK,
                                          fg_color=SLIDER_TRACK)
            self.greed_title_label.configure(text_color=TEXT_DISABLED)
            self.greed_display_label.configure(text_color=TEXT_DISABLED)
            self.custom_unit_label.configure(text=self._height_unit())
        else:
            self.greed_slider.configure(state="normal",
                                         button_color=GOLD_BTN,
                                         button_hover_color=GOLD_BTN_HOVER,
                                         progress_color=GOLD_BTN,
                                         fg_color=HOVER_ALT)
            self.on_greed_change(self.greed_slider.get())
            self.custom_unit_label.configure(text=self._height_unit())

    def on_greed_change(self, value):
        # 由于步长5，但滑块可能连续，四舍五入到最近的5的倍数
        val = round(value / 5) * 5
        val = max(-5, min(100, val))  # 限制范围
        if val != self.greed_slider.get():
            self.greed_slider.set(val)
        # 更新显示和配色
        if val < 0:
            self.greed_display_label.configure(text="关闭",
                                               text_color=TEXT_DISABLED)
            self.greed_title_label.configure(text_color=TEXT_DISABLED)
        else:
            self.greed_display_label.configure(text=f"意愿值: {int(val)}%",
                                               text_color=GOLD)
            self.greed_title_label.configure(text_color=GOLD)

    def _sync_personality_ui(self):
        """根据当前状态刷新性格按钮文本与描述，并触发外部回调。"""
        if self.current_personality is None:
            text, desc = "随机", ""
        elif (self.custom_personality is not None
              and self.current_personality is self.custom_personality):
            text, desc = "自定义", (self.custom_personality.description or "")
        else:
            text, desc = self.current_personality.name, (self.current_personality.description or "")
        self.personality_btn.configure(text=f"性格：{text}")
        self.personality_desc_label.configure(text=desc)
        if self.on_personality_changed:
            self.on_personality_changed()

    def _sync_preset_ui(self):
        """根据当前状态刷新身材按钮文本，并触发外部回调。"""
        if self.current_preset is None:
            text = "随机"
        elif (self.custom_preset is not None
              and self.current_preset is self.custom_preset):
            text = "自定义"
        else:
            text = self.current_preset.name
        self.preset_btn.configure(text=f"身材：{text}")
        if self.on_preset_changed:
            self.on_preset_changed()

    def on_slider_change(self, value=None):
        if not hasattr(self, 'max_slider') or not hasattr(self, 'range_label'):
            return
        if self.min_slider.get() > self.max_slider.get():
            self.min_slider.set(self.max_slider.get() - 0.1)
        min_value = 10 ** self.min_slider.get()
        max_value = 10 ** self.max_slider.get()

        def smart_fmt(v):
            if self.world_setting == "rel_giant":
                return f"{v:.2f}倍"
            return format_size(v)

        self.range_label.configure(text=f"范围: {smart_fmt(min_value)} - {smart_fmt(max_value)}")

    def update_image_status_display(self):
        if self.on_image_uploaded:
            self.on_image_uploaded()

    def get_uploaded_image_path(self) -> Optional[str]:
        return self.uploaded_image_path

    def set_uploaded_image_path(self, path: Optional[str]):
        self.uploaded_image_path = path
        self.update_image_status_display()

    def get_intro_data(self):
        return self.intro_hidden, self.intro_visible, self.selected_tags

    def set_intro_data(self, hidden: str, visible: str, tags: list):
        self.intro_hidden = hidden
        self.intro_visible = visible
        self.selected_tags = tags

    def get_birthday(self) -> str:
        return self.birthday_var.get()

    def set_birthday(self, value: str):
        self.birthday_var.set(value)

    def update_theme(self, mode=None):
        """外观模式切换时调用（当前无需要重建颜色的自绘控件）。"""

    # ---------- 内部构建方法----------
    def _build_name_input(self):
        # 章节标题 + 导出导入
        title_frame = ctk.CTkFrame(self, fg_color="transparent")
        title_frame.grid(row=0, column=0, columnspan=5, sticky='ew', padx=(14, 16), pady=(12, 4))

        ctk.CTkLabel(title_frame, text="✨ 角色邂逅",
                     font=ui_fonts.ui_font(13, "bold"),
                     text_color=TITLE).pack(side='left')

        sep = ctk.CTkFrame(
            self,
            height=3,
            corner_radius=0,
            fg_color=MENU_HOVER
        )
        sep.grid(
            row=1,
            column=0,
            columnspan=5,
            sticky="ew",
            padx=(14, 16),
            pady=(2, 4)
        )

        btn_fg = "transparent"
        btn_hover = HOVER_ALT
        btn_border = BORDER_ALT
        btn_text = TEXT_MUTED

        self.export_btn_title = ctk.CTkButton(title_frame, text="📥 导出", width=50,
                                               fg_color=btn_fg, text_color=btn_text,
                                               hover_color=btn_hover,
                                               border_width=1, border_color=btn_border,
                                               corner_radius=20, height=24,
                                               font=ui_fonts.ui_font(10),
                                               command=self._export_character_card)
        self.export_btn_title.pack(side='right', padx=(2, 0))

        self.import_btn_title = ctk.CTkButton(title_frame, text="📤 导入", width=50,
                                               fg_color=btn_fg, text_color=btn_text,
                                               hover_color=btn_hover,
                                               border_width=1, border_color=btn_border,
                                               corner_radius=20, height=24,
                                               font=ui_fonts.ui_font(10),
                                               command=self._import_character_card)
        self.import_btn_title.pack(side='right', padx=(0, 2))

        # 名字 + 昵称 + 骰子
        name_frame = ctk.CTkFrame(self, fg_color="transparent")
        name_frame.grid(row=2, column=0, columnspan=5, sticky='ew', padx=(14, 16), pady=(8, 4))
        name_frame.columnconfigure(0, weight=1)
        name_frame.columnconfigure(1, weight=1)
        name_frame.columnconfigure(2, weight=0)

        self.name_var = tk.StringVar(value="神秘少女")
        name_entry = ctk.CTkEntry(name_frame, textvariable=self.name_var,
                                  placeholder_text="名字",
                                  border_width=1,
                                  border_color=BORDER_ALT,
                                  fg_color=PNL_BG)
        name_entry.grid(row=0, column=0, sticky='ew', padx=(0, 6))

        self.nick_entry = ctk.CTkEntry(
            name_frame,
            placeholder_text="昵称",
            placeholder_text_color=PLACEHOLDER,
            border_width=1,
            border_color=BORDER_ALT,
            fg_color=PNL_BG
        )
        self.nick_entry.grid(row=0, column=1, sticky='ew', padx=(0, 6))

        self.random_btn = ctk.CTkButton(name_frame, text="🎲", width=34,
                                        fg_color="transparent",
                                        text_color=TEXT_MUTED,
                                        hover_color=HOVER_ALT,
                                        border_width=1,
                                        border_color=MENU_HOVER,
                                        command=self.random_fill_name_nick)
        self.random_btn.grid(row=0, column=2, padx=(6, 0))

    def random_fill_name_nick(self):
        name, nick = self.context.creation_service.generate_random_name_nick()
        self.name_var.set(name)
        self.nick_entry.delete(0, "end")
        if nick:
            self.nick_entry.insert(0, nick)
        height = self.context.creation_service.generate_random_height()
        self.original_height_var.set(str(height))

    def _build_height_input(self):
        # 参照身高行（行索引 3）
        height_row = ctk.CTkFrame(self, fg_color="transparent")
        height_row.grid(row=3, column=0, columnspan=5, sticky='ew', padx=(14, 16), pady=4)
        height_row.columnconfigure(0, weight=0)
        height_row.columnconfigure(1, weight=0)
        height_row.columnconfigure(2, weight=0)
        height_row.columnconfigure(3, weight=1)
        height_row.columnconfigure(4, weight=0)

        self._ref_height_label = ctk.CTkLabel(
            height_row, text="参照身高", font=ui_fonts.ui_font(10),
            text_color=TITLE
        )
        self._ref_height_label.grid(row=0, column=0, padx=(0, 6))
        self.original_height_var = tk.StringVar(value="1.6")
        ref_entry = ctk.CTkEntry(
            height_row, textvariable=self.original_height_var,
            width=48, justify='center', border_width=1,
            border_color=BORDER_ALT,
            fg_color=PNL_BG
        )
        ref_entry.grid(row=0, column=1, padx=(0, 6), sticky='w')
        self.orig_unit_label = ctk.CTkLabel(
            height_row, text=length_unit_label(), font=ui_fonts.ui_font(10),
            text_color=PLACEHOLDER
        )
        self.orig_unit_label.grid(row=0, column=2, padx=(0, 6), sticky='w')

        # 巨大化标签 + 模式切换按钮组（容器右对齐）
        self.height_option = tk.StringVar(value="random")
        mode_container = ctk.CTkFrame(height_row, fg_color="transparent")
        mode_container.grid(row=0, column=3, columnspan=2, sticky='e')
        ctk.CTkLabel(mode_container, text="巨大化", font=ui_fonts.ui_font(10),
                     text_color=TITLE).pack(side='left', padx=(0, 6))
        self._mode_segment = CTkSegmentedControl(
            mode_container,
            values=["随机", "指定"],
            command=self._set_height_mode,
            width=100, height=23, corner_radius=5,
            font=ui_fonts.ui_font(10)
        )
        self._mode_segment.pack(side='left')

        # 固定值输入行（与滑块行切换显示，固定高度）
        self.custom_frame = ctk.CTkFrame(self, fg_color="transparent", height=30)
        self.custom_frame.grid(row=4, column=0, columnspan=5, sticky='ew', padx=(14, 16), pady=(4, 2))
        self.custom_frame.grid_propagate(False)
        self.custom_frame.columnconfigure(0, weight=0)
        self.custom_frame.columnconfigure(1, weight=0)
        self.custom_frame.columnconfigure(2, weight=1)
        self.current_height_label = ctk.CTkLabel(
            self.custom_frame, text="当前身高", font=ui_fonts.ui_font(10),
            text_color=TITLE
        )
        self.current_height_label.grid(row=0, column=0, padx=(0, 6))
        self.custom_height_var = tk.StringVar(value="100")
        self.custom_height_entry = ctk.CTkEntry(
            self.custom_frame, textvariable=self.custom_height_var,
            width=50, justify='center', border_width=1,
            border_color=BORDER_ALT,
            fg_color=PNL_BG
        )
        self.custom_height_entry.grid(row=0, column=1, padx=(0, 6), sticky='w')
        self.custom_unit_label = ctk.CTkLabel(
            self.custom_frame, text=self._height_unit(), font=ui_fonts.ui_font(10),
            text_color=PLACEHOLDER
        )
        self.custom_unit_label.grid(row=0, column=2, sticky='w')

        # 滑块行（与固定值行切换显示，固定高度）
        self.range_frame = ctk.CTkFrame(self, fg_color="transparent", height=30)
        self.range_frame.grid(row=4, column=0, columnspan=5, sticky='ew', padx=(14, 16), pady=(4, 2))
        self.range_frame.grid_propagate(False)
        self._create_logarithmic_sliders()

    def _create_logarithmic_sliders(self):
        # 三列：min滑块 | 范围标签 | max滑块
        self.range_frame.columnconfigure(0, weight=1)
        self.range_frame.columnconfigure(1, weight=0)
        self.range_frame.columnconfigure(2, weight=1)

        self.min_slider = ctk.CTkSlider(
            self.range_frame, from_=1, to=5, number_of_steps=24,
                command=self.on_slider_change,
                button_color=PROGRESS_BTN,
                button_hover_color=PROGRESS_BTN_HOVER,
                progress_color=PROGRESS_BTN,
                fg_color=HOVER_ALT
        )
        self.min_slider.set(2)
        self.min_slider.grid(row=0, column=0, sticky='ew', padx=(0, 8))

        self.range_label = ctk.CTkLabel(
            self.range_frame, text="", font=("Consolas", 10),
            text_color=SOFT
        )
        self.range_label.grid(row=0, column=1, padx=8)

        self.max_slider = ctk.CTkSlider(
            self.range_frame, from_=1, to=5, number_of_steps=24,
            command=self.on_slider_change,
button_color=PROGRESS_BTN,
                button_hover_color=PROGRESS_BTN_HOVER,
                progress_color=PROGRESS_BTN,
                fg_color=HOVER_ALT
        )
        self.max_slider.set(3)
        self.max_slider.grid(row=0, column=2, sticky='ew', padx=(8, 0))

        self.on_slider_change()

    def _set_height_mode(self, label: str):
        mode = "random" if label == "随机" else "custom"
        self.height_option.set(mode)
        self.toggle_height_options()

    def _update_mode_buttons(self):
        mode = self.height_option.get()
        self._mode_segment.set("随机" if mode == "random" else "指定")
        # 切换显示（两个frame在同一行，高度固定）
        if mode == "random":
            self.custom_frame.grid_remove()
            self.range_frame.grid()
        else:
            self.range_frame.grid_remove()
            self.custom_frame.grid()

    def _build_greed_slider(self):
        self.greed_row = ctk.CTkFrame(self, fg_color="transparent", height=30)
        self.greed_row.grid(row=6, column=0, columnspan=5, sticky='ew', padx=(14, 16), pady=(2, 4))
        self.greed_row.grid_propagate(False)
        self.greed_row.columnconfigure(0, weight=0, minsize=85)
        self.greed_row.columnconfigure(1, weight=0)
        self.greed_row.columnconfigure(2, weight=1)

        self.greed_title_label = ctk.CTkLabel(
            self.greed_row, text="少女的意愿   ", font=ui_fonts.ui_font(10, "italic"),
            text_color=TEXT_DISABLED
        )
        self.greed_title_label.grid(row=0, column=0, padx=(0, 6), sticky='w')

        self.greed_slider = ctk.CTkSlider(
            self.greed_row, from_=-5, to=100, number_of_steps=21,
            width=180,
            button_color=GOLD_BTN,
            button_hover_color=GOLD_BTN_HOVER,
            progress_color=GOLD_BTN,
            fg_color=HOVER_ALT,
            command=self.on_greed_change
        )
        self.greed_slider.set(-5)
        self.greed_slider.grid(row=0, column=1, padx=(4, 8))

        self.greed_display_label = ctk.CTkLabel(
            self.greed_row, text="关闭", font=ui_fonts.ui_font(10),
            text_color=TEXT_DISABLED
        )
        self.greed_display_label.grid(row=0, column=2, sticky='e', padx=(0, 4))

    def _build_personality_and_preset(self):
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(row=8, column=0, columnspan=5, sticky='ew', padx=(14, 16), pady=(4, 0))
        row.columnconfigure(0, weight=1)
        row.columnconfigure(1, weight=1)

        btn_style = dict(
            font=ui_fonts.ui_font(10),
            fg_color="transparent", text_color=SOFT,
            hover_color=HOVER_ALT,
            border_width=1, border_color=MENU_HOVER,
            corner_radius=13,
        )

        self.personality_btn = ctk.CTkButton(row, text="性格：随机", height=26,
                                             command=self._open_personality_custom,
                                             **btn_style)
        self.personality_btn.grid(row=0, column=0, sticky='ew', padx=(0, 4))
        self.preset_btn = ctk.CTkButton(row, text="身材：随机", height=26,
                                        command=self._open_preset_custom,
                                        **btn_style)
        self.preset_btn.grid(row=0, column=1, sticky='ew', padx=(4, 0))

        desc_row = ctk.CTkFrame(self, fg_color="transparent")
        desc_row.grid(row=9, column=0, columnspan=5, sticky='ew', padx=(14, 16), pady=(1, 20))
        self.personality_desc_label = ctk.CTkLabel(
            desc_row,
            text="",
            text_color=PLACEHOLDER,
            wraplength=400,
            font=ui_fonts.ui_font(10),
            anchor="w"
        )
        self.personality_desc_label.pack(side='left', fill='x', expand=True)

    # ---------- 自定义对话框 ----------
    def _open_personality_custom(self):
        self.personalities = self.personality_repo.load()
        initial = self.custom_personality
        if initial is None and self.current_personality is not None and \
                any(p is self.current_personality for p in self.personalities):
            initial = self.current_personality
        dialog = PersonalityCustomDialog(self, self.personalities, initial=initial)
        res = dialog.result
        if res is None:
            return  # 直接关闭，无操作
        action, label, obj = res
        if action != "ok" or label == "随机":
            self.custom_personality = None
            self.current_personality = None
        else:
            self.custom_personality = obj if label == "自定义" else None
            self.current_personality = obj
        self._sync_personality_ui()

    def _open_preset_custom(self):
        self.body_presets = self.preset_repo.load()
        initial = self.custom_preset
        if initial is None and self.current_preset is not None and \
                any(p is self.current_preset for p in self.body_presets):
            initial = self.current_preset
        dialog = PresetCustomDialog(self, self.body_presets, initial=initial)
        res = dialog.result
        if res is None:
            return  # 直接关闭，无操作
        action, label, obj = res
        if action != "ok" or label == "随机":
            self.custom_preset = None
            self.current_preset = None
        else:
            self.custom_preset = obj if label == "自定义" else None
            self.current_preset = obj
        self._sync_preset_ui()

    def _get_personality_index(self) -> int:
        if self.current_personality is None:
            return -1
        for i, p in enumerate(self.personalities):
            if p == self.current_personality:
                return i
        return -1

    def _get_current_personality_object(self):
        return self.current_personality

    def _get_current_preset_object(self):
        return self.current_preset

    def _import_character_card(self):
        file_path = filedialog.askopenfilename(
            title="导入角色卡",
            filetypes=[("角色卡", "*.chara.json"), ("所有文件", "*.*")]
        )
        if not file_path:
            return
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            ui.common.dialogs.showerror("错误", f"读取文件失败：{e}")
            return

        self.name_var.set(data.get("name", ""))
        self.nick_entry.delete(0, "end")
        if data.get("nick"):
            self.nick_entry.insert(0, data["nick"])

        if "original_height" in data:
            self.original_height_var.set(str(data["original_height"]))

        version = data.get("version", "1.0")
        if version == "2.0":
            p_data = data.get("personality_data")
            pr_data = data.get("preset_data")
            if p_data:
                self.custom_personality = Personality(
                    **{k: v for k, v in p_data.items()
                       if k in {f.name for f in fields(Personality)}})
            else:
                self.custom_personality = None
            if pr_data:
                self.custom_preset = BodyPreset(
                    **{k: v for k, v in pr_data.items()
                       if k in {f.name for f in fields(BodyPreset)}})
            else:
                self.custom_preset = None
        else:
            self.custom_personality = None
            self.custom_preset = None

        self.update_personality_combo()
        self.update_preset_combo()

        self.current_personality = self.custom_personality
        self.current_preset = self.custom_preset
        self._sync_personality_ui()
        self._sync_preset_ui()

        self.intro_hidden = data.get("intro_hidden", "")
        self.intro_visible = data.get("intro_visible", "")
        self.selected_tags = data.get("tags", [])
        self.birthday_var.set(data.get("birthday", ""))

        if self.uploaded_image_path:
                try:
                    if os.path.exists(self.uploaded_image_path):
                        os.remove(self.uploaded_image_path)
                except Exception:
                    pass
                self.uploaded_image_path = None
                self.update_image_status_display()

        if self.on_personality_changed:
            self.on_personality_changed()
        if self.on_preset_changed:
            self.on_preset_changed()
        if self.on_image_uploaded:
            self.on_image_uploaded()

        ui.common.dialogs.showinfo("成功", "角色卡导入完成！")

    def _export_character_card(self):
        params = self.get_params()
        personality_obj = params["current_personality_obj"]
        preset_obj = params["current_preset_obj"]
        if personality_obj is None:
            ui.common.dialogs.showerror("错误", "请选择一个具体的性格（不能为随机）")
            return
        if preset_obj is None:
            ui.common.dialogs.showerror("错误", "请选择一个具体的身材（不能为随机）")
            return

        file_path = filedialog.asksaveasfilename(
            title="导出角色卡",
            defaultextension=".chara.json",
            filetypes=[("角色卡", "*.chara.json"), ("所有文件", "*.*")]
        )
        if not file_path:
            return

        if self.gui_ref and hasattr(self.gui_ref, 'export_character_card_to_path'):
            self.gui_ref.export_character_card_to_path(file_path)
