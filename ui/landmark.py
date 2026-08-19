import os
import re
import tkinter as tk

import customtkinter as ctk

import ui.common.dialogs
from models import Landmark
from persistence.landmark_repo import LandmarkRepo, DEFAULT_LANDMARK_STYLE
from services import get_challenge_packs, import_landmark_challenge_pack
from ui.common.dialogs import BaseDialog
from ui.common.managers import CardManager
from ui.common.widgets import ClickableCard, CTkScrollableDropdownFrame
from ui.common.theme import (
    BASE, HOVER, BORDER_ALT, TEXT, SOFT,
    PNL_BG, HOVER_ALT, MENU_HOVER, LINK_BLUE,
    BLUE_HOVER, STATUS_OK, OK_HOVER, ERR_STRONG, ERR_HOVER,
)
from ui.common import fonts as ui_fonts


class LandmarkCardManager(CardManager):
    SIZE_RANGES = [
        (0, 3, "0~3米"),
        (3, 10, "3~10米"),
        (10, 30, "10~30米"),
        (30, 100, "30~100米"),
        (100, 300, "100~300米"),
        (300, 1000, "300~1000米"),
        (1000, 3000, "1~3千米"),
        (3000, 10000, "3~10千米"),
        (10000, 30000, "10~30千米"),
        (30000, 100000, "30~100千米"),
        (100000, 300000, "100~300千米"),
        (300000, float("inf"), "300千米以上"),
    ]

    def __init__(self, parent, landmark_repo: LandmarkRepo, settings_repo, gui_ref):
        self.landmark_repo = landmark_repo
        self._settings_repo = settings_repo
        self.style_var = tk.StringVar()
        super().__init__(parent, gui_ref, item_name="地标")
        self.create_style_widgets(self.style_frame)
        self.refresh_styles()
        self.show_first_view()

    # ----- 实现基类抽象方法 -----
    def _get_style_list(self):
        return self.landmark_repo.get_styles()

    def _get_current_style(self):
        return self.gui.current_style

    def _set_current_style(self, style):
        self.gui.current_style = style

    def _get_default_style(self):
        return DEFAULT_LANDMARK_STYLE

    def _create_style_impl(self, name):
        self.landmark_repo.create_style(name)

    def _rename_style_impl(self, old, new):
        self.landmark_repo.rename_style(old, new)

    def _delete_style_impl(self, name):
        self.landmark_repo.delete_style(name)

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
        styles = self.landmark_repo.get_styles()
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
                imported = import_landmark_challenge_pack(self._settings_repo, pack_name, self.landmark_repo)
            except ValueError as e:
                ui.common.dialogs.showerror("错误", str(e))
                self._set_combo_to_current_style()
                return
            self.refresh_styles()
            self.load_items()
            self.show_first_view()
            self.gui.load_combined_landmarks()
            ui.common.dialogs.showinfo("成功", f"已从挑战包导入 {imported} 条地标数据")
        else:
            new_style = choice
            if new_style == self._get_current_style():
                return
            self._set_current_style(new_style)
            self.load_items()
            self.show_first_view()
            self.gui.sync_landmark_styles_state()

    def _set_combo_to_current_style(self):
        current = self._get_current_style()
        self.style_var.set(current)

    def refresh_styles(self):
        self.styles = self.landmark_repo.get_styles()
        self._rebuild_dropdown()
        if self.gui.current_style not in self.styles:
            self.gui.current_style = self.styles[0] if self.styles else DEFAULT_LANDMARK_STYLE
        self.style_var.set(self.gui.current_style)

    # ---------- 数据加载与保存 ----------
    def load_items(self):
        self.all_items = self.landmark_repo.load(self.gui.current_style)

    def save_items(self, items):
        self.gui.landmarks = items
        self.landmark_repo.save(self.gui.current_style, items)
        self.gui.load_combined_landmarks()
        self.all_items = items
        self.refresh_ui()

    # ---------- 尺寸分类 ----------
    @staticmethod
    def _size_category(size):
        for low, high, name in LandmarkCardManager.SIZE_RANGES:
            if low <= size < high:
                return name
        return "300千米以上"

    def get_category(self, item):
        return self._size_category(item.size)

    def get_card_display(self, item):
        if item.dimension == "vertical":
            dim_text = "高"
        else:
            dim_text = "长" if item.horizontal_type == "length" else "宽"
        freq_text = "精确" if item.frequency == "unique" else "均值"
        return {
            "名称": item.name,
            "尺寸": f"{item.size}米",
            "属性": f"{dim_text} | {freq_text}"
        }

    # ---------- 排序控件 ----------
    def create_sort_widgets(self, parent):
        self.sort_var = tk.StringVar(value="size_asc")
        ctk.CTkRadioButton(parent, text="尺寸\u2191", variable=self.sort_var,
                           value="size_asc", command=self.refresh_ui,
                           font=ui_fonts.ui_font(13),
                           fg_color=(HOVER[1], TEXT[1]),
                           border_color=BORDER_ALT,
                           hover_color=HOVER,
                           text_color=TEXT).pack(side='left', padx=2)
        ctk.CTkRadioButton(parent, text="尺寸\u2193", variable=self.sort_var,
                           value="size_desc", command=self.refresh_ui,
                           font=ui_fonts.ui_font(13),
                           fg_color=(HOVER[1], TEXT[1]),
                           border_color=BORDER_ALT,
                           hover_color=HOVER,
                           text_color=TEXT).pack(side='left', padx=2)

    # ---------- 视图切换，控制排序控件可见性 ----------
    def show_first_view(self):
        self.current_view = "first"
        self.second_view.pack_forget()
        self.first_view.pack(fill='both', expand=True)
        self.back_btn.pack_forget()
        self._hide_category_title()
        self.sort_frame.pack(anchor='w')
        self._populate_categories()

    def show_second_view(self, category_key):
        self.current_view = "second"
        self.current_category = category_key
        self.first_view.pack_forget()
        self.second_view.pack(fill='both', expand=True)
        self.back_btn.pack(anchor='w', pady=(5, 0))
        self._show_category_title(category_key)
        self.sort_frame.pack_forget()
        self._populate_items(category_key)

    # ---------- 分类视图渲染（按尺寸排序）----------
    def _populate_categories(self):
        for w in self.first_view.winfo_children():
            w.destroy()

        self.load_items()
        cat_counts = {}
        cat_example = {}
        cat_items = {}
        for item in self.all_items:
            cat = self.get_category(item)
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
            if cat not in cat_example:
                cat_example[cat] = item
            if cat not in cat_items:
                cat_items[cat] = []
            cat_items[cat].append(item)

        if not cat_counts:
            ctk.CTkLabel(self.first_view, text="暂无条目，请先添加",
                         font=ui_fonts.ui_font(14),
                         text_color=SOFT).pack(pady=20)
            return

        sort_mode = self.sort_var.get()

        def cat_sort_key(cat_name):
            example = cat_example.get(cat_name)
            size = example.size if example else 0
            if sort_mode == "size_desc":
                return -size
            return size

        sorted_cats = sorted(cat_counts.keys(), key=cat_sort_key)

        for cat in sorted_cats:
            items_in_cat = cat_items.get(cat, [])
            shown_names = [it.name for it in items_in_cat[:5]]
            names_text = "\u3001".join(shown_names)
            if len(items_in_cat) > 5:
                names_text += f"  等{len(items_in_cat)}条"
            else:
                names_text += f"  共{len(items_in_cat)}条"

            card = ClickableCard(
                self.first_view,
                title=cat,
                title_font=ui_fonts.ui_font(14, "bold"),
                title_extra=[{
                    "text": names_text,
                    "font": ui_fonts.ui_font(12),
                    "text_color": SOFT
                }],
                on_click=lambda c=cat: self.show_second_view(c)
            )
            card.pack(fill='x', padx=10, pady=4)

    def _populate_items(self, category_key):
        for w in self.second_view.winfo_children():
            w.destroy()

        items = [item for item in self.all_items if self.get_category(item) == category_key]
        items.sort(key=lambda x: x.size)

        for item in items:
            bold_text = f"{item.name}  {item.size}米"

            if item.dimension == "vertical":
                dim_text = "高"
            else:
                dim_text = "长" if item.horizontal_type == "length" else "宽"
            freq_text = "精确" if item.frequency == "unique" else "均值"
            sub_text = f"{dim_text}方向 \u00B7 {freq_text}"

            card = ClickableCard(
                self.second_view,
                title=bold_text,
                title_font=ui_fonts.ui_font(14, "bold"),
                title_extra=[{
                    "text": sub_text,
                    "font": ui_fonts.ui_font(12),
                    "text_color": SOFT
                }],
                on_click=lambda i=item: self.edit_item(i),
                buttons=[
                    {"text": "删除",
                     "command": lambda i=item: self.delete_item(i),
                     "fg_color": "transparent",
                     "text_color": ERR_STRONG,
                     "hover_color": ERR_HOVER,
                     "border_width": 1, "border_color": ERR_STRONG,
                     "corner_radius": 6, "width": 50}
                ]
            )
            card.pack(fill='x', padx=5, pady=3)

    # ---------- 增删改实现----------
    @staticmethod
    def _parse_category(category: str) -> tuple:
        m = re.search(r'介入(\d+)\s+破坏(\d+)', category)
        if m:
            return int(m.group(1)), int(m.group(2))
        return 1, 1

    def add_item_in_category(self):
        dlg = LandmarkDialog(self, "添加地标", None)
        if dlg.result:
            new_item = Landmark(
                name=dlg.result["name"],
                size=dlg.result["size"],
                dimension=dlg.result["dimension"],
                frequency=dlg.result["frequency"],
                horizontal_type=dlg.result.get("horizontal_type")
            )
            self.all_items.append(new_item)
            self.save_items(self.all_items)
            if self.current_view == "second" and self.current_category:
                self.show_second_view(self.current_category)
            else:
                self.show_first_view()

    def edit_item(self, item):
        dlg = LandmarkDialog(self, f"编辑地标", item)
        if dlg.result:
            item.name = dlg.result["name"]
            item.size = dlg.result["size"]
            item.dimension = dlg.result["dimension"]
            item.frequency = dlg.result["frequency"]
            item.horizontal_type = dlg.result.get("horizontal_type")
            self.save_items(self.all_items)
            if self.current_view == "second":
                self.show_second_view(self.current_category)
            else:
                self.show_first_view()

    def delete_item(self, item):
        if ui.common.dialogs.askyesno("确认", f"确定要删除地标 '{item.name}' 吗？"):
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

    def _on_module_switch(self, value):
        if value == "描述管理":
            if hasattr(self.gui, 'show_quip_manager'):
                self.gui.show_quip_manager()


class LandmarkDialog(BaseDialog):
    """地标编辑对话框"""

    def __init__(self, parent, title, landmark=None):
        super().__init__(parent.winfo_toplevel())
        self.title(title)
        self.landmark = landmark
        self.result = None
        self._parent = parent.winfo_toplevel()   # 保存顶层父窗口引用

        # 模态设置
        self.transient(self._parent)
        self.grab_set()

        self._create_widgets()
        self.geometry("368x207")
        self._center_dialog(parent)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # 等待窗口关闭后再返回
        self.wait_window()

    def _create_widgets(self):
        # 名称
        ctk.CTkLabel(self, text="名称:", font=self.UI_FONT_BOLD).grid(row=0, column=0, sticky='w', pady=(10,4), padx=(20,5))
        self.name_var = tk.StringVar(value=self.landmark.name if self.landmark else "")
        ctk.CTkEntry(self, textvariable=self.name_var, width=210, height=28,
                     font=self.UI_FONT).grid(row=0, column=1, pady=(10,4), padx=5)

        # 尺寸(米)
        ctk.CTkLabel(self, text="尺寸(米):", font=self.UI_FONT).grid(row=1, column=0, sticky='w', pady=(4,6), padx=(20,5))
        self.size_var = tk.StringVar(value=str(self.landmark.size) if self.landmark else "")
        ctk.CTkEntry(self, textvariable=self.size_var, width=210, height=28,
                     font=self.UI_FONT).grid(row=1, column=1, pady=(4,6), padx=5)

        # 地标方向：长、宽、高
        ctk.CTkLabel(self, text="方向:", font=self.UI_FONT).grid(row=2, column=0, sticky='w', pady=(6,4), padx=20)
        self.type_var = tk.StringVar()
        if self.landmark:
            if self.landmark.dimension == "vertical":
                self.type_var.set("vertical")
            elif self.landmark.dimension == "horizontal":
                self.type_var.set("width" if self.landmark.horizontal_type == "width" else "length")
            else:
                self.type_var.set("vertical")
        else:
            self.type_var.set("vertical")

        type_frame = ctk.CTkFrame(self, fg_color="transparent")
        type_frame.grid(row=2, column=1, sticky='w', pady=(6,4))
        ctk.CTkRadioButton(type_frame, text="长", variable=self.type_var, value="length",
                           font=self.UI_FONT).pack(side='left', padx=5)
        ctk.CTkRadioButton(type_frame, text="宽", variable=self.type_var, value="width",
                           font=self.UI_FONT).pack(side='left', padx=5)
        ctk.CTkRadioButton(type_frame, text="高", variable=self.type_var, value="vertical",
                           font=self.UI_FONT).pack(side='left', padx=5)

        # 类型选择
        ctk.CTkLabel(self, text="类型:", font=self.UI_FONT).grid(row=3, column=0, sticky='w', pady=4, padx=20)
        self.frequency_var = tk.StringVar(value=self.landmark.frequency if self.landmark else "unique")
        freq_frame = ctk.CTkFrame(self, fg_color="transparent")
        freq_frame.grid(row=3, column=1, sticky='w', pady=4)
        ctk.CTkRadioButton(freq_frame, text="精确", variable=self.frequency_var, value="unique",
                           font=self.UI_FONT).pack(side='left', padx=5)
        ctk.CTkRadioButton(freq_frame, text="均值", variable=self.frequency_var, value="common",
                           font=self.UI_FONT).pack(side='left', padx=5)

        # 确定/取消按钮
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=4, column=0, columnspan=2, pady=15)
        ctk.CTkButton(btn_frame, text="确定", command=self.ok, width=80,
                      height=28, font=self.UI_FONT).pack(side='left', padx=(0,7))
        ctk.CTkButton(btn_frame, text="取消", command=self._on_close, width=80,
                      height=28, font=self.UI_FONT).pack(side='left', padx=7)

    def validate(self):
        if not self.name_var.get().strip():
            ui.common.dialogs.showwarning("警告", "请输入地标名称")
            return False
        try:
            size = float(self.size_var.get())
            if size <= 0:
                raise ValueError("尺寸必须大于0")
        except ValueError:
            ui.common.dialogs.showwarning("警告", "请输入有效的正数尺寸")
            return False
        return True

    def ok(self):
        if not self.validate():
            return
        type_choice = self.type_var.get()
        if type_choice == "vertical":
            dimension = "vertical"
            horizontal_type = None
        elif type_choice == "length":
            dimension = "horizontal"
            horizontal_type = "length"
        elif type_choice == "width":
            dimension = "horizontal"
            horizontal_type = "width"
        else:
            dimension = "vertical"
            horizontal_type = None

        self.result = {
            "name": self.name_var.get().strip(),
            "size": float(self.size_var.get()),
            "dimension": dimension,
            "frequency": self.frequency_var.get(),
            "horizontal_type": horizontal_type
        }
        self._on_close()

    def _on_close(self):
        """安全关闭对话框，释放抓取并销毁窗口"""
        try:
            self.grab_release()          # 释放模态抓取
        except Exception:
            pass
        self.withdraw()                 # 先隐藏，避免 CTk 缩放追踪器报错
        self.destroy()
        # 强制父窗口重获焦点并刷新，解决透明/冻结问题
        try:
            self._parent.focus_force()
            self._parent.update_idletasks()
        except Exception:
            pass
