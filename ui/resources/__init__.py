"""资源中心标签页：联网资源（预留）+ 静态表选择、风格素材选择与世界包管理。

页面主区为**联网资源**预留（在线下载静态表/风格素材/世界包，未来接入）；
本地资源经顶部切换条按需进入，改动**即时生效**：每次选择立即写回
``resource_settings.json``（经 :meth:`SettingsRepo.save_resource_keys`，只动
资源域键）并同步生成上下文；世界包的启用/停用/删除同样即时执行（与挂件模式
调查系统的行为一致）。设置页只保留"当前生成规则与偏好"。
"""
import tkinter as tk
from typing import Dict, Callable, Optional, TYPE_CHECKING

import customtkinter as ctk

import ui.ctk.dialogs

if TYPE_CHECKING:
    from persistence.settings_repo import SettingsRepo
    from persistence.landmark_repo import LandmarkRepo
    from persistence.quip_repo import QuipRepo

from core.address_model import world_of
from persistence import PresetRepo, PersonalityRepo
from persistence.landmark_repo import DEFAULT_LANDMARK_STYLE
from persistence.name_repo import NameRepo, DEFAULT_NAME_TABLE
from persistence.world_pack import list_behavior_packs
from dungeon.terms import is_default_scenario
from services.challenges import ChallengeService
from services.news import DEFAULT_NEWS_TABLE, NewsService
from ui.resources.dialogs import WorldPackCreateDialog
from ui.ctk.widgets import CollapsibleBlock, StyleListBox, OptionButton
from ui.ctk import fonts as ui_fonts
from ui.ctk.theme import (
    SETTINGS_PANEL_BG, SETTINGS_HOVER, SETTINGS_INPUT_HOVER,
    SETTINGS_INPUT_BG, SETTINGS_INPUT_BORDER,
    SETTINGS_SWITCH_TRACK, SETTINGS_SWITCH_PROGRESS, SETTINGS_SWITCH_KNOB,
    SETTINGS_TEXT_SOFT,
    SETTINGS_LABEL, SETTINGS_OK, SETTINGS_ERR,
)

_FONT = ui_fonts.ui_font(13)


class ResourceHubPanel(ctk.CTkFrame):
    # 顶部切换条：键 → 显示名（在线资源为主区默认页，其余为本地分区）
    _VIEWS = (
        ("online", "在线资源"),
        ("tables", "静态表"),
        ("styles", "风格素材"),
        ("pack", "世界包"),
    )

    def __init__(
            self,
            parent,
            settings_repo: 'SettingsRepo',
            landmark_repo: 'LandmarkRepo',
            quip_repo: 'QuipRepo',
            initial_settings: Dict,
            on_styles_changed: Callable,
            on_name_table_changed: Optional[Callable] = None,
            on_news_table_changed: Optional[Callable] = None,
            on_preset_table_changed: Optional[Callable] = None,
            on_personality_table_changed: Optional[Callable] = None,
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

        self.name_table = self.settings.get("name_table", DEFAULT_NAME_TABLE)
        self.news_table = self.settings.get("news_table", DEFAULT_NEWS_TABLE)
        self.preset_table = self.settings.get("preset_table", "default")
        self.personality_table = self.settings.get("personality_table", "default")
        self.selected_styles = self.settings.get("selected_styles", [DEFAULT_LANDMARK_STYLE])
        self.selected_quip_styles = self.settings.get("selected_quip_styles", [])

        self._create_widgets()
        self._sync_all_states()
        # System 模式的实际颜色由 CustomTkinter 决定，原生 Listbox 也必须使用
        # 这个实际模式，否则首次进入本页可能短暂显示浅色。
        actual_mode = ctk.get_appearance_mode()
        self.landmark_selector.apply_theme(actual_mode)
        self.quip_selector.apply_theme(actual_mode)

    # ───────────────────── UI 构建 ─────────────────────
    def _create_widgets(self):
        # 顶部切换条：在线资源是本页主区（为联网下载预留），本地资源按需进入
        self._view_bar = ctk.CTkFrame(self, fg_color="transparent")
        self._view_bar.pack(fill='x', padx=10, pady=(4, 2))
        self._view_buttons = {}
        for key, label in self._VIEWS:
            btn = ctk.CTkButton(
                self._view_bar, text=label, width=96, height=30,
                anchor='center', fg_color="transparent",
                text_color=SETTINGS_TEXT_SOFT, hover_color=SETTINGS_HOVER,
                corner_radius=8, font=ui_fonts.ui_font(13),
                command=lambda k=key: self._show_view(k))
            btn.pack(side='left', padx=(0, 6))
            self._view_buttons[key] = btn

        self._view_area = ctk.CTkFrame(self, fg_color="transparent")
        self._view_area.pack(fill='both', expand=True)

        # 在线资源（默认主区，占位等待接入）
        self.online_view = ctk.CTkFrame(self._view_area, fg_color="transparent")
        self._build_online_view()

        # 本地资源：每个分区一个独立滚动视图，内嵌原样式的折叠卡片
        self.tables_view = ctk.CTkScrollableFrame(self._view_area, fg_color="transparent")
        self.styles_view = ctk.CTkScrollableFrame(self._view_area, fg_color="transparent")
        self.pack_view = ctk.CTkScrollableFrame(self._view_area, fg_color="transparent")

        self.tables_block = CollapsibleBlock(self.tables_view, "静态表",
                                             body_padx=(6, 12), body_pady=(6, 12))
        self.styles_block = CollapsibleBlock(self.styles_view, "风格素材",
                                             body_padx=(6, 12), body_pady=(6, 12))
        self.pack_block = CollapsibleBlock(self.pack_view, "世界包",
                                           body_padx=(6, 12), body_pady=(6, 12))

        for block in (self.tables_block, self.styles_block, self.pack_block):
            block.body.configure(fg_color="transparent")
            block.header.pack(fill='x', padx=8, pady=(8, 0))
            if block._expanded:
                block.body.pack(fill='x', padx=block._body_padx,
                                pady=block._body_pady, after=block.header)

        self._build_tables_block()
        self._build_styles_block()
        self._build_pack_block()

        self._show_view("online")

    def _show_view(self, key):
        """切换主区视图并同步切换条高亮。"""
        views = {"online": self.online_view, "tables": self.tables_view,
                 "styles": self.styles_view, "pack": self.pack_view}
        for k, view in views.items():
            if k == key:
                view.pack(fill='both', expand=True)
            else:
                view.pack_forget()
        for k, btn in self._view_buttons.items():
            selected = k == key
            btn.configure(
                fg_color=SETTINGS_INPUT_BG if selected else "transparent",
                text_color=SETTINGS_LABEL if selected else SETTINGS_TEXT_SOFT)

    def _build_online_view(self):
        """联网资源占位页：布局与导航均已就位，等下载能力接入后填充主区。"""
        self.online_view.columnconfigure(0, weight=1)
        self.online_view.rowconfigure(0, weight=1)
        card = ctk.CTkFrame(self.online_view, fg_color=SETTINGS_PANEL_BG,
                            corner_radius=6)
        card.grid(row=0, column=0, sticky='nsew', padx=10, pady=(6, 8))

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.place(relx=0.5, rely=0.45, anchor='center')
        ctk.CTkLabel(inner, text="🌐", font=ui_fonts.ui_font(44)).pack()
        ctk.CTkLabel(inner, text="联网资源",
                     font=ui_fonts.ui_font(16, "bold"),
                     text_color=SETTINGS_LABEL).pack(pady=(8, 4))
        ctk.CTkLabel(
            inner,
            text="在线下载静态表、风格素材与世界包的能力将在这里提供。\n"
                 "当前版本请通过上方切换使用本地资源。",
            font=ui_fonts.ui_font(12), text_color=SETTINGS_TEXT_SOFT,
            justify="center").pack()

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

    # ───────────────── 各区块构建 ─────────────────
    def _build_tables_block(self):
        body = self.tables_block.body
        self._setup_body_grid(body, "tables")
        row = 0

        _, ctrl = self._make_row(body, row, "姓名表")
        row += 1
        self.name_table_var = tk.StringVar(value=self.name_table)
        name_tables = self.name_repo.get_tables()
        if self.name_table not in name_tables:
            name_tables.insert(0, self.name_table)
        self.name_table_menu = self._make_option_button(
            ctrl, name_tables,
            self._on_name_table_changed, self.name_table_var
        )

        _, ctrl = self._make_row(body, row, "新闻表")
        row += 1
        self.news_table_var = tk.StringVar(value=self.news_table)
        news_service = NewsService(news_table=self.news_table,
                                   world_state=self.world_state)
        news_tables = news_service.get_tables()
        if self.news_table not in news_tables:
            news_tables.insert(0, self.news_table)
        if not news_tables:
            news_tables = [DEFAULT_NEWS_TABLE]
        self.news_table_menu = self._make_option_button(
            ctrl, news_tables,
            self._on_news_table_changed, self.news_table_var
        )

        _, ctrl = self._make_row(body, row, "身材表")
        row += 1
        self.preset_table_var = tk.StringVar(value=self.preset_table)
        preset_tables = self.preset_repo.get_tables()
        if self.preset_table not in preset_tables:
            preset_tables.insert(0, self.preset_table)
        if not preset_tables:
            preset_tables = ["default"]
        self.preset_table_menu = self._make_option_button(
            ctrl, preset_tables,
            self._on_preset_table_changed, self.preset_table_var
        )

        _, ctrl = self._make_row(body, row, "性格表")
        row += 1
        self.personality_table_var = tk.StringVar(value=self.personality_table)
        personality_tables = self.personality_repo.get_tables()
        if self.personality_table not in personality_tables:
            personality_tables.insert(0, self.personality_table)
        if not personality_tables:
            personality_tables = ["default"]
        self.personality_table_menu = self._make_option_button(
            ctrl, personality_tables,
            self._on_personality_table_changed, self.personality_table_var
        )

    def _build_styles_block(self):
        body = self.styles_block.body
        self._setup_body_grid(body, "styles")
        body.columnconfigure(0, weight=1, uniform="hub_style")
        body.columnconfigure(1, weight=1, uniform="hub_style")

        self.landmark_selector = StyleListBox(body, "地标风格组", height=2,
                                              on_change=self._on_landmark_selection_changed)
        self.landmark_selector.grid(row=0, column=0, sticky='nsew', padx=(10, 5), pady=6)
        self.landmark_selector.add_button("默认", self.landmark_selector.set_default, padx=0)
        self.landmark_selector.add_button("全选", self.landmark_selector.select_all, padx=10)

        self.quip_selector = StyleListBox(body, "描述风格组", height=2,
                                          on_change=self._on_quip_selection_changed)
        self.quip_selector.grid(row=0, column=1, sticky='nsew', padx=(5, 10), pady=6)
        self.quip_selector.add_button("清空", self.quip_selector.clear_selection, padx=0)
        self.quip_selector.add_button("全选", self.quip_selector.select_all, padx=10)

        ctk.CTkLabel(
            body, text="编辑风格条目请前往「文本管理」页", anchor='w',
            font=ui_fonts.ui_font(12),
            text_color=SETTINGS_TEXT_SOFT
        ).grid(row=1, column=0, columnspan=2, sticky='w', padx=10, pady=(0, 6))

    def _build_pack_block(self):
        body = self.pack_block.body
        self._setup_body_grid(body, "pack")
        row = 0

        # 第1行：世界包
        self.world_pack_label, ctrl = self._make_row(body, row, "世界包")
        row += 1

        self.world_pack_idle_frame = ctk.CTkFrame(ctrl, fg_color="transparent")
        self.world_pack_idle_frame.pack(side='left')
        self.world_pack_var = tk.StringVar(value="")
        self.world_pack_menu = self._make_option_button(
            self.world_pack_idle_frame, [],
            self._on_world_pack_changed, self.world_pack_var, expand=False
        )
        self.world_pack_new_btn = ctk.CTkButton(
            self.world_pack_idle_frame, text="新建", width=60, height=28,
            command=self._create_world_pack, fg_color="transparent",
            text_color=SETTINGS_TEXT_SOFT, hover_color=SETTINGS_HOVER,
            border_width=1, border_color=SETTINGS_INPUT_BORDER,
            corner_radius=8, font=ui_fonts.ui_font(12)
        )
        self.world_pack_new_btn.pack(side='left', padx=(8, 0))

        self.world_pack_active_frame = ctk.CTkFrame(ctrl, fg_color="transparent")
        self.world_pack_active_label = ctk.CTkLabel(
            self.world_pack_active_frame, text="", anchor='w',
            font=_FONT, text_color=SETTINGS_OK)
        self.world_pack_active_label.pack(side='left')
        self.world_pack_dissolve_btn = ctk.CTkButton(
            self.world_pack_active_frame, text="删除", width=60, height=28,
            command=self._dissolve_world_pack, fg_color="transparent",
            text_color=SETTINGS_ERR, hover_color=SETTINGS_HOVER,
            border_width=1, border_color=SETTINGS_INPUT_BORDER,
            corner_radius=8, font=ui_fonts.ui_font(12)
        )
        self.world_pack_dissolve_btn.pack(side='left', padx=(12, 0))
        self.world_pack_active_frame.pack_forget()

        # 第2行：启用开关（切换即生效，无需保存）
        _, ctrl = self._make_row(body, row, "启用")
        row += 1
        self.world_pack_enable_var = tk.BooleanVar(value=False)
        self.world_pack_switch = ctk.CTkSwitch(
            ctrl, text="", variable=self.world_pack_enable_var,
            width=44, height=22, command=self._on_world_pack_switch,
            progress_color=SETTINGS_SWITCH_PROGRESS, fg_color=SETTINGS_SWITCH_TRACK,
            button_color=SETTINGS_SWITCH_KNOB, button_hover_color=SETTINGS_SWITCH_KNOB
        )
        self.world_pack_switch.pack(side='left')

        self._refresh_world_pack_ui()

    # ───────────────── 资源键即时落盘 ─────────────────
    def _persist_resource_keys(self):
        """把本页当前选择写进共享 settings 并即时落盘（只动资源域键）。"""
        self.settings.update({
            "name_table": self.name_table,
            "news_table": self.news_table,
            "preset_table": self.preset_table,
            "personality_table": self.personality_table,
            "selected_styles": self.selected_styles,
            "selected_quip_styles": self.selected_quip_styles,
        })
        self.settings_repo.save_resource_keys(self.settings)

    # ───────────────── 世界包操作 ─────────────────
    def _refresh_world_pack_ui(self):
        """刷新世界包块：未启用时显示 optionmenu+新建键，已启用时显示启用标签+删除键。"""
        if self.world_manager is None:
            self.world_pack_label.configure(
                text="世界包（不可用）", text_color=SETTINGS_TEXT_SOFT)
            self.world_pack_menu.configure(state="disabled")
            self.world_pack_new_btn.configure(state="disabled")
            self.world_pack_dissolve_btn.configure(state="disabled")
            self.world_pack_switch.configure(state="disabled")
            self._show_pack_row(False)
            self.world_pack_var.set("")
            self._set_table_menus_locked()
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
        if current not in names:
            self.world_pack_var.set(names[0] if names else "")

        active = self.world_state is not None and self.world_state.active
        if active:
            self.world_pack_label.configure(text="世界包", text_color=SETTINGS_LABEL)
            self.world_pack_active_label.configure(
                text=f"已启用：{self.world_state.pack_name}")
            self._show_pack_row(True)
            self.world_pack_var.set(self.world_state.pack_name)
            self.world_pack_enable_var.set(True)
        else:
            self.world_pack_label.configure(text="世界包", text_color=SETTINGS_LABEL)
            self.world_pack_active_label.configure(text="已启用：")
            self._show_pack_row(False)
            self.world_pack_enable_var.set(False)
        self._set_table_menus_locked()

    def _show_pack_row(self, active):
        """在“optionmenu+新建键”与“启用标签+删除键”两种布局间切换。"""
        if active:
            self.world_pack_idle_frame.pack_forget()
            self.world_pack_active_frame.pack(side='left')
        else:
            self.world_pack_active_frame.pack_forget()
            self.world_pack_idle_frame.pack(side='left')

    def _on_world_pack_changed(self, choice):
        """仅记录 optionmenu 选择，启用/停用由“启用”开关即时触发。"""
        pass

    def _on_world_pack_switch(self):
        """启用开关即时生效：开=激活所选包，关=卸载当前包（与挂件模式调查系统一致）。"""
        if self.world_manager is None:
            self.world_pack_enable_var.set(False)
            return
        if self.world_pack_enable_var.get():
            target = None
            for p in self.world_manager.list_packs():
                if p["name"] == self.world_pack_var.get() and not p.get("error"):
                    target = p
                    break
            if target is None:
                self.world_pack_enable_var.set(False)
                ui.ctk.dialogs.showwarning("提示", "没有可加载的世界包，请先创建")
                return
            try:
                self.world_manager.activate(
                    target["world_id"], self.settings, self.settings_repo)
            except ValueError as e:
                self.world_pack_enable_var.set(False)
                ui.ctk.dialogs.showerror("错误", str(e))
                return
            self._after_world_state_change()
        else:
            if self.world_state is None or not self.world_state.active:
                return
            self.world_manager.deactivate(self.settings, self.settings_repo)
            self._after_world_state_change()

    def _build_available_resources(self) -> Dict[str, list]:
        """返回各资源类型可选打包的具体条目（供创建对话框的选择控件使用）。"""
        resources: Dict[str, list] = {}
        resources["landmarks"] = list(self.landmark_repo.get_styles())
        resources["quips"] = list(self.quip_repo.get_styles())
        resources["presets"] = list(self.preset_repo.get_tables())
        resources["personalities"] = list(self.personality_repo.get_tables())
        scenario_repo = getattr(self.gui_ref, "_scenario_repo", None) if self.gui_ref else None
        if scenario_repo is not None:
            resources["scenarios"] = [d for d in scenario_repo.list_all()
                                      if not is_default_scenario(d)]
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

    def _set_table_menus_locked(self):
        """按包锁定的资源键禁用静态表菜单（规则段的体型/种子锁在设置页）。"""
        locked = self.world_state.locked_keys() if self.world_state is not None else set()
        self.name_table_menu.configure(
            state="disabled" if "name_table" in locked else "normal")
        self.news_table_menu.configure(
            state="disabled" if "news_table" in locked else "normal")
        self.preset_table_menu.configure(
            state="disabled" if "preset_table" in locked else "normal")
        self.personality_table_menu.configure(
            state="disabled" if "personality_table" in locked else "normal")

    def _sync_table_vars(self):
        """激活/卸载/解散后，按 self.settings 回读静态表选择。"""
        name_tables = self.name_repo.get_tables()
        name_table = self.settings.get("name_table", DEFAULT_NAME_TABLE)
        if name_table not in name_tables:
            name_tables.insert(0, name_table)
        self.name_table = name_table
        self.name_table_var.set(name_table)
        self.name_table_menu.configure(values=name_tables)

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

        preset_table = self.settings.get("preset_table", "default")
        preset_tables = self.preset_repo.get_tables()
        if preset_table not in preset_tables:
            preset_tables.insert(0, preset_table)
        if not preset_tables:
            preset_tables = ["default"]
        self.preset_table = preset_table
        self.preset_table_var.set(preset_table)
        self.preset_table_menu.configure(values=preset_tables)

        personality_table = self.settings.get("personality_table", "default")
        personality_tables = self.personality_repo.get_tables()
        if personality_table not in personality_tables:
            personality_tables.insert(0, personality_table)
        if not personality_tables:
            personality_tables = ["default"]
        self.personality_table = personality_table
        self.personality_table_var.set(personality_table)
        self.personality_table_menu.configure(values=personality_tables)

    def _after_world_state_change(self):
        """世界包状态变化后：回读本页状态 → 推给上下文 → 全局刷新。"""
        self._sync_table_vars()
        self.selected_styles = self.settings.get("selected_styles", [DEFAULT_LANDMARK_STYLE])
        self.selected_quip_styles = self.settings.get("selected_quip_styles", [])
        self._sync_all_states()
        if self.gui_ref is not None:
            for method, arg in [
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

    def on_world_state_changed(self):
        """世界包状态变化后的本页刷新（由 gui_ref.refresh_world_ui 调度）。"""
        self._sync_table_vars()
        self._refresh_world_pack_ui()

    def _create_world_pack(self):
        if self.world_manager is None:
            ui.ctk.dialogs.showerror("错误", "世界包功能不可用")
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
                    getattr(self.gui_ref, "_scenario_repo", None) if self.gui_ref else None,
                )
                manifest = self.world_manager.create_from_current(
                    cfg["world_id"], cfg["name"], self.settings,
                    landmark_repo=self.landmark_repo,
                    quip_repo=self.quip_repo,
                    preset_repo=getattr(self.gui_ref, "_preset_repo", None) if self.gui_ref else None,
                    personality_repo=getattr(self.gui_ref, "_personality_repo", None) if self.gui_ref else None,
                    scenario_repo=getattr(self.gui_ref, "_scenario_repo", None) if self.gui_ref else None,
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
                self.after(0, lambda: ui.ctk.dialogs.showerror(
                    "错误", f"创建世界包失败：{e}"))
                return
            self.after(0, lambda m=manifest: self._on_pack_created(m))

        self.after(50, _do_work)

    def _on_pack_created(self, manifest):
        ui.ctk.dialogs.showinfo(
            "成功", f"世界包「{manifest.name}」已创建\nworld_id: {manifest.world_id}")
        self._refresh_world_pack_ui()

    def _dissolve_world_pack(self):
        if self.world_manager is None:
            ui.ctk.dialogs.showerror("错误", "世界包功能不可用")
            return
        if self.world_state is None or not self.world_state.active:
            return
        world_id = self.world_state.world_id
        name = self.world_state.pack_name
        choice = ui.ctk.dialogs.askyesnocancel(
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
                getattr(self.gui_ref, "_scenario_repo", None) if self.gui_ref else None,
            )
            if choice:
                self.world_manager.dissolve(
                    world_id, self.settings, self.settings_repo,
                    remove_pack=True, challenge_mgr=challenge_mgr)
            else:
                self.world_manager.deactivate(self.settings, self.settings_repo)
                self.world_manager.delete_pack(world_id)
        except ValueError as e:
            ui.ctk.dialogs.showerror("错误", str(e))
            return
        ui.ctk.dialogs.showinfo("成功", f"世界包「{name}」已删除")
        self._after_world_state_change()

    # ───────────────── 回调 ─────────────────
    def _on_name_table_changed(self, choice):
        self.name_table = choice or DEFAULT_NAME_TABLE
        self.name_table_var.set(choice)
        if self.on_name_table_changed:
            self.on_name_table_changed(self.name_table)
        self._persist_resource_keys()

    def _on_news_table_changed(self, choice):
        self.news_table = choice or DEFAULT_NEWS_TABLE
        self.news_table_var.set(self.news_table)
        if self.on_news_table_changed:
            self.on_news_table_changed(self.news_table)
        self._persist_resource_keys()

    def _on_preset_table_changed(self, choice):
        self.preset_table = choice or "default"
        self.preset_table_var.set(self.preset_table)
        if self.on_preset_table_changed:
            self.on_preset_table_changed(self.preset_table)
        self._persist_resource_keys()

    def _on_personality_table_changed(self, choice):
        self.personality_table = choice or "default"
        self.personality_table_var.set(self.personality_table)
        if self.on_personality_table_changed:
            self.on_personality_table_changed(self.personality_table)
        self._persist_resource_keys()

    # ───────────────── 风格选择逻辑 ─────────────────
    def _sync_all_states(self):
        self._sync_landmark_styles()
        self._sync_quip_styles()

    def _sync_landmark_styles(self, filter_world=None):
        styles = self.landmark_repo.get_styles()
        display_items = []
        kept = []
        for style in styles:
            reg_world = world_of(self.landmark_repo.load_style_address(style))
            if filter_world and reg_world and reg_world != filter_world:
                continue  # 同一维度自动筛选：隐藏其它维度的地标风格
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
                continue  # 同一维度自动筛选：隐藏其它维度的描述风格
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
        """当前选中风格共同锁定的维度（先地标风格后描述风格）；未注册返回 None。"""
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
        """同一维度互斥：把两栏风格列表自动筛选到同一个维度，剔除混选。

        forced：用户本次新点选的风格带维度时，以该维度为准切换（自动筛选）。
        """
        lock = forced if forced is not None else self._lock_world_from_selection()
        # 剔除与锁定维度不一致的已选风格（未注册风格不限制）
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
            self.selected_styles = [DEFAULT_LANDMARK_STYLE]
        self._sync_landmark_styles(lock)
        self._sync_quip_styles(lock)
        # 同步后以实际可见的选中项为准
        self.selected_styles = self.landmark_selector.get_selected_raw_names() or self.selected_styles
        self.selected_quip_styles = self.quip_selector.get_selected_raw_names() or self.selected_quip_styles
        if self.on_styles_changed:
            self.on_styles_changed()
        self._persist_resource_keys()

    def _on_landmark_selection_changed(self):
        prev = list(self.selected_styles)
        names = self.landmark_selector.get_selected_raw_names()
        if not names:
            names = [DEFAULT_LANDMARK_STYLE]
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

    # ───────────────── 主题与深跳 ─────────────────
    def update_theme(self, mode=None):
        """刷新本页中的原生 Listbox，不改变任何选择。"""
        actual_mode = mode or ctk.get_appearance_mode()
        self.landmark_selector.apply_theme(actual_mode)
        self.quip_selector.apply_theme(actual_mode)

    def scroll_to_style_section(self):
        """外部深跳：切到风格素材视图并滚动到地标风格组。"""
        self._show_view("styles")
        self.update_idletasks()
        canvas = getattr(self.styles_view, '_parent_canvas', None)
        frame = getattr(self.styles_view, '_parent_frame', None)
        if canvas is None or frame is None:
            return
        total_height = frame.winfo_reqheight()
        if total_height <= 0:
            return
        y = self.landmark_selector.border.winfo_y()
        fraction = max(0.0, min(1.0, (y - 10) / total_height))
        canvas.yview_moveto(fraction)
