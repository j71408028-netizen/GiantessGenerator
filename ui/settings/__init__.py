import random
import uuid
import tkinter as tk
import tkinter.font as tkfont
from typing import Dict, Callable, Optional, TYPE_CHECKING
from ui.settings.dialogs import AIConfigDialog

import customtkinter as ctk

import ui.ctk.dialogs

if TYPE_CHECKING:
    from persistence.settings_repo import SettingsRepo

from core.logic import ALL_PART_NAMES, normalize_blocked_words
from core import netproxy
from persistence import DungeonSettingsRepo
from dungeon.audio import buses
import services.ui_mode as ui_mode
from ui.ctk.widgets import CollapsibleBlock, OptionButton
from ui.ctk import fonts as ui_fonts
from ui.ctk.theme import (
    SETTINGS_PANEL_BG, SETTINGS_HOVER, SETTINGS_CHECKBOX_HOVER, SETTINGS_INPUT_HOVER,
    SETTINGS_INPUT_BG, SETTINGS_INPUT_BORDER,
    SETTINGS_SWITCH_TRACK, SETTINGS_SWITCH_PROGRESS, SETTINGS_SWITCH_KNOB, SETTINGS_TEXT_SOFT,
    SETTINGS_LABEL,
)
from ui.ctk.theme import (
    DEFAULT_PALETTE, current_palette, palette_display_names,
)

_FONT = ui_fonts.ui_font(13)

# 信息更新覆写概率四档：显示名 → 覆写概率
INFO_UPDATE_OPTIONS = {"保守": 0.25, "中等": 0.5, "高效": 0.75, "激进": 1.0}



class SettingsPanel(ctk.CTkScrollableFrame):
    def __init__(
            self,
            parent,
            settings_repo: 'SettingsRepo',
            initial_settings: Dict,
            on_world_setting_changed: Optional[Callable] = None,
            on_return_callback: Optional[Callable] = None,
            gui_ref=None,
            world_manager=None,
            **kwargs
    ):
        super().__init__(parent, fg_color="transparent", **kwargs)
        self.settings_repo = settings_repo
        # 副本独占设置（音量 / 段落字体 / 记忆窗口 / 自动回放 / 帧率）独立成
        # data/user/dungeon_settings.json，见 persistence/dungeon_settings_repo.py。
        self.dungeon_settings_repo = DungeonSettingsRepo()
        self.dungeon_settings = self.dungeon_settings_repo.load()
        self.settings = initial_settings
        self.on_world_setting_changed = on_world_setting_changed
        self.on_return_callback = on_return_callback
        self.gui_ref = gui_ref
        self.world_manager = world_manager
        self.world_state = getattr(world_manager, "world_state", None) \
            if world_manager is not None else None
        self.parts_checkboxes = {}

        self.theme_mode = self.settings.get("theme_mode", "Light")
        # 界面配色（assets/theme/ 下的一套 token）。默认取当前已生效的配色，
        # 这样即使设置里没存过也不会与界面实际配色不一致。
        self.theme_palette = self.settings.get("theme_palette") or current_palette()
        self.user_seed = self.settings.get("seed", 0)
        self.comparison_count = self.settings.get("comparison_count", 5)
        self.comparison_order = self.settings.get("comparison_order", "match")
        self.world_setting = self.settings.get("world_setting", "appear")
        self.ai_configs = self._load_ai_configs()
        self.ai_provider = self.settings.get("ai_provider", "zhipu")
        if self.ai_provider not in self.ai_configs:
            self.ai_provider = next(iter(self.ai_configs), "")
        self.enable_confusion = self.settings.get("enable_confusion", False)
        self.quip_rate_factor = self.settings.get("quip_rate_factor", 1.0)
        self.info_update_rate = self.settings.get("info_update_rate", 0.5)
        self.blocked_words = normalize_blocked_words(self.settings.get("blocked_words", []))
        # 音频总线玩家电平（总音量 / 音乐 / 语音 / 音效），见 dungeon/audio/buses.py
        self.audio_levels = buses.levels_from_settings(self.dungeon_settings)
        self._bus_level_vars = {}
        self._bus_level_labels = {}

        self._create_widgets()
        self._sync_parts_states()

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
            self.content, fg_color=SETTINGS_PANEL_BG,
            border_width=0, corner_radius=6)
        self.left_col.grid(row=0, column=0, sticky='nsew', padx=5)
        self.right_col = ctk.CTkFrame(
            self.content, fg_color=SETTINGS_PANEL_BG,
            border_width=0, corner_radius=6)
        self.right_col.grid(row=0, column=1, sticky='nsew', padx=(12,0))

        self.world_block = CollapsibleBlock(self.left_col, "世界", body_padx=(6,12), body_pady=(6,12),
                                            on_toggle=self._schedule_fit)
        self.gen_block = CollapsibleBlock(self.left_col, "生成", body_padx=(6, 12), body_pady=(6, 12),
                                          on_toggle=self._schedule_fit)

        self.world_header = self.world_block.header
        self.world_body = self.world_block.body
        self.gen_header = self.gen_block.header
        self.gen_body = self.gen_block.body

        self.disp_block = CollapsibleBlock(self.right_col, "外观", body_padx=(6, 12), body_pady=(6, 12),
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

    # ───────────────── 通用控件辅助 ─────────────────
    @staticmethod
    def _setup_body_grid(body, name):
        """块体内统一两列：0=标签列，1=控件列，跨行对齐。"""
        body.columnconfigure(0, weight=2, uniform=name + "_cols")
        body.columnconfigure(1, weight=3, uniform=name + "_cols")

    def _section_label(self, parent, row, text):
        ctk.CTkLabel(
            parent, text=text,
            font=ui_fonts.ui_font(13, "bold"),
            text_color=SETTINGS_TEXT_SOFT
        ).grid(row=row, column=0, columnspan=2, sticky='w',
               padx=10, pady=6)

    def _make_row(self, parent, row, text):
        """创建一行：标签在左，控件容器在右，两列对齐。"""
        label = ctk.CTkLabel(
            parent, text=text, anchor='w',
            font=_FONT, text_color=SETTINGS_LABEL
        )
        label.grid(row=row, column=0, sticky='w', padx=(18, 12), pady=(0, 8))
        control = ctk.CTkFrame(parent, fg_color="transparent")
        control.grid(row=row, column=1, sticky='e', padx=(0, 18), pady=(0, 8))
        return label, control

    @staticmethod
    def _pack_control(control, expand=True):
        if expand:
            control.pack(fill='both', expand=True)
        else:
            control.pack(side='left')
        return control

    def _make_option_button(self, parent, values, command, variable=None, expand=True):
        """行内选项控件统一用 widgets.OptionButton：按钮 + 顶部锚定下拉列表。"""
        return self._pack_control(OptionButton(
            parent, values=values, command=command, variable=variable,
            width=120, height=28, corner_radius=7, font=_FONT,
            fg_color=SETTINGS_INPUT_BG, border_color=SETTINGS_INPUT_BORDER,
            hover_color=SETTINGS_INPUT_HOVER, text_color=SETTINGS_LABEL,
            text_color_disabled=SETTINGS_TEXT_SOFT,
            popup_fg_color=SETTINGS_INPUT_BG,
            popup_hover_color=SETTINGS_INPUT_HOVER,
            popup_text_color=SETTINGS_LABEL,
        ), expand)

    def _make_entry(self, parent, variable, width=120, show=None, placeholder_text=None):
        return ctk.CTkEntry(
            parent, textvariable=variable, width=width, height=28, show=show,
            placeholder_text=placeholder_text or "",
            border_width=1, border_color=SETTINGS_INPUT_BORDER,
            fg_color=SETTINGS_INPUT_BG, corner_radius=8, font=_FONT
        )

    def _make_switch(self, parent, variable):
        switch = ctk.CTkSwitch(
            parent, text="", variable=variable, width=44, height=22,
            progress_color=SETTINGS_SWITCH_PROGRESS, fg_color=SETTINGS_SWITCH_TRACK,
            button_color=SETTINGS_SWITCH_KNOB, button_hover_color=SETTINGS_SWITCH_KNOB
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
        self.world_setting_button = self._make_option_button(
            ctrl, list(world_options.keys()),
            self._on_world_option_changed, self.world_setting_var
        )
        for display, value in world_options.items():
            if value == self.world_setting:
                self.world_setting_button.set(display)
                break

        _, ctrl = self._make_row(self.world_body, row, "种子")
        row += 1
        self.seed_var = tk.StringVar(value=str(self.user_seed) if self.user_seed != 0 else "")
        self.seed_entry = self._make_entry(ctrl, self.seed_var, width=100)
        self.seed_entry.pack(side='left')
        self.seed_random_btn = ctk.CTkButton(
            ctrl, text="随机", width=60, height=28,
            command=self._randomize_seed,
            fg_color="transparent", text_color=SETTINGS_TEXT_SOFT,
            hover_color=SETTINGS_HOVER,
            border_width=1, border_color=SETTINGS_INPUT_BORDER,
            corner_radius=8, font=ui_fonts.ui_font(12)
        )
        self.seed_random_btn.pack(side='left', padx=(8,0))

        self._set_world_controls_locked()

    # ───────────────── 世界包联动（规则段） ─────────────────
    def _set_world_controls_locked(self):
        """按包锁定的设置键禁用规则段控件（静态表菜单的锁在资源中心页）。"""
        locked = self.world_state.locked_keys() if self.world_state is not None else set()
        self.world_setting_button.configure(
            state="disabled" if "world_setting" in locked else "normal")
        self.seed_entry.configure(state="disabled" if "seed" in locked else "normal")
        self.seed_random_btn.configure(state="disabled" if "seed" in locked else "normal")

    def _sync_world_vars(self):
        """激活/卸载/解散后，按 self.settings 刷新规则段控件（世界包 settings
        可能改写体型/种子）。"""
        world_options = {"出现": "appear", "绝对巨大化": "abs_giant", "相对巨大化": "rel_giant"}
        world_setting = self.settings.get("world_setting", "appear")
        for display, value in world_options.items():
            if value == world_setting:
                self.world_setting_button.set(display)
                break
        self.world_setting = world_setting

        seed = self.settings.get("seed", 0)
        self.seed_var.set(str(seed) if seed != 0 else "")
        self.user_seed = seed
        self._set_world_controls_locked()

    def on_world_state_changed(self):
        """世界包状态变化后的本页刷新（由 gui_ref.refresh_world_ui 调度）。"""
        self._sync_world_vars()

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
        self.order_button = self._make_option_button(
            ctrl, list(order_options.keys()),
            self._on_order_option_changed, self.order_var
        )
        for display, value in order_options.items():
            if value == self.comparison_order:
                self.order_button.set(display)
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
            ctrl, self.blocked_words_var, width=190,
            placeholder_text="（逗号分隔）"
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

        self._section_label(self.gen_body, row, "副本引擎")
        row += 1

        _, ctrl = self._make_row(self.gen_body, row, "记忆窗口大小")
        row += 1
        self.story_recent_var = tk.StringVar(
            value=str(self.dungeon_settings.get("story_recent_count", 20)))
        self.story_recent_entry = self._make_entry(ctrl, self.story_recent_var, width=64)
        self.story_recent_entry.pack(side='left')

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

        _, ctrl = self._make_row(self.disp_body, row, "色彩")
        row += 1
        palette_options = {display: name
                           for name, display in palette_display_names().items()}
        self.palette_var = tk.StringVar(value=self.theme_palette)
        self.palette_menu = self._make_option_button(
            ctrl, list(palette_options.keys()),
            self._on_palette_option_changed, self.palette_var
        )
        for display, value in palette_options.items():
            if value == self.theme_palette:
                self.palette_menu.set(display)
                break

        _, ctrl = self._make_row(self.disp_body, row, "主题模式")
        row += 1
        theme_options = {"亮色": "Light", "暗色": "Dark", "跟随系统": "System"}
        self.theme_mode_var = tk.StringVar(value=self.theme_mode)
        self.theme_mode_button = self._make_option_button(
            ctrl, list(theme_options.keys()),
            self._on_theme_option_changed, self.theme_mode_var
        )
        for display, value in theme_options.items():
            if value == self.theme_mode:
                self.theme_mode_button.set(display)
                break

        self._section_label(self.disp_body, row, "界面选项")
        row += 1

        _, ctrl = self._make_row(self.disp_body, row, "显示伤亡统计")
        row += 1
        self.show_casualties_var = tk.BooleanVar(value=self.settings.get("show_casualties", True))
        self.show_casualties_switch = self._make_switch(ctrl, self.show_casualties_var)

        # 启动界面模式：只决定**下次启动**用哪套界面，点这里不重建窗口。
        # 「退出时模式」跟随上次退出时所在的界面
        # （见 services.ui_mode.resolve_startup_mode）。
        _, ctrl = self._make_row(self.disp_body, row, "启动界面模式")
        row += 1
        pref = self.settings.get(ui_mode.STARTUP_MODE_KEY, ui_mode.STARTUP_EXIT)
        self.ui_mode_var = tk.StringVar(value=ui_mode.label_of(pref))
        self.ui_mode_button = self._make_option_button(
            ctrl, [label for label, _pref in ui_mode.STARTUP_CHOICES],
            self._on_ui_mode_changed, self.ui_mode_var)

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
        self.dungeon_font_var = tk.StringVar(value=self.dungeon_settings.get("dungeon_font") or ui_fonts.dungeon_font_default())
        self.dungeon_font_entry = self._make_entry(ctrl, self.dungeon_font_var, width=150)
        self.dungeon_font_entry.pack(side='left')

        # 「朗读副本对话」总开关已撤掉：不想听就把下面「音量」里的语音拧到 0
        self._build_audio_section(self.disp_body, row)

    def _build_audio_section(self, parent, row):
        """音量：四条总线各一根 0-100 滑条（缺省 100 = 不衰减）。

        与方案里的「档位」不同——档位是作者配内容时选的响度，这里拧的是玩家
        自己那层总电平；两者相乘，见 ``dungeon/audio/buses.py`` 的三层单位表。
        滑条只改内存中的待保存值，点「保存并返回」才落盘。
        """
        self._section_label(parent, row, "音量")
        row += 1
        for bus in buses.BUSES:
            _, ctrl = self._make_row(parent, row, buses.BUS_LABELS[bus])
            row += 1
            var = tk.IntVar(value=self.audio_levels.get(bus, buses.MAX_LEVEL))
            ctk.CTkSlider(
                ctrl, from_=0, to=buses.MAX_LEVEL,
                number_of_steps=buses.MAX_LEVEL // 5, variable=var, width=120,
                command=lambda value, b=bus: self._on_bus_level_change(b, value),
            ).pack(side='left')
            label = ctk.CTkLabel(
                ctrl, text=str(var.get()), width=30, anchor='w',
                font=_FONT, text_color=SETTINGS_LABEL)
            label.pack(side='left', padx=(6, 0))
            self._bus_level_vars[bus] = var
            self._bus_level_labels[bus] = label

    def _on_bus_level_change(self, bus, value):
        label = self._bus_level_labels.get(bus)
        if label is not None:
            label.configure(text=str(int(float(value))))

    def _sync_audio_levels(self):
        """按内存里的副本设置刷新音频滑条（面板常驻，需显式回读）。"""
        self.audio_levels = buses.levels_from_settings(self.dungeon_settings)
        for bus, var in self._bus_level_vars.items():
            level = self.audio_levels.get(bus, buses.MAX_LEVEL)
            var.set(level)
            self._bus_level_labels[bus].configure(text=str(level))

    def _sync_dungeon_settings(self):
        """进入设置页时回读副本独占设置并刷新相关控件。

        设置页常驻，而副本窗口本身也能改这份设置（``dungeon_settings.json``），
        所以进页时从仓库重读一次，避免显示陈旧值。
        """
        self.dungeon_settings = self.dungeon_settings_repo.load()
        self.dungeon_font_var.set(self.dungeon_settings.get("dungeon_font")
                                  or ui_fonts.dungeon_font_default())
        self.story_recent_var.set(
            str(self.dungeon_settings.get("story_recent_count", 20)))
        self.auto_save_replay_var.set(
            bool(self.dungeon_settings.get("auto_save_replay", False)))
        self._sync_audio_levels()

    def _on_ui_mode_changed(self, value):
        """记录「下次启动用哪套界面」，仅此而已。

        这个选项**只影响启动**：点击只把选择写进设置，不重建窗口。界面重建会
        丢掉当前界面上的编辑状态，所以实时切换不放在设置页——那是导航栏底部
        「切换界面」的职责（见 ``MainWindowManager.prompt_switch_to_mini``）。

        「退出时模式」意味着下次启动跟随上次退出时所在的界面——实际模式由
        ``app.shell.switch_to`` 在每次切换时落盘（``ui_mode`` 键），这里不碰它。
        """
        # 词表与落盘都在共享的 services.ui_mode（ui 层不得 import 应用外壳）。
        pref = ui_mode.pref_of_label(value)
        # 同步内存里的设置副本：本页「保存并返回」会把 settings 整份回写，
        # 不同步就会把这里刚选的值覆盖回旧值。
        self.settings[ui_mode.STARTUP_MODE_KEY] = pref
        ui_mode.save_startup_mode(pref)

    def _story_recent_count_value(self) -> int:
        """剧情概要保留的最近段落数（1~200，非法输入回退到原值/20）。"""
        try:
            value = int(float(self.story_recent_var.get().strip()))
        except (ValueError, AttributeError):
            return int(self.dungeon_settings.get("story_recent_count", 20) or 20)
        return max(1, min(200, value))

    def _build_archive_block(self):
        self._setup_body_grid(self.archive_body, "archive")
        row = 0

        _, ctrl = self._make_row(self.archive_body, row, "自动保存报告")
        row += 1
        self.auto_save_report_var = tk.BooleanVar(value=self.settings.get("auto_save_report", False))
        self.auto_save_report_switch = self._make_switch(ctrl, self.auto_save_report_var)

        _, ctrl = self._make_row(self.archive_body, row, "自动保存副本回放")
        row += 1
        self.auto_save_replay_var = tk.BooleanVar(value=self.dungeon_settings.get("auto_save_replay", False))
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
        self.info_update_button = self._make_option_button(
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
                grid_frame, text=part_name, variable=var, width=95,
                font=ui_fonts.ui_font(12), text_color=SETTINGS_LABEL,
                fg_color=SETTINGS_TEXT_SOFT, hover_color=SETTINGS_CHECKBOX_HOVER,
                checkbox_width=18, checkbox_height=18, corner_radius=4
            )
            row_i = idx // cols
            col_i = idx % cols
            cb.grid(row=row_i, column=col_i, padx=2, pady=3, sticky='w')
            self.parts_checkboxes[part_name] = var

    def _build_ai_section(self, parent, row):
        # AI设置标题
        self._section_label(parent, row, "网络与 AI")
        row += 1

        # 第1行：下拉框和新建按钮并排
        _, ai_provider_control = self._make_row(parent, row, "选择配置")
        self.ai_provider_var = tk.StringVar(value=self._profile_name(self.ai_provider))
        self.ai_provider_menu = self._make_option_button(
            ai_provider_control, self._profile_names(),
            self._on_ai_provider_changed, self.ai_provider_var, expand=False
        )
        ctk.CTkButton(
            ai_provider_control, text="新建", width=60, height=28,
            command=self._new_ai_config, fg_color="transparent", text_color=SETTINGS_TEXT_SOFT,
            hover_color=SETTINGS_HOVER, border_width=1, border_color=SETTINGS_INPUT_BORDER,
            corner_radius=8, font=ui_fonts.ui_font(12)
        ).pack(side="left", padx=(8, 0))
        row += 1

        # 第2行：连接状态标签 + 配置按钮（配置按钮打开对话框，删除功能在对话框内）
        _, status_control = self._make_row(parent, row, "连接状态")
        ctk.CTkButton(
            status_control, text="配置", width=60, height=28,
            command=self._open_ai_config_dialog,
            fg_color="transparent", text_color=SETTINGS_TEXT_SOFT,
            hover_color=SETTINGS_HOVER,
            border_width=1, border_color=SETTINGS_INPUT_BORDER,
            corner_radius=8, font=ui_fonts.ui_font(12)
        ).pack(side='left', padx=(12, 0))
        row += 1

        # 网络代理（可留空）：语音合成与 AI 文本生成**共用**同一份联网代理，见
        # core/netproxy.py。留空 = 直连（或跟随环境变量 HTTPS_PROXY）；填了就立即
        # 对后续请求生效。VPN 的「系统代理」Python 看不见，那种情况必须填在这里。
        _, proxy_control = self._make_row(parent, row, "网络代理")
        self.net_proxy_var = tk.StringVar(
            value=self.settings.get("net_proxy", "") or "")
        self.net_proxy_entry = self._make_entry(
            proxy_control, self.net_proxy_var, width=190,
            placeholder_text="直连")
        self.net_proxy_entry.pack(side='left')

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
        self.ai_provider_menu.set(self._profile_name(self.ai_provider))

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

    def _on_order_option_changed(self, choice):
        order_options = {"匹配度": "match", "尺寸升序": "size_asc", "尺寸降序": "size_desc"}
        self.comparison_order = order_options.get(choice, "match")

    def _on_info_update_changed(self, choice):
        self.info_update_rate = INFO_UPDATE_OPTIONS.get(choice, 0.5)

    def _on_theme_option_changed(self, choice):
        theme_options = {"亮色": "Light", "暗色": "Dark", "跟随系统": "System"}
        self.theme_mode = theme_options.get(choice, "Light")
        self.theme_mode_var.set(choice)  # 保持显示文字

    def _on_palette_option_changed(self, choice):
        """记录待保存的界面配色；真正的换色在保存返回时由主窗口统一执行。"""
        palette_options = {display: name
                           for name, display in palette_display_names().items()}
        self.theme_palette = palette_options.get(choice, DEFAULT_PALETTE)

    def _on_world_changed(self):
        self.world_setting = self.world_setting_var.get()
        if self.on_world_setting_changed:
            self.on_world_setting_changed(self.world_setting)

    def _on_theme_changed(self):
        mode = self.theme_mode_var.get()
        self.theme_mode = mode
        # 主题只保存在编辑状态中。点击“保存并返回主页面”后，才由主窗口
        # 管理器统一写入设置并应用，避免全局实际主题与 settings 暂存值分离。

    def sync_saved_theme(self):
        """进入设置页时丢弃未保存的主题选择，显示持久化值。"""
        saved_mode = self.settings.get("theme_mode", "Light")
        self.theme_mode = saved_mode
        # 设置轮换控件显示值
        theme_options = {"亮色": "Light", "暗色": "Dark", "跟随系统": "System"}
        for display, value in theme_options.items():
            if value == saved_mode:
                self.theme_mode_button.set(display)
                break

        saved_palette = self.settings.get("theme_palette") or current_palette()
        self.theme_palette = saved_palette
        # palette_display_names() 是 {配色名: 显示名}，这里要反查显示名
        self.palette_menu.set(palette_display_names().get(saved_palette, saved_palette))
        self._sync_dungeon_settings()

    def _on_order_changed(self):
        self.comparison_order = self.order_var.get()

    def _randomize_seed(self):
        new_seed = random.randint(1, 2 ** 31 - 1)
        self.seed_var.set(str(new_seed))

    # ───────────────── 部位选择同步 ─────────────────
    def _sync_parts_states(self):
        """按 self.settings 刷新部位多选（风格列表的同步在资源中心页）。"""
        selected = self.settings.get("selected_parts", ALL_PART_NAMES)
        for part, var in self.parts_checkboxes.items():
            var.set(part in selected)

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
            "theme_palette": self.theme_palette or DEFAULT_PALETTE,
            "seed": seed,
            "comparison_count": count,
            "comparison_order": comparison_order,
            "world_setting": world_setting,
            "show_all_details": self.show_all_details_var.get(),
            "enable_confusion": self.confusion_var.get(),
            "blocked_words": normalize_blocked_words(self.blocked_words_var.get()),
            "ai_provider": self.ai_provider,
            "ai_configs": {pid: dict(cfg) for pid, cfg in self.ai_configs.items()},
            "report_font": report_font,
            "desc_font": desc_font,
            "quip_rate_factor": rate_factor,
            "show_casualties": self.show_casualties_var.get(),
            "auto_save_report": self.auto_save_report_var.get(),
            "net_proxy": self.net_proxy_var.get().strip(),
            "save_low_resolution_image": self.save_low_resolution_var.get(),
            "use_preview_image_as_avatar": self.use_preview_avatar_var.get(),
            "info_update_rate": INFO_UPDATE_OPTIONS.get(self.info_update_var.get(), self.info_update_rate)
        })

        selected_parts = [part for part, var in self.parts_checkboxes.items() if var.get()]
        if not selected_parts:
            selected_parts = ALL_PART_NAMES.copy()
        self.settings["selected_parts"] = selected_parts
        self.settings["reverse_details_order"] = self.reverse_details_var.get()

        # 副本独占设置落自己的文件（data/user/dungeon_settings.json）。
        self.dungeon_settings = self.dungeon_settings_repo.update({
            "dungeon_font": dungeon_font,
            "audio_levels": {bus: int(self._bus_level_vars[bus].get())
                             for bus in buses.BUSES},
            "story_recent_count": self._story_recent_count_value(),
            "auto_save_replay": self.auto_save_replay_var.get(),
        })

        # 先保存当前（自由）设置，再应用世界包启用/卸载更改
        self.settings_repo.save(self.settings)

        # 网络代理改动立即生效（否则要等下一次切界面/进副本才重读）。
        # 语音与 AI 共用同一份覆盖值——设一次两处都跟。
        netproxy.set_override(self.settings.get("net_proxy"))

        if self.gui_ref and hasattr(self.gui_ref, 'apply_settings'):
            self.gui_ref.apply_settings()

        if self.on_return_callback:
            self.on_return_callback()
