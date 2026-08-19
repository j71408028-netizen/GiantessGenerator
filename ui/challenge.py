# ui/challenge.py
import os
import json

import ui.common.dialogs
import customtkinter as ctk

from ai import resolve_ai_config
from services.challenge_service import ChallengeService
from models import Personality, BodyPreset
from ui.common.widgets import ClickableCard, CollapsibleBlock, StyleListBox, CTkScrollableDropdownFrame
from ui.common.theme import (
    TEXT, HARD_TITLE, SOFT, TEXT_MUTED, PLACEHOLDER,
    PNL_BG, BORDER_ALT, HOVER_ALT, MENU_HOVER, BLUE_HOVER,
    STATUS_OK, OK_HOVER, ERR_STRONG, ERR_HOVER,
    TEXT_CYAN, TEXT_ORANGE, STATUS_ERR,
)
from ui.common import fonts as ui_fonts


class ChallengeModePanel(ctk.CTkFrame):
    def __init__(self, parent, gui_ref):
        super().__init__(parent, fg_color="transparent")
        self.gui = gui_ref
        self.challenge_mgr = ChallengeService(
            gui_ref._settings_repo, gui_ref._character_repo,
            gui_ref._landmark_repo, gui_ref._quip_repo, gui_ref._dungeon_repo,
            world_state=getattr(gui_ref, "world_state", None)
        )

        self._build_ui()
        self._refresh_packs()

    def _build_ui(self):
        title_bar = ctk.CTkFrame(self, fg_color="transparent")
        title_bar.pack(fill='x', padx=22, pady=(13, 2))

        self.title_label = ctk.CTkLabel(title_bar, text="🎯  挑战模式", font=ui_fonts.ui_font(16, "bold"),
                                        text_color=SOFT)
        self.title_label.pack(side='left')

        info_bar = ctk.CTkFrame(self, fg_color="transparent")
        info_bar.pack(fill='x', padx=22, pady=(2, 5))
        self.info_label = ctk.CTkLabel(info_bar, text="", font=ui_fonts.ui_font(11),
                                       text_color=PLACEHOLDER)
        self.info_label.pack(side='left')

        # 创建挑战包折叠块：标题按钮置于标题栏右侧，内容区展开在信息栏下方
        self.create_block = CollapsibleBlock(self, "创建挑战包", expanded=False,
                                             body_after=info_bar, width=300,
                                             header_parent=title_bar)
        self.create_block.header.pack(side='right', padx=10)

        # ---- 创建挑战包折叠块（嵌入主界面） ----
        form_body = self.create_block.body

        ctk.CTkLabel(form_body, text="挑战包名称", font=ui_fonts.ui_font(13, "bold"),
                     text_color=HARD_TITLE).pack(anchor='w', pady=(6, 2), padx=10)
        self.pack_name_entry = ctk.CTkEntry(form_body, width=300, placeholder_text="输入挑战包名称（不含扩展名）",
                                            border_width=1, border_color=BORDER_ALT,
                                            fg_color=PNL_BG)
        self.pack_name_entry.pack(fill='x', pady=(0, 6), padx=10)

        # ---- 三列布局 ----
        three_col = ctk.CTkFrame(form_body, fg_color="transparent")
        three_col.pack(fill='x', pady=2, padx=10)
        three_col.grid_columnconfigure(0, weight=1)
        three_col.grid_columnconfigure(1, weight=1)
        three_col.grid_columnconfigure(2, weight=1)

        # 左列：角色 + 副本方案
        left_col = ctk.CTkFrame(three_col, fg_color="transparent")
        left_col.grid(row=0, column=0, sticky='nsew', padx=(0, 10))

        ctk.CTkLabel(left_col, text="选择角色", font=ui_fonts.ui_font(11, "bold"),
                     text_color=SOFT).pack(anchor='w', pady=(2, 2))
        self.char_combo = ctk.CTkComboBox(left_col, values=[], state="readonly",
                                          border_width=1, border_color=BORDER_ALT,
                                          fg_color=PNL_BG,
                                          button_color=HOVER_ALT,
                                          button_hover_color=HARD_TITLE,
                                          dropdown_fg_color=PNL_BG,
                                          dropdown_hover_color=HOVER_ALT)
        self.char_combo.pack(fill='x', pady=(2, 2))
        self._char_dropdown = CTkScrollableDropdownFrame(
            attach=self.char_combo, values=[], command=self._on_char_change,
            height=160, button_height=28,
            fg_color=PNL_BG,
            hover_color=BLUE_HOVER,
            frame_border_color=BORDER_ALT,
            text_color=TEXT,
            button_color=PNL_BG,
            scrollbar_button_color=HOVER_ALT,
            scrollbar_button_hover_color=HOVER_ALT,
            frame_border_width=1, justify="left")
        self.char_info_label = ctk.CTkLabel(left_col, text="", font=ui_fonts.ui_font(10),
                                             text_color=PLACEHOLDER)
        self.char_info_label.pack(anchor='w')

        ctk.CTkLabel(left_col, text="副本方案", font=ui_fonts.ui_font(11, "bold"),
                     text_color=SOFT).pack(anchor='w', pady=2)
        dungeons = self.gui._dungeon_repo.list_all()
        self.dungeon_combo = ctk.CTkComboBox(left_col, values=dungeons, state="readonly",
                                             border_width=1, border_color=BORDER_ALT,
                                             fg_color=PNL_BG,
                                             button_color=HOVER_ALT,
                                             button_hover_color=HARD_TITLE,
                                             dropdown_fg_color=PNL_BG,
                                             dropdown_hover_color=HOVER_ALT)
        self.dungeon_combo.pack(fill='x', pady=(2, 6))
        self._dungeon_dropdown = CTkScrollableDropdownFrame(
            attach=self.dungeon_combo, values=dungeons, command=self.dungeon_combo.set,
            height=160, button_height=28,
            fg_color=PNL_BG,
            hover_color=BLUE_HOVER,
            frame_border_color=BORDER_ALT,
            text_color=TEXT,
            button_color=PNL_BG,
            scrollbar_button_color=HOVER_ALT,
            scrollbar_button_hover_color=HOVER_ALT,
            frame_border_width=1, justify="left")
        if dungeons:
            self.dungeon_combo.set(dungeons[0])

        # 中列：地标风格组
        center_col = ctk.CTkFrame(three_col, fg_color="transparent")
        center_col.grid(row=0, column=1, sticky='nsew', padx=10)

        self.landmark_selector = StyleListBox(center_col, "地标风格组", height=2)
        self.landmark_selector.pack(fill='x', pady=2)
        self.landmark_selector.add_button("默认", self.landmark_selector.set_default, padx=0)
        self.landmark_selector.add_button("全选", self.landmark_selector.select_all, padx=10)

        # 右列：描述风格组
        right_col = ctk.CTkFrame(three_col, fg_color="transparent")
        right_col.grid(row=0, column=2, sticky='nsew', padx=(10, 0))

        self.quip_selector = StyleListBox(right_col, "描述风格组", height=2)
        self.quip_selector.pack(fill='x', pady=2)
        self.quip_selector.add_button("清空", self.quip_selector.clear_selection, padx=0)
        self.quip_selector.add_button("全选", self.quip_selector.select_all, padx=10)

        # 简介（可用 / 分隔背景与目标文本，卡片视图中会自动着色）
        ctk.CTkLabel(form_body, text="简介（可用 / 分隔背景与目标文本）", font=ui_fonts.ui_font(11, "bold"),
                     text_color=SOFT).pack(anchor='w', pady=(6, 2), padx=10)
        self.intro_text = ctk.CTkTextbox(form_body, height=80, wrap='word',
                                         border_width=1, border_color=BORDER_ALT,
                                         fg_color=PNL_BG)
        self.intro_text.pack(fill='x', pady=(0, 6), padx=10)

        # 创建/取消按钮
        btn_frame = ctk.CTkFrame(form_body, fg_color="transparent")
        btn_frame.pack(fill='x', pady=(6, 6))
        ctk.CTkButton(btn_frame, text="创建", command=self._do_create, width=100,
                      fg_color="transparent",
                    text_color=STATUS_OK,
                    hover_color=OK_HOVER,
                    border_width=2, border_color=STATUS_OK,
                      corner_radius=10).pack(side='left', padx=10)
        ctk.CTkButton(btn_frame, text="取消", command=self._toggle_create_block, width=100,
                      fg_color="transparent",
                      text_color=TEXT_MUTED,
                      hover_color=HOVER_ALT,
                      border_width=1, border_color=BORDER_ALT,
                      corner_radius=8).pack(side='left')

        # 卡片列表
        self.card_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.card_frame.pack(fill='both', expand=True, padx=22, pady=5)
        self._pack_cards = {}

        self._init_char_data()
        self._sync_landmark_styles()
        self._sync_quip_styles()

    def _init_char_data(self):
        self.char_map = {}
        states_dir = self.gui._character_repo.states_dir
        if not os.path.exists(states_dir):
            return
        chars = []
        for folder in os.listdir(states_dir):
            data_file = os.path.join(states_dir, folder, "info.json")
            if not os.path.isfile(data_file):
                continue
            try:
                with open(data_file, 'r', encoding='utf-8') as f:
                    d = json.load(f)
                cid = d.get('giantess_id', folder)
                name = d.get('name', '')
                nick = d.get('nick', '')
                dis = f"{name} ({nick})" if nick else name
                chars.append(dis)
                self.char_map[dis] = (cid, d)
            except Exception:
                pass
        chars.sort()
        self.char_combo.configure(values=chars)
        if hasattr(self, '_char_dropdown'):
            self._char_dropdown.configure(values=chars)
        if chars:
            self.char_combo.set(chars[0])
            self._on_char_change(chars[0])

        self.char_combo.configure(command=self._on_char_change)

    def _on_char_change(self, choice):
        if choice in getattr(self, 'char_map', {}):
            _, d = self.char_map[choice]
            h = d.get('height', 0)
            p = d.get('personality', {})
            pname = p.get('name', '无') if isinstance(p, dict) else getattr(p, 'name', '无')
            self.char_info_label.configure(text=f"身高: {h:.1f}m | 个性: {pname}")

    def _sync_landmark_styles(self):
        styles = self.gui._landmark_repo.get_styles()
        display_items = []
        for style in styles:
            landmarks = self.gui._landmark_repo.load(style)
            display_items.append(f"{style} ({len(landmarks)})")
        default_idx = styles.index(self.gui._landmark_repo.default_style) if self.gui._landmark_repo.default_style in styles else 0
        self.landmark_selector.sync_items(display_items, [default_idx])

    def _sync_quip_styles(self):
        styles = self.gui._quip_repo.get_styles()
        size_order = ["small", "medium", "large", "huge", "colossal"]
        display_items = []
        for style in styles:
            quips = self.gui._quip_repo.load(style)
            counts = []
            for size in size_order:
                matrix = quips.get(size, {})
                total = sum(len(qlist) for qlist in matrix.values())
                counts.append(str(total))
            display_items.append(f"{style} ({', '.join(counts)})")
        self.quip_selector.sync_items(display_items)

    def _update_info_bar(self, count: int):
        self.info_label.configure(text=f"挑战包: {count} 个")

    def set_world_active(self, active: bool):
        """世界包加载状态：挑战模式标题保持不变；加载期间禁用挑战包新建。"""
        self.create_block.collapse()
        self.create_block.header.configure(
            state="disabled" if active else "normal",
            text=("▶ 创建挑战包（世界包加载期间不可用）" if active
                  else "▶ 创建挑战包"))

    def refresh_world_resources(self):
        """世界包状态变化后，刷新地标/描述风格组、副本方案下拉框与挑战包列表。"""
        self._sync_landmark_styles()
        self._sync_quip_styles()
        dungeons = self.gui._dungeon_repo.list_all()
        self.dungeon_combo.configure(values=dungeons)
        if hasattr(self, '_dungeon_dropdown'):
            self._dungeon_dropdown.configure(values=dungeons)
        current = self.dungeon_combo.get()
        if current not in dungeons:
            self.dungeon_combo.set(dungeons[0] if dungeons else "")
        self._refresh_packs()

    def _refresh_packs(self):
        for w in self.card_frame.winfo_children():
            w.destroy()

        self._pack_cards.clear()
        metas = self.challenge_mgr.get_all_metas()
        self._update_info_bar(len(metas))
        world_active = (getattr(self.gui, "world_state", None) is not None
                        and self.gui.world_state.active)

        if not metas:
            msg = ("本世界包未附带挑战包\n\n自由挑战包会显示在此处" if world_active
                   else "暂无挑战包\n\n点击上方「创建挑战包」折叠块创建新挑战")
            ctk.CTkLabel(self.card_frame, text=msg,
                         font=ui_fonts.ui_font(14),
                         text_color=PLACEHOLDER).pack(expand=True, pady=60)
            return

        for meta in metas:
            filename = meta.get("filename", "")
            file_path = meta.get("file_path", "")
            pack_base = meta.get("pack_base", os.path.splitext(filename)[0])
            bundled = meta.get("bundled", False)
            intro = meta.get("intro", "")[:60]
            title = os.path.splitext(filename)[0] if filename else pack_base
            if not bundled and world_active:
                title = f"{title}  ·  非本世界场景"

            def _colorize_card(tb):
                txt = tb._textbox
                txt.tag_configure("cyan_part", foreground=TEXT_CYAN)
                txt.tag_configure("orange_part", foreground=TEXT_ORANGE)
                content = txt.get("1.0", "end-1c")
                dbl = "//"
                if dbl in content:
                    dbl_idx = content.index(dbl)
                    txt.tag_add("orange_part", f"1.0 + {dbl_idx + 2}c", "end")
                    if "/" in content and content.index("/") < dbl_idx:
                        s_idx = content.index("/")
                        txt.tag_add("cyan_part", f"1.0 + {s_idx + 1}c", f"1.0 + {dbl_idx}c")
                elif "/" in content:
                    s_idx = content.index("/")
                    txt.tag_add("cyan_part", f"1.0 + {s_idx + 1}c", "end")

            buttons = []
            if not bundled:
                buttons.append({
                    "text": "删除",
                    "command": lambda p=file_path: self._delete_pack(p),
                    "fg_color": "transparent",
                    "text_color": STATUS_ERR,
                    "hover_color": ERR_HOVER,
                    "border_width": 1,
                    "border_color": STATUS_ERR,
                    "corner_radius": 8,
                    "width": 50,
                    "pack_kw": {"side": "top", "pady": 2}
                })
            card = ClickableCard(
                self.card_frame,
                title=title,
                title_font=ui_fonts.ui_font(13, "bold"),
                detail=intro + ("..." if intro and len(intro) >= 60 else ""),
                is_detail_textbox=True,
                detail_height=55,
                detail_cb=_colorize_card,
                info_pad=(10, 6),
                on_click=lambda p=pack_base: self._launch_pack(p),
                gold_hover=True,
                cursor="xterm",
                buttons=[{
                    "text": "读取",
                    "command": lambda p=pack_base: None,
                    "fg_color": "transparent",
                    "text_color": STATUS_OK,
                    "hover_color": OK_HOVER,
                    "border_width": 1,
                    "border_color": STATUS_OK,
                    "corner_radius": 8,
                    "width": 50,
                    "pack_kw": {"side": "top", "pady": 2}
                }] + buttons
            )
            card.pack(fill='x', padx=5, pady=3)
            self._pack_cards[file_path] = card

    def _on_create_ok(self, key):
        ui.common.dialogs.showinfo("成功", f"挑战包已创建\n自动生成秘钥:\n{key}\n\n秘钥已写入环境变量")
        self._refresh_packs()
        if hasattr(self.gui, 'refresh_challenge_pack_dropdowns'):
            self.gui.refresh_challenge_pack_dropdowns()

    def _delete_pack(self, file_path):
        if self.challenge_mgr._is_bundled_path(file_path):
            ui.common.dialogs.showwarning("提示", "世界包附带的挑战包不可删除")
            return
        filename = os.path.basename(file_path)
        if not ui.common.dialogs.askyesno("确认", f"确定删除挑战包 '{filename}' 吗？"):
            return
        card = self._pack_cards.pop(file_path, None)
        if card:
            card.destroy()

        def _do_work():
            if os.path.exists(file_path):
                os.remove(file_path)
            self.after(0, lambda: self._on_delete_done())

        self.after(50, _do_work)

    def _on_delete_done(self):
        remaining = self.challenge_mgr.get_all_metas()
        self._update_info_bar(len(remaining))
        if not remaining:
            self._refresh_packs()
        if hasattr(self.gui, 'refresh_challenge_pack_dropdowns'):
            self.gui.refresh_challenge_pack_dropdowns()

    def _toggle_create_block(self):
        self.create_block.toggle()
        if self.create_block._expanded:
            self.card_frame.pack_forget()
        else:
            self.card_frame.pack(fill='both', expand=True, padx=22, pady=5)

    def _do_create(self):
        if (getattr(self.gui, "world_state", None) is not None
                and self.gui.world_state.active):
            ui.common.dialogs.showwarning("提示", "世界包加载期间不可创建挑战包")
            return
        pack_name = self.pack_name_entry.get().strip()
        if not pack_name:
            ui.common.dialogs.showerror("错误", "请输入挑战包名称")
            return

        choice = self.char_combo.get()
        cid = self.char_map.get(choice, ("", {}))[0]
        if not cid:
            ui.common.dialogs.showerror("错误", "请选择角色")
            return

        lms = self.landmark_selector.get_selected_raw_names()
        qps = self.quip_selector.get_selected_raw_names()
        if not lms:
            ui.common.dialogs.showerror("错误", "请至少选择一个地标风格组")
            return
        if not qps:
            qps = []

        dg = self.dungeon_combo.get()
        if not dg:
            ui.common.dialogs.showerror("错误", "请选择副本方案")
            return

        intro = self.intro_text.get("1.0", "end-1c").strip()

        # 先关闭折叠块，界面即时响应
        self._toggle_create_block()

        def _do_work():
            try:
                key = self.challenge_mgr.create_challenge(
                    cid, lms, qps, dg, intro, pack_name
                )
                self.after(0, lambda k=key: self._on_create_ok(k))
            except Exception as e:
                self.after(0, lambda e=e: ui.common.dialogs.showerror("错误", str(e)))

        self.after(50, _do_work)

    def _launch_pack(self, pack_base: str):
        try:
            data = self.challenge_mgr.open_challenge_by_name(pack_base)
        except Exception as e:
            ui.common.dialogs.showerror("错误", f"打开挑战包失败: {e}")
            return

        if data is None:
            ui.common.dialogs.showerror("错误", "无法读取挑战包内容")
            return

        self._launch_dungeon_from_data(data)

    def _launch_dungeon_from_data(self, data: dict):
        char_data = data.get("character_data", {})
        name = char_data.get("name", "未知")
        nick = char_data.get("nick", "")
        height = char_data.get("height", 1.6)
        original_height = char_data.get("original_height", 1.6)
        intro_hidden = char_data.get("intro_hidden", "")
        intro_visible = char_data.get("intro_visible", "")
        tags = char_data.get("selected_tags", [])
        greed = char_data.get("greed", 0)
        body_parts = char_data.get("body_parts", {})

        personality_dict = char_data.get("personality")
        if not personality_dict:
            ui.common.dialogs.showerror("错误", "挑战包中缺少性格数据")
            return
        personality = Personality.from_dict(personality_dict)

        preset = BodyPreset(
            name="标准", leg_ratio=0.5, foot_length_ratio=0.15, arm_span_ratio=0.4,
            index_finger_ratio=0.05, palm_length_ratio=0.1, chest_width_ratio=0.25,
            thigh_diameter_ratio=0.12, forearm_diameter_ratio=0.08,
            knee_height_ratio=0.3, ankle_height_ratio=0.085,
            finger_gap_ratio=0.02, stride_ratio=0.8
        )

        dungeon_config = data.get("dungeon_config", {})
        dungeon_id = data.get("dungeon_id", "")

        gui_settings = self.gui._settings_repo.load()
        ai_config = resolve_ai_config(gui_settings)
        from ui.common.fonts import dungeon_font_default
        dungeon_font = gui_settings.get("dungeon_font", dungeon_font_default())

        landmark_styles = data.get("landmark_styles", [])
        quip_styles_data = data.get("quip_styles", [])

        saved_styles = self.gui.context.selected_styles.copy()
        saved_quip = self.gui.context.selected_quip_styles.copy()

        self.gui.context.selected_styles = landmark_styles
        self.gui.context.selected_quip_styles = quip_styles_data
        self.gui.context.reload_merged_data()
        self.gui.selected_styles = self.gui.context.selected_styles
        self.gui.selected_quip_styles = self.gui.context.selected_quip_styles

        from dungeon.window import DungeonSessionWindow
        DungeonSessionWindow(
            self.gui.root,
            name=name, nick=nick,
            personality=personality, preset=preset,
            original_height=original_height, intro_hidden=intro_hidden,
            intro_visible=intro_visible, tags=tags, uploaded_image=None,
            dungeon_config=dungeon_config, dungeon_repo=self.gui._dungeon_repo,
            merged_landmarks=self.gui.context.merged_landmarks, merged_quips=self.gui.context.quips,
            selected_styles=landmark_styles, selected_quip_styles=quip_styles_data,
            detail_pools=self.gui.context.detail_pools, height=height,
            ai_config=ai_config,
            greed=greed, is_replay=False, replay_data=None,
            dungeon_id=dungeon_id, dungeon_font=dungeon_font, body_parts=body_parts,
            character=None, character_repo=self.gui._character_repo, gui=self.gui,
            mode="challenge"
        )

        self.gui.context.selected_styles = saved_styles
        self.gui.context.selected_quip_styles = saved_quip
        self.gui.context.reload_merged_data()
        self.gui.selected_styles = self.gui.context.selected_styles
        self.gui.selected_quip_styles = self.gui.context.selected_quip_styles
