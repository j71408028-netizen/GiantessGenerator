# ui/exploration/report.py
import datetime
import os
import tkinter as tk

import ui.common.dialogs
from paths import data_dir

import customtkinter as ctk

from logic import ALL_PART_NAMES, format_size
from services.image_service import ImageService
from models import CharacterSnapshot
from ui.common.theme import (
    BORDER_ALT, VIEW_PNL_FG, SOFT, TEXT_MUTED,
    HARD_TITLE, HOVER_ALT, HEADER_BG, GOLD_TITLE,
    TAG_SEPARATOR, TAG_INTRO, TAG_WILL, TAG_MEASURE, TAG_COMPARE,
    TAG_QUIP, TAG_CASUALTY, TAG_BODY, PLACEHOLDER,
)
from ui.common import fonts as ui_fonts


class ReportPanel(ctk.CTkFrame):
    """
    右侧大容器：报告正文 + 详细尺寸（可翻页）+ 形象图。
    """

    def __init__(self, parent, app, context, params_panel, host):
        super().__init__(parent, border_width=1, corner_radius=12,
                         border_color=BORDER_ALT,
                         fg_color=VIEW_PNL_FG)
        self.app = app
        self.context = context
        self.params_panel = params_panel
        self.host = host

        self.last_report = None
        self._report_saved = False
        self.details_visible = False
        self._img_cache = []
        self._details_page = 0
        self._details_pages = []
        self._details_body_parts = {}
        self._details_height = 0.0
        self._details_unlocks = {}
        self._details_has_character = False

        self._build_ui()
        self._update_save_btn_state()

    # ---------- UI 构建 ----------
    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=0)   # 标题栏
        self.rowconfigure(1, weight=0)   # 详细尺寸（可折叠）
        self.rowconfigure(2, weight=1)   # 报告正文

        # 报告标题栏（轻度游戏质感）
        header_frame = ctk.CTkFrame(self, fg_color=HEADER_BG,
                                    corner_radius=8)
        header_frame.grid(row=0, column=0, sticky='ew', padx=4, pady=(5, 0))
        ctk.CTkLabel(header_frame, text="📜 探索报告", font=ui_fonts.ui_font(13, "bold"),
                     text_color=GOLD_TITLE).pack(side='left', padx=12, pady=4)

        # 保存报告按钮
        self.save_report_btn = ctk.CTkButton(
            header_frame, text="💾",
            font=ui_fonts.ui_font(12),
            width=25, height=20,
            fg_color="transparent",
            text_color=TEXT_MUTED,
            hover_color=HOVER_ALT,
            border_color=BORDER_ALT,
            corner_radius=6,
            command=self._save_report
        )
        self.save_report_btn.pack(side='left', padx=3, pady=(3, 2))

        # 标题栏右侧：详细尺寸切换标签
        self.details_toggle = ctk.CTkLabel(
            header_frame, text="显示详细尺寸",
            font=ctk.CTkFont(family=ui_fonts.cjk_family(), size=11, underline=True),
            text_color=TEXT_MUTED,
            cursor="hand2"
        )
        self.details_toggle.pack(side='right', padx=(0, 12), pady=4)
        self.details_toggle.bind("<Button-1>", lambda e: self._toggle_details())

        def _dt_hover_enter(_e):
            self.details_toggle.configure(text_color=GOLD_TITLE)

        def _dt_hover_leave(_e):
            self.details_toggle.configure(text_color=TEXT_MUTED)

        self.details_toggle.bind("<Enter>", _dt_hover_enter)
        self.details_toggle.bind("<Leave>", _dt_hover_leave)

        # 详细尺寸面板
        self.details_frame = ctk.CTkFrame(self, fg_color="transparent", height=195)
        self.details_frame.grid(row=1, column=0, sticky='nsew', padx=5, pady=2)
        self.details_frame.grid_propagate(False)
        self.details_frame.grid_columnconfigure(0, weight=1)
        self.details_frame.grid_columnconfigure(1, weight=0)
        self.details_frame.grid_rowconfigure(0, weight=1)

        self.details_text = ctk.CTkTextbox(self.details_frame, wrap='word',
                                            font=("仿宋", 14),
                                            fg_color="transparent",
                                            border_width=0)
        self.details_text.grid(row=0, column=0, sticky='nsew')

        self.details_image_label = ctk.CTkLabel(self.details_frame, text="", fg_color="transparent")
        self.details_image_label.grid(row=0, column=1, sticky='ne', padx=7, pady=5)

        # 翻页控件：place 在左下角，不占用额外网格行
        _pf = ctk.CTkFrame(self.details_frame, fg_color="transparent")
        _pf.place(relx=0, rely=1.0, anchor='sw')

        self.prev_page_btn = ctk.CTkButton(
            _pf, text="‹ 上一页", width=60,
            font=ui_fonts.ui_font(11),
            fg_color="transparent",
text_color=TEXT_MUTED,
                hover_color=HOVER_ALT,
                border_color=BORDER_ALT,
            corner_radius=4,
            command=self._prev_details_page
        )
        self.prev_page_btn.pack(side='left', padx=4, pady=(3, 6))

        self.page_label = ctk.CTkLabel(
            _pf, text="",
            font=ui_fonts.ui_font(11, "bold"),
            text_color=TEXT_MUTED
        )
        self.page_label.pack(side='left', padx=5, pady=(3, 6))

        self.next_page_btn = ctk.CTkButton(
            _pf, text="下一页 ›", width=60,
            font=ui_fonts.ui_font(11),
            fg_color="transparent",
text_color=TEXT_MUTED,
                hover_color=HOVER_ALT,
                border_color=BORDER_ALT,
            corner_radius=4,
            command=self._next_details_page
        )
        self.next_page_btn.pack(side='left', padx=4, pady=(3, 6))

        self.details_frame.grid_remove()

        text_container = ctk.CTkFrame(self, fg_color="transparent")
        text_container.grid(row=2, column=0, sticky='nsew', padx=6, pady=6)
        text_container.rowconfigure(0, weight=1)
        text_container.columnconfigure(0, weight=1)

        self.result_text = ctk.CTkTextbox(text_container, wrap='word',
                                           font=("仿宋", 15),
                                           fg_color="transparent",
                                           border_width=0)
        self.result_text.grid(row=0, column=0, sticky='nsew')
        self.result_text.configure(state='disabled')

    # ---------- 公开方法 ----------
    def render_report(self, report):
        """渲染完整报告（正文 + 详细尺寸 + 形象图）。"""
        self.last_report = report
        self._report_saved = False
        self._render_result_ui(report.report_text, report.height, report.body_parts)
        self._update_save_btn_state()

    def clear(self):
        """清空报告正文与详细尺寸区域。"""
        self.result_text.configure(state='normal')
        self.result_text.delete("1.0", "end")
        self.result_text.configure(state='disabled')
        self._clear_details_table()
        ImageService.clear_ctk_label_image(self.details_image_label)
        self.details_image_label.configure(text="")
        self._img_cache.clear()
        self.last_report = None
        self._report_saved = False
        self._update_save_btn_state()

    def save_report_to_file(self, giantess_id: str, name: str) -> str:
        """将当前报告写入文件并返回路径。"""
        return self._do_save_report(giantess_id, name, self.last_report.report_text)

    def mark_saved(self):
        self._report_saved = True
        self._update_save_btn_state()

    def update_theme(self, mode=None):
        """重设原生 Text 的标签颜色，不销毁或重建报告面板。"""
        if self.last_report is None:
            return
        self._render_result_ui(
            self.last_report.report_text,
            self.last_report.height,
            self.last_report.body_parts,
        )

    # ---------- 保存 ----------
    def _update_save_btn_state(self):
        if (self.last_report is not None
                and self.last_report.report_text.strip()
                and not self._report_saved):
            self.save_report_btn.configure(state='normal')
        else:
            self.save_report_btn.configure(state='disabled')

    def _do_save_report(self, giantess_id: str, name: str, report_text: str) -> str:
        report_dir = os.path.join(data_dir(), "archives", giantess_id, "报告")
        os.makedirs(report_dir, exist_ok=True)

        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        filename = f"{name}_报告_{timestamp}.txt"
        filepath = os.path.join(report_dir, filename)

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(report_text)

        return filepath

    def _save_report(self):
        if self.last_report is None:
            return

        report_text = self.last_report.report_text
        if not report_text.strip():
            ui.common.dialogs.showwarning("警告", "没有可保存的报告内容。")
            return

        if self.host.current_state is None:
            if ui.common.dialogs.askyesno("创建角色", "当前无角色。\n是否创建角色并保存报告？"):
                snapshot = self.context.character_from_core_or_report(self.last_report)
                self.host.current_state = snapshot
                self.host.switch_to_state_panel(snapshot)
                giantess_id = snapshot.giantess_id
                name = snapshot.name
            else:
                return
        else:
            giantess_id = self.host.current_state.giantess_id
            name = self.host.current_state.name

        filepath = self._do_save_report(giantess_id, name, report_text)
        self._report_saved = True
        self._update_save_btn_state()
        ui.common.dialogs.showinfo("保存成功", f"报告已保存至：{filepath}")

    # ---------- 正文渲染 ----------
    def _render_result_ui(self, text, height, body_parts):
        report_font = self.app.settings.get("report_font", "仿宋")
        desc_font = self.app.settings.get("desc_font", "Consolas")
        show_casualties = self.app.settings.get("show_casualties", True)

        self.result_text.configure(font=(report_font, 14))
        self.details_text.configure(font=(report_font, 12))

        text_widget = self.result_text._textbox
        self.result_text.configure(state='normal')
        text_widget.delete("1.0", "end")

        # 主题色板（浅/深双模式，轻度游戏质感）
        is_dark = ctk.get_appearance_mode() == "Dark"

        def C(light, dark):
            return dark if is_dark else light

        # 字体完全遵循设置：斜体描述用 desc_font，其余用 report_font
        text_widget.tag_configure('title', font=(report_font, 18, "bold"),
                                  foreground=C(*GOLD_TITLE),
                                  spacing1=0, spacing3=4)
        text_widget.tag_configure('separator', font=(report_font, 16),
                                  foreground=C(*TAG_SEPARATOR),
                                  spacing1=0, spacing3=0)
        text_widget.tag_configure('intro', font=(report_font, 16),
                                  foreground=C(*TAG_INTRO),
                                  spacing1=3, spacing3=3)
        text_widget.tag_configure('will', font=(report_font, 15, "bold"),
                                  foreground=C(*TAG_WILL),
                                  spacing1=6, spacing3=6)
        text_widget.tag_configure('measure', font=(report_font, 16, "bold"),
                                  foreground=C(*TAG_MEASURE),
                                  spacing1=7, spacing3=2)
        text_widget.tag_configure('compare', font=(report_font, 16),
                                  foreground=C(*TAG_COMPARE),
                                  spacing1=2, spacing3=3)
        text_widget.tag_configure('quip', font=(desc_font, 16, "italic"),
                                  foreground=C(*TAG_QUIP),
                                  spacing1=4, spacing3=10)
        text_widget.tag_configure('casualty_sep', font=(report_font, 15),
                                  foreground=C(*PLACEHOLDER),
                                  spacing1=6, spacing3=2)
        text_widget.tag_configure('casualty', font=(report_font, 15, "bold"),
                                  foreground=C(*TAG_CASUALTY),
                                  spacing1=2, spacing3=0)
        text_widget.tag_configure('body', font=(report_font, 15),
                                  foreground=C(*TAG_BODY))
        text_widget.tag_configure('strikethrough', overstrike=True)

        def insert_with_strike(widget, line, base_tag=None):
            if "[STRIKE]" not in line:
                if base_tag:
                    widget.insert(tk.END, line + "\n", base_tag)
                else:
                    widget.insert(tk.END, line + "\n")
                return
            pos = 0
            while True:
                start = line.find("[STRIKE]", pos)
                if start == -1:
                    seg = line[pos:]
                    if seg:
                        if base_tag:
                            widget.insert(tk.END, seg, base_tag)
                        else:
                            widget.insert(tk.END, seg)
                    break
                seg = line[pos:start]
                if seg:
                    if base_tag:
                        widget.insert(tk.END, seg, base_tag)
                    else:
                        widget.insert(tk.END, seg)
                end = line.find("[/STRIKE]", start + 8)
                if end == -1:
                    seg = line[start:]
                    if seg:
                        if base_tag:
                            widget.insert(tk.END, seg, base_tag)
                        else:
                            widget.insert(tk.END, seg)
                    break
                strike_text = line[start + 8:end]
                if base_tag:
                    widget.insert(tk.END, strike_text, (base_tag, "strikethrough"))
                else:
                    widget.insert(tk.END, strike_text, "strikethrough")
                pos = end + 9
            widget.insert(tk.END, "\n")

        for line in text.split('\n'):
            stripped = line.strip()
            if stripped == "":
                text_widget.insert(tk.END, "\n")
            elif line.startswith("QUIP_LINE:"):
                content = line.replace("QUIP_LINE:", "").replace('"', '').strip()
                if content:
                    text_widget.insert(tk.END, content + "\n", "quip")
                else:
                    text_widget.insert(tk.END, "（暂无事件记录）\n", "compare")
            elif "身高：" in line:
                insert_with_strike(text_widget, line, "title")
            elif line.startswith("═"):
                text_widget.insert(tk.END, line + "\n", "separator")
            elif line.startswith("\u200b"):
                text_widget.insert(tk.END, line + "\n", "intro")
            elif any(m in line for m in ("✨", "💔", "✅")):
                text_widget.insert(tk.END, line + "\n", "will")
            elif "📏" in line:
                text_widget.insert(tk.END, line + "\n", "measure")
            elif "└─" in line:
                text_widget.insert(tk.END, line + "\n", "compare")
            elif stripped.startswith("─") and ("─" * 10) in stripped:
                text_widget.insert(tk.END, line + "\n", "casualty_sep")
            elif show_casualties and "本报告总计" in line:
                text_widget.insert(tk.END, line + "\n", "casualty")
            else:
                text_widget.insert(tk.END, line + "\n", "body")

        self.result_text.configure(state='disabled')

        # 图片处理
        uploaded_path = self.params_panel.get_uploaded_image_path()
        if not uploaded_path and self.last_report and self.last_report.uploaded_image_path:
            uploaded_path = self.last_report.uploaded_image_path
        pil_img = ImageService.load_from_path(uploaded_path)

        if pil_img is not None:
            ctk_img = ImageService.format_image(pil_img)
            bg_color = self.details_image_label.cget("fg_color")
            self.details_image_label.configure(image=ctk_img, text="", fg_color=bg_color)
            self._img_cache.append(ctk_img)
        else:
            ImageService.clear_ctk_label_image(self.details_image_label)
            self.details_image_label.configure(text="", fg_color="transparent")

        self._render_details_table(body_parts, height)

    # ---------- 详细尺寸（可翻页文本） ----------
    def _render_details_table(self, body_parts, height):
        self._details_body_parts = body_parts
        self._details_height = height

        self._details_unlocks = {}
        state = self.host.current_state
        self._details_has_character = isinstance(state, CharacterSnapshot)
        if self._details_has_character:
            self._details_unlocks = state.size_unlocks or {}

        selected = set(self.context.selected_parts)
        selected.add("身高")
        ordered = []
        for p in ALL_PART_NAMES:
            if p in body_parts and p in selected:
                ordered.append(p)
        if self.context.reverse_details_order:
            ordered.reverse()

        parts_per_page = 5
        self._details_pages = []
        for i in range(0, len(ordered), parts_per_page):
            self._details_pages.append(ordered[i:i + parts_per_page])
        if not self._details_pages:
            self._details_pages.append([])

        self._details_page = 0
        self._show_details_page()

    def _show_details_page(self):
        page = self._details_page
        pages = self._details_pages
        body_parts = self._details_body_parts
        height = self._details_height
        report_font = self.app.settings.get("report_font", "仿宋")
        is_dark = ctk.get_appearance_mode() == "Dark"

        def C(light, dark):
            return dark if is_dark else light

        tw = self.details_text._textbox
        tw.tag_configure('dl_label',
            font=(report_font, 16, "bold"),
            foreground=C(*GOLD_TITLE),
            spacing1=3, spacing3=3)
        tw.tag_configure('dl_value',
            font=(report_font, 16),
            foreground=C(*TAG_BODY))

        name = self.last_report.name if self.last_report else ""
        age_str = self._calculate_age()

        self.details_text.configure(state='normal')
        self.details_text.delete("1.0", "end")

        def insert_line(label, value):
            sp = max(0, 20 - 2 * len(label))
            tw.insert("end", label, "dl_label")
            tw.insert("end", f"{' ' * sp}{value}\n", "dl_value")

        insert_line("姓名", name)
        insert_line("年龄", age_str)
        show_all = self.app.settings.get("show_all_details", False)
        for part_name in pages[page]:
            val = body_parts.get(part_name, 0)
            unlocked = (
                show_all
                or not self._details_has_character
                or part_name == "身高"
                or self._details_unlocks.get(part_name, "") != ""
            )
            size_str = format_size(val, base_size=height) if unlocked else ""
            insert_line(part_name, size_str)

        self.details_text.configure(state='disabled')

        total = len(pages)
        self.page_label.configure(text=f"第 {page + 1}/{total} 页")
        self.prev_page_btn.configure(state='normal' if page > 0 else 'disabled')
        self.next_page_btn.configure(state='normal' if page < total - 1 else 'disabled')

    def _prev_details_page(self):
        if self._details_page > 0:
            self._details_page -= 1
            self._show_details_page()

    def _next_details_page(self):
        if self._details_page < len(self._details_pages) - 1:
            self._details_page += 1
            self._show_details_page()

    def _calculate_age(self):
        if not self.last_report or not self.last_report.birthday:
            return ""
        birthday = self.last_report.birthday.strip()
        if not birthday:
            return ""
        try:
            bd = datetime.datetime.strptime(birthday.replace('/', '-')[:10], "%Y-%m-%d")
            today = datetime.datetime.now()
            age = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
            return f"{age}"
        except (ValueError, IndexError):
            try:
                year = int(birthday[:4])
                age = datetime.datetime.now().year - year
                return f"{age}"
            except (ValueError, IndexError):
                return birthday

    def _clear_details_table(self):
        self.details_text.configure(state='normal')
        self.details_text.delete("1.0", "end")
        self.details_text.configure(state='disabled')
        if hasattr(self, 'page_label'):
            self.page_label.configure(text="")
        self._details_page = 0
        self._details_pages = []
        self._details_body_parts = {}
        self._details_height = 0.0
        self._details_unlocks = {}
        self._details_has_character = False

    # ---------- 辅助UI更新 ----------
    def _toggle_details(self):
        if self.details_visible:
            self.details_frame.grid_remove()
            self.details_toggle.configure(text="显示详细尺寸")
            self.details_visible = False
        else:
            self.details_frame.grid()
            self.details_toggle.configure(text="隐藏详细尺寸")
            self.details_visible = True
