# ui/exploration/exp_frame.py
import math

import ui.common.dialogs

import customtkinter as ctk

from dungeon.window import DungeonSessionWindow
from core.ai import resolve_ai_config
from ui.exploration.giantess_state import GiantessStatePanel
from ui.exploration.creation_params import CreationParamsPanel
from ui.exploration.select_character import SelectCharacterPanel
from ui.exploration.intro import IntroPanel
from ui.exploration.report import ReportPanel
from ui.exploration.chat_panel import ChatPanel
from services.chat import events as chat_events
from services.chat import ChatService, pending_char_messages
from ui.common.dialogs import BaseDialog
from ui.common.theme import (
    EXP_BG, EXP_BORDER, EXP_BORDER_STRONG, EXP_HOVER,
    EXP_TEXT_SOFT, EXP_TEXT_MUTED, EXP_TEXT_DISABLED, EXP_OK,
    EXP_OK_HOVER, EXP_ERR, EXP_ERR_HOVER, EXP_REPORT,
    EXP_REPORT_HOVER, EXP_DUNGEON, EXP_DUNGEON_HOVER, EXP_STYLE_SELECTED_BORDER,
)
from ui.common import fonts as ui_fonts
from core.models import CharacterSnapshot
from services.exploration.context import ExplorationContext
from core.address_model import world_of, distance_m, format_addr_verbose


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
        self._chat_open = False
        self._unread_chat = 0
        self._chat_service = ChatService()
        # 启动投递调度器（接管上次会话遗留的 queued 消息；进程级单例）
        from services.chat.delivery import get_scheduler
        get_scheduler(self._chat_service)

        self._build_ui()

        self.update_world_setting(self.context.world_setting)
        self.refresh_style_hint()

        # 聊天事件广播：后台线程发来，经 after 投递回主线程刷新徽标
        chat_events.subscribe(self._on_chat_event)
        self.bind("<Destroy>", self._on_chat_events_detach, add="+")

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
                                        text_color=EXP_TEXT_SOFT)
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
            text_color=EXP_REPORT)
        left_side = ctk.CTkFrame(self.button_inner, fg_color="transparent")
        left_side.pack(side='left')
        select_btn = ctk.CTkButton(left_side, text="加载角色", width=100,
                                   font=ui_fonts.ui_font(12),
                                   fg_color="transparent",
                                   text_color=EXP_TEXT_MUTED,
                                   hover_color=EXP_HOVER,
                                   border_color=EXP_BORDER_STRONG,
                                   border_width=1,
                                   corner_radius=6,
                                   command=self.switch_to_select_panel)
        select_btn.pack(side='left', padx=3, pady=(0,5))

        right_side = ctk.CTkFrame(self.button_inner, fg_color="transparent")
        right_side.pack(side='right')

        btn_cr = EXP_OK
        btn_ch = EXP_OK_HOVER
        self.action_btn = ctk.CTkButton(
            right_side, text="✨ 创建角色", width=100,
            font=ui_fonts.ui_font(12),
            fg_color="transparent",
            border_width=1,
            border_color=btn_cr,
            text_color=btn_cr,
            hover_color=btn_ch,
            corner_radius=6,
            command=self._on_action_btn_click
        )
        self.action_btn.pack(side='left', padx=4, pady=(0,5))

        btn_rr = EXP_REPORT
        btn_rh = EXP_REPORT_HOVER
        self.report_btn = ctk.CTkButton(
            right_side, text="📜 生成报告", width=100,
            font=ui_fonts.ui_font(12),
            fg_color="transparent",
            border_width=1,
            border_color=btn_rr,
            text_color=btn_rr,
            hover_color=btn_rh,
            corner_radius=6,
            command=self._generate_giantess
        )
        self.report_btn.pack(side='left', padx=4, pady=(0,5))

        btn_dr = EXP_DUNGEON
        btn_dh = EXP_DUNGEON_HOVER
        self.dungeon_btn = ctk.CTkButton(
            right_side, text="🏰 进入副本", width=100,
            font=ui_fonts.ui_font(12),
            fg_color="transparent",
            border_width=1,
            border_color=btn_dr,
            text_color=btn_dr,
            hover_color=btn_dh,
            corner_radius=6,
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
        style_frame = ctk.CTkFrame(right_frame, fg_color=EXP_BG,
                                   border_width=1, border_color=EXP_BORDER,
                                   corner_radius=10, cursor="hand2")
        style_frame.grid(row=0, column=0, sticky='ew', padx=5, pady=(0, 5))
        style_frame.bind("<Button-1>", lambda e: self._jump_to_style_settings())
        def _style_hover_enter(e):
            style_frame.configure(border_color=EXP_STYLE_SELECTED_BORDER, border_width=2)
        def _style_hover_leave(e):
            style_frame.configure(border_color=EXP_BORDER, border_width=1)

        style_frame.bind("<Enter>", _style_hover_enter)
        style_frame.bind("<Leave>", _style_hover_leave)

        style_bar = ctk.CTkFrame(style_frame, fg_color="transparent")
        style_bar.pack(fill='x', padx=8, pady=4)
        style_bar.bind("<Button-1>", lambda e: self._jump_to_style_settings())
        style_bar.bind("<Enter>", _style_hover_enter)
        style_bar.bind("<Leave>", _style_hover_leave)

        self.landmark_hint_label = ctk.CTkLabel(
            style_bar, text="🏔 地标: 未选择",
            text_color=EXP_TEXT_SOFT,
            font=ui_fonts.ui_font(12, "bold")
        )
        self.landmark_hint_label.pack(side='left', padx=(0, 14))
        self.landmark_hint_label.bind("<Button-1>", lambda e: self._jump_to_style_settings())
        self.landmark_hint_label.bind("<Enter>", _style_hover_enter)
        self.landmark_hint_label.bind("<Leave>", _style_hover_leave)

        self.quip_hint_label = ctk.CTkLabel(
            style_bar, text="💬 描述: 未选择",
            text_color=EXP_TEXT_SOFT,
            font=ui_fonts.ui_font(12, "bold")
        )
        self.quip_hint_label.pack(side='left')
        self.quip_hint_label.bind("<Button-1>", lambda e: self._jump_to_style_settings())
        self.quip_hint_label.bind("<Enter>", _style_hover_enter)
        self.quip_hint_label.bind("<Leave>", _style_hover_leave)

        click_label = ctk.CTkLabel(style_bar, text="点击切换",
                     text_color=EXP_TEXT_DISABLED,
                     font=ui_fonts.ui_font(12))
        click_label.pack(side='left', padx=(20,0))
        click_label.bind("<Enter>", _style_hover_enter)
        click_label.bind("<Leave>", _style_hover_leave)

        self._build_result_area(right_frame)

    def _build_result_area(self, parent):
        # 右侧大容器（报告正文 + 详细尺寸）已拆分为独立组件；
        # 聊天面板与报告面板共用同一格，切换显示。
        self.report_panel = ReportPanel(parent, self.app, self.context,
                                        self.params_panel, host=self)
        self.report_panel.grid(row=1, column=0, sticky='nsew', padx=5, pady=(0, 4))
        self.chat_panel = ChatPanel(parent, self.app, host=self,
                                    chat_service=self._chat_service)
        self.chat_panel.grid(row=1, column=0, sticky='nsew', padx=5, pady=(0, 4))
        self.chat_panel.grid_remove()

    # ---------- 聊天 ----------
    def toggle_chat_panel(self):
        """介绍条「💬 聊天」入口：在右栏的聊天面板与报告面板之间切换。"""
        if self.current_state is None:
            return
        if self._chat_open:
            self.close_chat_panel()
        else:
            self._open_chat_panel()

    def _open_chat_panel(self):
        self._chat_open = True
        self._unread_chat = 0
        self.intro_panel.set_chat_badge(0)
        self.chat_panel.set_state(self.current_state)
        self.report_panel.grid_remove()
        self.chat_panel.grid()

    def close_chat_panel(self):
        if not self._chat_open:
            return
        self._chat_open = False
        self.chat_panel.grid_remove()
        self.report_panel.grid()

    def on_chat_ops_applied(self):
        """AI 在聊天中改动了角色属性：刷新状态面板与介绍条。"""
        if self.current_state is not None and self.current_panel == "state":
            self.state_panel.update_state(self.current_state)
            self.intro_panel.refresh_display()

    def _on_chat_event(self, event):
        """聊天事件（后台线程）：投递回主线程处理。"""
        try:
            self.after(0, lambda: self._handle_chat_event(event))
        except Exception:
            pass

    def _handle_chat_event(self, event):
        if event.get("giantess_id") != (
                self.current_state.giantess_id if self.current_state else ""):
            return
        # AI 在聊天中改动了角色属性：刷新状态面板与介绍条
        if event.get("ops_applied") and self.current_panel == "state":
            self.state_panel.update_state(self.current_state)
            self.intro_panel.refresh_display()
        # 聊天面板关闭时：按存档重算玩家未读的角色消息（徽标唯一依据）
        if not self._chat_open:
            chat_state = self._chat_service.load_chat(
                self.current_state.giantess_id, blocking=False)
            if chat_state is not None:
                self._unread_chat = len(pending_char_messages(chat_state))
                self.intro_panel.set_chat_badge(self._unread_chat)

    def _on_chat_events_detach(self, event):
        if str(event.type) == "Destroy" and event.widget is self:
            chat_events.unsubscribe(self._on_chat_event)

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
        self.close_chat_panel()
        self._unread_chat = 0
        self.intro_panel.set_chat_badge(0)

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
            border_color=EXP_OK,
            text_color=EXP_OK,
            hover_color=EXP_OK_HOVER
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
        # 换了角色：未读徽标按存档重算（已投递但玩家未读的角色消息）；
        # 聊天面板开着时重载为新角色的历史
        pending = 0
        if self.current_state is not None:
            chat_state = self._chat_service.load_chat(
                self.current_state.giantess_id, blocking=False)
            pending = len(pending_char_messages(chat_state)) \
                if chat_state is not None else 0
        self._unread_chat = pending
        self.intro_panel.set_chat_badge(pending)
        if self._chat_open and self.current_state is not None:
            self.chat_panel.set_state(self.current_state)

    def switch_to_select_panel(self):
        self._loading_character = False
        self.state_panel.stop_auto_recovery()
        self.close_chat_panel()
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
    def _resolve_stuck_choice(self, state, stuck, ctx):
        """地址系统“无路可走”时的决策入口（切换地址 / 世界观，或放弃负向演化）。"""
        options = ctx.stuck_options(state, stuck)
        dialog = _StuckRelocateDialog(self, state, stuck, options, ctx.state_service)
        if dialog.result is None:
            self._report_aborted_stuck = True
        return dialog.result

    def _generate_giantess(self):
        if self.current_state:
            consume = self.current_state.report_generated
            report = self.context.report_from_core_or_character(
                self.current_state,
                self.context.selected_styles,
                self.context.selected_quip_styles,
                consume_points=consume,
                state_service=self.context.state_service,
                resolve_stuck=self._resolve_stuck_choice,
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
            if getattr(self, "_report_aborted_stuck", False):
                self._report_aborted_stuck = False
                return
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
        ai_config = resolve_ai_config(self.app.settings)
        from ui.common.fonts import dungeon_font_default
        from ui.common.tk_host import TkHost
        dungeon_font = self.app.settings.get("dungeon_font", dungeon_font_default())
        # 宿主适配器：副本窗口只通过端口取尺寸/DPI、显隐宿主、弹收尾提示
        host = TkHost(self)

        dungeons = self.app._scenario_repo.list_all()
        if not dungeons:
            ui.common.dialogs.showerror("错误", "请先到“副本编辑”创建副本方案")
            return

        # 探索模式：入口阶段在 DungeonSessionWindow 内部完成副本方案选择，
        # 与正式副本会话界面共享同一个 DPG 生命周期，不再创建独立窗口。
        # L4：run() 返回结果对象，调用方不再读窗口私有属性；入口页选「加载回放」
        # 也在窗口内部切 is_replay 完成，无需在这里 new 第二个窗口。
        window = DungeonSessionWindow(
            self, name=data["name"], nick=data.get("nick", ""),
            height=data["height"], personality=data["personality_obj"],
            preset=data.get("preset_obj"), greed=data.get("greed", 0),
            original_height=data.get("original_height", 1.6),
            intro_hidden=data.get("intro_hidden", ""),
            intro_visible=data.get("intro_visible", ""),
            tags=data.get("selected_tags", []),
            uploaded_image=data.get("uploaded_image"),
            scenario_config=None, scenario_repo=self.app._scenario_repo,
            merged_landmarks=self.context.merged_landmarks,
            merged_quips=self.context.quips,
            selected_styles=self.context.selected_styles,
            selected_quip_styles=self.context.selected_quip_styles,
            detail_pools=self.context.detail_pools,
            ai_config=ai_config,
            is_replay=False, replay_data=None,
            dungeon_font=dungeon_font,
            body_parts=data.get("body_parts", {}),
            character=self.current_state,
            character_repo=self.app._character_repo,
            gui=self.app,
            scenario_ids=self.app._scenario_repo.list_all(),
            host=host,
        )
        result = window.run()

        # 入口阶段失败（配置缺失/行动点数不足/校验错误）：窗口已关闭，在主线程提示
        if result.failed:
            ui.common.dialogs.showerror("错误", result.launch_error)
            return
        # 其余结果（入口返回 / 正常会话 / 回放）窗口内部已处理完，无需 UI 层介入
        return

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


class _StuckRelocateDialog(BaseDialog):
    """无路可走（地址系统）时的切换决策对话框。

    提供三种选择：前往同世界观内的另一个已注册地标地址（消耗
    0.05×距离/身高 行动点数）、切换世界观（消耗 125 行动点数，
    落到该世界观第一个可用地标地址）、或放弃并进入负向演化。
    """

    def __init__(self, host, state, stuck, options, state_service):
        super().__init__(host)
        self.title("角色无法行动")
        self.state = state
        self.stuck = stuck
        self.options = options
        self.state_service = state_service
        self.result = None
        top = host.winfo_toplevel()
        self.transient(top)
        # 抓取延到窗口可见之后（X11 要求 viewable，见 BaseDialog._grab_deferred）
        self._grab_deferred()
        self._create_widgets()
        self.geometry("560x430")
        self._center_dialog(host)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.wait_window()

    # ---------- UI ----------
    def _create_widgets(self):
        reason = self.stuck.get("reason", "")
        reason_text = {
            "world_mismatch": "当前选用的地标世界观已切换：角色的位置（世界观 "
                              f"{self.stuck.get('current_world') or '未知'}）没有可去的地址，"
                              "需要接入当前世界观或前往其它地标。",
            "no_reachable": "角色当前位置（10×身高可达范围）内没有可对比的已注册地标地址。",
            "all_damaged": "角色所在范围（50×身高×个性强度）内的独特建筑耐久均已低于 0.5。",
        }.get(reason, "角色目前没有可去的地标地址。")
        ctk.CTkLabel(self, text=f"📡 {reason_text}", font=ui_fonts.ui_font(12),
                     text_color=EXP_TEXT_SOFT, justify='left', wraplength=520).pack(
            anchor='w', padx=18, pady=(16, 6))

        addresses = self.options.get("addresses", [])
        self._entries = {}
        labels = []
        for it in addresses:
            label = self._entry_label(it)
            self._entries[label] = it
            labels.append(label)
        if labels:
            ctk.CTkLabel(self, text="可切换的地标地址：", font=ui_fonts.ui_font(12, "bold"),
                         text_color=EXP_TEXT_MUTED).pack(anchor='w', padx=18, pady=(14, 2))
            self.combo = ctk.CTkComboBox(self, values=labels, state="readonly", width=520,
                                         height=30, font=ui_fonts.ui_font(11))
            self.combo.set(labels[0])
            self.combo.pack(padx=18, pady=2)
        else:
            self.combo = None
            ctk.CTkLabel(self, text="（当前选中风格内没有已注册地标的可切换地址）",
                         font=ui_fonts.ui_font(11), text_color=EXP_TEXT_SOFT).pack(
                anchor='w', padx=18, pady=(14, 2))

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill='x', padx=18, pady=(16, 4))
        if self.combo is not None:
            ctk.CTkButton(btn_frame, text="🚶 前往该地址", width=130,
                          font=ui_fonts.ui_font(12), command=self._apply_move,
                          fg_color="transparent", border_width=1, border_color=EXP_OK,
                          text_color=EXP_OK, hover_color=EXP_OK_HOVER).pack(side='left', padx=(0, 8))
        if self.options.get("worlds"):
            ctk.CTkButton(btn_frame, text="🌍 切换世界观（125 AP）", width=200,
                          font=ui_fonts.ui_font(12), command=self._apply_world_switch,
                          fg_color="transparent", border_width=1,
                          border_color=EXP_DUNGEON, text_color=EXP_DUNGEON,
                          hover_color=EXP_DUNGEON_HOVER).pack(side='left', padx=(0, 8))
        ctk.CTkButton(btn_frame, text="💤 放弃（负向演化）", width=170,
                      font=ui_fonts.ui_font(12), command=self._decline,
                      fg_color="transparent", border_width=1, border_color=EXP_ERR,
                      text_color=EXP_ERR, hover_color=EXP_ERR_HOVER).pack(side='left')

        ctk.CTkLabel(self,
                     text="切换地址消耗 = 0.05 × 距离 ÷ 身高 的行动点数；不切换则进入负向演化。",
                     font=ui_fonts.ui_font(10), text_color=EXP_TEXT_DISABLED).pack(
            anchor='w', padx=18, pady=(10, 0))

    def _entry_label(self, it) -> str:
        """构造地址下拉项：地标名 + 位置 + 所需行动点数。"""
        addr = it["address"]
        name = it.get("name", "")
        verbose = format_addr_verbose(addr)
        base = f"{name}　{verbose}"
        if self.state.position:
            d = distance_m(self.state.position, addr)
            if d is None:
                return f"{base}　（跨世界观，请用「切换世界观」）"
            cost = self._move_cost(d)
            dist_text = f"{d / 1000:.1f} 千米" if d >= 10000 else f"{d:.0f} 米"
            return f"{base}　距离 {dist_text}　-{cost} AP"
        return f"{base}　（锚定位置，0 AP）"

    def _move_cost(self, distance_meters: float) -> int:
        return max(1, int(math.ceil(0.05 * distance_meters / max(0.01, self.state.height))))

    def _apply_move(self):
        if not self.combo or not self._entries:
            ui.common.dialogs.showwarning("提示", "没有可前往的地址。")
            return
        it = self._entries.get(self.combo.get())
        if it is None:
            return
        d = distance_m(self.state.position, it["address"]) if self.state.position else None
        if d is None:
            ui.common.dialogs.showerror("无法直接前往", "该地址与角色位置不在同一世界观，请使用「切换世界观」。")
            return
        cost = self._move_cost(d)
        if not self.state_service.consume_action_points(self.state, cost):
            ui.common.dialogs.showerror(
                "行动点数不足",
                f"前往该地址需要 {cost} 行动点数，当前仅剩 {self.state.action_points}。\n"
                "无法切换时将进入负向演化。")
            self._close_with(None)
            return
        self._close_with({"kind": "address", "address": it["address"]})

    def _apply_world_switch(self):
        worlds = self.options.get("worlds") or []
        if not worlds:
            ui.common.dialogs.showwarning("提示", "当前没有其它世界观可供切换。")
            return
        target_world = worlds[0]
        anchor = None
        for it in self.options.get("addresses", []):
            if world_of(it["address"]) == target_world:
                anchor = it["address"]
                break
        if anchor is None:
            ui.common.dialogs.showerror("无法切换世界观", "目标世界观内没有已注册地标可作为落脚点。")
            return
        if not self.state_service.consume_action_points(self.state, 125):
            ui.common.dialogs.showerror(
                "行动点数不足",
                f"切换世界观需要 125 行动点数，当前仅剩 {self.state.action_points}。\n"
                "无法切换时将进入负向演化。")
            self._close_with(None)
            return
        self._close_with({"kind": "world", "address": anchor})

    def _decline(self):
        self._close_with(None)

    def _close_with(self, result):
        self.result = result
        self._on_close()

    def _on_close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.withdraw()
        self.destroy()
