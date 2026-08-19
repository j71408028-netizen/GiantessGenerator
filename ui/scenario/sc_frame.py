import ui.common.dialogs
import customtkinter as ctk
import re
import copy
import os
from dungeon.rules import EvolutionRules
from ui.common.widgets import CTkScrollableDropdownFrame, CTkSegmentedControl
from ui.common.dialogs import BaseDialog, InputDialog
from ui.scenario.evolution_attributes import EvolutionAtrrManager
from ui.scenario.trigger_mgr import TriggerManager
from ui.common.theme import (
    BORDER, BORDER_ALT, PNL_BG, HARD_TITLE, TEXT, SOFT,
    HOVER, HOVER_ALT, MENU_HOVER, STATUS_OK, OK_HOVER, ERR_STRONG, ERR_HOVER,
    BLUE_HOVER, LINK_BLUE, BASE,
)
from ui.common import fonts as ui_fonts


# ==================== 副本编辑器 ====================
class ScenarioEditor(ctk.CTkFrame):
    """副本编辑器：通用区域 + 两个 TreeviewManager 子面板（属性/触发器）"""

    def __init__(self, parent, dungeon_repo, gui_ref, challenge_mgr=None):
        super().__init__(parent, fg_color="transparent")
        self._dungeon_repo = dungeon_repo
        self._challenge_mgr = challenge_mgr
        self.gui = gui_ref
        self.current_scenario_id = None
        self.initial_prompt = ""
        self.section_prompts = {}
        self.view_mode = "story"
        self.evolution_attrs = []  # 统一演化量列表
        self.triggers = []
        self._modified = False
        self._saved_prompt_snapshot = {}  # 用于检测提示词是否被修改
        self._prompt_modified = False  # 辅助标志（实际用快照对比更准确）

        # --- 新增：自定义步进值与转移矩阵 ---
        self.section_steps = {}
        self.transition_matrix = None

        # 进入副本所需行动点数（0 表示免费）
        self.entry_action_cost = 0

        self._build_ui()
        self._refresh_scenario_list()
        self._load_scenario("_default")

    def _build_ui(self):
        toolbar = ctk.CTkFrame(self, fg_color=BASE)
        toolbar.pack(fill='x', padx=10, pady=6)
        self.dungeon_switch_btn = CTkSegmentedControl(
            toolbar,
            values=[" 通用 ", "演化量", "触发器"],
            command=self._on_scenario_tab_switch,
            width=160, font=ui_fonts.ui_font(12)
        )
        self.dungeon_switch_btn.pack(side='left', padx=5, pady=(3, 1))
        # 初始化即选中“通用”
        self.dungeon_switch_btn.set(" 通用 ")
        right_frame = ctk.CTkFrame(toolbar, fg_color="transparent")
        right_frame.pack(side='right', padx=5)
        ctk.CTkLabel(right_frame, text="副本方案:",
                     text_color=SOFT).pack(side='left', padx=5)
        # ---- CTkComboBox + CTkScrollableDropdownFrame ----
        self.scenario_combo = ctk.CTkComboBox(
            right_frame,
            values=[],
            state="readonly",
            width=150,
            fg_color=PNL_BG,
            border_color=BORDER_ALT,
            button_color=BORDER_ALT,
            button_hover_color=MENU_HOVER,
            dropdown_fg_color=PNL_BG,
            dropdown_hover_color=HOVER_ALT
        )
        self.scenario_combo.pack(side='left', padx=7)
        self._rebuild_dropdown()

        _btn_spec = {"fg_color": "transparent", "border_width": 1, "corner_radius": 8}
        _btn_muted = {"text_color": SOFT,
                      "hover_color": HOVER_ALT,
                      "border_color": BORDER_ALT}
        ctk.CTkButton(right_frame, text="新建", width=80, command=self._new_scenario,
                       text_color=STATUS_OK,
                       hover_color=OK_HOVER,
                       border_color=STATUS_OK,
                       **_btn_spec).pack(side='left', padx=2)
        ctk.CTkButton(right_frame, text="重命名", width=80, command=self._rename_scenario,
                       **_btn_spec, **_btn_muted).pack(side='left', padx=2)
        _del_spec = {"text_color": ERR_STRONG,
                     "hover_color": ERR_HOVER,
                     "border_color": ERR_STRONG}
        ctk.CTkButton(right_frame, text="删除", width=80, command=self._delete_scenario,
                       **_btn_spec, **_del_spec).pack(side='left', padx=2)

        # 内容容器
        self.content_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.content_frame.pack(fill='both', expand=True, padx=10, pady=10)

        # 创建三个面板
        self.prompt_panel = ctk.CTkFrame(self.content_frame, fg_color="transparent")
        self._build_prompt_ui(self.prompt_panel)  # 重写 UI 构建

        self.evolution_panel = EvolutionAtrrManager(self.content_frame, self._dungeon_repo, self)
        self.trigger_panel = TriggerManager(self.content_frame, self._dungeon_repo, self)

        # 默认显示通用面板
        self.evolution_panel.pack_forget()
        self.trigger_panel.pack_forget()
        self.prompt_panel.pack(fill='both', expand=True)

    def _build_combined_items(self):
        schemes = self._dungeon_repo.list_all()
        items = list(schemes)
        text_colors = {}
        self._pack_display_map = {}
        if self._challenge_mgr and self._challenge_mgr.has_any_valid_key():
            packs = self._challenge_mgr.get_available_packs()
            for pack in packs:
                display = f"⚔ {os.path.splitext(pack)[0]}"
                items.append(display)
                text_colors[display] = LINK_BLUE
                self._pack_display_map[display] = pack
        return items, text_colors

    def _rebuild_dropdown(self):
        items, text_colors = self._build_combined_items()
        self.scenario_combo.configure(values=items if items else [])
        if hasattr(self, '_scenario_dropdown') and self._scenario_dropdown:
            self._scenario_dropdown.configure(values=items, text_colors=text_colors)
        else:
            _FG = PNL_BG
            _BD = BORDER_ALT
            _HOVER = HOVER_ALT
            self._scenario_dropdown = CTkScrollableDropdownFrame(
                attach=self.scenario_combo,
                values=items,
                text_colors=text_colors,
                command=self._on_dropdown_select,
                height=200, button_height=28,
                fg_color=_FG,
                hover_color=BLUE_HOVER,
                scrollbar_button_color=_HOVER,
                scrollbar_button_hover_color=_HOVER,
                frame_border_color=_BD,
                text_color=TEXT,
                button_color=_FG,
                frame_border_width=1,
                justify="left"
            )

    def _on_dropdown_select(self, choice):
        if choice in getattr(self, '_pack_display_map', {}):
            pack_name = self._pack_display_map[choice]
            self._on_scenario_challenge_pack_select(pack_name)
        else:
            self._on_scenario_select(choice)

    def _on_scenario_challenge_pack_select(self, choice):
        if not choice:
            return
        file_path = self._challenge_mgr.resolve_pack_path(choice)
        if file_path is None:
            ui.common.dialogs.showerror("错误", f"无法读取挑战包 '{choice}'，未找到对应秘钥")
            return
        result = self._challenge_mgr.try_open_with_keys(file_path)
        if result is None:
            ui.common.dialogs.showerror("错误", f"无法读取挑战包 '{choice}'，未找到对应秘钥")
            return
        data, _, _ = result
        if not data:
            return
        scenario_config = data.get("dungeon_config", {})
        scenario_id = data.get("dungeon_id", "")
        if not scenario_config:
            ui.common.dialogs.showinfo("提示", "该挑战包中没有副本配置")
            return

        if self._dungeon_repo.exists(scenario_id):
            self._dungeon_repo.save_config(scenario_id, scenario_config)
        else:
            self._dungeon_repo.create(scenario_id, scenario_config)

        self._refresh_scenario_list()
        self._load_scenario(scenario_id)
        ui.common.dialogs.showinfo("成功", f"已从挑战包加载副本 '{scenario_id}'")

    def _on_scenario_tab_switch(self, value):
        # 隐藏所有面板
        self.prompt_panel.pack_forget()
        self.evolution_panel.pack_forget()
        self.trigger_panel.pack_forget()

        if value == " 通用 ":
            self.prompt_panel.pack(fill='both', expand=True)
        elif value == "演化量":
            self.evolution_panel.pack(fill='both', expand=True)
        elif value == "触发器":
            self.trigger_panel.pack(fill='both', expand=True)

    # ------------------ 新版通用面板 ------------------
    def _build_prompt_ui(self, parent):
        """通用面板：副本视图/进入消耗/初始提示/段落分类提示，卡片式布局，内容可滚动"""
        for child in parent.winfo_children():
            child.destroy()

        # 与其他管理页面保持一致的配色
        _CARD_BORDER = BORDER
        _CARD_BG = "transparent"
        _TITLE = HARD_TITLE
        _MUTED = SOFT
        _DARK = TEXT

        def _section_card(master):
            card = ctk.CTkFrame(master, fg_color=_CARD_BG,
                                border_width=1, corner_radius=12,
                                border_color=_CARD_BORDER)
            card.pack(fill='x', pady=4)
            inner = ctk.CTkFrame(card, fg_color="transparent")
            inner.pack(fill='x', padx=12, pady=10)
            return inner

        # ---------- 底部按钮 ----------
        bottom_frame = ctk.CTkFrame(parent, fg_color="transparent")
        bottom_frame.pack(side='bottom', fill='x', pady=(5, 5))
        ctk.CTkButton(bottom_frame, text="转移设置", width=80,
                      fg_color="transparent", border_width=1, corner_radius=8,
                      text_color=_MUTED,
                      hover_color=HOVER,
                      border_color=BORDER_ALT,
                      font=ui_fonts.ui_font(12),
                      command=lambda: self._edit_transition_matrix(None)).pack(side='left', padx=5)
        ctk.CTkButton(bottom_frame, text="保存此页面", width=120,
                      fg_color="transparent", border_width=2, corner_radius=10,
                      text_color=STATUS_OK,
                      hover_color=OK_HOVER,
                      border_color=STATUS_OK,
                      font=ui_fonts.ui_font(12, "bold"),
                      command=self._save_prompts).pack(side='right', padx=5)

        # ---------- 可滚动内容区 ----------
        scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        scroll.pack(fill='both', expand=True, pady=(0, 4))

        # ---------- 副本窗口视图 ----------
        view_card = ctk.CTkFrame(scroll, fg_color=_CARD_BG,
                                 border_width=1, corner_radius=12,
                                 border_color=_CARD_BORDER)
        view_card.pack(fill='x', pady=4)
        view_inner = ctk.CTkFrame(view_card, fg_color="transparent")
        view_inner.pack(fill='x', padx=12, pady=10)
        ctk.CTkLabel(view_inner, text="副本窗口视图:", font=ui_fonts.ui_font(12),
                     text_color=_TITLE).pack(side='left')
        self.view_mode_segment = CTkSegmentedControl(
            view_inner, values=["故事", "Galgame"], command=self._on_view_mode_changed,
            width=140, height=23, font=ui_fonts.ui_font(12)
        )
        self.view_mode_segment.pack(side='left', padx=10)

        ctk.CTkLabel(view_inner, text="行动点数消耗:", font=ui_fonts.ui_font(12),
                     text_color=_TITLE).pack(side='left', padx=(100,10))
        self.entry_cost_var = ctk.StringVar(value="0")
        self.entry_cost_entry = ctk.CTkEntry(view_inner, textvariable=self.entry_cost_var,
                                             width=70, font=ui_fonts.ui_font(12),
                                             text_color=_DARK,
                                             fg_color=PNL_BG,
                                             border_color=BORDER_ALT)
        self.entry_cost_entry.pack(side='left')

        # ---------- 初始提示 ----------
        initial_inner = _section_card(scroll)
        ctk.CTkLabel(initial_inner, text="初始提示（角色设定/世界背景）", font=ui_fonts.ui_font(12, "bold"),
                     text_color=_TITLE).pack(anchor='w')
        self.initial_prompt_text = ctk.CTkTextbox(initial_inner, height=100, wrap='word',
                                                  font=ui_fonts.ui_font(12),
                                                  text_color=_DARK,
                                                  fg_color=PNL_BG,
                                                  border_width=1,
                                                  border_color=BORDER_ALT)
        self.initial_prompt_text.pack(fill='x', pady=(6, 0))

        # ---------- 段落分类提示（含转移设置） ----------
        sections_inner = _section_card(scroll)
        title_row = ctk.CTkFrame(sections_inner, fg_color="transparent")
        title_row.pack(fill='x')
        ctk.CTkLabel(title_row, text="段落分类提示（针对不同文本类型）", font=ui_fonts.ui_font(12, "bold"),
                     text_color=_TITLE).pack(side='left')

        # ---------- 各分类行 ----------
        self.section_frames = {}  # key -> CTkTextbox
        self.section_step_vars = {}  # key -> DoubleVar

        section_types = [
            ("background", "背景"),
            ("branch", "分支"),
            ("dialog", "对话"),
            ("interaction", "互动"),
            ("action", "行动")
        ]

        for key, label in section_types:
            row_frame = ctk.CTkFrame(sections_inner, fg_color="transparent")
            row_frame.pack(fill='x', pady=4)

            # ---- 左侧：分类名 + 步进输入（固定宽度） ----
            left_frame = ctk.CTkFrame(row_frame, fg_color="transparent", width=120, height=70)
            left_frame.pack(side='left', fill='y', padx=(0, 5))
            left_frame.pack_propagate(False)

            ctk.CTkLabel(left_frame, text=label, font=ui_fonts.ui_font(12, "bold"), anchor='w').pack(anchor='w', pady=(0, 1))

            step_container = ctk.CTkFrame(left_frame, fg_color="transparent")
            step_container.pack(anchor='w')

            ctk.CTkLabel(step_container, text="步进:", font=ui_fonts.ui_font(12)).pack(side='left', padx=(0, 4))

            step_var = ctk.DoubleVar(value=self._default_step_for(key))
            step_entry = ctk.CTkEntry(step_container, textvariable=step_var, width=55)
            step_entry.pack(side='left')
            self.section_step_vars[key] = step_var

            # ---- 右侧：提示文本框 ----
            textbox = ctk.CTkTextbox(row_frame, height=65, wrap='word',
                                     font=ui_fonts.ui_font(12),
                                     text_color=_DARK,
                                     fg_color=PNL_BG,
                                     border_width=1,
                                     border_color=BORDER_ALT)
            textbox.pack(side='left', fill='x', expand=True, padx=(0, 5))
            self.section_frames[key] = textbox

    # ------------------ 转移矩阵编辑对话框 ------------------
    def _edit_transition_matrix(self, section_key):
        """打开转移矩阵编辑窗口（编辑整个矩阵）"""
        dialog = BaseDialog(self)
        dialog.title("编辑转移概率矩阵")
        dialog.geometry("450x350")
        dialog.transient(self)
        dialog.grab_set()

        types = ["background", "branch", "dialog", "interaction", "action"]
        matrix = self.transition_matrix if self.transition_matrix else self._default_transition_matrix()

        # 创建变量网格
        vars_dict = {}
        for src in types:
            vars_dict[src] = {}
            for dst in types:
                val = matrix.get(src, {}).get(dst, 0.0)
                vars_dict[src][dst] = ctk.DoubleVar(value=val)

        # 主框架
        main_frame = ctk.CTkFrame(dialog)
        main_frame.pack(fill='both', expand=True, padx=10, pady=10)

        # 列标题
        ctk.CTkLabel(main_frame, text="源\\目标", font=ui_fonts.ui_font(10, "bold")).grid(row=0, column=0, padx=5, pady=5)
        for col, dst in enumerate(types, start=1):
            ctk.CTkLabel(main_frame, text=dst, font=ui_fonts.ui_font(10, "bold")).grid(row=0, column=col, padx=5, pady=5)

        # 行
        for row, src in enumerate(types, start=1):
            ctk.CTkLabel(main_frame, text=src, font=ui_fonts.ui_font(10, "bold")).grid(row=row, column=0, padx=5, pady=5)
            for col, dst in enumerate(types, start=1):
                entry = ctk.CTkEntry(main_frame, textvariable=vars_dict[src][dst], width=60)
                entry.grid(row=row, column=col, padx=2, pady=2)

        # 按钮区域
        btn_frame = ctk.CTkFrame(dialog)
        btn_frame.pack(pady=10)
        ctk.CTkButton(btn_frame, text="保存",
                      command=lambda: self._save_transition_matrix(dialog, vars_dict, types)).pack(side='left', padx=10)
        ctk.CTkButton(btn_frame, text="取消", command=dialog.destroy).pack(side='left', padx=10)
        ctk.CTkButton(btn_frame, text="重置默认", command=lambda: self._reset_transition_matrix(vars_dict, types)).pack(
            side='left', padx=10)

        dialog._center_dialog(self)

    def _save_transition_matrix(self, dialog, vars_dict, types):
        """保存矩阵并自动归一化每行"""
        new_matrix = {}
        for src in types:
            row = {}
            total = 0.0
            for dst in types:
                val = vars_dict[src][dst].get()
                if val < 0:
                    val = 0.0
                row[dst] = val
                total += val
            if total == 0:
                for dst in types:
                    row[dst] = 1.0 / len(types)
            else:
                for dst in types:
                    row[dst] /= total
            new_matrix[src] = row
        self.transition_matrix = new_matrix
        dialog.destroy()

    def _reset_transition_matrix(self, vars_dict, types):
        default = EvolutionRules().transition_matrix  # 使用 EvolutionRules 默认矩阵
        for src in types:
            for dst in types:
                vars_dict[src][dst].set(default.get(src, {}).get(dst, 0.0))

    def _default_transition_matrix(self):
        """返回默认转移矩阵（字符串键）"""
        return copy.deepcopy(EvolutionRules.DEFAULT_TRANSITION_MATRIX)

    def _default_step_for(self, key):
        """返回默认步进值"""
        steps = {
            "background": 0.02,
            "branch": 0.05,
            "dialog": 0.1,
            "interaction": 0.2,
            "action": 0.3
        }
        return steps.get(key, 0.1)

    def get_scenario_logic(self) -> EvolutionRules:
        """返回基于当前副本配置的 EvolutionRules 实例"""
        return EvolutionRules(transition_matrix=self.transition_matrix,
                              step_overrides=self.section_steps)

    def _refresh_ui_from_config(self):
        """更新所有UI控件（包括步进值）"""
        self.initial_prompt_text.delete("1.0", "end")
        self.initial_prompt_text.insert("1.0", self.initial_prompt)
        self.view_mode_segment.set("游戏视图" if self.view_mode == "game" else "故事视图")
        self.entry_cost_var.set(str(int(self.entry_action_cost)))

        for key, box in self.section_frames.items():
            box.delete("1.0", "end")
            box.insert("1.0", self.section_prompts.get(key, ""))

        # 更新步进值
        for key, var in self.section_step_vars.items():
            step = self.section_steps.get(key, self._default_step_for(key))
            var.set(step)

        # 转移矩阵无需显示，但已保存在 self.transition_matrix 中

        self.evolution_panel.refresh_list()
        self.trigger_panel.refresh_list()
        self._update_prompt_snapshot()

    def _load_scenario(self, scenario_id):
        config = self._dungeon_repo.load_config(scenario_id)
        if config is None:
            return
        self.initial_prompt = config.get("initial_prompt", "")
        self.section_prompts = config.get("section_prompts", {})
        self.view_mode = config.get("view_mode", "story")
        if self.view_mode not in ("story", "game"):
            self.view_mode = "story"
        self.evolution_attrs = config.get("evolution_attrs", [])
        self.triggers = config.get("triggers", [])
        self.entry_action_cost = max(0, int(config.get("entry_action_cost", 0) or 0))

        # 加载步进值和转移矩阵，缺失则用默认
        self.section_steps = config.get("section_steps", {})
        for key in ["background","branch","dialog","interaction","action"]:
            if key not in self.section_steps:
                self.section_steps[key] = self._default_step_for(key)

        self.transition_matrix = config.get("transition_matrix")
        if not self.transition_matrix:
            self.transition_matrix = self._default_transition_matrix()

        self._refresh_ui_from_config()
        self.current_scenario_id = scenario_id
        self.scenario_combo.set(scenario_id)

    def _get_prompt_data_from_ui(self) -> dict:
        """从UI控件收集提示词相关数据"""
        try:
            entry_cost = max(0, int(self.entry_cost_var.get()))
        except Exception:
            entry_cost = self.entry_action_cost
        return {
            "initial_prompt": self.initial_prompt_text.get("1.0", "end-1c").strip(),
            "view_mode": self.view_mode,
            "section_prompts": {key: box.get("1.0", "end-1c").strip()
                                for key, box in self.section_frames.items()},
            "section_steps": {key: var.get() for key, var in self.section_step_vars.items()},
            "transition_matrix": self.transition_matrix,
            "entry_action_cost": entry_cost
        }

    def _save_prompts(self):
        """仅保存提示词部分，不修改演化量和触发器"""
        if not self.current_scenario_id:
            return
        # 加载现有配置
        config = self._dungeon_repo.load_config(self.current_scenario_id)
        if config is None:
            config = {}
        # 更新提示词字段
        prompt_data = self._get_prompt_data_from_ui()
        config["initial_prompt"] = prompt_data["initial_prompt"]
        config["view_mode"] = prompt_data["view_mode"]
        config["section_prompts"] = prompt_data["section_prompts"]
        config["section_steps"] = prompt_data["section_steps"]
        config["transition_matrix"] = prompt_data["transition_matrix"]
        config["entry_action_cost"] = prompt_data["entry_action_cost"]
        # 保存（保留 evolution_attrs 和 triggers）
        self._dungeon_repo.save_config(self.current_scenario_id, config)
        # 更新快照
        self._update_prompt_snapshot()
        ui.common.dialogs.showinfo("成功", "通用设置已保存")

    def _save_evolution_triggers(self):
        """仅保存演化量和触发器，不修改提示词"""
        if not self.current_scenario_id:
            return
        config = self._dungeon_repo.load_config(self.current_scenario_id)
        if config is None:
            config = {}
        config["evolution_attrs"] = self.evolution_attrs
        config["triggers"] = self.triggers
        self._dungeon_repo.save_config(self.current_scenario_id, config)
        # 不更新提示词快照

    def _rename_scenario(self):
        old_name = self.current_scenario_id
        if old_name == "_default":
            ui.common.dialogs.showwarning("警告", "默认副本不可重命名")
            return

        dlg = InputDialog(self, title="重命名副本", prompt=f"将 '{old_name}' 重命名为:")
        new_name = dlg.get_input()
        if not new_name or not re.match(r'^\w+$', new_name):
            return

        if self._dungeon_repo.exists(new_name):
            ui.common.dialogs.showerror("错误", "副本名称已存在")
            return

        # 加载旧配置并保存为新名称，再删除旧配置
        config = self._dungeon_repo.load_config(old_name)
        if config is None:
            ui.common.dialogs.showerror("错误", f"无法加载副本 '{old_name}'")
            return

        try:
            self._dungeon_repo.save_config(new_name, config)
            self._dungeon_repo.delete(old_name)
            self._refresh_scenario_list()
            self._load_scenario(new_name)
        except Exception as e:
            ui.common.dialogs.showerror("错误", f"重命名失败: {e}")

    def _delete_scenario(self):
        if self.current_scenario_id == "_default":
            ui.common.dialogs.showwarning("警告", "默认副本不可删除")
            return
        if not ui.common.dialogs.askyesno("确认", f"确定删除副本 '{self.current_scenario_id}' 吗？\n此操作不可恢复！"):
            return
        try:
            self._dungeon_repo.delete(self.current_scenario_id)
            self._refresh_scenario_list()
            self._load_scenario("_default")
        except Exception as e:
            ui.common.dialogs.showerror("错误", str(e))

    def _new_scenario(self):
        dlg = InputDialog(self, title="新建副本", prompt="请输入新副本名称:")
        new_id = dlg.get_input()
        if not new_id or not re.match(r'^\w+$', new_id):
            return
        if self._dungeon_repo.exists(new_id):
            ui.common.dialogs.showerror("错误", "副本已存在")
            return
        empty_config = {
            "initial_prompt": "",
            "section_prompts": {k: "" for k in ["background","branch","dialog","interaction","action"]},
            "custom_attrs": [],
            "triggers": [],
            "entry_action_cost": 0
        }
        if self._dungeon_repo.create(new_id, empty_config):
            self._refresh_scenario_list()
            self._load_scenario(new_id)
        else:
            ui.common.dialogs.showerror("错误", "创建失败")

    def _update_prompt_snapshot(self):
        """用当前UI内容更新快照（用于对比）"""
        self._saved_prompt_snapshot = self._get_prompt_data_from_ui()

    def _is_prompt_modified(self) -> bool:
        """对比当前UI与快照，判断提示词是否被修改"""
        current = self._get_prompt_data_from_ui()
        return current != self._saved_prompt_snapshot

    def _on_scenario_select(self, choice):
        if self.current_scenario_id == choice:
            return
        # 如果提示词有修改，询问是否保存
        if self._is_prompt_modified():
            if ui.common.dialogs.askyesno("通用设置未保存", "当前通用设置已修改，是否保存？", parent=self):
                self._save_prompts()
            # 如果用户选择“否”，则放弃修改（快照不变）
        # 加载新副本
        self._load_scenario(choice)

    def _refresh_scenario_list(self):
        dungeon_list = self._dungeon_repo.list_all()
        self._rebuild_dropdown()
        if self.current_scenario_id not in dungeon_list:
            self.current_scenario_id = dungeon_list[0] if dungeon_list else None
            self.scenario_combo.set(self.current_scenario_id or "")
            if self.current_scenario_id:
                self._load_scenario(self.current_scenario_id)

    def _mark_modified(self):
        # 仅用于标识，暂不需要额外操作，因为保存时重新收集
        pass

    def _on_view_mode_changed(self, value):
        self.view_mode = "game" if value == "游戏视图" else "story"


