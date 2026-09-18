import re
import tkinter as tk

import customtkinter as ctk

import ui.common.dialogs
from dungeon.actions import VISUAL_FILTERS
from dungeon.chapters import (
    CHAPTER_COLOR_PRESETS, DEFAULT_SENSITIVITY_ATTR, normalize_chapter,
)
from ui.common.dialogs import BaseDialog
from ui.common.theme import (
    CHAPTER_BORDER, CHAPTER_HOVER, CHAPTER_TEXT, CHAPTER_TEXT_SOFT,
    CHAPTER_HINT, CHAPTER_ERR, CHAPTER_ERR_HOVER, CHAPTER_SWATCH_SELECTED_BORDER,
)
from ui.scenario.asset_import import import_background_image

NO_FILTER_LABEL = "（不使用滤镜）"


class ChapterEditDialog(BaseDialog):
    """章节编辑对话框。

    章节没有条件字段：进入与离开都由触发器的「跳转章节」动作执行。
    这里只编辑“身处其中时的环境”——特定背景与持续敏感效果，外加编辑器配色。
    """

    def __init__(self, parent, chapter: dict = None, dungeon_repo=None, dungeon_id=None,
                 evolution_attrs=None, all_chapters=None):
        super().__init__(parent)
        self.title("编辑章节")
        self.geometry("580x620")
        self.minsize(540, 500)
        self.resizable(True, True)
        self.chapter = chapter if chapter is not None else {}
        self._dungeon_repo = dungeon_repo
        self.dungeon_id = dungeon_id
        self.all_chapters = all_chapters or []
        self.result = None
        self.evolution_attrs = [a for a in (evolution_attrs or []) if isinstance(a, dict)]
        # 敏感效果只针对介入度、破坏性与自定义属性，总伤亡不在演化对象内
        self.sens_targets = [a["name"] for a in self.evolution_attrs
                             if a.get("name") and a.get("type") != "casualty"] \
            or ["介入度", "破坏性"]
        self.sens_rows = []
        self._swatch_buttons = {}
        self.transient(parent)
        self.grab_set()
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
        info.grid_columnconfigure(3, weight=1)

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
            text="敏感效果倍率 = 强度 ×（人物敏感值 + 客观影响）；破坏性使用性格重力。\n"
                 "章节背景与敏感效果由「跳转章节」触发器在进入时应用，离开章节即失效。",
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
        rel_path = import_background_image(self, self._dungeon_repo, self.dungeon_id)
        if not rel_path:
            return
        self.background_path_var.set(rel_path)
        self.smooth_var.set(True)

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

    # ---------- 确定 ----------
    def _ok(self):
        name = self.name_var.get().strip()
        if not name:
            ui.common.dialogs.showerror("错误", "章节名称不能为空")
            return
        original_name = self.chapter.get("name", "")
        for other in self.all_chapters:
            if other.get("name") == name and other.get("name") != original_name:
                ui.common.dialogs.showerror("错误", f"章节名称 '{name}' 已被使用")
                return

        color = (self.color_var.get() or "").strip()
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            ui.common.dialogs.showerror("错误", "配色必须是 #RRGGBB 形式的颜色值")
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
            ui.common.dialogs.showerror("错误", "配置了滤镜但没有选择背景图片，滤镜不会生效")
            return

        sensitivity = []
        for index, row in enumerate(self.sens_rows, start=1):
            attr = row["attr_var"].get().strip()
            if not attr:
                ui.common.dialogs.showerror("错误", f"第 {index} 条敏感效果没有选择属性")
                return
            try:
                strength = float(row["strength_var"].get())
            except ValueError:
                ui.common.dialogs.showerror("错误", f"第 {index} 条敏感效果的强度必须是数字")
                return
            try:
                objective = float(row["objective_var"].get())
            except ValueError:
                ui.common.dialogs.showerror("错误", f"第 {index} 条敏感效果的客观影响必须是数字")
                return
            sensitivity.append({"attr": attr, "strength": strength, "objective": objective})

        self.result = normalize_chapter({
            "name": name,
            "color": color,
            "start": bool(self.start_var.get()),
            "background": background,
            "sensitivity": sensitivity,
            "note": self.note_var.get().strip(),
        })
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()
