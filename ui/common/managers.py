from tkinter import ttk

import ui.common.dialogs
from typing import List, Any, Optional

import customtkinter as ctk

from ui.common.dialogs import InputDialog
from ui.common.widgets import ClickableCard, CTkSegmentedControl
from ui.common.theme import (
    BASE, BORDER, HOVER, BORDER_ALT, TEXT, SOFT,
    HOVER_ALT, STATUS_OK, OK_HOVER, ERR_STRONG, ERR_HOVER,
    TREE_ALT, TREE_SELECT_BG, TREE_SELECT_FG, TREE_HEAD_BG, TREE_HEAD_FG,
    TREE_DISABLED_FG,
)
from ui.common import fonts as ui_fonts


class TreeviewManager(ctk.CTkFrame):
    """
    通用数据库管理面板基类。
    提供 Treeview 列表、刷新、增删改按钮的标准实现。
    子类需重写部分方法以适配具体数据模型。
    """
    def __init__(self, parent, repository, columns: List[tuple],
                 title: str = "管理", item_name: str = "项目"):
        """
        :param parent: 父容器
        :param repository: 数据仓库实例
        :param columns: Treeview 列定义，格式 [(列标题, 宽度, 数据键名), ...]
        :param title: 标签页标题（未使用）
        :param item_name: 项目名称（仅用于提示，不再用于按钮文本）
        """
        super().__init__(parent, fg_color=BASE)
        self.repository = repository
        self.columns = columns
        self.item_name = item_name
        self._create_ui()
        self.update_theme(ctk.get_appearance_mode())

    def update_theme(self, theme_mode: str):
        style = ttk.Style()
        if style.theme_use() != 'clam':
            style.theme_use('clam')

        if theme_mode == "Dark":
            bg_color = BASE[1]
            alt_color = TREE_ALT[1]
            fg_color = TEXT[1]
            select_bg = TREE_SELECT_BG[1]
            select_fg = TREE_SELECT_FG[1]
            heading_bg = TREE_HEAD_BG[1]
            heading_fg = TREE_HEAD_FG[1]
            field_bg = BASE[1]
            border_color = BORDER[1]
        else:
            bg_color = BASE[0]
            alt_color = TREE_ALT[0]
            fg_color = TEXT[0]
            select_bg = TREE_SELECT_BG[0]
            select_fg = TREE_SELECT_FG[0]
            heading_bg = TREE_HEAD_BG[0]
            heading_fg = TREE_HEAD_FG[0]
            field_bg = BASE[0]
            border_color = BORDER[0]

        style.configure("Custom.Treeview",
                        background=bg_color,
                        foreground=fg_color,
                        fieldbackground=field_bg,
                        rowheight=34,
                        font=ui_fonts.ui_font(16),
                        borderwidth=0,
                        bordercolor=bg_color,
                        lightcolor=bg_color,
                        darkcolor=bg_color)
        style.map("Custom.Treeview",
                  background=[('selected', select_bg)],
                  foreground=[('selected', select_fg)],
                  bordercolor=[('focus', bg_color)])

        style.layout("Custom.Treeview", [
            ("Custom.Treeview.field", {
                "sticky": "nswe",
                "children": [
                    ("Custom.Treeview.padding", {
                        "sticky": "nswe",
                        "children": [
                            ("Custom.Treeview.treearea", {"sticky": "nswe"})
                        ]
                    })
                ]
            })
        ])

        style.configure("Custom.Treeview.Heading",
                        background=heading_bg,
                        foreground=heading_fg,
                        font=ui_fonts.ui_font(16, "bold"),
                        relief="flat",
                        borderwidth=0)
        style.map("Custom.Treeview.Heading",
                  background=[('active', heading_bg)])

        # 隔行变色标签
        if hasattr(self, 'tree'):
            self.tree.configure(style="Custom.Treeview")
            self.tree.tag_configure("evenrow", background=alt_color)
            self.tree.tag_configure("disabled", foreground=TREE_DISABLED_FG)

    def _create_ui(self):
        """构建标准 UI 布局（使用 CTk 组件）- 探索模式风格"""
        # 顶部工具栏区域（留给子类自定义）
        self.toolbar_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.toolbar_frame.pack(fill='x', padx=5, pady=(5, 0))
        self.create_toolbar()

        # 中间列表区域 - 带圆角边框
        list_frame = ctk.CTkFrame(self, fg_color="transparent",
                                  border_width=1, corner_radius=12,
                                  border_color=BORDER)
        list_frame.pack(fill='both', expand=True, padx=9, pady=(0,9))
        self.list_frame = list_frame

        col_ids = [f"col_{i}" for i in range(len(self.columns))]
        self.tree = ttk.Treeview(list_frame, columns=col_ids, show="headings", style="Custom.Treeview")
        for i, (heading, width, _) in enumerate(self.columns):
            self.tree.heading(col_ids[i], text=heading)
            self.tree.column(col_ids[i], width=width)

        self.scrollbar = ctk.CTkScrollbar(list_frame, orientation="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=self.scrollbar.set)

        self.tree.pack(side='left', fill='both', expand=True, padx=(5, 2), pady=5)
        self.scrollbar.pack(side='right', fill='y', padx=(2, 5), pady=5)

        # 底部按钮栏
        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.pack(fill='x', padx=5, pady=(0, 5))
        self.button_frame = button_frame

        _btn_style = dict(
            fg_color="transparent", border_width=1, corner_radius=8,
            font=ui_fonts.ui_font(13)
        )
        _btn_text = SOFT
        _btn_hover = HOVER
        _btn_border = BORDER_ALT

        # 左侧按钮组（添加、编辑、删除）
        left_btn_frame = ctk.CTkFrame(button_frame, fg_color="transparent")
        left_btn_frame.pack(side='left', fill='x', expand=True)

        ctk.CTkButton(left_btn_frame, text="添加",
                      command=self.add_item, width=80,
                      text_color=_btn_text, hover_color=_btn_hover,
                      border_color=_btn_border, **_btn_style).pack(side='left', padx=5)
        ctk.CTkButton(left_btn_frame, text="编辑",
                      command=self.edit_item, width=80,
                      text_color=_btn_text, hover_color=_btn_hover,
                      border_color=_btn_border, **_btn_style).pack(side='left', padx=5)
        ctk.CTkButton(left_btn_frame, text="删除",
                      command=self.delete_item, width=80,
                      fg_color="transparent", border_width=1, corner_radius=8,
                      text_color=ERR_STRONG,
                      hover_color=ERR_HOVER,
                      border_color=ERR_STRONG,
                      font=ui_fonts.ui_font(13)).pack(side='left', padx=5)

        # 右侧按钮组（上移、下移）
        right_btn_frame = ctk.CTkFrame(button_frame, fg_color="transparent")
        right_btn_frame.pack(side='right', fill='x', expand=True)

        ctk.CTkButton(right_btn_frame, text="上移",
                      command=self.move_up, width=80,
                      text_color=_btn_text, hover_color=_btn_hover,
                      border_color=_btn_border, **_btn_style).pack(side='right', padx=5)
        ctk.CTkButton(right_btn_frame, text="下移",
                      command=self.move_down, width=80,
                      text_color=_btn_text, hover_color=_btn_hover,
                      border_color=_btn_border, **_btn_style).pack(side='right', padx=5)

    def create_toolbar(self):
        """子类可重写此方法以添加顶部自定义控件"""
        pass

    # ---------- 需要子类实现的方法 ----------
    def get_items(self) -> List[Any]:
        """返回当前所有数据项的列表"""
        raise NotImplementedError

    def get_item_values(self, item: Any) -> tuple:
        """
        将数据对象转换为 Treeview 显示的一行值 (tuple)。
        顺序必须与 self.columns 中定义的数据键对应。
        """
        raise NotImplementedError

    def save_items(self, items: List[Any]):
        """保存数据列表到持久化存储"""
        raise NotImplementedError

    def create_item_dialog(self, item: Optional[Any] = None):
        """
        打开添加/编辑对话框，返回新数据字典（用于新建）或修改后的对象。
        若用户取消则返回 None。
        """
        raise NotImplementedError

    # ---------- 通用操作方法 ----------
    def refresh_list(self):
        self.tree.delete(*self.tree.get_children())
        for i, item in enumerate(self.get_items()):
            tags = ("evenrow",) if i % 2 == 0 else ()
            self.tree.insert("", "end", values=self.get_item_values(item), tags=tags)

    def get_selected_index(self) -> int:
        """返回当前选中项的索引，若未选中返回 -1"""
        selection = self.tree.selection()
        if not selection:
            return -1
        # 获取选中项在 Treeview 中的索引（所有子项列表中的位置）
        all_items = self.tree.get_children()
        return all_items.index(selection[0])

    def get_selected_item(self) -> Optional[Any]:
        """返回当前选中的数据对象，若未选中返回 None"""
        idx = self.get_selected_index()
        if idx == -1:
            return None
        items = self.get_items()
        if 0 <= idx < len(items):
            return items[idx]
        return None

    def add_item(self):
        """添加新项"""
        result = self.create_item_dialog()
        if result is not None:
            items = self.get_items()
            items.append(result)  # 假设 create_item_dialog 返回可直接添加的对象
            self.save_items(items)
            self.refresh_list()

    def edit_item(self):
        """编辑选中项"""
        item = self.get_selected_item()
        if item is None:
            ui.common.dialogs.showwarning("警告", "请先选择一个项目")
            return
        idx = self.get_selected_index()
        result = self.create_item_dialog(item)
        if result is not None:
            items = self.get_items()
            items[idx] = result  # 直接替换
            self.save_items(items)
            self.refresh_list()

    def delete_item(self):
        """删除选中项"""
        item = self.get_selected_item()
        if item is None:
            return
        idx = self.get_selected_index()
        # 子类可以重写获取显示名称的方法，这里默认尝试获取 name 属性
        name = getattr(item, 'name', str(item))
        if ui.common.dialogs.askyesno("确认", f"确定要删除 '{name}' 吗？"):
            items = self.get_items()
            del items[idx]
            self.save_items(items)
            self.refresh_list()

    def move_up(self):
        """将选中项上移一位"""
        idx = self.get_selected_index()
        if idx == -1:
            ui.common.dialogs.showwarning("警告", "请先选择一个项目")
            return
        if idx == 0:
            return
        items = self.get_items()
        items[idx], items[idx - 1] = items[idx - 1], items[idx]
        self.save_items(items)
        self.refresh_list()
        # 重新选中移动后的项
        children = self.tree.get_children()
        if idx - 1 < len(children):
            self.tree.selection_set(children[idx - 1])

    def move_down(self):
        """将选中项下移一位"""
        idx = self.get_selected_index()
        if idx == -1:
            ui.common.dialogs.showwarning("警告", "请先选择一个项目")
            return
        items = self.get_items()
        if idx == len(items) - 1:
            return
        items[idx], items[idx + 1] = items[idx + 1], items[idx]
        self.save_items(items)
        self.refresh_list()
        # 重新选中移动后的项
        children = self.tree.get_children()
        if idx + 1 < len(children):
            self.tree.selection_set(children[idx + 1])


class CardManager(ctk.CTkFrame):
    """重构后的卡片式管理基类 - 分离视图，流畅点击"""
    def __init__(self, parent, gui_ref, item_name="条目"):
        super().__init__(parent, fg_color="transparent", corner_radius=0)
        self.gui = gui_ref
        self.item_name = item_name

        self.current_view = "first"
        self.current_category = None
        self.all_items = []
        self._world_locked = False
        self._masked_pack = []
        self._lock_overlay = None

        self._setup_ui()

    # ---------- 抽象方法（子类实现） ----------
    def _get_style_list(self) -> List[str]:
        raise NotImplementedError

    def _get_current_style(self) -> str:
        raise NotImplementedError

    def _set_current_style(self, style: str):
        raise NotImplementedError

    def _get_default_style(self) -> str:
        raise NotImplementedError

    def _create_style_impl(self, name: str):
        raise NotImplementedError

    def _rename_style_impl(self, old: str, new: str):
        raise NotImplementedError

    def _delete_style_impl(self, name: str):
        raise NotImplementedError

    def _notify_style_list_changed(self):
        """风格新建/重命名/删除后，通知刷新各处 StyleListBox。"""
        callback = getattr(self.gui, 'refresh_style_listboxes', None)
        if callback is not None:
            callback()

    # ---------- 世界包锁定 ----------
    def set_world_locked(self, locked: bool):
        """世界包锁定该资源管理时，屏蔽除左上角切换控件外的整个视图。"""
        if locked == self._world_locked:
            return
        self._world_locked = locked
        if locked:
            self._mask_world_locked()
        else:
            self._unmask_world_locked()

    def _mask_world_locked(self):
        """隐藏全部内容视图（保留切换控件），并显示锁定遮罩。"""
        self._masked_pack = []
        # 按当前 pack 顺序收集（pack_info 不保留 before/after，需用 pack_slaves 保持布局顺序）
        for widget in [w for w in self.pack_slaves() if w is not self.top_row]:
            if widget.winfo_manager() == "pack":
                self._masked_pack.append((widget, widget.pack_info()))
                widget.pack_forget()
        for child in self.top_row.winfo_children():
            if child is not self.switch_btn and child.winfo_manager() == "pack":
                self._masked_pack.append((child, child.pack_info()))
                child.pack_forget()
        self._lock_overlay = ctk.CTkFrame(
            self, fg_color=BASE, corner_radius=12)
        ctk.CTkLabel(
            self._lock_overlay,
            text=f"世界包已锁定{self.item_name}资源管理，暂不可查看",
            font=ui_fonts.ui_font(14),
            text_color=SOFT,
        ).pack(expand=True)
        self._lock_overlay.pack(fill='both', expand=True, padx=10, pady=5)

    def _unmask_world_locked(self):
        """恢复被隐藏的视图并移除锁定遮罩。"""
        if self._lock_overlay is not None:
            self._lock_overlay.pack_forget()
            self._lock_overlay.destroy()
            self._lock_overlay = None
        for widget, info in getattr(self, "_masked_pack", []):
            widget.pack(**info)
        self._masked_pack = []

    # ---------- 通用风格管理方法 ----------
    def refresh_styles(self):
        styles = self._get_style_list()
        self.style_combo.configure(values=styles)
        current = self._get_current_style()
        if current not in styles:
            current = styles[0] if styles else self._get_default_style()
            self._set_current_style(current)
        self.style_var.set(current)

    def create_style(self):
        dlg = InputDialog(self, title="新建风格", prompt="请输入风格名称:")
        name = dlg.get_input()
        if not name or not name.strip():
            return
        try:
            self._create_style_impl(name.strip())
            self.refresh_styles()
            self.load_items()
            self.show_first_view()
        except ValueError as e:
            ui.common.dialogs.showerror("错误", str(e))
        self._notify_style_list_changed()

    def rename_style(self):
        old = self._get_current_style()
        if old == self._get_default_style():
            ui.common.dialogs.showwarning("警告", f"不能重命名默认风格")
            return
        dlg = InputDialog(self, title="重命名风格", prompt=f"将 '{old}' 重命名为:")
        new = dlg.get_input()
        if not new or not new.strip():
            return
        try:
            self._rename_style_impl(old, new.strip())
            self._set_current_style(new.strip())
            self.refresh_styles()
            self.load_items()
            self.show_first_view()
        except ValueError as e:
            ui.common.dialogs.showerror("错误", str(e))
        self._notify_style_list_changed()

    def delete_style(self):
        name = self._get_current_style()
        if name == self._get_default_style():
            ui.common.dialogs.showwarning("警告", "不能删除默认风格")
            return
        if not ui.common.dialogs.askyesno("确认", f"确定删除风格 '{name}' 吗？"):
            return
        try:
            self._delete_style_impl(name)
            self.refresh_styles()
            self.load_items()
            self.show_first_view()
        except ValueError as e:
            ui.common.dialogs.showerror("错误", str(e))
        self._notify_style_list_changed()

    # ---------- 添加按钮行为修改 ----------
    def on_add_click(self):
        self.add_item_in_category()

    def _show_category_title(self, text):
        self.category_title_label.configure(text=f"\U0001F4C1 {text}")
        self.category_title_label.pack(side='left', padx=(10, 5), before=self.back_btn)

    def _hide_category_title(self):
        self.category_title_label.pack_forget()

    def _setup_ui(self):
        self.top_row = ctk.CTkFrame(self, fg_color=BASE)
        self.top_row.pack(fill='x', padx=10, pady=6)

        self.switch_btn = CTkSegmentedControl(
            self.top_row,
            values=["地标管理", "描述管理"],
            command=self._on_module_switch,
            width=160,
            font=ui_fonts.ui_font(12)
        )
        self.switch_btn.pack(side='left', padx=5, pady=(3, 1))
        self.switch_btn.set("地标管理" if "Landmark" in self.__class__.__name__ else "描述管理")

        self.style_frame = ctk.CTkFrame(self.top_row, fg_color="transparent")
        self.style_frame.pack(side='right', padx=5)

        self.view_container = ctk.CTkFrame(self, fg_color="transparent")
        self.view_container.pack(fill='both', expand=True, padx=10, pady=5)

        self.first_view = ctk.CTkScrollableFrame(
            self.view_container,
            fg_color=BASE,
            corner_radius=12
        )
        self.second_view = ctk.CTkScrollableFrame(
            self.view_container,
            fg_color=BASE,
            corner_radius=12
        )

        self.first_view.pack(fill='both', expand=True)

        self.bottom_row = ctk.CTkFrame(self, fg_color="transparent")
        self.bottom_row.pack(fill='x', padx=10, pady=(5, 10))

        self.left_action_area = ctk.CTkFrame(self.bottom_row, fg_color="transparent")
        self.left_action_area.pack(side='left', fill='y')

        self.sort_frame = ctk.CTkFrame(self.left_action_area, fg_color="transparent")
        self.sort_frame.pack(anchor='w')
        ctk.CTkLabel(self.sort_frame, text="排序:",
                     font=ui_fonts.ui_font(13),
                     text_color=SOFT).pack(side='left', padx=5)
        self.create_sort_widgets(self.sort_frame)

        self.category_title_label = ctk.CTkLabel(
            self.left_action_area, text="",
            font=ui_fonts.ui_font(14, "bold"),
            text_color=TEXT
        )
        self.category_title_label.pack_forget()

        self.back_btn = ctk.CTkButton(
            self.left_action_area, text="\u21A9 返回分类",
            fg_color="transparent", width=100, height=28,
            text_color=SOFT,
            hover_color=HOVER_ALT,
            border_width=1, border_color=BORDER_ALT,
            corner_radius=8,
            command=self.show_first_view
        )

        self.add_btn = ctk.CTkButton(
            self.bottom_row, text=f"\uFF0B 添加{self.item_name}",
            width=120, font=ui_fonts.ui_font(14, "bold"),
            fg_color="transparent",
text_color=STATUS_OK,
                hover_color=OK_HOVER,
                border_width=2, border_color=STATUS_OK,
            corner_radius=10,
            command=self.on_add_click
        )
        self.add_btn.pack(side='right', pady=5)

    # ---------- 视图切换 ----------
    def show_first_view(self):
        self.current_view = "first"
        self.second_view.pack_forget()
        self.first_view.pack(fill='both', expand=True)
        self.back_btn.pack_forget()
        self._populate_categories()

    def show_second_view(self, category_key):
        self.current_view = "second"
        self.current_category = category_key
        self.first_view.pack_forget()
        self.second_view.pack(fill='both', expand=True)
        self.back_btn.pack(anchor='w', pady=(5, 0))
        self._populate_items(category_key)

    def refresh_ui(self):
        if self.current_view == "first":
            self._populate_categories()
        else:
            self._populate_items(self.current_category)

    def update_theme(self, theme_mode: str):
        # CTk 控件会自行响应外观变化；这里只刷新原生 ttk Treeview 样式，
        # 不销毁并重建卡片，避免主题切换后切换页面出现旧色闪烁。
        return

    # ---------- 分类视图渲染 ----------
    def _populate_categories(self):
        for w in self.first_view.winfo_children():
            w.destroy()

        self.load_items()
        cat_counts = {}
        for item in self.all_items:
            cat = self.get_category(item)
            cat_counts[cat] = cat_counts.get(cat, 0) + 1

        sorted_cats = sorted(cat_counts.keys())
        if not sorted_cats:
            ctk.CTkLabel(self.first_view, text="暂无条目，请先添加",
                         font=ui_fonts.ui_font(14),
                         text_color=SOFT).pack(pady=20)
            return

        for cat in sorted_cats:
            card = ClickableCard(
                self.first_view,
                title=f"\U0001F4C2 {cat}",
                on_click=lambda c=cat: self.show_second_view(c)
            )
            card.pack(fill='x', padx=10, pady=4)

    # ---------- 条目视图渲染 ----------
    def _populate_items(self, category_key):
        for w in self.second_view.winfo_children():
            w.destroy()

        title_label = ctk.CTkLabel(self.second_view, text=category_key,
                                   font=ui_fonts.ui_font(15, "bold"),
                                   text_color=TEXT)
        title_label.pack(anchor='w', padx=5, pady=(5, 10))

        items = [item for item in self.all_items if self.get_category(item) == category_key]
        items = self.sort_items(items)

        for item in items:
            disp = self.get_card_display(item)
            info_parts = [f"{k}: {v}" for k, v in disp.items()]
            info = " | ".join(info_parts)

            buttons = [{
                "text": "删除",
                "command": lambda i=item: self.delete_item(i),
                "fg_color": "transparent",
"text_color": ERR_STRONG,
            "hover_color": ERR_HOVER,
            "border_color": ERR_STRONG,
                "corner_radius": 6,
                "width": 50
            }]

            if hasattr(self, 'extra_buttons'):
                for btn_text, btn_cmd in self.extra_buttons:
                    buttons.append({
                        "text": btn_text,
                        "command": lambda i=item, bc=btn_cmd: bc(i),
                        "fg_color": "transparent",
                        "text_color": SOFT,
                        "hover_color": HOVER_ALT,
                        "border_width": 1,
                        "border_color": BORDER_ALT,
                        "corner_radius": 6,
                        "width": 50
                    })

            card = ClickableCard(
                self.second_view,
                title=info,
                title_font=ui_fonts.ui_font(12),
                on_click=lambda i=item: self.edit_item(i),
                buttons=buttons
            )
            card.pack(fill='x', padx=5, pady=3)

    def create_style_widgets(self, parent):
        pass

    def create_sort_widgets(self, parent):
        pass

    def load_items(self):
        raise NotImplementedError

    def get_category(self, item):
        raise NotImplementedError

    def get_card_display(self, item):
        raise NotImplementedError

    def sort_items(self, items):
        return items

    def add_item_in_category(self):
        raise NotImplementedError

    def edit_item(self, item):
        raise NotImplementedError

    def delete_item(self, item):
        raise NotImplementedError

    def _on_module_switch(self, value):
        pass
