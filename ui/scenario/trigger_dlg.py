import copy
import datetime
import os
import re
import tkinter as tk
from tkinter import filedialog

import customtkinter as ctk

import ui.common
from ui.common.dialogs import BaseDialog, ImageCropDialog
from ui.common.theme import TEXT, BROWN_HINT


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

    # 当前动作类型的参数说明（显示在参数滚动框末尾）
    ACTION_DESCRIPTIONS = {
        "insert": (
            "触发后将文本插入插入故事正文，不能为空。\n"
            "段落属性：决定插入内容在副本中的文本类型，可选背景、分支、对话、互动或行动。\n"
            "高亮：勾选后用高亮样式显示该段落。\n"
            "延迟插入：勾选后先排队，等待下一条 AI 段落返回后再显示；不勾选则在触发后立即插入。"
        ),
        "option": (
            "选项列表：至少填写 1 项；每项的编号从 0 开始，玩家的选择会按编号记录。\n"
            "选择记录：其他触发器可使用“选择:本触发器名称”判定选择次数、某编号占比、最后一次选择或趋势变化。\n"
            "注意：选项触发器需要先填写有效选项，否则触发时会被跳过。"
        ),
        "sensitivity": (
            "触发后属性的演化将偏置。相对偏置量 = 强度 ×（人物敏感值 + 客观影响），可为正数或负数。\n"
            "属性：选择要影响的属性，例如介入度、破坏性或自定义演化属性。\n"
            "持续步数：效果持续的副本步数，至少按 1 步计算。"
        ),
        "ending": (
            "触发后副本结束。\n"
            "结局结算增量：结束时统一结算一次，可修改介入度、破坏性、伤亡步进、自定义属性和行动点数返还。\n"
            "结局图标：选择 png 图标后视为重要结局，并在首次达成时记入档案；不配置图标则为普通结局。"
        ),
        "background": (
            "背景图片：选择或填写副本目录中的图片路径；条件满足后切换当前背景。\n"
            "平滑切换：勾选后使用淡入淡出过渡，不勾选则直接切换。\n"
            "图片路径会按副本目录保存相对路径，使用“选择图片”可完成裁剪并复制到副本 images 目录。"
        ),
        "none": (
            "条件满足时不执行实际动作，只记录该触发器已经成立，其他触发器可以在前置条件中引用它。\n"
            "适用场景：将复杂流程拆成多个阶段，或作为多个触发器共用的中间条件节点。"
        ),
    }
    ACTION_TITLES = {
        "insert": "插入段落", "option": "选项分支", "sensitivity": "敏感效果",
        "ending": "结局", "background": "背景图片", "none": "空触发器",
    }
    OPERATOR_LABELS = (("且（全部满足）", "and"), ("或（任一满足）", "or"))


    def __init__(self, parent, trigger: dict, evolution_names=None, all_triggers=None,
                 dungeon_repo=None, dungeon_id=None, evolution_attrs=None):
        if evolution_names is None:
            evolution_names = []
        if all_triggers is None:
            all_triggers = []
        super().__init__(parent)
        self.title("编辑触发器")
        self.geometry("540x540")
        self.minsize(480, 480)
        self.resizable(True, True)
        self.trigger = trigger if trigger is not None else {}
        self.evolution_names = evolution_names
        self.all_triggers = all_triggers   # 用于校验名称唯一性
        self._dungeon_repo = dungeon_repo
        self.dungeon_id = dungeon_id
        self.result = None
        self.evolution_attrs = [a for a in (evolution_attrs or []) if isinstance(a, dict)]
        # 敏感效果只针对介入度、破坏性与自定义属性，总伤亡不在演化对象内
        self.sens_targets = [a["name"] for a in self.evolution_attrs
                             if a.get("name") and a.get("type") != "casualty"] or evolution_names
        self.transient(parent)
        self.grab_set()
        self._hint_visible = False
        self._build_ui()
        self._center_dialog(parent)
        self.wait_window()

    def _build_ui(self):
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill='both', expand=True, padx=14, pady=12)

        # 模板按钮行（选择动作类型）
        btn_frame = ctk.CTkFrame(main, fg_color="transparent")
        btn_frame.pack(fill='x', pady=(0, 8))
        for text, command in (
            ("插入段落", self._insert_template), ("选项分支", self._option_template),
            ("敏感效果", self._sensitivity_template), ("结局", self._ending_template),
            ("背景图片", self._background_template), ("空触发器", self._empty_template),
        ):
            ctk.CTkButton(btn_frame, text=text, width=80, height=28,
                          font=self.UI_FONT, command=command).pack(side='left', padx=3)

        # 主滚动区：基础信息 -> 动作设置 -> 类型说明
        content = ctk.CTkScrollableFrame(main)
        content.pack(fill='both', expand=True, pady=(4, 0))
        section_label = dict(font=self.SECTION_FONT, anchor='w')

        # ---------- 基础信息 ----------
        ctk.CTkLabel(content, text="基础信息", **section_label).pack(fill='x', padx=6, pady=(4, 4))
        common = ctk.CTkFrame(content, fg_color="transparent")
        common.pack(fill='x', padx=6, pady=(0, 6))

        ctk.CTkLabel(common, text="名称:", font=self.UI_FONT).grid(
            row=0, column=0, sticky='w', padx=5)
        self.name_var = tk.StringVar(value=self.trigger.get("name", ""))
        self.name_entry = ctk.CTkEntry(
            common, textvariable=self.name_var, width=250, height=28, font=self.UI_FONT)
        self.name_entry.grid(row=0, column=1, sticky='w', padx=5)

        ctk.CTkLabel(common, text="前置条件 (名称, 逗号分隔):", font=self.UI_FONT).grid(
            row=1, column=0, sticky='w', padx=5, pady=(5, 0))
        pre_names = ",".join(self.trigger.get("precondition_names", []))
        self.pre_var = tk.StringVar(value=pre_names)
        self.pre_entry = ctk.CTkEntry(
            common, textvariable=self.pre_var, width=250, height=28, font=self.UI_FONT)
        self.pre_entry.grid(row=1, column=1, sticky='w', padx=5, pady=(4, 0))

        # 可再次触发（所有动作类型通用）
        self.repeatable_var = tk.BooleanVar(value=self.trigger.get("repeatable", True))
        self.repeatable_check = ctk.CTkCheckBox(
            common, text="可再次触发", variable=self.repeatable_var,
            font=self.UI_FONT, text_color=TEXT,
            checkbox_width=20, checkbox_height=20)
        self.repeatable_check.grid(row=2, column=0, columnspan=2, sticky='w', padx=5, pady=(4, 0))
        common.grid_columnconfigure(1, weight=1)

        # ---------- 动作设置（各类型独立分区，仅显示当前所选类型）----------
        self.action_section_label = ctk.CTkLabel(
            content, text="动作设置", font=self.SECTION_FONT, anchor='w')
        self.action_section_label.pack(fill='x', padx=6, pady=(6, 2))
        self.action_area = ctk.CTkFrame(content, fg_color="transparent")
        self.action_area.pack(fill='x', padx=6)
        self.action_frames = {}
        self.action_frames["insert"] = self._build_insert_form(self.action_area)
        self.action_frames["option"] = self._build_option_form(self.action_area)
        self.action_frames["sensitivity"] = self._build_sensitivity_form(self.action_area)
        self.action_frames["ending"] = self._build_ending_form(self.action_area)
        self.action_frames["background"] = self._build_background_form(self.action_area)

        # ---------- 类型说明（滚动区内的说明文案）----------
        hint_area = ctk.CTkFrame(content, fg_color="transparent")
        hint_area.pack(fill='x', padx=6, pady=(8, 2))
        ctk.CTkLabel(hint_area, text="类型说明", **section_label).pack(fill='x', pady=(0, 4))
        self.action_description_label = ctk.CTkLabel(
            hint_area, text="", justify='left', anchor='nw', wraplength=460,
            font=self.UI_FONT, text_color=BROWN_HINT)
        self.action_description_label.pack(fill='x', pady=(2, 6))

        # ---------- 触发条件（固定区域，始终可见）----------
        condition_frame = ctk.CTkFrame(main, fg_color="transparent")
        condition_frame.pack(fill='x', pady=(10, 0))
        ctk.CTkLabel(condition_frame, text="触发条件", **section_label).pack(fill='x', pady=(0, 4))
        self._build_condition_controls(condition_frame)

        self._load_condition_controls()
        self._load_action_controls(self.trigger.get("action_data", {}))
        self._update_action_description()

        # 确定/取消按钮
        btn_frame2 = ctk.CTkFrame(main, fg_color="transparent")
        btn_frame2.pack(pady=(10, 0))
        ctk.CTkButton(btn_frame2, text="确定", width=88, height=28,
                      font=self.UI_FONT, command=self._ok).pack(side='left', padx=5)
        ctk.CTkButton(btn_frame2, text="取消", width=88, height=28,
                      font=self.UI_FONT, command=self._cancel).pack(side='left', padx=5)

        self.bind("<Escape>", lambda _event: self._cancel())
        self.bind("<Control-s>", lambda _event: self._ok())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

    # ---------- 各动作类型的独立表单构造 ----------
    def _build_insert_form(self, parent):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        ctk.CTkLabel(frame, text="插入段落文本:",
                     font=self.UI_FONT_BOLD).pack(anchor='w', pady=(2, 4))
        self.insert_text = ctk.CTkTextbox(
            frame, height=82, wrap='word', font=self.UI_FONT)
        self.insert_text.pack(fill='x')
        attr_row = ctk.CTkFrame(frame, fg_color="transparent")
        attr_row.pack(fill='x', pady=(8, 2))
        ctk.CTkLabel(attr_row, text="段落属性:", font=self.UI_FONT).pack(side='left')
        self.insert_type_var = tk.StringVar(value="background")
        self.insert_type_combo = ctk.CTkComboBox(
            attr_row, values=["background", "branch", "dialog", "interaction", "action"],
            variable=self.insert_type_var, state="readonly", width=140, height=28,
            font=self.UI_FONT)
        self.insert_type_combo.pack(side='left', padx=(5, 12))
        self.highlight_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(attr_row, text="高亮", variable=self.highlight_var,
                        font=self.UI_FONT, text_color=TEXT,
                        checkbox_width=20, checkbox_height=20).pack(side='left', padx=5)
        self.delayed_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(attr_row, text="延迟插入", variable=self.delayed_var,
                        font=self.UI_FONT, text_color=TEXT,
                        checkbox_width=20, checkbox_height=20).pack(side='left', padx=5)
        return frame

    def _build_option_form(self, parent):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        ctk.CTkLabel(frame, text="选项列表（固定 4 项，可留空）:",
                     font=self.UI_FONT_BOLD).pack(anchor='w', pady=(2, 4))
        option_grid = ctk.CTkFrame(frame, fg_color="transparent")
        option_grid.pack(fill='x')
        self.option_entries = []
        for index in range(4):
            ctk.CTkLabel(option_grid, text=f"{index + 1}.", width=24,
                         font=self.UI_FONT,
                         anchor="e").grid(row=index, column=0, padx=(0, 6), pady=3)
            entry = ctk.CTkEntry(option_grid, height=28, font=self.UI_FONT,
                                 placeholder_text=f"选项 {index + 1} 提示（可留空）")
            entry.grid(row=index, column=1, sticky='ew', pady=3)
            self.option_entries.append(entry)
        option_grid.grid_columnconfigure(1, weight=1)
        return frame

    def _build_sensitivity_form(self, parent):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        attr_row = ctk.CTkFrame(frame, fg_color="transparent")
        attr_row.pack(fill='x', pady=(2, 8))
        ctk.CTkLabel(attr_row, text="目标属性:", font=self.UI_FONT).pack(side='left')
        self.sens_attr_var = tk.StringVar(value="介入度")
        self.sens_attr_combo = ctk.CTkComboBox(
            attr_row, values=self.sens_targets if self.sens_targets else ["介入度"],
            variable=self.sens_attr_var, state="readonly", width=160, height=28,
            font=self.UI_FONT)
        self.sens_attr_combo.pack(side='left', padx=(6, 0))

        num_row = ctk.CTkFrame(frame, fg_color="transparent")
        num_row.pack(fill='x', pady=(0, 2))
        self.sens_strength_var = tk.StringVar(value="1.0")
        self.sens_objective_var = tk.StringVar(value="0.0")
        self.sens_duration_var = tk.StringVar(value="3")
        ctk.CTkLabel(num_row, text="强度:", font=self.UI_FONT).pack(side='left')
        ctk.CTkEntry(num_row, textvariable=self.sens_strength_var,
                     width=64, height=28, font=self.UI_FONT).pack(side='left', padx=(4, 12))
        ctk.CTkLabel(num_row, text="客观影响:", font=self.UI_FONT).pack(side='left')
        ctk.CTkEntry(num_row, textvariable=self.sens_objective_var,
                     width=64, height=28, font=self.UI_FONT).pack(side='left', padx=(4, 12))
        ctk.CTkLabel(num_row, text="持续步数:", font=self.UI_FONT).pack(side='left')
        ctk.CTkEntry(num_row, textvariable=self.sens_duration_var,
                     width=64, height=28, font=self.UI_FONT).pack(side='left', padx=(4, 0))
        return frame

    def _build_ending_form(self, parent):
        self.ending_frame = ctk.CTkFrame(parent, fg_color="transparent")

        name_row = ctk.CTkFrame(self.ending_frame, fg_color="transparent")
        name_row.pack(fill='x')
        ctk.CTkLabel(name_row, text="结局名称:",
                     font=self.UI_FONT_BOLD).pack(side='left')
        self.ending_name_var = tk.StringVar(value="")
        self.ending_name_entry = ctk.CTkEntry(
            name_row, textvariable=self.ending_name_var, width=250, height=28, font=self.UI_FONT)
        self.ending_name_entry.pack(side='left', padx=(6, 0))

        ctk.CTkLabel(self.ending_frame, text="结算增量（触发结局后统一计算一次）:",
                     font=self.UI_FONT_BOLD).pack(anchor='w', pady=(8, 3))
        delta_row = ctk.CTkFrame(self.ending_frame, fg_color="transparent")
        delta_row.pack(fill='x')
        self.ending_intrusion_var = tk.StringVar(value="0")
        self.ending_destruction_var = tk.StringVar(value="0")
        self.ending_casualty_var = tk.StringVar(value="0")

        def _delta_entry(row_frame, text, var):
            ctk.CTkLabel(row_frame, text=text, font=self.UI_FONT).pack(side='left')
            ctk.CTkEntry(row_frame, textvariable=var, width=70, height=28,
                         font=self.UI_FONT).pack(side='left', padx=(4, 10))

        _delta_entry(delta_row, "介入度", self.ending_intrusion_var)
        _delta_entry(delta_row, "破坏性", self.ending_destruction_var)
        _delta_entry(delta_row, "伤亡步进", self.ending_casualty_var)

        # 自定义属性增量
        self.ending_custom_delta_vars = {}
        custom_names = [a["name"] for a in self.evolution_attrs
                        if a.get("name") and a.get("type") == "custom"]
        if custom_names:
            ctk.CTkLabel(self.ending_frame, text="自定义属性增量:",
                         font=self.UI_FONT_BOLD).pack(anchor='w', pady=(8, 3))
            for name in custom_names:
                row = ctk.CTkFrame(self.ending_frame, fg_color="transparent")
                row.pack(fill='x')
                ctk.CTkLabel(row, text=name, width=90, anchor='w',
                             font=self.UI_FONT).pack(side='left')
                var = tk.StringVar(value="0")
                self.ending_custom_delta_vars[name] = var
                ctk.CTkEntry(row, textvariable=var, width=90, height=28,
                             font=self.UI_FONT).pack(side='left', padx=(4, 0))

        # 行动点数返还
        refund_row = ctk.CTkFrame(self.ending_frame, fg_color="transparent")
        refund_row.pack(fill='x', pady=(6, 0))
        self.ending_refund_var = tk.StringVar(value="0")
        ctk.CTkLabel(refund_row, text="行动点数返还:", font=self.UI_FONT_BOLD).pack(side='left')
        ctk.CTkEntry(refund_row, textvariable=self.ending_refund_var,
                     width=90, height=28, font=self.UI_FONT).pack(side='left', padx=(4, 0))

        # 结局图标（png）：不上传图标则该结局视为不重要，不计入档案
        icon_row = ctk.CTkFrame(self.ending_frame, fg_color="transparent")
        icon_row.pack(fill='x', pady=(6, 0))
        ctk.CTkLabel(icon_row, text="结局图标(png):",
                     font=self.UI_FONT_BOLD).pack(side='left')
        self.ending_icon_var = tk.StringVar(value="")
        ctk.CTkEntry(icon_row, textvariable=self.ending_icon_var, width=180,
                     height=28, font=self.UI_FONT).pack(side='left', padx=(6, 0))
        ctk.CTkButton(icon_row, text="选择图标", width=78, command=self._import_ending_icon,
                      height=28, font=self.UI_FONT).pack(side='left', padx=(6, 0))
        ctk.CTkButton(icon_row, text="清除", width=52, command=self._clear_ending_icon,
                      height=28, font=self.UI_FONT).pack(side='left', padx=(4, 0))
        self.ending_icon_preview = ctk.CTkLabel(self.ending_frame, text="")
        self.ending_icon_preview.pack(anchor='w', pady=(2, 0))
        self._ending_icon_ctkimg = None
        return self.ending_frame

    def _build_background_form(self, parent):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        path_row = ctk.CTkFrame(frame, fg_color="transparent")
        path_row.pack(fill='x', pady=(2, 8))
        ctk.CTkLabel(path_row, text="背景图片:",
                     font=self.UI_FONT_BOLD).pack(side='left')
        self.background_path_var = tk.StringVar()
        self.background_path_entry = ctk.CTkEntry(
            path_row, textvariable=self.background_path_var, height=28, font=self.UI_FONT)
        self.background_path_entry.pack(side='left', fill='x', expand=True, padx=(8, 6))
        self.background_import_btn = ctk.CTkButton(
            path_row, text="选择图片", width=88, height=28,
            font=self.UI_FONT, command=self._import_background)
        self.background_import_btn.pack(side='left')
        self.smooth_var = tk.BooleanVar(value=True)
        self.smooth_check = ctk.CTkCheckBox(
            frame, text="平滑切换（背景图淡入淡出切换）",
            variable=self.smooth_var, font=self.UI_FONT,
            text_color=TEXT, checkbox_width=20, checkbox_height=20)
        self.smooth_check.pack(anchor='w', pady=(2, 2))
        return frame

    def _show_action_frame(self, action_type):
        """仅显示当前动作类型的表单，其余动作帧隐藏。"""
        if action_type == "none":
            self.action_section_label.pack_forget()
            self.action_area.pack_forget()
        else:
            if self.action_section_label.winfo_manager() != "pack":
                self.action_section_label.pack(
                    fill='x', padx=6, pady=(6, 2), before=self.action_description_label.master)
            if self.action_area.winfo_manager() != "pack":
                self.action_area.pack(
                    fill='x', padx=6, before=self.action_description_label.master)
        for type_name, frame in self.action_frames.items():
            if type_name == action_type:
                frame.pack(fill='both', expand=True)
            else:
                frame.pack_forget()
        self.current_action_type = action_type

    # ---------- 模板方法 ----------
    def _set_action_template(self, action_type, name, action_data):
        self.name_var.set(name)
        self._refresh_action_ui(action_type, action_data)

    def _background_template(self):
        self._set_action_template("background", "背景图",
                                  {"image_path": self.background_path_var.get(),
                                   "smooth_transition": True})

    def _empty_template(self):
        self._set_action_template("none", "新空触发", {})

    def _build_condition_controls(self, parent):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill='x', pady=(0, 4))
        top = ctk.CTkFrame(frame, fg_color="transparent")
        top.pack(fill='x', pady=(0, 4))
        ctk.CTkLabel(top, text="各规则间关系", font=self.UI_FONT).pack(side='left')
        self.condition_operator_var = tk.StringVar(value=self.OPERATOR_LABELS[0][0])
        op_labels = [label for label, _code in self.OPERATOR_LABELS]
        ctk.CTkComboBox(top, values=op_labels, variable=self.condition_operator_var,
                        state="readonly", width=170, height=28,
                        font=self.UI_FONT).pack(side='left', padx=(8, 0))
        ctk.CTkLabel(top, text="最多 3 条，整行留空表示忽略",
                     font=self.UI_FONT_SMALL).pack(side='left', padx=10)
        self.condition_operator_var.trace_add(
            "write", lambda *_a: self._update_condition_preview())

        # 条件预览：把所有规则实时翻译成中文句子，数组判定更直观
        self.cond_preview = ctk.CTkLabel(
            frame, text="条件预览: （留空或整行未填 → 无条件触发）", anchor='w', justify='left',
            wraplength=470, font=self.UI_FONT_SMALL,
            text_color=BROWN_HINT)
        self.cond_preview.pack(fill='x', padx=6, pady=(0, 4))

        self.condition_rows = []
        base_keys = list(dict.fromkeys(
            self.evolution_names + ["介入度", "破坏性", "总伤亡", "总计数", "间隔计数", "伤亡数组"]))
        # 只有选项分支触发器才有选择数组可供判定
        base_keys += [f"选择:{t['name']}" for t in self.all_triggers
                      if t.get("name") and t.get("action_type") == "option"]
        for index in range(3):
            row = ctk.CTkFrame(frame, fg_color="transparent")
            row.pack(fill='x', pady=3)
            key_var = tk.StringVar()
            key_widget = ctk.CTkComboBox(row, values=base_keys or ["总计数"], variable=key_var,
                                         state="readonly", width=150, height=28,
                                         font=self.UI_FONT,
                                         command=lambda _v, i=index: self._sync_condition_row(i))
            key_widget.grid(row=0, column=0, sticky='w')
            metric_var = tk.StringVar(value=self._metric_options("总伤亡")[0][0])
            metric_widget = ctk.CTkComboBox(row, values=[], variable=metric_var,
                                            state="readonly", width=100, height=28,
                                            font=self.UI_FONT,
                                            command=lambda _v, i=index: self._sync_condition_row(i))
            metric_widget.grid(row=0, column=1, padx=(6, 0))
            target_var = tk.StringVar()
            target_widget = ctk.CTkEntry(row, textvariable=target_var, width=68,
                                         height=28, font=self.UI_FONT,
                                         placeholder_text="目标编号")
            target_widget.grid(row=0, column=2, padx=(6, 0))
            comparator_var = tk.StringVar(value=">=")
            comparator_widget = ctk.CTkComboBox(
                row, values=[">=", "<=", ">", "<", "==", "!="],
                variable=comparator_var, state="readonly", width=58, height=28,
                font=self.UI_FONT)
            comparator_widget.grid(row=0, column=3, padx=(6, 0))
            value_var = tk.StringVar()
            value_widget = ctk.CTkEntry(row, textvariable=value_var, width=88,
                                        height=28, font=self.UI_FONT,
                                        placeholder_text="数值")
            value_widget.grid(row=0, column=4, padx=(6, 0), sticky='w')
            window_var = tk.StringVar()
            window_widget = ctk.CTkEntry(row, textvariable=window_var, width=68,
                                         height=28, font=self.UI_FONT,
                                         placeholder_text="窗口(≥2)")
            window_widget.grid(row=0, column=5, padx=(6, 0))
            self.condition_rows.append({
                "key_var": key_var, "metric_var": metric_var, "target_var": target_var,
                "comparator_var": comparator_var, "value_var": value_var, "window_var": window_var,
                "key_widget": key_widget, "metric_widget": metric_widget,
                "target_widget": target_widget, "comparator_widget": comparator_widget,
                "value_widget": value_widget, "window_widget": window_widget,
            })
            for var in (value_var, target_var, window_var):
                var.trace_add("write", lambda *_a, i=index: self._update_condition_preview())
            self._sync_condition_row(index)

    # ---------- 条件规则的直观化 ----------
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

    def _sync_condition_row(self, index):
        row = self.condition_rows[index]
        key = row["key_var"].get().strip()
        metric_label = row["metric_var"].get().strip()
        if not key.startswith("选择:") and key != "伤亡数组":
            row["metric_widget"].grid_remove()
            row["target_widget"].grid_remove()
            row["window_widget"].grid_remove()
            self._update_condition_preview()
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
            row["target_widget"].configure(placeholder_text="目标选项编号")
            row["target_widget"].grid()
        else:
            row["target_widget"].grid_remove()
        if needs_window:
            row["window_widget"].configure(placeholder_text="窗口(≥2 步)")
            row["window_widget"].grid()
        else:
            row["window_widget"].grid_remove()
        self._update_condition_preview()

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

    def _update_condition_preview(self):
        texts = []
        for row in self.condition_rows:
            key = row["key_var"].get().strip()
            value = row["value_var"].get().strip()
            if not key or not value:
                continue
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
            texts.append(self._rule_to_text(rule))
        op = self._op_label_to_code(self.condition_operator_var.get())
        if not texts:
            preview = "（留空或整行未填 → 无条件触发）"
        else:
            preview = (" 且 " if op == "and" else " 或 ").join(texts)
        self.cond_preview.configure(text="条件预览: " + preview)

    def _load_condition_controls(self):
        condition = self.trigger.get("condition") or self.DEFAULT_CONDITION_TEMPLATE
        self.condition_operator_var.set(self._op_code_to_label(condition.get("operator", "and")))
        for index, rule in enumerate(condition.get("rules", [])[:4]):
            row = self.condition_rows[index]
            key = rule.get("key", "")
            row["key_var"].set(key)
            row["comparator_var"].set(rule.get("comparator", ">="))
            row["value_var"].set(str(rule.get("value", "")))
            row["metric_var"].set(self._metric_code_to_label(key, rule.get("metric", "count")))
            row["target_var"].set(str(rule.get("target", "")) if rule.get("target") is not None else "")
            row["window_var"].set(str(rule.get("window", "")) if rule.get("window") is not None else "")
            self._sync_condition_row(index)

    def _collect_condition(self):
        rules = []
        for row in self.condition_rows:
            key = row["key_var"].get().strip()
            value = row["value_var"].get().strip()
            if not key and not value:
                continue
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
        return {"operator": self._op_label_to_code(self.condition_operator_var.get()), "rules": rules}

    def _load_action_controls(self, action_data):
        action_type = self.trigger.get("action_type") or action_data.get("type", "insert")
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
        elif action_type == "sensitivity":
            data.update({"attr": self.sens_attr_var.get(), "strength": self.sens_strength_var.get(),
                         "objective": self.sens_objective_var.get(), "duration": self.sens_duration_var.get()})
        elif action_type == "ending":
            data["name"] = self.ending_name_var.get().strip()
            data["intrusion_delta"] = self.ending_intrusion_var.get().strip()
            data["destruction_delta"] = self.ending_destruction_var.get().strip()
            data["casualty_step"] = self.ending_casualty_var.get().strip()
            data["action_points_refund"] = self.ending_refund_var.get().strip()
            data["custom_deltas"] = {name: var.get().strip()
                                     for name, var in self.ending_custom_delta_vars.items()}
            data["icon_path"] = self.ending_icon_var.get().strip()
        elif action_type == "background":
            data.update({"image_path": self.background_path_var.get().strip(),
                         "smooth_transition": bool(self.smooth_var.get())})
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
        elif action_type == "sensitivity":
            self.sens_attr_var.set(action_data.get("attr", "介入度"))
            self.sens_strength_var.set(str(action_data.get("strength", 1.0)))
            self.sens_objective_var.set(str(action_data.get("objective", 0.0)))
            self.sens_duration_var.set(str(action_data.get("duration", 3)))
        elif action_type == "ending":
            self.ending_name_var.set(action_data.get("name") or action_data.get("ending_text") or "")
            self._load_ending_deltas(action_data)
            self.ending_icon_var.set(action_data.get("icon_path", ""))
            self._refresh_ending_icon_preview()
        elif action_type == "background":
            self.background_path_var.set(action_data.get("image_path", ""))
            self.smooth_var.set(bool(action_data.get("smooth_transition", True)))
        self._update_action_description()

    def _update_action_description(self):
        """只显示当前动作类型的详细参数说明。"""
        title = self.ACTION_TITLES.get(self.current_action_type, "")
        description = self.ACTION_DESCRIPTIONS.get(self.current_action_type, "")
        text = f"【{title}】\n{description}" if title else ""
        self.action_description_label.configure(text=text)

    def _sensitivity_template(self):
        self._set_action_template("sensitivity", "新敏感",
                                  {"attr": "介入度", "strength": 1.0,
                                   "objective": 0.0, "duration": 3})

    def _load_ending_deltas(self, action_data):
        action_data = action_data or {}
        self.ending_intrusion_var.set(str(action_data.get("intrusion_delta", 0)))
        self.ending_destruction_var.set(str(action_data.get("destruction_delta", 0)))
        self.ending_casualty_var.set(str(action_data.get("casualty_step", 0)))
        self.ending_refund_var.set(str(action_data.get("action_points_refund", 0)))
        custom = action_data.get("custom_deltas", {}) or {}
        for name, var in self.ending_custom_delta_vars.items():
            var.set(str(custom.get(name, 0)))

    def _insert_template(self):
        self._set_action_template("insert", "新插入",
                                  {"text": "", "text_type": "background",
                                   "highlight": False, "delayed": False})

    def _option_template(self):
        self._set_action_template("option", "新选项",
                                  {"prompt": "",
                                   "options": [{"id": 0, "prompt": ""}, {"id": 1, "prompt": ""}]})

    def _ending_template(self):
        self._set_action_template("ending", "新结束", {
            "name": "",
            "intrusion_delta": 0, "destruction_delta": 0, "casualty_step": 0,
            "action_points_refund": 0,
            "custom_deltas": {name: 0 for name in self.ending_custom_delta_vars},
            "icon_path": "",
        })

    # ---------- 结局图标 ----------
    def _resolve_ending_icon_abspath(self, icon_path: str) -> str:
        """把配置的相对图标路径解析为实际文件路径（相对副本目录）。"""
        if not icon_path:
            return ""
        if os.path.isabs(icon_path):
            return icon_path
        if self.dungeon_id and self._dungeon_repo:
            candidate = os.path.normpath(
                os.path.join(self._dungeon_repo.root, self.dungeon_id, icon_path))
            if os.path.exists(candidate):
                return candidate
        return icon_path

    def _refresh_ending_icon_preview(self):
        """按当前图标路径刷新预览缩略图。"""
        path = self._resolve_ending_icon_abspath(self.ending_icon_var.get().strip())
        if not path or not os.path.exists(path):
            self._ending_icon_ctkimg = None
            self.ending_icon_preview.configure(image=None, text="（未选择图标）")
            return
        try:
            from PIL import Image
            img = Image.open(path)
            w, h = img.size
            max_side = 96
            if max(w, h) > max_side:
                scale = max_side / max(w, h)
                w, h = max(1, int(w * scale)), max(1, int(h * scale))
                img = img.resize((w, h), Image.Resampling.LANCZOS)
            self._ending_icon_ctkimg = ctk.CTkImage(light_image=img, dark_image=img, size=(w, h))
            self.ending_icon_preview.configure(image=self._ending_icon_ctkimg, text="")
        except Exception as e:
            print(f"结局图标预览失败: {e}")
            self._ending_icon_ctkimg = None
            self.ending_icon_preview.configure(image=None, text="（预览失败）")

    def _import_ending_icon(self):
        """选择 png 图标并存入副本 endings 目录，路径以相对副本目录保存。"""
        file_path = filedialog.askopenfilename(
            title="选择结局图标",
            filetypes=[("PNG 图片", "*.png"), ("图片文件", "*.png *.jpg *.jpeg *.bmp")]
        )
        if not file_path:
            return
        from PIL import Image as PILImage
        try:
            with PILImage.open(file_path) as img:
                img.load()
        except Exception as e:
            ui.common.dialogs.showerror("错误", f"图片加载失败: {e}")
            return
        if not (self.dungeon_id and self._dungeon_repo):
            ui.common.dialogs.showerror("错误", "无法解析副本目录，无法保存结局图标")
            return
        dungeon_dir = os.path.join(self._dungeon_repo.root, self.dungeon_id)
        endings_dir = os.path.join(dungeon_dir, "endings")
        os.makedirs(endings_dir, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        base = os.path.splitext(os.path.basename(file_path))[0]
        safe = re.sub(r"[^\w\u4e00-\u9fff-]", "_", base)[:40] or "ending"
        dest_path = os.path.join(endings_dir, f"{ts}_{safe}.png")
        try:
            with PILImage.open(file_path) as img:
                if img.mode in ("RGBA", "LA", "P"):
                    img.convert("RGBA").save(dest_path, "PNG")
                else:
                    img.convert("RGB").save(dest_path, "PNG")
        except Exception as e:
            ui.common.dialogs.showerror("错误", f"保存结局图标失败: {e}")
            return
        rel = os.path.relpath(dest_path, dungeon_dir).replace("\\", "/")
        self.ending_icon_var.set(rel)
        self._refresh_ending_icon_preview()

    def _clear_ending_icon(self):
        self.ending_icon_var.set("")
        self._refresh_ending_icon_preview()

    def _import_background(self):
        file_path = filedialog.askopenfilename(
            title="选择背景图片",
            filetypes=[("图片文件", "*.png *.jpg *.jpeg *.gif *.bmp *.webp")]
        )
        if not file_path:
            return

        # 先弹出裁剪对话框（固定 16:9 比例）
        try:
            crop_dlg = ImageCropDialog(self, file_path, mode=ImageCropDialog.MODE_BACKGROUND)
        except Exception as e:
            ui.common.dialogs.showerror("错误", f"图片加载失败: {e}")
            return
        cropped_path = crop_dlg.get_cropped_path()
        if not cropped_path:
            return  # 用户取消裁剪

        # 背景动作默认开启平滑切换
        self.smooth_var.set(True)

        # 若指定了副本ID且存在 dungeon_repo，则将裁剪结果存入副本 images 目录
        if self.dungeon_id and self._dungeon_repo:
            dungeon_root = self._dungeon_repo.root
            dungeon_dir = os.path.join(dungeon_root, self.dungeon_id)
            if not os.path.exists(dungeon_dir):
                ui.common.dialogs.showerror("错误", f"副本目录不存在: {dungeon_dir}")
                return

            # 确保 images 子目录存在
            images_dir = os.path.join(dungeon_dir, "images")
            os.makedirs(images_dir, exist_ok=True)

            basename = os.path.basename(file_path)
            if os.path.splitext(basename)[1].lower() not in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"):
                basename += ".png"
            dest_path = os.path.join(images_dir, basename)
            # 若目标已存在，询问是否覆盖
            if os.path.exists(dest_path):
                if not ui.common.dialogs.askyesno("文件已存在", f"图片 {basename} 已存在，是否覆盖？"):
                    return
            try:
                self._save_cropped_image(cropped_path, dest_path)
            except Exception as e:
                ui.common.dialogs.showerror("错误", f"保存裁剪图片失败: {e}")
                return
            rel_path = os.path.relpath(dest_path, dungeon_dir)

            # 更新 action_data 并刷新编辑框（默认开启平滑切换）
            action_data = {"type": "background", "image_path": rel_path, "smooth_transition": True}
            self._refresh_action_ui("background", action_data)
        else:
            # 无副本信息，使用裁剪后的临时路径（兼容旧版本）
            self._refresh_action_ui("background",
                                    {"image_path": cropped_path, "smooth_transition": True})

    def _save_cropped_image(self, cropped_path, dest_path):
        """将裁剪结果按目标扩展名保存，并清理临时文件"""
        from PIL import Image as PILImage
        ext = os.path.splitext(dest_path)[1].lower()
        fmt_map = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG",
                   ".bmp": "BMP", ".gif": "GIF", ".webp": "WEBP"}
        fmt = fmt_map.get(ext, "PNG")
        try:
            with PILImage.open(cropped_path) as img:
                if fmt in ("JPEG", "BMP", "GIF"):
                    img.convert("RGB").save(dest_path, format=fmt)
                else:
                    img.save(dest_path, format=fmt)
        finally:
            try:
                os.remove(cropped_path)
            except OSError:
                pass

    # ---------- 确定 ----------
    def _ok(self):
        name = self.name_var.get().strip()
        if not name:
            ui.common.dialogs.showerror("错误", "名称不能为空")
            return

        # 校验名称唯一性（排除自身）
        original_name = self.trigger.get("name", "")
        for t in self.all_triggers:
            if t.get("name") == name and t.get("name") != original_name:
                ui.common.dialogs.showerror("错误", f"名称 '{name}' 已被其他触发器使用")
                return

        # 解析前置条件名称
        pre_names = []
        if self.pre_var.get().strip():
            for part in self.pre_var.get().split(','):
                part = part.strip()
                if part:
                    pre_names.append(part)

        # 校验前置条件是否存在，并检查循环依赖
        existing_names = {t["name"] for t in self.all_triggers if t.get("name") != name}
        for pn in pre_names:
            if pn not in existing_names and pn != name:  # 自身不能作为前置条件
                ui.common.dialogs.showerror("错误", f"前置条件名称 '{pn}' 不存在")
                return
            if pn == name:
                ui.common.dialogs.showerror("错误", "触发器不能将自身作为前置条件")
                return
        # 简单循环依赖检测（A->B, B->A）
        # 构建依赖图，从当前名称开始，检查是否形成环（包括间接）
        # 这里只做一次直接检查，更复杂需要递归，但暂不实现

        try:
            condition = self._collect_condition()
            action_type = self.current_action_type
            action_data = self._collect_action_data()
        except ValueError as exc:
            ui.common.dialogs.showerror("输入有误", str(exc))
            return

        # 平滑切换开关（仅背景动作生效）
        if action_type == "background":
            action_data["smooth_transition"] = bool(self.smooth_var.get())

        # 插入段落表单字段（仅插入动作生效）
        if action_type == "insert":
            action_data["text"] = self.insert_text.get("1.0", "end-1c").strip()
            action_data["text_type"] = self.insert_type_var.get()
            action_data["highlight"] = bool(self.highlight_var.get())
            action_data["delayed"] = bool(self.delayed_var.get())

        # 选项表单字段（仅选项动作生效）
        if action_type == "option":
            action_data["options"] = [{"id": i, "prompt": entry.get().strip()}
                                       for i, entry in enumerate(self.option_entries)
                                       if entry.get().strip()]

        # 敏感表单字段（仅敏感动作生效）
        if action_type == "sensitivity":
            action_data["attr"] = self.sens_attr_var.get()
            try:
                action_data["strength"] = float(self.sens_strength_var.get())
                action_data["objective"] = float(self.sens_objective_var.get())
                action_data["duration"] = int(float(self.sens_duration_var.get()))
            except ValueError:
                ui.common.dialogs.showerror("错误", "强度、客观影响、持续步数必须是数字")
                return

        # 结局表单字段（仅结局动作生效）
        if action_type == "ending":
            action_data["name"] = self.ending_name_var.get().strip()
            for field, var in (("介入度增量", self.ending_intrusion_var),
                               ("破坏性增量", self.ending_destruction_var),
                               ("伤亡步进", self.ending_casualty_var)):
                try:
                    float(var.get())
                except ValueError:
                    ui.common.dialogs.showerror("错误", f"结局的{field}必须是数字")
                    return
            for name, var in self.ending_custom_delta_vars.items():
                try:
                    float(var.get())
                except ValueError:
                    ui.common.dialogs.showerror("错误", f"自定义属性 {name} 的增量必须是数字")
                    return
            try:
                int(float(self.ending_refund_var.get()))
            except ValueError:
                ui.common.dialogs.showerror("错误", "结局的行动点数返还必须是数字")
                return

        # 验证 condition 结构
        if not isinstance(condition, dict):
            ui.common.dialogs.showerror("错误", "condition 必须是字典")
            return
        rules = condition.get("rules", [])
        if not isinstance(rules, list):
            ui.common.dialogs.showerror("错误", "condition.rules 必须是列表")
            return
        allowed_keys = set(self.evolution_names) | {"介入度", "破坏性", "总伤亡", "总计数", "间隔计数", "伤亡数组"}
        # 只有选项分支触发器才维护选择数组
        allowed_keys |= {f"选择:{t['name']}" for t in self.all_triggers
                         if t.get("name") and t.get("action_type") == "option"}
        if action_type == "option":
            allowed_keys.add(f"选择:{name}")
        allowed_comparators = {">=", "<=", ">", "<", "==", "!="}
        for rule in rules:
            if not isinstance(rule, dict):
                ui.common.dialogs.showerror("错误", "规则项必须是字典")
                return
            key = rule.get("key")
            if not key or key not in allowed_keys:
                ui.common.dialogs.showerror("错误", f"规则中的 key '{key}' 不在允许列表中")
                return
            if rule.get("comparator", ">=") not in allowed_comparators:
                ui.common.dialogs.showerror("错误", "规则中的 comparator 不受支持")
                return
            metric = rule.get("metric", "count")
            if key.startswith("选择:"):
                allowed_metrics = {"count", "ratio", "trend", "last"}
            else:
                allowed_metrics = {"count", "avg", "trend", "last"}
            if metric not in allowed_metrics:
                ui.common.dialogs.showerror("错误", f"规则中的 metric '{metric}' 不受支持")
                return
            if key.startswith("选择:") and metric in ("ratio", "trend", "last"):
                try:
                    target = rule.get("target")
                    if target is None or int(target) < 0:
                        raise ValueError
                except (TypeError, ValueError):
                    ui.common.dialogs.showerror("错误", f"规则 {key} 的 metric='{metric}' 需要非负整数 target（目标选项编号）")
                    return
                if metric == "ratio":
                    try:
                        ratio_value = float(rule.get("value", 0))
                    except (TypeError, ValueError):
                        ratio_value = -1.0
                    if not 0.0 <= ratio_value <= 1.0:
                        ui.common.dialogs.showerror("错误", f"规则 {key} 的 ratio 指标 value 必须在 0~1 之间")
                        return
            if metric == "trend" and rule.get("window") is not None:
                try:
                    if int(rule["window"]) < 2:
                        raise ValueError
                except (TypeError, ValueError):
                    ui.common.dialogs.showerror("错误", f"规则 {key} 的 trend 指标 window 必须是 >=2 的整数")
                    return

        # 验证 action_data
        if not isinstance(action_data, dict):
            ui.common.dialogs.showerror("错误", "action_data 必须是字典")
            return
        evo_dir = action_data.get("evolution_directions")
        if evo_dir is not None:
            if not isinstance(evo_dir, dict):
                ui.common.dialogs.showerror("错误", "evolution_directions 必须是字典")
                return
            for evo_name, delta in evo_dir.items():
                if evo_name not in self.evolution_names:
                    ui.common.dialogs.showerror("错误", f"演化量 '{evo_name}' 不存在")
                    return
                if not isinstance(delta, (int, float)):
                    ui.common.dialogs.showerror("错误", f"演化量 '{evo_name}' 的变化值必须是数字")
                    return

        # 特定类型验证（除background外均需prompt）
        if action_type == "insert" and not action_data.get("text"):
            ui.common.dialogs.showerror("错误", "插入动作必须填写插入段落文本")
            return
        if action_type == "option" and (not action_data.get("options")
                                        or len(action_data["options"]) < 2):
            ui.common.dialogs.showerror("错误", "选项动作至少需要 2 个选项")
            return
        if action_type == "ending" and not action_data.get("name"):
            ui.common.dialogs.showerror("错误", "结局动作必须填写结局名称")
            return
        if action_type == "option" and ("options" not in action_data or not isinstance(action_data["options"], list)):
            ui.common.dialogs.showerror("错误", "选项类型必须包含 options 列表")
            return
        if action_type == "background" and "image_path" not in action_data:
            ui.common.dialogs.showerror("错误", "背景图必须指定 image_path")
            return

        self.result = {
            "name": name,
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
