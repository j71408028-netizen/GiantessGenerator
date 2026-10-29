import re
import tkinter as tk

import customtkinter as ctk

import ui.ctk.dialogs
from dungeon.actions import VISUAL_FILTERS
from dungeon.audio import buses
from dungeon.audio.chapters import (
    CHAPTER_COLOR_PRESETS, DEFAULT_BGM_FADE_SECONDS,
    DEFAULT_MAX_PARAGRAPHS, DEFAULT_SENSITIVITY_ATTR, bgm_level,
    chapter_names, normalize_chapter,
)
from dungeon.audio.preview import VoicePreview
from dungeon.audio.speech import DEFAULT_RATE, normalize_voice
from dungeon.audio.voice_fx import (DEFAULT_INTENSITY, MAX_INTENSITY, PRESET_NONE,
                              SLOT_HER, SLOT_LABELS, SLOTS, label_of,
                              preset_keys_for)
from dungeon.script_text import parse_script
from ui.ctk.dialogs import BaseDialog
from ui.ctk.widgets import CollapsibleBlock, OptionButton
from ui.ctk.theme import (
    CHAPTER_ACCENT, CHAPTER_BORDER, CHAPTER_CARD_BG, CHAPTER_CARD_BORDER,
    CHAPTER_CHECK_BG, CHAPTER_CHECK_BG_HOVER, CHAPTER_ERR, CHAPTER_ERR_HOVER,
    CHAPTER_FIELD_BG, CHAPTER_FIELD_BORDER, CHAPTER_FIELD_HOVER, CHAPTER_HINT,
    CHAPTER_LABEL, CHAPTER_SWATCH_SELECTED_BORDER, CHAPTER_TEXT,
    CHAPTER_TEXT_SOFT, WIDGET_HOVER, WIDGET_TITLE,
)
from ui.scenario.asset_import import (import_background_image,
                                      import_bgm_audio, import_ending_icon,
                                      import_script_text, read_script_text,
                                      suggest_script_path)
from ui.scenario.text_editor_dlg import ScriptTextDialog

NO_FILTER_LABEL = "（不使用滤镜）"
OVERFLOW_LEAVE_LABEL = "（离开章节）"


class ChapterEditDialog(BaseDialog):
    """章节编辑对话框。

    章节没有条件字段：进入与离开都由触发器的「跳转章节」动作执行。
    这里只编辑“身处其中时的环境”——特定背景、背景音乐、对话语音的物理效果与
    持续敏感效果，外加编辑器配色。

    版面沿用触发器编辑对话框确立的风格：底部按钮固定，整张表单（基础信息、
    结束结算与章节环境）一起滚动；章节环境内各项按折叠块分组，每个折叠块
    各自成卡，使较长的表单在小屏上也能一眼看全、按需展开。

    表单按「标签列 / 控件列」对齐：基础信息与结束结算用四栏网格（一行两项），
    章节环境各折叠卡片用两栏网格（一行一项），且各卡片共用同一标签列宽度，
    使控件列起点跨卡片对齐。
    """

    #: 对话框固定尺寸（逻辑像素）：对齐触发器编辑对话框
    DIALOG_WIDTH = 580
    DIALOG_HEIGHT = 640
    DIALOG_MIN_WIDTH = 500
    DIALOG_MIN_HEIGHT = 540

    #: 章节环境各卡片的标签列固定宽度：卡片各自成网格，靠固定宽度对齐控件列起点
    FORM_LABEL_WIDTH = 105

    #: 章节角色（三选一，起始 / 结束互斥），编辑器不再支持同一章节兼任两者
    ROLE_NORMAL = "普通章节"
    ROLE_START = "起始章节"
    ROLE_ENDING = "结束章节"
    ROLE_LABELS = (ROLE_NORMAL, ROLE_START, ROLE_ENDING)

    def __init__(self, parent, chapter: dict = None, scenario_repo=None, scenario_id=None,
                 evolution_attrs=None, all_chapters=None, is_new: bool = True):
        super().__init__(parent)
        self.title("编辑章节")
        self.geometry(f"{self.DIALOG_WIDTH}x{self.DIALOG_HEIGHT}")
        self.minsize(self.DIALOG_MIN_WIDTH, self.DIALOG_MIN_HEIGHT)
        self.resizable(True, True)
        self.chapter = chapter if chapter is not None else {}
        # 新建时环境各折叠块按默认展开；编辑已有章节时一律收起，先看全貌
        self.is_new = is_new
        self._scenario_repo = scenario_repo
        self.scenario_id = scenario_id
        self.all_chapters = all_chapters or []
        self.result = None
        self.evolution_attrs = [a for a in (evolution_attrs or []) if isinstance(a, dict)]
        # 敏感效果只针对介入度、破坏性与自定义属性，总伤亡不在演化对象内
        self.sens_targets = [a["name"] for a in self.evolution_attrs
                             if a.get("name") and a.get("type") != "casualty"] \
            or ["介入度", "破坏性"]
        self.sens_rows = []
        self._swatch_buttons = {}
        # 预听：懒建（第一次点「预听」才起播放线程），关对话框时收尾
        self._preview = None
        self._preview_log = []
        self.transient(parent)
        # 抓取延到窗口可见之后（X11 要求 viewable，见 BaseDialog._grab_deferred）
        self._grab_deferred()
        self._build_ui()
        self._center_dialog(parent)
        self.wait_window()

    # ---------- 构建 ----------
    def _build_ui(self):
        main = ctk.CTkFrame(self, fg_color="transparent")
        # 表单为主，边距收紧让滚动区更贴近对话框边缘
        main.pack(fill='both', expand=True, padx=8, pady=8)
        section_label = dict(font=self.SECTION_FONT, anchor='w')

        # 确定/取消：先按 side='bottom' 占位，任何布局下都留在可视区
        btn_frame = ctk.CTkFrame(main, fg_color="transparent")
        btn_frame.pack(side='bottom', pady=(10, 0))
        self._make_std_button(btn_frame, "确定", True, self._ok).pack(side='left', padx=6)
        self._make_std_button(btn_frame, "取消", False, self._cancel).pack(side='left', padx=6)

        # ---------- 整张表单一起滚动（基础信息 + 结束结算 + 章节环境）----------
        self.form_scroll = ctk.CTkScrollableFrame(main, fg_color="transparent")
        # 滚动条收窄（默认 16），少占横向空间
        self.form_scroll._scrollbar.configure(width=12)
        self.form_scroll.pack(side='top', fill='both', expand=True)
        form = ctk.CTkFrame(self.form_scroll, fg_color="transparent")
        form.pack(fill='x')

        # ---------- 基础信息 ----------
        ctk.CTkLabel(form, text="基础信息", **section_label).pack(
            side='top', fill='x', pady=(0, 4))
        common = ctk.CTkFrame(form, fg_color="transparent")
        common.pack(side='top', fill='x', padx=6, pady=(0, 6))
        self._build_basic_info(common)

        # ---------- 结束章节结算（仅结束章节时展开）----------
        # 展开时按 side='top' 占位，排在章节环境之前
        self.ending_frame = ctk.CTkFrame(form, fg_color="transparent")
        self._build_ending_fields(self.ending_frame)

        # ---------- 章节环境（各折叠块自成卡片，随整张表单滚动）----------
        self.env_head = ctk.CTkLabel(form, text="章节环境", **section_label)
        self.env_head.pack(side='top', fill='x', pady=(6, 4))
        self.env_area = ctk.CTkFrame(form, fg_color="transparent")
        self.env_area.pack(side='top', fill='x', padx=6, pady=(0, 6))
        self._build_env_sections()
        self._toggle_ending_ui()

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Control-s>", lambda _e: self._ok())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

    # ---------- 表单排版（标签列 + 控件列）----------
    def _form_grid(self, frame):
        """把 frame 排成两列：0 = 标签列（固定宽），1 = 控件列（吃掉剩余宽度）。"""
        frame.grid_columnconfigure(1, weight=1)
        return frame

    def _form_row(self, parent, row, text, fill=False):
        """一行：标签在左，控件容器在右（fill=True 时容器拉满控件列）。"""
        ctk.CTkLabel(parent, text=text, anchor='w', font=self.UI_FONT,
                     text_color=CHAPTER_LABEL, width=self.FORM_LABEL_WIDTH).grid(
                         row=row, column=0, sticky='w', padx=(10, 12), pady=(0, 8))
        control = ctk.CTkFrame(parent, fg_color="transparent")
        control.grid(row=row, column=1, sticky='ew' if fill else 'e',
                     padx=(0, 10), pady=(0, 8))
        if fill:
            control.grid_columnconfigure(0, weight=1)
        return control

    def _option_field(self, parent, values, variable=None, command=None, width=150):
        """表单下拉统一用 OptionButton + 输入底色。"""
        return OptionButton(
            parent, values=values, variable=variable, command=command,
            width=width, height=28, corner_radius=7, font=self.UI_FONT,
            fg_color=CHAPTER_FIELD_BG, border_color=CHAPTER_FIELD_BORDER,
            hover_color=CHAPTER_FIELD_HOVER, text_color=CHAPTER_LABEL,
            text_color_disabled=CHAPTER_HINT,
            popup_fg_color=CHAPTER_FIELD_BG, popup_hover_color=CHAPTER_FIELD_HOVER,
            popup_text_color=CHAPTER_LABEL, popup_border_color=CHAPTER_FIELD_BORDER)

    def _entry_field(self, parent, variable=None, width=110, placeholder=""):
        return ctk.CTkEntry(
            parent, textvariable=variable, width=width, height=28,
            font=self.UI_FONT, placeholder_text=placeholder, border_width=1,
            border_color=CHAPTER_FIELD_BORDER, fg_color=CHAPTER_FIELD_BG,
            corner_radius=7)

    def _label_field(self, parent, text="", width=None):
        return ctk.CTkLabel(parent, text=text, font=self.UI_FONT,
                            text_color=CHAPTER_LABEL, anchor='w', width=width or 0)

    def _checkbox_field(self, parent, text, variable, command=None):
        return ctk.CTkCheckBox(parent, text=text, variable=variable,
                               width=100 if text else 24,
                               font=self.UI_FONT, text_color=CHAPTER_TEXT,
                               fg_color=CHAPTER_CHECK_BG,
                               hover_color=CHAPTER_CHECK_BG_HOVER,
                               checkbox_width=20, checkbox_height=20,
                               command=command)

    def _small_button(self, parent, text, command, width=76, danger=False):
        """卡片内的次要动作按钮（描边透明底）。danger=True 用警示色描边。"""
        return ctk.CTkButton(
            parent, text=text, width=width, height=28, font=self.UI_FONT,
            fg_color="transparent", border_width=1, corner_radius=8,
            text_color=CHAPTER_HINT,
            hover_color=CHAPTER_ERR_HOVER if danger else CHAPTER_FIELD_HOVER,
            border_color=CHAPTER_ERR if danger else CHAPTER_FIELD_BORDER,
            command=command)

    # ---------- 基础信息（固定区域）----------
    def _basic_label(self, parent, row, column, text):
        ctk.CTkLabel(parent, text=text, anchor='w', font=self.UI_FONT,
                     text_color=CHAPTER_LABEL).grid(
                         row=row, column=column, sticky='w',
                         padx=(12, 8) if column == 0 else (0, 8), pady=4)

    def _build_basic_info(self, common):
        common.grid_columnconfigure(4, weight=1)

        # 行 0：名称 + 章节角色（普通 / 起始 / 结束，三选一）
        self._basic_label(common, 0, 0, "名称")
        self.name_var = tk.StringVar(value=self.chapter.get("name", ""))
        self._entry_field(common, self.name_var, width=180).grid(
            row=0, column=1, sticky='w', padx=(0, 20), pady=4)
        self._basic_label(common, 0, 2, "章节角色")
        self.role_var = tk.StringVar(value=self._role_from_chapter())
        self.role_combo = self._option_field(
            common, list(self.ROLE_LABELS), variable=self.role_var,
            command=self._toggle_ending_ui, width=150)
        self.role_combo.grid(row=0, column=3, sticky='w', pady=4)

        # 行 1：配色（预设色板 + 手填 hex）
        self._basic_label(common, 1, 0, "配色")
        self.color_var = tk.StringVar(value=self.chapter.get("color") or CHAPTER_COLOR_PRESETS[0])
        swatch_row = ctk.CTkFrame(common, fg_color="transparent")
        swatch_row.grid(row=1, column=1, columnspan=3, sticky='w', pady=4)
        for preset in CHAPTER_COLOR_PRESETS:
            button = ctk.CTkButton(swatch_row, text="", width=24, height=24, corner_radius=6,
                                   fg_color=preset, hover_color=preset, border_width=2,
                                   border_color=CHAPTER_BORDER,
                                   command=lambda c=preset: self._select_color(c))
            button.pack(side='left', padx=2)
            self._swatch_buttons[preset.lower()] = button
        self.color_entry = self._entry_field(swatch_row, self.color_var, width=96)
        self.color_entry.pack(side='left', padx=(10, 0))
        self.color_var.trace_add("write", lambda *_a: self._refresh_swatches())
        self._refresh_swatches()

        # 行 2：说明
        self._basic_label(common, 2, 0, "说明")
        self.note_var = tk.StringVar(value=self.chapter.get("note", ""))
        self._entry_field(common, self.note_var, width=380).grid(
            row=2, column=1, columnspan=3, sticky='w', padx=(0, 20), pady=4)

        # 行 3：最大段落数 + 超限跳转
        self._basic_label(common, 3, 0, "最大段落数")
        self.max_paragraphs_var = tk.StringVar(
            value=str(self.chapter.get("max_paragraphs", DEFAULT_MAX_PARAGRAPHS)))
        self._entry_field(common, self.max_paragraphs_var, width=80).grid(
            row=3, column=1, sticky='w', padx=(0, 20), pady=4)
        self._basic_label(common, 3, 2, "超限跳转")
        self.overflow_var = tk.StringVar(
            value=self.chapter.get("overflow_target") or OVERFLOW_LEAVE_LABEL)
        other_chapters = [n for n in chapter_names(self.all_chapters)
                          if n != self.chapter.get("name")]
        self.overflow_combo = self._option_field(
            common, [OVERFLOW_LEAVE_LABEL] + other_chapters,
            variable=self.overflow_var, width=150)
        self.overflow_combo.grid(row=3, column=3, sticky='w', pady=4)

    def _role_from_chapter(self) -> str:
        if self.chapter.get("ending"):
            return self.ROLE_ENDING
        if self.chapter.get("start"):
            return self.ROLE_START
        return self.ROLE_NORMAL

    def _is_ending(self) -> bool:
        return self.role_var.get() == self.ROLE_ENDING

    # ---------- 章节环境（卡片内分组）----------
    def _build_env_sections(self):
        area = self.env_area
        self._section_blocks = []
        # 编辑已有章节时全部收起；新建时除固定文本外默认展开
        default_open = self.is_new

        # 固定文本：不进 AI 生成，默认收起（多数章节用不到）
        block = self._add_section(area, "固定文本", expanded=False,
                                  status=self._fixed_text_status)
        self._build_script_section(block.body)
        self._refresh_section_status(block)

        # 特定背景
        block = self._add_section(area, "特定背景", expanded=default_open,
                                  status=self._background_status)
        self._build_background_section(block.body)
        self._refresh_section_status(block)

        # 背景音乐
        block = self._add_section(area, "背景音乐", expanded=default_open,
                                  status=self._bgm_status)
        self._build_bgm_section(block.body)
        self._refresh_section_status(block)

        # 对话语音效果
        block = self._add_section(area, "对话语音效果", expanded=default_open,
                                  status=self._voice_fx_status)
        self._build_voice_fx_rows(block.body)
        self._refresh_section_status(block)

        # 持续敏感效果
        block = self._add_section(area, "持续敏感效果", expanded=default_open,
                                  status=self._sensitivity_status)
        self._build_sensitivity_section(block.body)
        self._refresh_section_status(block)

    def _add_section(self, parent, title, expanded=True, status=None):
        """每个折叠块自成一张卡：折叠时显示「标题行 + 状态行」，展开后只留标题行。

        ``status`` 是返回该小节配置摘要的无参回调：返回非空字符串表示已配置，
        状态行用主题强调色显示这段摘要；返回空串表示未配置，状态行置灰。
        标题行整行高亮（WIDGET_HOVER）且文字恒用常规标题色，配置与否只看状态行。
        """
        card = ctk.CTkFrame(parent, fg_color=CHAPTER_CARD_BG, border_width=1,
                            border_color=CHAPTER_CARD_BORDER, corner_radius=8)
        card.pack(fill='x', pady=5)
        block = CollapsibleBlock(card, title, expanded=expanded,
                                 header_parent=card,
                                 body_padx=(12, 12), body_pady=(8, 6))
        # 收起/展开后刷新状态行（回调在状态翻转后触发）
        block._hint_configured = status
        block._on_toggle_cb = lambda: self._refresh_section_status(block)
        # 标题行融进卡片：整行高亮底色，去掉自身描边，文字不随配置变色
        block.header.configure(fg_color=WIDGET_HOVER, hover_color=WIDGET_HOVER,
                               text_color=WIDGET_TITLE, border_width=0)
        block.body.configure(fg_color="transparent")
        block.header.pack(fill='x', padx=4, pady=(4, 0))
        # 状态行：配置摘要行，仅折叠时显示（展开后由 _lay_out_section 隐藏）
        status_label = ctk.CTkLabel(card, text="", anchor='w', justify='left',
                                    wraplength=520, font=self.UI_FONT,
                                    text_color=CHAPTER_HINT)
        status_label.pack(fill='x', padx=14, pady=(1, 4))
        block._status_label = status_label
        # 展开时正文排在状态行之后（toggle 也按此重排），随后 _lay_out_section
        # 收起状态行、把正文挪到标题行之下
        block._body_after = status_label
        self._section_blocks.append(block)
        self._lay_out_section(block)
        return block

    def _refresh_section_status(self, block):
        """刷新状态行：已配置显示主题色摘要，未配置显示灰色「未配置」。"""
        label = getattr(block, "_status_label", None)
        if label is None:
            return
        provider = getattr(block, "_hint_configured", None)
        text = provider() if callable(provider) else ""
        if text:
            label.configure(text=text, text_color=CHAPTER_ACCENT)
        else:
            label.configure(text="（未配置）", text_color=CHAPTER_HINT)
        self._lay_out_section(block)

    def _lay_out_section(self, block):
        """状态行只在折叠时显示；展开后隐藏，正文紧跟在标题行之下。"""
        label = block._status_label
        packed = bool(label.winfo_manager())
        if block._expanded:
            if packed:
                label.pack_forget()
                block.body.pack(fill='x', padx=block._body_padx,
                                pady=block._body_pady, after=block.header)
        elif not packed:
            label.pack(fill='x', padx=14, pady=(1, 4), after=block.header)

    def _refresh_all_section_status(self):
        """任一配置字段变化时，让各卡片的状态行同步刷新。"""
        for block in getattr(self, "_section_blocks", []):
            self._refresh_section_status(block)

    @staticmethod
    def _brief_name(path: str) -> str:
        """从相对/绝对路径取文件名，仅用于状态行摘要。"""
        return (path or "").replace("\\", "/").rsplit("/", 1)[-1]

    # ---------- 折叠块「已配置」摘要 ----------
    def _fixed_text_status(self) -> str:
        rel = self._script_rel_path()
        return f"固定文本：{self._brief_name(rel)}" if rel else ""

    def _background_status(self) -> str:
        path = self.background_path_var.get().strip()
        if not path:
            return ""
        parts = [self._brief_name(path)]
        filter_label = self.filter_var.get()
        if filter_label and filter_label != NO_FILTER_LABEL:
            parts.append(filter_label)
        return "特定背景：" + " · ".join(parts)

    def _bgm_status(self) -> str:
        path = self.bgm_path_var.get().strip()
        if not path:
            return ""
        parts = [self._brief_name(path), self.bgm_level_var.get()]
        if self.bgm_loop_var.get():
            parts.append("循环")
        return "背景音乐：" + " · ".join(parts)

    def _voice_fx_status(self) -> str:
        fx = self._voice_fx_from_ui()
        if not fx:
            return ""
        parts = [f"{SLOT_LABELS[slot]}：{label_of(fx[slot]['preset'])}"
                 for slot in SLOTS if slot in fx]
        return "对话语音：" + "、".join(parts)

    def _sensitivity_status(self) -> str:
        if not self.sens_rows:
            return ""
        attrs = [row["attr_var"].get().strip() for row in self.sens_rows]
        attrs = [a for a in attrs if a]
        text = f"持续敏感：{len(self.sens_rows)} 条"
        return text + (f"（{'、'.join(attrs)}）" if attrs else "")

    # ---------- 固定文本 ----------
    def _build_script_section(self, parent):
        frame = self._form_grid(ctk.CTkFrame(parent, fg_color="transparent"))
        frame.pack(fill='x')

        control = self._form_row(frame, 0, "文本", fill=True)
        fixed_text = self.chapter.get("fixed_text") or {}
        self.fixed_path_var = tk.StringVar(value=fixed_text.get("path", ""))
        self._entry_field(control, self.fixed_path_var).pack(
            side='left', fill='x', expand=True, padx=(0, 6))
        self._small_button(control, "选择…", self._import_script, width=60).pack(side='left')
        self._small_button(control, "编辑", self._edit_script, width=52).pack(
            side='left', padx=(6, 0))
        self._small_button(control, "移除", self._clear_script, width=52,
                           danger=True).pack(
            side='left', padx=(6, 0))

        self.fixed_status_label = ctk.CTkLabel(
            frame, text="", anchor='w', justify='left', wraplength=520,
            font=self.UI_FONT_SMALL, text_color=CHAPTER_HINT)
        self.fixed_status_label.grid(row=1, column=0, columnspan=2, sticky='w',
                                     padx=10, pady=(0, 8))
        self.fixed_path_var.trace_add("write", self._on_fixed_path_changed)
        self._refresh_script_status()

    def _on_fixed_path_changed(self, *_a):
        self._refresh_script_status()
        self._refresh_all_section_status()

    # ---------- 特定背景 ----------
    def _build_background_section(self, parent):
        background = self.chapter.get("background") or {}
        frame = self._form_grid(ctk.CTkFrame(parent, fg_color="transparent"))
        frame.pack(fill='x')

        control = self._form_row(frame, 0, "图片", fill=True)
        self.background_path_var = tk.StringVar(value=background.get("image_path", ""))
        self.background_path_var.trace_add(
            "write", lambda *_a: self._refresh_all_section_status())
        self._entry_field(control, self.background_path_var).pack(
            side='left', fill='x', expand=True, padx=(0, 6))
        self._small_button(control, "选择…", self._import_background, width=60).pack(side='left')
        self._small_button(control, "移除",
                           lambda: self.background_path_var.set(""), width=52,
                           danger=True).pack(
                               side='left', padx=(6, 0))

        option = self._form_row(frame, 1, "平滑切换")
        self.smooth_var = tk.BooleanVar(value=bool(background.get("smooth_transition", True)))
        self._checkbox_field(option, "", self.smooth_var).pack(side='left')

        filter_row = self._form_row(frame, 2, "滤镜")
        self.filter_labels = [NO_FILTER_LABEL] + [label for _key, label in VISUAL_FILTERS]
        current_filter = background.get("filter_effect") or ""
        self.filter_var = tk.StringVar(value=self._filter_key_to_label(current_filter))
        self.filter_var.trace_add(
            "write", lambda *_a: self._refresh_all_section_status())
        self._option_field(filter_row, self.filter_labels, variable=self.filter_var,
                           width=130).pack(side='left')

    # ---------- 背景音乐 ----------
    def _build_bgm_section(self, parent):
        bgm = self.chapter.get("bgm") or {}
        frame = self._form_grid(ctk.CTkFrame(parent, fg_color="transparent"))
        frame.pack(fill='x')

        control = self._form_row(frame, 0, "音频", fill=True)
        self.bgm_path_var = tk.StringVar(value=bgm.get("path", ""))
        self.bgm_path_var.trace_add(
            "write", lambda *_a: self._refresh_all_section_status())
        self._entry_field(control, self.bgm_path_var).pack(
            side='left', fill='x', expand=True, padx=(0, 6))
        self._small_button(control, "选择…", self._import_bgm, width=60).pack(side='left')
        self._small_button(control, "移除", lambda: self.bgm_path_var.set(""),
                           width=52, danger=True).pack(side='left', padx=(6, 0))

        option = self._form_row(frame, 1, "音量")
        self.bgm_level_var = tk.StringVar(value=buses.step_display(bgm_level(bgm)))
        self.bgm_level_var.trace_add(
            "write", lambda *_a: self._refresh_all_section_status())
        self._option_field(option, [label for label, _ in buses.step_choices()],
                           variable=self.bgm_level_var, width=96).pack(side='left')

        loop = self._form_row(frame, 2, "循环播放")
        self.bgm_loop_var = tk.BooleanVar(value=bool(bgm.get("loop", True)))
        self.bgm_loop_var.trace_add(
            "write", lambda *_a: self._refresh_all_section_status())
        self._checkbox_field(loop, "", self.bgm_loop_var).pack(side='left')

        fade = self._form_row(frame, 3, "切换淡入淡出")
        self.bgm_fade_var = tk.StringVar(
            value=str(bgm.get("fade_seconds", DEFAULT_BGM_FADE_SECONDS)))
        self._entry_field(fade, self.bgm_fade_var, width=64).pack(side='left')
        ctk.CTkLabel(fade, text="秒（0 = 直接切换）", font=self.UI_FONT_SMALL,
                     text_color=CHAPTER_HINT).pack(side='left', padx=(6, 0))

    # ---------- 持续敏感效果 ----------
    def _build_sensitivity_section(self, parent):
        head = ctk.CTkFrame(parent, fg_color="transparent")
        head.pack(fill='x')
        # 提示与「添加效果」同一行：提示占左、按钮靠右
        self._empty_sens_hint = ctk.CTkLabel(
            head, justify='left', anchor='w',
            text="敏感效果倍率 = 强度 ×（性格二阶量 + 客观影响）；\n"
                 "介入度取敏感值、破坏性取重力、其余属性取策略值。",
            font=self.UI_FONT_SMALL, text_color=CHAPTER_HINT)
        self._empty_sens_hint.pack(side='left', fill='x', expand=True)
        self._small_button(head, "添加效果", self._add_sensitivity_row,
                           width=84).pack(side='right', padx=(8, 0))
        # 空容器不占位：默认 CTkFrame 高度为 200，空着会撑出一大块空白
        self.sens_container = ctk.CTkFrame(parent, fg_color="transparent", height=0)
        self.sens_container.pack(fill='x', pady=(4, 0))
        for effect in self.chapter.get("sensitivity") or []:
            if isinstance(effect, dict):
                self._add_sensitivity_row(effect)

    # ---------- 配色 ----------
    def _select_color(self, color: str):
        self.color_var.set(color)

    def _refresh_swatches(self):
        current = (self.color_var.get() or "").strip().lower()
        for preset, button in self._swatch_buttons.items():
            button.configure(border_color=CHAPTER_SWATCH_SELECTED_BORDER if preset == current else CHAPTER_BORDER)

    # ---------- 滤镜 ----------
    def _filter_key_to_label(self, key: str) -> str:
        for filter_key, label in VISUAL_FILTERS:
            if filter_key == key:
                return label
        return NO_FILTER_LABEL

    def _filter_label_to_key(self, label: str) -> str:
        for filter_key, item_label in VISUAL_FILTERS:
            if item_label == label:
                return filter_key
        return ""

    # ---------- 背景 ----------
    def _import_background(self):
        rel_path = import_background_image(self, self._scenario_repo, self.scenario_id)
        if not rel_path:
            return
        self.background_path_var.set(rel_path)
        self.smooth_var.set(True)

    # ---------- 背景音乐 ----------
    def _import_bgm(self):
        rel_path = import_bgm_audio(self, self._scenario_repo, self.scenario_id)
        if rel_path:
            self.bgm_path_var.set(rel_path)

    # ---------- 对话语音效果 ----------
    def _build_voice_fx_rows(self, parent):
        """两档（她 / 其他人）：预设下拉 + 强度滑杆 + 声明响度 + 预听。

        预设是**物理**效果（尺度 / 距离 / 空间），不是语气；按档给：她走巨大声源
        那组，主角与其余角色走普通尺度那组。高级参数（回声拍数、缓抖频率等）手改
        JSON 也能用，编辑器只暴露预设 + 强度，免得对话框变成调音台。

        「响度」是只读的**声明值**（``voice_fx.Preset.loudness_db``）：渲染时按它把
        实测响度拉回目标，所以各预设之间的响度差是设计出来的，不是调出来的——想
        知道这一档选出来会有多响，看这个标签；想听，点「预听」。
        """
        self._fx_vars = {}
        self._fx_label_keys = {}
        self._fx_preview_buttons = {}
        frame = self._form_grid(ctk.CTkFrame(parent, fg_color="transparent"))
        frame.pack(fill='x')
        for row_index, slot in enumerate(SLOTS):
            fx = (self.chapter.get("voice_fx") or {}).get(slot) or {}
            keys = preset_keys_for(slot)
            labels = [label_of(key) for key in keys]
            self._fx_label_keys[slot] = {label_of(key): key for key in keys}

            # 档名进标签列，预设 / 强度进控件列，与其它卡片共用同一分栏
            control = self._form_row(frame, row_index, SLOT_LABELS[slot])
            line = ctk.CTkFrame(control, fg_color="transparent")
            line.pack(side='top', fill='x')
            intensity_var = tk.IntVar(
                value=int(fx.get("intensity", DEFAULT_INTENSITY)))
            value_label = ctk.CTkLabel(line, text=str(intensity_var.get()),
                                       width=30, anchor='w', font=self.UI_FONT)
            value_label.pack(side='right', padx=(6, 0))
            ctk.CTkSlider(line, from_=0, to=MAX_INTENSITY,
                          number_of_steps=MAX_INTENSITY,
                          variable=intensity_var, width=110,
                          ).pack(side='right')
            preset_var = tk.StringVar(
                value=label_of(fx.get("preset") or PRESET_NONE))
            self._label_field(line, "强度").pack(side='right', padx=(12, 4))
            self._option_field(line, labels, variable=preset_var, width=150).pack(
                side='right')

            # 第二行：声明响度（只读）+ 预听
            sub = ctk.CTkFrame(control, fg_color="transparent")
            sub.pack(side='top', fill='x', pady=(2, 0))
            audition = self._small_button(
                sub, "预听", lambda s=slot: self._preview_fx(s), width=52)
            audition.pack(side='right', padx=5)

            self._fx_vars[slot] = (preset_var, intensity_var)
            self._fx_preview_buttons[slot] = audition
            intensity_var.trace_add(
                "write",
                lambda *_a, label=value_label, var=intensity_var:
                    label.configure(text=str(var.get())))
            for var in (preset_var, intensity_var):
                var.trace_add("write", lambda *_a, s=slot: self._on_fx_changed(s))

        self._preview_status = ctk.CTkLabel(
            frame, text="", anchor='w', font=self.UI_FONT_SMALL,
            text_color=CHAPTER_HINT)
        self._preview_status.grid(row=len(SLOTS), column=0, columnspan=2,
                                  sticky='w', padx=10, pady=(2, 0))

    def _on_fx_changed(self, slot):
        self._refresh_all_section_status()

    def _voice_config(self) -> dict:
        """方案级对话语音配置（预听用它取音色 / 语速 / 档位）；读不到就用默认。"""
        if self._scenario_repo is not None and self.scenario_id:
            try:
                config = self._scenario_repo.load_config(self.scenario_id) or {}
                return normalize_voice(config.get("voice"))
            except Exception:
                pass
        return normalize_voice(None)

    def _preview_fx(self, slot):
        """预听这一档当前的效果（联网合成 → 渲染/播放侧近似 → 播一次）。"""
        preset_var, intensity_var = self._fx_vars[slot]
        key = self._fx_label_keys[slot].get(preset_var.get().strip(), PRESET_NONE)
        if key == PRESET_NONE:
            self._set_preview_status("先给这一档选一个效果预设，再点预听。")
            return
        voice = self._voice_config()
        preview = self._ensure_preview()
        if preview is None:
            return
        button = self._fx_preview_buttons.get(slot)
        if button is not None:
            button.configure(state="disabled", text="…")
        self._set_preview_status(
            f"正在合成「{label_of(key)}」的预听样例…（首次约 1 秒，需联网）")
        spec = {"preset": key,
                "intensity": max(0, min(MAX_INTENSITY, int(intensity_var.get())))}
        preview.play(
            voice["voice_her"] if slot == SLOT_HER else voice["voice_protagonist"],
            slot=slot, rate=voice.get("rate", DEFAULT_RATE),
            level=voice.get("level"), effect=spec,
            on_done=lambda ok, msg, s=slot: self._on_preview_done(s, ok, msg))

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
            label.configure(text=text)
        except tk.TclError:
            pass

    def _on_preview_done(self, slot, ok, message):
        """工作线程的回调 → 回 Tk 主线程改按钮与状态（对话框可能已经关了）。"""
        try:
            self.after(0, lambda: self._finish_preview(slot, ok, message))
        except tk.TclError:
            pass

    def _finish_preview(self, slot, ok, message):
        button = self._fx_preview_buttons.get(slot)
        if button is not None:
            try:
                button.configure(state="normal", text="预听")
            except tk.TclError:
                pass
        self._set_preview_status("" if ok else f"预听失败：{message}")

    def _voice_fx_from_ui(self) -> dict:
        """收集两档效果；选了「不加效果」的那一档不写进配置。"""
        result = {}
        for slot in SLOTS:
            preset_var, intensity_var = self._fx_vars[slot]
            key = self._fx_label_keys[slot].get(preset_var.get().strip(), PRESET_NONE)
            if key == PRESET_NONE:
                continue
            result[slot] = {"preset": key,
                            "intensity": max(0, min(MAX_INTENSITY,
                                                   int(intensity_var.get())))}
        return result

    # ---------- 敏感效果 ----------
    def _add_sensitivity_row(self, effect: dict = None):
        effect = effect or {}

        row = ctk.CTkFrame(self.sens_container, fg_color="transparent")
        row.pack(fill='x', pady=3)
        attr_var = tk.StringVar(value=effect.get("attr") or self.sens_targets[0]
                                or DEFAULT_SENSITIVITY_ATTR)
        self._option_field(row, self.sens_targets, variable=attr_var,
                           width=140).pack(side='left')
        self._label_field(row, "强度").pack(side='left', padx=(12, 2))
        strength_var = tk.StringVar(value=str(effect.get("strength", 1.0)))
        self._entry_field(row, strength_var, width=64).pack(side='left')
        self._label_field(row, "客观影响").pack(side='left', padx=(12, 2))
        objective_var = tk.StringVar(value=str(effect.get("objective", 0.0)))
        self._entry_field(row, objective_var, width=64).pack(side='left')

        entry = {"frame": row, "attr_var": attr_var, "strength_var": strength_var,
                 "objective_var": objective_var}
        self._small_button(row, "移除", lambda: self._remove_sensitivity_row(entry),
                           width=52, danger=True).pack(side='left', padx=(12, 0))
        self.sens_rows.append(entry)
        self._refresh_all_section_status()

    def _remove_sensitivity_row(self, entry):
        entry["frame"].destroy()
        if entry in self.sens_rows:
            self.sens_rows.remove(entry)
        self._refresh_all_section_status()

    # ---------- 结束章节结算字段 ----------
    def _build_ending_fields(self, parent):
        ctk.CTkLabel(parent, text="结束章节结算", font=self.SECTION_FONT,
                     anchor='w').pack(fill='x', pady=(0, 2))
        # 与基础信息同一套四栏网格：标签 + 控件 ×2，一行两项
        grid = ctk.CTkFrame(parent, fg_color="transparent")
        grid.pack(fill='x', padx=6)
        grid.grid_columnconfigure(4, weight=1)

        self.ending_intrusion_var = tk.StringVar(
            value=str(self.chapter.get("intrusion_delta", 0)))
        self.ending_destruction_var = tk.StringVar(
            value=str(self.chapter.get("destruction_delta", 0)))
        self.ending_casualty_var = tk.StringVar(
            value=str(self.chapter.get("casualty_step", 0)))
        self.ending_refund_var = tk.StringVar(
            value=str(self.chapter.get("action_points_refund", 0)))

        pairs = ((0, 0, "介入度增量", self.ending_intrusion_var),
                 (0, 2, "破坏性增量", self.ending_destruction_var),
                 (1, 0, "伤亡步进", self.ending_casualty_var),
                 (1, 2, "行动点返还", self.ending_refund_var))
        for row, column, text, var in pairs:
            self._basic_label(grid, row, column, text)
            self._entry_field(grid, var, width=60).grid(
                row=row, column=column + 1, sticky='w',
                padx=(0, 20) if column == 0 else 0, pady=4)

        self._basic_label(grid, 2, 0, "结局图标")
        self.ending_icon_var = tk.StringVar(value=self.chapter.get("icon_path", ""))
        icon_row = ctk.CTkFrame(grid, fg_color="transparent")
        icon_row.grid(row=2, column=1, columnspan=4, sticky='we', pady=4)
        self._entry_field(icon_row, self.ending_icon_var).pack(
            side='left', fill='x', expand=True)
        self._small_button(icon_row, "选择…", self._import_ending_icon,
                           width=60).pack(side='left', padx=(6, 0))
        self._small_button(icon_row, "移除", lambda: self.ending_icon_var.set(""),
                           width=56, danger=True).pack(side='left', padx=(6, 0))

        ctk.CTkLabel(
            parent, justify='left', anchor='w', wraplength=520,
            font=self.UI_FONT_SMALL, text_color=CHAPTER_HINT,
            text="结束章节内段落类型固定为「结局」；触发器不能跳出，"
                 "段落数耗尽即按上方配置结算。").pack(
            fill='x', padx=6, pady=(4, 0))

    def _toggle_ending_ui(self, *_a):
        """结束章节时展开固定结算区（排在环境卡片之前，挤压其纵向空间）。"""
        if self._is_ending():
            self.ending_frame.pack(side='top', fill='x', pady=(6, 0),
                                   before=self.env_head)
            self.overflow_combo.configure(state="disabled")
        else:
            self.ending_frame.pack_forget()
            self.overflow_combo.configure(state="normal")

    def _import_ending_icon(self):
        rel = import_ending_icon(self, self._scenario_repo, self.scenario_id)
        if rel:
            self.ending_icon_var.set(rel)

    # ---------- 固定文本 ----------
    def _script_rel_path(self) -> str:
        return self.fixed_path_var.get().strip().replace("\\", "/")

    def _refresh_script_status(self):
        """解析预览：段落数 / 会演化的段数 / 首条警告（读不到文件就说清原因）。"""
        label = getattr(self, "fixed_status_label", None)
        if label is None:
            return
        rel = self._script_rel_path()
        if not rel:
            label.configure(text="未配置：本章照常由 AI 生成。", text_color=CHAPTER_HINT)
            return
        text, error = read_script_text(self._scenario_repo, self.scenario_id, rel)
        if text is None:
            label.configure(text=f"⚠ 无法读取「{rel}」：{error}", text_color=CHAPTER_ERR)
            return
        script = parse_script(text)
        evolving = sum(1 for p in script.paragraphs if p.directive.complete)
        summary = f"{len(script.paragraphs)} 个逻辑段落（每段一次步进）"
        if evolving:
            summary += f"，其中 {evolving} 段会演化"
        if script.warnings:
            summary += f"\n⚠ {script.warnings[0]}"
            if len(script.warnings) > 1:
                summary += f"（共 {len(script.warnings)} 条）"
        label.configure(text=summary, text_color=CHAPTER_HINT)

    def _import_script(self):
        rel = import_script_text(self, self._scenario_repo, self.scenario_id)
        if rel:
            self.fixed_path_var.set(rel)
            self._refresh_script_status()

    def _edit_script(self):
        rel = self._script_rel_path()
        if not rel:
            # 还没有文本：给一个不冲突的默认路径，直接进编辑器写第一版
            rel = suggest_script_path(self._scenario_repo, self.scenario_id,
                                      self.name_var.get().strip() or "chapter")
            self.fixed_path_var.set(rel)
        text, error = read_script_text(self._scenario_repo, self.scenario_id, rel)
        if text is None:
            if error != "文件不存在":
                ui.ctk.dialogs.showerror("错误", f"无法读取「{rel}」：{error}")
                return
            text = ""  # 新建：从空文本起步
        result = ScriptTextDialog(self, text=text, rel_path=rel,
                                  scenario_repo=self._scenario_repo,
                                  scenario_id=self.scenario_id).result
        if result is not None:
            self._refresh_script_status()

    def _clear_script(self):
        self.fixed_path_var.set("")
        self._refresh_script_status()

    # ---------- 确定 ----------
    def _ok(self):
        name = self.name_var.get().strip()
        if not name:
            ui.ctk.dialogs.showerror("错误", "章节名称不能为空")
            return
        original_name = self.chapter.get("name", "")
        for other in self.all_chapters:
            if other.get("name") == name and other.get("name") != original_name:
                ui.ctk.dialogs.showerror("错误", f"章节名称 '{name}' 已被使用")
                return

        color = (self.color_var.get() or "").strip()
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            ui.ctk.dialogs.showerror("错误", "配色必须是 #RRGGBB 形式的颜色值")
            return

        background = {}
        image_path = self.background_path_var.get().strip()
        filter_key = self._filter_label_to_key(self.filter_var.get())
        if image_path:
            background["image_path"] = image_path
            background["smooth_transition"] = bool(self.smooth_var.get())
            if filter_key:
                background["filter_effect"] = filter_key
        elif filter_key:
            ui.ctk.dialogs.showerror("错误", "配置了滤镜但没有选择背景图片，滤镜不会生效")
            return

        bgm = {}
        audio_path = self.bgm_path_var.get().strip()
        if audio_path:
            try:
                fade_seconds = float(self.bgm_fade_var.get())
            except ValueError:
                ui.ctk.dialogs.showerror("错误", "背景音乐的淡入淡出秒数必须是数字")
                return
            if fade_seconds < 0:
                ui.ctk.dialogs.showerror("错误", "背景音乐的淡入淡出秒数不能为负")
                return
            bgm = {
                "path": audio_path,
                "level": buses.step_from_display(self.bgm_level_var.get()),
                "loop": bool(self.bgm_loop_var.get()),
                "fade_seconds": fade_seconds,
            }

        sensitivity = []
        for index, row in enumerate(self.sens_rows, start=1):
            attr = row["attr_var"].get().strip()
            if not attr:
                ui.ctk.dialogs.showerror("错误", f"第 {index} 条敏感效果没有选择属性")
                return
            try:
                strength = float(row["strength_var"].get())
            except ValueError:
                ui.ctk.dialogs.showerror("错误", f"第 {index} 条敏感效果的强度必须是数字")
                return
            try:
                objective = float(row["objective_var"].get())
            except ValueError:
                ui.ctk.dialogs.showerror("错误", f"第 {index} 条敏感效果的客观影响必须是数字")
                return
            sensitivity.append({"attr": attr, "strength": strength, "objective": objective})

        try:
            max_paragraphs = int(float(self.max_paragraphs_var.get()))
        except ValueError:
            ui.ctk.dialogs.showerror("错误", "最大段落数必须是数字")
            return
        if max_paragraphs < 1:
            ui.ctk.dialogs.showerror("错误", "最大段落数至少为 1")
            return

        is_ending = self._is_ending()
        overflow_target = "" if is_ending else (
            "" if self.overflow_var.get() == OVERFLOW_LEAVE_LABEL
            else self.overflow_var.get())

        fixed_text = {}
        script_rel = self._script_rel_path()
        if script_rel:
            # 存盘前先确认读得到：读不到就当场改，别留到「启动前校验」才拦
            existing, error = read_script_text(self._scenario_repo, self.scenario_id,
                                               script_rel)
            if existing is None:
                ui.ctk.dialogs.showerror("错误", f"固定文本「{script_rel}」不可读：{error}")
                return
            fixed_text = {"path": script_rel}

        result = {
            "name": name,
            "color": color,
            "start": self.role_var.get() == self.ROLE_START,
            "ending": is_ending,
            "max_paragraphs": max_paragraphs,
            "overflow_target": overflow_target,
            "background": background,
            "bgm": bgm,
            "voice_fx": self._voice_fx_from_ui(),
            "fixed_text": fixed_text,
            "sensitivity": sensitivity,
            "note": self.note_var.get().strip(),
        }
        if is_ending:
            delta_fields = {"介入度增量": "intrusion_delta",
                            "破坏性增量": "destruction_delta",
                            "伤亡步进": "casualty_step"}
            for field, var in (("介入度增量", self.ending_intrusion_var),
                               ("破坏性增量", self.ending_destruction_var),
                               ("伤亡步进", self.ending_casualty_var)):
                try:
                    result[delta_fields[field]] = float(var.get())
                except ValueError:
                    ui.ctk.dialogs.showerror("错误", f"结局结算的{field}必须是数字")
                    return
            try:
                result["action_points_refund"] = int(float(self.ending_refund_var.get()))
            except ValueError:
                ui.ctk.dialogs.showerror("错误", "结局结算的行动点数返还必须是数字")
                return
            result["icon_path"] = self.ending_icon_var.get().strip()

        # 地点/取景地址/出口地址目前只能在 config.json 里手写：编辑其他字段保存时
        # 原样带过去，避免一轮编辑把已经写好的地址悄悄抹掉（表单接入后删掉这段）。
        for key in ("place", "address", "landmarks", "exit_address"):
            if key in self.chapter:
                result[key] = self.chapter[key]

        self.result = normalize_chapter(result)
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()

    def destroy(self):
        """关对话框：先收预听（停播、收线程、清掉临时 WAV）再销毁。"""
        preview, self._preview = getattr(self, "_preview", None), None
        if preview is not None:
            try:
                preview.shutdown()
            except Exception:
                pass
        super().destroy()
