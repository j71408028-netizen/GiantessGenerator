import re
import tkinter as tk
from typing import Optional

import customtkinter as ctk

import ui.common
from logic import SIZE_CATEGORIES
from persistence import QuipRepo
from ui.common.dialogs import BaseDialog
from ui.common.theme import QUIP_TYPE_COLORS, COLD_DEL_BG, COLD_DEL_HOVER



class QuipDialog(BaseDialog):
    """描述编辑对话框（customtkinter 版本），含右侧插入类型面板，描述支持多行编辑"""

    def __init__(self, parent, title, quip=None, intrusion=None, destruction=None, summary=None,
                 style_name=None, quip_repo: Optional['QuipRepo'] = None, step=None):
        super().__init__(parent.winfo_toplevel())
        self.title(title)
        self.quip = quip
        self.intrusion = intrusion
        self.destruction = destruction
        self.summary = summary
        self.quip_repo = quip_repo          # 直接持有 QuipRepo 实例
        self.style_name = style_name
        self.result = None
        self._parent = parent.winfo_toplevel()

        # 确定步进初始值
        if step is not None:
            self.step_value = step
        elif isinstance(quip, dict) and "step" in quip:
            self.step_value = quip["step"]
        else:
            # 使用 QuipRepo.default_step 静态方法
            self.step_value = QuipRepo.default_step(intrusion or 1, destruction or 1)

        # 记录默认步进值（用于无效输入时回退）
        self.default_step = QuipRepo.default_step(intrusion or 1, destruction or 1)
        self._step_manually_edited = False

        # 去除 quip 中的 summary 标记
        if quip:
            clean_quip, extracted_summary = self._strip_summary_mark(quip)
            if summary is None and extracted_summary:
                self.summary = extracted_summary
            self.quip = clean_quip

        self.transient(self._parent)
        self.grab_set()

        self.type_list = self._build_type_list()
        self._create_widgets()
        self.geometry("560x315")
        self._center_dialog(parent)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.wait_window()

    def _strip_summary_mark(self, text):
        """从文本末尾移除 [summary:xxx] 标记，返回 (清理后的文本, 提取的summary或None)"""
        pattern = r'\[summary:(.*?)\]$'
        match = re.search(pattern, text.strip())
        if match:
            summary = match.group(1).strip()
            clean = re.sub(pattern, '', text).rstrip()
            return clean, summary
        return text, None

    def _build_type_list(self):
        """构建类型列表，每个元素为 (显示名称, 字母, 细分列表)"""
        type_list = [
            ("衣着", "a", ["裙子", "制服", "夏季", "冬季"]),
            ("姿势", "b", ["站立", "坐下", "躺下", "蹲跪"])
        ]
        # 使用 QuipRepo 加载自定义类型
        if self.quip_repo and self.style_name:
            meta = self.quip_repo.load_meta(self.style_name)
            custom_types = meta.get("custom_types", {})
            for key, info in custom_types.items():
                display = info.get("name", f"自定义{key.upper()}")
                subtypes = info.get("subtypes", ["选项1", "选项2", "选项3", "选项4"])
                type_list.append((display, key, subtypes))
        else:
            # 无仓库或无风格时，使用默认自定义类型（保持向后兼容）
            type_list.extend([
                ("自定义1", "c", ["选项1", "选项2", "选项3", "选项4"]),
                ("自定义2", "d", ["选项1", "选项2", "选项3", "选项4"]),
                ("自定义3", "e", ["选项1", "选项2", "选项3", "选项4"])
            ])
        return type_list

    def _create_widgets(self):
        # 主容器：左右分栏
        self.grid_columnconfigure(0, weight=3)   # 左侧编辑区
        self.grid_columnconfigure(1, weight=1)   # 右侧插入面板
        self.grid_rowconfigure(0, weight=1)

        # ========== 左侧区域 ==========
        left_frame = ctk.CTkFrame(self, fg_color="transparent")
        left_frame.grid(row=0, column=0, sticky="nsew", padx=(10, 5), pady=10)
        left_frame.grid_columnconfigure(1, weight=1)
        left_frame.grid_rowconfigure(3, weight=1)  # 描述行可伸展

        # ---- 第一行：介入度 + 破坏性（并排放置） ----
        row_frame = ctk.CTkFrame(left_frame, fg_color="transparent")
        row_frame.grid(row=0, column=0, columnspan=2, sticky='w', pady=(3, 6), padx=10)

        ctk.CTkLabel(row_frame, text="介入度:", font=self.UI_FONT).pack(side='left', padx=(0, 5))
        self.intrusion_var = tk.IntVar(value=self.intrusion if self.intrusion is not None else 1)
        self.intrusion_combo = ctk.CTkOptionMenu(row_frame, values=["1", "2", "3", "4"],
                                                 variable=self.intrusion_var, width=60,
                                                 height=28, font=self.UI_FONT,
                                                 dropdown_font=self.UI_FONT)
        self.intrusion_combo.pack(side='left', padx=5)

        ctk.CTkLabel(row_frame, text="破坏性:", font=self.UI_FONT).pack(side='left', padx=(25, 5))
        self.destruction_var = tk.IntVar(value=self.destruction if self.destruction is not None else 1)
        self.destruction_combo = ctk.CTkOptionMenu(row_frame, values=["1", "2", "3", "4"],
                                                   variable=self.destruction_var, width=60,
                                                   height=28, font=self.UI_FONT,
                                                   dropdown_font=self.UI_FONT)
        self.destruction_combo.pack(side='left', padx=5)

        ctk.CTkLabel(row_frame, text="步进:", font=self.UI_FONT).pack(side='left', padx=(25, 5))
        self.step_var = tk.StringVar()
        self.step_var.set(str(self.step_value))
        self.step_entry = ctk.CTkEntry(row_frame, textvariable=self.step_var, width=60,
                                       height=28, font=self.UI_FONT)
        self.step_entry.pack(side='left', padx=5)

        # 绑定步进输入修改事件，标记手动编辑
        self.step_var.trace_add("write", lambda *args: setattr(self, '_step_manually_edited', True))

        # ---- 第二行：简介（可选） ----
        ctk.CTkLabel(left_frame, text="简介:", font=self.UI_FONT).grid(row=1, column=0, sticky='w', pady=6, padx=10)
        self.summary_var = tk.StringVar(value=self.summary if self.summary else "")
        self.summary_entry = ctk.CTkEntry(left_frame, textvariable=self.summary_var, width=400,
                                          height=28, font=self.UI_FONT)
        self.summary_entry.grid(row=1, column=1, pady=5, sticky='ew', padx=10)

        # ---- 第三行：描述（多行文本框） ----
        ctk.CTkLabel(left_frame, text="描述:", font=self.UI_FONT).grid(row=2, column=0, sticky='nw', pady=6, padx=10)
        self.quip_textbox = ctk.CTkTextbox(left_frame, wrap='word', height=130, width=400,
                                           font=self.UI_FONT_LARGE)
        self.quip_textbox.grid(row=2, column=1, pady=5, sticky='nsew', padx=10)
        if self.quip:
            self.quip_textbox.insert("1.0", self.quip)
        self._setup_rich_text()

        # 提示栏（称呼占位符按钮）
        hint_bar = ctk.CTkFrame(left_frame, fg_color="transparent")
        hint_bar.grid(row=3, column=0, columnspan=2, sticky='w', pady=6, padx=10)
        ctk.CTkLabel(hint_bar, text="称呼占位符:", font=self.UI_FONT).pack(side='left')
        ctk.CTkButton(hint_bar, text="名字", width=80, height=28,
                      font=self.UI_FONT,
                      command=lambda: self._insert_text("{name}")).pack(side='left', padx=10)
        ctk.CTkButton(hint_bar, text="昵称", width=80, height=28,
                      font=self.UI_FONT,
                      command=lambda: self._insert_text("{nick}")).pack(side='left')

        # 确定/取消按钮（仍放在左侧区域底部）
        confirm_frame = ctk.CTkFrame(left_frame, fg_color="transparent")
        confirm_frame.grid(row=4, column=0, columnspan=2, pady=5)
        ctk.CTkButton(confirm_frame, text="确定", command=self.ok, width=80,
                      height=28, font=self.UI_FONT).pack(side='left', padx=10)
        ctk.CTkButton(confirm_frame, text="取消", command=self._on_close, width=80,
                      height=28, font=self.UI_FONT).pack(side='left', padx=10)

        # ========== 右侧区域：插入类型面板 ==========
        right_frame = ctk.CTkFrame(self, fg_color="transparent", border_width=1, corner_radius=8)
        right_frame.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)

        # 类型选择
        ctk.CTkLabel(right_frame, text="选择类型", font=self.UI_FONT_BOLD).pack(pady=(5, 0))
        self.type_var = tk.StringVar()
        self.type_combo = ctk.CTkOptionMenu(right_frame, variable=self.type_var,
                                            values=[t[0] for t in self.type_list],
                                            height=28, font=self.UI_FONT,
                                            dropdown_font=self.UI_FONT,
                                            command=self._on_type_changed)
        self.type_combo.pack(pady=5, padx=15, fill='x')
        if self.type_list:
            self.type_combo.set(self.type_list[0][0])

        # 细分选择
        ctk.CTkLabel(right_frame, text="选择细分", font=self.UI_FONT_BOLD).pack(pady=(10, 0))
        self.subtype_var = tk.StringVar()
        self.subtype_combo = ctk.CTkOptionMenu(right_frame, variable=self.subtype_var, values=[],
                                               height=28, font=self.UI_FONT,
                                               dropdown_font=self.UI_FONT)
        self.subtype_combo.pack(pady=5, padx=15, fill='x')

        # 插入按钮
        ctk.CTkButton(right_frame, text="插入", height=28, font=self.UI_FONT,
                      command=self._insert_tag).pack(padx=15, pady=(35, 3))
        ctk.CTkButton(right_frame, text="标记", height=28, font=self.UI_FONT,
                      command=self._insert_mark_tag).pack(padx=15, pady=3)
        ctk.CTkButton(right_frame, text="删除", height=28, font=self.UI_FONT,
                      command=self._delete_tag, fg_color=COLD_DEL_BG, hover_color=COLD_DEL_HOVER).pack(padx=15, pady=3)

        # 初始化细分选项
        self._on_type_changed()

    def _on_type_changed(self, *args):
        """当类型改变时，更新细分下拉选项"""
        selected = self.type_var.get()
        for name, letter, subtypes in self.type_list:
            if name == selected:
                self.subtype_combo.configure(values=subtypes)
                if subtypes:
                    self.subtype_var.set(subtypes[0])
                break

    def _setup_rich_text(self):
        tb = self.quip_textbox._textbox
        self._quip_inner = tb
        mode = ctk.get_appearance_mode()
        for letter, colors in QUIP_TYPE_COLORS.items():
            color = colors[1] if mode == "Dark" else colors[0]
            tb.tag_configure(f"type_{letter}", foreground=color)
        tb.bind("<BackSpace>", self._on_quip_backspace, add="+")
        tb.bind("<KeyRelease>", self._on_quip_keyrelease, add="+")
        self._render_quip_tags()

    def _render_quip_tags(self, event=None):
        tb = getattr(self, '_quip_inner', None)
        if tb is None:
            return
        for letter in QUIP_TYPE_COLORS:
            tb.tag_remove(f"type_{letter}", "1.0", "end")
        content = tb.get("1.0", "end-1c")
        for m in re.finditer(r'\[([a-e]):\d+:[^\]]*\]', content):
            letter = m.group(1)
            tb.tag_add(f"type_{letter}", f"1.0 + {m.start()}c", f"1.0 + {m.end()}c")

    def _on_quip_keyrelease(self, event):
        self._render_quip_tags()

    def _on_quip_backspace(self, event):
        tb = self._quip_inner
        cursor = tb.index(tk.INSERT)
        if tb.compare(cursor, "<=", "1.0"):
            return None
        prev = tb.index(f"{cursor} - 1c")
        if tb.get(prev) != "]":
            return None
        content_before = tb.get("1.0", cursor)
        m = None
        for mm in re.finditer(r'\[([a-e]):\d+:([^\]]*)\]', content_before):
            if mm.end() == len(content_before):
                m = mm
                break
        if not m:
            return None
        s, e = m.span()
        inner = m.group(2)
        if inner == "MARK":
            tb.delete(f"1.0 + {s}c", f"1.0 + {e}c")
            tb.mark_set(tk.INSERT, f"1.0 + {s}c")
        else:
            c, d = m.start(2), m.end(2)
            tb.delete(f"1.0 + {e - 1}c", f"1.0 + {e}c")
            tb.delete(f"1.0 + {s}c", f"1.0 + {c}c")
            tb.mark_set(tk.INSERT, f"1.0 + {s + (d - c)}c")
        self._render_quip_tags()
        return "break"

    def _insert_text(self, text):
        """在光标处插入指定文本"""
        self.quip_textbox.focus_set()
        self.quip_textbox.insert(tk.INSERT, text)
        self._render_quip_tags()

    def _insert_tag(self):
        """插入类型标记，格式 [字母:索引:]，光标置于最后一个冒号后（即右括号前）"""
        selected_type = self.type_var.get()
        selected_subtype = self.subtype_var.get()

        for name, letter, subtypes in self.type_list:
            if name == selected_type:
                if not subtypes:
                    ui.common.dialogs.showwarning("警告", "该类型没有细分选项")
                    return
                try:
                    idx = subtypes.index(selected_subtype) + 1
                except ValueError:
                    ui.common.dialogs.showwarning("警告", "请选择一个有效的细分")
                    return
                # 生成标签： [字母:索引:]
                tag = f"[{letter}:{idx}:]"
                self.quip_textbox.focus_set()
                # 记录插入前的光标位置
                insert_pos = self.quip_textbox.index(tk.INSERT)
                self.quip_textbox.insert(tk.INSERT, tag)
                # 光标移动到插入文本末尾前一个字符（即右括号前）
                # 插入后光标位于 insert_pos + len(tag)，需要向左移动 1 个字符
                new_pos = f"{insert_pos} + {len(tag)}c - 1c"
                self.quip_textbox.mark_set(tk.INSERT, new_pos)
                self._render_quip_tags()
                return
        ui.common.dialogs.showwarning("警告", "未找到对应的类型")

    def _insert_mark_tag(self):
        """插入 [字母:数字:MARK] 标记，内容固定为 MARK"""
        selected_type = self.type_var.get()
        selected_subtype = self.subtype_var.get()

        for name, letter, subtypes in self.type_list:
            if name == selected_type:
                try:
                    idx = subtypes.index(selected_subtype) + 1
                except ValueError:
                    ui.common.dialogs.showwarning("警告", "请选择一个有效的细分")
                    return
                tag = f"[{letter}:{idx}:MARK]"
                self.quip_textbox.focus_set()
                self.quip_textbox.insert(tk.INSERT, tag)
                self._render_quip_tags()
                return
        ui.common.dialogs.showwarning("警告", "未找到对应的类型")

    def _delete_tag(self):
        """删除匹配当前类型和细分的标记：
        - 如果标记内容为 MARK，则完全删除（包括括号）
        - 否则只删除标记外壳，保留内部内容
        """
        selected_type = self.type_var.get()
        selected_subtype = self.subtype_var.get()

        # 查找对应的字母和索引
        letter = None
        idx = None
        for name, l, subtypes in self.type_list:
            if name == selected_type:
                letter = l
                try:
                    idx = subtypes.index(selected_subtype) + 1
                except ValueError:
                    pass
                break

        if not letter or idx is None:
            ui.common.dialogs.showwarning("警告", "请选择有效的类型和细分")
            return

        # 获取当前文本框内容
        content = self.quip_textbox.get("1.0", "end-1c")

        import re
        # 匹配两种格式：
        # 1. [字母:索引:MARK] - 完全删除
        # 2. [字母:索引:内容] - 只删除括号，保留内容
        def replace_match(match):
            full = match.group(0)
            inner = match.group(1)
            if inner == "MARK":
                return ""  # 完全删除
            else:
                return inner  # 保留内部内容

        # 正则：匹配 [字母:索引:内容]，捕获内容部分
        pattern = re.compile(rf'\[{letter}:{idx}:([^\]]*)\]')
        new_content = pattern.sub(replace_match, content)

        # 更新文本框
        self.quip_textbox.delete("1.0", "end")
        self.quip_textbox.insert("1.0", new_content)
        self.quip_textbox.mark_set(tk.INSERT, "1.0")
        self.quip_textbox.focus_set()
        self._render_quip_tags()

    def validate(self):
        quip_text = self.quip_textbox.get("1.0", "end-1c").strip()
        if not quip_text:
            ui.common.dialogs.showwarning("警告", "请输入描述内容")
            return False
        return True

    def ok(self):
        if not self.validate():
            return
        clean_quip = self.quip_textbox.get("1.0", "end-1c").strip()
        summary_text = self.summary_var.get().strip()
        if summary_text:
            full_quip = f"{clean_quip}[summary:{summary_text}]"
        else:
            full_quip = clean_quip

        # 获取步进值，若无效则使用默认值
        try:
            step = float(self.step_var.get().strip())
        except (ValueError, AttributeError):
            step = self.default_step

        self.result = {
            "quip": full_quip,
            "intrusion": self.intrusion_var.get(),
            "destruction": self.destruction_var.get(),
            "summary": summary_text,
            "step": step
        }
        self._on_close()

    def _on_close(self):
        """安全关闭对话框，避免触发父窗口额外刷新"""
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


def _to_level(value, default):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(4, n))


class _TargetSelectDialog(BaseDialog):
    """复制/移动描述时的目标坐标选择对话框。"""

    def __init__(self, parent, src_intr, src_dest, src_size):
        super().__init__(parent)
        self.title("选择目标位置")
        self.result = None
        self.geometry("360x230")

        self.size_var = tk.StringVar(value=src_size)
        self.intr_var = tk.StringVar(value=str(src_intr))
        self.dest_var = tk.StringVar(value=str(src_dest))

        form = ctk.CTkFrame(self, fg_color="transparent")
        form.pack(fill="x", padx=20, pady=(20, 6))

        ctk.CTkLabel(form, text="目标体型:", font=self.UI_FONT).grid(row=0, column=0, pady=6, sticky="e")
        ctk.CTkOptionMenu(form, variable=self.size_var, values=SIZE_CATEGORIES,
                          width=120, height=28, font=self.UI_FONT,
                          dropdown_font=self.UI_FONT).grid(row=0, column=1, pady=6, padx=8)

        ctk.CTkLabel(form, text="介入度:", font=self.UI_FONT).grid(row=1, column=0, pady=6, sticky="e")
        ctk.CTkOptionMenu(form, variable=self.intr_var, values=["1", "2", "3", "4"], width=60,
                          height=28, font=self.UI_FONT,
                          dropdown_font=self.UI_FONT).grid(row=1, column=1, pady=6, padx=8, sticky="w")

        ctk.CTkLabel(form, text="破坏性:", font=self.UI_FONT).grid(row=2, column=0, pady=6, sticky="e")
        ctk.CTkOptionMenu(form, variable=self.dest_var, values=["1", "2", "3", "4"], width=60,
                          height=28, font=self.UI_FONT,
                          dropdown_font=self.UI_FONT).grid(row=2, column=1, pady=6, padx=8, sticky="w")

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(pady=(6, 16))
        ctk.CTkButton(btn_row, text="确定", width=90, height=28, font=self.UI_FONT,
                      command=self._ok).pack(side="left", padx=8)
        ctk.CTkButton(btn_row, text="取消", width=90, height=28, font=self.UI_FONT,
                      command=self._close).pack(side="left", padx=8)

        self._show_modal()

    def _ok(self):
        self.result = (
            self.size_var.get(),
            _to_level(self.intr_var.get(), 1),
            _to_level(self.dest_var.get(), 1),
        )
        self._close()
