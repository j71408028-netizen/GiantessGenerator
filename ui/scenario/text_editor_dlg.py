"""固定文本编辑器：正文 + 实时解析预览。

现成文本存在 ``<方案>/texts/`` 下（章节字段只记相对路径），所以正文不进
``config.json``；这个对话框就是它的编辑入口——写回 UTF-8，预览按
``dungeon.script_text`` 的同一套解析给出段落数、每段的指令与校验警告，
让作者在编辑器里就能看见「哪一段会演化、哪一段不会」。

格式说明（逻辑段落 / 指令行）见 ``docs/Dungeon/script.md``。
"""

import tkinter as tk

import customtkinter as ctk

import ui.ctk
from dungeon.script_text import (TYPE_SHORT_NAMES, describe_directive,
                                 parse_script)
from ui.ctk.theme import (
    CHAPTER_BORDER, CHAPTER_HINT, CHAPTER_TEXT, CHAPTER_TEXT_SOFT,
)
from ui.ctk.dialogs import BaseDialog
from ui.scenario.asset_import import write_script_text

#: 预览里最多逐段列出的段落数 / 警告条数
_PREVIEW_LIMIT = 12

#: 类型短名一览（提示文案用，顺序与 DungeonTextType 一致）
_TYPE_HINT = " / ".join(TYPE_SHORT_NAMES.values())


class ScriptTextDialog(BaseDialog):
    """编辑一篇固定文本；保存成功时 ``result`` 为正文，取消为 ``None``。"""

    def __init__(self, parent, text: str = "", rel_path: str = "",
                 scenario_repo=None, scenario_id=None):
        super().__init__(parent)
        self.title("编辑固定文本")
        self.geometry("800x700")
        self.minsize(640, 460)
        self.resizable(True, True)
        self._scenario_repo = scenario_repo
        self.scenario_id = scenario_id
        self.rel_path = rel_path or ""
        self.result = None
        self.transient(parent)
        # 抓取延到窗口可见之后（X11 要求 viewable，见 BaseDialog._grab_deferred）
        self._grab_deferred()
        self._build_ui(text or "")
        self._center_dialog(parent)
        self.wait_window()

    # ---------- 构建 ----------
    def _build_ui(self, text: str):
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill='both', expand=True, padx=14, pady=12)

        header = ctk.CTkLabel(
            main, text=f"文本文件：{self.rel_path or '（未指定）'}",
            font=self.UI_FONT_BOLD, anchor='w', text_color=CHAPTER_TEXT)
        header.pack(fill='x', pady=(0, 4))

        self.textbox = ctk.CTkTextbox(main, wrap='word', font=self.UI_FONT)
        self.textbox.pack(fill='both', expand=True)
        self.textbox.insert("1.0", text)
        self.textbox.bind("<KeyRelease>", lambda _e: self._refresh_preview())

        ctk.CTkLabel(main, text="解析预览", font=self.SECTION_FONT,
                     anchor='w').pack(fill='x', pady=(8, 2))
        self.preview = ctk.CTkTextbox(main, height=150, wrap='word',
                                      font=self.UI_FONT_SMALL)
        self.preview.pack(fill='x')
        self.preview.configure(state="disabled")

        ctk.CTkLabel(
            main,
            text=("空行分段（一个逻辑段落 = 一次步进）；段落首行可写独占一行的指令：\n"
                  f"  [段落类型 方向]   类型取 {_TYPE_HINT}\n"
                  "  方向写 +1 / -1 / 0（同时作用于介入度与破坏性），"
                  "或写 破坏性-1 / 热度+1 只改一路。\n"
                  "类型与方向都写全的段落才演化（改变坐标并产生伤亡）；"
                  "缺任一项只推进步数。"),
            justify='left', anchor='w', wraplength=640,
            font=self.UI_FONT_SMALL, text_color=CHAPTER_HINT).pack(
            fill='x', pady=(6, 0))

        buttons = ctk.CTkFrame(main, fg_color="transparent")
        buttons.pack(pady=(10, 0))
        ctk.CTkButton(buttons, text="保存", width=88, height=28, font=self.UI_FONT,
                      command=self._ok).pack(side='left', padx=5)
        ctk.CTkButton(buttons, text="取消", width=88, height=28, font=self.UI_FONT,
                      fg_color="transparent", border_width=1, corner_radius=8,
                      text_color=CHAPTER_TEXT_SOFT, border_color=CHAPTER_BORDER,
                      command=self._cancel).pack(side='left', padx=5)

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Control-s>", lambda _e: self._ok())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self._refresh_preview()

    # ---------- 预览 ----------
    def current_text(self) -> str:
        return self.textbox.get("1.0", "end-1c")

    def _refresh_preview(self):
        script = parse_script(self.current_text())
        lines = [f"共 {len(script.paragraphs)} 个逻辑段落（每段一次步进）"]
        for paragraph in script.paragraphs[:_PREVIEW_LIMIT]:
            lines.append(f"  {paragraph.index}. {describe_directive(paragraph.directive)}")
        if len(script.paragraphs) > _PREVIEW_LIMIT:
            lines.append(f"  ……另有 {len(script.paragraphs) - _PREVIEW_LIMIT} 段")
        for warning in script.warnings[:6]:
            lines.append(f"⚠ {warning}")
        if len(script.warnings) > 6:
            lines.append(f"⚠ ……另有 {len(script.warnings) - 6} 条")
        if not script.paragraphs:
            lines.append("（正文为空：本章不会播放任何内容）")
        try:
            self.preview.configure(state="normal")
            self.preview.delete("1.0", "end")
            self.preview.insert("1.0", "\n".join(lines))
            self.preview.configure(state="disabled")
        except tk.TclError:
            pass  # 对话框已在关闭途中

    # ---------- 保存 ----------
    def _ok(self):
        text = self.current_text()
        if not parse_script(text).paragraphs:
            if not ui.ctk.dialogs.askyesno(
                    "文本为空",
                    "这篇文本没有任何逻辑段落，运行时不会播放任何内容。仍要保存吗？"):
                return
        if not write_script_text(self._scenario_repo, self.scenario_id,
                                 self.rel_path, text):
            return
        self.result = text
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()
