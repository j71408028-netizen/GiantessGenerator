"""副本显示组件管理面板（多选 + 参数编辑）。

面板布局：
- 左侧：可用官方组件多选列表（StyleListBox 复用）；
- 右侧：当前选中组件的参数编辑表单（text / int / bool / color）。
保存时把选中的组件 id 与各自的参数覆盖写回副本配置。

组件 id 与参数声明来自 dungeon.components.available_component_descriptions()，
由默认组件包（data/packs/dungeons/_default/components/components.py）提供。
"""

import tkinter as tk
from tkinter import colorchooser

import customtkinter as ctk

import ui.common.dialogs
from ui.common.widgets import StyleListBox
from ui.common.theme import (
    BORDER, BORDER_ALT, PNL_BG, HARD_TITLE, TEXT, SOFT,
    HOVER, HOVER_ALT, STATUS_OK, OK_HOVER, BASE, LINK_BLUE,
)
from ui.common import fonts as ui_fonts


def _default_value_for(spec):
    """从 param_spec 取默认值；color 默认转 hex 便于展示。"""
    value = spec.get("default")
    if spec.get("type") == "color":
        return _rgba_to_hex(value) if value else "#FFE196"
    return value


def _rgba_to_hex(rgba):
    """DPG 使用的 (r,g,b,a) 元组转 hex 字符串。"""
    if not rgba:
        return "#FFE196"
    try:
        r, g, b = int(rgba[0]), int(rgba[1]), int(rgba[2])
        return "#{:02X}{:02X}{:02X}".format(max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))
    except (TypeError, ValueError, IndexError):
        return "#FFE196"


def _hex_to_rgba(hex_str, alpha=255):
    """hex 颜色字符串转 DPG (r,g,b,a) 元组。"""
    hex_str = (hex_str or "").strip().lstrip("#")
    if len(hex_str) != 6:
        return (255, 225, 150, alpha)
    try:
        return (int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16), alpha)
    except ValueError:
        return (255, 225, 150, alpha)


class ComponentManager(ctk.CTkFrame):
    """副本显示组件管理面板（挂在 ScenarioEditor 的第四个 tab）。"""

    def __init__(self, parent, dungeon_repo, scenario_editor_ref):
        super().__init__(parent, fg_color="transparent")
        self.scenario_editor = scenario_editor_ref
        self.components = []            # 当前配置选中的组件 id 列表
        self.components_params = {}     # {组件 id: {参数key: 值}}
        self._descriptions = []
        self._param_widgets = {}        # {组件 id: {参数key: 控件}}
        self._color_vars = {}           # {组件 id: {参数key: StringVar}}
        self._building_form = False

        self._load_descriptions()
        self._build_ui()
        self._refresh_component_list()

    # ---------------- 描述加载 ----------------
    def _load_descriptions(self):
        from dungeon.components import available_component_descriptions
        try:
            self._descriptions = available_component_descriptions()
        except Exception as exc:
            print(f"[ComponentManager] 加载组件描述失败: {exc}")
            self._descriptions = []

    def _desc_by_id(self, cid):
        return next((d for d in self._descriptions if d["id"] == cid), None)

    # ---------------- UI 构建 ----------------
    def _build_ui(self):
        toolbar = ctk.CTkFrame(self, fg_color=BASE)
        toolbar.pack(fill='x', padx=10, pady=6)
        ctk.CTkLabel(toolbar, text="副本显示组件：",
                     font=ui_fonts.ui_font(12, "bold"),
                     text_color=HARD_TITLE).pack(side='left', padx=5)
        ctk.CTkLabel(toolbar, text="选择该副本会话窗口显示的组件，可调整各组件参数",
                     font=ui_fonts.ui_font(11),
                     text_color=SOFT).pack(side='left', padx=8)
        ctk.CTkButton(toolbar, text="保存组件", width=110,
                      fg_color="transparent", border_width=2, corner_radius=10,
                      text_color=STATUS_OK, hover_color=OK_HOVER,
                      border_color=STATUS_OK,
                      font=ui_fonts.ui_font(12, "bold"),
                      command=self.save_items).pack(side='right', padx=5)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill='both', expand=True, padx=10, pady=(0, 10))

        # 左侧：组件多选
        left = ctk.CTkFrame(body, fg_color="transparent", width=220)
        left.pack(side='left', fill='y', padx=(0, 10))
        left.pack_propagate(False)
        ctk.CTkLabel(left, text="已启用组件", font=ui_fonts.ui_font(12, "bold"),
                     text_color=HARD_TITLE).pack(anchor='w', pady=(0, 4))
        self.listbox = StyleListBox(left, title="", height=6,
                                    on_change=self._on_selection_changed)
        self.listbox.pack(fill='both', expand=True)
        self.listbox.add_button("全选", command=self._select_all, side='left', padx=5)
        self.listbox.add_button("清空", command=self._clear_all, side='left', padx=5)

        # 右侧：参数编辑
        right = ctk.CTkFrame(body, fg_color="transparent")
        right.pack(side='left', fill='both', expand=True)
        self.params_scroll = ctk.CTkScrollableFrame(right, fg_color="transparent")
        self.params_scroll.pack(fill='both', expand=True)
        self.params_hint = ctk.CTkLabel(
            self.params_scroll, text="在左侧选择组件后，此处编辑其参数",
            font=ui_fonts.ui_font(12), text_color=SOFT)
        self.params_hint.pack(anchor='w', padx=6, pady=8)

    def _refresh_component_list(self):
        """按当前配置刷新左侧列表选中态。"""
        items = [d["id"] for d in self._descriptions]
        selected = [items.index(cid) for cid in self.components if cid in items]
        self.listbox.sync_items(items, selected)
        self.listbox.set_title(f"可用组件（已启用 {len(self.components)}）")

    def _on_selection_changed(self):
        if self._building_form:
            return
        selected_raw = self.listbox.get_selected_raw_names()
        self.components = selected_raw
        self._rebuild_params_form()

    def _select_all(self):
        self.listbox.select_all()
        self._on_selection_changed()

    def _clear_all(self):
        self.listbox.clear_selection()
        self._on_selection_changed()

    # ---------------- 参数表单 ----------------
    def _rebuild_params_form(self):
        """为当前选中的第一个组件重建参数表单（一次编辑一个组件）。"""
        for child in self.params_scroll.winfo_children():
            child.destroy()
        if not self.components:
            self.params_hint = ctk.CTkLabel(
                self.params_scroll, text="未启用任何组件。左侧勾选以添加。",
                font=ui_fonts.ui_font(12), text_color=SOFT)
            self.params_hint.pack(anchor='w', padx=6, pady=8)
            return
        cid = self.components[0]
        desc = self._desc_by_id(cid)
        if desc is None:
            ctk.CTkLabel(self.params_scroll, text=f"组件 {cid} 无参数信息",
                         font=ui_fonts.ui_font(12), text_color=SOFT).pack(anchor='w', padx=6, pady=8)
            return

        specs = desc.get("param_specs") or []
        if not specs:
            ctk.CTkLabel(self.params_scroll, text=f"组件「{cid}」无可配置参数",
                         font=ui_fonts.ui_font(12), text_color=SOFT).pack(anchor='w', padx=6, pady=8)
            return

        ctk.CTkLabel(self.params_scroll, text=f"组件「{cid}」参数",
                     font=ui_fonts.ui_font(13, "bold"),
                     text_color=HARD_TITLE).pack(anchor='w', padx=6, pady=(4, 8))

        self._param_widgets[cid] = {}
        self._color_vars[cid] = {}
        saved = self.components_params.get(cid, {})

        for spec in specs:
            key = spec.get("key", "")
            label = spec.get("label", key)
            ptype = spec.get("type", "text")
            row = ctk.CTkFrame(self.params_scroll, fg_color="transparent")
            row.pack(fill='x', padx=6, pady=4)
            ctk.CTkLabel(row, text=label, font=ui_fonts.ui_font(12),
                         text_color=TEXT, width=110, anchor='w').pack(side='left')

            if ptype == "color":
                # 颜色：色块按钮 + hex 输入
                current = saved.get(key) or _default_value_for(spec)
                if isinstance(current, (tuple, list)):
                    current = _rgba_to_hex(current)
                hex_var = tk.StringVar(value=str(current))
                self._color_vars[cid][key] = hex_var

                color_frame = ctk.CTkFrame(row, fg_color="transparent")
                color_frame.pack(side='left', fill='x', expand=True)
                swatch = ctk.CTkButton(
                    color_frame, text="", width=34, height=26, corner_radius=6,
                    fg_color=str(current),
                    command=lambda k=key, s=spec: self._pick_color(cid, k, s))
                swatch.pack(side='left', padx=(0, 6))
                entry = ctk.CTkEntry(color_frame, textvariable=hex_var, width=110,
                                     font=ui_fonts.ui_font(12))
                entry.pack(side='left')
                entry.bind("<FocusOut>", lambda e, k=key, s=spec: self._sync_swatch(cid, k, s))
                self._param_widgets[cid][key] = hex_var
            elif ptype == "bool":
                bool_var = tk.BooleanVar(
                    value=bool(saved.get(key, _default_value_for(spec))))
                self._param_widgets[cid][key] = bool_var
                ctk.CTkSwitch(row, text="", variable=bool_var,
                              font=ui_fonts.ui_font(12)).pack(side='left', padx=6)
            elif ptype == "int":
                var = tk.StringVar(
                    value=str(saved.get(key, _default_value_for(spec))))
                self._param_widgets[cid][key] = var
                ctk.CTkEntry(row, textvariable=var, width=120,
                             font=ui_fonts.ui_font(12)).pack(side='left', padx=6)
            else:  # text / 其他
                var = tk.StringVar(
                    value=str(saved.get(key, _default_value_for(spec))))
                self._param_widgets[cid][key] = var
                ctk.CTkEntry(row, textvariable=var, width=200,
                             font=ui_fonts.ui_font(12)).pack(side='left', padx=6, fill='x', expand=True)

    def _pick_color(self, cid, key, spec):
        """打开系统颜色选择器，更新色块与 hex 输入。"""
        var = self._color_vars.get(cid, {}).get(key)
        if var is None:
            return
        current = var.get()
        result = colorchooser.askcolor(current, title=f"选择颜色 - {spec.get('label', key)}",
                                       parent=self.winfo_toplevel())
        if result and result[1]:
            var.set(result[1])
            self._sync_swatch(cid, key, spec)

    def _sync_swatch(self, cid, key, spec):
        var = self._color_vars.get(cid, {}).get(key)
        if var is None:
            return
        hex_str = var.get()
        # 找到色块按钮并更新
        for child in self.params_scroll.winfo_children():
            for sub in child.winfo_children():
                if isinstance(sub, ctk.CTkFrame):
                    for btn in sub.winfo_children():
                        if isinstance(btn, ctk.CTkButton) and btn.cget("text") == "":
                            try:
                                btn.configure(fg_color=hex_str)
                            except Exception:
                                pass

    # ---------------- 保存 ----------------
    def collect_params(self) -> dict:
        """从表单控件收集各已启用组件的参数覆盖（仅记录被修改/非默认的）。"""
        result = {}
        for cid in self.components:
            widgets = self._param_widgets.get(cid, {})
            desc = self._desc_by_id(cid)
            specs = (desc or {}).get("param_specs", []) or []
            entry = {}
            for spec in specs:
                key = spec.get("key", "")
                widget = widgets.get(key)
                if widget is None:
                    continue
                ptype = spec.get("type", "text")
                if ptype == "color":
                    entry[key] = _hex_to_rgba(widget.get())
                elif ptype == "bool":
                    entry[key] = bool(widget.get())
                elif ptype == "int":
                    try:
                        entry[key] = int(widget.get())
                    except (TypeError, ValueError):
                        entry[key] = spec.get("default")
                else:
                    entry[key] = widget.get()
            result[cid] = entry
        return result

    def save_items(self):
        """保存组件选择与参数到副本配置。"""
        editor = self.scenario_editor
        if not editor.current_scenario_id:
            return
        config = editor._dungeon_repo.load_config(editor.current_scenario_id)
        if config is None:
            config = {}
        config["components"] = list(self.components)
        config["components_params"] = self.collect_params()
        editor._dungeon_repo.save_config(editor.current_scenario_id, config)
        # 同步内部状态与编辑器，保持一致性
        self.components_params = config["components_params"]
        self.scenario_editor.components = list(self.components)
        self.scenario_editor.components_params = dict(self.components_params)
        ui.common.dialogs.showinfo("成功", "组件设置已保存")

    # ---------------- 由编辑器调用 ----------------
    def refresh_list(self):
        """外部（加载副本时）同步组件选择与参数。"""
        editor = self.scenario_editor
        self.components = list(getattr(editor, "components", []) or [])
        self.components_params = dict(getattr(editor, "components_params", {}) or {})
        self._refresh_component_list()
        self._rebuild_params_form()


__all__ = ["ComponentManager"]
