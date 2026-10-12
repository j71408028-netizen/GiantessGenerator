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
                              loudness_hint, preset_keys_for)
from ui.ctk.dialogs import BaseDialog
from ui.ctk.widgets import OptionButton
from ui.ctk.theme import (
    CHAPTER_BORDER, CHAPTER_HOVER, CHAPTER_TEXT, CHAPTER_TEXT_SOFT,
    CHAPTER_HINT, CHAPTER_ERR, CHAPTER_ERR_HOVER, CHAPTER_SWATCH_SELECTED_BORDER,
)
from ui.scenario.asset_import import (import_background_image,
                                      import_bgm_audio, import_ending_icon)

NO_FILTER_LABEL = "（不使用滤镜）"
OVERFLOW_LEAVE_LABEL = "（离开章节）"


class ChapterEditDialog(BaseDialog):
    """章节编辑对话框。

    章节没有条件字段：进入与离开都由触发器的「跳转章节」动作执行。
    这里只编辑“身处其中时的环境”——特定背景、背景音乐、对话语音的物理效果与
    持续敏感效果，外加编辑器配色。
    """

    def __init__(self, parent, chapter: dict = None, scenario_repo=None, scenario_id=None,
                 evolution_attrs=None, all_chapters=None):
        super().__init__(parent)
        self.title("编辑章节")
        self.geometry("600x900")
        self.minsize(560, 560)
        self.resizable(True, True)
        self.chapter = chapter if chapter is not None else {}
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
        main.pack(fill='both', expand=True, padx=14, pady=12)
        section_label = dict(font=self.SECTION_FONT, anchor='w')

        ctk.CTkLabel(main, text="章节信息", **section_label).pack(fill='x', pady=(0, 4))
        info = ctk.CTkFrame(main, fg_color="transparent")
        info.pack(fill='x', padx=6, pady=(0, 8))

        ctk.CTkLabel(info, text="名称:", font=self.UI_FONT).grid(row=0, column=0, sticky='w', pady=3)
        self.name_var = tk.StringVar(value=self.chapter.get("name", ""))
        ctk.CTkEntry(info, textvariable=self.name_var, width=250, height=28,
                     font=self.UI_FONT).grid(row=0, column=1, sticky='w', padx=6, pady=3)

        self.start_var = tk.BooleanVar(value=bool(self.chapter.get("start", False)))
        ctk.CTkCheckBox(info, text="起始章节（副本开始时自动进入）", variable=self.start_var,
                        font=self.UI_FONT, text_color=CHAPTER_TEXT,
                        checkbox_width=20, checkbox_height=20).grid(
                            row=0, column=2, columnspan=2, sticky='w', padx=(14, 0), pady=3)

        ctk.CTkLabel(info, text="配色:", font=self.UI_FONT).grid(row=1, column=0, sticky='wn', pady=3)
        self.color_var = tk.StringVar(value=self.chapter.get("color") or CHAPTER_COLOR_PRESETS[0])
        swatch_row = ctk.CTkFrame(info, fg_color="transparent")
        swatch_row.grid(row=1, column=1, columnspan=3, sticky='w', padx=6, pady=3)
        for preset in CHAPTER_COLOR_PRESETS:
            button = ctk.CTkButton(swatch_row, text="", width=24, height=24, corner_radius=6,
                                   fg_color=preset, hover_color=preset, border_width=2,
                                   border_color=CHAPTER_BORDER,
                                   command=lambda c=preset: self._select_color(c))
            button.pack(side='left', padx=2)
            self._swatch_buttons[preset.lower()] = button
        self.color_entry = ctk.CTkEntry(swatch_row, textvariable=self.color_var, width=96,
                                        height=28, font=self.UI_FONT)
        self.color_entry.pack(side='left', padx=(10, 0))
        self.color_var.trace_add("write", lambda *_a: self._refresh_swatches())
        self._refresh_swatches()

        ctk.CTkLabel(info, text="说明:", font=self.UI_FONT).grid(row=2, column=0, sticky='w', pady=3)
        self.note_var = tk.StringVar(value=self.chapter.get("note", ""))
        ctk.CTkEntry(info, textvariable=self.note_var, width=380, height=28,
                     font=self.UI_FONT).grid(row=2, column=1, columnspan=3,
                                             sticky='we', padx=6, pady=3)

        ctk.CTkLabel(info, text="最大段落数:", font=self.UI_FONT).grid(row=3, column=0, sticky='w', pady=3)
        self.max_paragraphs_var = tk.StringVar(
            value=str(self.chapter.get("max_paragraphs", DEFAULT_MAX_PARAGRAPHS)))
        ctk.CTkEntry(info, textvariable=self.max_paragraphs_var, width=80, height=28,
                     font=self.UI_FONT).grid(row=3, column=1, sticky='w', padx=6, pady=3)

        ctk.CTkLabel(info, text="超限跳转:", font=self.UI_FONT).grid(row=3, column=2, sticky='w', padx=(14, 0), pady=3)
        self.overflow_var = tk.StringVar(
            value=self.chapter.get("overflow_target") or OVERFLOW_LEAVE_LABEL)
        other_chapters = [n for n in chapter_names(self.all_chapters)
                          if n != self.chapter.get("name")]
        self.overflow_combo = ctk.CTkComboBox(
            info, values=[OVERFLOW_LEAVE_LABEL] + other_chapters,
            variable=self.overflow_var, state="readonly", width=150, height=28,
            font=self.UI_FONT)
        self.overflow_combo.grid(row=3, column=3, sticky='w', padx=6, pady=3)

        self.ending_var = tk.BooleanVar(value=bool(self.chapter.get("ending", False)))
        ctk.CTkCheckBox(info, text="结束章节（段落数耗尽后终止副本并生成结局）",
                        variable=self.ending_var, font=self.UI_FONT,
                        text_color=CHAPTER_TEXT, checkbox_width=20, checkbox_height=20,
                        command=self._toggle_ending_ui).grid(
                            row=4, column=0, columnspan=4, sticky='w', pady=3)
        info.grid_columnconfigure(3, weight=1)

        # ---------- 结束章节结算（仅勾选结束章节时可编辑）----------
        self.ending_frame = ctk.CTkFrame(main, fg_color="transparent")
        self._build_ending_fields(self.ending_frame)
        self._toggle_ending_ui()

        # ---------- 特定背景 ----------
        ctk.CTkLabel(main, text="特定背景（进入本章节时切换）", **section_label).pack(fill='x', pady=(4, 4))
        bg_frame = ctk.CTkFrame(main, fg_color="transparent")
        bg_frame.pack(fill='x', padx=6)

        path_row = ctk.CTkFrame(bg_frame, fg_color="transparent")
        path_row.pack(fill='x')
        ctk.CTkLabel(path_row, text="图片:", font=self.UI_FONT).pack(side='left')
        background = self.chapter.get("background") or {}
        self.background_path_var = tk.StringVar(value=background.get("image_path", ""))
        ctk.CTkEntry(path_row, textvariable=self.background_path_var, height=28,
                     font=self.UI_FONT).pack(side='left', fill='x', expand=True, padx=(6, 6))
        ctk.CTkButton(path_row, text="选择图片", width=84, height=28, font=self.UI_FONT,
                      command=self._import_background).pack(side='left')
        ctk.CTkButton(path_row, text="清除", width=52, height=28, font=self.UI_FONT,
                      fg_color="transparent", border_width=1, corner_radius=8,
                      text_color=CHAPTER_TEXT_SOFT, hover_color=CHAPTER_HOVER, border_color=CHAPTER_BORDER,
                      command=lambda: self.background_path_var.set("")).pack(side='left', padx=(6, 0))

        option_row = ctk.CTkFrame(bg_frame, fg_color="transparent")
        option_row.pack(fill='x', pady=(6, 0))
        self.smooth_var = tk.BooleanVar(value=bool(background.get("smooth_transition", True)))
        ctk.CTkCheckBox(option_row, text="平滑切换", variable=self.smooth_var,
                        font=self.UI_FONT, text_color=CHAPTER_TEXT,
                        checkbox_width=20, checkbox_height=20).pack(side='left')
        ctk.CTkLabel(option_row, text="滤镜:", font=self.UI_FONT).pack(side='left', padx=(18, 4))
        self.filter_labels = [NO_FILTER_LABEL] + [label for _key, label in VISUAL_FILTERS]
        current_filter = background.get("filter_effect") or ""
        self.filter_var = tk.StringVar(value=self._filter_key_to_label(current_filter))
        ctk.CTkComboBox(option_row, values=self.filter_labels, variable=self.filter_var,
                        state="readonly", width=130, height=28,
                        font=self.UI_FONT).pack(side='left')

        # ---------- 背景音乐 ----------
        ctk.CTkLabel(main, text="背景音乐（进入本章节时播放）", **section_label).pack(
            fill='x', pady=(10, 4))
        bgm_frame = ctk.CTkFrame(main, fg_color="transparent")
        bgm_frame.pack(fill='x', padx=6)

        bgm_path_row = ctk.CTkFrame(bgm_frame, fg_color="transparent")
        bgm_path_row.pack(fill='x')
        ctk.CTkLabel(bgm_path_row, text="音频:", font=self.UI_FONT).pack(side='left')
        bgm = self.chapter.get("bgm") or {}
        self.bgm_path_var = tk.StringVar(value=bgm.get("path", ""))
        ctk.CTkEntry(bgm_path_row, textvariable=self.bgm_path_var, height=28,
                     font=self.UI_FONT).pack(side='left', fill='x', expand=True,
                                             padx=(6, 6))
        ctk.CTkButton(bgm_path_row, text="选择音频", width=84, height=28, font=self.UI_FONT,
                      command=self._import_bgm).pack(side='left')
        ctk.CTkButton(bgm_path_row, text="清除", width=52, height=28, font=self.UI_FONT,
                      fg_color="transparent", border_width=1, corner_radius=8,
                      text_color=CHAPTER_TEXT_SOFT, hover_color=CHAPTER_HOVER,
                      border_color=CHAPTER_BORDER,
                      command=lambda: self.bgm_path_var.set("")).pack(side='left', padx=(6, 0))

        bgm_option_row = ctk.CTkFrame(bgm_frame, fg_color="transparent")
        bgm_option_row.pack(fill='x', pady=(6, 0))
        ctk.CTkLabel(bgm_option_row, text="音量:", font=self.UI_FONT).pack(side='left')
        self.bgm_level_var = tk.StringVar(value=buses.step_display(bgm_level(bgm)))
        OptionButton(bgm_option_row, values=[label for label, _ in buses.step_choices()],
                     variable=self.bgm_level_var, width=96, height=28,
                     font=self.UI_FONT).pack(side='left', padx=(6, 0))
        self.bgm_loop_var = tk.BooleanVar(value=bool(bgm.get("loop", True)))
        ctk.CTkCheckBox(bgm_option_row, text="循环播放", variable=self.bgm_loop_var,
                        font=self.UI_FONT, text_color=CHAPTER_TEXT,
                        checkbox_width=20, checkbox_height=20).pack(side='left', padx=(18, 0))

        bgm_fade_row = ctk.CTkFrame(bgm_frame, fg_color="transparent")
        bgm_fade_row.pack(fill='x', pady=(6, 0))
        ctk.CTkLabel(bgm_fade_row, text="切换淡入淡出:", font=self.UI_FONT).pack(side='left')
        self.bgm_fade_var = tk.StringVar(
            value=str(bgm.get("fade_seconds", DEFAULT_BGM_FADE_SECONDS)))
        ctk.CTkEntry(bgm_fade_row, textvariable=self.bgm_fade_var, width=64, height=28,
                     font=self.UI_FONT).pack(side='left', padx=(6, 2))
        ctk.CTkLabel(bgm_fade_row, text="秒（0 = 直接切换）", font=self.UI_FONT_SMALL,
                     text_color=CHAPTER_HINT).pack(side='left')

        # ---------- 对话语音效果（她 / 其他人两档）----------
        ctk.CTkLabel(main, text="对话语音效果（进入本章节时生效，离开即失效）",
                     **section_label).pack(fill='x', pady=(10, 4))
        self._build_voice_fx_rows(main)

        # ---------- 持续敏感效果 ----------
        sens_head = ctk.CTkFrame(main, fg_color="transparent")
        sens_head.pack(fill='x', pady=(10, 2))
        ctk.CTkLabel(sens_head, text="持续敏感效果（身处本章节时一直生效）",
                     **section_label).pack(side='left')
        ctk.CTkButton(sens_head, text="添加效果", width=84, height=26, font=self.UI_FONT,
                      fg_color="transparent", border_width=1, corner_radius=8,
                      text_color=CHAPTER_TEXT_SOFT, hover_color=CHAPTER_HOVER, border_color=CHAPTER_BORDER,
                      command=self._add_sensitivity_row).pack(side='right')

        self.sens_container = ctk.CTkFrame(main, fg_color="transparent")
        self.sens_container.pack(fill='x', padx=6)
        for effect in self.chapter.get("sensitivity") or []:
            if isinstance(effect, dict):
                self._add_sensitivity_row(effect)
        if not self.sens_rows:
            self._empty_sens_hint = ctk.CTkLabel(
                self.sens_container, text="（未配置敏感效果）", font=self.UI_FONT_SMALL,
                text_color=CHAPTER_HINT, anchor='w')
            self._empty_sens_hint.pack(fill='x')

        ctk.CTkLabel(
            main,
            text="敏感效果倍率 = 强度 ×（性格值 + 客观影响）：介入度用敏感值，\n"
                 "破坏性用重力，其余属性用策略值（敏感×(1-归一行动点数)+重力×归一个性强度）。\n"
                 "章节背景/音乐与敏感效果由「跳转章节」触发器在进入时应用，离开章节即失效；\n"
                 "进入没有配置音乐的章节时会停掉上一章的曲目。",
            justify='left', anchor='w', wraplength=520, font=self.UI_FONT_SMALL,
            text_color=CHAPTER_HINT).pack(fill='x', padx=6, pady=(10, 0))

        buttons = ctk.CTkFrame(main, fg_color="transparent")
        buttons.pack(pady=(12, 0))
        ctk.CTkButton(buttons, text="确定", width=88, height=28, font=self.UI_FONT,
                      command=self._ok).pack(side='left', padx=5)
        ctk.CTkButton(buttons, text="取消", width=88, height=28, font=self.UI_FONT,
                      command=self._cancel).pack(side='left', padx=5)

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Control-s>", lambda _e: self._ok())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

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
        实测响度拉回目标，所以各预设之间的响度差是设计出来的，不是调出来的——
        想知道这一档选出来会有多响，看这个标签；想听，点「预听」。
        """
        self._fx_vars = {}
        self._fx_label_keys = {}
        self._fx_loudness_labels = {}
        self._fx_preview_buttons = {}
        container = ctk.CTkFrame(parent, fg_color="transparent")
        container.pack(fill='x', padx=6)
        for slot in SLOTS:
            fx = (self.chapter.get("voice_fx") or {}).get(slot) or {}
            keys = preset_keys_for(slot)
            labels = [label_of(key) for key in keys]
            self._fx_label_keys[slot] = {label_of(key): key for key in keys}

            box = ctk.CTkFrame(container, fg_color="transparent")
            box.pack(fill='x', pady=(3, 0))
            row = ctk.CTkFrame(box, fg_color="transparent")
            row.pack(fill='x')
            ctk.CTkLabel(row, text=f"{SLOT_LABELS[slot]}:", font=self.UI_FONT
                         ).pack(side='left')
            preset_var = tk.StringVar(
                value=label_of(fx.get("preset") or PRESET_NONE))
            ctk.CTkComboBox(row, values=labels, variable=preset_var,
                            state="readonly", width=176, height=28,
                            font=self.UI_FONT).pack(side='left', padx=(6, 14))

            ctk.CTkLabel(row, text="强度:", font=self.UI_FONT).pack(side='left')
            intensity_var = tk.IntVar(
                value=int(fx.get("intensity", DEFAULT_INTENSITY)))
            ctk.CTkSlider(row, from_=0, to=MAX_INTENSITY,
                          number_of_steps=MAX_INTENSITY,
                          variable=intensity_var, width=150,
                          ).pack(side='left', padx=(6, 0))
            value_label = ctk.CTkLabel(row, text=str(intensity_var.get()),
                                       width=30, anchor='w', font=self.UI_FONT)
            value_label.pack(side='left', padx=(6, 0))

            # 第二行：声明响度（只读）+ 预听
            sub = ctk.CTkFrame(box, fg_color="transparent")
            sub.pack(fill='x', padx=(48, 0), pady=(2, 0))
            loud_label = ctk.CTkLabel(sub, text="", width=104, anchor='w',
                                      font=self.UI_FONT_SMALL,
                                      text_color=CHAPTER_TEXT_SOFT)
            loud_label.pack(side='left')
            audition = ctk.CTkButton(
                sub, text="预听", width=52, height=24,
                corner_radius=self.corner_radius, font=self.UI_FONT_SMALL,
                fg_color=CHAPTER_HOVER, hover_color=CHAPTER_BORDER,
                border_width=1, border_color=CHAPTER_BORDER,
                text_color=CHAPTER_TEXT, command=lambda s=slot: self._preview_fx(s))
            audition.pack(side='left', padx=(6, 0))

            self._fx_vars[slot] = (preset_var, intensity_var)
            self._fx_loudness_labels[slot] = loud_label
            self._fx_preview_buttons[slot] = audition
            intensity_var.trace_add(
                "write",
                lambda *_a, label=value_label, var=intensity_var:
                    label.configure(text=str(var.get())))
            for var in (preset_var, intensity_var):
                var.trace_add("write", lambda *_a, s=slot: self._refresh_fx_loudness(s))
            self._refresh_fx_loudness(slot)

        self._preview_status = ctk.CTkLabel(
            parent, text="", anchor='w', font=self.UI_FONT_SMALL,
            text_color=CHAPTER_HINT)
        self._preview_status.pack(fill='x', padx=6, pady=(2, 0))

        ctk.CTkLabel(
            parent,
            text="效果是物理层面的：她按巨大尺度（低频轰鸣、旷野回声、空气缓抖），"
                 "其他人按普通尺度在同一个环境里（回声、隔墙、震地余波）——不含情绪语气。\n"
                 "响度是预设的声明值（渲染时按实测 RMS 对齐，全表跨度 1.5 档）；"
                 "预听会联网合成一句样例（首次约 1 秒）。\n"
                 "没装音频解码器（pip install miniaudio）的机器上，"
                 "「近距震耳」这类预设会退回原声，其余走零依赖的回声/缓抖近似。",
            justify='left', anchor='w', wraplength=560, font=self.UI_FONT_SMALL,
            text_color=CHAPTER_HINT).pack(fill='x', padx=6, pady=(4, 0))

    def _refresh_fx_loudness(self, slot):
        """刷新该档的只读「声明响度」（随预设/强度联动）。"""
        label = self._fx_loudness_labels.get(slot)
        if label is None:
            return
        preset_var, intensity_var = self._fx_vars[slot]
        key = self._fx_label_keys[slot].get(preset_var.get().strip(), PRESET_NONE)
        spec = {"preset": key, "intensity": int(intensity_var.get())}
        label.configure(text=loudness_hint(spec) or "—")

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
        hint = getattr(self, "_empty_sens_hint", None)
        if hint is not None:
            hint.destroy()
            self._empty_sens_hint = None

        row = ctk.CTkFrame(self.sens_container, fg_color="transparent")
        row.pack(fill='x', pady=3)
        attr_var = tk.StringVar(value=effect.get("attr") or self.sens_targets[0]
                                or DEFAULT_SENSITIVITY_ATTR)
        ctk.CTkComboBox(row, values=self.sens_targets, variable=attr_var, state="readonly",
                        width=140, height=28, font=self.UI_FONT).pack(side='left')
        ctk.CTkLabel(row, text="强度:", font=self.UI_FONT).pack(side='left', padx=(12, 2))
        strength_var = tk.StringVar(value=str(effect.get("strength", 1.0)))
        ctk.CTkEntry(row, textvariable=strength_var, width=64, height=28,
                     font=self.UI_FONT).pack(side='left')
        ctk.CTkLabel(row, text="客观影响:", font=self.UI_FONT).pack(side='left', padx=(12, 2))
        objective_var = tk.StringVar(value=str(effect.get("objective", 0.0)))
        ctk.CTkEntry(row, textvariable=objective_var, width=64, height=28,
                     font=self.UI_FONT).pack(side='left')

        entry = {"frame": row, "attr_var": attr_var, "strength_var": strength_var,
                 "objective_var": objective_var}
        ctk.CTkButton(row, text="移除", width=52, height=28, font=self.UI_FONT,
                      fg_color="transparent", border_width=1, corner_radius=8,
                      text_color=CHAPTER_ERR, hover_color=CHAPTER_ERR_HOVER, border_color=CHAPTER_ERR,
                      command=lambda: self._remove_sensitivity_row(entry)).pack(side='left', padx=(12, 0))
        self.sens_rows.append(entry)

    def _remove_sensitivity_row(self, entry):
        entry["frame"].destroy()
        if entry in self.sens_rows:
            self.sens_rows.remove(entry)

    # ---------- 结束章节结算字段 ----------
    def _build_ending_fields(self, parent):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill='x', pady=(2, 0))
        ctk.CTkLabel(row, text="结局结算:", font=self.UI_FONT_BOLD).pack(side='left')
        ctk.CTkLabel(row, text="介入度增量:", font=self.UI_FONT).pack(side='left', padx=(12, 2))
        self.ending_intrusion_var = tk.StringVar(value=str(self.chapter.get("intrusion_delta", 0)))
        ctk.CTkEntry(row, textvariable=self.ending_intrusion_var, width=60, height=28,
                     font=self.UI_FONT).pack(side='left')
        ctk.CTkLabel(row, text="破坏性增量:", font=self.UI_FONT).pack(side='left', padx=(12, 2))
        self.ending_destruction_var = tk.StringVar(value=str(self.chapter.get("destruction_delta", 0)))
        ctk.CTkEntry(row, textvariable=self.ending_destruction_var, width=60, height=28,
                     font=self.UI_FONT).pack(side='left')
        ctk.CTkLabel(row, text="伤亡步进:", font=self.UI_FONT).pack(side='left', padx=(12, 2))
        self.ending_casualty_var = tk.StringVar(value=str(self.chapter.get("casualty_step", 0)))
        ctk.CTkEntry(row, textvariable=self.ending_casualty_var, width=60, height=28,
                     font=self.UI_FONT).pack(side='left')
        ctk.CTkLabel(row, text="行动点返还:", font=self.UI_FONT).pack(side='left', padx=(12, 2))
        self.ending_refund_var = tk.StringVar(value=str(self.chapter.get("action_points_refund", 0)))
        ctk.CTkEntry(row, textvariable=self.ending_refund_var, width=60, height=28,
                     font=self.UI_FONT).pack(side='left')

        row2 = ctk.CTkFrame(parent, fg_color="transparent")
        row2.pack(fill='x', pady=(4, 0))
        ctk.CTkLabel(row2, text="结局图标:", font=self.UI_FONT).pack(side='left')
        self.ending_icon_var = tk.StringVar(value=self.chapter.get("icon_path", ""))
        ctk.CTkEntry(row2, textvariable=self.ending_icon_var, height=28,
                     font=self.UI_FONT).pack(side='left', fill='x', expand=True, padx=(6, 6))
        ctk.CTkButton(row2, text="选择图标", width=84, height=28, font=self.UI_FONT,
                      command=self._import_ending_icon).pack(side='left')
        ctk.CTkButton(row2, text="清除", width=56, height=28, font=self.UI_FONT,
                      fg_color="transparent", border_width=1, corner_radius=8,
                      text_color=CHAPTER_ERR, hover_color=CHAPTER_ERR_HOVER,
                      border_color=CHAPTER_ERR,
                      command=lambda: self.ending_icon_var.set("")).pack(side='left', padx=(6, 0))

        ctk.CTkLabel(
            parent, justify='left', anchor='w', wraplength=520,
            font=self.UI_FONT_SMALL, text_color=CHAPTER_HINT,
            text="结束章节内的所有段落类型固定为「结局」、步进为 0，"
                 "触发器不能跳出或弹出选项；段落数耗尽即按上方结算生成结局。").pack(
            fill='x', pady=(4, 0))

    def _toggle_ending_ui(self):
        if self.ending_var.get():
            self.ending_frame.pack(fill='x', padx=6, pady=(4, 0))
            self.overflow_combo.configure(state="disabled")
        else:
            self.ending_frame.pack_forget()
            self.overflow_combo.configure(state="readonly")

    def _import_ending_icon(self):
        rel = import_ending_icon(self, self._scenario_repo, self.scenario_id)
        if rel:
            self.ending_icon_var.set(rel)

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

        is_ending = bool(self.ending_var.get())
        overflow_target = "" if is_ending else (
            "" if self.overflow_var.get() == OVERFLOW_LEAVE_LABEL
            else self.overflow_var.get())

        result = {
            "name": name,
            "color": color,
            "start": bool(self.start_var.get()),
            "ending": is_ending,
            "max_paragraphs": max_paragraphs,
            "overflow_target": overflow_target,
            "background": background,
            "bgm": bgm,
            "voice_fx": self._voice_fx_from_ui(),
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
