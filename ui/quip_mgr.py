import os
import tkinter as tk

import ui.common.dialogs

import customtkinter as ctk
import re

from logic import SIZE_CATEGORIES, SIZE_DISPLAY
from persistence import QuipRepo
from persistence.quip_repo import DEFAULT_QUIP_STYLE
from services import get_challenge_packs, import_quip_challenge_pack
from ui.common.managers import TreeviewManager, CardManager
from ui.common.widgets import ClickableCard, CTkScrollableDropdownFrame
from ui.common.dialogs import BaseDialog
from ui.quip_dlg import QuipDialog, _TargetSelectDialog
from ui.common.theme import (
    BASE, HOVER, BORDER_ALT, TEXT, SOFT,
    PNL_BG, HOVER_ALT, MENU_HOVER, LINK_BLUE,
    BLUE_HOVER, STATUS_OK, OK_HOVER, ERR_STRONG, ERR_HOVER,
    QUIP_TYPE_COLORS, TYPEVIEW, TYPEVIEW_HOVER, PLACEHOLDER
)
from ui.common import fonts as ui_fonts


class QuipCardManager(CardManager):
    SIZE_CATEGORIES = SIZE_CATEGORIES
    SIZE_DISPLAY = SIZE_DISPLAY
    SUMMARY_RE = re.compile(r'\s*\[summary:(.*?)\]$')

    def __init__(self, parent, quip_repo: QuipRepo, settings_repo, gui_ref):
        self.quip_repo = quip_repo
        self._settings_repo = settings_repo
        self.style_var = tk.StringVar()
        self.size_var = tk.StringVar(value="small")
        self.sort_var = tk.StringVar(value="intrusion_asc")
        self.current_intrusion = 1
        self.current_destruction = 1
        super().__init__(parent, gui_ref, item_name="描述")
        self.size_row = ctk.CTkFrame(self, fg_color="transparent")
        self.size_row.pack(fill='x', padx=10, pady=(5, 2), before=self.view_container)
        self._create_size_widgets()
        self.create_style_widgets(self.style_frame)
        self.refresh_styles()
        self.show_first_view()
        self.type_view_active = False
        self.type_view = QuipTypeViewManager(self, self)
        self.type_view.pack_forget()

    # ----- 实现基类抽象方法 -----
    def _get_style_list(self):
        return self.quip_repo.get_styles()

    def _get_current_style(self):
        return self.gui.current_quip_style

    def _set_current_style(self, style):
        self.gui.current_quip_style = style

    def _get_default_style(self):
        return DEFAULT_QUIP_STYLE

    def _create_style_impl(self, name):
        self.quip_repo.create_style(name)

    def _rename_style_impl(self, old, new):
        self.quip_repo.rename_style(old, new)

    def _delete_style_impl(self, name):
        self.quip_repo.delete_style(name)

    # ----- 风格控件 -----
    def create_style_widgets(self, parent):
        ctk.CTkLabel(parent, text="当前风格:",
                     text_color=SOFT).pack(side='left', padx=5)
        self.style_combo = ctk.CTkComboBox(
            parent, variable=self.style_var, state="readonly",
            width=150,
            fg_color=PNL_BG,
            border_color=BORDER_ALT,
            button_color=BORDER_ALT,
            button_hover_color=MENU_HOVER,
            dropdown_fg_color=PNL_BG,
            dropdown_hover_color=HOVER_ALT
        )
        self.style_combo.pack(side='left', padx=7)
        self._rebuild_dropdown()
        _btn_spec = {"fg_color": "transparent", "border_width": 1, "corner_radius": 8}
        _btn_muted = {"text_color": SOFT,
                      "hover_color": HOVER_ALT,
                      "border_color": BORDER_ALT}
        ctk.CTkButton(parent, text="新建", width=80, command=self.create_style,
                       text_color=STATUS_OK,
                       hover_color=OK_HOVER,
                       border_color=STATUS_OK,
                       **_btn_spec).pack(side='left', padx=2)
        ctk.CTkButton(parent, text="重命名", width=80, command=self.rename_style,
                       **_btn_spec, **_btn_muted).pack(side='left', padx=2)
        _del_spec = {"text_color": ERR_STRONG,
                     "hover_color": ERR_HOVER,
                     "border_color": ERR_STRONG}
        ctk.CTkButton(parent, text="删除", width=80, command=self.delete_style,
                       **_btn_spec, **_del_spec).pack(side='left', padx=2)

    def _get_challenge_packs_with_keys(self):
        packs = get_challenge_packs(self._settings_repo)
        return packs

    def _build_combined_items(self):
        styles = self.quip_repo.get_styles()
        packs = self._get_challenge_packs_with_keys()
        items = list(styles)
        text_colors = {}
        self._pack_display_map = {}
        for pack in packs:
            display = f"⚔ {os.path.splitext(pack)[0]}"
            items.append(display)
            text_colors[display] = LINK_BLUE
            self._pack_display_map[display] = pack
        return items, text_colors

    def _rebuild_dropdown(self):
        items, text_colors = self._build_combined_items()
        self.style_combo.configure(values=items if items else [])
        if hasattr(self, '_style_dropdown') and self._style_dropdown:
            self._style_dropdown.configure(values=items, text_colors=text_colors)
        else:
            self._style_dropdown = CTkScrollableDropdownFrame(
                attach=self.style_combo,
                values=items,
                text_colors=text_colors,
                command=self._on_dropdown_select,
                height=200, button_height=28,
                fg_color=BASE,
                hover_color=BLUE_HOVER,
                scrollbar_button_color=HOVER,
                scrollbar_button_hover_color=HOVER_ALT,
                frame_border_color=BORDER_ALT,
                text_color=TEXT,
                button_color=BASE,
                frame_border_width=1,
                justify="left"
            )

    def _on_dropdown_select(self, choice):
        if choice in getattr(self, '_pack_display_map', {}):
            pack_name = self._pack_display_map[choice]
            try:
                imported = import_quip_challenge_pack(self._settings_repo, pack_name, self.quip_repo)
            except ValueError as e:
                ui.common.dialogs.showerror("错误", str(e))
                self._set_combo_to_current_style()
                return
            self.refresh_styles()
            self.load_items()
            self._refresh_active_view()
            self.gui.load_combined_quips()
            ui.common.dialogs.showinfo("成功", f"已从挑战包导入 {imported} 条描述数据")
        else:
            if choice != self._get_current_style():
                self._set_current_style(choice)
                self.load_items()
                self._refresh_active_view()

    # ---------- 工具方法 ----------
    @classmethod
    def extract_summary(cls, quip_text: str) -> tuple:
        text = quip_text.strip()
        m = cls.SUMMARY_RE.search(text)
        if m:
            summary = m.group(1).strip()
            clean = cls.SUMMARY_RE.sub('', text).strip()
            return clean, summary
        else:
            summary = text[:7].strip()
            return text, summary if summary else "无简介"

    @classmethod
    def embed_summary(cls, clean_text: str, summary: str) -> str:
        clean = cls.SUMMARY_RE.sub('', clean_text).strip()
        return f"{clean}[summary:{summary}]"

    def _parse_quip_tags(self, text: str):
        pattern = r'\[([a-e]):(\d+):([^\]]+)\]'
        tags = []
        mode = ctk.get_appearance_mode()
        for m in re.finditer(pattern, text):
            start, end = m.span()
            letter = m.group(1)
            colors = QUIP_TYPE_COLORS.get(letter)
            if colors:
                color = colors[1] if mode == "Dark" else colors[0]
            else:
                color = PLACEHOLDER
            tags.append((start, end, color))
        return tags

    # ---------- 风格控件 ----------
    def edit_custom_types(self, on_saved=None):
        style = self.style_var.get()
        meta = self.quip_repo.load_meta(style)
        custom = meta.get("custom_types", {})

        dialog = BaseDialog(self)
        dialog.title(f"编辑自定义类型 - {style}")
        dialog.geometry("600x320")
        dialog.transient(self)
        dialog.grab_set()

        main_frame = ctk.CTkFrame(dialog)
        main_frame.pack(fill='both', expand=True, padx=10, pady=10)

        entries = {}
        for i, key in enumerate(["c", "d", "e"]):
            frame = ctk.CTkFrame(main_frame, border_width=1, corner_radius=6)
            frame.pack(side='left', fill='both', expand=True, padx=5, pady=5)

            ctk.CTkLabel(frame, text=f"自定义类型 {key.upper()}", font=ui_fonts.ui_font(12, "bold")).pack(anchor='w', padx=5, pady=(5, 0))

            name_row = ctk.CTkFrame(frame, fg_color="transparent")
            name_row.pack(fill='x', padx=5, pady=(5, 0))
            ctk.CTkLabel(name_row, text="名称:  ").pack(side='left')
            name_var = tk.StringVar(value=custom.get(key, {}).get("name", f"自定义{i+1}"))
            ctk.CTkEntry(name_row, textvariable=name_var, width=120).pack(side='left', padx=(5, 0))

            sub_vars = []
            subtypes = custom.get(key, {}).get("subtypes", [])
            for j in range(4):
                sub_row = ctk.CTkFrame(frame, fg_color="transparent")
                sub_row.pack(fill='x', padx=5, pady=(2, 0))
                ctk.CTkLabel(sub_row, text=f"细分{j+1}:").pack(side='left')
                default_sub = subtypes[j] if j < len(subtypes) else f"细分{j+1}"
                var = tk.StringVar(value=default_sub)
                ctk.CTkEntry(sub_row, textvariable=var, width=120).pack(side='left', padx=(5, 0))
                sub_vars.append(var)

            allow_var = tk.BooleanVar(value=custom.get(key, {}).get("allow_confusion", True))
            ctk.CTkSwitch(frame, text="允许混淆", variable=allow_var).pack(pady=(10, 5), padx=5)

            entries[key] = (name_var, sub_vars, allow_var)

        def save():
            new_custom = {}
            for key, (name_var, sub_vars, allow_var) in entries.items():
                new_custom[key] = {
                    "name": name_var.get().strip() or f"自定义{key[1]}",
                    "subtypes": [v.get().strip() or f"细分{idx+1}" for idx, v in enumerate(sub_vars)],
                    "allow_confusion": allow_var.get()
                }
            meta["custom_types"] = new_custom
            self.quip_repo.save_meta(style, meta)
            dialog.destroy()
            if on_saved:
                on_saved()

        button_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        button_frame.pack(pady=10)
        ctk.CTkButton(button_frame, text="保存", command=save, width=80).pack(side='left', padx=10)
        ctk.CTkButton(button_frame, text="取消", command=dialog.destroy, width=80).pack(side='left', padx=10)

        dialog._center_dialog(self)

    # ---------- 体型选择 ----------
    def _create_size_widgets(self):
        ctk.CTkLabel(self.size_row, text="体型:",
                     text_color=SOFT).pack(side='left', padx=5)
        for cat in self.SIZE_CATEGORIES:
            rb = ctk.CTkRadioButton(
                self.size_row, text=self.SIZE_DISPLAY.get(cat, cat),
                variable=self.size_var, value=cat,
                command=self.on_size_changed,
                fg_color=(HOVER[1], TEXT[1]),
                border_color=BORDER_ALT,
                hover_color=HOVER,
                text_color=TEXT
            )
            rb.pack(side='left', padx=5)
        _purple_btn = {"fg_color": "transparent", "border_width": 1,
                       "border_color": TYPEVIEW,
                       "text_color": TYPEVIEW,
                       "hover_color": TYPEVIEW_HOVER,
                       "corner_radius": 8}
        self.type_view_btn = ctk.CTkButton(self.size_row, text="类型视图", width=80,
                                           command=self.toggle_type_view, **_purple_btn)
        self.type_view_btn.pack(side='right', padx=6)

    # ---------- 排序控件 ----------
    def create_sort_widgets(self, parent):
        ctk.CTkRadioButton(parent, text="介入度\u2191", variable=self.sort_var,
                           value="intrusion_asc", command=self._on_category_sort_changed,
                           font=ui_fonts.ui_font(13),
                           fg_color=(HOVER[1], TEXT[1]),
                           border_color=BORDER_ALT,
                           hover_color=HOVER,
                           text_color=TEXT).pack(side='left', padx=5)
        ctk.CTkRadioButton(parent, text="破坏性\u2191", variable=self.sort_var,
                           value="destruction_asc", command=self._on_category_sort_changed,
                           font=ui_fonts.ui_font(13),
                           fg_color=(HOVER[1], TEXT[1]),
                           border_color=BORDER_ALT,
                           hover_color=HOVER,
                           text_color=TEXT).pack(side='left', padx=5)

    # ---------- 视图切换 ----------
    def show_first_view(self):
        self.current_view = "first"
        self.second_view.pack_forget()
        self.first_view.pack(fill='both', expand=True)
        self.sort_frame.pack(anchor='w')
        self.back_btn.pack_forget()
        self._hide_category_title()
        self._populate_categories()

    def show_second_view(self, category_key):
        self.current_view = "second"
        self.current_category = category_key
        parts = category_key.split()
        if len(parts) >= 2:
            try:
                self.current_intrusion = int(parts[0].replace("介入", ""))
                self.current_destruction = int(parts[1].replace("破坏", ""))
            except ValueError:
                pass
        self.first_view.pack_forget()
        self.second_view.pack(fill='both', expand=True)
        self.sort_frame.pack_forget()
        self.back_btn.pack(side='left', padx=(5, 0))
        self._show_category_title(category_key)
        self._populate_items(category_key)

    # ---------- 类型视图切换 ----------
    def toggle_type_view(self):
        if getattr(self, 'type_view_active', False):
            self._hide_type_view()
        else:
            self._show_type_view()

    def _show_type_view(self):
        self.type_view_active = True
        self.view_container.pack_forget()
        self.bottom_row.pack_forget()
        self.type_view.pack(fill='both', expand=True, padx=10, pady=5)
        self.type_view_btn.configure(text="返回描述")
        self.type_view.refresh_matches()

    def _hide_type_view(self):
        self.type_view_active = False
        self.type_view.pack_forget()
        self.view_container.pack(fill='both', expand=True, padx=10, pady=5)
        self.bottom_row.pack(fill='x', padx=10, pady=(5, 10))
        self.type_view_btn.configure(text="类型视图")
        self.refresh_ui()

    def _refresh_active_view(self):
        if getattr(self, 'type_view_active', False):
            self.type_view.refresh_matches()
        else:
            self.show_first_view()

    # ---------- 风格 / 体型变更 ----------
    def _set_combo_to_current_style(self):
        current = self._get_current_style()
        self.style_var.set(current)

    def refresh_styles(self):
        styles = self.quip_repo.get_styles()
        self._rebuild_dropdown()
        current = self._get_current_style()
        if current not in styles:
            current = styles[0] if styles else DEFAULT_QUIP_STYLE
            self._set_current_style(current)
        self.style_var.set(current)

    def on_size_changed(self):
        self.load_items()
        self._refresh_active_view()

    def update_theme(self, theme_mode: str):
        super().update_theme(theme_mode)
        if hasattr(self, 'type_view'):
            self.type_view.update_theme(theme_mode)

    def _on_category_sort_changed(self):
        if self.current_view == "first":
            self._populate_categories()

    # ---------- 数据加载与保存 ----------
    def load_items(self):
        style = self.style_var.get()
        size = self.size_var.get()
        quips = self.quip_repo.load(style)
        matrix = quips.get(size, {})
        items = []
        for (intr, dest), qlist in matrix.items():
            for q in qlist:
                text = q.get('text', '')
                step = q.get('step', 1.0)
                q_style = q.get('style', style)
                clean, summary = self.extract_summary(text)
                item = {
                    'text': clean,
                    'style': q_style,
                    'step': step,
                    'intrusion': int(intr),
                    'destruction': int(dest),
                    'summary': summary
                }
                items.append(item)
        self.all_items = items

    def save_items(self, items):
        style = self.style_var.get()
        size = self.size_var.get()
        quips = self.quip_repo.load(style)
        new_matrix = {}
        for item in items:
            full_text = self.embed_summary(item['text'], item['summary'])
            key = (item['intrusion'], item['destruction'])
            new_matrix.setdefault(key, []).append({
                'text': full_text,
                'style': item.get('style', style),
                'step': item.get('step', 1.0)
            })
        quips[size] = new_matrix
        self.quip_repo.save(style, quips)
        self.gui.load_combined_quips()
        self.all_items = items
        self.refresh_ui()

    # ---------- 分类与卡片显示 ----------
    def get_category(self, item):
        return f"介入{item['intrusion']} 破坏{item['destruction']}"

    def get_card_display(self, item):
        return {
            "简介": item['summary'] or "无简介",
            "介入": f"{item['intrusion']}",
            "破坏": f"{item['destruction']}",
            "步进": f"{item.get('step', 1.0):.2f}"
        }

    # ---------- 分类视图 ----------
    def _populate_categories(self):
        for w in self.first_view.winfo_children():
            w.destroy()

        self.load_items()
        cat_items = {}
        for item in self.all_items:
            cat = self.get_category(item)
            cat_items.setdefault(cat, []).append(item)

        if not cat_items:
            ctk.CTkLabel(self.first_view, text="暂无描述，请先添加",
                         font=ui_fonts.ui_font(14),
                         text_color=SOFT).pack(pady=20)
            return

        sort_mode = self.sort_var.get()
        def sort_key(cat):
            parts = cat.split()
            intr = int(parts[0].replace("介入", ""))
            dest = int(parts[1].replace("破坏", ""))
            return (dest, intr) if sort_mode == "destruction_asc" else (intr, dest)

        sorted_cats = sorted(cat_items.keys(), key=sort_key)

        for cat in sorted_cats:
            items_in_cat = cat_items[cat]
            summaries = [it['summary'] for it in items_in_cat[:5]]
            preview = "\u3001".join(summaries)
            if len(items_in_cat) > 5:
                preview += f"  等{len(items_in_cat)}条"
            else:
                preview += f"  共{len(items_in_cat)}条"

            card = ClickableCard(
                self.first_view,
                title=cat,
                title_font=ui_fonts.ui_font(14, "bold"),
                title_extra=[{
                    "text": preview,
                    "font": ui_fonts.ui_font(12),
                    "text_color": SOFT
                }],
                on_click=lambda c=cat: self.show_second_view(c),
                gold_hover=True
            )
            card.pack(fill='x', padx=10, pady=4)

    # ---------- 条目视图 ----------
    def _populate_items(self, category_key):
        for w in self.second_view.winfo_children():
            w.destroy()

        items = [item for item in self.all_items if self.get_category(item) == category_key]
        items = self.sort_items(items)

        for item in items:
            clean_text = item['text']
            display_len = len(clean_text) - len(self._parse_quip_tags(clean_text)) * 6
            summary_text = item['summary'] if item['summary'] else "无简介"
            step_val = item.get('step', 1.0)

            def apply_tags(tb):
                for start, end, color in self._parse_quip_tags(clean_text):
                    tag_name = f"tag_{start}_{end}"
                    tb._textbox.tag_configure(tag_name, foreground=color)
                    tb._textbox.tag_add(tag_name, f"1.0+{start}c", f"1.0+{end}c")

            card = ClickableCard(
                self.second_view,
                title=summary_text,
                title_extra=[
                    {"text": f"  ({display_len}字)",
                     "font": ui_fonts.ui_font(13, "bold"),
                     "text_color": TEXT},
                    {"text": f"步进: {step_val:.2f}",
                     "font": ui_fonts.ui_font(13),
                     "text_color": SOFT},
                ],
                detail=clean_text,
                is_detail_textbox=True,
                detail_cb=apply_tags,
                on_click=lambda i=item: self.edit_item(i),
                gold_hover=True,
                cursor="xterm",
                info_pad=(10, 6),
                buttons=[
                    {"text": "复制",
                     "command": lambda i=item: self.copy_item(i),
                     "fg_color": "transparent", "border_width": 1,
                     "border_color": BORDER_ALT,
                     "text_color": SOFT,
                     "hover_color": HOVER_ALT,
                     "corner_radius": 6, "width": 50, "height": 25,
                     "pack_kw": {"side": "top", "pady": 2}},
                    {"text": "移动",
                     "command": lambda i=item: self.move_item(i),
                     "fg_color": "transparent", "border_width": 1,
                     "border_color": BORDER_ALT,
                     "text_color": SOFT,
                     "hover_color": HOVER_ALT,
                     "corner_radius": 6, "width": 50, "height": 25,
                     "pack_kw": {"side": "top", "pady": 2}},
                    {"text": "删除",
                     "fg_color": "transparent",
"text_color": ERR_STRONG,
            "hover_color": ERR_HOVER,
            "border_width": 1, "border_color": ERR_STRONG,
                     "corner_radius": 6, "width": 50, "height": 25,
                     "command": lambda i=item: self.delete_item(i),
                     "pack_kw": {"side": "top", "pady": 2}},
                ]
            )
            card.pack(fill='x', padx=5, pady=3)

    # ---------- 复制 / 移动 ----------
    def copy_item(self, item):
        self._copy_move_item(item, copy=True)

    def move_item(self, item):
        self._copy_move_item(item, copy=False)

    def _select_target_dialog(self, src_intr, src_dest, src_size):
        gui_select = getattr(self.gui, '_select_target_dialog', None)
        if gui_select is not None:
            return gui_select(src_intr, src_dest, src_size)
        dlg = _TargetSelectDialog(self, src_intr, src_dest, src_size)
        return dlg.result

    def _copy_move_item(self, item, copy=True):
        src_intr, src_dest = item['intrusion'], item['destruction']
        src_size = self.size_var.get()

        result = self._select_target_dialog(src_intr, src_dest, src_size)
        if not result:
            return
        target_size, target_intr, target_dest = result

        if not copy and target_size == src_size and target_intr == src_intr and target_dest == src_dest:
            ui.common.dialogs.showwarning("警告", "不能移动到相同位置")
            return

        style = self.style_var.get()
        quips = self.quip_repo.load(style)

        if target_size not in quips:
            quips[target_size] = {}
        key = (target_intr, target_dest)
        if key not in quips[target_size]:
            quips[target_size][key] = []
        full_text = self.embed_summary(item['text'], item['summary'])
        quips[target_size][key].append({
            'text': full_text,
            'style': item.get('style', style),
            'step': item.get('step', 1.0)
        })

        if not copy:
            src_key = (src_intr, src_dest)
            if src_size in quips and src_key in quips[src_size]:
                lst = quips[src_size][src_key]
                to_remove = None
                for q in lst:
                    if q.get('text') == full_text and q.get('step') == item.get('step', 1.0):
                        to_remove = q
                        break
                if to_remove:
                    lst.remove(to_remove)
                if not lst:
                    del quips[src_size][src_key]

        self.quip_repo.save(style, quips)
        self.load_items()
        if self.current_view == "second":
            remaining = [i for i in self.all_items if self.get_category(i) == self.current_category]
            if not remaining:
                self.show_first_view()
            else:
                self.show_second_view(self.current_category)
        else:
            self.show_first_view()

        action = "复制" if copy else "移动"
        ui.common.dialogs.showinfo("成功", f"已{action}描述至 {target_size} {target_intr}/{target_dest}")

    # ---------- 增删改 ----------
    def add_item_in_category(self):
        default_intr, default_dest = 1, 1
        if self.current_view == "second" and self.current_category:
            parts = self.current_category.split()
            default_intr = int(parts[0].replace("介入", ""))
            default_dest = int(parts[1].replace("破坏", ""))

        dlg = QuipDialog(
            self, "添加描述",
            intrusion=default_intr,
            destruction=default_dest,
            style_name=self._get_current_style(),
            quip_repo=self.quip_repo,
            step=None
        )
        if dlg.result:
            clean_text = dlg.result["quip"]
            summary = dlg.result.get("summary", "").strip()
            if not summary:
                clean_text, summary = self.extract_summary(clean_text)
            else:
                clean_text = self.SUMMARY_RE.sub('', clean_text).strip()
            step = dlg.result.get("step", QuipRepo.default_step(default_intr, default_dest))
            new_item = {
                'text': clean_text,
                'style': self._get_current_style(),
                'step': step,
                'intrusion': int(dlg.result["intrusion"]),
                'destruction': int(dlg.result["destruction"]),
                'summary': summary
            }
            self.all_items.append(new_item)
            self.save_items(self.all_items)
            if self.current_view == "second" and self.current_category:
                self.show_second_view(self.current_category)
            else:
                self.show_first_view()

    def edit_item(self, item):
        dlg = QuipDialog(
            self, "编辑描述",
            quip=item['text'],
            intrusion=item['intrusion'],
            destruction=item['destruction'],
            summary=item['summary'],
            style_name=self.style_var.get(),
            quip_repo=self.quip_repo,
            step=item.get('step', None)
        )
        if dlg.result:
            new_clean = dlg.result["quip"]
            new_summary = dlg.result.get("summary", "").strip()
            if not new_summary:
                new_clean, new_summary = self.extract_summary(new_clean)
            else:
                new_clean = self.SUMMARY_RE.sub('', new_clean).strip()
            new_step = dlg.result.get("step", item.get('step', 1.0))
            item['text'] = new_clean
            item['summary'] = new_summary
            item['step'] = new_step
            item['intrusion'] = int(dlg.result["intrusion"])
            item['destruction'] = int(dlg.result["destruction"])
            self.save_items(self.all_items)

    def delete_item(self, item):
        if ui.common.dialogs.askyesno("确认", "确定要删除这条描述吗？"):
            self.all_items.remove(item)
            self.save_items(self.all_items)
            if self.current_view == "second":
                remaining = [i for i in self.all_items if self.get_category(i) == self.current_category]
                if not remaining:
                    self.show_first_view()
                else:
                    self.show_second_view(self.current_category)
            else:
                self.show_first_view()

    # ---------- 模块切换 ----------
    def _on_module_switch(self, value):
        if value == "地标管理" and hasattr(self.gui, 'show_landmark_manager'):
            self.gui.show_landmark_manager()

    # ---------- 排序 ----------
    def sort_items(self, items):
        mode = self.sort_var.get()
        if mode == "intrusion_asc":
            return sorted(items, key=lambda x: (x['intrusion'], x['destruction']))
        elif mode == "destruction_asc":
            return sorted(items, key=lambda x: (x['destruction'], x['intrusion']))
        return items


class QuipTypeViewManager(TreeviewManager):
    """类型视图：以 Treeview 列出当前风格/体型下全部类型标记，行按类型着色。
    底部按钮：重命名(编辑自定义类型) / 编辑(源描述) / 删除(剥离标记外壳保留内容)，无上移下移。"""

    def __init__(self, parent, quip_mgr: 'QuipCardManager'):
        self.quip_mgr = quip_mgr
        self.quip_repo = quip_mgr.quip_repo
        self._matches = []
        super().__init__(
            parent, repository=None,
            columns=[("类型", 60, "type"), ("细分", 60, "subtype"),
                     ("内容", 220, "content"), ("上下文片段", 450, "snippet")],
            item_name="类型标记"
        )
        self.toolbar_frame.pack_forget()
        self._rearrange_buttons()

    # ---------- UI ----------

    def _rearrange_buttons(self):
        for child in self.button_frame.winfo_children():
            child.destroy()

        _btn_style = dict(fg_color="transparent", border_width=1, corner_radius=8,
                          font=ui_fonts.ui_font(13))
        _btn_text = SOFT
        _btn_hover = HOVER
        _btn_border = BORDER_ALT

        left_frame = ctk.CTkFrame(self.button_frame, fg_color="transparent")
        left_frame.pack(side='left', fill='x', expand=True)

        ctk.CTkButton(left_frame, text="重命名", command=self.rename_types, width=90,
                      text_color=_btn_text, hover_color=_btn_hover,
                      border_color=_btn_border, **_btn_style).pack(side='left', padx=5)
        ctk.CTkButton(left_frame, text="编辑", command=self.edit_item, width=90,
                      text_color=_btn_text, hover_color=_btn_hover,
                      border_color=_btn_border, **_btn_style).pack(side='left', padx=5)
        ctk.CTkButton(left_frame, text="删除", command=self.delete_item, width=90,
                      fg_color="transparent", border_width=1, corner_radius=8,
text_color=ERR_STRONG,
                hover_color=ERR_HOVER,
                border_color=ERR_STRONG,
                      font=ui_fonts.ui_font(13)).pack(side='left', padx=5)

    def update_theme(self, theme_mode: str):
        super().update_theme(theme_mode)
        for letter, colors in QUIP_TYPE_COLORS.items():
            color = colors[1] if theme_mode == "Dark" else colors[0]
            self.tree.tag_configure(f"type_{letter}", foreground=color)

    # ---------- 数据 ----------
    def refresh_matches(self):
        self.load_matches()
        self.refresh_list()

    def load_matches(self):
        self.quip_mgr.load_items()
        items = self.quip_mgr.all_items

        style = self.quip_mgr.style_var.get()
        meta = self.quip_repo.load_meta(style)
        custom_types = meta.get("custom_types", {})
        builtin_type_names = {'a': '衣着', 'b': '姿势'}
        builtin_subtypes = {
            'a': ["裙子", "制服", "夏季", "冬季"],
            'b': ["站立", "坐下", "躺下", "蹲跪"]
        }

        matches = []
        pattern = re.compile(r'\[([a-e]):(\d+):([^\]]*)\]')
        for idx_in_items, item in enumerate(items):
            clean_text = item['text']
            for m in pattern.finditer(clean_text):
                letter = m.group(1)
                idx = int(m.group(2))
                content = m.group(3)
                start, end = m.span()

                if letter in builtin_subtypes:
                    type_name = builtin_type_names.get(letter, letter.upper())
                    subtypes = builtin_subtypes[letter]
                    subtype_name = subtypes[idx - 1] if 1 <= idx <= len(subtypes) else f"未知{idx}"
                elif letter in custom_types:
                    info = custom_types[letter]
                    type_name = info.get("name", f"自定义{letter.upper()}")
                    subtypes = info.get("subtypes", [])
                    subtype_name = subtypes[idx - 1] if 1 <= idx <= len(subtypes) else f"细分{idx}"
                else:
                    type_name = f"未知类型{letter}"
                    subtype_name = f"索引{idx}"

                ctx_start = max(0, start - 5)
                ctx_end = min(len(clean_text), end + 5)
                snippet = clean_text[ctx_start:ctx_end]
                matches.append((letter, idx, type_name, subtype_name, content,
                                snippet, idx_in_items, start))

        letter_order = {'a': 0, 'b': 1, 'c': 2, 'd': 3, 'e': 4}
        matches.sort(key=lambda x: (letter_order.get(x[0], 99), x[1]))
        self._matches = matches

    def get_items(self):
        return self._matches

    def get_item_values(self, item):
        letter, idx, tname, sname, cont, snippet, item_idx, start = item
        return (tname, sname, cont, snippet)

    def refresh_list(self):
        self.tree.delete(*self.tree.get_children())
        for i, match in enumerate(self._matches):
            tags = [f"type_{match[0]}"]
            if i % 2 == 0:
                tags.append("evenrow")
            self.tree.insert("", "end", values=self.get_item_values(match), tags=tuple(tags))

    # ---------- 操作 ----------
    def rename_types(self):
        self.quip_mgr.edit_custom_types(on_saved=self.refresh_matches)

    def edit_item(self):
        match = self.get_selected_item()
        if match is None:
            ui.common.dialogs.showwarning("警告", "请先选择一个类型标记")
            return
        item_idx = match[6]
        items = self.quip_mgr.all_items
        if not (0 <= item_idx < len(items)):
            ui.common.dialogs.showwarning("警告", "源描述已失效，请刷新")
            return
        self.quip_mgr.edit_item(items[item_idx])
        self.refresh_matches()

    def delete_item(self):
        match = self.get_selected_item()
        if match is None:
            ui.common.dialogs.showwarning("警告", "请先选择一个类型标记")
            return
        letter, idx, tname, sname, cont, snippet, item_idx, start = match
        items = self.quip_mgr.all_items
        if not (0 <= item_idx < len(items)):
            ui.common.dialogs.showwarning("警告", "源描述已失效，请刷新")
            return
        target_item = items[item_idx]
        text = target_item['text']
        m = re.match(rf'\[{letter}:{idx}:([^\]]*)\]', text[start:])
        if not m:
            ui.common.dialogs.showwarning("警告", "未找到对应类型标记，可能已被修改")
            return
        inner = m.group(1)
        target_item['text'] = text[:start] + inner + text[start + len(m.group(0)):]
        self.quip_mgr.save_items(items)
        self.refresh_matches()

    def create_item_dialog(self, item=None):
        return None

    def save_items(self, items):
        pass


