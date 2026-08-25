# ui/settings_dlg.py
import os
import re
import threading
import uuid
import tkinter as tk
from typing import Dict, Optional

import customtkinter as ctk

from ui.common.dialogs import BaseDialog
from ui.common.widgets import StyleListBox
from ui.common import fonts as ui_fonts
from ui.common import dialogs
from ui.common.theme import (
    TEXT, SOFT, HOVER, INPUT_BORDER, STATUS_OK, STATUS_ERR,
)

from ai import PROVIDER_DEFAULTS, create_client


class AIConfigDialog(BaseDialog):
    """OpenAI-compatible profile editor."""

    def __init__(self, parent, provider, config, is_new=False, can_delete=False):
        super().__init__(parent)
        self.title("新建 AI 配置" if is_new else f"配置{config.get('name', provider)}")
        self.provider = provider
        self.result = None
        self.delete_requested = False
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.is_new = is_new
        self.can_delete = can_delete

        defaults = PROVIDER_DEFAULTS.get(provider, {})
        self.name_var = tk.StringVar(value=config.get("name") or "新配置")
        self.url_var = tk.StringVar(value=config.get("url") or defaults.get("url", ""))
        self.model_var = tk.StringVar(value=config.get("model") or defaults.get("model", ""))
        self.key_var = tk.StringVar(value=config.get("api_key") or "")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(padx=22, pady=(18, 6), fill='x')

        # 新建配置时显示模板选择
        if is_new:
            template_frame = ctk.CTkFrame(body, fg_color="transparent")
            template_frame.pack(fill='x', pady=(0, 16))
            ctk.CTkLabel(
                template_frame, text="快速模板：", anchor='w',
                font=self.UI_FONT, text_color=TEXT
            ).pack(side='left')

            for profile_id, defaults in PROVIDER_DEFAULTS.items():
                template_name = defaults.get("name", profile_id)
                template_url = defaults.get("url", "")
                template_model = defaults.get("model", "")

                def make_template_command(name, url, model):
                    def template_command():
                        self.name_var.set(name)
                        self.url_var.set(url)
                        self.model_var.set(model)
                    return template_command

                btn = ctk.CTkButton(
                    template_frame, text=template_name, width=80, height=28,
                    command=make_template_command(template_name, template_url, template_model),
                    fg_color="transparent", text_color=SOFT,
                    hover_color=HOVER,
                    border_width=1, border_color=INPUT_BORDER,
                    corner_radius=8, font=ui_fonts.ui_font(11)
                )
                btn.pack(side='right', padx=(12, 0))

        fields = [
            ("配置名称", self.name_var, False),
            ("接口 URL", self.url_var, False),
            ("模型名称", self.model_var, False),
            ("API Key", self.key_var, True),
        ]
        for text, var, hidden in fields:
            row = ctk.CTkFrame(body, fg_color="transparent")
            row.pack(fill='x', pady=4)
            ctk.CTkLabel(
                row, text=text, width=76, anchor='w',
                font=self.UI_FONT, text_color=TEXT
            ).pack(side='left')
            entry = ctk.CTkEntry(
                row, textvariable=var, width=300, height=28,
                show="*" if hidden else None,
                font=self.UI_FONT
            )
            entry.pack(side='left')
            if not hidden and text == "接口 URL":
                self.url_entry = entry

        self.test_label = ctk.CTkLabel(
            body, text="", anchor='w', font=self.UI_FONT_SMALL,
            text_color=SOFT)
        self.test_label.pack(fill='x', pady=(6, 0))

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=(8, 16))
        self.test_btn = self._make_std_button(btn_frame, "测试连接", False, self._test_connection)
        self.test_btn.pack(side='left', padx=6)

        if can_delete:
            ctk.CTkButton(
                btn_frame, text="删除配置", width=80, height=30,
                command=self._delete_config, fg_color="transparent",
                text_color=STATUS_ERR,
                hover_color=HOVER,
                border_width=1, border_color=INPUT_BORDER,
                corner_radius=8, font=ui_fonts.ui_font(13)
            ).pack(side='left', padx=6)

        self._make_std_button(btn_frame, "确定", True, self._ok).pack(side='left', padx=6)
        self._make_std_button(btn_frame, "取消", False, self._cancel).pack(side='left', padx=6)

        self.bind("<Escape>", self._cancel)
        self._show_modal(focus_widget=getattr(self, "url_entry", None))

    def _current_config(self):
        return {
            "name": self.name_var.get().strip(),
            "url": self.url_var.get().strip(),
            "model": self.model_var.get().strip(),
            "api_key": self.key_var.get().strip(),
        }

    def _test_connection(self):
        cfg = self._current_config()
        if not cfg["name"]:
            self.test_label.configure(text="请填写配置名称", text_color=STATUS_ERR)
            return
        if not cfg["api_key"]:
            self.test_label.configure(text="请先填写 API Key", text_color=STATUS_ERR)
            return
        self.test_label.configure(text="正在测试连接...", text_color=SOFT)
        self.test_btn.configure(state="disabled")
        threading.Thread(
            target=self._do_test, args=(self.provider, cfg), daemon=True
        ).start()

    def _do_test(self, provider, cfg):
        try:
            client = create_client(
                provider, cfg["api_key"],
                base_url=cfg["url"] or None, model=cfg["model"] or None)
            ok, msg = client.test_connection()
        except Exception as e:
            ok, msg = False, str(e)
        self.after(0, lambda: self._test_done(ok, msg))

    def _test_done(self, ok, msg):
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        self.test_btn.configure(state="normal")
        if ok:
            self.test_label.configure(text="连接成功", text_color=STATUS_OK)
        else:
            text = msg or "连接失败"
            self.test_label.configure(text=f"连接失败：{text}", text_color=STATUS_ERR)

    def _ok(self, _event=None):
        self.result = self._current_config()
        if not self.result["name"]:
            self.test_label.configure(text="请填写配置名称", text_color=STATUS_ERR)
            return
        self._close()

    def _cancel(self, _event=None):
        self.result = None
        self._close()

    def _delete_config(self):
        config_name = self.name_var.get().strip() or self.provider
        if dialogs.askyesno(
            "确认删除",
            f"确定要删除配置 \"{config_name}\" 吗？\n此操作不可恢复。",
            parent=self
        ):
            self.delete_requested = True
            self.result = None
            self._close()


class WorldPackCreateDialog(BaseDialog):
    """创建世界包的配置对话框（风格参考 AI 配置对话框）。

    两级页面：基础信息 → 资源打包（可回退）。
    资源打包页用 StyleListBox 多选要打包的地标/描述风格、副本方案与挑战包，
    用下拉框单选身材/性格/姓名/新闻表与行为包；各列表标题实时显示选中数量，
    底部汇总标签同步更新。
    """

    _WORLD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

    # 打包资源类型 → 显示名（按显示顺序）
    _TYPE_LABELS = [
        ("landmarks", "地标风格"),
        ("quips", "描述风格"),
        ("presets", "身材表"),
        ("personalities", "性格表"),
        ("dungeons", "副本方案"),
        ("challenges", "挑战包"),
        ("names", "姓名表"),
        ("news", "新闻表"),
        ("behaviors", "行为包"),
    ]

    # 风格/副本/挑战包：StyleListBox 多选；静态表：下拉单选（第一项为空）
    _MULTI_TYPES = ("landmarks", "quips", "dungeons", "challenges")
    _SINGLE_TYPES = ("presets", "personalities", "names", "news", "behaviors")

    def __init__(self, parent, available_resources=None):
        super().__init__(parent)
        self.title("创建世界包")
        self.result = None
        self.geometry("450x350")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self.available_resources = available_resources or {}
        self.listboxes: Dict[str, StyleListBox] = {}
        self.combo_vars: Dict[str, tk.StringVar] = {}
        self._rtype_labels: Dict[str, str] = dict(self._TYPE_LABELS)

        # 基础信息变量（跨控件重建保留）
        self.name_var = tk.StringVar(value="")
        self.world_id_var = tk.StringVar(value="world_" + uuid.uuid4().hex[:8])
        self.version_var = tk.StringVar(value="1.0")
        self.author_var = tk.StringVar(value="")
        self._description_text = ""
        self._resource_selection = None

        self._multi_types = ["landmarks", "quips", "dungeons", "challenges"]  # 多选类型顺序
        self._multi_index = 0  # 当前显示的类型索引
        self._multi_selections = {}  # 记录每个多选类型的已选项列表
        self._single_vars = {}  # 静态表下拉框的 StringVar 字典
        self._current_listbox = None  # 当前显示的 StyleListBox 实例

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(padx=22, pady=(10, 6), fill='both', expand=True)

        self.step_area = ctk.CTkFrame(body, fg_color="transparent")
        self.step_area.pack(fill='both', expand=True)

        # 底部按钮
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=(8, 16))
        self.btn_next = self._make_std_button(btn_frame, "下一步", True, self._go_resources)
        self.btn_back = self._make_std_button(btn_frame, "上一步", False, self._go_basic)
        self.btn_ok = self._make_std_button(btn_frame, "确定", True, self._ok)
        self.btn_cancel = self._make_std_button(btn_frame, "取消", False, self._cancel)
        self.btn_next.grid(row=0, column=0, padx=6)
        self.btn_back.grid(row=0, column=1, padx=6)
        self.btn_ok.grid(row=0, column=2, padx=6)
        self.btn_cancel.grid(row=0, column=3, padx=6)

        self.bind("<Escape>", self._cancel)
        self._show_step("basic")
        self._show_modal(focus_widget=self.name_entry)

    # ---------- 基础信息页 ----------
    def _build_basic_page(self, parent):
        # 配置列权重：标签列最小宽度，输入列可伸缩
        parent.columnconfigure(0, weight=0, minsize=90)
        parent.columnconfigure(1, weight=1)

        # 清空旧的行配置（避免残留）
        for r in range(10):
            parent.grid_rowconfigure(r, weight=0)

        row = 0

        def add_row(label_text, var, widget_type="entry", **kwargs):
            nonlocal row
            ctk.CTkLabel(
                parent, text=label_text, anchor='w',
                font=self.UI_FONT, text_color=TEXT
            ).grid(row=row, column=0, sticky='w', padx=(10, 30), pady=(9,3))

            if widget_type == "entry":
                entry = ctk.CTkEntry(
                    parent, textvariable=var, width=300, height=26,
                    font=self.UI_FONT, **kwargs
                )
                entry.grid(row=row, column=1, padx=(0, 10), pady=(9,3))
                ret = entry
            elif widget_type == "disabled_entry":
                entry = ctk.CTkEntry(
                    parent, textvariable=var, width=300, height=26,
                    state="disabled", font=self.UI_FONT, **kwargs
                )
                entry.grid(row=row, column=1, padx=(0, 10), pady=(9,3))
                ret = entry
            else:
                ret = None

            row += 1
            return ret

        self.name_entry = add_row("包名称", self.name_var)
        self.world_id_entry = add_row("ID", self.world_id_var, "disabled_entry")
        self.version_entry = add_row("版本号", self.version_var)
        self.author_entry = add_row("作者", self.author_var)

        # 简介行：标签在列0，文本框在列1，并让该行可垂直拉伸
        ctk.CTkLabel(
            parent, text="简介", anchor='nw',
            font=self.UI_FONT, text_color=TEXT
        ).grid(row=row, column=0, sticky='nw', padx=(10, 30), pady=(15, 3))

        self.desc_text = ctk.CTkTextbox(
            parent, width=300, height=60, wrap="word",
            font=self.UI_FONT
        )
        if self._description_text:
            self.desc_text.insert("1.0", self._description_text)
        self.desc_text.grid(row=row, column=1, sticky='ns', padx=(0, 10), pady=(9, 3))

        # 设置简介行权重为1，使其在窗口高度变化时优先扩展
        parent.grid_rowconfigure(row, weight=1)
        row += 1

        self.basic_hint = ctk.CTkLabel(
            parent, text="", anchor='w', font=self.UI_FONT_SMALL,
            text_color=STATUS_ERR)
        self.basic_hint.grid(row=row, column=0, columnspan=2, sticky='w', padx=10)
        parent.grid_rowconfigure(row, weight=0)
        row += 1

    # ---------- 资源打包页 ----------
    def _build_resources_page(self, parent):
        """资源打包页：左栏静态表下拉框，右栏可切换的多选列表（带全选/清空），底部提示跨两栏。"""
        # 主容器：左右两栏
        main_row = ctk.CTkFrame(parent, fg_color="transparent")
        main_row.pack(fill='both', expand=True, pady=4)

        # ---- 左栏：静态表配置 ----
        left_frame = ctk.CTkFrame(main_row, fg_color="transparent", width=180)
        left_frame.pack(side='left', fill='y', padx=(0, 10))
        left_frame.pack_propagate(False)  # 固定宽度

        ctk.CTkLabel(
            left_frame, text="配置静态表", anchor='w',
            font=ui_fonts.ui_font(12, "bold"),
            text_color=SOFT
        ).pack(fill='x', pady=(0, 8))

        single_types = [("names", "姓名"),
                        ("news", "新闻"),
                        ("personalities", "性格"),
                        ("presets", "身材"),
                        ("behaviors", "行为包")
                        ]

        self._single_vars = {}
        for rtype, label in single_types:
            items = [str(i) for i in (self.available_resources.get(rtype) or []) if str(i)]
            if not items:
                continue
            row = ctk.CTkFrame(left_frame, fg_color="transparent")
            row.pack(fill='x', pady=2)
            ctk.CTkLabel(
                row, text=label, width=60, anchor='w',
                font=self.UI_FONT, text_color=TEXT
            ).pack(side='left')
            var = tk.StringVar(value="<不配置>")
            combo = ctk.CTkComboBox(
                row, values=["<不配置>"] + items, variable=var,
                width=100, height=24,
                font=self.UI_FONT,
                command=self._refresh_resource_label
            )
            combo.pack(side='left', padx=(4, 0))
            saved = self._resource_selection or {}
            if rtype in saved:
                var.set(saved[rtype][0])
            self._single_vars[rtype] = var

        # ---- 右栏：多选列表（可切换） ----
        right_frame = ctk.CTkFrame(main_row, fg_color="transparent")
        right_frame.pack(side='left', fill='both', expand=True)

        # 标题栏（显示当前类型 + 切换按钮）
        header_frame = ctk.CTkFrame(right_frame, fg_color="transparent")
        header_frame.pack(fill='x', pady=(0, 10))

        self._multi_label = ctk.CTkLabel(
            header_frame, text="配置资源包", anchor='w',
            font=ui_fonts.ui_font(12, "bold"),
            text_color=SOFT
        )
        self._multi_label.pack(side='left')

        ctk.CTkButton(
            header_frame, text="▶", width=22, height=22,
            command=self._switch_multi_type,
            fg_color="transparent", text_color=SOFT,
            hover_color=HOVER,
            border_width=1, border_color=INPUT_BORDER,
            corner_radius=6, font=ui_fonts.ui_font(12)
        ).pack(side='left', padx=8)

        # 创建 StyleListBox，并添加全选/清空按钮
        self._current_listbox = StyleListBox(
            right_frame, title="", height=3,
            on_change=self._refresh_resource_label
        )
        # 添加全选和清空按钮
        self._current_listbox.add_button("清空", self._current_listbox.clear_selection, padx=(10, 0))
        self._current_listbox.add_button("全选", self._current_listbox.select_all, padx=0)
        self._current_listbox.pack(fill='both', expand=True)

        # ---- 底部信息（跨两栏） ----
        # 挑战包提示（如适用）
        if "challenges" in self._multi_types:
            ctk.CTkLabel(
                parent,
                text="提示：挑战包密钥如需随包共享，请手动把 keys.json 复制到包内 challenges 目录。",
                anchor='w', wraplength=560, font=self.UI_FONT_SMALL,
                text_color=SOFT
            ).pack(fill='x', pady=2)

        # 汇总标签（放在底部）
        self.res_summary = ctk.CTkLabel(
            parent, text="", anchor='w', font=self.UI_FONT_SMALL,
            text_color=TEXT
        )
        self.res_summary.pack(fill='x', pady=2)

        # 初始化当前多选类型
        self._multi_selections = {}
        self._multi_index = 0
        self._update_current_listbox()
        self._refresh_resource_label()

    def _switch_multi_type(self):
        """循环切换到下一个多选类型，保存当前选择。"""
        current_rtype = self._multi_types[self._multi_index]
        if self._current_listbox:
            selected = self._current_listbox.get_selected_raw_names()
            if current_rtype == "challenges":
                selected = [n if n.endswith(".chal") else f"{n}.chal" for n in selected]
            self._multi_selections[current_rtype] = selected

        self._multi_index = (self._multi_index + 1) % len(self._multi_types)
        self._update_current_listbox()
        self._refresh_resource_label()

    def _update_current_listbox(self):
        """根据当前多选类型刷新右栏的 StyleListBox（保留已添加的全选/清空按钮）。"""
        rtype = self._multi_types[self._multi_index]
        label = dict(self._TYPE_LABELS).get(rtype, rtype)

        items = [str(i) for i in (self.available_resources.get(rtype) or []) if str(i)]
        if not items:
            self._current_listbox.sync_items([])
            self._current_listbox.set_title(f"{label}（0/0）")
            return

        display = self._display_items(rtype, items)
        saved = self._multi_selections.get(rtype, [])
        if not saved:
            self._current_listbox.sync_items(display)
        else:
            sel_indices = []
            for i, disp in enumerate(display):
                if rtype == "challenges":
                    raw = os.path.splitext(disp)[0]
                    if raw in saved:
                        sel_indices.append(i)
                else:
                    if disp in saved:
                        sel_indices.append(i)
            self._current_listbox.sync_items(display, selected_indices=sel_indices)
        # 标题更新由 _refresh_resource_label 统一处理，但这里可先设置基本计数
        total = len(display)
        count = len(self._current_listbox.listbox.curselection())
        self._current_listbox.set_title(f"{label}（{count}/{total}）")

    def _refresh_resource_label(self, *args):
        """刷新当前列表标题与底部汇总标签（跨两栏）。"""
        # 更新当前多选列表的标题
        if self._current_listbox:
            rtype = self._multi_types[self._multi_index]
            total = self._current_listbox.listbox.size()
            count = len(self._current_listbox.listbox.curselection())
            label = dict(self._TYPE_LABELS).get(rtype, rtype)
            self._current_listbox.set_title(f"{label}（{count}/{total}）")

        # 收集所有选中资源包（多选类型），不包含静态表
        parts = []
        for rtype in self._multi_types:
            label = dict(self._TYPE_LABELS).get(rtype, rtype)
            if rtype == self._multi_types[self._multi_index]:
                if self._current_listbox:
                    selected = self._current_listbox.get_selected_raw_names()
                    if rtype == "challenges":
                        selected = [n if n.endswith(".chal") else f"{n}.chal" for n in selected]
                    count = len(selected)
                    if count:
                        parts.append(f"{label}×{count}")
                    self._multi_selections[rtype] = selected
            else:
                cached = self._multi_selections.get(rtype, [])
                if cached:
                    parts.append(f"{label}×{len(cached)}")

        if parts:
            self.res_summary.configure(text="已选资源包：" + "、".join(parts))
        else:
            self.res_summary.configure(text="未配置资源包")

    def _save_resources_state(self):
        """保存当前所有资源选择（供返回基础页时保留）。"""
        state = {}
        # 静态表
        for rtype, var in self._single_vars.items():
            val = var.get().strip()
            if val and val != "<不配置>":
                state[rtype] = [val]
        # 多选类型：先保存当前显示的类型（从 listbox 获取）
        if self._current_listbox:
            rtype = self._multi_types[self._multi_index]
            selected = self._current_listbox.get_selected_raw_names()
            if rtype == "challenges":
                selected = [n if n.endswith(".chal") else f"{n}.chal" for n in selected]
            self._multi_selections[rtype] = selected
        # 合并所有多选缓存
        for rtype, sel in self._multi_selections.items():
            if sel:
                state[rtype] = sel
        self._resource_selection = state


    @staticmethod
    def _display_items(rtype: str, items) -> list:
        if rtype == "challenges":
            return [os.path.splitext(i)[0] for i in items]
        return list(items)


    def _validate_basic(self) -> bool:
        name = self.name_var.get().strip()
        world_id = self.world_id_var.get().strip()
        ok = bool(name) and bool(self._WORLD_ID_RE.match(world_id))
        if self._step == "basic" and hasattr(self, "basic_hint"):
            if not name:
                self.basic_hint.configure(text="请填写世界包名称", text_color=STATUS_ERR)
            elif not self._WORLD_ID_RE.match(world_id):
                self.basic_hint.configure(
                    text="世界 ID 需为字母数字开头、1-64 位的安全名称（可含 . _ -）",
                    text_color=STATUS_ERR)
            else:
                self.basic_hint.configure(text="")
        return ok

    # ---------- 两级导航 ----------
    def _show_step(self, step):
        self._step = step
        for w in self.step_area.winfo_children():
            w.destroy()
        if step == "basic":
            self._build_basic_page(self.step_area)
            self.btn_back.grid_remove()
            self.btn_ok.grid_remove()
            self.btn_next.grid()
            self.btn_cancel.grid()
        else:
            self._build_resources_page(self.step_area)
            self.btn_next.grid_remove()
            self.btn_back.grid()
            self.btn_ok.grid()
            self.btn_cancel.grid()

    def _go_resources(self, _event=None):
        if not self._validate_basic():
            return
        self._description_text = self.desc_text.get("1.0", "end").strip()
        self._show_step("resources")

    def _go_basic(self, _event=None):
        self._save_resources_state()
        self._show_step("basic")

    def _ok(self, _event=None):
        if not self._validate_basic():
            return
        # 收集最终选择（先保存当前状态）
        self._save_resources_state()
        selected = self._resource_selection or {}
        if not selected:
            self.res_summary.configure(text="请至少选择一种要打包的资源", text_color=STATUS_ERR)
            return
        self.result = {
            "name": self.name_var.get().strip(),
            "world_id": self.world_id_var.get().strip(),
            "version": self.version_var.get().strip() or "1.0",
            "author": self.author_var.get().strip(),
            "description": self._description_text,
            "selected_resources": selected,
        }
        self._close()

    def _cancel(self, _event=None):
        self.result = None
        self._close()
