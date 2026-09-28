"""挂件版报告区（低像素风）。

报告按「一步一屏」给出：正文生成后先切成若干步，面板每次只显示一步，点一下看
下一步（单向，不能回退）；前三步看完先问一句是否继续，继续则再给两步。第一步
固定是身高对比——开头的标题、意愿与引言都不进播放；播完之后面板清空，报告全文
任何时候都不显示（要数值请切「尺寸」栏）。

顶部只有一个按钮：还在分步时是「下一步 / 继续」，分步结束后变回「报告 / 尺寸」
的视图切换。挂件版没有保存键——只要当前有角色，生成后就直接归档到
``data/archives/<角色>/报告/``。
"""

import datetime
import os
import tkinter as tk

from logic import ALL_PART_NAMES, format_size, comparison_lines, get_comparisons
from models import CharacterSnapshot, ReportData
from paths import data_dir
from ui.mini import pixel as px

#: 报告分两步给出：先 3 步，看过之后再问一句，答是则再给 2 步。
STEP_FIRST = 3
STEP_SECOND = 2
STEP_TOTAL = STEP_FIRST + STEP_SECOND

#: 对比段落的开头标记：报告里每个部位一段，第一段就是身高对比。
COMPARE_MARK = "📏"

EMPTY_HINT = """（尚无报告）

· 点「调查」掷出世界 / 地标 / 副本
· 点「创建」邂逅一位少女
· 点「报告」把她的身体与地标逐一对比
· 切到「尺寸」可看部位数值一览"""

FINISHED_HINT = """（报告播放完毕）

· 点「▸ 尺寸」看部位数值一览
· 再点「报告」可生成新的一份"""

ARCHIVED_HINT = "· 本次报告已归档到角色档案"

# 报告正文的分类配色：从像素调色板里挑，不再单独维护一套标签色。
TAG_TONES = {
    "title": "accent_hi",
    "separator": "text_off",
    "intro": "text_dim",
    "will": "ok",
    "measure": "challenge",
    "compare": "report",
    "quip": "dungeon",
    "casualty_sep": "text_off",
    "casualty": "danger",
    "body": "text",
    "dl_label": "accent",
    "dl_value": "text",
    "hint": "text_off",
}

#: 各标签的字号与字重。西文片段要按同一字号换成点阵西文字，行高才对得齐，
#: 因此字号集中在这里定义，正文与尺寸一览共用。
TAG_STYLES = {
    "title": (14, True),
    "separator": (13, False),
    "intro": (14, False),
    "will": (14, True),
    "measure": (14, True),
    "compare": (14, False),
    "quip": (14, False),
    "casualty_sep": (14, False),
    "casualty": (13, True),
    "body": (14, False),
    "dl_label": (14, True),
    "dl_value": (14, False),
    "hint": (13, False),
}

#: 各标签的额外行距（原样的呼吸感，逐条对应）。
TAG_SPACING = {
    "title": {'spacing3': 3},
    "intro": {'spacing1': 2, 'spacing3': 2},
    "will": {'spacing1': 4, 'spacing3': 4},
    "measure": {'spacing1': 5},
    "compare": {'spacing3': 2},
    "quip": {'spacing1': 3, 'spacing3': 8},
    "casualty_sep": {'spacing1': 4},
    "dl_label": {'spacing1': 2},
    "hint": {'spacing1': 3, 'spacing3': 3},
}

#: 纯西文片段专用标签名（按字号一档一个）。
LATIN_TAG_FMT = "latin{}"


def _balance(atoms: list, count: int) -> list:
    """把若干自然段按字数均分到 ``count`` 步，保持原顺序。

    切点是「最接近该步应占字数的段落边界」，因此各步长短相当，也不会出现空步。
    """
    if not atoms:
        return []
    if count <= 1:
        return ["\n\n".join(atoms)]
    if len(atoms) <= count:
        return list(atoms)                 # 段落比步数还少：一段一步

    total = sum(len(atom) for atom in atoms)
    prefix = [0]
    for atom in atoms:
        prefix.append(prefix[-1] + len(atom))

    cuts, start = [], 1
    for index in range(1, count):
        upper = len(atoms) - (count - index)      # 给后面每步留一个段落
        if start > upper:
            break
        ideal = total * index / count
        best = min(range(start, upper + 1), key=lambda c: abs(prefix[c] - ideal))
        cuts.append(best)
        start = best + 1

    bounds = [0] + cuts + [len(atoms)]
    return ["\n\n".join(atoms[bounds[i]:bounds[i + 1]])
            for i in range(len(bounds) - 1)]


def _compare_part(atom: str) -> str:
    """取对比段第一行的部位名（正文里写成 ``📏 部位 尺寸``）。"""
    lines = atom.strip().splitlines()
    tokens = lines[0].split() if lines else []
    if len(tokens) > 1 and tokens[0] == COMPARE_MARK:
        return tokens[1]
    return ""


def split_steps(text: str, count: int = STEP_TOTAL, opening: str = None) -> list:
    """把报告正文切成若干步。

    第一步固定是身高对比，且与其他步毫无二致：报告正文里若已有「身高」那一节
    （位置不定）就直接把它提到最前；正文没有挑中身高（罕见）才用 ``opening``
    按同一格式补一条。其余对比段均分到剩下的步里。报告开头的标题、意愿与引言
    不进播放，收尾的尾注（分隔线、时间、伤亡）同样不播。正文里 QUIP_LINE 自带
    的行尾换行使后续段落天然顶着一个空行，切完统一抹平再补一个，保证每一步
    （含提出来的身高）都是「空一行 + 📏 对比」的同一种形态。
    """
    steps = _split_steps_raw(text, count, opening)
    return ["\n" + step.strip() for step in steps if step.strip()]


def _split_steps_raw(text: str, count: int, opening: str) -> list:
    """``split_steps`` 的切分本体：只负责分段与均分，形态由外层统一。"""
    atoms = [block for block in (text or "").split("\n\n") if block.strip()]
    if not atoms:
        return [opening] if opening else []

    first = next((index for index, atom in enumerate(atoms)
                  if _compare_part(atom)), None)
    if first is None:                      # 没有对比段（罕见）：整篇按字数均分
        body = atoms
        return ([opening] if opening else []) + _balance(
            body, count - 1 if opening else count)

    body = atoms[first:]
    compares = [atom for atom in body if _compare_part(atom)]
    height = next((atom for atom in compares if _compare_part(atom) == "身高"), None)
    rest = [atom for atom in compares if atom is not height]

    if height is not None:
        return [height] + _balance(rest, count - 1)
    if opening:
        return [opening] + _balance(rest, count - 1)
    return _balance(compares, count)


class MiniReportView(px.Panel):
    """报告正文 + 尺寸一览，像素化渲染。"""

    def __init__(self, parent, app, context):
        super().__init__(parent, fill="ink_alt", border="line")
        self.app = app
        self.context = context
        self.last_report: ReportData = None
        self._view = "report"
        self._archived_path = ""
        self._steps = []
        self._step = 0
        self._stepping = False
        self._finished = False

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._build_ui()

    # ==================== UI ====================
    def _build_ui(self):
        # 标题条压在比面板更深的底色上，和正文区一起构成「凹进去」的层次。
        header = px.surface(self, fill="ink")
        header.pack(fill='x', padx=4, pady=(4, 0))

        self._title_label = px.label(header, "▤ 报告", tone="accent", size=13,
                                     bold=True)
        self._title_label.pack(side='left', padx=4, pady=2)

        self._toggle_btn = px.PixelButton(header, "▸ 尺寸", self.on_header_button,
                                          tone="text_dim", width=62, height=20)
        self._toggle_btn.pack(side='right', padx=2, pady=2)

        container = px.transparent(self)
        container.pack(fill='both', expand=True, padx=4, pady=(0, 4))
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)

        self.textbox = px.PixelText(container, size=11)
        self.textbox.grid(row=0, column=0, sticky='nsew')
        self.scrollbar = px.scrollbar(container, command=self.textbox.yview)
        self.scrollbar.grid(row=0, column=1, sticky='ns')
        self.textbox.configure(yscrollcommand=self.scrollbar.set)
        # 正文也能点：小游戏里「点一下继续」比去找按钮自然。
        self.textbox.bind("<Button-1>", lambda _e: self.advance())

        self._configure_tags()
        self.clear()

    def _configure_tags(self):
        """报告正文的着色标签：字号收窄、颜色取自像素调色板。"""
        text = self.textbox

        def tone(name):
            return px.pick(TAG_TONES[name])

        for name, (size, bold) in TAG_STYLES.items():
            options = {'font': px.font(size, bold), 'foreground': tone(name)}
            options.update(TAG_SPACING.get(name, {}))
            text.tag_configure(name, **options)
        text.tag_configure('strikethrough', overstrike=True)
        # 纯西文片段专用：只换字体不换颜色。Tk 的文本标签「后建者优先」，因此
        # 最后配置并抬到最上层，跑在段落的着色标签之上。
        for size in sorted({s for s, _ in TAG_STYLES.values()}):
            latin = LATIN_TAG_FMT.format(size)
            text.tag_configure(latin, font=(px.latin_family(), -size))
            text.tag_raise(latin)
        self._show_casualties = bool(self.app.settings.get("show_casualties", True))

    # ==================== 公开方法 ====================
    def render_report(self, report: ReportData):
        """显示一份新报告：从身高对比开始，逐条点下去。"""
        self.last_report = report
        self._archived_path = ""
        self._steps = split_steps(report.report_text, opening=self.opening_step(report))
        self._step = 0
        self._finished = False
        self._view = "report"
        self._configure_tags()
        self._render_current()

    def opening_step(self, report: ReportData) -> str:
        """开场的身高对比。

        报告正文的部位是按匹配度挑的，未必给身高留一节，而播放的第一屏要固定是
        身高对比，于是这里按同一套地标、同一套措辞补一条。它只出现在播放里：
        不进归档的正文，也不参与尺寸解锁。
        """
        rows = get_comparisons(self.context.merged_landmarks,
                               {"身高": report.height}, limit=1)
        if not rows:
            return ""
        size_str, compare_text = comparison_lines(rows[0], report.height)
        return f"{COMPARE_MARK} 身高 {size_str}\n{compare_text}"

    def show_details(self, state: CharacterSnapshot = None):
        """渲染尺寸一览；无报告时回退到当前角色的部位数据。"""
        self._view = "details"
        self._sync_header()
        self._configure_tags()
        self._render_details(state)

    def on_header_button(self):
        """顶部按钮：报告没走完时推进下一步，走完之后是「报告 / 尺寸」的切换。"""
        if self._view == "report" and self._steps and not self._finished:
            self.advance()
        else:
            self.toggle_view()

    def advance(self):
        """推进报告：批次边界先问是否继续，最后一步之后收尾并切到尺寸栏。"""
        if self._view != "report" or not self._steps or self._finished:
            return
        if not self._stepping:              # 已经在最后一步：这一点就是收尾
            self._finish_steps()
            return

        pending = len(self._steps) - (self._step + 1)
        if self._step + 1 == STEP_FIRST and pending:
            self.app.ask(f"继续生成后续 {pending} 步？", self._next_step,
                         self._finish_steps)
            return
        self._next_step()

    def _next_step(self):
        self._step = min(self._step + 1, len(self._steps) - 1)
        self._render_current()

    def _finish_steps(self):
        """收尾：清空面板，报告正文到此为止。

        报告全文任何时候都不显示（要数值就切「尺寸」栏），所以这里不是把面板
        切到尺寸，而是清干净并留一句「已播完」的提示。
        """
        self._finished = True
        self._stepping = False
        self._render_finished_hint()

    def _render_finished_hint(self):
        lines = list(FINISHED_HINT.split("\n"))
        if self._archived_path:
            lines.append(ARCHIVED_HINT)
        self._write_lines(lines, "hint")

    def _write_lines(self, lines, tag: str):
        self.textbox.configure(state='normal')
        self.textbox.delete("1.0", "end")
        for line in lines:
            self._write(line, tag if line.strip() else None)
        self.textbox.configure(state='disabled')

    def _render_current(self):
        """按当前步渲染：分步中只显示这一步，播完之后清空面板。"""
        self._stepping = (bool(self._steps) and not self._finished
                          and self._step < len(self._steps) - 1)
        self._sync_header()
        if self._finished:
            self._render_finished_hint()
        elif self._steps:
            self._render_report_text(self._steps[self._step])
        elif self.last_report is not None:
            self._render_report_text(self.last_report.report_text)

    def toggle_view(self):
        if self._view == "report":
            self.show_details(self.app.current_state)
        else:
            self._view = "report"
            self._configure_tags()
            self._render_current()

    def refresh_theme(self):
        """主题切换后重设标签颜色并重绘当前视图。"""
        self._configure_tags()
        if self._view == "details":
            self._render_details(self.app.current_state)
        elif self.last_report is not None:
            self._render_current()
        else:
            self.clear()

    def archive_report(self, giantess_id: str, name: str) -> str:
        """把当前报告写入档案目录；已写过则不重复写。"""
        if self.last_report is None or self._archived_path:
            return self._archived_path
        if not self.last_report.report_text.strip():
            return ""
        report_dir = os.path.join(data_dir(), "archives", giantess_id, "报告")
        try:
            os.makedirs(report_dir, exist_ok=True)
            timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
            path = os.path.join(report_dir, f"{name}_报告_{timestamp}.txt")
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write(self.last_report.report_text)
        except OSError as e:
            print(f"[Warning] 归档报告失败: {e}")
            return ""
        self._archived_path = path
        return path

    def clear(self):
        self.textbox.configure(state='normal')
        self.textbox.delete("1.0", "end")
        for line in EMPTY_HINT.split("\n"):
            self._write(line, "hint" if line else None)
        self.textbox.configure(state='disabled')
        self.last_report = None
        self._archived_path = ""
        self._steps = []
        self._step = 0
        self._stepping = False
        self._finished = False
        self._view = "report"
        self._sync_header()

    def _sync_header(self):
        """标题带步数、按钮随状态换名：分步中推进，走完后切换视图。"""
        if self._view == "details":
            self._title_label.configure(text="▤ 尺寸")
            self._toggle_btn.configure(text="▸ 报告")
        elif self._stepping:
            self._toggle_btn.configure(text="▸ 下一步")
        else:
            self._title_label.configure(text="▤ 报告")
            self._toggle_btn.configure(text="▸ 尺寸")

    # ==================== 渲染 ====================
    def _write(self, line: str, tag: str = None):
        self.textbox.write(line + "\n", tag)

    def insert_runs(self, text: str, tag: str = None, extra: tuple = ()):
        """写入一段文字，纯西文片段另外套上点阵西文字标签。

        「348.1 米」这类混排里，数字与拉丁字母用点阵西文字，中文留在中文字体，
        既有块状观感，西文也不会拖着一身衬线。
        """
        widget = self.textbox
        size = TAG_STYLES.get(tag, (14, False))[0]
        latin_tag = LATIN_TAG_FMT.format(size)
        for chunk, latin in px.split_runs(text):
            tags = tuple(t for t in ((tag,) + extra + ((latin_tag,) if latin else ()))
                         if t)
            if tags:
                widget.insert(tk.END, chunk, tags)
            else:
                widget.insert(tk.END, chunk)

    def _write_with_strike(self, line: str, tag: str):
        """正文里的 [STRIKE]…[/STRIKE] 渲染为删除线。"""
        widget = self.textbox
        widget.configure(state='normal')
        pos = 0
        while True:
            start = line.find("[STRIKE]", pos)
            if start == -1:
                if line[pos:]:
                    self.insert_runs(line[pos:], tag)
                break
            if line[pos:start]:
                self.insert_runs(line[pos:start], tag)
            end = line.find("[/STRIKE]", start + 8)
            if end == -1:
                self.insert_runs(line[start:], tag)
                break
            self.insert_runs(line[start + 8:end], tag, ("strikethrough",))
            pos = end + 9
        widget.insert(tk.END, "\n")
        widget.configure(state='disabled')

    def _render_report_text(self, text: str):
        self.textbox.configure(state='normal')
        self.textbox.delete("1.0", "end")
        for line in (text or "").split('\n'):
            stripped = line.strip()
            if stripped == "":
                self._write("")
            elif line.startswith("QUIP_LINE:"):
                content = line.replace("QUIP_LINE:", "").replace('"', '').strip()
                self._write(content if content else "（暂无事件记录）",
                            "quip" if content else "compare")
            elif "身高：" in line:
                self._write_with_strike(line, "title")
            elif line.startswith("═"):
                self._write(line, "separator")
            elif line.startswith("\u200b"):
                self._write(line, "intro")
            elif any(mark in line for mark in ("✨", "💔", "✅")):
                self._write(line, "will")
            elif "📏" in line:
                self._write(line, "measure")
            elif "└─" in line:
                self._write(line, "compare")
            elif stripped.startswith("─") and ("─" * 10) in stripped:
                self._write(line, "casualty_sep")
            elif self._show_casualties and "本报告总计" in line:
                self._write(line, "casualty")
            else:
                self._write(line, "body")
        self.textbox.configure(state='disabled')

    def _render_details(self, state: CharacterSnapshot = None):
        report = self.last_report
        if report is not None:
            body_parts = report.body_parts
            height = report.height
            name = report.name
        elif state is not None:
            body_parts = state.body_parts
            height = state.height
            name = state.name
        else:
            self.textbox.configure(state='normal')
            self.textbox.delete("1.0", "end")
            self._write("（暂无尺寸数据）", "hint")
            self.textbox.configure(state='disabled')
            return

        # 解锁判定对「有角色」与「只看报告」一视同仁：没有档案时按同一条规则从
        # 这份报告现推一份（报告里提过的部位才算已测量），否则会一次列出全部尺寸。
        if isinstance(state, CharacterSnapshot):
            unlocks = state.size_unlocks
        elif report is not None:
            unlocks = self.context.size_unlocks_from_report(report)
        else:
            unlocks = {}

        selected = set(self.context.selected_parts or ALL_PART_NAMES)
        selected.add("身高")
        ordered = [p for p in ALL_PART_NAMES if p in body_parts and p in selected]
        if self.context.reverse_details_order:
            ordered.reverse()

        show_all = bool(self.app.settings.get("show_all_details", False))

        self.textbox.configure(state='normal')
        self.textbox.delete("1.0", "end")
        for part in ordered:
            value = body_parts.get(part, 0)
            unlocked = (show_all or part == "身高"
                        or unlocks.get(part, "") != "")
            size_text = format_size(value, base_size=height) if unlocked else "—"
            gap = max(1, 10 - len(part) * 2)
            self.insert_runs(part, "dl_label")
            self.insert_runs(f"{' ' * gap}{size_text}\n", "dl_value")
        if name:
            self.insert_runs(f"\n（{name}）\n", "hint")
        self.textbox.configure(state='disabled')
