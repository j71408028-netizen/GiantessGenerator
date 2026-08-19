# ui/exploration/exp_frame.py
import json
from tkinter import filedialog

import ui.common.dialogs
from typing import Optional, Tuple, List

import customtkinter as ctk

from dungeon.window import DungeonSessionWindow
from ai import resolve_ai_config
from ui.exploration.giantess_state import GiantessStatePanel
from ui.exploration.creation_params import CreationParamsPanel
from ui.exploration.select_character import SelectCharacterPanel
from ui.exploration.intro import IntroPanel
from ui.exploration.report import ReportPanel
from ui.common.dialogs import BaseDialog
from ui.common.theme import (
    BASE, BORDER, SOFT, TEXT_MUTED, TEXT_DISABLED,
    BORDER_ALT, HOVER_ALT,
    STATUS_OK, OK_HOVER, REPORT, REPORT_HOVER, DUNGEON, DUNGEON_HOVER,
    GOLD_STRONG_BORDER,
)
from ui.common import fonts as ui_fonts
from models import CharacterSnapshot
from context import ExplorationContext


class ExplorationPanel(ctk.CTkFrame):
    """
    探索模式面板（优化版：适配浅色/深色模式）。
    """

    def __init__(self, parent, app, context: ExplorationContext):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self.context = context

        self.current_panel = "params"
        self.current_state = None
        self._cached_params_intro = None

        self._build_ui()

        self.update_world_setting(self.context.world_setting)
        self.refresh_style_hint()

    # ---------- UI 构建 ----------
    def _build_ui(self):
        self.grid_columnconfigure(0, weight=0, minsize=500)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ===== LEFT COLUMN =====
        left_col = ctk.CTkFrame(self, fg_color="transparent")
        left_col.grid(row=0, column=0, sticky='nsew', padx=(15, 5), pady=7)
        left_col.pack_propagate(False)
        left_col.configure(width=500)

        # 内容区域
        self.content_area = ctk.CTkFrame(left_col, fg_color="transparent")
        self.content_area.pack(fill='both', expand=True)

        # 左上面板：探索模式标题 + 参数/状态/选择面板
        left_top = ctk.CTkFrame(self.content_area, fg_color="transparent")
        left_top.pack(fill='x', pady=(0, 5))
        left_top.grid_columnconfigure(0, weight=1)

        # 探索模式标题 (row 0)
        title_frame = ctk.CTkFrame(left_top, fg_color="transparent")
        title_frame.grid(row=0, column=0, sticky='ew', padx=8, pady=(6, 2))
        self.title_label = ctk.CTkLabel(title_frame, text="🧭  探索模式",
                                        font=ui_fonts.ui_font(16, "bold"),
                                        text_color=SOFT)
        self.title_label.pack(side='left')

        # 面板堆叠层 (row 1)
        self.panel_stack = ctk.CTkFrame(left_top, fg_color="transparent")
        self.panel_stack.grid(row=1, column=0, sticky='nsew')
        self.panel_stack.grid_columnconfigure(0, weight=1)
        self.panel_stack.grid_rowconfigure(0, weight=1)

        self.params_panel = CreationParamsPanel(
            self.panel_stack,
            context=self.context,
            gui_ref=self.app,
            preset_repo=self.app._preset_repo,
            personality_repo=self.app._personality_repo,
            on_personality_changed=None,
            on_preset_changed=None,
            on_intro_edited=None,
            on_image_uploaded=None,
            on_intro_toggle=lambda: self.intro_panel.toggle()
        )
        self.params_panel.grid(row=0, column=0, sticky='ew', padx=5, pady=5)

        self.state_panel = GiantessStatePanel(self.panel_stack, self.app._character_repo, self.context, self.app)
        self.state_panel.grid(row=0, column=0, sticky='nsew', padx=5, pady=5)
        self.params_panel.bind('<Configure>', self._sync_state_panel_height, add='+')

        # 选择角色面板铺满 content_area，保留左上角的探索模式标题；
        # 进入选择模式时隐藏 panel_stack 与左下区域，与整栏切换。
        self.select_panel = SelectCharacterPanel(self.content_area, self.app._character_repo,
                                                   on_selected=self._on_character_selected,
                                                   on_back=self._back_from_select,
                                                   context=self.context)
        self.select_panel.pack_forget()
        self._show_panel(self.params_panel)

        # 介绍面板（社交媒体卡片风格）
        self.intro_panel = IntroPanel(self.content_area, self.params_panel, generator_panel=self)
        self.intro_panel.pack(fill='x', padx=5, pady=(0, 5))
        self.intro_panel.refresh_image_display()

        # 形象变更加载后刷新头像
        self.params_panel.on_image_uploaded = lambda: self.intro_panel.refresh_image_display()

        # 左下按钮区域
        self.button_inner = ctk.CTkFrame(left_col, fg_color="transparent")
        self.button_inner.pack(side='bottom', fill='x', padx=12, pady=0)

        self.button_hint = ctk.CTkFrame(left_col, fg_color="transparent")
        self.button_hint.pack(side='bottom', fill='x', padx=12, pady=0)

        self.report_cost_label = ctk.CTkLabel(
            self.button_hint, text="",
            font=ui_fonts.ui_font(10, "bold"),
            text_color=REPORT)
        left_side = ctk.CTkFrame(self.button_inner, fg_color="transparent")
        left_side.pack(side='left')
        select_btn = ctk.CTkButton(left_side, text="加载角色", width=100,
                                    font=ui_fonts.ui_font(12),
                                    fg_color="transparent",
                                    text_color=TEXT_MUTED,
                                    hover_color=HOVER_ALT,
                                    border_color=BORDER_ALT,
                                    border_width=1,
                                    corner_radius=8,
                                    command=self.switch_to_select_panel)
        select_btn.pack(side='left', padx=3, pady=(0,5))

        right_side = ctk.CTkFrame(self.button_inner, fg_color="transparent")
        right_side.pack(side='right')

        btn_cr = STATUS_OK
        btn_ch = OK_HOVER
        self.action_btn = ctk.CTkButton(
            right_side, text="✨ 创建角色", width=100,
            font=ui_fonts.ui_font(12),
            fg_color="transparent",
            border_width=2,
            border_color=btn_cr,
            text_color=btn_cr,
            hover_color=btn_ch,
            corner_radius=10,
            command=self._on_action_btn_click
        )
        self.action_btn.pack(side='left', padx=4, pady=(0,5))

        btn_rr = REPORT
        btn_rh = REPORT_HOVER
        self.report_btn = ctk.CTkButton(
            right_side, text="📜 生成报告", width=100,
            font=ui_fonts.ui_font(12),
            fg_color="transparent",
            border_width=2,
            border_color=btn_rr,
            text_color=btn_rr,
            hover_color=btn_rh,
            corner_radius=10,
            command=self._generate_giantess
        )
        self.report_btn.pack(side='left', padx=4, pady=(0,5))

        btn_dr = DUNGEON
        btn_dh = DUNGEON_HOVER
        self.dungeon_btn = ctk.CTkButton(
            right_side, text="🏰 进入副本", width=100,
            font=ui_fonts.ui_font(12),
            fg_color="transparent",
            border_width=2,
            border_color=btn_dr,
            text_color=btn_dr,
            hover_color=btn_dh,
            corner_radius=10,
            command=self._start_dungeon
        )
        self.dungeon_btn.pack(side='left', padx=4, pady=(0,5))

        # ===== RIGHT COLUMN =====
        right_frame = ctk.CTkFrame(self, fg_color="transparent")
        right_frame.grid(row=0, column=1, rowspan=3, sticky='nsew', padx=(5, 15), pady=7)
        right_frame.grid_rowconfigure(0, weight=0)
        right_frame.grid_rowconfigure(1, weight=1)
        right_frame.grid_columnconfigure(0, weight=1)

        # 风格栏 ─ 紧凑容器，整体可点击，hover 强化边框效果
        style_frame = ctk.CTkFrame(right_frame, fg_color=BASE,
                                   border_width=1, border_color=BORDER,
                                   corner_radius=10, cursor="hand2")
        style_frame.grid(row=0, column=0, sticky='ew', padx=5, pady=(0, 5))
        style_frame.bind("<Button-1>", lambda e: self._jump_to_style_settings())
        def _style_hover_enter(e):
            style_frame.configure(border_color=GOLD_STRONG_BORDER, border_width=2)
        def _style_hover_leave(e):
            style_frame.configure(border_color=BORDER, border_width=1)

        style_frame.bind("<Enter>", _style_hover_enter)
        style_frame.bind("<Leave>", _style_hover_leave)

        style_bar = ctk.CTkFrame(style_frame, fg_color="transparent")
        style_bar.pack(fill='x', padx=8, pady=4)
        style_bar.bind("<Button-1>", lambda e: self._jump_to_style_settings())
        style_bar.bind("<Enter>", _style_hover_enter)
        style_bar.bind("<Leave>", _style_hover_leave)

        self.landmark_hint_label = ctk.CTkLabel(
            style_bar, text="🏔 地标: 未选择",
            text_color=SOFT,
            font=ui_fonts.ui_font(11, "bold")
        )
        self.landmark_hint_label.pack(side='left', padx=(0, 14))
        self.landmark_hint_label.bind("<Button-1>", lambda e: self._jump_to_style_settings())
        self.landmark_hint_label.bind("<Enter>", _style_hover_enter)
        self.landmark_hint_label.bind("<Leave>", _style_hover_leave)

        self.quip_hint_label = ctk.CTkLabel(
            style_bar, text="💬 描述: 未选择",
            text_color=SOFT,
            font=ui_fonts.ui_font(11, "bold")
        )
        self.quip_hint_label.pack(side='left')
        self.quip_hint_label.bind("<Button-1>", lambda e: self._jump_to_style_settings())
        self.quip_hint_label.bind("<Enter>", _style_hover_enter)
        self.quip_hint_label.bind("<Leave>", _style_hover_leave)

        click_label = ctk.CTkLabel(style_bar, text="点击切换",
                     text_color=TEXT_DISABLED,
                     font=ui_fonts.ui_font(11))
        click_label.pack(side='left', padx=(20,0))
        click_label.bind("<Enter>", _style_hover_enter)
        click_label.bind("<Leave>", _style_hover_leave)

        self._build_result_area(right_frame)

    def _build_result_area(self, parent):
        # 右侧大容器（报告正文 + 详细尺寸）已拆分为独立组件
        self.report_panel = ReportPanel(parent, self.app, self.context,
                                        self.params_panel, host=self)
        self.report_panel.grid(row=1, column=0, sticky='nsew', padx=5, pady=(0, 4))

    # ---------- 面板切换 ----------
    def _show_panel(self, panel):
        """提升已创建的目标面板，避免重新映射时显示旧主题的首帧。"""
        panel.tkraise()

    def _repack_action_buttons(self):
        """按「创建角色 / 生成报告 / 进入副本」顺序重新打包右下按钮，
        避免切换面板时重建「创建角色」按钮导致其跑到「进入副本」右侧。"""
        for btn in (self.action_btn, self.report_btn, self.dungeon_btn):
            btn.pack_forget()
        self.action_btn.pack(side='left', padx=4, pady=(0, 5))
        self.report_btn.pack(side='left', padx=4, pady=(0, 5))
        self.dungeon_btn.pack(side='left', padx=4, pady=(0, 5))

    def _sync_state_panel_height(self, event):
        """状态面板始终占用与创建参数面板相同的高度。"""
        if event.widget is self.params_panel and event.height > 1:
            self.state_panel.configure(height=event.height)

    def _enter_select_mode(self):
        """显示铺满 content_area 的选择角色面板，保留左上探索模式标题。"""
        self.panel_stack.grid_remove()
        self.intro_panel.pack_forget()
        self.button_inner.pack_forget()
        self.button_hint.pack_forget()
        self.select_panel.pack(fill='both', pady=(0,5), expand=True)

    def _leave_select_mode(self):
        """隐藏选择角色面板，恢复左上堆叠层与左下按钮区。"""
        self.select_panel.pack_forget()
        self.panel_stack.grid()
        self.button_inner.pack(side='bottom', fill='x', padx=12, pady=0)
        self.button_hint.pack(side='bottom', fill='x', padx=12, pady=0)

    def switch_to_params_panel(self):
        self._loading_character = False
        self.state_panel.stop_auto_recovery()

        # 先完成内部创建或更新，再进行视图切换，避免主题/面板切换闪烁
        self.current_panel = "params"
        self.current_state = None
        self._update_report_cost_label()
        self.report_panel.clear()

        self.intro_panel.reset_state_mode()
        if self._cached_params_intro:
            hidden, visible, tags, birthday, image_path = self._cached_params_intro
            self.params_panel.set_intro_data(hidden, visible, tags)
            self.params_panel.set_birthday(birthday)
            self.params_panel.uploaded_image_path = image_path
            self.params_panel.update_image_status_display()
            self._cached_params_intro = None
        self.intro_panel.refresh_display()
        self.intro_panel.refresh_image_display()

        # 视图切换（内容就绪后再显示）
        self._show_panel(self.params_panel)
        self._repack_action_buttons()
        self.action_btn.configure(
            text="✨ 创建角色",
            border_color=STATUS_OK,
            text_color=STATUS_OK,
            hover_color=OK_HOVER
        )

    def switch_to_state_panel(self, state_data=None):
        # 先完成内部创建或更新，再进行视图切换，避免主题/面板切换闪烁
        if state_data is not None:
            self.state_panel.update_state(state_data)
        self._update_report_cost_label()

        # 视图切换（内容就绪后再显示）
        self._show_panel(self.state_panel)
        self.state_panel.start_auto_recovery()
        self.action_btn.pack_forget()
        self.current_panel = "state"

        # 进入状态面板后刷新介绍面板（含头像），保证新创建/加载的角色形象立即可见
        if self.current_state is not None:
            self.intro_panel.refresh_display()
            self.intro_panel.refresh_image_display()

    def switch_to_select_panel(self):
        self._loading_character = False
        self.state_panel.stop_auto_recovery()
        self.intro_panel.pack_forget()

        # 先完成内部创建或更新，再整体切换到占据整个左栏的选择面板
        self.select_panel.reset()

        self._enter_select_mode()
        self.current_panel = "select"

    def _back_from_select(self):
        self.select_panel.reset()
        self._leave_select_mode()
        if self.current_state:
            self.switch_to_state_panel(self.current_state)
            self.intro_panel.refresh_display()
            self.intro_panel.refresh_image_display()
        else:
            self.switch_to_params_panel()
        self.intro_panel.pack(fill='x', padx=5, pady=(0, 5))

    def _on_character_selected(self, giantess_id):
        if getattr(self, '_loading_character', False):
            return
        self._loading_character = True
        self._leave_select_mode()
        self.intro_panel.pack(fill='x', padx=5, pady=(0, 5))
        self.app.load_character_by_id(giantess_id)

    def _on_action_btn_click(self):
        if self.current_panel == "params":
            self._create_character()

    # ---------- 核心功能 ----------
    def _generate_giantess(self):
        if self.current_state:
            consume = self.current_state.report_generated
            report = self.context.report_from_core_or_character(
                self.current_state,
                self.context.selected_styles,
                self.context.selected_quip_styles,
                consume_points=consume,
                state_service=self.context.state_service
            )
            if report:
                self.current_state.report_generated = True
                self.context.character_repo.save(self.current_state)
        else:
            params = self.params_panel.get_params()
            core = self.context.creation_service.core_from_params(
                params, self.context.settings,
                self.context.preset_repo, self.context.personality_repo
            )
            if core is None:
                ui.common.dialogs.showerror("错误", "参数无效，请检查输入。")
                return
            report = self.context.report_from_core_or_character(
                core,
                self.context.selected_styles,
                self.context.selected_quip_styles,
                consume_points=False
            )
        if report is None:
            ui.common.dialogs.showerror("点数不足", "行动点数不足以生成报告。")
            return
        self.report_panel.render_report(report)
        if self.current_state:
            self.state_panel.update_state(self.current_state)
        self._update_report_cost_label()

        if (self.current_state is not None
                and self.app.settings.get("auto_save_report", False)):
            self.report_panel.save_report_to_file(
                self.current_state.giantess_id,
                self.current_state.name
            )
            self.report_panel.mark_saved()

    def _update_report_cost_label(self):
        if self.current_state is None:
            self.report_cost_label.pack_forget()
            return
        report_cost = 5 * int(self.app.settings.get("comparison_count", 5))
        if self.current_state.report_generated:
            self.report_cost_label.configure(text=f"- {report_cost} AP")
        else:
            self.report_cost_label.configure(text="- 0 AP")
        self.report_cost_label.pack(side='left', padx=(300,0), pady=(2, 0))

    def _create_character(self):
        last_report = self.report_panel.last_report
        if last_report and ui.common.dialogs.askyesno("使用已有数据", "是否使用当前报告数据创建角色？"):
            snapshot = self.context.character_from_core_or_report(last_report)
        else:
            self.report_panel.clear()

            params = self.params_panel.get_params()
            core = self.context.creation_service.core_from_params(
                params, self.context.settings,
                self.context.preset_repo, self.context.personality_repo
            )
            if core is None:
                ui.common.dialogs.showerror("错误", "参数无效，请检查输入。")
                return
            snapshot = self.context.character_from_core_or_report(core)

        self.current_state = snapshot
        self.switch_to_state_panel(snapshot)

    # ---------- 副本启动 ----------
    def _start_dungeon(self):
        if self.current_state:
            data = self.context.dungeon_data_from_any(self.current_state)
        else:
            params = self.params_panel.get_params()
            core = self.context.creation_service.core_from_params(
                params, self.context.settings,
                self.context.preset_repo, self.context.personality_repo
            )
            if core is None:
                return
            data = self.context.dungeon_data_from_any(core)
        self._launch_dungeon_with_data(data)

    def _launch_dungeon_with_data(self, data: dict):
        choice = self._choose_dungeon()
        if choice is None:
            return
        dungeon_id, replay_data = choice

        ai_config = resolve_ai_config(self.app.settings)
        from ui.common.fonts import dungeon_font_default
        dungeon_font = self.app.settings.get("dungeon_font", dungeon_font_default())

        if replay_data is not None:
            DungeonSessionWindow(
                self, name=data["name"], nick=data.get("nick", ""),
                height=data["height"], personality=data["personality_obj"],
                preset=data.get("preset_obj"), greed=data.get("greed", 0),
                original_height=data.get("original_height", 1.6),
                intro_hidden=data.get("intro_hidden", ""),
                intro_visible=data.get("intro_visible", ""),
                tags=data.get("selected_tags", []),
                uploaded_image=data.get("uploaded_image"),
                dungeon_config=None, dungeon_repo=self.app._dungeon_repo,
                merged_landmarks=self.context.merged_landmarks,
                merged_quips=self.context.quips,
                selected_styles=self.context.selected_styles,
                selected_quip_styles=self.context.selected_quip_styles,
                detail_pools=self.context.detail_pools,
                ai_config=ai_config,
                is_replay=True, replay_data=replay_data,
                dungeon_font=dungeon_font,
                body_parts=data.get("body_parts", {}),
                character=self.current_state,
                character_repo=self.app._character_repo,
                gui=self.app
            )
            return

        config = self.app._dungeon_repo.load_config(dungeon_id)
        if config is None:
            ui.common.dialogs.showerror("错误", f"无法加载副本配置 '{dungeon_id}'")
            return

        # 进入消耗：探索模式且已加载角色时按副本配置扣除行动点数
        if self.current_state is not None:
            try:
                entry_cost = max(0, int(config.get("entry_action_cost", 0) or 0))
            except (TypeError, ValueError):
                entry_cost = 0
            if entry_cost > 0:
                if not self.context.state_service.consume_action_points(self.current_state, entry_cost):
                    ui.common.dialogs.showerror(
                        "行动点数不足",
                        f"进入该副本需要 {entry_cost} 行动点数，当前仅剩 {self.current_state.action_points} 点。")
                    return
                self.app._character_repo.save(self.current_state)
                self.state_panel.update_state(self.current_state)
                self._update_report_cost_label()

        DungeonSessionWindow(
            self, ai_config=ai_config, name=data["name"],
            nick=data.get("nick", ""), height=data["height"],
            personality=data["personality_obj"], preset=data.get("preset_obj"),
            greed=data.get("greed", 0),
            original_height=data.get("original_height", 1.6),
            intro_hidden=data.get("intro_hidden", ""),
            intro_visible=data.get("intro_visible", ""),
            tags=data.get("selected_tags", []),
            uploaded_image=data.get("uploaded_image"),
            dungeon_config=config, dungeon_repo=self.app._dungeon_repo,
            merged_landmarks=self.context.merged_landmarks,
            merged_quips=self.context.quips,
            selected_styles=self.context.selected_styles,
            selected_quip_styles=self.context.selected_quip_styles,
            detail_pools=self.context.detail_pools,
            dungeon_id=dungeon_id,
            is_replay=False, replay_data=None,
            dungeon_font=dungeon_font,
            body_parts=data.get("body_parts", {}),
            character=self.current_state,
            character_repo=self.app._character_repo,
            gui=self.app
        )

    def _choose_dungeon(self) -> Optional[Tuple[str, Optional[List[dict]]]]:
        dungeons = self.app._dungeon_repo.list_all()
        if not dungeons:
            ui.common.dialogs.showwarning("警告", "没有可用的副本方案，请先在副本编辑器中创建。")
            return None

        dialog = BaseDialog(self)
        dialog.title("选择副本或加载回放")
        dialog.geometry("350x250")
        dialog.transient(self)
        dialog.grab_set()

        ctk.CTkLabel(dialog, text="请选择副本方案：").pack(pady=10)
        combo = ctk.CTkOptionMenu(dialog, values=dungeons, width=200)
        combo.pack(pady=5)

        result = [None, None]

        def confirm():
            result[0] = combo.get()
            result[1] = None
            dialog.destroy()

        def load_replay():
            file_path = filedialog.askopenfilename(
                title="选择回放文件",
                filetypes=[("副本回放", "*.replay.json"), ("所有文件", "*.*")]
            )
            if not file_path:
                return
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if not isinstance(data, list) or not data:
                    raise ValueError("回放文件格式错误")
                result[0] = None
                result[1] = data
                dialog.destroy()
            except Exception as e:
                ui.common.dialogs.showerror("错误", f"加载回放失败：{e}")

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=15)
        ctk.CTkButton(btn_frame, text="进入副本", command=confirm, width=100).pack(side='left', padx=10)
        ctk.CTkButton(btn_frame, text="加载回放", command=load_replay, width=100).pack(side='left', padx=10)
        ctk.CTkButton(btn_frame, text="取消", command=dialog.destroy, width=80).pack(side='left', padx=10)

        dialog._center_dialog(self)
        dialog.wait_window()
        if result[0] is None and result[1] is None:
            return None
        return (result[0], result[1])

    # ---------- 面板辅助 ----------
    def update_theme(self, mode=None):
        """同步刷新探索模式中需要原生 Tk 配色的控件，不重载面板。"""
        mode = mode or ctk.get_appearance_mode()
        self.params_panel.update_theme(mode)
        self.intro_panel.update_theme(mode)
        self.state_panel.update_theme(mode)
        self.select_panel.update_theme(mode)
        self.report_panel.update_theme(mode)

    def _jump_to_style_settings(self):
        self.app.show_page("settings")
        self.app.root.after(100, lambda: self.app.settings_panel.scroll_to_style_section())

    def update_world_setting(self, world_setting: str):
        self.params_panel.update_world_setting(world_setting)

    def set_world_active(self, active: bool):
        """世界包加载时探索模式标题保持不变（外观由导航栏统一呈现）。"""
        pass

    def refresh_style_hint(self):
        styles = self.context.selected_styles
        if styles:
            preview = ", ".join(styles[:3])
            if len(styles) > 3:
                preview += f" 等{len(styles)}个"
            self.landmark_hint_label.configure(text=f"🏔 地标: {preview}")
        else:
            self.landmark_hint_label.configure(text="🏔 地标: 未选择")

        quip_styles = self.context.selected_quip_styles
        if quip_styles:
            preview = ", ".join(quip_styles[:3])
            if len(quip_styles) > 3:
                preview += f" 等{len(quip_styles)}个"
            self.quip_hint_label.configure(text=f"💬 描述: {preview}")
        else:
            self.quip_hint_label.configure(text="💬 描述: 未选择")

    def refresh_dropdowns(self):
        self.params_panel.update_personality_combo()
        self.params_panel.update_preset_combo()

    def load_character_by_id(self, state: CharacterSnapshot):
        self.current_state = state
        self.report_panel.clear()

        self._cached_params_intro = (
            self.params_panel.intro_hidden,
            self.params_panel.intro_visible,
            self.params_panel.selected_tags.copy(),
            self.params_panel.birthday_var.get(),
            self.params_panel.uploaded_image_path,
        )

        self.switch_to_state_panel(state)
        self.intro_panel.refresh_display()
        self.intro_panel.refresh_image_display()
        self._update_report_cost_label()
