"""资源中心标签页：顶部导航「在线资源 / 社区 / 本地资源」。

在线资源与社区为联网能力预留占位；本地资源是本页功能区：世界包、风格组、
副本方案、取材表、挑战包以分区卡片呈现，各分区**启用项排在未启用项之前**
（世界包=激活包、风格组=已选风格、取材表=当前组、副本方案/挑战包=随
世界包附带的资源）。改动**即时生效**：每次选择立即写回
``resource_settings.json``（经 :meth:`SettingsRepo.save_resource_keys`，只动
资源域键）并同步生成上下文；世界包的启用/停用/删除同样即时执行。

功能边界：风格条目编辑在「文本管理」、副本方案编辑在「副本编辑」、挑战包
创建与启动在「挑战模式」，本页只做状态总览与深跳，不重复实现。
"""
import os
import tkinter as tk
from typing import Dict, Callable, List, Optional, TYPE_CHECKING

import customtkinter as ctk
from PIL import Image

import ui.ctk.dialogs

if TYPE_CHECKING:
    from persistence.settings_repo import SettingsRepo
    from persistence.landmark_repo import LandmarkRepo
    from persistence.quip_repo import QuipRepo

from core.address_model import world_of
from core.resource_spec import MATERIAL_MEMBERS
from paths import icon_dir
from persistence import PresetRepo, PersonalityRepo
from persistence.landmark_repo import DEFAULT_LANDMARK_STYLE
from persistence.material_tables import DEFAULT_MATERIAL_TABLE, present_members
from persistence.name_repo import NameRepo
from persistence.resources import registry
from persistence.resources.catalog_store import refresh_catalog
from persistence.resources.registry import LIST_RESOURCE_TYPES
from persistence.world_pack import list_behavior_packs, pack_icon_path
from dungeon.terms import is_default_scenario
from services.challenges import ChallengeService
from services.news import NewsService
from ui.resources.dialogs import WorldPackCreateDialog
from ui.ctk.widgets import StyleListBox, OptionButton
from ui.ctk.ctk_image import pil_to_ctk
from ui.ctk import fonts as ui_fonts
from ui.ctk.theme import (
    SETTINGS_PANEL_BG, SETTINGS_HOVER, SETTINGS_INPUT_HOVER,
    SETTINGS_INPUT_BG, SETTINGS_INPUT_BORDER,
    SETTINGS_TEXT_SOFT,
    SETTINGS_LABEL, SETTINGS_OK, SETTINGS_ERR,
)

_FONT = ui_fonts.ui_font(13)


class IconCard(ctk.CTkFrame):
    """方形资源卡片：图标 + 名称（+右上角徽标），可选底部小按钮与点击动作。

    ``icon_path`` 缺失或加载失败时回退到 ``fallback_emoji`` 文本占位。
    """

    def __init__(self, parent, title, icon_path=None, fallback_emoji="📦",
                 badge=None, badge_color=SETTINGS_OK, buttons=(),
                 on_click=None, size=112, icon_size=52, **kwargs):
        super().__init__(parent, width=size, height=132, corner_radius=10,
                         border_width=1, fg_color=SETTINGS_PANEL_BG,
                         border_color=SETTINGS_INPUT_BORDER, **kwargs)
        self.pack_propagate(False)
        self.grid_propagate(False)
        self._on_click = on_click
        self._icon_image = None

        if icon_path:
            try:
                self._icon_image = pil_to_ctk(Image.open(icon_path).convert("RGBA"),
                                              (icon_size, icon_size))
            except Exception as exc:
                print(f"[ResourceHub] 图标加载失败 {icon_path}: {exc}")
        if self._icon_image is not None:
            ctk.CTkLabel(self, image=self._icon_image, text="").pack(pady=(12, 3))
        else:
            ctk.CTkLabel(self, text=fallback_emoji,
                         font=ui_fonts.ui_font(30)).pack(pady=(12, 3))

        ctk.CTkLabel(self, text=title, font=ui_fonts.ui_font(12),
                     text_color=SETTINGS_LABEL, wraplength=size - 14,
                     justify='center').pack(fill='x', padx=6,
                                            expand=True)

        if buttons:
            row = ctk.CTkFrame(self, fg_color="transparent")
            row.pack(side='bottom', fill='x', padx=6, pady=(0, 8))
            for text, command, color in buttons:
                ctk.CTkButton(
                    row, text=text, width=42, height=22, command=command,
                    fg_color="transparent", text_color=color,
                    hover_color=SETTINGS_HOVER, border_width=1,
                    border_color=SETTINGS_INPUT_BORDER, corner_radius=7,
                    font=ui_fonts.ui_font(11)).pack(
                    side='right', padx=(4, 0), expand=True, fill='x')

        if badge:
            ctk.CTkLabel(self, text=badge, font=ui_fonts.ui_font(11),
                         text_color=badge_color, fg_color=SETTINGS_INPUT_BG,
                         corner_radius=6).place(relx=1.0, x=-8, y=8,
                                                anchor='ne')

        if on_click:
            self._bind_card(self)

    def _bind_card(self, widget):
        """点击与悬停绑定铺到全部子控件上（CTk 控件的点击落在内部部件上）。

        按钮自身有 command，跳过绑定避免双重触发。
        """
        if isinstance(widget, ctk.CTkButton):
            return
        widget.bind("<Button-1>", lambda _e: self._on_click(), add="+")
        widget.bind("<Enter>", lambda _e: self._on_hover(True), add="+")
        widget.bind("<Leave>", lambda _e: self._on_hover(False), add="+")
        if not isinstance(widget, ctk.CTkLabel):  # 叶子控件不进入其内部
            for child in widget.winfo_children():
                self._bind_card(child)

    def _on_hover(self, entered):
        try:
            self.configure(border_color=SETTINGS_INPUT_HOVER if entered
                           else SETTINGS_INPUT_BORDER)
        except Exception:
            pass


class ResourceHubPanel(ctk.CTkFrame):
    # 顶部导航：在线资源 / 社区 为联网能力预留，本地资源承载全部本地管理
    _VIEWS = (
        ("online", "在线资源"),
        ("community", "社区"),
        ("local", "本地资源"),
    )

    def __init__(
            self,
            parent,
            settings_repo: 'SettingsRepo',
            landmark_repo: 'LandmarkRepo',
            quip_repo: 'QuipRepo',
            initial_settings: Dict,
            on_styles_changed: Callable,
            on_material_table_changed: Optional[Callable] = None,
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
        self.on_material_table_changed = on_material_table_changed
        self.gui_ref = gui_ref
        self.world_manager = world_manager
        self.world_state = getattr(world_manager, "world_state", None) \
            if world_manager is not None else None
        self.name_repo = name_repo or NameRepo(world_state=self.world_state)
        self.preset_repo = preset_repo or PresetRepo(world_state=self.world_state)
        self.personality_repo = personality_repo or PersonalityRepo(world_state=self.world_state)

        self.material_table = self.settings.get(
            "material_table", DEFAULT_MATERIAL_TABLE)
        self.selected_styles = self.settings.get("selected_styles", [DEFAULT_LANDMARK_STYLE])
        self.selected_quip_styles = self.settings.get("selected_quip_styles", [])

        self._create_widgets()
        self._sync_all_states()
        self._refresh_local_sections()
        self._refresh_resource_catalog()
        # System 模式的实际颜色由 CustomTkinter 决定，原生 Listbox 也必须使用
        # 这个实际模式，否则首次进入本页可能短暂显示浅色。
        actual_mode = ctk.get_appearance_mode()
        self.landmark_selector.apply_theme(actual_mode)
        self.quip_selector.apply_theme(actual_mode)

    # ───────────────────── UI 构建 ─────────────────────
    def _create_widgets(self):
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

        self.online_view = ctk.CTkFrame(self._view_area, fg_color="transparent")
        self._build_online_view()

        self.community_view = ctk.CTkFrame(self._view_area, fg_color="transparent")
        self._build_community_view()

        self.local_view = ctk.CTkScrollableFrame(self._view_area,
                                                 fg_color="transparent")
        self._build_local_view()

        self._show_view("online")

    def _show_view(self, key):
        """切换主区视图并同步导航高亮。"""
        views = {"online": self.online_view, "community": self.community_view,
                 "local": self.local_view}
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

    def _build_placeholder(self, view, icon, title, text):
        view.columnconfigure(0, weight=1)
        view.rowconfigure(0, weight=1)
        card = ctk.CTkFrame(view, fg_color=SETTINGS_PANEL_BG, corner_radius=6)
        card.grid(row=0, column=0, sticky='nsew', padx=10, pady=(6, 8))

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.place(relx=0.5, rely=0.45, anchor='center')
        ctk.CTkLabel(inner, text=icon, font=ui_fonts.ui_font(44)).pack()
        ctk.CTkLabel(inner, text=title,
                     font=ui_fonts.ui_font(16, "bold"),
                     text_color=SETTINGS_LABEL).pack(pady=(8, 4))
        ctk.CTkLabel(inner, text=text,
                     font=ui_fonts.ui_font(12), text_color=SETTINGS_TEXT_SOFT,
                     justify="center").pack()

    def _build_online_view(self):
        """联网资源占位页：布局与导航均已就位，等下载能力接入后填充主区。"""
        self._build_placeholder(
            self.online_view, "🌐", "联网资源",
            "在线下载取材表、风格素材、副本方案与世界包的能力将在这里提供。\n"
            "当前版本请通过「本地资源」使用与管理资源。")

    def _build_community_view(self):
        """社区占位页：分享/订阅能力预留。"""
        self._build_placeholder(
            self.community_view, "🤝", "社区",
            "社区资源分享、订阅与交流能力将在这里提供，敬请期待。")

    # ───────────────── 本地资源视图 ─────────────────
    def _build_local_view(self):
        """本地资源：分区卡片依次排列，行级刷新由各 _refresh_* 负责。"""
        self._build_world_section()
        self._build_styles_section()
        self._build_scenarios_section()
        self._build_materials_section()
        self._build_challenges_section()

    def _add_section(self, title, note=None, action=None):
        """创建一个分区卡片：标题行（含说明与可选右上角动作键）+ 内容区。"""
        card = ctk.CTkFrame(self.local_view, fg_color=SETTINGS_PANEL_BG,
                            corner_radius=8)
        card.pack(fill='x', padx=10, pady=(8, 0))
        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill='x', padx=12, pady=(10, 2))
        ctk.CTkLabel(header, text=title, font=ui_fonts.ui_font(13, "bold"),
                     text_color=SETTINGS_LABEL, anchor='w').pack(side='left')
        if note:
            ctk.CTkLabel(header, text=note, font=ui_fonts.ui_font(11),
                         text_color=SETTINGS_TEXT_SOFT).pack(
                side='left', padx=(10, 0))
        action_btn = None
        if action:
            text, command = action
            action_btn = ctk.CTkButton(
                header, text=text, width=90, height=26, command=command,
                fg_color="transparent", text_color=SETTINGS_TEXT_SOFT,
                hover_color=SETTINGS_HOVER, border_width=1,
                border_color=SETTINGS_INPUT_BORDER, corner_radius=8,
                font=ui_fonts.ui_font(12))
            action_btn.pack(side='right')
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill='x', padx=12, pady=(2, 10))
        return body, action_btn

    def _rows_container(self, parent):
        """分区内容里的行容器：整体重建行时只需清空它。"""
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill='x')
        return frame

    def _clear(self, frame):
        for w in frame.winfo_children():
            w.destroy()

    def _note_row(self, parent, text):
        ctk.CTkLabel(parent, text=text, anchor='w', justify='left',
                     font=ui_fonts.ui_font(12),
                     text_color=SETTINGS_TEXT_SOFT).pack(
            fill='x', padx=8, pady=(2, 4))

    def _item_row(self, parent, title, subtitle=None, badge=None,
                  badge_color=SETTINGS_OK, buttons=()):
        """资源行：左侧标题(+徽标+说明)，右侧一排小按钮。"""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill='x', padx=8, pady=(0, 6))
        left = ctk.CTkFrame(row, fg_color="transparent")
        left.pack(side='left', fill='x', expand=True)
        title_row = ctk.CTkFrame(left, fg_color="transparent")
        title_row.pack(anchor='w')
        ctk.CTkLabel(title_row, text=title, font=_FONT,
                     text_color=SETTINGS_LABEL, anchor='w').pack(side='left')
        if badge:
            ctk.CTkLabel(title_row, text=badge, font=ui_fonts.ui_font(11),
                         text_color=badge_color, fg_color=SETTINGS_INPUT_BG,
                         corner_radius=6).pack(side='left', padx=(8, 0),
                                               ipadx=6, ipady=1)
        if subtitle:
            ctk.CTkLabel(left, text=subtitle, anchor='w', justify='left',
                         font=ui_fonts.ui_font(11),
                         text_color=SETTINGS_TEXT_SOFT).pack(
                anchor='w', fill='x')
        for spec in buttons:
            text, command, color = spec
            btn = ctk.CTkButton(
                row, text=text, width=56, height=26, command=command,
                fg_color="transparent", text_color=color,
                hover_color=SETTINGS_HOVER, border_width=1,
                border_color=SETTINGS_INPUT_BORDER, corner_radius=8,
                font=ui_fonts.ui_font(12))
            btn.pack(side='right', padx=(6, 0))
        return row

    def _grid_cards(self, frame, cards, cols=6):
        """卡片按固定列数从左往右铺成网格（不拉伸占满整行宽度）。"""
        for i, card in enumerate(cards):
            card.grid(row=i // cols, column=i % cols, padx=4, pady=4,
                      sticky='w')

    # ───────────────── 分区：世界包 ─────────────────
    def _build_world_section(self):
        self._world_rows_frame, self._world_new_btn = self._add_section(
            "世界包", note="启用中的世界包会接管其打包的资源",
            action=("＋ 新建", self._create_world_pack))
        # 行内容统一由 __init__ 末尾的 _refresh_local_sections 首次填充
        # （此刻取材表菜单尚未创建，行刷新里的锁键逻辑还不能跑）。

    def _refresh_world_rows(self):
        """重建世界包卡片：激活包在最前（启用项优先），其余按名称排列。

        图标取包目录内的 icon.png，缺失时用内置默认图标。
        """
        frame = self._world_rows_frame
        self._clear(frame)
        available = self.world_manager is not None
        self._world_new_btn.configure(state="normal" if available else "disabled")
        if not available:
            self._note_row(frame, "世界包功能不可用")
            self._set_table_menus_locked()
            return

        packs = [p for p in self.world_manager.list_packs() if not p.get("error")]
        active = self.world_state is not None and self.world_state.active
        active_id = self.world_state.world_id if active else None
        packs.sort(key=lambda p: (p["world_id"] != active_id, p["name"]))

        if not packs:
            self._note_row(frame, "暂无世界包，点击右上角「＋ 新建」创建")
            self._set_table_menus_locked()
            return

        default_icon = os.path.join(icon_dir(), "world_pack.png")
        cards: List[IconCard] = []
        for p in packs:
            icon = pack_icon_path(self.world_manager.data_dir, p["world_id"]) \
                or default_icon
            if active and p["world_id"] == active_id:
                cards.append(IconCard(
                    frame, p["name"], icon_path=icon, fallback_emoji="🌍",
                    badge="启用中",
                    buttons=(
                        ("停用", self._deactivate_world_pack, SETTINGS_TEXT_SOFT),
                        ("删除", self._dissolve_world_pack, SETTINGS_ERR),
                    )))
            else:
                cards.append(IconCard(
                    frame, p["name"], icon_path=icon, fallback_emoji="🌍",
                    on_click=lambda n=p["name"]: self._activate_world_pack(n)))
        self._grid_cards(frame, cards)
        self._set_table_menus_locked()

    def _activate_world_pack(self, pack_name):
        """启用（或切换到）指定世界包；已在启用中则忽略。"""
        target = next((p for p in self.world_manager.list_packs()
                       if p["name"] == pack_name and not p.get("error")), None)
        if target is None:
            ui.ctk.dialogs.showwarning("提示", "没有可加载的世界包，请先创建")
            return
        try:
            if self.world_state is not None and self.world_state.active:
                if self.world_state.world_id == target["world_id"]:
                    return
                self.world_manager.deactivate(self.settings, self.settings_repo)
            self.world_manager.activate(
                target["world_id"], self.settings, self.settings_repo)
        except ValueError as e:
            ui.ctk.dialogs.showerror("错误", str(e))
            return
        self._after_world_state_change()

    def _deactivate_world_pack(self):
        """停用当前世界包（即时生效）。"""
        if self.world_state is None or not self.world_state.active:
            return
        self.world_manager.deactivate(self.settings, self.settings_repo)
        self._after_world_state_change()

    # ───────────────── 分区：风格组 ─────────────────
    def _build_styles_section(self):
        can_nav = self.gui_ref is not None and hasattr(self.gui_ref, "show_page")
        action = ("编辑条目", lambda: self.gui_ref.show_page("text_mgmt")) \
            if can_nav else None
        body, _ = self._add_section(
            "风格组", note="启用中的风格组决定生成时使用的素材",
            action=action)
        body.columnconfigure(0, weight=1, uniform="hub_style")
        body.columnconfigure(1, weight=1, uniform="hub_style")

        self.landmark_selector = StyleListBox(body, "地标风格组", height=2,
                                              on_change=self._on_landmark_selection_changed)
        self.landmark_selector.grid(row=0, column=0, sticky='nsew', padx=(8, 5), pady=6)
        self.landmark_selector.add_button("默认", self.landmark_selector.set_default, padx=0)
        self.landmark_selector.add_button("全选", self.landmark_selector.select_all, padx=10)

        self.quip_selector = StyleListBox(body, "描述风格组", height=2,
                                          on_change=self._on_quip_selection_changed)
        self.quip_selector.grid(row=0, column=1, sticky='nsew', padx=(5, 8), pady=6)
        self.quip_selector.add_button("清空", self.quip_selector.clear_selection, padx=0)
        self.quip_selector.add_button("全选", self.quip_selector.select_all, padx=10)

        ctk.CTkLabel(
            body, text="编辑风格条目请前往「文本管理」页", anchor='w',
            font=ui_fonts.ui_font(12),
            text_color=SETTINGS_TEXT_SOFT
        ).grid(row=1, column=0, columnspan=2, sticky='w', padx=8, pady=(0, 6))

    # ───────────────── 分区：副本方案 ─────────────────
    def _build_scenarios_section(self):
        self._scenario_rows_frame, _ = self._add_section(
            "副本方案", note="方案在「副本编辑」页创建与编辑")
        self._refresh_scenario_rows()

    def _refresh_scenario_rows(self):
        """重建副本方案行：随世界包附带的（启用）方案排在最前。"""
        frame = self._scenario_rows_frame
        self._clear(frame)
        scenarios = self._available_scenarios()
        bundled = set()
        if self.world_state is not None and self.world_state.active:
            bundled = set(self.world_state.manifest.resources.get("scenarios") or [])
        ordered = ([s for s in scenarios if s in bundled]
                   + [s for s in scenarios if s not in bundled])
        if not ordered:
            self._note_row(frame, "暂无副本方案，可在「副本编辑」页创建")
            return
        editor_locked = (self.world_state is not None
                         and self.world_state.active
                         and self.world_state.owns("scenarios"))
        for scenario_id in ordered:
            if editor_locked:
                buttons = (("随包锁定", None, SETTINGS_TEXT_SOFT),)
            else:
                buttons = (("编辑",
                            lambda sid=scenario_id: self._goto_scenario_editor(sid),
                            SETTINGS_TEXT_SOFT),)
            self._item_row(
                frame, scenario_id,
                badge="随包启用" if scenario_id in bundled else None,
                buttons=buttons)

    # ───────────────── 分区：取材表 ─────────────────
    @staticmethod
    def _pinned_front(values, current):
        """把当前选中项排到最前（"启用项优先"）。"""
        rest = [v for v in values if v != current]
        return [current] + rest if current else rest

    def _build_materials_section(self):
        body, _ = self._add_section(
            "取材表", note="一组四张表同时生效，切换即时生效")
        body.columnconfigure(0, weight=2, uniform="hub_tables")
        body.columnconfigure(1, weight=3, uniform="hub_tables")

        _, ctrl = self._make_table_row(body, 0, "当前取材表")
        self.material_table_var = tk.StringVar(value=self.material_table)
        groups = self._pinned_front(self._material_groups(), self.material_table)
        if not groups:
            groups = [DEFAULT_MATERIAL_TABLE]
        self.material_table_menu = self._make_option_button(
            ctrl, groups, self._on_material_table_changed, self.material_table_var
        )

        self._material_members_frame = ctk.CTkFrame(body, fg_color="transparent")
        self._material_members_frame.grid(
            row=1, column=0, columnspan=2, sticky='ew', padx=10, pady=(0, 8))
        self._refresh_material_members()

    def _material_groups(self):
        """可用取材表组名（包内 + 自由层）。"""
        return self.name_repo.get_groups()

    def _refresh_material_members(self):
        """重建组内成员明细：文件名 + 是否回落内置。"""
        frame = self._material_members_frame
        self._clear(frame)
        present = set(present_members(self.material_table,
                                      self._resource_data_dir(), self.world_state))
        for member in MATERIAL_MEMBERS:
            row = ctk.CTkFrame(frame, fg_color="transparent")
            row.pack(fill='x')
            ctk.CTkLabel(row, text=member.label, width=64, anchor='w',
                         font=ui_fonts.ui_font(11),
                         text_color=SETTINGS_TEXT_SOFT).pack(side='left')
            ctk.CTkLabel(row, text=member.file_name(), anchor='w',
                         font=ui_fonts.ui_font(11),
                         text_color=SETTINGS_TEXT_SOFT).pack(side='left')
            if member.key not in present:
                ctk.CTkLabel(row, text="回落内置", anchor='w',
                             font=ui_fonts.ui_font(11),
                             text_color=SETTINGS_TEXT_SOFT).pack(
                    side='left', padx=(8, 0))

    def _make_table_row(self, parent, row, text):
        """取材表分区的一行：标签在左，下拉控件在右。"""
        label = ctk.CTkLabel(
            parent, text=text, anchor='w',
            font=_FONT, text_color=SETTINGS_LABEL
        )
        label.grid(row=row, column=0, sticky='w', padx=(10, 12), pady=(0, 8))
        control = ctk.CTkFrame(parent, fg_color="transparent")
        control.grid(row=row, column=1, sticky='e', padx=(0, 10), pady=(0, 8))
        return label, control

    def _make_option_button(self, parent, values, command, variable=None):
        """行内选项控件统一用 widgets.OptionButton：按钮 + 顶部锚定下拉列表。"""
        btn = OptionButton(
            parent, values=values, command=command, variable=variable,
            width=120, height=28, corner_radius=7, font=_FONT,
            fg_color=SETTINGS_INPUT_BG, border_color=SETTINGS_INPUT_BORDER,
            hover_color=SETTINGS_INPUT_HOVER, text_color=SETTINGS_LABEL,
            text_color_disabled=SETTINGS_TEXT_SOFT,
            popup_fg_color=SETTINGS_INPUT_BG,
            popup_hover_color=SETTINGS_INPUT_HOVER,
            popup_text_color=SETTINGS_LABEL,
        )
        btn.pack(fill='both', expand=True)
        return btn

    # ───────────────── 分区：挑战包 ─────────────────
    def _build_challenges_section(self):
        can_nav = self.gui_ref is not None and hasattr(self.gui_ref, "show_page")
        action = ("前往挑战模式", lambda: self.gui_ref.show_page("challenge")) \
            if can_nav else None
        self._challenge_rows_frame, _ = self._add_section(
            "挑战包", note="挑战包的创建与启动在「挑战模式」页",
            action=action)
        self._refresh_challenge_rows()

    def _refresh_challenge_rows(self):
        """重建挑战包卡片：随世界包附带的（启用）包排在最前。

        图标取 .chal 同目录同名 .png 旁车文件，缺失时用内置默认图标；
        点击卡片深跳到「挑战模式」。
        """
        frame = self._challenge_rows_frame
        self._clear(frame)
        try:
            manager = ChallengeService(self.settings_repo,
                                       world_state=self.world_state)
            metas = manager.get_all_metas()
        except Exception as exc:
            self._note_row(frame, f"挑战包读取失败: {exc}")
            return
        if not metas:
            self._note_row(frame, "暂无挑战包，可在「挑战模式」页创建")
            return

        default_icon = os.path.join(icon_dir(), "challenge_pack.png")
        cards: List[IconCard] = []
        for meta in metas:
            filename = meta.get("filename", "")
            title = meta.get("pack_base") or (
                os.path.splitext(filename)[0] if filename else "未命名")
            icon = manager.pack_icon_path(meta.get("file_path", "")) or default_icon
            cards.append(IconCard(
                frame, title, icon_path=icon, fallback_emoji="⚡",
                badge="随包启用" if meta.get("bundled") else None,
                on_click=lambda: self._goto_page("challenge")))
        self._grid_cards(frame, cards)

    # ───────────────── 本地分区统一刷新 ─────────────────
    def _refresh_local_sections(self):
        """重建会因外部操作而变化的行：世界包 / 副本方案 / 挑战包。"""
        self._refresh_world_rows()
        self._refresh_scenario_rows()
        self._refresh_challenge_rows()

    def on_page_shown(self):
        """切回本页时刷新：拾取挑战模式 / 副本编辑页的外部增删。"""
        self._refresh_scenario_rows()
        self._refresh_challenge_rows()
        self._refresh_material_options()

    def _refresh_material_options(self):
        """重读取材表组列表与成员明细（外部增删目录后切回本页可见）。"""
        groups = self._pinned_front(self._material_groups(), self.material_table)
        if not groups:
            groups = [DEFAULT_MATERIAL_TABLE]
        self.material_table_menu.configure(values=groups)
        self._refresh_material_members()

    def _goto_page(self, page_key):
        if self.gui_ref is not None and hasattr(self.gui_ref, "show_page"):
            self.gui_ref.show_page(page_key)

    def _goto_scenario_editor(self, scenario_id):
        """深跳到副本编辑页并载入指定方案（世界包锁定时行按钮不可用）。"""
        self._goto_page("dungeon")
        editor = getattr(self.gui_ref, "scenario_editor", None) if self.gui_ref else None
        if editor is not None and scenario_id:
            editor._load_scenario(scenario_id)

    # ───────────────── 资源键即时落盘 ─────────────────
    def _persist_resource_keys(self):
        """把本页当前选择写进共享 settings 并即时落盘（只动资源域键）。"""
        self.settings.update({
            "material_table": self.material_table,
            "selected_styles": self.selected_styles,
            "selected_quip_styles": self.selected_quip_styles,
        })
        self.settings_repo.save_resource_keys(self.settings)

    # ───────────────── 世界包资源枚举（创建对话框用） ─────────────────
    def _build_available_resources(self) -> Dict[str, list]:
        """返回各资源类型可选打包的具体条目（供创建对话框的选择控件使用）。

        只有需要业务过滤或跨目录合并的类型才登记取条目名的函数；其余类型
        交给注册表的 ``list_ids``，所以新增一类资源不必改这里。
        """
        sources = self._resource_id_sources()
        data_dir = self._resource_data_dir()
        resources: Dict[str, list] = {}
        for type_id in LIST_RESOURCE_TYPES:
            source = sources.get(type_id)
            try:
                items = list(source()) if source is not None else registry.list_ids(
                    type_id, data_dir, self.world_state)
            except Exception as exc:  # 单个类型出错不该拖垮整个对话框
                print(f"[ResourceHub] 枚举 {type_id} 失败: {exc}")
                continue
            items = [str(item) for item in items if str(item or "")]
            if items:
                resources[type_id] = items
        return resources

    def _resource_id_sources(self) -> Dict[str, Callable[[], list]]:
        """类型 id → 取可选条目名的函数（未登记的类型走注册表）。"""
        return {
            "landmarks": lambda: self.landmark_repo.get_styles(),
            "quips": lambda: self.quip_repo.get_styles(),
            "materials": lambda: self._material_groups(),
            "topics": self._available_topics,
            "scenarios": self._available_scenarios,
            "challenges": self._available_challenges,
            "behaviors": self._available_behaviors,
        }

    def _resource_data_dir(self) -> str:
        if self.world_manager is not None:
            return self.world_manager.data_dir
        from paths import data_dir
        return data_dir()

    def _refresh_resource_catalog(self) -> None:
        """重建 ``user/catalog.json`` 索引缓存（内容没变不会写盘）。

        缓存不是权威，失败只影响在线资源的列表，不该挡住本地操作。
        """
        try:
            refresh_catalog(self._resource_data_dir(), self.world_state)
        except Exception as exc:
            print(f"[ResourceHub] 资源索引刷新失败: {exc}")

    def _available_topics(self) -> list:
        # 话题表整表替换，内置表恒可用（无对应文件时指向代码里那张）
        from services.chat import topic_tables
        return topic_tables.list_tables(self._resource_data_dir(), self.world_state)

    def _available_scenarios(self) -> list:
        scenario_repo = getattr(self.gui_ref, "_scenario_repo", None) if self.gui_ref else None
        if scenario_repo is None:
            return []
        return [d for d in scenario_repo.list_all() if not is_default_scenario(d)]

    def _available_challenges(self) -> list:
        # 列出全部自由 .chal（打包不依赖密钥，密钥由用户手动放入包内）
        manager = ChallengeService(self.settings_repo, world_state=self.world_state)
        return [f for f in manager.get_available_packs()
                if not manager.is_bundled(f)]

    def _available_behaviors(self) -> list:
        if self.world_manager is None:
            return []
        behaviors = list(list_behavior_packs(self.world_manager.data_dir))
        if (self.world_state is not None and self.world_state.active
                and self.world_state.owns("behaviors")):
            for name in self.world_state.manifest.resources.get("behaviors") or []:
                if name not in behaviors:
                    behaviors.append(name)
            behaviors.sort()
        return behaviors

    def _set_table_menus_locked(self):
        """按包锁定的资源键禁用取材表菜单（规则段的体型/种子锁在设置页）。"""
        locked = self.world_state.locked_keys() if self.world_state is not None else set()
        self.material_table_menu.configure(
            state="disabled" if "material_table" in locked else "normal")

    def _sync_table_vars(self):
        """激活/卸载/解散后，按 self.settings 回读取材表选择。"""
        group = self.settings.get("material_table", DEFAULT_MATERIAL_TABLE)
        groups = self._pinned_front(self._material_groups(), group)
        if not groups:
            groups = [DEFAULT_MATERIAL_TABLE]
        self.material_table = groups[0]
        self.material_table_var.set(self.material_table)
        self.material_table_menu.configure(values=groups)
        self._refresh_material_members()

    def _after_world_state_change(self):
        """世界包状态变化后：回读本页状态 → 推给上下文 → 全局刷新。"""
        self._sync_table_vars()
        self.selected_styles = self.settings.get("selected_styles", [DEFAULT_LANDMARK_STYLE])
        self.selected_quip_styles = self.settings.get("selected_quip_styles", [])
        self._sync_all_states()
        if self.gui_ref is not None:
            if hasattr(self.gui_ref, "on_material_table_changed"):
                self.gui_ref.on_material_table_changed(self.material_table)
            if hasattr(self.gui_ref, "refresh_world_ui"):
                self.gui_ref.refresh_world_ui()
        self._refresh_local_sections()

    def on_world_state_changed(self):
        """世界包状态变化后的本页刷新（由 gui_ref.refresh_world_ui 调度）。"""
        self._sync_table_vars()
        self._refresh_local_sections()

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
                    scenario_repo=getattr(self.gui_ref, "_scenario_repo", None) if self.gui_ref else None,
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
        self._refresh_world_rows()

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
    def _on_material_table_changed(self, choice):
        self.material_table = choice or DEFAULT_MATERIAL_TABLE
        self.material_table_var.set(self.material_table)
        self._refresh_material_members()
        if self.on_material_table_changed:
            self.on_material_table_changed(self.material_table)
        self._persist_resource_keys()

    # ───────────────── 风格选择逻辑 ─────────────────
    def _sync_all_states(self):
        self._sync_landmark_styles()
        self._sync_quip_styles()

    def _sync_landmark_styles(self, filter_world=None):
        styles = self.landmark_repo.get_styles()
        selected = set(self.selected_styles)
        entries = []
        for style in styles:
            reg_world = world_of(self.landmark_repo.load_style_address(style))
            if filter_world and reg_world and reg_world != filter_world:
                continue  # 同一维度自动筛选：隐藏其它维度的地标风格
            landmarks = self.landmark_repo.load(style)
            entries.append((f"{style} ({len(landmarks)})", style,
                            style in selected))
        # 启用（已选）风格排在最前，其余保持原顺序
        entries.sort(key=lambda e: not e[2])
        display_items = [e[0] for e in entries]
        selected_indices = [i for i, e in enumerate(entries) if e[2]]
        self.landmark_selector.sync_items(display_items, selected_indices)

    def _sync_quip_styles(self, filter_world=None):
        styles = self.quip_repo.get_styles()
        size_order = ["small", "medium", "large", "huge", "colossal"]
        selected = set(self.selected_quip_styles)
        entries = []
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
            entries.append((f"{style} ({', '.join(counts)})", style,
                            style in selected))
        # 启用（已选）风格排在最前，其余保持原顺序
        entries.sort(key=lambda e: not e[2])
        display_items = [e[0] for e in entries]
        selected_indices = [i for i, e in enumerate(entries) if e[2]]
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
        """外部深跳：切到本地资源视图并滚动到风格组分区。"""
        self._show_view("local")
        self.update_idletasks()
        canvas = getattr(self.local_view, '_parent_canvas', None)
        frame = getattr(self.local_view, '_parent_frame', None)
        if canvas is None or frame is None:
            return
        try:
            total_height = frame.winfo_reqheight()
            if total_height <= 0:
                return
            y = (self.landmark_selector.winfo_rooty()
                 - self.local_view.winfo_rooty())
            fraction = max(0.0, min(1.0, y / total_height))
            canvas.yview_moveto(fraction)
        except Exception:
            pass
