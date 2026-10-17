import copy
import tkinter as tk

import customtkinter as ctk

import ui.ctk
from dungeon.actions import (
    MINI_GAME_DEFAULT_ID, NEW_ACTIONS, VISUAL_FILTERS, VISUAL_FILTER_KEYS,
    WINDOW_FX, WINDOW_FX_KEYS, action_label, normalize_action_type,
)
from dungeon.window.minigame import list_mini_games, mini_game_params
from dungeon.audio import buses
from dungeon.audio.chapters import (
    CHAPTER_ANY, CHAPTER_ANY_LABEL, CHAPTER_NONE, CHAPTER_NONE_LABEL,
    DEFAULT_BGM_LEVEL, DEFAULT_SENSITIVITY_ATTR, chapter_names,
)
from dungeon.audio.voice_fx import (DEFAULT_INTENSITY, MAX_INTENSITY, PRESET_NONE,
                                    SLOT_LABELS, SLOTS, label_of, preset_keys_for)
from dungeon.overrides import parse_overrides
# 旧版动作清单归校验器所有：这里只借来识别旧配置
from dungeon.validate import LEGACY_ACTIONS
from ui.ctk.dialogs import BaseDialog
from ui.ctk.theme import (
    TRIGGER_BTN_TEXT, TRIGGER_CARD_BG, TRIGGER_CARD_BORDER,
    TRIGGER_CHECK_BG, TRIGGER_CHECK_BG_HOVER, TRIGGER_CMP_BTN,
    TRIGGER_CMP_BTN_HOVER, TRIGGER_FIELD_BG, TRIGGER_FIELD_BORDER,
    TRIGGER_FIELD_HOVER, TRIGGER_HINT, TRIGGER_KEY_BTN, TRIGGER_KEY_BTN_HOVER,
    TRIGGER_LABEL, TRIGGER_METRIC_BTN, TRIGGER_METRIC_BTN_HOVER,
    TRIGGER_TEXT, WIDGET_HOVER,
)
from ui.ctk.widgets import OptionButton


class TriggerEditDialog(BaseDialog):
    DEFAULT_CONDITION_TEMPLATE = {
        "operator": "and",
        "rules": [
            {
                "key": "总计数",
                "comparator": "==",
                "value": 0
            }
        ]
    }

    # 当前动作类型的参数说明（固定显示在动作设置下方，只留要点，2~3 行）
    ACTION_DESCRIPTIONS = {
        "insert": (
            "把文本插入故事正文；勾选「延迟插入」则等下一段 AI 段落返回后再显示。"
        ),
        "option": (
            "至少填 2 项，编号从 0 开始；玩家的选择按编号记入本触发器的选择记录。\n"
            "其他触发器可用「选择:本触发器名称」判定。"
        ),
        "effect": (
            "在指定步数内临时改写本章节的部分「环境」（至少勾选一个），走完后交还章节默认值。"
        ),
        "fx": (
            "触发即播放一段作用于整个会话窗口的视效；按持续秒数自动消退，期间其他动作照常。"
        ),
        "goto": (
            "进入目标章节并应用该章节的「环境」;「结束章节」内不可用。"
        ),
        "mini_game": (
            "触发时进入小游戏、副本步进暂停；结算回传的整数记入本触发器的选择记录。\n"
            "其他触发器可用「选择:本触发器名称」判定。"
        ),
        "none": "不执行实际动作，只标记条件已成立，供其他触发器作为前置条件引用。",
    }
    #: 条件预览的行号（列表位置 → 标记）
    CONDITION_MARKS = ("①", "②", "③", "④")
    OPERATOR_LABELS = (("且（全部满足）", "and"), ("或（任一满足）", "or"))
    GOTO_LEAVE_LABEL = "（离开章节）"
    #: 「章节效果覆盖」滤镜下拉里表示**显式清除**的选项（写空串，区别于「不覆盖」）
    EFFECT_FILTER_CLEAR_LABEL = "（清除滤镜）"

    #: 动作卡片的基础高度（逻辑像素）：卡片会吃掉窗口剩余高度（expand），
    #: 这是它在窗口被压小时的下限——多数动作类型放得下，超出的才卡片内滚动
    ACTION_CARD_HEIGHT = 160
    #: 对话框固定尺寸（逻辑像素）：宽度按两列表单留够（旧版 560），高度比旧版 720 矮
    DIALOG_WIDTH = 580
    DIALOG_HEIGHT = 640
    DIALOG_MIN_WIDTH = 500
    DIALOG_MIN_HEIGHT = 540

    def __init__(self, parent, trigger: dict, evolution_names=None, all_triggers=None,
                 scenario_repo=None, scenario_id=None, evolution_attrs=None, chapters=None):
        if evolution_names is None:
            evolution_names = []
        if all_triggers is None:
            all_triggers = []
        super().__init__(parent)
        self.title("编辑触发器")
        self.geometry(f"{self.DIALOG_WIDTH}x{self.DIALOG_HEIGHT}")
        self.minsize(self.DIALOG_MIN_WIDTH, self.DIALOG_MIN_HEIGHT)
        self.resizable(True, True)
        self.trigger = trigger if trigger is not None else {}
        self.evolution_names = evolution_names
        self.all_triggers = all_triggers   # 用于校验名称唯一性
        self._scenario_repo = scenario_repo
        self.scenario_id = scenario_id
        self.result = None
        self.evolution_attrs = [a for a in (evolution_attrs or []) if isinstance(a, dict)]
        # 敏感效果只针对介入度、破坏性与自定义属性，总伤亡不在演化对象内
        self.sens_targets = [a["name"] for a in self.evolution_attrs
                             if a.get("name") and a.get("type") != "casualty"] or evolution_names
        self.chapter_names = chapter_names(chapters)
        # 固定文本章节：这些章节里「插入文本」与文本流是两套推进模型，运行时被跳过
        self.fixed_chapter_names = {
            str(c.get("name") or "")
            for c in (chapters or [])
            if isinstance(c, dict) and (c.get("fixed_text") or {}).get("path")
        }
        self.transient(parent)
        # 抓取延到窗口可见之后（X11 要求 viewable，见 BaseDialog._grab_deferred）
        self._grab_deferred()
        self._hint_visible = False
        self._build_ui()
        self._center_dialog(parent)
        self.wait_window()

    # ---------- 章节作用域 ----------
    def _chapter_scope_labels(self) -> list:
        return [CHAPTER_ANY_LABEL, CHAPTER_NONE_LABEL] + self.chapter_names

    def _chapter_scope_to_label(self, scope) -> str:
        if not scope:
            return CHAPTER_ANY_LABEL
        if scope == CHAPTER_NONE:
            return CHAPTER_NONE_LABEL
        return str(scope)

    def _chapter_label_to_scope(self, label: str) -> str:
        label = (label or "").strip()
        if label == CHAPTER_ANY_LABEL:
            return CHAPTER_ANY
        if label == CHAPTER_NONE_LABEL:
            return CHAPTER_NONE
        return label

    def _goto_scope_to_label(self, scope) -> str:
        return str(scope) if scope else self.GOTO_LEAVE_LABEL

    def _goto_label_to_scope(self, label: str) -> str:
        label = (label or "").strip()
        return "" if label == self.GOTO_LEAVE_LABEL else label

    def _build_ui(self):
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill='both', expand=True, padx=14, pady=12)
        self._main = main
        section_label = dict(font=self.SECTION_FONT, anchor='w')

        # 动作模板：仅新版动作（背景切换/性格敏感化已由章节属性承担，
        # 结局已迁移为「结束章节」；旧配置中的结局触发器仍可编辑保存）。
        # 由基础信息里的「动作类型」下拉选择，见 _on_action_type_picked。
        self._template_commands = {
            "insert": self._insert_template, "option": self._option_template,
            "effect": self._effect_template, "fx": self._fx_template,
            "goto": self._goto_template,
            "mini_game": self._mini_game_template, "none": self._empty_template,
        }

        # 确定/取消：先按 side='bottom' 占位，任何布局下都留在可视区
        btn_frame = ctk.CTkFrame(main, fg_color="transparent")
        btn_frame.pack(side='bottom', pady=(10, 0))
        self._make_std_button(btn_frame, "确定", True, self._ok).pack(
            side='left', padx=6)
        self._make_std_button(btn_frame, "取消", False, self._cancel).pack(
            side='left', padx=6)

        # ---------- 触发条件（固定区域，始终可见）----------
        # 条件区 / 类型说明 / 按钮都先按 side='bottom' 占位（顺序＝从下往上），
        # 中间的「动作设置」卡片吃掉剩余高度。
        condition_frame = ctk.CTkFrame(main, fg_color="transparent")
        condition_frame.pack(side='bottom', fill='x', pady=(10, 0))
        cond_head = ctk.CTkFrame(condition_frame, fg_color="transparent")
        cond_head.pack(fill='x', pady=(0, 2))
        ctk.CTkLabel(cond_head, text="触发条件", **section_label).pack(side='left')
        ctk.CTkLabel(cond_head, text="最多 4 条；连接符切换处保存时自动加括号",
                     font=self.UI_FONT_SMALL, text_color=TRIGGER_HINT).pack(
                         side='left', padx=(12, 0))
        self._build_condition_controls(condition_frame)

        # ---------- 类型说明（固定区域：当前动作类型的要点）----------
        hint_area = ctk.CTkFrame(main, fg_color="transparent")
        hint_area.pack(side='bottom', fill='x', pady=(10, 0))
        self.action_description_label = ctk.CTkLabel(
            hint_area, text="", justify='left', anchor='nw', wraplength=560,
            font=self.UI_FONT_SMALL, text_color=TRIGGER_HINT)
        self.action_description_label.pack(fill='x')

        # ---------- 基础信息（固定区域，不随动作设置滚动）----------
        ctk.CTkLabel(main, text="基础信息", **section_label).pack(
            side='top', fill='x', pady=(0, 4))
        common = ctk.CTkFrame(main, fg_color="transparent")
        common.pack(side='top', fill='x', padx=6, pady=(0, 6))
        self._build_basic_info(common)

        # ---------- 动作设置（明亮卡片：只放当前类型的动作表单）----------
        self.action_head = ctk.CTkLabel(main, text="动作设置", **section_label)
        self.action_head.pack(side='top', fill='x', pady=(6, 4))
        self.action_card = ctk.CTkScrollableFrame(
            main, fg_color=TRIGGER_CARD_BG, border_width=1,
            border_color=TRIGGER_CARD_BORDER, corner_radius=6,
            height=self.ACTION_CARD_HEIGHT)
        # 内置滚动条的默认请求高度是 200，会盖过更矮的卡片高度；缩到 1 让
        # ACTION_CARD_HEIGHT 说了算（滚动条本身仍随卡片拉伸，超长时才可拖）。
        self.action_card._scrollbar.configure(height=1)
        self.action_card.pack(side='top', fill='both', expand=True)
        self.action_area = ctk.CTkFrame(self.action_card, fg_color="transparent")
        self.action_area.pack(fill='x', padx=6, pady=6)
        self.action_frames = {}
        self.action_frames["insert"] = self._build_insert_form(self.action_area)
        self.action_frames["option"] = self._build_option_form(self.action_area)
        self.action_frames["effect"] = self._build_effect_form(self.action_area)
        self.action_frames["fx"] = self._build_fx_form(self.action_area)
        self.action_frames["goto"] = self._build_goto_form(self.action_area)
        self.action_frames["mini_game"] = self._build_mini_game_form(self.action_area)

        self._load_condition_controls()
        self._load_action_controls(self.trigger.get("action_data", {}))
        self._refresh_action_availability()

        self.bind("<Escape>", lambda _event: self._cancel())
        self.bind("<Control-s>", lambda _event: self._ok())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

    # ---------- 表单排版（标签列 + 控件列）----------
    #: 动作设置表单左右分栏的权重（标签列, 控件列）。文本型动作（插入 / 选项）
    #: 以填写文本为主，主要宽度让给右栏的输入控件；参数型动作（效果 / 视效 /
    #: 跳转 / 小游戏）标签较长、数值控件短，主要宽度留给左栏。
    FORM_SPLIT_TEXT = (1, 3)
    FORM_SPLIT_PARAM = (3, 2)

    def _form_grid(self, frame, weights=FORM_SPLIT_PARAM):
        """把 frame 排成两列：0 = 标签列，1 = 控件列。

        ``weights`` 只决定两列**分配多余宽度**时的比例（标签列, 控件列）——
        控件本身给不给拉伸由 ``_form_row`` 的 sticky 决定。这里刻意不设
        uniform：uniform 会按权重强行撑开列宽，小游戏参数那种长标签会把
        控件挤出卡片右侧。
        """
        frame.grid_columnconfigure(0, weight=weights[0])
        frame.grid_columnconfigure(1, weight=weights[1])
        return frame

    def _form_row(self, parent, row, text, fill=False):
        """一行：标签在左，控件容器在右（fill=True 时容器拉满控件列）。"""
        ctk.CTkLabel(parent, text=text, anchor='w', font=self.UI_FONT,
                     text_color=TRIGGER_LABEL).grid(
                         row=row, column=0, sticky='w', padx=(10, 12), pady=(0, 8))
        control = ctk.CTkFrame(parent, fg_color="transparent")
        control.grid(row=row, column=1, sticky='ew' if fill else 'e',
                     padx=(0, 10), pady=(0, 8))
        if fill:
            control.grid_columnconfigure(0, weight=1)
        return control

    def _form_wide(self, parent, row, pady=(0, 8)):
        """跨两列的容器：放子表单（语音两档 / 敏感效果条目 / 小游戏参数）。

        右侧不留边距，让子表单里的控件与其它行的控件落在同一条右边缘上。
        """
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=row, column=0, columnspan=2, sticky='ew',
                   padx=(10, 0), pady=pady)
        return frame

    def _form_hint(self, parent, row, text):
        ctk.CTkLabel(parent, text=text, justify='left', anchor='w', wraplength=520,
                     font=self.UI_FONT_SMALL, text_color=TRIGGER_HINT).grid(
                         row=row, column=0, columnspan=2, sticky='w',
                         padx=10, pady=(0, 8))

    def _option_field(self, parent, values, variable=None, command=None, width=150):
        """表单下拉统一用 OptionButton + 输入底色。"""
        return OptionButton(
            parent, values=values, variable=variable, command=command,
            width=width, height=28, corner_radius=7, font=self.UI_FONT,
            fg_color=TRIGGER_FIELD_BG, border_color=TRIGGER_FIELD_BORDER,
            hover_color=TRIGGER_FIELD_HOVER, text_color=TRIGGER_LABEL,
            text_color_disabled=TRIGGER_HINT,
            popup_fg_color=TRIGGER_FIELD_BG, popup_hover_color=TRIGGER_FIELD_HOVER,
            popup_text_color=TRIGGER_LABEL, popup_border_color=TRIGGER_FIELD_BORDER)

    def _entry_field(self, parent, variable=None, width=110, placeholder=""):
        return ctk.CTkEntry(
            parent, textvariable=variable, width=width, height=28,
            font=self.UI_FONT, placeholder_text=placeholder, border_width=1,
            border_color=TRIGGER_FIELD_BORDER, fg_color=TRIGGER_FIELD_BG,
            corner_radius=7)

    def _label_field(self, parent, text="", width=None):
        return ctk.CTkLabel(parent, text=text, font=self.UI_FONT,
                            text_color=TRIGGER_LABEL, anchor='w', width=width or 0)

    def _checkbox_field(self, parent, text, variable):
        # 无文字时收窄到只放得下勾选框，好让勾选框贴住右边的控件列
        return ctk.CTkCheckBox(parent, text=text, variable=variable,
                               width=100 if text else 24,
                               font=self.UI_FONT, text_color=TRIGGER_TEXT,
                               fg_color=TRIGGER_CHECK_BG,
                               hover_color=TRIGGER_CHECK_BG_HOVER,
                               checkbox_width=20, checkbox_height=20)

    def _small_button(self, parent, text, command, width=76, danger=False):
        """卡片内的次要动作按钮（描边透明底，与设置页 / 章节页一致）。

        danger=True 时用警示色描边（删除 / 移除这类破坏性操作）。
        """
        return ctk.CTkButton(
            parent, text=text, width=width, height=28, font=self.UI_FONT,
            fg_color="transparent", border_width=1, corner_radius=8,
            text_color=TRIGGER_HINT,
            hover_color=TRIGGER_CMP_BTN_HOVER if danger else TRIGGER_FIELD_HOVER,
            border_color=TRIGGER_CMP_BTN if danger else TRIGGER_FIELD_BORDER,
            command=command)

    # ---------- 基础信息 ----------
    def _build_basic_info(self, common):
        """名称 / 动作类型 / 所在章节 / 前置条件 / 可再次触发（固定区域）。

        两列「标签 + 控件」：控件用固定宽度、左对齐，不随窗口拉伸；行末留一列
        吸收多余宽度，使输入框两侧始终有留白（对比 _entry_field / _option_field）。
        """
        label_style = dict(font=self.UI_FONT, text_color=TRIGGER_LABEL, anchor='w')
        common.grid_columnconfigure(4, weight=1)

        ctk.CTkLabel(common, text="名称", **label_style).grid(
            row=0, column=0, sticky='w', padx=(12, 8), pady=4)
        self.name_var = tk.StringVar(value=self.trigger.get("name", ""))
        self.name_entry = self._entry_field(common, self.name_var, width=180)
        self.name_entry.grid(row=0, column=1, sticky='w', padx=(0, 28), pady=4)

        ctk.CTkLabel(common, text="动作类型", **label_style).grid(
            row=0, column=2, sticky='w', padx=(0, 8), pady=4)
        self.action_labels = [action_label(action_type) for action_type in NEW_ACTIONS]
        self.action_type_var = tk.StringVar(value=action_label("none"))
        self.action_type_button = self._option_field(
            common, self.action_labels, variable=self.action_type_var,
            command=self._on_action_type_picked, width=150)
        self.action_type_button.grid(row=0, column=3, sticky='w', pady=4)

        ctk.CTkLabel(common, text="所在章节", **label_style).grid(
            row=1, column=0, sticky='w', padx=(12, 8), pady=4)
        self.chapter_var = tk.StringVar(
            value=self._chapter_scope_to_label(self.trigger.get("chapter")))
        self.chapter_combo = self._option_field(
            common, self._chapter_scope_labels(), variable=self.chapter_var,
            width=180)
        self.chapter_combo.grid(row=1, column=1, sticky='w', padx=(0, 28), pady=4)
        # 所在章节决定「插入文本」的说明（固定文本章节里与文本流冲突）
        self.chapter_var.trace_add(
            "write", lambda *_a: self._refresh_action_availability())

        ctk.CTkLabel(common, text="前置条件", **label_style).grid(
            row=1, column=2, sticky='w', padx=(0, 8), pady=4)
        pre_names = ",".join(self.trigger.get("precondition_names", []))
        self.pre_var = tk.StringVar(value=pre_names)
        self.pre_entry = self._entry_field(
            common, self.pre_var, width=180, placeholder="触发器名称，逗号分隔")
        self.pre_entry.grid(row=1, column=3, sticky='w', pady=4)

        # 可再次触发（所有动作类型通用）
        self.repeatable_var = tk.BooleanVar(value=self.trigger.get("repeatable", True))
        self.repeatable_check = ctk.CTkCheckBox(
            common, text="可再次触发", variable=self.repeatable_var,
            font=self.UI_FONT, text_color=TRIGGER_TEXT,
            fg_color=TRIGGER_CHECK_BG, hover_color=TRIGGER_CHECK_BG_HOVER,
            checkbox_width=20, checkbox_height=20)
        self.repeatable_check.grid(row=2, column=0, columnspan=5, sticky='w',
                                   padx=12, pady=(6, 0))

    def _action_type_from_label(self, label: str) -> str:
        for action_type in NEW_ACTIONS:
            if action_label(action_type) == label:
                return action_type
        return "none"

    def _on_action_type_picked(self, label):
        """动作类型下拉：切到该类型的空白模板（名称与动作数据一并重置）。"""
        action_type = self._action_type_from_label(label)
        if action_type == self.current_action_type:
            return
        self._template_commands[action_type]()

    # ---------- 各动作类型的独立表单构造 ----------
    def _build_insert_form(self, parent):
        frame = self._form_grid(ctk.CTkFrame(parent, fg_color="transparent"),
                                self.FORM_SPLIT_TEXT)
        row = 0
        text_control = self._form_row(frame, row, "插入段落文本", fill=True)
        self.insert_text = ctk.CTkTextbox(
            text_control, height=82, wrap='word', font=self.UI_FONT,
            border_width=1, border_color=TRIGGER_FIELD_BORDER,
            fg_color=TRIGGER_FIELD_BG, corner_radius=7)
        self.insert_text.pack(fill='both', expand=True)
        row += 1

        attr_control = self._form_row(frame, row, "段落属性")
        self.insert_type_var = tk.StringVar(value="background")
        self.insert_type_button = self._option_field(
            attr_control, ["background", "branch", "dialog", "interaction", "action"],
            variable=self.insert_type_var, width=140)
        self.insert_type_button.pack(side='left')
        row += 1

        self.highlight_var = tk.BooleanVar(value=False)
        self._checkbox_field(self._form_row(frame, row, "高亮"), "",
                             self.highlight_var).pack(side='left')
        row += 1

        self.delayed_var = tk.BooleanVar(value=False)
        self._checkbox_field(self._form_row(frame, row, "延迟插入"), "",
                             self.delayed_var).pack(side='left')
        return frame

    def _build_option_form(self, parent):
        frame = self._form_grid(ctk.CTkFrame(parent, fg_color="transparent"),
                                self.FORM_SPLIT_TEXT)
        self.option_entries = []
        for index in range(4):
            ctk.CTkLabel(frame, text=f"{index + 1}.", anchor='e', width=24,
                         font=self.UI_FONT, text_color=TRIGGER_LABEL).grid(
                             row=index, column=0, sticky='e', padx=(10, 12), pady=(0, 8))
            entry = self._entry_field(
                frame, width=240, placeholder=f"选项 {index + 1} 提示（可留空）")
            entry.grid(row=index, column=1, sticky='we', padx=(0, 10), pady=(0, 8))
            self.option_entries.append(entry)
        return frame

    # ---------- 新版动作：章节效果短暂覆盖 / 跳转章节 ----------
    def _build_effect_form(self, parent):
        """四个可覆盖维度的勾选表单（不勾 = 沿用章节，勾 = 这段期间改写）。

        背景滤镜 / 音乐音量 / 语音效果 / 敏感效果各自一个勾选；同名维度多条覆盖
        并存时后触发的优先，所以这里只负责挑选值，合成由运行时按集合算。
        """
        frame = self._form_grid(ctk.CTkFrame(parent, fg_color="transparent"))
        row = 0

        # --- 持续步数 ---
        duration_control = self._form_row(frame, row, "持续步数")
        self.effect_duration_var = tk.StringVar(value="1")
        self._entry_field(duration_control, self.effect_duration_var, width=68).pack(
            side='left')
        self._label_field(duration_control, "步").pack(side='left', padx=(6, 0))
        row += 1

        # --- 背景滤镜 ---
        self.effect_filter_on = tk.BooleanVar(value=False)
        filter_control = self._form_row(frame, row, "背景滤镜")
        self.effect_filter_labels = [self.EFFECT_FILTER_CLEAR_LABEL] + \
            [label for _key, label in VISUAL_FILTERS]
        self.effect_filter_var = tk.StringVar(value=self.effect_filter_labels[0])
        self._option_field(filter_control, self.effect_filter_labels,
                           variable=self.effect_filter_var, width=150).pack(
                               side='left')
        self._checkbox_field(filter_control, "", self.effect_filter_on).pack(
            side='left', padx=(10, 0))
        row += 1

        # --- 音乐档位 ---
        self.effect_bgm_on = tk.BooleanVar(value=False)
        bgm_control = self._form_row(frame, row, "音乐音量")
        self.effect_bgm_level_var = tk.StringVar(
            value=buses.step_display(DEFAULT_BGM_LEVEL))
        self._option_field(bgm_control, [label for label, _ in buses.step_choices()],
                           variable=self.effect_bgm_level_var, width=96).pack(
                               side='left')
        self._checkbox_field(bgm_control, "", self.effect_bgm_on).pack(
            side='left', padx=(10, 0))
        row += 1

        # --- 语音效果（她 / 其他人两档）---
        self.effect_voice_on = tk.BooleanVar(value=False)
        self._checkbox_field(self._form_row(frame, row, "语音效果"), "",
                             self.effect_voice_on).pack(side='left')
        row += 1
        self._build_effect_voice_rows(self._form_wide(frame, row, pady=(0, 8)))
        row += 1

        # --- 敏感效果 ---
        self.effect_sens_on = tk.BooleanVar(value=False)
        sens_control = self._form_row(frame, row, "敏感效果")
        self._small_button(sens_control, "添加条目", self._add_effect_sens_row).pack(
            side='left')
        self._checkbox_field(sens_control, "", self.effect_sens_on).pack(
            side='left', padx=(10, 0))
        row += 1
        self.effect_sens_container = self._form_wide(frame, row, pady=(0, 4))
        self.effect_sens_container.grid_columnconfigure(0, weight=1)
        self.effect_sens_rows = []
        self._effect_sens_hint = None
        self._refresh_effect_sens_hint()
        row += 1

        self._form_hint(
            frame, row,
            "覆盖是逐维度的：只改勾选的维度，其余沿用章节默认。同名维度多条并存时"
            "后触发的优先，到期逐条回退；换章立即清除全部未到期的覆盖。")
        return frame

    def _build_effect_voice_rows(self, parent):
        """两档语音效果的预设 + 强度（与章节编辑同一套预设，含「不加效果」）。"""
        self._effect_fx_vars = {}
        self._effect_fx_label_keys = {}
        self._form_grid(parent)
        for row_index, slot in enumerate(SLOTS):
            keys = preset_keys_for(slot)
            labels = [label_of(key) for key in keys]
            self._effect_fx_label_keys[slot] = {label_of(key): key for key in keys}

            ctk.CTkLabel(parent, text=SLOT_LABELS[slot], anchor='w',
                         font=self.UI_FONT, text_color=TRIGGER_LABEL).grid(
                             row=row_index, column=0, sticky='w',
                             padx=(10, 12), pady=(0, 8))
            control = ctk.CTkFrame(parent, fg_color="transparent")
            control.grid(row=row_index, column=1, sticky='e',
                         padx=(0, 10), pady=(0, 8))
            preset_var = tk.StringVar(value=label_of(PRESET_NONE))
            self._option_field(control, labels, variable=preset_var, width=150).pack(
                side='left')
            self._label_field(control, "强度").pack(side='left', padx=(10, 4))
            intensity_var = tk.IntVar(value=DEFAULT_INTENSITY)
            value_label = self._label_field(control, str(DEFAULT_INTENSITY), width=30)
            ctk.CTkSlider(control, from_=0, to=MAX_INTENSITY,
                          number_of_steps=MAX_INTENSITY, variable=intensity_var,
                          width=110).pack(side='left', padx=(0, 6))
            value_label.pack(side='left')
            intensity_var.trace_add(
                "write",
                lambda *_a, label=value_label, var=intensity_var:
                    label.configure(text=str(var.get())))
            self._effect_fx_vars[slot] = (preset_var, intensity_var)

    def _effect_voice_fx_from_ui(self) -> dict:
        """收集两档效果；选了「不加效果」的档不写进覆盖（两档都空 = 显式清除）。"""
        result = {}
        for slot in SLOTS:
            preset_var, intensity_var = self._effect_fx_vars[slot]
            key = self._effect_fx_label_keys[slot].get(preset_var.get().strip(), PRESET_NONE)
            if key == PRESET_NONE:
                continue
            result[slot] = {"preset": key,
                            "intensity": max(0, min(MAX_INTENSITY,
                                                   int(intensity_var.get())))}
        return result

    # ---------- 覆盖维度：敏感效果条目 ----------
    def _add_effect_sens_row(self, effect: dict = None):
        effect = effect or {}
        row = ctk.CTkFrame(self.effect_sens_container, fg_color="transparent")
        row.grid(row=len(self.effect_sens_rows), column=0, sticky='ew',
                 padx=(0, 10), pady=(0, 8))
        attrs = self.sens_targets or ["介入度", "破坏性"]
        attr_var = tk.StringVar(value=effect.get("attr") or attrs[0]
                                or DEFAULT_SENSITIVITY_ATTR)
        self._option_field(row, attrs, variable=attr_var, width=130).pack(side='left')
        self._label_field(row, "强度").pack(side='left', padx=(10, 4))
        strength_var = tk.StringVar(value=str(effect.get("strength", 1.0)))
        self._entry_field(row, strength_var, width=60).pack(side='left')
        self._label_field(row, "客观影响").pack(side='left', padx=(10, 4))
        objective_var = tk.StringVar(value=str(effect.get("objective", 0.0)))
        self._entry_field(row, objective_var, width=60).pack(side='left')

        entry = {"frame": row, "attr_var": attr_var, "strength_var": strength_var,
                 "objective_var": objective_var}
        self._small_button(
            row, "移除", lambda: self._remove_effect_sens_row(entry), width=52,
            danger=True).pack(side='left', padx=(10, 0))
        self.effect_sens_rows.append(entry)
        self._regrid_effect_sens_rows()
        self._refresh_effect_sens_hint()

    def _regrid_effect_sens_rows(self):
        """删条目后会留下空行号，重排一遍避免新条目与旧条目落进同一格。"""
        for index, entry in enumerate(self.effect_sens_rows):
            entry["frame"].grid_configure(row=index)

    def _remove_effect_sens_row(self, entry):
        entry["frame"].destroy()
        if entry in self.effect_sens_rows:
            self.effect_sens_rows.remove(entry)
        self._regrid_effect_sens_rows()
        self._refresh_effect_sens_hint()

    def _clear_effect_sens_rows(self):
        for entry in list(self.effect_sens_rows):
            entry["frame"].destroy()
        self.effect_sens_rows = []
        self._refresh_effect_sens_hint()

    def _refresh_effect_sens_hint(self):
        """没有条目时提示「空 = 临时清空」，有条目时收起提示。"""
        hint = getattr(self, "_effect_sens_hint", None)
        if self.effect_sens_rows:
            if hint is not None:
                hint.destroy()
                self._effect_sens_hint = None
            return
        if hint is None:
            self._effect_sens_hint = ctk.CTkLabel(
                self.effect_sens_container,
                text="（未添加条目：勾选后覆盖为空，即这段期间没有敏感效果）",
                font=self.UI_FONT_SMALL, text_color=TRIGGER_HINT, anchor='w')
            self._effect_sens_hint.grid(row=0, column=0, sticky='w')

    def _effect_sensitivity_from_ui(self) -> list:
        result = []
        for index, row in enumerate(self.effect_sens_rows, start=1):
            try:
                strength = float(row["strength_var"].get())
            except ValueError:
                raise ValueError(f"第 {index} 条敏感效果覆盖的强度必须是数字")
            try:
                objective = float(row["objective_var"].get())
            except ValueError:
                raise ValueError(f"第 {index} 条敏感效果覆盖的客观影响必须是数字")
            attr = row["attr_var"].get().strip() or DEFAULT_SENSITIVITY_ATTR
            result.append({"attr": attr, "strength": strength, "objective": objective})
        return result

    def _collect_effect_overrides(self) -> dict:
        """按勾选收集覆盖维度；未勾选的维度不写进 action_data（= 沿用章节）。"""
        data = {}
        if self.effect_filter_on.get():
            data["filter"] = self._effect_filter_label_to_key(self.effect_filter_var.get())
        if self.effect_bgm_on.get():
            data["bgm_level"] = buses.step_from_display(self.effect_bgm_level_var.get())
        if self.effect_voice_on.get():
            data["voice_fx"] = self._effect_voice_fx_from_ui()
        if self.effect_sens_on.get():
            data["sensitivity"] = self._effect_sensitivity_from_ui()
        return data

    def _load_effect_overrides(self, action_data):
        """把已有配置回填到四个维度（未写出的维度 = 不勾选）。"""
        parsed = parse_overrides(action_data)
        self.effect_filter_on.set("filter" in parsed)
        self.effect_filter_var.set(
            self._effect_filter_key_to_label(parsed.get("filter") or ""))
        self.effect_bgm_on.set("bgm_level" in parsed)
        self.effect_bgm_level_var.set(
            buses.step_display(parsed.get("bgm_level", DEFAULT_BGM_LEVEL)))
        self.effect_voice_on.set("voice_fx" in parsed)
        voice = parsed.get("voice_fx") or {}
        for slot in SLOTS:
            spec = voice.get(slot) or {}
            preset_var, intensity_var = self._effect_fx_vars[slot]
            preset_var.set(label_of(spec.get("preset") or PRESET_NONE))
            intensity_var.set(int(spec.get("intensity", DEFAULT_INTENSITY)))
        self.effect_sens_on.set("sensitivity" in parsed)
        self._clear_effect_sens_rows()
        for effect in parsed.get("sensitivity") or []:
            self._add_effect_sens_row(effect)

    # ---------- 新版动作：窗口视效 ----------
    def _build_fx_form(self, parent):
        frame = self._form_grid(ctk.CTkFrame(parent, fg_color="transparent"))
        row = 0

        fx_control = self._form_row(frame, row, "视效")
        self.fx_labels = [label for _key, label in WINDOW_FX]
        self.fx_var = tk.StringVar(value=self.fx_labels[0])
        self._option_field(fx_control, self.fx_labels, variable=self.fx_var,
                           width=150).pack(side='left')
        row += 1

        duration_control = self._form_row(frame, row, "持续秒数")
        self.fx_duration_var = tk.StringVar(value="0.8")
        self._entry_field(duration_control, self.fx_duration_var, width=68).pack(
            side='left')
        self._label_field(duration_control, "秒").pack(side='left', padx=(6, 0))
        row += 1

        intensity_control = self._form_row(frame, row, "强度 (0~100)")
        self.fx_intensity_var = tk.StringVar(value="60")
        self._entry_field(intensity_control, self.fx_intensity_var, width=68).pack(
            side='left')
        row += 1

        return frame

    def _fx_label_to_key(self, label: str) -> str:
        for key, item_label in WINDOW_FX:
            if item_label == label:
                return key
        return WINDOW_FX_KEYS[0]

    def _fx_key_to_label(self, key: str) -> str:
        for item_key, label in WINDOW_FX:
            if item_key == key:
                return label
        return WINDOW_FX[0][1]

    def _build_goto_form(self, parent):
        frame = self._form_grid(ctk.CTkFrame(parent, fg_color="transparent"))
        control = self._form_row(frame, 0, "目标章节")
        self.goto_values = [self.GOTO_LEAVE_LABEL] + self.chapter_names
        self.goto_var = tk.StringVar(value=self.goto_values[0])
        self._option_field(control, self.goto_values, variable=self.goto_var,
                           width=200).pack(side='left')
        if not self.chapter_names:
            self._form_hint(frame, 1, "当前副本还没有章节，请先到「章节」页签添加。")
        return frame

    def _build_mini_game_form(self, parent):
        frame = self._form_grid(ctk.CTkFrame(parent, fg_color="transparent"))
        game_ids = [gid for gid, _label, _backend in list_mini_games()] \
            or [MINI_GAME_DEFAULT_ID]
        self.minigame_game_var = tk.StringVar(
            value=MINI_GAME_DEFAULT_ID if MINI_GAME_DEFAULT_ID in game_ids
            else game_ids[0])
        game_control = self._form_row(frame, 0, "小游戏")
        self._option_field(game_control, game_ids, variable=self.minigame_game_var,
                           width=160, command=self._rebuild_minigame_params).pack(
                               side='left')

        # 参数区：按所选小游戏 manifest.params 动态生成（切换游戏即重建）
        self.minigame_param_vars = {}
        self.minigame_params_frame = self._form_wide(frame, 1)
        self._form_grid(self.minigame_params_frame)
        self._rebuild_minigame_params()
        return frame

    def _rebuild_minigame_params(self, *_args):
        """按当前所选小游戏的参数声明重建参数控件。

        值来源优先级：显式传入的 action_data（回填已有触发器时）→
        重建前控件里已填的值（切换游戏时保留同名键）→ 参数默认值。
        """
        frame = getattr(self, "minigame_params_frame", None)
        if frame is None:
            return
        prev_values = {}
        for key, (var, _ptype) in getattr(self, "minigame_param_vars", {}).items():
            try:
                prev_values[key] = var.get()
            except Exception:
                pass
        source = next((a for a in _args if isinstance(a, dict)), None)
        for child in frame.winfo_children():
            child.destroy()
        self.minigame_param_vars = {}

        params = mini_game_params(self.minigame_game_var.get())
        if not params:
            ctk.CTkLabel(frame, text="该小游戏没有可配参数。", anchor='w',
                         font=self.UI_FONT_SMALL, text_color=TRIGGER_HINT).grid(
                             row=0, column=0, columnspan=2, sticky='w', pady=(0, 8))
            return

        def initial(key, default):
            if source is not None and key in source:
                return source[key]
            if key in prev_values:
                return prev_values[key]
            return default

        for index, param in enumerate(params):
            key = str(param.get("key") or "").strip()
            ptype = str(param.get("type") or "str")
            label = str(param.get("label") or key)
            lo, hi = param.get("min"), param.get("max")
            if ptype in ("int", "float") and lo is not None and hi is not None:
                label = f"{label} ({lo}~{hi})"
            default = param.get("default", "" if ptype == "str" else 0)
            if ptype == "bool":
                value = initial(key, default)
                if isinstance(value, str):
                    value = value.strip().lower() in ("1", "true", "yes", "是")
                var = tk.BooleanVar(value=bool(value))
                self._checkbox_field(self._form_row(frame, index, label), "",
                                     var).pack(side='left')
                self.minigame_param_vars[key] = (var, ptype)
                continue
            var = tk.StringVar()
            value = initial(key, default)
            var.set("" if value is None else str(value))
            self._entry_field(self._form_row(frame, index, label), var, width=110).pack(
                side='left')
            self.minigame_param_vars[key] = (var, ptype)

    def _minigame_params_to_data(self, data):
        """把参数区当前值按声明类型写进 action_data（解析失败的键省略）。"""
        for key, (var, ptype) in getattr(self, "minigame_param_vars", {}).items():
            try:
                if ptype == "bool":
                    data[key] = bool(var.get())
                    continue
                raw = str(var.get()).strip()
                if raw == "":
                    continue
                if ptype == "int":
                    data[key] = int(float(raw))
                elif ptype == "float":
                    data[key] = float(raw)
                else:
                    data[key] = raw
            except (TypeError, ValueError):
                continue

    def _effect_filter_label_to_key(self, label: str) -> str:
        """标签 → 滤镜键；「（清除滤镜）」映射为空串（显式清除）。"""
        if label == self.EFFECT_FILTER_CLEAR_LABEL:
            return ""
        for key, item_label in VISUAL_FILTERS:
            if item_label == label:
                return key
        return ""

    def _effect_filter_key_to_label(self, key: str) -> str:
        """滤镜键 → 标签；空串 / 未知键都显示为「（清除滤镜）」。"""
        for item_key, label in VISUAL_FILTERS:
            if item_key == key:
                return label
        return self.EFFECT_FILTER_CLEAR_LABEL

    def _show_action_frame(self, action_type):
        """仅显示当前动作类型的表单，其余动作帧隐藏。

        「条件标记」没有动作参数：连标题一起收起整个动作区
        （空卡片留着会撑出一块空白，说明由下方「类型说明」承担）。
        """
        for type_name, frame in self.action_frames.items():
            if type_name == action_type:
                frame.pack(fill='x')
            else:
                frame.pack_forget()
        if action_type == "none":
            self.action_card.pack_forget()
            self.action_head.pack_forget()
        else:
            self.action_head.pack(side='top', fill='x', pady=(6, 4))
            self.action_card.pack(side='top', fill='both', expand=True)
        self.current_action_type = action_type
        self.action_type_var.set(action_label(action_type))

    # ---------- 模板方法 ----------
    def _set_action_template(self, action_type, name, action_data):
        self.name_var.set(name)
        self._refresh_action_ui(action_type, action_data)

    def _empty_template(self):
        self._set_action_template("none", "新空触发", {})

    def _build_condition_controls(self, parent):
        """条件区：可点击的文字预览（每条一行）+ 一行编辑控件。

        预览只列已添加的条件，末尾固定「＋ 添加条件」；点某条即用下面这一行
        控件编辑它——四条条件共用同一个编辑行，不必把四行控件同时铺开。
        """
        self._active_slots = []      # 已添加的条件槽位（按下标升序）
        self._selected_slot = None   # 正在编辑的槽位
        self.condition_rows = []
        # 行间连接符（第 i 个连接第 i-1 与第 i 条）；切换处保存时成组加括号
        self.condition_connectors = []

        self.cond_list_frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.cond_list_frame.pack(fill='x', pady=(0, 4))
        self.cond_editor_frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.cond_editor_frame.pack(fill='x')

        op_labels = [label for label, _code in self.OPERATOR_LABELS]
        base_keys = list(dict.fromkeys(
            self.evolution_names + ["介入度", "破坏性", "总伤亡", "总计数", "间隔计数",
                                    "节内计数", "伤亡数组"]))
        # 维护选择数组（整数记录）的触发器：选项分支 + 小游戏（返回值=达成关数）
        base_keys += [f"选择:{t['name']}" for t in self.all_triggers
                      if t.get("name") and t.get("action_type") in ("option", "mini_game")]
        for index in range(4):
            self.condition_rows.append(
                self._build_condition_row(index, op_labels, base_keys))

    def _build_condition_row(self, index, op_labels, base_keys):
        """单条条件的编辑控件（默认隐藏；选中该条才显示，见 _select_condition）。"""
        frame = ctk.CTkFrame(self.cond_editor_frame, fg_color="transparent")
        top = ctk.CTkFrame(frame, fg_color="transparent")
        top.pack(fill='x', pady=(0, 3))
        bottom = ctk.CTkFrame(frame, fg_color="transparent")
        bottom.pack(fill='x')

        conn_var = None
        key_column = 0
        if index > 0:
            ctk.CTkLabel(top, text="与上一条:", font=self.UI_FONT,
                         text_color=TRIGGER_HINT).grid(row=0, column=0, sticky='w')
            conn_var = tk.StringVar(value=self.OPERATOR_LABELS[0][0])
            OptionButton(top, values=op_labels, variable=conn_var, width=150,
                         height=28, font=self.UI_FONT).grid(
                             row=0, column=1, sticky='w', padx=(6, 18))
            conn_var.trace_add("write", lambda *_a: self._refresh_condition_list())
            self.condition_connectors.append(conn_var)
            key_column = 2

        key_var = tk.StringVar()
        key_button = self._dropdown_button(
            top, lambda: base_keys or ["总计数"], key_var,
            TRIGGER_KEY_BTN, TRIGGER_KEY_BTN_HOVER, width=150,
            command=lambda i=index: self._sync_condition_row(i))
        key_button.grid(row=0, column=key_column, sticky='w')

        metric_var = tk.StringVar(value=self._metric_options("总伤亡")[0][0])
        metric_button = self._dropdown_button(
            top,
            lambda kv=key_var: [lab for lab, _c in self._metric_options(kv.get().strip())],
            metric_var, TRIGGER_METRIC_BTN, TRIGGER_METRIC_BTN_HOVER, width=104,
            command=lambda i=index: self._sync_condition_row(i))
        metric_button.grid(row=0, column=key_column + 1, padx=(8, 0), sticky='w')

        comparator_var = tk.StringVar(value=">=")
        comparator_button = self._dropdown_button(
            bottom, lambda: [">=", "<=", ">", "<", "==", "!="], comparator_var,
            TRIGGER_CMP_BTN, TRIGGER_CMP_BTN_HOVER, width=58)
        comparator_button.grid(row=0, column=0, sticky='w')
        comparator_var.trace_add("write", lambda *_a: self._refresh_condition_list())

        value_var = tk.StringVar()
        value_widget = ctk.CTkEntry(bottom, textvariable=value_var, width=120,
                                    height=28, font=self.UI_FONT,
                                    placeholder_text="数值")
        value_widget.grid(row=0, column=1, padx=(8, 18), sticky='w')

        target_var = tk.StringVar()
        target_widget = ctk.CTkEntry(bottom, textvariable=target_var, width=100,
                                     height=28, font=self.UI_FONT,
                                     placeholder_text="目标编号")
        target_widget.grid(row=0, column=2, padx=(0, 8), sticky='w')

        window_var = tk.StringVar()
        window_widget = ctk.CTkEntry(bottom, textvariable=window_var, width=100,
                                     height=28, font=self.UI_FONT,
                                     placeholder_text="窗口(≥2)")
        window_widget.grid(row=0, column=3, sticky='w')

        row = {
            "frame": frame,
            "key_var": key_var, "metric_var": metric_var, "target_var": target_var,
            "comparator_var": comparator_var, "value_var": value_var, "window_var": window_var,
            "conn_var": conn_var,
            "key_widget": key_button, "metric_widget": metric_button,
            "target_widget": target_widget, "comparator_widget": comparator_button,
            "value_widget": value_widget, "window_widget": window_widget,
        }
        for var in (value_var, target_var, window_var):
            var.trace_add("write", lambda *_a: self._refresh_condition_list())
        self._sync_condition_row_widgets(row)
        return row

    # ---------- 条件规则的直观化 ----------
    def _dropdown_button(self, parent, values, var, fg_color, hover_color,
                         width=110, command=None):
        """语义色下拉按钮（见 OptionButton）：点击弹出候选菜单，选中写回 var。

        ``values`` 是返回候选列表的函数（度量的候选随条件键变化），变化后由
        调用方对返回控件 ``configure(values=...)``；``command`` 在写回 var 之后触发。
        """
        return OptionButton(
            parent, values=values(), variable=var,
            command=lambda _choice: command() if command else None,
            width=width, font=self.UI_FONT, border_width=0,
            fg_color=fg_color, hover_color=hover_color, text_color=TRIGGER_BTN_TEXT)

    def _metric_options(self, key):
        """按规则键返回可选的数组统计方式（展示文案, 内部代码）。"""
        if key.startswith("选择:"):
            return [("选择次数", "count"), ("出现占比", "ratio"),
                    ("趋势变化", "trend"), ("最后一次选择", "last")]
        return [("伤亡条数", "count"), ("平均伤亡", "avg"),
                ("伤亡趋势", "trend"), ("最近一次", "last")]

    def _metric_label_to_code(self, key, label):
        for lab, code in self._metric_options(key):
            if lab == label:
                return code
        return "count"

    def _metric_code_to_label(self, key, code):
        options = self._metric_options(key)
        for lab, _code in options:
            if _code == code:
                return lab
        return options[0][0]

    def _op_label_to_code(self, label):
        for lab, code in self.OPERATOR_LABELS:
            if lab == label.strip():
                return code
        return "and"

    def _op_code_to_label(self, code):
        for lab, _code in self.OPERATOR_LABELS:
            if _code == code:
                return lab
        return self.OPERATOR_LABELS[0][0]

    def _sync_condition_row_widgets(self, row):
        """按当前键 / 统计方式调整单条条件的控件可见性。

        度量下拉只在「选择:」与「伤亡数组」上出现；目标编号跟着需要它的
        统计方式（占比 / 趋势 / 最后一次），窗口只在趋势上出现。
        """
        key = row["key_var"].get().strip()
        metric_label = row["metric_var"].get().strip()
        if not key.startswith("选择:") and key != "伤亡数组":
            row["metric_widget"].grid_remove()
            row["target_widget"].grid_remove()
            row["window_widget"].grid_remove()
            return
        options = self._metric_options(key)
        labels = [lab for lab, _code in options]
        row["metric_widget"].configure(values=labels)
        if metric_label not in labels:
            metric_label = labels[0]
            row["metric_var"].set(metric_label)
        row["metric_widget"].grid()
        code = self._metric_label_to_code(key, metric_label)
        needs_target = key.startswith("选择:") and code in ("ratio", "trend", "last")
        needs_window = code == "trend"
        if needs_target:
            row["target_widget"].configure(placeholder_text="目标编号")
            row["target_widget"].grid()
        else:
            row["target_widget"].grid_remove()
        if needs_window:
            row["window_widget"].configure(placeholder_text="窗口(≥2 步)")
            row["window_widget"].grid()
        else:
            row["window_widget"].grid_remove()

    def _sync_condition_row(self, index):
        self._sync_condition_row_widgets(self.condition_rows[index])
        self._refresh_condition_list()

    def _rule_to_text(self, rule) -> str:
        key = rule.get("key", "")
        cmp_text = {">=": "≥", "<=": "≤", ">": ">", "<": "<",
                    "==": "=", "!=": "≠"}.get(rule.get("comparator"), rule.get("comparator", "≥"))
        value = rule.get("value", 0)
        metric = rule.get("metric", "count")
        target = rule.get("target")
        window = rule.get("window")
        if key.startswith("选择:"):
            name = key.split(":", 1)[1]
            if metric == "ratio":
                return f"选择“{name}”时选到选项 #{target} 的占比 {cmp_text} {value}"
            if metric == "last":
                return f"“{name}”最后一次选择的选项编号 {cmp_text} {value}"
            if metric == "trend":
                w = window if window is not None else "最近若干"
                return f"“{name}”选项 #{target} 近 {w} 步占比变化 {cmp_text} {value}"
            return f"选择“{name}”的次数 {cmp_text} {value}"
        if key == "伤亡数组":
            if metric == "avg":
                return f"每步平均伤亡 {cmp_text} {value}"
            if metric == "last":
                return f"最近一步的伤亡 {cmp_text} {value}"
            if metric == "trend":
                w = window if window is not None else "最近若干"
                return f"近 {w} 步平均伤亡比更早时段差幅 {cmp_text} {value}"
            return f"伤亡数组累计条数 {cmp_text} {value}"
        return f"{key}{cmp_text}{value}"

    def _filled_rows(self):
        """非全空的规则行下标（升序）；部分填写也计入，交给收集阶段报错。"""
        filled = []
        for index, row in enumerate(self.condition_rows):
            if row["key_var"].get().strip() or row["value_var"].get().strip():
                filled.append(index)
        return filled

    def _effective_connectors(self, filled):
        """已填规则之间的有效连接符（and/or）。

        连接符属于其上方的空隙（第 i 个连接第 i-1、i 行）：中间行整行留空时，
        该空隙被跳过，以后一条已填行上方的连接符为准。
        """
        return [self._op_label_to_code(self.condition_connectors[cur - 1].get())
                for prev, cur in zip(filled, filled[1:])]

    @staticmethod
    def _split_condition_groups(leaves, conns):
        """把叶子序列按连接符切成条件组：连接符切换处即括号边界。

        顶层连接符取第一处切换后的连接符（无切换时即唯一连接符）；
        与顶层不同的连接符的极大连续段各自成组，其余叶子保持独立。
        返回 (顶层连接符, [(组内连接符或 None, [叶子...]), ...])——
        组内连接符为 None 表示独立叶子（单元素组，不需要括号）。
        """
        if not leaves:
            return "and", []
        top_op = conns[0] if conns else "and"
        for prev, cur in zip(conns, conns[1:]):
            if cur != prev:
                top_op = cur
                break
        groups = []
        current = [leaves[0]]
        current_op = None
        for connector, leaf in zip(conns, leaves[1:]):
            if connector == top_op:
                groups.append((current_op, current))
                current = [leaf]
                current_op = None
            else:
                if current_op is None:
                    current_op = connector
                current.append(leaf)
        groups.append((current_op, current))
        return top_op, groups

    @staticmethod
    def _build_condition(leaves, conns):
        """按连接符分段组条件：同连接符连续段嵌套成子条件组，段间用顶层连接符。

        全部同连接符时保持扁平结构（与旧格式一致，不产生无谓的嵌套）。
        """
        top_op, groups = TriggerEditDialog._split_condition_groups(leaves, conns)
        rules = []
        for op, group in groups:
            if op and len(group) > 1:
                rules.append({"operator": op, "rules": group})
            else:
                rules.extend(group)
        return {"operator": top_op, "rules": rules}

    @staticmethod
    def _flatten_condition(condition):
        """把（可能一层嵌套的）条件摊平为 (叶子, 叶子间连接符)。

        组内叶子之间用组内连接符，组边界处用顶层连接符——与
        ``_build_condition`` 互为逆操作；更深的嵌套超出编辑器表达能力，忽略。
        """
        if not isinstance(condition, dict):
            return [], []
        top_op = condition.get("operator")
        top_op = top_op if top_op in ("and", "or") else "and"
        leaves, conns = [], []

        def push(rule, connector):
            if leaves:
                conns.append(connector)
            leaves.append(rule)

        for rule in (condition.get("rules") or []):
            if isinstance(rule, dict) and isinstance(rule.get("rules"), list):
                sub_op = rule.get("operator")
                sub_op = sub_op if sub_op in ("and", "or") else top_op
                for sub_index, sub in enumerate(rule["rules"]):
                    if isinstance(sub, dict) and isinstance(sub.get("rules"), list):
                        continue
                    push(sub, sub_op if sub_index > 0 else top_op)
            else:
                push(rule, top_op)
        return leaves, conns

    def _rule_text_of(self, slot) -> str:
        """某条条件的可读文案（未填完整时给出提示），供预览行显示。"""
        row = self.condition_rows[slot]
        key = row["key_var"].get().strip()
        value = row["value_var"].get().strip()
        if not key and not value:
            return "（未填写）"
        if not key or not value:
            return "（未填完整）"
        rule = {
            "key": key,
            "comparator": row["comparator_var"].get(),
            "metric": self._metric_label_to_code(key, row["metric_var"].get().strip()),
        }
        try:
            parsed = float(value)
            rule["value"] = int(parsed) if parsed.is_integer() else parsed
        except ValueError:
            rule["value"] = value
        target = row["target_var"].get().strip()
        if key.startswith("选择:") and rule["metric"] in ("ratio", "trend", "last") and target:
            try:
                rule["target"] = int(float(target))
            except ValueError:
                rule["target"] = target
        window = row["window_var"].get().strip()
        if rule["metric"] == "trend" and window:
            try:
                rule["window"] = int(float(window))
            except ValueError:
                rule["window"] = window
        return self._rule_to_text(rule)

    def _connector_label_of(self, slot) -> str:
        """某条条件与上一条之间的连接符（预览行前缀）。"""
        var = self.condition_connectors[slot - 1]
        return "且" if self._op_label_to_code(var.get()) == "and" else "或"

    # ---------- 条件预览（可点击的文字列表）----------
    def _refresh_condition_list(self):
        """重建条件预览：每条已添加的条件一行，末尾固定「＋ 添加条件」。"""
        frame = getattr(self, "cond_list_frame", None)
        if frame is None:
            return
        for child in frame.winfo_children():
            child.destroy()
        if not self._active_slots:
            ctk.CTkLabel(frame, text="（无条件：每一步都算条件成立）",
                         font=self.UI_FONT_SMALL, text_color=TRIGGER_HINT,
                         anchor='w').pack(fill='x', padx=6, pady=(0, 2))
        for order, slot in enumerate(self._active_slots):
            self._build_condition_preview_row(frame, order, slot)
        if len(self._active_slots) < len(self.condition_rows):
            ctk.CTkButton(frame, text="＋ 添加条件", anchor='w', height=26,
                          font=self.UI_FONT, fg_color="transparent",
                          hover_color=WIDGET_HOVER, text_color=TRIGGER_KEY_BTN,
                          command=self._add_condition).pack(fill='x', pady=(2, 0))

    def _build_condition_preview_row(self, parent, order, slot):
        """一行条件预览：点它就用下面的编辑行改它，右侧「删除」删掉这条。"""
        selected = slot == self._selected_slot
        text = f"{self.CONDITION_MARKS[order]} {self._rule_text_of(slot)}"
        if order > 0:
            text = f"{self._connector_label_of(slot)}　{text}"
        row = ctk.CTkFrame(parent, corner_radius=6,
                           fg_color=WIDGET_HOVER if selected else "transparent")
        row.pack(fill='x', pady=1)
        label = ctk.CTkLabel(row, text=text, justify='left', anchor='w', wraplength=430,
                             cursor="hand2", font=self.UI_FONT,
                             text_color=TRIGGER_TEXT if selected else TRIGGER_HINT)
        label.pack(side='left', fill='x', expand=True, padx=(6, 4), pady=2)
        ctk.CTkButton(row, text="删除", width=52, height=24, font=self.UI_FONT_SMALL,
                      fg_color="transparent", border_width=1, corner_radius=8,
                      text_color=TRIGGER_HINT, hover_color=TRIGGER_CMP_BTN_HOVER,
                      border_color=TRIGGER_CMP_BTN,
                      command=lambda s=slot: self._remove_condition(s)).pack(
                          side='right', padx=(0, 4), pady=2)
        for widget in (row, label):
            widget.bind("<Button-1>", lambda _event, s=slot: self._select_condition(s))
        row.bind("<Enter>", lambda _event, r=row: r.configure(fg_color=WIDGET_HOVER))
        row.bind("<Leave>", lambda _event, r=row, s=selected:
                 r.configure(fg_color=WIDGET_HOVER if s else "transparent"))

    # ---------- 条件的增删与选中 ----------
    def _select_condition(self, slot):
        """选中一条条件：只显示它的编辑控件（其余条目收起）。"""
        if slot not in self._active_slots:
            return
        self._selected_slot = slot
        for index, row in enumerate(self.condition_rows):
            if index == slot:
                row["frame"].pack(fill='x')
            else:
                row["frame"].pack_forget()
        self._refresh_condition_list()

    def _hide_condition_editor(self):
        for row in self.condition_rows:
            row["frame"].pack_forget()

    def _add_condition(self):
        """占用一个空槽位并选中它（最多 4 条）。"""
        free = [index for index in range(len(self.condition_rows))
                if index not in self._active_slots]
        if not free:
            return
        self._active_slots.append(free[0])
        self._active_slots.sort()
        self._select_condition(free[0])

    def _remove_condition(self, slot):
        """删掉一条条件：清空它的槽位，选中位置上的下一条。"""
        if slot not in self._active_slots:
            return
        order = self._active_slots.index(slot)
        self._active_slots.remove(slot)
        self._clear_condition_slot(slot)
        if self._selected_slot != slot:
            self._refresh_condition_list()
            return
        self._hide_condition_editor()
        self._selected_slot = None
        if self._active_slots:
            self._select_condition(
                self._active_slots[min(order, len(self._active_slots) - 1)])
        else:
            self._refresh_condition_list()

    def _clear_condition_slot(self, slot):
        """把槽位变量清回默认（删除后的槽位可以被再次添加）。"""
        row = self.condition_rows[slot]
        row["key_var"].set("")
        row["metric_var"].set(self._metric_options("总伤亡")[0][0])
        row["comparator_var"].set(">=")
        row["value_var"].set("")
        row["target_var"].set("")
        row["window_var"].set("")
        self._sync_condition_row_widgets(row)

    def _load_condition_controls(self):
        condition = self.trigger.get("condition") or self.DEFAULT_CONDITION_TEMPLATE
        leaves, conns = self._flatten_condition(condition)
        self._active_slots = []
        for index, row in enumerate(self.condition_rows):
            if index >= len(leaves):
                break
            rule = leaves[index]
            key = rule.get("key", "")
            row["key_var"].set(key)
            row["comparator_var"].set(rule.get("comparator", ">="))
            row["value_var"].set(str(rule.get("value", "")))
            row["metric_var"].set(self._metric_code_to_label(key, rule.get("metric", "count")))
            row["target_var"].set(str(rule.get("target", "")) if rule.get("target") is not None else "")
            row["window_var"].set(str(rule.get("window", "")) if rule.get("window") is not None else "")
            self._active_slots.append(index)
            self._sync_condition_row_widgets(row)
        for index, conn_var in enumerate(self.condition_connectors):
            conn_var.set(self._op_code_to_label(conns[index]) if index < len(conns)
                         else self.OPERATOR_LABELS[0][0])
        if self._active_slots:
            self._select_condition(self._active_slots[0])
        else:
            self._hide_condition_editor()
            self._refresh_condition_list()

    def _collect_condition(self):
        rules = []
        filled = []
        for index, row in enumerate(self.condition_rows):
            key = row["key_var"].get().strip()
            value = row["value_var"].get().strip()
            if not key and not value:
                continue
            filled.append(index)
            if not key or not value:
                raise ValueError("条件规则的关键字和值必须同时填写")
            try:
                parsed = float(value)
                if parsed.is_integer():
                    parsed = int(parsed)
            except ValueError:
                raise ValueError(f"条件 '{key}' 的值必须是数字")
            metric_code = self._metric_label_to_code(key, row["metric_var"].get().strip())
            rule = {"key": key, "comparator": row["comparator_var"].get(), "value": parsed}
            if (key.startswith("选择:") or key == "伤亡数组") and metric_code != "count":
                rule["metric"] = metric_code
            if key.startswith("选择:") and metric_code in ("ratio", "trend", "last"):
                target = row["target_var"].get().strip()
                try:
                    rule["target"] = int(float(target))
                except ValueError:
                    raise ValueError(f"规则 '{key}' 的目标选项编号必须是数字")
            if metric_code == "trend":
                window = row["window_var"].get().strip()
                if window:
                    try:
                        rule["window"] = int(float(window))
                    except ValueError:
                        raise ValueError(f"规则 '{key}' 的窗口必须是数字")
            rules.append(rule)
        if not rules:
            return copy.deepcopy(self.DEFAULT_CONDITION_TEMPLATE)
        return self._build_condition(rules, self._effective_connectors(filled))

    def _load_action_controls(self, action_data):
        action_data = action_data or {}
        action_type = normalize_action_type(
            self.trigger.get("action_type"), action_data)
        if action_type in LEGACY_ACTIONS:
            # 旧版动作（背景切换 / 性格敏感化）已不再支持：没有对应表单，
            # 回落到“条件标记”让用户重新选择动作类型，或直接在列表中删除它
            action_type = "none"
        self._refresh_action_ui(action_type, action_data)

    def _collect_action_data(self):
        action_type = self.current_action_type
        data = {"type": action_type}
        if action_type == "insert":
            data.update({"text": self.insert_text.get("1.0", "end-1c").strip(),
                         "text_type": self.insert_type_var.get(), "highlight": bool(self.highlight_var.get()),
                         "delayed": bool(self.delayed_var.get())})
        elif action_type == "option":
            data["options"] = [{"id": i, "prompt": entry.get().strip()}
                               for i, entry in enumerate(self.option_entries) if entry.get().strip()]
        elif action_type == "effect":
            data.update(self._collect_effect_overrides())
            data["duration"] = self.effect_duration_var.get().strip()
        elif action_type == "fx":
            data.update({"fx": self._fx_label_to_key(self.fx_var.get()),
                         "duration": self.fx_duration_var.get().strip(),
                         "intensity": self.fx_intensity_var.get().strip()})
        elif action_type == "goto":
            data["chapter"] = self._goto_label_to_scope(self.goto_var.get())
        elif action_type == "mini_game":
            data["game"] = self.minigame_game_var.get().strip() or MINI_GAME_DEFAULT_ID
            self._minigame_params_to_data(data)
        return data

    def _refresh_action_ui(self, action_type, action_data):
        """按动作类型切换到对应表单并回填数据。"""
        action_data = action_data or {}
        self._show_action_frame(action_type or "none")
        if action_type == "insert":
            self.insert_text.delete("1.0", "end")
            self.insert_text.insert("1.0", action_data.get("text", ""))
            self.insert_type_var.set(action_data.get("text_type") or "background")
            self.highlight_var.set(bool(action_data.get("highlight", False)))
            self.delayed_var.set(bool(action_data.get("delayed", False)))
        elif action_type == "option":
            for entry in self.option_entries:
                entry.delete(0, "end")
            for entry, opt in zip(self.option_entries, action_data.get("options", [])):
                entry.insert(0, opt.get("prompt", ""))
        elif action_type == "effect":
            self.effect_duration_var.set(str(action_data.get("duration", 1)))
            self._load_effect_overrides(action_data)
        elif action_type == "fx":
            self.fx_var.set(self._fx_key_to_label(action_data.get("fx") or ""))
            self.fx_duration_var.set(str(action_data.get("duration", 0.8)))
            self.fx_intensity_var.set(str(action_data.get("intensity", 60)))
        elif action_type == "goto":
            scope = action_data.get("chapter") or ""
            if scope and scope not in self.goto_values:
                # 目标章节已被删除：补进列表，避免直接改写既有配置
                self.goto_values.append(scope)
                self.goto_var.set(scope)
            else:
                self.goto_var.set(self._goto_scope_to_label(scope))
        elif action_type == "mini_game":
            game_id = str(action_data.get("game") or MINI_GAME_DEFAULT_ID)
            self.minigame_game_var.set(game_id)
            self._rebuild_minigame_params(action_data)
        self._update_action_description()

    def _is_fixed_scope(self) -> bool:
        """当前「所在章节」是不是固定文本章节。"""
        return (self._chapter_label_to_scope(self.chapter_var.get())
                in getattr(self, "fixed_chapter_names", set()))

    def _refresh_action_availability(self):
        """所在章节变化后刷新动作类型说明。

        固定文本章节内「插入文本」与文本流冲突、运行时被跳过；动作类型改成
        下拉后无法单独禁用其中一项，改为在说明里给出警示（见 _update_action_description）。
        """
        self._update_action_description()

    def _update_action_description(self):
        """只显示当前动作类型的要点（2~3 行，固定区域）。"""
        title = action_label(self.current_action_type)
        description = self.ACTION_DESCRIPTIONS.get(self.current_action_type, "")
        text = f"{title}：{description}" if title else ""
        if self.current_action_type == "insert" and self._is_fixed_scope():
            text += ("\n⚠ 所在章节是固定文本章节，与文本流是两套推进模型，"
                     "插入文本运行时会被跳过。")
        self.action_description_label.configure(text=text)

    def _effect_template(self):
        self._set_action_template("effect", "新效果覆盖",
                                  {"filter": VISUAL_FILTER_KEYS[0], "duration": 1})

    def _fx_template(self):
        self._set_action_template("fx", "新窗口视效",
                                  {"fx": WINDOW_FX_KEYS[0], "duration": 0.8, "intensity": 60})

    def _goto_template(self):
        default_target = self.chapter_names[0] if self.chapter_names else ""
        self._set_action_template("goto", "新跳转", {"chapter": default_target})

    def _mini_game_template(self):
        self._set_action_template("mini_game", "新小游戏", {
            "game": MINI_GAME_DEFAULT_ID, "target_level": 3,
        })

    def _insert_template(self):
        self._set_action_template("insert", "新插入",
                                  {"text": "", "text_type": "background",
                                   "highlight": False, "delayed": False})

    def _option_template(self):
        self._set_action_template("option", "新选项",
                                  {"prompt": "",
                                   "options": [{"id": 0, "prompt": ""}, {"id": 1, "prompt": ""}]})

    # ---------- 确定 ----------
    def _ok(self):
        name = self.name_var.get().strip()
        if not name:
            ui.ctk.dialogs.showerror("错误", "名称不能为空")
            return

        # 校验名称唯一性（排除自身）
        original_name = self.trigger.get("name", "")
        for t in self.all_triggers:
            if t.get("name") == name and t.get("name") != original_name:
                ui.ctk.dialogs.showerror("错误", f"名称 '{name}' 已被其他触发器使用")
                return

        # 解析前置条件名称
        pre_names = []
        if self.pre_var.get().strip():
            for part in self.pre_var.get().split(','):
                part = part.strip()
                if part:
                    pre_names.append(part)

        # 校验前置条件是否存在，并检查是否把自己当作前置条件
        existing_names = {t["name"] for t in self.all_triggers if t.get("name") != name}
        for pn in pre_names:
            if pn not in existing_names and pn != name:
                ui.ctk.dialogs.showerror("错误", f"前置条件名称 '{pn}' 不存在")
                return
            if pn == name:
                ui.ctk.dialogs.showerror("错误", "触发器不能将自身作为前置条件")
                return

        try:
            condition = self._collect_condition()
            action_type = self.current_action_type
            action_data = self._collect_action_data()
        except ValueError as exc:
            ui.ctk.dialogs.showerror("输入有误", str(exc))
            return

        # 所在章节：空值=任意章节，CHAPTER_NONE=仅无章节，否则必须是已有章节
        chapter_scope = self._chapter_label_to_scope(self.chapter_var.get())
        if chapter_scope not in (CHAPTER_ANY, CHAPTER_NONE) \
                and chapter_scope not in self.chapter_names:
            ui.ctk.dialogs.showerror("错误", f"所在章节 '{chapter_scope}' 不存在")
            return

        # 各动作类型的字段已在 _collect_action_data 收集好；这里只做校验与规范化。
        if action_type == "effect":
            try:
                duration = int(float(action_data.get("duration", 1)))
            except (TypeError, ValueError):
                ui.ctk.dialogs.showerror("错误", "章节效果覆盖的持续步数必须是数字")
                return
            if duration < 1:
                ui.ctk.dialogs.showerror("错误", "章节效果覆盖的持续步数至少为 1")
                return
            if not any(key in action_data for key in
                       ("filter", "bgm_level", "voice_fx", "sensitivity")):
                ui.ctk.dialogs.showerror(
                    "错误", "请至少勾选一个要覆盖的维度"
                            "（背景滤镜 / 音乐音量 / 语音效果 / 敏感效果）")
                return
            action_data["duration"] = duration

        if action_type == "fx":
            try:
                fx_duration = float(action_data.get("duration", 0.8))
                fx_intensity = float(action_data.get("intensity", 60))
            except (TypeError, ValueError):
                ui.ctk.dialogs.showerror("错误", "视效的持续秒数与强度必须是数字")
                return
            if not 0.1 <= fx_duration <= 10:
                ui.ctk.dialogs.showerror("错误", "视效的持续秒数必须在 0.1~10 之间")
                return
            if not 0 <= fx_intensity <= 100:
                ui.ctk.dialogs.showerror("错误", "视效的强度必须在 0~100 之间")
                return
            action_data["duration"] = fx_duration
            action_data["intensity"] = fx_intensity

        if action_type == "goto":
            target = action_data.get("chapter") or ""
            if target and target not in self.chapter_names:
                ui.ctk.dialogs.showerror(
                    "错误", f"目标章节 '{target}' 不存在，请先在「章节」页签添加")
                return
            if target and target == chapter_scope:
                ui.ctk.dialogs.showerror(
                    "错误", "目标章节与所在章节相同，触发器触发后会立即被跳过")
                return

        # 验证 condition 结构
        if not isinstance(condition, dict):
            ui.ctk.dialogs.showerror("错误", "condition 必须是字典")
            return
        rules = condition.get("rules", [])
        if not isinstance(rules, list):
            ui.ctk.dialogs.showerror("错误", "condition.rules 必须是列表")
            return
        allowed_keys = set(self.evolution_names) | {"介入度", "破坏性", "总伤亡", "总计数",
                                                   "间隔计数", "节内计数", "伤亡数组"}
        # 维护选择数组（整数记录）的触发器：选项分支 + 小游戏（返回值=达成关数）
        allowed_keys |= {f"选择:{t['name']}" for t in self.all_triggers
                         if t.get("name") and t.get("action_type") in ("option", "mini_game")}
        if action_type in ("option", "mini_game"):
            allowed_keys.add(f"选择:{name}")
        allowed_comparators = {">=", "<=", ">", "<", "==", "!="}
        for rule in rules:
            if not isinstance(rule, dict):
                ui.ctk.dialogs.showerror("错误", "规则项必须是字典")
                return
            key = rule.get("key")
            if not key or key not in allowed_keys:
                ui.ctk.dialogs.showerror("错误", f"规则中的 key '{key}' 不在允许列表中")
                return
            if rule.get("comparator", ">=") not in allowed_comparators:
                ui.ctk.dialogs.showerror("错误", "规则中的 comparator 不受支持")
                return
            metric = rule.get("metric", "count")
            if key.startswith("选择:"):
                allowed_metrics = {"count", "ratio", "trend", "last"}
            else:
                allowed_metrics = {"count", "avg", "trend", "last"}
            if metric not in allowed_metrics:
                ui.ctk.dialogs.showerror("错误", f"规则中的 metric '{metric}' 不受支持")
                return
            if key.startswith("选择:") and metric in ("ratio", "trend", "last"):
                try:
                    target = rule.get("target")
                    if target is None or int(target) < 0:
                        raise ValueError
                except (TypeError, ValueError):
                    ui.ctk.dialogs.showerror("错误", f"规则 {key} 的 metric='{metric}' 需要非负整数 target（目标选项编号）")
                    return
                if metric == "ratio":
                    try:
                        ratio_value = float(rule.get("value", 0))
                    except (TypeError, ValueError):
                        ratio_value = -1.0
                    if not 0.0 <= ratio_value <= 1.0:
                        ui.ctk.dialogs.showerror("错误", f"规则 {key} 的 ratio 指标 value 必须在 0~1 之间")
                        return
            if metric == "trend" and rule.get("window") is not None:
                try:
                    if int(rule["window"]) < 2:
                        raise ValueError
                except (TypeError, ValueError):
                    ui.ctk.dialogs.showerror("错误", f"规则 {key} 的 trend 指标 window 必须是 >=2 的整数")
                    return

        # 验证 action_data
        if not isinstance(action_data, dict):
            ui.ctk.dialogs.showerror("错误", "action_data 必须是字典")
            return
        evo_dir = action_data.get("evolution_directions")
        if evo_dir is not None:
            if not isinstance(evo_dir, dict):
                ui.ctk.dialogs.showerror("错误", "evolution_directions 必须是字典")
                return
            for evo_name, delta in evo_dir.items():
                if evo_name not in self.evolution_names:
                    ui.ctk.dialogs.showerror("错误", f"演化量 '{evo_name}' 不存在")
                    return
                if not isinstance(delta, (int, float)):
                    ui.ctk.dialogs.showerror("错误", f"演化量 '{evo_name}' 的变化值必须是数字")
                    return

        # 特定类型验证
        if action_type == "insert" and not action_data.get("text"):
            ui.ctk.dialogs.showerror("错误", "插入动作必须填写插入段落文本")
            return
        if action_type == "option":
            options = action_data.get("options")
            if not isinstance(options, list) or len(options) < 2:
                ui.ctk.dialogs.showerror("错误", "选项动作至少需要 2 个选项")
                return

        self.result = {
            "name": name,
            "chapter": chapter_scope,
            "condition": condition,
            "precondition_names": pre_names,
            "action_type": action_type,
            "action_data": action_data,
            "repeatable": bool(self.repeatable_var.get()),
            "immutable": self.trigger.get("immutable", False)
        }
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()
