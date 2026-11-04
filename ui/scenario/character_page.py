"""角色页：方案里「有谁」以及每个人的音色与演化量。

三种角色来自不同地方，页面上按这个分工组织：

- **她**：本局角色档案（名字 / 性格 / 体型）不在方案里，这里只配她的音色与
  归属她的演化量；
- **主角**：只有「称呼」一项（Solea / Bulla 的对话行用它做说话人标注）；
- **其他角色**：不单独建模，用一把共享音色池按名字稳定分配；
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
from dungeon.coupling import VELUM
from dungeon.schema import EVOLUTION_OWNER_LABELS, owner_label
from ui.ctk.dialogs import BaseDialog, DLG_BORDER_STRONG, DLG_PANEL_BG, DLG_TEXT, DLG_TEXT_SOFT
from ui.ctk.theme import (SC_BG, SC_BORDER, SC_BORDER_STRONG, SC_HOVER,
                          SC_LINK, SC_PANEL_BG, SC_TEXT, SC_TEXT_SOFT,
                          SC_TITLE)
from ui.ctk.widgets import OptionButton
from ui.ctk import fonts as ui_fonts
from ui.scenario.evolution_attributes import EvolutionAtrrManager, state_label

VIEW_CHARACTERS = "人物"
VIEW_ATTRS = "演化量"


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

    def __init__(self, parent, scenario_repo, scenario_editor_ref):
        super().__init__(parent, fg_color="transparent")
        self.scenario_editor = scenario_editor_ref
        self._repo = scenario_repo
        self._preview = None
        self._preview_log = []
        self._coupling_level = None
        self._summary_labels = {}
        self._save_job = None      # 主角称呼 / 语音的防抖保存
        self._loading = False      # 回填期间抑制自动保存

        # 语音：未配置时按默认值走（与方案配置同构，保存时整体写回）
        self._voice = normalize_voice(None)

        self._build_ui()
        self._attach_autosave()

    # ---------------- UI ----------------
    def _build_ui(self):
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(fill='x', padx=10, pady=6)
        self.view_switch = ctk.CTkSegmentedButton(
            toolbar, values=[VIEW_CHARACTERS, VIEW_ATTRS],
            font=ui_fonts.ui_font(13), height=28,
            command=self._on_view_switch)
        self.view_switch.pack(side='left', padx=5)
        self.view_switch.set(VIEW_CHARACTERS)
        self._view_hint = ctk.CTkLabel(
            toolbar, text="", font=ui_fonts.ui_font(11),
            text_color=SC_TEXT_SOFT, anchor='w')
        self._view_hint.pack(side='left', padx=10)
        self._save_hint = ctk.CTkLabel(
            toolbar, text="主角称呼与语音修改后自动保存",
            font=ui_fonts.ui_font(11), text_color=SC_TEXT_SOFT)
        self._save_hint.pack(side='right', padx=6)

        self.view_host = ctk.CTkFrame(self, fg_color="transparent")
        self.view_host.pack(fill='both', expand=True, padx=10, pady=(0, 10))

        # ---- 人物视图 ----
        self.characters_view = ctk.CTkFrame(self.view_host, fg_color="transparent")
        self._build_voice_bar(self.characters_view)
        self.cards = ctk.CTkScrollableFrame(self.characters_view,
                                            fg_color="transparent")
        self.cards.pack(fill='both', expand=True, pady=(6, 0))

        # ---- 演化量视图（同一批数据的平铺表） ----
        self.attrs_view = ctk.CTkFrame(self.view_host, fg_color="transparent")
        self.evolution_panel = EvolutionAtrrManager(
            self.attrs_view, self._repo, self.scenario_editor)
        self.evolution_panel.pack(fill='both', expand=True)

        self.characters_view.pack(fill='both', expand=True)
        self._set_view_hint(VIEW_CHARACTERS)
        self._rebuild_cards()
        self._apply_voice_state()

    def _build_voice_bar(self, parent):
        """方案级语音条：开关 / 语速 / 音量 / 预听。"""
        bar = ctk.CTkFrame(parent, fg_color=SC_PANEL_BG, corner_radius=10)
        bar.pack(fill='x')

        head = ctk.CTkFrame(bar, fg_color="transparent")
        head.pack(fill='x', padx=12, pady=(8, 2))
        ctk.CTkLabel(head, text="朗读对话", font=ui_fonts.ui_font(13, "bold"),
                     text_color=SC_TITLE).pack(side='left')
        self.voice_enabled_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(head, text="", variable=self.voice_enabled_var,
                      width=44, height=22, progress_color=SC_LINK,
                      button_color=SC_TEXT_SOFT,
                      button_hover_color=SC_TEXT_SOFT).pack(side='left', padx=(8, 12))

        ctk.CTkLabel(head, text="语速", font=ui_fonts.ui_font(12),
                     text_color=SC_TEXT_SOFT).pack(side='left')
        self.voice_rate_var = ctk.StringVar(value=DEFAULT_RATE)
        ctk.CTkEntry(head, textvariable=self.voice_rate_var, width=58,
                     height=26, font=ui_fonts.ui_font(12),
                     text_color=SC_TEXT, fg_color=SC_BG,
                     border_color=SC_BORDER_STRONG).pack(side='left', padx=(6, 10))

        ctk.CTkLabel(head, text="音量", font=ui_fonts.ui_font(12),
                     text_color=SC_TEXT_SOFT).pack(side='left')
        self.voice_level_var = ctk.StringVar(
            value=buses.step_display(DEFAULT_VOICE_LEVEL))
        OptionButton(head, values=[label for label, _ in buses.step_choices()],
                     variable=self.voice_level_var, width=88, height=26,
                     font=ui_fonts.ui_font(12),
                     fg_color=SC_BG, border_color=SC_BORDER_STRONG,
                     hover_color=SC_HOVER, text_color=SC_TITLE,
                     popup_fg_color=SC_PANEL_BG, popup_hover_color=SC_HOVER,
                     popup_text_color=SC_TITLE,
                     popup_border_color=SC_BORDER_STRONG,
                     ).pack(side='left', padx=(6, 10))

        self._preview_button = ctk.CTkButton(
            head, text="预听", width=52, height=26,
            font=ui_fonts.ui_font(12), fg_color="transparent",
            border_width=1, border_color=SC_BORDER_STRONG, text_color=SC_LINK,
            hover_color=SC_HOVER, corner_radius=6,
            command=lambda: self._preview_voice("her"))
        self._preview_button.pack(side='left', padx=(0, 6))
        ctk.CTkButton(
            head, text="其它音色", width=76, height=26,
            font=ui_fonts.ui_font(12), fg_color="transparent",
            border_width=1, border_color=SC_BORDER_STRONG, text_color=SC_TEXT_SOFT,
            hover_color=SC_HOVER, corner_radius=6,
            command=lambda: self._preview_voice("other")).pack(side='left')

        self.voice_hint_label = ctk.CTkLabel(
            bar, text="", font=ui_fonts.ui_font(11), text_color=SC_TEXT_SOFT,
            anchor='w', justify='left', wraplength=900)
        self.voice_hint_label.pack(fill='x', padx=12, pady=(0, 8))
        # 预听状态按需显示：没有状态时不占一行高度
        self._preview_status = ctk.CTkLabel(
            bar, text="", font=ui_fonts.ui_font(11), text_color=SC_TEXT_SOFT,
            anchor='w', justify='left', wraplength=900)

    # ---------------- 人物卡片 ----------------
    def _rebuild_cards(self):
        for child in self.cards.winfo_children():
            child.destroy()
        self._summary_labels = {}

        self._her_card(self.cards)
        self._protagonist_card(self.cards)
        # 环境与其他角色各占半栏：不发声的那两个并排，首屏就装得下
        row = ctk.CTkFrame(self.cards, fg_color="transparent")
        row.pack(fill='x')
        row.grid_columnconfigure(0, weight=1, uniform='half')
        row.grid_columnconfigure(1, weight=1, uniform='half')
        left = ctk.CTkFrame(row, fg_color="transparent")
        left.grid(row=0, column=0, sticky='nsew', padx=(0, 5))
        right = ctk.CTkFrame(row, fg_color="transparent")
        right.grid(row=0, column=1, sticky='nsew', padx=(5, 0))
        self._environment_card(left)
        self._other_voices_card(right)

    def _card(self, parent, title, subtitle):
        card = ctk.CTkFrame(parent, fg_color=SC_PANEL_BG, corner_radius=12,
                            border_width=1, border_color=SC_BORDER)
        card.pack(fill='both', expand=True, pady=5)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill='x', padx=12, pady=(10, 0))
        ctk.CTkLabel(head, text=title, font=ui_fonts.ui_font(14, "bold"),
                     text_color=SC_TITLE).pack(side='left')
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill='x', padx=12, pady=(2, 10))
        ctk.CTkLabel(body, text=subtitle, font=ui_fonts.ui_font(11),
                     text_color=SC_TEXT_SOFT, anchor='w',
                     justify='left', wraplength=980).pack(fill='x', pady=(0, 4))
        return body

    def _field_row(self, parent, label, var, placeholder, pick=None,
                   width_label=64, entry_width=220):
        """「标签 + 输入框 + 可选按钮」一行；返回输入框。"""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill='x', pady=2)
        ctk.CTkLabel(row, text=label, font=ui_fonts.ui_font(12),
                     text_color=SC_TITLE, width=width_label,
                     anchor='w').pack(side='left')
        entry = ctk.CTkEntry(row, textvariable=var, width=entry_width, height=26,
                             font=ui_fonts.ui_font(12), text_color=SC_TEXT,
                             placeholder_text=placeholder,
                             fg_color=SC_BG, border_color=SC_BORDER_STRONG)
        entry.pack(side='left', padx=(6, 4))
        if pick is not None:
            ctk.CTkButton(row, text="选", width=28, height=26,
                          font=ui_fonts.ui_font(12), fg_color="transparent",
                          border_width=1, border_color=SC_BORDER_STRONG,
                          text_color=SC_LINK, hover_color=SC_HOVER,
                          corner_radius=6, command=pick).pack(side='left')
        return entry

    def _voice_field(self, parent, label, var, placeholder, kind, append=False):
        """音色一栏：输入框 + 选 + 预听（预听按该角色的音色合成）。"""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill='x', pady=2)
        ctk.CTkLabel(row, text=label, font=ui_fonts.ui_font(12),
                     text_color=SC_TITLE, width=64,
                     anchor='w').pack(side='left')
        entry = ctk.CTkEntry(row, textvariable=var, height=26,
                             font=ui_fonts.ui_font(12), text_color=SC_TEXT,
                             placeholder_text=placeholder,
                             fg_color=SC_BG, border_color=SC_BORDER_STRONG)
        entry.pack(side='left', fill='x', expand=True, padx=(6, 4))
        ctk.CTkButton(row, text="选", width=28, height=26,
                      font=ui_fonts.ui_font(12), fg_color="transparent",
                      border_width=1, border_color=SC_BORDER_STRONG,
                      text_color=SC_LINK, hover_color=SC_HOVER,
                      corner_radius=6,
                      command=lambda v=var, a=append: self._pick_voice(v, a)).pack(side='left')
        if kind:
            ctk.CTkButton(row, text="预听", width=52, height=26,
                          font=ui_fonts.ui_font(12), fg_color="transparent",
                          border_width=1, border_color=SC_BORDER_STRONG,
                          text_color=SC_TEXT_SOFT, hover_color=SC_HOVER,
                          corner_radius=6,
                          command=lambda k=kind: self._preview_voice(k)).pack(
                side='left', padx=(6, 0))
        return entry

    def _her_card(self, parent):
        body = self._card(
            parent, "她",
            "名字、性格与体型来自本局角色档案，不在方案里；这里只配她的音色，"
            "以及归属她的演化量。")
        self.voice_her_var = ctk.StringVar(value=DEFAULT_VOICE_HER)
        self._voice_field(body, "她的音色", self.voice_her_var,
                          DEFAULT_VOICE_HER, "her")
        self._attrs_summary(body, "her")

    def _protagonist_card(self, parent):
        body = self._card(
            parent, "主角",
            "「称呼」是主角台词的说话人标注（指挥官、长官……），留空显示为「主角」；"
            "主角的音色只在 Solea / Bulla 生效。")
        self.protagonist_var = ctk.StringVar()
        self.protagonist_entry = self._field_row(
            body, "称呼", self.protagonist_var, "主角", entry_width=180)
        self.voice_protagonist_var = ctk.StringVar(value=DEFAULT_VOICE_PROTAGONIST)
        self._voice_field(body, "主角音色", self.voice_protagonist_var,
                          DEFAULT_VOICE_PROTAGONIST, "protagonist")
        self._attrs_summary(body, "protagonist")

    def _environment_card(self, parent):
        body = self._card(
            parent, "环境",
            "世界层面的量（总伤亡、灾害、见证者人数……）归在这里；环境不发声，"
            "所以没有音色。")
        self._attrs_summary(body, "environment")

    def _other_voices_card(self, parent):
        body = self._card(
            parent, "其他角色",
            "没有单独设定的配角：按名字从下面的音色池里稳定分配一把嗓子，"
            "「其他角色」归属的量也记在这里。")
        self.voice_other_var = ctk.StringVar(
            value="、".join(DEFAULT_OTHER_VOICES))
        self._voice_field(body, "音色池", self.voice_other_var,
                          "、".join(DEFAULT_OTHER_VOICES), None, append=True)
        self._attrs_summary(body, "other")

    def _attrs_summary(self, parent, owner):
        """卡片底部：归属该角色的演化量摘要 + 跳到演化量视图。"""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill='x', pady=(8, 0))
        ctk.CTkLabel(row, text="演化量", font=ui_fonts.ui_font(12, "bold"),
                     text_color=SC_TITLE).pack(side='left')
        label = ctk.CTkLabel(row, text=self._summary_text(owner),
                             font=ui_fonts.ui_font(12), text_color=SC_TEXT_SOFT,
                             anchor='w', justify='left')
        label.pack(side='left', padx=(8, 6))
        self._summary_labels[owner] = label
        ctk.CTkButton(row, text="管理", width=60, height=24,
                      font=ui_fonts.ui_font(12), fg_color="transparent",
                      border_width=1, border_color=SC_BORDER_STRONG,
                      text_color=SC_LINK, hover_color=SC_HOVER, corner_radius=6,
                      command=lambda o=owner: self.show_owner_attrs(o)).pack(side='right')

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
        self._set_view_hint(view)

    def _set_view_hint(self, view):
        if view == VIEW_ATTRS:
            text = ("同一批演化量按归属平铺；归属只决定展示在谁名下，"
                    "数值仍由介入度 / 破坏性 / 总伤亡与自定义属性各自的规则演化。")
        else:
            text = "这里的归属同时决定提示词怎么解释每项量的来处。"
        self._view_hint.configure(text=text)

    def show_owner_attrs(self, owner):
        """跳到演化量视图并只看某个归属（角色卡片的「管理」）。"""
        self.view_switch.set(VIEW_ATTRS)
        self.attrs_view.pack_forget()
        self.characters_view.pack_forget()
        self.attrs_view.pack(fill='both', expand=True)
        self.evolution_panel.set_owner_filter(owner)
        self._set_view_hint(VIEW_ATTRS)

    def _refresh_summaries(self):
        """只改摘要文字：卡片（与正在编辑的音色输入）保持不动。"""
        for owner, label in list(self._summary_labels.items()):
            try:
                label.configure(text=self._summary_text(owner))
            except Exception:
                pass

    # ---------------- 与编辑器同步 ----------------
    def refresh_from_config(self):
        """方案切换后回填：主角称呼、语音、演化量摘要与列表。"""
        self._loading = True
        try:
            self.protagonist_var.set(
                str(getattr(self.scenario_editor, "protagonist_title", "") or ""))
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
        self._set_save_hint("主角称呼与语音修改后自动保存")

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
        """把主角称呼与语音写回方案配置（静默），并同步编辑器状态与快照。"""
        self._cancel_pending_save()
        editor = self.scenario_editor
        if self._loading or not getattr(editor, "current_scenario_id", None):
            return
        if not self.winfo_exists():
            return
        voice = self.collect_voice()
        title = self.protagonist_title()
        config = editor._scenario_repo.load_config(editor.current_scenario_id)
        if config is None:
            config = {}
        config["protagonist_title"] = title
        config["voice"] = voice
        try:
            editor._scenario_repo.save_config(editor.current_scenario_id, config)
        except Exception as exc:
            self._set_save_hint(f"保存失败：{exc}")
            return
        self._voice = voice
        editor.protagonist_title = title
        editor.voice = voice
        # 只把这两项写回通用页的已保存快照：整份重拍会把「通用」页里尚未保存的
        # 编辑当成已保存，切方案时就再也不会提示了
        snapshot = getattr(editor, "_saved_prompt_snapshot", None)
        if isinstance(snapshot, dict) and snapshot:
            snapshot["protagonist_title"] = title
            snapshot["voice"] = dict(voice)
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
            text = "未安装 edge-tts（pip install edge-tts），运行时不会出声"
        elif level == VELUM:
            text = "仅 Solea / Bulla 的对话句会朗读，当前等级 Velum 不适用"
        else:
            text = "仅带说话人的对话句会朗读，需联网合成（首次约 1 秒）"
        label.configure(text=text)

    # ---------------- 收集 ----------------
    def protagonist_title(self) -> str:
        return self.protagonist_var.get().strip()

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
