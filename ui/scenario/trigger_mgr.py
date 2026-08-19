from typing import Optional

import customtkinter as ctk

import ui.common
from ui.common.dialogs import BaseDialog
from ui.common.managers import TreeviewManager
from ui.scenario.trigger_dlg import TriggerEditDialog
from ui.common.theme import SOFT, HOVER, BORDER_ALT, STATUS_ERR, STATUS_OK
from ui.common import fonts as ui_fonts


class TriggerManager(TreeviewManager):
    """触发器管理面板（名称唯一，无数字ID，前置条件使用名称列表）"""
    def __init__(self, parent, repository, scenario_editor_ref):
        self.scenario_editor = scenario_editor_ref
        columns = [
            ("名称", 150, "name"),
            ("条件", 200, "condition_str"),
            ("动作类型", 120, "action_type"),
            ("重复触发", 80, "repeatable"),
            ("前置条件", 200, "precondition_str"),
        ]
        super().__init__(parent, repository, columns, item_name="触发器")
        # 隐藏顶部工具栏（因为本面板无需额外控件）
        self.toolbar_frame.pack_forget()
        self._build_dependency_bar()
        self.refresh_list()

    def _build_dependency_bar(self):
        """面板下方的依赖检查栏：状态提示 + “检查依赖”按钮"""
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill='x', padx=9, pady=(0, 5))
        self.dep_status_label = ctk.CTkLabel(
            bar, text="点击 “检查依赖” 查看触发器前置依赖与循环依赖",
            font=ui_fonts.ui_font(12),
            text_color=SOFT)
        self.dep_status_label.pack(side='left', padx=(4, 5))
        ctk.CTkButton(
            bar, text="检查依赖", width=100,
            fg_color="transparent", border_width=1, corner_radius=8,
            font=ui_fonts.ui_font(13),
            text_color=SOFT,
            hover_color=HOVER,
            border_color=BORDER_ALT,
            command=self.check_dependencies).pack(side='right', padx=(8, 0))

    def check_dependencies(self):
        triggers = self.get_items()
        if not triggers:
            ui.common.dialogs.showwarning("检查依赖", "当前没有触发器，无需检查依赖。")
            return
        from ui.scenario.dependency_dlg import DependencyGraphDialog
        dlg = DependencyGraphDialog(self, triggers)
        if getattr(dlg, "cycle_nodes", set()):
            num = len(dlg.cycle_nodes)
            self.dep_status_label.configure(
                text=f"发现循环依赖（{num} 个触发器）", text_color=STATUS_ERR)
        else:
            self.dep_status_label.configure(
                text="依赖关系正常，无循环依赖", text_color=STATUS_OK)

    # ---------- 实现 TreeviewManager 抽象方法 ----------
    def get_items(self) -> list:
        """返回当前副本的触发器列表（直接引用，便于实时更新）"""
        return self.scenario_editor.triggers

    def get_item_values(self, item: dict) -> tuple:
        """返回 Treeview 各列显示内容"""
        cond_str = self._format_condition(item.get("condition", {}))
        pre_str = ", ".join(item.get("precondition_names", [])) or "无"
        action_map = {"insert": "插入段落", "option": "选项分支", "ending": "结局", "background": "背景图", "sensitivity": "敏感", "none": "空触发器"}
        action_type = action_map.get(item.get("action_type"), item.get("action_type", "未知"))
        if item.get("action_type") == "ending":
            icon = (item.get("action_data") or {}).get("icon_path")
            action_type = "结局（重要）" if icon else "结局（不重要）"
        repeatable = "是" if item.get("repeatable", True) else "否"
        return (
            item.get("name", "未命名"),
            cond_str,
            action_type,
            repeatable,
            pre_str
        )

    def save_items(self, items: list):
        """保存触发器列表（直接更新到 scenario_editor，并标记修改）"""
        self.scenario_editor.triggers = items
        self.scenario_editor._save_evolution_triggers()

    def create_item_dialog(self, item: Optional[dict] = None):
        evolution_names = [attr["name"] for attr in self.scenario_editor.evolution_attrs if attr.get("name")]
        all_triggers = self.get_items()
        scenario_id = self.scenario_editor.current_scenario_id
        dlg = TriggerEditDialog(
            self, item, evolution_names, all_triggers,
            dungeon_repo=self.scenario_editor._dungeon_repo,
            dungeon_id=scenario_id,
            evolution_attrs=self.scenario_editor.evolution_attrs
        )
        if dlg.result:
            return dlg.result
        return None

    # ---------- 辅助方法 ----------
    def _format_condition(self, cond: dict) -> str:
        """将条件字典格式化为易读的字符串"""
        if not cond:
            return "无条件"
        op = cond.get("operator", "and")
        rules = cond.get("rules", [])
        if not rules:
            return "无规则"
        parts = []
        for r in rules:
            key = r.get("key", "")
            comp = r.get("comparator", ">=")
            val = r.get("value", 0)
            parts.append(f"{key}{comp}{val}")
        joiner = " 且 " if op == "and" else " 或 "
        return joiner.join(parts)


