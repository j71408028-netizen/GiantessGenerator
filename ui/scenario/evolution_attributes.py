import tkinter as tk

import customtkinter as ctk

import ui.common
from ui.common.dialogs import BaseDialog
from ui.common.managers import TreeviewManager



class EvolutionAtrrManager(TreeviewManager):
    """演化量管理面板（统一管理介入度、破坏性、自定义属性）"""
    def __init__(self, parent, repository, scenario_editor_ref):
        self.scenario_editor = scenario_editor_ref
        columns = [
            ("名称", 100, "name"),
            ("类型", 80, "type"),
            ("展示状态", 100, "display_state"),
            ("初始值", 70, "init_value"),
            ("倍率", 70, "rate"),
            ("偏移率", 90, "random_offset"),
        ]
        super().__init__(parent, repository, columns, item_name="演化量")
        self.toolbar_frame.pack_forget()
        self.refresh_list()

    def get_items(self):
        """返回演化量列表"""
        return self.scenario_editor.evolution_attrs

    def get_item_values(self, item):
        """返回Treeview显示的值"""
        if item["type"] == "intrusion":
            return (item["name"], "介入度", item["display_state"], "-", "-", "-")
        elif item["type"] == "destruction":
            return (item["name"], "破坏性", item["display_state"], "-", "-", "-")
        elif item["type"] == "casualty":
            return (item["name"], "总伤亡", item["display_state"], "-", "-", "-")
        else:
            return (
                item["name"],
                "自定义",
                item["display_state"],
                item.get("init_value", 0.0),
                item.get("rate", 1.0),
                item.get("random_offset", 0.0),
            )

    def save_items(self, items):
        self.scenario_editor.evolution_attrs = items
        self.scenario_editor._save_evolution_triggers()

    def create_item_dialog(self, item=None):
        """根据 item 类型调用正确的编辑对话框"""
        if item is None:
            # 新建：默认创建自定义属性
            return self._custom_attr_dialog(None)
        else:
            if item["type"] in ("intrusion", "destruction", "casualty"):
                return self._fixed_attr_dialog(item)
            else:
                return self._custom_attr_dialog(item)

    def _fixed_attr_dialog(self, item):
        """编辑介入度/破坏性/总伤亡（仅修改展示状态）；保存时直接在 item 上修改"""
        FixedAttrDialog(self, item)
        return item

    def _custom_attr_dialog(self, item=None):
        """编辑或添加自定义属性；取消时返回 None"""
        dialog = CustomAttrDialog(self, item)
        return dialog.result

    def delete_item(self):
        """重写删除方法，禁止删除介入度、破坏性和总伤亡"""
        item = self.get_selected_item()
        if not item:
            return
        if item["type"] in ("intrusion", "destruction", "casualty"):
            ui.common.dialogs.showwarning("警告", "介入度、破坏性和总伤亡不可删除")
            return
        name = item["name"]
        if ui.common.dialogs.askyesno("确认", f"确定要删除自定义属性 '{name}' 吗？"):
            idx = self.get_selected_index()
            items = self.get_items()
            del items[idx]
            self.save_items(items)
            self.refresh_list()


class FixedAttrDialog(BaseDialog):
    """介入度/破坏性/总伤亡编辑对话框（仅修改展示状态）"""

    STATE_OPTIONS = ["show", "collapse", "internal"]

    def __init__(self, parent, item):
        super().__init__(parent)
        self.title(f"编辑 {item['name']}")
        self._item = item
        self.result = None
        self.geometry("300x130")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self._build_ui()
        self._show_modal()

    def _build_ui(self):
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill='both', expand=True, padx=20, pady=15)

        row1 = ctk.CTkFrame(main, fg_color="transparent")
        row1.pack(fill='x', pady=5)
        ctk.CTkLabel(row1, text="展示状态:", width=80, anchor='w',
                     font=self.UI_FONT).pack(side='left', padx=(0, 10))
        self.state_var = tk.StringVar(value=self._item["display_state"])
        self.state_combo = ctk.CTkComboBox(
            row1, values=self.STATE_OPTIONS, variable=self.state_var,
            state="readonly", height=28, font=self.UI_FONT)
        self.state_combo.pack(side='left', padx=5, fill='x', expand=True)

        btn_frame = ctk.CTkFrame(main, fg_color="transparent")
        btn_frame.pack(pady=(20, 0))
        ctk.CTkButton(btn_frame, text="保存", width=88, height=28,
                      font=self.UI_FONT, command=self._ok).pack(side='left', padx=7)
        ctk.CTkButton(btn_frame, text="取消", width=88, height=28,
                      font=self.UI_FONT, command=self._cancel).pack(side='left', padx=7)

    def _ok(self, _event=None):
        self._item["display_state"] = self.state_var.get()
        self.result = self._item
        self._close()

    def _cancel(self, _event=None):
        self.result = None
        self._close()


class CustomAttrDialog(BaseDialog):
    """自定义演化属性编辑/添加对话框"""

    STATE_OPTIONS = ["show", "collapse", "internal"]
    FIELDS = [
        ("名称", "name", "", True),
        ("展示状态", "display_state", "show", False),
        ("初始值", "init_value", "0.0", False),
        ("倍率", "rate", "1.0", False),
        ("偏移率", "random_offset", "0.0", False),
    ]

    def __init__(self, parent, item=None):
        super().__init__(parent)
        self.title("编辑自定义属性" if item else "添加自定义属性")
        self._item = item
        self.result = None
        self.geometry("300x250")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self._entries = {}
        self._build_ui()
        self._show_modal()

    def _build_ui(self):
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill='both', expand=True, padx=20, pady=10)

        for label, key, default, required in self.FIELDS:
            value = str(self._item[key]) if self._item else default
            row = ctk.CTkFrame(main, fg_color="transparent")
            row.pack(fill='x', pady=4)
            ctk.CTkLabel(row, text=label + ":", width=80, anchor='w',
                         font=self.UI_FONT).pack(side='left', padx=(0, 10))
            var = tk.StringVar(value=value)
            if key == "display_state":
                widget = ctk.CTkComboBox(
                    row, values=self.STATE_OPTIONS, variable=var,
                    state="readonly", height=28, font=self.UI_FONT)
            else:
                widget = ctk.CTkEntry(
                    row, textvariable=var, height=28, font=self.UI_FONT)
            widget.pack(side='left', padx=5, fill='x', expand=True)
            self._entries[key] = (var, required)

        btn_frame = ctk.CTkFrame(main, fg_color="transparent")
        btn_frame.pack(pady=(15, 0))
        ctk.CTkButton(btn_frame, text="保存", width=88, height=28,
                      font=self.UI_FONT, command=self._ok).pack(side='left', padx=7)
        ctk.CTkButton(btn_frame, text="取消", width=88, height=28,
                      font=self.UI_FONT, command=self._cancel).pack(side='left', padx=7)

    def _ok(self, _event=None):
        try:
            new_item = {}
            for key, (var, required) in self._entries.items():
                value = var.get().strip()
                if required and not value:
                    raise ValueError(f"{key} 不能为空")
                if key == "name":
                    new_item["name"] = value
                elif key == "display_state":
                    new_item["display_state"] = value
                else:
                    try:
                        new_item[key] = float(value)
                    except ValueError:
                        raise ValueError(f"{key} 必须是数字")
            new_item["type"] = "custom"
            if self._item is not None:
                self._item.update(new_item)
                self.result = self._item
            else:
                self.result = new_item
            self._close()
        except Exception as e:
            ui.common.dialogs.showerror("错误", str(e))

    def _cancel(self, _event=None):
        self.result = None
        self._close()