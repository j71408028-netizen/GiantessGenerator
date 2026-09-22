"""副本显示组件管理面板（卡片式启用 + 参数自动保存）。

面板布局：可用官方组件逐个渲染为一张可展开的卡片，卡片头部是启用开关、
展示名与说明；启用后卡片展开，内嵌该组件的参数编辑表单
（text / int / bool / color）。开关切换与参数修改都会**自动保存**到副本方案
配置（输入类修改带短防抖），无需手动保存按钮。

组件 id 与参数声明来自 dungeon.window.component_registry.available_component_descriptions()，
由默认组件包（data/packs/scenarios/_default/components/components.py）提供；
展示名与说明文案维护在 _COMPONENT_META，未登记的组件回退为原始 id。
"""

import tkinter as tk
from tkinter import colorchooser

import customtkinter as ctk

from ui.common.theme import (
    SC_BORDER, SC_BORDER_STRONG, SC_TEXT_SOFT, SC_TITLE,
)
from ui.common import fonts as ui_fonts

#: 组件展示名与说明（未登记的组件回退为原始 id，无说明文案）
_COMPONENT_META = {
    "text": {
        "label": "文本栏",
        "desc": "故事正文的显示容器：故事视图占视口中部，游戏视图为底部矮栏。",
    },
    "attr_bar": {
        "label": "属性条",
        "desc": "在窗口左上角实时显示演化属性数值与总伤亡。",
    },
}


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
    """副本显示组件管理面板（挂在 ScenarioEditor 的「组件」页）。

    每个可用组件一张卡片：头部为启用开关与组件说明，启用后展开参数表单。
    参数控件在卡片内常驻（仅首次启用时构建），禁用只收起表单，再次启用
    不丢已填内容。所有修改自动保存（见 _schedule_save/_flush_save）。
    """

    #: 输入类修改的防抖落盘间隔（毫秒）
    _SAVE_DEBOUNCE_MS = 700

    def __init__(self, parent, scenario_repo, scenario_editor_ref):
        super().__init__(parent, fg_color="transparent")
        self.scenario_editor = scenario_editor_ref
        self.components = []            # 当前配置启用的组件 id 列表
        self.components_params = {}     # {组件 id: {参数key: 值}}
        self._descriptions = []
        self._param_widgets = {}        # {组件 id: {参数key: 控件/变量}}
        self._color_vars = {}           # {组件 id: {参数key: StringVar}}
        self._swatches = {}             # {(组件 id, 参数key): 色块按钮}
        self._switch_vars = {}          # {组件 id: BooleanVar}
        self._card_bodies = {}          # {组件 id: 参数表单容器}
        self._card_chrome = {}          # {组件 id: (卡片, 标题标签)}
        self._save_job = None           # 挂起的防抖保存 after id
        self._loading = False           # 重建卡片期间抑制自动保存
        # 最近一次落盘的 (启用列表, 参数)；refresh_list 用它与编辑器传入
        # 状态比对，一致时不重建卡片（保留挂起的防抖保存）
        self._saved_snapshot = (None, None)

        self._load_descriptions()
        self._build_ui()
        self._rebuild_cards()

    # ---------------- 描述加载 ----------------
    def _load_descriptions(self):
        from dungeon.window.component_registry import available_component_descriptions
        try:
            self._descriptions = available_component_descriptions()
        except Exception as exc:
            print(f"[ComponentManager] 加载组件描述失败: {exc}")
            self._descriptions = []

    def _desc_by_id(self, cid):
        return next((d for d in self._descriptions if d["id"] == cid), None)

    def _meta_by_id(self, cid):
        meta = _COMPONENT_META.get(cid) or {}
        return meta.get("label", cid), meta.get("desc", "")

    # ---------------- UI 构建 ----------------
    def _build_ui(self):
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(fill='x', padx=10, pady=6)
        ctk.CTkLabel(toolbar, text="副本显示组件：",
                     font=ui_fonts.ui_font(13, "bold"),
                     text_color=SC_TITLE).pack(side='left', padx=5)
        ctk.CTkLabel(toolbar, text="打开卡片开关启用组件，修改后自动保存",
                     font=ui_fonts.ui_font(11),
                     text_color=SC_TEXT_SOFT).pack(side='left', padx=8)

        self.cards_scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.cards_scroll.pack(fill='both', expand=True, padx=10, pady=(0, 10))

    def _rebuild_cards(self):
        """按组件描述重建全部卡片（加载副本/切页时调用）。"""
        self._loading = True
        try:
            for child in self.cards_scroll.winfo_children():
                child.destroy()
            self._param_widgets.clear()
            self._color_vars.clear()
            self._swatches.clear()
            self._switch_vars.clear()
            self._card_bodies.clear()
            self._card_chrome.clear()

            if not self._descriptions:
                ctk.CTkLabel(self.cards_scroll, text="未加载到可用组件，请检查组件包",
                             font=ui_fonts.ui_font(12),
                             text_color=SC_TEXT_SOFT).pack(anchor='w', padx=6, pady=8)
                return

            for desc in self._descriptions:
                self._build_component_card(desc)
        finally:
            self._loading = False

    def _build_component_card(self, desc):
        cid = desc["id"]
        label, blurb = self._meta_by_id(cid)
        enabled = cid in self.components

        card = ctk.CTkFrame(self.cards_scroll, fg_color="transparent",
                            border_width=1, corner_radius=12,
                            border_color=SC_BORDER_STRONG if enabled else SC_BORDER)
        card.pack(fill='x', pady=5)

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill='x', padx=12, pady=(10, 4))

        switch_var = tk.BooleanVar(value=enabled)
        ctk.CTkSwitch(head, text="启用", variable=switch_var, width=64,
                      font=ui_fonts.ui_font(11),
                      command=lambda c=cid: self._apply_card_state(c)).pack(side='left', padx=(0, 12))
        self._switch_vars[cid] = switch_var

        text_column = ctk.CTkFrame(head, fg_color="transparent")
        text_column.pack(side='left', fill='x', expand=True)
        title_row = ctk.CTkFrame(text_column, fg_color="transparent")
        title_row.pack(fill='x')
        title_label = ctk.CTkLabel(title_row, text=label,
                                   font=ui_fonts.ui_font(14, "bold"),
                                   text_color=SC_TITLE if enabled else SC_TEXT_SOFT)
        title_label.pack(side='left')
        ctk.CTkLabel(title_row, text=cid, font=ui_fonts.ui_font(11),
                     text_color=SC_TEXT_SOFT).pack(side='left', padx=(8, 0))
        if blurb:
            ctk.CTkLabel(text_column, text=blurb, font=ui_fonts.ui_font(11),
                         text_color=SC_TEXT_SOFT, anchor='w',
                         justify='left').pack(fill='x')

        body = ctk.CTkFrame(card, fg_color="transparent")
        self._card_bodies[cid] = body
        self._card_chrome[cid] = (card, title_label)
        if enabled:
            body.pack(fill='x', padx=(34, 12), pady=(0, 10))
            self._build_param_rows(cid, desc, body)

    def _build_param_rows(self, cid, desc, parent):
        """在卡片 body 内构建该组件的参数编辑行。"""
        specs = desc.get("param_specs") or []
        if not specs:
            ctk.CTkLabel(parent, text="该组件无可配置参数",
                         font=ui_fonts.ui_font(11),
                         text_color=SC_TEXT_SOFT).pack(anchor='w', pady=(0, 2))
            return

        self._param_widgets[cid] = {}
        self._color_vars[cid] = {}
        saved = self.components_params.get(cid, {})

        for spec in specs:
            key = spec.get("key", "")
            label = spec.get("label", key)
            ptype = spec.get("type", "text")
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill='x', pady=3)
            ctk.CTkLabel(row, text=label, font=ui_fonts.ui_font(12),
                         text_color=SC_TEXT_SOFT, width=110,
                         anchor='w').pack(side='left')

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
                    border_width=1, border_color=SC_BORDER_STRONG,
                    fg_color=str(current),
                    command=lambda k=key, s=spec: self._pick_color(cid, k, s))
                swatch.pack(side='left', padx=(0, 6))
                self._swatches[(cid, key)] = swatch
                entry = ctk.CTkEntry(color_frame, textvariable=hex_var, width=110,
                                     font=ui_fonts.ui_font(12))
                entry.pack(side='left')
                entry.bind("<FocusOut>",
                           lambda e, k=key, s=spec: self._on_hex_committed(cid, k, s))
                self._param_widgets[cid][key] = hex_var
            elif ptype == "bool":
                bool_var = tk.BooleanVar(
                    value=bool(saved.get(key, _default_value_for(spec))))
                self._param_widgets[cid][key] = bool_var
                ctk.CTkSwitch(row, text="", variable=bool_var,
                              font=ui_fonts.ui_font(12),
                              command=self._flush_save).pack(side='left', padx=6)
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
                             font=ui_fonts.ui_font(12)).pack(side='left', padx=6,
                                                             fill='x', expand=True)

            if ptype in ("int", "text"):
                # 输入类参数：击键防抖保存（初始赋值由 _loading 抑制）
                var.trace_add("write", lambda *_: self._schedule_save())

    # ---------------- 启用切换 ----------------
    def _apply_card_state(self, cid):
        """按开关状态更新启用列表、卡片边框/标题配色，并展开或收起参数区。"""
        desc = self._desc_by_id(cid)
        switch_var = self._switch_vars.get(cid)
        chrome = self._card_chrome.get(cid)
        body = self._card_bodies.get(cid)
        if desc is None or switch_var is None or chrome is None or body is None:
            return
        enabled = bool(switch_var.get())

        if enabled and cid not in self.components:
            self.components.append(cid)
        elif not enabled and cid in self.components:
            self.components.remove(cid)

        card, title_label = chrome
        card.configure(border_color=SC_BORDER_STRONG if enabled else SC_BORDER)
        title_label.configure(text_color=SC_TITLE if enabled else SC_TEXT_SOFT)

        if enabled:
            if not body.winfo_children():
                self._build_param_rows(cid, desc, body)
            body.pack(fill='x', padx=(34, 12), pady=(0, 10))
        else:
            body.pack_forget()
        self._flush_save()

    # ---------------- 颜色参数 ----------------
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
            self._on_hex_committed(cid, key, spec)

    def _on_hex_committed(self, cid, key, spec=None):
        """hex 输入提交（取色或失焦）：同步色块并立即保存。"""
        self._sync_swatch(cid, key, spec)
        self._flush_save()

    def _sync_swatch(self, cid, key, spec=None):
        """把 hex 输入同步到色块按钮。"""
        var = self._color_vars.get(cid, {}).get(key)
        swatch = self._swatches.get((cid, key))
        if var is None or swatch is None:
            return
        try:
            swatch.configure(fg_color=var.get())
        except Exception:
            pass

    # ---------------- 自动保存 ----------------
    def _schedule_save(self):
        """输入类修改的防抖保存：连击输入只落盘最后一次。"""
        if self._loading:
            return
        if self._save_job is not None:
            try:
                self.after_cancel(self._save_job)
            except Exception:
                pass
        self._save_job = self.after(self._SAVE_DEBOUNCE_MS, self._flush_save)

    def _cancel_pending_save(self):
        if self._save_job is not None:
            try:
                self.after_cancel(self._save_job)
            except Exception:
                pass
            self._save_job = None

    def _flush_save(self):
        """把当前启用列表与参数写入副本方案配置（静默），并同步编辑器状态。"""
        self._cancel_pending_save()
        editor = self.scenario_editor
        if self._loading or not editor.current_scenario_id:
            return
        if not self.winfo_exists():
            return
        self.components = self._ordered_components()
        config = editor._scenario_repo.load_config(editor.current_scenario_id)
        if config is None:
            config = {}
        config["components"] = list(self.components)
        # 已禁用组件的参数保留在配置里（临时关闭不丢自定义参数），
        # 启用中的组件以表单当前值为准
        params = dict(self.components_params)
        params.update(self.collect_params())
        config["components_params"] = params
        editor._scenario_repo.save_config(editor.current_scenario_id, config)
        self.components_params = params
        editor.components = list(self.components)
        editor.components_params = dict(params)
        self._saved_snapshot = (list(self.components), dict(params))

    # ---------------- 保存 ----------------
    def _ordered_components(self):
        """按描述顺序归一化启用列表，保证配置顺序稳定。"""
        known = [d["id"] for d in self._descriptions]
        return [cid for cid in known if cid in self.components]

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

    # ---------------- 由编辑器调用 ----------------
    def refresh_list(self):
        """外部（加载副本/切页时）同步组件选择与参数。

        编辑器传入状态与已落盘快照一致时不重建卡片——典型为切回本页，
        此时保留卡片与挂起的防抖保存（输入不丢）；不一致说明换了副本或
        配置被外部修改，取消防抖并按传入状态整体重建。
        """
        editor = self.scenario_editor
        incoming = (list(getattr(editor, "components", []) or []),
                    dict(getattr(editor, "components_params", {}) or {}))
        if incoming == self._saved_snapshot:
            return
        self._cancel_pending_save()
        self.components, self.components_params = incoming
        self._rebuild_cards()
        # 重建后卡片展示的正是传入状态，落快照供下次比对
        self._saved_snapshot = (list(self.components), dict(self.components_params))


__all__ = ["ComponentManager"]
