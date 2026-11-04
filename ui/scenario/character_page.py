"""角色页：方案里「有谁」以及每个人的音色与演化量。

三种角色来自不同地方，页面上按这个分工组织：

- **她**：本局角色档案（名字 / 性格 / 体型）不在方案里，这里只配她的音色与
  归属她的演化量；
- **主角**：称呼（Solea / Bulla 的对话行用它做说话人标注）与一段身份背景；
- **人物表**：配角（重要角色 / 群像），设定只在首次出场时注入一次；
- **其他角色**：不在人物表里的配角，用一把共享音色池按名字稳定分配；
- **环境**：世界层面的量（总伤亡等）归在这里，不发声。

演化量按归属分组，页面底部的「演化量」视图是同一批数据的平铺表（可再按归属
过滤），两者不是两套数据。语音配置仍在方案配置的 ``voice`` 字段里，只是从
「通用」页搬到了这里。
"""

import re

import customtkinter as ctk

import ui.ctk.dialogs
from dungeon.audio import buses
from dungeon.audio.preview import VoicePreview
from dungeon.audio.speech import (DEFAULT_OTHER_VOICES, DEFAULT_RATE,
                                  DEFAULT_VOICE_HER, DEFAULT_VOICE_PROTAGONIST,
                                  DEFAULT_VOICE_LEVEL, RECOMMENDED_VOICES,
                                  edge_tts_available, normalize_voice,
                                  voice_level)
from dungeon.cast import (CAST_PRONOUNS, DEFAULT_CAST_ROLE,
                          normalize_cast)
from dungeon.coupling import VELUM
from dungeon.schema import EVOLUTION_OWNER_LABELS, owner_label
from ui.ctk.dialogs import BaseDialog, DLG_BORDER_STRONG, DLG_PANEL_BG, DLG_TEXT, DLG_TEXT_SOFT
from ui.ctk.theme import (SC_ACTION_BG, SC_ACTION_HOVER, SC_BG, SC_BORDER,
                          SC_BORDER_STRONG, SC_DELETE_BG, SC_DELETE_HOVER,
                          SC_DELETE_TEXT, SC_HOVER, SC_LINK,
                          SC_PANEL_BG, SC_TEXT, SC_TEXT_SOFT, SC_TITLE)
from ui.ctk.widgets import CTkToggleButtons, OptionButton
from ui.ctk import fonts as ui_fonts
from ui.scenario.evolution_attributes import EvolutionAtrrManager, state_label

VIEW_CHARACTERS = "人物"
VIEW_ATTRS = "演化量"

#: 角色页字号：标题 / 正文 / 次要说明（与「通用」页保持同一梯次）
_F_TITLE = 14
_F_BODY = 14
_F_SMALL = 12.5

#: 人物表的定位标签（存储值是 ``key`` / ``extra``，界面用中文显示）
ROLE_KEY_LABEL = "重要角色"
ROLE_EXTRA_LABEL = "群像"
ROLE_LABELS = (ROLE_KEY_LABEL, ROLE_EXTRA_LABEL)
ROLE_VALUES = {ROLE_KEY_LABEL: "key", ROLE_EXTRA_LABEL: "extra"}

#: 代词下拉：空 = 不指定（渲染与音色分池都不猜）
PRONOUN_UNSET_LABEL = "不指定"
PRONOUN_LABELS = (PRONOUN_UNSET_LABEL,) + tuple(p for p in CAST_PRONOUNS if p)
PRONOUN_VALUES = {PRONOUN_UNSET_LABEL: ""}
PRONOUN_VALUES.update({p: p for p in CAST_PRONOUNS if p})

#: 别称输入框的切分符（与 dungeon.cast 的归一化保持一致）
_ALIAS_SEPARATORS = re.compile(r"[、,，;；\s]+")


class VoicePickDialog(BaseDialog):
    """音色选择：推荐音色单选 + 手填 edge-tts voice id。"""

    def __init__(self, parent, title="选择音色", current=""):
        super().__init__(parent)
        self.title(title)
        self.result = None
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        ctk.CTkLabel(self, text="推荐音色（也可在下方直接填 voice id）",
                     font=self.UI_FONT, text_color=DLG_TEXT_SOFT).pack(
            padx=20, pady=(16, 6))
        self._var = ctk.StringVar(value=str(current or "").strip())
        list_frame = ctk.CTkScrollableFrame(self, width=360, height=220,
                                            fg_color=DLG_PANEL_BG)
        list_frame.pack(padx=20, fill='both', expand=True)
        for label, voice in RECOMMENDED_VOICES:
            ctk.CTkRadioButton(list_frame, text=f"{label}  {voice}",
                               variable=self._var, value=voice,
                               font=self.UI_FONT,
                               text_color=DLG_TEXT).pack(anchor='w', pady=2)

        entry_row = ctk.CTkFrame(self, fg_color="transparent")
        entry_row.pack(fill='x', padx=20, pady=(8, 0))
        self._entry = ctk.CTkEntry(entry_row, height=28, font=self.UI_FONT,
                                   fg_color=DLG_PANEL_BG,
                                   border_color=DLG_BORDER_STRONG,
                                   text_color=DLG_TEXT)
        self._entry.insert(0, self._var.get())
        self._entry.pack(fill='x', expand=True)

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=(12, 16))
        self._make_std_button(btn_frame, "确定", True, self._ok).pack(side='left', padx=6)
        self._make_std_button(btn_frame, "取消", False, self._cancel).pack(side='left', padx=6)

        self.bind("<Return>", self._ok)
        self.bind("<Escape>", self._cancel)
        self._show_modal(focus_widget=self._entry)

    def _ok(self, _event=None):
        value = self._entry.get().strip() or self._var.get().strip()
        self.result = value or None
        self._close()

    def _cancel(self):
        self.result = None
        self._close()


class CharacterPage(ctk.CTkFrame):
    """「角色」页：人物卡片（含语音）与演化量视图。"""

    #: 卡片里演化量摘要最多列几项（其余折叠成「等 N 项」）
    SUMMARY_LIMIT = 6
    #: 卡片说明文字的折行宽度（角色页左右两栏，每栏约半屏）
    CARD_WRAP = 470

    def __init__(self, parent, scenario_repo, scenario_editor_ref):
        super().__init__(parent, fg_color="transparent")
        self.scenario_editor = scenario_editor_ref
        self._repo = scenario_repo
        self._preview = None
        self._preview_log = []
        self._coupling_level = None
        self._summary_labels = {}
        self._save_job = None      # 主角称呼 / 背景 / 人物表 / 语音的防抖保存
        self._loading = False      # 回填期间抑制自动保存
        self._cast_cards = []      # 人物表卡片的状态（每张卡一组 Tk 变量）

        # 语音：未配置时按默认值走（与方案配置同构，保存时整体写回）
        self._voice = normalize_voice(None)

        self._build_ui()
        self._attach_autosave()

    # ---------------- UI ----------------
    def _build_ui(self):
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(fill='x', padx=10, pady=6)
        self.view_switch = CTkToggleButtons(
            toolbar, values=[VIEW_CHARACTERS, VIEW_ATTRS],
            font=ui_fonts.ui_font(_F_BODY, "bold"), height=30,
            command=self._on_view_switch)
        self.view_switch.pack(side='left', padx=5)
        self.view_switch.set(VIEW_CHARACTERS)
        self._save_hint = ctk.CTkLabel(
            toolbar, text="自动保存", font=ui_fonts.ui_font(_F_SMALL),
            text_color=SC_TEXT_SOFT)
        self._save_hint.pack(side='right', padx=6)

        self.view_host = ctk.CTkFrame(self, fg_color="transparent")
        self.view_host.pack(fill='both', expand=True, padx=10, pady=(0, 10))

        # ---- 人物视图 ----
        self.characters_view = ctk.CTkFrame(self.view_host, fg_color="transparent")
        self._build_voice_bar(self.characters_view)
        # 两栏各自成列：左栏固定，右栏独立滚动
        self.card_columns = ctk.CTkFrame(self.characters_view,
                                         fg_color="transparent")
        self.card_columns.pack(fill='both', expand=True, pady=(6, 0))
        self.card_columns.grid_rowconfigure(0, weight=1)
        self.card_columns.grid_columnconfigure(0, weight=4, uniform='half')
        self.card_columns.grid_columnconfigure(1, weight=5, uniform='half')
        self.left_column = ctk.CTkFrame(self.card_columns, fg_color="transparent")
        self.left_column.grid(row=0, column=0, sticky='nsew', padx=(0, 5))
        self.right_column = ctk.CTkScrollableFrame(
            self.card_columns, fg_color="transparent")
        self.right_column.grid(row=0, column=1, sticky='nsew', padx=(5, 0))

        # ---- 演化量视图（同一批数据的平铺表） ----
        self.attrs_view = ctk.CTkFrame(self.view_host, fg_color="transparent")
        self.evolution_panel = EvolutionAtrrManager(
            self.attrs_view, self._repo, self.scenario_editor)
        self.evolution_panel.pack(fill='both', expand=True)

        self.characters_view.pack(fill='both', expand=True)
        self._rebuild_cards()
        self._apply_voice_state()

    def _build_voice_bar(self, parent):
        """方案级语音条：开关 / 语速 / 音量 / 预听。"""
        bar = ctk.CTkFrame(parent, fg_color=SC_PANEL_BG, corner_radius=10)
        bar.pack(fill='x')

        head = ctk.CTkFrame(bar, fg_color="transparent")
        head.pack(fill='x', padx=12, pady=(8, 2))
        ctk.CTkLabel(head, text="朗读对话",
                     font=ui_fonts.ui_font(_F_BODY, "bold"),
                     text_color=SC_TITLE).pack(side='left')
        self.voice_enabled_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(head, text="", variable=self.voice_enabled_var,
                      width=44, height=22, progress_color=SC_LINK,
                      button_color=SC_TEXT_SOFT,
                      button_hover_color=SC_TEXT_SOFT).pack(side='left', padx=(8, 12))

        ctk.CTkLabel(head, text="语速", font=ui_fonts.ui_font(_F_SMALL),
                     text_color=SC_TEXT_SOFT).pack(side='left')
        self.voice_rate_var = ctk.StringVar(value=DEFAULT_RATE)
        ctk.CTkEntry(head, textvariable=self.voice_rate_var, width=58,
                     height=28, font=ui_fonts.ui_font(_F_SMALL),
                     text_color=SC_TEXT, fg_color=SC_BG,
                     border_color=SC_BORDER_STRONG).pack(side='left', padx=(6, 10))

        ctk.CTkLabel(head, text="音量", font=ui_fonts.ui_font(_F_SMALL),
                     text_color=SC_TEXT_SOFT).pack(side='left')
        self.voice_level_var = ctk.StringVar(
            value=buses.step_display(DEFAULT_VOICE_LEVEL))
        OptionButton(head, values=[label for label, _ in buses.step_choices()],
                     variable=self.voice_level_var, width=88, height=28,
                     font=ui_fonts.ui_font(_F_SMALL),
                     fg_color=SC_BG, border_color=SC_BORDER_STRONG,
                     hover_color=SC_HOVER, text_color=SC_TITLE,
                     popup_fg_color=SC_PANEL_BG, popup_hover_color=SC_HOVER,
                     popup_text_color=SC_TITLE,
                     popup_border_color=SC_BORDER_STRONG,
                     ).pack(side='left', padx=(6, 10))

        self._preview_button = ctk.CTkButton(
            head, text="预听", width=52, height=28,
            font=ui_fonts.ui_font(_F_SMALL), fg_color="transparent",
            border_width=1, border_color=SC_BORDER_STRONG, text_color=SC_LINK,
            hover_color=SC_HOVER, corner_radius=6,
            command=lambda: self._preview_voice("her"))
        self._preview_button.pack(side='left', padx=(0, 6))
        ctk.CTkButton(
            head, text="其它音色", width=76, height=28,
            font=ui_fonts.ui_font(_F_SMALL), fg_color="transparent",
            border_width=1, border_color=SC_BORDER_STRONG, text_color=SC_TEXT_SOFT,
            hover_color=SC_HOVER, corner_radius=6,
            command=lambda: self._preview_voice("other")).pack(side='left')

        self.voice_hint_label = ctk.CTkLabel(
            bar, text="", font=ui_fonts.ui_font(_F_SMALL),
            text_color=SC_TEXT_SOFT, anchor='w', justify='left', wraplength=900)
        self.voice_hint_label.pack(fill='x', padx=12, pady=(0, 8))
        # 预听状态按需显示：没有状态时不占一行高度
        self._preview_status = ctk.CTkLabel(
            bar, text="", font=ui_fonts.ui_font(_F_SMALL),
            text_color=SC_TEXT_SOFT, anchor='w', justify='left', wraplength=900)

    # ---------------- 人物卡片 ----------------
    def _rebuild_cards(self):
        for child in self.left_column.winfo_children():
            child.destroy()
        for child in self.right_column.winfo_children():
            child.destroy()
        self._summary_labels = {}

        self._her_card(self.left_column)
        self._protagonist_card(self.left_column)
        self._cast_section(self.right_column)
        self._environment_card(self.right_column)
        self._other_voices_card(self.right_column)

    def _card(self, parent, title, subtitle=""):
        # 卡片纵向堆叠，按内容定高；`expand` 会在半屏栏里被拉出空白
        card = ctk.CTkFrame(parent, fg_color=SC_PANEL_BG, corner_radius=12,
                            border_width=1, border_color=SC_BORDER)
        card.pack(fill='x', pady=5)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill='x', padx=12, pady=(10, 0))
        ctk.CTkLabel(head, text=title, font=ui_fonts.ui_font(_F_TITLE, "bold"),
                     text_color=SC_TITLE).pack(side='left')
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill='x', padx=12, pady=(2, 10))
        if subtitle:
            ctk.CTkLabel(body, text=subtitle, font=ui_fonts.ui_font(_F_SMALL),
                         text_color=SC_TEXT_SOFT, anchor='w',
                         justify='left', wraplength=self.CARD_WRAP).pack(
                             fill='x', pady=(0, 4))
        return body

    def _field_row(self, parent, label, var, placeholder, pick=None,
                   width_label=64, entry_width=220):
        """「标签 + 输入框 + 可选按钮」一行；返回输入框。"""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill='x', pady=2)
        ctk.CTkLabel(row, text=label, font=ui_fonts.ui_font(_F_BODY),
                     text_color=SC_TITLE, width=width_label,
                     anchor='w').pack(side='left')
        entry = ctk.CTkEntry(row, textvariable=var, width=entry_width, height=28,
                             font=ui_fonts.ui_font(_F_BODY), text_color=SC_TEXT,
                             placeholder_text=placeholder,
                             fg_color=SC_BG, border_color=SC_BORDER_STRONG)
        entry.pack(side='left', padx=(6, 4))
        if pick is not None:
            ctk.CTkButton(row, text="选", width=28, height=28,
                          font=ui_fonts.ui_font(_F_SMALL), fg_color="transparent",
                          border_width=1, border_color=SC_BORDER_STRONG,
                          text_color=SC_LINK, hover_color=SC_HOVER,
                          corner_radius=6, command=pick).pack(side='left')
        return entry

    def _voice_field(self, parent, label, var, placeholder, kind, append=False):
        """音色一栏：输入框 + 选 + 预听（预听按该角色的音色合成）。"""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill='x', pady=2)
        ctk.CTkLabel(row, text=label, font=ui_fonts.ui_font(_F_BODY),
                     text_color=SC_TITLE, width=64,
                     anchor='w').pack(side='left')
        entry = ctk.CTkEntry(row, textvariable=var, height=28,
                             font=ui_fonts.ui_font(_F_BODY), text_color=SC_TEXT,
                             placeholder_text=placeholder,
                             fg_color=SC_BG, border_color=SC_BORDER_STRONG)
        entry.pack(side='left', fill='x', expand=True, padx=(6, 4))
        ctk.CTkButton(row, text="选", width=28, height=28,
                      font=ui_fonts.ui_font(_F_SMALL), fg_color="transparent",
                      border_width=1, border_color=SC_BORDER_STRONG,
                      text_color=SC_LINK, hover_color=SC_HOVER,
                      corner_radius=6,
                      command=lambda v=var, a=append: self._pick_voice(v, a)).pack(side='left')
        if kind:
            ctk.CTkButton(row, text="预听", width=52, height=28,
                          font=ui_fonts.ui_font(_F_SMALL), fg_color="transparent",
                          border_width=1, border_color=SC_BORDER_STRONG,
                          text_color=SC_TEXT_SOFT, hover_color=SC_HOVER,
                          corner_radius=6,
                          command=lambda k=kind: self._preview_voice(k)).pack(
                side='left', padx=(6, 0))
        return entry

    def _text_field(self, parent, label, height=56, value=""):
        """「标签 + 多行输入」一行；返回 ``(行容器, 文本框)``。"""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill='x', pady=2)
        ctk.CTkLabel(row, text=label, font=ui_fonts.ui_font(_F_BODY),
                     text_color=SC_TITLE, width=64, anchor='nw').pack(
            side='left', anchor='n', pady=(4, 0))
        box = ctk.CTkTextbox(row, height=height, wrap='word',
                             font=ui_fonts.ui_font(_F_BODY), text_color=SC_TEXT,
                             fg_color=SC_BG, border_width=1,
                             border_color=SC_BORDER_STRONG)
        box.pack(side='left', fill='x', expand=True, padx=(6, 4))
        box.insert("1.0", value)
        return row, box

    def _bind_text_autosave(self, box):
        """多行框没有 textvariable，用按键 / 失焦 / 粘贴事件触发防抖保存。"""
        box.bind("<KeyRelease>", lambda _e: self._schedule_save())
        box.bind("<FocusOut>", lambda _e: self._schedule_save())
        box.bind("<<Paste>>", lambda _e: self.after(0, self._schedule_save))

    def _her_card(self, parent):
        body = self._card(
            parent, "她", "档案来自本局角色，这里只配音色与演化量。")
        self.voice_her_var = ctk.StringVar(value=DEFAULT_VOICE_HER)
        self._voice_field(body, "她的音色", self.voice_her_var,
                          DEFAULT_VOICE_HER, "her")
        self._attrs_summary(body, "her")

    def _protagonist_card(self, parent):
        body = self._card(
            parent, "主角",
            "称呼用于标注主角台词，音色仅在 Solea / Bulla 生效。")
        self.protagonist_var = ctk.StringVar()
        self.protagonist_entry = self._field_row(
            body, "称呼", self.protagonist_var, "主角", entry_width=180)
        self.voice_protagonist_var = ctk.StringVar(value=DEFAULT_VOICE_PROTAGONIST)
        self._voice_field(body, "主角音色", self.voice_protagonist_var,
                          DEFAULT_VOICE_PROTAGONIST, "protagonist")
        self.protagonist_background_box = self._text_field(
            body, "身份背景", height=58)[1]
        self._bind_text_autosave(self.protagonist_background_box)
        self._attrs_summary(body, "protagonist")

    # ---------------- 人物表（配角） ----------------
    def _cast_section(self, parent):
        """人物表卡片列表：重要角色 / 群像，增删排序都在这里。"""
        section = ctk.CTkFrame(parent, fg_color="transparent")
        section.pack(fill='x')

        head = ctk.CTkFrame(section, fg_color="transparent")
        head.pack(fill='x', pady=(10, 0))
        ctk.CTkLabel(head, text="人物表",
                     font=ui_fonts.ui_font(_F_TITLE, "bold"),
                     text_color=SC_TITLE).pack(side='left')
        self._cast_count_label = ctk.CTkLabel(
            head, text="", font=ui_fonts.ui_font(_F_SMALL),
            text_color=SC_TEXT_SOFT)
        self._cast_count_label.pack(side='left', padx=(8, 0))
        ctk.CTkButton(
            head, text="添加角色", width=84, height=28,
            font=ui_fonts.ui_font(_F_SMALL), fg_color="transparent",
            border_width=1, border_color=SC_BORDER_STRONG,
            text_color=SC_LINK, hover_color=SC_HOVER, corner_radius=6,
            command=self._add_cast_member).pack(side='right')
        ctk.CTkLabel(section, text="首次出场时注入设定，之后只保留名字。",
                     font=ui_fonts.ui_font(_F_SMALL), text_color=SC_TEXT_SOFT,
                     anchor='w').pack(fill='x', pady=(2, 4))

        self.cast_list = ctk.CTkFrame(section, fg_color="transparent")
        self.cast_list.pack(fill='x')
        self._rebuild_cast_cards()

    def _mini_button(self, parent, text, command, width=52, color=SC_TEXT_SOFT):
        """卡片内的次要按钮（描边透明底）。"""
        return ctk.CTkButton(
            parent, text=text, width=width, height=28,
            font=ui_fonts.ui_font(_F_SMALL), fg_color="transparent",
            border_width=1, border_color=SC_BORDER_STRONG,
            text_color=color, hover_color=SC_HOVER, corner_radius=6,
            command=command)

    def _rail_button(self, parent, text, command, *, danger=False, width=44):
        """卡片右缘的动作按钮：上移 / 下移用中性底色，删除用警示底色。"""
        return ctk.CTkButton(
            parent, text=text, width=width, height=26,
            font=ui_fonts.ui_font(_F_SMALL), border_width=0, corner_radius=6,
            fg_color=SC_DELETE_BG if danger else SC_ACTION_BG,
            hover_color=SC_DELETE_HOVER if danger else SC_ACTION_HOVER,
            text_color=SC_DELETE_TEXT if danger else SC_TEXT_SOFT,
            command=command)

    def _rebuild_cast_cards(self, members=None):
        """按成员数据重建整张人物表；成员为 ``None`` 时取编辑器里的当前值。"""
        host = getattr(self, "cast_list", None)
        if host is None:
            return
        for child in host.winfo_children():
            child.destroy()
        self._cast_cards = []
        if members is None:
            members = list(getattr(self.scenario_editor, "cast", None) or [])
        members = [m for m in normalize_cast(members) if m.get("name")]
        if not members:
            ctk.CTkLabel(
                host, text="（还没有配角；点右上「添加角色」）",
                font=ui_fonts.ui_font(_F_SMALL), text_color=SC_TEXT_SOFT,
                anchor='w').pack(fill='x', padx=6, pady=(0, 4))
        else:
            for member in members:
                self._cast_card(host, member)
        self._update_cast_count()

    def _update_cast_count(self):
        label = getattr(self, "_cast_count_label", None)
        if label is None:
            return
        key_count = sum(1 for state in self._cast_cards
                        if state["role"].get() == ROLE_KEY_LABEL)
        extra_count = len(self._cast_cards) - key_count
        parts = []
        if key_count:
            parts.append(f"重要角色 {key_count}")
        if extra_count:
            parts.append(f"群像 {extra_count}")
        try:
            label.configure(text=" · ".join(parts))
        except Exception:
            pass

    def _cast_card(self, parent, member):
        card = ctk.CTkFrame(parent, fg_color=SC_PANEL_BG, corner_radius=10,
                            border_width=1, border_color=SC_BORDER)
        card.pack(fill='x', pady=4)
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(0, weight=1)
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.grid(row=0, column=0, sticky='nsew', padx=(12, 6), pady=8)
        # 右缘动作区：上移 / 下移 / 删除（删除贴底，避免误触）
        rail = ctk.CTkFrame(card, fg_color="transparent")
        rail.grid(row=0, column=1, sticky='ns', padx=(0, 6), pady=8)

        state = {"original": dict(member), "rows": {}}
        state["role"] = ctk.StringVar(
            value=ROLE_EXTRA_LABEL if member.get("role") == "extra"
            else ROLE_KEY_LABEL)
        state["name"] = ctk.StringVar(value=member.get("name", ""))
        state["aliases"] = ctk.StringVar(
            value="、".join(member.get("aliases") or ()))
        state["pronoun"] = ctk.StringVar(
            value=member.get("pronoun") or PRONOUN_UNSET_LABEL)
        state["call_her"] = ctk.StringVar(value=member.get("call_her", ""))
        state["voice"] = ctk.StringVar(value=member.get("voice", ""))
        state["detail_open"] = False

        rows = state["rows"]

        # ---- 第一行：定位 / 称呼（详细按钮由布局按定位显示） ----
        head = ctk.CTkFrame(body, fg_color="transparent")
        rows["head"] = head
        OptionButton(
            head, values=list(ROLE_LABELS), variable=state["role"],
            width=86, height=28, font=ui_fonts.ui_font(_F_SMALL),
            fg_color=SC_BG, border_color=SC_BORDER_STRONG,
            hover_color=SC_HOVER, text_color=SC_TITLE,
            popup_fg_color=SC_PANEL_BG, popup_hover_color=SC_HOVER,
            popup_text_color=SC_TITLE, popup_border_color=SC_BORDER_STRONG,
            command=lambda s=state: self._cast_role_changed(s)).pack(
                side='left', padx=(0, 8))
        ctk.CTkLabel(head, text="称呼", font=ui_fonts.ui_font(_F_BODY),
                     text_color=SC_TITLE).pack(side='left')
        ctk.CTkEntry(head, textvariable=state["name"], width=110, height=28,
                     font=ui_fonts.ui_font(_F_BODY), text_color=SC_TEXT, fg_color=SC_BG,
                     border_color=SC_BORDER_STRONG).pack(
                         side='left', fill='x', expand=True, padx=(6, 4))
        state["detail_button"] = self._mini_button(
            head, "详细 ▸", lambda s=state: self._toggle_cast_detail(s), width=66)

        # ---- 右缘动作区 ----
        self._rail_button(
            rail, "↑", lambda s=state: self._move_cast_member(s, -1)).pack(
                fill='x', pady=(0, 4))
        self._rail_button(
            rail, "↓", lambda s=state: self._move_cast_member(s, 1)).pack(
                fill='x', pady=(0, 4))
        self._rail_button(
            rail, "删除", lambda s=state: self._delete_cast_member(s),
            danger=True).pack(side='bottom', fill='x')

        # ---- 代词 / 别称 / 称她为 ----
        meta = ctk.CTkFrame(body, fg_color="transparent")
        rows["meta"] = meta
        ctk.CTkLabel(meta, text="代词", font=ui_fonts.ui_font(_F_BODY),
                     text_color=SC_TITLE, width=40, anchor='w').pack(side='left')
        OptionButton(
            meta, values=list(PRONOUN_LABELS), variable=state["pronoun"],
            width=74, height=28, font=ui_fonts.ui_font(_F_SMALL),
            fg_color=SC_BG, border_color=SC_BORDER_STRONG,
            hover_color=SC_HOVER, text_color=SC_TITLE,
            popup_fg_color=SC_PANEL_BG, popup_hover_color=SC_HOVER,
            popup_text_color=SC_TITLE, popup_border_color=SC_BORDER_STRONG,
            command=lambda s=state: self._update_cast_count()).pack(
                side='left', padx=(4, 10))
        ctk.CTkLabel(meta, text="别称", font=ui_fonts.ui_font(_F_BODY),
                     text_color=SC_TITLE).pack(side='left')
        ctk.CTkEntry(meta, textvariable=state["aliases"], width=92, height=28,
                     font=ui_fonts.ui_font(_F_BODY), text_color=SC_TEXT, fg_color=SC_BG,
                     border_color=SC_BORDER_STRONG).pack(
                         side='left', fill='x', expand=True, padx=(6, 10))
        ctk.CTkLabel(meta, text="称她为", font=ui_fonts.ui_font(_F_BODY),
                     text_color=SC_TITLE).pack(side='left')
        ctk.CTkEntry(meta, textvariable=state["call_her"], width=92, height=28,
                     font=ui_fonts.ui_font(_F_BODY), text_color=SC_TEXT, fg_color=SC_BG,
                     border_color=SC_BORDER_STRONG).pack(
                         side='left', fill='x', expand=True, padx=(6, 0))

        # ---- 音色 ----
        voice = ctk.CTkFrame(body, fg_color="transparent")
        rows["voice"] = voice
        ctk.CTkLabel(voice, text="音色", font=ui_fonts.ui_font(_F_BODY),
                     text_color=SC_TITLE, width=64, anchor='w').pack(side='left')
        ctk.CTkEntry(voice, textvariable=state["voice"], height=28,
                     font=ui_fonts.ui_font(_F_BODY), text_color=SC_TEXT,
                     placeholder_text="留空 = 按名字从音色池分配",
                     fg_color=SC_BG, border_color=SC_BORDER_STRONG).pack(
                         side='left', fill='x', expand=True, padx=(6, 4))
        self._mini_button(
            voice, "选", lambda v=state["voice"]: self._pick_voice(v),
            width=28, color=SC_LINK).pack(side='left')

        # ---- 身份背景（重要角色） / 作用（群像） ----
        background_row, state["background_box"] = self._text_field(
            body, "身份背景", height=58, value=member.get("background", ""))
        rows["background"] = background_row
        self._bind_text_autosave(state["background_box"])

        purpose_row, state["purpose_box"] = self._text_field(
            body, "作用", height=50, value=member.get("purpose", ""))
        rows["purpose"] = purpose_row
        self._bind_text_autosave(state["purpose_box"])

        speech = ctk.CTkFrame(body, fg_color="transparent")
        rows["speech"] = speech
        ctk.CTkLabel(speech, text="说话方式", font=ui_fonts.ui_font(_F_BODY),
                     text_color=SC_TITLE, width=64, anchor='w').pack(side='left')
        state["speech"] = ctk.StringVar(value=member.get("speech", ""))
        ctk.CTkEntry(speech, textvariable=state["speech"], height=28,
                     font=ui_fonts.ui_font(_F_BODY), text_color=SC_TEXT, fg_color=SC_BG,
                     border_color=SC_BORDER_STRONG).pack(
                         side='left', fill='x', expand=True, padx=(6, 4))

        for var in (state["role"], state["name"], state["aliases"],
                    state["pronoun"], state["call_her"], state["voice"],
                    state["speech"]):
            var.trace_add("write", lambda *_a: self._schedule_save())

        self._cast_cards.append(state)
        self._layout_cast_card(state)

    def _layout_cast_card(self, state):
        """按定位重排一张人物卡：群像折叠作用 / 说话方式，重要角色展开。"""
        rows = state["rows"]
        for row in rows.values():
            row.pack_forget()
        is_key = state["role"].get() == ROLE_KEY_LABEL

        rows["head"].pack(fill='x', pady=(0, 2))
        rows["meta"].pack(fill='x', pady=2)
        rows["voice"].pack(fill='x', pady=2)
        if is_key:
            state["detail_button"].pack_forget()
            rows["background"].pack(fill='x', pady=2)
            rows["speech"].pack(fill='x', pady=2)
        else:
            state["detail_button"].pack(side='right', padx=(6, 0))
            if state["detail_open"]:
                rows["purpose"].pack(fill='x', pady=2)
                rows["speech"].pack(fill='x', pady=2)
            state["detail_button"].configure(
                text="详细 ▾" if state["detail_open"] else "详细 ▸")

    def _toggle_cast_detail(self, state):
        state["detail_open"] = not state["detail_open"]
        self._layout_cast_card(state)

    def _cast_role_changed(self, state):
        """切换定位后整张表重建：群像与重要角色显示不同的字段。"""
        self._cast_changed()

    def _cast_changed(self):
        self._rebuild_cast_cards(self.collect_cast())
        self._schedule_save()

    def _add_cast_member(self):
        members = self.collect_cast()
        members.append({"name": self._next_cast_name(members),
                        "role": DEFAULT_CAST_ROLE})
        self._rebuild_cast_cards(members)
        self._schedule_save()

    @staticmethod
    def _next_cast_name(members) -> str:
        names = {m.get("name") for m in members}
        index = 1
        while f"角色{index}" in names:
            index += 1
        return f"角色{index}"

    def _move_cast_member(self, state, delta: int):
        members = self.collect_cast()
        index = self._cast_state_index(state)
        target = index + delta
        if index < 0 or not (0 <= target < len(members)):
            return
        members[index], members[target] = members[target], members[index]
        self._rebuild_cast_cards(members)
        self._schedule_save()

    def _delete_cast_member(self, state):
        members = self.collect_cast()
        index = self._cast_state_index(state)
        if index < 0:
            return
        del members[index]
        self._rebuild_cast_cards(members)
        self._schedule_save()

    def _cast_state_index(self, state) -> int:
        try:
            return self._cast_cards.index(state)
        except ValueError:
            return -1

    def collect_cast(self) -> list:
        """从卡片收集人物表（保留作者手写的未知字段）。"""
        raw = []
        for state in self._cast_cards:
            name = state["name"].get().strip()
            if not name:
                continue
            member = dict(state["original"])
            member["role"] = ROLE_VALUES.get(
                state["role"].get(), DEFAULT_CAST_ROLE)
            member["name"] = name
            # id 缺省或只是旧的自动值（等于旧称呼）时跟着称呼走
            old_name = str(state["original"].get("name") or "").strip()
            old_id = str(state["original"].get("id") or "").strip()
            if not old_id or old_id == old_name:
                member["id"] = name
            member["aliases"] = [a for a in _ALIAS_SEPARATORS.split(
                state["aliases"].get().strip()) if a]
            member["pronoun"] = PRONOUN_VALUES.get(
                state["pronoun"].get(), "")
            member["call_her"] = state["call_her"].get().strip()
            member["voice"] = state["voice"].get().strip()
            member["background"] = self._box_text(state["background_box"])
            member["purpose"] = self._box_text(state["purpose_box"])
            member["speech"] = state["speech"].get().strip()
            raw.append(member)
        return normalize_cast(raw)

    @staticmethod
    def _box_text(box) -> str:
        try:
            return box.get("1.0", "end-1c").strip()
        except Exception:
            return ""

    def _environment_card(self, parent):
        body = self._card(parent, "环境", "世界层面的演化量。")
        self._attrs_summary(body, "environment")

    def _other_voices_card(self, parent):
        body = self._card(
            parent, "其他角色",
            "不在人物表里的配角按名字从音色池分配音色。")
        self.voice_other_var = ctk.StringVar(
            value="、".join(DEFAULT_OTHER_VOICES))
        self._voice_field(body, "音色池", self.voice_other_var,
                          "、".join(DEFAULT_OTHER_VOICES), None, append=True)
        self._attrs_summary(body, "other")

    def _attrs_summary(self, parent, owner):
        """卡片底部：归属该角色的演化量摘要 + 跳到演化量视图。"""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill='x', pady=(8, 0))
        # 半屏栏里先放右侧按钮，摘要文字再吃剩余宽度，按钮不会被挤掉
        ctk.CTkButton(row, text="管理", width=60, height=26,
                      font=ui_fonts.ui_font(_F_SMALL), fg_color="transparent",
                      border_width=1, border_color=SC_BORDER_STRONG,
                      text_color=SC_LINK, hover_color=SC_HOVER, corner_radius=6,
                      command=lambda o=owner: self.show_owner_attrs(o)).pack(
                          side='right', padx=(6, 0))
        ctk.CTkLabel(row, text="演化量",
                     font=ui_fonts.ui_font(_F_BODY, "bold"),
                     text_color=SC_TITLE).pack(side='left')
        label = ctk.CTkLabel(row, text=self._summary_text(owner),
                             font=ui_fonts.ui_font(_F_SMALL),
                             text_color=SC_TEXT_SOFT,
                             anchor='w', justify='left', wraplength=320)
        label.pack(side='left', padx=(8, 0))
        self._summary_labels[owner] = label

    def _summary_text(self, owner) -> str:
        attrs = [a for a in self._attr_list()
                 if owner_label(a.get("owner"), a.get("type")) ==
                 EVOLUTION_OWNER_LABELS[owner]]
        if not attrs:
            return "（无）"
        parts = []
        for attr in attrs[:self.SUMMARY_LIMIT]:
            name = attr.get("name") or attr.get("type") or "未命名"
            parts.append(f"{name}·{state_label(attr.get('display_state'))}")
        text = "  ".join(parts)
        if len(attrs) > self.SUMMARY_LIMIT:
            text += f"  等 {len(attrs)} 项"
        return text

    def _attr_list(self) -> list:
        return list(getattr(self.scenario_editor, "evolution_attrs", None) or [])

    # ---------------- 视图切换 ----------------
    def _on_view_switch(self, value):
        view = str(value or "").strip()
        self.characters_view.pack_forget()
        self.attrs_view.pack_forget()
        if view == VIEW_ATTRS:
            self.evolution_panel.refresh_list()
            self.attrs_view.pack(fill='both', expand=True)
        else:
            self._refresh_summaries()
            self.characters_view.pack(fill='both', expand=True)

    def show_owner_attrs(self, owner):
        """跳到演化量视图并只看某个归属（角色卡片的「管理」）。"""
        self.view_switch.set(VIEW_ATTRS)
        self._on_view_switch(VIEW_ATTRS)
        self.evolution_panel.set_owner_filter(owner)

    def _refresh_summaries(self):
        """只改摘要文字：卡片（与正在编辑的音色输入）保持不动。"""
        for owner, label in list(self._summary_labels.items()):
            try:
                label.configure(text=self._summary_text(owner))
            except Exception:
                pass

    # ---------------- 与编辑器同步 ----------------
    def refresh_from_config(self):
        """方案切换后回填：主角称呼与背景、人物表、语音、演化量摘要与列表。"""
        self._loading = True
        try:
            self.protagonist_var.set(
                str(getattr(self.scenario_editor, "protagonist_title", "") or ""))
            self._set_box_text(
                getattr(self, "protagonist_background_box", None),
                getattr(self.scenario_editor, "protagonist_background", "") or "")
            self._rebuild_cast_cards(
                list(getattr(self.scenario_editor, "cast", None) or []))
            voice = normalize_voice(getattr(self.scenario_editor, "voice", None))
            self._voice = voice
            self.voice_enabled_var.set(bool(voice.get("enabled", True)))
            self.voice_her_var.set(voice.get("voice_her", DEFAULT_VOICE_HER))
            self.voice_protagonist_var.set(
                voice.get("voice_protagonist", DEFAULT_VOICE_PROTAGONIST))
            self.voice_other_var.set("、".join(voice.get("voice_other") or ()))
            self.voice_rate_var.set(voice.get("rate", DEFAULT_RATE))
            self.voice_level_var.set(buses.step_display(voice_level(voice)))
        finally:
            self._loading = False
        self._refresh_summaries()
        self.evolution_panel.refresh_list()
        self._set_save_hint("自动保存")

    @staticmethod
    def _set_box_text(box, text: str):
        if box is None:
            return
        try:
            box.delete("1.0", "end")
            box.insert("1.0", str(text or ""))
        except Exception:
            pass

    # ---------------- 自动保存 ----------------
    #: 输入类修改的防抖落盘间隔（毫秒）
    _SAVE_DEBOUNCE_MS = 700

    def _attach_autosave(self):
        for var in (self.protagonist_var, self.voice_enabled_var,
                    self.voice_her_var, self.voice_protagonist_var,
                    self.voice_other_var, self.voice_rate_var,
                    self.voice_level_var):
            var.trace_add("write", lambda *_: self._schedule_save())

    def _schedule_save(self):
        if self._loading:
            return
        self._cancel_pending_save()
        self._save_job = self.after(self._SAVE_DEBOUNCE_MS, self._flush_save)

    def _cancel_pending_save(self):
        if self._save_job is not None:
            try:
                self.after_cancel(self._save_job)
            except Exception:
                pass
            self._save_job = None

    def _flush_save(self):
        """把主角与人物表、语音写回方案配置（静默），并同步编辑器状态与快照。"""
        self._cancel_pending_save()
        editor = self.scenario_editor
        if self._loading or not getattr(editor, "current_scenario_id", None):
            return
        if not self.winfo_exists():
            return
        voice = self.collect_voice()
        title = self.protagonist_title()
        background = self.protagonist_background()
        cast = self.collect_cast()
        config = editor._scenario_repo.load_config(editor.current_scenario_id)
        if config is None:
            config = {}
        config["protagonist_title"] = title
        # 旧方案没有这两个键时，空值不写回（读取时空缺本来就等价于空）
        if background or "protagonist_background" in config:
            config["protagonist_background"] = background
        if cast or "cast" in config:
            config["cast"] = cast
        config["voice"] = voice
        try:
            editor._scenario_repo.save_config(editor.current_scenario_id, config)
        except Exception as exc:
            self._set_save_hint(f"保存失败：{exc}")
            return
        self._voice = voice
        editor.protagonist_title = title
        editor.protagonist_background = background
        editor.cast = cast
        editor.voice = voice
        # 只把这几项写回通用页的已保存快照：整份重拍会把「通用」页里尚未保存的
        # 编辑当成已保存，切方案时就再也不会提示了
        snapshot = getattr(editor, "_saved_prompt_snapshot", None)
        if isinstance(snapshot, dict) and snapshot:
            snapshot["protagonist_title"] = title
            snapshot["voice"] = dict(voice)
        self._update_cast_count()
        self._set_save_hint("已保存")

    def _set_save_hint(self, text):
        label = getattr(self, "_save_hint", None)
        if label is None:
            return
        try:
            label.configure(text=text)
        except Exception:
            pass

    def on_coupling_changed(self, level):
        """耦合等级变化：Velum 没有说话人标记，把主角称呼与语音提示置灰。"""
        self._coupling_level = level
        self._apply_voice_state()

    def _apply_voice_state(self):
        level = getattr(self, "_coupling_level", None)
        if level is None:
            level = getattr(self.scenario_editor, "coupling_level", None)
        state = "disabled" if level == VELUM else "normal"
        entry = getattr(self, "protagonist_entry", None)
        if entry is not None:
            try:
                entry.configure(state=state)
            except Exception:
                pass
        self._refresh_voice_hint()

    def _refresh_voice_hint(self):
        """音色区提示：缺依赖 / 等级不适用 / 正常联网合成。"""
        label = getattr(self, "voice_hint_label", None)
        if label is None:
            return
        level = getattr(self, "_coupling_level", None)
        if level is None:
            level = getattr(self.scenario_editor, "coupling_level", None)
        if not edge_tts_available():
            text = "未安装 edge-tts，运行时不会出声"
        elif level == VELUM:
            text = "当前等级不朗读对话"
        else:
            text = "需联网合成，首次约 1 秒"
        label.configure(text=text)

    # ---------------- 收集 ----------------
    def protagonist_title(self) -> str:
        return self.protagonist_var.get().strip()

    def protagonist_background(self) -> str:
        return self._box_text(getattr(self, "protagonist_background_box", None))

    def flush_pending(self):
        """立即落盘挂起的防抖保存（切方案 / 保存通用页前调用）。"""
        if self._save_job is not None:
            self._flush_save()

    def collect_voice(self) -> dict:
        """收集对话语音配置（音色池按「、」或逗号分隔，空则回退默认池）。"""
        raw_others = re.split(r"[、,，;；\s]+", self.voice_other_var.get().strip())
        return normalize_voice({
            "enabled": bool(self.voice_enabled_var.get()),
            "voice_her": self.voice_her_var.get().strip(),
            "voice_protagonist": self.voice_protagonist_var.get().strip(),
            "voice_other": [v for v in raw_others if v],
            "rate": self.voice_rate_var.get().strip(),
            "level": buses.step_from_display(self.voice_level_var.get()),
        })

    # ---------------- 音色选择与预听 ----------------
    def _pick_voice(self, var, append: bool = False):
        """打开音色选择器；``append`` 为真时把结果追加进已有的音色池。"""
        current = var.get().strip()
        dialog = VoicePickDialog(self, current=current)
        picked = dialog.result
        if not picked:
            return
        if append and current:
            items = [item for item in re.split(r"[、,，;；\s]+", current) if item]
            if picked not in items:
                items.append(picked)
            var.set("、".join(items))
        else:
            var.set(picked)

    def _preview_voice(self, kind: str = "her"):
        """预听方案级语音（不带章节效果）：听音色 + 语速 + 档位。"""
        voice = self.collect_voice()
        if kind == "protagonist":
            voice_id = voice.get("voice_protagonist", "")
        elif kind == "other":
            pool = voice.get("voice_other") or ()
            voice_id = pool[0] if pool else ""
        else:
            voice_id = voice["voice_her"]
        preview = self._ensure_preview()
        if preview is None:
            return
        button = getattr(self, "_preview_button", None)
        if button is not None:
            button.configure(state="disabled", text="…")
        self._set_preview_status("正在合成预听样例…（首次约 1 秒，需联网）")
        preview.play(voice_id, rate=voice.get("rate", DEFAULT_RATE),
                     level=voice.get("level"), effect=None,
                     on_done=self._on_preview_done)

    def _ensure_preview(self):
        if self._preview is None:
            try:
                self._preview = VoicePreview(logger=self._preview_log.append)
            except Exception as exc:
                self._set_preview_status(f"初始化预听失败：{exc}")
                return None
        return self._preview

    def _set_preview_status(self, text: str):
        label = getattr(self, "_preview_status", None)
        if label is None:
            return
        try:
            if text:
                if label.winfo_manager() != "pack":
                    label.pack(fill='x', padx=12, pady=(0, 6))
                label.configure(text=text)
            else:
                label.configure(text="")
                label.pack_forget()
        except Exception:
            pass

    def _on_preview_done(self, ok, message):
        """工作线程的回调 → 回 Tk 主线程改按钮与状态（控件可能已销毁）。"""
        try:
            self.after(0, lambda: self._finish_preview(ok, message))
        except Exception:
            pass

    def _finish_preview(self, ok, message):
        button = getattr(self, "_preview_button", None)
        if button is not None:
            try:
                button.configure(state="normal", text="预听")
            except Exception:
                pass
        self._set_preview_status("" if ok else f"预听失败：{message}")

    # ---------------- 生命周期 ----------------
    def shutdown(self):
        """收尾：停掉挂起的自动保存与预听（停播、收线程、清临时 WAV）。"""
        self._cancel_pending_save()
        preview, self._preview = self._preview, None
        if preview is not None:
            try:
                preview.shutdown()
            except Exception:
                pass

    #: 兼容旧调用名
    shutdown_preview = shutdown


__all__ = ["CharacterPage", "VoicePickDialog"]
