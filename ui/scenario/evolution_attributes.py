"""演化量视图：按归属（她 / 主角 / 环境 / 其他角色）分组展示与编辑。

归属是**分组键**：它决定这一项量归在角色页的谁名下、提示词怎么解释它的来处，
数值本身仍由 ``DungeonState`` 统一持有（介入度 / 破坏性 / 总伤亡 + 自定义属性）。
展示状态（显示 / 折叠 / 隐藏）决定它进不进展示组件，与归属互不影响。
"""

import tkinter as tk

import customtkinter as ctk

import ui.ctk
from dungeon.schema import (EVOLUTION_OWNER_LABELS, EVOLUTION_OWNERS,
                            normalize_owner, owner_label)
from ui.ctk.dialogs import BaseDialog
from ui.ctk.managers import TreeviewManager
from ui.ctk.widgets import CTkSegmentedControl
from ui.ctk import fonts as ui_fonts
from ui.ctk.theme import (SC_BORDER_STRONG, SC_HOVER, SC_LINK, SC_PANEL_BG,
                          SC_TEXT_SOFT, SC_TITLE)

#: 展示状态的两套写法（配置里存键，界面显示中文）
STATE_LABELS = (("show", "显示"), ("collapse", "折叠"), ("internal", "隐藏"))
STATE_KEYS = tuple(key for key, _label in STATE_LABELS)
#: 归属筛选里「不筛」的档位
OWNER_ANY_LABEL = "全部"
#: 内置演化量的展示类型
BUILTIN_TYPE_LABELS = {
    "intrusion": "介入度",
    "destruction": "破坏性",
    "casualty": "总伤亡",
}
PROTECTED_TYPES = tuple(BUILTIN_TYPE_LABELS)


def state_label(value) -> str:
    """``show`` / ``collapse`` / ``internal`` → 显示 / 折叠 / 隐藏（未知值回退显示）。"""
    text = str(value or "").strip()
    for key, label in STATE_LABELS:
        if text == key:
            return label
    return "显示"


def state_key(value) -> str:
    """展示状态的中文标签或键 → 配置里存的键（无法识别时回退 ``show``）。"""
    text = str(value or "").strip()
    for key, label in STATE_LABELS:
        if text in (key, label):
            return key
    return "show"


def owner_label_of(item: dict) -> str:
    """条目的归属展示名（未填按类型取缺省）。"""
    return owner_label(item.get("owner"), item.get("type"))


def _identity_index(items: list, target):
    """按对象身份查下标（演化量是可变字典，``list.index`` 会误命中等值项）。"""
    for index, item in enumerate(items):
        if item is target:
            return index
    return -1


def _labels_for_owner(key: str) -> str:
    return EVOLUTION_OWNER_LABELS.get(key, key)


class EvolutionAtrrManager(TreeviewManager):
    """演化量视图（统一管理介入度、破坏性、总伤亡与自定义属性）。

    列表可按归属过滤；过滤只影响显示，保存永远写回完整列表，删除/上移/下移
    都按对象身份定位，不会因为过滤而与别的条目错位。
    """

    def __init__(self, parent, repository, scenario_editor_ref):
        self.scenario_editor = scenario_editor_ref
        self.owner_filter = None  # None = 全部；否则为 EVOLUTION_OWNERS 之一
        self._row_items = []      # 列表行 ↔ 条目（过滤后）
        columns = [
            ("名称", 110, "name"),
            ("归属", 78, "owner"),
            ("类型", 76, "type"),
            ("展示状态", 88, "display_state"),
            ("初始值", 70, "init_value"),
            ("倍率", 64, "rate"),
            ("偏移率", 80, "random_offset"),
        ]
        super().__init__(parent, repository, columns, item_name="演化量")
        self._build_filter_bar()
        self.refresh_list()

    # ---------- 顶部：归属筛选 ----------
    def _build_filter_bar(self):
        for child in self.toolbar_frame.winfo_children():
            child.destroy()
        ctk.CTkLabel(self.toolbar_frame, text="归属:",
                     font=ui_fonts.ui_font(13),
                     text_color=SC_TITLE).pack(side='left', padx=(5, 4))
        self._filter_switch = CTkSegmentedControl(
            self.toolbar_frame,
            values=[OWNER_ANY_LABEL] + [_labels_for_owner(k) for k in EVOLUTION_OWNERS],
            command=self._on_filter_change, width=280,
            font=ui_fonts.ui_font(12))
        self._filter_switch.pack(side='left', padx=4, pady=(3, 1))
        self._filter_switch.set(OWNER_ANY_LABEL)

    def _on_filter_change(self, value):
        label = str(value or "").strip()
        self.owner_filter = None
        for key in EVOLUTION_OWNERS:
            if _labels_for_owner(key) == label:
                self.owner_filter = key
                break
        self.refresh_list()

    def set_owner_filter(self, owner):
        """外部（角色卡片）指定归属；``None`` 表示全部。"""
        self.owner_filter = owner if owner in EVOLUTION_OWNERS else None
        self._filter_switch.set(
            _labels_for_owner(self.owner_filter) if self.owner_filter else OWNER_ANY_LABEL)
        self.refresh_list()

    # ---------- 列表 ----------
    def visible_items(self) -> list:
        """当前过滤下可见的条目。"""
        items = self.get_items()
        if self.owner_filter is None:
            return list(items)
        return [item for item in items
                if normalize_owner(item.get("owner"), item.get("type")) == self.owner_filter]

    def refresh_list(self):
        self.tree.delete(*self.tree.get_children())
        self._row_items = self.visible_items()
        for index, item in enumerate(self._row_items):
            tags = ("evenrow",) if index % 2 == 0 else ()
            self.tree.insert("", "end", iid=f"row_{index}",
                             values=self.get_item_values(item), tags=tags)

    def _select_object(self, target):
        for index, item in enumerate(self._row_items):
            if item is target:
                self.tree.selection_set(f"row_{index}")
                self.tree.see(f"row_{index}")
                return

    def get_items(self):
        """返回演化量列表（完整列表，与过滤无关）。"""
        return self.scenario_editor.evolution_attrs

    def get_item_values(self, item):
        """返回 Treeview 显示的值。"""
        type_key = item.get("type")
        if type_key in BUILTIN_TYPE_LABELS:
            return (item.get("name") or BUILTIN_TYPE_LABELS[type_key],
                    owner_label_of(item),
                    BUILTIN_TYPE_LABELS[type_key],
                    state_label(item.get("display_state")),
                    "-", "-", "-")
        return (
            item.get("name", ""),
            owner_label_of(item),
            "自定义",
            state_label(item.get("display_state")),
            item.get("init_value", 0.0),
            item.get("rate", 1.0),
            item.get("random_offset", 0.0),
        )

    def save_items(self, items):
        self.scenario_editor.evolution_attrs = items
        self.scenario_editor._save_evolution_triggers()

    # ---------- 选中（按身份定位，过滤下也准） ----------
    def get_selected_index(self) -> int:
        selection = self.tree.selection()
        if not selection:
            return -1
        try:
            return self.tree.index(selection[0])
        except Exception:
            return -1

    def get_selected_item(self):
        index = self.get_selected_index()
        if 0 <= index < len(self._row_items):
            return self._row_items[index]
        return None

    # ---------- 增删改 ----------
    def create_item_dialog(self, item=None):
        """根据 item 类型调用正确的编辑对话框。"""
        if item is None:
            # 新建：默认落在当前过滤的归属下
            return self._custom_attr_dialog(None, default_owner=self.owner_filter)
        if item.get("type") in PROTECTED_TYPES:
            return self._fixed_attr_dialog(item)
        return self._custom_attr_dialog(item)

    def _fixed_attr_dialog(self, item):
        """编辑介入度/破坏性/总伤亡（归属与展示状态）；保存时直接在 item 上修改。"""
        FixedAttrDialog(self, item)
        return item

    def _custom_attr_dialog(self, item=None, default_owner=None):
        """编辑或添加自定义属性；取消时返回 None。"""
        dialog = CustomAttrDialog(self, item, default_owner=default_owner)
        return dialog.result

    def edit_item(self):
        item = self.get_selected_item()
        if item is None:
            ui.ctk.dialogs.showwarning("警告", "请先选择一个演化量")
            return
        result = self.create_item_dialog(item)
        if result is None:
            return
        items = self.get_items()
        position = _identity_index(items, item)
        if position >= 0:
            items[position] = result
        self.save_items(items)
        self.refresh_list()
        self._select_object(result)

    def delete_item(self):
        """重写删除方法，禁止删除介入度、破坏性和总伤亡。"""
        item = self.get_selected_item()
        if not item:
            return
        if item.get("type") in PROTECTED_TYPES:
            ui.ctk.dialogs.showwarning("警告", "介入度、破坏性和总伤亡不可删除")
            return
        name = item.get("name", "")
        if ui.ctk.dialogs.askyesno("确认", f"确定要删除自定义属性 '{name}' 吗？"):
            items = self.get_items()
            position = _identity_index(items, item)
            if position >= 0:
                del items[position]
            self.save_items(items)
            self.refresh_list()

    def move_up(self):
        self._move(-1)

    def move_down(self):
        self._move(1)

    def _move(self, delta: int):
        """在完整列表里按身份交换（列表顺序就是属性条的展示顺序）。"""
        item = self.get_selected_item()
        if item is None:
            ui.ctk.dialogs.showwarning("警告", "请先选择一个演化量")
            return
        items = self.get_items()
        position = _identity_index(items, item)
        target = position + delta
        if position < 0 or not (0 <= target < len(items)):
            return
        items[position], items[target] = items[target], items[position]
        self.save_items(items)
        self.refresh_list()
        self._select_object(item)


class FixedAttrDialog(BaseDialog):
    """介入度/破坏性/总伤亡编辑对话框（归属 + 展示状态）。"""

    def __init__(self, parent, item):
        super().__init__(parent)
        self.title(f"编辑 {item.get('name') or '演化量'}")
        self._item = item
        self.result = None
        self.geometry("320x190")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self._build_ui()
        self._show_modal()

    def _build_ui(self):
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill='both', expand=True, padx=20, pady=15)

        self.owner_var, owner_widget = _combo_row(
            main, "归属:", self.UI_FONT,
            [label for _key, label in _owner_choices()],
            owner_label_of(self._item))
        owner_widget.pack(side='left', padx=5, fill='x', expand=True)

        self.state_var, state_widget = _combo_row(
            main, "展示状态:", self.UI_FONT,
            [label for _key, label in STATE_LABELS],
            state_label(self._item.get("display_state")))
        state_widget.pack(side='left', padx=5, fill='x', expand=True)

        btn_frame = ctk.CTkFrame(main, fg_color="transparent")
        btn_frame.pack(pady=(20, 0))
        ctk.CTkButton(btn_frame, text="保存", width=88, height=28,
                      font=self.UI_FONT, command=self._ok).pack(side='left', padx=7)
        ctk.CTkButton(btn_frame, text="取消", width=88, height=28,
                      font=self.UI_FONT, command=self._cancel).pack(side='left', padx=7)

    def _ok(self, _event=None):
        self._item["owner"] = _owner_key(self.owner_var.get())
        self._item["display_state"] = state_key(self.state_var.get())
        self.result = self._item
        self._close()

    def _cancel(self, _event=None):
        self.result = None
        self._close()


class CustomAttrDialog(BaseDialog):
    """自定义演化属性编辑/添加对话框（名称 / 归属 / 展示状态 / 初始值 / 倍率 / 偏移率）。"""

    NUMERIC_FIELDS = (("初始值", "init_value", "0.0"),
                      ("倍率", "rate", "1.0"),
                      ("偏移率", "random_offset", "0.0"))

    def __init__(self, parent, item=None, default_owner=None):
        super().__init__(parent)
        self.title("编辑自定义属性" if item else "添加自定义属性")
        self._item = item
        self._default_owner = normalize_owner(default_owner, "custom")
        self.result = None
        self.geometry("320x300")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self._vars = {}
        self._build_ui()
        self._show_modal()

    def _build_ui(self):
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill='both', expand=True, padx=20, pady=10)

        name_row = ctk.CTkFrame(main, fg_color="transparent")
        name_row.pack(fill='x', pady=4)
        ctk.CTkLabel(name_row, text="名称:", width=80, anchor='w',
                     font=self.UI_FONT).pack(side='left', padx=(0, 10))
        self._name_var = tk.StringVar(
            value=str(self._item.get("name", "")) if self._item else "")
        ctk.CTkEntry(name_row, textvariable=self._name_var, height=28,
                     font=self.UI_FONT).pack(side='left', padx=5, fill='x',
                                              expand=True)

        owner_value = (owner_label_of(self._item) if self._item
                       else _labels_for_owner(self._default_owner))
        self._owner_var, owner_widget = _combo_row(
            main, "归属:", self.UI_FONT,
            [label for _key, label in _owner_choices()], owner_value)
        owner_widget.pack(side='left', padx=5, fill='x', expand=True)

        state_value = (state_label(self._item.get("display_state"))
                       if self._item else state_label("show"))
        self._state_var, state_widget = _combo_row(
            main, "展示状态:", self.UI_FONT,
            [label for _key, label in STATE_LABELS], state_value)
        state_widget.pack(side='left', padx=5, fill='x', expand=True)

        for label, key, default in self.NUMERIC_FIELDS:
            row = ctk.CTkFrame(main, fg_color="transparent")
            row.pack(fill='x', pady=4)
            ctk.CTkLabel(row, text=label + ":", width=80, anchor='w',
                         font=self.UI_FONT).pack(side='left', padx=(0, 10))
            value = (str(self._item.get(key, default)) if self._item else default)
            var = tk.StringVar(value=value)
            ctk.CTkEntry(row, textvariable=var, height=28,
                         font=self.UI_FONT).pack(side='left', padx=5, fill='x',
                                                  expand=True)
            self._vars[key] = var

        btn_frame = ctk.CTkFrame(main, fg_color="transparent")
        btn_frame.pack(pady=(15, 0))
        ctk.CTkButton(btn_frame, text="保存", width=88, height=28,
                      font=self.UI_FONT, command=self._ok).pack(side='left', padx=7)
        ctk.CTkButton(btn_frame, text="取消", width=88, height=28,
                      font=self.UI_FONT, command=self._cancel).pack(side='left', padx=7)

    def _ok(self, _event=None):
        try:
            name = self._name_var.get().strip()
            if not name:
                raise ValueError("名称不能为空")
            new_item = dict(self._item) if self._item else {}
            new_item["type"] = "custom"
            new_item["name"] = name
            new_item["owner"] = _owner_key(self._owner_var.get())
            new_item["display_state"] = state_key(self._state_var.get())
            for key, var in self._vars.items():
                text = var.get().strip()
                try:
                    new_item[key] = float(text) if text else 0.0
                except ValueError:
                    raise ValueError(f"{key} 必须是数字")
            self.result = new_item
            self._close()
        except Exception as e:
            ui.ctk.dialogs.showerror("错误", str(e))

    def _cancel(self, _event=None):
        self.result = None
        self._close()


# ---------------- 归属下拉的小工具 ----------------

def _owner_choices():
    return [(key, EVOLUTION_OWNER_LABELS[key]) for key in EVOLUTION_OWNERS]


def _owner_key(label) -> str:
    """归属展示名 → 键（未知值按自定义类型取缺省）。"""
    text = str(label or "").strip()
    for key, value in EVOLUTION_OWNER_LABELS.items():
        if text == value:
            return key
    return normalize_owner(text, "custom")


def _combo_row(parent, label, font, values, current):
    """「标签 + 只读下拉」一行，返回 (StringVar, 下拉控件)。"""
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill='x', pady=4)
    ctk.CTkLabel(row, text=label, width=80, anchor='w',
                 font=font).pack(side='left', padx=(0, 10))
    var = tk.StringVar(value=str(current or ""))
    combo = ctk.CTkComboBox(
        row, values=list(values), variable=var, state="readonly",
        height=28, font=font, fg_color=SC_PANEL_BG,
        border_color=SC_BORDER_STRONG, button_color=SC_BORDER_STRONG,
        button_hover_color=SC_HOVER, dropdown_fg_color=SC_PANEL_BG,
        dropdown_hover_color=SC_HOVER, text_color=SC_TITLE)
    return var, combo
