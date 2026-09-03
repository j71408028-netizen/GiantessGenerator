import os
import random
import uuid
import tkinter as tk
import tkinter.font as tkfont
from typing import Dict, Callable, Optional, TYPE_CHECKING
from ui.settings_dlg import AIConfigDialog, WorldPackCreateDialog

import customtkinter as ctk

import ui.common.dialogs

if TYPE_CHECKING:
    from persistence.settings_repo import SettingsRepo
    from persistence.landmark_repo import LandmarkRepo
    from persistence.quip_repo import QuipRepo

from logic import ALL_PART_NAMES, normalize_blocked_words
from address_model import world_of
from persistence import PresetRepo, PersonalityRepo
from persistence.name_repo import NameRepo, DEFAULT_NAME_TABLE
from persistence.world_pack import list_behavior_packs
from services.challenge_service import ChallengeService
from services.news_service import DEFAULT_NEWS_TABLE, NewsService
from ui.common.widgets import CollapsibleBlock, StyleListBox, CTkScrollableDropdownFrame
from ui.common import fonts as ui_fonts
from ui.common.theme import (
    HOVER,
    HARD_LABEL, SOFT, CHECKBOX_HOVER,
    PNL_BG, PNL_BORDER,
    INPUT_BG, INPUT_BORDER, INPUT_HOVER,
    MENU_BTN, MENU_BTN_HOVER,
    SWC_FG, SWC_PROGRESS, SWC_BTN,
    STATUS_OK, STATUS_ERR,
)

_FONT = ui_fonts.ui_font(12)

# 信息更新覆写概率四档：显示名 → 覆写概率
INFO_UPDATE_OPTIONS = {"保守": 0.25, "中等": 0.5, "高效": 0.75, "激进": 1.0}



class SettingsPanel(ctk.CTkScrollableFrame):
    def __init__(
            self,
            parent,
            settings_repo: 'SettingsRepo',
            landmark_repo: 'LandmarkRepo',
            quip_repo: 'QuipRepo',
            initial_settings: Dict,
            on_styles_changed: Callable,
            on_world_setting_changed: Optional[Callable] = None,
            on_name_table_changed: Optional[Callable] = None,
            on_news_table_changed: Optional[Callable] = None,
            on_preset_table_changed: Optional[Callable] = None,
            on_personality_table_changed: Optional[Callable] = None,
            on_return_callback: Optional[Callable] = None,
            gui_ref=None,
            world_manager=None,
            name_repo: Optional[NameRepo] = None,
            preset_repo=None,
            personality_repo=None,
            **kwargs
    ):
        super().__init__(parent, fg_color="transparent", **kwargs)
        self.settings_repo = settings_repo
        self.landmark_repo = landmark_repo
        self.quip_repo = quip_repo
        self.settings = initial_settings
        self.on_styles_changed = on_styles_changed
        self.on_return_callback = on_return_callback
        self.on_world_setting_changed = on_world_setting_changed
        self.on_name_table_changed = on_name_table_changed
        self.on_news_table_changed = on_news_table_changed
        self.on_preset_table_changed = on_preset_table_changed
        self.on_personality_table_changed = on_personality_table_changed
        self.gui_ref = gui_ref
        self.world_manager = world_manager
        self.world_state = getattr(world_manager, "world_state", None) \
            if world_manager is not None else None
        self.name_repo = name_repo or NameRepo(world_state=self.world_state)
        self.preset_repo = preset_repo or PresetRepo(world_state=self.world_state)
        self.personality_repo = personality_repo or PersonalityRepo(world_state=self.world_state)
        self.parts_checkboxes = {}

        self.theme_mode = self.settings.get("theme_mode", "Light")
        self.user_seed = self.settings.get("seed", 0)
        self.comparison_count = self.settings.get("comparison_count", 5)
        self.comparison_order = self.settings.get("comparison_order", "match")
        self.world_setting = self.settings.get("world_setting", "appear")
        self.name_table = self.settings.get("name_table", DEFAULT_NAME_TABLE)
        self.news_table = self.settings.get("news_table", DEFAULT_NEWS_TABLE)
        self.preset_table = self.settings.get("preset_table", "default")
        self.personality_table = self.settings.get("personality_table", "default")
        self.selected_styles = self.settings.get("selected_styles", ["ChineseMix"])
        self.selected_quip_styles = self.settings.get("selected_quip_styles", [])
        self.ai_configs = self._load_ai_configs()
        self.ai_provider = self.settings.get("ai_provider", "zhipu")
        if self.ai_provider not in self.ai_configs:
            self.ai_provider = next(iter(self.ai_configs), "")
        self.enable_confusion = self.settings.get("enable_confusion", False)
        self.quip_rate_factor = self.settings.get("quip_rate_factor", 1.0)
        self.info_update_rate = self.settings.get("info_update_rate", 0.5)
        self.blocked_words = normalize_blocked_words(self.settings.get("blocked_words", []))

        self._create_widgets()
        self._sync_all_states()
        # System 模式的实际颜色由 CustomTkinter 决定，原生 Listbox 也必须使用
        # 这个实际模式，否则首次打开设置页可能短暂显示浅色。
        actual_mode = ctk.get_appearance_mode()
        self.landmark_selector.apply_theme(actual_mode)
        self.quip_selector.apply_theme(actual_mode)

    # ───────────────────── UI 构建 ─────────────────────
    def _create_widgets(self):
        # 不对称左右两栏布局
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.pack(fill='both', expand=True, padx=10, pady=(4,8))
        self.content.columnconfigure(0, weight=8, uniform="cols")
        self.content.columnconfigure(1, weight=7, uniform="cols")
        self.content.rowconfigure(0, weight=1)

        # 左右卡片：统一背景 + 圆角框线，sticky nsew 保证向下填满
        self.left_col = ctk.CTkFrame(
            self.content, fg_color=PNL_BG,
            border_width=1, border_color=PNL_BORDER, corner_radius=10)
        self.left_col.grid(row=0, column=0, sticky='nsew', padx=5)
        self.right_col = ctk.CTkFrame(
            self.content, fg_color=PNL_BG,
            border_width=1, border_color=PNL_BORDER, corner_radius=10)
        self.right_col.grid(row=0, column=1, sticky='nsew', padx=(12,0))

        self.world_block = CollapsibleBlock(self.left_col, "世界", body_padx=(6,12), body_pady=(6,12),
                                            on_toggle=self._schedule_fit)
        self.gen_block = CollapsibleBlock(self.left_col, "生成", body_padx=(6, 12), body_pady=(6, 12),
                                          on_toggle=self._schedule_fit)

        self.world_header = self.world_block.header
        self.world_body = self.world_block.body
        self.gen_header = self.gen_block.header
        self.gen_body = self.gen_block.body

        self.disp_block = CollapsibleBlock(self.right_col, "显示", body_padx=(6, 12), body_pady=(6, 12),
                                           on_toggle=self._schedule_fit)
        self.archive_block = CollapsibleBlock(self.right_col, "档案", body_padx=(6, 12), body_pady=(15, 12),
                                              on_toggle=self._schedule_fit)

        self.disp_header = self.disp_block.header
        self.disp_body = self.disp_block.body
        self.archive_header = self.archive_block.header
        self.archive_body = self.archive_block.body

        # 折叠块内控件透明
        for block in [self.world_block, self.disp_block, self.archive_block, self.gen_block]:
            block.body.configure(fg_color="transparent")

        for header, col in [(self.world_header, self.left_col),
                            (self.disp_header, self.left_col),
                            (self.archive_header, self.left_col),
                            (self.gen_header, self.right_col)]:
            header.pack(fill='x', padx=(8,10), pady=(10, 0))

        # 在 header pack 之后 pack body
        # 注意：必须与 CollapsibleBlock.toggle() 使用相同的 padx/body_padx，
        # 否则首次加载与收/展后内容左端间距不一致，造成整块横向跳动。
        for block in [self.world_block, self.disp_block, self.archive_block, self.gen_block]:
            if block._expanded:
                block.body.pack(fill='x', padx=block._body_padx,
                                pady=block._body_pady, after=block.header)

        self._build_world_block()
        self._build_generation_block()
        self._build_display_block()
        self._build_archive_block()

        # 内容不足视口高度时，把内部 Frame 撑到视口高度，使左右栏向下填满。
        # pack_propagate(False)：固定内框高度，避免 packer 与 canvas 窗口项
        # 在 <Configure> 里互相把高度改回来，形成无限循环导致界面卡死。
        self.pack_propagate(False)
        self.bind("<Configure>", self._schedule_fit, add="+")
        self._parent_canvas.bind("<Configure>", self._schedule_fit, add="+")

    def _schedule_fit(self, event=None):
        """收/展折叠块或视口变化后，延迟一帧再适配高度：
        此刻 pack_forget 的几何传播已生效，winfo_reqheight 才不会读到旧值。"""
        self.after(20, self._fit_content_height)

    def _fit_content_height(self, event=None):
        try:
            view_height = self._parent_canvas.winfo_height()
            if view_height <= 0:
                return
            content_height = self.content.winfo_reqheight()
            if content_height < view_height:
                # 内容不足视口高度：把 canvas 窗口项撑到视口高度，左右栏向下填满
                self._parent_canvas.itemconfigure(self._create_window_id, height=view_height)
            else:
                # 内容超出一屏：恢复自然高度，允许滚动
                self._parent_canvas.itemconfigure(self._create_window_id, height=0)
        except Exception:
            pass

    def scroll_to_style_section(self):
        if not self.world_body.winfo_ismapped():
            self.world_block.toggle()
            self.update_idletasks()

        if hasattr(self, '_parent_frame') and hasattr(self, '_parent_canvas'):
            total_height = self._parent_frame.winfo_reqheight()
            if total_height <= 0:
                return
            y = self.landmark_selector.border.winfo_y()
            fraction = max(0.0, min(1.0, (y - 10) / total_height))
            self._parent_canvas.yview_moveto(fraction)

    # ───────────────── 通用控件辅助 ─────────────────
    @staticmethod
    def _setup_body_grid(body, name):
        """块体内统一两列：0=标签列，1=控件列，跨行对齐。"""
        body.columnconfigure(0, weight=2, uniform=name + "_cols")
        body.columnconfigure(1, weight=3, uniform=name + "_cols")

    def _section_label(self, parent, row, text):
        ctk.CTkLabel(
            parent, text=text,
            font=ui_fonts.ui_font(12, "bold"),
            text_color=SOFT
        ).grid(row=row, column=0, columnspan=2, sticky='w',
               padx=10, pady=6)

    def _make_row(self, parent, row, text):
        """创建一行：标签在左，控件容器在右，两列对齐。"""
        label = ctk.CTkLabel(
            parent, text=text, anchor='w',
            font=_FONT, text_color=HARD_LABEL
        )
        label.grid(row=row, column=0, sticky='w', padx=(18, 12), pady=(0, 8))
        control = ctk.CTkFrame(parent, fg_color="transparent")
        control.grid(row=row, column=1, sticky='e', padx=(0, 18), pady=(0, 8))
        return label, control

    def _make_option_menu(self, parent, values, command, variable=None, expand=True):
        menu = ctk.CTkOptionMenu(
            parent,
            values=values, command=command, variable=variable,
            width=120, height=27, corner_radius=7,
            fg_color=INPUT_BG, button_color=MENU_BTN,
            button_hover_color=MENU_BTN_HOVER,
            text_color=HARD_LABEL, font=_FONT,
            dropdown_fg_color=INPUT_BG,
            dropdown_hover_color=INPUT_HOVER,
            dropdown_text_color=HARD_LABEL,
            dropdown_font=_FONT
        )
        if expand:
            menu.pack(fill='both', expand=True)
        else:
            menu.pack(side='left')

        # 下拉列表使用统一的 CTkScrollableDropdownFrame
        dropdown = CTkScrollableDropdownFrame(
            attach=menu, values=values, command=command,
            height=100, button_height=30,
            fg_color=INPUT_BG, button_color=INPUT_BG,
            hover_color=INPUT_HOVER, text_color=HARD_LABEL,
            scrollbar_button_color=INPUT_HOVER,
            scrollbar_button_hover_color=INPUT_HOVER,
            frame_border_color=INPUT_BORDER,
            frame_border_width=1, justify="left", font=_FONT
        )
        menu._profile_dropdown = dropdown
        return menu

    def _make_entry(self, parent, variable, width=120, show=None, placeholder_text=None):
        return ctk.CTkEntry(
            parent, textvariable=variable, width=width, height=28, show=show,
            placeholder_text=placeholder_text or "",
            border_width=1, border_color=INPUT_BORDER,
            fg_color=INPUT_BG, corner_radius=8, font=_FONT
        )

    def _make_switch(self, parent, variable):
        switch = ctk.CTkSwitch(
            parent, text="", variable=variable, width=44, height=22,
            progress_color=SWC_PROGRESS, fg_color=SWC_FG,
            button_color=SWC_BTN, button_hover_color=SWC_BTN
        )
        switch.pack(side='left')
        return switch

    # ───────────────── 各区块构建 ─────────────────
    def _build_world_block(self):
        self._setup_body_grid(self.world_body, "world")
        row = 0

        # ── 规则 ──
        self._section_label(self.world_body, row, "规则")
        row += 1

        _, ctrl = self._make_row(self.world_body, row, "体型设定")
        row += 1
        world_options = {"出现": "appear", "绝对巨大化": "abs_giant", "相对巨大化": "rel_giant"}
        self.world_setting_var = tk.StringVar(value=self.world_setting)
        self.world_setting_menu = self._make_option_menu(
            ctrl, list(world_options.keys()),
            self._on_world_option_changed, self.world_setting_var
        )
        for display, value in world_options.items():
            if value == self.world_setting:
                self.world_setting_var.set(display)
                break

        _, ctrl = self._make_row(self.world_body, row, "种子")
        row += 1
        self.seed_var = tk.StringVar(value=str(self.user_seed) if self.user_seed != 0 else "")
        self.seed_entry = self._make_entry(ctrl, self.seed_var, width=100)
        self.seed_entry.pack(side='left')
        self.seed_random_btn = ctk.CTkButton(
            ctrl, text="随机", width=60, height=28,
            command=self._randomize_seed,
            fg_color="transparent", text_color=SOFT,
            hover_color=HOVER,
            border_width=1, border_color=INPUT_BORDER,
            corner_radius=8, font=ui_fonts.ui_font(11)
        )
        self.seed_random_btn.pack(side='left', padx=(8,0))

        # ── 世界资源 ──
        self._section_label(self.world_body, row, "世界资源")
        row += 1

        _, ctrl = self._make_row(self.world_body, row, "姓名表")
        row += 1
        self.name_table_var = tk.StringVar(value=self.name_table)
        name_tables = self.name_repo.get_tables()
        if self.name_table not in name_tables:
            name_tables.insert(0, self.name_table)
        self.name_table_menu = self._make_option_menu(
            ctrl, name_tables,
            self._on_name_table_changed, self.name_table_var
        )

        _, ctrl = self._make_row(self.world_body, row, "新闻表")
        row += 1
        self.news_table_var = tk.StringVar(value=self.news_table)
        news_service = NewsService(news_table=self.news_table,
                                    world_state=self.world_state)
        news_tables = news_service.get_tables()
        if self.news_table not in news_tables:
            news_tables.insert(0, self.news_table)
        if not news_tables:
            news_tables = [DEFAULT_NEWS_TABLE]
        self.news_table_menu = self._make_option_menu(
            ctrl, news_tables,
            self._on_news_table_changed, self.news_table_var
        )
        row += 1

        _, ctrl = self._make_row(self.world_body, row, "身材表")
        row += 1
        self.preset_table_var = tk.StringVar(value=self.preset_table)
        preset_tables = self.preset_repo.get_tables()
        if self.preset_table not in preset_tables:
            preset_tables.insert(0, self.preset_table)
        if not preset_tables:
            preset_tables = ["default"]
        self.preset_table_menu = self._make_option_menu(
            ctrl, preset_tables,
            self._on_preset_table_changed, self.preset_table_var
        )

        _, ctrl = self._make_row(self.world_body, row, "性格表")
        row += 1
        self.personality_table_var = tk.StringVar(value=self.personality_table)
        personality_tables = self.personality_repo.get_tables()
        if self.personality_table not in personality_tables:
            personality_tables.insert(0, self.personality_table)
        if not personality_tables:
            personality_tables = ["default"]
        self.personality_table_menu = self._make_option_menu(
            ctrl, personality_tables,
            self._on_personality_table_changed, self.personality_table_var
        )
        row += 1

        # 地标/描述风格多选（世界资源段内，占整行）
        styles_row = ctk.CTkFrame(self.world_body, fg_color="transparent")
        styles_row.grid(row=row, column=0, columnspan=2, sticky='ew',
                        padx=18, pady=(10, 6))
        styles_row.columnconfigure(0, weight=1, uniform="world_style")
        styles_row.columnconfigure(1, weight=1, uniform="world_style")
        row += 1

        self.landmark_selector = StyleListBox(styles_row, "地标风格组", height=2,
                                              on_change=self._on_landmark_selection_changed)
        self.landmark_selector.grid(row=0, column=0, sticky='nsew', padx=(0, 10))
        self.landmark_selector.add_button("默认", self.landmark_selector.set_default, padx=0)
        self.landmark_selector.add_button("全选", self.landmark_selector.select_all, padx=10)

        self.quip_selector = StyleListBox(styles_row, "描述风格组", height=2,
                                          on_change=self._on_quip_selection_changed)
        self.quip_selector.grid(row=0, column=1, sticky='nsew', padx=(10, 0))
        self.quip_selector.add_button("清空", self.quip_selector.clear_selection, padx=0)
        self.quip_selector.add_button("全选", self.quip_selector.select_all, padx=10)

        # ── 包设置 ──
        self._section_label(self.world_body, row, "包设置")
        row += 1

        # 第1行：世界包
        self.world_pack_label, ctrl = self._make_row(self.world_body, row, "世界包")
        row += 1

        self.world_pack_idle_frame = ctk.CTkFrame(ctrl, fg_color="transparent")
        self.world_pack_idle_frame.pack(side='left')
        self.world_pack_var = tk.StringVar(value="")
        self.world_pack_menu = self._make_option_menu(
            self.world_pack_idle_frame, [],
            self._on_world_pack_changed, self.world_pack_var, expand=False
        )
        self.world_pack_new_btn = ctk.CTkButton(
            self.world_pack_idle_frame, text="新建", width=60, height=28,
            command=self._create_world_pack, fg_color="transparent",
            text_color=SOFT, hover_color=HOVER,
            border_width=1, border_color=INPUT_BORDER,
            corner_radius=8, font=ui_fonts.ui_font(11)
        )
        self.world_pack_new_btn.pack(side='left', padx=(8, 0))

        self.world_pack_active_frame = ctk.CTkFrame(ctrl, fg_color="transparent")
        self.world_pack_active_label = ctk.CTkLabel(
            self.world_pack_active_frame, text="", anchor='w',
            font=_FONT, text_color=STATUS_OK)
        self.world_pack_active_label.pack(side='left')
        self.world_pack_dissolve_btn = ctk.CTkButton(
            self.world_pack_active_frame, text="删除", width=60, height=28,
            command=self._dissolve_world_pack, fg_color="transparent",
            text_color=STATUS_ERR, hover_color=HOVER,
            border_width=1, border_color=INPUT_BORDER,
            corner_radius=8, font=ui_fonts.ui_font(11)
        )
        self.world_pack_dissolve_btn.pack(side='left', padx=(12, 0))
        self.world_pack_active_frame.pack_forget()

        # 第2行：启用开关（参考 AI 设置段的布局风格）
        _, ctrl = self._make_row(self.world_body, row, "启用")
        row += 1
        self.world_pack_enable_var = tk.BooleanVar(value=False)
        self.world_pack_switch = ctk.CTkSwitch(
            ctrl, text="", variable=self.world_pack_enable_var,
            width=44, height=22, command=self._on_world_pack_switch,
            progress_color=SWC_PROGRESS, fg_color=SWC_FG,
            button_color=SWC_BTN, button_hover_color=SWC_BTN
        )
        self.world_pack_switch.pack(side='left')

        self._refresh_world_pack_ui()

    # ───────────────── 世界包操作 ─────────────────
    def _refresh_world_pack_ui(self):
        """刷新包设置段：未启用时显示 optionmenu+新建键，已启用时显示启用标签+解散键。"""
        if self.world_manager is None:
            self.world_pack_label.configure(
                text="世界包（不可用）", text_color=SOFT)
            self.world_pack_menu.configure(state="disabled")
            self.world_pack_new_btn.configure(state="disabled")
            self.world_pack_dissolve_btn.configure(state="disabled")
            self.world_pack_switch.configure(state="disabled")
            self._show_pack_row(False)
            self.world_pack_var.set("")
            self._set_world_controls_locked()
            return

        self.world_pack_menu.configure(state="normal")
        self.world_pack_new_btn.configure(state="normal")
        self.world_pack_dissolve_btn.configure(state="normal")
        self.world_pack_switch.configure(state="normal")

        names = []
        for p in self.world_manager.list_packs():
            if not p.get("error"):
                names.append(p["name"])
        current = self.world_pack_var.get()
        self.world_pack_menu.configure(values=names)
        self.world_pack_menu._profile_dropdown.configure(values=names)
        if current not in names:
            self.world_pack_var.set(names[0] if names else "")

        active = self.world_state is not None and self.world_state.active
        if active:
            self.world_pack_label.configure(text="世界包", text_color=HARD_LABEL)
            self.world_pack_active_label.configure(
                text=f"已启用：{self.world_state.pack_name}")
            self._show_pack_row(True)
            self.world_pack_var.set(self.world_state.pack_name)
            self.world_pack_enable_var.set(True)
        else:
            self.world_pack_label.configure(text="世界包", text_color=HARD_LABEL)
            self.world_pack_active_label.configure(text="已启用：")
            self._show_pack_row(False)
            self.world_pack_enable_var.set(False)
        self._set_world_controls_locked()

    def _show_pack_row(self, active):
        """在“optionmenu+新建键”与“启用标签+解散键”两种布局间切换。"""
        if active:
            self.world_pack_idle_frame.pack_forget()
            self.world_pack_active_frame.pack(side='left')
        else:
            self.world_pack_active_frame.pack_forget()
            self.world_pack_idle_frame.pack(side='left')

    def _on_world_pack_changed(self, choice):
        """仅记录 optionmenu 选择，加装/卸载由第2行“启用”开关触发。"""
        pass

    def _on_world_pack_switch(self):
        """开关仅记录启用意图，不立即应用任何更改；保存设置时才生效。"""
        if self.world_manager is None:
            self.world_pack_enable_var.set(False)
            return
        if self.world_pack_enable_var.get():
            for p in self.world_manager.list_packs():
                if p["name"] == self.world_pack_var.get() and not p.get("error"):
                    return
            self.world_pack_enable_var.set(False)
            ui.common.dialogs.showwarning("提示", "没有可加载的世界包，请先创建")

    def _activate_selected_pack(self):
        """根据 optionmenu 当前选择的包执行激活（供保存流程调用）。"""
        if self.world_manager is None:
            raise ValueError("世界包功能不可用")
        target = None
        for p in self.world_manager.list_packs():
            if p["name"] == self.world_pack_var.get() and not p.get("error"):
                target = p
                break
        if target is None:
            raise ValueError("没有可加载的世界包，请先创建")
        self.world_manager.activate(
            target["world_id"], self.settings, self.settings_repo)

    def _apply_world_pack_changes_on_save(self):
        """保存设置时应用世界包启用/卸载更改：先保存自由设置，再执行切换。"""
        if self.world_manager is None or self.world_state is None:
            return
        enabled = self.world_pack_enable_var.get()
        active = self.world_state.active
        if enabled and active:
            if self.world_state.pack_name == self.world_pack_var.get():
                return
            self.world_manager.deactivate(self.settings, self.settings_repo)
            self._activate_selected_pack()
        elif enabled and not active:
            self._activate_selected_pack()
        elif not enabled and active:
            self.world_manager.deactivate(self.settings, self.settings_repo)

    def _build_available_resources(self) -> Dict[str, list]:
        """返回各资源类型可选打包的具体条目（供创建对话框的选择控件使用）。"""
        resources: Dict[str, list] = {}
        resources["landmarks"] = list(self.landmark_repo.get_styles())
        resources["quips"] = list(self.quip_repo.get_styles())
        resources["presets"] = list(self.preset_repo.get_tables())
        resources["personalities"] = list(self.personality_repo.get_tables())
        dungeon_repo = getattr(self.gui_ref, "_dungeon_repo", None) if self.gui_ref else None
        if dungeon_repo is not None:
            resources["dungeons"] = [d for d in dungeon_repo.list_all() if d != "_default"]
        # 挑战包：列出全部自由 .chal（打包不依赖密钥，密钥由用户手动放入包内）
        cm = ChallengeService(self.settings_repo, world_state=self.world_state)
        resources["challenges"] = [
            f for f in cm.get_available_packs() if not cm.is_bundled(f)]
        resources["names"] = list(self.name_repo.get_tables())
        news_service = getattr(getattr(self.gui_ref, "context", None), "news_service", None)
        if news_service is not None:
            resources["news"] = list(news_service.get_tables())
        if self.world_manager is not None:
            behaviors = list_behavior_packs(self.world_manager.data_dir)
            if (self.world_state is not None and self.world_state.active
                    and self.world_state.owns("behaviors")):
                for name in self.world_state.manifest.resources.get("behaviors") or []:
                    if name not in behaviors:
                        behaviors.append(name)
                behaviors.sort()
            if behaviors:
                resources["behaviors"] = behaviors
        return {k: v for k, v in resources.items() if v}

    def _set_world_controls_locked(self):
        """按包锁定的设置键禁用对应控件（世界块）。"""
        locked = self.world_state.locked_keys() if self.world_state is not None else set()
        self.world_setting_menu.configure(
            state="disabled" if "world_setting" in locked else "normal")
        self.seed_entry.configure(state="disabled" if "seed" in locked else "normal")
        self.seed_random_btn.configure(state="disabled" if "seed" in locked else "normal")
        self.name_table_menu.configure(
            state="disabled" if "name_table" in locked else "normal")
        self.news_table_menu.configure(
            state="disabled" if "news_table" in locked else "normal")
        self.preset_table_menu.configure(
            state="disabled" if "preset_table" in locked else "normal")
        self.personality_table_menu.configure(
            state="disabled" if "personality_table" in locked else "normal")

    def _sync_world_vars(self):
        """激活/卸载/解散后，按 self.settings 刷新世界块控件。"""
        world_options = {"出现": "appear", "绝对巨大化": "abs_giant", "相对巨大化": "rel_giant"}
        world_setting = self.settings.get("world_setting", "appear")
        for display, value in world_options.items():
            if value == world_setting:
                self.world_setting_var.set(display)
                break
        self.world_setting = world_setting

        seed = self.settings.get("seed", 0)
        self.seed_var.set(str(seed) if seed != 0 else "")
        self.user_seed = seed

        name_tables = self.name_repo.get_tables()
        name_table = self.settings.get("name_table", DEFAULT_NAME_TABLE)
        if name_table not in name_tables:
            name_tables.insert(0, name_table)
        self.name_table = name_table
        self.name_table_var.set(name_table)
        self.name_table_menu.configure(values=name_tables)
        self.name_table_menu._profile_dropdown.configure(values=name_tables)

        news_table = self.settings.get("news_table", DEFAULT_NEWS_TABLE)
        news_service = NewsService(news_table=news_table,
                                   world_state=self.world_state)
        news_tables = news_service.get_tables()
        if news_table not in news_tables:
            news_tables.insert(0, news_table)
        if not news_tables:
            news_tables = [DEFAULT_NEWS_TABLE]
        self.news_table = news_table
        self.news_table_var.set(news_table)
        self.news_table_menu.configure(values=news_tables)
        self.news_table_menu._profile_dropdown.configure(values=news_tables)

        preset_table = self.settings.get("preset_table", "default")
        preset_tables = self.preset_repo.get_tables()
        if preset_table not in preset_tables:
            preset_tables.insert(0, preset_table)
        if not preset_tables:
            preset_tables = ["default"]
        self.preset_table = preset_table
        self.preset_table_var.set(preset_table)
        self.preset_table_menu.configure(values=preset_tables)
        self.preset_table_menu._profile_dropdown.configure(values=preset_tables)

        personality_table = self.settings.get("personality_table", "default")
        personality_tables = self.personality_repo.get_tables()
        if personality_table not in personality_tables:
            personality_tables.insert(0, personality_table)
        if not personality_tables:
            personality_tables = ["default"]
        self.personality_table = personality_table
        self.personality_table_var.set(personality_table)
        self.personality_table_menu.configure(values=personality_tables)
        self.personality_table_menu._profile_dropdown.configure(values=personality_tables)

    def _after_world_state_change(self):
        """世界包状态变化后同步面板控件、上下文与全局绿色指示。"""
        self._sync_world_vars()
        self.selected_styles = self.settings.get("selected_styles", ["ChineseMix"])
        self.selected_quip_styles = self.settings.get("selected_quip_styles", [])
        self._sync_all_states()
        if self.on_styles_changed:
            self.on_styles_changed()
        if self.gui_ref is not None:
            for method, arg in [
                ("on_world_setting_changed", self.world_setting),
                ("on_name_table_changed", self.name_table),
                ("on_news_table_changed", self.news_table),
                ("on_preset_table_changed", self.preset_table),
                ("on_personality_table_changed", self.personality_table),
            ]:
                if hasattr(self.gui_ref, method):
                    getattr(self.gui_ref, method)(arg)
            if hasattr(self.gui_ref, "refresh_world_ui"):
                self.gui_ref.refresh_world_ui()
        self._refresh_world_pack_ui()

    def _create_world_pack(self):
        if self.world_manager is None:
            ui.common.dialogs.showerror("错误", "世界包功能不可用")
            return
        dialog = WorldPackCreateDialog(
            self, available_resources=self._build_available_resources())
        cfg = dialog.result
        if not cfg:
            return

        def _do_work():
            try:
                challenge_mgr = ChallengeService(
                    self.settings_repo,
                    getattr(self.gui_ref, "_character_repo", None) if self.gui_ref else None,
                    self.landmark_repo,
                    self.quip_repo,
                    getattr(self.gui_ref, "_dungeon_repo", None) if self.gui_ref else None,
                )
                manifest = self.world_manager.create_from_current(
                    cfg["world_id"], cfg["name"], self.settings,
                    landmark_repo=self.landmark_repo,
                    quip_repo=self.quip_repo,
                    preset_repo=getattr(self.gui_ref, "_preset_repo", None) if self.gui_ref else None,
                    personality_repo=getattr(self.gui_ref, "_personality_repo", None) if self.gui_ref else None,
                    dungeon_repo=getattr(self.gui_ref, "_dungeon_repo", None) if self.gui_ref else None,
                    name_repo=self.name_repo,
                    news_service=getattr(getattr(self.gui_ref, "context", None),
                                         "news_service", None),
                    challenge_mgr=challenge_mgr,
                    version=cfg["version"],
                    author=cfg["author"],
                    description=cfg["description"],
                    selected_resources=cfg["selected_resources"],
                )
            except Exception as e:
                self.after(0, lambda: ui.common.dialogs.showerror(
                    "错误", f"创建世界包失败：{e}"))
                return
            self.after(0, lambda m=manifest: self._on_pack_created(m))

        self.after(50, _do_work)

    def _on_pack_created(self, manifest):
        ui.common.dialogs.showinfo(
            "成功", f"世界包「{manifest.name}」已创建\nworld_id: {manifest.world_id}")
        self._refresh_world_pack_ui()

    def _dissolve_world_pack(self):
        if self.world_manager is None:
            ui.common.dialogs.showerror("错误", "世界包功能不可用")
            return
        if self.world_state is None or not self.world_state.active:
            return
        world_id = self.world_state.world_id
        name = self.world_state.pack_name
        choice = ui.common.dialogs.askyesnocancel(
            "删除世界包",
            f"删除「{name}」会立即退出该世界包。\n是否将包内资源解散到自由资源？")
        if choice is None:
            return
        try:
            challenge_mgr = ChallengeService(
                self.settings_repo,
                getattr(self.gui_ref, "_character_repo", None) if self.gui_ref else None,
                self.landmark_repo,
                self.quip_repo,
                getattr(self.gui_ref, "_dungeon_repo", None) if self.gui_ref else None,
            )
            if choice:
                self.world_manager.dissolve(
                    world_id, self.settings, self.settings_repo,
                    remove_pack=True, challenge_mgr=challenge_mgr)
            else:
                self.world_manager.deactivate(self.settings, self.settings_repo)
                self.world_manager.delete_pack(world_id)
        except ValueError as e:
            ui.common.dialogs.showerror("错误", str(e))
            return
        ui.common.dialogs.showinfo("成功", f"世界包「{name}」已删除")
        self._after_world_state_change()

    def _build_generation_block(self):
        self._setup_body_grid(self.gen_body, "gen")
        row = 0

        self._section_label(self.gen_body, row, "尺寸对比")
        row += 1

        _, ctrl = self._make_row(self.gen_body, row, "对比数量")
        row += 1
        self.count_var = tk.IntVar(value=self.comparison_count)
        self.count_entry = self._make_entry(ctrl, self.count_var, width=80)
        self.count_entry.pack(side='left')

        _, ctrl = self._make_row(self.gen_body, row, "排序方式")
        row += 1
        order_options = {"匹配度": "match", "尺寸升序": "size_asc", "尺寸降序": "size_desc"}
        self.order_var = tk.StringVar(value=self.comparison_order)
        self.order_menu = self._make_option_menu(
            ctrl, list(order_options.keys()),
            self._on_order_option_changed, self.order_var
        )
        for display, value in order_options.items():
            if value == self.comparison_order:
                self.order_var.set(display)
                break
        row += 1

        self._section_label(self.gen_body, row, "叙述优化")
        row += 1

        _, ctrl = self._make_row(self.gen_body, row, "叙述速率")
        row += 1
        self.rate_factor_var = tk.DoubleVar(value=self.quip_rate_factor)
        self.rate_factor_entry = self._make_entry(ctrl, self.rate_factor_var, width=80)
        self.rate_factor_entry.pack(side='left')

        _, ctrl = self._make_row(self.gen_body, row, "混淆描述细分词库")
        row += 1
        self.confusion_var = tk.BooleanVar(value=self.enable_confusion)
        self.confusion_switch = self._make_switch(ctrl, self.confusion_var)

        _, ctrl = self._make_row(self.gen_body, row, "屏蔽词")
        row += 1
        self.blocked_words_var = tk.StringVar(value=", ".join(self.blocked_words))
        self.blocked_words_entry = self._make_entry(
            ctrl, self.blocked_words_var, width=180,
            placeholder_text="用逗号分隔"
        )
        self.blocked_words_entry.pack(side='left')

        self._section_label(self.gen_body, row, "报告详情栏")
        row += 1

        _, ctrl = self._make_row(self.gen_body, row, "测量所有尺寸")
        row += 1
        self.show_all_details_var = tk.BooleanVar(value=self.settings.get("show_all_details", False))
        self.show_all_switch = self._make_switch(ctrl, self.show_all_details_var)

        _, ctrl = self._make_row(self.gen_body, row, "倒序展示尺寸")
        row += 1
        self.reverse_details_var = tk.BooleanVar(value=self.settings.get("reverse_details_order", False))
        self.reverse_details_switch = self._make_switch(ctrl, self.reverse_details_var)

        _, ctrl = self._make_row(self.gen_body, row, "未上传形象时使用身材预览图")
        row += 1
        self.use_preview_avatar_var = tk.BooleanVar(value=self.settings.get("use_preview_image_as_avatar", False))
        self.use_preview_avatar_switch = self._make_switch(ctrl, self.use_preview_avatar_var)

        self._section_label(self.gen_body, row, "部位设置")
        row += 1
        self._build_parts_selection(self.gen_body, row)
        row += 1

        self._build_ai_section(self.gen_body, row)

    def _build_display_block(self):
        self._setup_body_grid(self.disp_body, "disp")
        row = 0

        self._section_label(self.disp_body, row, "主题")
        row += 1

        _, ctrl = self._make_row(self.disp_body, row, "主题模式")
        row += 1
        theme_options = {"亮色": "Light", "暗色": "Dark", "跟随系统": "System"}
        self.theme_mode_var = tk.StringVar(value=self.theme_mode)
        self.theme_menu = self._make_option_menu(
            ctrl, list(theme_options.keys()),
            self._on_theme_option_changed, self.theme_mode_var
        )
        for display, value in theme_options.items():
            if value == self.theme_mode:
                self.theme_mode_var.set(display)
                break

        self._section_label(self.disp_body, row, "界面选项")
        row += 1

        _, ctrl = self._make_row(self.disp_body, row, "显示伤亡统计")
        row += 1
        self.show_casualties_var = tk.BooleanVar(value=self.settings.get("show_casualties", True))
        self.show_casualties_switch = self._make_switch(ctrl, self.show_casualties_var)

        self._section_label(self.disp_body, row, "字体设置")
        row += 1

        _, ctrl = self._make_row(self.disp_body, row, "报告主体")
        row += 1
        self.report_font_var = tk.StringVar(value=self.settings.get("report_font", ui_fonts.report_font_default()))
        self.report_font_entry = self._make_entry(ctrl, self.report_font_var, width=150)
        self.report_font_entry.pack(side='left')

        _, ctrl = self._make_row(self.disp_body, row, "报告描述")
        row += 1
        self.desc_font_var = tk.StringVar(value=self.settings.get("desc_font", ui_fonts.desc_font_default()))
        self.desc_font_entry = self._make_entry(ctrl, self.desc_font_var, width=150)
        self.desc_font_entry.pack(side='left')

        _, ctrl = self._make_row(self.disp_body, row, "副本段落")
        row += 1
        self.dungeon_font_var = tk.StringVar(value=self.settings.get("dungeon_font", ui_fonts.dungeon_font_default()))
        self.dungeon_font_entry = self._make_entry(ctrl, self.dungeon_font_var, width=150)
        self.dungeon_font_entry.pack(side='left')

    def _build_archive_block(self):
        self._setup_body_grid(self.archive_body, "archive")
        row = 0

        _, ctrl = self._make_row(self.archive_body, row, "自动保存报告")
        row += 1
        self.auto_save_report_var = tk.BooleanVar(value=self.settings.get("auto_save_report", False))
        self.auto_save_report_switch = self._make_switch(ctrl, self.auto_save_report_var)

        _, ctrl = self._make_row(self.archive_body, row, "自动保存副本回放")
        row += 1
        self.auto_save_replay_var = tk.BooleanVar(value=self.settings.get("auto_save_replay", False))
        self.auto_save_replay_switch = self._make_switch(ctrl, self.auto_save_replay_var)

        _, ctrl = self._make_row(self.archive_body, row, "以低分辨率保存图片")
        row += 1
        self.save_low_resolution_var = tk.BooleanVar(value=self.settings.get("save_low_resolution_image", False))
        self.save_low_resolution_switch = self._make_switch(ctrl, self.save_low_resolution_var)

        _, ctrl = self._make_row(self.archive_body, row, "尺寸信息更新速率")
        row += 1
        self.info_update_var = tk.StringVar(value="中等")
        for display, value in INFO_UPDATE_OPTIONS.items():
            if abs(value - self.info_update_rate) < 1e-9:
                self.info_update_var.set(display)
                break
        self.info_update_menu = self._make_option_menu(
            ctrl, list(INFO_UPDATE_OPTIONS.keys()),
            self._on_info_update_changed, self.info_update_var
        )

    def _build_parts_selection(self, parent, row):
        parts_frame = ctk.CTkFrame(parent, fg_color="transparent")
        parts_frame.grid(row=row, column=0, columnspan=2, sticky='ew',
                         padx=18, pady=(0, 8))

        grid_frame = ctk.CTkFrame(parts_frame, fg_color="transparent")
        grid_frame.pack(fill='x', expand=True)

        cols = 5
        selected = self.settings.get("selected_parts", ALL_PART_NAMES)
        for idx, part_name in enumerate(ALL_PART_NAMES):
            var = tk.BooleanVar(value=(part_name in selected))
            cb = ctk.CTkCheckBox(
                grid_frame, text=part_name, variable=var,
                font=ui_fonts.ui_font(11), text_color=HARD_LABEL,
                fg_color=SOFT, hover_color=CHECKBOX_HOVER,
                checkbox_width=18, checkbox_height=18, corner_radius=4
            )
            row_i = idx // cols
            col_i = idx % cols
            cb.grid(row=row_i, column=col_i, padx=2, pady=3, sticky='w')
            self.parts_checkboxes[part_name] = var

    def _build_ai_section(self, parent, row):
        # AI设置标题
        self._section_label(parent, row, "副本 AI 设置")
        row += 1

        # 第1行：下拉框和新建按钮并排
        _, ai_provider_control = self._make_row(parent, row, "选择配置")
        self.ai_provider_var = tk.StringVar(value=self._profile_name(self.ai_provider))
        self.ai_provider_menu = self._make_option_menu(
            ai_provider_control, self._profile_names(),
            self._on_ai_provider_changed, self.ai_provider_var, expand=False
        )
        ctk.CTkButton(
            ai_provider_control, text="新建", width=60, height=28,
            command=self._new_ai_config, fg_color="transparent", text_color=SOFT,
            hover_color=HOVER, border_width=1, border_color=INPUT_BORDER,
            corner_radius=8, font=ui_fonts.ui_font(11)
        ).pack(side="left", padx=(8, 0))
        row += 1

        # 第2行：连接状态标签 + 配置按钮（配置按钮打开对话框，删除功能在对话框内）
        _, status_control = self._make_row(parent, row, "连接状态")
        ctk.CTkButton(
            status_control, text="配置", width=60, height=28,
            command=self._open_ai_config_dialog,
            fg_color="transparent", text_color=SOFT,
            hover_color=HOVER,
            border_width=1, border_color=INPUT_BORDER,
            corner_radius=8, font=ui_fonts.ui_font(11)
        ).pack(side='left', padx=(12, 0))

    def _load_ai_configs(self):
        """Load named profiles from settings."""
        configs = self.settings.get("ai_configs") or {}
        result = {pid: dict(cfg) for pid, cfg in configs.items()}
        return result

    def _profile_name(self, profile_id):
        return (self.ai_configs.get(profile_id) or {}).get("name", profile_id)

    def _profile_names(self):
        return [cfg.get("name", pid) for pid, cfg in self.ai_configs.items()]

    def _refresh_ai_menu(self):
        names = self._profile_names()
        self.ai_provider_menu.configure(values=names)
        self.ai_provider_menu._profile_dropdown.configure(values=names)
        self.ai_provider_var.set(self._profile_name(self.ai_provider))

    def _on_ai_provider_changed(self, choice):
        for pid, cfg in self.ai_configs.items():
            if cfg.get("name", pid) == choice:
                self.ai_provider = pid
                break

    def _open_ai_config_dialog(self):
        provider = self.ai_provider
        # 判断是否可以删除（至少保留一个配置）
        can_delete = len(self.ai_configs) > 1
        dialog = AIConfigDialog(self, provider, dict(self.ai_configs.get(provider, {})), can_delete=can_delete)
        if dialog.delete_requested:
            # 用户请求删除配置
            del self.ai_configs[provider]
            self.ai_provider = next(iter(self.ai_configs))
            self._refresh_ai_menu()
        elif dialog.result is not None:
            self.ai_configs[provider] = dialog.result
            self._refresh_ai_menu()

    def _new_ai_config(self):
        provider = "profile_" + uuid.uuid4().hex[:8]
        dialog = AIConfigDialog(self, provider, {"name": "新配置", "url": "", "model": "", "api_key": ""}, True)
        if dialog.result is not None:
            self.ai_configs[provider] = dialog.result
            self.ai_provider = provider
            self._refresh_ai_menu()

    # ───────────────── 回调 ─────────────────
    def _on_world_option_changed(self, choice):
        world_options = {"出现": "appear", "绝对巨大化": "abs_giant", "相对巨大化": "rel_giant"}
        self.world_setting = world_options.get(choice, "appear")
        if self.on_world_setting_changed:
            self.on_world_setting_changed(self.world_setting)

    def _on_name_table_changed(self, choice):
        self.name_table = choice or DEFAULT_NAME_TABLE
        self.name_table_var.set(choice)
        if self.on_name_table_changed:
            self.on_name_table_changed(self.name_table)

    def _on_news_table_changed(self, choice):
        self.news_table = choice or DEFAULT_NEWS_TABLE
        self.news_table_var.set(self.news_table)
        if self.on_news_table_changed:
            self.on_news_table_changed(self.news_table)

    def _on_preset_table_changed(self, choice):
        self.preset_table = choice or "default"
        self.preset_table_var.set(self.preset_table)
        if self.on_preset_table_changed:
            self.on_preset_table_changed(self.preset_table)

    def _on_personality_table_changed(self, choice):
        self.personality_table = choice or "default"
        self.personality_table_var.set(self.personality_table)
        if self.on_personality_table_changed:
            self.on_personality_table_changed(self.personality_table)

    def _on_order_option_changed(self, choice):
        order_options = {"匹配度": "match", "尺寸升序": "size_asc", "尺寸降序": "size_desc"}
        self.comparison_order = order_options.get(choice, "match")

    def _on_info_update_changed(self, choice):
        self.info_update_rate = INFO_UPDATE_OPTIONS.get(choice, 0.5)

    def _on_theme_option_changed(self, choice):
        theme_options = {"亮色": "Light", "暗色": "Dark", "跟随系统": "System"}
        self.theme_mode = theme_options.get(choice, "Light")
        self.theme_mode_var.set(choice)  # 保持显示文字

    def _on_world_changed(self):
        self.world_setting = self.world_setting_var.get()
        if self.on_world_setting_changed:
            self.on_world_setting_changed(self.world_setting)

    def _on_theme_changed(self):
        mode = self.theme_mode_var.get()
        self.theme_mode = mode
        # 主题只保存在编辑状态中。点击“保存并返回主页面”后，才由主窗口
        # 管理器统一写入设置并应用，避免全局实际主题与 settings 暂存值分离。

    def update_theme(self, mode=None):
        """刷新设置页中的原生 Listbox，不改变待保存的主题选择。"""
        actual_mode = mode or ctk.get_appearance_mode()
        self.landmark_selector.apply_theme(actual_mode)
        self.quip_selector.apply_theme(actual_mode)

    def sync_saved_theme(self):
        """进入设置页时丢弃未保存的主题选择，显示持久化值。"""
        saved_mode = self.settings.get("theme_mode", "Light")
        self.theme_mode = saved_mode
        # 设置 OptionMenu 显示值
        theme_options = {"亮色": "Light", "暗色": "Dark", "跟随系统": "System"}
        for display, value in theme_options.items():
            if value == saved_mode:
                self.theme_mode_var.set(display)
                break

    def _on_order_changed(self):
        self.comparison_order = self.order_var.get()

    def _randomize_seed(self):
        new_seed = random.randint(1, 2 ** 31 - 1)
        self.seed_var.set(str(new_seed))

    # ───────────────── 风格选择逻辑 ─────────────────
    def _sync_all_states(self):
        self._sync_landmark_styles()
        self._sync_quip_styles()
        selected = self.settings.get("selected_parts", ALL_PART_NAMES)
        for part, var in self.parts_checkboxes.items():
            var.set(part in selected)

    def _sync_landmark_styles(self, filter_world=None):
        styles = self.landmark_repo.get_styles()
        display_items = []
        kept = []
        for style in styles:
            reg_world = world_of(self.landmark_repo.load_style_address(style))
            if filter_world and reg_world and reg_world != filter_world:
                continue  # 同一世界观自动筛选：隐藏其它世界观的地标风格
            landmarks = self.landmark_repo.load(style)
            display_items.append(f"{style} ({len(landmarks)})")
            kept.append(style)
        selected_indices = [i for i, s in enumerate(kept) if s in self.selected_styles]
        self.landmark_selector.sync_items(display_items, selected_indices)

    def _sync_quip_styles(self, filter_world=None):
        styles = self.quip_repo.get_styles()
        size_order = ["small", "medium", "large", "huge", "colossal"]
        display_items = []
        kept = []
        for style in styles:
            reg_world = world_of(self.quip_repo.load_style_address(style))
            if filter_world and reg_world and reg_world != filter_world:
                continue  # 同一世界观自动筛选：隐藏其它世界观的描述风格
            quips = self.quip_repo.load(style)
            counts = []
            for size in size_order:
                matrix = quips.get(size, {})
                total = sum(len(qlist) for qlist in matrix.values())
                counts.append(str(total))
            display_items.append(f"{style} ({', '.join(counts)})")
            kept.append(style)
        selected_indices = [i for i, s in enumerate(kept) if s in self.selected_quip_styles]
        self.quip_selector.sync_items(display_items, selected_indices)

    def _lock_world_from_selection(self) -> Optional[str]:
        """当前选中风格共同锁定的世界观（先地标风格后描述风格）；未注册返回 None。"""
        for s in self.selected_styles:
            w = world_of(self.landmark_repo.load_style_address(s))
            if w:
                return w
        for s in self.selected_quip_styles:
            w = world_of(self.quip_repo.load_style_address(s))
            if w:
                return w
        return None

    def _apply_world_lock(self, forced: Optional[str] = None):
        """同一世界观互斥：把两栏风格列表自动筛选到同一个世界观，剔除混选。

        forced：用户本次新点选的风格带世界观时，以该世界观为准切换（自动筛选）。
        """
        lock = forced if forced is not None else self._lock_world_from_selection()
        # 剔除与锁定世界观不一致的已选风格（未注册风格不限制）
        def _filter_for_world(names, repo):
            out = []
            for s in names:
                w = world_of(repo.load_style_address(s))
                if not w or (lock is not None and w == lock):
                    out.append(s)
            return out

        self.selected_styles = _filter_for_world(self.selected_styles, self.landmark_repo)
        self.selected_quip_styles = _filter_for_world(self.selected_quip_styles, self.quip_repo)
        if not self.selected_styles:
            self.selected_styles = ["ChineseMix"]
        self._sync_landmark_styles(lock)
        self._sync_quip_styles(lock)
        # 同步后以实际可见的选中项为准
        self.selected_styles = self.landmark_selector.get_selected_raw_names() or self.selected_styles
        self.selected_quip_styles = self.quip_selector.get_selected_raw_names() or self.selected_quip_styles
        self.on_styles_changed()

    def _on_landmark_selection_changed(self):
        prev = list(self.selected_styles)
        names = self.landmark_selector.get_selected_raw_names()
        if not names:
            names = ["ChineseMix"]
        forced = None
        for n in names:
            if n in prev:
                continue
            w = world_of(self.landmark_repo.load_style_address(n))
            if w:
                forced = w
                break
        self.selected_styles = names
        self._apply_world_lock(forced)

    def _on_quip_selection_changed(self):
        prev = list(self.selected_quip_styles)
        names = self.quip_selector.get_selected_raw_names()
        forced = None
        for n in names:
            if n in prev:
                continue
            w = world_of(self.quip_repo.load_style_address(n))
            if w:
                forced = w
                break
        self.selected_quip_styles = names
        self._apply_world_lock(forced)

    # ───────────────── 保存与返回 ─────────────────────
    def _save_and_return(self):
        raw_seed = self.seed_var.get().strip()
        raw_count = self.count_var.get()
        raw_report_font = self.report_font_var.get().strip()
        raw_desc_font = self.desc_font_var.get().strip()
        raw_dungeon_font = self.dungeon_font_var.get().strip()

        def validate_font(font_name, default):
            font_name = ui_fonts.font_family_for(font_name, default)
            if not font_name:
                return default
            available = [f.lower() for f in tkfont.families()]
            if font_name.lower() in available:
                return font_name
            cleaned = font_name.strip("'\"")
            if cleaned.lower() in available:
                return cleaned
            return default

        report_font = validate_font(raw_report_font, ui_fonts.report_font_default())
        desc_font = validate_font(raw_desc_font, ui_fonts.desc_font_default())
        dungeon_font = validate_font(raw_dungeon_font, ui_fonts.dungeon_font_default())

        try:
            seed = int(float(raw_seed)) & 0x7FFFFFFF if raw_seed != "" else 0
        except ValueError:
            seed = self.settings.get("seed", 0)

        try:
            count = int(raw_count)
            count = max(1, min(10, count))
        except (ValueError, TypeError):
            count = self.settings.get("comparison_count", 5)

        try:
            rate_factor = float(self.rate_factor_var.get())
            rate_factor = max(0.5, min(2.0, rate_factor))
        except ValueError:
            rate_factor = 1.0

        # 从 OptionMenu 获取实际值
        world_options = {"出现": "appear", "绝对巨大化": "abs_giant", "相对巨大化": "rel_giant"}
        order_options = {"匹配度": "match", "尺寸升序": "size_asc", "尺寸降序": "size_desc"}
        theme_options = {"亮色": "Light", "暗色": "Dark", "跟随系统": "System"}

        world_setting = world_options.get(self.world_setting_var.get(), "appear")
        comparison_order = order_options.get(self.order_var.get(), "match")
        theme_mode = theme_options.get(self.theme_mode_var.get(), "Light")

        self.settings.update({
            "theme_mode": theme_mode,
            "seed": seed,
            "comparison_count": count,
            "comparison_order": comparison_order,
            "world_setting": world_setting,
            "name_table": self.name_table,
            "news_table": self.news_table,
            "preset_table": self.preset_table,
            "personality_table": self.personality_table,
            "selected_styles": self.selected_styles,
            "selected_quip_styles": self.selected_quip_styles,
            "show_all_details": self.show_all_details_var.get(),
            "enable_confusion": self.confusion_var.get(),
            "blocked_words": normalize_blocked_words(self.blocked_words_var.get()),
            "ai_provider": self.ai_provider,
            "ai_configs": {pid: dict(cfg) for pid, cfg in self.ai_configs.items()},
            "report_font": report_font,
            "desc_font": desc_font,
            "dungeon_font": dungeon_font,
            "quip_rate_factor": rate_factor,
            "show_casualties": self.show_casualties_var.get(),
            "auto_save_report": self.auto_save_report_var.get(),
            "auto_save_replay": self.auto_save_replay_var.get(),
            "save_low_resolution_image": self.save_low_resolution_var.get(),
            "use_preview_image_as_avatar": self.use_preview_avatar_var.get(),
            "info_update_rate": INFO_UPDATE_OPTIONS.get(self.info_update_var.get(), self.info_update_rate)
        })

        selected_parts = [part for part, var in self.parts_checkboxes.items() if var.get()]
        if not selected_parts:
            selected_parts = ALL_PART_NAMES.copy()
        self.settings["selected_parts"] = selected_parts
        self.settings["reverse_details_order"] = self.reverse_details_var.get()

        # 先保存当前（自由）设置，再应用世界包启用/卸载更改
        self.settings_repo.save(self.settings)

        try:
            self._apply_world_pack_changes_on_save()
        except ValueError as e:
            ui.common.dialogs.showerror("错误", str(e))
            self._refresh_world_pack_ui()
            return

        if self.gui_ref and hasattr(self.gui_ref, 'apply_settings'):
            self.gui_ref.apply_settings()

        self._after_world_state_change()

        if self.on_return_callback:
            self.on_return_callback()
