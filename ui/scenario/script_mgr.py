import os
from typing import Optional

import customtkinter as ctk

import ui.common
from dungeon.actions import (LEGACY_ACTIONS, VISUAL_FILTERS, action_label,
                             normalize_action_type)
from dungeon.chapters import (
    CHAPTER_ANY, CHAPTER_NONE, CHAPTER_ANY_LABEL, CHAPTER_NONE_LABEL,
    default_chapter_color, shade_for_mode,
)
from ui.common.managers import TreeviewManager
from ui.common.theme import (
    SCRIPT_BORDER, SCRIPT_HOVER, SCRIPT_TEXT_SOFT, SCRIPT_OK,
    SCRIPT_OK_HOVER, SCRIPT_ERR, SCRIPT_ERR_HOVER,
)
from ui.common import fonts as ui_fonts
from ui.scenario.chapter_dlg import ChapterEditDialog
from ui.scenario.trigger_dlg import TriggerEditDialog

ROW_CHAPTER = "chapter"
ROW_TRIGGER = "trigger"

# 无章节归属的触发器（任意章节 / 无章节）在列表里合成一组，排在所有章节之后
LOOSE_GROUP = "__loose__"


class ScriptManager(TreeviewManager):
    """章节与触发器的一体化编辑面板。

    章节行按章节自定义配色着色，其下的触发器紧跟章节行显示；不属于任何章节
    的触发器（任意章节 / 无章节）排在最后。触发器与触发器之间、章节与章节
    之间才允许调序：触发器上移/下移不会越过所属章节的收尾，章节移动时其下
    的触发器整组跟随。删除章节会连带删除其下所有触发器（删除前提示）。
    """

    def __init__(self, parent, repository, scenario_editor_ref):
        self.scenario_editor = scenario_editor_ref
        self._rows = []  # [(行类型, 数据对象), ...]，与 Treeview 子项一一对应
        columns = [
            ("名称", 140, "name"),
            ("章节", 80, "scope"),
            ("特定背景", 100, "background"),
            ("敏感效果", 100, "sensitivity"),
            ("条件", 150, "condition"),
            ("动作类型", 60, "action_type"),
            ("重复", 40, "repeatable"),
            ("前置条件", 80, "precondition")
        ]
        super().__init__(parent, repository, columns, item_name="章节/触发器")
        self.toolbar_frame.pack_forget()
        self._build_buttons()
        self._build_hint_bar()
        self.refresh_list()

    # ---------- 底部按钮：两个新建键 + 共用编辑/删除/上移/下移 ----------
    def _build_buttons(self):
        for child in self.button_frame.winfo_children():
            child.destroy()

        btn_style = dict(fg_color="transparent", border_width=1, corner_radius=8,
                         font=ui_fonts.ui_font(13))
        muted = dict(text_color=SCRIPT_TEXT_SOFT, hover_color=SCRIPT_HOVER, border_color=SCRIPT_BORDER)
        create = dict(text_color=SCRIPT_OK, hover_color=SCRIPT_OK_HOVER, border_color=SCRIPT_OK)

        left = ctk.CTkFrame(self.button_frame, fg_color="transparent")
        left.pack(side='left', fill='x', expand=True)
        ctk.CTkButton(left, text="新建章节", command=self.create_chapter,
                      width=88, **create, **btn_style).pack(side='left', padx=5)
        ctk.CTkButton(left, text="新建触发器", command=self.create_trigger,
                      width=96, **create, **btn_style).pack(side='left', padx=5)
        ctk.CTkButton(left, text="编辑", command=self.edit_item,
                      width=80, **muted, **btn_style).pack(side='left', padx=5)
        ctk.CTkButton(left, text="删除", command=self.delete_item,
                      width=80, text_color=SCRIPT_ERR, hover_color=SCRIPT_ERR_HOVER,
                      border_color=SCRIPT_ERR, **btn_style).pack(side='left', padx=5)

        right = ctk.CTkFrame(self.button_frame, fg_color="transparent")
        right.pack(side='right', fill='x', expand=True)
        ctk.CTkButton(right, text="下移", command=self.move_down,
                      width=80, **muted, **btn_style).pack(side='right', padx=5)
        ctk.CTkButton(right, text="上移", command=self.move_up,
                      width=80, **muted, **btn_style).pack(side='right', padx=5)

    def _build_hint_bar(self):
        """面板下方的说明与依赖检查栏。"""
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill='x', padx=9, pady=(0, 5))
        ctk.CTkLabel(
            bar,
            text="章节行下方的缩进行是其触发器；「任意章节 / 无章节」的触发器排在最后。",
            font=ui_fonts.ui_font(12),
            text_color=SCRIPT_TEXT_SOFT).pack(side='left', padx=(4, 12))
        self.dep_status_label = ctk.CTkLabel(
            bar, text="点击 “检查依赖” 查看触发器前置依赖与循环依赖",
            font=ui_fonts.ui_font(12),
            text_color=SCRIPT_TEXT_SOFT)
        self.dep_status_label.pack(side='left', padx=(4, 5))
        ctk.CTkButton(
            bar, text="检查依赖", width=100,
            fg_color="transparent", border_width=1, corner_radius=8,
            font=ui_fonts.ui_font(13),
            text_color=SCRIPT_TEXT_SOFT,
            hover_color=SCRIPT_HOVER,
            border_color=SCRIPT_BORDER,
            command=self.check_dependencies).pack(side='right', padx=(8, 0))

    def check_dependencies(self):
        triggers = self.get_trigger_items()
        if not triggers:
            ui.common.dialogs.showwarning("检查依赖", "当前没有触发器，无需检查依赖。")
            return
        from ui.scenario.dependency_dlg import DependencyGraphDialog
        dlg = DependencyGraphDialog(self, triggers)
        if getattr(dlg, "cycle_nodes", set()):
            self.dep_status_label.configure(
                text=f"发现循环依赖（{len(dlg.cycle_nodes)} 个触发器）", text_color=SCRIPT_ERR)
        else:
            self.dep_status_label.configure(
                text="依赖关系正常，无循环依赖", text_color=SCRIPT_OK)

    # ---------- 行着色 ----------
    def update_theme(self, theme_mode: str):
        super().update_theme(theme_mode)
        self._apply_row_tags(theme_mode)

    def _apply_row_tags(self, theme_mode: str):
        self.tree.tag_configure("chapter_head", font=ui_fonts.ui_font(13, "bold"))
        self.tree.tag_configure(
            "loose_trigger", foreground=SCRIPT_TEXT_SOFT[1] if theme_mode == "Dark" else SCRIPT_TEXT_SOFT[0])
        for index, chapter in enumerate(self._chapter_list()):
            self.tree.tag_configure(
                f"chapter_color_{index}",
                foreground=shade_for_mode(chapter.get("color"), theme_mode))

    # ---------- 列表构建 ----------
    def refresh_list(self):
        self.tree.delete(*self.tree.get_children())
        self._rows = []

        grouped = {}
        loose = []
        known = self._chapter_names()
        for trigger in self._trigger_list():
            scope = trigger.get("chapter")
            if scope and scope not in (CHAPTER_ANY, CHAPTER_NONE) and scope in known:
                grouped.setdefault(scope, []).append(trigger)
            else:
                loose.append(trigger)

        for chapter in self._chapter_list():
            self._insert_row(ROW_CHAPTER, chapter)
            for trigger in grouped.get(chapter.get("name"), []):
                self._insert_row(ROW_TRIGGER, trigger)
        for trigger in loose:
            self._insert_row(ROW_TRIGGER, trigger)

        self._apply_row_tags(ctk.get_appearance_mode())

    def _insert_row(self, row_kind: str, item):
        index = len(self._rows)
        tags = ["evenrow"] if index % 2 == 0 else []
        if row_kind == ROW_CHAPTER:
            tags.append("chapter_head")
            tags.append(self._chapter_tag(item))
        else:
            group_index = self._chapter_group_index(item.get("chapter"))
            tags.append(self._chapter_tag_by_index(group_index)
                        if group_index is not None else "loose_trigger")
        self.tree.insert("", "end", iid=f"row_{index}",
                         values=self._values(row_kind, item), tags=tuple(tags))
        self._rows.append((row_kind, item))

    def _chapter_tag(self, chapter) -> str:
        return self._chapter_tag_by_index(self._index_of(self._chapter_list(), chapter))

    def _chapter_tag_by_index(self, index) -> str:
        return f"chapter_color_{index}"

    # ---------- 行内容 ----------
    def _values(self, row_kind: str, item) -> tuple:
        if row_kind == ROW_CHAPTER:
            name = item.get("name", "未命名")
            return (
                f"▌ {name}" + ("  ★" if item.get("start") else ""),
                "起始章节" if item.get("start") else "章节",
                self._format_background(item.get("background") or {}),
                self._format_sensitivity(item.get("sensitivity") or []),
                "", "", "", "",
            )
        return (
            f"    └ {item.get('name', '未命名')}",
            self._trigger_scope_text(item.get("chapter")),
            "", "",
            self._format_condition(item.get("condition", {})),
            self._action_text(item),
            "是" if item.get("repeatable", True) else "否",
            ", ".join(item.get("precondition_names", [])) or "无",
        )

    def _trigger_scope_text(self, scope) -> str:
        """触发器已按章节归组，只有特殊作用域需要在行内点明。"""
        if not scope or scope == CHAPTER_ANY:
            return CHAPTER_ANY_LABEL
        if scope == CHAPTER_NONE:
            return CHAPTER_NONE_LABEL
        if scope in self._chapter_names():
            return ""
        return f"{scope}（章节已删除）"

    def _action_text(self, trigger: dict) -> str:
        action_type = normalize_action_type(trigger.get("action_type"), trigger.get("action_data"))
        if action_type in LEGACY_ACTIONS:
            # 旧版动作已不再支持（运行时直接跳过），列表里标出来供用户删除
            return f"{action_label(action_type)}（旧版·已失效）"
        if action_type == "ending":
            icon = (trigger.get("action_data") or {}).get("icon_path")
            return "结局（重要）" if icon else "结局（不重要）"
        return action_label(action_type)

    def _format_background(self, background: dict) -> str:
        image_path = background.get("image_path") or ""
        if not image_path:
            return "（无）"
        text = os.path.basename(str(image_path))
        if background.get("filter_effect"):
            text += f"（{self._filter_label(background['filter_effect'])}）"
        return text

    def _filter_label(self, key: str) -> str:
        for filter_key, label in VISUAL_FILTERS:
            if filter_key == key:
                return label
        return str(key)

    def _format_sensitivity(self, effects: list) -> str:
        if not effects:
            return "（无）"
        parts = []
        for effect in effects:
            text = f"{effect.get('attr', '')}×{effect.get('strength', 1.0)}"
            objective = effect.get("objective", 0.0)
            if objective:
                text += f"(客观{objective:+g})"
            parts.append(text)
        return "，".join(parts)

    def _format_condition(self, cond: dict) -> str:
        if not cond:
            return "无条件"
        rules = cond.get("rules", [])
        if not rules:
            return "无规则"
        parts = [f"{r.get('key', '')}{r.get('comparator', '>=')}{r.get('value', 0)}"
                 for r in rules]
        joiner = " 且 " if cond.get("operator", "and") == "and" else " 或 "
        return joiner.join(parts)

    # ---------- 数据访问 ----------
    def _chapter_list(self) -> list:
        return self.scenario_editor.chapters

    def _trigger_list(self) -> list:
        return self.scenario_editor.triggers

    def get_trigger_items(self) -> list:
        return self._trigger_list()

    def _chapter_names(self) -> list:
        return [c.get("name") for c in self._chapter_list() if c.get("name")]

    def _chapter_group_index(self, scope) -> Optional[int]:
        """触发器所属章节在章节列表中的下标；不属于任何章节时返回 None。"""
        if not scope or scope in (CHAPTER_ANY, CHAPTER_NONE):
            return None
        names = self._chapter_names()
        return names.index(scope) if scope in names else None

    def _group_key(self, trigger: dict) -> str:
        scope = trigger.get("chapter") or ""
        return scope if scope in self._chapter_names() else LOOSE_GROUP

    @staticmethod
    def _index_of(items: list, target) -> int:
        for index, item in enumerate(items):
            if item is target:
                return index
        return -1

    def _save(self):
        self.scenario_editor._save_evolution_triggers()

    def _select_object(self, target):
        for index, (_row_kind, item) in enumerate(self._rows):
            if item is target:
                iid = f"row_{index}"
                self.tree.selection_set(iid)
                self.tree.see(iid)
                return

    # ---------- 选中项 ----------
    def get_selected_index(self) -> int:
        selection = self.tree.selection()
        if not selection:
            return -1
        return self.tree.index(selection[0])

    def get_selected_row(self):
        index = self.get_selected_index()
        if 0 <= index < len(self._rows):
            return self._rows[index]
        return None

    def get_selected_item(self):
        row = self.get_selected_row()
        return row[1] if row else None

    def get_items(self) -> list:
        return [item for _row_kind, item in self._rows]

    def get_item_values(self, item) -> tuple:
        for chapter in self._chapter_list():
            if chapter is item:
                return self._values(ROW_CHAPTER, item)
        return self._values(ROW_TRIGGER, item)

    def save_items(self, items: Optional[list] = None):
        self._save()

    def create_item_dialog(self, item=None):
        """基类的「添加」按钮不参与本面板，新建由两个专用入口驱动。"""
        return None

    # ---------- 新建 ----------
    def create_chapter(self):
        result = self._chapter_dialog(None)
        if result is None:
            return
        chapters = self._chapter_list()
        insert_at = len(chapters)
        row = self.get_selected_row()
        if row and row[0] == ROW_CHAPTER:
            position = self._index_of(chapters, row[1])
            if position >= 0:
                insert_at = position + 1
        if result.get("start"):
            self._clear_start_flags()
        chapters.insert(insert_at, result)
        self._save()
        self.refresh_list()
        self._select_object(result)

    def create_trigger(self):
        """新建触发器：默认落在当前选中的章节里。"""
        scope = self._scope_for_new_trigger()
        result = self._trigger_dialog({"chapter": scope} if scope else None)
        if result is None:
            return
        self._trigger_list().append(result)
        self._save()
        self.refresh_list()
        self._select_object(result)

    def _scope_for_new_trigger(self) -> str:
        row = self.get_selected_row()
        if not row:
            return ""
        row_kind, item = row
        if row_kind == ROW_CHAPTER:
            return item.get("name") or ""
        scope = item.get("chapter") or ""
        return scope if scope in self._chapter_names() else ""

    def _clear_start_flags(self):
        for chapter in self._chapter_list():
            chapter["start"] = False

    # ---------- 编辑 ----------
    def edit_item(self):
        row = self.get_selected_row()
        if row is None:
            ui.common.dialogs.showwarning("警告", "请先选择一个章节或触发器")
            return
        row_kind, item = row
        target = item

        if row_kind == ROW_CHAPTER:
            result = self._chapter_dialog(item)
            if result is None:
                return
            chapters = self._chapter_list()
            position = self._index_of(chapters, item)
            if position < 0:
                return
            self._retarget_chapter(item.get("name", ""), result.get("name", ""))
            if result.get("start"):
                self._clear_start_flags()
            chapters[position] = result
            target = result
        else:
            result = self._trigger_dialog(item)
            if result is None:
                return
            triggers = self._trigger_list()
            position = self._index_of(triggers, item)
            if position < 0:
                return
            triggers[position] = result
            target = result

        self._save()
        self.refresh_list()
        self._select_object(target)

    def _retarget_chapter(self, old_name: str, new_name: str):
        """章节改名后，指向它的触发器作用域与跳转目标一并改名。"""
        if not old_name or old_name == new_name:
            return
        for trigger in self._trigger_list():
            if trigger.get("chapter") == old_name:
                trigger["chapter"] = new_name
            action_data = trigger.get("action_data")
            if isinstance(action_data, dict) and action_data.get("chapter") == old_name:
                action_data["chapter"] = new_name

    # ---------- 删除 ----------
    def delete_item(self):
        row = self.get_selected_row()
        if row is None:
            ui.common.dialogs.showwarning("警告", "请先选择一个章节或触发器")
            return
        row_kind, item = row
        if row_kind == ROW_TRIGGER:
            self._delete_trigger(item)
        else:
            self._delete_chapter(item)
        self.refresh_list()

    def _delete_trigger(self, trigger):
        name = trigger.get("name") or "未命名"
        if not ui.common.dialogs.askyesno("确认", f"确定要删除触发器 '{name}' 吗？"):
            return
        triggers = self._trigger_list()
        position = self._index_of(triggers, trigger)
        if position >= 0:
            del triggers[position]
        self._save()

    def _delete_chapter(self, chapter):
        name = chapter.get("name", "")
        triggers = self._trigger_list()
        children = [t for t in triggers if t.get("chapter") == name]
        if children:
            detail = f"其下的 {len(children)} 个触发器也会一并删除：\n" + \
                     "、".join(t.get("name") or "未命名" for t in children)
        else:
            detail = "该章节下没有触发器。"
        if not ui.common.dialogs.askyesno("确认", f"确定要删除章节 '{name}' 吗？\n{detail}"):
            return

        chapters = self._chapter_list()
        position = self._index_of(chapters, chapter)
        if position >= 0:
            del chapters[position]
        triggers[:] = [t for t in triggers if t.get("chapter") != name]

        stale = [t.get("name") or "未命名" for t in triggers
                 if isinstance(t.get("action_data"), dict)
                 and t["action_data"].get("chapter") == name]
        if stale:
            ui.common.dialogs.showwarning(
                "仍有触发器指向该章节",
                "以下触发器的「跳转章节」仍指向已删除的章节：\n" + "、".join(stale))
        self._save()

    # ---------- 上移 / 下移 ----------
    def move_up(self):
        self._move(-1)

    def move_down(self):
        self._move(1)

    def _move(self, delta: int):
        row = self.get_selected_row()
        if row is None:
            ui.common.dialogs.showwarning("警告", "请先选择一个章节或触发器")
            return
        index = self.get_selected_index()
        row_kind, item = row

        if row_kind == ROW_CHAPTER:
            # 章节整组移动：其下触发器按章节归组，自动跟随
            chapters = self._chapter_list()
            position = self._index_of(chapters, item)
            target = position + delta
            if position < 0 or not (0 <= target < len(chapters)):
                return
            chapters[position], chapters[target] = chapters[target], chapters[position]
        else:
            partner = self._sibling_trigger(index, delta)
            if partner is None:
                return
            triggers = self._trigger_list()
            position = self._index_of(triggers, item)
            target = self._index_of(triggers, partner)
            if position < 0 or target < 0:
                return
            triggers[position], triggers[target] = triggers[target], triggers[position]

        self._save()
        self.refresh_list()
        self._select_object(item)

    def _sibling_trigger(self, index: int, delta: int):
        """同一分组内相邻的触发器；越过章节行即到达章节收尾，返回 None。"""
        group = self._group_key(self._rows[index][1])
        cursor = index + delta
        while 0 <= cursor < len(self._rows):
            row_kind, other = self._rows[cursor]
            if row_kind == ROW_CHAPTER:
                return None
            if self._group_key(other) == group:
                return other
            cursor += delta
        return None

    # ---------- 对话框 ----------
    def _chapter_dialog(self, item):
        """item 为 None 时新建，配色按预设轮换，避免新章节全是一个颜色。"""
        if item is None:
            item = {"color": default_chapter_color(len(self._chapter_list()))}
        dlg = ChapterEditDialog(
            self, item,
            dungeon_repo=self.scenario_editor._dungeon_repo,
            dungeon_id=self.scenario_editor.current_scenario_id,
            evolution_attrs=self.scenario_editor.evolution_attrs,
            all_chapters=self._chapter_list(),
        )
        return dlg.result

    def _trigger_dialog(self, item):
        evolution_names = [attr["name"] for attr in self.scenario_editor.evolution_attrs
                           if attr.get("name")]
        dlg = TriggerEditDialog(
            self, item, evolution_names, self._trigger_list(),
            dungeon_repo=self.scenario_editor._dungeon_repo,
            dungeon_id=self.scenario_editor.current_scenario_id,
            evolution_attrs=self.scenario_editor.evolution_attrs,
            chapters=self._chapter_list(),
        )
        return dlg.result
